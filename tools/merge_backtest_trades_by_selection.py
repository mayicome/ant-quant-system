#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将「买入成交明细」与「卖出成交明细」两份 CSV（策略生成器导出的格式）按
「选股日 + 股票代码」汇总：买卖金额/数量、净现金流、剩余持仓、收益率等。

选股日优先读 CSV 独立列「选股日」（与批量/下一轮回测导出列一致）；无该列时再从「触发信息」
中解析 [选股日 yyyy-mm-dd]，避免仅靠长文本解析导致混批或漏解析。

收益率（收益率pct）统一为相对买入金额的盈亏比例：
  (卖出金额合计 + 剩余持仓数量 × 收盘价 − 买入金额合计) / 买入金额合计 × 100
已清仓时剩余为 0，等价于 (卖−买)/买。

汇总表中的 **end_date** 由「选股日 + 区间起算（T+1 或 T 当日）+ 持有交易日数」经 **trading_calendar**
与批量回测相同公式精确计算，不采用 CSV 中 end_date 的众数或对齐。

未清仓盯市：默认按上式 **end_date** 取收盘；可选改为按「选股日后第 N 个交易日」盯市。

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


def _sel_from_row(r: dict) -> str:
    """
    汇总键「选股日」：优先使用导出 CSV 独立列（与触发信息解析解耦，避免漏解析/混批）。
    兼容列名：选股日、selection_date、选股日期；否则回退 [选股日 yyyy-mm-dd] 触发信息。
    """
    if not isinstance(r, dict):
        return ""
    for key in ("选股日", "selection_date", "选股日期"):
        v = r.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if not s or s.lower() == "nan":
            continue
        d = _parse_row_date(s)
        if d:
            return d.strftime("%Y-%m-%d")
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        if len(s) >= 10:
            return s[:10].replace("/", "-")
    return _parse_sel(r.get("触发信息") or "")


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
    """汇总分组键：选股日统一为 YYYY-MM-DD，避免同一日多种写法拆成多组。"""
    s = (raw or "").strip()
    if not s:
        return ""
    d = _parse_row_date(s)
    if d:
        return d.strftime("%Y-%m-%d")
    if len(s) >= 10 and s[4] in "-/":
        return s[:10].replace("/", "-")
    return s


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

    # 1) daily_cache（只读本地，不连 QMT）
    try:
        from utils.daily_cache_reader import load_daily_from_cache
    except Exception:
        load_daily_from_cache = None  # type: ignore

    missing: List[str] = []
    if load_daily_from_cache is not None:
        for c in codes_6:
            try:
                df = load_daily_from_cache(c, through_date=as_of)
            except Exception:
                df = None
            px = _close_on_date(df, as_of)
            if px is not None:
                out[c] = px
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
            "buy_price": None,
            "trigger_info": "",
            "end_date": None,  # 严格模式：仅来自卖出明细 end_date 列
        }
    )

    for r in buy_rows:
        side = (r.get("方向") or "").strip()
        if side != "买入":
            continue
        sel = _norm_sel_key(_sel_from_row(r))
        code = (r.get("代码") or "").strip()
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

    for r in sell_rows:
        side = (r.get("方向") or "").strip()
        if side != "卖出":
            continue
        sel = _norm_sel_key(_sel_from_row(r))
        code = (r.get("代码") or "").strip()
        if not sel or not code:
            continue
        k = (sel, code)
        st[k]["sell_amt"] += _num(r.get("金额"))
        st[k]["sell_vol"] += _int_vol(r.get("数量"))
        st[k]["sell_n"] += 1
        ed = _parse_row_date(r.get("end_date"))
        if ed:
            cur = st[k]["end_date"]
            if cur is None:
                st[k]["end_date"] = ed
            elif cur != ed:
                # 同组多笔卖出应同属一档回测；若不一致保留首次并略作提示
                st[k]["end_date_warn"] = True

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
        if not end_s and rem > 0:
            note = (note + "；" if note else "") + "未清仓但卖出明细缺少 end_date（严格模式不回退买入）"
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
            "收盘价": "",
            "剩余市值_盯市": "",
            "收益率pct": "",
            "备注": note,
        }
        bd = v.get("buy_date")
        row_out["买入日"] = bd.strftime("%Y-%m-%d") if bd else ""
        bp = v.get("buy_price")
        row_out["买入成交价"] = round(float(bp), 2) if bp else ""
        row_out["触发信息"] = str(v.get("trigger_info") or "")
        for fk in TB_SUMMARY_FIELDS:
            row_out[fk] = str(v.get(fk) or "")
        out.append(row_out)
    return out


def _build_prices_by_mark_date(
    rows: List[dict],
    mark_n: int = 3,
    *,
    use_nth_trading_day: bool = False,
) -> Tuple[Dict[date, Dict[str, float]], str]:
    """未清仓行：按 end_date 或按选股日后第 N 个交易日分组拉收盘价。"""
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
        else:
            md = _parse_row_date(r.get("end_date"))
            if not md:
                continue
        by_mark[md].append(code)

    prices_by_mark: Dict[date, Dict[str, float]] = {}
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
) -> None:
    mark_n = int(mark_n or 0)
    if mark_n < 0:
        mark_n = 0
    for r in rows:
        rem = int(r.get("剩余持仓数量") or 0)
        buy_amt = float(r.get("买入金额合计") or 0)
        sell_amt = float(r.get("卖出金额合计") or 0)
        code = _norm_code_6(str(r.get("代码") or ""))

        if rem < 0 or buy_amt <= 0:
            continue

        if rem == 0:
            r["收益率pct"] = round((sell_amt - buy_amt) / buy_amt * 100, 4)
            continue

        base_note = (r.get("备注") or "").strip()
        if use_nth_trading_day:
            sel_d = _parse_row_date(r.get("选股日"))
            if not sel_d:
                r["备注"] = base_note + "；无法解析选股日，无法推算盯市日"
                continue
            mark_d = _nth_trading_day_after(sel_d, mark_n)
        else:
            mark_d = _parse_row_date(r.get("end_date"))
            if not mark_d:
                r["备注"] = (
                    base_note
                    + "；未清仓但缺少 end_date（请使用含 end_date 的卖出明细，或勾选「第 N 个交易日」盯市）"
                )
                continue

        r["盯市日期"] = mark_d.strftime("%Y-%m-%d")
        pmap = prices_by_mark.get(mark_d) or {}
        close = float(pmap.get(code) or 0)
        if close <= 0:
            extra = "；该盯市日缺收盘价，收益率未算"
            if price_warn:
                extra += f"（{price_warn}）"
            r["备注"] = base_note + extra
            continue

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


def apply_selection_file_fields(rows: List[dict], selection_file: Path) -> str:
    """
    对 rows 中每个 (选股日, 代码) 组合，从选股文件中找对应行，
    将选股文件中的**全部列**复制到 rows（找不到则跳过该行回填）。
    汇总侧已有字段（买卖金额、收益率等）在列名冲突时保留汇总值。
    """
    try:
        df = _read_selection_file(selection_file)
    except Exception as e:
        return f"读取选股文件失败：{type(e).__name__}: {e}"

    if df is None or len(df) == 0:
        return "选股文件为空，未回填字段。"

    index, col_order = _build_selection_file_index(df)
    if not index:
        return "选股文件缺少关键列（需要「选股日/日期」与「代码/股票代码」），未回填。"

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

    return f"已从选股文件回填全部列：命中 {hit}/{len(rows)} 行"


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
            load_daily_from_cache,
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

        # 本地不足时补拉长历史（不覆盖已有 cache 写盘，仅本次计算用）
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
            load_daily_from_cache,
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
        # 偏短则补拉（与选股日均线同一套回退）
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
        description="按选股日汇总买卖 CSV（未清仓默认按卖出明细 end_date 盯市，可选第 N 个交易日）"
    )
    ap.add_argument("--buy", required=True, help="买入侧成交明细 CSV")
    ap.add_argument("--sell", required=True, help="卖出侧成交明细 CSV")
    ap.add_argument("--out", default="", help="输出 CSV 路径；省略则打印到 stdout")
    ap.add_argument(
        "--use-nth-trading-day",
        action="store_true",
        help="未清仓按选股日后第 --mark-n 个交易日盯市；省略则按 CSV 中 end_date",
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
        default=5,
        help="持有交易日数：与批量回测一致，用于由选股日精确计算 end_date（默认 5）",
    )
    ap.add_argument(
        "--from-t0",
        action="store_true",
        help="区间从选股日当日起（默认从下一交易日起 T+1）",
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

    rows = aggregate(buy_p, sell_p)
    apply_end_date_from_trading_calendar(
        rows,
        from_t1=not bool(args.from_t0),
        hold_n=int(args.hold_days),
    )

    use_nth = bool(args.use_nth_trading_day)
    prices_by_mark, price_warn = _build_prices_by_mark_date(
        rows, mark_n=int(args.mark_n), use_nth_trading_day=use_nth
    )
    n_mark = len(prices_by_mark)
    if n_mark:
        if use_nth:
            print(
                f"未清仓行：盯市日 = 各「选股日」后第 {int(args.mark_n)} 个交易日，共 {n_mark} 个不同盯市日已拉取行情",
                file=sys.stderr,
            )
        else:
            print(
                f"未清仓行：盯市日 = 各「end_date」（由选股日+交易日历计算），共 {n_mark} 个不同盯市日已拉取行情",
                file=sys.stderr,
            )
    if price_warn:
        print(f"⚠ {price_warn}", file=sys.stderr)

    n_open = sum(1 for r in rows if int(r.get("剩余持仓数量") or 0) > 0)
    if n_open and not prices_by_mark and not price_warn:
        hint = (
            "检查选股日格式"
            if use_nth
            else "检查选股日是否可解析且 end_date 能否由交易日历算出"
        )
        print(f"⚠ 有未清仓行但未能构建任何盯市日（{hint}）", file=sys.stderr)

    apply_mark_and_returns(
        rows,
        prices_by_mark,
        price_warn,
        mark_n=int(args.mark_n),
        use_nth_trading_day=use_nth,
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
        "end_date",
        "涨停日期",
        "选股日为涨停后第几日",
        "代码",
        "买入时间",
        "买入笔数",
        "卖出笔数",
        "买入金额合计",
        "卖出金额合计",
        "买入数量合计",
        "卖出数量合计",
        "剩余持仓数量",
        "净现金流_卖减买",
        "盯市日期",
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
