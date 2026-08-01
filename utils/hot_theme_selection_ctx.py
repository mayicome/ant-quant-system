# -*- coding: utf-8 -*-
"""选股引擎用：按日加载涨停榜「十大热门板块 / 十大热门概念」成员，供规则 ctx['hot_theme']。"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Union

from utils.limit_up_day_path import resolve_limit_up_day_json_path

DateLike = Union[date, datetime, str, None]
_CODE_RE = re.compile(r"(\d{6})")


def _dashed(d: DateLike) -> Optional[str]:
    if d is None:
        d = date.today()
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    s = str(d).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _norm_code6(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    m = _CODE_RE.search(s)
    if m:
        return m.group(1)
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)[-6:]
    return ""


def _extract_codes_from_stat_row(row: dict) -> Set[str]:
    out: Set[str] = set()
    if not isinstance(row, dict):
        return out
    stocks = row.get("stocks") or []
    if isinstance(stocks, list):
        for item in stocks:
            c = _norm_code6(item)
            if c:
                out.add(c)
    return out


def _aggregate_tag_name(raw_name: str) -> str:
    """与涨停监控一致：将「xxx概念」归一化为「xxx」。"""
    name = (raw_name or "").strip()
    if not name or name == "nan":
        return ""
    if name.endswith("概念"):
        base = name[:-2].strip()
        return base if base else ""
    return name


def _strip_qmt_prefix(tag: str) -> str:
    s = str(tag or "").strip()
    for pref in ("SW1", "SW2", "SW3", "GN"):
        if s.startswith(pref):
            return s[len(pref) :].strip()
    return s


def _theme_match_keys(name: str) -> Set[str]:
    """热门题材名可用于精确比对的键集合（原文 + 去「概念」）。"""
    n = str(name or "").strip()
    if not n:
        return set()
    keys = {n}
    agg = _aggregate_tag_name(n)
    if agg:
        keys.add(agg)
    if not n.endswith("概念") and n:
        keys.add(f"{n}概念")
    return keys


def _tag_matches_hot(tag: str, hot_keys: Set[str]) -> bool:
    if not hot_keys:
        return False
    bare = _strip_qmt_prefix(tag)
    if not bare:
        return False
    if bare in hot_keys:
        return True
    agg = _aggregate_tag_name(bare)
    return bool(agg) and agg in hot_keys


def _expand_affiliation_from_qmt_index(
    hot_sectors: List[str],
    hot_concepts: List[str],
    *,
    sector_codes: Set[str],
    concept_codes: Set[str],
    code_hits: Dict[str, Dict[str, Any]],
    hit_fn,
) -> None:
    """用 QMT 板块反查索引，把「归属」热门板块/概念的股票并入成员集合。

    当日涨停名单仅覆盖涨停股；归属扩展覆盖同题材非涨停股（配合「近10日有涨停」）。
    """
    try:
        from utils.qmt_sector_store import get_qmt_sector_store
    except Exception:
        return
    try:
        store = get_qmt_sector_store()
        store.ensure_inverted_index()
        code_sectors = getattr(store, "_code_sectors", None) or {}
    except Exception:
        return
    if not isinstance(code_sectors, dict) or not code_sectors:
        return

    sector_keys: Set[str] = set()
    for n in hot_sectors:
        sector_keys |= _theme_match_keys(n)
    concept_keys: Set[str] = set()
    for n in hot_concepts:
        concept_keys |= _theme_match_keys(n)
    if not sector_keys and not concept_keys:
        return

    for code, tags in code_sectors.items():
        c6 = _norm_code6(code)
        if not c6 or not isinstance(tags, (list, tuple, set)):
            continue
        matched_sectors: List[str] = []
        matched_concepts: List[str] = []
        for tag in tags:
            t = str(tag or "").strip()
            if not t:
                continue
            if _tag_matches_hot(t, sector_keys):
                bare = _strip_qmt_prefix(t) or t
                # 回写到用户可见的热门名（优先原文命中）
                for hn in hot_sectors:
                    if _tag_matches_hot(t, _theme_match_keys(hn)):
                        if hn not in matched_sectors:
                            matched_sectors.append(hn)
                        break
                else:
                    if bare not in matched_sectors:
                        matched_sectors.append(bare)
            if _tag_matches_hot(t, concept_keys):
                for hn in hot_concepts:
                    if _tag_matches_hot(t, _theme_match_keys(hn)):
                        if hn not in matched_concepts:
                            matched_concepts.append(hn)
                        break
                else:
                    bare = _strip_qmt_prefix(t) or t
                    agg = _aggregate_tag_name(bare) or bare
                    if agg not in matched_concepts:
                        matched_concepts.append(agg)
        if matched_sectors:
            sector_codes.add(c6)
            h = hit_fn(c6)
            h["in_hot_sector"] = True
            for name in matched_sectors:
                if name and name not in h["sectors"]:
                    h["sectors"].append(name)
        if matched_concepts:
            concept_codes.add(c6)
            h = hit_fn(c6)
            h["in_hot_concept"] = True
            for name in matched_concepts:
                if name and name not in h["concepts"]:
                    h["concepts"].append(name)


def _load_day_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def resolve_hot_theme_json_path(
    as_of: DateLike,
    history_dir: str = "history_data",
    *,
    lookback_calendar_days: int = 10,
) -> Optional[str]:
    """选股日对应涨停日 JSON；当日缺失则向前找最近有文件的交易日快照。"""
    ds = _dashed(as_of)
    if not ds:
        return None
    p = resolve_limit_up_day_json_path(ds, history_dir=history_dir)
    if p:
        return p
    try:
        y, m, d = int(ds[0:4]), int(ds[5:7]), int(ds[8:10])
        cur = date(y, m, d)
    except Exception:
        return None
    for i in range(1, max(1, int(lookback_calendar_days)) + 1):
        prev = cur - timedelta(days=i)
        p = resolve_limit_up_day_json_path(prev.strftime("%Y-%m-%d"), history_dir=history_dir)
        if p:
            return p
    return None


def load_hot_theme_map(
    as_of: DateLike,
    history_dir: str = "history_data",
    *,
    top_n: int = 10,
) -> Dict[str, Any]:
    """返回热门主题上下文。

    结构::
        {
          "as_of": "YYYY-MM-DD",
          "source_date": "YYYY-MM-DD",  # 实际用到的涨停日 JSON 日期
          "top_n": 10,
          "hot_sectors": ["华为概念", ...],          # sector_plate_stats 前 N
          "hot_concepts": ["华为", ...],            # concept_stats 前 N
          "sector_codes": {"000001", ...},          # 落入热门板块的代码
          "concept_codes": {"000001", ...},
          "union_codes": {"000001", ...},           # 板块∪概念
          "code_hits": {                           # code6 -> 命中明细
              "301121": {
                  "in_hot_sector": True,
                  "in_hot_concept": True,
                  "sectors": ["华为概念"],
                  "concepts": ["华为"],
              },
          },
        }
    """
    empty: Dict[str, Any] = {
        "as_of": _dashed(as_of) or "",
        "source_date": "",
        "top_n": int(top_n),
        "hot_sectors": [],
        "hot_concepts": [],
        "sector_codes": set(),
        "concept_codes": set(),
        "union_codes": set(),
        "code_hits": {},
    }
    path = resolve_hot_theme_json_path(as_of, history_dir=history_dir)
    if not path or not os.path.isfile(path):
        return empty

    try:
        data = _load_day_json(path)
    except Exception:
        return empty

    n = max(1, int(top_n or 10))
    sector_stats = data.get("sector_plate_stats") or []
    concept_stats = data.get("concept_stats") or []
    if not isinstance(sector_stats, list):
        sector_stats = []
    if not isinstance(concept_stats, list):
        concept_stats = []

    top_sectors = [x for x in sector_stats[:n] if isinstance(x, dict) and str(x.get("name") or "").strip()]
    top_concepts = [x for x in concept_stats[:n] if isinstance(x, dict) and str(x.get("name") or "").strip()]

    hot_sectors: List[str] = [str(x.get("name") or "").strip() for x in top_sectors]
    hot_concepts: List[str] = [str(x.get("name") or "").strip() for x in top_concepts]

    sector_codes: Set[str] = set()
    concept_codes: Set[str] = set()
    code_hits: Dict[str, Dict[str, Any]] = {}

    def _hit(code: str) -> Dict[str, Any]:
        h = code_hits.get(code)
        if h is None:
            h = {
                "in_hot_sector": False,
                "in_hot_concept": False,
                "sectors": [],
                "concepts": [],
            }
            code_hits[code] = h
        return h

    for row in top_sectors:
        name = str(row.get("name") or "").strip()
        for code in _extract_codes_from_stat_row(row):
            sector_codes.add(code)
            h = _hit(code)
            h["in_hot_sector"] = True
            if name and name not in h["sectors"]:
                h["sectors"].append(name)

    for row in top_concepts:
        name = str(row.get("name") or "").strip()
        for code in _extract_codes_from_stat_row(row):
            concept_codes.add(code)
            h = _hit(code)
            h["in_hot_concept"] = True
            if name and name not in h["concepts"]:
                h["concepts"].append(name)

    # 归属扩展：QMT 反查索引中标签命中十大热门板块/概念的全部成分
    _expand_affiliation_from_qmt_index(
        hot_sectors,
        hot_concepts,
        sector_codes=sector_codes,
        concept_codes=concept_codes,
        code_hits=code_hits,
        hit_fn=_hit,
    )

    source_date = str(data.get("date") or "").strip()
    if not source_date:
        base = os.path.basename(path)
        if base.endswith(".json"):
            source_date = base[:-5]

    return {
        "as_of": _dashed(as_of) or "",
        "source_date": source_date,
        "top_n": n,
        "hot_sectors": hot_sectors,
        "hot_concepts": hot_concepts,
        "sector_codes": sector_codes,
        "concept_codes": concept_codes,
        "union_codes": set(sector_codes) | set(concept_codes),
        "code_hits": code_hits,
    }
