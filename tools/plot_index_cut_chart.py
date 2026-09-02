# -*- coding: utf-8 -*-
"""画上证 + 中证全指日线宽图，用于人工切趋势底色。

用法：
  python tools/plot_index_cut_chart.py --year 2025
  python tools/plot_index_cut_chart.py --from-date 2025-01-01 --to-date 2025-12-31
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SSE_CACHE = ROOT / "data" / "index_cache" / "000001.SH.csv"
CSI_CACHE = ROOT / "data" / "index_cache" / "000985.SH.csv"
OUT_DIR = ROOT / "data" / "market_regime"

BACKDROP_COLOR = {
    "多头趋势底色": "#2e7d32",
    "空头趋势底色": "#c62828",
    "震荡底色": "#f9a825",
}


def _setup_font() -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def _load_index(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["close"]).reset_index(drop=True)


def _overlay_backdrop(ax, dates: list, labels: list) -> None:
    if not dates or not labels:
        return
    from matplotlib.patches import Patch, Rectangle

    y0, y1 = ax.get_ylim()
    i = 0
    while i < len(labels):
        j = i + 1
        while j < len(labels) and labels[j] == labels[i]:
            j += 1
        color = BACKDROP_COLOR.get(labels[i], "#eeeeee")
        x0 = mdates.date2num(dates[i]) - 0.5
        x1 = mdates.date2num(dates[j - 1]) + 0.5
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=color, alpha=0.18, zorder=0, lw=0))
        i = j
    handles = [
        Patch(facecolor=BACKDROP_COLOR["多头趋势底色"], alpha=0.35, label="多头"),
        Patch(facecolor=BACKDROP_COLOR["空头趋势底色"], alpha=0.35, label="空头"),
        Patch(facecolor=BACKDROP_COLOR["震荡底色"], alpha=0.35, label="震荡"),
    ]
    ax.legend(handles=handles, loc="upper right", fontsize=9, title="底色")


def plot_cut(
    *,
    from_date: str,
    to_date: str,
    out_path: Path,
    overlay_backdrop: bool = False,
) -> Path:
    _setup_font()
    sse = _load_index(SSE_CACHE)
    csi = _load_index(CSI_CACHE)
    a = pd.Timestamp(from_date)
    b = pd.Timestamp(to_date)
    sse = sse[(sse["date"] >= a) & (sse["date"] <= b)].copy()
    csi = csi[(csi["date"] >= a) & (csi["date"] <= b)].copy()
    if sse.empty or csi.empty:
        raise SystemExit("指数区间无数据，请检查缓存与日期")

    m = sse[["date", "close"]].rename(columns={"close": "sse_close"}).merge(
        csi[["date", "close"]].rename(columns={"close": "csi_close"}),
        on="date",
        how="inner",
    )
    dates = list(m["date"])
    n = len(m)
    labels = []
    if overlay_backdrop:
        from utils.market_regime_rules import classify_backdrop_manual, load_rules

        rules = load_rules()
        labels = classify_backdrop_manual(m["date"].dt.strftime("%Y-%m-%d"), rules)

    fig_w = max(40.0, n * 0.38)
    fig, ax = plt.subplots(figsize=(fig_w, 7.5), constrained_layout=True)

    ax.plot(m["date"], m["sse_close"], color="#212121", lw=1.7, label="上证指数", zorder=3)
    ax.set_ylabel("上证指数", color="#212121")
    ax.tick_params(axis="y", labelcolor="#212121")
    ax.grid(True, axis="y", alpha=0.25)

    ax2 = ax.twinx()
    ax2.plot(m["date"], m["csi_close"], color="#546e7a", lw=1.2, alpha=0.9, label="中证全指", zorder=3)
    ax2.set_ylabel("中证全指", color="#546e7a")
    ax2.tick_params(axis="y", labelcolor="#546e7a")

    if overlay_backdrop and labels:
        _overlay_backdrop(ax, dates, labels)
    else:
        lines1, lab1 = ax.get_legend_handles_labels()
        lines2, lab2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, lab1 + lab2, loc="upper left", fontsize=10)

    ax.set_title(
        "指数日线（人工切底色）  %s ~ %s   共 %d 个交易日"
        % (m["date"].iloc[0].strftime("%Y-%m-%d"), m["date"].iloc[-1].strftime("%Y-%m-%d"), n),
        fontsize=13,
    )
    ax.set_xticks(dates)
    ax.set_xticklabels(
        [d.strftime("%m-%d") for d in dates],
        rotation=90,
        fontsize=6,
        ha="center",
        va="top",
    )
    ax.set_xlim(mdates.date2num(dates[0]) - 0.5, mdates.date2num(dates[-1]) + 0.5)
    ax.tick_params(axis="x", pad=2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Plot SSE + CSI for manual backdrop cutting")
    ap.add_argument("--year", type=int, default=0)
    ap.add_argument("--from-date", default="")
    ap.add_argument("--to-date", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--overlay-backdrop", action="store_true", help="叠加 config 人工底色")
    args = ap.parse_args()
    if args.year:
        from_date = f"{args.year}-01-01"
        to_date = f"{args.year}-12-31"
        out = Path(args.out) if args.out else OUT_DIR / f"index_cut_{args.year}.png"
    else:
        from_date = args.from_date or "2025-01-01"
        to_date = args.to_date or "2025-12-31"
        out = Path(args.out) if args.out else OUT_DIR / "index_cut_custom.png"

    path = plot_cut(
        from_date=from_date,
        to_date=to_date,
        out_path=out,
        overlay_backdrop=bool(args.overlay_backdrop),
    )
    print("[plot] saved", path)

    if args.overlay_backdrop:
        from utils.market_regime_rules import classify_backdrop_manual, load_rules

        csi = _load_index(CSI_CACHE)
        a = pd.Timestamp(from_date)
        b = pd.Timestamp(to_date)
        csi = csi[(csi["date"] >= a) & (csi["date"] <= b)].reset_index(drop=True)
        rules = load_rules()
        labels = classify_backdrop_manual(csi["date"].dt.strftime("%Y-%m-%d"), rules)
        # 缺口：落在区间外、被默认成震荡的交易日（且不在任何 from-to 内）
        ranges = rules.get("backdrop_manual_ranges") or []
        covered = set()
        for item in ranges:
            fa = str(item.get("from") or "")[:10]
            tb = str(item.get("to") or "")[:10]
            for d in csi["date"].dt.strftime("%Y-%m-%d"):
                if fa <= d <= tb:
                    covered.add(d)
        gaps = [d for d in csi["date"].dt.strftime("%Y-%m-%d") if d not in covered]
        from collections import Counter

        cnt = Counter(labels)
        print("[backdrop]", dict(cnt), "blocks", sum(1 for i in range(1, len(labels)) if labels[i] != labels[i - 1]) + (1 if labels else 0))
        if gaps:
            print("[gap trading days] (未落入任何人工区间，当前默认震荡):", ", ".join(gaps))
        else:
            print("[gap] 2025 交易日全部落在人工区间内")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
