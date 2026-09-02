# -*- coding: utf-8 -*-
"""绘制市场行情日度描绘图：上证指数 + 涨跌家数 + 标签色带。

用法：
  python tools/plot_market_regime.py
  python tools/plot_market_regime.py --csv data/market_regime/market_regime_daily.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch, Rectangle

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SSE_CACHE = ROOT / "data" / "index_cache" / "000001.SH.csv"
DEFAULT_CSV = ROOT / "data" / "market_regime" / "market_regime_daily.csv"
DEFAULT_OUT = ROOT / "data" / "market_regime" / "market_regime_chart.png"

BACKDROP_COLOR = {
    "多头趋势底色": "#2e7d32",
    "空头趋势底色": "#c62828",
    "震荡底色": "#f9a825",
}
PULSE_COLOR = {
    "普涨脉冲": "#43a047",
    "杀跌脉冲": "#e53935",
    "无脉冲": "#bdbdbd",
    "分化波动": "#7e57c2",
}
DIV_COLOR = {
    "顶背离警告": "#ad1457",
    "底背离提示": "#00838f",
    "无背离": "#e0e0e0",
}


def _setup_font() -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def ensure_sse_cache() -> pd.DataFrame:
    if SSE_CACHE.is_file():
        df = pd.read_csv(SSE_CACHE)
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        return df
    import akshare as ak

    print("[plot] fetching sh000001 …")
    df = ak.stock_zh_index_daily_tx(symbol="sh000001")
    df = df.sort_values("date").copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    out = df.copy()
    out.insert(0, "code", "000001.SH")
    SSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(SSE_CACHE, index=False, encoding="utf-8-sig")
    return out


def load_frame(csv_path: Path) -> pd.DataFrame:
    reg = pd.read_csv(csv_path, encoding="utf-8-sig")
    reg["trade_date"] = reg["trade_date"].astype(str).str[:10]
    sse = ensure_sse_cache()
    m = reg.merge(
        sse[["date", "close"]].rename(columns={"close": "sse_close"}),
        left_on="trade_date",
        right_on="date",
        how="left",
    )
    m["dt"] = pd.to_datetime(m["trade_date"])
    return m.sort_values("dt").reset_index(drop=True)


def _strip(ax, dates, values, cmap, y0, height, label):
    ax.set_yticks([y0 + height / 2])
    ax.set_yticklabels([label], fontsize=9)
    for i, (d, v) in enumerate(zip(dates, values)):
        c = cmap.get(str(v), "#eeeeee")
        if i + 1 < len(dates):
            x0, x1 = mdates.date2num(d), mdates.date2num(dates[i + 1])
        else:
            x0 = mdates.date2num(d)
            x1 = x0 + 1.0
        ax.add_patch(Rectangle((x0, y0), x1 - x0, height, facecolor=c, edgecolor="none", linewidth=0))
    ax.set_xlim(mdates.date2num(dates[0]), mdates.date2num(dates[-1]) + 1)
    ax.set_ylim(0, 1)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(axis="x", bottom=False, labelbottom=False)
    ax.tick_params(axis="y", length=0)


def plot_regime(df: pd.DataFrame, out_path: Path) -> Path:
    _setup_font()
    dates = list(df["dt"])
    n = len(df)
    # 每个交易日留出刻度空间：图加宽，底轴标全日日期
    fig_w = max(36.0, n * 0.38)
    fig = plt.figure(figsize=(fig_w, 11), constrained_layout=True)
    gs = fig.add_gridspec(4, 1, height_ratios=[3.2, 2.2, 0.55, 0.7])

    ax_px = fig.add_subplot(gs[0, 0])
    ax_bd = fig.add_subplot(gs[1, 0], sharex=ax_px)
    ax_s1 = fig.add_subplot(gs[2, 0], sharex=ax_px)
    ax_s3 = fig.add_subplot(gs[3, 0], sharex=ax_px)

    # backdrop shading behind price
    for i in range(len(df)):
        row = df.iloc[i]
        c = BACKDROP_COLOR.get(str(row["backdrop"]), "#f5f5f5")
        if i + 1 < len(df):
            x0, x1 = mdates.date2num(row["dt"]), mdates.date2num(df.iloc[i + 1]["dt"])
        else:
            x0 = mdates.date2num(row["dt"])
            x1 = x0 + 1.0
        ax_px.axvspan(x0, x1, color=c, alpha=0.18, linewidth=0)

    ax_px.plot(df["dt"], df["sse_close"], color="#212121", lw=1.6, label="上证指数收盘")
    if "csi_close" in df.columns:
        # 右轴：中证全指（标签所用指数），便于对照
        ax_csi = ax_px.twinx()
        ax_csi.plot(df["dt"], df["csi_close"], color="#546e7a", lw=1.0, alpha=0.85, label="中证全指收盘")
        ax_csi.set_ylabel("中证全指", color="#546e7a")
        ax_csi.tick_params(axis="y", labelcolor="#546e7a")
        lines1, lab1 = ax_px.get_legend_handles_labels()
        lines2, lab2 = ax_csi.get_legend_handles_labels()
        ax_px.legend(lines1 + lines2, lab1 + lab2, loc="upper left", fontsize=9)
    else:
        ax_px.legend(loc="upper left", fontsize=9)

    # mark divergence days
    top = df["divergence"].astype(str) == "顶背离警告"
    bot = df["divergence"].astype(str) == "底背离提示"
    if top.any():
        ax_px.scatter(df.loc[top, "dt"], df.loc[top, "sse_close"], marker="v", c="#ad1457", s=28, zorder=5, label="_top")
    if bot.any():
        ax_px.scatter(df.loc[bot, "dt"], df.loc[bot, "sse_close"], marker="^", c="#00838f", s=28, zorder=5, label="_bot")

    ax_px.set_ylabel("上证指数")
    ax_px.set_title(
        "市场行情日度描绘  %s ~ %s\n（背景色=趋势底色；▼顶背离 ▲底背离；标签基于中证全指宽度规则）"
        % (df["trade_date"].iloc[0], df["trade_date"].iloc[-1]),
        fontsize=12,
    )
    ax_px.grid(True, axis="y", alpha=0.25)

    # up / down counts（按交易日间距估柱宽）
    if n >= 2:
        span = float(mdates.date2num(dates[-1]) - mdates.date2num(dates[0])) / max(n - 1, 1)
        bar_w = max(span * 0.7, 0.35)
    else:
        bar_w = 0.6
    ax_bd.bar(df["dt"], df["n_up"], width=bar_w, color="#43a047", alpha=0.85, label="上涨家数")
    ax_bd.bar(df["dt"], -df["n_down"], width=bar_w, color="#e53935", alpha=0.85, label="下跌家数")
    ax_bd.axhline(0, color="#424242", lw=0.8)
    ax_bd.set_ylabel("家数")
    ax_bd.legend(loc="upper left", fontsize=9, ncol=2)
    ax_bd.grid(True, axis="y", alpha=0.25)

    _strip(ax_s1, dates, df["backdrop"], BACKDROP_COLOR, 0.15, 0.7, "趋势底色")
    _strip(ax_s3, dates, df["pulse"], PULSE_COLOR, 0.15, 0.7, "脉冲形态")

    # 每个交易日标注日期（上轴隐藏刻度字，只留底轴）
    for ax in (ax_px, ax_bd, ax_s1):
        ax.tick_params(axis="x", bottom=True, labelbottom=False)
    ax_s3.set_xticks(dates)
    ax_s3.set_xticklabels(
        [d.strftime("%m-%d") for d in dates],
        rotation=90,
        fontsize=6,
        ha="center",
        va="top",
    )
    ax_s3.tick_params(axis="x", bottom=True, labelbottom=True, length=3, pad=2)
    ax_s3.set_xlim(mdates.date2num(dates[0]) - 0.5, mdates.date2num(dates[-1]) + 1.0)

    # legends for strips
    legend_items = (
        [Patch(facecolor=c, label=k) for k, c in BACKDROP_COLOR.items()]
        + [Patch(facecolor=c, label=k) for k, c in PULSE_COLOR.items()]
        + [Patch(facecolor=c, label=k) for k, c in DIV_COLOR.items()]
    )
    fig.legend(
        handles=legend_items,
        loc="lower center",
        ncol=5,
        fontsize=8,
        frameon=False,
        bbox_to_anchor=(0.5, -0.01),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Plot market regime chart")
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    df = load_frame(Path(args.csv))
    if df.empty:
        raise SystemExit("empty regime csv")
    out = plot_regime(df, Path(args.out))
    print("[plot] saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
