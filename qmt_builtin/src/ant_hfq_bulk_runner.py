# -*- coding: utf-8 -*-
"""一次性大批量后复权日线：daily_full_hfq（至去年末）+ daily_cache_hfq（当年起）。

历史用途：由「蚂蚁量化规则」临时注入调用 hfq_bulk_pump(ContextInfo)。
启动条件：data/daily_cache_hfq/FORCE_HFQ_BULK 存在。
完成：写 HFQ_BULK_DONE.json 并删除 FORCE 标记。

日常增量：已并入 ant_daily_sync_runner 的 _mirror_hfq_cache_csv /
_mirror_hfq_full_csv（随 15:35 日线 / 按需全量），无需再注入本 runner。

dividend_type = back（QMT 后复权）。

兼容 QMT 内置旧 Python（无 future annotations）。
"""
import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

HFQ_BULK_VERSION = "20260907.02"
FORCE_FLAG = "FORCE_HFQ_BULK"
DONE_NAME = "HFQ_BULK_DONE.json"
PROGRESS_NAME = "_hfq_bulk_progress.json"
# 单次 pump 尽量多拉；不按盘中限流
BATCH_SIZE = 60
SLICE_SEC = 45.0
SLEEP_BETWEEN_BATCH = 0.02
DIVIDEND = "back"
FULL_START = date(1990, 1, 1)

_BUSY = False
_LAST_LOG_TS = 0.0


def _paths() -> Tuple[str, str, str, str, str]:
    try:
        from ant_qmt_paths import PROJECT_ROOT
    except ImportError:
        from qmt_builtin.ant_qmt_paths import PROJECT_ROOT  # type: ignore

    root = str(PROJECT_ROOT).rstrip("\\/")
    data = os.path.join(root, "data")
    full_dir = os.path.join(data, "daily_full_hfq")
    cache_dir = os.path.join(data, "daily_cache_hfq")
    return root, data, full_dir, cache_dir, os.path.join(cache_dir, FORCE_FLAG)


def _full_cap() -> date:
    return date(date.today().year - 1, 12, 31)


def _cache_floor() -> date:
    return date(date.today().year, 1, 1)


def _armed(cache_dir: str) -> bool:
    return os.path.isfile(os.path.join(cache_dir, FORCE_FLAG))


def _done_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, DONE_NAME)


def _progress_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, PROGRESS_NAME)


def _load_progress(cache_dir: str) -> Dict[str, Any]:
    path = _progress_path(cache_dir)
    if not os.path.isfile(path):
        return {"cursor": 0, "ok": 0, "fail": 0, "skip": 0, "codes": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {"cursor": 0, "ok": 0, "fail": 0, "skip": 0, "codes": []}


def _save_progress(cache_dir: str, prog: Dict[str, Any]) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    path = _progress_path(cache_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _list_universe() -> List[str]:
    """优先对齐已有前复权全量/滚动目录；否则 all_a_stocks。"""
    _, data, _, _, _ = _paths()
    seen = set()
    out: List[str] = []
    for sub in ("daily_full_qfq", "daily_cache_qfq", "daily_full", "daily_cache"):
        d = os.path.join(data, sub)
        if not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".csv"):
                continue
            code = name[:-4].strip().upper()
            if "." not in code or code in seen:
                continue
            seen.add(code)
            out.append(code)
    if out:
        return sorted(out)

    csv_path = os.path.join(data, "all_a_stocks.csv")
    if os.path.isfile(csv_path):
        import csv

        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or [])
            key = fields[0] if fields else "code"
            for row in reader:
                raw = "".join(ch for ch in str(row.get(key) or "") if ch.isdigit())
                if len(raw) < 6:
                    continue
                code6 = raw[-6:]
                if code6.startswith(("6", "9")):
                    full = code6 + ".SH"
                elif code6.startswith(("0", "3")):
                    full = code6 + ".SZ"
                elif code6.startswith(("4", "8")):
                    full = code6 + ".BJ"
                else:
                    continue
                if full not in seen:
                    seen.add(full)
                    out.append(full)
    return sorted(out)


def _peek_last_date(path: str) -> Optional[date]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096), os.SEEK_SET)
            chunk = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    last = None
    for line in chunk.splitlines():
        if not line or line.startswith("date"):
            continue
        d_s = line.split(",", 1)[0].strip()[:10]
        try:
            last = datetime.strptime(d_s, "%Y-%m-%d").date()
        except ValueError:
            continue
    return last


def _code_ready(full_dir: str, cache_dir: str, code: str, today: date) -> bool:
    """full 覆盖到 cap、cache 覆盖到最近交易日附近。"""
    cap = _full_cap()
    floor = _cache_floor()
    full_path = os.path.join(full_dir, code + ".csv")
    cache_path = os.path.join(cache_dir, code + ".csv")
    full_last = _peek_last_date(full_path)
    cache_last = _peek_last_date(cache_path)
    if full_last is None or full_last < cap - timedelta(days=5):
        # 若当年才上市，full 可空但 cache 必须有
        if full_last is None and cache_last is not None and cache_last >= floor:
            # 仅 2026 上市：允许无 full
            pass
        else:
            return False
    if today >= floor:
        # 需要 cache；允许落后 5 个自然日
        if cache_last is None or cache_last < today - timedelta(days=10):
            # 若全量末日已是最近（停牌/退市），可接受无 cache
            if full_last is not None and full_last >= cap and cache_last is None:
                # 2026 尚无成交：仍算未齐，继续尝试拉 cache 窗
                return False
            if cache_last is None:
                return False
            if cache_last < today - timedelta(days=10):
                return False
    return True


def _split_and_write(
    code: str,
    bars: List[Dict[str, Any]],
    full_dir: str,
    cache_dir: str,
) -> bool:
    try:
        import ant_daily_sync_runner as ds
    except ImportError:
        import qmt_builtin.ant_daily_sync_runner as ds  # type: ignore

    bars = ds._sanitize_bars(bars or [])
    if len(bars) < 1:
        return False
    rows: Dict[str, Dict[str, Any]] = {}
    ds._merge_bars(rows, bars)
    cap = _full_cap()
    floor = _cache_floor()
    full_rows = ds._trim_rows_after(rows, cap)
    cache_rows = ds._trim_rows_before(rows, floor)
    os.makedirs(full_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    wrote = False
    if len(full_rows) >= 1:
        ds._write_csv_atomic(os.path.join(full_dir, code + ".csv"), full_rows)
        wrote = True
    if len(cache_rows) >= 1:
        ds._write_csv_atomic(os.path.join(cache_dir, code + ".csv"), cache_rows)
        wrote = True
    return wrote


def _fetch_batch(
    ContextInfo,
    codes: List[str],
    start_d: date,
    end_d: date,
) -> Dict[str, List[Dict[str, Any]]]:
    try:
        import ant_daily_sync_runner as ds
    except ImportError:
        import qmt_builtin.ant_daily_sync_runner as ds  # type: ignore

    # 尽量先 download（内置），再 get_market_data_ex(back)
    try:
        ds._ensure_builtin_download_bound()
    except Exception:
        pass
    try:
        xtdata = None
        try:
            from xtquant import xtdata as _xt

            xtdata = _xt
        except Exception:
            xtdata = None
        if xtdata is not None:
            ds._download_batch(xtdata, codes, start_d, end_d)
        try:
            ds._download_1d_via_builtin(codes, start_d, end_d)
        except Exception:
            pass
    except Exception as e:
        print("[后复权批量] download 跳过: %s" % e)

    batch_map, src = ds._batch_fetch_1d_bars(
        ContextInfo,
        None,
        codes,
        start_d,
        end_d,
        prefer_count=-1,
        quality_min=1,
        dividend_type=DIVIDEND,
    )
    print(
        "[后复权批量] fetch n=%d src=%s hit=%d"
        % (len(codes), src, sum(1 for v in batch_map.values() if v))
    )
    return batch_map or {}


def _mark_done(cache_dir: str, prog: Dict[str, Any]) -> None:
    payload = {
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "version": HFQ_BULK_VERSION,
        "ok": int(prog.get("ok") or 0),
        "fail": int(prog.get("fail") or 0),
        "skip": int(prog.get("skip") or 0),
        "total": len(prog.get("codes") or []),
    }
    path = _done_path(cache_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    flag = os.path.join(cache_dir, FORCE_FLAG)
    try:
        if os.path.isfile(flag):
            os.remove(flag)
    except OSError as e:
        print("[后复权批量] 清除 FORCE 失败: %s" % e)
    print("[后复权批量] 完成 %s" % payload)


def hfq_bulk_pump(ContextInfo) -> Optional[str]:
    """入口：handlebar/periodic/init 反复调用，直到 FORCE 消失。"""
    global _BUSY, _LAST_LOG_TS
    if _BUSY:
        return "busy"
    root, data, full_dir, cache_dir, flag = _paths()
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(full_dir, exist_ok=True)
    if not _armed(cache_dir):
        return "idle"
    if ContextInfo is None:
        return "no_ctx"

    _BUSY = True
    t0 = time.time()
    try:
        prog = _load_progress(cache_dir)
        codes = list(prog.get("codes") or [])
        if not codes:
            codes = _list_universe()
            prog["codes"] = codes
            prog["cursor"] = int(prog.get("cursor") or 0)
            prog["ok"] = int(prog.get("ok") or 0)
            prog["fail"] = int(prog.get("fail") or 0)
            prog["skip"] = int(prog.get("skip") or 0)
            prog["started_at"] = datetime.now().isoformat(timespec="seconds")
            _save_progress(cache_dir, prog)
            print(
                "[后复权批量] 启动 universe=%d full=%s cache=%s ver=%s"
                % (len(codes), full_dir, cache_dir, HFQ_BULK_VERSION)
            )

        total = len(codes)
        cur = int(prog.get("cursor") or 0)
        if cur >= total:
            _mark_done(cache_dir, prog)
            return "done"

        today = date.today()
        end_d = today
        start_d = FULL_START
        processed = 0
        while cur < total and (time.time() - t0) < SLICE_SEC:
            batch = codes[cur : cur + BATCH_SIZE]
            need: List[str] = []
            for code in batch:
                if _code_ready(full_dir, cache_dir, code, today):
                    prog["skip"] = int(prog.get("skip") or 0) + 1
                else:
                    need.append(code)
            if need:
                try:
                    got = _fetch_batch(ContextInfo, need, start_d, end_d)
                except Exception as e:
                    print("[后复权批量] batch 失败: %s" % e)
                    got = {}
                for code in need:
                    bars = got.get(code) or []
                    try:
                        ok = _split_and_write(code, bars, full_dir, cache_dir)
                    except Exception as e:
                        ok = False
                        print("[后复权批量] 写 %s 失败: %s" % (code, e))
                    if ok and _code_ready(full_dir, cache_dir, code, today):
                        prog["ok"] = int(prog.get("ok") or 0) + 1
                    elif ok:
                        # 写了但未达 ready（停牌等）— 仍计 ok，避免死循环
                        prog["ok"] = int(prog.get("ok") or 0) + 1
                    else:
                        prog["fail"] = int(prog.get("fail") or 0) + 1
                time.sleep(SLEEP_BETWEEN_BATCH)

            cur += len(batch)
            prog["cursor"] = cur
            processed += len(batch)
            _save_progress(cache_dir, prog)

            now = time.time()
            if now - _LAST_LOG_TS >= 10.0:
                _LAST_LOG_TS = now
                print(
                    "[后复权批量] %d/%d ok=%s fail=%s skip=%s (%.0fs)"
                    % (
                        cur,
                        total,
                        prog.get("ok"),
                        prog.get("fail"),
                        prog.get("skip"),
                        now - t0,
                    )
                )

        if cur >= total:
            _mark_done(cache_dir, prog)
            return "done"
        return "slice_%d/%d" % (cur, total)
    finally:
        _BUSY = False


def hfq_bulk_status() -> Dict[str, Any]:
    _, _, full_dir, cache_dir, _ = _paths()
    prog = _load_progress(cache_dir)
    return {
        "armed": _armed(cache_dir),
        "done": os.path.isfile(_done_path(cache_dir)),
        "full_dir": full_dir,
        "cache_dir": cache_dir,
        "cursor": prog.get("cursor"),
        "total": len(prog.get("codes") or []),
        "ok": prog.get("ok"),
        "fail": prog.get("fail"),
        "skip": prog.get("skip"),
        "full_csv": len([n for n in os.listdir(full_dir) if n.endswith(".csv")])
        if os.path.isdir(full_dir)
        else 0,
        "cache_csv": len([n for n in os.listdir(cache_dir) if n.endswith(".csv")])
        if os.path.isdir(cache_dir)
        else 0,
    }
