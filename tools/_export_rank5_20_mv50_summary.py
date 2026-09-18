# -*- coding: utf-8 -*-
"""合并 2024/ + 最终/，过滤「最佳排名5-20 ∧ 市值<50亿」并输出各项汇总。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "history_data" / "马总选股逻辑"
OUT_DIR = BASE / "过滤_排名5-20_市值lt50"
SHEET_GLOB = "*按票_已完成_收盘上MA10_latest.xlsx"

SOURCES = {
    "2024": BASE / "2024",
    "最终": BASE / "最终",
}


def find_sheet(folder: Path) -> Path:
    hits = sorted(folder.glob(SHEET_GLOB))
    if not hits:
        raise FileNotFoundError(f"未找到 {SHEET_GLOB} in {folder}")
    return hits[0]


def metrics(r: pd.Series) -> dict:
    r = pd.to_numeric(r, errors="coerce").dropna()
    n = len(r)
    if n == 0:
        return {
            "n": 0,
            "票均%": None,
            "中位%": None,
            "胜率%": None,
            "亏损率%": None,
            "平盘率%": None,
            "累加%": None,
            "标准差%": None,
            "P5%": None,
            "P25%": None,
            "P75%": None,
            "P95%": None,
            "最差%": None,
            "最好%": None,
            "盈利笔数": 0,
            "亏损笔数": 0,
            "平盘笔数": 0,
        }
    return {
        "n": n,
        "票均%": round(float(r.mean()), 4),
        "中位%": round(float(r.median()), 4),
        "胜率%": round(float((r > 0).mean() * 100), 2),
        "亏损率%": round(float((r < 0).mean() * 100), 2),
        "平盘率%": round(float((r == 0).mean() * 100), 2),
        "累加%": round(float(r.sum()), 2),
        "标准差%": round(float(r.std(ddof=1)), 4) if n > 1 else 0.0,
        "P5%": round(float(r.quantile(0.05)), 4),
        "P25%": round(float(r.quantile(0.25)), 4),
        "P75%": round(float(r.quantile(0.75)), 4),
        "P95%": round(float(r.quantile(0.95)), 4),
        "最差%": round(float(r.min()), 4),
        "最好%": round(float(r.max()), 4),
        "盈利笔数": int((r > 0).sum()),
        "亏损笔数": int((r < 0).sum()),
        "平盘笔数": int((r == 0).sum()),
    }


def apply_filter(df: pd.DataFrame) -> pd.DataFrame:
    rk = pd.to_numeric(df["最佳板块排名"], errors="coerce")
    mv_col = "流通市值_亿_选股日" if "流通市值_亿_选股日" in df.columns else "流通市值_亿"
    mv = pd.to_numeric(df[mv_col], errors="coerce")
    out = df[(rk >= 5) & (rk <= 20) & (mv < 50)].copy()
    out["_最佳板块排名"] = rk.loc[out.index]
    out["_流通市值_亿"] = mv.loc[out.index]
    out["_市值列"] = mv_col
    return out


def load_merged() -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    meta = []
    frames = []
    pool_frames = []
    for tag, folder in SOURCES.items():
        path = find_sheet(folder)
        df = pd.read_excel(path)
        df["_src"] = tag
        df["_src_file"] = path.name
        buy = pd.to_datetime(df.get("买入日"), errors="coerce")
        df["_买入日"] = buy
        df["_年"] = buy.dt.year
        df["_收益率pct"] = pd.to_numeric(df["收益率pct"], errors="coerce")
        meta.append(
            {
                "来源目录": tag,
                "文件": path.name,
                "路径": str(path),
                "原始行数": len(df),
                "买入日起": str(buy.min()),
                "买入日止": str(buy.max()),
            }
        )
        pool_frames.append(df)
        frames.append(apply_filter(df))
    pool = pd.concat(pool_frames, ignore_index=True)
    filt = pd.concat(frames, ignore_index=True)
    return pool, filt, meta


def rows_from_metrics(label: str, m: dict, extra: dict | None = None) -> dict:
    row = {"分组": label, **m}
    if extra:
        row.update(extra)
    return row


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pool, filt, meta = load_merged()
    mv_used = filt["_市值列"].iloc[0] if len(filt) else "流通市值_亿"
    print("filter", len(filt), "/", len(pool), "mv_col", mv_used)
    print("years", filt["_年"].value_counts(dropna=False).to_dict())

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_rows = []

    # 全池对照
    summary_rows.append(
        rows_from_metrics(
            "三年全池(未过滤)",
            metrics(pool["_收益率pct"]),
            {"说明": "硬过滤入池已完成样本"},
        )
    )
    summary_rows.append(
        rows_from_metrics(
            "三年过滤(排名5-20∧市值<50亿)",
            metrics(filt["_收益率pct"]),
            {"说明": f"最佳板块排名∈[5,20] 且 {mv_used}<50"},
        )
    )

    for y in (2024, 2025, 2026):
        p = pool[pool["_年"] == y]
        f = filt[filt["_年"] == y]
        bm = metrics(p["_收益率pct"])
        fm = metrics(f["_收益率pct"])
        summary_rows.append(
            rows_from_metrics(
                f"{y}全池",
                bm,
                {"说明": "对照"},
            )
        )
        lift = None
        if bm["票均%"] is not None and fm["票均%"] is not None:
            lift = round(fm["票均%"] - bm["票均%"], 4)
        summary_rows.append(
            rows_from_metrics(
                f"{y}过滤",
                fm,
                {"说明": f"相对全池票均Δ={lift}"},
            )
        )

    # 分月（过滤）
    filt = filt.copy()
    filt["_月"] = filt["_买入日"].dt.to_period("M").astype(str)
    month_rows = []
    for ym, g in filt.groupby("_月", sort=True):
        month_rows.append(rows_from_metrics(str(ym), metrics(g["_收益率pct"])))

    # 分季
    filt["_季"] = filt["_买入日"].dt.to_period("Q").astype(str)
    quarter_rows = []
    for q, g in filt.groupby("_季", sort=True):
        quarter_rows.append(rows_from_metrics(str(q), metrics(g["_收益率pct"])))

    # 收益分布
    r = pd.to_numeric(filt["_收益率pct"], errors="coerce").dropna()
    bins = [-np.inf, -10, -5, -2, 0, 2, 5, 10, np.inf]
    labels = ["<-10%", "-10~-5", "-5~-2", "-2~0", "0~2", "2~5", "5~10", ">10%"]
    cat = pd.cut(r, bins=bins, labels=labels)
    dist = (
        cat.value_counts()
        .reindex(labels)
        .rename_axis("区间")
        .reset_index(name="笔数")
    )
    dist["占比%"] = (dist["笔数"] / max(len(r), 1) * 100).round(2)

    # 票明细精简列
    keep = [
        c
        for c in [
            "_src",
            "_年",
            "选股日",
            "买入日",
            "代码",
            "股票代码",
            "股票名称",
            "最佳板块排名",
            "_流通市值_亿",
            "收益率pct",
            "满足条件",
            "所属行业最高排名名次",
            "所属概念最高排名名次",
            "买入成交价",
            "样本完成",
        ]
        if c in filt.columns or c.startswith("_")
    ]
    # rebuild keep properly
    detail_cols = []
    for c in [
        "_src",
        "_年",
        "选股日",
        "买入日",
        "代码",
        "股票代码",
        "股票名称",
        "最佳板块排名",
        "_流通市值_亿",
        "收益率pct",
        "满足条件",
        "所属行业最高排名名次",
        "所属概念最高排名名次",
        "买入成交价",
        "样本完成",
    ]:
        if c in filt.columns:
            detail_cols.append(c)
    detail = filt[detail_cols].copy()
    detail = detail.rename(columns={"_src": "来源目录", "_年": "年", "_流通市值_亿": "流通市值_亿_过滤用"})

    # 按年来源对照表
    src_rows = []
    for tag in ("2024", "最终"):
        sub = filt[filt["_src"] == tag]
        src_rows.append(
            rows_from_metrics(
                f"来源={tag}",
                metrics(sub["_收益率pct"]),
                {"买入日起": str(sub["_买入日"].min()), "买入日止": str(sub["_买入日"].max())},
            )
        )

    xlsx_name = f"汇总_排名5-20_市值lt50_三年合并_{stamp}.xlsx"
    xlsx_path = OUT_DIR / xlsx_name
    latest_path = OUT_DIR / "汇总_排名5-20_市值lt50_三年合并_latest.xlsx"

    summary_df = pd.DataFrame(summary_rows)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
        pd.DataFrame(
            [
                {
                    "过滤条件": "最佳板块排名∈[5,20] 且 流通市值<50亿",
                    "市值字段": mv_used,
                    "策略口径": "日线 ma10 买 + sell_half；已完成∧收盘上MA10 按票表",
                    "合并来源": "history_data/马总选股逻辑/2024 + 最终",
                    "生成时间": stamp,
                    "过滤后笔数": len(filt),
                    "全池笔数": len(pool),
                }
            ]
        ).to_excel(w, sheet_name="说明", index=False)
        pd.DataFrame(meta).to_excel(w, sheet_name="数据源", index=False)
        summary_df.to_excel(w, sheet_name="总览", index=False)
        pd.DataFrame(src_rows).to_excel(w, sheet_name="按来源目录", index=False)
        pd.DataFrame(quarter_rows).to_excel(w, sheet_name="分季_过滤", index=False)
        pd.DataFrame(month_rows).to_excel(w, sheet_name="分月_过滤", index=False)
        dist.to_excel(w, sheet_name="收益分布_过滤", index=False)
        detail.to_excel(w, sheet_name="过滤票明细", index=False)

    # copy latest
    latest_path.write_bytes(xlsx_path.read_bytes())

    payload = {
        "xlsx": str(xlsx_path),
        "latest": str(latest_path),
        "mv_col": mv_used,
        "n_pool": len(pool),
        "n_filt": len(filt),
        "summary": summary_rows,
        "quarters": quarter_rows,
        "months": month_rows,
        "dist": dist.to_dict(orient="records"),
        "meta": meta,
    }
    json_path = OUT_DIR / f"汇总_排名5-20_市值lt50_三年合并_{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "汇总_排名5-20_市值lt50_三年合并_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("wrote", xlsx_path)
    print("latest", latest_path)
    for row in summary_rows:
        if row["分组"] in (
            "三年全池(未过滤)",
            "三年过滤(排名5-20∧市值<50亿)",
            "2024过滤",
            "2025过滤",
            "2026过滤",
        ):
            print(
                row["分组"],
                f"n={row['n']} 票均={row['票均%']} 胜率={row['胜率%']}",
            )


if __name__ == "__main__":
    main()
