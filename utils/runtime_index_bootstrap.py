# -*- coding: utf-8 -*-
"""新机缺失时从腾讯云 COS 拉取股票池 / 板块索引 / 日线近窗底稿（无密钥，公共读）。

已有完整本地文件一律不覆盖。策略 / 选股规则 JSON 不在此列。
日线是整包 zip，不是逐票对象。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

UNIVERSE_NAME = "a_share_universe.json"
INDEX_NAME = "qmt_sector_index.json"
DAILY_CACHE_ZIP_NAME = "daily_cache.zip"
DAILY_CACHE_DIR_NAME = "daily_cache"
RUNTIME_DIR_NAME = "runtime"

_DEFAULT_BUCKET = "ant-quant-data-1428892855"
_DEFAULT_REGION = "ap-guangzhou"
_DEFAULT_PREFIX = "cos"

_ABORT = object()

MIN_UNIVERSE_CODES = 1000
MIN_INDEX_STOCKS = 500
MIN_UI_SECTORS = 30
MIN_DAILY_CACHE_CSV = 2000
_CSV_NAME_RE = re.compile(r"^[0-9]{6}\.(SZ|SH|BJ)\.csv$", re.IGNORECASE)


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


def daily_cache_dir(root: str) -> str:
    return os.path.join(project_data_dir(root), DAILY_CACHE_DIR_NAME)


def _is_daily_csv_name(name: str) -> bool:
    base = os.path.basename(str(name or "")).replace("\\", "/")
    base = base.split("/")[-1]
    return bool(_CSV_NAME_RE.match(base))


def count_daily_cache_csv(root: str) -> int:
    path = daily_cache_dir(root)
    n = 0
    try:
        with os.scandir(path) as it:
            for ent in it:
                if ent.is_file() and _is_daily_csv_name(ent.name):
                    n += 1
    except OSError:
        return 0
    return n


def daily_cache_missing(root: str) -> bool:
    """无 manifest、或 CSV 明显不够，才需要从 COS 拉整包。已有完整目录不覆盖。"""
    path = daily_cache_dir(root)
    manifest = os.path.join(path, "manifest.json")
    try:
        if (not os.path.isfile(manifest)) or os.path.getsize(manifest) <= 0:
            return True
    except OSError:
        return True
    return count_daily_cache_csv(root) < MIN_DAILY_CACHE_CSV


def missing_runtime(root: str) -> List[str]:
    names = missing_files(root)
    if daily_cache_missing(root):
        names.append(DAILY_CACHE_ZIP_NAME)
    return names


def _file_missing(path: str) -> bool:
    try:
        if (not os.path.isfile(path)) or os.path.getsize(path) <= 0:
            return True
    except OSError:
        return True
    name = os.path.basename(path)
    validator = _validator_for(name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return True
    return validator(payload) is not None


def missing_files(root: str) -> List[str]:
    return [name for name, path in dest_paths(root).items() if _file_missing(path)]


def validate_universe_payload(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return "股票池不是 JSON 对象"
    codes = payload.get("codes")
    if not isinstance(codes, list) or not codes:
        return "股票池缺少 codes"
    if len(codes) < MIN_UNIVERSE_CODES:
        return "股票池过少（%d < %d，疑似空底稿）" % (len(codes), MIN_UNIVERSE_CODES)
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
    real = 0
    for s in sectors:
        name = str(s or "")
        if name.startswith(("SW1", "GN", "SW2", "SW3")):
            real += 1
    if real < MIN_UI_SECTORS:
        return "板块过少（%d < %d，疑似空底稿）" % (real, MIN_UI_SECTORS)
    if len(mapping) < MIN_INDEX_STOCKS:
        return "板块成分过少（%d < %d，疑似空底稿）" % (len(mapping), MIN_INDEX_STOCKS)
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
    import ssl
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": "ant-quant-launcher"})
    contexts = [ssl.create_default_context()]
    try:
        contexts.append(ssl._create_unverified_context())
    except Exception:
        pass
    last_err = None
    for i, ctx in enumerate(contexts):
        try:
            with urlopen(req, timeout=timeout_sec, context=ctx) as resp:
                status = getattr(resp, "status", None) or getattr(resp, "code", None)
                if status not in (None, 200):
                    last_err = "HTTP %s" % status
                    continue
                with open(dest_tmp, "wb") as out:
                    while True:
                        if should_abort and should_abort():
                            return "aborted"
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
            return None
        except Exception as e:
            last_err = str(e) or type(e).__name__
            continue
    return last_err


def _atomic_place(src: str, dest: str) -> None:
    """写入目标盘上的 dest。

    先复制到 dest 同目录的 .installing，再 replace。
    不要对跨盘的 C:\\Temp 源文件直接 os.replace（WinError 17）。
    """
    dest_dir = os.path.dirname(dest) or "."
    os.makedirs(dest_dir, exist_ok=True)
    staging = dest + ".installing"
    try:
        shutil.copy2(src, staging)
        os.replace(staging, dest)
    except Exception:
        try:
            if os.path.isfile(staging):
                os.remove(staging)
        except OSError:
            pass
        raise


def _install_download(tmp_path: str, dest: str, name: str) -> Optional[str]:
    try:
        with open(tmp_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return "无法解析 JSON：%s" % e
    err = _validator_for(name)(payload)
    if err:
        return err
    try:
        _atomic_place(tmp_path, dest)
    except Exception as e:
        return "写入失败：%s" % e
    return None


def _validate_daily_cache_zip(path: str) -> Optional[str]:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            csv_n = 0
            has_manifest = False
            for info in zf.infolist():
                if info.is_dir():
                    continue
                raw = str(info.filename or "").replace("\\", "/")
                if raw.startswith("/") or ".." in raw.split("/"):
                    return "zip 含非法路径"
                base = raw.split("/")[-1]
                if base.lower() == "manifest.json":
                    has_manifest = True
                elif _is_daily_csv_name(base):
                    csv_n += 1
            if not has_manifest:
                return "zip 缺少 manifest.json"
            if csv_n < MIN_DAILY_CACHE_CSV:
                return "zip 内日线过少（%d < %d）" % (csv_n, MIN_DAILY_CACHE_CSV)
    except zipfile.BadZipFile as e:
        return "不是有效 zip：%s" % e
    except Exception as e:
        return "无法读取 zip：%s" % e
    return None


def _cache_dir_complete(dest_dir: str) -> bool:
    manifest = os.path.join(dest_dir, "manifest.json")
    try:
        if (not os.path.isfile(manifest)) or os.path.getsize(manifest) <= 0:
            return False
    except OSError:
        return False
    n = 0
    try:
        with os.scandir(dest_dir) as it:
            for ent in it:
                if ent.is_file() and _is_daily_csv_name(ent.name):
                    n += 1
                    if n >= MIN_DAILY_CACHE_CSV:
                        return True
    except OSError:
        return False
    return False


def _install_daily_cache_zip(
    zip_path: str,
    dest_dir: str,
    *,
    should_abort: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    err = _validate_daily_cache_zip(zip_path)
    if err:
        return err
    os.makedirs(dest_dir, exist_ok=True)
    written = 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [info for info in zf.infolist() if not info.is_dir()]
            total = len(members) or 1
            for i, info in enumerate(members, 1):
                if should_abort and should_abort():
                    return "aborted"
                raw = str(info.filename or "").replace("\\", "/")
                base = raw.split("/")[-1]
                if base.lower() == "manifest.json":
                    out_name = "manifest.json"
                elif _is_daily_csv_name(base):
                    out_name = base
                else:
                    continue
                dest = os.path.join(dest_dir, out_name)
                part = dest + ".part"
                with zf.open(info, "r") as src, open(part, "wb") as out:
                    shutil.copyfileobj(src, out, 256 * 1024)
                os.replace(part, dest)
                written += 1
                if on_progress and (i == 1 or i % 800 == 0 or i == total):
                    on_progress("正在解压日线缓存 %d/%d …" % (i, total))
    except Exception as e:
        return "解压失败：%s" % e
    if not _cache_dir_complete(dest_dir):
        return "解压后仍不完整（写入 %d 个文件）" % written
    return None


def _fetch_daily_cache_zip(
    root: str,
    *,
    should_abort: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    dest_dir = daily_cache_dir(root)
    tmp_path = os.path.join(project_data_dir(root), DAILY_CACHE_ZIP_NAME + ".part")
    try:
        if on_progress:
            on_progress("正在获取日线缓存包（约数十MB）…")
        url = public_object_url(DAILY_CACHE_ZIP_NAME)
        dl_err = _download_url(
            url, tmp_path, timeout_sec=600.0, should_abort=should_abort
        )
        if dl_err:
            return dl_err
        if not daily_cache_missing(root):
            return None
        return _install_daily_cache_zip(
            tmp_path,
            dest_dir,
            should_abort=should_abort,
            on_progress=on_progress,
        )
    finally:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def stage_daily_cache_zip(project_root: str, cos_dir: str) -> Optional[str]:
    """把 data/daily_cache 打成 runtime/daily_cache.zip，供 COS 覆盖上传。"""
    root = os.path.abspath(project_root or ".")
    if daily_cache_missing(root):
        print("[runtime] skip daily_cache.zip: local cache thin or missing")
        return None
    cache = daily_cache_dir(root)
    runtime = os.path.join(os.path.abspath(cos_dir), RUNTIME_DIR_NAME)
    os.makedirs(runtime, exist_ok=True)
    dest = os.path.join(runtime, DAILY_CACHE_ZIP_NAME)
    tmp_path = dest + ".part"
    n_csv = 0
    try:
        with zipfile.ZipFile(
            tmp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=1,
        ) as zf:
            zf.write(os.path.join(cache, "manifest.json"), arcname="manifest.json")
            with os.scandir(cache) as it:
                for ent in it:
                    if not ent.is_file() or not _is_daily_csv_name(ent.name):
                        continue
                    zf.write(ent.path, arcname=ent.name)
                    n_csv += 1
        if n_csv < MIN_DAILY_CACHE_CSV:
            raise RuntimeError("packed csv too few: %d" % n_csv)
        _atomic_place(tmp_path, dest)
    except Exception as e:
        print("[runtime] pack daily_cache.zip failed:", e)
        return None
    finally:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
    print("[runtime] staged", dest, "csv=%d size=%d" % (n_csv, os.path.getsize(dest)))
    return DAILY_CACHE_ZIP_NAME


def fetch_missing(
    root: str,
    *,
    should_abort: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    include_daily_cache: bool = False,
) -> Dict[str, Any]:
    """仅补本地缺失文件。返回 fetched / skipped / errors。"""
    needed = missing_files(root)
    want_cache = bool(include_daily_cache) and daily_cache_missing(root)
    result: Dict[str, Any] = {
        "fetched": [],
        "skipped": [n for n in dest_paths(root) if n not in needed],
        "errors": {},
        "aborted": False,
    }
    if not want_cache:
        result["skipped"].append(DAILY_CACHE_ZIP_NAME)
    if not needed and not want_cache:
        return result

    paths = dest_paths(root)
    for name in needed:
        tmp_path = ""
        try:
            if should_abort and should_abort():
                result["aborted"] = True
                break
            dest = paths[name]
            if not _file_missing(dest):
                result["skipped"].append(name)
                continue
            if on_progress:
                on_progress("正在获取 %s …" % name)
            url = public_object_url(name)
            tmp_path = dest + ".part"
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
        except Exception as e:
            result["errors"][name] = str(e) or type(e).__name__
        finally:
            if tmp_path:
                try:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass
    if result.get("aborted"):
        return result
    if want_cache and daily_cache_missing(root):
        try:
            cache_err = _fetch_daily_cache_zip(
                root, should_abort=should_abort, on_progress=on_progress
            )
            if cache_err == "aborted":
                result["aborted"] = True
            elif cache_err:
                result["errors"][DAILY_CACHE_ZIP_NAME] = cache_err
            else:
                result["fetched"].append(DAILY_CACHE_ZIP_NAME)
        except Exception as e:
            result["errors"][DAILY_CACHE_ZIP_NAME] = str(e) or type(e).__name__
    return result


def summarize_fetch(result: Dict[str, Any]) -> Tuple[bool, str]:
    """(是否算成功完成启动补齐, 短提示)。缺文件且全失败才算失败。"""
    if result.get("aborted"):
        return True, ""
    fetched = list(result.get("fetched") or [])
    errors = dict(result.get("errors") or {})
    has_cache = DAILY_CACHE_ZIP_NAME in fetched
    has_json = any(n != DAILY_CACHE_ZIP_NAME for n in fetched)
    if fetched and not errors:
        if has_cache and has_json:
            return True, "已从云端补齐股票池、板块索引与日线缓存。"
        if has_cache:
            return True, "已从云端补齐日线缓存。"
        return True, "已从云端补齐股票池与板块索引。"
    if fetched and errors:
        return True, "已补齐部分底稿，其余稍后可由大 QMT 盘后同步。"
    if errors:
        first = next(iter(errors.values()))
        return False, "未能从云端获取运行底稿（%s）。" % first
    return True, ""


def stage_runtime_for_upload(project_root: str, cos_dir: str) -> List[str]:
    """把索引 JSON 和 daily_cache.zip 放到 data/cos/runtime/，供上传；不进离线 zip。"""
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
    packed = stage_daily_cache_zip(project_root, cos_dir)
    if packed:
        copied.append(packed)
    return copied


def is_runtime_relpath(rel: str) -> bool:
    norm = str(rel or "").replace("\\", "/").lstrip("/")
    return norm == RUNTIME_DIR_NAME or norm.startswith(RUNTIME_DIR_NAME + "/")
