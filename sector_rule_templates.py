#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块选股规则：自包含代码模板（不依赖 ctx['builtin_check'] 等引擎外部逻辑）。"""

from typing import Optional

# 嵌入选股规则代码中的涨跌停幅度函数（沪深主板 ST 自 2026-07-06 起 ±10%）
_EMBEDDED_LIMIT_RATIO_FUNC = '''
def _limit_ratio(stock_code, stock_name, as_of_date=None):
    from datetime import date as _date
    name = str(stock_name or "").upper()
    code = str(stock_code or "").strip()
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "920")):
        return 0.30
    if "ST" in name:
        ref = as_of_date if as_of_date is not None else _date.today()
        if not isinstance(ref, _date):
            try:
                ref = ref.date()
            except Exception:
                try:
                    ref = ref.to_pydatetime().date()
                except Exception:
                    ref = _date.today()
        if ref >= _date(2026, 7, 6):
            return 0.10
        return 0.05
    return 0.10
'''.strip()

_EMBEDDED_TRI_STATE_HELPER = '''
def _apply_bool_tri_state(flag, positive_ok):
    """True=须满足 positive_ok；False=须不满足；None=忽略此条件。"""
    if flag is None:
        return True
    if flag:
        return positive_ok
    return not positive_ok
'''.strip()

_TRI_STATE_HINT = "；None=忽略此条件"

_EMBEDDED_CONDITION_DIAG_HELPER = '''
def _condition_diag_true_false(flag):
    return "True" if flag else "False"


def _collect_limit_up_condition_diag(
    *,
    has_lower_shadow,
    prev_is_lu,
    boll_break,
    has_old_high,
    has_obvious_new_high,
    ma_support_held,
    has_prior_lu_in_l=None,
):
    """命中时写入 extra：各条件对该股的实际 True/False，与规则开关配置无关。"""
    diag = {
        "REQUIRE_LOWER_SHADOW": has_lower_shadow,
        "REJECT_PRIOR_LIMIT_UP": prev_is_lu,
        "REQUIRE_BOLL_BREAK": boll_break,
        "REQUIRE_OLD_HIGH": has_old_high,
        "REQUIRE_OBVIOUS_NEW_HIGH": has_obvious_new_high,
        "REQUIRE_MA_SUPPORT_AFTER": ma_support_held,
    }
    if has_prior_lu_in_l is not None:
        diag["REQUIRE_PRIOR_LU_IN_L"] = has_prior_lu_in_l
    return {k: _condition_diag_true_false(v) for k, v in diag.items()}
'''.strip()


def _mode12_rule_code(
    *,
    require_old_high: bool,
    reject_prior_limit_up: bool,
    require_obvious_new_high: bool = False,
    require_lower_shadow: bool = True,
    require_boll_break: bool = True,
    require_ma_support_after: bool = True,
    default_n: int = 3,
    default_m: int = 30,
    prior_l_limit_days: Optional[int] = None,
    prior_l_require_limit_up: bool = True,
) -> str:
    """模式一/二共用骨架：涨停窗口 + 旧高 + 涨停后新高/均线。"""
    l_param_line = ""
    if prior_l_limit_days is not None:
        l_param_line = (
            f"L = {prior_l_limit_days}  "
            f"# 锚定涨停日前回溯 L 个交易日（不含锚定日）\n"
            f"REQUIRE_PRIOR_LU_IN_L = {prior_l_require_limit_up!r}  "
            f"# True=L日内至少一个涨停；False=L日内全无涨停{_TRI_STATE_HINT}\n"
        )
    prior_l_check = ""
    if prior_l_limit_days is not None:
        prior_l_check = """
        l_win = int(L)
        if l_win > 0:
            prev_dates_all = sorted(before["date"].unique().tolist())
            if not prev_dates_all:
                continue
            lb_dates = (
                prev_dates_all[-l_win:]
                if len(prev_dates_all) >= l_win
                else prev_dates_all
            )
            has_prior_lu_in_l = False
            for d_chk in lb_dates:
                if _is_limit_up_on_day(dd, d_chk, stock_code, stock_name)[0]:
                    has_prior_lu_in_l = True
                    break
            if REQUIRE_PRIOR_LU_IN_L is not None:
                if REQUIRE_PRIOR_LU_IN_L:
                    if not has_prior_lu_in_l:
                        continue
                else:
                    if has_prior_lu_in_l:
                        continue
"""
    return f'''# 本规则独立参数（仅本条规则生效）
N = {default_n}  # 最近 N 个交易日窗口（末日本身不作涨停候选）
M = {default_m}  # 涨停前 M 个交易日旧高判断
{l_param_line}REQUIRE_OLD_HIGH = {require_old_high!r}  # True=须有旧高(模式二)；False=须无旧高(涨停后N日){_TRI_STATE_HINT}
REJECT_PRIOR_LIMIT_UP = {reject_prior_limit_up!r}  # True=排除二连板第二板；False/None=不限制
REQUIRE_OBVIOUS_NEW_HIGH = {require_obvious_new_high!r}  # True=须明显新高；False=须无明显新高{_TRI_STATE_HINT}
REQUIRE_LOWER_SHADOW = {require_lower_shadow!r}  # True=须下影线(low<前日高)；False=须跳空涨停(low>=前日高){_TRI_STATE_HINT}
REQUIRE_BOLL_BREAK = {require_boll_break!r}  # True=涨停价须>=布林上轨；False=须低于布林上轨{_TRI_STATE_HINT}
REQUIRE_MA_SUPPORT_AFTER = {require_ma_support_after!r}  # True=涨停后不破均线；False=须至少一日跌破均线{_TRI_STATE_HINT}


{_EMBEDDED_LIMIT_RATIO_FUNC}


{_EMBEDDED_TRI_STATE_HELPER}


{_EMBEDDED_CONDITION_DIAG_HELPER}


def _dates_up_to(daily_data, as_of_date):
    dd = daily_data.sort_values("date")
    if as_of_date is not None:
        dd = dd[dd["date"] <= as_of_date]
    return dd


def _recent_n_dates(daily_data, n, as_of_date):
    dd = _dates_up_to(daily_data, as_of_date)
    u = sorted(dd["date"].unique().tolist())
    if not u:
        return []
    return u[-n:] if len(u) >= n else u


def _is_limit_up_on_row(prev_close, close_price, limit_ratio):
    if prev_close is None or prev_close <= 0:
        return False, 0.0
    limit_up_price = round(float(prev_close) * (1.0 + limit_ratio), 2)
    price_diff = abs(float(close_price) - limit_up_price)
    inc = (float(close_price) - float(prev_close)) / float(prev_close)
    ok = (price_diff < 0.02) or (inc >= limit_ratio * 0.99)
    return ok, limit_up_price


def _is_limit_up_on_day(dd, trade_date, stock_code, stock_name):
    limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)
    sub = dd[dd["date"] == trade_date]
    if sub.empty:
        return False, 0.0
    row = sub.iloc[-1]
    prev = dd[dd["date"] < trade_date]
    if prev.empty:
        return False, 0.0
    prev_close = float(prev.iloc[-1]["close"])
    return _is_limit_up_on_row(prev_close, float(row["close"]), limit_ratio)


def _screen_limit_up(stock_code, stock_name, daily_data, as_of_date):
    window_dates = _recent_n_dates(daily_data, int(N), as_of_date)
    if not window_dates:
        return False, None, None, None

    dd = _dates_up_to(daily_data, as_of_date).copy()
    dd["MA5"] = dd["close"].rolling(window=5, min_periods=1).mean()
    dd["MA10"] = dd["close"].rolling(window=10, min_periods=1).mean()
    dd["MA20"] = dd["close"].rolling(window=20, min_periods=1).mean()
    dd["MA30"] = dd["close"].rolling(window=30, min_periods=1).mean()
    dd["MA60"] = dd["close"].rolling(window=60, min_periods=1).mean()
    dd["MA120"] = dd["close"].rolling(window=120, min_periods=1).mean()
    dd["STD20"] = dd["close"].rolling(window=20, min_periods=1).std()
    dd["BOLL_UPPER"] = dd["MA20"] + 2.0 * dd["STD20"]

    recent = dd[dd["date"].isin(set(window_dates))]
    if recent.empty:
        return False, None, None, None

    last_trading_date = recent["date"].max()
    m = int(M)
    nh_tol = 0.01

    for _, row in recent.sort_values("date").iterrows():
        trade_date = row["date"]
        if trade_date >= last_trading_date:
            continue

        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)

        prev = dd[dd["date"] < trade_date]
        if prev.empty:
            continue
        prev_row = prev.iloc[-1]
        prev_close = float(prev_row["close"])
        prev_high = float(prev_row["high"])

        is_lu, limit_up_price = _is_limit_up_on_row(prev_close, float(row["close"]), limit_ratio)
        if not is_lu:
            continue

        has_lower_shadow = float(row["low"]) < prev_high
        if not _apply_bool_tri_state(REQUIRE_LOWER_SHADOW, has_lower_shadow):
            continue

        prev_d = prev_row["date"]
        prev_is_lu, _ = _is_limit_up_on_day(dd, prev_d, stock_code, stock_name)
        if REJECT_PRIOR_LIMIT_UP is True and prev_is_lu:
            continue

        limit_day = dd[dd["date"] == trade_date]
        if limit_day.empty:
            continue
        boll_upper = float(limit_day["BOLL_UPPER"].iloc[0])
        boll_break = limit_up_price >= boll_upper - 0.01
        if not _apply_bool_tri_state(REQUIRE_BOLL_BREAK, boll_break):
            continue

        before = dd[dd["date"] < trade_date]
        has_old_high = False
        if not before.empty:
            prev_dates = sorted(before["date"].unique().tolist())
            win_dates = prev_dates[-m:] if len(prev_dates) > m else prev_dates
            win = before[before["date"].isin(win_dates)]
            max_close_before = float(win["close"].max())
            has_old_high = max_close_before >= limit_up_price - 0.01
        if REQUIRE_OLD_HIGH is not None:
            if before.empty:
                if REQUIRE_OLD_HIGH:
                    continue
            elif REQUIRE_OLD_HIGH:
                if not has_old_high:
                    continue
            else:
                if has_old_high:
                    continue

        has_prior_lu_in_l = None
{prior_l_check}
        after = dd[dd["date"] > trade_date]
        if after.empty:
            continue

        max_allowed = limit_up_price * (1.0 + limit_ratio * 0.5)
        max_high_after = float(after["close"].max())
        signal_date = None
        has_obvious_new_high = max_high_after > max_allowed + nh_tol

        if REQUIRE_OBVIOUS_NEW_HIGH is not None:
            if REQUIRE_OBVIOUS_NEW_HIGH:
                if not has_obvious_new_high:
                    continue
                nh = after.sort_values("date")
                nh = nh[nh["close"] > max_allowed + nh_tol]
                if nh.empty:
                    continue
                signal_date = nh.iloc[0]["date"]
            elif has_obvious_new_high:
                continue

        after_ma = after.copy()
        after_ma["MA_MIN_5_10"] = after_ma[["MA5", "MA10"]].min(axis=1, skipna=True)
        after_ma["MA_MIN_20_120"] = after_ma[["MA20", "MA30", "MA60", "MA120"]].min(axis=1, skipna=True)

        bad_short = after_ma["close"] < (after_ma["MA_MIN_5_10"] - 0.01)
        bad_long = after_ma["close"] < (after_ma["MA_MIN_20_120"] - 0.01)
        breaks_ma = bad_short | bad_long
        ma_support_held = not breaks_ma.any()
        if REQUIRE_MA_SUPPORT_AFTER is not None:
            if REQUIRE_MA_SUPPORT_AFTER:
                if bad_short.any():
                    continue
                if bad_long.any():
                    continue
            elif not breaks_ma.any():
                continue

        diag = _collect_limit_up_condition_diag(
            has_lower_shadow=has_lower_shadow,
            prev_is_lu=prev_is_lu,
            boll_break=boll_break,
            has_old_high=has_old_high,
            has_obvious_new_high=has_obvious_new_high,
            ma_support_held=ma_support_held,
            has_prior_lu_in_l=has_prior_lu_in_l,
        )
        return True, trade_date, signal_date, diag

    return False, None, None, None


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    ok, limit_up_date, signal_date, diag = _screen_limit_up(stock_code, stock_name, daily_data, as_of_date)
    extra = {{}}
    if limit_up_date is not None:
        extra["涨停日期"] = limit_up_date.strftime("%Y-%m-%d")
    if signal_date is not None:
        extra["信号日期"] = signal_date.strftime("%Y-%m-%d")
    if isinstance(diag, dict):
        extra.update(diag)
    return bool(ok), extra
'''


def rule_code_limit_up_after_n_days(default_n: int = 3, default_m: int = 30) -> str:
    """涨停后N日：涨停前 M 日无旧高，涨停后无明显新高且不破均线，排除二连板。"""
    return _mode12_rule_code(
        require_old_high=False,
        reject_prior_limit_up=True,
        require_obvious_new_high=False,
        default_n=default_n,
        default_m=default_m,
    )


def rule_code_limit_up_with_prior_l_in_l_days(
    default_n: int = 3,
    default_m: int = 30,
    default_l: int = 10,
    require_prior_lu_in_l: bool = True,
) -> str:
    """N天内涨停，且锚定涨停日前 L 日窗口内按 REQUIRE_PRIOR_LU_IN_L 要求有/无涨停；其余同 N天内涨停。"""
    return _mode12_rule_code(
        require_old_high=False,
        reject_prior_limit_up=True,
        require_obvious_new_high=False,
        default_n=default_n,
        default_m=default_m,
        prior_l_limit_days=default_l,
        prior_l_require_limit_up=require_prior_lu_in_l,
    )


def rule_code_limit_up_p_to_n_days_after(
    default_p: int = 1,
    default_n: int = 3,
    default_m: int = 30,
    default_l: int = 10,
    require_prior_lu_in_l: bool = False,
) -> str:
    """锚定最近涨停日，选股日须为涨停后第 P~N 个交易日；其余同 L 日前置涨停规则。"""
    return f'''# 本规则独立参数（仅本条规则生效）
P = {default_p}  # 选股日 = 锚定涨停后第 P 个交易日（含）；第1日=涨停后首个交易日
N = {default_n}  # 选股日 = 锚定涨停后第 N 个交易日（含）上限；须 P <= N
M = {default_m}  # 涨停前 M 个交易日旧高判断
L = {default_l}  # 锚定涨停日前回溯 L 个交易日（不含锚定日）
REQUIRE_PRIOR_LU_IN_L = {require_prior_lu_in_l!r}  # True=L日内至少一个涨停；False=L日内全无涨停{_TRI_STATE_HINT}
REQUIRE_OLD_HIGH = False  # True=须有旧高(模式二)；False=须无旧高(涨停后N日){_TRI_STATE_HINT}
REJECT_PRIOR_LIMIT_UP = True  # True=排除二连板第二板；False/None=不限制
REQUIRE_OBVIOUS_NEW_HIGH = False  # True=须明显新高；False=须无明显新高{_TRI_STATE_HINT}
REQUIRE_LOWER_SHADOW = True  # True=须下影线(low<前日高)；False=须跳空涨停(low>=前日高){_TRI_STATE_HINT}
REQUIRE_BOLL_BREAK = True  # True=涨停价须>=布林上轨；False=须低于布林上轨{_TRI_STATE_HINT}
REQUIRE_MA_SUPPORT_AFTER = True  # True=涨停后不破均线；False=须至少一日跌破均线{_TRI_STATE_HINT}


{_EMBEDDED_LIMIT_RATIO_FUNC}


{_EMBEDDED_TRI_STATE_HELPER}


{_EMBEDDED_CONDITION_DIAG_HELPER}


def _dates_up_to(daily_data, as_of_date):
    dd = daily_data.sort_values("date")
    if as_of_date is not None:
        dd = dd[dd["date"] <= as_of_date]
    return dd


def _is_limit_up_on_row(prev_close, close_price, limit_ratio):
    if prev_close is None or prev_close <= 0:
        return False, 0.0
    limit_up_price = round(float(prev_close) * (1.0 + limit_ratio), 2)
    price_diff = abs(float(close_price) - limit_up_price)
    inc = (float(close_price) - float(prev_close)) / float(prev_close)
    ok = (price_diff < 0.02) or (inc >= limit_ratio * 0.99)
    return ok, limit_up_price


def _is_limit_up_on_day(dd, trade_date, stock_code, stock_name):
    limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)
    sub = dd[dd["date"] == trade_date]
    if sub.empty:
        return False, 0.0
    row = sub.iloc[-1]
    prev = dd[dd["date"] < trade_date]
    if prev.empty:
        return False, 0.0
    prev_close = float(prev.iloc[-1]["close"])
    return _is_limit_up_on_row(prev_close, float(row["close"]), limit_ratio)


def _as_of_effective_date(all_dates, as_of_date):
    if not all_dates:
        return None
    if as_of_date is not None and as_of_date in all_dates:
        return as_of_date
    if as_of_date is None:
        return all_dates[-1]
    prior = [d for d in all_dates if d <= as_of_date]
    return prior[-1] if prior else None


def _post_lu_trading_offset(all_dates, trade_date, as_of_effective):
    try:
        idx_lu = all_dates.index(trade_date)
        idx_as = all_dates.index(as_of_effective)
    except ValueError:
        return None
    if idx_as <= idx_lu:
        return None
    return idx_as - idx_lu


def _screen_limit_up(stock_code, stock_name, daily_data, as_of_date):
    p_day = int(P)
    n_day = int(N)
    if p_day > n_day:
        return False, None, None, None

    dd = _dates_up_to(daily_data, as_of_date).copy()
    if dd.empty:
        return False, None, None, None

    dd["MA5"] = dd["close"].rolling(window=5, min_periods=1).mean()
    dd["MA10"] = dd["close"].rolling(window=10, min_periods=1).mean()
    dd["MA20"] = dd["close"].rolling(window=20, min_periods=1).mean()
    dd["MA30"] = dd["close"].rolling(window=30, min_periods=1).mean()
    dd["MA60"] = dd["close"].rolling(window=60, min_periods=1).mean()
    dd["MA120"] = dd["close"].rolling(window=120, min_periods=1).mean()
    dd["STD20"] = dd["close"].rolling(window=20, min_periods=1).std()
    dd["BOLL_UPPER"] = dd["MA20"] + 2.0 * dd["STD20"]

    all_dates = sorted(dd["date"].unique().tolist())
    as_of_eff = _as_of_effective_date(all_dates, as_of_date)
    if as_of_eff is None:
        return False, None, None, None

    idx_as = all_dates.index(as_of_eff)
    lookback = n_day + p_day + 5
    scan_start = max(0, idx_as - lookback)
    anchor_candidates = all_dates[scan_start:idx_as][::-1]

    m = int(M)
    nh_tol = 0.01

    for trade_date in anchor_candidates:
        offset = _post_lu_trading_offset(all_dates, trade_date, as_of_eff)
        if offset is None or offset < p_day or offset > n_day:
            continue

        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)

        row = dd[dd["date"] == trade_date].iloc[-1]
        prev = dd[dd["date"] < trade_date]
        if prev.empty:
            continue
        prev_row = prev.iloc[-1]
        prev_close = float(prev_row["close"])
        prev_high = float(prev_row["high"])

        is_lu, limit_up_price = _is_limit_up_on_row(prev_close, float(row["close"]), limit_ratio)
        if not is_lu:
            continue

        has_lower_shadow = float(row["low"]) < prev_high
        if not _apply_bool_tri_state(REQUIRE_LOWER_SHADOW, has_lower_shadow):
            continue

        prev_d = prev_row["date"]
        prev_is_lu, _ = _is_limit_up_on_day(dd, prev_d, stock_code, stock_name)
        if REJECT_PRIOR_LIMIT_UP is True and prev_is_lu:
            continue

        limit_day = dd[dd["date"] == trade_date]
        if limit_day.empty:
            continue
        boll_upper = float(limit_day["BOLL_UPPER"].iloc[0])
        boll_break = limit_up_price >= boll_upper - 0.01
        if not _apply_bool_tri_state(REQUIRE_BOLL_BREAK, boll_break):
            continue

        before = dd[dd["date"] < trade_date]
        has_old_high = False
        if not before.empty:
            prev_dates = sorted(before["date"].unique().tolist())
            win_dates = prev_dates[-m:] if len(prev_dates) > m else prev_dates
            win = before[before["date"].isin(win_dates)]
            max_close_before = float(win["close"].max())
            has_old_high = max_close_before >= limit_up_price - 0.01
        if REQUIRE_OLD_HIGH is not None:
            if before.empty:
                if REQUIRE_OLD_HIGH:
                    continue
            elif REQUIRE_OLD_HIGH:
                if not has_old_high:
                    continue
            else:
                if has_old_high:
                    continue

        has_prior_lu_in_l = None
        l_win = int(L)
        if l_win > 0:
            prev_dates_all = sorted(before["date"].unique().tolist())
            if not prev_dates_all:
                continue
            lb_dates = (
                prev_dates_all[-l_win:]
                if len(prev_dates_all) >= l_win
                else prev_dates_all
            )
            has_prior_lu_in_l = False
            for d_chk in lb_dates:
                if _is_limit_up_on_day(dd, d_chk, stock_code, stock_name)[0]:
                    has_prior_lu_in_l = True
                    break
            if REQUIRE_PRIOR_LU_IN_L is not None:
                if REQUIRE_PRIOR_LU_IN_L:
                    if not has_prior_lu_in_l:
                        continue
                else:
                    if has_prior_lu_in_l:
                        continue

        after = dd[(dd["date"] > trade_date) & (dd["date"] <= as_of_eff)]
        if after.empty:
            continue

        max_allowed = limit_up_price * (1.0 + limit_ratio * 0.5)
        max_high_after = float(after["close"].max())
        signal_date = None
        has_obvious_new_high = max_high_after > max_allowed + nh_tol

        if REQUIRE_OBVIOUS_NEW_HIGH is not None:
            if REQUIRE_OBVIOUS_NEW_HIGH:
                if not has_obvious_new_high:
                    continue
                nh = after.sort_values("date")
                nh = nh[nh["close"] > max_allowed + nh_tol]
                if nh.empty:
                    continue
                signal_date = nh.iloc[0]["date"]
            elif has_obvious_new_high:
                continue

        after_ma = after.copy()
        after_ma["MA_MIN_5_10"] = after_ma[["MA5", "MA10"]].min(axis=1, skipna=True)
        after_ma["MA_MIN_20_120"] = after_ma[["MA20", "MA30", "MA60", "MA120"]].min(axis=1, skipna=True)

        bad_short = after_ma["close"] < (after_ma["MA_MIN_5_10"] - 0.01)
        bad_long = after_ma["close"] < (after_ma["MA_MIN_20_120"] - 0.01)
        breaks_ma = bad_short | bad_long
        ma_support_held = not breaks_ma.any()
        if REQUIRE_MA_SUPPORT_AFTER is not None:
            if REQUIRE_MA_SUPPORT_AFTER:
                if bad_short.any():
                    continue
                if bad_long.any():
                    continue
            elif not breaks_ma.any():
                continue

        diag = _collect_limit_up_condition_diag(
            has_lower_shadow=has_lower_shadow,
            prev_is_lu=prev_is_lu,
            boll_break=boll_break,
            has_old_high=has_old_high,
            has_obvious_new_high=has_obvious_new_high,
            ma_support_held=ma_support_held,
            has_prior_lu_in_l=has_prior_lu_in_l,
        )
        return True, trade_date, signal_date, diag

    return False, None, None, None


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    ok, limit_up_date, signal_date, diag = _screen_limit_up(stock_code, stock_name, daily_data, as_of_date)
    extra = {{}}
    if limit_up_date is not None:
        extra["涨停日期"] = limit_up_date.strftime("%Y-%m-%d")
    if signal_date is not None:
        extra["信号日期"] = signal_date.strftime("%Y-%m-%d")
    if isinstance(diag, dict):
        extra.update(diag)
    return bool(ok), extra
'''


def rule_code_mode2_old_high(default_n: int = 3, default_m: int = 30) -> str:
    """模式二：与涨停后N日相同，但涨停前 M 日须有旧高。"""
    return _mode12_rule_code(
        require_old_high=True,
        reject_prior_limit_up=False,
        require_obvious_new_high=False,
        default_n=default_n,
        default_m=default_m,
    )


def rule_code_mode3_volume_reversal(
    default_n: int = 4, default_m: int = 100, default_l: int = 10
) -> str:
    """模式三：N 日内涨停 + 放量反包等条件。"""
    return f'''# 本规则独立参数（仅本条规则生效）
N = {default_n}
M = {default_m}
L = {default_l}
N_MODE3 = N
M_MODE3 = M
L_MODE3 = L


{_EMBEDDED_LIMIT_RATIO_FUNC}


def _dates_up_to(daily_data, as_of_date):
    dd = daily_data.sort_values("date")
    if as_of_date is not None:
        dd = dd[dd["date"] <= as_of_date]
    return dd


def _recent_n_dates(daily_data, n, as_of_date):
    dd = _dates_up_to(daily_data, as_of_date)
    u = sorted(dd["date"].unique().tolist())
    if not u:
        return []
    return u[-n:] if len(u) >= n else u


def _screen_mode3(stock_code, stock_name, daily_data, as_of_date):
    n, m, l = int(N), int(M), int(L)
    if m <= 0 or l <= 0:
        return False, None, None

    dd = _dates_up_to(daily_data, as_of_date)
    window = set(_recent_n_dates(daily_data, n, as_of_date))
    data_n = dd[dd["date"].isin(window)]
    if data_n.empty:
        return False, None, None

    all_dates = sorted(dd["date"].unique().tolist())

    for _, row in data_n.sort_values("date").iterrows():
        trade_date = row["date"]
        close_price = float(row["close"])

        prev = dd[dd["date"] < trade_date]
        if prev.empty:
            continue
        prev_row = prev.iloc[-1]
        prev_close = float(prev_row["close"])

        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)

        limit_up_price = round(prev_close * (1.0 + limit_ratio), 2)
        price_diff = abs(close_price - limit_up_price)
        inc = (close_price - prev_close) / prev_close if prev_close > 0 else 0.0
        if not ((price_diff < 0.02) or (inc >= limit_ratio * 0.99)):
            continue

        if trade_date not in all_dates:
            continue
        idx = all_dates.index(trade_date)

        if idx < 60:
            continue
        prev_60 = all_dates[idx - 60:idx]
        d60 = dd[dd["date"].isin(prev_60)]
        if len(d60) < 60:
            continue
        closes60 = d60["close"].tolist()[-60:]
        ma60 = sum(closes60) / 60.0
        if ma60 <= 0 or not (ma60 * 0.7 <= prev_close <= ma60 * 1.3):
            continue

        if idx < 30:
            continue
        prev_30 = all_dates[idx - 30:idx]
        d30 = dd[dd["date"].isin(prev_30)]
        if len(d30) < 30:
            continue
        closes30 = d30["close"].tolist()[-30:]
        ma30 = sum(closes30) / 30.0
        if ma30 <= 0 or not (ma30 * 0.7 <= prev_close <= ma30 * 1.3):
            continue

        if idx < l:
            continue
        prev_l_dates = all_dates[idx - l:idx]
        prev_l_data = dd[dd["date"].isin(prev_l_dates)]
        skip = False
        for _, pr in prev_l_data.iterrows():
            ptd = pr["date"]
            ppc = float(pr["close"])
            pprev = dd[dd["date"] < ptd]
            if pprev.empty:
                continue
            ppclose = float(pprev.iloc[-1]["close"])
            pl_ratio = _limit_ratio(stock_code, stock_name, ptd)
            plup = round(ppclose * (1.0 + pl_ratio), 2)
            pdiff = abs(ppc - plup)
            pinc = (ppc - ppclose) / ppclose if ppclose > 0 else 0.0
            if (pdiff < 0.02) or (pinc >= pl_ratio * 0.99):
                skip = True
                break
        if skip:
            continue

        if idx < m:
            continue
        prev_m_dates = all_dates[idx - m:idx]
        prev_m_data = dd[dd["date"].isin(prev_m_dates)]
        if prev_m_data.empty:
            continue
        if any(close_price < float(x) for x in prev_m_data["close"].tolist()):
            continue

        after = dd[dd["date"] > trade_date].sort_values("date")
        if after.empty:
            continue

        first = after.iloc[0]
        if float(first["close"]) >= float(first["open"]):
            continue

        negatives = [first]
        for i in range(1, len(after)):
            r2 = after.iloc[i]
            if float(r2["close"]) < float(r2["open"]):
                negatives.append(r2)
            else:
                break

        first_neg_open = float(negatives[0]["open"])
        neg_vol_sum = sum(float(x["volume"]) for x in negatives)
        last_neg_date = negatives[-1]["date"]

        first_pos = None
        for _, ar in after.iterrows():
            if ar["date"] <= last_neg_date:
                continue
            if float(ar["close"]) >= float(ar["open"]):
                first_pos = ar
                break
        if first_pos is None:
            continue

        if float(first_pos["close"]) > first_neg_open and float(first_pos["volume"]) > neg_vol_sum:
            return True, trade_date, first_pos["date"]

    return False, None, None


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    ok, limit_up_date, signal_date = _screen_mode3(stock_code, stock_name, daily_data, as_of_date)
    extra = {{}}
    if limit_up_date is not None:
        extra["涨停日期"] = limit_up_date.strftime("%Y-%m-%d")
    if signal_date is not None:
        extra["放量反包日期"] = signal_date.strftime("%Y-%m-%d")
    return bool(ok), extra
'''


def rule_code_main_inflow_tier(
    *,
    tier: str = "A",
    top_n: int = 20,
    require_ma5_gt_ma10: bool = True,
    min_inflow_pct: Optional[float] = None,
    require_strict_bull: Optional[bool] = None,
) -> str:
    """主力净流入占流通 A/B 档选股规则代码。

    依赖引擎注入 ctx['inflow_rank'] = {code6: {rank, pct, name}}。
    严多头使用编译时注入的 apply_strict_bull_requirement。
    """
    tier_u = str(tier or "A").upper()
    if tier_u == "B":
        if min_inflow_pct is None:
            min_inflow_pct = 2.0
        if require_strict_bull is None:
            require_strict_bull = True
    min_lit = "None" if min_inflow_pct is None else repr(float(min_inflow_pct))
    bull_lit = "None" if require_strict_bull is None else ("True" if require_strict_bull else "False")
    ma_lit = "True" if require_ma5_gt_ma10 else "False"
    return f'''# 主力净流入选股 · {tier_u}档
# 依赖引擎 ctx["inflow_rank"]（选股日净流入占流通% 全市场排名）
# A档：排名≤TOP_N 且 MA5>MA10
# B档：A档 + 净流入占流通≥MIN_INFLOW_PCT + 严多头
# 严多头：MA5>MA10 且 MA5>max(MA20,30,60[,120])；无 MA120 时忽略 120 日线
N = 5   # 仅供引擎日历预检；本规则不依赖涨停窗口
TOP_N = {int(top_n)}
REQUIRE_MA5_GT_MA10 = {ma_lit}
MIN_INFLOW_PCT = {min_lit}  # None=不限制占流通%；B档默认 2.0
REQUIRE_STRICT_BULL = {bull_lit}  # True=须严多头；False=须非；None=忽略


def _code6(stock_code):
    s = str(stock_code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _ma(closes, n):
    if closes is None or len(closes) < n:
        return None
    return float(sum(closes[-n:])) / float(n)


def _closes_through(daily_data, as_of_date):
    if daily_data is None:
        return None
    try:
        if len(daily_data) == 0:
            return None
    except Exception:
        return None
    rows = []
    for _, r in daily_data.iterrows():
        d = r.get("date")
        if d is None:
            continue
        try:
            if hasattr(d, "date") and not isinstance(d, type(as_of_date)):
                dd = d.date()
            else:
                dd = d
        except Exception:
            dd = d
        if as_of_date is not None and dd is not None and dd > as_of_date:
            continue
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:
            rows.append(c)
    return rows


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    code = _code6(stock_code)
    inflow_map = {{}}
    if isinstance(ctx, dict):
        inflow_map = ctx.get("inflow_rank") or {{}}
    info = inflow_map.get(code) if isinstance(inflow_map, dict) else None
    if not isinstance(info, dict):
        return False, {{"_skip": "不在当日净流入表"}}

    try:
        rank = int(info.get("rank"))
    except (TypeError, ValueError):
        return False, {{"_skip": "无排名"}}
    pct = info.get("pct")
    try:
        pct_f = float(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct_f = None

    if rank < 1 or rank > int(TOP_N):
        return False, {{
            "净流入排名": rank,
            "净流入占流通%": pct_f if pct_f is not None else "",
            "_skip": "排名超出TOP_N",
        }}

    if MIN_INFLOW_PCT is not None:
        if pct_f is None or pct_f < float(MIN_INFLOW_PCT):
            return False, {{
                "净流入排名": rank,
                "净流入占流通%": pct_f if pct_f is not None else "",
                "_skip": "占流通%不足",
            }}

    closes = _closes_through(daily_data, as_of_date)
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma30 = _ma(closes, 30)
    ma60 = _ma(closes, 60)
    ma120 = _ma(closes, 120)

    if REQUIRE_MA5_GT_MA10:
        if ma5 is None or ma10 is None or not (ma5 > ma10):
            return False, {{
                "净流入排名": rank,
                "净流入占流通%": pct_f if pct_f is not None else "",
                "MA5": ma5 if ma5 is not None else "",
                "MA10": ma10 if ma10 is not None else "",
                "_skip": "未满足MA5>MA10",
            }}

    is_strict_bull = None
    if REQUIRE_STRICT_BULL is not None:
        bull = apply_strict_bull_requirement(
            REQUIRE_STRICT_BULL, ma5, ma10, ma20, ma30, ma60, ma120
        )
        if bull is None:
            return False, {{
                "净流入排名": rank,
                "净流入占流通%": pct_f if pct_f is not None else "",
                "_skip": "均线不足无法判严多头",
            }}
        passed_bull, is_strict_bull = bull
        if not passed_bull:
            return False, {{
                "净流入排名": rank,
                "净流入占流通%": pct_f if pct_f is not None else "",
                "严多头": "True" if is_strict_bull else "False",
                "_skip": "未满足严多头开关",
            }}

    extra = {{
        "净流入排名": rank,
        "净流入占流通%": round(pct_f, 4) if pct_f is not None else "",
        "MA5": round(ma5, 4) if ma5 is not None else "",
        "MA10": round(ma10, 4) if ma10 is not None else "",
        "MA20": round(ma20, 4) if ma20 is not None else "",
        "MA30": round(ma30, 4) if ma30 is not None else "",
        "MA60": round(ma60, 4) if ma60 is not None else "",
        "MA120": round(ma120, 4) if ma120 is not None else "",
        "档位": "{tier_u}",
        "TOP_N": int(TOP_N),
        "REQUIRE_MA5_GT_MA10": "True" if REQUIRE_MA5_GT_MA10 else "False",
        "MIN_INFLOW_PCT": "" if MIN_INFLOW_PCT is None else float(MIN_INFLOW_PCT),
        "REQUIRE_STRICT_BULL": (
            "None" if REQUIRE_STRICT_BULL is None else ("True" if REQUIRE_STRICT_BULL else "False")
        ),
    }}
    if is_strict_bull is not None:
        extra["严多头"] = "True" if is_strict_bull else "False"
    return True, extra
'''


def rule_code_main_inflow_tier_a() -> str:
    return rule_code_main_inflow_tier(tier="A", top_n=20, require_ma5_gt_ma10=True)


def rule_code_main_inflow_tier_b() -> str:
    return rule_code_main_inflow_tier(
        tier="B",
        top_n=20,
        require_ma5_gt_ma10=True,
        min_inflow_pct=2.0,
        require_strict_bull=True,
    )


def rule_code_recent_limit_up_in_hot_theme(
    *,
    lookback_days: int = 10,
    top_n: int = 10,
) -> str:
    """近 N 日有涨停，且当日归属十大热门板块或十大热门概念。

    依赖引擎注入 ctx['hot_theme']（见 utils.hot_theme_selection_ctx）。
    """
    return f'''# 近{int(lookback_days)}日有涨停 + 当日十大热门板块或概念
# 依赖引擎 ctx["hot_theme"]（涨停日数据 sector_plate_stats / concept_stats 前 TOP_N + QMT 归属扩展）
LOOKBACK_DAYS = {int(lookback_days)}  # 近 N 个交易日（含选股日）内至少 1 次涨停
TOP_N = {int(top_n)}  # 与引擎加载的 top_n 对齐（仅展示）
N = LOOKBACK_DAYS  # 供引擎日历预检


{_EMBEDDED_LIMIT_RATIO_FUNC}


def _code6(stock_code):
    s = str(stock_code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _dates_up_to(daily_data, as_of_date):
    dd = daily_data.sort_values("date")
    if as_of_date is not None:
        dd = dd[dd["date"] <= as_of_date]
    return dd


def _is_limit_up_on_row(prev_close, close_price, limit_ratio):
    if prev_close is None or prev_close <= 0:
        return False, 0.0
    limit_up_price = round(float(prev_close) * (1.0 + limit_ratio), 2)
    price_diff = abs(float(close_price) - limit_up_price)
    inc = (float(close_price) - float(prev_close)) / float(prev_close)
    ok = (price_diff < 0.02) or (inc >= limit_ratio * 0.99)
    return ok, limit_up_price


def _find_recent_limit_ups(stock_code, stock_name, daily_data, as_of_date, lookback):
    """返回近 lookback 个交易日内的涨停日列表（新→旧）。"""
    if daily_data is None or getattr(daily_data, "empty", True):
        return []
    dd = _dates_up_to(daily_data, as_of_date)
    if dd.empty or "date" not in dd.columns or "close" not in dd.columns:
        return []
    dates = list(dd["date"].tolist())
    if not dates:
        return []
    window = dates[-int(lookback):] if len(dates) >= int(lookback) else dates
    found = []
    for trade_date in reversed(window):
        sub = dd[dd["date"] == trade_date]
        if sub.empty:
            continue
        prev = dd[dd["date"] < trade_date]
        if prev.empty:
            continue
        prev_close = float(prev.iloc[-1]["close"])
        close_price = float(sub.iloc[-1]["close"])
        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)
        ok, _lu_px = _is_limit_up_on_row(prev_close, close_price, limit_ratio)
        if ok:
            ds = trade_date.strftime("%Y-%m-%d") if hasattr(trade_date, "strftime") else str(trade_date)
            found.append(ds)
    return found


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    """近 LOOKBACK_DAYS 日有涨停，且归属当日十大热门板块或概念。"""
    ht = (ctx or {{}}).get("hot_theme") or {{}}
    if not isinstance(ht, dict) or not ht:
        return False, {{"_skip": "无热门题材上下文"}}

    union_codes = ht.get("union_codes") or set()
    code_hits = ht.get("code_hits") or {{}}
    c6 = _code6(stock_code)
    in_union = c6 in union_codes
    if not in_union:
        return False, {{
            "热门源日期": str(ht.get("source_date") or ""),
            "十大热门板块": ",".join(ht.get("hot_sectors") or []),
            "十大热门概念": ",".join(ht.get("hot_concepts") or []),
            "_skip": "不在十大热门板块/概念内",
        }}

    lu_dates = _find_recent_limit_ups(stock_code, stock_name, daily_data, as_of_date, LOOKBACK_DAYS)
    if not lu_dates:
        return False, {{
            "热门源日期": str(ht.get("source_date") or ""),
            "_skip": f"近{{LOOKBACK_DAYS}}日无涨停",
        }}

    hit = code_hits.get(c6) if isinstance(code_hits, dict) else None
    if not isinstance(hit, dict):
        hit = {{}}
    hot_secs = list(hit.get("sectors") or [])
    hot_cons = list(hit.get("concepts") or [])
    extra = {{
        "近涨停日": ",".join(lu_dates),
        "涨停次数": len(lu_dates),
        "命中热门板块": ",".join(hot_secs),
        "命中热门概念": ",".join(hot_cons),
        "在热门板块": "True" if hit.get("in_hot_sector") else "False",
        "在热门概念": "True" if hit.get("in_hot_concept") else "False",
        "热门源日期": str(ht.get("source_date") or ""),
        "十大热门板块": ",".join(ht.get("hot_sectors") or []),
        "十大热门概念": ",".join(ht.get("hot_concepts") or []),
        "LOOKBACK_DAYS": int(LOOKBACK_DAYS),
        "TOP_N": int(TOP_N),
    }}
    return True, extra
'''


def rule_code_em_continuous_hot_rs_top20(
    *,
    top_n: int = 50,
    rs_top_k: int = 20,
    rs_lookback: int = 10,
    min_members: int = 30,
) -> str:
    """东财连续2日合格 TopN 热门板块/概念 + 组内近 lookback 日 RS 前 rs_top_k。

    合格 TopN：按涨跌幅排名遍历全日榜，跳过成分股数 < min_members 的标签，取满 top_n；
    连续热门 = D∩D-1 合格 TopN 名称。依赖引擎注入 ctx['em_board_hot']。
    命中时额外输出当日收盘后 MA5/MA10，以及近 RS_LOOKBACK 日涨停次数/最近涨停距今交易日数。
    """
    return f'''# 东财热门：连续2日合格 Top{int(top_n)}（成分≥{int(min_members)}）板块或概念 + 组内近{int(rs_lookback)}日 RS 前{int(rs_top_k)}
# 合格 TopN：按排名遍历全日榜，跳过成分股数 < MIN_MEMBERS 的标签，取满 TOP_N；连续热门=D∩D-1
# 依赖引擎 ctx["em_board_hot"]（D 与 D-1 东财 industry 必齐；concept 可选缺则概念侧为空 + QMT/all_a_stock_info 归属 + 组内 RS）
# 不要求近期涨停；不做涨停后第1-2日禁入；无回撤过滤；无 MA5>MA10 硬条件（仅输出均线）
# 分析字段：近 RS_LOOKBACK 日涨停板数量、最近涨停板距 as_of 几个交易日（0=当日）
TOP_N = {int(top_n)}
RS_TOP_K = {int(rs_top_k)}
RS_LOOKBACK = {int(rs_lookback)}
MIN_MEMBERS = {int(min_members)}
N = 5  # 供引擎日历预检；本规则不依赖涨停窗口


{_EMBEDDED_LIMIT_RATIO_FUNC}


def _code6(stock_code):
    s = str(stock_code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _ma(closes, n):
    if closes is None or len(closes) < n:
        return None
    return float(sum(closes[-n:])) / float(n)


def _closes_through(daily_data, as_of_date):
    if daily_data is None:
        return None
    try:
        if len(daily_data) == 0:
            return None
    except Exception:
        return None
    rows = []
    for _, r in daily_data.iterrows():
        d = r.get("date")
        if d is None:
            continue
        try:
            if hasattr(d, "date") and not isinstance(d, type(as_of_date)):
                dd = d.date()
            else:
                dd = d
        except Exception:
            dd = d
        if as_of_date is not None and dd is not None and dd > as_of_date:
            continue
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:
            rows.append(c)
    return rows


def _is_limit_up_on_row(prev_close, close_price, limit_ratio):
    if prev_close is None or prev_close <= 0:
        return False, 0.0
    limit_up_price = round(float(prev_close) * (1.0 + limit_ratio), 2)
    price_diff = abs(float(close_price) - limit_up_price)
    inc = (float(close_price) - float(prev_close)) / float(prev_close)
    ok = (price_diff < 0.02) or (inc >= limit_ratio * 0.99)
    return ok, limit_up_price


def _recent_lu_stats(stock_code, stock_name, daily_data, as_of_date, lookback):
    """近 lookback 个交易日（含 as_of）涨停次数，及最近一次涨停距 as_of 的交易日偏移。

    偏移：0=as_of 当日涨停，1=上一交易日，…；窗口内无涨停则返回 ""。
    """
    if daily_data is None or getattr(daily_data, "empty", True):
        return 0, ""
    dd = daily_data.sort_values("date")
    if as_of_date is not None:
        dd = dd[dd["date"] <= as_of_date]
    if dd.empty or "date" not in dd.columns or "close" not in dd.columns:
        return 0, ""
    dates = list(dd["date"].tolist())
    if not dates:
        return 0, ""
    lb = max(1, int(lookback))
    window = dates[-lb:] if len(dates) >= lb else dates
    lu_offsets = []
    for i, trade_date in enumerate(window):
        sub = dd[dd["date"] == trade_date]
        if sub.empty:
            continue
        prev = dd[dd["date"] < trade_date]
        if prev.empty:
            continue
        prev_close = float(prev.iloc[-1]["close"])
        close_price = float(sub.iloc[-1]["close"])
        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)
        ok, _ = _is_limit_up_on_row(prev_close, close_price, limit_ratio)
        if ok:
            # 窗口末日 = as_of → offset 0
            lu_offsets.append(len(window) - 1 - i)
    count = len(lu_offsets)
    days_ago = min(lu_offsets) if lu_offsets else ""
    return count, days_ago


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    """连续热门标签组内 RS 前 RS_TOP_K 进池；输出 A/B/RS 名次、MA5/MA10、近窗涨停统计。"""
    em = (ctx or {{}}).get("em_board_hot") or {{}}
    if not isinstance(em, dict) or not em:
        return False, {{"_skip": "无东财热门上下文"}}

    err = str(em.get("error") or "").strip()
    if err:
        return False, {{
            "东财榜日期D": str(em.get("as_of") or ""),
            "东财榜日期D-1": str(em.get("prev_date") or ""),
            "_skip": err,
        }}

    c6 = _code6(stock_code)
    pool = em.get("pool_codes") or set()
    hits = em.get("code_hits") or {{}}
    if c6 not in pool:
        return False, {{
            "东财榜日期D": str(em.get("as_of") or ""),
            "东财榜日期D-1": str(em.get("prev_date") or ""),
            "_skip": "不在连续热门组内RS前{int(rs_top_k)}",
        }}

    hit = hits.get(c6) if isinstance(hits, dict) else None
    if not isinstance(hit, dict):
        return False, {{"_skip": "池内无明细"}}

    closes = _closes_through(daily_data, as_of_date)
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    lu_count, lu_days_ago = _recent_lu_stats(
        stock_code, stock_name, daily_data, as_of_date, RS_LOOKBACK
    )

    extra = {{
        "连续2天热门板块最高排名A": hit.get("连续2天热门板块最高排名A", ""),
        "连续两天热门概念最高排名B": hit.get("连续两天热门概念最高排名B", ""),
        "在A中的RS排名": hit.get("在A中的RS排名", ""),
        "在B中的RS排名": hit.get("在B中的RS排名", ""),
        "A对应板块": hit.get("A对应板块", ""),
        "B对应概念": hit.get("B对应概念", ""),
        "RS最好的热门板块或概念": hit.get("RS最好的热门板块或概念", ""),
        "在其中的RS排名": hit.get("在其中的RS排名", ""),
        "近10日RS": hit.get("近10日RS", ""),
        "命中连续热门标签数": hit.get("命中连续热门标签数", ""),
        "最近10个交易日内的涨停板数量": int(lu_count),
        "最近的涨停板是几日前": lu_days_ago,
        "MA5": round(ma5, 4) if ma5 is not None else "",
        "MA10": round(ma10, 4) if ma10 is not None else "",
        "东财榜日期D": str(em.get("as_of") or ""),
        "东财榜日期D-1": str(em.get("prev_date") or ""),
        "TOP_N": int(TOP_N),
        "RS_TOP_K": int(RS_TOP_K),
        "RS_LOOKBACK": int(RS_LOOKBACK),
        "MIN_MEMBERS": int(MIN_MEMBERS),
    }}
    return True, extra
'''


def rule_code_em_hot_rs_ma_band(
    *,
    hot_mode: str = "continuous",
    top_n: int = 50,
    rs_top_k: int = 20,
    rs_lookback: int = 10,
    min_members: int = 30,
    ma_gap_lo: float = 0.005,
    ma_gap_hi: float = 0.02,
) -> str:
    """东财热门 + 组内 RS + 均线夹档过滤（对照实验用）。

    hot_mode:
      - continuous: D∩D-1 合格 TopN（连续2日）
      - new_only: D 合格 TopN 且不在 D-1（仅今日）

    个股硬过滤（相对选股日收盘）：
      - |MA5−MA10|/min ∈ [ma_gap_lo, ma_gap_hi]
      - 近 rs_lookback 日无涨停
      - MA5 < MA10 < MA20
    """
    mode = str(hot_mode or "continuous").strip().lower()
    if mode not in ("continuous", "new_only"):
        raise ValueError(f"hot_mode 须为 continuous|new_only，收到: {hot_mode!r}")
    is_new = mode == "new_only"
    mode_label = "仅今日热门(D在TopN且D-1不在)" if is_new else "连续2日热门(D∩D-1)"
    pool_key = "new_only_pool_codes" if is_new else "pool_codes"
    hits_key = "new_only_code_hits" if is_new else "code_hits"
    skip_pool = (
        f"不在仅今日热门组内RS前{int(rs_top_k)}"
        if is_new
        else f"不在连续热门组内RS前{int(rs_top_k)}"
    )
    a_key = "仅今日热门板块最高排名A" if is_new else "连续2天热门板块最高排名A"
    b_key = "仅今日热门概念最高排名B" if is_new else "连续两天热门概念最高排名B"
    hit_cnt_key = "命中仅今日热门标签数" if is_new else "命中连续热门标签数"
    gap_lo_pct = float(ma_gap_lo) * 100.0
    gap_hi_pct = float(ma_gap_hi) * 100.0

    return f'''# 东财热门对照：{mode_label} + 组内近{int(rs_lookback)}日 RS 前{int(rs_top_k)}
# 合格 TopN：成分≥{int(min_members)}，取满 TOP_N={int(top_n)}
# 个股过滤：|MA5-MA10|/min∈[{gap_lo_pct:.1f}%,{gap_hi_pct:.1f}%]；近{int(rs_lookback)}日无涨停；MA5<MA10<MA20
# 依赖引擎 ctx["em_board_hot"]
TOP_N = {int(top_n)}
RS_TOP_K = {int(rs_top_k)}
RS_LOOKBACK = {int(rs_lookback)}
MIN_MEMBERS = {int(min_members)}
MA_GAP_LO = {float(ma_gap_lo)}
MA_GAP_HI = {float(ma_gap_hi)}
HOT_MODE = {mode!r}
N = 20  # 日历预检：需 MA20


{_EMBEDDED_LIMIT_RATIO_FUNC}


def _code6(stock_code):
    s = str(stock_code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _ma(closes, n):
    if closes is None or len(closes) < n:
        return None
    return float(sum(closes[-n:])) / float(n)


def _closes_through(daily_data, as_of_date):
    if daily_data is None:
        return None
    try:
        if len(daily_data) == 0:
            return None
    except Exception:
        return None
    rows = []
    for _, r in daily_data.iterrows():
        d = r.get("date")
        if d is None:
            continue
        try:
            if hasattr(d, "date") and not isinstance(d, type(as_of_date)):
                dd = d.date()
            else:
                dd = d
        except Exception:
            dd = d
        if as_of_date is not None and dd is not None and dd > as_of_date:
            continue
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:
            rows.append(c)
    return rows


def _is_limit_up_on_row(prev_close, close_price, limit_ratio):
    if prev_close is None or prev_close <= 0:
        return False, 0.0
    limit_up_price = round(float(prev_close) * (1.0 + limit_ratio), 2)
    price_diff = abs(float(close_price) - limit_up_price)
    inc = (float(close_price) - float(prev_close)) / float(prev_close)
    ok = (price_diff < 0.02) or (inc >= limit_ratio * 0.99)
    return ok, limit_up_price


def _recent_lu_stats(stock_code, stock_name, daily_data, as_of_date, lookback):
    if daily_data is None or getattr(daily_data, "empty", True):
        return 0, ""
    dd = daily_data.sort_values("date")
    if as_of_date is not None:
        dd = dd[dd["date"] <= as_of_date]
    if dd.empty or "date" not in dd.columns or "close" not in dd.columns:
        return 0, ""
    dates = list(dd["date"].tolist())
    if not dates:
        return 0, ""
    lb = max(1, int(lookback))
    window = dates[-lb:] if len(dates) >= lb else dates
    lu_offsets = []
    for i, trade_date in enumerate(window):
        sub = dd[dd["date"] == trade_date]
        if sub.empty:
            continue
        prev = dd[dd["date"] < trade_date]
        if prev.empty:
            continue
        prev_close = float(prev.iloc[-1]["close"])
        close_price = float(sub.iloc[-1]["close"])
        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)
        ok, _ = _is_limit_up_on_row(prev_close, close_price, limit_ratio)
        if ok:
            lu_offsets.append(len(window) - 1 - i)
    count = len(lu_offsets)
    days_ago = min(lu_offsets) if lu_offsets else ""
    return count, days_ago


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    em = (ctx or {{}}).get("em_board_hot") or {{}}
    if not isinstance(em, dict) or not em:
        return False, {{"_skip": "无东财热门上下文"}}

    err = str(em.get("error") or "").strip()
    if err:
        return False, {{
            "东财榜日期D": str(em.get("as_of") or ""),
            "东财榜日期D-1": str(em.get("prev_date") or ""),
            "热门模式": HOT_MODE,
            "_skip": err,
        }}

    c6 = _code6(stock_code)
    pool = em.get({pool_key!r}) or set()
    hits = em.get({hits_key!r}) or {{}}
    if c6 not in pool:
        return False, {{
            "东财榜日期D": str(em.get("as_of") or ""),
            "东财榜日期D-1": str(em.get("prev_date") or ""),
            "热门模式": HOT_MODE,
            "_skip": {skip_pool!r},
        }}

    hit = hits.get(c6) if isinstance(hits, dict) else None
    if not isinstance(hit, dict):
        return False, {{"_skip": "池内无明细", "热门模式": HOT_MODE}}

    closes = _closes_through(daily_data, as_of_date)
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    if ma5 is None or ma10 is None or ma20 is None:
        return False, {{
            "热门模式": HOT_MODE,
            "_skip": "均线不足(需MA5/MA10/MA20)",
        }}

    lo = min(float(ma5), float(ma10))
    if lo <= 0:
        return False, {{"热门模式": HOT_MODE, "_skip": "均线无效"}}
    gap = abs(float(ma5) - float(ma10)) / lo
    if gap < float(MA_GAP_LO) or gap > float(MA_GAP_HI):
        return False, {{
            "热门模式": HOT_MODE,
            "MA5": round(ma5, 4),
            "MA10": round(ma10, 4),
            "MA20": round(ma20, 4),
            "均线差占比": round(gap, 6),
            "_skip": "均线差不在[{gap_lo_pct:.1f}%,{gap_hi_pct:.1f}%]",
        }}

    if not (float(ma5) < float(ma10) < float(ma20)):
        return False, {{
            "热门模式": HOT_MODE,
            "MA5": round(ma5, 4),
            "MA10": round(ma10, 4),
            "MA20": round(ma20, 4),
            "_skip": "非MA5<MA10<MA20",
        }}

    lu_count, lu_days_ago = _recent_lu_stats(
        stock_code, stock_name, daily_data, as_of_date, RS_LOOKBACK
    )
    if int(lu_count) > 0:
        return False, {{
            "热门模式": HOT_MODE,
            "最近10个交易日内的涨停板数量": int(lu_count),
            "最近的涨停板是几日前": lu_days_ago,
            "_skip": "近{int(rs_lookback)}日有涨停",
        }}

    extra = {{
        "热门模式": HOT_MODE,
        {a_key!r}: hit.get({a_key!r}, ""),
        {b_key!r}: hit.get({b_key!r}, ""),
        "在A中的RS排名": hit.get("在A中的RS排名", ""),
        "在B中的RS排名": hit.get("在B中的RS排名", ""),
        "A对应板块": hit.get("A对应板块", ""),
        "B对应概念": hit.get("B对应概念", ""),
        "RS最好的热门板块或概念": hit.get("RS最好的热门板块或概念", ""),
        "在其中的RS排名": hit.get("在其中的RS排名", ""),
        "近10日RS": hit.get("近10日RS", ""),
        {hit_cnt_key!r}: hit.get({hit_cnt_key!r}, ""),
        "最近10个交易日内的涨停板数量": int(lu_count),
        "最近的涨停板是几日前": lu_days_ago,
        "MA5": round(ma5, 4),
        "MA10": round(ma10, 4),
        "MA20": round(ma20, 4),
        "均线差占比": round(gap, 6),
        "东财榜日期D": str(em.get("as_of") or ""),
        "东财榜日期D-1": str(em.get("prev_date") or ""),
        "TOP_N": int(TOP_N),
        "RS_TOP_K": int(RS_TOP_K),
        "RS_LOOKBACK": int(RS_LOOKBACK),
        "MIN_MEMBERS": int(MIN_MEMBERS),
        "MA_GAP_LO": float(MA_GAP_LO),
        "MA_GAP_HI": float(MA_GAP_HI),
    }}
    return True, extra
'''


def rule_code_em_today_hot_elig_band(
    *,
    elig_lo: int = 1,
    elig_hi: int = 15,
    top_n: int = 50,
    rs_top_k: int = 20,
    rs_lo: Optional[int] = None,
    rs_hi: Optional[int] = None,
    rs_lookback: int = 10,
    min_members: int = 20,
    ma_gap_lo: float = 0.005,
    ma_gap_hi: float = 0.02,
    min_float_mv_yi: float = 120.0,
) -> str:
    """今日合格 TopN 全量 + 合格榜内序位档 + 组内 RS 档 + 均线/市值过滤。

    EligibleRank = 命中标签中最小的合格榜内序位（行业/概念各自 1..top_n）。
    组内 RS 默认用「合格榜标签内RS排名」（与定义序位的同一标签）。

    rs_lo / rs_hi：
      - 默认 None → 使用 [1, rs_top_k]（兼容旧头/中/尾档）
      - rs_hi=None 且 rs_lo 有值 → RS ≥ rs_lo（钝档 ≥21）
    """
    lo = int(elig_lo)
    hi = int(elig_hi)
    if lo < 1 or hi < lo:
        raise ValueError(f"elig 区间无效: [{elig_lo}, {elig_hi}]")

    if rs_lo is None and rs_hi is None:
        r_lo, r_hi = 1, int(rs_top_k)
    else:
        r_lo = 1 if rs_lo is None else int(rs_lo)
        r_hi = None if rs_hi is None else int(rs_hi)
    if r_lo < 1:
        raise ValueError(f"rs_lo 无效: {rs_lo}")
    if r_hi is not None and r_hi < r_lo:
        raise ValueError(f"rs 区间无效: [{r_lo}, {r_hi}]")

    gap_lo_pct = float(ma_gap_lo) * 100.0
    gap_hi_pct = float(ma_gap_hi) * 100.0
    band_label = f"合格榜内序位[{lo},{hi}]"
    if r_hi is None:
        rs_label = f"合格榜标签内RS≥{r_lo}"
        rs_skip = f"合格榜标签内RS<{r_lo}"
        rs_hi_lit = "None"
        rs_mode_note = "钝档：固定名次门槛，不做前1/3截断"
    else:
        rs_label = f"合格榜标签内RS≤min({r_hi},ceil(样本/3))"
        rs_skip = f"合格榜标签内RS超出min({r_hi},ceil(样本/3))"
        rs_hi_lit = str(int(r_hi))
        rs_mode_note = (
            "组内RS：每标签取前1/3（按有RS样本数向上取整），且不超过 RS_HI，"
            "避免小板块全入选、大板块只取固定名次"
        )

    return f'''# 东财今日热门：合格 Top{int(top_n)}（成分≥{int(min_members)}）+ {band_label} + {rs_label}
# {rs_mode_note}
# 个股过滤：|MA5-MA10|/min∈[{gap_lo_pct:.1f}%,{gap_hi_pct:.1f}%]；近{int(rs_lookback)}日无涨停；MA5<MA10<MA20；流通市值≥{float(min_float_mv_yi):.0f}亿
# 依赖引擎 ctx["em_board_hot"]（today_pool_codes / today_code_hits / float_mv_yi）
TOP_N = {int(top_n)}
RS_TOP_K = {int(rs_top_k)}
RS_LO = {int(r_lo)}
RS_HI = {rs_hi_lit}
RS_TOP_FRAC_NUM = 1
RS_TOP_FRAC_DEN = 3
RS_LOOKBACK = {int(rs_lookback)}
MIN_MEMBERS = {int(min_members)}
MA_GAP_LO = {float(ma_gap_lo)}
MA_GAP_HI = {float(ma_gap_hi)}
ELIG_LO = {lo}
ELIG_HI = {hi}
MIN_FLOAT_MV_YI = {float(min_float_mv_yi)}
HOT_MODE = "today_elig_{lo}_{hi}_rs_{r_lo}_{'ge' if r_hi is None else r_hi}_frac1of3"
N = 20


{_EMBEDDED_LIMIT_RATIO_FUNC}


def _code6(stock_code):
    s = str(stock_code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _ma(closes, n):
    if closes is None or len(closes) < n:
        return None
    return float(sum(closes[-n:])) / float(n)


def _closes_through(daily_data, as_of_date):
    if daily_data is None:
        return None
    try:
        if len(daily_data) == 0:
            return None
    except Exception:
        return None
    rows = []
    for _, r in daily_data.iterrows():
        d = r.get("date")
        if d is None:
            continue
        try:
            if hasattr(d, "date") and not isinstance(d, type(as_of_date)):
                dd = d.date()
            else:
                dd = d
        except Exception:
            dd = d
        if as_of_date is not None and dd is not None and dd > as_of_date:
            continue
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:
            rows.append(c)
    return rows


def _is_limit_up_on_row(prev_close, close_price, limit_ratio):
    if prev_close is None or prev_close <= 0:
        return False, 0.0
    limit_up_price = round(float(prev_close) * (1.0 + limit_ratio), 2)
    price_diff = abs(float(close_price) - limit_up_price)
    inc = (float(close_price) - float(prev_close)) / float(prev_close)
    ok = (price_diff < 0.02) or (inc >= limit_ratio * 0.99)
    return ok, limit_up_price


def _recent_lu_stats(stock_code, stock_name, daily_data, as_of_date, lookback):
    if daily_data is None or getattr(daily_data, "empty", True):
        return 0, ""
    dd = daily_data.sort_values("date")
    if as_of_date is not None:
        dd = dd[dd["date"] <= as_of_date]
    if dd.empty or "date" not in dd.columns or "close" not in dd.columns:
        return 0, ""
    dates = list(dd["date"].tolist())
    if not dates:
        return 0, ""
    lb = max(1, int(lookback))
    window = dates[-lb:] if len(dates) >= lb else dates
    lu_offsets = []
    for i, trade_date in enumerate(window):
        sub = dd[dd["date"] == trade_date]
        if sub.empty:
            continue
        prev = dd[dd["date"] < trade_date]
        if prev.empty:
            continue
        prev_close = float(prev.iloc[-1]["close"])
        close_price = float(sub.iloc[-1]["close"])
        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)
        ok, _ = _is_limit_up_on_row(prev_close, close_price, limit_ratio)
        if ok:
            lu_offsets.append(len(window) - 1 - i)
    count = len(lu_offsets)
    days_ago = min(lu_offsets) if lu_offsets else ""
    return count, days_ago


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    em = (ctx or {{}}).get("em_board_hot") or {{}}
    if not isinstance(em, dict) or not em:
        return False, {{"_skip": "无东财热门上下文"}}

    err = str(em.get("error") or "").strip()
    if err:
        return False, {{
            "东财榜日期D": str(em.get("as_of") or ""),
            "东财榜日期D-1": str(em.get("prev_date") or ""),
            "热门模式": HOT_MODE,
            "_skip": err,
        }}

    c6 = _code6(stock_code)
    pool = em.get("today_pool_codes") or set()
    hits = em.get("today_code_hits") or {{}}
    if c6 not in pool:
        return False, {{
            "东财榜日期D": str(em.get("as_of") or ""),
            "热门模式": HOT_MODE,
            "_skip": "不在今日热门组内RS池",
        }}

    hit = hits.get(c6) if isinstance(hits, dict) else None
    if not isinstance(hit, dict):
        return False, {{"_skip": "池内无明细", "热门模式": HOT_MODE}}

    try:
        elig = int(hit.get("合格榜内序位") or 0)
    except (TypeError, ValueError):
        elig = 0
    if elig < int(ELIG_LO) or elig > int(ELIG_HI):
        return False, {{
            "热门模式": HOT_MODE,
            "合格榜内序位": elig,
            "合格榜对应标签": hit.get("合格榜对应标签", ""),
            "_skip": "合格序位不在[{lo},{hi}]",
        }}

    # 只认「定 Elig 的同一标签」上的组内 RS；禁止回退到「在其中的RS」（会蹭其他弱标签）
    if "合格榜标签内RS排名" not in hit or hit.get("合格榜标签内RS排名") in (None, ""):
        return False, {{
            "热门模式": HOT_MODE,
            "合格榜内序位": elig,
            "合格榜对应标签": hit.get("合格榜对应标签", ""),
            "_skip": "缺合格榜标签内RS排名（禁止回退在其中的RS）",
        }}
    try:
        rs_in_tag = int(hit.get("合格榜标签内RS排名") or 0)
    except (TypeError, ValueError):
        rs_in_tag = 0
    if rs_in_tag <= 0:
        return False, {{"热门模式": HOT_MODE, "_skip": "无合格榜标签内RS排名"}}
    try:
        tag_rs_n = int(hit.get("合格榜标签RS样本数") or 0)
    except (TypeError, ValueError):
        tag_rs_n = 0
    if RS_HI is None:
        rs_ok = rs_in_tag >= int(RS_LO)
        rs_cut = None
    else:
        # 每标签前 RS_TOP_FRAC_NUM/RS_TOP_FRAC_DEN，硬顶 RS_HI
        den = int(RS_TOP_FRAC_DEN) if int(RS_TOP_FRAC_DEN) > 0 else 3
        num = int(RS_TOP_FRAC_NUM) if int(RS_TOP_FRAC_NUM) > 0 else 1
        if tag_rs_n <= 0:
            return False, {{
                "热门模式": HOT_MODE,
                "合格榜内序位": elig,
                "合格榜对应标签": hit.get("合格榜对应标签", ""),
                "_skip": "缺合格榜标签RS样本数",
            }}
        frac_cut = max(1, (tag_rs_n * num + den - 1) // den)  # ceil(n*num/den)
        rs_cut = min(int(RS_HI), frac_cut)
        rs_ok = int(RS_LO) <= rs_in_tag <= int(rs_cut)
    if not rs_ok:
        return False, {{
            "热门模式": HOT_MODE,
            "合格榜内序位": elig,
            "合格榜标签内RS排名": rs_in_tag,
            "合格榜标签RS样本数": tag_rs_n,
            "合格榜标签RS截断": rs_cut if rs_cut is not None else "",
            "_skip": {rs_skip!r},
        }}

    mv_map = em.get("float_mv_yi") or {{}}
    mv = None
    if isinstance(mv_map, dict):
        try:
            raw = mv_map.get(c6)
            mv = float(raw) if raw is not None and raw != "" else None
        except (TypeError, ValueError):
            mv = None
    if mv is None or mv < float(MIN_FLOAT_MV_YI):
        return False, {{
            "热门模式": HOT_MODE,
            "合格榜内序位": elig,
            "流通市值_亿": "" if mv is None else round(mv, 2),
            "_skip": "流通市值缺失或<{float(min_float_mv_yi):.0f}亿",
        }}

    closes = _closes_through(daily_data, as_of_date)
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    if ma5 is None or ma10 is None or ma20 is None:
        return False, {{
            "热门模式": HOT_MODE,
            "_skip": "均线不足(需MA5/MA10/MA20)",
        }}

    lo_ma = min(float(ma5), float(ma10))
    if lo_ma <= 0:
        return False, {{"热门模式": HOT_MODE, "_skip": "均线无效"}}
    gap = abs(float(ma5) - float(ma10)) / lo_ma
    if gap < float(MA_GAP_LO) or gap > float(MA_GAP_HI):
        return False, {{
            "热门模式": HOT_MODE,
            "MA5": round(ma5, 4),
            "MA10": round(ma10, 4),
            "均线差占比": round(gap, 6),
            "_skip": "均线差不在[{gap_lo_pct:.1f}%,{gap_hi_pct:.1f}%]",
        }}

    if not (float(ma5) < float(ma10) < float(ma20)):
        return False, {{
            "热门模式": HOT_MODE,
            "MA5": round(ma5, 4),
            "MA10": round(ma10, 4),
            "MA20": round(ma20, 4),
            "_skip": "非MA5<MA10<MA20",
        }}

    lu_count, lu_days_ago = _recent_lu_stats(
        stock_code, stock_name, daily_data, as_of_date, RS_LOOKBACK
    )
    if int(lu_count) > 0:
        return False, {{
            "热门模式": HOT_MODE,
            "最近10个交易日内的涨停板数量": int(lu_count),
            "_skip": "近{int(rs_lookback)}日有涨停",
        }}

    extra = {{
        "热门模式": HOT_MODE,
        "合格榜内序位": elig,
        "合格榜对应标签": hit.get("合格榜对应标签", ""),
        "合格榜标签类型": hit.get("合格榜标签类型", ""),
        "合格榜标签东财排名": hit.get("合格榜标签东财排名", ""),
        "合格榜标签内RS排名": rs_in_tag,
        "合格榜标签RS样本数": tag_rs_n,
        "合格榜标签RS截断": rs_cut if rs_cut is not None else "",
        "今日热门板块最高排名A": hit.get("今日热门板块最高排名A", ""),
        "今日热门概念最高排名B": hit.get("今日热门概念最高排名B", ""),
        "在A中的RS排名": hit.get("在A中的RS排名", ""),
        "在B中的RS排名": hit.get("在B中的RS排名", ""),
        "A对应板块": hit.get("A对应板块", ""),
        "B对应概念": hit.get("B对应概念", ""),
        "RS最好的热门板块或概念": hit.get("RS最好的热门板块或概念", ""),
        "在其中的RS排名": hit.get("在其中的RS排名", ""),
        "近10日RS": hit.get("近10日RS", ""),
        "命中今日热门标签数": hit.get("命中今日热门标签数", ""),
        "流通市值_亿": round(mv, 2),
        "最近10个交易日内的涨停板数量": int(lu_count),
        "最近的涨停板是几日前": lu_days_ago,
        "MA5": round(ma5, 4),
        "MA10": round(ma10, 4),
        "MA20": round(ma20, 4),
        "均线差占比": round(gap, 6),
        "东财榜日期D": str(em.get("as_of") or ""),
        "东财榜日期D-1": str(em.get("prev_date") or ""),
        "TOP_N": int(TOP_N),
        "RS_TOP_K": int(RS_TOP_K),
        "RS_LO": int(RS_LO),
        "RS_HI": RS_HI,
        "RS_TOP_FRAC_NUM": int(RS_TOP_FRAC_NUM),
        "RS_TOP_FRAC_DEN": int(RS_TOP_FRAC_DEN),
        "RS_LOOKBACK": int(RS_LOOKBACK),
        "MIN_MEMBERS": int(MIN_MEMBERS),
        "ELIG_LO": int(ELIG_LO),
        "ELIG_HI": int(ELIG_HI),
        "MIN_FLOAT_MV_YI": float(MIN_FLOAT_MV_YI),
        "MA_GAP_LO": float(MA_GAP_LO),
        "MA_GAP_HI": float(MA_GAP_HI),
    }}
    return True, extra
'''

