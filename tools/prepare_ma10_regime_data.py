# -*- coding: utf-8 -*-
"""MA10 风格切换监控 · 准备数据（近 N 个交易日选股 + ma10/sell_half 回测）

默认窗口 = 回测按票选股日缺口 + 已覆盖选股日最近 ``REFRESH_LOOKBACK`` 天重跑
（持有未完成样本需随收盘更新；已有选股日优先复用选股文件，不必重选）。

选股规则：马总选股逻辑-次日MA10；回测挂单窗 entry_window=1。
卖出：sell_half；持有最多 2 个交易日强清；默认关闭 1455 破 MA20 清仓。

用法:
  python tools/prepare_ma10_regime_data.py
  python tools/prepare_ma10_regime_data.py --days 15
  python tools/prepare_ma10_regime_data.py --end 2026-08-18 --days 15
  python tools/prepare_ma10_regime_data.py --force-days 15
  python tools/prepare_ma10_regime_data.py --force-days 60 --no-reuse

输出:
  history_data/马总选股逻辑/选股结果_马总选股逻辑-次日MA10_{start}_{end}.xls
  history_data/马总选股逻辑/各日选股收益汇总_日线-ma10-sell_half-单点_按票_*.xlsx
  （及对应 *_latest）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "history_data" / "马总选股逻辑"
RULE_NAME = "马总选股逻辑-次日MA10"
DEFAULT_DAYS = 15
# 与回测 --sell-hold 一致：已覆盖选股日至少再跑这么多天，更新未完成样本
SELL_HOLD = 2
REFRESH_LOOKBACK = SELL_HOLD
ENTRY_WINDOW = 1  # 与次日MA10 / 监控挂单窗一致
# 监控日线回测：不挂 1455 破 MA20 清仓
DISABLE_MA20_CLEAR = True

ProgressCb = Optional[Callable[[str], None]]


def _log(msg: str, progress: ProgressCb = None) -> None:
    print(msg, flush=True)
    if progress:
        try:
            progress(msg)
        except Exception:
            pass


def _parse_d(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    return date.fromisoformat(s[:10])


def last_closed_trading_day(now: datetime | None = None) -> date:
    now = now or datetime.now()
    today = now.date()
    hi = today if now.time() >= dt_time(15, 0) else today - timedelta(days=1)
    try:
        from utils.trading_day import get_trading_dates_in_range_sorted

        days = list(get_trading_dates_in_range_sorted(hi - timedelta(days=21), hi))
        if days:
            return days[-1]
    except Exception:
        pass
    d = hi
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def resolve_window(days: int, end: Optional[date] = None) -> Tuple[date, date, List[date]]:
    """最近 ``days`` 个交易日（含 end，默认最近已收盘日）。"""
    end = end or last_closed_trading_day()
    from utils.trading_day import get_trading_dates

    raw = list(get_trading_dates(max(int(days) + 10, 20)) or [])
    raw = [d for d in raw if d <= end]
    if len(raw) < days:
        from utils.trading_day import get_trading_dates_in_range_sorted

        lo = end - timedelta(days=int(days * 2.5) + 10)
        raw = list(get_trading_dates_in_range_sorted(lo, end) or [])
    if not raw:
        raise RuntimeError(f"无法取得截至 {end} 的交易日列表")
    win = raw[-int(days) :]
    return win[0], win[-1], win


def latest_backtest_sel_day() -> Optional[date]:
    """回测按票（优先 ma10-sell_half / latest / 收盘上MA10）里最晚的选股日。"""
    try:
        import pandas as pd
    except Exception:
        return None

    pats = (
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_*收盘上MA10_latest.xlsx",
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_已完成_收盘上MA10_latest.xlsx",
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_latest.xlsx",
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票*_收盘上MA10.xlsx",
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票*.xlsx",
        "各日选股收益汇总_日线-ma10*-单点_按票*_收盘上MA10.xlsx",
        "各日选股收益汇总_日线-ma10*-单点_按票*.xlsx",
    )
    seen: set[Path] = set()
    files: List[Path] = []
    for pat in pats:
        for p in sorted(OUT_DIR.glob(pat), key=lambda x: x.stat().st_mtime, reverse=True):
            key = p.resolve()
            if key in seen:
                continue
            seen.add(key)
            files.append(p)
            if "_latest" in p.name:
                # latest 优先：只读这一份
                files = [p]
                break
        if files and "_latest" in files[0].name:
            break
        if len(files) >= 8:
            break
    if not files:
        return None

    best: Optional[date] = None
    for p in files:
        try:
            d = pd.read_excel(p, usecols=["选股日"])
        except Exception:
            continue
        if "选股日" not in d.columns or d.empty:
            continue
        s = pd.to_datetime(d["选股日"], errors="coerce").dropna()
        if s.empty:
            continue
        cur = s.max().date()
        if best is None or cur > best:
            best = cur
        if "_latest" in p.name:
            break
    return best


def plan_window(
    *,
    end: Optional[date] = None,
    max_days: int = DEFAULT_DAYS,
    force_days: Optional[int] = None,
    refresh_lookback: int = REFRESH_LOOKBACK,
    progress: ProgressCb = None,
) -> Optional[Tuple[date, date, List[date], str]]:
    """决定本次要跑的选股日区间。

    = 回测选股日缺口（新日） + 已覆盖选股日最近 ``refresh_lookback`` 天（更新未完成）。
    返回 (start, end, days, reason)。
    """
    end_d = end or last_closed_trading_day()
    rb = max(1, int(refresh_lookback))

    if force_days is not None and int(force_days) > 0:
        start, end2, win = resolve_window(int(force_days), end_d)
        reason = f"强制近 {int(force_days)} 个交易日"
        return start, end2, win, reason

    last_sel = latest_backtest_sel_day()
    if last_sel is None:
        start, end2, win = resolve_window(int(max_days), end_d)
        reason = f"无历史回测按票，回退近 {len(win)} 个交易日"
        _log(f"已有回测选股日: （无）→ 目标末日 {end_d}", progress)
        return start, end2, win, reason

    _log(f"已有回测最晚选股日: {last_sel} → 目标末日 {end_d}", progress)

    from utils.trading_day import get_trading_dates_in_range_sorted

    # 缺口：最晚回测选股日之后 → 最新收盘
    axis_gap = list(get_trading_dates_in_range_sorted(last_sel, end_d) or [])
    gap = [d for d in axis_gap if d > last_sel]

    # 选股文件也可能落后于回测按票：补到最新收盘日，便于监控量柱看「当日选股」
    sel_file_last = None
    try:
        from tools.ma10_regime_switch import latest_selection_file_day

        sel_file_last = latest_selection_file_day()
    except Exception:
        sel_file_last = None
    if sel_file_last is None or sel_file_last < end_d:
        axis_sel = list(get_trading_dates_in_range_sorted(sel_file_last or last_sel, end_d) or [])
        lo = sel_file_last or last_sel
        gap_sel = [d for d in axis_sel if d > lo]
        gap = sorted(set(gap) | set(gap_sel))

    # 重跑：已覆盖区间内最近 refresh_lookback 个选股日（持有未完成要更新）
    cover_hi = min(last_sel, end_d)
    cover_lo = cover_hi - timedelta(days=int(rb * 3) + 10)
    covered = list(get_trading_dates_in_range_sorted(cover_lo, cover_hi) or [])
    covered = [d for d in covered if d <= cover_hi]
    refresh = covered[-rb:] if covered else []

    win = sorted(set(refresh) | set(gap))
    if not win:
        start, end2, win = resolve_window(rb, end_d)
        reason = f"日历兜底：近 {len(win)} 个交易日"
        return start, end2, win, reason

    if len(win) > int(max_days):
        win = win[-int(max_days) :]

    parts = []
    if gap:
        parts.append(f"缺口补 {len([d for d in win if d in gap])} 天")
    if refresh:
        n_ref = len([d for d in win if d in refresh])
        if n_ref:
            parts.append(f"已有选股日重跑近 {n_ref} 天(持有{rb})")
    if sel_file_last is not None:
        parts.append(f"选股文件最晚 {sel_file_last}")
    reason = " + ".join(parts) if parts else f"准备 {len(win)} 天"
    reason += f"（{win[0]} → {win[-1]}）"
    return win[0], win[-1], win, reason


def _read_selection_xls(path: Path):
    import pandas as pd

    try:
        return pd.read_excel(path, engine="xlrd")
    except Exception:
        return pd.read_excel(path)


def collect_existing_selection(want_days: List[date], progress: ProgressCb = None):
    """从已有选股结果里抽出指定选股日；返回 (DataFrame|None, 已覆盖日期集合)。"""
    import pandas as pd

    want = set(want_days)
    if not want:
        return None, set()

    files = sorted(
        OUT_DIR.glob(f"选股结果_{RULE_NAME}_*.xls"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:12]
    parts = []
    found: set = set()
    for p in files:
        if found >= want:
            break
        try:
            df = _read_selection_xls(p)
        except Exception:
            continue
        if df is None or df.empty or "选股日" not in df.columns:
            continue
        df = _normalize_sel_day_col(df)
        sel = pd.to_datetime(df["选股日"], errors="coerce").dt.date
        mask = sel.isin(want - found)
        if not mask.any():
            continue
        sub = df.loc[mask].copy()
        sub["选股日"] = sel.loc[mask].map(lambda d: d.isoformat() if d else "").values
        parts.append(sub)
        got = {d for d in sel.loc[mask].tolist() if d is not None}
        found |= got
        _log(f"复用选股 {p.name}: +{len(got)} 日 / {len(sub)} 行", progress)

    if not parts:
        return None, set()
    out = pd.concat(parts, ignore_index=True)
    # 同日同代码保留较新文件中的行（parts 已按文件从新到旧）
    if "股票代码" in out.columns:
        out["股票代码"] = out["股票代码"].astype(str).str.strip().str.zfill(6)
        out = out.drop_duplicates(subset=["选股日", "股票代码"], keep="first")
    return out, found


def _normalize_sel_day_col(df):
    """选股日统一写成 YYYY-MM-DD 字符串，避免 xlwt 写成 Excel 序列号导致回测解析失败。"""
    import pandas as pd

    if df is None or df.empty or "选股日" not in df.columns:
        return df
    out = df.copy()
    raw = out["选股日"]
    # 已是 date/datetime
    parsed = pd.to_datetime(raw, errors="coerce")
    # 纯数字 Excel 序列（如 46247 → 2026-08-13）
    need_serial = parsed.isna()
    if need_serial.any():
        nums = pd.to_numeric(raw, errors="coerce")
        serial_ok = need_serial & nums.notna() & (nums >= 20000) & (nums <= 80000)
        if serial_ok.any():
            parsed.loc[serial_ok] = pd.to_datetime(
                nums.loc[serial_ok], unit="D", origin="1899-12-30", errors="coerce"
            )
    out["选股日"] = parsed.dt.strftime("%Y-%m-%d")
    # 解析失败的保留空串，后续回测会跳过
    out.loc[parsed.isna(), "选股日"] = ""
    return out


def write_selection_df(df, start: date, end: date, progress: ProgressCb = None) -> Path:
    import pandas as pd
    from sector_stock_filter import save_xls_with_text_code

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = (
        f"{start.isoformat()}"
        if start == end
        else f"{start.isoformat()}_{end.isoformat()}"
    )
    out_path = OUT_DIR / f"选股结果_{RULE_NAME}_{suffix}.xls"
    df = _normalize_sel_day_col(df)
    if "股票代码" in df.columns:
        df = df.copy()
        df["股票代码"] = df["股票代码"].astype(str).str.strip().str.zfill(6)
    save_xls_with_text_code(str(out_path), df)
    _log(f"选股文件: {out_path.name}  共 {len(df)} 行", progress)
    return out_path


def load_universe() -> List[Tuple[str, str, str]]:
    """全 A 底池（不含基金/ETF、不含 ST）：(code, name, sectors_str)。"""
    from core.utils.security_type import SecurityTypeUtil
    from utils.limit_ratio import is_st_stock

    path = ROOT / "data" / "all_a_stock_info.json"
    if not path.is_file():
        raise FileNotFoundError(f"缺少股票底池: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    out: List[Tuple[str, str, str]] = []
    skipped_fund = 0
    skipped_st = 0
    if not isinstance(data, dict):
        raise RuntimeError("all_a_stock_info.json 格式异常")
    for code, info in data.items():
        if not isinstance(info, dict):
            continue
        c6 = "".join(ch for ch in str(code) if ch.isdigit()).zfill(6)[-6:]
        if not c6:
            continue
        if SecurityTypeUtil.is_fund(c6):
            skipped_fund += 1
            continue
        name = str(info.get("name") or info.get("stock_name") or "未知")
        if is_st_stock(name):
            skipped_st += 1
            continue
        plates = info.get("plates") or []
        concepts = info.get("concepts") or []
        industry = info.get("industry") or ""
        tags = []
        for x in list(plates) + list(concepts) + ([industry] if industry else []):
            t = str(x or "").strip()
            if t and t not in tags:
                tags.append(t)
        out.append((c6, name, ";".join(tags)))
    out.sort(key=lambda x: x[0])
    if len(out) < 1000:
        raise RuntimeError(f"底池过小: {len(out)} 只，请检查 {path}")
    if skipped_fund or skipped_st:
        parts = []
        if skipped_fund:
            parts.append(f"基金/ETF {skipped_fund} 只")
        if skipped_st:
            parts.append(f"ST {skipped_st} 只")
        print(f"选股底池已排除 {'、'.join(parts)}，剩余 {len(out)} 只", flush=True)
    return out


def pick_rule(rules: List[Dict[str, object]], name: str = RULE_NAME) -> Dict[str, object]:
    for r in rules:
        if str(r.get("name") or "") == name:
            rr = dict(r)
            rr["enabled"] = True
            return rr
    raise FileNotFoundError(f"未找到选股规则「{name}」，请先安装/导入该规则")


def run_selection(
    start: date,
    end: date,
    *,
    progress: ProgressCb = None,
    rule_name: str = RULE_NAME,
) -> Path:
    """区间选股 → 写入 OUT_DIR，返回 xls 路径。"""
    from PyQt5.QtCore import QEventLoop
    from PyQt5.QtWidgets import QApplication

    import sector_stock_filter as ssf

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    universe = load_universe()
    _log(f"选股底池 {len(universe)} 只 | {start} → {end}", progress)
    rules_all = ssf.load_sector_rules()
    rule = pick_rule(rules_all, rule_name)
    rid = str(rule.get("id") or "")

    rows_by_rule: Dict[str, List[Dict[str, object]]] = {rid: []}
    err_box: List[str] = []

    def on_found(rec: object) -> None:
        if not isinstance(rec, dict):
            return
        r_id = str(rec.get("rule_id") or "")
        if r_id != rid:
            return
        code = str(rec.get("stock_code") or "")
        as_of = str(rec.get("as_of") or "")
        extra = rec.get("extra") if isinstance(rec.get("extra"), dict) else {}
        bucket = rows_by_rule.setdefault(r_id, [])
        for i, old in enumerate(bucket):
            if old.get("code") == code and old.get("as_of") == as_of:
                # 合并板块
                old_sec = set(str(old.get("sectors") or "").split(";"))
                new_sec = set(str(rec.get("sectors") or "").split(";"))
                secs = sorted(s for s in (old_sec | new_sec) if s)
                old["sectors"] = ";".join(secs)
                if isinstance(extra, dict):
                    for k, v in extra.items():
                        if not str(k).startswith("_"):
                            old[k] = v
                return
        row: Dict[str, object] = {
            "code": code,
            "name": str(rec.get("stock_name") or ""),
            "sectors": str(rec.get("sectors") or ""),
            "as_of": as_of,
        }
        if isinstance(extra, dict):
            for k, v in extra.items():
                if not str(k).startswith("_"):
                    row[k] = v
        bucket.append(row)

    def on_progress(cur: int, total: int, tip: str) -> None:
        _log(f"选股进度 {cur}/{total}  {tip}", progress)

    def on_debug(info: str) -> None:
        s = (info or "").strip()
        if not s:
            return
        last = s.splitlines()[-1]
        if last:
            _log(last, progress)

    def on_error(msg: str) -> None:
        err_box.append(str(msg))
        _log(f"选股错误: {msg}", progress)

    th = ssf.SectorStockFilterThread(
        universe,
        ssf.SECTOR_RULE_DEFAULT_N,
        ssf.SECTOR_RULE_DEFAULT_M,
        ssf.SECTOR_RULE_DEFAULT_N,
        ssf.SECTOR_RULE_DEFAULT_M,
        ssf.SECTOR_RULE_DEFAULT_N_MODE3,
        ssf.SECTOR_RULE_DEFAULT_M_MODE3,
        ssf.SECTOR_RULE_DEFAULT_L_MODE3,
        [rule],
        start,
        end,
    )
    loop = QEventLoop()
    th.stock_found.connect(on_found)
    th.progress_updated.connect(on_progress)
    th.debug_info.connect(on_debug)
    th.error_occurred.connect(on_error)
    th.finished.connect(lambda _=None: loop.quit())
    th.start()
    loop.exec_()
    th.wait(5000)

    if err_box and not rows_by_rule.get(rid):
        raise RuntimeError(err_box[0])

    rows = rows_by_rule.get(rid) or []
    if not rows:
        raise RuntimeError(f"选股无结果（规则 {rule_name}，{start}→{end}）")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"{start.isoformat()}_{end.isoformat()}"
    out_path = OUT_DIR / f"选股结果_{rule_name}_{suffix}.xls"

    rows = ssf.reorder_selection_rows_for_export(rows)
    base_cols = ["股票代码", "股票名称", "所属板块", "选股日"]
    extra_cols: List[str] = []
    for rr in rows:
        for k in rr.keys():
            if k in ("code", "name", "sectors", "as_of"):
                continue
            if k not in extra_cols:
                extra_cols.append(k)
    cols = base_cols + extra_cols
    out_rows = []
    for rr in rows:
        out = {
            "股票代码": rr.get("code", ""),
            "股票名称": rr.get("name", ""),
            "所属板块": rr.get("sectors", ""),
            "选股日": rr.get("as_of", ""),
        }
        for k in extra_cols:
            v = rr.get(k, "")
            out[k] = "" if v is None else v
        out_rows.append(out)

    import pandas as pd

    df = pd.DataFrame(out_rows, columns=cols)
    if "股票代码" in df.columns:
        df["股票代码"] = df["股票代码"].astype(str).str.strip().str.zfill(6)
    ssf.save_xls_with_text_code(str(out_path), df)
    _log(f"选股完成: {out_path.name}  共 {len(df)} 行", progress)
    return out_path


def run_backtest(sel_path: Path, *, progress: ProgressCb = None) -> int:
    """跑 ma10 × sell_half 日线回测。"""
    script = ROOT / "tools" / "run_ma_zong1_single_daily_backtest.py"
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        str(script),
        str(sel_path),
        "--buy",
        "ma10",
        "--sell",
        "half",
        "--entry-window",
        str(ENTRY_WINDOW),
        "--sell-hold",
        str(SELL_HOLD),
        "--out-dir",
        str(OUT_DIR),
    ]
    if DISABLE_MA20_CLEAR:
        cmd.append("--no-ma20-clear")
    _log("开始回测: " + " ".join(cmd[3:]), progress)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("BACKTEST_FILL_ADJUST", "qfq")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        s = line.rstrip()
        if s:
            _log(s, progress)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"回测失败，退出码 {rc}")
    _log("回测完成", progress)
    return rc


def _missing_day_segments(missing: List[date]) -> List[Tuple[date, date]]:
    """把缺失选股日切成连续交易日段（用于只重选缺口）。"""
    if not missing:
        return []
    from utils.trading_day import get_trading_dates_in_range_sorted

    segs: List[Tuple[date, date]] = []
    seg_lo = missing[0]
    prev = missing[0]
    for d in missing[1:]:
        between = list(get_trading_dates_in_range_sorted(prev, d) or [])
        # prev 与 d 之间若只有它们自己，视为连续
        mid = [x for x in between if prev < x < d]
        if mid:
            segs.append((seg_lo, prev))
            seg_lo = d
        prev = d
    segs.append((seg_lo, prev))
    return segs


def prepare(
    *,
    days: int = DEFAULT_DAYS,
    end: Optional[date] = None,
    skip_select: bool = False,
    skip_backtest: bool = False,
    selection_file: Optional[Path] = None,
    force_days: Optional[int] = None,
    no_reuse: bool = False,
    progress: ProgressCb = None,
) -> Dict[str, Any]:
    import pandas as pd

    planned = plan_window(
        end=end,
        max_days=int(days),
        force_days=force_days,
        progress=progress,
    )
    if planned is None:
        end_d = end or last_closed_trading_day()
        last_sel = latest_backtest_sel_day()
        msg = f"无需补数：回测选股日已覆盖到 {last_sel}（目标末日 {end_d}）"
        _log(msg, progress)
        return {
            "start": str(last_sel) if last_sel else "",
            "end": str(end_d),
            "n_days": 0,
            "selection": "",
            "skipped": True,
            "reason": msg,
        }

    start, end_d, win, reason = planned
    _log(f"准备窗口: {start} → {end_d}（{len(win)} 个交易日）| {reason}", progress)
    sel_path = selection_file

    if skip_select:
        if sel_path is None:
            cands = sorted(
                OUT_DIR.glob(f"选股结果_{RULE_NAME}_*.xls"),
                key=lambda p: p.stat().st_mtime,
            )
            if not cands:
                raise FileNotFoundError("未指定选股文件且目录中无可用选股结果")
            sel_path = cands[-1]
            _log(f"跳过选股，使用已有文件: {sel_path.name}", progress)
    else:
        frames: List[Any] = []
        found: set = set()
        if no_reuse:
            _log("已指定 --no-reuse：不复用旧选股，窗口内全部重选（含满足条件新列）", progress)
            missing = list(win)
        else:
            reused_df, found = collect_existing_selection(win, progress=progress)
            missing = [d for d in win if d not in found]
            if reused_df is not None and not reused_df.empty:
                frames.append(reused_df)
                _log(f"复用选股日 {len(found)}/{len(win)}：{sorted(found)}", progress)

        if missing:
            _log(f"需新选股 {len(missing)} 日：{missing}", progress)
            for lo, hi in _missing_day_segments(missing):
                new_path = run_selection(lo, hi, progress=progress)
                new_df = _read_selection_xls(new_path)
                if new_df is None or new_df.empty:
                    continue
                sel = pd.to_datetime(new_df["选股日"], errors="coerce").dt.date
                keep = sel.isin(set(missing))
                sub = new_df.loc[keep].copy()
                if sub.empty:
                    continue
                sub["选股日"] = sel.loc[keep].values
                frames.append(sub)
        else:
            _log("窗口内选股日均可复用，跳过重选", progress)

        if not frames:
            raise RuntimeError(f"窗口 {start}→{end_d} 无可用选股结果")
        merged = pd.concat(frames, ignore_index=True)
        if "选股日" in merged.columns:
            sel = pd.to_datetime(merged["选股日"], errors="coerce").dt.date
            merged = merged.loc[sel.isin(set(win))].copy()
            merged["选股日"] = sel.loc[sel.isin(set(win))].values
        if "股票代码" in merged.columns:
            merged["股票代码"] = merged["股票代码"].astype(str).str.strip().str.zfill(6)
            merged = merged.drop_duplicates(subset=["选股日", "股票代码"], keep="first")
        if merged.empty:
            raise RuntimeError(f"合并后选股为空（{start}→{end_d}）")
        sel_path = write_selection_df(merged, start, end_d, progress=progress)

    if not skip_backtest:
        run_backtest(Path(sel_path), progress=progress)
    return {
        "start": str(start),
        "end": str(end_d),
        "n_days": len(win),
        "selection": str(sel_path),
        "skipped": False,
        "reason": reason,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MA10 监控数据准备：近N日选股 + ma10/sell_half 回测")
    ap.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help="自动补数时的上限交易日数，默认15",
    )
    ap.add_argument(
        "--force-days",
        type=int,
        default=0,
        help="强制跑近 N 个交易日（忽略已有回测缺口判断）；0=自动",
    )
    ap.add_argument("--end", type=str, default="", help="窗口末日 YYYY-MM-DD，默认最近已收盘日")
    ap.add_argument("--skip-select", action="store_true", help="跳过选股，只用已有选股文件回测")
    ap.add_argument("--skip-backtest", action="store_true", help="只选股不回测")
    ap.add_argument(
        "--no-reuse",
        action="store_true",
        help="不复用旧选股文件，窗口内全部重选（改满足条件后必须加）",
    )
    ap.add_argument("--selection", type=str, default="", help="指定选股文件（配合 --skip-select）")
    args = ap.parse_args(argv)
    try:
        out = prepare(
            days=int(args.days),
            end=_parse_d(args.end) if args.end else None,
            skip_select=bool(args.skip_select),
            skip_backtest=bool(args.skip_backtest),
            selection_file=Path(args.selection) if args.selection else None,
            force_days=int(args.force_days) if int(args.force_days or 0) > 0 else None,
            no_reuse=bool(args.no_reuse),
        )
        if out.get("skipped"):
            return 0
        return 0
    except Exception as e:
        print(f"失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
