# -*- coding: utf-8 -*-
"""同步 data/cos/ 到腾讯云 COS（智能体数据）。

默认：
  本地 data/cos/**  →  cos://{bucket}/cos/**
  可选打包并上传离线包 ant-quant-data.zip → 桶根目录

密钥（勿写入仓库）：
  环境变量 COS_SECRET_ID / COS_SECRET_KEY
  或本地配置 config/cos_upload.local.json（已 gitignore）

用法：
  python tools/upload_cos_data.py
  python tools/upload_cos_data.py --dry-run
  python tools/upload_cos_data.py --with-zip
  python tools/upload_cos_data.py --force
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOCAL_COS_DIR = ROOT / "data" / "cos"
ZIP_PATH = ROOT / "ant-quant-data.zip"
LOCAL_CONFIG = ROOT / "config" / "cos_upload.local.json"
CONFIG_EXAMPLE = ROOT / "config" / "cos_upload.example.json"

DEFAULTS = {
    "region": "ap-guangzhou",
    "bucket": "ant-quant-data-1428892855",
    "prefix": "cos",
    "zip_key": "ant-quant-data.zip",
}


def _load_config() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    if LOCAL_CONFIG.is_file():
        try:
            raw = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update({k: v for k, v in raw.items() if v not in (None, "")})
        except Exception as e:
            print("[warn] bad local config:", e)
    # env overrides
    for env_k, cfg_k in (
        ("COS_SECRET_ID", "secret_id"),
        ("COS_SECRET_KEY", "secret_key"),
        ("COS_REGION", "region"),
        ("COS_BUCKET", "bucket"),
        ("COS_PREFIX", "prefix"),
        ("COS_ZIP_KEY", "zip_key"),
    ):
        v = os.environ.get(env_k)
        if v:
            cfg[cfg_k] = v
    return cfg


def _ensure_sdk():
    try:
        from qcloud_cos import CosConfig, CosS3Client  # noqa: F401
    except ImportError:
        print("正在安装 cos-python-sdk-v5 …")
        import subprocess

        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "cos-python-sdk-v5", "-q"]
        )
    from qcloud_cos import CosConfig, CosS3Client

    return CosConfig, CosS3Client


def _client(cfg: Dict[str, Any]):
    CosConfig, CosS3Client = _ensure_sdk()
    secret_id = str(cfg.get("secret_id") or "").strip()
    secret_key = str(cfg.get("secret_key") or "").strip()
    if not secret_id or not secret_key:
        raise SystemExit(
            "缺少密钥。请设置环境变量 COS_SECRET_ID / COS_SECRET_KEY，"
            "或复制 config/cos_upload.example.json → config/cos_upload.local.json 填写。"
        )
    cos_cfg = CosConfig(
        Region=str(cfg.get("region") or DEFAULTS["region"]),
        SecretId=secret_id,
        SecretKey=secret_key,
        Scheme="https",
    )
    return CosS3Client(cos_cfg)


def _rel_posix(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _object_key(prefix: str, rel: str) -> str:
    p = (prefix or "").strip().strip("/")
    r = rel.lstrip("/")
    return "%s/%s" % (p, r) if p else r


SKILL_SRC = ROOT / "skills" / "ant-quant-data"
SKILL_DIR_IN_COS = LOCAL_COS_DIR / "skills" / "ant-quant-data"


def refresh_embedded_skill() -> None:
    """把仓库 skills/ant-quant-data 同步进 data/cos/skills（仅目录，供客户端指定文件夹安装）。"""
    import shutil

    if not SKILL_SRC.is_dir():
        print("[skill] skip: missing", SKILL_SRC)
        return
    SKILL_DIR_IN_COS.parent.mkdir(parents=True, exist_ok=True)
    if SKILL_DIR_IN_COS.exists():
        shutil.rmtree(SKILL_DIR_IN_COS)
    shutil.copytree(SKILL_SRC, SKILL_DIR_IN_COS)
    # 去掉旧版误放的小 zip（WorkBuddy 指定目录安装即可，不必再带 skill zip）
    stale_zip = SKILL_DIR_IN_COS.parent / "ant-quant-data-skill.zip"
    if stale_zip.is_file():
        stale_zip.unlink()
        print("[skill] removed stale", stale_zip.name)
    print("[skill] refreshed", SKILL_DIR_IN_COS)


def build_offline_zip(
    src_dir: Path = LOCAL_COS_DIR,
    zip_path: Path = ZIP_PATH,
) -> Path:
    """把 data/cos 打成 ant-quant-data.zip（根内直接是子目录，无多余 data/cos 前缀）。"""
    if src_dir.resolve() == LOCAL_COS_DIR.resolve():
        refresh_embedded_skill()
    if zip_path.is_file():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(src_dir.rglob("*")):
            if not p.is_file():
                continue
            zf.write(p, arcname=_rel_posix(p, src_dir))
    return zip_path


def _head_size(client, bucket: str, key: str) -> Optional[int]:
    try:
        h = client.head_object(Bucket=bucket, Key=key)
        return int(h.get("Content-Length") or 0)
    except Exception:
        return None


def iter_local_files(src_dir: Path) -> List[Path]:
    return sorted(p for p in src_dir.rglob("*") if p.is_file())


def sync_cos_tree(
    client,
    *,
    bucket: str,
    prefix: str,
    src_dir: Path,
    force: bool = False,
    dry_run: bool = False,
) -> Dict[str, int]:
    stats = {"total": 0, "upload": 0, "skip": 0, "fail": 0}
    files = iter_local_files(src_dir)
    stats["total"] = len(files)
    for i, path in enumerate(files, 1):
        rel = _rel_posix(path, src_dir)
        key = _object_key(prefix, rel)
        local_size = path.stat().st_size
        remote_size = None if force else _head_size(client, bucket, key)
        if remote_size is not None and remote_size == local_size and not force:
            stats["skip"] += 1
            if i == 1 or i % 40 == 0 or i == len(files):
                print("  [%d/%d] skip %s" % (i, len(files), key))
            continue
        print(
            "  [%d/%d] %s %s (%s bytes)"
            % (i, len(files), "would-upload" if dry_run else "upload", key, local_size)
        )
        if dry_run:
            stats["upload"] += 1
            continue
        try:
            client.upload_file(
                Bucket=bucket,
                LocalFilePath=str(path),
                Key=key,
                PartSize=8,
                MAXThread=4,
                EnableMD5=False,
            )
            stats["upload"] += 1
        except Exception as e:
            stats["fail"] += 1
            print("    FAIL", key, e)
    return stats


def upload_zip(
    client,
    *,
    bucket: str,
    zip_path: Path,
    zip_key: str,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    if not zip_path.is_file():
        raise FileNotFoundError(str(zip_path))
    local_size = zip_path.stat().st_size
    remote = None if force else _head_size(client, bucket, zip_key)
    if remote is not None and remote == local_size and not force:
        print("[zip] skip (same size)", zip_key)
        return "skip"
    print(
        "[zip] %s %s (%s bytes)"
        % ("would-upload" if dry_run else "upload", zip_key, local_size)
    )
    if dry_run:
        return "dry-run"
    client.upload_file(
        Bucket=bucket,
        LocalFilePath=str(zip_path),
        Key=zip_key,
        PartSize=8,
        MAXThread=4,
        EnableMD5=False,
    )
    return "uploaded"


def main() -> None:
    ap = argparse.ArgumentParser(description="上传 data/cos 到腾讯云 COS")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略远端同名同尺寸，全部重传")
    ap.add_argument(
        "--with-zip",
        action="store_true",
        help="重新打包 ant-quant-data.zip 并上传到桶根（试用离线包）",
    )
    ap.add_argument("--zip-only", action="store_true", help="只打包/上传 zip，不同步 cos/ 树")
    ap.add_argument("--src", default=str(LOCAL_COS_DIR), help="本地目录，默认 data/cos")
    args = ap.parse_args()

    cfg = _load_config()
    src = Path(args.src)
    if not src.is_dir():
        raise SystemExit("本地目录不存在: %s" % src)

    bucket = str(cfg.get("bucket") or DEFAULTS["bucket"])
    region = str(cfg.get("region") or DEFAULTS["region"])
    prefix = str(cfg.get("prefix") if cfg.get("prefix") is not None else DEFAULTS["prefix"])
    zip_key = str(cfg.get("zip_key") or DEFAULTS["zip_key"])

    print(
        "bucket=%s region=%s prefix=%s src=%s"
        % (bucket, region, prefix or "(root)", src)
    )
    if not args.dry_run and Path(args.src).resolve() == LOCAL_COS_DIR.resolve():
        refresh_embedded_skill()
    client = _client(cfg)

    summary: Dict[str, Any] = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bucket": bucket,
        "region": region,
        "prefix": prefix,
    }

    if not args.zip_only:
        stats = sync_cos_tree(
            client,
            bucket=bucket,
            prefix=prefix,
            src_dir=src,
            force=bool(args.force),
            dry_run=bool(args.dry_run),
        )
        summary["tree"] = stats
        print(
            "[tree] total=%s upload=%s skip=%s fail=%s"
            % (stats["total"], stats["upload"], stats["skip"], stats["fail"])
        )

    if args.with_zip or args.zip_only:
        print("[zip] packing", ZIP_PATH.name, "from", src)
        if not args.dry_run:
            build_offline_zip(src, ZIP_PATH)
        else:
            print("[zip] dry-run skip pack")
        if ZIP_PATH.is_file() or args.dry_run:
            # dry-run 若无本地 zip，仍提示
            if args.dry_run and not ZIP_PATH.is_file():
                print("[zip] dry-run: local zip missing, would pack then upload", zip_key)
                summary["zip"] = "dry-run-missing-local"
            else:
                summary["zip"] = upload_zip(
                    client,
                    bucket=bucket,
                    zip_path=ZIP_PATH,
                    zip_key=zip_key,
                    force=bool(args.force),
                    dry_run=bool(args.dry_run),
                )

    base = "https://%s.cos.%s.myqcloud.com" % (bucket, region)
    summary["urls"] = {
        "tree_prefix": "%s/%s/" % (base, prefix.strip("/")) if prefix.strip("/") else base + "/",
        "zip": "%s/%s" % (base, zip_key.lstrip("/")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if (summary.get("tree") or {}).get("fail"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
