# -*- coding: utf-8 -*-
"""策略生成 → 交易系统 的待加载任务箱（inbox）。

策略生成系统只写入 data/pending_tasks.json，不再直接改 current_tasks_*.xlsx，
避免主程序 save_tasks 用内存旧表盖掉刚生成的任务。

交易系统在「加载任务」时读取并清空该文件，合并进当日任务表。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
PENDING_FILENAME = "pending_tasks.json"
PENDING_VERSION = 1

# 任务字典上的内部标记（写入 current_tasks 前会剥掉）
META_DROP_CLEAR = "_inbox_drop_scheduled_clear"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def pending_path(project_root: str) -> str:
    root = (project_root or "").strip() or os.getcwd()
    return os.path.join(root, "data", PENDING_FILENAME)


def _normalize_stock_code(stock_code: Any) -> str:
    s = str(stock_code or "").strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    s = "".join(c for c in s if c.isdigit())
    if not s:
        return ""
    return s.zfill(6) if len(s) >= 6 else s[:6].zfill(6)


def _project_root_from_tasks_file(tasks_file: str) -> str:
    """current_tasks 路径 → 项目根（…/data/current_tasks_….xlsx → …）。"""
    data_dir = os.path.dirname(os.path.abspath(tasks_file or ""))
    return os.path.dirname(data_dir)


def _sanitize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """深拷贝并把 params 规范成 dict。"""
    t = deepcopy(task) if isinstance(task, dict) else {}
    params = t.get("params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            params = {}
    if not isinstance(params, dict):
        params = {}
    t["params"] = params
    if not isinstance(params.get("rules"), list):
        params["rules"] = list(params.get("rules") or []) if params.get("rules") else []
    return t


def _strip_inbox_meta(task: Dict[str, Any]) -> Dict[str, Any]:
    t = dict(task or {})
    t.pop(META_DROP_CLEAR, None)
    t.pop("_inbox_meta", None)
    return t


def _merge_rules(
    old_rules: List[Dict[str, Any]],
    new_rules: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    try:
        from strategy_generator_app.task_builder import _merge_rules_replace_by_identity

        return _merge_rules_replace_by_identity(old_rules, new_rules)
    except Exception:
        pass
    try:
        from task_builder import _merge_rules_replace_by_identity  # type: ignore

        return _merge_rules_replace_by_identity(old_rules, new_rules)
    except Exception:
        # 极简回退：新规则追加，同 name 后者覆盖
        out = [dict(r) for r in (old_rules or []) if isinstance(r, dict)]
        by_name = {
            (str(r.get("leg_key") or "").strip() or str(r.get("name") or "").strip()): i
            for i, r in enumerate(out)
        }
        for nr in new_rules or []:
            if not isinstance(nr, dict):
                continue
            key = str(nr.get("leg_key") or "").strip() or str(nr.get("name") or "").strip()
            if key and key in by_name:
                out[by_name[key]] = dict(nr)
            else:
                by_name[key or f"#{len(out)}"] = len(out)
                out.append(dict(nr))
        return out


def merge_task_lists(
    base_tasks: List[Dict[str, Any]],
    incoming_tasks: List[Dict[str, Any]],
    *,
    drop_scheduled_clear_on_merge: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """按 6 位代码合并任务；同股规则按身份用 incoming 覆盖 base。

    drop_scheduled_clear_on_merge:
      - True/False：整批统一处理
      - None：看每条 incoming 上的 META_DROP_CLEAR 标记
    """
    final: List[Dict[str, Any]] = []
    code_to_idx: Dict[str, int] = {}
    for t in base_tasks or []:
        st = _sanitize_task(t)
        c = _normalize_stock_code(st.get("stock_code"))
        if not c:
            final.append(st)
            continue
        if c in code_to_idx:
            # base 内同股：后者覆盖索引（与 load_tasks 一致取后出现）
            idx = code_to_idx[c]
            old = final[idx]
            old_rules = list((old.get("params") or {}).get("rules") or [])
            new_rules = list((st.get("params") or {}).get("rules") or [])
            old.setdefault("params", {})["rules"] = _merge_rules(old_rules, new_rules)
        else:
            code_to_idx[c] = len(final)
            final.append(st)

    for raw in incoming_tasks or []:
        nt = _sanitize_task(raw)
        c = _normalize_stock_code(nt.get("stock_code"))
        drop_clear = drop_scheduled_clear_on_merge
        if drop_clear is None:
            drop_clear = bool(nt.get(META_DROP_CLEAR))
        if not c:
            final.append(_strip_inbox_meta(nt))
            continue
        if c in code_to_idx:
            idx = code_to_idx[c]
            old = final[idx]
            old_params = old.get("params") if isinstance(old.get("params"), dict) else {}
            old_rules = list(old_params.get("rules") or [])
            new_rules = list((nt.get("params") or {}).get("rules") or [])
            merged_rules = _merge_rules(old_rules, new_rules)
            if drop_clear:
                merged_rules = [
                    r
                    for r in merged_rules
                    if (r.get("type") or "").strip() != "scheduled_clear"
                ]
            old_params["rules"] = merged_rules
            old["params"] = old_params
            # 用新任务补缺字段（名称/数量等），保留旧 task_id
            for fld in ("stock_name", "strategy", "init_volume", "init_cost", "buy_date", "create_time"):
                if nt.get(fld) not in (None, "", "nan") and (
                    old.get(fld) in (None, "", "nan") or fld in ("create_time", "init_cost", "init_volume")
                ):
                    # create_time / 量价：incoming 更新时覆盖展示字段更合理
                    if fld in ("create_time", "init_cost", "init_volume", "stock_name"):
                        old[fld] = nt.get(fld)
                    elif not old.get(fld):
                        old[fld] = nt.get(fld)
            if not old.get("status") or old.get("status") in ("未运行", "待审核"):
                if nt.get("status"):
                    old["status"] = nt.get("status")
        else:
            clean = _strip_inbox_meta(nt)
            if drop_clear:
                params = clean.get("params") if isinstance(clean.get("params"), dict) else {}
                rules = list(params.get("rules") or [])
                params["rules"] = [
                    r for r in rules if (r.get("type") or "").strip() != "scheduled_clear"
                ]
                clean["params"] = params
            code_to_idx[c] = len(final)
            final.append(clean)
    return final


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp", prefix=".pending_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def _read_payload(path: str) -> Dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {"version": PENDING_VERSION, "updated_at": "", "tasks": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("读取待加载箱失败 %s: %s", path, e)
        return {"version": PENDING_VERSION, "updated_at": "", "tasks": []}
    if not isinstance(raw, dict):
        return {"version": PENDING_VERSION, "updated_at": "", "tasks": []}
    tasks = raw.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    clean = []
    for t in tasks:
        if isinstance(t, dict) and _normalize_stock_code(t.get("stock_code")):
            clean.append(_sanitize_task(t))
    return {
        "version": int(raw.get("version") or PENDING_VERSION),
        "updated_at": str(raw.get("updated_at") or ""),
        "tasks": clean,
    }


def load_pending(project_root: str) -> List[Dict[str, Any]]:
    with _LOCK:
        return list(_read_payload(pending_path(project_root)).get("tasks") or [])


def peek_pending_count(project_root: str) -> int:
    return len(load_pending(project_root))


def pending_exists(project_root: str) -> bool:
    path = pending_path(project_root)
    if not os.path.isfile(path):
        return False
    return peek_pending_count(project_root) > 0


def clear_pending(project_root: str) -> bool:
    """删除待加载箱；成功返回 True。"""
    path = pending_path(project_root)
    with _LOCK:
        if not os.path.isfile(path):
            return True
        try:
            os.remove(path)
            return True
        except OSError as e:
            logger.warning("清空待加载箱失败 %s: %s", path, e)
            return False


def save_pending_tasks(project_root: str, tasks: List[Dict[str, Any]]) -> str:
    """整表写回待加载箱（原子）。返回路径。"""
    path = pending_path(project_root)
    payload = {
        "version": PENDING_VERSION,
        "updated_at": _now_iso(),
        "tasks": [_sanitize_task(t) for t in (tasks or []) if isinstance(t, dict)],
    }
    with _LOCK:
        if not payload["tasks"]:
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return path
        _atomic_write_json(path, payload)
    return path


def enqueue_tasks(
    project_root: str,
    new_tasks: List[Dict[str, Any]],
    *,
    drop_scheduled_clear_on_merge: bool = False,
) -> Tuple[str, int, int]:
    """把新任务并入待加载箱（若箱内已有未加载任务则先合并）。

    返回 (path, 箱内总条数, 本次传入条数)。
    """
    incoming: List[Dict[str, Any]] = []
    for t in new_tasks or []:
        if not isinstance(t, dict):
            continue
        st = _sanitize_task(t)
        if not _normalize_stock_code(st.get("stock_code")):
            continue
        if drop_scheduled_clear_on_merge:
            st[META_DROP_CLEAR] = True
        incoming.append(st)
    with _LOCK:
        existing = list(_read_payload(pending_path(project_root)).get("tasks") or [])
        merged = merge_task_lists(
            existing,
            incoming,
            drop_scheduled_clear_on_merge=None,  # 尊重每条 META_DROP_CLEAR
        )
        path = save_pending_tasks(project_root, merged)
    logger.info(
        "待加载箱已更新: +%d → 共 %d 条 → %s",
        len(incoming),
        len(merged),
        path,
    )
    return path, len(merged), len(incoming)


def take_pending(project_root: str) -> List[Dict[str, Any]]:
    """取出并清空待加载箱（先 rename 再读，避免半读）。

    若应用失败，调用方可把任务再次 enqueue。
    """
    path = pending_path(project_root)
    with _LOCK:
        if not os.path.isfile(path):
            return []
        taking = path + ".taking"
        try:
            if os.path.isfile(taking):
                # 上次崩溃残留：优先恢复
                try:
                    os.remove(taking)
                except OSError:
                    pass
            os.replace(path, taking)
        except OSError as e:
            logger.warning("取出待加载箱 rename 失败，回退直接读: %s", e)
            tasks = list(_read_payload(path).get("tasks") or [])
            try:
                os.remove(path)
            except OSError:
                pass
            return tasks
        tasks = list(_read_payload(taking).get("tasks") or [])
        try:
            os.remove(taking)
        except OSError:
            pass
        return tasks


def recover_orphan_taking(project_root: str) -> int:
    """恢复异常中断留下的 .taking 文件到 pending。"""
    path = pending_path(project_root)
    taking = path + ".taking"
    with _LOCK:
        if not os.path.isfile(taking):
            return 0
        tasks_taking = list(_read_payload(taking).get("tasks") or [])
        if not tasks_taking:
            try:
                os.remove(taking)
            except OSError:
                pass
            return 0
        existing = list(_read_payload(path).get("tasks") or [])
        merged = merge_task_lists(existing, tasks_taking)
        save_pending_tasks(project_root, merged)
        try:
            os.remove(taking)
        except OSError:
            pass
        return len(tasks_taking)


def norms_in_tasks(tasks: List[Dict[str, Any]]) -> List[str]:
    out = []
    seen = set()
    for t in tasks or []:
        c = _normalize_stock_code((t or {}).get("stock_code"))
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out
