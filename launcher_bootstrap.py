# -*- coding: utf-8 -*-
"""启动器引导：仅负责拉起本机 Python 运行 launcher.py。

用 PyInstaller onefile 打包本文件（不含 PyQt）为 launcher.exe。
关闭 GUI 时由 python.exe 进程退出，引导进程早已结束，
可避免「Failed to remove temporary directory: ...\\_MEI*」警告框。
"""

from __future__ import annotations

import os
import subprocess
import sys


def _project_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    root = _project_root()
    script = os.path.join(root, "launcher.py")
    if not os.path.isfile(script):
        # 尽量弹出错误；引导包不含 Qt，用 Windows 消息框
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"未找到启动器脚本：\n{script}",
                "蚂蚁量化启动器",
                0x10,
            )
        except Exception:
            print(f"launcher.py not found: {script}", file=sys.stderr)
        return 1

    python_exe = os.path.join(root, "python.exe")
    if not os.path.isfile(python_exe):
        python_exe = "python"

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    popen_kwargs = {
        "cwd": root,
        "env": env,
        "close_fds": True,
    }
    if sys.platform == "win32":
        # 脱离引导进程，便于 onefile 引导立刻退出并删掉 _MEI
        flags = 0
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        popen_kwargs["creationflags"] = flags
        # 不继承控制台；GUI 由 launcher.py / Qt 自己建窗
        try:
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
            popen_kwargs["stdin"] = subprocess.DEVNULL
        except Exception:
            pass

    try:
        subprocess.Popen([python_exe, script], **popen_kwargs)
    except Exception as exc:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"无法启动 launcher.py：\n{exc}",
                "蚂蚁量化启动器",
                0x10,
            )
        except Exception:
            print(f"failed to start launcher.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
