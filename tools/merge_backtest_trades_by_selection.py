#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将「买入成交明细」与「卖出成交明细」两份 CSV（策略生成器导出的格式）按
「选股日 + 股票代码」汇总：买卖金额/数量、净现金流、剩余持仓、收益率等。

选股日优先读 CSV 独立列「选股日」（与批量/下一轮回测导出列一致）；无该列时再从「触发信息」
中解析 [选股日 yyyy-mm-dd]，避免仅靠长文本解析导致混批或漏解析。

仅接受可解析的日历日作为选股日键；``live_align`` / ``live_align_N*`` 等实盘对齐占位
视为「无选股日」。卖出侧无选股日时按代码回挂买入腿，同码多笔按买入日 FIFO 分摊数量与金额。

收益率（收益率pct）统一为相对买入金额的盈亏比例：
  (卖出金额合计 + 剩余持仓数量 × 收盘价 − 买入金额合计) / 买入金额合计 × 100
已清仓时剩余为 0，等价于 (卖−买)/买。

汇总表中的 **end_date** 默认按「锚定买入日 + 持有交易日数」经交易日历推算（与下一轮接续
口径一致：买入次日=持有第1日；有第二腿时锚定=末笔买入日）；无买入日时回退「选股日 T+1 起」。
生成汇总前会按该结束日**截断卖出**（丢弃早于该腿买入日、或晚于结束日的 live_align 错挂），
避免「卖出数量多于买入」。CSV 卖出明细里的 end_date 仅作参考，汇总时会被上述规则覆盖。

未清仓盯市：默认用该票 **持仓结束日（end_date）** 收盘价；已清仓不写盯市日。
若结束日晚于今天（或本地尚无该日K线），则按「今天及之前最近交易日」做**临时盯市**，
备注标明「持仓未到期…计划结束日=…」；end_date 列仍保留计划清仓日。
可选：选股日后第 N 日，或「最后可得收盘」（数据排查用）。

拉取收盘价优先用本地 daily_cache；缺数据时再尝试 data_provider / xtquant（未开 QMT 也可生成汇总）。

用法:
  python tools/merge_backtest_trades_by_selection.py \\
    --buy history_data/回测成交明细_xxx.csv \\
    --sell history_data/回测成交明细_yyy.csv \\
    --out history_data/选股日收益汇总.csv

  # 旧版：按选股日后第 3 个交易日盯市
  # ... same --buy/--sell/--out ... --use-nth-trading-day --mark-n 3
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _fill_adjust() -> str:
    """盯市/撮合收盘价复权口径（与 BACKTEST_FILL_ADJUST 对齐，默认 qfq）。"""
    import os

    try:
        from utils.daily_adjust_paths import normalize_adjust

        return normalize_adjust(os.environ.get("BACKTEST_FILL_ADJUST") or "qfq")
    except Exception:
        raw = (os.environ.get("BACKTEST_FILL_ADJUST") or "qfq").strip().lower()
        return "qfq" if raw in ("qfq", "front", "前复权") else "none"

SEL_RE = re.compile(r"\[选股日\s*(\d{4}-\d{2}-\d{2})\]")

# 与 strategy_generator_app.backtest.true_breakthrough.TRUE_BREAKTHROUGH_EXPORT_FIELDS 一致
TB_SUMMARY_FIELDS: Tuple[str, ...] = (
    "真突破①量均量比",
    "真突破①通过",
    "真突破②委卖委买比",
    "真突破②通过",
    "真突破③量被吃卖档比",
    "真突破③通过",
    "真突破③被吃档数",
)

# 选股日对应均线（供对照「是否要过滤均线关系」）
MA_SUMMARY_FIELDS: Tuple[str, ...] = (
    "5日线",
    "10日线",
    "20日线",
    "30日线",
    "60日线",
    "120日线",
)

# 买入日 MA5 重合参考（与旧「贴/穿 MA5」对照；口径=买入日早盘 MA5）
BUY_DAY_MA5_FIELDS: Tuple[str, ...] = (
    "买入日",
    "买入成交价",
    "买入日5日线",
    "成交相对买入日MA5_pct",
    "价格带下沿",
    "价格带上沿",
)


def _parse_sel(trigger: str) -> str:
    if not trigger:
        return ""
    m = SEL_RE.search(trigger)
    return m.group(1) if m else ""


def _calendar_sel_str(raw: Any) -> str:
    """仅接受可解析日历日；live_align 等占位 / 非日期字符串一律视为空。"""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    sl = s.lower()
    if sl.startswith("live_align") or "live_align" in sl:
        return ""
    d = _parse_row_date(s)
    if d is not None:
        return d.strftime("%Y-%m-%d")
    return ""


def _sel_from_row(r: dict, *, fallback_trade_date: bool = False) -> str:
    """
    汇总键「选股日」：优先使用导出 CSV 独立列（与触发信息解析解耦，避免漏解析/混批）。
    兼容列名：选股日、selection_date、选股日期；否则回退 [选股日 yyyy-mm-dd] 触发信息。

    仅返回 YYYY-MM-DD；live_align 等占位当作缺失。
    若「选股日」列等于成交日、但触发信息里另有选股日，则信触发信息
    （兼容旧实盘对齐把选股日写成买入日的导出）。
    fallback_trade_date：列与触发信息都空时，用成交「日期」兜底（盘中当日买、无选股文件时）。
    """
    if not isinstance(r, dict):
        return ""
    col_sel = ""
    for key in ("选股日", "selection_date", "选股日期"):
        cal = _calendar_sel_str(r.get(key))
        if cal:
            col_sel = cal
            break
    from_trig = _calendar_sel_str(_parse_sel(r.get("触发信息") or ""))
    trade_d = _parse_row_date(
        r.get("date") or r.get("日期") or r.get("买入日期") or r.get("trade_date")
    )
    trade_s = trade_d.strftime("%Y-%m-%d") if trade_d is not None else ""
    if col_sel and from_trig and col_sel != from_trig and trade_s and col_sel == trade_s:
        return from_trig
    if col_sel:
        return col_sel
    if from_trig:
        return from_trig
    if fallback_trade_date and trade_d is not None:
        return trade_s
    return ""


def _code6_from_row(r: dict) -> str:
    s = str(r.get("代码") or r.get("stock_code") or r.get("股票代码") or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)[-6:]
    return s


def _resolve_sell_sel_when_missing(
    code: str,
    sell_d: Optional[date],
    buy_sels_by_code: Dict[str, List[Tuple[str, Optional[date]]]],
) -> str:
    """卖出无选股日时：按代码回挂到买入侧选股日（同码多笔则取买入日<=卖出日的最近一笔，否则最早）。"""
    cands = list(buy_sels_by_code.get(code) or [])
    if not cands:
        return ""
    if len(cands) == 1:
        return cands[0][0]
    if sell_d is not None:
        le = [(sel, bd) for sel, bd in cands if bd is not None and bd <= sell_d]
        if le:
            le.sort(key=lambda x: x[1] or date.min, reverse=True)
            return le[0][0]
    cands.sort(key=lambda x: x[1] or date.max)
    return cands[0][0]


def _fifo_open_lots(
    code: str,
    sell_d: Optional[date],
    st: Dict[Tuple[str, str], dict],
    buy_sels_by_code: Dict[str, List[Tuple[str, Optional[date]]]],
) -> List[Tuple[date, str, int, Tuple[str, str]]]:
    """同码未清仓买入腿，按买入日升序：(buy_date, sel, rem_vol, key)。"""
    lots: List[Tuple[date, str, int, Tuple[str, str]]] = []
    for sel, bd in buy_sels_by_code.get(code) or []:
        k = (sel, code)
        v = st.get(k)
        if not v or int(v.get("buy_n") or 0) <= 0:
            continue
        rem = int(v.get("buy_vol") or 0) - int(v.get("sell_vol") or 0)
        if rem <= 0:
            continue
        if sell_d is not None and bd is not None and bd > sell_d:
            continue
        lots.append((bd or date.min, sel, rem, k))
    lots.sort(key=lambda x: x[0])
    return lots


def _apply_sell_to_group(
    st: Dict[Tuple[str, str], dict],
    k: Tuple[str, str],
    *,
    sell_vol: int,
    sell_amt: float,
    sell_n: int = 1,
    end_d: Optional[date] = None,
) -> None:
    st[k]["sell_vol"] += int(sell_vol)
    st[k]["sell_amt"] += float(sell_amt)
    st[k]["sell_n"] += int(sell_n)
    if end_d is not None:
        cur = st[k]["end_date"]
        if cur is None:
            st[k]["end_date"] = end_d
        elif cur != end_d:
            st[k]["end_date_warn"] = True


def _allocate_sell_fifo(
    st: Dict[Tuple[str, str], dict],
    buy_sels_by_code: Dict[str, List[Tuple[str, Optional[date]]]],
    *,
    code: str,
    sell_vol: int,
    sell_amt: float,
    sell_d: Optional[date],
    end_d: Optional[date] = None,
) -> None:
    """
    卖出无真实选股日时：按买入日 FIFO 把数量/金额摊到仍有剩余仓位的买入腿。
    超出买入合计的部分挂到最后一腿（或回挂解析出的选股日），保留「含初始仓」语义。
    """
    vol = int(sell_vol or 0)
    amt = float(sell_amt or 0.0)
    if vol <= 0 and end_d is None:
        return

    lots = _fifo_open_lots(code, sell_d, st, buy_sels_by_code)
    if vol <= 0:
        # 仅 end_date 占位：写到仍有剩余的腿，否则回挂一腿
        targets = [k for _bd, _sel, _rem, k in lots]
        if not targets:
            sel = _resolve_sell_sel_when_missing(code, sell_d, buy_sels_by_code)
            if sel:
                targets = [(sel, code)]
        for k in targets:
            if end_d is not None:
                cur = st[k]["end_date"]
                if cur is None:
                    st[k]["end_date"] = end_d
                elif cur != end_d:
                    st[k]["end_date_warn"] = True
        return

    if not lots:
        sel = _resolve_sell_sel_when_missing(code, sell_d, buy_sels_by_code)
        if not sel:
            return
        _apply_sell_to_group(st, (sel, code), sell_vol=vol, sell_amt=amt, end_d=end_d)
        return

    left_v = vol
    left_a = amt
    for i, (_bd, _sel, rem, k) in enumerate(lots):
        if left_v <= 0:
            break
        take_v = min(rem, left_v)
        if take_v <= 0:
            continue
        if take_v == left_v:
            take_a = left_a
        else:
            take_a = round(left_a * (take_v / float(left_v)), 2) if left_v else 0.0
        _apply_sell_to_group(
            st, k, sell_vol=take_v, sell_amt=take_a, sell_n=1, end_d=end_d
        )
        left_v -= take_v
        left_a = round(left_a - take_a, 2)

    if left_v > 0:
        # 卖超买入：挂到最后一腿
        _apply_sell_to_group(
            st, lots[-1][3], sell_vol=left_v, sell_amt=left_a, sell_n=0, end_d=end_d
        )


def _read_rows(path: Path) -> List[dict]:
    encodings = ("utf-8-sig", "utf-8", "gbk")
    last_err = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise last_err or OSError(path)


def _parse_row_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = str(val).strip()
    if not s:
        return None
    for part in (s[:10], s.replace("/", "-")[:10]):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(part, fmt).date()
            except ValueError:
                continue
    # 8 位 YYYYMMDD 或 Excel 序列日（与单元格显示为 yyyy-mm-dd 混排时可对齐到同一自然日）
    try:
        fv = float(s)
    except (ValueError, TypeError):
        fv = None
    if fv is None and isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            fv = float(val)
        except (TypeError, ValueError, OverflowError):
            fv = None
    if fv is not None:
        try:
            si = int(round(fv))
            if 19900101 <= si <= 21001231 and len(str(si)) == 8:
                y, m, d = si // 10000, (si // 100) % 100, si % 100
                if 1 <= m <= 12 and 1 <= d <= 31:
                    return date(y, m, d)
            if 30000 <= si <= 80000:
                base = date(1899, 12, 30)
                return base + timedelta(days=si)
        except (ValueError, TypeError, OverflowError):
            pass
    return None


def _norm_time_str(val) -> str:
    """
    将成交时间归一为 HH:MM:SS（兼容 HH:MM、'YYYY-MM-DD HH:MM:SS'、'YYYY-MM-DDTHH:MM:SS'）。
    失败则返回原始裁剪字符串，保证汇总不丢信息。
    """
    if val is None:
        return ""
    s = str(val).strip()
    if (not s) or s.lower() == "nan":
        return ""
    # 常见：整串 datetime
    if "T" in s and len(s) >= 19:
        s = s.split("T", 1)[-1].strip()
    if " " in s and len(s) >= 17:
        s = s.rsplit(" ", 1)[-1].strip()
    # 只取前 8 个字符尝试解析（避免带毫秒）
    cand = s[:8]
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            dt = datetime.strptime(cand[: len(fmt)], fmt)
            return dt.strftime("%H:%M:%S")
        except ValueError:
            continue
    # 宽松兜底：补零到 HH:MM:SS
    try:
        parts = (cand.split(":") + ["0", "0", "0"])[:3]
        hh, mm, ss = (int(parts[0]), int(parts[1]), int(parts[2]))
        if 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59:
            return f"{hh:02d}:{mm:02d}:{ss:02d}"
    except Exception:
        pass
    return s


def _norm_sel_key(raw: str) -> str:
    """汇总分组键：仅 YYYY-MM-DD；live_align 等占位归一为空。"""
    return _calendar_sel_str(raw)


def apply_end_date_from_trading_calendar(
    rows: List[dict],
    *,
    from_t1: bool,
    hold_n: int,
) -> List[str]:
    """
    与「批量回测」相同规则：由选股日 + T+1 与否 + 持有交易日数，用交易日历精确计算区间结束日，
    写入每行 end_date（覆盖 CSV 中可能不一致的 end_date）。不使用众数或猜测。

    对每个「规范化后的选股日」只调用一次交易日历，再赋给该日下所有行，避免混用 Excel 序列号
    与字符串日期时逐行失败保留旧 CSV end_date 的不一致。

    返回：未能计算结束日的警告文案列表（便于 UI 展示）。
    """
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    try:
        from strategy_generator_app.trading_calendar import backtest_window_from_selection_day
    except ImportError:
        from trading_calendar import backtest_window_from_selection_day  # type: ignore

    hold_n = int(hold_n or 1)
    if hold_n < 1:
        hold_n = 1
    warns: List[str] = []

    seen: set = set()
    uniq_keys: List[str] = []
    for r in rows:
        sk = _norm_sel_key(str(r.get("选股日") or ""))
        if sk and sk not in seen:
            seen.add(sk)
            uniq_keys.append(sk)

    end_map: Dict[str, str] = {}
    for sk in uniq_keys:
        sel_d = _parse_row_date(sk)
        if not sel_d:
            warns.append(f"选股日无法解析，跳过结束日：{sk!r}")
            continue
        _start, end_d, msg = backtest_window_from_selection_day(
            sel_d,
            start_next_trading_day=bool(from_t1),
            hold_trading_days=hold_n,
        )
        if end_d is None:
            warns.append(f"{sk}: {msg}")
            continue
        end_map[sk] = end_d.strftime("%Y-%m-%d")

    for r in rows:
        sk = _norm_sel_key(str(r.get("选股日") or ""))
        if sk:
            r["选股日"] = sk
        if sk and sk in end_map:
            r["end_date"] = end_map[sk]
        elif sk:
            note = str(r.get("备注") or "").strip()
            r["备注"] = (note + "；" if note else "") + "无法由交易日历写入 end_date（请检查选股日格式与日历）"
    return warns


def apply_hold_end_date_from_buy(
    rows: List[dict],
    *,
    hold_from_next_day: int,
) -> List[str]:
    """按买入日写入持仓结束日 end_date（覆盖 CSV / 选股日窗算出的 end_date）。

    口径与「下一轮接续→持有交易日数」一致：
    - 界面 N = 锚定买入日【次日】起持有第 1…N 日，第 N 日为结束日
    - 等价于含锚定日共 N+1 个交易日：trading_day_window_from_start(锚定日, N+1)
    - 默认锚定=首买日（买入日）；若有第二腿（买入笔数>=2 且有末笔买入日），
      则锚定改为末笔买入日（通常即第二腿），持仓截止相应延后

    无买入日时：回退选股日，按 T+1 起连续 N 个交易日（与选股后首买日对齐的近似）。
    仅买入金额>0 的行写入（纯卖出挂账行不改）。
    """
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    try:
        from strategy_generator_app.trading_calendar import (
            backtest_window_from_selection_day,
            trading_day_window_from_start,
        )
    except ImportError:
        from trading_calendar import (  # type: ignore
            backtest_window_from_selection_day,
            trading_day_window_from_start,
        )

    try:
        ui_n = int(hold_from_next_day)
    except (TypeError, ValueError):
        ui_n = 2
    ui_n = max(1, ui_n)
    engine_n = ui_n + 1  # 含买入日
    warns: List[str] = []
    # 必须带 hold 天数：同一买入日不同 N 不能共用缓存
    cache_buy: Dict[Tuple[date, int], Optional[date]] = {}
    cache_sel: Dict[Tuple[date, int], Optional[date]] = {}

    for r in rows:
        if float(r.get("买入金额合计") or 0) <= 0:
            r["持有交易日数"] = ""
            continue
        r["持有交易日数"] = int(ui_n)
        buy_d = _parse_row_date(r.get("买入日"))
        last_d = _parse_row_date(r.get("末笔买入日"))
        try:
            buy_n = int(r.get("买入笔数") or 0)
        except (TypeError, ValueError):
            buy_n = 0
        # 有第二腿：从末笔买入日次日起算持仓 N 日
        anchor_d = buy_d
        if buy_n >= 2 and last_d is not None:
            anchor_d = last_d
        elif last_d is not None and buy_d is not None and last_d > buy_d:
            anchor_d = last_d
        end_d: Optional[date] = None
        if anchor_d is not None:
            bkey = (anchor_d, engine_n)
            if bkey not in cache_buy:
                _s, e, msg = trading_day_window_from_start(anchor_d, engine_n)
                if e is None:
                    warns.append(
                        f"{r.get('代码') or ''} 锚定买入日 {anchor_d}: {msg or '无法推算持仓结束日'}"
                    )
                cache_buy[bkey] = e
            end_d = cache_buy.get(bkey)
        else:
            sel_d = _parse_row_date(r.get("选股日"))
            if sel_d is None:
                note = str(r.get("备注") or "").strip()
                r["备注"] = (note + "；" if note else "") + "无买入日/选股日，无法推算持仓结束日"
                continue
            key = (sel_d, ui_n)
            if key not in cache_sel:
                _s, e, msg = backtest_window_from_selection_day(
                    sel_d,
                    start_next_trading_day=True,
                    hold_trading_days=ui_n,
                )
                if e is None:
                    warns.append(f"选股日 {sel_d}: {msg or '无法推算持仓结束日'}")
                cache_sel[key] = e
            end_d = cache_sel.get(key)
        if end_d is not None:
            end_s = end_d.strftime("%Y-%m-%d")
            r["end_date"] = end_s
            r["计划持仓结束日"] = end_s
    return warns


def _nth_trading_day_after(sel: date, n: int) -> date:
    """
    选股日当日不计入；从次日开始往后数，第 n 个交易日（按 A 股真实交易日历；取不到则退化为周一～周五）。
    n=3 即「选股日后的第三个交易日」。
    """
    if n <= 0:
        return sel
    # 优先使用项目内交易日判断（支持法定节假日）
    try:
        from utils.trading_day import is_tradeday  # type: ignore
    except Exception:
        is_tradeday = None  # type: ignore
    d = sel
    counted = 0
    while counted < n:
        d += timedelta(days=1)
        if is_tradeday is not None:
            try:
                if bool(is_tradeday(d)):
                    counted += 1
            except Exception:
                # 兜底：若交易日历失败，退化为工作日判断
                if d.weekday() < 5:
                    counted += 1
        else:
            if d.weekday() < 5:
                counted += 1
    return d


def _num(s) -> float:
    try:
        return float(s) if s is not None and str(s).strip() != "" else 0.0
    except (TypeError, ValueError):
        return 0.0


def _int_vol(s) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _norm_code_6(code: str) -> str:
    s = (code or "").strip().replace(".", "")
    if len(s) < 6:
        return s.zfill(6) if s else ""
    return s[:6]


def _df_last_bar(df) -> Tuple[Optional[date], Optional[float]]:
    """日线 DF 最后一根有效 K：返回 (date, close)。"""
    if df is None or getattr(df, "empty", True):
        return None, None
    try:
        if "date" not in df.columns or "close" not in df.columns:
            return None, None
        row = df.iloc[-1]
        d = row["date"]
        if hasattr(d, "date"):
            try:
                d = d.date()
            except Exception:
                pass
        if not isinstance(d, date):
            d = _parse_row_date(d)
        px = float(row["close"])
        if d is None or px <= 0:
            return None, None
        return d, px
    except Exception:
        return None, None


def _fetch_last_available_closes(
    codes_6: List[str],
    *,
    through: Optional[date] = None,
) -> Tuple[Dict[str, Tuple[date, float]], str]:
    """
    每只股票取「能获取到的最后交易日」收盘。
    返回 ({code: (mark_date, close)}, warn)。
    through：若给定则只看到该日及之前（默认不截断，用缓存全部）。
    """
    codes_6 = [_norm_code_6(c) for c in codes_6 if (c or "").strip()]
    codes_6 = list(dict.fromkeys(codes_6))
    if not codes_6:
        return {}, ""

    out: Dict[str, Tuple[date, float]] = {}
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from utils.daily_cache_reader import load_daily_bars as load_daily_from_cache
    except Exception:
        load_daily_from_cache = None  # type: ignore

    missing: List[str] = []
    if load_daily_from_cache is not None:
        for c in codes_6:
            try:
                df = load_daily_from_cache(c, through_date=through, adjust=_fill_adjust())
            except Exception:
                df = None
            md, px = _df_last_bar(df)
            if md is not None and px is not None:
                out[c] = (md, px)
            else:
                missing.append(c)
    else:
        missing = list(codes_6)

    # data_provider：按 through 或今天取价（当作「最后可得」的兜底）
    if missing:
        as_of = through or date.today()
        still: List[str] = []
        try:
            from strategy_generator_app.backtest.data_provider import (
                get_historical_prices_for_date,
            )

            raw = get_historical_prices_for_date(missing, as_of, None)
            if isinstance(raw, dict) and "_error" not in raw:
                for c in missing:
                    p = (raw.get(c) or {}).get("current") or (raw.get(c) or {}).get("最新价")
                    try:
                        if p is not None and float(p) > 0:
                            out[c] = (as_of, float(p))
                            continue
                    except Exception:
                        pass
                    still.append(c)
            else:
                still = list(missing)
        except Exception:
            still = list(missing)
        missing = still

    warn = ""
    if missing:
        warn = (
            f"部分代码无可用日线收盘（示例）: "
            f"{missing[:12]}{'…' if len(missing) > 12 else ''}"
        )
    if not out and codes_6:
        warn = "未能取得任何未清仓股票的最后可得收盘价（已优先 daily_cache）"
    return out, warn


def _fetch_close_prices(codes_6: List[str], as_of: date) -> Tuple[Dict[str, float], str]:
    """
    返回 ({code: close}, error_msg)。error_msg 非空表示整体失败或部分说明。

    优先 daily_cache（与 builtin / 均线回填一致），缺的再尝试 data_provider，
    最后才回退 xtquant（无 QMT 时不会拖垮整次汇总）。
    """
    codes_6 = [_norm_code_6(c) for c in codes_6 if (c or "").strip()]
    codes_6 = list(dict.fromkeys(codes_6))
    if not codes_6:
        return {}, ""

    out: Dict[str, float] = {}
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    def _close_on_date(df, day: date) -> Optional[float]:
        if df is None or getattr(df, "empty", True):
            return None
        try:
            if "date" not in df.columns or "close" not in df.columns:
                return None
            m = df[df["date"] == day]
            if m is None or len(m) == 0:
                return None
            v = float(m.iloc[-1]["close"])
            return v if v > 0 else None
        except Exception:
            return None

    # 1) daily_cache：先精确日；没有则退化为 through_date 内最后一根（数据不全时）
    try:
        from utils.daily_cache_reader import load_daily_from_cache
    except Exception:
        load_daily_from_cache = None  # type: ignore

    missing: List[str] = []
    if load_daily_from_cache is not None:
        for c in codes_6:
            try:
                df = load_daily_from_cache(c, through_date=as_of, adjust=_fill_adjust())
            except Exception:
                df = None
            px = _close_on_date(df, as_of)
            if px is not None:
                out[c] = px
            else:
                # 停牌等：用 as_of 及之前最后一根收盘（through_date 已截断）
                _md, last_px = _df_last_bar(df)
                if last_px is not None and float(last_px) > 0:
                    out[c] = float(last_px)
                else:
                    missing.append(c)
    else:
        missing = list(codes_6)

    # 2) data_provider 日线视角（内部也会优先 cache / on-demand）
    if missing:
        still: List[str] = []
        try:
            from strategy_generator_app.backtest.data_provider import (
                get_historical_prices_for_date,
            )

            raw = get_historical_prices_for_date(missing, as_of, None)
            if isinstance(raw, dict) and "_error" not in raw:
                for c in missing:
                    p = (raw.get(c) or {}).get("current") or (raw.get(c) or {}).get("最新价")
                    try:
                        if p is not None and float(p) > 0:
                            out[c] = float(p)
                            continue
                    except Exception:
                        pass
                    still.append(c)
            else:
                still = list(missing)
        except Exception:
            still = list(missing)
        missing = still

    # 3) xtquant 仅补缺（QMT 未开时跳过，不把整次盯市标成失败）
    if missing:
        try:
            import xtquant.xtdata as xtdata
            import pandas as pd

            def _full_code(code_6: str) -> str:
                code_6 = (code_6 or "").strip().zfill(6)
                return f"{code_6}.SH" if code_6.startswith("6") else f"{code_6}.SZ"

            full_codes = [_full_code(c) for c in missing]
            start_str = as_of.strftime("%Y%m%d")
            end_str = start_str
            for fc in full_codes:
                try:
                    xtdata.download_history_data(fc, "1d", start_str, end_str)
                except Exception:
                    pass
            try:
                df_map = xtdata.get_market_data_ex(
                    [],
                    full_codes,
                    period="1d",
                    start_time=start_str,
                    end_time=end_str,
                    count=-1,
                )
            except Exception:
                df_map = {}
            still2: List[str] = []
            for c6, fc in zip(missing, full_codes):
                try:
                    if not df_map or fc not in df_map or len(df_map[fc]) == 0:
                        still2.append(c6)
                        continue
                    data = pd.DataFrame(df_map[fc])
                    if len(data) == 0:
                        still2.append(c6)
                        continue
                    row = data.iloc[-1]
                    close = None
                    for col in ("close", "收盘价", "Close", "CLOSE"):
                        if col in data.columns:
                            close = row[col]
                            break
                    if close is None or float(close) <= 0:
                        still2.append(c6)
                        continue
                    out[c6] = float(close)
                except Exception:
                    still2.append(c6)
            missing = still2
        except Exception:
            # 无 xtquant / 连不上：保留 missing，由下方 warn 说明
            pass

    warn = ""
    if missing:
        warn = (
            f"部分代码缺 {as_of} 收盘价（示例）: "
            f"{missing[:12]}{'…' if len(missing) > 12 else ''}"
        )
    if not out and codes_6:
        warn = (
            f"未能取得 {as_of} 收盘价（已优先 daily_cache；"
            "若本地无该日K线且未开 QMT，未清仓收益率可能为空）"
        )
    return out, warn


def build_position_corrected_ledger(buy_path: Path, sell_path: Path) -> List[dict]:
    """
    合并买卖 CSV 后按「选股日+代码」时间序重算「交易后持仓」。

    买入侧单独导出时 position_after 只含买入路径（未扣已卖），与接续卖出对照会虚高；
    本流水用买卖合并回放得到真实持仓。

    若卖出 CSV 已含「买入注入」接续流水：以卖出 CSV 为完整账本，不再并入买入 CSV，
    避免同一笔买入（注入 09:30 vs 真实成交时刻）被双计导致持仓虚高。
    """
    buy_rows = _read_rows(buy_path)
    sell_rows = _read_rows(sell_path)

    def _side_norm(r: dict) -> str:
        s = (r.get("方向") or r.get("side") or "").strip().lower()
        if s in ("买入", "buy", "b"):
            return "买入"
        if s in ("卖出", "sell", "s"):
            return "卖出"
        return ""

    def _is_inject_buy(r: dict) -> bool:
        name = str(r.get("规则名") or r.get("rule_name") or "")
        trig = str(r.get("触发信息") or "")
        reason = str(r.get("reason") or "")
        if "买入注入" in name:
            return True
        if "接续卖出" in trig and ("注入" in trig or "建仓流水" in trig):
            return True
        if reason in ("chain_buy_fill", "chain_buy_fill_opening"):
            return True
        return False

    sell_has_inject = any(
        _side_norm(r) == "买入" and _is_inject_buy(r) for r in sell_rows
    )

    def _code6(r: dict) -> str:
        code = (r.get("代码") or r.get("code") or "").strip()
        if code.endswith(".0"):
            code = code[:-2]
        if code.isdigit():
            code = code.zfill(6)
        return code

    def _row_key(r: dict) -> Tuple[str, str, str, str, str, int, str]:
        sel = _norm_sel_key(_sel_from_row(r))
        code = _code6(r)
        d = _parse_row_date(r.get("日期") or r.get("date"))
        ds = d.isoformat() if d else str(r.get("日期") or r.get("date") or "").strip()[:10]
        tm = _norm_time_str(r.get("时间") or r.get("time") or "")
        side = _side_norm(r)
        vol = _int_vol(r.get("数量") or r.get("volume"))
        # 同秒两腿（OPEN50+LU10）常同量，须带规则名/腿键，否则第二笔会被去重丢掉
        leg = str(r.get("腿键") or r.get("leg_key") or "").strip()
        rule = str(r.get("规则名") or r.get("rule_name") or "").strip()
        return (sel, code, ds, tm, side, vol, leg or rule)

    # 卖出侧已是完整接续账本时，只回放卖出 CSV
    sources: List[Tuple[str, List[dict]]]
    if sell_has_inject:
        sources = [("sell", sell_rows)]
    else:
        sources = [("buy", buy_rows), ("sell", sell_rows)]

    seen = set()
    merged: List[dict] = []
    for src, rows in sources:
        for r in rows:
            side = _side_norm(r)
            if side not in ("买入", "卖出"):
                continue
            # 汇总占位 0 股卖出不参与持仓回放
            if side == "卖出" and _int_vol(r.get("数量") or r.get("volume")) <= 0:
                continue
            k = _row_key(r)
            if not k[0] or not k[1]:
                continue
            if k in seen:
                continue
            seen.add(k)
            nr = dict(r)
            nr["_src"] = src
            nr["_side"] = side
            nr["_sort_sel"] = k[0]
            nr["_sort_code"] = k[1]
            nr["_sort_date"] = k[2]
            nr["_sort_time"] = k[3] or "00:00:00"
            # 同时刻：先买后卖
            nr["_sort_side"] = 0 if side == "买入" else 1
            merged.append(nr)

    merged.sort(
        key=lambda r: (
            r.get("_sort_sel") or "",
            r.get("_sort_code") or "",
            r.get("_sort_date") or "",
            r.get("_sort_time") or "",
            int(r.get("_sort_side") or 0),
            str(r.get("触发信息") or ""),
        )
    )

    pos: Dict[Tuple[str, str], int] = defaultdict(int)
    out: List[dict] = []
    for r in merged:
        sel = _norm_sel_key(_sel_from_row(r))
        code = _code6(r)
        side = r["_side"]
        vol = _int_vol(r.get("数量") or r.get("volume"))
        key = (sel, code)
        if side == "买入":
            pos[key] = int(pos.get(key) or 0) + vol
        else:
            pos[key] = max(0, int(pos.get(key) or 0) - vol)
        old_pa = r.get("交易后持仓")
        if old_pa is None or str(old_pa).strip() == "":
            old_pa = r.get("position_after")
        row_out = {
            "选股日": sel,
            "日期": r.get("_sort_date") or "",
            "时间": r.get("_sort_time") or "",
            "代码": code,
            "股票名称": (r.get("股票名称") or r.get("stock_name") or "").strip(),
            "方向": side,
            "价格": r.get("价格") if r.get("价格") not in (None, "") else r.get("price"),
            "数量": vol,
            "金额": r.get("金额") if r.get("金额") not in (None, "") else r.get("amount"),
            "交易后持仓(原)": old_pa,
            "交易后持仓": int(pos[key]),
            "规则名": r.get("规则名") or r.get("rule_name") or "",
            "腿键": r.get("腿键") or r.get("leg_key") or "",
            "触发信息": r.get("触发信息") or "",
            "来源文件": "买入CSV" if r.get("_src") == "buy" else "卖出CSV",
        }
        out.append(row_out)
    return out


def aggregate(buy_path: Path, sell_path: Path) -> List[dict]:
    buy_rows = _read_rows(buy_path)
    sell_rows = _read_rows(sell_path)

    st: Dict[Tuple[str, str], dict] = defaultdict(
        lambda: {
            "buy_amt": 0.0,
            "sell_amt": 0.0,
            "buy_vol": 0,
            "sell_vol": 0,
            "buy_n": 0,
            "sell_n": 0,
            "buy_time_min": "",
            "buy_date": None,
            "last_buy_date": None,
            "buy_price": None,
            "trigger_info": "",
            "end_date": None,  # 严格模式：仅来自卖出明细 end_date 列
        }
    )

    for r in buy_rows:
        side = (r.get("方向") or "").strip()
        if side != "买入":
            continue
        # 无选股日时用买入成交日兜底（马总盘中等当日扫当日买）
        sel = _norm_sel_key(_sel_from_row(r, fallback_trade_date=True))
        code = _code6_from_row(r)
        if not sel or not code:
            continue
        k = (sel, code)
        st[k]["buy_amt"] += _num(r.get("金额"))
        st[k]["buy_vol"] += _int_vol(r.get("数量"))
        st[k]["buy_n"] += 1
        bt = _norm_time_str(r.get("time") or r.get("时间") or r.get("成交时间"))
        bd = _parse_row_date(
            r.get("date")
            or r.get("日期")
            or r.get("start_date")
            or r.get("买入日期")
        )
        buy_px = _num(r.get("价格") or r.get("price") or 0)
        trig = str(r.get("触发信息") or "").strip()
        if bd is not None:
            cur_last = st[k].get("last_buy_date")
            if cur_last is None or bd >= cur_last:
                st[k]["last_buy_date"] = bd
            # 无时间戳时仍记下首买日，避免末笔有、首买空
            if st[k].get("buy_date") is None:
                st[k]["buy_date"] = bd
        if bt:
            cur_bt = (st[k].get("buy_time_min") or "").strip()
            cur_bd = st[k].get("buy_date")
            # 取「最早买入」：先比日期再比时间
            take = False
            if not cur_bt and cur_bd is None:
                take = True
            elif bd is not None and cur_bd is None:
                take = True
            elif bd is not None and cur_bd is not None and bd < cur_bd:
                take = True
            elif bd is not None and cur_bd is not None and bd == cur_bd and bt < cur_bt:
                take = True
            elif bd is None and cur_bd is None and ((not cur_bt) or bt < cur_bt):
                take = True
            if take:
                st[k]["buy_time_min"] = bt
                if bd is not None:
                    st[k]["buy_date"] = bd
                if buy_px > 0:
                    st[k]["buy_price"] = buy_px
                if trig:
                    st[k]["trigger_info"] = trig
        for fk in TB_SUMMARY_FIELDS:
            val = str(r.get(fk) or "").strip()
            if val and not str(st[k].get(fk) or "").strip():
                st[k][fk] = val

    # 买入侧 (选股日,代码) → 供卖出无选股日时回挂
    buy_sels_by_code: Dict[str, List[Tuple[str, Optional[date]]]] = defaultdict(list)
    for (sel, code), v in st.items():
        if int(v.get("buy_n") or 0) <= 0:
            continue
        buy_sels_by_code[code].append((sel, v.get("buy_date")))

    for r in sell_rows:
        side = (r.get("方向") or "").strip()
        if side != "卖出":
            continue
        code = _code6_from_row(r)
        if not code:
            continue
        sel = _norm_sel_key(_sel_from_row(r))
        sell_d = _parse_row_date(r.get("日期") or r.get("date"))
        ed = _parse_row_date(r.get("end_date"))
        sell_vol = _int_vol(r.get("数量") or r.get("volume"))
        # 与持仓回放一致：汇总占位 0 股卖出不计入清仓，但仍可写 end_date
        if sell_vol <= 0:
            if sel:
                k = (sel, code)
                if ed and st[k]["end_date"] is None:
                    st[k]["end_date"] = ed
            else:
                _allocate_sell_fifo(
                    st,
                    buy_sels_by_code,
                    code=code,
                    sell_vol=0,
                    sell_amt=0.0,
                    sell_d=sell_d,
                    end_d=ed,
                )
            continue
        sell_amt = _num(r.get("金额"))
        if sel:
            # 买卖两侧都有真实选股日：按原键合并
            _apply_sell_to_group(
                st, (sel, code), sell_vol=sell_vol, sell_amt=sell_amt, end_d=ed
            )
        else:
            # live_align / 空选股日：按代码 FIFO 挂回买入腿
            _allocate_sell_fifo(
                st,
                buy_sels_by_code,
                code=code,
                sell_vol=sell_vol,
                sell_amt=sell_amt,
                sell_d=sell_d,
                end_d=ed,
            )

    out: List[dict] = []
    for (sel, code) in sorted(st.keys(), key=lambda x: (x[0], x[1])):
        v = st[(sel, code)]
        buy_amt = round(v["buy_amt"], 2)
        sell_amt = round(v["sell_amt"], 2)
        rem = v["buy_vol"] - v["sell_vol"]
        net_cash = round(sell_amt - buy_amt, 2)
        if rem > 0:
            note = f"未清仓，余{rem}股"
        elif rem < 0:
            # 常见于：回测使用了“初始持仓/接续回测”，本轮卖出包含上一轮带来的持仓，
            # 因而可能出现“卖出数量 > 本轮买入数量”。这并不一定是 CSV 不匹配。
            if v["buy_vol"] <= 0 and v["sell_vol"] > 0:
                note = f"卖出来自初始/上一轮持仓（本轮无买入），超出{ -rem }股"
            else:
                note = f"卖出数量多于买入{ -rem }股（可能含初始/上一轮持仓；若非接续回测再核对CSV）"
        else:
            note = "已清仓" if v["buy_vol"] > 0 else ""
        ed = v.get("end_date")
        end_s = ed.strftime("%Y-%m-%d") if ed else ""
        # 零卖出时卖出 CSV 无 end_date 属正常；盯市改走「最后可得收盘」，不再写误导备注
        if v.get("end_date_warn"):
            note = (note + "；" if note else "") + "卖出明细中 end_date 不一致，已取首次出现值"
        row_out = {
            "选股日": _norm_sel_key(sel),
            "end_date": end_s,
            "代码": _norm_code_6(code) or code,
            "买入时间": (v.get("buy_time_min") or ""),
            "买入笔数": v["buy_n"],
            "卖出笔数": v["sell_n"],
            "买入金额合计": buy_amt,
            "卖出金额合计": sell_amt,
            "买入数量合计": v["buy_vol"],
            "卖出数量合计": v["sell_vol"],
            "剩余持仓数量": rem,
            "净现金流_卖减买": net_cash,
            "盯市日期": "",
            "盯市类型": "",
            "收盘价": "",
            "剩余市值_盯市": "",
            "收益率pct": "",
            "备注": note,
            "持有交易日数": "",
            "计划持仓结束日": "",
        }
        bd = v.get("buy_date")
        row_out["买入日"] = bd.strftime("%Y-%m-%d") if bd else ""
        lbd = v.get("last_buy_date")
        row_out["末笔买入日"] = lbd.strftime("%Y-%m-%d") if lbd else ""
        if end_s:
            row_out["计划持仓结束日"] = end_s
        bp = v.get("buy_price")
        row_out["买入成交价"] = round(float(bp), 2) if bp else ""
        row_out["触发信息"] = str(v.get("trigger_info") or "")
        for fk in TB_SUMMARY_FIELDS:
            row_out[fk] = str(v.get(fk) or "")
        out.append(row_out)
    return out


def aggregate_with_hold_sell_filter(
    buy_path: Path,
    sell_path: Path,
    hold_from_next_day: int,
) -> Tuple[List[dict], dict]:
    """按持仓窗截断卖出后再汇总。

    解决卖出 CSV 选股日为 live_align 时，FIFO 把上一轮卖出挂到本轮买入、
    出现「卖出数量多于买入」的问题。截断规则与 summarize_hold_days_from_trades
    一致：卖出不得早于该腿买入日，且不得晚于选股锚定结束日（多腿按末笔买入起算）。
    """
    import tempfile

    from tools.summarize_hold_days_from_trades import (  # noqa: WPS433
        _write_csv,
        filter_sells_within_hold,
    )

    hold_n = max(1, int(hold_from_next_day))
    buy_rows = _read_rows(buy_path)
    sell_rows = _read_rows(sell_path)
    filt, st = filter_sells_within_hold(buy_rows, sell_rows, hold_n)
    with tempfile.TemporaryDirectory() as td:
        sell_filtered = Path(td) / f"sell_hold{hold_n}_filtered.csv"
        _write_csv(filt, sell_filtered)
        rows = aggregate(buy_path, sell_filtered)
    return rows, st


def _uncleared_codes(rows: List[dict]) -> List[str]:
    codes: List[str] = []
    for r in rows:
        if int(r.get("剩余持仓数量") or 0) <= 0:
            continue
        code = _norm_code_6(str(r.get("代码") or ""))
        if code:
            codes.append(code)
    return list(dict.fromkeys(codes))


def _last_trading_day_on_or_before(d: date) -> date:
    """含 d 的最近交易日（日历失败则退化为周一～周五）。"""
    try:
        from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted

        lo = d - timedelta(days=40)
        lst = get_trading_dates_in_range_sorted(lo, d)
        if lst:
            return lst[-1]
    except Exception:
        pass
    try:
        from trading_calendar import get_trading_dates_in_range_sorted as _g2  # type: ignore

        lo = d - timedelta(days=40)
        lst = _g2(lo, d)
        if lst:
            return lst[-1]
    except Exception:
        pass
    x = d
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x


def _resolve_hold_mark_as_of(
    end_d: Optional[date],
    *,
    data_cap: Optional[date] = None,
) -> Tuple[Optional[date], bool]:
    """持仓结束日 → 实际盯市日。

    若结束日晚于「数据截止日」（默认今天及之前最近交易日），则按截止日临时盯市，
    返回 (实际盯市日, True=未到期临时盯市)。
    """
    if end_d is None:
        return None, False
    cap = data_cap or _last_trading_day_on_or_before(date.today())
    if end_d > cap:
        return cap, True
    return end_d, False


def _lookup_mark_price(
    prices_by_mark: Dict[date, Dict[str, float]],
    code: str,
    preferred: Optional[date] = None,
) -> Tuple[Optional[date], float]:
    """在 prices_by_mark 中取某代码收盘。

    preferred 有值时只取该日（持仓结束日/第N日/未到期临时日），禁止回退到其它盯市日
    （否则会把无关的「最后可得」日误当成目标盯市）。
    preferred 为空时才扫表（最后可得模式）。
    """
    if preferred is not None:
        px = float((prices_by_mark.get(preferred) or {}).get(code) or 0)
        if px > 0:
            return preferred, px
        return None, 0.0
    for md in sorted(prices_by_mark.keys(), reverse=True):
        px = float((prices_by_mark.get(md) or {}).get(code) or 0)
        if px > 0:
            return md, px
    return None, 0.0


def _build_prices_by_mark_date(
    rows: List[dict],
    mark_n: int = 3,
    *,
    use_nth_trading_day: bool = False,
    use_last_available: bool = False,
    data_cap: Optional[date] = None,
) -> Tuple[Dict[date, Dict[str, float]], str]:
    """
    未清仓行拉收盘价。

    - 默认：按行内 end_date；若结束日尚未到来/无该日K线，则按 data_cap（默认今天及之前
      最近交易日）临时盯市
    - use_nth_trading_day：选股日后第 N 个交易日
    - use_last_available：每票日线最后可得交易日（排查用）
    """
    if use_nth_trading_day:
        use_last_available = False

    if use_last_available and not use_nth_trading_day:
        last_map, warn = _fetch_last_available_closes(_uncleared_codes(rows))
        prices_by_mark: Dict[date, Dict[str, float]] = {}
        for code, (md, px) in last_map.items():
            prices_by_mark.setdefault(md, {})[code] = px
        return prices_by_mark, warn

    cap = data_cap or _last_trading_day_on_or_before(date.today())
    by_mark: Dict[date, List[str]] = defaultdict(list)
    mark_n = int(mark_n or 0)
    if mark_n < 0:
        mark_n = 0
    for r in rows:
        if int(r.get("剩余持仓数量") or 0) <= 0:
            continue
        code = _norm_code_6(str(r.get("代码") or ""))
        if not code:
            continue
        if use_nth_trading_day:
            sel_d = _parse_row_date(r.get("选股日"))
            if not sel_d:
                continue
            md = _nth_trading_day_after(sel_d, mark_n)
            md, _interim = _resolve_hold_mark_as_of(md, data_cap=cap)
        else:
            end_d = _parse_row_date(r.get("end_date"))
            if not end_d:
                continue
            md, _interim = _resolve_hold_mark_as_of(end_d, data_cap=cap)
        if not md:
            continue
        by_mark[md].append(code)

    prices_by_mark = {}
    warns: List[str] = []
    for md in sorted(by_mark.keys()):
        codes = list(dict.fromkeys(by_mark[md]))
        pm, err = _fetch_close_prices(codes, md)
        prices_by_mark[md] = pm
        if err:
            warns.append(f"{md}: {err}")
    summary = "; ".join(warns)
    return prices_by_mark, summary


def apply_mark_and_returns(
    rows: List[dict],
    prices_by_mark: Dict[date, Dict[str, float]],
    price_warn: str,
    mark_n: int = 3,
    *,
    use_nth_trading_day: bool = False,
    use_last_available: bool = False,
    data_cap: Optional[date] = None,
) -> None:
    mark_n = int(mark_n or 0)
    if mark_n < 0:
        mark_n = 0
    if use_nth_trading_day:
        use_last_available = False
    cap = data_cap or _last_trading_day_on_or_before(date.today())

    for r in rows:
        rem = int(r.get("剩余持仓数量") or 0)
        buy_amt = float(r.get("买入金额合计") or 0)
        sell_amt = float(r.get("卖出金额合计") or 0)
        code = _norm_code_6(str(r.get("代码") or ""))

        # 已清仓：只算卖出收益，不写盯市日
        if rem == 0 and buy_amt > 0:
            r["盯市日期"] = ""
            r["收盘价"] = ""
            r["剩余市值_盯市"] = ""
            r["盯市类型"] = "已清仓"
            r["收益率pct"] = round((sell_amt - buy_amt) / buy_amt * 100, 4)
            continue

        if rem < 0 or buy_amt <= 0:
            continue

        base_note = (r.get("备注") or "").strip()
        # 去掉旧版「缺 end_date」误导备注（零卖出时也会被标上）
        if "未清仓但卖出明细缺少 end_date" in base_note:
            base_note = base_note.replace("；未清仓但卖出明细缺少 end_date（严格模式不回退买入）", "")
            base_note = base_note.replace("未清仓但卖出明细缺少 end_date（严格模式不回退买入）", "")
            base_note = base_note.strip("；").strip()
        for old in (
            "；持仓结束日盯市",
            "持仓结束日盯市",
            "；持仓未到期，按最近可得交易日盯市",
            "持仓未到期，按最近可得交易日盯市",
        ):
            if old in base_note:
                base_note = base_note.replace(old, "")
        # 去掉「（计划结束日=…）」残留
        base_note = re.sub(r"；?（计划结束日=\d{4}-\d{2}-\d{2}）", "", base_note)
        base_note = base_note.strip("；").strip()

        preferred: Optional[date] = None
        interim = False
        planned_end: Optional[date] = None
        if use_nth_trading_day:
            sel_d = _parse_row_date(r.get("选股日"))
            if not sel_d:
                r["备注"] = base_note + "；无法解析选股日，无法推算盯市日"
                continue
            planned_end = _nth_trading_day_after(sel_d, mark_n)
            preferred, interim = _resolve_hold_mark_as_of(planned_end, data_cap=cap)
        elif not use_last_available:
            planned_end = _parse_row_date(r.get("end_date"))
            if not planned_end:
                r["备注"] = (
                    base_note
                    + "；未清仓但缺少持仓结束日 end_date（请设置持有交易日数后重算）"
                )
                continue
            preferred, interim = _resolve_hold_mark_as_of(planned_end, data_cap=cap)
        # use_last_available：preferred 保持 None，由 lookup 扫表

        mark_d, close = _lookup_mark_price(prices_by_mark, code, preferred)
        if mark_d is None or close <= 0:
            extra = "；无可用盯市收盘价，收益率未算"
            if interim and planned_end is not None:
                extra = (
                    f"；持仓未到期（计划结束日={planned_end}）且最近交易日无收盘价，收益率未算"
                )
            elif planned_end is not None and not use_last_available:
                extra = f"；持仓结束日 {planned_end} 无可用收盘价，收益率未算"
            if price_warn:
                extra += f"（{price_warn}）"
            r["备注"] = (base_note + extra) if base_note else extra.lstrip("；")
            continue

        r["盯市日期"] = mark_d.strftime("%Y-%m-%d")
        note = base_note or f"未清仓，余{rem}股"
        if use_last_available:
            r["盯市类型"] = "最后可得"
            if "最后可得收盘盯市" not in note:
                note = (note + "；" if note else "") + "最后可得收盘盯市"
        elif interim and planned_end is not None:
            r["盯市类型"] = "未到期临时"
            r["计划持仓结束日"] = planned_end.strftime("%Y-%m-%d")
            note = (
                (note + "；" if note else "")
                + f"持仓未到期，按最近可得交易日临时估值（不是持仓结束；计划结束日={planned_end}）"
            )
        elif not use_nth_trading_day:
            r["盯市类型"] = "持仓到期"
            if planned_end is not None:
                r["计划持仓结束日"] = planned_end.strftime("%Y-%m-%d")
            if "持仓结束日盯市" not in note:
                note = (note + "；" if note else "") + "持仓结束日盯市"
        else:
            r["盯市类型"] = "选股日后第N日"
        r["备注"] = note

        mv = round(rem * close, 2)
        r["收盘价"] = round(close, 4)
        r["剩余市值_盯市"] = mv
        pnl = sell_amt + mv - buy_amt
        r["收益率pct"] = round(pnl / buy_amt * 100, 4)


def _norm_sel_str(val) -> str:
    d = _parse_row_date(val)
    if not d:
        s = str(val).strip() if val is not None else ""
        return s[:10] if len(s) >= 10 else s
    return d.strftime("%Y-%m-%d")


def _pick_col(df, candidates: List[str]) -> Optional[str]:
    cols = {str(c).strip(): c for c in df.columns}
    for name in candidates:
        if name in cols:
            return cols[name]
    # 宽松匹配：忽略空格
    norm = {str(c).replace(" ", ""): c for c in df.columns}
    for name in candidates:
        k = name.replace(" ", "")
        if k in norm:
            return norm[k]
    return None


def _read_selection_file(path: Path):
    """
    读入选股文件（Excel/CSV），返回 DataFrame。
    允许列名差异：选股日/日期、代码/股票代码 等。
    """
    import pandas as pd

    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        # 默认读第一个 sheet；用户导出的选股文件通常第一张就是数据
        return pd.read_excel(path)
    # CSV：兼容常见编码
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:
            last_err = e
            continue
    raise last_err or OSError(path)


def _export_selection_cell_value(v):
    import pandas as pd

    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    if isinstance(v, datetime):
        return v.date().strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    return v


def _build_selection_file_index(df) -> Tuple[Dict[Tuple[str, str], dict], List[str]]:
    sel_col = _pick_col(
        df,
        [
            "选股日",
            "screen_as_of",
            "基准日",
            "选股基准日",
            "选股日期",
            "交易日期",
            "trade_date",
            "日期",
            "sel_date",
        ],
    )
    code_col = _pick_col(df, ["代码", "股票代码", "证券代码", "code", "stock_code"])
    if not sel_col or not code_col:
        return {}, [str(c) for c in df.columns]
    col_order = [str(c) for c in df.columns]
    index: Dict[Tuple[str, str], dict] = {}
    for _, row in df.iterrows():
        sel = _norm_sel_str(row.get(sel_col))
        code = _norm_code_6(str(row.get(code_col) or ""))
        if not sel or not code:
            continue
        key = (sel, code)
        if key in index:
            continue
        index[key] = {
            str(col): _export_selection_cell_value(row.get(col)) for col in df.columns
        }
    return index, col_order


def apply_first_selection_date_from_file(rows: List[dict], selection_file: Path) -> str:
    """按股票代码从选股文件回填「首次选股日」（该码在文件中最早出现的选股日）。

    实盘对齐滚动等导出若曾把「选股日」写成买入日，本函数另写「首次选股日」便于对照；
    真正纠正「选股日」列请看 ``repair_selection_dates_from_file``。
    """
    try:
        df = _read_selection_file(selection_file)
    except Exception as e:
        return f"读取选股文件失败（首次选股日）：{type(e).__name__}: {e}"
    if df is None or len(df) == 0:
        return "选股文件为空，未回填首次选股日。"

    day_col = None
    code_col = None
    for c in df.columns:
        n = str(c).strip()
        nl = n.lower()
        if day_col is None and (
            n in ("选股日", "选股日期", "日期", "as_of")
            or "选股日" in n
            or nl in ("selection_date", "screen_as_of", "as_of")
        ):
            day_col = c
        if code_col is None and (
            n in ("代码", "股票代码", "证券代码", "code", "stock_code")
            or ("代码" in n and "概念" not in n)
        ):
            code_col = c
    if day_col is None or code_col is None:
        return "选股文件缺少选股日/代码列，未回填首次选股日。"

    first_by_code: Dict[str, date] = {}
    for _, row in df.iterrows():
        c6 = _norm_code_6(str(row.get(code_col) or ""))
        d = _parse_row_date(row.get(day_col))
        if not c6 or d is None:
            continue
        prev = first_by_code.get(c6)
        if prev is None or d < prev:
            first_by_code[c6] = d

    if not first_by_code:
        return "选股文件未能解析出任何首次选股日。"

    hit = 0
    for r in rows:
        c6 = _norm_code_6(str(r.get("代码") or ""))
        d0 = first_by_code.get(c6)
        if d0 is None:
            r.setdefault("首次选股日", "")
            continue
        r["首次选股日"] = d0.strftime("%Y-%m-%d")
        hit += 1
    return f"已回填首次选股日：命中 {hit}/{len(rows)} 行（按代码取选股文件最早日）"


def repair_selection_dates_from_file(
    rows: List[dict],
    selection_file: Path,
    *,
    entry_window: int = 10,
) -> str:
    """纠正汇总表「选股日」：为空，或与买入日相同（旧实盘对齐导出用买入日兜底）时，
    按选股文件用「买入日前最近一次选股日」回填（可选限制在入场窗 entry_window 内）。
    """
    try:
        df = _read_selection_file(selection_file)
    except Exception as e:
        return f"读取选股文件失败（纠正选股日）：{type(e).__name__}: {e}"
    if df is None or len(df) == 0:
        return "选股文件为空，未纠正选股日。"

    day_col = None
    code_col = None
    for c in df.columns:
        n = str(c).strip()
        nl = n.lower()
        if day_col is None and (
            n in ("选股日", "选股日期", "日期", "as_of")
            or "选股日" in n
            or nl in ("selection_date", "screen_as_of", "as_of")
        ):
            day_col = c
        if code_col is None and (
            n in ("代码", "股票代码", "证券代码", "code", "stock_code")
            or ("代码" in n and "概念" not in n)
        ):
            code_col = c
    if day_col is None or code_col is None:
        return "选股文件缺少选股日/代码列，未纠正选股日。"

    dates_by_code: Dict[str, List[date]] = defaultdict(list)
    for _, row in df.iterrows():
        c6 = _norm_code_6(str(row.get(code_col) or ""))
        d = _parse_row_date(row.get(day_col))
        if not c6 or d is None:
            continue
        dates_by_code[c6].append(d)
    for c6, ds in list(dates_by_code.items()):
        dates_by_code[c6] = sorted(set(ds))

    if not dates_by_code:
        return "选股文件未能解析出选股日，未纠正。"

    try:
        ew = max(1, int(entry_window or 1))
    except (TypeError, ValueError):
        ew = 10

    # 交易日历：用于「选股日后第 1～ew 个交易日」覆盖买入日
    cal_dates = None
    try:
        try:
            from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted
        except ImportError:
            from trading_calendar import get_trading_dates_in_range_sorted  # type: ignore
        all_ds = [d for ds in dates_by_code.values() for d in ds]
        buy_ds = []
        for r in rows:
            bd = _parse_row_date(r.get("买入日") or r.get("date") or r.get("日期"))
            if bd is not None:
                buy_ds.append(bd)
        if all_ds or buy_ds:
            d0 = min(all_ds + buy_ds)
            d1 = max(all_ds + buy_ds)
            cal_dates = get_trading_dates_in_range_sorted(
                d0 - timedelta(days=5),
                d1 + timedelta(days=ew * 3 + 10),
            )
    except Exception:
        cal_dates = None

    def _in_entry_window(sel_d: date, buy_d: date) -> bool:
        if buy_d <= sel_d:
            return False
        if not cal_dates:
            # 无日历：日历日差粗判（约 ew*1.6 天）
            return 0 < (buy_d - sel_d).days <= max(ew * 2, ew + 5)
        # 选股日下一交易日起连续 ew 日
        after = [d for d in cal_dates if d > sel_d]
        if not after:
            return False
        window = after[:ew]
        return buy_d in window or (window and window[0] <= buy_d <= window[-1])

    fixed = 0
    skipped = 0
    for r in rows:
        c6 = _norm_code_6(str(r.get("代码") or ""))
        buy_d = _parse_row_date(r.get("买入日") or r.get("date") or r.get("日期"))
        sel_s = _norm_sel_key(str(r.get("选股日") or ""))
        buy_s = buy_d.strftime("%Y-%m-%d") if buy_d else ""
        need = (not sel_s) or (bool(buy_s) and sel_s == buy_s)
        if not need or not c6:
            continue
        cands = dates_by_code.get(c6) or []
        if not cands:
            skipped += 1
            continue
        chosen = None
        if buy_d is not None:
            before = [d for d in cands if d < buy_d]
            # 优先：买入落在该选股日入场窗内的最近选股日
            in_win = [d for d in before if _in_entry_window(d, buy_d)]
            if in_win:
                chosen = in_win[-1]
            elif before:
                chosen = before[-1]
        if chosen is None:
            chosen = cands[0]
        new_s = chosen.strftime("%Y-%m-%d")
        if new_s != sel_s:
            r["选股日"] = new_s
            fixed += 1
    return (
        f"已纠正选股日（空或等于买入日→选股文件）：{fixed} 行"
        + (f"；无匹配 {skipped} 行" if skipped else "")
    )


def apply_selection_file_fields(rows: List[dict], selection_file: Path) -> str:
    """
    对 rows 中每个 (选股日, 代码) 组合，从选股文件中找对应行，
    将选股文件中的**全部列**复制到 rows（找不到则跳过该行回填）。
    汇总侧已有字段（买卖金额、收益率等）在列名冲突时保留汇总值。
    另外按代码回填「首次选股日」（与表内选股日可能不同）。
    """
    repair_hint = repair_selection_dates_from_file(rows, selection_file)
    try:
        df = _read_selection_file(selection_file)
    except Exception as e:
        return f"{repair_hint}；读取选股文件失败：{type(e).__name__}: {e}"

    if df is None or len(df) == 0:
        return f"{repair_hint}；选股文件为空，未回填字段。"

    index, col_order = _build_selection_file_index(df)
    if not index:
        first_hint = apply_first_selection_date_from_file(rows, selection_file)
        return f"{repair_hint}；选股文件缺少关键列（需要「选股日/日期」与「代码/股票代码」），未回填明细列。{first_hint}"

    hit = 0
    # 优先使用项目内交易日判断（支持法定节假日）
    try:
        from utils.trading_day import is_tradeday  # type: ignore
    except Exception:
        is_tradeday = None  # type: ignore

    def _nth_after(start_d: date, end_d: date) -> int:
        """返回 end_d 相对 start_d 之后的第几个交易日（不含 start_d，当日为0）；失败则按工作日。"""
        if end_d <= start_d:
            return 0
        d0 = start_d
        n = 0
        while d0 < end_d:
            d0 += timedelta(days=1)
            if is_tradeday is not None:
                try:
                    if bool(is_tradeday(d0)):
                        n += 1
                except Exception:
                    if d0.weekday() < 5:
                        n += 1
            else:
                if d0.weekday() < 5:
                    n += 1
        return n

    lu_keys = ("涨停日期", "涨停日", "limit_up_date", "limit_date")

    for r in rows:
        k = (_norm_sel_str(r.get("选股日")), _norm_code_6(str(r.get("代码") or "")))
        m = index.get(k)
        if not m:
            r.setdefault("选股日为涨停后第几日", "")
            continue
        trade_fields = dict(r)
        for col in col_order:
            r[col] = m.get(col, "")
        r.update(trade_fields)
        lu_val = ""
        for lk in lu_keys:
            if lk in r and r.get(lk) not in (None, ""):
                lu_val = r.get(lk)
                break
        try:
            sel_d = _parse_row_date(r.get("选股日"))
            lu_d = _parse_row_date(lu_val)
            if sel_d and lu_d:
                r["选股日为涨停后第几日"] = _nth_after(lu_d, sel_d)
            else:
                r.setdefault("选股日为涨停后第几日", "")
        except Exception:
            r.setdefault("选股日为涨停后第几日", "")
        hit += 1

    first_hint = apply_first_selection_date_from_file(rows, selection_file)
    return (
        f"{repair_hint}；已从选股文件回填全部列：命中 {hit}/{len(rows)} 行；{first_hint}"
    )


def apply_ma_fields_from_daily_cache(rows: List[dict]) -> str:
    """
    按「选股日 + 代码」从 daily_cache 回填 5/10/20/30/60/120 日线。
    口径与策略早盘视角一致：均线用选股日之前收盘（不含选股日当日）。
    已有非空均线列则不覆盖（例如选股文件已带）。
    若本地 K 线不足（常见：cache 仅约 120 根导致 MA120 空），再尝试拉长历史合并。
    """
    if not rows:
        return "无汇总行，跳过均线回填。"

    # 保证列存在
    for r in rows:
        for col in MA_SUMMARY_FIELDS:
            r.setdefault(col, "")

    try:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from strategy_generator_app.backtest.data_provider import _full_code
        from utils.daily_cache_reader import (
            load_daily_bars as load_daily_from_cache,
            load_daily_xtdata_fallback,
        )
    except Exception as e:
        return f"均线回填跳过（无法导入日线模块）：{type(e).__name__}: {e}"

    import pandas as pd

    period_map = {
        "5日线": 5,
        "10日线": 10,
        "20日线": 20,
        "30日线": 30,
        "60日线": 60,
        "120日线": 120,
    }
    # 早盘 MA_n 需选股日前 (n-1) 根；MA120 → 119
    min_prior_for_ma120 = 119

    def _load_merged_df(code: str, sel_d: date):
        df = None
        try:
            df = load_daily_from_cache(code, through_date=sel_d)
        except Exception:
            df = None
        if df is None:
            try:
                df = load_daily_from_cache(_full_code(code), through_date=sel_d)
            except Exception:
                df = None

        def _prior_len(frame) -> int:
            if frame is None or getattr(frame, "empty", True) or "date" not in frame.columns:
                return 0
            dd = pd.to_datetime(frame["date"]).dt.date
            return int((dd < sel_d).sum())

        # 本地不足时补拉长历史（不覆盖已有 cache 写盘；xtdata 仅 mini 模式，见 load_daily_xtdata_fallback）
        if _prior_len(df) < min_prior_for_ma120:
            ext = None
            try:
                ext = load_daily_xtdata_fallback(
                    code, through_date=sel_d, history_days=500
                )
            except Exception:
                ext = None
            if ext is None:
                try:
                    ext = load_daily_xtdata_fallback(
                        _full_code(code), through_date=sel_d, history_days=500
                    )
                except Exception:
                    ext = None
            if ext is not None and not getattr(ext, "empty", True):
                if df is None or getattr(df, "empty", True):
                    df = ext
                else:
                    try:
                        df = (
                            pd.concat([df, ext], ignore_index=True)
                            .drop_duplicates(subset=["date"], keep="last")
                            .sort_values("date")
                        )
                    except Exception:
                        if _prior_len(ext) > _prior_len(df):
                            df = ext
            # builtin：请求大 QMT 补齐更长日线（同步参数加长后下次落盘才永久变长）
            if _prior_len(df) < min_prior_for_ma120:
                try:
                    from utils.data_sync_request import (
                        ensure_daily_dataframe,
                        use_on_demand_qmt_sync,
                    )

                    if use_on_demand_qmt_sync() and callable(ensure_daily_dataframe):
                        got = ensure_daily_dataframe(code, through_date=sel_d)
                        if got is not None and _prior_len(got) > _prior_len(df):
                            df = got
                except Exception:
                    pass
        return df

    by_day: Dict[date, List[dict]] = defaultdict(list)
    for r in rows:
        d = _parse_row_date(r.get("选股日"))
        if not d:
            continue
        by_day[d].append(r)

    hit = 0
    miss = 0
    ma120_miss = 0
    for sel_d, day_rows in by_day.items():
        for r in day_rows:
            if all(
                str(r.get(c) or "").strip() not in ("", "None", "nan")
                for c in MA_SUMMARY_FIELDS
            ):
                hit += 1
                continue
            code = _norm_code_6(str(r.get("代码") or ""))
            if not code:
                miss += 1
                continue
            df = _load_merged_df(code, sel_d)
            if df is None:
                miss += 1
                continue
            try:
                dfi = df.copy()
                if "date" not in dfi.columns:
                    miss += 1
                    continue
                dfi["_d"] = pd.to_datetime(dfi["date"]).dt.date
                dfi = dfi[dfi["_d"] <= sel_d]
                if dfi.empty:
                    miss += 1
                    continue
                close_col = "close" if "close" in dfi.columns else None
                if close_col is None:
                    for c in dfi.columns:
                        if str(c).lower() in ("close", "收盘", "收盘价"):
                            close_col = c
                            break
                if close_col is None:
                    miss += 1
                    continue
                prior = dfi[dfi["_d"] < sel_d]
                if prior.empty:
                    miss += 1
                    continue
                closes = prior[close_col].astype(float)
                filled = False
                for out_col, period in period_map.items():
                    if str(r.get(out_col) or "").strip() not in ("", "None", "nan"):
                        continue
                    days_needed = period - 1
                    if days_needed <= 0 or len(closes) < days_needed:
                        continue
                    r[out_col] = round(float(closes.iloc[-days_needed:].mean()), 2)
                    filled = True
                if str(r.get("120日线") or "").strip() in ("", "None", "nan"):
                    ma120_miss += 1
            except Exception:
                filled = False
            if filled:
                hit += 1
            else:
                miss += 1

    msg = f"已从 daily_cache 回填均线：命中约 {hit} 行，未命中约 {miss} 行"
    if ma120_miss:
        msg += (
            f"；其中 120日线仍缺约 {ma120_miss} 行"
            "（本地日线偏短时需大 QMT 按新参数重新同步，或开启 xtquant 补拉）"
        )
    return msg


def _parse_price_band_from_trigger(trigger: str) -> Tuple[Optional[float], Optional[float]]:
    s = str(trigger or "")
    m = re.search(r"带\s*=\s*\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]", s)
    if not m:
        return None, None
    try:
        return float(m.group(1)), float(m.group(2))
    except (TypeError, ValueError):
        return None, None


def _morning_ma_from_df(df, as_of: date, period: int = 5) -> Optional[float]:
    """早盘口径 MA：只用 as_of 之前收盘；与策略 _build_morning_row_from_df 一致用 (period-1) 根。"""
    if df is None or getattr(df, "empty", True) or period <= 1:
        return None
    try:
        import pandas as pd

        dfi = df.copy()
        if "date" not in dfi.columns:
            return None
        dfi["_d"] = pd.to_datetime(dfi["date"]).dt.date
        close_col = "close" if "close" in dfi.columns else None
        if close_col is None:
            return None
        prior = dfi[dfi["_d"] < as_of][close_col].astype(float)
        need = period - 1
        if len(prior) < need:
            return None
        return round(float(prior.iloc[-need:].mean()), 2)
    except Exception:
        return None


def apply_buy_day_ma5_ref_fields(rows: List[dict]) -> str:
    """
    回填「买入日5日线」= 买入日早盘 MA5（旧策略 MA5 重合/上穿参考价）。
    并解析触发信息中的价格带上下沿，计算成交相对买入日 MA5 的偏离%。
    """
    if not rows:
        return "无汇总行，跳过买入日MA5回填。"

    for r in rows:
        for col in BUY_DAY_MA5_FIELDS:
            r.setdefault(col, "")

    try:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from strategy_generator_app.backtest.data_provider import _full_code
        from utils.daily_cache_reader import (
            load_daily_bars as load_daily_from_cache,
            load_daily_xtdata_fallback,
        )
    except Exception as e:
        return f"买入日MA5回填跳过（无法导入日线模块）：{type(e).__name__}: {e}"

    # 按代码缓存日线，避免重复读
    df_by_code: Dict[str, Any] = {}
    hit = 0
    miss = 0

    def _df_for(code: str, through: date):
        key = f"{code}|{through.isoformat()}"
        if key in df_by_code:
            return df_by_code[key]
        df = None
        try:
            df = load_daily_from_cache(code, through_date=through)
        except Exception:
            df = None
        if df is None:
            try:
                df = load_daily_from_cache(_full_code(code), through_date=through)
            except Exception:
                df = None
        # 偏短则补拉（与选股日均线同一套回退；xtdata 仅 mini 模式）
        try:
            import pandas as pd

            prior_n = 0
            if df is not None and "date" in df.columns:
                prior_n = int((pd.to_datetime(df["date"]).dt.date < through).sum())
            if prior_n < 4:
                ext = None
                try:
                    ext = load_daily_xtdata_fallback(code, through_date=through, history_days=400)
                except Exception:
                    ext = None
                if ext is not None and not getattr(ext, "empty", True):
                    df = ext if df is None else (
                        pd.concat([df, ext], ignore_index=True)
                        .drop_duplicates(subset=["date"], keep="last")
                        .sort_values("date")
                    )
        except Exception:
            pass
        df_by_code[key] = df
        return df

    for r in rows:
        # 价格带（有则填）
        lo, hi = _parse_price_band_from_trigger(str(r.get("触发信息") or ""))
        if lo is not None and not str(r.get("价格带下沿") or "").strip():
            r["价格带下沿"] = round(lo, 2)
        if hi is not None and not str(r.get("价格带上沿") or "").strip():
            r["价格带上沿"] = round(hi, 2)

        buy_d = _parse_row_date(r.get("买入日"))
        if not buy_d:
            # 兼容：买入时间里若带日期
            buy_d = _parse_row_date(r.get("买入时间"))
        if buy_d and not str(r.get("买入日") or "").strip():
            r["买入日"] = buy_d.strftime("%Y-%m-%d")

        code = _norm_code_6(str(r.get("代码") or ""))
        if not buy_d or not code:
            miss += 1
            continue

        ma5 = None
        if str(r.get("买入日5日线") or "").strip() not in ("", "None", "nan"):
            try:
                ma5 = float(r.get("买入日5日线"))
            except (TypeError, ValueError):
                ma5 = None
        if ma5 is None:
            df = _df_for(code, buy_d)
            ma5 = _morning_ma_from_df(df, buy_d, 5)
            if ma5 is None:
                miss += 1
                continue
            r["买入日5日线"] = ma5
            hit += 1
        else:
            hit += 1

        # 成交相对偏离
        try:
            px = float(r.get("买入成交价") or r.get("价格") or 0)
            if px > 0 and ma5 and float(ma5) > 0:
                r["成交相对买入日MA5_pct"] = round((px / float(ma5) - 1.0) * 100.0, 3)
        except (TypeError, ValueError):
            pass

    return f"已回填买入日MA5重合参考：命中约 {hit} 行，未命中约 {miss} 行"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="按选股日汇总买卖 CSV（未清仓默认按买入日推算的持仓结束日盯市）"
    )
    ap.add_argument("--buy", required=True, help="买入侧成交明细 CSV")
    ap.add_argument("--sell", required=True, help="卖出侧成交明细 CSV")
    ap.add_argument("--out", default="", help="输出 CSV 路径；省略则打印到 stdout")
    ap.add_argument(
        "--use-nth-trading-day",
        action="store_true",
        help="未清仓按选股日后第 --mark-n 个交易日盯市",
    )
    ap.add_argument(
        "--last-available",
        action="store_true",
        help="未清仓改用日线最后可得收盘盯市（排查用；默认按买入日推算的持仓结束日）",
    )
    ap.add_argument(
        "--mark-at-end-date",
        action="store_true",
        help="（兼容旧开关）按持仓结束日盯市；现为默认，可省略",
    )
    ap.add_argument(
        "--mark-n",
        type=int,
        default=3,
        help="与 --use-nth-trading-day 联用：选股日后的第 N 个交易日（默认 3）",
    )
    ap.add_argument(
        "--hold-days",
        type=int,
        default=2,
        help="持有交易日数（买入次日起算，与下一轮接续同口径；默认 2）",
    )
    ap.add_argument(
        "--from-t0",
        action="store_true",
        help="无买入日回退选股日时：区间从选股日当日起（默认 T+1）",
    )
    args = ap.parse_args()

    buy_p = Path(args.buy)
    sell_p = Path(args.sell)
    if not buy_p.is_file():
        print(f"找不到买入文件: {buy_p}", file=sys.stderr)
        return 1
    if not sell_p.is_file():
        print(f"找不到卖出文件: {sell_p}", file=sys.stderr)
        return 1

    rows, sell_filt_st = aggregate_with_hold_sell_filter(
        buy_p, sell_p, hold_from_next_day=int(args.hold_days)
    )
    print(
        f"卖出截断: kept {sell_filt_st.get('sell_kept_full', 0)}"
        f"+partial{sell_filt_st.get('sell_kept_partial', 0)}"
        f" / in{sell_filt_st.get('sell_in', 0)}；"
        f"vol {sell_filt_st.get('vol_kept', 0)}/{sell_filt_st.get('vol_in', 0)}；"
        f"dropped {sell_filt_st.get('sell_dropped', 0)}",
        file=sys.stderr,
    )
    # 优先：按买入日 + 持有天数写 end_date；再可选覆盖为纯选股日窗
    end_warns = apply_hold_end_date_from_buy(
        rows, hold_from_next_day=int(args.hold_days)
    )
    if bool(args.from_t0):
        # 显式要求选股日 T0 窗时覆盖（少见）
        apply_end_date_from_trading_calendar(
            rows,
            from_t1=False,
            hold_n=int(args.hold_days),
        )
    for w in end_warns[:12]:
        print(f"⚠ {w}", file=sys.stderr)

    use_nth = bool(args.use_nth_trading_day)
    use_last = bool(args.last_available) and not use_nth
    prices_by_mark, price_warn = _build_prices_by_mark_date(
        rows,
        mark_n=int(args.mark_n),
        use_nth_trading_day=use_nth,
        use_last_available=use_last,
    )
    n_mark = len(prices_by_mark)
    if n_mark:
        if use_nth:
            print(
                f"未清仓行：盯市日 = 各「选股日」后第 {int(args.mark_n)} 个交易日，共 {n_mark} 个不同盯市日已拉取行情",
                file=sys.stderr,
            )
        elif use_last:
            print(
                f"未清仓行：盯市日 = 各票日线最后可得交易日，共 {n_mark} 个不同盯市日已拉取行情",
                file=sys.stderr,
            )
        else:
            print(
                f"未清仓行：盯市日 = 各票持仓结束日（买入日+持有{int(args.hold_days)}日），共 {n_mark} 个不同盯市日已拉取行情",
                file=sys.stderr,
            )
    if price_warn:
        print(f"⚠ {price_warn}", file=sys.stderr)

    n_open = sum(1 for r in rows if int(r.get("剩余持仓数量") or 0) > 0)
    if n_open and not prices_by_mark and not price_warn:
        hint = (
            "检查选股日格式"
            if use_nth
            else (
                "检查 daily_cache 是否有日线"
                if use_last
                else "检查买入日/持有天数能否算出 end_date"
            )
        )
        print(f"⚠ 有未清仓行但未能构建任何盯市日（{hint}）", file=sys.stderr)

    apply_mark_and_returns(
        rows,
        prices_by_mark,
        price_warn,
        mark_n=int(args.mark_n),
        use_nth_trading_day=use_nth,
        use_last_available=use_last,
    )

    try:
        ma_hint = apply_ma_fields_from_daily_cache(rows)
        if ma_hint:
            print(ma_hint, file=sys.stderr)
    except Exception as e:
        print(f"⚠ 均线回填失败: {e}", file=sys.stderr)

    try:
        buy_ma_hint = apply_buy_day_ma5_ref_fields(rows)
        if buy_ma_hint:
            print(buy_ma_hint, file=sys.stderr)
    except Exception as e:
        print(f"⚠ 买入日MA5回填失败: {e}", file=sys.stderr)

    if n_open:
        missing = []
        for r in rows:
            if int(r.get("剩余持仓数量") or 0) <= 0:
                continue
            rp = r.get("收益率pct")
            if isinstance(rp, (int, float)):
                continue
            c = _norm_code_6(str(r.get("代码") or ""))
            if c:
                missing.append(c)
        if missing:
            print(
                f"⚠ 未清仓且收益率仍为空的代码（示例）: {missing[:15]}{'…' if len(missing) > 15 else ''}",
                file=sys.stderr,
            )

    fieldnames = [
        "选股日",
        "首次选股日",
        "持有交易日数",
        "计划持仓结束日",
        "end_date",
        "涨停日期",
        "选股日为涨停后第几日",
        "代码",
        "买入时间",
        "买入日",
        "末笔买入日",
        "买入笔数",
        "卖出笔数",
        "买入金额合计",
        "卖出金额合计",
        "买入数量合计",
        "卖出数量合计",
        "剩余持仓数量",
        "净现金流_卖减买",
        "盯市日期",
        "盯市类型",
        "收盘价",
        "剩余市值_盯市",
        "收益率pct",
        "备注",
    ] + list(TB_SUMMARY_FIELDS) + list(MA_SUMMARY_FIELDS) + list(BUY_DAY_MA5_FIELDS) + [
        "触发信息",
        # 选股文件回填字段（若有）
        "当日最多涨停概念",
        "该概念当日涨停数",
        "该概念当日排名",
        "主力净流入",
    ]

    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with open(outp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"已写入: {outp.resolve()}  共 {len(rows)} 行")
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
