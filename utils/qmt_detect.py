# -*- coding: utf-8 -*-
"""本机探测大 QMT 安装目录与资金账号（不连券商、不读密码）。"""
from __future__ import annotations

import os
import re
import string
from typing import Any, Dict, List, Optional, Tuple

_EXE_REL = os.path.join("bin.x64", "XtItClient.exe")
_ACCOUNT_RE = re.compile(r"^\d{8,16}$")


def _norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(str(path or "").strip()))


def _is_qmt_root(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    return os.path.isfile(os.path.join(path, _EXE_REL))


def _python_dir(root: str) -> str:
    p = os.path.join(root, "python")
    return p if os.path.isdir(p) else ""


def _running_install_roots() -> List[str]:
    out: List[str] = []
    try:
        import psutil
    except Exception:
        return out
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = str((proc.info or {}).get("name") or "").lower()
                exe = str((proc.info or {}).get("exe") or "")
            except Exception:
                continue
            if name != "xtitclient.exe" or not exe:
                continue
            root = os.path.dirname(os.path.dirname(_norm(exe)))
            if _is_qmt_root(root) and root not in out:
                out.append(root)
    except Exception:
        pass
    return out


def _registry_install_roots() -> List[str]:
    out: List[str] = []
    try:
        import winreg
    except Exception:
        return out
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, sub in keys:
        try:
            hkey = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(hkey, i)
                except OSError:
                    break
                i += 1
                try:
                    sk = winreg.OpenKey(hkey, name)
                except OSError:
                    continue
                try:
                    loc, _ = winreg.QueryValueEx(sk, "InstallLocation")
                except OSError:
                    loc = ""
                finally:
                    winreg.CloseKey(sk)
                loc = str(loc or "").strip().strip('"')
                if loc and _is_qmt_root(loc) and loc not in out:
                    out.append(_norm(loc))
        finally:
            winreg.CloseKey(hkey)
    return out


def _scan_drive_children() -> List[str]:
    out: List[str] = []
    extra = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files")),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    roots = [("%s:\\" % ch) for ch in string.ascii_uppercase if os.path.isdir("%s:\\" % ch)]
    roots.extend([p for p in extra if p and os.path.isdir(p)])
    seen = set()
    for base in roots:
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            full = os.path.join(base, name)
            if full in seen:
                continue
            seen.add(full)
            if _is_qmt_root(full):
                out.append(_norm(full))
    return out


def discover_qmt_installs() -> List[Dict[str, Any]]:
    """返回大 QMT 安装：root / python_dir / running / label。"""
    running = {_norm(p) for p in _running_install_roots()}
    roots: List[str] = []
    for group in (_running_install_roots(), _registry_install_roots(), _scan_drive_children()):
        for r in group:
            n = _norm(r)
            if n not in roots and _is_qmt_root(n):
                roots.append(n)

    items: List[Dict[str, Any]] = []
    for root in roots:
        py = _python_dir(root)
        if not py:
            continue
        base = os.path.basename(root.rstrip("\\/"))
        tag = "运行中" if root in running else ""
        if re.search(r"mini\s*qmt|副本", base, re.I):
            kind = "可能是 Mini/副本"
        else:
            kind = "大 QMT"
        label = "%s（%s）" % (base, kind)
        if tag:
            label += " · %s" % tag
        items.append(
            {
                "root": root,
                "python_dir": py,
                "running": root in running,
                "label": label,
                "base": base,
            }
        )

    def _rank(it: Dict[str, Any]) -> Tuple[int, int, str]:
        mini = 1 if re.search(r"mini\s*qmt|副本", it.get("base") or "", re.I) else 0
        return (0 if it.get("running") else 1, mini, str(it.get("base") or ""))

    items.sort(key=_rank)
    return items


def discover_accounts(qmt_root: str) -> List[Dict[str, Any]]:
    """userdata/users 下数字目录视为资金账号，最近用过的排前。"""
    root = _norm(qmt_root)
    users = os.path.join(root, "userdata", "users")
    if not os.path.isdir(users):
        return []
    out: List[Dict[str, Any]] = []
    try:
        names = os.listdir(users)
    except OSError:
        return []
    for name in names:
        if not _ACCOUNT_RE.match(name):
            continue
        folder = os.path.join(users, name)
        if not os.path.isdir(folder):
            continue
        stamp = 0.0
        for fn in ("accountInfo.ini", "Config.xml", "authAndConfig.xml"):
            fp = os.path.join(folder, fn)
            try:
                stamp = max(stamp, os.path.getmtime(fp))
            except OSError:
                continue
        out.append({"account_id": name, "mtime": stamp, "path": folder})
    out.sort(key=lambda x: -float(x.get("mtime") or 0))
    return out


def install_root_from_python_dir(python_dir: str) -> str:
    p = _norm(python_dir)
    parent = os.path.dirname(p)
    return parent if _is_qmt_root(parent) else ""
