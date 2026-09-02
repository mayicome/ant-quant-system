# -*- coding: utf-8 -*-
"""从涨停日 JSON 导出按涨停家数排名的行业/概念 CSV（东财板块口径）。

输出（写入 data/eastmoney_board_rank/，与真实东财涨跌幅榜文件名区分）::

    industry_lu_rank_YYYY-MM-DD.csv
    concept_lu_rank_YYYY-MM-DD.csv

口径
----
按东财「行业/概念板块」成分统计涨停家数（与软件「板块监测」一致），
而不是按个股 F10「所属行业」叶子字段：

- 行业：涨停股的 industry/plates（并集 all_a_stock_info）∩
  ``industry_rank_*.csv`` 板块名称（无当日则用最近可用日）
- 概念：涨停股的 concepts/plates ∩ ``concept_rank_*.csv`` 板块名称
- 名称归一：去尾缀「概念」；``装修装饰`` 可匹配 ``装修装饰Ⅱ/Ⅲ`` 等
- 排除：本地地域/通道类标签 + ``utils.em_board_exclude``（昨日涨停、大小盘、重仓、指数篮子等）

列约定与 ``tools/snapshot_eastmoney_board_rank.py`` 对齐；代理语义：
  - ``上涨家数`` ← 涨停家数
  - ``涨跌幅``   ← 同数值（便于按幅排序回退）
  - ``板块代码`` ← 来自对应 rank CSV（若有）

用法::

  python tools/export_limit_up_board_rank.py
  python tools/export_limit_up_board_rank.py --date 2026-08-25 --force
  python tools/export_limit_up_board_rank.py --start 2026-06-01 --end 2026-08-25 --force
  python tools/export_limit_up_board_rank.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.em_board_exclude import is_excluded_em_board  # noqa: E402
from utils.limit_up_day_path import (  # noqa: E402
    list_limit_up_day_json_files,
    resolve_limit_up_day_json_path,
)

HISTORY_DIR = os.path.join(ROOT, "history_data")
STOCK_INFO_PATH = os.path.join(ROOT, "data", "all_a_stock_info.json")
OUT_DIR = os.path.join(ROOT, "data", "eastmoney_board_rank")

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

_EXCLUDED_CONCEPTS: Set[str] = {"央国企改革", "融资融券"}
_EXCLUDED_SECTORS: Set[str] = {
    "央国企改革",
    "融资融券",
    "深股通",
    "沪股通",
    "机构重仓",
    "QFII重仓",
    "专精特新",
    "标准普尔",
    "富时罗素",
    "创业板综",
    "中证",
    "上证",
    "MSCI中国",
    "转债标的",
    "低价股",
    "AH股",
    "光学光电",
    "广东板块",
    "深圳特区",
    "北京板块",
    "上海板块",
    "山东板块",
    "四川板块",
    "福建板块",
    "湖北板块",
    "安徽板块",
    "河南板块",
    "湖南板块",
    "小盘股",
    "江苏板块",
    "最近多板",
    "长江三角",
    "深成",
    "一带一路",
    "西部大开发",
    "浙江板块",
    "中字头",
    "创投",
}
_TIER_PLACEHOLDERS: Set[str] = {"一级", "二级", "三级"}

# 东财行业名常见尾缀：装修装饰Ⅱ / 装修装饰II
_ROMAN_TAIL_RE = re.compile(
    r"(Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|I{1,3}|IV|V)$",
    re.IGNORECASE,
)
_RANK_FILE_RE = {
    "industry": re.compile(r"^industry_rank_(\d{4}-\d{2}-\d{2})\.csv$"),
    "concept": re.compile(r"^concept_rank_(\d{4}-\d{2}-\d{2})\.csv$"),
}

_stock_info_cache: Optional[Dict[str, Any]] = None
_board_universe_cache: Dict[Tuple[str, str], Tuple[Dict[str, str], str]] = {}


def _parse_ymd(raw: str) -> date:
    s = str(raw or "").strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _aggregate_tag_name(raw_name: str) -> str:
    name = (raw_name or "").strip()
    if not name or name == "nan":
        return ""
    if name.endswith("概念"):
        base = name[:-2].strip()
        return base if base else ""
    return name


def _strip_roman_tail(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    return _ROMAN_TAIL_RE.sub("", s).strip()


def _is_excluded_tag(name: str) -> bool:
    """本地排除集 + 东财风格/状态板过滤（utils.em_board_exclude）。"""
    n = str(name or "").strip()
    if not n:
        return True
    if n in _EXCLUDED_SECTORS or n in _EXCLUDED_CONCEPTS or n in _TIER_PLACEHOLDERS:
        return True
    return is_excluded_em_board(n)


def _leader_name(stocks: Any) -> str:
    if not isinstance(stocks, list) or not stocks:
        return ""
    first = str(stocks[0] or "").strip()
    if not first:
        return ""
    parts = first.split(None, 1)
    if len(parts) >= 2:
        return parts[1].strip()
    return first


def stats_to_rank_df(
    stats: Sequence[Dict[str, Any]],
    *,
    board_codes: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """涨停统计列表 → 东财榜单列格式；按 count 降序排名。"""
    code_map = board_codes or {}
    rows: List[Dict[str, Any]] = []
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
                "板块代码": code_map.get(name, "") or "",
                "最新价": "",
                "涨跌额": "",
                "涨跌幅": float(cnt),
                "总市值": "",
                "换手率": "",
                "上涨家数": cnt,
                "下跌家数": "",
                "领涨股票": _leader_name(item.get("stocks")),
                "领涨股票-涨跌幅": "",
            }
        )
    if not rows:
        return pd.DataFrame(columns=_BOARD_OUT_COLS)
    return pd.DataFrame(rows)[_BOARD_OUT_COLS]


def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 读取失败 {path}: {e}")
        return None
    return data if isinstance(data, dict) else None


def _load_stock_info() -> Dict[str, Any]:
    global _stock_info_cache
    if _stock_info_cache is not None:
        return _stock_info_cache
    if not os.path.isfile(STOCK_INFO_PATH):
        print(f"[warn] 缺少 {STOCK_INFO_PATH}，无法补全 industry/concepts")
        _stock_info_cache = {}
        return _stock_info_cache
    data = _load_json(STOCK_INFO_PATH)
    if data is None:
        _stock_info_cache = {}
        return _stock_info_cache
    if isinstance(data.get("stocks"), dict):
        _stock_info_cache = data["stocks"]
    else:
        _stock_info_cache = {k: v for k, v in data.items() if isinstance(v, dict)}
    print(f"[info] loaded stock_info entries={len(_stock_info_cache)}")
    return _stock_info_cache


def _norm_code6(code: Any) -> str:
    s = str(code or "").strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _lookup_info(stock_info: Dict[str, Any], code: Any) -> Dict[str, Any]:
    code6 = _norm_code6(code)
    if not code6:
        return {}
    info = stock_info.get(code6)
    if isinstance(info, dict):
        return info
    stripped = code6.lstrip("0") or "0"
    info = stock_info.get(stripped)
    if isinstance(info, dict):
        return info
    if code6.isdigit():
        info = stock_info.get(str(int(code6)))
        if isinstance(info, dict):
            return info
    return {}


def _list_rank_csv_dates(out_dir: str, kind: str) -> List[str]:
    pat = _RANK_FILE_RE[kind]
    if not os.path.isdir(out_dir):
        return []
    dates: List[str] = []
    for name in os.listdir(out_dir):
        m = pat.match(name)
        if m:
            dates.append(m.group(1))
    dates.sort()
    return dates


def _load_board_code_map(path: str) -> Dict[str, str]:
    """板块名称 → 板块代码。"""
    df = None
    last_err: Optional[BaseException] = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            df = pd.read_csv(path, encoding=enc)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            df = None
    if df is None:
        print(f"[warn] 读取板块榜失败 {path}: {last_err}")
        return {}
    name_col = "板块名称" if "板块名称" in df.columns else None
    if not name_col:
        return {}
    code_col = "板块代码" if "板块代码" in df.columns else None
    out: Dict[str, str] = {}
    for _, row in df.iterrows():
        name = str(row.get(name_col) or "").strip()
        if not name or name == "nan":
            continue
        code = ""
        if code_col:
            code = str(row.get(code_col) or "").strip()
            if code == "nan":
                code = ""
        out[name] = code
    return out


def resolve_board_universe(
    ds: str,
    kind: str,
    *,
    out_dir: str = OUT_DIR,
) -> Tuple[Dict[str, str], str]:
    """返回 ({板块名称: 板块代码}, 来源说明)。优先当日 rank CSV，否则最近可用日。"""
    cache_key = (ds, kind)
    if cache_key in _board_universe_cache:
        return _board_universe_cache[cache_key]

    exact = os.path.join(out_dir, f"{kind}_rank_{ds}.csv")
    if os.path.isfile(exact):
        m = _load_board_code_map(exact)
        src = f"{kind}_rank:{ds}"
        _board_universe_cache[cache_key] = (m, src)
        return m, src

    target = _parse_ymd(ds)
    candidates = _list_rank_csv_dates(out_dir, kind)
    # 先找 <= ds 的最近日，再找任意最近日
    prior = [d for d in candidates if _parse_ymd(d) <= target]
    pick = prior[-1] if prior else (candidates[-1] if candidates else None)
    if not pick:
        _board_universe_cache[cache_key] = ({}, "missing")
        return {}, "missing"
    path = os.path.join(out_dir, f"{kind}_rank_{pick}.csv")
    m = _load_board_code_map(path)
    src = f"{kind}_rank:{pick}"
    _board_universe_cache[cache_key] = (m, src)
    return m, src


def _tag_match_keys(tag: str) -> Set[str]:
    """生成用于匹配东财板块名的键。"""
    raw = (tag or "").strip()
    if not raw or raw == "nan" or _is_excluded_tag(raw):
        return set()
    keys = {raw}
    agg = _aggregate_tag_name(raw)
    if agg:
        keys.add(agg)
        keys.add(f"{agg}概念")
    base = _strip_roman_tail(raw)
    if base:
        keys.add(base)
        keys.add(_aggregate_tag_name(base) or base)
    return {k for k in keys if k and not _is_excluded_tag(k)}


def _boards_hit_by_tags(tags: Sequence[str], universe: Dict[str, str]) -> List[str]:
    """股票标签 → 命中的东财板块名列表（去重，保持稳定顺序）。"""
    if not universe:
        return []
    uni_names = list(universe.keys())
    # base(去罗马尾缀) → 板块名列表，便于 装修装饰 → 装修装饰Ⅱ
    by_base: Dict[str, List[str]] = {}
    for b in uni_names:
        by_base.setdefault(b, []).append(b)
        base = _strip_roman_tail(b)
        if base and base != b:
            by_base.setdefault(base, []).append(b)
        agg = _aggregate_tag_name(b)
        if agg:
            by_base.setdefault(agg, []).append(b)

    hit: List[str] = []
    seen: Set[str] = set()
    for tag in tags:
        for key in _tag_match_keys(tag):
            for board in by_base.get(key, []):
                if board not in seen:
                    seen.add(board)
                    hit.append(board)
            # 精确在 universe
            if key in universe and key not in seen:
                seen.add(key)
                hit.append(key)
    return hit


def _merged_stock_fields(
    stock: dict, stock_info: Dict[str, Any]
) -> Tuple[str, List[str], List[str]]:
    """合并 JSON 与 all_a_stock_info 的 industry / plates / concepts。"""
    info = _lookup_info(stock_info, stock.get("code"))
    industry = str(stock.get("industry") or "").strip()
    if not industry or industry == "nan":
        industry = str(info.get("industry") or "").strip()

    plates: List[str] = []
    for src in (stock.get("plates"), info.get("plates")):
        if isinstance(src, list):
            for p in src:
                s = str(p or "").strip()
                if s and s not in plates:
                    plates.append(s)

    concepts: List[str] = []
    for src in (stock.get("concepts"), info.get("concepts")):
        if isinstance(src, list):
            for c in src:
                s = str(c or "").strip()
                if s and s not in concepts:
                    concepts.append(s)
    return industry, plates, concepts


def _build_board_lu_stats(
    stocks: List[dict],
    stock_info: Dict[str, Any],
    universe: Dict[str, str],
    *,
    kind: str,
) -> List[Dict[str, Any]]:
    """按东财板块成分统计涨停家数。"""
    board_count: Dict[str, Dict[str, Any]] = {}
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        industry, plates, concepts = _merged_stock_fields(stock, stock_info)
        if kind == "industry":
            tags = []
            if industry:
                tags.append(industry)
            tags.extend(plates)
        else:
            tags = list(concepts)
            tags.extend(plates)
            if industry:
                tags.append(industry)

        boards = _boards_hit_by_tags(tags, universe)
        if not boards:
            continue
        code = _norm_code6(stock.get("code")) or str(stock.get("code", ""))
        name = str(stock.get("name") or "").strip()
        stock_str = f"{code} {name}".strip()
        for board in boards:
            if _is_excluded_tag(board):
                continue
            if board not in board_count:
                board_count[board] = {"name": board, "count": 0, "stocks": []}
            board_count[board]["count"] += 1
            if stock_str not in board_count[board]["stocks"]:
                board_count[board]["stocks"].append(stock_str)

    out = [item for item in board_count.values() if item["count"] >= 1]
    out.sort(key=lambda x: (-x["count"], x["name"]))
    return out


def _build_leaf_industry_fallback(
    stocks: List[dict], stock_info: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """无东财行业榜宇宙时的回退：按个股 industry 叶子聚合。"""
    industry_count: Dict[str, Dict[str, Any]] = {}
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        industry, _plates, _concepts = _merged_stock_fields(stock, stock_info)
        industry_name = industry.strip()
        if not industry_name or industry_name == "nan" or _is_excluded_tag(industry_name):
            continue
        if industry_name not in industry_count:
            industry_count[industry_name] = {
                "name": industry_name,
                "count": 0,
                "stocks": [],
            }
        industry_count[industry_name]["count"] += 1
        code = _norm_code6(stock.get("code")) or str(stock.get("code", ""))
        name = str(stock.get("name") or "").strip()
        industry_count[industry_name]["stocks"].append(f"{code} {name}".strip())
    out = list(industry_count.values())
    out.sort(key=lambda x: (-x["count"], x["name"]))
    return out


def _save_csv(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def export_one_day(
    ds: str,
    *,
    history_dir: str = HISTORY_DIR,
    out_dir: str = OUT_DIR,
    stock_info: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """导出单日；返回 status 摘要。"""
    src = resolve_limit_up_day_json_path(ds, history_dir)
    if not src:
        return {"date": ds, "status": "missing_json"}

    industry_path = os.path.join(out_dir, f"industry_lu_rank_{ds}.csv")
    concept_path = os.path.join(out_dir, f"concept_lu_rank_{ds}.csv")
    if (
        not force
        and not dry_run
        and os.path.isfile(industry_path)
        and os.path.isfile(concept_path)
    ):
        return {
            "date": ds,
            "status": "skipped_exists",
            "industry_path": industry_path,
            "concept_path": concept_path,
        }

    data = _load_json(src)
    if data is None:
        return {"date": ds, "status": "bad_json", "src": src}

    stocks = data.get("limit_up_stocks") or []
    if not isinstance(stocks, list) or not stocks:
        return {"date": ds, "status": "empty_stocks", "src": src}

    info = stock_info if stock_info is not None else _load_stock_info()

    ind_uni, ind_uni_src = resolve_board_universe(ds, "industry", out_dir=out_dir)
    con_uni, con_uni_src = resolve_board_universe(ds, "concept", out_dir=out_dir)

    if ind_uni:
        industry_stats = _build_board_lu_stats(
            stocks, info, ind_uni, kind="industry"
        )
        ind_src = f"em_board({ind_uni_src})"
    else:
        industry_stats = _build_leaf_industry_fallback(stocks, info)
        ind_src = "leaf_industry_fallback"

    if con_uni:
        concept_stats = _build_board_lu_stats(stocks, info, con_uni, kind="concept")
        con_src = f"em_board({con_uni_src})"
    else:
        # 无概念宇宙：用 concepts 聚合（count>=2）
        concept_stats = _build_board_lu_stats(
            stocks, info, {c: "" for c in _collect_all_concept_names(stocks, info)}, kind="concept"
        )
        concept_stats = [x for x in concept_stats if x["count"] >= 2]
        con_src = "concepts_fallback"

    industry_df = stats_to_rank_df(industry_stats, board_codes=ind_uni)
    concept_df = stats_to_rank_df(concept_stats, board_codes=con_uni)

    if industry_df.empty and concept_df.empty:
        return {
            "date": ds,
            "status": "empty_stats",
            "src": src,
            "industry_source": ind_src,
            "concept_source": con_src,
        }

    result = {
        "date": ds,
        "status": "ok" if not dry_run else "dry_run",
        "src": src,
        "industry_n": len(industry_df),
        "concept_n": len(concept_df),
        "industry_source": ind_src,
        "concept_source": con_src,
        "industry_path": industry_path,
        "concept_path": concept_path,
    }

    if dry_run:
        return result

    if not industry_df.empty:
        _save_csv(industry_df, industry_path)
    if not concept_df.empty:
        _save_csv(concept_df, concept_path)
    if industry_df.empty or concept_df.empty:
        result["status"] = "partial"
    try:
        from tools.export_board_rank_to_jsonl import write_daily_board_rank_jsonl

        write_daily_board_rank_jsonl(ds)
    except Exception as e:
        print("[board_rank jsonl] export failed:", e)
    return result


def _collect_all_concept_names(
    stocks: List[dict], stock_info: Dict[str, Any]
) -> List[str]:
    names: List[str] = []
    seen: Set[str] = set()
    for stock in stocks:
        _ind, plates, concepts = _merged_stock_fields(stock, stock_info)
        for c in list(concepts) + list(plates):
            for k in _tag_match_keys(c):
                if k not in seen:
                    seen.add(k)
                    names.append(k)
    return names


def export_dates(
    dates: Sequence[str],
    *,
    history_dir: str = HISTORY_DIR,
    out_dir: str = OUT_DIR,
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    info = _load_stock_info()
    by_status: Dict[str, List[str]] = {}
    written: List[str] = []

    for ds in dates:
        r = export_one_day(
            ds,
            history_dir=history_dir,
            out_dir=out_dir,
            stock_info=info,
            dry_run=dry_run,
            force=force,
        )
        st = str(r.get("status") or "unknown")
        by_status.setdefault(st, []).append(ds)
        if st in ("ok", "partial", "dry_run"):
            written.append(ds)
            print(
                f"[{st}] {ds}: industry={r.get('industry_n')}({r.get('industry_source')}) "
                f"concept={r.get('concept_n')}({r.get('concept_source')})"
            )
        elif st == "skipped_exists":
            print(f"[skip] {ds}: lu_rank already exists (use --force)")
        else:
            print(f"[skip] {ds}: {st}")

    return {
        "written": written,
        "by_status": by_status,
        "out_dir": out_dir,
        "dry_run": dry_run,
    }


def _select_dates(
    *,
    one_date: Optional[str],
    start: Optional[str],
    end: Optional[str],
    history_dir: str,
) -> List[str]:
    if one_date:
        return [_parse_ymd(one_date).strftime("%Y-%m-%d")]

    all_pairs = list_limit_up_day_json_files(history_dir)
    all_dates = [d for d, _ in all_pairs]
    if not start and not end:
        return all_dates

    start_d = _parse_ymd(start) if start else date.min
    end_d = _parse_ymd(end) if end else date.max
    out: List[str] = []
    for ds in all_dates:
        d = _parse_ymd(ds)
        if start_d <= d <= end_d:
            out.append(ds)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="导出涨停家数行业/概念排名 CSV（东财板块口径）")
    ap.add_argument("--date", help="单日 YYYY-MM-DD")
    ap.add_argument("--start", help="起始日（含）")
    ap.add_argument("--end", help="结束日（含）")
    ap.add_argument("--history-dir", default=HISTORY_DIR, help="history_data 根目录")
    ap.add_argument("--out-dir", default=OUT_DIR, help="输出目录")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖已有 *_lu_rank_*")
    args = ap.parse_args(list(argv) if argv is not None else None)

    dates = _select_dates(
        one_date=args.date,
        start=args.start,
        end=args.end,
        history_dir=args.history_dir,
    )
    if not dates:
        print("[done] 无待处理日期")
        return 0

    print(
        f"[info] dates={len(dates)} out_dir={args.out_dir} "
        f"dry_run={args.dry_run} force={args.force}"
    )
    report = export_dates(
        dates,
        history_dir=args.history_dir,
        out_dir=args.out_dir,
        dry_run=args.dry_run,
        force=args.force,
    )
    by_status = report.get("by_status") or {}
    print()
    print("=" * 60)
    print(f"[done] written={len(report.get('written') or [])}")
    for st, lst in sorted(by_status.items()):
        print(f"  {st}: {len(lst)}")
    if args.date:
        ds = _parse_ymd(args.date).strftime("%Y-%m-%d")
        for st, lst in by_status.items():
            if ds in lst and st not in ("ok", "partial", "dry_run", "skipped_exists"):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
