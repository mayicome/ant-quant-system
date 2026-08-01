# -*- coding: utf-8 -*-
"""
封单评级「次日验证日报」——给持仓人看的一目了然对照表。

用法（收盘后跑）：
  python tools/seal_rating_daily_verify.py
  python tools/seal_rating_daily_verify.py --seal-file history_data/封单结构_20260715.xlsx
  python tools/seal_rating_daily_verify.py --seal-date 20260715
  python tools/seal_rating_daily_verify.py --auto-run

逻辑：
  1) 读昨日（封板日）封单结构表
  2) 用 v2 规则重算「强/中/弱」（与现程序一致；表里若已是新评级也可）
  3) 拉次一交易日 OHLC，相对封板日收盘算开盘/收盘/最低
  4) 输出：总览一句话 + 分档统计 + 个股明细
  5) --auto-run：验证日=今天，封板日=上一交易日；另导出「分档对照」图
     图片名：封单评级验证日报_YYYYMMDD.png（日期=验证日，不是封板日）
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from seal_rating_v2 import rate_seal_v2  # noqa: E402
from utils.history_data_archive import archive_search_roots  # noqa: E402
from utils.trading_day import is_tradeday  # noqa: E402

HIST = ROOT / "history_data"
DAILY = ROOT / "data" / "daily_cache"


def resolve_seal_file(seal_ymd: str) -> Optional[Path]:
    """现行 history_data 优先，再回退存档。"""
    name = f"封单结构_{seal_ymd}.xlsx"
    for root in archive_search_roots(str(HIST)):
        p = Path(root) / name
        if p.is_file():
            return p
    return None

# 旧五档 → 粗映射（仅当无法用特征重算时兜底）
_LEGACY_MAP = {
    "强": "强",
    "中": "中",
    "弱": "弱",
    "🟢 强势封板": "强",
    "🔥 超强极致封板": "强",
    "🟡 中等封板": "中",
    "🟠 弱势封板": "弱",
    "🔴 虚封高危": "弱",
}
_SCORE = {"强": 3, "中": 2, "弱": 1}


def normalize_code(v: object) -> str:
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", str(v or "").strip().upper())
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def find_col(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for a in aliases:
        if a.strip().lower() in lower:
            return lower[a.strip().lower()]
    for a in aliases:
        k = a.strip().lower()
        for c in df.columns:
            if k in str(c).strip().lower():
                return c
    return None


def newest_seal_file() -> Optional[Path]:
    cands = []
    seen = set()
    for root in archive_search_roots(str(HIST)):
        for p in Path(root).glob("封单结构_*.xlsx"):
            if any(x in p.name for x in ("含次日", "滚动", "评估", "参数", "特征", "v2", "持仓", "验证日报")):
                continue
            if p.name.startswith("~$"):
                continue
            m = re.search(r"(\d{8})", p.name)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            cands.append((m.group(1), p))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[-1][1]


def prev_tradeday_before(verify: date) -> Optional[date]:
    """验证日的前一个交易日（不含验证日本身）= 封板日。"""
    d = verify - timedelta(days=1)
    for _ in range(30):
        if is_tradeday(d):
            return d
        d -= timedelta(days=1)
    return None


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def export_summary_png(
    summary: pd.DataFrame,
    headline: str,
    verify_ymd: str,
    seal_ymd: str,
    out_path: Path,
) -> bool:
    """把「分档对照」画成 PNG（无 GUI，适合盘后批跑）。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        matplotlib.rcParams["font.sans-serif"] = [
            "SimHei",
            "Microsoft YaHei",
            "Arial Unicode MS",
            "sans-serif",
        ]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception as e:
        print(f"导出图片失败（缺少 matplotlib）: {e}")
        return False

    # PNG 不展示「读法」（偏长提示语，对照表里看数字即可）
    view = summary.drop(columns=["读法"], errors="ignore")
    cols = list(view.columns)
    cell = []
    for _, row in view.iterrows():
        line = []
        for c in cols:
            v = row[c]
            if pd.isna(v):
                line.append("")
            else:
                line.append(str(v))
        cell.append(line)

    n_rows = max(len(cell), 1)
    n_cols = max(len(cols), 1)
    fig_w = max(10.0, 1.15 * n_cols + 2.0)
    fig_h = 1.05 + 0.36 * (n_rows + 1)
    fig = plt.figure(figsize=(fig_w, fig_h), facecolor="white")
    # 上：三行说明；下：表格。hspace 尽量小，避免一句话与表拉开
    gs = fig.add_gridspec(2, 1, height_ratios=[0.85, 2.0], hspace=0.0)
    ax_head = fig.add_subplot(gs[0])
    ax_tab = fig.add_subplot(gs[1])
    ax_head.axis("off")
    ax_tab.axis("off")

    title = f"封单评级验证日报_{verify_ymd}"
    subtitle = f"封板日 {seal_ymd} → 验证日 {verify_ymd}"
    ax_head.text(
        0.0,
        0.0,
        f"{title}\n{subtitle}\n{headline}",
        transform=ax_head.transAxes,
        ha="left",
        va="bottom",
        fontsize=10,
        linespacing=1.25,
    )

    table = ax_tab.table(
        cellText=cell,
        colLabels=cols,
        cellLoc="center",
        loc="upper center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.35)
    for (r, c), cell_obj in table.get_celld().items():
        cell_obj.set_edgecolor("#CCCCCC")
        if r == 0:
            cell_obj.set_facecolor("#F0F4F8")
            cell_obj.set_text_props(weight="bold")
        elif c == 0 and r > 0 and r - 1 < len(cell):
            lab = cell[r - 1][0] if cell else ""
            if lab == "强":
                cell_obj.set_facecolor("#E8F5E9")
            elif lab == "弱":
                cell_obj.set_facecolor("#FFEBEE")
            elif lab == "中":
                cell_obj.set_facecolor("#FFF8E1")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", pad_inches=0.08, facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_path.is_file()


def load_daily(code6: str) -> Optional[pd.DataFrame]:
    for suf in (".SZ", ".SH", ".BJ"):
        p = DAILY / f"{code6}{suf}.csv"
        if p.is_file():
            df = pd.read_csv(p)
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
            df = df.dropna(subset=["date"]).sort_values("date")
            for c in ("open", "high", "low", "close"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            return df
    hits = list(DAILY.glob(f"{code6}.*.csv"))
    if not hits:
        return None
    df = pd.read_csv(hits[0])
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    return df.dropna(subset=["date"]).sort_values("date")


def next_day_ohlc(code6: str, seal_ymd: str) -> Optional[dict]:
    df = load_daily(code6)
    if df is None or df.empty:
        return None
    seal_dt = pd.Timestamp(datetime.strptime(seal_ymd, "%Y%m%d"))
    before = df[df["date"] <= seal_dt]
    if before.empty:
        return None
    seal_close = float(before.iloc[-1]["close"] or 0)
    if seal_close <= 0:
        return None
    after = df[df["date"] > before.iloc[-1]["date"]]
    if after.empty:
        return None
    nxt = after.iloc[0]
    o, h, l, c = [float(nxt[x] or 0) for x in ("open", "high", "low", "close")]
    if min(o, h, l, c) <= 0:
        return None
    return {
        "seal_close": seal_close,
        "next_date": nxt["date"].strftime("%Y-%m-%d"),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "open_ret": (o / seal_close - 1) * 100,
        "close_ret": (c / seal_close - 1) * 100,
        "low_ret": (l / seal_close - 1) * 100,
        "high_ret": (h / seal_close - 1) * 100,
        "hold_vs_open": (c / seal_close - o / seal_close) * 100,
        "early_better": o > c,
        "gap_down": (o / seal_close - 1) * 100 < -1,
    }


def resolve_rating(row: pd.Series, cols: dict) -> Tuple[str, int, str]:
    """返回 (强/中/弱, score, 来源说明)。优先用特征重算 v2。"""
    stab = row[cols["stab"]] if cols.get("stab") else None
    amt = row[cols["amt"]] if cols.get("amt") else None
    hard = row[cols["hard"]] if cols.get("hard") else None
    rush = row[cols["rush"]] if cols.get("rush") else None
    trend = row[cols["trend"]] if cols.get("trend") else None
    if cols.get("stab") or cols.get("amt"):
        v2 = rate_seal_v2(
            stability=stab,
            close_amt_yi=amt,
            hardness_pct=hard,
            rush_pct=rush,
            trend=trend,
        )
        return v2["rating_v2"], int(v2["rating_v2_score"]), "v2规则重算"
    raw = str(row[cols["rating"]]).strip() if cols.get("rating") else ""
    mapped = _LEGACY_MAP.get(raw)
    if mapped:
        return mapped, _SCORE[mapped], f"表内原文映射({raw})"
    return "弱", 1, "无法识别，记弱"


def build_detail(seal_path: Path, seal_ymd: str) -> pd.DataFrame:
    raw = pd.read_excel(seal_path)
    cols = {
        "code": find_col(raw, ["股票代码", "代码", "code"]),
        "name": find_col(raw, ["股票名称", "名称", "name"]),
        "amt": find_col(raw, ["收盘封单金额(亿)", "close_order_amount_yi"]),
        "hard": find_col(raw, ["封板硬度", "seal_hardness"]),
        "rush": find_col(raw, ["抢筹烈度", "rush_intensity"]),
        "stab": find_col(raw, ["封单稳定性", "order_stability"]),
        "trend": find_col(raw, ["封单运行趋势", "order_trend"]),
        "rating": find_col(raw, ["封单评级", "order_rating"]),
    }
    if cols["code"] is None:
        raise ValueError("找不到股票代码列")

    rows = []
    for _, r in raw.iterrows():
        code = normalize_code(r[cols["code"]])
        if not code:
            continue
        rating, score, src = resolve_rating(r, cols)
        ohlc = next_day_ohlc(code, seal_ymd)
        file_rating = str(r[cols["rating"]]).strip() if cols.get("rating") else ""
        base = {
            "代码": code,
            "名称": str(r[cols["name"]]).strip() if cols.get("name") else "",
            "昨日评级": rating,
            "评级分": score,
            "表内原评级": file_rating,
            "评级来源": src,
        }
        if ohlc is None:
            base.update({"次日": "", "有效": False})
            rows.append(base)
            continue
        base.update(
            {
                "次日": ohlc["next_date"],
                "有效": True,
                "开盘涨跌%": round(ohlc["open_ret"], 2),
                "收盘涨跌%": round(ohlc["close_ret"], 2),
                "最低涨跌%": round(ohlc["low_ret"], 2),
                "最高涨跌%": round(ohlc["high_ret"], 2),
                "持有相对开盘卖(pp)": round(ohlc["hold_vs_open"], 2),
                "早卖更优": "是" if ohlc["early_better"] else "否",
                "开盘低开<-1%": "是" if ohlc["gap_down"] else "否",
            }
        )
        rows.append(base)
    return pd.DataFrame(rows)


def summarize(detail: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    valid = detail[detail["有效"] == True].copy()  # noqa: E712
    rows = []
    for lab in ("强", "中", "弱"):
        g = valid[valid["昨日评级"] == lab]
        if g.empty:
            rows.append(
                {
                    "昨日评级": lab,
                    "只数": 0,
                    "开盘均%": np.nan,
                    "收盘均%": np.nan,
                    "最低均%": np.nan,
                    "低开占比%": np.nan,
                    "早卖更优%": np.nan,
                    "持有相对开盘卖(pp)": np.nan,
                    "读法": "无样本",
                }
            )
            continue
        early_pct = (g["早卖更优"] == "是").mean() * 100
        gap_pct = (g["开盘低开<-1%"] == "是").mean() * 100
        hold_edge = g["持有相对开盘卖(pp)"].mean()
        if lab == "强":
            tip = "应少见深低开；可观察到尾盘再定"
        elif lab == "中":
            tip = "可逢高减，不必极端"
        else:
            tip = "低开/盘中砸更常见；倾向盯盘早处理"
        rows.append(
            {
                "昨日评级": lab,
                "只数": len(g),
                "开盘均%": round(g["开盘涨跌%"].mean(), 2),
                "收盘均%": round(g["收盘涨跌%"].mean(), 2),
                "最低均%": round(g["最低涨跌%"].mean(), 2),
                "低开占比%": round(gap_pct, 1),
                "早卖更优%": round(early_pct, 1),
                "持有相对开盘卖(pp)": round(hold_edge, 2),
                "读法": tip,
            }
        )
    summary = pd.DataFrame(rows)

    # 一句话结论
    strong = valid[valid["昨日评级"] == "强"]
    weak = valid[valid["昨日评级"] == "弱"]
    parts = []
    if len(strong) and len(weak):
        gap_s = (strong["开盘低开<-1%"] == "是").mean() * 100
        gap_w = (weak["开盘低开<-1%"] == "是").mean() * 100
        parts.append(f"低开占比：强 {gap_s:.0f}% vs 弱 {gap_w:.0f}%")
        parts.append(
            f"开盘均：强 {strong['开盘涨跌%'].mean():+.1f}% vs 弱 {weak['开盘涨跌%'].mean():+.1f}%"
        )
        parts.append(
            f"盘中最低均：强 {strong['最低涨跌%'].mean():+.1f}% vs 弱 {weak['最低涨跌%'].mean():+.1f}%"
        )
        ok = (gap_s + 5 < gap_w) or (strong["开盘涨跌%"].mean() > weak["开盘涨跌%"].mean() + 1)
        verdict = "昨日分层有效（强档路径更稳）" if ok else "昨日分层不明显（弱市/样本少时常见）"
    elif len(valid) == 0:
        verdict = "次日行情尚未入库，无法验证"
        parts = ["请确认 daily_cache 已有次日 K 线"]
    else:
        verdict = "样本偏少，仅供参考"
        parts.append(f"有效 {len(valid)} 只")
    headline = verdict + "。" + "；".join(parts) if parts else verdict
    return summary, headline


def write_report(detail: pd.DataFrame, summary: pd.DataFrame, headline: str, seal_ymd: str, next_day: str, out_path: Path) -> None:
    how = pd.DataFrame(
        [
            {"项目": "验证目的", "说明": "评级给持仓人看：次日尽早出手还是可拿到尾盘再定（不涉及次日买入）"},
            {"项目": "封板日", "说明": seal_ymd},
            {"项目": "表现日", "说明": next_day or "（无）"},
            {"项目": "一看什么", "说明": "强档应：高开更多、低开更少、盘中最低更浅；弱档相反"},
            {"项目": "不必死盯", "说明": "「早卖更优%」接近50%很正常；主看低开占比与开盘/最低均值"},
            {"项目": "一句话", "说明": headline},
        ]
    )
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        how.to_excel(w, sheet_name="说明", index=False)
        summary.to_excel(w, sheet_name="分档对照", index=False)
        # 明细按评级、开盘涨跌排序，方便扫
        d = detail.copy()
        d["_s"] = d["评级分"].fillna(0) if "评级分" in d.columns else 0
        sort_cols = ["_s"]
        ascending = [False]
        if "开盘涨跌%" in d.columns:
            sort_cols.append("开盘涨跌%")
            ascending.append(False)
        d = d.sort_values(sort_cols, ascending=ascending, na_position="last")
        d = d.drop(columns=["_s", "评级分"], errors="ignore")
        d.to_excel(w, sheet_name="个股明细", index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="封单评级次日验证日报")
    parser.add_argument("--seal-file", help="封单结构 xlsx")
    parser.add_argument("--seal-date", help="封板日 YYYYMMDD（与 --seal-file 二选一优先文件）")
    parser.add_argument(
        "--auto-run",
        action="store_true",
        help="盘后批跑：验证日=今天，封板日=上一交易日；写出 Excel + 分档对照 PNG",
    )
    parser.add_argument("--verify-date", help="验证日 YYYYMMDD（默认今天；auto-run 可用）")
    parser.add_argument("--out", help="输出 Excel 路径")
    parser.add_argument("--png-out", help="输出 PNG 路径（默认 history_data/封单评级验证日报_验证日.png）")
    args = parser.parse_args()

    verify_ymd = ""
    if args.verify_date:
        verify_ymd = args.verify_date.strip().replace("-", "")[:8]
    elif args.auto_run:
        verify_ymd = ymd(date.today())

    if args.auto_run and not args.seal_file and not args.seal_date:
        try:
            verify_d = datetime.strptime(verify_ymd, "%Y%m%d").date()
        except ValueError:
            print(f"无效验证日: {verify_ymd}")
            return 1
        seal_d = prev_tradeday_before(verify_d)
        if seal_d is None:
            print(f"找不到验证日 {verify_ymd} 的上一交易日")
            return 1
        seal_ymd = ymd(seal_d)
        seal_path = resolve_seal_file(seal_ymd)
        if seal_path is None:
            print(f"找不到封单结构: 封单结构_{seal_ymd}.xlsx（含存档）")
            return 1
    elif args.seal_file:
        seal_path = Path(args.seal_file)
        if not seal_path.is_file():
            print(f"找不到文件: {seal_path}")
            return 1
        m = re.search(r"(\d{8})", seal_path.name)
        seal_ymd = args.seal_date or (m.group(1) if m else "")
    elif args.seal_date:
        seal_ymd = args.seal_date.strip()
        seal_path = resolve_seal_file(seal_ymd)
        if seal_path is None:
            print(f"找不到: 封单结构_{seal_ymd}.xlsx（含存档）")
            return 1
    else:
        seal_path = newest_seal_file()
        if seal_path is None:
            print("history_data（含存档）下没有封单结构_YYYYMMDD.xlsx")
            return 1
        m = re.search(r"(\d{8})", seal_path.name)
        seal_ymd = m.group(1) if m else ""

    if not seal_ymd:
        print("无法解析封板日，请传 --seal-date")
        return 1

    print(f"封板日 {seal_ymd}  文件 {seal_path.name}" + (f"  验证日 {verify_ymd}" if verify_ymd else ""))
    detail = build_detail(seal_path, seal_ymd)
    summary, headline = summarize(detail)
    valid = detail[detail["有效"] == True]  # noqa: E712
    next_day = str(valid["次日"].iloc[0]) if len(valid) else ""
    # 验证日优先：显式参数 / auto-run 的今天；否则用数据里的次日
    if not verify_ymd:
        verify_ymd = next_day.replace("-", "") if next_day else ""

    if args.auto_run and verify_ymd:
        out_name = f"封单评级验证日报_{verify_ymd}.xlsx"
    else:
        out_name = (
            f"封单评级验证日报_{seal_ymd}_次日"
            f"{next_day.replace('-', '') if next_day else '未知'}.xlsx"
        )
    out_path = Path(args.out) if args.out else HIST / out_name

    write_report(detail, summary, headline, seal_ymd, next_day or verify_ymd, out_path)

    print("\n【一句话】", headline)
    print("\n【分档对照】")
    print(summary.to_string(index=False))
    print(f"\n已写出: {out_path}")

    if args.auto_run:
        if not verify_ymd:
            print("无法确定验证日，跳过 PNG")
            return 1
        png_path = Path(args.png_out) if args.png_out else HIST / f"封单评级验证日报_{verify_ymd}.png"
        ok = export_summary_png(summary, headline, verify_ymd, seal_ymd, png_path)
        if not ok:
            print(f"PNG 写出失败: {png_path}")
            return 1
        print(f"已写出图片: {png_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
