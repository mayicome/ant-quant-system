# -*- coding: utf-8 -*-
"""盘后批跑：按交易日检查各步骤产物是否已齐全。"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]


def _exists_nonempty(path: Path, min_bytes: int = 1) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def _glob_any(patterns: List[str]) -> Optional[Path]:
    for pat in patterns:
        hits = sorted(ROOT.glob(pat))
        if hits:
            return hits[-1]
    return None


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _csv_has_data_row(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            next(f, None)
            for line in f:
                if line.strip():
                    return True
    except Exception:
        return False
    return False


CheckResult = Tuple[bool, str]
Checker = Callable[[date], CheckResult]


def check_daily_cache(asof: date) -> CheckResult:
    from utils.daily_cache_reader import MANIFEST_PATH, load_manifest

    m = load_manifest()
    if not m:
        return False, f"manifest 缺失: {MANIFEST_PATH}"
    status = str(m.get("status") or "").strip().lower()
    sync = str(m.get("sync_trade_date") or "").strip().replace("-", "")[:8]
    want = asof.strftime("%Y%m%d")
    ok = status == "completed" and sync == want
    return ok, f"status={status} sync={sync or '-'} 期望={want}"


def check_limit_up(asof: date) -> CheckResult:
    d = asof.strftime("%Y-%m-%d")
    j = ROOT / "history_data" / "涨停日数据" / f"{d}.json"
    if _exists_nonempty(j, 20):
        return True, str(j.relative_to(ROOT))
    # 归档兜底
    arch = _glob_any([f"history_data/存档/**/涨停日数据/{d}.json"])
    if arch:
        return True, str(arch.relative_to(ROOT))
    return False, f"缺少 history_data/涨停日数据/{d}.json"


def check_lu_board_rank(asof: date) -> CheckResult:
    d = asof.strftime("%Y-%m-%d")
    a = ROOT / "data" / "eastmoney_board_rank" / f"industry_lu_rank_{d}.csv"
    b = ROOT / "data" / "eastmoney_board_rank" / f"concept_lu_rank_{d}.csv"
    ok = _exists_nonempty(a, 10) and _exists_nonempty(b, 10)
    if ok:
        return True, "industry/concept_lu_rank 已存在"
    miss = []
    if not _exists_nonempty(a, 10):
        miss.append(a.name)
    if not _exists_nonempty(b, 10):
        miss.append(b.name)
    return False, "缺少 " + ", ".join(miss)


def check_capital_flow(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    p = ROOT / "history_data" / "个股主力净流入" / f"个股主力净流入_{d8}.csv"
    if _exists_nonempty(p, 100) and _csv_has_data_row(p):
        return True, str(p.relative_to(ROOT))
    return False, f"缺少 {p.relative_to(ROOT)}"


def check_board_snapshot(asof: date) -> CheckResult:
    d = asof.strftime("%Y-%m-%d")
    names = [
        f"industry_rank_{d}.csv",
        f"concept_rank_{d}.csv",
        f"industry_fund_flow_{d}.csv",
        f"concept_fund_flow_{d}.csv",
    ]
    base = ROOT / "data" / "eastmoney_board_rank"
    miss = [n for n in names if not _exists_nonempty(base / n, 10)]
    if miss:
        return False, "缺少 " + ", ".join(miss)
    return True, "板块排名+资金流 4 文件齐全"


def check_stock_filter(asof: date) -> CheckResult:
    d = asof.strftime("%Y-%m-%d")
    hits = list((ROOT / "history_data").glob(f"选股结果_*_{d}.xls"))
    hits += list((ROOT / "history_data").glob(f"选股结果_*_{d}.xlsx"))
    if hits:
        return True, f"{len(hits)} 个选股结果"
    arch = list((ROOT / "history_data" / "存档").rglob(f"选股结果_*_{d}.xls"))
    if arch:
        return True, f"存档中 {len(arch)} 个选股结果"
    return False, f"无选股结果_*_{d}.xls"


def _check_history_file(patterns: List[str], label: str) -> CheckResult:
    p = _glob_any(patterns)
    if p and _exists_nonempty(p, 20):
        try:
            return True, str(p.relative_to(ROOT))
        except ValueError:
            return True, str(p)
    return False, f"缺少 {label}"


def check_profit_index(asof: date) -> CheckResult:
    d = asof.strftime("%Y-%m-%d")
    return _check_history_file(
        [f"history_data/daily_change_{d}.xlsx", f"history_data/存档/**/daily_change_{d}.xlsx"],
        f"daily_change_{d}.xlsx",
    )


def check_main_line(asof: date) -> CheckResult:
    d = asof.strftime("%Y-%m-%d")
    return _check_history_file(
        [
            f"history_data/main_line_group_{d}.txt",
            f"history_data/存档/**/main_line_group_{d}.txt",
        ],
        f"main_line_group_{d}.txt",
    )


def check_lu_structure(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    return _check_history_file(
        [f"history_data/封单结构_{d8}.xlsx", f"history_data/存档/**/封单结构_{d8}.xlsx"],
        f"封单结构_{d8}.xlsx",
    )


def check_seal_rating(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    return _check_history_file(
        [
            f"history_data/封单评级验证日报_{d8}.xlsx",
            f"history_data/存档/**/封单评级验证日报_{d8}.xlsx",
        ],
        f"封单评级验证日报_{d8}.xlsx",
    )


def check_lu_gene(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    return _check_history_file(
        [f"history_data/涨停基因_{d8}.xlsx", f"history_data/存档/**/涨停基因_{d8}.xlsx"],
        f"涨停基因_{d8}.xlsx",
    )


def check_main_force(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    return _check_history_file(
        [
            f"history_data/主力净流入统计_{d8}.xlsx",
            f"history_data/存档/**/主力净流入统计_{d8}.xlsx",
        ],
        f"主力净流入统计_{d8}.xlsx",
    )


def check_lhb(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    return _check_history_file(
        [f"history_data/龙虎榜解析_{d8}.xlsx", f"history_data/存档/**/龙虎榜解析_{d8}.xlsx"],
        f"龙虎榜解析_{d8}.xlsx",
    )


def check_lhb_premium(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    return _check_history_file(
        [
            f"history_data/龙虎榜溢价验证日报_{d8}.xlsx",
            f"history_data/存档/**/龙虎榜溢价验证日报_{d8}.xlsx",
        ],
        f"龙虎榜溢价验证日报_{d8}.xlsx",
    )


def check_inst_rank(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    return _check_history_file(
        [
            f"history_data/机构四榜连排_{d8}.xlsx",
            f"history_data/存档/**/机构四榜连排_{d8}.xlsx",
        ],
        f"机构四榜连排_{d8}.xlsx",
    )


def check_stock_info_f10(asof: date) -> CheckResult:
    meta_path = ROOT / "data" / "all_a_stock_info.meta.json"
    meta = _read_json(meta_path)
    if not meta:
        return False, "缺少 all_a_stock_info.meta.json"
    built = str(meta.get("built_at") or "")[:10]
    want = asof.strftime("%Y-%m-%d")
    ok = built == want
    return ok, f"built_at={built or '-'} 期望={want} stocks={meta.get('stock_count', '-')}"


def check_after_hours(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    p = ROOT / "data" / "after_hours_rank" / d8 / "top10.csv"
    if _exists_nonempty(p, 10) and _csv_has_data_row(p):
        return True, str(p.relative_to(ROOT))
    return False, f"缺少 {p.relative_to(ROOT)}"


def check_after_hours_export(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    meta = _read_json(ROOT / "data" / "cos" / "after_hours" / "after_hours_top_export_meta.json")
    if not meta:
        return False, "缺少 after_hours_top_export_meta.json"
    date_to = str(meta.get("date_to") or "").replace("-", "")[:8]
    day_counts = meta.get("day_counts") or {}
    ok = date_to == d8 or d8 in {str(k).replace("-", "")[:8] for k in day_counts}
    return ok, f"date_to={date_to or '-'} has_day={d8 in day_counts}"


def check_cos_shards(asof: date) -> CheckResult:
    d8 = asof.strftime("%Y%m%d")
    paths = [
        ROOT / "data" / "cos" / "board_rank" / f"{d8}.board_industry.jsonl",
        ROOT / "data" / "cos" / "main_flow" / f"{d8}.main_flow.jsonl",
    ]
    present = [p for p in paths if _exists_nonempty(p, 10)]
    if len(present) >= 1:
        return True, f"本地 COS 分片 {len(present)}/{len(paths)}（上传成功无本地硬标记，仅作参考）"
    return False, "本地 COS 日分片未见（上传状态无法精确判定）"


def check_wechat_draft(asof: date) -> CheckResult:
    from utils.post_market_step_status import get_step_record

    rec = get_step_record(asof, "wechat_draft")
    if rec and rec.get("ok"):
        ts = str(rec.get("finished_at") or "").strip()
        return True, f"本地记录已完成 {ts}".strip()
    if rec and not rec.get("ok"):
        ts = str(rec.get("finished_at") or "").strip()
        return False, f"本地记录失败 {ts} {rec.get('detail') or ''}".strip()
    return False, "无本地完成记录（生成草稿成功后会写入）"


def check_market_regime(asof: date) -> CheckResult:
    d = asof.strftime("%Y-%m-%d")
    d8 = asof.strftime("%Y%m%d")
    meta = _read_json(ROOT / "data" / "market_regime" / "market_regime_export_meta.json")
    if meta:
        date_to = str(meta.get("date_to") or "").replace("-", "")[:8]
        if date_to == d8:
            return True, f"meta.date_to={date_to}"
    csv_path = ROOT / "data" / "market_regime" / "market_regime_daily.csv"
    if _exists_nonempty(csv_path, 20):
        try:
            with csv_path.open("r", encoding="utf-8-sig", errors="ignore") as f:
                for line in f:
                    if d in line or d8 in line:
                        return True, f"CSV 含 {d}"
        except Exception:
            pass
    return False, f"行情描绘未覆盖 {d}"


# step_id -> checker（与流水线 id 对齐）
COMPLETENESS_CHECKERS: Dict[str, Checker] = {
    "daily_cache": check_daily_cache,
    "limit_up": check_limit_up,
    "lu_board_rank": check_lu_board_rank,
    "capital_flow": check_capital_flow,
    "board_snapshot": check_board_snapshot,
    "stock_filter": check_stock_filter,
    "profit_index": check_profit_index,
    "main_line_group": check_main_line,
    "limit_up_structure": check_lu_structure,
    "seal_rating": check_seal_rating,
    "limit_up_gene": check_lu_gene,
    "main_force_net_inflow": check_main_force,
    "lhb_analysis": check_lhb,
    "lhb_premium": check_lhb_premium,
    "inst_net_rank": check_inst_rank,
    "stock_info_f10": check_stock_info_f10,
    "after_hours_wait": check_after_hours,
    "after_hours_export": check_after_hours_export,
    "cos_upload": check_cos_shards,
    "wechat_draft": check_wechat_draft,
    "market_regime": check_market_regime,
}


def check_all(asof: date, step_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """返回各步骤齐全状态；finished_at 优先用产物文件 mtime，无文件的（如公众号）用本地记录。"""
    from utils.post_market_step_status import get_step_finished_at

    ids = step_ids or list(COMPLETENESS_CHECKERS.keys())
    out: List[Dict[str, Any]] = []
    for sid in ids:
        fn = COMPLETENESS_CHECKERS.get(sid)
        if not fn:
            out.append({"id": sid, "ok": False, "detail": "无检测器", "finished_at": ""})
            continue
        try:
            ok, detail = fn(asof)
        except Exception as e:
            ok, detail = False, f"检测异常: {e}"
        # 有文件：一律用文件时间；无文件（公众号）：用本地完成记录
        finished_at = artifact_finished_at(asof, sid, detail)
        if not finished_at and sid == "wechat_draft":
            finished_at = get_step_finished_at(asof, sid)
        out.append(
            {
                "id": sid,
                "ok": bool(ok),
                "detail": detail,
                "finished_at": finished_at,
            }
        )
    return out


def _artifact_paths(asof: date, step_id: str) -> List[Path]:
    d = asof.strftime("%Y-%m-%d")
    d8 = asof.strftime("%Y%m%d")
    hd = ROOT / "history_data"
    em = ROOT / "data" / "eastmoney_board_rank"
    mapping: Dict[str, List[Path]] = {
        "daily_cache": [ROOT / "data" / "daily_cache" / "manifest.json"],
        "limit_up": [hd / "涨停日数据" / f"{d}.json"],
        "lu_board_rank": [
            em / f"industry_lu_rank_{d}.csv",
            em / f"concept_lu_rank_{d}.csv",
        ],
        "capital_flow": [hd / "个股主力净流入" / f"个股主力净流入_{d8}.csv"],
        "board_snapshot": [
            em / f"industry_rank_{d}.csv",
            em / f"concept_rank_{d}.csv",
            em / f"industry_fund_flow_{d}.csv",
            em / f"concept_fund_flow_{d}.csv",
        ],
        "profit_index": [hd / f"daily_change_{d}.xlsx"],
        "main_line_group": [hd / f"main_line_group_{d}.txt"],
        "limit_up_structure": [hd / f"封单结构_{d8}.xlsx"],
        "seal_rating": [hd / f"封单评级验证日报_{d8}.xlsx"],
        "limit_up_gene": [hd / f"涨停基因_{d8}.xlsx"],
        "main_force_net_inflow": [hd / f"主力净流入统计_{d8}.xlsx"],
        "lhb_analysis": [hd / f"龙虎榜解析_{d8}.xlsx"],
        "lhb_premium": [hd / f"龙虎榜溢价验证日报_{d8}.xlsx"],
        "inst_net_rank": [hd / f"机构四榜连排_{d8}.xlsx"],
        "stock_info_f10": [ROOT / "data" / "all_a_stock_info.meta.json"],
        "after_hours_wait": [ROOT / "data" / "after_hours_rank" / d8 / "top10.csv"],
        "after_hours_export": [
            ROOT / "data" / "cos" / "after_hours" / "after_hours_top_export_meta.json"
        ],
        "cos_upload": [
            ROOT / "data" / "cos" / "board_rank" / f"{d8}.board_industry.jsonl",
            ROOT / "data" / "cos" / "main_flow" / f"{d8}.main_flow.jsonl",
        ],
        "market_regime": [
            ROOT / "data" / "market_regime" / "market_regime_export_meta.json"
        ],
    }
    paths = list(mapping.get(step_id) or [])
    if step_id == "stock_filter":
        paths.extend(sorted((hd).glob(f"选股结果_*_{d}.xls")))
        paths.extend(sorted((hd).glob(f"选股结果_*_{d}.xlsx")))
    return paths


def artifact_finished_at(asof: date, step_id: str, detail: str = "") -> str:
    """有产物文件则取其中最新 mtime；否则空。"""
    candidates = list(_artifact_paths(asof, step_id))
    det = str(detail or "").strip()
    # detail 里若带相对路径也纳入
    for token in det.replace(",", " ").split():
        if ("/" in token or "\\" in token) and not token.startswith("缺少"):
            p = ROOT / token
            if p.is_file():
                candidates.append(p)
    best_ts = 0.0
    for p in candidates:
        try:
            if p.is_file():
                best_ts = max(best_ts, p.stat().st_mtime)
        except OSError:
            continue
    if best_ts <= 0:
        return ""
    return datetime.fromtimestamp(best_ts).strftime("%Y-%m-%d %H:%M:%S")
