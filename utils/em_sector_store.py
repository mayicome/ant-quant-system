# -*- coding: utf-8 -*-
"""选股界面的东财行业列表和成分。

行业名来自最近一份东财行业榜。成分来自 data/all_a_stock_info.json
里每只股票的行业和板块标签。不读 qmt_sector_index.json。
"""
from __future__ import annotations

import csv
import json
import os
import threading
from typing import Dict, List, Literal, Optional, Set

_INDUSTRY_RANK_PREFIX = "industry_rank_"
_INFO_FILENAME = "all_a_stock_info.json"
_UNIVERSE_FILENAME = "a_share_universe.json"
UNCLASSIFIED_INDUSTRY = "不属于任何行业"


def _project_data_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data")


def _code6(raw) -> str:
    s = str(raw or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _read_csv_rows(path: str) -> List[dict]:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except Exception:
            continue
    return []


def latest_industry_rank_path(data_dir: Optional[str] = None) -> str:
    base = os.path.join(data_dir or _project_data_dir(), "eastmoney_board_rank")
    names: List[str] = []
    try:
        names = [
            n
            for n in os.listdir(base)
            if n.startswith(_INDUSTRY_RANK_PREFIX) and n.endswith(".csv")
        ]
    except OSError:
        return ""
    if not names:
        return ""
    names.sort()
    return os.path.join(base, names[-1])


def load_industry_names(data_dir: Optional[str] = None) -> List[str]:
    path = latest_industry_rank_path(data_dir)
    if not path:
        return []
    rows = _read_csv_rows(path)
    if not rows:
        return []
    cols = list(rows[0].keys())
    name_col = "板块名称" if "板块名称" in cols else ""
    if not name_col:
        for col in cols:
            if "名称" in str(col):
                name_col = col
                break
    if not name_col:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for row in rows:
        name = str(row.get(name_col) or "").strip()
        if not name or name == "nan" or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


class EmSectorStore:
    """东财行业榜 + 股票信息标签。"""

    _instance: Optional["EmSectorStore"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "EmSectorStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._ready = False
                    cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_inited", False):
            return
        self._inited = True
        self._industries: List[str] = []
        self._industry_set: Set[str] = set()
        self._code_industries: Dict[str, List[str]] = {}
        self._concepts: Dict[str, List[str]] = {}
        self._plates: Dict[str, List[str]] = {}
        self._names: Dict[str, str] = {}
        self._universe: Optional[Set[str]] = None

    def list_ui_sectors(self) -> List[str]:
        self.ensure_inverted_index()
        return list(self._industries)

    def ensure_inverted_index(self, force_rebuild: bool = False) -> None:
        if self._ready and not force_rebuild:
            return
        industries = load_industry_names()
        industry_set = set(industries)
        code_industries: Dict[str, List[str]] = {}
        concepts: Dict[str, List[str]] = {}
        plates: Dict[str, List[str]] = {}
        names: Dict[str, str] = {}
        path = os.path.join(_project_data_dir(), _INFO_FILENAME)
        payload = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f) or {}
            except Exception:
                payload = {}
        if isinstance(payload, dict):
            for raw_code, row in payload.items():
                if not isinstance(row, dict):
                    continue
                code = _code6(row.get("stock_code") or raw_code)
                if not code:
                    continue
                name = str(row.get("name") or "").strip()
                if name:
                    names[code] = name
                hit: List[str] = []
                ind = str(row.get("industry") or "").strip()
                if ind and ind in industry_set and ind not in hit:
                    hit.append(ind)
                plate_names: List[str] = []
                for item in row.get("plates") or []:
                    plate = str(item or "").strip()
                    if not plate:
                        continue
                    if plate not in plate_names:
                        plate_names.append(plate)
                    if plate in industry_set and plate not in hit:
                        hit.append(plate)
                concept_names: List[str] = []
                for item in row.get("concepts") or []:
                    concept = str(item or "").strip()
                    if concept and concept not in concept_names:
                        concept_names.append(concept)
                if hit:
                    code_industries[code] = hit
                if concept_names:
                    concepts[code] = concept_names
                if plate_names:
                    plates[code] = plate_names
        self._industries = industries
        self._industry_set = industry_set
        self._code_industries = code_industries
        self._concepts = concepts
        self._plates = plates
        self._names = names
        self._universe = None
        self._ready = True

    def unclassified_codes(self) -> Set[str]:
        """股票列表里、东财行业都没有归进去的代码。"""
        self.ensure_inverted_index()
        return set(self.get_universe()) - set(self._code_industries.keys())

    def _members_of(self, name: str) -> Set[str]:
        if name == UNCLASSIFIED_INDUSTRY:
            return self.unclassified_codes()
        return {code for code, tags in self._code_industries.items() if name in tags}

    def sectors_for_stock(self, code6: str) -> List[str]:
        self.ensure_inverted_index()
        return list(self._code_industries.get(_code6(code6), []))

    def concepts_for_stock(self, code6: str) -> List[str]:
        self.ensure_inverted_index()
        return list(self._concepts.get(_code6(code6), []))

    def plates_for_stock(self, code6: str) -> List[str]:
        self.ensure_inverted_index()
        return list(self._plates.get(_code6(code6), []))

    def stock_name(self, code6: str) -> str:
        self.ensure_inverted_index()
        code = _code6(code6)
        name = self._names.get(code) or ""
        if name:
            return name
        try:
            from utils.stock_info_manager import get_stock_name

            got = get_stock_name(code)
            if got and got != "未知名称":
                return str(got)
        except Exception:
            pass
        return "未知"

    def get_universe(self) -> Set[str]:
        if self._universe is not None:
            return set(self._universe)
        path = os.path.join(_project_data_dir(), _UNIVERSE_FILENAME)
        codes: Set[str] = set()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f) or {}
                raw = list((payload or {}).get("codes") or [])
                codes = {_code6(c) for c in raw if _code6(c)}
            except Exception:
                codes = set()
        if not codes:
            self.ensure_inverted_index()
            codes = set(self._names.keys()) or set(self._code_industries.keys())
        self._universe = codes
        return set(codes)

    def stocks_for_sectors(
        self,
        selected: List[str],
        mode: Literal["union", "intersection"] = "union",
    ) -> Dict[str, Dict[str, object]]:
        selected = [s.strip() for s in (selected or []) if s and str(s).strip()]
        if not selected:
            return {}
        self.ensure_inverted_index()
        selected_set = set(selected)
        real_selected = [s for s in selected if s != UNCLASSIFIED_INDUSTRY]
        want_unclassified = UNCLASSIFIED_INDUSTRY in selected_set
        all_real = bool(self._industries) and set(real_selected) == set(self._industries)

        if all_real:
            # 行业全选只含有行业归属的股票。无行业的要另勾「不属于任何行业」。
            matched_set = set(self._code_industries.keys())
            if want_unclassified:
                matched_set |= self.unclassified_codes()
        elif mode == "intersection":
            matched: Optional[Set[str]] = None
            for name in selected:
                members = self._members_of(name)
                matched = members if matched is None else (matched & members)
            matched_set = matched or set()
        else:
            matched_set = set()
            for name in selected:
                matched_set |= self._members_of(name)

        out: Dict[str, Dict[str, object]] = {}
        unclassified = self.unclassified_codes() if want_unclassified else set()
        for code in sorted(matched_set):
            all_s = list(self._code_industries.get(code, []))
            if not all_s and code in unclassified:
                all_s = [UNCLASSIFIED_INDUSTRY]
            if all_real:
                matched_s = list(all_s)
            else:
                matched_s = sorted(selected_set.intersection(all_s))
            out[code] = {
                "name": self.stock_name(code),
                "matched_sectors": matched_s,
                "all_sectors": all_s,
            }
        return out


_store: Optional[EmSectorStore] = None


def get_em_sector_store() -> EmSectorStore:
    global _store
    if _store is None:
        _store = EmSectorStore()
    return _store


def load_all_sectors() -> List[str]:
    """选股界面用：东财行业榜名称。"""
    return get_em_sector_store().list_ui_sectors()
