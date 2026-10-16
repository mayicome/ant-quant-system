# -*- coding: utf-8 -*-
"""从 QMT 行情页导出沪深京A股，写成当天股票池和日线用的行情表。

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

    edit = []

    def child(hwnd, _):
        if win32gui.GetClassName(hwnd) == "Edit":
            edit.append(hwnd)

    win32gui.EnumChildWindows(dialog, child, None)
    if not edit:
        return False
    win32gui.SendMessage(edit[-1], 0x000C, 0, str(path))  # WM_SETTEXT
    buttons = []

    def buttons_of(hwnd, _):
        if win32gui.GetClassName(hwnd) == "Button":
            buttons.append((win32gui.GetWindowText(hwnd) or "", hwnd))

    win32gui.EnumChildWindows(dialog, buttons_of, None)
    for text, hwnd in buttons:
        if text.startswith("保存") or text.startswith("保存(&S)"):
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


def export_via_ui(dest: Path) -> None:
    import win32con
    import win32gui

    main = _find_live_qmt()
    if not main:
        raise RuntimeError("没有找到国金证券 QMT 交易端")
    if win32gui.IsIconic(main):
        win32gui.ShowWindow(main, win32con.SW_RESTORE)
        time.sleep(0.4)
    try:
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass
    time.sleep(0.2)
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
    bands = _menu_bands(popup)
    # 当前菜单固定 7 行，第 6 行是「导出数据」。行数变了就停，避免点到别的项。
    if len(bands) != 7:
        _press_escape()
        raise RuntimeError(f"右键菜单行数是 {len(bands)}，不是导出那一版")
    left, top, right, bottom = win32gui.GetWindowRect(popup)
    before_save = _visible_windows()
    _left_click((left + right) // 2, top + bands[5])
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
    ap.add_argument("--click", action="store_true", help="在行情页上右键导出。窗口必须停在沪深京A股")
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
                    "还没有当天的沪深京行情表。请在 QMT 行情页打开沪深京A股后右键导出"
                )
            count = len(universe_codes(read_table(QUOTE_PATH)))
    except Exception as exc:
        print(f"[行情导出] 失败: {exc}", flush=True)
        return 1
    print(f"[行情导出] {day_s} 代码={count} -> {QUOTE_PATH.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
