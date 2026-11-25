# -*- coding: utf-8 -*-
"""马总逻辑1开盘前即可确定的跳过检查。

覆盖两份买入策略（参数、窗口、已成交腿、除权函数各自按其 run()）：
- 弹性：strategy_mz1_ma1020_half（规则名「马总1-跌破MA10/MA20」）
- 单点：strategy_dacdcef1（规则名「马总1单点-跌破MA10/MA20」）
腿键均为 code:MA10 / code:MA20，与各自 run() 里 leg_key 一致。

与对应 strategy_code 的 run() 对下列条件的判断保持一致（不看今日盘中现价，
也不请求 MiniQMT / xtdata）：

1. 已出买入窗口（选股日当天不买，从下一交易日起连续
   entry_window_trading_days，默认 10；未开窗今天同样不会挂，
   但股票池「检查」不因此勾选，避免被当成可以删除）
2. 近 ex_div_lookback（默认 20）交易日疑似除权 → 整只不再挂
3. 该腿已买过（params._filled_legs 与 data/filled_legs.json 合并，
   与生成策略注入 _filled_legs 的来源一致；不连 QMT 做持仓对账）
4. 选股日之后、今天之前，窗口内日线已出现最低价跌破早盘均线
   （utils.first_ma_touch.already_touched_ma_in_entry_window）

「今天」与生成时一致：无 backtest_trade_date 时用 date.today()。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# 与弹性版 / 单点版 run() 中 LEG_SPECS 一致（leg_id 同为 MA10/MA20，规则名不同）
LEG_SPECS = (
    ("MA10", "10日", "马总1-跌破MA10", 10),
    ("MA20", "20日", "马总1-跌破MA20", 20),
)
SINGLE_LEG_SPECS = (
    ("MA10", "10日", "马总1单点-跌破MA10", 10),
    ("MA20", "20日", "马总1单点-跌破MA20", 20),
)

MZ1_STRATEGY_ID = "strategy_mz1_ma1020_half"
MZ1_STRATEGY_NAME = "买：马总逻辑1-涨停后跌破MA10/20各1/2"
MZ1_SINGLE_STRATEGY_ID = "strategy_dacdcef1"
MZ1_SINGLE_STRATEGY_NAME = "买：马总逻辑1-涨停后跌破MA10/20各1/2-单点"


def is_mz1_single_buy_strategy(
    *,
    strategy_id: Any = "",
    name: Any = "",
    strategy_code: Any = "",
    params: Any = None,
) -> bool:
    """是否为马总逻辑1「单点」买入策略。"""
    _ = params
    sid = str(strategy_id or "").strip()
    if sid == MZ1_SINGLE_STRATEGY_ID:
        return True
    nm = str(name or "").strip()
    if nm == MZ1_SINGLE_STRATEGY_NAME:
        return True
    if "马总逻辑1" in nm and "跌破MA10" in nm and "MA20" in nm and "单点" in nm:
        return True
    code = str(strategy_code or "")
    if "马总1单点-跌破MA10" in code and "马总1单点-跌破MA20" in code:
        return True
    return False


def is_mz1_elastic_buy_strategy(
    *,
    strategy_id: Any = "",
    name: Any = "",
    strategy_code: Any = "",
    params: Any = None,
) -> bool:
    """是否为马总逻辑1「弹性 / best_buy」买入策略（不含单点）。"""
    if is_mz1_single_buy_strategy(
        strategy_id=strategy_id, name=name, strategy_code=strategy_code, params=params
    ):
        return False
    sid = str(strategy_id or "").strip()
    if sid == MZ1_STRATEGY_ID:
        return True
    nm = str(name or "").strip()
    if nm == MZ1_STRATEGY_NAME:
        return True
    if "马总逻辑1" in nm and "跌破MA10" in nm and "MA20" in nm:
        return True
    code = str(strategy_code or "")
    if "马总1-跌破MA10" in code and "马总1-跌破MA20" in code:
        return True
    return False


def is_mz1_buy_strategy(
    *,
    strategy_id: Any = "",
    name: Any = "",
    strategy_code: Any = "",
    params: Any = None,
) -> bool:
    """弹性版或单点版：股票池「检查」都可按各自 run() 的开盘前条件核对。"""
    return is_mz1_single_buy_strategy(
        strategy_id=strategy_id, name=name, strategy_code=strategy_code, params=params
    ) or is_mz1_elastic_buy_strategy(
        strategy_id=strategy_id, name=name, strategy_code=strategy_code, params=params
    )


def resolve_mz1_pool_check_variant(
    *,
    strategy_id: Any = "",
    name: Any = "",
    strategy_code: Any = "",
    params: Any = None,
    variant: Any = None,
) -> str:
    """返回 'single' 或 'elastic'。显式 variant 优先；否则按策略 id/名称/代码判断。"""
    raw = str(variant or "").strip().lower()
    if raw in ("single", "elastic"):
        return raw
    if is_mz1_single_buy_strategy(
        strategy_id=strategy_id, name=name, strategy_code=strategy_code, params=params
    ):
        return "single"
    return "elastic"


def leg_specs_for_variant(variant: str):
    if str(variant or "").strip().lower() == "single":
        return SINGLE_LEG_SPECS
    return LEG_SPECS


def _parse_d(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _code6(c: Any) -> str:
    s = str(c or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _next_td(d0: date) -> date:
    """与 run()._next_td 相同：优先交易日历，否则跳过周末。"""
    try:
        from strategy_generator_app.trading_calendar import next_trading_day_after

        nd = next_trading_day_after(d0)
        if nd is not None:
            return nd
    except Exception:
        pass
    try:
        from trading_calendar import next_trading_day_after as _n2

        nd = _n2(d0)
        if nd is not None:
            return nd
    except Exception:
        pass
    d = d0 + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _nth_td(d0: date, n: int) -> date:
    """与 run()._nth_td 相同：d0 为第 1 个交易日，返回第 n 个（含 d0）。"""
    if n <= 1:
        return d0
    try:
        from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted

        lst = get_trading_dates_in_range_sorted(d0, d0 + timedelta(days=400))
        if lst and len(lst) >= n:
            return lst[n - 1]
    except Exception:
        pass
    try:
        from trading_calendar import get_trading_dates_in_range_sorted as _g2

        lst = _g2(d0, d0 + timedelta(days=400))
        if lst and len(lst) >= n:
            return lst[n - 1]
    except Exception:
        pass
    d = d0
    counted = 0
    while counted < n:
        if d.weekday() < 5:
            counted += 1
            if counted == n:
                return d
        d += timedelta(days=1)
    return d


def resolve_trade_date(params: Optional[Dict[str, Any]] = None) -> date:
    """与 run() 一致：有 backtest_trade_date 用它，否则 date.today()。"""
    trade_d = _parse_d((params or {}).get("backtest_trade_date"))
    if trade_d is None:
        trade_d = date.today()
    return trade_d


def merge_filled_leg_keys(
    params: Optional[Dict[str, Any]] = None,
    project_root: Optional[str] = None,
) -> Set[str]:
    """合并策略 params._filled_legs 与 data/filled_legs.json。

    与生成策略时注入 params._filled_legs 的来源一致；
    不查询 QMT 持仓，因此不做「可用<100 则清腿」对账。
    """
    disk_legs: Set[str] = set()
    try:
        from utils.filled_legs import load_leg_keys

        disk_legs = set(load_leg_keys(project_root) or [])
    except Exception:
        try:
            from filled_legs import load_leg_keys as _lk  # type: ignore

            disk_legs = set(_lk(project_root) or [])
        except Exception:
            disk_legs = set()
    cur = (params or {}).get("_filled_legs") or []
    if isinstance(cur, dict):
        merged = set(str(k) for k, v in cur.items() if v) | disk_legs
    else:
        merged = set(str(x) for x in cur if x) | disk_legs
    return merged


def _entry_range_by_code(
    sel_map: Dict[str, Any],
    entry_window: int,
) -> Dict[str, Tuple[date, date]]:
    """预计算每只股票入场窗口，算法与 run() 相同。"""
    out: Dict[str, Tuple[date, date]] = {}
    if not sel_map:
        return out
    try:
        from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted
    except Exception:
        try:
            from trading_calendar import get_trading_dates_in_range_sorted
        except Exception:
            get_trading_dates_in_range_sorted = None
    cal_list = None
    if get_trading_dates_in_range_sorted is not None:
        try:
            parsed_dates = [_parse_d(v) for v in sel_map.values()]
            parsed_dates = [d for d in parsed_dates if d is not None]
            if parsed_dates:
                dmin = min(parsed_dates)
                dmax = max(parsed_dates)
                cal_list = get_trading_dates_in_range_sorted(
                    dmin, dmax + timedelta(days=400)
                )
        except Exception:
            cal_list = None
    for raw_k, raw_v in sel_map.items():
        c6k = _code6(raw_k)
        sd = _parse_d(raw_v)
        if not c6k or sd is None:
            continue
        if cal_list:
            after = [d for d in cal_list if d > sd]
            if not after:
                continue
            start_d = after[0]
            end_d = after[entry_window - 1] if len(after) >= entry_window else after[-1]
        else:
            start_d = _next_td(sd)
            end_d = _nth_td(start_d, entry_window)
        out[c6k] = (start_d, end_d)
    return out


def _already_touched(
    c6: str,
    sel_d: Optional[date],
    start_d: Optional[date],
    end_d: Optional[date],
    ma_period: int,
    trade_d: date,
) -> Tuple[bool, Optional[date]]:
    """与 run()._already_touched 相同，额外返回首次触达日便于标注。"""
    if sel_d is None or start_d is None or end_d is None:
        return False, None
    try:
        from utils.first_ma_touch import already_touched_ma_in_entry_window

        hit, touch_d = already_touched_ma_in_entry_window(
            c6,
            selection_date=sel_d,
            before_date=trade_d,
            ma_period=int(ma_period),
            entry_start=start_d,
            entry_end=end_d,
        )
        return bool(hit), touch_d
    except Exception:
        try:
            from first_ma_touch import already_touched_ma_in_entry_window as _fn  # type: ignore

            hit, touch_d = _fn(
                c6,
                selection_date=sel_d,
                before_date=trade_d,
                ma_period=int(ma_period),
                entry_start=start_d,
                entry_end=end_d,
            )
            return bool(hit), touch_d
        except Exception:
            return False, None


def _parse_ex_div_day(reason: Any) -> Optional[date]:
    s = str(reason or "")
    key = "day="
    if key not in s:
        return None
    raw = s.split(key, 1)[1].strip().replace("gap=", " ").split()[0]
    return _parse_d(raw[:10])


def _has_ex_div(
    c6: str,
    name: str,
    trade_d: date,
    lookback: int,
    *,
    variant: str = "elastic",
) -> Tuple[bool, Optional[date]]:
    """弹性版用 has_ex_div_gap/find_ex_div_gap；单点版用 should_block_buy_ex_div。

    检查不拉行情，prices_row 不传，因此单点版第 2/3 条（行情昨收偏离、今开缺口）
    在此不会触发，只使用与弹性版相同的日线开盘缺口判定。
    """
    if str(variant or "").strip().lower() == "single":
        try:
            from utils.ex_div_gap import should_block_buy_ex_div
        except Exception:
            try:
                from ex_div_gap import should_block_buy_ex_div  # type: ignore
            except Exception:
                should_block_buy_ex_div = None
        if should_block_buy_ex_div is not None:
            try:
                blocked, reason = should_block_buy_ex_div(
                    c6,
                    stock_name=name,
                    through_date=trade_d,
                    lookback=lookback,
                    prices_row=None,
                )
                return bool(blocked), _parse_ex_div_day(reason)
            except Exception:
                pass
        # 回退到日线缺口，避免单点检查完全失效
    try:
        from utils.ex_div_gap import find_ex_div_gap, has_ex_div_gap
    except Exception:
        try:
            from ex_div_gap import find_ex_div_gap, has_ex_div_gap  # type: ignore
        except Exception:
            return False, None
    try:
        hit, _gap, gap_d = find_ex_div_gap(
            c6,
            stock_name=name,
            through_date=trade_d,
            lookback=lookback,
        )
        return bool(hit), gap_d
    except Exception:
        try:
            blocked = bool(
                has_ex_div_gap(
                    c6,
                    stock_name=name,
                    through_date=trade_d,
                    lookback=lookback,
                )
            )
            return blocked, None
        except Exception:
            return False, None


def _fmt_d(d: Optional[date]) -> str:
    return d.isoformat() if d else ""


def should_check_for_removal(row: Dict[str, Any]) -> bool:
    """是否勾选以便删除：确实不会再挂。

    未开窗只表示窗口开始前（含选股日当天）不买，开窗后仍可能挂，因此不勾选。
    已出窗口、疑似除权、两腿都已买过或窗口内日线已跌破，仍勾选。
    仅一腿不挂不勾选。此函数只服务于股票池「检查」勾选，不改变真实挂买逻辑。
    """
    if row.get("ex_div"):
        return True
    ws = str(row.get("window_status") or "")
    if ws == "ended":
        return True
    legs = row.get("legs") or {}
    specs = row.get("leg_specs") or LEG_SPECS
    if specs and all(
        bool((legs.get(leg_id) or {}).get("reasons"))
        for leg_id, *_ in specs
    ):
        return True
    return False


def format_row_label(row: Dict[str, Any]) -> str:
    """列表里展示的检查说明。"""
    parts: List[str] = []
    ws = str(row.get("window_status") or "")
    if ws == "ended":
        parts.append("整只出窗口")
    elif ws == "not_started":
        if should_check_for_removal(row):
            parts.append("未开窗")
        else:
            parts.append("今天不挂（未开窗），先不要删")
    if row.get("ex_div"):
        ed = _fmt_d(row.get("ex_div_date"))
        parts.append("疑似除权（%s）" % ed if ed else "疑似除权")
    legs = row.get("legs") or {}
    hangable: List[str] = []
    specs = row.get("leg_specs") or LEG_SPECS
    for leg_id, _, _, _ in specs:
        info = legs.get(leg_id) or {}
        reasons = list(info.get("reasons") or [])
        if "filled" in reasons:
            parts.append("%s 已买过" % leg_id)
        if "already_touched" in reasons:
            td = _fmt_d(info.get("touch_date"))
            if td:
                parts.append("%s 日线已跌破（%s）" % (leg_id, td))
            else:
                parts.append("%s 日线已跌破" % leg_id)
        if not info.get("skip"):
            hangable.append(leg_id)
    if row.get("skip_both"):
        return "；".join(parts) if parts else "两腿都不会挂"
    if hangable and parts:
        parts.append("、".join(hangable) + " 仍可挂")
        return "；".join(parts)
    if hangable and not parts:
        if ws == "no_sel":
            return "无选股日（按生成逻辑仍可挂）"
        return "两腿均可挂"
    return "；".join(parts) if parts else ""


def check_mz1_pool_preopen(
    codes: Sequence[Any],
    params: Optional[Dict[str, Any]] = None,
    *,
    project_root: Optional[str] = None,
    names: Optional[Dict[str, str]] = None,
    trade_date: Any = None,
    filled_keys: Optional[Iterable[str]] = None,
    strategy_id: Any = "",
    name: Any = "",
    strategy_code: Any = "",
    variant: Any = None,
) -> List[Dict[str, Any]]:
    """逐只、逐腿核对开盘前跳过条件。不拉盘中现价。

    返回列表，每项含 code/label/skip_both/window_status/legs 等。
    variant/strategy_* 用于选择弹性或单点的规则名与除权函数；
    腿键仍是 code:MA10 / code:MA20，已成交腿取该策略 params._filled_legs
    与 data/filled_legs.json 的合并（与生成注入来源一致）。
    """
    params = params or {}
    variant = resolve_mz1_pool_check_variant(
        strategy_id=strategy_id,
        name=name,
        strategy_code=strategy_code,
        params=params,
        variant=variant,
    )
    specs = leg_specs_for_variant(variant)
    try:
        entry_window = int(params.get("entry_window_trading_days", 10) or 10)
    except (TypeError, ValueError):
        entry_window = 10
    if entry_window < 1:
        entry_window = 1
    try:
        ex_lookback = int(params.get("ex_div_lookback", 20) or 20)
    except (TypeError, ValueError):
        ex_lookback = 20
    if ex_lookback < 1:
        ex_lookback = 20

    sel_map_raw = params.get("selection_date_by_code") or {}
    if not isinstance(sel_map_raw, dict):
        sel_map_raw = {}
    sel_map: Dict[str, Any] = {}
    for k, v in sel_map_raw.items():
        c6 = _code6(k)
        if c6:
            sel_map[c6] = v

    trade_d = _parse_d(trade_date)
    if trade_d is None:
        trade_d = resolve_trade_date(params)

    if filled_keys is None:
        filled = merge_filled_leg_keys(params, project_root)
    else:
        filled = set(str(x) for x in filled_keys if x)

    name_map = names or {}
    entry_range = _entry_range_by_code(sel_map, entry_window)

    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for code in codes or []:
        c6 = _code6(code)
        if not c6 or c6 in seen:
            continue
        seen.add(c6)
        name = str(name_map.get(c6) or name_map.get(code) or "")
        sel_d = _parse_d(sel_map.get(c6) or sel_map_raw.get(code))
        win = entry_range.get(c6)
        start_d: Optional[date] = None
        end_d: Optional[date] = None
        window_status = "no_sel"
        window_skip = False
        if win is None:
            if sel_d is not None:
                start_d = _next_td(sel_d)
                end_d = _nth_td(start_d, entry_window)
            # 与 run() 相同：算不出窗口且无选股日时不因窗口跳过
        else:
            start_d, end_d = win
        if sel_d is not None and start_d is not None and end_d is not None:
            if trade_d < start_d:
                window_status = "not_started"
                window_skip = True
            elif trade_d > end_d:
                window_status = "ended"
                window_skip = True
            else:
                window_status = "open"

        ex_hit, ex_d = _has_ex_div(
            c6, name, trade_d, ex_lookback, variant=variant
        )

        legs: Dict[str, Dict[str, Any]] = {}
        for leg_id, _field, _rule_name, ma_period in specs:
            leg_key = "%s:%s" % (c6, leg_id)
            reasons: List[str] = []
            touch_d = None
            if leg_key in filled:
                reasons.append("filled")
            hit, touch_d = _already_touched(
                c6, sel_d, start_d, end_d, ma_period, trade_d
            )
            if hit:
                reasons.append("already_touched")
            # 整只跳过时两腿都不会挂（与 run() 在挂腿循环前 continue 一致）
            skip = bool(reasons) or window_skip or bool(ex_hit)
            legs[leg_id] = {
                "skip": skip,
                "reasons": reasons,
                "touch_date": touch_d,
                "leg_key": leg_key,
                "rule_name": _rule_name,
            }

        skip_both = bool(window_skip or ex_hit) or all(
            (legs.get(leg_id) or {}).get("skip") for leg_id, _, _, _ in specs
        )
        row: Dict[str, Any] = {
            "code": c6,
            "name": name,
            "variant": variant,
            "leg_specs": specs,
            "selection_date": _fmt_d(sel_d),
            "window_status": window_status,
            "window_start": _fmt_d(start_d),
            "window_end": _fmt_d(end_d),
            "trade_date": _fmt_d(trade_d),
            "ex_div": bool(ex_hit),
            "ex_div_date": ex_d,
            "legs": legs,
            "stock_skip": bool(window_skip or ex_hit),
            "skip_both": bool(skip_both),
        }
        # skip_both 仍表示今天不会挂（含未开窗）；check_select 才用于勾选删除
        row["check_select"] = should_check_for_removal(row)
        row["label"] = format_row_label(row)
        rows.append(row)
    return rows
