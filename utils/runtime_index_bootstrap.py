# -*- coding: utf-8 -*-
"""从腾讯云 COS 拉取/发布运行底稿（无密钥公共读）。

启动补齐：股票池 / 板块索引 / 日线近窗 zip（缺才拉，不覆盖完整目录）。
朋友机同步：按 friend_sync_meta.json 比较交易日/清单/size/uploaded_at，有变化才拉日线 zip、选股 xls、主力净流入 CSV、沪深京行情 txt；force 只表示允许立刻跑，不强制重下。
主站上传：stage_runtime_for_upload 把上述对象放到 data/cos/runtime/。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

UNIVERSE_NAME = "a_share_universe.json"
INDEX_NAME = "qmt_sector_index.json"
DAILY_CACHE_ZIP_NAME = "daily_cache.zip"
DAILY_CACHE_DIR_NAME = "daily_cache"
RUNTIME_DIR_NAME = "runtime"
FRIEND_SYNC_META_NAME = "friend_sync_meta.json"
STOCK_FILTER_GLOB = "选股结果_*_{date}.xls"
STOCK_FILTER_GLOB_X = "选股结果_*_{date}.xlsx"
CAPITAL_FLOW_DIR_NAME = "个股主力净流入"
CAPITAL_FLOW_NAME_TMPL = "个股主力净流入_{date8}.csv"
QMT_HSJ_QUOTE_NAME = "qmt_hsj_quote.txt"
MIN_QMT_HSJ_QUOTE_SIZE = 10000
HISTORY_DATA_DIR_NAME = "history_data"

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
    name = str(filename or "").lstrip("/").replace("\\", "/")
    quoted = "/".join(quote(seg, safe="") for seg in name.split("/") if seg != "")
    base = "https://%s.cos.%s.myqcloud.com" % (bucket, region)
    parts = [p for p in (prefix, RUNTIME_DIR_NAME, quoted) if p]
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
    force: bool = False,
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
        if (not force) and (not daily_cache_missing(root)):
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
    status, sync_td = local_manifest_sync_info(root)
    if str(status or "").lower() != "completed":
        print("[runtime] skip daily_cache.zip: status=%s (need completed)" % (status or "-"))
        return None
    if not sync_td:
        print("[runtime] skip daily_cache.zip: missing sync_trade_date")
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


def stage_runtime_for_upload(
    project_root: str,
    cos_dir: str,
    *,
    trade_date: Optional[str] = None,
) -> List[str]:
    """把索引 JSON、daily_cache.zip、选股 xls、主力净流入、沪深京行情 txt、meta 放到 data/cos/runtime/。"""
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
    friend_names = stage_friend_payloads(project_root, cos_dir, trade_date=trade_date)
    copied.extend(friend_names)
    return copied


def is_runtime_relpath(rel: str) -> bool:
    norm = str(rel or "").replace("\\", "/").lstrip("/")
    return norm == RUNTIME_DIR_NAME or norm.startswith(RUNTIME_DIR_NAME + "/")


def history_data_dir(root: str) -> str:
    path = os.path.join(os.path.abspath(root or "."), HISTORY_DATA_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def capital_flow_dir(root: str) -> str:
    path = os.path.join(history_data_dir(root), CAPITAL_FLOW_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _norm_trade_date(value: Any) -> str:
    s = str(value or "").strip().replace("/", "-")
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return "%s-%s-%s" % (digits[:4], digits[4:6], digits[6:8])
    return s[:10]


def _date8(trade_date: str) -> str:
    return _norm_trade_date(trade_date).replace("-", "")[:8]


def local_manifest_sync_info(root: str) -> Tuple[str, str]:
    """返回 (status, sync_trade_date YYYY-MM-DD)。"""
    path = os.path.join(daily_cache_dir(root), "manifest.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    status = str(payload.get("status") or "").strip()
    sync = _norm_trade_date(payload.get("sync_trade_date") or payload.get("pending_trade_date"))
    return status, sync


def list_local_stock_filter_files(root: str, trade_date: str) -> List[str]:
    d = _norm_trade_date(trade_date)
    if not d:
        return []
    hd = history_data_dir(root)
    names: List[str] = []
    for pat in (
        STOCK_FILTER_GLOB.format(date=d),
        STOCK_FILTER_GLOB_X.format(date=d),
    ):
        try:
            import glob as _glob

            for p in sorted(_glob.glob(os.path.join(hd, pat))):
                if os.path.isfile(p) and os.path.getsize(p) > 0:
                    names.append(os.path.basename(p))
        except Exception:
            continue
    return names


def local_capital_flow_path(root: str, trade_date: str) -> str:
    name = CAPITAL_FLOW_NAME_TMPL.format(date8=_date8(trade_date))
    return os.path.join(capital_flow_dir(root), name)


def local_qmt_hsj_quote_path(root: str) -> str:
    return os.path.join(project_data_dir(root), QMT_HSJ_QUOTE_NAME)


def _file_ok(path: str, *, min_size: int = 1) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= int(min_size)
    except OSError:
        return False


def verify_friend_local(
    root: str,
    trade_date: str,
    *,
    expect_stock_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """朋友机本地是否齐：日线、选股、资金流、沪深京行情 txt。"""
    td = _norm_trade_date(trade_date)
    status, sync = local_manifest_sync_info(root)
    daily_ok = (
        str(status).lower() == "completed"
        and sync == td
        and count_daily_cache_csv(root) >= MIN_DAILY_CACHE_CSV
    )
    expect = [str(n).strip() for n in (expect_stock_names or []) if str(n).strip()]
    if expect:
        missing = [
            n for n in expect
            if not _file_ok(os.path.join(history_data_dir(root), n))
        ]
        stock_names = [n for n in expect if n not in missing]
        stock_ok = not missing
    else:
        stock_names = list_local_stock_filter_files(root, td)
        stock_ok = len(stock_names) >= 1
    cf_path = local_capital_flow_path(root, td)
    cf_ok = _file_ok(cf_path, min_size=20)
    quote_path = local_qmt_hsj_quote_path(root)
    quote_ok = _file_ok(quote_path, min_size=MIN_QMT_HSJ_QUOTE_SIZE)
    ok = bool(daily_ok and stock_ok and cf_ok and quote_ok)
    parts = [
        "日线=%s(status=%s sync=%s)"
        % ("OK" if daily_ok else "缺", status or "-", sync or "-"),
        "选股=%s(%d)" % ("OK" if stock_ok else "缺", len(stock_names)),
        "资金流=%s" % ("OK" if cf_ok else "缺"),
        "行情=%s" % ("OK" if quote_ok else "缺"),
    ]
    return {
        "ok": ok,
        "trade_date": td,
        "daily_ok": daily_ok,
        "stock_ok": stock_ok,
        "capital_flow_ok": cf_ok,
        "qmt_hsj_quote_ok": quote_ok,
        "stock_files": stock_names,
        "capital_flow_file": os.path.basename(cf_path) if cf_ok else "",
        "qmt_hsj_quote_file": QMT_HSJ_QUOTE_NAME if quote_ok else "",
        "message": (
            "COS同步成功 %s：%s" % (td, "；".join(parts))
            if ok
            else "COS同步未齐 %s：%s" % (td or "-", "；".join(parts))
        ),
    }



def stage_friend_payloads(
    project_root: str,
    cos_dir: str,
    *,
    trade_date: Optional[str] = None,
) -> List[str]:
    """把当日选股 xls、主力净流入 CSV、沪深京行情 txt、friend_sync_meta.json 放到 runtime/。"""
    root = os.path.abspath(project_root or ".")
    runtime = os.path.join(os.path.abspath(cos_dir), RUNTIME_DIR_NAME)
    os.makedirs(runtime, exist_ok=True)
    status, sync = local_manifest_sync_info(root)
    td = _norm_trade_date(trade_date) or sync
    if not td:
        td = datetime.now().strftime("%Y-%m-%d")
    staged: List[str] = []
    stock_files = list_local_stock_filter_files(root, td)
    for name in stock_files:
        src = os.path.join(history_data_dir(root), name)
        dest = os.path.join(runtime, name)
        try:
            shutil.copy2(src, dest)
            staged.append(name)
            print("[runtime] staged", dest)
        except Exception as e:
            print("[runtime] skip stock_filter", name, e)
    cf_src = local_capital_flow_path(root, td)
    cf_name = os.path.basename(cf_src)
    if _file_ok(cf_src, min_size=20):
        try:
            shutil.copy2(cf_src, os.path.join(runtime, cf_name))
            staged.append(cf_name)
            print("[runtime] staged", os.path.join(runtime, cf_name))
        except Exception as e:
            print("[runtime] skip capital_flow", cf_name, e)
            cf_name = ""
    else:
        print("[runtime] skip capital_flow missing", cf_src)
        cf_name = ""
    quote_src = local_qmt_hsj_quote_path(root)
    quote_name = QMT_HSJ_QUOTE_NAME
    if _file_ok(quote_src, min_size=MIN_QMT_HSJ_QUOTE_SIZE):
        try:
            shutil.copy2(quote_src, os.path.join(runtime, quote_name))
            staged.append(quote_name)
            print("[runtime] staged", os.path.join(runtime, quote_name))
        except Exception as e:
            print("[runtime] skip qmt_hsj_quote", quote_name, e)
            quote_name = ""
    else:
        print("[runtime] skip qmt_hsj_quote missing", quote_src)
        quote_name = ""
    meta = {
        "version": 1,
        "trade_date": td,
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "daily_cache": {
            "object": DAILY_CACHE_ZIP_NAME,
            "sync_trade_date": sync or td,
            "status": status or "",
        },
        "stock_filter": [
            {"name": n, "size": int(os.path.getsize(os.path.join(runtime, n)))}
            for n in stock_files
            if _file_ok(os.path.join(runtime, n))
        ],
        "capital_flow": (
            {"name": cf_name, "size": int(os.path.getsize(os.path.join(runtime, cf_name)))}
            if cf_name and _file_ok(os.path.join(runtime, cf_name))
            else None
        ),
        "qmt_hsj_quote": (
            {
                "name": quote_name,
                "size": int(os.path.getsize(os.path.join(runtime, quote_name))),
            }
            if quote_name and _file_ok(os.path.join(runtime, quote_name), min_size=MIN_QMT_HSJ_QUOTE_SIZE)
            else None
        ),
    }
    meta_path = os.path.join(runtime, FRIEND_SYNC_META_NAME)
    tmp = meta_path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    _atomic_place(tmp, meta_path)
    staged.append(FRIEND_SYNC_META_NAME)
    print("[runtime] staged", meta_path, "trade_date=%s stocks=%d cf=%s quote=%s" % (
        td, len(stock_files), cf_name or "-", quote_name or "-"
    ))
    return staged


def _load_json_file(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _friend_meta_sig(meta: Optional[Dict[str, Any]]) -> str:
    """远端/本地 meta 可比指纹：交易日、上传时间、各对象清单与 size。"""
    if not isinstance(meta, dict):
        return ""
    payload = {
        "trade_date": _norm_trade_date(meta.get("trade_date")),
        "uploaded_at": str(meta.get("uploaded_at") or "").strip(),
        "daily_cache": meta.get("daily_cache") if isinstance(meta.get("daily_cache"), dict) else {},
        "stock_filter": meta.get("stock_filter") if isinstance(meta.get("stock_filter"), list) else [],
        "capital_flow": meta.get("capital_flow") if isinstance(meta.get("capital_flow"), dict) else None,
        "qmt_hsj_quote": meta.get("qmt_hsj_quote") if isinstance(meta.get("qmt_hsj_quote"), dict) else None,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _local_size_matches(path: str, expect_size: Any, *, min_size: int = 1) -> bool:
    if not _file_ok(path, min_size=min_size):
        return False
    if expect_size is None:
        return True
    try:
        return os.path.getsize(path) == int(expect_size)
    except (OSError, TypeError, ValueError):
        return False


def fetch_friend_sync_meta(
    root: str,
    *,
    should_abort: Optional[Callable[[], bool]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """下载 friend_sync_meta.json，返回 (meta, err)。"""
    data_dir = project_data_dir(root)
    tmp = os.path.join(data_dir, FRIEND_SYNC_META_NAME + ".part")
    try:
        url = public_object_url(FRIEND_SYNC_META_NAME)
        err = _download_url(url, tmp, timeout_sec=60.0, should_abort=should_abort)
        if err:
            return None, err
        payload = _load_json_file(tmp)
        if not isinstance(payload, dict):
            return None, "meta 不是 JSON 对象"
        td = _norm_trade_date(payload.get("trade_date"))
        if not td:
            return None, "meta 缺少 trade_date"
        payload["trade_date"] = td
        # 落一份本地副本便于排查
        dest = os.path.join(data_dir, FRIEND_SYNC_META_NAME)
        try:
            _atomic_place(tmp, dest)
        except Exception:
            pass
        return payload, None
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _remote_newer_or_missing(local_sync: str, remote_sync: str) -> bool:
    loc = _date8(local_sync)
    rem = _date8(remote_sync)
    if not rem:
        return False
    if not loc:
        return True
    return rem > loc


def sync_friend_runtime(
    root: str,
    *,
    force: bool = False,
    should_abort: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """朋友机：按远端 meta 与本地比较后按需拉取日线/选股/资金流/沪深京行情。

    force 保留给启动器语义（忽略今晚已 OK、窗口外立刻跑），不再表示无视本地强下大文件。
    仍会轻量拉取 friend_sync_meta.json；trade_date / uploaded_at / 对象清单与 size
    未变且本地 verify 已齐时跳过各大数据对象下载。
    """
    _ = bool(force)  # API 兼容；下载决策不依赖 force
    result: Dict[str, Any] = {
        "ok": False,
        "fetched": [],
        "skipped": [],
        "errors": {},
        "aborted": False,
        "trade_date": "",
        "message": "",
        "verify": {},
    }
    if on_progress:
        on_progress("正在检查 COS 同步元数据…")
    data_dir = project_data_dir(root)
    prev_meta = _load_json_file(os.path.join(data_dir, FRIEND_SYNC_META_NAME))
    meta, meta_err = fetch_friend_sync_meta(root, should_abort=should_abort)
    if meta_err:
        result["errors"]["meta"] = meta_err
        result["message"] = "COS同步失败：无法读取远端 meta（%s）" % meta_err
        return result
    assert meta is not None
    td = _norm_trade_date(meta.get("trade_date"))
    result["trade_date"] = td
    stock_meta = meta.get("stock_filter") or []
    expect_stocks = []
    stock_size_by_name: Dict[str, Any] = {}
    if isinstance(stock_meta, list):
        for item in stock_meta:
            if isinstance(item, dict) and item.get("name"):
                name = str(item["name"])
                expect_stocks.append(name)
                if "size" in item:
                    stock_size_by_name[name] = item.get("size")
            elif isinstance(item, str) and item.strip():
                expect_stocks.append(item.strip())
    cf_meta = meta.get("capital_flow") if isinstance(meta.get("capital_flow"), dict) else {}
    cf_name = str((cf_meta or {}).get("name") or "").strip()
    if not cf_name:
        cf_name = CAPITAL_FLOW_NAME_TMPL.format(date8=_date8(td))
    quote_meta = meta.get("qmt_hsj_quote") if isinstance(meta.get("qmt_hsj_quote"), dict) else {}
    quote_name = str((quote_meta or {}).get("name") or "").strip() or QMT_HSJ_QUOTE_NAME

    # meta 指纹未变且本地已齐：只耗 meta 流量，大文件全部跳过
    verify_now = verify_friend_local(root, td, expect_stock_names=expect_stocks or None)
    meta_unchanged = bool(_friend_meta_sig(prev_meta)) and (
        _friend_meta_sig(prev_meta) == _friend_meta_sig(meta)
    )
    if meta_unchanged and bool(verify_now.get("ok")):
        result["skipped"].extend(
            [DAILY_CACHE_ZIP_NAME, "stock_filter", "capital_flow", "qmt_hsj_quote"]
        )
        result["verify"] = verify_now
        result["ok"] = True
        base = str(verify_now.get("message") or "COS同步成功")
        result["message"] = base + "（本地已是最新，未重复下载）"
        return result

    local_status, local_sync = local_manifest_sync_info(root)
    daily_meta = meta.get("daily_cache") if isinstance(meta.get("daily_cache"), dict) else {}
    remote_daily_sync = _norm_trade_date(
        (daily_meta or {}).get("sync_trade_date") or td
    )
    need_daily = _remote_newer_or_missing(local_sync, remote_daily_sync or td)
    if not need_daily:
        # 同日但未完成 / CSV 不够也要拉
        if str(local_status).lower() != "completed" or daily_cache_missing(root):
            need_daily = True
    # 选股 / 资金流 / 行情：缺文件、size 与 meta 不符、或交易日更新才拉（不因 force 强下）
    need_stock = not bool(expect_stocks)
    if expect_stocks:
        hd = history_data_dir(root)
        for n in expect_stocks:
            expect_sz = stock_size_by_name.get(n)
            if not _local_size_matches(
                os.path.join(hd, n), expect_sz, min_size=1
            ):
                need_stock = True
                break
        else:
            need_stock = False
    cf_path = os.path.join(capital_flow_dir(root), cf_name)
    need_cf = not _local_size_matches(
        cf_path, (cf_meta or {}).get("size"), min_size=20
    )
    need_quote = not _local_size_matches(
        local_qmt_hsj_quote_path(root),
        (quote_meta or {}).get("size"),
        min_size=MIN_QMT_HSJ_QUOTE_SIZE,
    )
    if _remote_newer_or_missing(local_sync, td):
        # 交易日推进时即使旧文件尚在，也按新日清单重拉配套文件
        need_stock = True
        need_cf = True
        need_quote = True

    if not need_daily:
        result["skipped"].append(DAILY_CACHE_ZIP_NAME)
    if not need_stock:
        result["skipped"].append("stock_filter")
    if not need_cf:
        result["skipped"].append("capital_flow")
    if not need_quote:
        result["skipped"].append("qmt_hsj_quote")

    if need_daily:
        if on_progress:
            on_progress("正在下载并覆盖日线缓存…")
        err = _fetch_daily_cache_zip(
            root,
            should_abort=should_abort,
            on_progress=on_progress,
            force=True,
        )
        if err == "aborted":
            result["aborted"] = True
            result["message"] = "COS同步已中止"
            return result
        if err:
            result["errors"][DAILY_CACHE_ZIP_NAME] = err
        else:
            result["fetched"].append(DAILY_CACHE_ZIP_NAME)

    if need_stock and expect_stocks:
        hd = history_data_dir(root)
        for name in expect_stocks:
            if should_abort and should_abort():
                result["aborted"] = True
                result["message"] = "COS同步已中止"
                return result
            if on_progress:
                on_progress("正在获取选股 %s …" % name)
            tmp = os.path.join(hd, name + ".part")
            try:
                url = public_object_url(name)
                dl_err = _download_url(url, tmp, timeout_sec=180.0, should_abort=should_abort)
                if dl_err == "aborted":
                    result["aborted"] = True
                    result["message"] = "COS同步已中止"
                    return result
                if dl_err:
                    result["errors"][name] = dl_err
                    continue
                if not _file_ok(tmp, min_size=20):
                    result["errors"][name] = "下载为空"
                    continue
                _atomic_place(tmp, os.path.join(hd, name))
                result["fetched"].append(name)
            except Exception as e:
                result["errors"][name] = str(e) or type(e).__name__
            finally:
                try:
                    if os.path.isfile(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
    elif need_stock and not expect_stocks:
        result["errors"]["stock_filter"] = "远端 meta 未声明选股文件"

    if need_cf and cf_name:
        if on_progress:
            on_progress("正在获取主力净流入 %s …" % cf_name)
        dest_dir = capital_flow_dir(root)
        tmp = os.path.join(dest_dir, cf_name + ".part")
        try:
            url = public_object_url(cf_name)
            dl_err = _download_url(url, tmp, timeout_sec=180.0, should_abort=should_abort)
            if dl_err == "aborted":
                result["aborted"] = True
                result["message"] = "COS同步已中止"
                return result
            if dl_err:
                result["errors"][cf_name] = dl_err
            elif not _file_ok(tmp, min_size=20):
                result["errors"][cf_name] = "下载为空"
            else:
                _atomic_place(tmp, os.path.join(dest_dir, cf_name))
                result["fetched"].append(cf_name)
        except Exception as e:
            result["errors"][cf_name] = str(e) or type(e).__name__
        finally:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    if need_quote and quote_name:
        if on_progress:
            on_progress("正在获取沪深京行情 %s …" % quote_name)
        dest = local_qmt_hsj_quote_path(root)
        tmp = dest + ".part"
        try:
            url = public_object_url(quote_name)
            dl_err = _download_url(url, tmp, timeout_sec=180.0, should_abort=should_abort)
            if dl_err == "aborted":
                result["aborted"] = True
                result["message"] = "COS同步已中止"
                return result
            if dl_err:
                result["errors"][quote_name] = dl_err
            elif not _file_ok(tmp, min_size=MIN_QMT_HSJ_QUOTE_SIZE):
                result["errors"][quote_name] = "下载过小或为空"
            else:
                _atomic_place(tmp, dest)
                result["fetched"].append(quote_name)
        except Exception as e:
            result["errors"][quote_name] = str(e) or type(e).__name__
        finally:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    verify = verify_friend_local(root, td, expect_stock_names=expect_stocks or None)
    result["verify"] = verify
    result["ok"] = bool(verify.get("ok")) and not result["errors"]
    if result["ok"]:
        base = str(verify.get("message") or "COS同步成功")
        if not result["fetched"]:
            result["message"] = base + "（本地已是最新，未重复下载）"
        else:
            result["message"] = base
    else:
        # 有错误或校验未齐
        err_tip = ""
        if result["errors"]:
            first_k = next(iter(result["errors"]))
            err_tip = "%s=%s" % (first_k, result["errors"][first_k])
        base = str(verify.get("message") or "COS同步未齐")
        result["message"] = ("%s；%s" % (base, err_tip)).strip("；")
    return result

