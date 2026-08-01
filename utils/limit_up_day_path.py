# -*- coding: utf-8 -*-
"""涨停日快照 JSON 路径约定。

落盘目录：history_data/涨停日数据/YYYY-MM-DD.json
读取顺序：现行目录 → 存档目录；子目录优先于根目录旧文件。
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from utils.history_data_archive import archive_search_roots

LIMIT_UP_DAY_SUBDIR_NAME = "涨停日数据"
_DATE_JSON_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def _norm_dashed_date(date_str: str) -> str:
    s = str(date_str or "").strip()
    if s.endswith(".json"):
        s = s[:-5]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    s8 = s.replace("-", "")
    if len(s8) == 8 and s8.isdigit():
        return f"{s8[:4]}-{s8[4:6]}-{s8[6:8]}"
    raise ValueError(f"无效日期: {date_str}")


def limit_up_day_data_dir(history_dir: str = "history_data") -> str:
    return os.path.join(history_dir, LIMIT_UP_DAY_SUBDIR_NAME)


def ensure_limit_up_day_data_dir(history_dir: str = "history_data") -> str:
    d = limit_up_day_data_dir(history_dir)
    os.makedirs(d, exist_ok=True)
    return d


def limit_up_day_json_path(date_str: str, history_dir: str = "history_data") -> str:
    """新约定路径（写入用）。"""
    ds = _norm_dashed_date(date_str)
    return os.path.join(limit_up_day_data_dir(history_dir), f"{ds}.json")


def resolve_limit_up_day_json_path(date_str: str, history_dir: str = "history_data") -> Optional[str]:
    """返回已存在的文件路径；现行优先，再存档。"""
    ds = _norm_dashed_date(date_str)
    name = f"{ds}.json"
    for root in archive_search_roots(history_dir):
        for p in (
            os.path.join(root, LIMIT_UP_DAY_SUBDIR_NAME, name),
            os.path.join(root, name),
        ):
            if os.path.isfile(p):
                return p
    return None


def list_limit_up_day_json_files(history_dir: str = "history_data") -> List[Tuple[str, str]]:
    """返回 [(YYYY-MM-DD, filepath), ...]；同日优先现行目录。"""
    by_date = {}
    for root in reversed(archive_search_roots(history_dir)):
        for d in (root, os.path.join(root, LIMIT_UP_DAY_SUBDIR_NAME)):
            if not os.path.isdir(d):
                continue
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for name in names:
                m = _DATE_JSON_RE.match(name)
                if not m:
                    continue
                by_date[m.group(1)] = os.path.join(d, name)
    return sorted(by_date.items(), key=lambda x: x[0])


def list_limit_up_day_dates(history_dir: str = "history_data", *, reverse: bool = True) -> List[str]:
    dates = [d for d, _ in list_limit_up_day_json_files(history_dir)]
    dates.sort(reverse=reverse)
    return dates
