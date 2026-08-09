# -*- coding: utf-8 -*-
"""龙虎榜「次日溢价预测」单日复盘。

每个交易日对照「上一交易日」龙虎榜解析里的溢价预测 vs 当日（验证日）真实开高低收。

用法：
  python tools/lhb_premium_daily_verify.py --auto-run
  python tools/lhb_premium_daily_verify.py --verify-date 20260717
  python tools/lhb_premium_daily_verify.py --pred-date 20260716
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

from lhb_analysis_gui import compute_lhb_strength_and_premium, extract_lhb_seat_amounts  # noqa: E402
from utils.daily_cache_reader import load_daily_from_cache  # noqa: E402
from utils.trading_day import is_tradeday  # noqa: E402

HIST = ROOT / "history_data"

# 新文案优先；旧五档文案映射到三档，便于读历史 xlsx
_TIER_MAP = {
    "强": "强",
    "中": "中",
    "弱": "弱",
    "极高溢价": "强",
    "高溢价": "中",
    "中等溢价": "中",
    "低溢价": "弱",
    "负溢价": "弱",
}
_TIER_ORDER = ["强", "中", "弱"]


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def prev_tradeday_before(verify: date) -> Optional[date]:
    d = verify - timedelta(days=1)
    for _ in range(30):
        if is_tradeday(d):
            return d
        d -= timedelta(days=1)
    return None


def _norm_header(c: object) -> str:
    return re.sub(r"\s+", "", str(c or "").replace("\n", ""))


def _norm_code(v: object) -> str:
    s = re.sub(r"\D", "", str(v or ""))
    if not s:
        return ""
    return (s.zfill(6) if len(s) < 6 else s)[-6:]


def _tier_from_premium(text: object) -> str:
    s = str(text or "")
    for k, v in _TIER_MAP.items():
        if k in s:
            return v
    return "未知"


def find_lhb_file(pred_ymd: str) -> Optional[Path]:
    name = f"龙虎榜解析_{pred_ymd}.xlsx"
    for base in (HIST, HIST / "存档"):
        p = base / name
        if p.is_file():
            return p
    arch = HIST / "存档"
    if arch.is_dir():
        hits = list(arch.rglob(name))
        if hits:
            return hits[0]
    return None


def read_summary(path: Path) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_excel(path, sheet_name=0)
    except Exception:
        return None
    df = df.copy()
    df.columns = [_norm_header(c) for c in df.columns]
    if "代码" not in df.columns:
        return None
    if "主力强度(0-100)" not in df.columns and "主力强度" in df.columns:
        df = df.rename(columns={"主力强度": "主力强度(0-100)"})
    return df


def next_day_rets(code6: str, pred_ymd: str) -> Optional[dict]:
    df = load_daily_from_cache(code6)
    if df is None or df.empty:
        return None
    seal_dt = datetime.strptime(pred_ymd, "%Y%m%d").date()
    before = df[df["date"] <= seal_dt]
    if before.empty:
        return None
    base_close = float(before.iloc[-1]["close"] or 0)
    if base_close <= 0:
        return None
    after = df[df["date"] > before.iloc[-1]["date"]]
    if after.empty:
        return None
    nxt = after.iloc[0]
    o, h, l, c = [float(nxt[x] or 0) for x in ("open", "high", "low", "close")]
    if min(o, h, l, c) <= 0:
        return None
    return {
        "次日": nxt["date"].strftime("%Y-%m-%d") if hasattr(nxt["date"], "strftime") else str(nxt["date"]),
        "基准收盘": round(base_close, 4),
        "开盘涨跌%": round((o / base_close - 1) * 100, 3),
        "最高涨跌%": round((h / base_close - 1) * 100, 3),
        "最低涨跌%": round((l / base_close - 1) * 100, 3),
        "收盘涨跌%": round((c / base_close - 1) * 100, 3),
    }


def hit_rule(tier: str, open_ret: float, high_ret: float, close_ret: float) -> Tuple[bool, str]:
    if tier == "强":
        return open_ret > 0, "次日高开(开盘>0%)"
    if tier == "中":
        return abs(open_ret) <= 2.0, "震荡(|开|≤2%)"
    if tier == "弱":
        return open_ret < 0, "确实低开(开盘<0%)"
    return False, "未知档"


def build_detail(pred_path: Path, pred_ymd: str) -> pd.DataFrame:
    summary = read_summary(pred_path)
    if summary is None or summary.empty:
        return pd.DataFrame()
    rows = []
    for _, r in summary.iterrows():
        code = _norm_code(r.get("代码"))
        if not code:
            continue
        jg_cnt, jg_buy, jg_net, bx_buy, bx_net, yz_net = extract_lhb_seat_amounts(r)
        score, prem = compute_lhb_strength_and_premium(
            jg_cnt=jg_cnt,
            jg_buy=jg_buy,
            jg_net=jg_net,
            bx_buy=bx_buy,
            bx_net=bx_net,
            yz_net=yz_net,
        )
        tier = _tier_from_premium(prem)
        rets = next_day_rets(code, pred_ymd)
        base = {
            "代码": code,
            "名称": str(r.get("名称") or ""),
            "主力强度": score,
            "预测档": tier,
            "次日溢价预测": prem,
        }
        if rets is None:
            base["有效"] = False
            rows.append(base)
            continue
        hit, rule = hit_rule(tier, rets["开盘涨跌%"], rets["最高涨跌%"], rets["收盘涨跌%"])
        base.update(rets)
        base["有效"] = True
        base["命中"] = bool(hit)
        base["命中规则"] = rule
        rows.append(base)
    return pd.DataFrame(rows)


_MIN_HIT_N = 3  # 少于此只数不报命中率（单日 0/100% 无意义）


def summarize(detail: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    valid = detail[detail["有效"] == True].copy()  # noqa: E712
    tier_rows = []
    for tier in _TIER_ORDER:
        g = valid[valid["预测档"] == tier]
        n = int(len(g))
        if n == 0:
            continue  # 当日无该档，不占一行
        hit = (
            round((g["命中"] == True).mean() * 100, 1)  # noqa: E712
            if n >= _MIN_HIT_N
            else np.nan
        )
        tier_rows.append(
            {
                "预测档": tier,
                "只数": n,
                "命中率%": hit,
                "开盘均%": round(g["开盘涨跌%"].mean(), 2),
                "最高均%": round(g["最高涨跌%"].mean(), 2),
                "收盘均%": round(g["收盘涨跌%"].mean(), 2),
                "高开占比%": round((g["开盘涨跌%"] > 0).mean() * 100, 1),
                "低开占比%": round((g["开盘涨跌%"] < 0).mean() * 100, 1),
            }
        )
    summary = pd.DataFrame(tier_rows)

    hi = valid[valid["预测档"] == "强"]
    lo = valid[valid["预测档"] == "弱"]
    parts = [f"有效 {len(valid)} 只"]
    if len(hi) and len(lo):
        parts.append(
            f"开盘均：强 {hi['开盘涨跌%'].mean():+.2f}% vs 弱 {lo['开盘涨跌%'].mean():+.2f}%"
        )
        parts.append(
            f"高开占比：强 {(hi['开盘涨跌%']>0).mean()*100:.0f}% vs 弱 {(lo['开盘涨跌%']>0).mean()*100:.0f}%"
        )
        ok = hi["开盘涨跌%"].mean() > lo["开盘涨跌%"].mean() + 0.3
        verdict = "昨日预测分层有效" if ok else "昨日预测分层偏弱"
    elif len(valid) == 0:
        verdict = "次日行情未入库或无龙虎榜样本"
        parts = ["请确认 daily_cache 与龙虎榜解析文件"]
    else:
        verdict = "样本偏少，仅供参考"
    headline = verdict + "。" + "；".join(parts) if parts else verdict
    return summary, headline


def export_summary_png(
    summary: pd.DataFrame,
    headline: str,
    verify_ymd: str,
    pred_ymd: str,
    out_path: Path,
) -> bool:
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
        print(f"导出图片失败: {e}")
        return False

    cols = list(summary.columns)
    cell = []
    for _, row in summary.iterrows():
        cell.append(["" if pd.isna(row[c]) else str(row[c]) for c in cols])

    n_rows = max(len(cell), 1)
    fig_w = max(10.0, 1.15 * max(len(cols), 1) + 2.0)
    fig_h = 1.05 + 0.36 * (n_rows + 1)
    fig = plt.figure(figsize=(fig_w, fig_h), facecolor="white")
    gs = fig.add_gridspec(2, 1, height_ratios=[0.85, 2.0], hspace=0.0)
    ax_head = fig.add_subplot(gs[0])
    ax_tab = fig.add_subplot(gs[1])
    ax_head.axis("off")
    ax_tab.axis("off")
    ax_head.text(
        0.0,
        0.0,
        f"龙虎榜溢价验证日报_{verify_ymd}\n预测日 {pred_ymd} → 验证日 {verify_ymd}\n{headline}",
        transform=ax_head.transAxes,
        ha="left",
        va="bottom",
        fontsize=10,
        linespacing=1.25,
    )
    table = ax_tab.table(cellText=cell, colLabels=cols, cellLoc="center", loc="upper center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.35)
    for (r, c), cell_obj in table.get_celld().items():
        cell_obj.set_edgecolor("#CCCCCC")
        if r == 0:
            cell_obj.set_facecolor("#F0F4F8")
            cell_obj.set_text_props(weight="bold")
        elif c == 0 and r > 0 and r - 1 < len(cell):
            lab = cell[r - 1][0]
            if lab == "强":
                cell_obj.set_facecolor("#E8F5E9")
            elif lab == "弱":
                cell_obj.set_facecolor("#FFEBEE")
            elif lab == "中":
                cell_obj.set_facecolor("#FFF8E1")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", pad_inches=0.08, facecolor="white")
    plt.close(fig)
    return out_path.is_file()


def write_report(
    detail: pd.DataFrame,
    summary: pd.DataFrame,
    headline: str,
    pred_ymd: str,
    verify_ymd: str,
    out_path: Path,
) -> None:
    how = pd.DataFrame(
        [
            {"项目": "预测日", "说明": pred_ymd},
            {"项目": "验证日", "说明": verify_ymd},
            {"项目": "算法", "说明": "compute_lhb_strength_and_premium（按席位特征重算）"},
            {"项目": "基准价", "说明": "预测日收盘（或此前最近有K线日）"},
            {"项目": "一句话", "说明": headline},
        ]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        how.to_excel(w, sheet_name="说明", index=False)
        summary.to_excel(w, sheet_name="分档对照", index=False)
        d = detail.copy()
        if "主力强度" in d.columns:
            d = d.sort_values("主力强度", ascending=False, na_position="last")
        d.to_excel(w, sheet_name="个股明细", index=False)


def run_verify(
    *,
    verify_ymd: str,
    pred_ymd: str = "",
    out: str = "",
    png_out: str = "",
    write_png: bool = False,
) -> Tuple[int, str, pd.DataFrame, pd.DataFrame]:
    """执行单日复盘。返回 (exit_code, headline, summary, detail)。"""
    if not pred_ymd:
        try:
            vd = datetime.strptime(verify_ymd, "%Y%m%d").date()
        except ValueError:
            print(f"无效验证日: {verify_ymd}")
            return 1, "", pd.DataFrame(), pd.DataFrame()
        pd_ = prev_tradeday_before(vd)
        if pd_ is None:
            print(f"找不到验证日 {verify_ymd} 的上一交易日")
            return 1, "", pd.DataFrame(), pd.DataFrame()
        pred_ymd = ymd(pd_)

    pred_path = find_lhb_file(pred_ymd)
    if pred_path is None:
        print(f"找不到龙虎榜解析: 龙虎榜解析_{pred_ymd}.xlsx（含存档）")
        return 1, "", pd.DataFrame(), pd.DataFrame()

    print(f"预测日 {pred_ymd}  文件 {pred_path}  验证日 {verify_ymd}")
    detail = build_detail(pred_path, pred_ymd)
    if detail.empty:
        print("龙虎榜文件无有效股票行")
        return 1, "", pd.DataFrame(), pd.DataFrame()

    summary, headline = summarize(detail)
    out_path = Path(out) if out else HIST / f"龙虎榜溢价验证日报_{verify_ymd}.xlsx"
    write_report(detail, summary, headline, pred_ymd, verify_ymd, out_path)

    print("\n【一句话】", headline)
    print("\n【分档对照】")
    print(summary.to_string(index=False))
    print(f"\n已写出: {out_path}")

    if write_png:
        png_path = Path(png_out) if png_out else HIST / f"龙虎榜溢价验证日报_{verify_ymd}.png"
        if export_summary_png(summary, headline, verify_ymd, pred_ymd, png_path):
            print(f"已写出图片: {png_path}")
        else:
            print(f"PNG 写出失败: {png_path}")
            return 1, headline, summary, detail

    return 0, headline, summary, detail


def main() -> int:
    parser = argparse.ArgumentParser(description="龙虎榜次日溢价预测单日复盘")
    parser.add_argument("--auto-run", action="store_true", help="验证日=今天，预测日=上一交易日；写 Excel+PNG")
    parser.add_argument("--verify-date", help="验证日 YYYYMMDD（默认今天）")
    parser.add_argument("--pred-date", help="预测日/龙虎榜日 YYYYMMDD（默认验证日上一交易日）")
    parser.add_argument("--out", help="输出 Excel 路径")
    parser.add_argument("--png-out", help="输出 PNG 路径")
    args = parser.parse_args()

    if args.verify_date:
        verify_ymd = args.verify_date.strip().replace("-", "")[:8]
    elif args.auto_run:
        verify_ymd = ymd(date.today())
    else:
        verify_ymd = ymd(date.today())

    pred_ymd = args.pred_date.strip().replace("-", "")[:8] if args.pred_date else ""
    # 手动运行与 auto-run 均写出 PNG（也可 --png-out 指定路径）
    write_png = True
    rc, _, _, _ = run_verify(
        verify_ymd=verify_ymd,
        pred_ymd=pred_ymd,
        out=args.out or "",
        png_out=args.png_out or "",
        write_png=write_png,
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
