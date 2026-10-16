# -*- coding: utf-8 -*-
"""从 QMT 交易端读取持仓、当日委托和账户资产。

持仓和委托在「模型交易」里右键导出。可用资金在「我的」的资产栏。
不点买入、卖出、撤单、清仓。导出里的账号列当场丢掉，不写进结果。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[1]
_TAB_DIR = Path(__file__).resolve().parent / "qmt_trade_tabs"
_EXPORT_DIR = _ROOT / "data" / "qmt_model_trade"

_LAST_NOTE = ""
_LAST_ASSET: Dict[str, float] = {}
_LAST_HEADER = ""
_LAST_HOLD_FP = ""
_LAST_HOLD_POS: Optional[Dict[str, Dict]] = None
_LAST_ORDER_FP = ""
_LAST_ORDER_ROWS: Optional[List[Dict]] = None
_CURSOR_DEPTH = 0
_CURSOR_POS = None
_FORE_HWND = None

_DROP_HEADER = ("账号", "股东", "身份证", "手机", "电话", "客户姓名", "姓名")
_FORBIDDEN = ("买入", "卖出", "撤单", "清仓", "闪电买", "闪电卖")


def last_note() -> str:
    return _LAST_NOTE


def last_asset() -> Dict[str, float]:
    return dict(_LAST_ASSET)


def last_header() -> str:
    return _LAST_HEADER


def hold_cursor():
    """一次刷新里读持仓和委托时包住，光标和前台窗口在全部点完后回到原处。"""
    return _CursorHold()


class _CursorHold:
    def __enter__(self):
        _remember_cursor()
        return self

    def __exit__(self, *_exc):
        _restore_cursor()
        return False


def _remember_cursor() -> None:
    global _CURSOR_DEPTH, _CURSOR_POS, _FORE_HWND
    if _CURSOR_DEPTH == 0:
        try:
            import win32api
            import win32gui

            _CURSOR_POS = win32api.GetCursorPos()
            _FORE_HWND = win32gui.GetForegroundWindow()
        except Exception:
            _CURSOR_POS = None
            _FORE_HWND = None
    _CURSOR_DEPTH += 1


def _restore_cursor() -> None:
    global _CURSOR_DEPTH, _CURSOR_POS, _FORE_HWND
    if _CURSOR_DEPTH <= 0:
        return
    _CURSOR_DEPTH -= 1
    if _CURSOR_DEPTH > 0:
        return
    pos = _CURSOR_POS
    fore = _FORE_HWND
    _CURSOR_POS = None
    _FORE_HWND = None
    try:
        import win32api
        import win32gui

        if pos:
            win32api.SetCursorPos((int(pos[0]), int(pos[1])))
        if fore and win32gui.IsWindow(fore):
            try:
                win32gui.SetForegroundWindow(fore)
            except Exception:
                pass
    except Exception:
        pass


def _find_main():
    import win32gui

    found = None

    def top(hwnd, _):
        nonlocal found
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetClassName(hwnd) != "Qt5QWindowIcon":
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if "国金证券QMT交易端" in title and "模拟" not in title:
            found = hwnd

    win32gui.EnumWindows(top, None)
    return found


def _grab(hwnd):
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
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


def _locate_patch(image, name: str, region: Tuple[int, int, int, int], min_ratio: float = 0.35):
    """在一块区域里找模板，返回模板左上角。对不上返回 None。"""
    import numpy as np
    from numpy.fft import fft2, ifft2
    from PIL import Image

    needle_path = _TAB_DIR / ("%s.png" % name)
    if not needle_path.is_file():
        return None
    x0, y0, x1, y1 = region
    hay = image.convert("L")
    x1 = min(x1, hay.size[0])
    y1 = min(y1, hay.size[1])
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    needle = Image.open(needle_path).convert("L")
    box = hay.crop((x0, y0, x1, y1))
    h = np.asarray(box, np.float32)
    n = np.asarray(needle, np.float32)
    n = n - float(n.mean())
    th, tw = n.shape
    if h.shape[0] < th or h.shape[1] < tw:
        return None
    score = np.real(ifft2(fft2(h) * np.conj(fft2(n, h.shape))))
    valid = score[: h.shape[0] - th + 1, : h.shape[1] - tw + 1]
    y, x = np.unravel_index(int(np.argmax(valid)), valid.shape)
    peak = float(valid[y, x])
    flat = float(np.mean(np.abs(n)))
    if flat <= 0 or peak < flat * n.size * min_ratio:
        return None
    return x0 + int(x), y0 + int(y), tw, th


def _locate_tab(image, name: str) -> Optional[Tuple[int, int]]:
    # 页签在窗口中部靠左，不搜整屏
    width, height = image.size
    found = _locate_patch(
        image, name, (40, 250, min(width - 40, 1100), max(260, height - 80))
    )
    if found is None:
        return None
    x, y, tw, th = found
    return x + tw // 2, y + th // 2


def _left_click(x: int, y: int) -> None:
    import win32api
    import win32con

    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _right_click(x: int, y: int) -> None:
    import win32api
    import win32con

    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)


def _press_escape() -> None:
    import win32api
    import win32con

    win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
    win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)


def _visible_windows():
    import win32gui

    found = set()

    def top(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            found.add(hwnd)

    win32gui.EnumWindows(top, None)
    return found


def _menu_export_point(hwnd) -> Optional[Tuple[int, int]]:
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width < 40 or height < 80:
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
        for x in range(8, max(9, img.size[0] - 8)):
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
    # 模型交易表格右键固定 7 行，第 6 行是「导出数据」
    if len(bands) != 7:
        return None
    return (left + right) // 2, top + bands[5]


def _filename_edit(dialog):
    import win32gui

    found = []

    def walk(hwnd, _):
        if win32gui.GetClassName(hwnd) != "ComboBoxEx32":
            return

        def sub(child, __):
            if win32gui.GetClassName(child) == "Edit":
                found.append(child)

        win32gui.EnumChildWindows(hwnd, sub, None)

    win32gui.EnumChildWindows(dialog, walk, None)
    return found[-1] if found else None


def _click_button_text(dialog, prefix: str) -> bool:
    import win32gui

    hit = []

    def walk(hwnd, _):
        if win32gui.GetClassName(hwnd) != "Button":
            return
        text = (win32gui.GetWindowText(hwnd) or "").replace(" ", "")
        if any(word in text for word in _FORBIDDEN):
            return
        if text.startswith(prefix):
            hit.append(hwnd)

    win32gui.EnumChildWindows(dialog, walk, None)
    if not hit:
        return False
    left, top, right, bottom = win32gui.GetWindowRect(hit[0])
    _left_click((left + right) // 2, (top + bottom) // 2)
    return True


def _paste_filename(edit, path: Path) -> None:
    import win32api
    import win32clipboard
    import win32con
    import win32gui

    prev = None
    win32clipboard.OpenClipboard()
    try:
        try:
            prev = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        except Exception:
            prev = None
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(str(path))
    finally:
        win32clipboard.CloseClipboard()
    left, top, right, bottom = win32gui.GetWindowRect(edit)
    _left_click((left + right) // 2, (top + bottom) // 2)
    time.sleep(0.08)

    def chord(key: int) -> None:
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(key, 0, 0, 0)
        win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

    chord(ord("A"))
    time.sleep(0.05)
    chord(ord("V"))
    time.sleep(0.05)
    if prev is None:
        return
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(prev)
    finally:
        win32clipboard.CloseClipboard()


def _dismiss_export_success() -> None:
    """关掉 QMT 自己弹出的「数据导出成功」。只点这个框右下角的确定。"""
    import win32gui

    end = time.time() + 2.5
    while time.time() < end:
        found = []

        def top(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            if (win32gui.GetWindowText(hwnd) or "") != "数据导出":
                return
            if "Qt" not in win32gui.GetClassName(hwnd):
                return
            found.append(hwnd)

        win32gui.EnumWindows(top, None)
        for hwnd in found:
            if _click_export_ok(hwnd):
                return
        time.sleep(0.12)


def _click_export_ok(hwnd) -> bool:
    import win32gui

    image = _grab(hwnd)
    width, height = image.size
    if width < 80 or height < 60:
        return False
    px = image.load()
    xs = []
    ys = []
    for y in range(int(height * 0.55), height - 6):
        for x in range(int(width * 0.45), width - 8):
            red, green, blue = px[x, y][:3]
            if blue > 180 and blue > red + 40 and blue > green + 15:
                xs.append(x)
                ys.append(y)
    if len(xs) < 40:
        return False
    left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
    _left_click(left + (min(xs) + max(xs)) // 2, top + (min(ys) + max(ys)) // 2)
    time.sleep(0.25)
    return not win32gui.IsWindowVisible(hwnd)


def _confirm_overwrite() -> None:
    import win32gui

    end = time.time() + 1.5
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
            if _click_button_text(hwnd, "是"):
                return
        time.sleep(0.12)


def _save_dialog_to(dialog, dest: Path) -> bool:
    edit = _filename_edit(dialog)
    if not edit:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    _paste_filename(edit, dest)
    if not _click_button_text(dialog, "保存"):
        return False
    _confirm_overwrite()
    deadline = time.time() + 8.0
    while time.time() < deadline and not (dest.is_file() and dest.stat().st_size > 20):
        time.sleep(0.15)
    return dest.is_file() and dest.stat().st_size > 20


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("gbk", "replace")


def _redact(path: Path) -> Tuple[List[str], List[List[str]]]:
    """丢掉账号一类的列，再写回。返回表头和数据行。"""
    text = _read_text(path)
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return [], []
    delim = "\t" if "\t" in lines[0] else ","
    header = [cell.strip() for cell in lines[0].split(delim)]
    keep = [
        i
        for i, name in enumerate(header)
        if name and not any(word in name for word in _DROP_HEADER)
    ]
    rows = []
    for line in lines[1:]:
        cells = [cell.strip() for cell in line.split(delim)]
        if len(cells) < len(header):
            cells.extend([""] * (len(header) - len(cells)))
        rows.append([cells[i] for i in keep])
    header = [header[i] for i in keep]
    body = "\n".join(
        [delim.join(header)] + [delim.join(row) for row in rows]
    )
    path.write_text(body + "\n", encoding="utf-8")
    return header, rows


def _cell(header: List[str], row: List[str], *names: str) -> str:
    for name in names:
        if name in header:
            i = header.index(name)
            if i < len(row):
                return row[i].strip()
    return ""


def _as_float(text: str) -> float:
    raw = (text or "").replace(",", "").replace("%", "").strip()
    if raw in ("", "-", "—"):
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _as_int(text: str) -> int:
    try:
        return max(0, int(float((text or "0").replace(",", "").strip() or 0)))
    except ValueError:
        return 0


def _code6(text: str) -> str:
    digits = "".join(ch for ch in str(text or "") if ch.isdigit())
    return digits[:6] if len(digits) >= 6 else ""


def _export_tab(tab_name: str, filename: str) -> Optional[Path]:
    import win32con
    import win32gui

    main = _find_main()
    if not main:
        return None
    if win32gui.IsIconic(main):
        win32gui.ShowWindow(main, win32con.SW_RESTORE)
        time.sleep(0.3)
    try:
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass
    time.sleep(0.15)
    _press_escape()
    time.sleep(0.15)
    image = _grab(main)
    spot = _locate_tab(image, tab_name)
    if not spot:
        _click_nav(main, 4)
        time.sleep(0.35)
        image = _grab(main)
        spot = _locate_tab(image, tab_name)
    if not spot:
        _press_escape()
        return None
    left, top, _right, _bottom = win32gui.GetWindowRect(main)
    _left_click(left + spot[0], top + spot[1])
    time.sleep(0.35)
    before = _visible_windows()
    _right_click(left + spot[0] + 280, top + spot[1] + 150)
    popup = None
    deadline = time.time() + 2.2
    while time.time() < deadline and popup is None:
        time.sleep(0.12)
        for hwnd in _visible_windows() - before:
            if "Popup" in win32gui.GetClassName(hwnd):
                popup = hwnd
                break
    if not popup:
        _press_escape()
        return None
    point = _menu_export_point(popup)
    if not point:
        _press_escape()
        return None
    before_save = _visible_windows()
    _left_click(*point)
    dialog = None
    deadline = time.time() + 4.0
    while time.time() < deadline and dialog is None:
        time.sleep(0.12)
        for hwnd in _visible_windows() - before_save:
            title = win32gui.GetWindowText(hwnd) or ""
            if "导出数据" in title or "另存为" in title:
                dialog = hwnd
                break
    dest = _EXPORT_DIR / filename
    if not dialog or not _save_dialog_to(dialog, dest):
        _press_escape()
        if dest.exists() and dest.stat().st_size < 30:
            dest.unlink()
        return None
    _dismiss_export_success()
    return dest


def _hold_fp(positions: Dict[str, Dict]) -> str:
    rows = []
    for code in sorted(positions):
        row = positions[code]
        rows.append(
            (
                code,
                row.get("stock_name") or "",
                int(row.get("volume") or 0),
                int(row.get("can_use_volume") or 0),
                round(float(row.get("open_price") or 0), 4),
            )
        )
    return repr(rows)


def _order_fp(fills: List[Dict]) -> str:
    rows = []
    for row in fills:
        rows.append(
            (
                str(row.get("order_id") or ""),
                str(row.get("time") or ""),
                str(row.get("stock_code") or ""),
                str(row.get("side") or ""),
                str(row.get("status") or ""),
                int(row.get("volume") or 0),
                int(row.get("trade_volume") or 0),
                round(float(row.get("price") or 0), 4),
            )
        )
    return repr(rows)


def _parse_holdings(header: List[str], rows: List[List[str]]) -> Dict[str, Dict]:
    from utils.daily_cache_reader import to_full_stock_code

    if not any(name in header for name in ("证券代码", "代码")):
        return {}
    out: Dict[str, Dict] = {}
    market = 0.0
    for parts in rows:
        code6 = _code6(_cell(header, parts, "证券代码", "代码"))
        if len(code6) != 6:
            continue
        name = _cell(header, parts, "证券名称", "名称")
        if name in ("合计", "汇总"):
            continue
        volume = _as_int(
            _cell(header, parts, "当前拥股", "证券数量", "持仓数量", "股票余额", "当前持仓")
        )
        can_use_text = _cell(header, parts, "可用数量", "可卖数量", "可用余额", "可卖")
        can_use = _as_int(can_use_text) if can_use_text else volume
        cost = _as_float(_cell(header, parts, "成本价", "摊薄成本", "参考成本价"))
        mv = _as_float(_cell(header, parts, "市值", "最新市值", "参考市值"))
        full = to_full_stock_code(code6)
        prev = out.get(full)
        if prev:
            prev["volume"] = int(prev["volume"]) + volume
            prev["can_use_volume"] = int(prev["can_use_volume"]) + can_use
            if mv:
                prev["market_value"] = float(prev["market_value"]) + mv
        else:
            out[full] = {
                "stock_code": full,
                "stock_name": name,
                "volume": volume,
                "can_use_volume": can_use,
                "open_price": cost,
                "market_value": mv,
            }
        market += mv
    global _LAST_ASSET
    # 持仓表没有可用资金。这里只在还没读到「我的」资产栏时，暂记一笔市值。
    if market > 0 and "cash" not in _LAST_ASSET:
        kept = dict(_LAST_ASSET)
        kept["market_value"] = market
        _LAST_ASSET = kept
    return out


_DIGIT_FONT = None


def _digit_font():
    """QMT 资产栏上这套数字的样子。文件名 dot 是小数点。"""
    global _DIGIT_FONT
    if _DIGIT_FONT is not None:
        return _DIGIT_FONT
    from PIL import Image

    glyphs = {}
    folder = _TAB_DIR / "digits"
    for name in list("0123456789") + ["dot"]:
        path = folder / ("%s.png" % name)
        if path.is_file():
            glyphs["." if name == "dot" else name] = Image.open(path).convert("L")
    _DIGIT_FONT = glyphs
    return glyphs


def _tight_bright(image):
    from PIL import Image

    rgb = image.convert("RGB")
    bright = Image.new("L", rgb.size, 0)
    src = rgb.load()
    dst = bright.load()
    for y in range(rgb.size[1]):
        for x in range(rgb.size[0]):
            dst[x, y] = max(src[x, y])
    box = bright.point(lambda p: 255 if p > 140 else 0).getbbox()
    if not box:
        return None
    return bright.crop(box)


def _score_glyph(seen, tmpl) -> float:
    from PIL import Image

    def norm(im):
        height = 10
        width = max(1, int(round(im.size[0] * height / max(im.size[1], 1))))
        return im.resize((width, height), Image.BILINEAR)

    left, right = norm(seen), norm(tmpl)
    if abs(left.size[0] - right.size[0]) > 3:
        return 0.0
    width = max(left.size[0], right.size[0])

    def pad(im):
        canvas = Image.new("L", (width, 10), 0)
        canvas.paste(im, ((width - im.size[0]) // 2, 0))
        return list(canvas.getdata())

    aa, bb = pad(left), pad(right)
    inter = sum(1 for u, v in zip(aa, bb) if u > 100 and v > 100)
    union = sum(1 for u, v in zip(aa, bb) if u > 100 or v > 100)
    return inter / union if union else 0.0


def _classify_digit(crop) -> str:
    seen = _tight_bright(crop)
    if seen is None:
        return ""
    if seen.size[0] <= 3 and seen.size[1] <= 4:
        return "."
    best_ch, best_score = "", 0.0
    for ch, glyph in _digit_font().items():
        if ch == ".":
            continue
        score = _score_glyph(seen, glyph)
        if score > best_score:
            best_ch, best_score = ch, score
    if best_score < 0.55:
        return ""
    return best_ch


def _amount_in(image, x0: int, x1: int, y0: int, y1: int):
    """读一块区域里的金额。界面按万位插空格，这里把空格去掉。"""
    if x1 <= x0 or y1 <= y0:
        return None
    px = image.load()
    width = image.size[0]
    height = image.size[1]
    x0 = max(0, x0)
    x1 = min(width, x1)
    y0 = max(0, y0)
    y1 = min(height, y1)
    columns = []
    for x in range(x0, x1):
        ink = 0
        for y in range(y0, y1):
            red, green, blue = px[x, y][:3]
            if max(red, green, blue) > 150 and red + green + blue > 280:
                ink += 1
        # 竖线分隔会整列都亮，数字不会
        columns.append(ink >= 2 and ink <= (y1 - y0) * 0.8)
    spans = []
    start = None
    for i, on in enumerate(columns + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if 1 <= i - start <= 10:
                spans.append((x0 + start, x0 + i))
            start = None
    if len(spans) < 3:
        return None
    groups = [[spans[0]]]
    for span in spans[1:]:
        if span[0] - groups[-1][-1][1] <= 8:
            groups[-1].append(span)
        else:
            groups.append([span])
    best = None
    for group in groups:
        chars = []
        for left, right in group:
            chars.append(_classify_digit(image.crop((left, y0, right, y1))))
        text = "".join(chars)
        if text.count(".") != 1:
            continue
        whole, frac = text.split(".", 1)
        if not whole.isdigit() or not frac.isdigit() or len(frac) != 2:
            continue
        value = float(text)
        if value < 0:
            continue
        best = value
    return best


def _asset_from_image(image) -> Optional[Dict[str, float]]:
    width, height = image.size
    found = _locate_patch(
        image,
        "label_cash",
        (int(width * 0.68), 40, width - 4, min(height - 40, 420)),
        12.0,
    )
    if found is None:
        return None
    x, y, tw, th = found
    cash = _amount_in(image, x + tw + 2, x + tw + 160, y - 2, y + th + 2)
    total = _amount_in(image, x - 210, x - 6, y - 2, y + th + 2)
    market = _amount_in(image, x - 210, x - 6, y + th + 4, y + th * 2 + 16)
    if cash is None or total is None or market is None:
        return None
    if cash <= 0 or total <= 0 or market < 0:
        return None
    if abs(total - cash - market) > 1.0:
        return None
    return {
        "cash": cash,
        "market_value": market,
        "total_asset": total,
    }


_NAV_X = 44
_NAV_SLOTS = (87, 167, 247, 327, 407)


def _nav_y(image, index: int) -> int:
    """左侧五个按钮。只拿单个按钮那么高的蓝底来对齐，避免两个按钮连成一块时把整列带偏。"""
    slots = list(_NAV_SLOTS)
    px = image.load()
    runs = []
    start = None
    limit = min(image.size[1] - 10, 560)
    for y in range(30, limit + 1):
        selected = False
        if y < limit:
            red, green, blue = px[_NAV_X, y][:3]
            selected = blue > 180 and blue > red + 40 and blue > green + 20
        if selected and start is None:
            start = y
        elif not selected and start is not None:
            if 50 <= y - start <= 110:
                runs.append((start, y - 1))
            start = None
    if not runs:
        return slots[index]
    expected = slots[index]
    top, bottom = min(runs, key=lambda item: abs((item[0] + item[1]) / 2 - expected))
    center = (top + bottom) // 2
    nearest = min(slots, key=lambda slot: abs(slot - center))
    delta = center - nearest
    if abs(delta) > 20:
        delta = 0
    return slots[index] + delta


def _current_nav(image) -> int:
    """当前打开的是左侧哪一页。模型交易在策略运行时常亮，有别的页亮着就以那一页为准。"""
    px = image.load()
    lit = []
    for index in range(len(_NAV_SLOTS)):
        red, green, blue = px[24, _nav_y(image, index)][:3]
        if blue > 180 and blue > red + 40 and blue > green + 20:
            lit.append(index)
    opened = [index for index in lit if index != 4]
    if opened:
        return opened[0]
    if lit:
        return lit[-1]
    return 0


def _click_nav(hwnd, index: int) -> None:
    import win32api
    import win32con
    import win32gui

    image = _grab(hwnd)
    left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
    win32api.SetCursorPos((left + _NAV_X, top + _nav_y(image, index)))
    time.sleep(0.08)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.12)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _read_asset_image(hwnd):
    """窗口右缘切掉最后一位时，临时加宽再读，读完把窗口放回原处。"""
    import win32con
    import win32gui

    image = _grab(hwnd)
    labels = _asset_from_image(image)
    if labels is not None:
        return labels
    width = image.size[0]
    found = _locate_patch(
        image,
        "label_cash",
        (int(width * 0.68), 40, width - 4, min(image.size[1] - 40, 420)),
        12.0,
    )
    if found is None or found[0] + found[2] < width - 220:
        return None
    try:
        import win32api

        monitor = win32api.GetMonitorInfo(
            win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        )
        work_right = int(monitor["Work"][2])
    except Exception:
        return None
    rect = win32gui.GetWindowRect(hwnd)
    extra = 180
    origin_x, origin_y = rect[0], rect[1]
    wide = rect[2] - rect[0] + extra
    tall = rect[3] - rect[1]
    if origin_x + wide > work_right:
        origin_x = max(0, work_right - wide)
    win32gui.SetWindowPos(
        hwnd, 0, origin_x, origin_y, wide, tall, win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
    )
    try:
        time.sleep(0.45)
        return _asset_from_image(_grab(hwnd))
    finally:
        win32gui.SetWindowPos(
            hwnd,
            0,
            rect[0],
            rect[1],
            rect[2] - rect[0],
            rect[3] - rect[1],
            win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
        )


def read_asset() -> Optional[Dict[str, float]]:
    """读「我的」里的可用金额、总市值和总资产。不在这一页时临时打开，读完回到原来的页。"""
    global _LAST_ASSET
    import win32con
    import win32gui

    main = _find_main()
    if not main:
        return None
    if win32gui.IsIconic(main):
        win32gui.ShowWindow(main, win32con.SW_RESTORE)
        time.sleep(0.3)
    try:
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass
    time.sleep(0.1)
    origin = _current_nav(_grab(main))
    labels = _read_asset_image(main)
    if labels is None and origin != 0:
        _click_nav(main, 0)
        time.sleep(0.45)
        labels = _read_asset_image(main)
        _click_nav(main, origin)
        time.sleep(0.25)
    if not labels:
        return None
    _LAST_ASSET = {
        "cash": float(labels["cash"]),
        "market_value": float(labels["market_value"]),
        "total_asset": float(labels["total_asset"]),
    }
    return dict(_LAST_ASSET)


def _status_text(raw: str) -> str:
    text = (raw or "").strip()
    if text.isdigit():
        from qmt_builtin.src.ant_account_snapshot import ORDER_STATUS_TEXT

        return ORDER_STATUS_TEXT.get(int(text), text)
    return text


def _parse_orders(header: List[str], rows: List[List[str]]) -> List[Dict]:
    from utils.daily_cache_reader import to_full_stock_code

    if not any(name in header for name in ("证券代码", "代码")):
        return []
    if not any(name in header for name in ("委托价格", "价格", "委托价")):
        return []
    out: List[Dict] = []
    for parts in rows:
        code6 = _code6(_cell(header, parts, "证券代码", "代码"))
        if len(code6) != 6:
            continue
        order_volume = _as_int(_cell(header, parts, "委托数量", "委托量", "委托股数"))
        trade_volume = _as_int(_cell(header, parts, "成交数量", "成交量"))
        if order_volume <= 0 and trade_volume <= 0:
            continue
        side = _cell(header, parts, "买卖标记", "买卖方向", "委托方向", "方向", "操作") or "委托"
        out.append(
            {
                "stock_code": to_full_stock_code(code6),
                "stock_name": _cell(header, parts, "证券名称", "名称"),
                "time": _cell(header, parts, "委托时间", "报单时间", "时间"),
                "side": side,
                "status": _status_text(
                    _cell(header, parts, "委托状态", "状态说明", "状态")
                ),
                "price": _as_float(_cell(header, parts, "委托价格", "委托价", "价格")),
                "trade_price": _as_float(
                    _cell(header, parts, "成交均价", "成交价格", "成交价")
                ),
                "volume": order_volume,
                "trade_volume": trade_volume,
                "order_id": _cell(
                    header, parts, "合同编号", "委托编号", "订单编号", "柜台委托号", "合同号"
                ),
                "note": _cell(header, parts, "废单原因", "备注"),
            }
        )
    return out


def read_holdings() -> Optional[Dict[str, Dict]]:
    """读取模型交易持仓。数量和成本没变就沿用上一次。失败返回 None。"""
    global _LAST_NOTE, _LAST_HEADER, _LAST_HOLD_FP, _LAST_HOLD_POS
    path = _export_tab("holdings", "holdings.txt")
    if path is None:
        _LAST_NOTE = "no-window" if _find_main() is None else "export-failed"
        return None
    try:
        header, rows = _redact(path)
    except Exception:
        _LAST_NOTE = "save-failed"
        return None
    _LAST_HEADER = " ".join(header)[:240]
    if not any(name in header for name in ("证券代码", "代码")):
        _LAST_NOTE = "bad-header"
        return None
    positions = _parse_holdings(header, rows)
    if not positions and rows:
        _LAST_NOTE = "bad-header"
        return None
    fp = _hold_fp(positions)
    if fp == _LAST_HOLD_FP and _LAST_HOLD_POS is not None:
        _LAST_NOTE = "unchanged"
        return _LAST_HOLD_POS
    _LAST_HOLD_FP = fp
    _LAST_HOLD_POS = positions
    _LAST_NOTE = "ok"
    return positions


def read_fills() -> Optional[List[Dict]]:
    """读取模型交易委托。状态没变就沿用上一次。失败返回 None；没有委托时返回空列表。"""
    global _LAST_NOTE, _LAST_HEADER, _LAST_ORDER_FP, _LAST_ORDER_ROWS
    path = _export_tab("orders", "orders.txt")
    if path is None:
        _LAST_NOTE = "no-window" if _find_main() is None else "export-failed"
        return None
    try:
        header, rows = _redact(path)
    except Exception:
        _LAST_NOTE = "save-failed"
        return None
    _LAST_HEADER = " ".join(header)[:240]
    if not any(name in header for name in ("证券代码", "代码")):
        if not rows:
            if _LAST_ORDER_ROWS == []:
                _LAST_NOTE = "unchanged"
                return []
            _LAST_ORDER_FP = _order_fp([])
            _LAST_ORDER_ROWS = []
            _LAST_NOTE = "empty"
            return []
        _LAST_NOTE = "bad-header"
        return None
    fills = _parse_orders(header, rows)
    if not fills and rows and not any(name in header for name in ("委托价格", "价格", "委托价")):
        _LAST_NOTE = "bad-header"
        return None
    fp = _order_fp(fills)
    if fp == _LAST_ORDER_FP and _LAST_ORDER_ROWS is not None:
        _LAST_NOTE = "unchanged"
        return _LAST_ORDER_ROWS
    _LAST_ORDER_FP = fp
    _LAST_ORDER_ROWS = fills
    _LAST_NOTE = "ok" if fills else "empty"
    return fills
