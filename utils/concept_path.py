# -*- coding: utf-8 -*-
"""概念汇总 / 五日排名 Excel 路径约定。

落盘目录：
  history_data/concept/concept_summary_YYYY-MM-DD.xlsx
  history_data/concept/concept_rank_YYYY-MM-DD.xlsx

读取顺序：现行目录 → 存档目录；子目录优先于根目录旧文件。
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from utils.history_data_archive import archive_search_roots

CONCEPT_SUBDIR_NAME = "concept"
SUMMARY_PREFIX = "concept_summary_"
RANK_PREFIX = "concept_rank_"
XLSX_SUFFIX = ".xlsx"

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
_DATE8_RE = re.compile(r"^(\d{8})$")


def _to_dashed_date(trade_date: str) -> str:
    """YYYYMMDD 或 YYYY-MM-DD → YYYY-MM-DD。"""
    s = str(trade_date or "").strip()
    if _DATE_RE.match(s):
        return s
    s8 = s.replace("-", "")
    if _DATE8_RE.match(s8):
        return f"{s8[:4]}-{s8[4:6]}-{s8[6:8]}"
    raise ValueError(f"无效交易日: {trade_date}")


def concept_data_dir(history_dir: str = "history_data") -> str:
    return os.path.join(history_dir, CONCEPT_SUBDIR_NAME)


def ensure_concept_data_dir(history_dir: str = "history_data") -> str:
    d = concept_data_dir(history_dir)
    os.makedirs(d, exist_ok=True)
    return d


def concept_summary_path(trade_date: str, history_dir: str = "history_data") -> str:
    """新约定路径（写入用）。"""
    ds = _to_dashed_date(trade_date)
    return os.path.join(concept_data_dir(history_dir), f"{SUMMARY_PREFIX}{ds}{XLSX_SUFFIX}")


def concept_rank_path(trade_date: str, history_dir: str = "history_data") -> str:
    """新约定路径（写入用）。"""
    ds = _to_dashed_date(trade_date)
    return os.path.join(concept_data_dir(history_dir), f"{RANK_PREFIX}{ds}{XLSX_SUFFIX}")


def resolve_concept_summary_path(trade_date: str, history_dir: str = "history_data") -> Optional[str]:
    ds = _to_dashed_date(trade_date)
    name = f"{SUMMARY_PREFIX}{ds}{XLSX_SUFFIX}"
    for root in archive_search_roots(history_dir):
        for p in (
            os.path.join(root, CONCEPT_SUBDIR_NAME, name),
            os.path.join(root, name),
        ):
            if os.path.isfile(p):
                return p
    return None


def resolve_concept_rank_path(trade_date: str, history_dir: str = "history_data") -> Optional[str]:
    ds = _to_dashed_date(trade_date)
    name = f"{RANK_PREFIX}{ds}{XLSX_SUFFIX}"
    for root in archive_search_roots(history_dir):
        for p in (
            os.path.join(root, CONCEPT_SUBDIR_NAME, name),
            os.path.join(root, name),
        ):
            if os.path.isfile(p):
                return p
    return None


def list_concept_summary_files(history_dir: str = "history_data") -> List[Tuple[str, str]]:
    """返回 [(YYYYMMDD, filepath), ...]；同日优先现行目录。"""
    by_date: dict = {}
    # 先扫存档，再扫现行（覆盖），保证现行优先
    for root in reversed(archive_search_roots(history_dir)):
        for d in (root, os.path.join(root, CONCEPT_SUBDIR_NAME)):
            if not os.path.isdir(d):
                continue
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for name in names:
                if not (name.startswith(SUMMARY_PREFIX) and name.endswith(XLSX_SUFFIX)):
                    continue
                m = re.match(
                    rf"^{re.escape(SUMMARY_PREFIX)}(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(XLSX_SUFFIX)}$",
                    name,
                )
                if not m:
                    continue
                d8 = m.group(1).replace("-", "")
                by_date[d8] = os.path.join(d, name)
    return sorted(by_date.items(), key=lambda x: x[0])
