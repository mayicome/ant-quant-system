# -*- coding: utf-8 -*-
"""从涨停日数据回填东财板块榜单 CSV（LU-proxy，非真实东财涨跌幅）。

.. deprecated::
    **已过时 / OBSOLETE。** LU-proxy（按涨停家数排名）与东财涨跌幅榜含义不同，
    请改用 ``tools/backfill_em_board_rank_from_hist.py``：从东财板块日 K
    按当日涨跌幅重建 EOD 全榜。本脚本仅保留作对照，请勿再写入生产榜单目录。

用途
----
选股规则「东财热门-连续2日Top50-组内RS前20」依赖::

    data/eastmoney_board_rank/concept_rank_YYYY-MM-DD.csv
    data/eastmoney_board_rank/industry_rank_YYYY-MM-DD.csv

东财 push2 接口只能拿到「当前交易日」点位，无法历史回填真实涨跌幅榜。
本脚本用 ``history_data/涨停日数据/YYYY-MM-DD.json`` 中的统计字段做代理榜：

  - ``concept_stats``        → ``concept_rank_*.csv``
  - ``sector_plate_stats``   → ``industry_rank_*.csv``（板块）

排名规则（与 LU 日统计一致）
----------------------------
按「涨停家数」(``count``) **降序** 排名；并列时保持 JSON 原有顺序（稳定排序）。

列约定（与 ``tools/snapshot_eastmoney_board_rank.py`` / akshare name_em 对齐）::

    排名, 板块名称, 板块代码, 最新价, 涨跌额, 涨跌幅,
    总市值, 换手率, 上涨家数, 下跌家数, 领涨股票, 领涨股票-涨跌幅

代理字段说明（**不是**真实东财涨跌幅榜）:

  - ``上涨家数`` ← 涨停家数 ``count``
  - ``涨跌幅``   ← 合成值，等于 ``count``（便于 ctx 按涨跌幅降序回退排序；
                   数值含义是涨停家数，不是真实板块涨跌幅）
  - ``板块代码`` ← 空（涨停日数据无东财 BK 代码）
  - 其余数值列填空；``领涨股票`` 取该题材涨停股列表首只名称（若有）

``utils/eastmoney_board_rank_ctx._load_rank_map`` 优先读「排名」列，
因此只要「排名」「板块名称」正确即可被选股 ctx 正常加载。

默认只写 ``2026-06-09`` .. ``2026-07-31``；该区间外已有真实 EM 快照
（如 ``2026-08-03``）不会被本脚本触及。区间内缺 JSON / 空 stats 则跳过并报告。

用法::

  python tools/backfill_em_board_rank_from_limit_up.py
  python tools/backfill_em_board_rank_from_limit_up.py --start 2026-06-09 --end 2026-07-31
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LU_DIR = os.path.join(ROOT, "history_data", "涨停日数据")
OUT_DIR = os.path.join(ROOT, "data", "eastmoney_board_rank")

# 与 snapshot_eastmoney_board_rank._BOARD_OUT_COLS 一致
_BOARD_OUT_COLS = [
    "排名",
    "板块名称",
    "板块代码",
    "最新价",
    "涨跌额",
    "涨跌幅",
    "总市值",
    "换手率",
    "上涨家数",
    "下跌家数",
    "领涨股票",
    "领涨股票-涨跌幅",
]

DEFAULT_START = "2026-06-09"
DEFAULT_END = "2026-07-31"


def _parse_ymd(raw: str) -> date:
    s = str(raw or "").strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return datetime.strptime(s, "%Y-%m-%d").date()


def _daterange(start: date, end: date) -> List[date]:
    out: List[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _leader_name(stocks: Any) -> str:
    """从 '000001 平安银行' / '000001' 列表取首只名称。"""
    if not isinstance(stocks, list) or not stocks:
        return ""
    first = str(stocks[0] or "").strip()
    if not first:
        return ""
    parts = first.split(None, 1)
    if len(parts) >= 2:
        return parts[1].strip()
    return first


def stats_to_rank_df(stats: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    """涨停统计列表 → 东财榜单列格式；按 count 降序排名。"""
    rows: List[Dict[str, Any]] = []
    # 先按 count 降序；同 count 保持原序（稳定）
    indexed = list(enumerate(stats))
    indexed.sort(key=lambda iv: (-int(iv[1].get("count") or 0), iv[0]))
    for rank, (_i, item) in enumerate(indexed, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            cnt = int(item.get("count") or 0)
        except (TypeError, ValueError):
            cnt = 0
        if cnt <= 0:
            continue
        rows.append(
            {
                "排名": rank,
                "板块名称": name,
                "板块代码": "",
                "最新价": "",
                "涨跌额": "",
                "涨跌幅": float(cnt),  # LU-proxy：合成涨跌幅=涨停家数
                "总市值": "",
                "换手率": "",
                "上涨家数": cnt,  # 涨停家数落在此列
                "下跌家数": "",
                "领涨股票": _leader_name(item.get("stocks")),
                "领涨股票-涨跌幅": "",
            }
        )
    if not rows:
        return pd.DataFrame(columns=_BOARD_OUT_COLS)
    df = pd.DataFrame(rows)
    return df[_BOARD_OUT_COLS]


def _save_csv(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def _load_lu_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 读取失败 {path}: {e}")
        return None
    return data if isinstance(data, dict) else None


def backfill_range(
    start: date,
    end: date,
    *,
    lu_dir: str = LU_DIR,
    out_dir: str = OUT_DIR,
    sample_date: Optional[str] = None,
) -> Dict[str, Any]:
    """回填 [start, end]；返回 written/skipped 汇总。"""
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    skipped_missing: List[str] = []
    skipped_empty: List[str] = []
    skipped_bad: List[str] = []

    for d in _daterange(start, end):
        ds = d.strftime("%Y-%m-%d")
        src = os.path.join(lu_dir, f"{ds}.json")
        if not os.path.isfile(src):
            skipped_missing.append(ds)
            continue
        data = _load_lu_json(src)
        if data is None:
            skipped_bad.append(ds)
            continue

        concept_stats = data.get("concept_stats") or []
        sector_stats = data.get("sector_plate_stats") or []
        if not isinstance(concept_stats, list):
            concept_stats = []
        if not isinstance(sector_stats, list):
            sector_stats = []

        concept_df = stats_to_rank_df(concept_stats)
        industry_df = stats_to_rank_df(sector_stats)
        if concept_df.empty or industry_df.empty:
            skipped_empty.append(ds)
            print(
                f"[skip] {ds}: empty stats "
                f"(concept={len(concept_df)}, industry={len(industry_df)})"
            )
            continue

        concept_path = os.path.join(out_dir, f"concept_rank_{ds}.csv")
        industry_path = os.path.join(out_dir, f"industry_rank_{ds}.csv")
        _save_csv(concept_df, concept_path)
        _save_csv(industry_df, industry_path)
        written.append(ds)
        print(
            f"[ok] {ds}: concept={len(concept_df)} industry={len(industry_df)} "
            f"-> {os.path.basename(concept_path)}, {os.path.basename(industry_path)}"
        )

    report = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "written": written,
        "skipped_missing": skipped_missing,
        "skipped_empty": skipped_empty,
        "skipped_bad": skipped_bad,
        "out_dir": out_dir,
    }

    # 样例 top5
    pick = sample_date
    if not pick and written:
        pick = written[-1]
    if pick and pick in written:
        cpath = os.path.join(out_dir, f"concept_rank_{pick}.csv")
        ipath = os.path.join(out_dir, f"industry_rank_{pick}.csv")
        report["sample_date"] = pick
        report["sample_concept_top5"] = _top5(cpath)
        report["sample_industry_top5"] = _top5(ipath)

    return report


def _top5(path: str) -> List[Tuple[Any, ...]]:
    if not os.path.isfile(path):
        return []
    df = pd.read_csv(path, encoding="utf-8-sig")
    cols = [c for c in ("排名", "板块名称", "上涨家数", "涨跌幅") if c in df.columns]
    head = df[cols].head(5)
    return [tuple(row) for row in head.itertuples(index=False, name=None)]


def _print_report(report: Dict[str, Any]) -> None:
    written = report.get("written") or []
    miss = report.get("skipped_missing") or []
    empty = report.get("skipped_empty") or []
    bad = report.get("skipped_bad") or []
    print()
    print("=" * 60)
    print(
        f"[done] range={report['start']}..{report['end']} "
        f"written={len(written)} missing={len(miss)} empty={len(empty)} bad={len(bad)}"
    )
    if miss:
        print(f"  skipped_missing ({len(miss)}): {', '.join(miss)}")
    if empty:
        print(f"  skipped_empty ({len(empty)}): {', '.join(empty)}")
    if bad:
        print(f"  skipped_bad ({len(bad)}): {', '.join(bad)}")
    sample = report.get("sample_date")
    if sample:
        print(f"  sample_date={sample}")
        print(f"  concept top5 (排名,名称,上涨家数=涨停数,涨跌幅=proxy):")
        for row in report.get("sample_concept_top5") or []:
            print(f"    {row}")
        print(f"  industry top5 (排名,名称,上涨家数=涨停数,涨跌幅=proxy):")
        for row in report.get("sample_industry_top5") or []:
            print(f"    {row}")
    # 确认区间外真实 EM 未被触及
    em_aug = os.path.join(report["out_dir"], "concept_rank_2026-08-03.csv")
    if os.path.isfile(em_aug):
        print(f"  note: 保留真实 EM 快照 concept_rank_2026-08-03.csv（未改写）")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="用涨停日数据回填东财板块榜 CSV（LU-proxy，非真实涨跌幅）",
    )
    parser.add_argument("--start", default=DEFAULT_START, help="起始日 YYYY-MM-DD（含）")
    parser.add_argument("--end", default=DEFAULT_END, help="结束日 YYYY-MM-DD（含）")
    parser.add_argument(
        "--lu-dir",
        default=LU_DIR,
        help="涨停日数据目录（默认 history_data/涨停日数据）",
    )
    parser.add_argument(
        "--out-dir",
        default=OUT_DIR,
        help="输出目录（默认 data/eastmoney_board_rank）",
    )
    parser.add_argument(
        "--sample-date",
        default="",
        help="报告用样例日（默认取最后一个成功写出的日期）",
    )
    args = parser.parse_args()
    start = _parse_ymd(args.start)
    end = _parse_ymd(args.end)
    if end < start:
        raise SystemExit(f"end < start: {args.end} < {args.start}")

    print(f"[info] lu_dir={args.lu_dir}")
    print(f"[info] out_dir={args.out_dir}")
    print(f"[info] range={start.isoformat()} .. {end.isoformat()} (inclusive)")
    print(
        "[info] LU-proxy ranks: sort by 涨停家数 desc; "
        "上涨家数=count, 涨跌幅=count (synthetic, NOT EM 涨跌幅)"
    )

    report = backfill_range(
        start,
        end,
        lu_dir=args.lu_dir,
        out_dir=args.out_dir,
        sample_date=(args.sample_date or None),
    )
    _print_report(report)


if __name__ == "__main__":
    main()
