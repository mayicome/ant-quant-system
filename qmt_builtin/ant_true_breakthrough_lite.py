#coding:gbk
"""真突破 tick 判定（精简版，无 PyQt 依赖）。"""
# docstring removed for QMT gbk loader

from typing import Any, Dict, List, Optional, Tuple

TRUE_BREAKTHROUGH_MIN_BREAK_VS_AVG = 1.25
TRUE_BREAKTHROUGH_MAX_ASK_OVER_BID = 0.6
TRUE_BREAKTHROUGH_ASK_BID_RATIO_TICK_WINDOW = 5
TRUE_BREAKTHROUGH_ASK_BID_RATIO_TICK_WINDOW_3 = 3
TRUE_BREAKTHROUGH_MIN_BREAK_VS_PREV_DEPTH = 2.0
#            tick    N     30s                    per-tick    
TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS = 10
TRUE_BREAKTHROUGH_COND1_MODE_TICK3 = "tick3"
TRUE_BREAKTHROUGH_COND1_MODE_WINDOW = "window"

#      CSV /                        
TRUE_BREAKTHROUGH_EXPORT_FIELDS: Tuple[str, ...] = (
    "\u771f\u7a81\u7834\u2460\u91cf\u5747\u91cf\u6bd4",
    "\u771f\u7a81\u7834\u2460\u901a\u8fc7",
    "\u771f\u7a81\u7834\u2461\u59d4\u5356\u59d4\u4e70\u6bd4",
    "\u771f\u7a81\u7834\u2461\u901a\u8fc7",
    "\u771f\u7a81\u7834\u2462\u91cf\u88ab\u5403\u5356\u6863\u6bd4",
    "\u771f\u7a81\u7834\u2462\u901a\u8fc7",
    "\u771f\u7a81\u7834\u2462\u88ab\u5403\u6863\u6570",
)


def _tick_scalar_to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        if hasattr(x, "item"):
            x = x.item()
        if isinstance(x, (list, tuple)) and len(x) > 0:
            return _tick_scalar_to_float(x[0])
        v = float(x)
        if v != v:  # nan
            return None
        return v
    except (TypeError, ValueError):
        return None


def round_price_like_display(stock_code: str, p: float) -> float:
    code = (stock_code or "").strip()
    try:
        from core.utils.security_type import SecurityTypeUtil

        return float(SecurityTypeUtil.round_price(code, float(p)))
    except Exception:
        base = code.split(".")[0] if "." in code else code
        prec = 3 if (len(base) == 6 and base.startswith("5")) else 2
        return round(float(p), prec)


def infer_tick_vol_to_shares_multiplier(raw_df) -> float:
#  intelligentbuy amount/volume/lastPrice 
    if raw_df is None or getattr(raw_df, "empty", True):
        return 100.0
    need_cols = {"amount", "volume", "lastPrice"}
    cols = set(str(c) for c in list(getattr(raw_df, "columns", [])))
    if not need_cols.issubset(cols):
        return 100.0
    try:
        probe = raw_df.tail(400)
        samples: List[float] = []
        prev_amt = None
        prev_vol = None
        for _, row in probe.iterrows():
            amt = _tick_scalar_to_float(row.get("amount"))
            vol = _tick_scalar_to_float(row.get("volume"))
            px = _tick_scalar_to_float(row.get("lastPrice"))
            if amt is None or vol is None or px is None or px <= 0:
                prev_amt, prev_vol = amt, vol
                continue
            if prev_amt is None or prev_vol is None:
                prev_amt, prev_vol = amt, vol
                continue
            d_amt = amt - prev_amt
            d_vol = vol - prev_vol
            prev_amt, prev_vol = amt, vol
            if d_amt <= 0 or d_vol <= 0:
                continue
            ratio = (d_amt / d_vol) / px
            if ratio > 0:
                samples.append(ratio)
        if not samples:
            return 100.0
        samples.sort()
        mid = samples[len(samples) // 2]
        return 1.0 if abs(mid - 1.0) <= abs(mid - 100.0) else 100.0
    except Exception:
        return 100.0


def _row_ask_price_vol_pairs(row: Any, vol_to_shares: float) -> List[Tuple[float, float]]:
    pairs: List[Tuple[float, float]] = []
    mul = float(vol_to_shares or 100.0)

    def add_px_vol(px: Optional[float], vol: Optional[float]) -> None:
        if px is None or vol is None or px <= 0 or vol < 0:
            return
        pairs.append((float(px), float(vol) * mul))

    ap = row.get("askPrice") if isinstance(row, dict) else None
    av = row.get("askVol") if isinstance(row, dict) else None
    if isinstance(ap, (list, tuple)) and isinstance(av, (list, tuple)):
        n = min(5, len(ap), len(av))
        for i in range(n):
            add_px_vol(_tick_scalar_to_float(ap[i]), _tick_scalar_to_float(av[i]))
        if pairs:
            return pairs
    for i in range(1, 6):
        px = None
        vol = None
        for pn in (f"askPrice{i}", f"ask{i}", f"sellPrice{i}", f"a{i}_p"):
            if pn in row:
                px = _tick_scalar_to_float(row.get(pn))
                break
        for vn in (f"askVol{i}", f"askVolume{i}", f"sellVol{i}", f"a{i}_v", f"ask_size{i}"):
            if vn in row:
                vol = _tick_scalar_to_float(row.get(vn))
                break
        add_px_vol(px, vol)
    return pairs


def _row_bid_price_vol_pairs(row: Any, vol_to_shares: float) -> List[Tuple[float, float]]:
    pairs: List[Tuple[float, float]] = []
    mul = float(vol_to_shares or 100.0)

    def add_px_vol(px: Optional[float], vol: Optional[float]) -> None:
        if px is None or vol is None or px <= 0 or vol < 0:
            return
        pairs.append((float(px), float(vol) * mul))

    bp = row.get("bidPrice") if isinstance(row, dict) else None
    bv = row.get("bidVol") if isinstance(row, dict) else None
    if isinstance(bp, (list, tuple)) and isinstance(bv, (list, tuple)):
        n = min(5, len(bp), len(bv))
        for i in range(n):
            add_px_vol(_tick_scalar_to_float(bp[i]), _tick_scalar_to_float(bv[i]))
        if pairs:
            return pairs
    for i in range(1, 6):
        px = None
        vol = None
        for pn in (f"bidPrice{i}", f"bid{i}", f"buyPrice{i}", f"b{i}_p"):
            if pn in row:
                px = _tick_scalar_to_float(row.get(pn))
                break
        for vn in (f"bidVol{i}", f"bidVolume{i}", f"buyVol{i}", f"b{i}_v", f"bid_size{i}"):
            if vn in row:
                vol = _tick_scalar_to_float(row.get(vn))
                break
        add_px_vol(px, vol)
    return pairs


def _depth_vol_sum_bid_1_to_5(row: Any, vol_mul: float) -> float:
    return sum(v for _, v in _row_bid_price_vol_pairs(row, vol_mul))


def _depth_vol_sum_ask_1_to_5(row: Any, vol_mul: float) -> float:
    return sum(v for _, v in _row_ask_price_vol_pairs(row, vol_mul))


def depth_ask_over_bid_ratio_for_row(row: Any) -> Optional[float]:
    b_dep = max(0.0, float(_depth_vol_sum_bid_1_to_5(row, 1.0)))
    a_dep = max(0.0, float(_depth_vol_sum_ask_1_to_5(row, 1.0)))
    if b_dep <= 1e-9:
        return None
    return float(a_dep) / float(b_dep)


def avg_depth_ask_over_bid_ratio_for_rows(
    rows: List[Dict[str, Any]],
    window: int = TRUE_BREAKTHROUGH_ASK_BID_RATIO_TICK_WINDOW,
) -> Tuple[Optional[float], int]:
    if not rows:
        return None, 0
    win = max(1, int(window or 1))
    slice_rows = list(rows)[-win:]
    ratios: List[float] = []
    for row in slice_rows:
        r = depth_ask_over_bid_ratio_for_row(row)
        if r is not None:
            ratios.append(float(r))
    if not ratios:
        return None, 0
    return sum(ratios) / float(len(ratios)), len(ratios)


def min_cond2_depth_ask_over_bid_ratio_for_rows(
    rows: List[Dict[str, Any]],
) -> Tuple[Optional[float], int]:
#  tick3tick5tick /
    if not rows:
        return None, 0
    candidates: List[float] = []
    r0 = depth_ask_over_bid_ratio_for_row(rows[-1])
    if r0 is not None:
        candidates.append(float(r0))
    r3, _ = avg_depth_ask_over_bid_ratio_for_rows(
        rows, TRUE_BREAKTHROUGH_ASK_BID_RATIO_TICK_WINDOW_3
    )
    if r3 is not None:
        candidates.append(float(r3))
    r5, _ = avg_depth_ask_over_bid_ratio_for_rows(
        rows, TRUE_BREAKTHROUGH_ASK_BID_RATIO_TICK_WINDOW
    )
    if r5 is not None:
        candidates.append(float(r5))
    if not candidates:
        return None, 0
    return min(candidates), len(candidates)


def _prev_row_asks_sorted_merged_asc(row: Any, vol_mul: float) -> List[Tuple[float, float]]:
    raw = _row_ask_price_vol_pairs(row, vol_mul)
    if not raw:
        return []
    raw.sort(key=lambda x: float(x[0]))
    out: List[List[float]] = []
    for px, sh in raw:
        p = float(px)
        v = float(sh)
        if p <= 0 or v < 0:
            continue
        if not out or abs(out[-1][0] - p) > 1e-9:
            out.append([p, v])
        else:
            out[-1][1] += v
    return [(float(a[0]), float(a[1])) for a in out]


def _tick_trade_price_primary(row: Any) -> Optional[float]:
    for k in ("lastPrice", "tradePrice", "matchPrice", "price", "last"):
        v = _tick_scalar_to_float(row.get(k)) if isinstance(row, dict) else None
        if v is not None and float(v) > 0:
            return float(v)
    return None


def break_tick_eaten_prev_ask_depth_sum_shares(
    stock_code: str,
    prev_row: Any,
    break_row: Any,
    vol_mul: float,
) -> Tuple[Optional[int], Optional[float]]:
    merged = _prev_row_asks_sorted_merged_asc(prev_row, vol_mul)
    if not merged:
        return None, None
    trade_px = _tick_trade_price_primary(break_row)
    if trade_px is None or float(trade_px) <= 0:
        trade_px = _tick_scalar_to_float(break_row.get("lastPrice")) if isinstance(break_row, dict) else None
    if trade_px is None or float(trade_px) <= 0:
        return None, None
    code = (stock_code or "").strip()
    r_trade = round_price_like_display(code, float(trade_px))
    total = 0.0
    n = 0
    for px, sh in merged:
        r_px = round_price_like_display(code, float(px))
        if r_trade + 1e-12 >= r_px:
            n += 1
            total += float(sh)
        else:
            break
    if n <= 0:
        return 0, None
    return n, float(total)


def choose_incremental_volume_column(rows_list: List[Any]) -> Optional[str]:
#  intelligentbuy._per_tick_trade_volumes tick 
    inc_names = (
        "lastVol",
        "tradeVol",
        "tradeVolume",
        "tickVol",
        "singleVol",
        "matchQty",
        "qty",
        "volume_delta",
    )
    n = len(rows_list)
    if n == 0:
        return None
    best_name: Optional[str] = None
    best_cnt = 0
    for name in inc_names:
        cnt = sum(
            1
            for r in rows_list
            if _tick_scalar_to_float(r.get(name) if hasattr(r, "get") else None) is not None
        )
        if cnt > best_cnt:
            best_cnt = cnt
            best_name = name
    if best_name is not None and best_cnt >= max(1, (n + 1) // 2):
        return best_name
    return None


def per_tick_trade_volumes_list(rows_list: List[Any], vol_mul: float) -> List[Optional[float]]:
#  intelligentbuy 
    n = len(rows_list)
    if n == 0:
        return []
    inc_col = choose_incremental_volume_column(rows_list)
    if inc_col:
        out: List[Optional[float]] = []
        for r in rows_list:
            v = _tick_scalar_to_float(r.get(inc_col) if hasattr(r, "get") else None)
            if v is None or float(v) < 0:
                out.append(None)
            else:
                out.append(float(v))
        return out

    vm = float(vol_mul or 100.0)
    cum_names = ("volume", "cumVol", "totalVol", "cum_volume", "dealVol")
    out = []
    prev: Optional[float] = None
    for r in rows_list:
        cv: Optional[float] = None
        for cn in cum_names:
            cv = _tick_scalar_to_float(r.get(cn) if hasattr(r, "get") else None)
            if cv is not None:
                break
        if cv is None:
            out.append(None)
            continue
        fv = float(cv)
        if prev is None:
            out.append(max(0.0, fv) * vm)
            prev = fv
            continue
        d = fv - prev
        if d < -1e-3:
            out.append(max(0.0, fv) * vm)
            prev = fv
        else:
            out.append(max(0.0, d) * vm)
            prev = fv
    return out


def normalize_true_breakthrough_cond1_mode(mode: Any) -> str:
# tick3=+2window=+10/
    s = str(mode or TRUE_BREAKTHROUGH_COND1_MODE_TICK3).strip().lower()
    if s in (
        TRUE_BREAKTHROUGH_COND1_MODE_WINDOW,
        "window10",
        "win10",
        "cond1_window",
    ):
        return TRUE_BREAKTHROUGH_COND1_MODE_WINDOW
    return TRUE_BREAKTHROUGH_COND1_MODE_TICK3


def max_cond1_breakthrough_volume_sh(
    per_vols: List[Optional[float]], idx: int
) -> Optional[float]:
#  tick23 
    if not per_vols or idx < 0 or idx >= len(per_vols):
        return None
    candidates: List[float] = []
    v0 = per_vols[idx]
    if v0 is not None:
        candidates.append(float(v0))
    if idx >= 1:
        v1 = per_vols[idx - 1]
        if v0 is not None and v1 is not None:
            candidates.append((float(v0) + float(v1)) / 2.0)
    if idx >= 2:
        v1 = per_vols[idx - 1]
        v2 = per_vols[idx - 2]
        if v0 is not None and v1 is not None and v2 is not None:
            candidates.append((float(v0) + float(v1) + float(v2)) / 3.0)
    return max(candidates) if candidates else None


def max_cond1_breakthrough_volume_window_sh(
    per_vols: List[Optional[float]],
    idx: int,
    lookback_prior: int = TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS,
) -> Optional[float]:
# docstring removed for QMT gbk loader
# docstring removed for QMT gbk loader
    if not per_vols or idx < 0 or idx >= len(per_vols):
        return None
    prior = max(0, int(lookback_prior or 0))
    start = max(0, idx - prior)
    window = [float(v) for v in per_vols[start : idx + 1] if v is not None]
    if not window:
        return None
    max_single = max(window)
    mean_vol = sum(window) / float(len(window))
    return max(max_single, mean_vol)


def resolve_cond1_breakthrough_volume_sh(
    per_vols: Optional[List[Optional[float]]],
    idx: int,
    recent_vols: Optional[List[Optional[float]]] = None,
    current_vol: Optional[float] = None,
    cond1_mode: Any = TRUE_BREAKTHROUGH_COND1_MODE_TICK3,
    lookback_prior: Optional[int] = None,
) -> Optional[float]:
    mode = normalize_true_breakthrough_cond1_mode(cond1_mode)
    prior = (
        TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS
        if lookback_prior is None
        else max(0, int(lookback_prior))
    )
    if per_vols and 0 <= idx < len(per_vols):
        if mode == TRUE_BREAKTHROUGH_COND1_MODE_WINDOW:
            return max_cond1_breakthrough_volume_window_sh(
                per_vols, idx, lookback_prior=prior
            )
        return max_cond1_breakthrough_volume_sh(per_vols, idx)
    combined = list(recent_vols or [])
    if current_vol is not None:
        combined.append(current_vol)
    if not combined:
        return None
    idx2 = len(combined) - 1
    if mode == TRUE_BREAKTHROUGH_COND1_MODE_WINDOW:
        return max_cond1_breakthrough_volume_window_sh(
            combined, idx2, lookback_prior=prior
        )
    return max_cond1_breakthrough_volume_sh(combined, idx2)


def max_cond1_breakthrough_volume_from_recent(
    recent_vols: List[Optional[float]],
    current_vol: Optional[float],
    cond1_mode: Any = TRUE_BREAKTHROUGH_COND1_MODE_TICK3,
    lookback_prior: Optional[int] = None,
) -> Optional[float]:
    combined = list(recent_vols or []) + [current_vol]
    return resolve_cond1_breakthrough_volume_sh(
        combined,
        len(combined) - 1,
        cond1_mode=cond1_mode,
        lookback_prior=lookback_prior,
    )


def cond1_mode_description(
    cond1_mode: Any, lookback_prior: Optional[int] = None
) -> str:
    mode = normalize_true_breakthrough_cond1_mode(cond1_mode)
    if mode == TRUE_BREAKTHROUGH_COND1_MODE_WINDOW:
        prior = (
            TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS
            if lookback_prior is None
            else max(0, int(lookback_prior))
        )
        n = prior + 1
        return (
            f"\u8fd1{n}\u5e27(\u672c+\u524d{prior})\u6700\u5927\u5355\u7b14/"
            f"\u7a97\u53e3\u5747\u91cf\u53d6\u5927"
        )
    return "\u5355tick/\u8fd12\u5e27\u5747/\u8fd13\u5e27\u5747\u53d6\u5927"


def window_prior_ticks_from_seconds(window_sec: Any, tick_sec: float = 3.0) -> int:
    """时间窗 -> 条件① lookback_prior（不含本帧）；默认约 3 秒/tick。"""
    try:
        sec = float(window_sec)
    except (TypeError, ValueError):
        sec = 0.0
    if sec <= 0:
        return TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS
    ts = float(tick_sec) if tick_sec and tick_sec > 0 else 3.0
    total = max(1, int(round(sec / ts)))
    return max(1, total - 1)


def is_breakthrough_buy_price_cross_tick(
    stock_code: str,
    last_price: float,
    trigger_price: float,
    prev_last_price: Optional[float] = None,
) -> bool:
# docstring removed for QMT gbk loader
# docstring removed for QMT gbk loader
    lp = float(last_price or 0)
    trig = float(trigger_price or 0)
    if lp <= 0 or trig <= 0:
        return False
    code6 = (stock_code or "").strip().split(".")[0][:6]
    r_lp = round_price_like_display(code6, lp)
    r_trig = round_price_like_display(code6, trig)
    if r_lp <= r_trig:
        return False
    prev = prev_last_price
    if prev is None or float(prev) <= 0:
        return True
    r_prev = round_price_like_display(code6, float(prev))
    return r_prev <= r_trig and r_lp > r_trig


def is_breakthrough_break_below_trigger_tick(
    stock_code: str,
    last_price: float,
    trigger_price: float,
    prev_last_price: Optional[float] = None,
) -> bool:
# docstring removed for QMT gbk loader
# docstring removed for QMT gbk loader
    lp = float(last_price or 0)
    trig = float(trigger_price or 0)
    if lp <= 0 or trig <= 0:
        return False
    code6 = (stock_code or "").strip().split(".")[0][:6]
    r_lp = round_price_like_display(code6, lp)
    r_trig = round_price_like_display(code6, trig)
    if r_lp >= r_trig:
        return False
    prev = prev_last_price
    if prev is None or float(prev) <= 0:
        return False
    r_prev = round_price_like_display(code6, float(prev))
    return r_prev >= r_trig and r_lp < r_trig


def is_breakthrough_sell_price_cross_tick(
    stock_code: str,
    last_price: float,
    trigger_price: float,
    prev_last_price: Optional[float] = None,
) -> bool:
    """突破卖出：展示价首次下穿触发价。"""
    lp = float(last_price or 0)
    trig = float(trigger_price or 0)
    if lp <= 0 or trig <= 0:
        return False
    code6 = (stock_code or "").strip().split(".")[0][:6]
    r_lp = round_price_like_display(code6, lp)
    r_trig = round_price_like_display(code6, trig)
    if r_lp >= r_trig:
        return False
    prev = prev_last_price
    if prev is None or float(prev) <= 0:
        return True
    r_prev = round_price_like_display(code6, float(prev))
    return r_prev >= r_trig and r_lp < r_trig


def is_breakthrough_break_above_trigger_tick(
    stock_code: str,
    last_price: float,
    trigger_price: float,
    prev_last_price: Optional[float] = None,
) -> bool:
    """突破卖出前置：须先上破触发价，再下穿才允许卖。"""
    lp = float(last_price or 0)
    trig = float(trigger_price or 0)
    if lp <= 0 or trig <= 0:
        return False
    code6 = (stock_code or "").strip().split(".")[0][:6]
    r_lp = round_price_like_display(code6, lp)
    r_trig = round_price_like_display(code6, trig)
    if r_lp <= r_trig:
        return False
    prev = prev_last_price
    if prev is None or float(prev) <= 0:
        return False
    r_prev = round_price_like_display(code6, float(prev))
    return r_prev <= r_trig and r_lp > r_trig


def compute_true_breakthrough_tick_metrics(
    stock_code: str,
    break_row: Dict[str, Any],
    prev_row: Optional[Dict[str, Any]],
    vol_mul: float,
    avg_shares_before: Optional[float],
    v_break_sh: Optional[float],
    recent_rows: Optional[List[Dict[str, Any]]] = None,
    v_break_cond1: Optional[float] = None,
    recent_vols: Optional[List[Optional[float]]] = None,
    cond1_mode: Any = TRUE_BREAKTHROUGH_COND1_MODE_TICK3,
    lookback_prior: Optional[int] = None,
) -> Dict[str, Any]:
# /
    cond1_mode_norm = normalize_true_breakthrough_cond1_mode(cond1_mode)
    prior = (
        TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS
        if lookback_prior is None
        else max(0, int(lookback_prior))
    )
    metrics: Dict[str, Any] = {
        "cond1": False,
        "cond2": False,
        "cond3": False,
        "passed": False,
        "cond1_mode": cond1_mode_norm,
        "cond1_lookback_prior": prior,
        "v_break_cond1": v_break_cond1,
        "v_break_sh": v_break_sh,
        "avg_shares_before": avg_shares_before,
        "ratio_cond1": None,
        "ask_bid_ratio_cond2": None,
        "ask_bid_ratio_n": 0,
        "eaten_n": None,
        "prev_eaten_depth_shares": None,
        "ratio_cond3": None,
        "error": None,
    }
    if prev_row is None:
        metrics["error"] = "\u65e0\u524dtick\u76d8\u53e3"
        return metrics
    if v_break_sh is None:
        metrics["error"] = "\u65e0\u672c\u7b14\u6210\u4ea4\u91cf"
        return metrics

    if v_break_cond1 is None:
        v_break_cond1 = max_cond1_breakthrough_volume_from_recent(
            list(recent_vols or []),
            v_break_sh,
            cond1_mode=cond1_mode_norm,
            lookback_prior=prior,
        )
    metrics["v_break_cond1"] = v_break_cond1

    ratio_rows: List[Dict[str, Any]] = list(recent_rows or [])
    if not ratio_rows or ratio_rows[-1] is not break_row:
        ratio_rows = (ratio_rows + [break_row])[-TRUE_BREAKTHROUGH_ASK_BID_RATIO_TICK_WINDOW:]
    ask_bid_ratio_cond2, ask_bid_ratio_n = min_cond2_depth_ask_over_bid_ratio_for_rows(
        ratio_rows
    )
    eaten_n, eaten_sum = break_tick_eaten_prev_ask_depth_sum_shares(
        stock_code, prev_row, break_row, vol_mul
    )
    metrics["ask_bid_ratio_cond2"] = ask_bid_ratio_cond2
    metrics["ask_bid_ratio_n"] = int(ask_bid_ratio_n or 0)
    metrics["eaten_n"] = eaten_n
    metrics["prev_eaten_depth_shares"] = eaten_sum

    if (
        v_break_cond1 is not None
        and avg_shares_before is not None
        and float(avg_shares_before) > 1e-12
    ):
        metrics["ratio_cond1"] = float(v_break_cond1) / float(avg_shares_before)

    if (
        v_break_sh is not None
        and eaten_sum is not None
        and float(eaten_sum) > 1e-12
    ):
        metrics["ratio_cond3"] = float(v_break_sh) / float(eaten_sum)

    cond1 = (
        metrics["ratio_cond1"] is not None
        and float(metrics["ratio_cond1"])
        > float(TRUE_BREAKTHROUGH_MIN_BREAK_VS_AVG)
    )
    cond2 = (
        ask_bid_ratio_cond2 is not None
        and ask_bid_ratio_cond2 < float(TRUE_BREAKTHROUGH_MAX_ASK_OVER_BID)
    )
    cond3 = (
        metrics["ratio_cond3"] is not None
        and float(metrics["ratio_cond3"])
        > float(TRUE_BREAKTHROUGH_MIN_BREAK_VS_PREV_DEPTH)
    )
    metrics["cond1"] = bool(cond1)
    metrics["cond2"] = bool(cond2)
    metrics["cond3"] = bool(cond3)
    metrics["passed"] = bool(cond1 and (cond2 or cond3))
    return metrics


def format_true_breakthrough_conditions_detail(metrics: Dict[str, Any]) -> str:
# 
    if metrics.get("error"):
        return str(metrics["error"])

    parts: List[str] = []
    ratio1 = metrics.get("ratio_cond1")
    if ratio1 is not None:
        mark = "\u8fc7" if metrics.get("cond1") else "\u5426"
        parts.append(
            f"\u2460\u91cf/\u5747\u91cf={float(ratio1):.2f}\u500d(\u9700>{TRUE_BREAKTHROUGH_MIN_BREAK_VS_AVG}){mark}"
        )
    else:
        parts.append(
            f"\u2460\u91cf/\u5747\u91cf\u7f3a\u5931(\u9700>{TRUE_BREAKTHROUGH_MIN_BREAK_VS_AVG})\u5426"
        )

    ratio2 = metrics.get("ask_bid_ratio_cond2")
    n2 = int(metrics.get("ask_bid_ratio_n") or 0)
    if ratio2 is not None and n2 > 0:
        mark = "\u8fc7" if metrics.get("cond2") else "\u5426"
        parts.append(
            f"\u2461\u59d4\u5356/\u59d4\u4e70(\u53d6\u5c0f)={float(ratio2):.2f}(\u9700<{TRUE_BREAKTHROUGH_MAX_ASK_OVER_BID}){mark}"
        )
    else:
        parts.append(
            f"\u2461\u59d4\u5356/\u59d4\u4e70\u6bd4\u7f3a\u5931(\u9700<{TRUE_BREAKTHROUGH_MAX_ASK_OVER_BID})\u5426"
        )

    ratio3 = metrics.get("ratio_cond3")
    eaten_n = metrics.get("eaten_n")
    if ratio3 is not None and eaten_n:
        mark = "\u8fc7" if metrics.get("cond3") else "\u5426"
        parts.append(
            f"\u2462\u91cf/\u88ab\u5403\u5356{int(eaten_n)}\u6863={float(ratio3):.2f}\u500d"
            f"(\u9700>{TRUE_BREAKTHROUGH_MIN_BREAK_VS_PREV_DEPTH}){mark}"
        )
    else:
        parts.append(
            f"\u2462\u91cf/\u88ab\u5403\u5356\u6863\u7f3a\u5931(\u9700>{TRUE_BREAKTHROUGH_MIN_BREAK_VS_PREV_DEPTH})\u5426"
        )

    verdict = "\u771f\u7a81\u7834" if metrics.get("passed") else "\u975e\u771f\u7a81\u7834"
    return " ".join(parts) + f" \u2192 {verdict}"


def _true_breakthrough_fail_message(metrics: Dict[str, Any]) -> str:
    if metrics.get("error"):
        return str(metrics["error"])
    parts: List[str] = []
    v_break_cond1 = metrics.get("v_break_cond1")
    avg_shares_before = metrics.get("avg_shares_before")
    ask_bid_ratio_cond2 = metrics.get("ask_bid_ratio_cond2")
    ask_bid_ratio_n = int(metrics.get("ask_bid_ratio_n") or 0)
    if not metrics.get("cond1"):
        cond1_desc = cond1_mode_description(
            metrics.get("cond1_mode"),
            lookback_prior=metrics.get("cond1_lookback_prior"),
        )
        parts.append(
            f"\u2460\u91cf/\u5747\u91cf\u4e0d\u8db3(\u9700>{TRUE_BREAKTHROUGH_MIN_BREAK_VS_AVG}\u00d7,{cond1_desc})"
            if v_break_cond1 is not None
            and avg_shares_before
            and float(avg_shares_before) > 1e-12
            else "\u2460\u5747\u91cf\u6216\u7a81\u7834\u91cf\u7f3a\u5931"
        )
    if not metrics.get("cond2"):
        if ask_bid_ratio_cond2 is None or ask_bid_ratio_n <= 0:
            parts.append(
                f"\u2461\u65e0\u6709\u6548\u59d4\u5356/\u59d4\u4e70\u6bd4(\u53d6\u5c0f,\u9700<{TRUE_BREAKTHROUGH_MAX_ASK_OVER_BID})"
            )
        else:
            parts.append(
                f"\u2461\u59d4\u5356/\u59d4\u4e70(\u53d6\u5c0f)={float(ask_bid_ratio_cond2):.2f}"
                f"(\u9700<{TRUE_BREAKTHROUGH_MAX_ASK_OVER_BID})"
            )
    if not metrics.get("cond3"):
        parts.append(
            f"\u2462\u91cf/\u88ab\u5403\u5356\u6863\u5408\u8ba1\u2264{TRUE_BREAKTHROUGH_MIN_BREAK_VS_PREV_DEPTH}\u00d7\u6216\u76d8\u53e3\u4e0d\u8db3"
        )
    if metrics.get("cond1") and (not metrics.get("cond2")) and (not metrics.get("cond3")):
        parts.append("\u2460\u5df2\u6ee1\u8db3,\u4f46\u2461\u2462\u5747\u672a\u6ee1\u8db3(\u9700\u2461<0.6\u6216\u2462>2.0\u00d7)")
    return ";".join(parts)


def true_breakthrough_export_fields(metrics: Dict[str, Any]) -> Dict[str, str]:
#  metrics /

    def _fmt_ratio(v: Any, digits: int = 2) -> str:
        if v is None:
            return ""
        try:
            return f"{float(v):.{digits}f}"
        except (TypeError, ValueError):
            return ""

    eaten_n = metrics.get("eaten_n")
    eaten_s = ""
    if eaten_n is not None:
        try:
            eaten_s = str(int(eaten_n))
        except (TypeError, ValueError):
            eaten_s = str(eaten_n)

    return {
        "\u771f\u7a81\u7834\u2460\u91cf\u5747\u91cf\u6bd4": _fmt_ratio(metrics.get("ratio_cond1")),
        "\u771f\u7a81\u7834\u2460\u901a\u8fc7": "\u662f" if metrics.get("cond1") else "\u5426",
        "\u771f\u7a81\u7834\u2461\u59d4\u5356\u59d4\u4e70\u6bd4": _fmt_ratio(metrics.get("ask_bid_ratio_cond2")),
        "\u771f\u7a81\u7834\u2461\u901a\u8fc7": "\u662f" if metrics.get("cond2") else "\u5426",
        "\u771f\u7a81\u7834\u2462\u91cf\u88ab\u5403\u5356\u6863\u6bd4": _fmt_ratio(metrics.get("ratio_cond3")),
        "\u771f\u7a81\u7834\u2462\u901a\u8fc7": "\u662f" if metrics.get("cond3") else "\u5426",
        "\u771f\u7a81\u7834\u2462\u88ab\u5403\u6863\u6570": eaten_s,
    }


def evaluate_true_breakthrough_tick_with_detail(
    stock_code: str,
    break_row: Dict[str, Any],
    prev_row: Optional[Dict[str, Any]],
    vol_mul: float,
    avg_shares_before: Optional[float],
    v_break_sh: Optional[float],
    recent_rows: Optional[List[Dict[str, Any]]] = None,
    v_break_cond1: Optional[float] = None,
    recent_vols: Optional[List[Optional[float]]] = None,
    cond1_mode: Any = TRUE_BREAKTHROUGH_COND1_MODE_TICK3,
    lookback_prior: Optional[int] = None,
) -> Tuple[bool, str, str, Dict[str, Any]]:
#  metrics
    metrics = compute_true_breakthrough_tick_metrics(
        stock_code,
        break_row,
        prev_row,
        vol_mul,
        avg_shares_before,
        v_break_sh,
        recent_rows,
        v_break_cond1,
        recent_vols,
        cond1_mode=cond1_mode,
        lookback_prior=lookback_prior,
    )
    detail = format_true_breakthrough_conditions_detail(metrics)
    if metrics.get("error"):
        return False, str(metrics["error"]), detail, metrics
    if metrics.get("passed"):
        return True, "\u771f\u7a81\u7834", detail, metrics
    return False, _true_breakthrough_fail_message(metrics), detail, metrics


def evaluate_true_breakthrough_tick(
    stock_code: str,
    break_row: Dict[str, Any],
    prev_row: Optional[Dict[str, Any]],
    vol_mul: float,
    avg_shares_before: Optional[float],
    v_break_sh: Optional[float],
    recent_rows: Optional[List[Dict[str, Any]]] = None,
    v_break_cond1: Optional[float] = None,
    recent_vols: Optional[List[Optional[float]]] = None,
    cond1_mode: Any = TRUE_BREAKTHROUGH_COND1_MODE_TICK3,
) -> Tuple[bool, str]:
# docstring removed for QMT gbk loader
# docstring removed for QMT gbk loader
    ok, msg, _detail, _metrics = evaluate_true_breakthrough_tick_with_detail(
        stock_code,
        break_row,
        prev_row,
        vol_mul,
        avg_shares_before,
        v_break_sh,
        recent_rows,
        v_break_cond1,
        recent_vols,
        cond1_mode=cond1_mode,
    )
    return ok, msg
