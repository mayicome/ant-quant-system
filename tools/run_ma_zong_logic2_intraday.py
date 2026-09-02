# -*- coding: utf-8 -*-
"""马总选股逻辑2 · 盘中一键选股（建议 14:40）。

流程：
  1) 东财 push2 拉全市场当时涨幅 + 主力净流入
  2) 东财拉行业/概念涨幅榜
  3) 硬过滤：主板涨幅>=6%，创/科/北>=10%
  4) 对硬通过票加载日线，算前10日/均线与满足条件
  5) 导出 xls 到 history_data/马总选股逻辑/

用法：
  python tools/run_ma_zong_logic2_intraday.py
  python tools/run_ma_zong_logic2_intraday.py --persist
  python tools/run_ma_zong_logic2_intraday.py --date 2026-08-07   # 用落地 CSV（非实时）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = ROOT / "tools" / "_rule_src_ma_zong_logic2.py"
OUT_DIR = ROOT / "history_data" / "马总选股逻辑"


def _parse_date(s: Optional[str]) -> date:
    if not s:
        return date.today()
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    return date.fromisoformat(s[:10])


def _load_select():
    ns: Dict[str, Any] = {}
    exec(SRC.read_text(encoding="utf-8"), ns, ns)
    return ns["select"], ns


def _persist_snapshot(as_of: date) -> None:
    """把本次实时快照落盘，便于复盘。"""
    from utils.eastmoney_fund_flow import fetch_individual_fund_flow_df
    from utils.main_force_inflow_path import ensure_flow_data_dir, flow_csv_path
    from utils.main_force_inflow_rank import enrich_and_rank_by_inflow_ratio
    from tools.snapshot_eastmoney_board_rank import OUT_DIR as BOARD_DIR, fetch_board_rank_direct

    print("persist: fund flow …")
    df, meta = fetch_individual_fund_flow_df()
    print("  rows", meta)
    if df is not None and not df.empty:
        ranked, _stats = enrich_and_rank_by_inflow_ratio(df, min_inflow_wan=0.0)
        ensure_flow_data_dir(str(ROOT / "history_data"))
        out_p = flow_csv_path(as_of.strftime("%Y%m%d"), str(ROOT / "history_data"))
        ranked.to_csv(out_p, index=False, encoding="utf-8-sig")
        print("  wrote", out_p)
        try:
            from tools.export_main_flow_to_jsonl import write_daily_main_flow_jsonl_shard

            write_daily_main_flow_jsonl_shard(out_p, as_of.strftime("%Y%m%d"))
        except Exception as e:
            print("  main_flow jsonl fail:", e)

    print("persist: board ranks …")
    os.makedirs(BOARD_DIR, exist_ok=True)
    ds = as_of.strftime("%Y-%m-%d")
    for kind, fname in (
        ("industry", "industry_rank_%s.csv" % ds),
        ("concept", "concept_rank_%s.csv" % ds),
    ):
        bdf = fetch_board_rank_direct(kind)
        path = os.path.join(BOARD_DIR, fname)
        bdf.to_csv(path, index=False, encoding="utf-8-sig")
        print("  wrote", path, "n=", len(bdf))


def _write_xls(path: Path, rows: List[Dict[str, Any]]) -> None:
    import pandas as pd

    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    try:
        from sector_stock_filter import save_xls_with_text_code

        save_xls_with_text_code(str(path), df)
        return
    except Exception as e:
        print("save_xls_with_text_code failed:", e)
    alt = path.with_suffix(".xlsx")
    df.to_excel(alt, index=False, engine="openpyxl")
    print("fallback wrote", alt)


def main() -> None:
    ap = argparse.ArgumentParser(description="马总选股逻辑2 盘中选股")
    ap.add_argument("--date", default="", help="选股日 YYYY-MM-DD；默认今天(实时)")
    ap.add_argument("--persist", action="store_true", help="将本次实时快照落盘")
    ap.add_argument("--force-live", action="store_true", help="即使非今天也强制拉东财实时")
    ap.add_argument("--no-em-hot", action="store_true", help="不加载东财热门诊断字段（更快）")
    args = ap.parse_args()

    as_of = _parse_date(args.date or None)
    force_live = True if args.force_live or as_of == date.today() else False
    if args.date and as_of != date.today() and not args.force_live:
        force_live = False

    from utils.ma_zong_intraday_ctx import clear_ma_zong_intraday_cache, load_ma_zong_intraday_bundle
    from utils.daily_cache_reader import load_daily_dataframe
    from utils.eastmoney_board_rank_ctx import _load_stock_info_tag_index

    clear_ma_zong_intraday_cache()
    print("loading intraday bundle …", "as_of=", as_of, "live=", force_live)
    bundle = load_ma_zong_intraday_bundle(
        as_of, force_live=force_live, refresh=True, ttl_sec=1
    )
    quotes = bundle.get("quotes") or {}
    print(
        "mode=",
        bundle.get("mode"),
        "quotes=",
        len(quotes),
        "ind=",
        len(bundle.get("ind_rank") or {}),
        "con=",
        len(bundle.get("con_rank") or {}),
        "fetched_at=",
        bundle.get("fetched_at"),
        "err=",
        bundle.get("error") or "",
    )
    if not quotes:
        raise SystemExit("无行情/资金流数据，中止")

    if args.persist and force_live:
        _persist_snapshot(as_of)

    select, ns = _load_select()
    main_lo = float(ns.get("MAIN_PCT_LO", 6.0))
    growth_lo = float(ns.get("GROWTH_PCT_LO", 10.0))

    def is_growth(c6: str) -> bool:
        return c6.startswith(("300", "301", "688", "689", "8", "4", "920"))

    hard_codes: List[str] = []
    for c6, q in quotes.items():
        pct = q.get("pct")
        if pct is None:
            continue
        thr = growth_lo if is_growth(c6) else main_lo
        if float(pct) >= thr:
            hard_codes.append(c6)
    hard_codes.sort()
    print("hard-pass codes:", len(hard_codes))

    _tag_to, code_tags = _load_stock_info_tag_index()
    em_hot = {}
    if not args.no_em_hot:
        try:
            from utils.eastmoney_board_rank_ctx import load_em_board_hot_map

            em_hot = load_em_board_hot_map(
                as_of, top_n=50, rs_top_k=50, min_members=10, elig_bands=((1, 40),)
            )
            print("em_board_hot pool", len(em_hot.get("today_pool_codes") or []))
        except Exception as e:
            print("em_board_hot skip:", e)
            em_hot = {}

    rows: List[Dict[str, Any]] = []
    meet_n = 0
    for i, c6 in enumerate(hard_codes, 1):
        if i % 50 == 0 or i == 1:
            print("  evaluate %d/%d …" % (i, len(hard_codes)))
        q = quotes.get(c6) or {}
        name = str(q.get("name") or "")
        tags = list(code_tags.get(c6) or [])
        df = load_daily_dataframe(
            c6, through_date=as_of, allow_xtdata_fallback=False, allow_on_demand=False
        )
        if df is None or getattr(df, "empty", True):
            # 仍输出硬条件命中，均线/前10日条件标 False
            df = __import__("pandas").DataFrame(columns=["date", "close"])
        ok, extra = select(
            c6,
            name,
            tags,
            df,
            as_of,
            {"em_board_hot": em_hot, "inflow_rank": {}},
        )
        if not ok:
            continue
        if extra.get("满足条件"):
            meet_n += 1
        row = {
            "股票代码": c6,
            "股票名称": name or extra.get("股票名称_行情") or "",
            "所属板块": ";".join(tags[:12]),
            "选股日": as_of.isoformat(),
        }
        row.update(extra)
        rows.append(row)

    print("selected", len(rows), "meet", meet_n)
    if rows:
        from utils.ma_zong_logic2_scan import (
            format_qualifying_board_line,
            format_board_rank_line,
            summarize_qualifying_boards,
        )

        meet_rows = [r for r in rows if r.get("满足条件")]
        hard_like = [
            {
                "extra": r,
                "qualifying_board_text": format_qualifying_board_line(r),
            }
            for r in rows
        ]
        qb = summarize_qualifying_boards(hard_like)
        print("硬门槛通过票中，满足排名门槛的板块（行业前32/概念前8）：")
        if qb:
            ind = [x for x in qb if x[0] == "行业"]
            con = [x for x in qb if x[0] == "概念"]
            if ind:
                print("  行业（%d）：" % len(ind))
                for _kind, name, rk, cnt in ind:
                    print("    #%d %s（硬过票 %d 只）" % (rk, name, cnt))
            if con:
                print("  概念（%d）：" % len(con))
                for _kind, name, rk, cnt in con:
                    print("    #%d %s（硬过票 %d 只）" % (rk, name, cnt))
        else:
            print("  （无）")
        print(
            "硬门槛通过（共 %d 只，其中满足条件 %d 只）：" % (len(rows), len(meet_rows))
        )
        for r in sorted(rows, key=lambda x: str(x.get("股票代码") or "")):
            tag = "满足" if r.get("满足条件") else "未全满足"
            qual = format_qualifying_board_line(r)
            board = format_board_rank_line(r)
            print(
                "  %s %s %s [%s] 涨幅=%s%% 净流入=%s万"
                % (
                    "✓" if r.get("满足条件") else "·",
                    r.get("股票代码"),
                    r.get("股票名称") or "",
                    tag,
                    r.get("当时涨跌幅"),
                    r.get("主力净流入_万元"),
                )
            )
            if qual:
                print("    满足板块: %s" % qual)
            elif board:
                print("    满足板块: （无，所属最高 %s）" % board)
            else:
                print("    满足板块: （无）")
            if board and qual:
                print("    所属最高: %s" % board)
            reason = str(r.get("不满足的原因") or "").strip()
            if reason:
                print("    不满足: %s" % reason)
            cond_bits = []
            for k, label in (
                ("条件_行业或概念排名达标", "板块排名"),
                ("条件_主力净流入>=3500万", "主力净流入"),
                ("条件_前10日无大涨", "前10日无大涨"),
                ("条件_价站上MA5且MA20", "价>MA5且MA20"),
                ("条件_当天未涨停过", "当天未涨停"),
            ):
                v = r.get(k)
                cond_bits.append("%s:%s" % (label, "是" if v else "否"))
            if cond_bits:
                print("    软门槛: %s" % " | ".join(cond_bits))
    stamp = datetime.now().strftime("%H%M%S")
    out = OUT_DIR / ("选股结果_马总选股逻辑2_%s_盘中%s.xls" % (as_of.isoformat(), stamp))
    _write_xls(out, rows)
    print("wrote", out)

    # 简要汇总
    summary = {
        "as_of": as_of.isoformat(),
        "mode": bundle.get("mode"),
        "fetched_at": bundle.get("fetched_at"),
        "hard_pass": len(hard_codes),
        "exported": len(rows),
        "meet": meet_n,
        "out": str(out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
