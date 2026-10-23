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


def _find_live_qmt():
    import win32gui

    found = []

    def top(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetClassName(hwnd) != "Qt5QWindowIcon":
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if "国金证券QMT交易端" in title and "模拟" not in title:
            found.append(hwnd)

    win32gui.EnumWindows(top, None)
    return found[0] if found else None


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


def _right_click(x: int, y: int) -> None:
    import win32api
    import win32con

    win32api.SetCursorPos((x, y))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)


def _left_click(x: int, y: int) -> None:
    import win32api
    import win32con

    win32api.SetCursorPos((x, y))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


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
    """恢复并前置主窗，返回 (left, top, right, bottom)。"""
    import win32con
    import win32gui

    if win32gui.IsIconic(main):
        win32gui.ShowWindow(main, win32con.SW_RESTORE)
        time.sleep(0.4)
    try:
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass
    time.sleep(0.25)
    return win32gui.GetWindowRect(main)


def _navigate_to_hsj_a_shares(main) -> None:
    """切到「行情」并用板块搜索定位「沪深京A股」。

    QMT 为 Qt 自绘，无可靠 UIA 子控件；用左侧图标栏相对坐标 + 搜索框输入。
    用户只需保证客户端已打开登录。
    """
    left, top, right, bottom = _ensure_qmt_foreground(main)
    width = max(1, right - left)
    height = max(1, bottom - top)

    # 左侧竖条自上而下：我的 / 行情 / 交易 / 模型研究 / 模型交易
    # 行情图标中心约相对 y=150~160。旧式 height*0.22 在 1080p 会钳到 200，正好点到「交易」。
    quote_x = left + max(24, min(40, width // 55))
    quote_y = top + max(148, min(162, int(height * 0.15)))
    _left_click(quote_x, quote_y)
    time.sleep(0.9)

    # 「搜索板块」在行情页板块树顶部（rail 右侧）；略下移避免点到上方工具栏
    search_x = left + max(120, min(200, int(width * 0.10)))
    search_y = top + max(68, min(95, int(height * 0.08)))
    _left_click(search_x, search_y)
    time.sleep(0.35)
    _ctrl_a()
    time.sleep(0.08)
    _send_unicode("沪深京A股")
    time.sleep(0.35)
    _press_enter()
    time.sleep(1.0)



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
        needle = Image.new("RGB", (tw, th), (30, 30, 30))
        ImageDraw.Draw(needle).text((3, 3), label, font=font, fill=(230, 230, 230))
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
        # 回退：行情表完整菜单里「导出数据」通常靠近顶部第 3 个亮行
        bands = _menu_bands(popup)
        if len(bands) >= 3:
            y_rel = bands[2]
        elif bands:
            y_rel = bands[min(5, len(bands) - 1)]
        else:
            return False
    _left_click((left + right) // 2, top + y_rel)
    return True


def export_via_ui(dest: Path) -> None:
    import win32gui

    main = _find_live_qmt()
    if not main:
        raise RuntimeError(
            "未检测到国金证券 QMT 交易端窗口。请先打开并登录 QMT 客户端，然后再跑本导出。"
        )
    # 自动：行情 → 搜索「沪深京A股」（用户只需保证客户端开着）
    _navigate_to_hsj_a_shares(main)
    left, top, right, bottom = win32gui.GetWindowRect(main)
    # 点行情表中部，避开左侧板块树和上下工具条
    x = left + int((right - left) * 0.55)
    y = top + int((bottom - top) * 0.45)
    before = _visible_windows()
    _right_click(x, y)
    popup = None
    deadline = time.time() + 2.5
    while time.time() < deadline and popup is None:
        time.sleep(0.15)
        for hwnd in _visible_windows() - before:
            if hwnd == main:
                continue
            if "Popup" in win32gui.GetClassName(hwnd):
                popup = hwnd
                break
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
        raise RuntimeError("没有出现导出保存框")
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
    ap = argparse.ArgumentParser(description="导出 QMT 沪深京A股行情表")
    ap.add_argument("--from-file", default="", help="用已经导出的表格，不再去点 QMT")
    ap.add_argument("--click", action="store_true", help="自动切到行情页「沪深京A股」后右键导出（只需 QMT 已打开登录）")
    ap.add_argument("--date", default="", help="交易日 YYYY-MM-DD，默认按收盘时间")
    args = ap.parse_args()
    if args.date:
        day = datetime.strptime(args.date[:10], "%Y-%m-%d").date()
    else:
        day = resolve_asof_day()
    day_s = day.isoformat()
    try:
        if args.from_file:
            count = install_table(Path(args.from_file), day_s, "file")
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
