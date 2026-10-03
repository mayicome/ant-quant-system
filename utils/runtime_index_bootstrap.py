# -*- coding: utf-8 -*-
"""新机缺失时从腾讯云 COS 拉取股票池 / 板块索引底稿（无密钥，公共读）。

已有本地文件一律不覆盖。策略 / 选股规则 JSON 不在此列。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

UNIVERSE_NAME = "a_share_universe.json"
INDEX_NAME = "qmt_sector_index.json"
RUNTIME_DIR_NAME = "runtime"

_DEFAULT_BUCKET = "ant-quant-data-1428892855"
_DEFAULT_REGION = "ap-guangzhou"
_DEFAULT_PREFIX = "cos"

_ABORT = object()


def project_data_dir(root: str) -> str:
    path = os.path.join(os.path.abspath(root or "."), "data")
    os.makedirs(path, exist_ok=True)
    return path


def dest_paths(root: str) -> Dict[str, str]:
    data = project_data_dir(root)
    return {
        UNIVERSE_NAME: os.path.join(data, UNIVERSE_NAME),
        INDEX_NAME: os.path.join(data, INDEX_NAME),
    }


def public_object_url(filename: str) -> str:
    bucket = (os.environ.get("COS_BUCKET") or _DEFAULT_BUCKET).strip()
    region = (os.environ.get("COS_REGION") or _DEFAULT_REGION).strip()
    prefix = (os.environ.get("COS_PREFIX") or _DEFAULT_PREFIX).strip().strip("/")
    name = str(filename or "").lstrip("/")
    base = "https://%s.cos.%s.myqcloud.com" % (bucket, region)
    parts = [p for p in (prefix, RUNTIME_DIR_NAME, name) if p]
    return "%s/%s" % (base, "/".join(parts))


def _file_missing(path: str) -> bool:
    try:
        return (not os.path.isfile(path)) or os.path.getsize(path) <= 0
    except OSError:
        return True


def missing_files(root: str) -> List[str]:
    return [name for name, path in dest_paths(root).items() if _file_missing(path)]


def validate_universe_payload(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return "股票池不是 JSON 对象"
    codes = payload.get("codes")
    if not isinstance(codes, list) or not codes:
        return "股票池缺少 codes"
    return None


def validate_index_payload(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return "板块索引不是 JSON 对象"
    sectors = payload.get("ui_sectors")
    mapping = payload.get("code_sectors")
    if not isinstance(sectors, list) or not sectors:
        return "板块索引缺少 ui_sectors"
    if not isinstance(mapping, dict) or not mapping:
        return "板块索引缺少 code_sectors"
    return None


def _validator_for(name: str):
    if name == UNIVERSE_NAME:
        return validate_universe_payload
    if name == INDEX_NAME:
        return validate_index_payload
    return lambda _p: "未知文件"


def _download_url(
    url: str,
    dest_tmp: str,
    *,
    timeout_sec: float = 120.0,
    should_abort: Optional[Callable[[], bool]] = None,
) -> Optional[str]:
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": "ant-quant-launcher"})
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", None)
            if status not in (None, 200):
                return "HTTP %s" % status
            with open(dest_tmp, "wb") as out:
                while True:
                    if should_abort and should_abort():
                        return "aborted"
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    except Exception as e:
        return str(e) or type(e).__name__
    return None


def _install_download(tmp_path: str, dest: str, name: str) -> Optional[str]:
    try:
        with open(tmp_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return "无法解析 JSON：%s" % e
    err = _validator_for(name)(payload)
    if err:
        return err
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.replace(tmp_path, dest)
    return None


def fetch_missing(
    root: str,
    *,
    should_abort: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """仅补本地缺失文件。返回 fetched / skipped / errors。"""
    needed = missing_files(root)
    result: Dict[str, Any] = {
        "fetched": [],
        "skipped": [
            n for n in dest_paths(root) if n not in needed
        ],
        "errors": {},
        "aborted": False,
    }
    if not needed:
        return result

    paths = dest_paths(root)
    tmp_dir = tempfile.mkdtemp(prefix="ant_runtime_idx_")
    try:
        for name in needed:
            if should_abort and should_abort():
                result["aborted"] = True
                break
            dest = paths[name]
            # 下载过程中若另一进程已写入，仍不覆盖
            if not _file_missing(dest):
                result["skipped"].append(name)
                continue
            if on_progress:
                on_progress("正在获取 %s …" % name)
            url = public_object_url(name)
            tmp_path = os.path.join(tmp_dir, name + ".part")
            dl_err = _download_url(url, tmp_path, should_abort=should_abort)
            if dl_err == "aborted":
                result["aborted"] = True
                break
            if dl_err:
                result["errors"][name] = dl_err
                continue
            if not _file_missing(dest):
                result["skipped"].append(name)
                continue
            inst_err = _install_download(tmp_path, dest, name)
            if inst_err:
                result["errors"][name] = inst_err
            else:
                result["fetched"].append(name)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return result


def summarize_fetch(result: Dict[str, Any]) -> Tuple[bool, str]:
    """(是否算成功完成启动补齐, 短提示)。缺文件且全失败才算失败。"""
    if result.get("aborted"):
        return True, ""
    fetched = list(result.get("fetched") or [])
    errors = dict(result.get("errors") or {})
    if fetched and not errors:
        return True, "已从云端补齐股票池与板块索引。"
    if fetched and errors:
        return True, "已补齐部分索引，其余稍后可由大 QMT 盘后同步。"
    if errors:
        first = next(iter(errors.values()))
        return False, "未能从云端获取板块索引（%s）。" % first
    return True, ""


def stage_runtime_for_upload(project_root: str, cos_dir: str) -> List[str]:
    """把 data/ 下两份索引拷到 data/cos/runtime/，供上传；不进离线 zip。"""
    copied: List[str] = []
    src_map = dest_paths(project_root)
    runtime = os.path.join(os.path.abspath(cos_dir), RUNTIME_DIR_NAME)
    os.makedirs(runtime, exist_ok=True)
    for name, src in src_map.items():
        if _file_missing(src):
            print("[runtime] skip missing", src)
            continue
        dest = os.path.join(runtime, name)
        shutil.copy2(src, dest)
        copied.append(name)
        print("[runtime] staged", dest)
    return copied


def is_runtime_relpath(rel: str) -> bool:
    norm = str(rel or "").replace("\\", "/").lstrip("/")
    return norm == RUNTIME_DIR_NAME or norm.startswith(RUNTIME_DIR_NAME + "/")
