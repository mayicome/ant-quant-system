# -*- coding: utf-8 -*-
"""创建选股规则：选股基准日当天涨停。"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(r"d:\蚂蚁量化系统")
sys.path.insert(0, str(ROOT))

from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

RULE_NAME = "选股基准日当天涨停"

CODE = r'''# 选出选股基准日（as_of_date）当天收盘涨停的股票。
# 无额外过滤：不区分首板/连板，不要求多头/下影等。


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


def _dates_up_to(daily_data, as_of_date):
    dd = daily_data.sort_values("date")
    if as_of_date is not None:
        dd = dd[dd["date"] <= as_of_date]
    return dd


def _as_of_effective_date(all_dates, as_of_date):
    if not all_dates:
        return None
    if as_of_date is not None and as_of_date in all_dates:
        return as_of_date
    if as_of_date is None:
        return all_dates[-1]
    prior = [d for d in all_dates if d <= as_of_date]
    return prior[-1] if prior else None


def _is_limit_up_on_row(prev_close, close_price, limit_ratio):
    if prev_close is None or prev_close <= 0:
        return False, 0.0
    limit_up_price = round(float(prev_close) * (1.0 + limit_ratio), 2)
    price_diff = abs(float(close_price) - limit_up_price)
    inc = (float(close_price) - float(prev_close)) / float(prev_close)
    ok = (price_diff < 0.02) or (inc >= limit_ratio * 0.99)
    return ok, limit_up_price


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    """选股基准日当天收盘涨停则命中。"""
    if daily_data is None or getattr(daily_data, "empty", True):
        return False, {}
    dd = _dates_up_to(daily_data, as_of_date).copy()
    if dd.empty or "date" not in dd.columns or "close" not in dd.columns:
        return False, {}
    all_dates = list(dd["date"].tolist())
    as_of_eff = _as_of_effective_date(all_dates, as_of_date)
    if as_of_eff is None:
        return False, {}

    sub = dd[dd["date"] == as_of_eff]
    if sub.empty:
        return False, {}
    prev = dd[dd["date"] < as_of_eff]
    if prev.empty:
        return False, {}

    row = sub.iloc[-1]
    prev_close = float(prev.iloc[-1]["close"])
    close_price = float(row["close"])
    limit_ratio = _limit_ratio(stock_code, stock_name, as_of_eff)
    ok, limit_up_price = _is_limit_up_on_row(prev_close, close_price, limit_ratio)
    if not ok:
        return False, {}

    extra = {
        "涨停日期": as_of_eff.strftime("%Y-%m-%d")
        if hasattr(as_of_eff, "strftime")
        else str(as_of_eff),
        "涨停价": round(float(limit_up_price), 2),
        "收盘价": round(float(close_price), 2),
        "昨收": round(float(prev_close), 2),
        "涨停幅度": round(float(limit_ratio) * 100, 1),
    }
    return True, extra
'''


def main() -> None:
    compile(CODE, f"<{RULE_NAME}>", "exec")
    existing = [r for r in load_sector_rules() if r.get("name") == RULE_NAME]
    if existing:
        rid = str(existing[0]["id"])
        print("updating existing", rid)
    else:
        rid = str(uuid.uuid4())
        print("creating", rid)

    rule = {
        "id": rid,
        "name": RULE_NAME,
        "enabled": True,
        "code": CODE,
    }
    save_single_sector_rule(rule)
    names = [str(r.get("name")) for r in load_sector_rules()]
    assert RULE_NAME in names
    print("OK:", RULE_NAME)


if __name__ == "__main__":
    main()
