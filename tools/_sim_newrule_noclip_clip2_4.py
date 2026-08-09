# -*- coding: utf-8 -*-
"""新规则 215 买入（无 Seff clip）→ clip(2,4) 强度分仿真（原生收益，不回填）。

买入: 回测成交明细_新规则.csv
卖出/回笼日: 各日选股收益汇总_新规则 end_date（与卖出 CSV 末笔日一致）
选股序/S: besttest-无涨停均线差0.5to2-MA空头排列 选股结果
  - S = 全量选股池当日只数（sizing）
  - 取票 = 当日开盘夹档成交中按强度分(Elig×8+标签内RS)取前 Seff（不做 max_per_tag）
  - clip 排名 ≠ 导出 xls 行序
收益: 仅用 各日选股收益汇总_新规则；缺键直接失败（不回填 Cond123）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

_ROOT_PKG = Path(__file__).resolve().parents[1]
if str(_ROOT_PKG) not in sys.path:
    sys.path.insert(0, str(_ROOT_PKG))
from sector_stock_filter import EXPORT_ELIG_WEIGHT, clip_strength_sort_key  # noqa: E402

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
CAPITAL0 = 100_000.0
L_, U_ = 2, 4
TAG = "clip2_4"
# 覆盖旧产物；另写一份带 native 后缀便于辨认
PREFIX = f"新规则_无clip资格_{TAG}"
PREFIX_NATIVE = f"新规则_无clip资格_{TAG}_native"

BUY = ROOT / "回测成交明细_新规则.csv"
SELL = ROOT / "回测成交明细_新规则_卖出.csv"
NATIVE_SUM = ROOT / "各日选股收益汇总_新规则.xlsx"
COND_SUM = ROOT / (
    "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_"
    "条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx"
)
SEL = ROOT / (
    "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls"
)
PRIOR_FILLS = ROOT / "Cond123_besttest_clip2_4_成交明细.csv"
PRIOR_EQ = ROOT / "Cond123_besttest_clip2_4_权益曲线.csv"


def code6(v) -> str:
    if pd.isna(v):
        return ""
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        return str(v).strip().zfill(6)[-6:]


cal = ak.tool_trade_date_hist_sina()
cal["trade_date"] = pd.to_datetime(cal["trade_date"]).dt.strftime("%Y-%m-%d")
tdays = cal[(cal["trade_date"] >= "2026-07-01") & (cal["trade_date"] <= "2026-08-10")][
    "trade_date"
].tolist()
idx = {d: i for i, d in enumerate(tdays)}


def next_td(d: str, n: int = 1) -> str:
    return tdays[idx[d] + n]


def clip_n(S: int, L: int, U: int) -> int:
    return int(max(L, min(U, S)))


def max_dd(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    peak = equity.cummax()
    return float((equity / peak - 1.0).min() * 100.0)


def keyset(df: pd.DataFrame, dcol: str = "选股日", ccol: str = "代码") -> set:
    return set(zip(df[dcol], df[ccol]))


def main() -> None:
    sel = pd.read_excel(SEL)
    sel["选股日"] = pd.to_datetime(sel["选股日"]).dt.strftime("%Y-%m-%d")
    sel["股票代码"] = sel["股票代码"].map(code6)
    sel = sel[sel["选股日"].str.startswith("2026-07")].copy()
    sel["池序"] = sel.groupby("选股日").cumcount()  # 导出行序（对照用）
    order = sel.set_index(["选股日", "股票代码"])["池序"].to_dict()
    smap = sel.groupby("选股日").size().to_dict()
    strength = {
        (r["选股日"], r["股票代码"]): (r.get("合格榜内序位"), r.get("合格榜标签内RS排名"))
        for _, r in sel.iterrows()
    }

    native = pd.read_excel(NATIVE_SUM)
    native["代码"] = native["代码"].map(code6)
    native["选股日"] = pd.to_datetime(native["选股日"]).dt.strftime("%Y-%m-%d")
    native["end_date"] = pd.to_datetime(native["end_date"]).dt.strftime("%Y-%m-%d")
    native["收益率pct"] = pd.to_numeric(native["收益率pct"], errors="coerce")
    native = native[
        ["选股日", "代码", "收益率pct", "end_date", "合格榜内序位", "合格榜标签内RS排名"]
    ].drop_duplicates(["选股日", "代码"])

    buy = pd.read_csv(BUY, encoding="utf-8-sig")
    buy = buy.drop(columns=[c for c in ("start_date", "end_date") if c in buy.columns])
    buy["代码"] = buy["代码"].map(code6)
    buy["选股日"] = pd.to_datetime(buy["选股日"]).dt.strftime("%Y-%m-%d")
    buy["买入日"] = pd.to_datetime(buy["日期"]).dt.strftime("%Y-%m-%d")
    buy["价格"] = pd.to_numeric(buy["价格"], errors="coerce")
    buy["金额"] = pd.to_numeric(buy["金额"], errors="coerce")
    tip = buy["触发信息"].astype(str)
    buy["分支"] = np.where(
        tip.str.contains("开盘买入"),
        "开盘夹档",
        np.where(tip.str.contains("单点买入"), "高开回踩", "其他"),
    )
    buy["t"] = buy["时间"].astype(str) if "时间" in buy.columns else ""

    sell = pd.read_csv(SELL, encoding="utf-8-sig")
    sell["代码"] = sell["代码"].map(code6)
    sell["选股日"] = pd.to_datetime(sell["选股日"]).dt.strftime("%Y-%m-%d")
    sell["卖出日"] = pd.to_datetime(sell["日期"]).dt.strftime("%Y-%m-%d")
    sell_last = (
        sell.groupby(["选股日", "代码"], as_index=False)["卖出日"]
        .max()
        .rename(columns={"卖出日": "sell_date"})
    )

    # --- verify ---
    kb, kn, ks = keyset(buy), keyset(native), keyset(sell_last)
    print("===== 1) 资格集校验 =====")
    print(f"buy n={len(buy)} unique={len(kb)}")
    print(f"sell rows={len(sell)} unique={len(ks)}")
    print(f"summary n={len(native)} unique={len(kn)}")
    print(f"keys buy==sell={kb == ks}  buy==nat={kb == kn}")
    if kb != kn or kb != ks:
        print("buy-nat", sorted(kb - kn)[:10], "nat-buy", sorted(kn - kb)[:10])
        print("buy-sell", sorted(kb - ks)[:10], "sell-buy", sorted(ks - kb)[:10])
        raise SystemExit("FAIL: buy/sell/summary keys misaligned")

    # sell date vs summary end_date
    chk = native.merge(sell_last, on=["选股日", "代码"], how="left")
    ed_ok = (chk["sell_date"] == chk["end_date"]).all()
    print(f"sell末笔日==summary.end_date: {ed_ok} (mismatches={int((chk['sell_date'] != chk['end_date']).sum())})")
    if not ed_ok:
        bad = chk[chk["sell_date"] != chk["end_date"]][
            ["选股日", "代码", "end_date", "sell_date"]
        ]
        print(bad.head(20).to_string(index=False))
        raise SystemExit("FAIL: sell dates != summary end_date")

    univ_ret = native["收益率pct"]
    print(
        f"全资格集 ret: mean={univ_ret.mean():+.4f}% median={univ_ret.median():+.4f}% "
        f"winrate={(univ_ret > 0).mean() * 100:.1f}% na={int(univ_ret.isna().sum())}"
    )
    print(
        f"buy 金额 mean={float(buy['金额'].mean()):.2f} "
        f"≈100k/2? {abs(float(buy['金额'].mean()) - 50000) < 2000}"
    )

    # overlap vs Cond123 215
    cond = pd.read_excel(COND_SUM)
    cond["代码"] = cond["代码"].map(code6)
    cond["选股日"] = pd.to_datetime(cond["选股日"]).dt.strftime("%Y-%m-%d")
    cond["收益率pct"] = pd.to_numeric(cond["收益率pct"], errors="coerce")
    cond["end_date"] = pd.to_datetime(cond["end_date"]).dt.strftime("%Y-%m-%d")
    kc = keyset(cond)
    inter = kb & kc
    print("===== 2) vs Cond123 215 =====")
    print(
        f"|新|={len(kb)} |Cond123|={len(kc)} |交|={len(inter)} "
        f"仅新={len(kb - kc)} 仅旧={len(kc - kb)} same_set={kb == kc}"
    )
    cm = native.merge(
        cond[["选股日", "代码", "收益率pct", "end_date"]],
        on=["选股日", "代码"],
        suffixes=("_n", "_c"),
        how="inner",
    )
    same_ret = (cm["收益率pct_n"].round(6) == cm["收益率pct_c"].round(6)).sum()
    same_ed = (cm["end_date_n"] == cm["end_date_c"]).sum()
    print(f"交集 ret identical={same_ret}/{len(cm)}  end_date identical={same_ed}/{len(cm)}")

    # merge native only — fail loudly
    m = buy.merge(native, on=["选股日", "代码"], how="left")
    m["ret_src"] = "native_summary"
    still_miss = m["收益率pct"].isna() | m["end_date"].isna()
    n_miss = int(still_miss.sum())
    print("===== 3) 收益挂接 =====")
    print(f"native hit={len(m) - n_miss} missing={n_miss}")
    if n_miss:
        print(m.loc[still_miss, ["选股日", "代码", "股票名称"]].to_string(index=False))
        raise SystemExit(
            f"FAIL: {n_miss} keys missing native returns/end_date — refuse Cond123 backfill"
        )

    m["池序"] = m.apply(lambda r: order.get((r["选股日"], r["代码"]), 9999), axis=1)
    m["in_pool"] = m["池序"] < 9999
    # 强度：优先 native/cond 汇总行，缺则选股池
    if "合格榜内序位" not in m.columns:
        m["合格榜内序位"] = np.nan
    if "合格榜标签内RS排名" not in m.columns:
        m["合格榜标签内RS排名"] = np.nan
    for i, r in m.iterrows():
        key = (r["选股日"], r["代码"])
        if key not in strength:
            continue
        elig, rs = strength[key]
        if pd.isna(r.get("合格榜内序位")):
            m.at[i, "合格榜内序位"] = elig
        if pd.isna(r.get("合格榜标签内RS排名")):
            m.at[i, "合格榜标签内RS排名"] = rs
    m["强度分"] = m.apply(
        lambda r: clip_strength_sort_key(
            r.get("合格榜内序位"),
            r.get("合格榜标签内RS排名"),
            r.get("代码"),
            elig_weight=EXPORT_ELIG_WEIGHT,
        )[0],
        axis=1,
    )
    print(f"in_pool={int(m['in_pool'].sum())} out={int((~m['in_pool']).sum())}")
    print("分支", m["分支"].value_counts().to_dict())

    df = m[m["in_pool"]].copy()
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in df.groupby("买入日")}

    for d in all_days:
        still = []
        for p in held:
            if p["release_day"] == d:
                cash += p["cost"] + p["pnl"]
            else:
                still.append(p)
        held = still

        if d in by_day:
            for sel_day, g in by_day[d].groupby("选股日", sort=False):
                S = int(smap.get(sel_day, 0))
                if S <= 0:
                    continue
                Seff = clip_n(S, L_, U_)
                opens = g[g["分支"] == "开盘夹档"].copy()
                opens["_sk"] = opens.apply(
                    lambda r: clip_strength_sort_key(
                        r.get("合格榜内序位"),
                        r.get("合格榜标签内RS排名"),
                        r.get("代码"),
                        elig_weight=EXPORT_ELIG_WEIGHT,
                    ),
                    axis=1,
                )
                opens = opens.sort_values(["_sk", "t", "代码"]).drop(columns=["_sk"])
                g2 = opens.head(Seff).reset_index(drop=True)
                for _, r in g2.iterrows():
                    locked_cost = sum(p["cost"] for p in held)
                    equity_now = cash + locked_cost
                    target = equity_now / float(Seff)
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(
                            {
                                "选股日": sel_day,
                                "买入日": d,
                                "代码": r["代码"],
                                "分支": r["分支"],
                                "池序": int(r["池序"]),
                                "强度分": int(r["强度分"]) if pd.notna(r.get("强度分")) else None,
                                "原因": "没钱",
                                "target": target,
                                "cash": cash,
                            }
                        )
                        continue
                    cash -= spend
                    ret = float(r["收益率pct"]) / 100.0
                    pnl = spend * ret
                    held.append(
                        {
                            "release_day": next_td(str(r["end_date"]), 1),
                            "cost": spend,
                            "pnl": pnl,
                        }
                    )
                    fills.append(
                        {
                            "方案": "新规则_无clip资格_native",
                            "选股日": sel_day,
                            "买入日": d,
                            "代码": r["代码"],
                            "股票名称": r.get("股票名称", ""),
                            "分支": r["分支"],
                            "池序": int(r["池序"]),
                            "强度分": int(r["强度分"]) if pd.notna(r.get("强度分")) else None,
                            "S": S,
                            "Seff": Seff,
                            "spend": spend,
                            "ret_pct": float(r["收益率pct"]),
                            "pnl": pnl,
                            "equity_at_buy": equity_now,
                            "cash_after": cash,
                            "end_date": r["end_date"],
                            "ret_src": "native_summary",
                            "buy_price": r.get("价格", np.nan),
                            "合格榜内序位": r.get("合格榜内序位", np.nan),
                            "合格榜标签内RS排名": r.get("合格榜标签内RS排名", np.nan),
                        }
                    )

        locked_cost = sum(p["cost"] for p in held)
        locked_pnl = sum(p["pnl"] for p in held)
        equity = cash + locked_cost + locked_pnl
        eq_curve.append(
            {
                "date": d,
                "cash": cash,
                "locked_cost": locked_cost,
                "equity": equity,
                "n_held": len(held),
            }
        )

    fills_df = pd.DataFrame(fills)
    skips_df = pd.DataFrame(skips)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    ret_pct = (final / CAPITAL0 - 1.0) * 100.0

    rows = []
    for d in sorted(smap):
        if not d.startswith("2026-07"):
            continue
        S = int(smap[d])
        Seff = clip_n(S, L_, U_)
        fd = fills_df[fills_df["选股日"] == d] if len(fills_df) else fills_df
        n = len(fd)
        spend = float(fd["spend"].sum()) if n else 0.0
        pnl = float(fd["pnl"].sum()) if n else 0.0
        rows.append(
            {
                "选股日": d,
                "S": S,
                "Seff": Seff,
                "成交": n,
                "花费": spend,
                "盈亏": pnl,
                "日收益%": (pnl / spend * 100) if spend > 0 else 0.0,
                "均ret%": float(fd["ret_pct"].mean()) if n else float("nan"),
                "胜率%": float((fd["ret_pct"] > 0).mean() * 100) if n else float("nan"),
            }
        )
    day = pd.DataFrame(rows)

    stats = {
        "label": "新规则_无clip资格_native",
        "clip": f"clip({L_},{U_})",
        "L": L_,
        "U": U_,
        "capital0": CAPITAL0,
        "final": final,
        "ret_pct": ret_pct,
        "n_fill": int(len(fills_df)),
        "n_skip": int(len(skips_df)),
        "n_universe": int(len(df)),
        "n_out_of_pool": int((~m["in_pool"]).sum()),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if len(fills_df) else float("nan"),
        "median_ret_pct": float(fills_df["ret_pct"].median()) if len(fills_df) else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100)
        if len(fills_df)
        else float("nan"),
        "sum_pnl": float(fills_df["pnl"].sum()) if len(fills_df) else 0.0,
        "mean_spend": float(fills_df["spend"].mean()) if len(fills_df) else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "trade_days": int(fills_df["选股日"].nunique()) if len(fills_df) else 0,
        "mean_fills_per_sel_day": float(fills_df.groupby("选股日").size().mean())
        if len(fills_df)
        else 0.0,
        "ret_src_counts": fills_df["ret_src"].value_counts().to_dict()
        if len(fills_df)
        else {},
    }

    # refs: file-order backups + current Cond123 scorekey fills
    FILEORDER_NEW_RET = 2.457120189443729
    FILEORDER_BEST_RET = 8.749543784866876
    fo_fills_path = ROOT / "新规则_无clip资格_clip2_4_fileorder_成交明细.csv"
    fo_overlap = {}
    if fo_fills_path.exists():
        ff = pd.read_csv(fo_fills_path, encoding="utf-8-sig")
        ff["代码"] = ff["代码"].map(code6)
        ff["选股日"] = pd.to_datetime(ff["选股日"]).dt.strftime("%Y-%m-%d")
        kf = keyset(fills_df) if len(fills_df) else set()
        kfo = keyset(ff)
        fo_overlap = {
            "n_scorekey": len(kf),
            "n_fileorder": len(kfo),
            "n_inter": len(kf & kfo),
            "only_scorekey": len(kf - kfo),
            "only_fileorder": len(kfo - kf),
        }

    prior_final = prior_ret = None
    prior_n = None
    prior_mean = None
    pick_overlap = {}
    if PRIOR_EQ.exists():
        pe = pd.read_csv(PRIOR_EQ, encoding="utf-8-sig")
        prior_final = float(pe["equity"].iloc[-1])
        prior_ret = (prior_final / CAPITAL0 - 1.0) * 100.0
    if PRIOR_FILLS.exists():
        pf = pd.read_csv(PRIOR_FILLS, encoding="utf-8-sig")
        pf["代码"] = pf["代码"].map(code6)
        pf["选股日"] = pd.to_datetime(pf["选股日"]).dt.strftime("%Y-%m-%d")
        kp = keyset(pf)
        kf = keyset(fills_df) if len(fills_df) else set()
        pick_overlap = {
            "n_this": len(kf),
            "n_prior_scorekey_best": len(kp),
            "n_inter": len(kf & kp),
            "only_this": len(kf - kp),
            "only_prior": len(kp - kf),
            "jaccard": (len(kf & kp) / len(kf | kp)) if (kf | kp) else float("nan"),
        }
        prior_n = len(pf)
        prior_mean = float(pd.to_numeric(pf["ret_pct"], errors="coerce").mean())

    def _write(prefix: str) -> None:
        def _safe(path: Path, writer):
            try:
                writer(path)
                return path
            except PermissionError:
                alt = path.with_name(path.stem + "_scorekey" + path.suffix)
                print(f"[WARN] locked {path.name}, write {alt.name}")
                writer(alt)
                return alt

        _safe(
            ROOT / f"{prefix}_成交明细.csv",
            lambda p: fills_df.to_csv(p, index=False, encoding="utf-8-sig"),
        )
        _safe(
            ROOT / f"{prefix}_权益曲线.csv",
            lambda p: curve.to_csv(p, index=False, encoding="utf-8-sig"),
        )
        _safe(ROOT / f"{prefix}_按日汇总.xlsx", lambda p: day.to_excel(p, index=False))
        skip_path = ROOT / f"{prefix}_跳过.csv"
        if len(skips_df):
            _safe(skip_path, lambda p: skips_df.to_csv(p, index=False, encoding="utf-8-sig"))
        elif skip_path.exists():
            try:
                skip_path.unlink()
            except PermissionError:
                print(f"[WARN] skip file locked: {skip_path.name}")

        meta = {
            "method": "offline_postprocess_clip_equity",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": (
                "买入来自回测成交明细_新规则.csv（215笔，金额≈5万=全仓/2，无 Seff clip）；"
                f"Seff=clip(S,{L_},{U_})，S=MA空头排列选股池当日只数；"
                f"取票=按强度分(Elig×{EXPORT_ELIG_WEIGHT}+标签内RS，无max_per_tag)截断至 Seff"
                "（≠导出行序）；spend=min(equity/Seff,cash)；资金于 end_date 次一交易日回笼。"
                "收益率/end_date：仅用各日选股收益汇总_新规则（原生完整215键）；缺键即失败，不回填 Cond123。"
            ),
            "rank_mode": "strength_score",
            "EXPORT_ELIG_WEIGHT": EXPORT_ELIG_WEIGHT,
            "L": L_,
            "U": U_,
            "capital0": CAPITAL0,
            "date_range": "2026-07-01..2026-07-31",
            "inputs": {
                "buys": BUY.name,
                "n_buys": int(len(buy)),
                "selection": SEL.name,
                "native_summary": NATIVE_SUM.name,
            },
            "stats": stats,
            "compare_refs": {
                "fileorder_newrule_ret_pct": FILEORDER_NEW_RET,
                "fileorder_Cond123_besttest_ret_pct": FILEORDER_BEST_RET,
                "vs_fileorder_newrule_pp": ret_pct - FILEORDER_NEW_RET,
                "vs_fileorder_besttest_pp": ret_pct - FILEORDER_BEST_RET,
                "fileorder_pick_overlap": fo_overlap,
                "scorekey_Cond123_besttest_ret_pct": prior_ret,
                "scorekey_pick_overlap_vs_best": pick_overlap,
            },
            "outputs": {
                "fills": f"{prefix}_成交明细.csv",
                "curve": f"{prefix}_权益曲线.csv",
                "day": f"{prefix}_按日汇总.xlsx",
                "meta": f"{prefix}_meta.json",
            },
        }
        _safe(
            ROOT / f"{prefix}_meta.json",
            lambda p: p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"),
        )

    _write(PREFIX)
    _write(PREFIX_NATIVE)

    cmp_rows = [
        {
            "方案": "新规则_强度分_clip2_4",
            "期末": final,
            "收益%": ret_pct,
            "成交": stats["n_fill"],
            "均ret%": stats["mean_ret_pct"],
            "胜率%": stats["winrate_pct"],
            "最大回撤%": stats["max_dd_pct"],
        },
        {
            "方案": "新规则_fileorder_clip2_4(备份)",
            "期末": None,
            "收益%": FILEORDER_NEW_RET,
            "成交": fo_overlap.get("n_fileorder"),
            "均ret%": None,
            "胜率%": None,
            "最大回撤%": None,
        },
        {
            "方案": "Cond123_besttest_fileorder(历史)",
            "期末": None,
            "收益%": FILEORDER_BEST_RET,
            "成交": 41,
            "均ret%": None,
            "胜率%": None,
            "最大回撤%": None,
        },
        {
            "方案": "Cond123_besttest_强度分(同键)",
            "期末": prior_final,
            "收益%": prior_ret,
            "成交": prior_n,
            "均ret%": prior_mean,
            "胜率%": None,
            "最大回撤%": None,
        },
    ]
    try:
        with pd.ExcelWriter(ROOT / "新规则_native_vs_Cond123_clip2_4_对比.xlsx", engine="openpyxl") as w:
            pd.DataFrame(cmp_rows).to_excel(w, sheet_name="汇总", index=False)
            pd.DataFrame([fo_overlap]).to_excel(w, sheet_name="vs_fileorder重叠", index=False)
            pd.DataFrame([pick_overlap]).to_excel(w, sheet_name="vs_scorekey_best重叠", index=False)
            day.to_excel(w, sheet_name="新规则按日", index=False)
            fills_df.to_excel(w, sheet_name="新规则成交", index=False)
            curve.to_excel(w, sheet_name="新规则权益", index=False)
    except PermissionError:
        alt = ROOT / "新规则_native_vs_Cond123_clip2_4_对比_scorekey.xlsx"
        with pd.ExcelWriter(alt, engine="openpyxl") as w:
            pd.DataFrame(cmp_rows).to_excel(w, sheet_name="汇总", index=False)
            fills_df.to_excel(w, sheet_name="新规则成交", index=False)
        print(f"[WARN] compare xlsx locked, wrote {alt.name}")

    print("===== 4) clip(2,4)·10万 强度分仿真 =====")
    print(
        f"期末={final:.0f} 收益={ret_pct:+.2f}% 成交={stats['n_fill']} 跳过={stats['n_skip']} "
        f"均ret={stats['mean_ret_pct']:+.2f}% 中位={stats['median_ret_pct']:+.2f}% "
        f"胜率={stats['winrate_pct']:.1f}% 最大回撤={stats['max_dd_pct']:.2f}% "
        f"日均笔={stats['mean_fills_per_sel_day']:.2f}"
    )
    print(
        f"vs file-order 新规则: {FILEORDER_NEW_RET:+.2f}% → {ret_pct:+.2f}% "
        f"(Δ={ret_pct - FILEORDER_NEW_RET:+.2f}pp) overlap={fo_overlap}"
    )
    print(
        f"vs file-order Cond123_besttest: {FILEORDER_BEST_RET:+.2f}% → {ret_pct:+.2f}% "
        f"(Δ={ret_pct - FILEORDER_BEST_RET:+.2f}pp)"
    )
    if prior_ret is not None:
        print(
            f"vs 强度分 Cond123_besttest(同215): {prior_ret:+.2f}% → {ret_pct:+.2f}% "
            f"(Δ={ret_pct - prior_ret:+.2f}pp) identical_picks="
            f"{pick_overlap.get('n_inter') == pick_overlap.get('n_this') == pick_overlap.get('n_prior_scorekey_best')}"
        )
    print(f"exported {PREFIX}_* and {PREFIX_NATIVE}_*")


if __name__ == "__main__":
    main()
