# -*- coding: utf-8 -*-
"""从 QMT 行情页导出沪深京A股，写成当天股票池和日线用的行情表。

产物：data/qmt_hsj_quote.txt、data/qmt_hsj_quote.meta.json、data/a_share_universe.json。
不点买入、卖出、撤单、清仓。只在行情表的右键菜单里点「导出数据」。
用法：
  python tools/export_qmt_hsj_quote.py
  python tools/export_qmt_hsj_quote.py --from-file D:\\path\\quote.txt --date 2026-10-15
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.post_market_pipeline import resolve_asof_day  # noqa: E402
from utils.qmt_hsj_quote import QUOTE_PATH, install_table, read_table  # noqa: E402

_INCOMING = QUOTE_PATH.with_name("qmt_hsj_quote.incoming.txt")
_FORBIDDEN = ("买入", "卖出", "撤单", "清仓", "闪电买", "闪电卖")
_LOGIN_HINT = "请先打开并登录国金 QMT 客户端（保持运行即可；脚本会自动切到「行情 → 沪深京A股」）。"

# 自动控制前倒计时秒数：无人值守到点自动继续；点取消则中止。
_CONTROL_WARN_SECONDS = 12


def _prompt_before_control(seconds: int | None = None) -> bool:
    """即将 SendInput 控制 QMT 前提示用户停手。

    点「确定」或倒计时结束返回 True；点「取消」返回 False。
    盘后批跑无人值守时依赖倒计时自动继续，避免 MessageBox 无限挂死。
    """
    if seconds is None:
        seconds = _CONTROL_WARN_SECONDS
    seconds = max(0, int(seconds))
    try:
        import tkinter as tk
    except Exception:
        # 无 tkinter 时退回系统 MessageBox（无倒计时，需人工点）
        import ctypes

        ret = ctypes.windll.user32.MessageBoxW(
            0,
            "即将自动控制国金 QMT 客户端导出行情。\n请暂时不要动鼠标和键盘。\n\n确定=开始，取消=中止导出。",
            "行情导出 - 即将控制 QMT",
            0x00000031,  # MB_OKCANCEL | MB_ICONWARNING
        )
        return ret == 1  # IDOK

    result = {"ok": False}
    root = tk.Tk()
    root.title("行情导出 - 即将控制 QMT")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    remaining = {"n": seconds}

    tk.Label(
        root,
        text=(
            "即将自动控制国金 QMT 客户端导出行情。\n"
            "请暂时不要动鼠标和键盘。\n\n"
            "倒计时结束将自动开始；点取消则中止导出。"
        ),
        justify="left",
        padx=18,
        pady=12,
    ).pack()
    status = tk.Label(root, text="", padx=18)
    status.pack()

    def finish(ok: bool) -> None:
        result["ok"] = ok
        try:
            root.destroy()
        except Exception:
            pass

    def tick() -> None:
        n = remaining["n"]
        if n <= 0:
            finish(True)
            return
        status.config(text=f"{n} 秒后自动开始…")
        remaining["n"] = n - 1
        root.after(1000, tick)

    bf = tk.Frame(root)
    bf.pack(pady=(4, 12))
    tk.Button(bf, text="确定，开始", width=12, command=lambda: finish(True)).pack(side="left", padx=6)
    tk.Button(bf, text="取消", width=12, command=lambda: finish(False)).pack(side="left", padx=6)

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("+{0}+{1}".format((sw - w) // 2, (sh - h) // 3))
    if seconds <= 0:
        finish(True)
    else:
        root.after(0, tick)
        root.mainloop()
    return bool(result["ok"])


def _minimize_qmt(main=None) -> None:
    """导出控制结束后把国金 QMT 主窗最小化（成功或失败都调用）。"""
    import win32con
    import win32gui

    if main is None:
        main = _find_live_qmt()
    if not main:
        return
    try:
        win32gui.SetWindowPos(
            main,
            win32con.HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
    except Exception:
        pass
    try:
        win32gui.ShowWindow(main, win32con.SW_MINIMIZE)
        print("[行情导出] 已最小化 QMT 客户端", flush=True)
    except Exception as exc:
        print(f"[行情导出] 最小化 QMT 失败: {exc}", flush=True)




def _is_live_qmt_title(title: str) -> bool:
    title = title or ""
    if "模拟" in title or "模型交易" in title:
        return False
    # 常见标题：账号 - 国金证券QMT交易端 x.x.x
    if "国金证券QMT交易端" in title:
        return True
    if "QMT交易端" in title and "国金" in title:
        return True
    return False


def _find_live_qmt():
    """查找国金大 QMT 主窗。

    多屏/最小化均可：EnumWindows 跨虚拟桌面；IsIconic 的窗 IsWindowVisible 仍为 True。
    若仅可见窗未命中，再扫一遍含隐藏/最小化匹配标题的 Qt 主窗。
    """
    import win32gui

    visible = []
    any_match = []

    def top(hwnd, _):
        try:
            cls = win32gui.GetClassName(hwnd) or ""
        except Exception:
            return
        if cls != "Qt5QWindowIcon":
            return
        try:
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return
        if not _is_live_qmt_title(title):
            return
        any_match.append(hwnd)
        try:
            if win32gui.IsWindowVisible(hwnd):
                visible.append(hwnd)
        except Exception:
            pass

    win32gui.EnumWindows(top, None)
    if visible:
        # 优先非最小化
        for hwnd in visible:
            try:
                if not win32gui.IsIconic(hwnd):
                    return hwnd
            except Exception:
                return hwnd
        return visible[0]
    return any_match[0] if any_match else None


def _visible_windows():
    import win32gui

    found = set()

    def top(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            found.add(hwnd)

    win32gui.EnumWindows(top, None)
    return found


def _window_texts(hwnd) -> list:
    import win32gui

    texts = []
    title = (win32gui.GetWindowText(hwnd) or "").strip()
    if title:
        texts.append(title)

    def child(child_hwnd, _):
        text = (win32gui.GetWindowText(child_hwnd) or "").strip()
        if text:
            texts.append(text)

    try:
        win32gui.EnumChildWindows(hwnd, child, None)
    except Exception:
        pass
    return texts


def _press_escape() -> None:
    import win32api
    import win32con

    win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
    win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)


def _virtual_screen():
    """多屏虚拟桌面原点与宽高（SendInput 绝对坐标用）。"""
    import ctypes

    user32 = ctypes.windll.user32
    return (
        int(user32.GetSystemMetrics(76)),  # SM_XVIRTUALSCREEN
        int(user32.GetSystemMetrics(77)),  # SM_YVIRTUALSCREEN
        int(user32.GetSystemMetrics(78)),  # SM_CXVIRTUALSCREEN
        int(user32.GetSystemMetrics(79)),  # SM_CYVIRTUALSCREEN
    )


def _send_input_click(x: int, y: int, right: bool = False) -> None:
    """用 SendInput 绝对坐标点击。双屏下 mouse_event 经常点不出 Qt 右键菜单。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    INPUT_MOUSE = 0
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_VIRTUALDESK = 0x4000

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        )

    class INPUT(ctypes.Structure):
        class _I(ctypes.Union):
            _fields_ = (("mi", MOUSEINPUT),)

        _anonymous_ = ("i",)
        _fields_ = (("type", wintypes.DWORD), ("i", _I))

    vx, vy, vw, vh = _virtual_screen()
    ax = int((x - vx) * 65535 / max(1, vw - 1))
    ay = int((y - vy) * 65535 / max(1, vh - 1))
    base = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK | MOUSEEVENTF_MOVE

    def fire(flags):
        inp = INPUT(
            type=INPUT_MOUSE,
            mi=MOUSEINPUT(ax, ay, 0, base | flags, 0, None),
        )
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    fire(0)
    time.sleep(0.02)
    if right:
        fire(MOUSEEVENTF_RIGHTDOWN)
        time.sleep(0.03)
        fire(MOUSEEVENTF_RIGHTUP)
    else:
        fire(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.03)
        fire(MOUSEEVENTF_LEFTUP)


def _right_click(x: int, y: int) -> None:
    _send_input_click(x, y, right=True)


def _left_click(x: int, y: int) -> None:
    _send_input_click(x, y, right=False)


def _menu_bands(hwnd):
    """右键菜单没有控件文字。按亮字行数定位，第 6 行才是「导出数据」。"""
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width < 40 or height < 80:
        return []
    dc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(dc)
    save = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc, width, height)
        save.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(hwnd, save.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1
        )
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        save.DeleteDC()
        mfc.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)
    px = img.load()
    ink = []
    for y in range(img.size[1]):
        bright = 0
        for x in range(8, img.size[0] - 8):
            red, green, blue = px[x, y][:3]
            if red + green + blue > 180:
                bright += 1
        ink.append(bright > 8)
    bands = []
    start = None
    for y, on in enumerate(ink + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start >= 6:
                bands.append((start + y) // 2)
            start = None
    return bands


def _click_hwnd(hwnd) -> None:
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    _left_click((left + right) // 2, (top + bottom) // 2)


def _set_save_path(dialog, path: Path) -> bool:
    import win32gui

    # 现代「另存为」对话框里 Enum 到的 Edit 往往有两个：文件名框 + 地址栏。
    # 旧逻辑用 edit[-1]，会把路径写进地址栏，文件名仍为空，点保存后对话框不关、文件不落盘。
    target = None
    for ctrl_id in (0x047C, 0x0480):  # cmb13 文件名组合框 / edt1
        try:
            hwnd = win32gui.GetDlgItem(dialog, ctrl_id)
        except Exception:
            hwnd = 0
        if not hwnd:
            continue
        if win32gui.GetClassName(hwnd) == "Edit":
            target = hwnd
            break
        # ComboBox：取其内部 Edit
        inner = []

        def combo_child(ch, _):
            if win32gui.GetClassName(ch) == "Edit":
                inner.append(ch)

        try:
            win32gui.EnumChildWindows(hwnd, combo_child, None)
        except Exception:
            pass
        if inner:
            target = inner[0]
            break

    if target is None:
        edit = []

        def child(hwnd, _):
            if win32gui.GetClassName(hwnd) == "Edit":
                edit.append(hwnd)

        win32gui.EnumChildWindows(dialog, child, None)
        if not edit:
            return False
        # 回退：取第一个 Edit（文件名），不要最后一个（地址栏）
        target = edit[0]

    win32gui.SendMessage(target, 0x000C, 0, str(path))  # WM_SETTEXT
    buttons = []

    def buttons_of(hwnd, _):
        if win32gui.GetClassName(hwnd) == "Button":
            buttons.append((win32gui.GetWindowText(hwnd) or "", hwnd))

    win32gui.EnumChildWindows(dialog, buttons_of, None)
    for text, hwnd in buttons:
        if text.startswith("保存"):
            _click_hwnd(hwnd)
            return True
    return False


def _confirm_overwrite() -> None:
    import win32gui

    end = time.time() + 2.0
    while time.time() < end:
        found = []

        def top(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if "确认" in title or "另存为" in title:
                found.append(hwnd)

        win32gui.EnumWindows(top, None)
        for hwnd in found:
            buttons = []

            def child(child_hwnd, _):
                text = win32gui.GetWindowText(child_hwnd) or ""
                if text.startswith("是"):
                    buttons.append(child_hwnd)

            win32gui.EnumChildWindows(hwnd, child, None)
            if buttons:
                _click_hwnd(buttons[0])
                return
        time.sleep(0.15)



def _send_unicode(text: str) -> None:
    """向当前焦点控件输入 Unicode 文本（用于板块搜索框）。"""
    import ctypes

    PUL = ctypes.POINTER(ctypes.c_ulong)

    class KeyBdInput(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", PUL),
        ]

    class HardwareInput(ctypes.Structure):
        _fields_ = [
            ("uMsg", ctypes.c_ulong),
            ("wParamL", ctypes.c_short),
            ("wParamH", ctypes.c_ushort),
        ]

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", PUL),
        ]

    class Input_I(ctypes.Union):
        _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("ii", Input_I)]

    extra = ctypes.c_ulong(0)
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002
    for ch in text:
        down = Input(
            type=1,
            ii=Input_I(
                ki=KeyBdInput(0, ord(ch), KEYEVENTF_UNICODE, 0, ctypes.pointer(extra))
            ),
        )
        up = Input(
            type=1,
            ii=Input_I(
                ki=KeyBdInput(
                    0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, ctypes.pointer(extra)
                )
            ),
        )
        ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(down))
        ctypes.windll.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(up))
        time.sleep(0.015)


def _ctrl_a() -> None:
    import win32api
    import win32con

    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord("A"), 0, 0, 0)
    win32api.keybd_event(ord("A"), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def _press_enter() -> None:
    import win32api
    import win32con

    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)


def _ensure_qmt_foreground(main) -> tuple:
    """恢复并前置主窗，返回 (left, top, right, bottom)。

    最小化时 GetWindowRect 会落到 (-32000,-32000)，点不到；必须 SW_RESTORE。
    多屏时用虚拟桌面坐标，必要时拉回正常工作区。
    """
    import win32con
    import win32gui

    try:
        if win32gui.IsIconic(main):
            win32gui.ShowWindow(main, win32con.SW_RESTORE)
            time.sleep(0.5)
    except Exception:
        pass
    try:
        win32gui.ShowWindow(main, win32con.SW_SHOW)
    except Exception:
        pass
    # 仍像最小化坐标：按 placement 正常矩形挪回来
    try:
        left, top, right, bottom = win32gui.GetWindowRect(main)
        if right - left < 200 or bottom - top < 120 or left <= -10000 or top <= -10000:
            try:
                _flags, _show, _min, _max, normal = win32gui.GetWindowPlacement(main)
                # normal = (left, top, right, bottom)
                if normal and len(normal) >= 4:
                    nl, nt, nr, nb = [int(x) for x in normal[:4]]
                    if nr - nl >= 200 and nb - nt >= 120:
                        win32gui.SetWindowPos(
                            main,
                            win32con.HWND_TOP,
                            nl,
                            nt,
                            nr - nl,
                            nb - nt,
                            win32con.SWP_SHOWWINDOW,
                        )
                        time.sleep(0.35)
            except Exception:
                pass
            left, top, right, bottom = win32gui.GetWindowRect(main)
            if right - left < 200 or bottom - top < 120 or left <= -10000 or top <= -10000:
                vx, vy, vw, vh = _virtual_screen()
                win32gui.SetWindowPos(
                    main,
                    win32con.HWND_TOP,
                    int(vx + 80),
                    int(vy + 60),
                    max(1200, min(1600, int(vw * 0.7))),
                    max(700, min(900, int(vh * 0.75))),
                    win32con.SWP_SHOWWINDOW,
                )
                time.sleep(0.35)
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass
    time.sleep(0.25)
    return win32gui.GetWindowRect(main)


_NAV_MODE = "AB"  # legacy|A|B|AB；盘后默认 AB=Maximize + 比例单点「行情」
_DEBUG_DIR = ROOT / "data" / "export_nav_debug"


def _ensure_dpi_aware() -> None:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _screen_grab(left: int, top: int, right: int, bottom: int):
    import win32con
    import win32gui
    import win32ui
    from PIL import Image

    width, height = max(1, right - left), max(1, bottom - top)
    hwnd = win32gui.GetDesktopWindow()
    hdc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(hdc)
    save = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc, width, height)
        save.SelectObject(bmp)
        save.BitBlt((0, 0), (width, height), mfc, (left, top), win32con.SRCCOPY)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        return Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1
        )
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        save.DeleteDC()
        mfc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)


def _maximize_qmt(main) -> tuple:
    import win32con
    import win32gui

    win32gui.ShowWindow(main, win32con.SW_MAXIMIZE)
    time.sleep(0.45)
    try:
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass
    time.sleep(0.2)
    return win32gui.GetWindowRect(main)


def _match_rail_label_y(rail_img, label: str):
    """在左侧栏截图里找文字标签，返回 (y_rel, score)；失败 y_rel=None。"""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    fonts = []
    for size in (11, 12, 13):
        try:
            fonts.append(ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", size))
        except Exception:
            pass
    if not fonts:
        return None, -1.0
    H = np.asarray(rail_img.convert("RGB")).astype(np.float32)
    best = (-1.0, None)
    themes = (
        ((30, 30, 30), (220, 220, 220)),
        ((40, 50, 70), (200, 220, 255)),
        ((20, 28, 40), (90, 170, 255)),
        ((245, 245, 245), (20, 20, 20)),
        ((15, 20, 30), (255, 255, 255)),
    )
    y0, y1 = 40, min(420, H.shape[0] - 2)
    for font in fonts:
        bbox = ImageDraw.Draw(Image.new("RGB", (80, 30))).textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0] + 4, bbox[3] - bbox[1] + 4
        for bg, fg in themes:
            needle = Image.new("RGB", (tw, th), bg)
            ImageDraw.Draw(needle).text((2, 2), label, font=font, fill=fg)
            N = np.asarray(needle).astype(np.float32)
            nh, nw = N.shape[:2]
            if nh >= (y1 - y0) or nw >= H.shape[1]:
                continue
            n = (N.reshape(-1, 3) - N.mean(axis=(0, 1))) / (N.std() + 1e-6)
            for y in range(y0, y1 - nh, 2):
                for x in range(0, min(80, H.shape[1] - nw), 2):
                    patch = H[y : y + nh, x : x + nw]
                    p = (patch.reshape(-1, 3) - patch.mean(axis=(0, 1))) / (patch.std() + 1e-6)
                    score = float((n * p).mean())
                    if score > best[0]:
                        best = (score, y + nh // 2)
    if best[0] >= 0.58 and best[1] is not None:
        return int(best[1]), float(best[0])
    return None, float(best[0])


def _click_quote_rail_A(main, left: int, top: int, right: int, bottom: int) -> dict:
    """方案 A：左侧栏截图匹配「行情」，只点一次；失败用客户区比例第2格兜底。"""
    _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    height = max(1, bottom - top)
    rail_w = min(100, max(60, (right - left) // 18))
    img = _screen_grab(left, top, left + rail_w, bottom)
    stamp = time.strftime("%H%M%S")
    img.save(_DEBUG_DIR / f"rail_A_{stamp}.png")
    y_rel, score = _match_rail_label_y(img, "行情")
    method = "template"
    if y_rel is None:
        y_rel = int(height * 0.17)
        method = "ratio_fallback"
    rail_x = left + max(28, min(42, rail_w // 2))
    quote_y = top + int(y_rel)
    _left_click(rail_x, quote_y)
    info = {
        "method": method,
        "score": round(float(score), 4),
        "rail_x": rail_x,
        "quote_y": quote_y,
        "y_rel": int(y_rel),
        "rect": (left, top, right, bottom),
    }
    (_DEBUG_DIR / f"click_A_{stamp}.txt").write_text(str(info), encoding="utf-8")
    print(f"[nav-A] {info}", flush=True)
    time.sleep(0.5)
    return info


def _click_quote_rail_legacy(left: int, top: int) -> None:
    rail_x = left + 32
    for quote_y in (top + 130, top + 150, top + 118):
        _left_click(rail_x, quote_y)
        time.sleep(0.45)


def _navigate_to_hsj_a_shares(main) -> None:
    """从任意页（含模型交易）切到左侧栏「行情」，再搜索「沪深京A股」。

    试验模式 _NAV_MODE：
      legacy — 原固定 top+118/130/150 三连点
      A — 左侧栏模板匹配「行情」只点一次（比例兜底）
      B — 先 Maximize，再走 legacy
      AB — 先 Maximize，再走 A
    """
    import win32con
    import win32gui

    _ensure_dpi_aware()
    mode = (_NAV_MODE or "legacy").upper()
    left, top, right, bottom = _ensure_qmt_foreground(main)
    if mode in ("B", "AB"):
        left, top, right, bottom = _maximize_qmt(main)
        left, top, right, bottom = _ensure_qmt_foreground(main)

    width = max(1, right - left)
    height = max(1, bottom - top)

    try:
        win32gui.SetWindowPos(
            main,
            win32con.HWND_TOPMOST,
            0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass
    time.sleep(0.2)
    _press_escape()
    time.sleep(0.12)

    if mode in ("A", "AB"):
        _click_quote_rail_A(main, left, top, right, bottom)
    else:
        _click_quote_rail_legacy(left, top)

    search_x = left + max(140, min(220, int(width * 0.10)))
    search_y = top + max(72, min(92, int(height * 0.08)))
    if width >= 2400:
        search_x = left + 200
        search_y = top + 78
    _left_click(search_x, search_y)
    time.sleep(0.35)
    _ctrl_a()
    time.sleep(0.08)
    _send_unicode("沪深京A股")
    time.sleep(0.4)
    _press_enter()
    time.sleep(1.0)
    _press_escape()
    time.sleep(0.15)

    try:
        win32gui.SetWindowPos(
            main,
            win32con.HWND_NOTOPMOST,
            0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass
    time.sleep(0.15)


def _probe_quote_table_menu(main) -> dict:
    """导航后右键行情表，返回菜单尺寸，用于判断是否真的进了行情页。"""
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(main)
    x = left + int((right - left) * 0.55)
    y = top + int((bottom - top) * 0.45)
    popup = None
    sizes = []
    for _ in range(2):
        _press_escape()
        time.sleep(0.12)
        before = _visible_windows()
        _right_click(x, y)
        deadline = time.time() + 2.5
        while time.time() < deadline and popup is None:
            time.sleep(0.12)
            for hwnd in _visible_windows() - before:
                if hwnd == main:
                    continue
                cls = win32gui.GetClassName(hwnd) or ""
                if "Popup" not in cls:
                    continue
                l2, t2, r2, b2 = win32gui.GetWindowRect(hwnd)
                w2, h2 = r2 - l2, b2 - t2
                sizes.append((w2, h2))
                if 40 <= w2 <= 600 and 200 <= h2 <= 900:
                    popup = hwnd
                    break
        if popup:
            break
        time.sleep(0.2)
    _press_escape()
    ok = popup is not None
    info = {"ok": ok, "sizes": sizes, "click": (x, y)}
    print(f"[nav-probe] {info}", flush=True)
    return info


def _popup_screenshot(hwnd):
    """截右键弹出菜单，供找「导出数据」行。"""
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width < 40 or height < 40:
        return None
    dc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(dc)
    save = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc, width, height)
        save.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(hwnd, save.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        return Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1
        )
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        save.DeleteDC()
        mfc.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)


def _match_label_y(img, label: str):
    """在菜单截图里模板匹配中文标签，返回相对菜单顶的 y 中心。"""
    from pathlib import Path as _Path

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font_paths = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    font_path = next((p for p in font_paths if _Path(p).exists()), None)
    if font_path is None or img is None:
        return None

    H = np.asarray(img.convert("RGB")).astype(np.float32)
    best = (-1.0, None)
    for size in (11, 12, 13, 14):
        font = ImageFont.truetype(font_path, size)
        tmp = Image.new("RGB", (1, 1))
        bbox = ImageDraw.Draw(tmp).textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0] + 6, bbox[3] - bbox[1] + 6
        # 深色菜单：深底浅字；浅色菜单：浅底深字（国金 2.1 常见浅色右键）
        themes = (
            ((30, 30, 30), (230, 230, 230)),
            ((245, 245, 245), (20, 20, 20)),
            ((220, 220, 220), (0, 0, 0)),
        )
        for bg, fg in themes:
            needle = Image.new("RGB", (tw, th), bg)
            ImageDraw.Draw(needle).text((3, 3), label, font=font, fill=fg)
            N = np.asarray(needle).astype(np.float32)
            nh, nw = N.shape[:2]
            if nh >= H.shape[0] or nw >= H.shape[1]:
                continue
            n = (N.reshape(-1, 3) - N.reshape(-1, 3).mean(0)) / (N.std() + 1e-6)
            for y in range(0, H.shape[0] - nh, 2):
                for x in range(0, min(80, H.shape[1] - nw), 2):
                    patch = H[y : y + nh, x : x + nw].reshape(-1, 3)
                    p = (patch - patch.mean(0)) / (patch.std() + 1e-6)
                    score = float((n * p).mean())
                    if score > best[0]:
                        best = (score, y + nh // 2)
    if best[0] >= 0.55 and best[1] is not None:
        return int(best[1])
    return None


def _click_export_menu_item(popup) -> bool:
    """点右键菜单里的「导出数据」。先模板匹配，失败再按亮行启发式。"""
    import win32gui

    img = _popup_screenshot(popup)
    y_rel = _match_label_y(img, "导出数据")
    left, top, right, bottom = win32gui.GetWindowRect(popup)
    if y_rel is None:
        # 浅色菜单亮带检测会糊成一条，不能靠 bands。
        # 菜单顺序：调整列宽、定制列、导出数据… → 第 3 项约在高度 12%~18%
        left0, top0, right0, bottom0 = left, top, right, bottom
        height = max(1, bottom0 - top0)
        bands = _menu_bands(popup)
        if len(bands) >= 6:
            # 深色菜单条目带清晰时，第 3 项
            y_rel = bands[2]
        else:
            y_rel = int(height * 0.145)

    _left_click((left + right) // 2, top + y_rel)
    return True


def export_via_ui(dest: Path) -> None:
    import win32gui

    main = _find_live_qmt()
    if not main:
        raise RuntimeError(
            "未检测到国金证券 QMT 交易端窗口。请先打开并登录 QMT 客户端，然后再跑本导出。"
        )
    if not _prompt_before_control():
        raise RuntimeError("用户取消：未开始控制 QMT")
    try:
        _export_via_ui_controlled(dest, main)
    finally:
        _minimize_qmt(main)


def _export_via_ui_controlled(dest: Path, main) -> None:
    import win32gui

    # 自动：行情 → 搜索「沪深京A股」（用户只需保证客户端开着）
    _navigate_to_hsj_a_shares(main)
    import win32con
    try:
        win32gui.SetWindowPos(
            main, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass
    time.sleep(0.25)
    left, top, right, bottom = win32gui.GetWindowRect(main)
    # 点行情表中部，避开左侧板块树和上下工具条
    x = left + int((right - left) * 0.55)
    y = top + int((bottom - top) * 0.45)
    popup = None
    for attempt in range(3):
        _press_escape()
        time.sleep(0.15)
        before = _visible_windows()
        _right_click(x, y)
        deadline = time.time() + 3.0
        while time.time() < deadline and popup is None:
            time.sleep(0.15)
            for hwnd in _visible_windows() - before:
                if hwnd == main:
                    continue
                cls = win32gui.GetClassName(hwnd) or ""
                if "Popup" in cls:
                    left2, top2, right2, bottom2 = win32gui.GetWindowRect(hwnd)
                    w2, h2 = right2 - left2, bottom2 - top2
                    # 行情表右键「导出数据」菜单很高；模型交易短菜单通常 <200，直接丢弃
                    if 40 <= w2 <= 600 and 200 <= h2 <= 900:
                        popup = hwnd
                        break
        if popup:
            break
        time.sleep(0.3)

    if not popup:
        # 仍停在模型交易等页时，再导航一次后重试右键
        _navigate_to_hsj_a_shares(main)
        try:
            win32gui.SetWindowPos(
                main, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
            win32gui.SetForegroundWindow(main)
        except Exception:
            pass
        time.sleep(0.25)
        left, top, right, bottom = win32gui.GetWindowRect(main)
        x = left + int((right - left) * 0.55)
        y = top + int((bottom - top) * 0.45)
        for attempt in range(2):
            _press_escape()
            time.sleep(0.12)
            before = _visible_windows()
            _right_click(x, y)
            deadline = time.time() + 3.0
            while time.time() < deadline and popup is None:
                time.sleep(0.15)
                for hwnd in _visible_windows() - before:
                    if hwnd == main:
                        continue
                    cls = win32gui.GetClassName(hwnd) or ""
                    if "Popup" in cls:
                        left2, top2, right2, bottom2 = win32gui.GetWindowRect(hwnd)
                        w2, h2 = right2 - left2, bottom2 - top2
                        if 40 <= w2 <= 600 and 200 <= h2 <= 900:
                            popup = hwnd
                            break
            if popup:
                break
            time.sleep(0.25)
    if not popup:
        _press_escape()
        raise RuntimeError("行情表没有弹出右键菜单")
    before_save = _visible_windows()
    if not _click_export_menu_item(popup):
        _press_escape()
        raise RuntimeError("右键菜单里找不到「导出数据」")
    dialog = None
    deadline = time.time() + 4.0
    while time.time() < deadline and dialog is None:
        time.sleep(0.15)
        for hwnd in _visible_windows() - before_save:
            title = win32gui.GetWindowText(hwnd) or ""
            if "另存为" in title or "导出" in title or win32gui.GetClassName(hwnd) == "#32770":
                dialog = hwnd
                break
    if not dialog:
        _press_escape()
        try:
            win32gui.SetWindowPos(
                main, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
        except Exception:
            pass
        raise RuntimeError("没有出现导出保存框")
    try:
        win32gui.SetWindowPos(
            main, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
    except Exception:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    if not _set_save_path(dialog, dest):
        _press_escape()
        raise RuntimeError("保存框里没有文件名或保存按钮")
    _confirm_overwrite()
    deadline = time.time() + 20.0
    while time.time() < deadline and not dest.is_file():
        time.sleep(0.2)
    if not dest.is_file() or dest.stat().st_size < 1000:
        dest.unlink(missing_ok=True)
        raise RuntimeError("导出文件没有写出来")
    rows = read_table(dest)
    if not rows or "代码" not in rows[0] or "今开" not in rows[0]:
        dest.unlink(missing_ok=True)
        raise RuntimeError("导出的不是行情表（缺少代码或今开）")


def main() -> int:
    global _NAV_MODE, _CONTROL_WARN_SECONDS
    ap = argparse.ArgumentParser(description="导出 QMT 沪深京A股行情表")
    ap.add_argument("--from-file", default="", help="用已经导出的表格，不再去点 QMT")
    ap.add_argument("--click", action="store_true", help="自动切到行情页「沪深京A股」后右键导出（只需 QMT 已打开登录）")
    ap.add_argument("--date", default="", help="交易日 YYYY-MM-DD，默认按收盘时间")
    ap.add_argument(
        "--nav-mode",
        default="AB",
        choices=["legacy", "A", "B", "AB", "a", "b", "ab"],
        help="导航：legacy|A|B|AB；默认 AB（Maximize+比例单点行情）",
    )
    ap.add_argument(
        "--nav-probe",
        action="store_true",
        help="只测导航（行情右键长菜单），不导出文件",
    )
    ap.add_argument(
        "--control-wait",
        type=int,
        default=None,
        help="控制前提示倒计时秒数（默认 12；0=立即开始）",
    )
    args = ap.parse_args()
    _NAV_MODE = str(args.nav_mode).upper()
    if args.control_wait is not None:
        _CONTROL_WARN_SECONDS = max(0, int(args.control_wait))
    if args.date:
        day = datetime.strptime(args.date[:10], "%Y-%m-%d").date()
    else:
        day = resolve_asof_day()
    day_s = day.isoformat()
    try:
        if args.from_file:
            count = install_table(Path(args.from_file), day_s, "file")
        elif args.nav_probe:
            _ensure_dpi_aware()
            main_hwnd = _find_live_qmt()
            if not main_hwnd:
                raise RuntimeError("未检测到国金证券 QMT 交易端窗口。")
            if not _prompt_before_control():
                print("[nav-probe] 用户取消：未开始控制 QMT", flush=True)
                return 1
            print(f"[nav-probe] mode={_NAV_MODE}", flush=True)
            try:
                _navigate_to_hsj_a_shares(main_hwnd)
                probe = _probe_quote_table_menu(main_hwnd)
                print(f"[nav-probe] done ok={probe.get('ok')} mode={_NAV_MODE}", flush=True)
                return 0 if probe.get("ok") else 2
            finally:
                _minimize_qmt(main_hwnd)
        elif args.click:
            export_via_ui(_INCOMING)
            count = install_table(_INCOMING, day_s, "qmt_ui")
        else:
            from utils.qmt_hsj_quote import file_covers_day, read_table, universe_codes

            if not file_covers_day(day_s):
                raise RuntimeError(
                    "还没有当天的沪深京行情表。请打开 QMT 后运行: python tools/export_qmt_hsj_quote.py --click"
                )
            count = len(universe_codes(read_table(QUOTE_PATH)))
    except Exception as exc:
        print(f"[行情导出] 失败: {exc}", flush=True)
        if not args.from_file:
            print(f"[行情导出] {_LOGIN_HINT}", flush=True)
        return 1
    print(f"[行情导出] {day_s} 代码={count} -> {QUOTE_PATH.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
