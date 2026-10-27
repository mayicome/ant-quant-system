# -*- coding: utf-8 -*-
"""马总逻辑1 · QMT 模型交易细回测（3b）

生成可粘贴到国金 QMT「模型交易」的动态 Python；在 QMT 内按需
大QMT内置 download_history_data(tick) + ContextInfo.get_market_data_ex(subscribe=False) 回放买腿（禁 MiniQMT xtdata/58610），
结果用固定前缀 print 到 FormulaOutput，再回灌策略生成回测页。

不解析 datadir 加密 .dat；不落全 A；不走本地 data/ticks 模拟撮合主路径。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_PATH = Path(__file__).resolve().parent / "_mz1_qmt_strategy_template.py"

DEFAULT_TICK_DEPTH_CALENDAR_DAYS = 29
RESULT_PREFIX = "ANT_MZ1_BT|"

# Guojin QMT userdata watch file (probe 探针 hot-reloads this path).
# Model trading Forbidden FileIO on project dir; always write under userdata.
DEFAULT_QMT_USERDATA = Path(r"D:\国金证券QMT交易端2\userdata")
DEFAULT_WATCH_DIR = DEFAULT_QMT_USERDATA / "mz1_qmt_fine_watch"
DEFAULT_WATCH_PATH = DEFAULT_WATCH_DIR / "current.py"

_LAST_WATCH_PATH = None  # type: Optional[Path]
_LAST_WATCH_ERROR = None  # type: Optional[Exception]
STRATEGY_IDS_MZ1_BUY = (
    "strategy_mz1_ma1020_half",
)


@dataclass
class ClampResult:
    start: date
    end: date
    clamped: bool
    reason: str = ""
    dropped_selection_days: List[str] = field(default_factory=list)


def parse_ymd(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10].replace("/", "-")
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def tick_window_bounds(
    *,
    as_of: Optional[date] = None,
    depth_calendar_days: int = DEFAULT_TICK_DEPTH_CALENDAR_DAYS,
) -> Tuple[date, date]:
    end = as_of or date.today()
    start = end - timedelta(days=max(1, int(depth_calendar_days)))
    return start, end


def clamp_range(
    start: Optional[date],
    end: Optional[date],
    *,
    as_of: Optional[date] = None,
    depth_calendar_days: int = DEFAULT_TICK_DEPTH_CALENDAR_DAYS,
) -> ClampResult:
    w0, w1 = tick_window_bounds(as_of=as_of, depth_calendar_days=depth_calendar_days)
    if start is None:
        start = w0
    if end is None:
        end = w1
    if end < start:
        start, end = end, start
    ns, ne = max(start, w0), min(end, w1)
    if ns > ne:
        return ClampResult(
            start=w0,
            end=w1,
            clamped=True,
            reason="请求区间完全在券商 tick 可下窗口之外，已改为窗口全长 %s～%s" % (w0, w1),
        )
    clamped = (ns != start) or (ne != end)
    reason = ""
    if clamped:
        reason = "已按券商 tick 深度钳制为 %s～%s（原 %s～%s）" % (ns, ne, start, end)
    return ClampResult(start=ns, end=ne, clamped=clamped, reason=reason)


def clamp_selection_days(
    selection_dates: List[date],
    *,
    entry_window: int = 10,
    from_t1: bool = True,
    as_of: Optional[date] = None,
    depth_calendar_days: int = DEFAULT_TICK_DEPTH_CALENDAR_DAYS,
) -> ClampResult:
    w0, w1 = tick_window_bounds(as_of=as_of, depth_calendar_days=depth_calendar_days)
    kept: List[date] = []
    dropped: List[str] = []
    for sd in sorted(set(selection_dates)):
        pad = max(1, int(entry_window)) + 8
        s0 = sd + timedelta(days=1 if from_t1 else 0)
        s1 = s0 + timedelta(days=pad)
        if s1 < w0 or s0 > w1:
            dropped.append(sd.isoformat())
            continue
        kept.append(sd)
    if not kept:
        return ClampResult(
            start=w0,
            end=w1,
            clamped=True,
            reason="选股日全部超出 tick 窗口 %s～%s" % (w0, w1),
            dropped_selection_days=dropped,
        )
    first = min(kept)
    last = max(kept)
    raw0 = first + timedelta(days=1 if from_t1 else 0)
    raw1 = last + timedelta(days=max(1, int(entry_window)) + 8)
    cr = clamp_range(raw0, raw1, as_of=as_of, depth_calendar_days=depth_calendar_days)
    cr.dropped_selection_days = dropped
    if dropped:
        cr.clamped = True
        extra = "丢掉 %d 个超窗选股日" % len(dropped)
        cr.reason = (cr.reason + "；" + extra) if cr.reason else extra
    return cr


def _full_code(code6: str) -> str:
    c = str(code6 or "").strip()
    if "." in c:
        return c.upper()
    c = c.zfill(6) if c.isdigit() else c
    if c.startswith(("5", "6", "9")):
        return c + ".SH"
    return c + ".SZ"


def _code6(code: str) -> str:
    s = str(code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    return s.zfill(6) if s.isdigit() else s


def load_mz1_strategy_params(root: Optional[Path] = None) -> Dict[str, Any]:
    root = root or ROOT
    path = (
        root
        / "strategy_generator_app"
        / "config"
        / "strategies"
        / "strategy_mz1_ma1020_half.json"
    )
    if not path.is_file():
        return {
            "buy_amount_per_stock": 50000,
            "min_order_amount": 5000,
            "entry_window_trading_days": 10,
            "rise_percent": 0,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    sp = data.get("strategy_params") or data.get("params") or {}
    if not isinstance(sp, dict):
        sp = {}
    return dict(sp)


def is_mz1_buy_strategy(strategy_id: str = "", name: str = "") -> bool:
    sid = str(strategy_id or "").strip()
    if sid in STRATEGY_IDS_MZ1_BUY:
        return True
    n = str(name or "")
    return ("马总逻辑1" in n) and ("MA10/20" in n or "MA10" in n) and ("买" in n)


def build_qmt_strategy_source(
    *,
    codes: List[str],
    selection_date_by_code: Dict[str, str],
    start: date,
    end: date,
    entry_window: int = 10,
    buy_amount_per_stock: float = 50000.0,
    min_order_amount: float = 5000.0,
    rise_percent: float = 0.0,
    from_t1: bool = True,
    run_tag: str = "",
    daily_pools: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    pool = []
    sel: Dict[str, str] = {}
    for raw in codes:
        c6 = _code6(raw)
        if not c6 or c6 == "000000":
            continue
        pool.append(_full_code(c6))
        if c6 in selection_date_by_code:
            sel[c6] = str(selection_date_by_code[c6])[:10]
        elif _full_code(c6) in selection_date_by_code:
            sel[c6] = str(selection_date_by_code[_full_code(c6)])[:10]

    pools_out: Dict[str, Dict[str, Any]] = {}
    if isinstance(daily_pools, dict):
        for day_key, info in daily_pools.items():
            if not isinstance(info, dict):
                continue
            day_codes = []
            day_sel: Dict[str, str] = {}
            raw_sel = info.get("selection_date_by_code") or {}
            for raw in (info.get("codes") or []):
                c6 = _code6(raw)
                if not c6 or c6 == "000000":
                    continue
                fc = _full_code(c6)
                day_codes.append(fc)
                pool.append(fc)
                ds = ""
                if c6 in raw_sel:
                    ds = str(raw_sel[c6])[:10]
                elif fc in raw_sel:
                    ds = str(raw_sel[fc])[:10]
                elif str(raw) in raw_sel:
                    ds = str(raw_sel[str(raw)])[:10]
                if ds:
                    day_sel[c6] = ds
                    if c6 not in sel:
                        sel[c6] = ds
            if day_codes:
                pools_out[str(day_key)[:10]] = {
                    "codes": sorted(set(day_codes)),
                    "selection_date_by_code": day_sel,
                }

    pool = sorted(set(pool))
    tag = run_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    cfg = {
        "codes": pool,
        "selection_date_by_code": sel,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "entry_window_trading_days": int(entry_window),
        "buy_amount_per_stock": float(buy_amount_per_stock),
        "min_order_amount": float(min_order_amount),
        "rise_percent": float(rise_percent),
        "from_t1": bool(from_t1),
        "run_tag": tag,
        "result_prefix": RESULT_PREFIX,
    }
    if pools_out:
        cfg["daily_pools"] = pools_out
    cfg_json = json.dumps(cfg, ensure_ascii=False, indent=2)
    tmpl = _TEMPLATE_PATH.read_text(encoding="utf-8")
    if "___CFG_JSON___" not in tmpl:
        raise RuntimeError("QMT template missing ___CFG_JSON___ placeholder")
    return tmpl.replace("___CFG_JSON___", cfg_json)


def default_out_dir(root: Optional[Path] = None) -> Path:
    root = root or ROOT
    d = root / "data" / "mz1_qmt_fine_backtest"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_watch_path() -> Path:
    return Path(DEFAULT_WATCH_PATH)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write via temp + os.replace so the probe never loads a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(str(tmp), str(path))
    return path


def write_watch_strategy_file(
    source: str,
    *,
    watch_path: Optional[Path] = None,
) -> Path:
    """Atomic write of strategy source to QMT userdata watch path for 探针."""
    target = Path(watch_path) if watch_path else default_watch_path()
    # Prefer utf-8; QMT importlib loads by path and respects coding cookie in file.
    return atomic_write_text(target, source, encoding="utf-8")


def write_qmt_strategy_file(
    source: str,
    *,
    out_path: Optional[Path] = None,
    root: Optional[Path] = None,
    also_gbk_copy: bool = True,
    also_watch: bool = True,
    watch_path: Optional[Path] = None,
) -> Path:
    """Write timestamped backup under data/mz1_qmt_fine_backtest/, and optionally
    atomic-drop to userdata/mz1_qmt_fine_watch/current.py for the hot-reload probe.
    """
    out_dir = default_out_dir(root)
    if out_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / ("mz1_qmt_fine_%s.py" % stamp)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(source, encoding="utf-8")
    if also_gbk_copy:
        gbk_path = out_path.with_suffix(".gbk.py")
        try:
            gbk_path.write_text(source, encoding="gbk", errors="replace")
        except Exception:
            pass
    global _LAST_WATCH_PATH, _LAST_WATCH_ERROR
    _LAST_WATCH_PATH = None
    _LAST_WATCH_ERROR = None
    if also_watch:
        try:
            wp = write_watch_strategy_file(source, watch_path=watch_path)
            _LAST_WATCH_PATH = wp
        except Exception as e:
            _LAST_WATCH_ERROR = e
    return out_path



def last_watch_path():
    return _LAST_WATCH_PATH


def last_watch_write_error():
    return _LAST_WATCH_ERROR


_LINE_RE = re.compile(r"ANT_MZ1_BT\|([A-Z_]+)\|(.*)$")


def _trade_dedupe_key(t: dict) -> tuple:
    """Stable key for identical fill rows (guards multi-run log noise)."""
    return (
        str(t.get("code") or ""),
        str(t.get("leg") or ""),
        str(t.get("date") or ""),
        str(t.get("time") or ""),
        str(t.get("price") if t.get("price") is not None else ""),
        str(t.get("volume") if t.get("volume") is not None else ""),
        str(t.get("rule") or ""),
    )


def _dedupe_trades(trades: List[dict]) -> List[dict]:
    out: List[dict] = []
    seen = set()
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        k = _trade_dedupe_key(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def parse_formula_output_text(text: str) -> Dict[str, Any]:
    """Parse FormulaOutput lines.

    QMT keeps one growing log for the day; the same script may also be
    executed more than once. Keep only the *last* run's TRADE rows (the
    final START..SUMMARY block), then dedupe identical fills.
    """
    runs: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    all_logs: List[str] = []
    all_errors: List[str] = []

    def _open_run(start_payload: Optional[dict] = None) -> Dict[str, Any]:
        sp = start_payload if isinstance(start_payload, dict) else {}
        run = {
            "start": sp,
            "run_tag": str(sp.get("run_tag") or ""),
            "trades": [],
            "summary": {},
            "logs": [],
            "errors": [],
        }
        runs.append(run)
        return run

    for raw in (text or "").splitlines():
        line = raw.strip()
        idx = line.find("ANT_MZ1_BT|")
        if idx < 0:
            continue
        line = line[idx:]
        m = _LINE_RE.match(line)
        if not m:
            continue
        kind, body = m.group(1), m.group(2)
        if kind == "LOG":
            all_logs.append(body)
            if current is not None:
                current["logs"].append(body)
            continue
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"raw": body}
        if kind == "START":
            current = _open_run(payload if isinstance(payload, dict) else {})
        elif kind == "TRADE" and isinstance(payload, dict):
            if current is None:
                current = _open_run({})
            current["trades"].append(payload)
        elif kind == "SUMMARY" and isinstance(payload, dict):
            if current is None:
                current = _open_run(payload)
            current["summary"] = payload
            tag = str(payload.get("run_tag") or "")
            if tag and not current.get("run_tag"):
                current["run_tag"] = tag
        elif kind == "ERROR":
            err = str(payload)
            all_errors.append(err)
            if current is not None:
                current["errors"].append(err)

    chosen: Optional[Dict[str, Any]] = None
    for run in runs:
        if run.get("summary") or run.get("trades") or run.get("start"):
            chosen = run

    start = (chosen or {}).get("start") or {}
    summary = (chosen or {}).get("summary") or {}
    raw_trades = list((chosen or {}).get("trades") or [])
    trades = _dedupe_trades(raw_trades)
    logs = list((chosen or {}).get("logs") or all_logs[-50:])
    errors = list((chosen or {}).get("errors") or [])
    if not errors and all_errors:
        errors = all_errors[-5:]

    return {
        "start": start,
        "trades": trades,
        "summary": summary,
        "logs": logs,
        "errors": errors,
        "trade_count": len(trades),
        "run_tag": str(
            (chosen or {}).get("run_tag")
            or summary.get("run_tag")
            or start.get("run_tag")
            or ""
        ),
        "runs_in_log": len(runs),
        "raw_trade_count_in_run": len(raw_trades),
        "raw_trade_count_all_runs": sum(len(r.get("trades") or []) for r in runs),
    }



def parse_formula_output_file(path: Path) -> Dict[str, Any]:
    path = Path(path)
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8", "gbk", "utf-16", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    result = parse_formula_output_text(text)
    result["source_path"] = str(path)
    return result


def _fmt_trade_time(tm) -> str:
    """Normalize TRADE time to HH:MM:SS (accepts epoch ms/ns or HH:MM:SS)."""
    if tm is None:
        return ""
    from datetime import datetime as _dt

    if isinstance(tm, _dt):
        return tm.strftime("%H:%M:%S")
    s = str(tm).strip()
    if not s:
        return ""
    if len(s) >= 8 and s[2:3] == ":" and s[5:6] == ":":
        return s[:8]
    try:
        v = float(s)
    except Exception:
        v = None
    if v is not None and v == v and v > 0:
        try:
            if v >= 1e17:
                return _dt.fromtimestamp(v / 1e9).strftime("%H:%M:%S")
            if v >= 1e14:
                return _dt.fromtimestamp(v / 1e6).strftime("%H:%M:%S")
            if v >= 1e12:
                return _dt.fromtimestamp(v / 1e3).strftime("%H:%M:%S")
            if v >= 1e9:
                return _dt.fromtimestamp(v).strftime("%H:%M:%S")
        except Exception:
            pass
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 13:
        try:
            return _dt.fromtimestamp(float(digits[:13]) / 1e3).strftime("%H:%M:%S")
        except Exception:
            pass
    if len(digits) >= 14:
        return "%s:%s:%s" % (digits[-6:-4], digits[-4:-2], digits[-2:])
    return s


def trades_to_backtest_rows(trades: List[dict]) -> List[dict]:
    """映射为 GUI `_fill_backtest_trades_table` 期望字段。"""
    rows = []
    for t in trades or []:
        trig = t.get("trigger")
        tip = ("触发价=%s" % trig) if trig is not None else ""
        rows.append(
            {
                "code": t.get("code"),
                "date": t.get("date") or "",
                "time": _fmt_trade_time(t.get("time")),
                "选股日": t.get("selection_date") or "",
                "side": "buy",
                "price": t.get("price"),
                "volume": t.get("volume"),
                "amount": t.get("amount"),
                "position_after": "",
                "rule_name": t.get("rule") or t.get("leg") or "",
                "leg_key": t.get("leg") or "",
                "trigger_info": tip,
                "start_date": "",
                "end_date": "",
                "来源": "QMT细回测",
            }
        )
    return rows
