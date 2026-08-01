# -*- coding: utf-8 -*-
"""history_data 按日归档。

只归档 history_data **根目录**下带日期且早于「保留日」的文件，移到 history_data/存档/。
不进入、不挪动任何业务子目录（如 个股主力净流入、concept、涨停日数据 等）。
读取方可把 存档 作为根目录旧文件的回退查找路径。
"""
from __future__ import annotations

import os
import re
import shutil
from datetime import date
from typing import Iterable, List, Optional, Tuple

ARCHIVE_SUBDIR_NAME = "存档"

# 文件名中的日期：优先 YYYY-MM-DD，其次 YYYYMMDD
_DATE_DASH = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_DATE_COMPACT = re.compile(r"(20\d{2})(\d{2})(\d{2})")


def archive_dir(history_dir: str) -> str:
    return os.path.join(history_dir, ARCHIVE_SUBDIR_NAME)


def extract_file_date(filename: str) -> Optional[date]:
    """从文件名解析业务日期；解析不到则返回 None（不归档）。"""
    name = os.path.basename(filename)
    m = _DATE_DASH.search(name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = _DATE_COMPACT.search(name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def iter_history_files(history_dir: str) -> Iterable[str]:
    """仅遍历 history_data 根目录下的文件，不进入子目录。"""
    if not os.path.isdir(history_dir):
        return
    try:
        names = os.listdir(history_dir)
    except OSError:
        return
    for fn in names:
        if fn == ARCHIVE_SUBDIR_NAME:
            continue
        path = os.path.join(history_dir, fn)
        if os.path.isfile(path):
            yield path


def archive_history_before(
    history_dir: str,
    keep_on_or_after: date,
) -> Tuple[int, int, List[str]]:
    """
    将 history_data 根目录下日期 < keep_on_or_after 的文件移到 history_data/存档/。
    子目录一律不动。

    返回 (moved, skipped, errors)。
    """
    if not os.path.isdir(history_dir):
        return 0, 0, [f"目录不存在: {history_dir}"]

    arch_root = archive_dir(history_dir)
    os.makedirs(arch_root, exist_ok=True)

    moved = 0
    skipped = 0
    errors: List[str] = []

    for src in list(iter_history_files(history_dir)):
        d = extract_file_date(src)
        if d is None or d >= keep_on_or_after:
            skipped += 1
            continue
        rel = os.path.basename(src)
        dest = os.path.join(arch_root, rel)
        try:
            if os.path.exists(dest):
                os.remove(dest)
            shutil.move(src, dest)
            moved += 1
        except Exception as e:
            errors.append(f"{rel}: {e}")

    return moved, skipped, errors


def restore_subdir_archives(history_dir: str) -> Tuple[int, List[str]]:
    """
    把误归档到 存档/<子目录>/ 的内容移回 history_data/<子目录>/。
    存档根目录下的扁平文件不动（那些才是根目录归档结果）。
    """
    arch_root = archive_dir(history_dir)
    if not os.path.isdir(arch_root):
        return 0, []

    restored = 0
    errors: List[str] = []
    try:
        entries = list(os.listdir(arch_root))
    except OSError as e:
        return 0, [str(e)]

    for name in entries:
        src_dir = os.path.join(arch_root, name)
        if not os.path.isdir(src_dir):
            continue
        dest_dir = os.path.join(history_dir, name)
        os.makedirs(dest_dir, exist_ok=True)
        for root, _dirs, files in os.walk(src_dir):
            for fn in files:
                src = os.path.join(root, fn)
                rel = os.path.relpath(src, src_dir)
                dest = os.path.join(dest_dir, rel)
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    if os.path.exists(dest):
                        # 现行已有同名则删掉归档侧副本，避免重复
                        os.remove(src)
                        continue
                    shutil.move(src, dest)
                    restored += 1
                except Exception as e:
                    errors.append(f"{name}/{rel}: {e}")
        # 清空后的空目录尽量删掉
        for root, dirs, files in os.walk(src_dir, topdown=False):
            if not files and not dirs:
                try:
                    os.rmdir(root)
                except OSError:
                    pass

    return restored, errors


def archive_search_roots(history_dir: str) -> List[str]:
    """
    读取时的查找根：现行目录优先，再存档。
    用于「业务子目录」场景，调用方再拼子路径。
    """
    roots = [history_dir]
    arch = archive_dir(history_dir)
    if os.path.isdir(arch):
        roots.append(arch)
    return roots
