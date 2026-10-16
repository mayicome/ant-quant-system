# -*- coding: utf-8 -*-
"""从同花顺网上交易窗口读取资金数字、持仓和当日成交。

资金数字只读窗口文字。持仓和成交用表格自带的「保存」导出文件再读取，
不走剪贴板，避免复制验证码。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

_GRID_SAVE_CMD = 32827

_LABELS = {
    "可用金额": "cash",
    "股票市值": "market_value",
    "A股市值": "market_value",
    "总资产": "total_asset",
}


def _norm_label(text: str) -> str:
    return "".join(str(text or "").split())


def _find_main():
    import win32gui

    found = []

    def cb(hwnd, _):
        title = win32gui.GetWindowText(hwnd) or ""
        if "网上股票交易系统5.0" in title:
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def _as_float(text: str) -> Optional[float]:
    raw = str(text or "").replace(",", "").strip()
    if not raw or raw in ("-", "--"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def read_asset_labels() -> Optional[Dict[str, float]]:
    """返回 cash / market_value / total_asset。窗口不在或读不到则 None。"""
    try:
        import win32gui
    except ImportError:
        return None
    main = _find_main()
    if not main:
        return None
    statics = []

    def cb(hwnd, _):
        if win32gui.GetClassName(hwnd) != "Static":
            return
        text = (win32gui.GetWindowText(hwnd) or "").strip()
        if not text:
            return
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return
        if right <= left or bottom <= top:
            return
        statics.append((text, left, top, right))

    win32gui.EnumChildWindows(main, cb, None)
    labels = []
    values = []
    for text, left, top, right in statics:
        key = _LABELS.get(_norm_label(text))
        if key:
            labels.append((key, left, top, right))
            continue
        num = _as_float(text)
        if num is not None:
            values.append((num, left, top))
    picked: Dict[str, tuple] = {}
    for key, left, top, right in labels:
        best = None
        for num, vleft, vtop in values:
            if abs(vtop - top) > 8:
                continue
            dist = vleft - right
            # 贴在标签上的 0 是别的控件，不算这一格的金额
            if dist < 4 or dist > 140:
                continue
            score = dist + (0 if num > 0 else 50)
            if best is None or score < best[0]:
                best = (score, num)
        if best is None:
            continue
        old = picked.get(key)
        if old is None or best[0] < old[0]:
            picked[key] = best
    if "cash" not in picked and "total_asset" not in picked:
        return None
    out = {key: float(item[1]) for key, item in picked.items()}
    return out


def parse_holdings_text(text: str) -> Dict[str, Dict]:
    """把资金股票表格文本解析成持仓。键是 600358.SH 这种完整代码。"""
    from utils.daily_cache_reader import to_full_stock_code

    lines = [ln for ln in str(text or "").replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return {}
    header = lines[0].split("\t")
    index = {name.strip(): i for i, name in enumerate(header) if name.strip()}
    need = ("证券代码", "可用余额")
    if any(name not in index for name in need):
        return {}

    def cell(parts, name, default=""):
        i = index.get(name)
        if i is None or i >= len(parts):
            return default
        return parts[i].strip()

    out: Dict[str, Dict] = {}
    for line in lines[1:]:
        parts = line.split("\t")
        code6 = "".join(ch for ch in cell(parts, "证券代码") if ch.isdigit())
        if len(code6) != 6:
            continue
        full = to_full_stock_code(code6)
        try:
            volume = int(float(cell(parts, "股票余额", "0") or 0))
        except ValueError:
            volume = 0
        try:
            can_use = int(float(cell(parts, "可用余额", "0") or 0))
        except ValueError:
            can_use = 0
        cost = _as_float(cell(parts, "参考成本价")) or 0.0
        market_value = _as_float(cell(parts, "市值")) or 0.0
        out[full] = {
            "stock_code": full,
            "stock_name": cell(parts, "证券名称"),
            "volume": max(0, volume),
            "can_use_volume": max(0, can_use),
            "open_price": float(cost),
            "market_value": float(market_value),
        }
    return out


def parse_fills_text(text: str) -> Tuple[List[Dict], str]:
    """把当日成交表格文本解析成成交列表。列名对不上时 note 为 bad-header。"""
    from utils.daily_cache_reader import to_full_stock_code

    lines = [ln for ln in str(text or "").replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return [], "empty"
    header = lines[0].split("\t")
    index = {name.strip(): i for i, name in enumerate(header) if name.strip()}

    def pick(*names: str) -> Optional[str]:
        for name in names:
            if name in index:
                return name
        return None

    code_col = pick("证券代码", "股票代码")
    if not code_col:
        return [], "bad-header"
    name_col = pick("证券名称", "股票名称")
    time_col = pick("成交时间", "成交日期")
    side_col = pick("操作", "买卖标志", "买卖", "成交类型")
    price_col = pick("成交价格", "成交均价", "成交价")
    vol_col = pick("成交数量", "成交量")
    id_col = pick("合同编号", "成交编号", "委托编号", "合同号")

    def cell(parts, name, default=""):
        if not name:
            return default
        i = index.get(name)
        if i is None or i >= len(parts):
            return default
        return parts[i].strip()

    out: List[Dict] = []
    for line in lines[1:]:
        parts = line.split("\t")
        code6 = "".join(ch for ch in cell(parts, code_col) if ch.isdigit())
        if len(code6) != 6:
            continue
        price = _as_float(cell(parts, price_col)) or 0.0
        try:
            volume = int(float((cell(parts, vol_col, "0") or "0").replace(",", "")))
        except ValueError:
            volume = 0
        if price <= 0 and volume <= 0:
            continue
        side = cell(parts, side_col) or "成交"
        out.append(
            {
                "stock_code": to_full_stock_code(code6),
                "stock_name": cell(parts, name_col),
                "time": cell(parts, time_col),
                "side": side,
                "price": float(price),
                "volume": max(0, volume),
                "order_id": cell(parts, id_col),
            }
        )
    return out, "ok"


def _captcha_dialog():
    """返回验证码对话框句柄。窗口最小化时它可能不可见，也要认出来。"""
    try:
        import win32gui
    except ImportError:
        return None
    found = []

    def cb(hwnd, _):
        bits = []

        def child(ch, _):
            title = win32gui.GetWindowText(ch) or ""
            if title:
                bits.append(title)

        win32gui.EnumChildWindows(hwnd, child, None)
        blob = "".join(bits)
        if "验证码" in blob and "拷贝" in blob:
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def captcha_dialog_open() -> bool:
    """复制验证码对话框还在时，不再继续复制。"""
    return _captcha_dialog() is not None


def dismiss_captcha_dialog() -> bool:
    """只点「取消」。不输入、不点确定。"""
    try:
        import win32api
        import win32con
        import win32gui
    except ImportError:
        return False
    dlg = _captcha_dialog()
    if not dlg:
        return False
    cancel = []

    def child(hwnd, _):
        if win32gui.GetClassName(hwnd) == "Button" and (win32gui.GetWindowText(hwnd) or "") == "取消":
            cancel.append(hwnd)

    win32gui.EnumChildWindows(dlg, child, None)
    if not cancel:
        return False
    main = _find_main()
    if main and win32gui.IsIconic(main):
        win32gui.ShowWindow(main, win32con.SW_RESTORE)
    left, top, right, bottom = win32gui.GetWindowRect(cancel[0])
    if right <= left or bottom <= top:
        return False
    win32api.SetCursorPos(((left + right) // 2, (top + bottom) // 2))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    return True


def _focus_class(main) -> str:
    import ctypes
    import win32gui

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint),
            ("flags", ctypes.c_uint),
            ("hwndActive", ctypes.c_void_p),
            ("hwndFocus", ctypes.c_void_p),
            ("hwndCapture", ctypes.c_void_p),
            ("hwndMenuOwner", ctypes.c_void_p),
            ("hwndMoveSize", ctypes.c_void_p),
            ("hwndCaret", ctypes.c_void_p),
            ("rcCaret", ctypes.c_int * 4),
        ]

    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    tid = ctypes.windll.user32.GetWindowThreadProcessId(main, None)
    if not ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return ""
    if not info.hwndFocus:
        return ""
    try:
        return win32gui.GetClassName(info.hwndFocus)
    except Exception:
        return ""


def _bring_to_front(main) -> None:
    import ctypes
    import win32gui

    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    tgt_tid = user32.GetWindowThreadProcessId(main, None)
    user32.AttachThreadInput(fg_tid, tgt_tid, True)
    try:
        user32.ShowWindow(main, 9)
        user32.SetForegroundWindow(main)
    finally:
        user32.AttachThreadInput(fg_tid, tgt_tid, False)
    try:
        win32gui.SetForegroundWindow(main)
    except Exception:
        pass


_LAST_HOLDINGS_NOTE = ""
_LAST_FILLS_HEADER = ""


def _click_query_page(tree, page_name: str) -> bool:
    try:
        tree.get_item([r"查询[F4]", page_name]).click_input()
        return True
    except Exception:
        pass
    try:
        query = tree.get_item(r"查询[F4]")
        for child in query.children():
            if page_name in (child.text() or ""):
                child.click_input()
                return True
    except Exception:
        return False
    return False


def _export_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "data", "ths_export")
    os.makedirs(path, exist_ok=True)
    return path


def _find_save_dialog():
    import win32gui

    found = []

    def top(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and (win32gui.GetWindowText(hwnd) or "") == "另存为":
            found.append(hwnd)

    win32gui.EnumWindows(top, None)
    return found[0] if found else None


def _filename_edit(dlg):
    """另存为对话框里的文件名输入框。地址栏那一格不算。"""
    import win32gui

    edits = []

    def walk(hwnd, under_addr=False):
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd) or ""
        addr = under_addr or cls == "ComboBoxEx32" or "地址" in title
        if cls == "Edit" and not addr:
            edits.append(hwnd)
        child = win32gui.FindWindowEx(hwnd, 0, None, None)
        while child:
            walk(child, addr)
            child = win32gui.FindWindowEx(hwnd, child, None, None)

    walk(dlg)
    return edits[0] if edits else None


def _click_button(hwnd) -> None:
    import win32gui

    win32gui.PostMessage(hwnd, 0x00F5, 0, 0)


def _confirm_overwrite() -> bool:
    import win32gui

    clicked = False

    def top(hwnd, _):
        nonlocal clicked
        if clicked or not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetClassName(hwnd) != "#32770":
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if "另存为" in title and title != "确认另存为":
            return
        yes = []

        def child(ch, _):
            if win32gui.GetClassName(ch) == "Button" and (win32gui.GetWindowText(ch) or "") in ("是(&Y)", "是"):
                yes.append(ch)

        win32gui.EnumChildWindows(hwnd, child, None)
        if yes and ("替换" in title or "确认" in title or "已存在" in title):
            _click_button(yes[0])
            clicked = True

    win32gui.EnumWindows(top, None)
    return clicked


def _cancel_save_dialog() -> None:
    import win32gui

    dlg = _find_save_dialog()
    if not dlg:
        return
    cancel = None

    def find_cancel(hwnd, _):
        nonlocal cancel
        if win32gui.GetClassName(hwnd) == "Button" and (win32gui.GetWindowText(hwnd) or "") in ("取消", "取消(&C)"):
            cancel = hwnd

    win32gui.EnumChildWindows(dlg, find_cancel, None)
    if cancel:
        _click_button(cancel)


def _save_grid(grid_hwnd, out_path: str) -> bool:
    """让表格弹出「另存为」，存到指定路径。用 PostMessage，避免模态框把调用卡住。"""
    import time
    import win32con
    import win32gui

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    before = os.path.getmtime(out_path) if os.path.isfile(out_path) else 0
    win32gui.PostMessage(grid_hwnd, win32con.WM_COMMAND, _GRID_SAVE_CMD, 0)
    dlg = None
    for _ in range(30):
        if captcha_dialog_open():
            _cancel_save_dialog()
            return False
        dlg = _find_save_dialog()
        if dlg:
            break
        time.sleep(0.1)
    if not dlg:
        return False
    edit = _filename_edit(dlg)
    if edit:
        win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, out_path)
    save = None

    def find_save(hwnd, _):
        nonlocal save
        if win32gui.GetClassName(hwnd) == "Button" and "保存" in (win32gui.GetWindowText(hwnd) or ""):
            save = hwnd

    win32gui.EnumChildWindows(dlg, find_save, None)
    if not save:
        return False
    _click_button(save)
    for _ in range(30):
        if captcha_dialog_open():
            _cancel_save_dialog()
            return False
        _confirm_overwrite()
        if os.path.isfile(out_path):
            try:
                if os.path.getmtime(out_path) > before and os.path.getsize(out_path) > 0:
                    return True
            except OSError:
                pass
        time.sleep(0.1)
    return False


def _read_export(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="replace")


def _copy_query_grid(page_name: str) -> Optional[str]:
    """把「查询」下某一页保存成文件再读回。失败或验证码返回 None。"""
    global _LAST_HOLDINGS_NOTE
    import time

    try:
        import win32con
        import win32gui
        from pywinauto import Application
    except ImportError:
        return None
    if captcha_dialog_open():
        _LAST_HOLDINGS_NOTE = "captcha"
        return None
    main = _find_main()
    if not main:
        _LAST_HOLDINGS_NOTE = "no-window"
        return None
    was_iconic = bool(win32gui.IsIconic(main))
    prev = win32gui.GetForegroundWindow()
    if was_iconic:
        win32gui.ShowWindow(main, win32con.SW_RESTORE)
        time.sleep(0.25)
    try:
        _bring_to_front(main)
        time.sleep(0.15)
        app = Application(backend="win32").connect(handle=main)
        tree = None
        for cand in app.window(handle=main).descendants(class_name="SysTreeView32"):
            try:
                labels = [item.text() for item in cand.roots()]
            except Exception:
                continue
            if "买入[F1]" in labels and "查询[F4]" in labels:
                tree = cand
                break
        if tree is None:
            _LAST_HOLDINGS_NOTE = "no-tree"
            return None
        if not _click_query_page(tree, page_name):
            _LAST_HOLDINGS_NOTE = "no-page"
            return None
        time.sleep(0.35)
        grids = []

        def collect(hwnd, _):
            if win32gui.GetClassName(hwnd) != "CVirtualGridCtrl":
                return
            if not win32gui.IsWindowVisible(hwnd):
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left > 200 and bottom - top > 80:
                grids.append((hwnd, left, top, right, bottom))

        win32gui.EnumChildWindows(main, collect, None)
        if not grids:
            _LAST_HOLDINGS_NOTE = "no-grid"
            return None
        grid_hwnd, _left, _top, _right, _bottom = max(
            grids, key=lambda g: (g[3] - g[1]) * (g[4] - g[2])
        )
        name = "holdings.xls" if page_name == "资金股票" else "fills.xls"
        out_path = os.path.join(_export_dir(), name)
        if captcha_dialog_open():
            _LAST_HOLDINGS_NOTE = "captcha"
            return None
        if not _save_grid(grid_hwnd, out_path):
            if captcha_dialog_open():
                _LAST_HOLDINGS_NOTE = "captcha"
            else:
                _LAST_HOLDINGS_NOTE = "save-failed"
            return None
        text = _read_export(out_path)
        _LAST_HOLDINGS_NOTE = "ok"
        return text
    finally:
        if was_iconic and win32gui.IsWindow(main):
            win32gui.ShowWindow(main, win32con.SW_MINIMIZE)
        if prev and win32gui.IsWindow(prev) and prev != main:
            try:
                win32gui.SetForegroundWindow(prev)
            except Exception:
                pass


def read_holdings() -> Optional[Dict[str, Dict]]:
    """保存资金股票表再读取。失败或遇到验证码返回 None。"""
    global _LAST_HOLDINGS_NOTE
    text = _copy_query_grid("资金股票")
    if not text:
        return None
    positions = parse_holdings_text(text)
    if not positions:
        _LAST_HOLDINGS_NOTE = "parse-empty"
        return None
    _LAST_HOLDINGS_NOTE = "ok"
    return positions


def read_fills() -> Optional[List[Dict]]:
    """保存当日成交再读取。失败或验证码返回 None；今天还没有成交时返回空列表。"""
    global _LAST_HOLDINGS_NOTE, _LAST_FILLS_HEADER
    text = _copy_query_grid("当日成交")
    if text is None:
        return None
    fills, note = parse_fills_text(text)
    if note == "bad-header":
        first = ""
        for line in str(text).replace("\r\n", "\n").split("\n"):
            if line.strip():
                first = line.strip()
                break
        _LAST_FILLS_HEADER = first[:240]
        _LAST_HOLDINGS_NOTE = "bad-header"
        return None
    _LAST_HOLDINGS_NOTE = "ok" if fills else "empty"
    return fills
