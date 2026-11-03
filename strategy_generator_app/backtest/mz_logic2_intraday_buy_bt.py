# -*- coding: utf-8 -*-
"""马总盘中（买：满足条件单点）买入批量回测：读落盘候选 CSV，按当时最新价+1分简化成交。

- 输入：data/ma_zong_intraday_candidates/YYYY-MM-DD_HHMM.csv
- 不按「满足全部软门槛」过滤（硬门槛落盘行全部买入）
- 仅生成买入明细；卖出规则/回测后续扩展
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

RULE_NAME_BUY = "马总盘中-满足条件单点"
STRATEGY_IDS_MZ2_BUY = {
    "strategy_mz_logic2_buy",
    "strategy_704d7728",
}
DUMP_CLOCKS_HHMM = ("1100", "1400", "1430", "1440", "1450", "1455")


def is_mz_logic2_buy_strategy(strategy_id: str = "", name: str = "") -> bool:
    sid = str(strategy_id or "").strip()
    if sid in STRATEGY_IDS_MZ2_BUY:
        return True
    n = str(name or "")
    return ("马总盘中" in n) and ("满足条件" in n) and ("买" in n)


def repo_root_from_here() -> str:
    # strategy_generator_app/backtest/this.py -> repo root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def candidates_dir(root: Optional[str] = None) -> str:
    root = root or repo_root_from_here()
    return os.path.join(root, "data", "ma_zong_intraday_candidates")


def normalize_clock_hhmm(raw: Any) -> str:
    """把 14:50 / 1450 / 14:50:00 / QTime-ish 规范成 HHMM。"""
    if raw is None:
        return ""
    if hasattr(raw, "hour") and hasattr(raw, "minute"):
        try:
            return f"{int(raw.hour()):02d}{int(raw.minute()):02d}"
        except Exception:
            try:
                return f"{int(raw.hour):02d}{int(raw.minute):02d}"
            except Exception:
                pass
    s = str(raw).strip()
    if not s:
        return ""
    if ":" in s:
        parts = s.split(":")
        try:
            hh = int(parts[0])
            mm = int(parts[1]) if len(parts) > 1 else 0
            return f"{hh:02d}{mm:02d}"
        except Exception:
            return ""
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) >= 4:
        return digits[-4:]
    if len(digits) in (1, 2):
        return f"{int(digits):02d}00"
    return digits.zfill(4) if digits else ""


def hhmm_to_hms(hhmm: str) -> str:
    hhmm = normalize_clock_hhmm(hhmm)
    if len(hhmm) != 4:
        return "14:50:00"
    return f"{hhmm[:2]}:{hhmm[2:]}:00"


def candidate_csv_path(day: date, hhmm: str, root: Optional[str] = None) -> str:
    hhmm = normalize_clock_hhmm(hhmm)
    return os.path.join(candidates_dir(root), f"{day.isoformat()}_{hhmm}.csv")


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


def _parse_float(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(str(raw).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


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


def _vol_for(price: float, amount: float, min_order: float) -> int:
    if price is None or price <= 0 or amount <= 0:
        return 0
    v = max(100, int(float(amount) / float(price) / 100) * 100)
    if v * float(price) < float(min_order):
        return 0
    return int(v)


def _iter_days(start: date, end: date) -> List[date]:
    if end < start:
        start, end = end, start
    try:
        from trading_calendar import get_trading_dates_in_range_sorted

        days = list(get_trading_dates_in_range_sorted(start, end) or [])
        if days:
            return days
    except Exception:
        pass
    try:
        from utils.trading_day import get_trading_dates_in_range_sorted as _fn

        days = list(_fn(start, end) or [])
        if days:
            return days
    except Exception:
        pass
    out: List[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


@dataclass
class BuyBatchResult:
    trades: List[dict] = field(default_factory=list)
    missing_days: List[str] = field(default_factory=list)
    skipped_no_price: int = 0
    skipped_bad_vol: int = 0
    days_used: List[str] = field(default_factory=list)
    soft_yes: int = 0
    soft_no: int = 0
    csv_path: str = ""
    notes: List[str] = field(default_factory=list)

    def summary_text(self) -> str:
        lines = [
            f"买入笔数：{len(self.trades)}",
            f"用到档位日：{len(self.days_used)} 天",
            f"缺档跳过：{len(self.missing_days)} 天",
            f"软门槛满足：{self.soft_yes} / 不满足仍买入：{self.soft_no}",
            f"跳过无价：{self.skipped_no_price}；金额不足1手：{self.skipped_bad_vol}",
        ]
        if self.csv_path:
            lines.append(f"成交明细：{self.csv_path}")
        if self.missing_days:
            show = self.missing_days[:12]
            more = "" if len(self.missing_days) <= 12 else f" …共{len(self.missing_days)}天"
            lines.append("缺档日期：" + ", ".join(show) + more)
        for n in self.notes:
            lines.append(n)
        return "\n".join(lines)


def load_candidate_rows(path: str) -> List[Dict[str, str]]:
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(path)
    last_err: Optional[Exception] = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "cp936"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                return [dict(r) for r in reader]
        except UnicodeDecodeError as e:
            last_err = e
            continue
    if last_err is not None:
        raise last_err
    raise ValueError("无法读取候选 CSV: %s" % path)


def rows_to_buy_trades(
    rows: Sequence[Dict[str, Any]],
    *,
    trade_date: date,
    clock_hhmm: str,
    buy_amount: float = 100000.0,
    min_order: float = 5000.0,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    rule_name: str = RULE_NAME_BUY,
) -> Tuple[List[dict], int, int, int, int]:
    """返回 (trades, soft_yes, soft_no, skipped_no_price, skipped_bad_vol)。"""
    hhmm = normalize_clock_hhmm(clock_hhmm)
    t_hms = hhmm_to_hms(hhmm)
    sd = (start_date or trade_date).isoformat()
    ed = (end_date or trade_date).isoformat()
    trades: List[dict] = []
    soft_yes = soft_no = skip_px = skip_vol = 0
    for raw in rows:
        code = _norm_code6(_row_get(raw, "股票代码", "代码", "code", "stock_code"))
        if not code:
            continue
        name = str(_row_get(raw, "股票名称", "名称", "name", "stock_name") or "").strip()
        last_px = _parse_float(_row_get(raw, "当时最新价", "最新价", "price", "现价"))
        if last_px is None or last_px <= 0:
            skip_px += 1
            continue
        buy_px = round(float(last_px) + 0.01, 4)
        vol = _vol_for(buy_px, float(buy_amount), float(min_order))
        if vol <= 0:
            skip_vol += 1
            continue
        amount = round(buy_px * vol, 2)
        soft_raw = str(_row_get(raw, "满足全部软门槛", "meet", "满足条件") or "").strip()
        soft_ok = soft_raw in ("是", "True", "true", "1", "Y", "y")
        if soft_ok:
            soft_yes += 1
        else:
            soft_no += 1
        reason = str(_row_get(raw, "不满足的原因_汇总", "不满足的原因", "reason") or "").strip()
        trig = (
            f"盘中候选简化买入: 当时最新价={last_px:.4f}+0.01={buy_px:.4f}; "
            f"满足全部软门槛={'是' if soft_ok else '否'}"
        )
        if reason and not soft_ok:
            trig += f"; 原因={reason}"
        clock_raw = str(_row_get(raw, "落盘时钟") or "").strip()
        if clock_raw and ":" in clock_raw:
            try:
                parts = clock_raw.split(":")
                t_hms = f"{int(parts[0]):02d}:{int(parts[1]):02d}:00"
            except Exception:
                pass
        trades.append(
            {
                "date": trade_date.isoformat(),
                "time": t_hms,
                "code": code,
                "stock_name": name,
                "选股日": trade_date.isoformat(),
                "side": "buy",
                "price": buy_px,
                "volume": int(vol),
                "amount": amount,
                "position_after": int(vol),
                "rule_name": rule_name,
                "leg_key": "",
                "trigger_info": trig,
                "start_date": sd,
                "end_date": ed,
                "满足全部软门槛": "是" if soft_ok else "否",
            }
        )
    return trades, soft_yes, soft_no, skip_px, skip_vol


def write_trades_csv(path: str, trades: Sequence[dict]) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    headers = [
        "日期",
        "时间",
        "代码",
        "股票名称",
        "选股日",
        "方向",
        "价格",
        "数量",
        "金额",
        "交易后持仓",
        "规则名",
        "腿键",
        "触发信息",
        "start_date",
        "end_date",
        "满足全部软门槛",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for t in trades:
            w.writerow(
                [
                    t.get("date", ""),
                    t.get("time", ""),
                    t.get("code", ""),
                    t.get("stock_name", ""),
                    t.get("选股日", ""),
                    "买入" if t.get("side") == "buy" else "卖出",
                    t.get("price", ""),
                    t.get("volume", ""),
                    t.get("amount", ""),
                    t.get("position_after", ""),
                    t.get("rule_name", ""),
                    t.get("leg_key", ""),
                    t.get("trigger_info", ""),
                    t.get("start_date", ""),
                    t.get("end_date", ""),
                    t.get("满足全部软门槛", ""),
                ]
            )
    return path


def run_batch_buy_backtest(
    start: date,
    end: date,
    clock_hhmm: str,
    *,
    buy_amount: float = 100000.0,
    min_order: float = 5000.0,
    root: Optional[str] = None,
    out_csv: Optional[str] = None,
    auto_write_csv: bool = True,
) -> BuyBatchResult:
    root = root or repo_root_from_here()
    hhmm = normalize_clock_hhmm(clock_hhmm)
    if not hhmm or len(hhmm) != 4:
        raise ValueError("策略生成时间无效，需如 14:50")
    result = BuyBatchResult()
    if hhmm not in DUMP_CLOCKS_HHMM:
        result.notes.append(
            f"提示：时钟 {hhmm[:2]}:{hhmm[2:]} 不在常用 dump_times={','.join(DUMP_CLOCKS_HHMM)}，仍按该档文件名查找。"
        )
    for d in _iter_days(start, end):
        path = candidate_csv_path(d, hhmm, root=root)
        if not os.path.isfile(path):
            result.missing_days.append(d.isoformat())
            continue
        try:
            rows = load_candidate_rows(path)
        except Exception as e:
            result.missing_days.append(d.isoformat())
            result.notes.append(f"{d.isoformat()} 读CSV失败: {e}")
            continue
        trades, sy, sn, sp, sv = rows_to_buy_trades(
            rows,
            trade_date=d,
            clock_hhmm=hhmm,
            buy_amount=buy_amount,
            min_order=min_order,
            start_date=start,
            end_date=end,
        )
        result.trades.extend(trades)
        result.soft_yes += sy
        result.soft_no += sn
        result.skipped_no_price += sp
        result.skipped_bad_vol += sv
        result.days_used.append(d.isoformat())

    result.trades.sort(key=lambda r: (r.get("date") or "", r.get("time") or "", r.get("code") or ""))

    if auto_write_csv:
        if out_csv:
            csv_path = out_csv
        else:
            hd = os.path.join(root, "history_data", "马总盘中策略")
            os.makedirs(hd, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = os.path.join(
                hd,
                f"回测成交明细_买入_盘中候选_{start.isoformat()}_{end.isoformat()}_{hhmm}_{stamp}.csv",
            )
        result.csv_path = write_trades_csv(csv_path, result.trades)
    return result


__all__ = [
    "BuyBatchResult",
    "DUMP_CLOCKS_HHMM",
    "RULE_NAME_BUY",
    "candidate_csv_path",
    "hhmm_to_hms",
    "is_mz_logic2_buy_strategy",
    "normalize_clock_hhmm",
    "run_batch_buy_backtest",
    "write_trades_csv",
]
