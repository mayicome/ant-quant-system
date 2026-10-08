# -*- coding: utf-8 -*-
"""Windows 控制台小工具：关掉快速编辑，避免误点黑窗冻结进程。"""
from __future__ import annotations

import sys


def disable_quick_edit() -> bool:
    """关闭当前控制台 Quick Edit（选择模式）。成功返回 True。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        # STD_INPUT_HANDLE = (DWORD)-10
        h_in = kernel32.GetStdHandle(ctypes.c_ulong(-10).value)
        if not h_in or int(h_in) in (0, -1):
            return False
        mode = wintypes.DWORD(0)
        if not kernel32.GetConsoleMode(h_in, ctypes.byref(mode)):
            return False
        enable_quick_edit = 0x0040
        enable_extended_flags = 0x0080
        new_mode = (int(mode.value) | enable_extended_flags) & ~enable_quick_edit
        return bool(kernel32.SetConsoleMode(h_in, new_mode))
    except Exception:
        return False
