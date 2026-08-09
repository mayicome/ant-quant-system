# -*- coding: utf-8 -*-
"""选股日分时因子：读 data/ticks/{YYYYMMDD}/{code6}.parquet。

仅使用选股日当日分时（收盘后可知，次日开盘买入无未来函数）。
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

TradeDate = Union[date, datetime, str]

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TICKS_ROOT = _PROJECT_ROOT / "data" / "ticks"


def _code6(code: Any) -> str:
    s = str(code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _ymd(d: TradeDate) -> str:
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    s = str(d or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    raise ValueError("bad trade_date: %r" % (d,))


def tick_parquet_path(code: Any, trade_date: TradeDate) -> Path:
    return _TICKS_ROOT / _ymd(trade_date) / ("%s.parquet" % _code6(code))


def _hm(ts_ms: float) -> int:
    dt = datetime.fromtimestamp(float(ts_ms) / 1000.0)
    return int(dt.hour) * 100 + int(dt.minute)


def _price_at_or_before(times, prices, hm_target: int) -> Optional[float]:
    """times: hm ints; prices aligned. Last price with hm <= target."""
    best = None
    for hm, px in zip(times, prices):
        if hm <= hm_target:
            best = px
        elif hm > hm_target and best is not None:
            break
    return best


def compute_session_factors(
    code: Any, trade_date: TradeDate
) -> Optional[Dict[str, Any]]:
    """返回分时因子；无文件/无效返回 None。"""
    path = tick_parquet_path(code, trade_date)
    if not path.is_file() or path.stat().st_size < 32:
        return None
    try:
        import pandas as pd
    except ImportError:
        return None
    try:
        df = pd.read_parquet(path, columns=["time_ts", "lastPrice", "volume", "amount"])
    except Exception:
        try:
            df = pd.read_parquet(path)
        except Exception:
            return None
    if df is None or len(df) == 0:
        return None
    if "time_ts" not in df.columns or "lastPrice" not in df.columns:
        return None

    ts = pd.to_numeric(df["time_ts"], errors="coerce")
    px = pd.to_numeric(df["lastPrice"], errors="coerce")
    vol = pd.to_numeric(df["volume"], errors="coerce") if "volume" in df.columns else None
    amt = pd.to_numeric(df["amount"], errors="coerce") if "amount" in df.columns else None
    m = ts.notna() & px.notna() & (px > 0)
    if not bool(m.any()):
        return None
    ts = ts[m]
    px = px[m]
    hms = [_hm(float(x)) for x in ts.tolist()]
    prices = [float(x) for x in px.tolist()]
    if len(prices) < 10:
        return None

    open_px = prices[0]
    close_px = prices[-1]
    p_1000 = _price_at_or_before(hms, prices, 1000)
    p_1130 = _price_at_or_before(hms, prices, 1130)
    p_1300 = _price_at_or_before(hms, prices, 1300)
    p_1400 = _price_at_or_before(hms, prices, 1400)

    # VWAP：volume/amount 为累计字段
    vwap = None
    if vol is not None and amt is not None:
        vv = vol[m]
        aa = amt[m]
        v_last = float(vv.iloc[-1]) if len(vv) else 0.0
        a_last = float(aa.iloc[-1]) if len(aa) else 0.0
        if v_last > 0 and a_last > 0:
            # amount 元、volume 手 → 约价 = amount / (volume*100)
            vwap = a_last / (v_last * 100.0)

    am_vol_frac = None
    if vol is not None:
        vv = vol[m].tolist()
        v_end = float(vv[-1]) if vv else 0.0
        v_am = None
        for hm, v in zip(hms, vv):
            if hm <= 1130:
                v_am = float(v)
        if v_end > 0 and v_am is not None:
            am_vol_frac = v_am / v_end

    def _ret(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None or a <= 0:
            return None
        return b / a - 1.0

    day_ret = _ret(open_px, close_px)
    am_ret = _ret(open_px, p_1130) if p_1130 is not None else None
    pm_ret = _ret(p_1300, close_px) if p_1300 is not None else None
    late_ret = _ret(p_1400, close_px) if p_1400 is not None else None
    from_1000_ret = _ret(p_1000, close_px) if p_1000 is not None else None
    close_vs_vwap = _ret(vwap, close_px) if vwap is not None else None

    return {
        "分时_开盘价": round(open_px, 4),
        "分时_收盘价": round(close_px, 4),
        "分时_日收益": None if day_ret is None else round(day_ret, 6),
        "分时_早盘收益": None if am_ret is None else round(am_ret, 6),
        "分时_午后收益": None if pm_ret is None else round(pm_ret, 6),
        "分时_尾盘收益": None if late_ret is None else round(late_ret, 6),
        "分时_10点后收益": None if from_1000_ret is None else round(from_1000_ret, 6),
        "分时_收盘相对VWAP": None if close_vs_vwap is None else round(close_vs_vwap, 6),
        "分时_早盘量占比": None if am_vol_frac is None else round(am_vol_frac, 6),
        "分时_VWAP": None if vwap is None else round(vwap, 4),
        "分时_样本数": int(len(prices)),
        "分时_文件": str(path),
    }


def evaluate_tick_filters(
    factors: Optional[Dict[str, Any]],
    *,
    require_tick_data: bool = True,
    pm_ret_lo: float = -0.02,
    late_ret_lo: float = -0.015,
    close_vwap_lo: float = -0.01,
    require_pm_floor: bool = True,
    require_late_floor: bool = True,
    require_close_near_vwap: bool = True,
    reject_frontload_fade: bool = True,
    am_vol_frac_hi: float = 0.75,
) -> tuple[bool, str, Dict[str, Any]]:
    """返回 (通过?, skip原因, 因子字典)。"""
    if not factors:
        if require_tick_data:
            return False, "无选股日分时parquet", {}
        return True, "", {}

    out = dict(factors)
    pm = factors.get("分时_午后收益")
    late = factors.get("分时_尾盘收益")
    cv = factors.get("分时_收盘相对VWAP")
    amf = factors.get("分时_早盘量占比")

    if require_pm_floor:
        if pm is None:
            if require_tick_data:
                return False, "缺午后收益(无13:00锚点)", out
        elif float(pm) < float(pm_ret_lo):
            return False, "午后收益过弱(<%.1f%%)" % (pm_ret_lo * 100), out

    if require_late_floor:
        if late is None:
            if require_tick_data:
                return False, "缺尾盘收益(无14:00锚点)", out
        elif float(late) < float(late_ret_lo):
            return False, "尾盘收益过弱(<%.1f%%)" % (late_ret_lo * 100), out

    if require_close_near_vwap:
        if cv is None:
            if require_tick_data:
                return False, "缺收盘相对VWAP", out
        elif float(cv) < float(close_vwap_lo):
            return False, "收盘明显低于VWAP(<%.1f%%)" % (close_vwap_lo * 100), out

    if reject_frontload_fade:
        if amf is not None and pm is not None:
            if float(amf) >= float(am_vol_frac_hi) and float(pm) < 0:
                return False, "早盘量占比过高且午后收负", out

    return True, "", out
