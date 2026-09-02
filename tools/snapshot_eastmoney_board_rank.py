# -*- coding: utf-8 -*-
"""东财板块涨跌幅全榜日快照（概念 + 行业，非 Top10）。

数据与 akshare 同源（东财 push2 clist）：
  - stock_board_concept_name_em   → fs=m:90 t:3  概念板块全榜
  - stock_board_industry_name_em  → fs=m:90 t:2  行业板块全榜

实现上优先调用 akshare；失败时用本脚本多 host / 去代理直连兜底（列与 akshare 一致）。
直连 host 优先 ``push2delay``（主站 push2 在部分环境会被掐断）。

可选（--with-fund-flow）：
  - stock_sector_fund_flow_rank 同源：概念/行业资金流排行

重要说明：
  - 接口返回的是「当前/最新交易日」排行点位，不能按历史日期回填真实历史榜单。
  - --date 仅用于输出文件名日期戳（默认今天 YYYY-MM-DD），与接口数据无关。
  - 同日重复运行会覆盖同名文件。

输出目录：data/eastmoney_board_rank/
  concept_rank_YYYY-MM-DD.csv
  industry_rank_YYYY-MM-DD.csv
  （--with-fund-flow 时额外）
  concept_fund_flow_YYYY-MM-DD.csv
  industry_fund_flow_YYYY-MM-DD.csv

CSV：UTF-8-BOM，便于 Windows Excel 直接打开。

用法：
  python tools/snapshot_eastmoney_board_rank.py
  python tools/snapshot_eastmoney_board_rank.py --date 2026-08-03
  python tools/snapshot_eastmoney_board_rank.py --with-fund-flow
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "data", "eastmoney_board_rank")

_UT_QUOTE = "bd1d9ddb04089700cf9c27f6f7426281"
_UT_FUND = "b2884a393a59ad64002292a3e90d46a5"

# 本机常见：主站 push2 / 数字子域会被 RST；push2delay 仍可用，放最前。
_HOSTS = (
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://79.push2.eastmoney.com/api/qt/clist/get",
    "https://17.push2.eastmoney.com/api/qt/clist/get",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/center/boardlist.html",
    "Accept": "*/*",
}

# 与 akshare name_em 最终保留列一致
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

_BOARD_FIELDS = (
    "f2,f3,f4,f8,f12,f14,f15,f16,f17,f18,f20,f21,f24,f25,f22,f33,f11,f62,"
    "f128,f124,f107,f104,f105,f136"
)


def _clear_proxy_env() -> None:
    """与 build_stock_info_from_em_boards 一致：清代理，避免请求被系统代理干扰。"""
    for k in list(os.environ.keys()):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")


def _parse_date_stamp(raw: Optional[str]) -> str:
    """仅作文件名日期戳；不用于回填历史数据。"""
    if not raw or not str(raw).strip():
        return datetime.now().strftime("%Y-%m-%d")
    s = str(raw).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as e:
        raise SystemExit(f"--date 格式无效，请用 YYYY-MM-DD 或 YYYYMMDD: {raw!r}") from e


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # 避开系统坏代理
    return s


def _get_json(
    session: requests.Session,
    params: Dict[str, Any],
    *,
    hosts: Sequence[str] = _HOSTS,
    retries: int = 6,
    timeout: int = 30,
) -> dict:
    last_err: Optional[BaseException] = None
    host_list = list(hosts) or list(_HOSTS)
    # 第一轮：每个 host 快速试一次（主站常秒断，勿长时间卡在坏 host）
    quick_timeout = min(12, int(timeout))
    for host in host_list:
        try:
            r = session.get(host, params=params, headers=_HEADERS, timeout=quick_timeout)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("data") is not None:
                return data
            last_err = RuntimeError(f"empty payload from {host}")
        except Exception as e:
            last_err = e
    # 第二轮：按 retries 轮询，略退避
    for attempt in range(max(1, retries)):
        host = host_list[attempt % len(host_list)]
        try:
            r = session.get(host, params=params, headers=_HEADERS, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("data") is not None:
                return data
            last_err = RuntimeError(f"empty payload from {host}")
        except Exception as e:
            last_err = e
            time.sleep(0.35 * (attempt + 1) + random.random() * 0.2)
    raise RuntimeError(f"东财 clist 请求失败: {last_err}")


def _extend_diff(rows: List[dict], diff_obj: Any) -> None:
    if not diff_obj:
        return
    if isinstance(diff_obj, dict):
        rows.extend(list(diff_obj.values()))
    else:
        rows.extend(list(diff_obj))


def _fetch_clist_all(
    session: requests.Session,
    base_params: Dict[str, Any],
    *,
    page_size: int = 100,
    pause_s: float = 0.15,
) -> List[dict]:
    params = dict(base_params)
    params["pn"] = "1"
    params["pz"] = str(max(20, min(int(page_size), 100)))
    first = _get_json(session, params)
    data = first.get("data") or {}
    total = int(data.get("total") or 0)
    rows: List[dict] = []
    _extend_diff(rows, data.get("diff"))
    if total <= 0:
        return rows
    pz = int(params["pz"])
    total_pages = max(1, math.ceil(total / pz))
    for pn in range(2, total_pages + 1):
        params = dict(params)
        params["pn"] = str(pn)
        payload = _get_json(session, params)
        _extend_diff(rows, (payload.get("data") or {}).get("diff"))
        if pause_s > 0:
            time.sleep(pause_s + random.random() * 0.1)
    return rows


def _rows_to_board_rank_df(rows: List[dict]) -> pd.DataFrame:
    """按涨跌幅降序排名，列与 akshare name_em 一致。"""
    if not rows:
        raise RuntimeError("板块列表为空")
    raw = pd.DataFrame(rows)
    df = pd.DataFrame(
        {
            "板块名称": raw.get("f14"),
            "板块代码": raw.get("f12"),
            "最新价": pd.to_numeric(raw.get("f2"), errors="coerce"),
            "涨跌额": pd.to_numeric(raw.get("f4"), errors="coerce"),
            "涨跌幅": pd.to_numeric(raw.get("f3"), errors="coerce"),
            "总市值": pd.to_numeric(raw.get("f20"), errors="coerce"),
            "换手率": pd.to_numeric(raw.get("f8"), errors="coerce"),
            "上涨家数": pd.to_numeric(raw.get("f104"), errors="coerce"),
            "下跌家数": pd.to_numeric(raw.get("f105"), errors="coerce"),
            "领涨股票": raw.get("f128"),
            "领涨股票-涨跌幅": pd.to_numeric(raw.get("f136"), errors="coerce"),
        }
    )
    df = df.dropna(subset=["板块名称"]).copy()
    df = df.drop_duplicates(subset=["板块代码"], keep="first")
    df = df.sort_values("涨跌幅", ascending=False, kind="mergesort").reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))
    return df[_BOARD_OUT_COLS]


def fetch_board_rank_direct(kind: str) -> pd.DataFrame:
    """
    直连东财 push2，等价于 akshare stock_board_{concept|industry}_name_em。
    kind: concept | industry
    """
    if kind == "concept":
        fs = "m:90 t:3 f:!50"
    elif kind == "industry":
        fs = "m:90 t:2 f:!50"
    else:
        raise ValueError(f"unknown kind: {kind}")
    session = _session()
    params = {
        "po": "1",
        "np": "1",
        "ut": _UT_QUOTE,
        "fltt": "2",
        "invt": "2",
        "fid": "f3",  # 涨跌幅
        "fs": fs,
        "fields": _BOARD_FIELDS,
    }
    rows = _fetch_clist_all(session, params)
    return _rows_to_board_rank_df(rows)


def fetch_fund_flow_direct(sector_type: str) -> pd.DataFrame:
    """
    直连东财板块资金流，等价于 akshare stock_sector_fund_flow_rank(今日, ...)。
    sector_type: 概念资金流 | 行业资金流
    """
    type_map = {"行业资金流": "2", "概念资金流": "3", "地域资金流": "1"}
    if sector_type not in type_map:
        raise ValueError(f"unknown sector_type: {sector_type}")
    session = _session()
    params = {
        "po": "1",
        "np": "1",
        "ut": _UT_FUND,
        "fltt": "2",
        "invt": "2",
        "fid0": "f62",
        "fs": f"m:90 t:{type_map[sector_type]}",
        "stat": "1",
        "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124",
        "rt": "52975239",
    }
    rows = _fetch_clist_all(session, params)
    if not rows:
        raise RuntimeError(f"{sector_type} 为空")
    raw = pd.DataFrame(rows)
    df = pd.DataFrame(
        {
            "名称": raw.get("f14"),
            "今日涨跌幅": pd.to_numeric(raw.get("f3"), errors="coerce"),
            "今日主力净流入-净额": pd.to_numeric(raw.get("f62"), errors="coerce"),
            "今日主力净流入-净占比": pd.to_numeric(raw.get("f184"), errors="coerce"),
            "今日超大单净流入-净额": pd.to_numeric(raw.get("f66"), errors="coerce"),
            "今日超大单净流入-净占比": pd.to_numeric(raw.get("f69"), errors="coerce"),
            "今日大单净流入-净额": pd.to_numeric(raw.get("f72"), errors="coerce"),
            "今日大单净流入-净占比": pd.to_numeric(raw.get("f75"), errors="coerce"),
            "今日中单净流入-净额": pd.to_numeric(raw.get("f78"), errors="coerce"),
            "今日中单净流入-净占比": pd.to_numeric(raw.get("f81"), errors="coerce"),
            "今日小单净流入-净额": pd.to_numeric(raw.get("f84"), errors="coerce"),
            "今日小单净流入-净占比": pd.to_numeric(raw.get("f87"), errors="coerce"),
            "今日主力净流入最大股": raw.get("f204"),
        }
    )
    df = df.dropna(subset=["名称"]).copy()
    df = df.sort_values("今日主力净流入-净额", ascending=False, kind="mergesort").reset_index(drop=True)
    df.insert(0, "序号", range(1, len(df) + 1))
    return df


def _try_akshare(fn: Callable[[], pd.DataFrame], label: str) -> Optional[pd.DataFrame]:
    try:
        df = fn()
        if isinstance(df, pd.DataFrame) and not df.empty:
            print(f"[info] {label}: via akshare, rows={len(df)}")
            return df.copy()
        print(f"[warn] {label}: akshare 返回空，改用直连")
    except Exception as e:
        print(f"[warn] {label}: akshare 失败 ({type(e).__name__}: {e})，改用直连")
    return None


def _save_csv(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def _name_col(df: pd.DataFrame) -> Optional[str]:
    for c in ("板块名称", "名称", "板块", "行业", "概念"):
        if c in df.columns:
            return c
    return None


def _print_summary(label: str, df: pd.DataFrame, path: str, top_n: int = 5) -> None:
    print(f"[ok] {label}: rows={len(df)} cols={len(df.columns)} -> {path}")
    print(f"     columns: {list(df.columns)}")
    name_c = _name_col(df)
    if name_c is None:
        print(df.head(top_n).to_string(index=False))
        return
    show_cols = [name_c]
    for c in (
        "排名",
        "序号",
        "涨跌幅",
        "今日涨跌幅",
        "换手率",
        "最新价",
        "今日主力净流入-净额",
    ):
        if c in df.columns and c not in show_cols:
            show_cols.append(c)
    head = df[show_cols].head(top_n)
    names = ", ".join(str(x) for x in head[name_c].tolist())
    print(f"     top{top_n} names: {names}")
    print(head.to_string(index=False))


def fetch_and_save(
    date_stamp: str,
    *,
    with_fund_flow: bool = False,
    prefer_akshare: bool = True,
) -> None:
    _clear_proxy_env()
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"[info] out_dir={OUT_DIR}")
    print(f"[info] date_stamp={date_stamp}（仅文件名；数据为接口当前最新点位，不可历史回填）")
    print(f"[info] fetched_at={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    ak = None
    if prefer_akshare:
        try:
            import akshare as _ak

            ak = _ak
        except Exception as e:
            print(f"[warn] akshare 不可用: {e}，全程直连")

    # ---- 概念涨跌幅全榜 ----
    print("[info] fetching concept board rank...")
    concept_df = None
    if ak is not None:
        concept_df = _try_akshare(ak.stock_board_concept_name_em, "concept_name_em")
    if concept_df is None:
        concept_df = fetch_board_rank_direct("concept")
        print(f"[info] concept_name_em: via direct push2, rows={len(concept_df)}")
    concept_path = os.path.join(OUT_DIR, f"concept_rank_{date_stamp}.csv")
    _save_csv(concept_df, concept_path)
    _print_summary("concept_rank", concept_df, concept_path)

    # ---- 行业涨跌幅全榜 ----
    print("[info] fetching industry board rank...")
    industry_df = None
    if ak is not None:
        industry_df = _try_akshare(ak.stock_board_industry_name_em, "industry_name_em")
    if industry_df is None:
        industry_df = fetch_board_rank_direct("industry")
        print(f"[info] industry_name_em: via direct push2, rows={len(industry_df)}")
    industry_path = os.path.join(OUT_DIR, f"industry_rank_{date_stamp}.csv")
    _save_csv(industry_df, industry_path)
    _print_summary("industry_rank", industry_df, industry_path)

    if with_fund_flow:
        print("[info] fetching concept fund-flow rank...")
        concept_ff = None
        if ak is not None:
            concept_ff = _try_akshare(
                lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="概念资金流"),
                "concept_fund_flow",
            )
        if concept_ff is None:
            concept_ff = fetch_fund_flow_direct("概念资金流")
            print(f"[info] concept_fund_flow: via direct push2, rows={len(concept_ff)}")
        concept_ff_path = os.path.join(OUT_DIR, f"concept_fund_flow_{date_stamp}.csv")
        _save_csv(concept_ff, concept_ff_path)
        _print_summary("concept_fund_flow", concept_ff, concept_ff_path)

        print("[info] fetching industry fund-flow rank...")
        industry_ff = None
        if ak is not None:
            industry_ff = _try_akshare(
                lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"),
                "industry_fund_flow",
            )
        if industry_ff is None:
            industry_ff = fetch_fund_flow_direct("行业资金流")
            print(f"[info] industry_fund_flow: via direct push2, rows={len(industry_ff)}")
        industry_ff_path = os.path.join(OUT_DIR, f"industry_fund_flow_{date_stamp}.csv")
        _save_csv(industry_ff, industry_ff_path)
        _print_summary("industry_fund_flow", industry_ff, industry_ff_path)

    try:
        from tools.export_board_rank_to_jsonl import write_daily_board_rank_jsonl

        write_daily_board_rank_jsonl(date_stamp)
    except Exception as e:
        print("[board_rank jsonl] export failed:", e)

    print("[done] snapshot complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="东财概念/行业板块全榜日快照（点位数据，非历史回填）",
    )
    parser.add_argument(
        "--date",
        default="",
        help="输出文件名日期戳 YYYY-MM-DD 或 YYYYMMDD（默认今天）；不改变接口数据内容",
    )
    parser.add_argument(
        "--with-fund-flow",
        action="store_true",
        help="额外保存东财板块资金流排行（概念+行业）",
    )
    parser.add_argument(
        "--direct-only",
        action="store_true",
        help="跳过 akshare，直接走东财 push2（调试用）",
    )
    args = parser.parse_args()
    date_stamp = _parse_date_stamp(args.date)
    fetch_and_save(
        date_stamp,
        with_fund_flow=bool(args.with_fund_flow),
        prefer_akshare=not bool(args.direct_only),
    )


if __name__ == "__main__":
    main()
