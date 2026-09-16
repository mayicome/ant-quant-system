# -*- coding: utf-8 -*-
"""盘后批跑：根据当日 COS 增量，调用「蚂蚁量化研习社公众号」工程写入微信草稿箱。

不在本仓库存 AppSecret；凭证读公众号工程的 config.json（优先 dist/，与 GUI 一致）。

退出码：
  0  成功
  64 公众号 IP 不在白名单（供批跑分钟重试）
  其它 失败

用法：
  python tools/upload_cos_wechat_draft.py
  python tools/upload_cos_wechat_draft.py --date 2026-08-31
  python tools/upload_cos_wechat_draft.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_MP_ROOT = Path(os.environ.get("ANT_WECHAT_MP_ROOT") or r"D:\蚂蚁量化研习社公众号")

EXIT_IP_WHITELIST = 64


def _pick_python(mp_root: Path) -> str:
    venv_py = mp_root / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def _pick_config(mp_root: Path) -> Path:
    """GUI 打包版写 dist/config.json；开发版写根目录 config.json。优先用较新的。"""
    dist_cfg = mp_root / "dist" / "config.json"
    root_cfg = mp_root / "config.json"
    cands = [p for p in (dist_cfg, root_cfg) if p.is_file()]
    if not cands:
        raise SystemExit("未找到公众号 config.json：%s" % mp_root)
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]


def _looks_like_ip_whitelist(text: str) -> bool:
    t = (text or "").lower()
    return (
        "40164" in text
        or "not in whitelist" in t
        or "invalid ip" in t
        or "[wechat-ip-whitelist]" in t
    )


def _safe_print(text: str) -> None:
    """Windows 控制台常为 GBK：避免因 \\ufffd 等字符 print 崩掉把成功误判成失败。"""
    s = str(text or "")
    try:
        print(s)
        return
    except UnicodeEncodeError:
        pass
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        sys.stdout.buffer.write(
            (s + "\n").encode(enc, errors="replace")
        )
        sys.stdout.buffer.flush()
    except Exception:
        try:
            print(s.encode("ascii", errors="replace").decode("ascii"))
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="COS 日更 → 微信公众号草稿箱")
    ap.add_argument("--date", default="", help="YYYY-MM-DD，默认今天")
    ap.add_argument("--mp-root", default=str(DEFAULT_MP_ROOT))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mp_root = Path(args.mp_root)
    script = mp_root / "create_cos_daily_draft.py"
    if not script.is_file():
        raise SystemExit("缺少 %s（请确认公众号工程已包含 create_cos_daily_draft.py）" % script)

    trade = args.date or date.today().isoformat()
    cfg = _pick_config(mp_root)
    py = _pick_python(mp_root)
    cmd = [py, str(script), "--date", trade, "--cos-dir", str(ROOT / "data" / "cos")]
    if args.dry_run:
        cmd.append("--dry-run")

    env = os.environ.copy()
    env["ANT_WECHAT_CONFIG"] = str(cfg)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    _safe_print("[wechat-draft] mp_root= %s" % mp_root)
    _safe_print("[wechat-draft] config= %s" % cfg)
    _safe_print("[wechat-draft] python= %s" % py)
    _safe_print("[wechat-draft] cmd= %s" % " ".join(cmd))

    proc = subprocess.run(
        cmd,
        cwd=str(mp_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if out.strip():
        _safe_print(out.rstrip())

    rc = int(proc.returncode or 0)
    if rc == EXIT_IP_WHITELIST or (rc != 0 and _looks_like_ip_whitelist(out)):
        public_ip = ""
        try:
            from get_public_ip import get_public_ip

            public_ip = str(get_public_ip() or "").strip()
        except Exception as e:
            _safe_print("[wechat-draft] 获取公网IP失败: %s" % e)
        if public_ip:
            # 供批跑 GUI / 日志解析
            _safe_print("[wechat-ip-whitelist] public_ip=%s" % public_ip)
            _safe_print(
                "[wechat-draft] IP 不在公众号白名单，当前公网IP=%s，退出码=%d"
                % (public_ip, EXIT_IP_WHITELIST)
            )
        else:
            _safe_print(
                "[wechat-draft] IP 不在公众号白名单（未能解析公网IP），退出码=%d"
                % EXIT_IP_WHITELIST
            )
        return EXIT_IP_WHITELIST
    if rc == 0 and not args.dry_run:
        try:
            from utils.post_market_step_status import mark_step_finished

            mark_step_finished(
                trade,
                "wechat_draft",
                ok=True,
                detail="upload_cos_wechat_draft 成功",
            )
            _safe_print("[wechat-draft] 已写入本地完成记录 trade_date=%s" % trade)
        except Exception as e:
            _safe_print("[wechat-draft] 本地完成记录写入失败: %s" % e)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
