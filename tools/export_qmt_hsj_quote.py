# -*- coding: gbk -*-
"""�� QMT ����ҳ�������A�ɣ�д�ɵ����Ʊ�غ������õ��������

���data/qmt_hsj_quote.txt��data/qmt_hsj_quote.meta.json��data/a_share_universe.json��
�������롢��������������֡�ֻ����������Ҽ��˵���㡸�������ݡ���
�÷���
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
_FORBIDDEN = ("����", "����", "����", "���", "������", "������")
_LOGIN_HINT = "���ȴ򿪲���¼���� QMT �ͻ��ˣ��������м��ɣ��ű����Զ��е������� �� ���A�ɡ�����"

# �Զ�����ǰ����ʱ����������ֵ�ص����Զ���������ȡ������ֹ��
_CONTROL_WARN_SECONDS = 12
# True=ʧ��ʱ����С�����������ۿ���ƫ�����ɹ�����С����ȡ�����Ͳ���С����
_NO_MINIMIZE_ON_FAIL = False


def _prompt_before_control(seconds: int | None = None) -> bool:
    """���� SendInput ���� QMT ǰ��ʾ�û�ͣ�֡�

    �㡸ȷ�����򵹼�ʱ�������� True���㡸ȡ�������� False��
    �̺���������ֵ��ʱ��������ʱ�Զ����������� MessageBox ���޹�����
    """
    if seconds is None:
        seconds = _CONTROL_WARN_SECONDS
    seconds = max(0, int(seconds))
    try:
        import tkinter as tk
    except Exception:
        # �� tkinter ʱ�˻�ϵͳ MessageBox���޵���ʱ�����˹��㣩
        import ctypes

        ret = ctypes.windll.user32.MessageBoxW(
            0,
            "�����Զ����ƹ��� QMT �ͻ��˵������顣\n����ʱ��Ҫ�����ͼ��̡�\n\nȷ��=��ʼ��ȡ��=��ֹ������",
            "���鵼�� - �������� QMT",
            0x00000031,  # MB_OKCANCEL | MB_ICONWARNING
        )
        return ret == 1  # IDOK

    result = {"ok": False}
    root = tk.Tk()
    root.title("���鵼�� - �������� QMT")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    remaining = {"n": seconds}

    tk.Label(
        root,
        text=(
            "�����Զ����ƹ��� QMT �ͻ��˵������顣\n"
            "����ʱ��Ҫ�����ͼ��̡�\n\n"
            "����ʱ�������Զ���ʼ����ȡ������ֹ������"
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
        status.config(text=f"{n} ����Զ���ʼ��")
        remaining["n"] = n - 1
        root.after(1000, tick)

    bf = tk.Frame(root)
    bf.pack(pady=(4, 12))
    tk.Button(bf, text="ȷ������ʼ", width=12, command=lambda: finish(True)).pack(side="left", padx=6)
    tk.Button(bf, text="ȡ��", width=12, command=lambda: finish(False)).pack(side="left", padx=6)

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
    """�������ƽ�����ѹ��� QMT ������С�����ɹ���ʧ�ܶ����ã���"""
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
        print("[���鵼��] ����С�� QMT �ͻ���", flush=True)
    except Exception as exc:
        print(f"[���鵼��] ��С�� QMT ʧ��: {exc}", flush=True)




def _find_live_qmt():
    import win32gui

    found = []

    def top(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetClassName(hwnd) != "Qt5QWindowIcon":
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if "����֤ȯQMT���׶�" in title and "ģ��" not in title:
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


def _virtual_screen():
    """������������ԭ������ߣ�SendInput ���������ã���"""
    import ctypes

    user32 = ctypes.windll.user32
    return (
        int(user32.GetSystemMetrics(76)),  # SM_XVIRTUALSCREEN
        int(user32.GetSystemMetrics(77)),  # SM_YVIRTUALSCREEN
        int(user32.GetSystemMetrics(78)),  # SM_CXVIRTUALSCREEN
        int(user32.GetSystemMetrics(79)),  # SM_CYVIRTUALSCREEN
    )


def _send_input_click(x: int, y: int, right: bool = False) -> None:
    """�� SendInput ������������˫���� mouse_event �����㲻�� Qt �Ҽ��˵���"""
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
    """�Ҽ��˵�û�пؼ����֡�������������λ���� 6 �в��ǡ��������ݡ���"""
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

    # �ִ�������Ϊ���Ի����� Enum ���� Edit �������������ļ����� + ��ַ����
    # ���߼��� edit[-1]�����·��д����ַ�����ļ�����Ϊ�գ��㱣���Ի��򲻹ء��ļ������̡�
    target = None
    for ctrl_id in (0x047C, 0x0480):  # cmb13 �ļ�����Ͽ� / edt1
        try:
            hwnd = win32gui.GetDlgItem(dialog, ctrl_id)
        except Exception:
            hwnd = 0
        if not hwnd:
            continue
        if win32gui.GetClassName(hwnd) == "Edit":
            target = hwnd
            break
        # ComboBox��ȡ���ڲ� Edit
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
        # ���ˣ�ȡ��һ�� Edit���ļ���������Ҫ���һ������ַ����
        target = edit[0]

    win32gui.SendMessage(target, 0x000C, 0, str(path))  # WM_SETTEXT
    buttons = []

    def buttons_of(hwnd, _):
        if win32gui.GetClassName(hwnd) == "Button":
            buttons.append((win32gui.GetWindowText(hwnd) or "", hwnd))

    win32gui.EnumChildWindows(dialog, buttons_of, None)
    for text, hwnd in buttons:
        if text.startswith("����"):
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
            if "ȷ��" in title or "����Ϊ" in title:
                found.append(hwnd)

        win32gui.EnumWindows(top, None)
        for hwnd in found:
            buttons = []

            def child(child_hwnd, _):
                text = win32gui.GetWindowText(child_hwnd) or ""
                if text.startswith("��"):
                    buttons.append(child_hwnd)

            win32gui.EnumChildWindows(hwnd, child, None)
            if buttons:
                _click_hwnd(buttons[0])
                return
        time.sleep(0.15)



def _send_unicode(text: str) -> None:
    """��ǰ����ؼ����� Unicode �ı������ڰ�������򣩡�"""
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
    """�ָ���ǰ������������ (left, top, right, bottom)��"""
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


_NAV_MODE = "AB"  # legacy|A|B|AB���̺�Ĭ�� AB=Maximize + �������㡸���项
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


def _window_state_info(main) -> dict:
    """�������� / IsZoomed / �ͻ���������� maximize У����ʧ�� dump��"""
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(main)
    w, h = right - left, bottom - top
    zoomed = False
    try:
        zoomed = bool(win32gui.IsZoomed(main))
    except Exception:
        pass
    iconic = False
    try:
        iconic = bool(win32gui.IsIconic(main))
    except Exception:
        pass
    client_area = 0
    try:
        cl, ct, cr, cb = win32gui.GetClientRect(main)
        client_area = max(0, cr - cl) * max(0, cb - ct)
    except Exception:
        pass
    return {
        "rect": (left, top, right, bottom),
        "size": (w, h),
        "IsZoomed": zoomed,
        "IsIconic": iconic,
        "client_area": client_area,
    }


def _dump_window_state(main, tag: str) -> dict:
    info = _window_state_info(main)
    print(
        f"[���鵼��] {tag}: rect={info['rect']} size={info['size']} "
        f"IsZoomed={info['IsZoomed']} IsIconic={info['IsIconic']} "
        f"client_area={info['client_area']}",
        flush=True,
    )
    return info


def _looks_maximized(info: dict) -> bool:
    if info.get("IsZoomed"):
        return True
    w, h = info.get("size") or (0, 0)
    # δ�� IsZoomed ʱ��������ף�Լ��������1097x649 ����ָ�̬����ʧ��
    if w >= 1500 and h >= 850:
        return True
    if int(info.get("client_area") or 0) >= 1_200_000:
        return True
    return False


def _maximize_qmt(main) -> tuple:
    """Maximize ��У�� IsZoomed/�ͻ�����ʧ���� RESTORE �� MAXIMIZE��"""
    import win32con
    import win32gui

    before = _dump_window_state(main, "maximizeǰ")

    def _apply_maximize(label: str) -> dict:
        win32gui.ShowWindow(main, win32con.SW_MAXIMIZE)
        time.sleep(0.55)
        try:
            win32gui.SetForegroundWindow(main)
        except Exception:
            pass
        time.sleep(0.25)
        return _dump_window_state(main, label)

    after = _apply_maximize("maximize��")
    if not _looks_maximized(after):
        print(
            "[���鵼��] maximizeδ��ס��ShowWindow(SW_RESTORE) ���� SW_MAXIMIZE",
            flush=True,
        )
        win32gui.ShowWindow(main, win32con.SW_RESTORE)
        time.sleep(0.7)  # restore��maximize �ӳ��ȴ�
        try:
            win32gui.SetForegroundWindow(main)
        except Exception:
            pass
        time.sleep(0.35)
        after = _apply_maximize("maximize���Ժ�")
        if not _looks_maximized(after):
            print(
                "[���鵼��] ����: ���Ժ��Բ������ "
                f"(before={before['size']} after={after['size']} "
                f"IsZoomed={after['IsZoomed']})",
                flush=True,
            )
    return after["rect"]


def _match_rail_label_y(rail_img, label: str):
    """���������ͼ�������ֱ�ǩ������ (y_rel, score)��ʧ�� y_rel=None��"""
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
    """���� A���������ͼƥ�䡸���项��ֻ��һ�Σ�ʧ���ÿͻ���������2�񶵵ס�"""
    _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    height = max(1, bottom - top)
    rail_w = min(100, max(60, (right - left) // 18))
    img = _screen_grab(left, top, left + rail_w, bottom)
    stamp = time.strftime("%H%M%S")
    img.save(_DEBUG_DIR / f"rail_A_{stamp}.png")
    y_rel, score = _match_rail_label_y(img, "����")
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
    """������ҳ����ģ�ͽ��ף��е�����������项�������������A�ɡ���

    ����ģʽ _NAV_MODE��
      legacy �� ԭ�̶� top+118/130/150 ������
      A �� �����ģ��ƥ�䡸���项ֻ��һ�Σ��������ף�
      B �� �� Maximize������ legacy
      AB �� �� Maximize������ A
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
    _dump_window_state(main, f"�������ǰ mode={mode}")

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
    _send_unicode("���A��")
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
    """�������Ҽ�����������ز˵��ߴ磬�����ж��Ƿ���Ľ�������ҳ��"""
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
    """���Ҽ������˵������ҡ��������ݡ��С�"""
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
    """�ڲ˵���ͼ��ģ��ƥ�����ı�ǩ��������Բ˵����� y ���ġ�"""
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
        # ��ɫ�˵������ǳ�֣�ǳɫ�˵���ǳ�����֣����� 2.1 ����ǳɫ�Ҽ���
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
    """���Ҽ��˵���ġ��������ݡ�����ģ��ƥ�䣬ʧ���ٰ���������ʽ��"""
    import win32gui

    img = _popup_screenshot(popup)
    y_rel = _match_label_y(img, "��������")
    left, top, right, bottom = win32gui.GetWindowRect(popup)
    if y_rel is None:
        # ǳɫ�˵������������һ�������ܿ� bands��
        # �˵�˳�򣺵����п��������С��������ݡ� �� �� 3 ��Լ�ڸ߶� 12%~18%
        left0, top0, right0, bottom0 = left, top, right, bottom
        height = max(1, bottom0 - top0)
        bands = _menu_bands(popup)
        if len(bands) >= 6:
            # ��ɫ�˵���Ŀ������ʱ���� 3 ��
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
            "δ��⵽����֤ȯ QMT ���׶˴��ڡ����ȴ򿪲���¼ QMT �ͻ��ˣ�Ȼ�����ܱ�������"
        )
    if not _prompt_before_control():
        raise RuntimeError("�û�ȡ����δ��ʼ���� QMT")
    succeeded = False
    try:
        _export_via_ui_controlled(dest, main)
        succeeded = True
    finally:
        if succeeded or not _NO_MINIMIZE_ON_FAIL:
            _minimize_qmt(main)
        else:
            print("[���鵼��] ʧ���� --no-minimize-on-fail������ QMT ���ڱ��ں˶�", flush=True)


def _export_via_ui_controlled(dest: Path, main) -> None:
    import win32gui

    # �Զ������� �� ���������A�ɡ����û�ֻ�豣֤�ͻ��˿��ţ�
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
    # ��������в����ܿ�������������¹�����
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
                    # ������Ҽ����������ݡ��˵��ܸߣ�ģ�ͽ��׶̲˵�ͨ�� <200��ֱ�Ӷ���
                    if 40 <= w2 <= 600 and 200 <= h2 <= 900:
                        popup = hwnd
                        break
        if popup:
            break
        time.sleep(0.3)

    if not popup:
        # ��ͣ��ģ�ͽ��׵�ҳʱ���ٵ���һ�κ������Ҽ�
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
        _dump_window_state(main, "�Ҽ��˵�ʧ��ʱ����")
        raise RuntimeError("�����û�е����Ҽ��˵�")
    before_save = _visible_windows()
    if not _click_export_menu_item(popup):
        _press_escape()
        _dump_window_state(main, "�����˵����ʧ��ʱ����")
        raise RuntimeError("�Ҽ��˵����Ҳ������������ݡ�")
    dialog = None
    deadline = time.time() + 4.0
    while time.time() < deadline and dialog is None:
        time.sleep(0.15)
        for hwnd in _visible_windows() - before_save:
            title = win32gui.GetWindowText(hwnd) or ""
            if "����Ϊ" in title or "����" in title or win32gui.GetClassName(hwnd) == "#32770":
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
        raise RuntimeError("û�г��ֵ��������")
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
        raise RuntimeError("�������û���ļ����򱣴水ť")
    _confirm_overwrite()
    deadline = time.time() + 20.0
    while time.time() < deadline and not dest.is_file():
        time.sleep(0.2)
    if not dest.is_file() or dest.stat().st_size < 1000:
        dest.unlink(missing_ok=True)
        raise RuntimeError("�����ļ�û��д����")
    rows = read_table(dest)
    if not rows or "����" not in rows[0] or "��" not in rows[0]:
        dest.unlink(missing_ok=True)
        raise RuntimeError("�����Ĳ����������ȱ�ٴ����񿪣�")


def _log_quote_artifact(tag: str, path: Path | None = None) -> None:
    """��ӡĿ�������ļ��Ƿ���ڼ� mtime������;� qmt_hsj_quote.txt ������"""
    p = path or QUOTE_PATH
    try:
        if p.is_file():
            st = p.stat()
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[���鵼��] {tag}: ���� {p} size={st.st_size} mtime={mtime}",
                flush=True,
            )
        else:
            print(f"[���鵼��] {tag}: ������ {p}", flush=True)
    except Exception as exc:
        print(f"[���鵼��] {tag}: ��ȡ {p} ʧ��: {exc}", flush=True)


def main() -> int:
    global _NAV_MODE, _CONTROL_WARN_SECONDS, _NO_MINIMIZE_ON_FAIL
    ap = argparse.ArgumentParser(description="���� QMT ���A�������")
    ap.add_argument("--from-file", default="", help="���Ѿ������ı��񣬲���ȥ�� QMT")
    ap.add_argument("--click", action="store_true", help="�Զ��е�����ҳ�����A�ɡ����Ҽ�������ֻ�� QMT �Ѵ򿪵�¼��")
    ap.add_argument("--date", default="", help="������ YYYY-MM-DD��Ĭ�ϰ�����ʱ��")
    ap.add_argument(
        "--nav-mode",
        default="AB",
        choices=["legacy", "A", "B", "AB", "a", "b", "ab"],
        help="������legacy|A|B|AB��Ĭ�� AB��Maximize+�����������飩",
    )
    ap.add_argument(
        "--nav-probe",
        action="store_true",
        help="ֻ�⵼���������Ҽ����˵������������ļ�",
    )
    ap.add_argument(
        "--control-wait",
        type=int,
        default=None,
        help="����ǰ��ʾ����ʱ������Ĭ�� 12��0=������ʼ��",
    )
    ap.add_argument(
        "--no-minimize-on-fail",
        action="store_true",
        help="����/����ʧ��ʱ����С�� QMT���ɹ�����С����ȡ�����Ͳ���С����",
    )
    args = ap.parse_args()
    _NAV_MODE = str(args.nav_mode).upper()
    _NO_MINIMIZE_ON_FAIL = bool(args.no_minimize_on_fail)
    if args.control_wait is not None:
        _CONTROL_WARN_SECONDS = max(0, int(args.control_wait))
    if args.date:
        day = datetime.strptime(args.date[:10], "%Y-%m-%d").date()
    else:
        day = resolve_asof_day()
    day_s = day.isoformat()
    wrote_new = False
    try:
        if args.from_file:
            count = install_table(Path(args.from_file), day_s, "file")
            wrote_new = True
        elif args.nav_probe:
            _ensure_dpi_aware()
            main_hwnd = _find_live_qmt()
            if not main_hwnd:
                raise RuntimeError("δ��⵽����֤ȯ QMT ���׶˴��ڡ�")
            if not _prompt_before_control():
                print("[nav-probe] �û�ȡ����δ��ʼ���� QMT", flush=True)
                return 1
            print(f"[nav-probe] mode={_NAV_MODE}", flush=True)
            probe_ok = False
            try:
                _navigate_to_hsj_a_shares(main_hwnd)
                probe = _probe_quote_table_menu(main_hwnd)
                probe_ok = bool(probe.get("ok"))
                print(f"[nav-probe] done ok={probe.get('ok')} mode={_NAV_MODE}", flush=True)
                if not probe_ok:
                    _dump_window_state(main_hwnd, "nav-probeʧ��ʱ����")
                return 0 if probe_ok else 2
            finally:
                if probe_ok or not _NO_MINIMIZE_ON_FAIL:
                    _minimize_qmt(main_hwnd)
                else:
                    print(
                        "[nav-probe] ʧ���� --no-minimize-on-fail������ QMT ���ڱ��ں˶�",
                        flush=True,
                    )
        elif args.click:
            _log_quote_artifact("����ǰĿ���ļ�", QUOTE_PATH)
            export_via_ui(_INCOMING)
            _log_quote_artifact("UI��д�� incoming", _INCOMING)
            count = install_table(_INCOMING, day_s, "qmt_ui")
            wrote_new = True
            _log_quote_artifact("�Ѱ�װΪ��ʽ�����", QUOTE_PATH)
        else:
            from utils.qmt_hsj_quote import file_covers_day, read_table, universe_codes

            if not file_covers_day(day_s):
                raise RuntimeError(
                    "��û�е���Ļ������������ QMT ������: python tools/export_qmt_hsj_quote.py --click"
                )
            count = len(universe_codes(read_table(QUOTE_PATH)))
            _log_quote_artifact("ʹ�����������", QUOTE_PATH)
    except Exception as exc:
        print(f"[���鵼��] ʧ��: {exc}", flush=True)
        print("[���鵼��] ����δд���������ļ����������Կ����Ǿ��ļ���", flush=True)
        _log_quote_artifact("ʧ�ܺ�Ŀ���ļ�", QUOTE_PATH)
        _log_quote_artifact("ʧ�ܺ� incoming", _INCOMING)
        if not args.from_file:
            print(f"[���鵼��] {_LOGIN_HINT}", flush=True)
        return 1
    if wrote_new:
        print(f"[���鵼��] ������д������װ������: {day_s} ����={count} -> {QUOTE_PATH}", flush=True)
        _log_quote_artifact("�ɹ���Ŀ���ļ�", QUOTE_PATH)
    else:
        print(f"[���鵼��] {day_s} ����={count} -> {QUOTE_PATH.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
