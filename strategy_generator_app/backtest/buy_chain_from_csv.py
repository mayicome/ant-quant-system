# -*- coding: utf-8 -*-
"""从买入成交明细 CSV 构建「连续接续卖出」快照（对齐实盘仓位语义）。

- 不按选股日切开模拟：同一代码跨多日买入累加到同一条时间线
- buy_fills 供引擎按日注入（T+1 可卖 / positions_baseline / 末笔买入锚定清仓）
- 选股日仅作明细元数据，不参与分档回测
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple


def _norm_code6(raw: Any) -> str:
    s = str(raw or "").strip()
    if "." in s:
        s = s.split(".", 1)[0].strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _parse_date(val: Any) -> Optional[date]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return date.fromisoformat(s[:10])
        except Exception:
            return None
    if len(s) >= 8 and s[:8].isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except Exception:
            return None
    return None


def _parse_time(val: Any) -> str:
    """返回 HH:MM:SS；无法识别则 09:30:00。"""
    s = str(val or "").strip()
    if not s:
        return "09:30:00"
    if ":" in s:
        parts = s.replace(".", ":").split(":")
        try:
            hh = int(parts[0])
            mm = int(parts[1]) if len(parts) > 1 else 0
            ss = int(float(parts[2])) if len(parts) > 2 else 0
            if 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59:
                return f"{hh:02d}:{mm:02d}:{ss:02d}"
        except Exception:
            pass
    try:
        n = int(float(s))
        if n > 10_000_000_000:
            n = n // 1000
        if n > 1_000_000_000:
            dt = datetime.fromtimestamp(n)
            return dt.strftime("%H:%M:%S")
    except Exception:
        pass
    return "09:30:00"


def _is_buy_side(val: Any) -> bool:
    s = str(val or "").strip().lower()
    if not s:
        return True
    if s in ("buy", "b", "买入", "买"):
        return True
    if s in ("sell", "s", "卖出", "卖"):
        return False
    return "买" in str(val) and "卖" not in str(val)


def _row_get(row: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    lower = {str(a).strip().lower(): a for a in row.keys()}
    for k in keys:
        lk = k.lower()
        if lk in lower:
            v = row.get(lower[lk])
            if v not in (None, ""):
                return v
    return None


def load_buy_fills_from_trades_csv(path: str) -> Tuple[List[dict], str]:
    """解析导出的回测成交明细 CSV → compact buy_fills。

    返回 (fills, 说明文案)。只保留买入行。
    """
    if not path or not os.path.isfile(path):
        raise FileNotFoundError("找不到买入成交明细：%s" % path)
    fills: List[dict] = []
    skipped = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV 无表头")
        for raw in reader:
            if not _is_buy_side(_row_get(raw, "方向", "side", "买卖", "买卖方向")):
                skipped += 1
                continue
            code_6 = _norm_code6(_row_get(raw, "代码", "code", "stock_code", "证券代码"))
            bd = _parse_date(_row_get(raw, "日期", "date", "trade_date", "成交日期"))
            if not code_6 or bd is None:
                skipped += 1
                continue
            try:
                vol = int(float(_row_get(raw, "数量", "volume", "成交量") or 0))
            except (TypeError, ValueError):
                vol = 0
            try:
                price = float(_row_get(raw, "价格", "price", "成交价") or 0)
            except (TypeError, ValueError):
                price = 0.0
            try:
                amount = float(_row_get(raw, "金额", "amount", "成交额") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if vol <= 0 or price <= 0:
                skipped += 1
                continue
            if amount <= 0:
                amount = round(price * vol, 2)
            try:
                commission = float(_row_get(raw, "佣金", "commission", "手续费") or 0)
            except (TypeError, ValueError):
                commission = 0.0
            tm = _parse_time(_row_get(raw, "时间", "time", "成交时间"))
            row: Dict[str, Any] = {
                "code": code_6,
                "date": bd.isoformat(),
                "time": tm,
                "volume": vol,
                "price": price,
                "amount": round(amount, 2),
                "commission": round(commission, 2),
                "side": "buy",
            }
            rn = str(_row_get(raw, "规则名", "rule_name", "规则") or "").strip()
            if rn:
                row["rule_name"] = rn
            lk = str(_row_get(raw, "腿键", "leg_key", "腿") or "").strip()
            if lk:
                row["leg_key"] = lk
            sel = _parse_date(_row_get(raw, "选股日", "selection_date", "选股日期"))
            if sel is not None:
                row["selection_date"] = sel.isoformat()
            name = str(_row_get(raw, "股票名称", "name", "名称") or "").strip()
            if name:
                row["name"] = name
            fills.append(row)
    fills.sort(key=lambda r: (r["date"], r.get("time") or "", r["code"]))
    codes = sorted({r["code"] for r in fills})
    note = "解析买入 %d 笔 / %d 只" % (len(fills), len(codes))
    if skipped:
        note += "，跳过 %d 行" % skipped
    return fills, note


def build_continuous_chain_segment_from_buy_fills(
    fills: List[dict],
    *,
    source_path: str = "",
    source_label: str = "买入成交明细CSV",
) -> dict:
    """合成单档连续接续快照：空持仓 + 全量 buy_fills + 首次/末笔买入日。"""
    first_buy: Dict[str, str] = {}
    last_buy: Dict[str, str] = {}
    cost_amt: Dict[str, float] = {}
    vol_sum: Dict[str, int] = {}
    cash_used = 0.0
    for f in fills or []:
        c6 = _norm_code6(f.get("code"))
        bd = _parse_date(f.get("date"))
        if not c6 or bd is None:
            continue
        try:
            vol = int(f.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0
        try:
            amount = float(f.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            commission = float(f.get("commission") or 0)
        except (TypeError, ValueError):
            commission = 0.0
        if vol <= 0:
            continue
        iso = bd.isoformat()
        if c6 not in first_buy:
            first_buy[c6] = iso
        last_buy[c6] = iso
        vol_sum[c6] = int(vol_sum.get(c6) or 0) + vol
        cost_amt[c6] = float(cost_amt.get(c6) or 0) + float(amount)
        cash_used += float(amount) + float(commission)

    return {
        "version": 1,
        "kind": "buy_fills_csv_chain",
        "source_strategy_name": source_label,
        "source_buy_csv": source_path or "",
        "final_cash": 0.0,
        "positions": {},
        "buy_fills": list(fills or []),
        "first_buy_dates": dict(first_buy),
        "last_buy_dates": dict(last_buy),
        "buy_volume_sum": dict(vol_sum),
        "buy_cost_sum": {k: round(v, 2) for k, v in cost_amt.items()},
        "buy_cash_used": round(cash_used, 2),
        "note": "连续回放：同票跨选股日累加；半仓看 baseline；清仓锚定末笔买入",
    }
