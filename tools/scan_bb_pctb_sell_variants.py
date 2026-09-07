# -*- coding: utf-8 -*-
"""布林%b缩池 · 卖出规则对照扫描（不重跑引擎）。

在缩池买入样本上，按「昨收检查 → 次日开盘卖」重演多种止损：
  - 放宽/关闭 MA10 斜率阈值
  - 买入后缓冲 N 日不启用斜率
  - 仅浮亏时启用斜率
  - 用「收盘破 MA10」替代斜率止损
保留：%b≥0.5 止盈、满 12 日次日开盘强清。

用法:
  python tools/scan_bb_pctb_sell_variants.py
  python tools/scan_bb_pctb_sell_variants.py --grid   # 缓冲×%b止盈网格
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIR = ROOT / "history_data" / "布林%b回落选股"
OUT_JSON = DIR / "_tmp_bb_pctb_sell_scan.json"

HOLD_DAYS = 12
PCTB_TP = 0.5
SLIP_SELL = 0.001  # 与日线撮合 -0.1% 对齐


@dataclass
class Variant:
    name: str
    slope_stop: Optional[float]  # None=关闭斜率
    buffer_days: int = 0  # 买入后前 N 个持仓日（含买入日）不启用斜率/破MA10
    lose_only: bool = False  # 仅当昨收 < 买入价 才允许斜率/破MA10
    stop_kind: str = "slope"  # slope | ma10 | none
    pctb_tp: float = 0.5  # %b 止盈阈值


VARIANTS: List[Variant] = [
    Variant("baseline_斜率-0.008", -0.008, 0, False, "slope", 0.5),
    Variant("放宽_斜率-0.012", -0.012, 0, False, "slope", 0.5),
    Variant("放宽_斜率-0.015", -0.015, 0, False, "slope", 0.5),
    Variant("关闭斜率_仅%b+12日", None, 0, False, "none", 0.5),
    Variant("缓冲3日_斜率-0.008", -0.008, 3, False, "slope", 0.5),
    Variant("缓冲5日_斜率-0.008", -0.008, 5, False, "slope", 0.5),
    Variant("仅浮亏_斜率-0.008", -0.008, 0, True, "slope", 0.5),
    Variant("缓冲3+仅浮亏_斜率-0.008", -0.008, 3, True, "slope", 0.5),
    Variant("破MA10替代斜率", None, 0, False, "ma10", 0.5),
    Variant("破MA10+缓冲3", None, 3, False, "ma10", 0.5),
    Variant("破MA10+仅浮亏", None, 0, True, "ma10", 0.5),
    Variant("缓冲3+仅浮亏_斜率-0.012", -0.012, 3, True, "slope", 0.5),
]


def build_buffer_pctb_grid() -> List[Variant]:
    """强清固定12日 · 斜率固定-0.012 · 缓冲×%b止盈网格。"""
    out: List[Variant] = []
    for buf in (0, 3, 5):
        for tp in (0.4, 0.5, 0.6, 0.7):
            name = f"斜率-0.012_缓冲{buf}_%b{tp:g}"
            out.append(Variant(name, -0.012, buf, False, "slope", float(tp)))
    # 对照：先前扫描较优的缓冲5+斜率-0.008，同样扫 %b
    for tp in (0.4, 0.5, 0.6, 0.7):
        name = f"斜率-0.008_缓冲5_%b{tp:g}"
        out.append(Variant(name, -0.008, 5, False, "slope", float(tp)))
    return out


OUT_GRID_JSON = DIR / "_tmp_bb_pctb_sell_grid.json"


def _stats(rets: List[float]) -> Dict[str, Any]:
    if not rets:
        return {"n": 0}
    s = pd.Series(rets, dtype=float)
    return {
        "n": int(len(s)),
        "mean": round(float(s.mean()), 3),
        "median": round(float(s.median()), 3),
        "win": round(float((s > 0).mean() * 100), 1),
        "p25": round(float(s.quantile(0.25)), 3),
        "p75": round(float(s.quantile(0.75)), 3),
    }


class DailyCache:
    def __init__(self) -> None:
        self._none: Dict[str, Optional[pd.DataFrame]] = {}
        self._qfq: Dict[str, Optional[pd.DataFrame]] = {}
        self._cal: Optional[List[date]] = None

    def calendar(self) -> List[date]:
        if self._cal is not None:
            return self._cal
        from utils.trading_day import get_trading_dates_in_range_sorted

        self._cal = list(
            get_trading_dates_in_range_sorted(date(2024, 12, 1), date(2026, 9, 30)) or []
        )
        return self._cal

    def next_td(self, d: date) -> Optional[date]:
        cal = self.calendar()
        for x in cal:
            if x > d:
                return x
        return None

    def _load(self, code: str, adjust: str) -> Optional[pd.DataFrame]:
        store = self._none if adjust == "none" else self._qfq
        if code in store:
            return store[code]
        try:
            from utils.daily_cache_reader import load_daily_from_cache, to_full_stock_code

            full = to_full_stock_code(code)
            df = load_daily_from_cache(full, adjust=adjust)
            if df is None or (hasattr(df, "empty") and df.empty):
                df = load_daily_from_cache(code, adjust=adjust)
            if df is None or df.empty:
                store[code] = None
                return None
            df = df.copy()
            dcol = "date" if "date" in df.columns else "日期"
            df["_d"] = pd.to_datetime(df[dcol]).dt.date
            df = df.sort_values("_d")
            store[code] = df
            return df
        except Exception:
            store[code] = None
            return None

    def bar(self, code: str, d: date, adjust: str = "qfq") -> Optional[Dict[str, float]]:
        df = self._load(code, adjust)
        if df is None:
            return None
        sub = df[df["_d"] == d]
        if sub.empty:
            return None
        row = sub.iloc[-1]
        try:
            o = float(row.get("open") or row.get("开盘") or 0)
            h = float(row.get("high") or row.get("最高") or 0)
            low = float(row.get("low") or row.get("最低") or 0)
            c = float(row.get("close") or row.get("收盘") or 0)
        except (TypeError, ValueError):
            return None
        if c <= 0 and o <= 0:
            return None
        if o <= 0:
            o = c
        if c <= 0:
            c = o
        return {"open": o, "high": h, "low": low, "close": c}

    def closes_through(self, code: str, d: date) -> List[float]:
        df = self._load(code, "none")
        if df is None:
            return []
        sub = df[df["_d"] <= d]
        if sub.empty:
            return []
        closes = []
        for _, row in sub.iterrows():
            try:
                c = float(row.get("close") or row.get("收盘") or 0)
            except (TypeError, ValueError):
                continue
            if c > 0:
                closes.append(c)
        return closes


def _indicators(cache: DailyCache, code: str, check_d: date) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """返回 slope_norm, pctb, close, ma10。"""
    from utils.bb_pctb_indicators import boll_bands, ma, ma10_slope_norm, pct_b

    closes = cache.closes_through(code, check_d)
    if len(closes) < 20:
        return None, None, None, None
    _k, sn, _mas = ma10_slope_norm(closes, period=10, slope_days=5)
    mid, up, lo = boll_bands(closes, period=20, k=2.0)
    pb = pct_b(float(closes[-1]), up, lo)
    m10 = ma(closes, 10)
    return sn, pb, float(closes[-1]), m10


def simulate_one(
    cache: DailyCache,
    code: str,
    buy_d: date,
    buy_px: float,
    v: Variant,
) -> Optional[Dict[str, Any]]:
    # 持仓日序列：买入日=idx1
    cal = cache.calendar()
    hold_days = [d for d in cal if d >= buy_d]
    if not hold_days or hold_days[0] != buy_d:
        # 对齐到买日或之后第一个交易日
        hold_days = [d for d in cal if d >= buy_d]
        if not hold_days:
            return None

    # 最多检查到 hold_days+1 开盘强清
    max_idx = HOLD_DAYS + 2
    for i, check_d in enumerate(hold_days[:max_idx]):
        idx = i + 1  # 买入日=1
        sell_d = cache.next_td(check_d)
        if sell_d is None:
            continue

        force = idx > HOLD_DAYS
        sn, pb, last_c, m10 = None, None, None, None
        need_ind = force or idx >= 2
        if need_ind:
            sn, pb, last_c, m10 = _indicators(cache, code, check_d)

        stop = False
        reason = ""
        if force:
            stop = True
            reason = "12日强清"
        else:
            # 止盈优先于止损？原策略：force > slope > pctb
            # 扫描保持同一优先级：先止损类，再止盈
            can_stop = idx >= 2 and idx > int(v.buffer_days)
            # buffer_days=0 → idx>=2 即可（与原策略一致：买入日不查）
            # buffer_days=3 → idx>=4 才启用止损（前3个持仓日禁用）
            if v.buffer_days > 0:
                can_stop = idx > int(v.buffer_days)
            else:
                can_stop = idx >= 2

            underwater = True
            if v.lose_only:
                underwater = last_c is not None and float(last_c) < float(buy_px)

            stop_hit = False
            stop_reason = ""
            if can_stop and underwater:
                if v.stop_kind == "slope" and v.slope_stop is not None:
                    if sn is not None and float(sn) < float(v.slope_stop):
                        stop_hit = True
                        stop_reason = "斜率止损"
                elif v.stop_kind == "ma10":
                    if last_c is not None and m10 is not None and float(last_c) < float(m10):
                        stop_hit = True
                        stop_reason = "破MA10"

            tp = float(v.pctb_tp if v.pctb_tp is not None else PCTB_TP)
            take_pctb = idx >= 2 and pb is not None and float(pb) >= tp

            if stop_hit:
                stop = True
                reason = stop_reason
            elif take_pctb:
                stop = True
                reason = "%b止盈"

        if not stop:
            continue

        bar = cache.bar(code, sell_d, adjust="qfq")
        if not bar or bar["open"] <= 0:
            # 缺 qfq 用不复权
            bar = cache.bar(code, sell_d, adjust="none")
        if not bar or bar["open"] <= 0:
            continue
        sell_px = float(bar["open"]) * (1.0 - SLIP_SELL)
        ret = (sell_px / buy_px - 1.0) * 100.0
        return {
            "code": code,
            "buy_d": buy_d.isoformat(),
            "sell_d": sell_d.isoformat(),
            "check_d": check_d.isoformat(),
            "reason": reason,
            "hold_idx": idx,
            "buy_px": round(buy_px, 4),
            "sell_px": round(sell_px, 4),
            "ret": round(ret, 4),
            "slope": None if sn is None else round(float(sn), 6),
            "pctb": None if pb is None else round(float(pb), 4),
        }
    return None


def load_buys() -> pd.DataFrame:
    buy_path = DIR / "回测成交明细_日线-bb_pctb-bb_pctb_sell买入_latest.csv"
    df = pd.read_csv(buy_path, encoding="utf-8-sig")
    df["code"] = df["代码"].astype(str).str.zfill(6)
    df["sel"] = pd.to_datetime(df["选股日"]).dt.strftime("%Y-%m-%d")
    df["buy_d"] = pd.to_datetime(df["日期"]).dt.date
    df["buy_px"] = pd.to_numeric(df["价格"], errors="coerce")
    # 同键取首笔买入
    df = df.sort_values(["code", "sel", "日期", "时间"]).groupby(["code", "sel"], as_index=False).first()
    df = df[df["buy_px"].notna() & (df["buy_px"] > 0)].copy()
    return df


def run_scan(
    buys: pd.DataFrame,
    cache: DailyCache,
    variants: List[Variant],
    *,
    baseline_name: str,
    out_path: Path,
    meta_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "meta": {
            "n_buys": int(len(buys)),
            "hold_days": HOLD_DAYS,
            "note": "昨收检查→次日开盘卖；成交价=前复权开盘×(1-0.1%)；指标用不复权收盘",
            **(meta_extra or {}),
        },
        "variants": {},
    }

    for v in variants:
        print(f"扫描: {v.name} …", flush=True)
        rets: List[float] = []
        reasons: Dict[str, int] = {}
        by_exit_rets: Dict[str, List[float]] = {}
        miss = 0
        for _, row in buys.iterrows():
            r = simulate_one(cache, row["code"], row["buy_d"], float(row["buy_px"]), v)
            if r is None:
                miss += 1
                continue
            rets.append(float(r["ret"]))
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
            by_exit_rets.setdefault(r["reason"], []).append(float(r["ret"]))
        st = _stats(rets)
        share = {k: round(v0 / max(st.get("n", 1), 1) * 100, 1) for k, v0 in reasons.items()}
        out["variants"][v.name] = {
            **asdict(v),
            **st,
            "miss": miss,
            "exit_n": reasons,
            "exit_share": share,
            "by_exit": {k: _stats(vs) for k, vs in by_exit_rets.items()},
        }
        print(
            f"  n={st.get('n')} mean={st.get('mean')}% win={st.get('win')}% "
            f"exit={share} miss={miss}",
            flush=True,
        )

    base = out["variants"].get(baseline_name) or {}
    base_mean = base.get("mean")
    ranking = []
    for name, info in out["variants"].items():
        if info.get("n"):
            ranking.append(
                {
                    "name": name,
                    "mean": info["mean"],
                    "median": info["median"],
                    "win": info["win"],
                    "n": info["n"],
                    "buffer_days": info.get("buffer_days"),
                    "pctb_tp": info.get("pctb_tp"),
                    "slope_stop": info.get("slope_stop"),
                    "delta_vs_base": None
                    if base_mean is None
                    else round(float(info["mean"]) - float(base_mean), 3),
                    "exit_share": info.get("exit_share") or {},
                }
            )
    ranking.sort(key=lambda x: -x["mean"])
    out["ranking"] = ranking
    out["meta"]["baseline"] = baseline_name

    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out_path)
    print("排名:")
    for i, r in enumerate(ranking, 1):
        print(
            f"  {i}. {r['name']}: mean={r['mean']}% (Δ{r['delta_vs_base']}) "
            f"win={r['win']}% exit={r['exit_share']}"
        )
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="布林%b卖出规则扫描")
    ap.add_argument(
        "--grid",
        action="store_true",
        help="跑缓冲×%%b止盈网格（强清12、主网格斜率-0.012）",
    )
    args = ap.parse_args()

    buys = load_buys()
    print(f"缩池买入样本: {len(buys)}")
    cache = DailyCache()

    if args.grid:
        run_scan(
            buys,
            cache,
            build_buffer_pctb_grid(),
            baseline_name="斜率-0.012_缓冲0_%b0.5",
            out_path=OUT_GRID_JSON,
            meta_extra={
                "mode": "buffer_x_pctb",
                "force_hold_days": HOLD_DAYS,
                "grid_slope": -0.012,
                "extra_slope008_buffer5": True,
                "pctb_levels": [0.4, 0.5, 0.6, 0.7],
                "buffer_levels": [0, 3, 5],
            },
        )
        return 0

    run_scan(
        buys,
        cache,
        VARIANTS,
        baseline_name="baseline_斜率-0.008",
        out_path=OUT_JSON,
        meta_extra={"pctb_tp": PCTB_TP, "mode": "legacy_variants"},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
