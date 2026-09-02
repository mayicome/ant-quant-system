# -*- coding: utf-8 -*-
"""生成 COS 离线包 ant-quant-data.zip 的预签名下载 URL。

默认有效期 7 天，到期自动失效。密钥同 upload_cos_data.py。

用法：
  python tools/presign_cos_zip.py
  python tools/presign_cos_zip.py --days 7
  python tools/presign_cos_zip.py --key ant-quant-data.zip --days 3
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.upload_cos_data import (  # noqa: E402
    DEFAULTS,
    _client,
    _load_config,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成离线包预签名下载 URL")
    ap.add_argument("--days", type=float, default=7.0, help="有效天数，默认 7")
    ap.add_argument("--key", default="", help="对象键，默认用配置里的 zip_key")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    if args.days <= 0:
        raise SystemExit("--days 必须 > 0")
    # 腾讯云永久密钥预签名最长一般按秒传；7 天 = 604800
    expired_sec = int(args.days * 24 * 3600)
    if expired_sec < 60:
        expired_sec = 60

    cfg = _load_config()
    bucket = str(cfg.get("bucket") or DEFAULTS["bucket"])
    region = str(cfg.get("region") or DEFAULTS["region"])
    key = str(args.key or cfg.get("zip_key") or DEFAULTS["zip_key"]).lstrip("/")

    client = _client(cfg)
    url = client.get_presigned_download_url(
        Bucket=bucket,
        Key=key,
        Expired=expired_sec,
    )
    now = datetime.now()
    exp_at = now + timedelta(seconds=expired_sec)
    info = {
        "bucket": bucket,
        "region": region,
        "key": key,
        "expire_seconds": expired_sec,
        "expire_days": round(expired_sec / 86400.0, 4),
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at": exp_at.strftime("%Y-%m-%d %H:%M:%S"),
        "url": url,
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print("bucket:", bucket)
        print("key:", key)
        print("created:", info["created_at"])
        print("expires:", info["expires_at"], "(%s 天)" % info["expire_days"])
        print()
        print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
