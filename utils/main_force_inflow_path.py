# -*- coding: utf-8 -*-
"""个股主力净流入 CSV 路径约定。

落盘目录：history_data/个股主力净流入/个股主力净流入_YYYYMMDD.csv
读取顺序：现行目录 → 存档目录；子目录优先于根目录旧文件。
"""
from __future__ import annotations

import os
from typing import List, Optional

from utils.history_data_archive import archive_search_roots

FLOW_SUBDIR_NAME = "个股主力净流入"
FLOW_FILE_PREFIX = "个股主力净流入_"
FLOW_FILE_SUFFIX = ".csv"


def flow_data_dir(history_dir: str = "history_data") -> str:
    return os.path.join(history_dir, FLOW_SUBDIR_NAME)


def flow_csv_path(ymd: str, history_dir: str = "history_data") -> str:
    """新约定路径（写入用）。"""
    return os.path.join(flow_data_dir(history_dir), f"{FLOW_FILE_PREFIX}{ymd}{FLOW_FILE_SUFFIX}")


def ensure_flow_data_dir(history_dir: str = "history_data") -> str:
    d = flow_data_dir(history_dir)
    os.makedirs(d, exist_ok=True)
    return d


def resolve_flow_csv_path(ymd: str, history_dir: str = "history_data") -> Optional[str]:
    """返回已存在的文件路径；现行优先，再存档。"""
    name = f"{FLOW_FILE_PREFIX}{ymd}{FLOW_FILE_SUFFIX}"
    for root in archive_search_roots(history_dir):
        for p in (
            os.path.join(root, FLOW_SUBDIR_NAME, name),
            os.path.join(root, name),
        ):
            if os.path.isfile(p):
                return p
    return None


def list_flow_file_dates(history_dir: str = "history_data") -> List[str]:
    """列出可用的 YYYYMMDD（现行 + 存档去重）。"""
    dates = set()
    for root in archive_search_roots(history_dir):
        for d in (os.path.join(root, FLOW_SUBDIR_NAME), root):
            if not os.path.isdir(d):
                continue
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for fn in names:
                if not fn.startswith(FLOW_FILE_PREFIX) or not fn.endswith(FLOW_FILE_SUFFIX):
                    continue
                part = fn[len(FLOW_FILE_PREFIX) : -len(FLOW_FILE_SUFFIX)]
                if len(part) == 8 and part.isdigit():
                    dates.add(part)
    return sorted(dates)
