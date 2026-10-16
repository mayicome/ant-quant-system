# -*- coding: utf-8 -*-
"""从国金太阳（佣金宝交易面板）读取持仓和当日委托。

只点「查询」下的「资金股份」「当日委托」。数量和委托状态没变时不再点「输出」。
需要保存时，记事本一弹出来就关掉。不点买入、卖出、撤单，也不打印。
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

_LAST_NOTE = ""
_LAST_ASSET: Dict[str, float] = {}
_LAST_HEADER = ""
_LAST_HOLD_FP = ""
_LAST_HOLD_POS: Optional[Dict[str, Dict]] = None
_LAST_HOLD_HASH = ""
_LAST_ORDER_FP = ""
_LAST_ORDER_ROWS: Optional[List[Dict]] = None
_LAST_ORDER_HASH = ""
_PEEK_HASH = ""
_PEEK_ROWS = -1


def last_note() -> str:
    return _LAST_NOTE


def last_asset() -> Dict[str, float]:
    return dict(_LAST_ASSET)


def last_header() -> str:
    return _LAST_HEADER


def _export_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "data", "gjzq_export")
    os.makedirs(path, exist_ok=True)
    return path


def _split_row(line: str) -> List[str]:
    if "\t" in line:
        return [part.strip() for part in line.split("\t")]
    return [part.strip() for part in re.split(r"\s{2,}", line.strip()) if part.strip()]


def _parse_asset(text: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    patterns = (
        ("cash", r"可用:([0-9.]+)"),
        ("market_value", r"证券市值:([0-9.]+)"),
        ("total_asset", r"参考总资产:([0-9.]+)"),
    )
    for key, pattern in patterns:
        match = re.search(pattern, text or "")
        if not match:
            continue
        try:
            out[key] = float(match.group(1))
        except ValueError:
            continue
    return out


def _table(text: str) -> Tuple[List[str], List[List[str]]]:
    lines = [ln for ln in str(text or "").replace("\r\n", "\n").split("\n") if ln.strip()]
    header_i = None
    for i, line in enumerate(lines):
        if "证券代码" in line and ("可卖数量" in line or "成交数量" in line or "买卖方向" in line):
            header_i = i
            break
    if header_i is None:
        return [], []
    header = _split_row(lines[header_i])
    rows = []
    for line in lines[header_i + 1 :]:
        if set(line.strip()) <= {"-"}:
            continue
        parts = _split_row(line)
        if parts:
            rows.append(parts)
    return header, rows


def _cell(header: List[str], parts: List[str], *names: str) -> str:
    for name in names:
        if name in header:
            i = header.index(name)
            if i < len(parts):
                return parts[i].strip()
    return ""


def parse_holdings_text(text: str) -> Dict[str, Dict]:
    """资金股份文本。可卖数量当作可用股数。"""
    from utils.daily_cache_reader import to_full_stock_code

    header, rows = _table(text)
    if "证券代码" not in header or "可卖数量" not in header:
        return {}
    out: Dict[str, Dict] = {}
    for parts in rows:
        code6 = "".join(ch for ch in _cell(header, parts, "证券代码") if ch.isdigit())
        if len(code6) != 6:
            continue
        full = to_full_stock_code(code6)

        def as_int(name: str) -> int:
            raw = _cell(header, parts, name).replace(",", "")
            try:
                return max(0, int(float(raw or 0)))
            except ValueError:
                return 0

        def as_float(name: str) -> float:
            raw = _cell(header, parts, name).replace(",", "")
            try:
                return float(raw or 0)
            except ValueError:
                return 0.0

        out[full] = {
            "stock_code": full,
            "stock_name": _cell(header, parts, "证券名称"),
            "volume": as_int("证券数量"),
            "can_use_volume": as_int("可卖数量"),
            "open_price": as_float("参考成本价"),
            "market_value": as_float("最新市值"),
        }
    return out


def _as_float(text: str) -> float:
    try:
        return float((text or "0").replace(",", ""))
    except ValueError:
        return 0.0


def _as_int(text: str) -> int:
    try:
        return max(0, int(float((text or "0").replace(",", ""))))
    except ValueError:
        return 0


def parse_fills_text(text: str) -> Tuple[List[Dict], str]:
    """当日委托文本。列名对不上时 note 为 bad-header。已报、部成、已撤、废单都保留。"""
    from utils.daily_cache_reader import to_full_stock_code

    header, rows = _table(text)
    if "证券代码" not in header or "委托价格" not in header:
        return [], "bad-header"
    out: List[Dict] = []
    for parts in rows:
        code6 = "".join(ch for ch in _cell(header, parts, "证券代码") if ch.isdigit())
        if len(code6) != 6:
            continue
        order_volume = _as_int(_cell(header, parts, "委托数量"))
        trade_volume = _as_int(_cell(header, parts, "成交数量", "成交量"))
        if order_volume <= 0 and trade_volume <= 0:
            continue
        out.append(
            {
                "stock_code": to_full_stock_code(code6),
                "stock_name": _cell(header, parts, "证券名称", "股票名称"),
                "time": _cell(header, parts, "委托时间", "成交时间", "成交日期"),
                "side": _cell(header, parts, "买卖方向", "操作", "买卖标志") or "委托",
                "status": _cell(header, parts, "状态说明", "状态"),
                "price": _as_float(_cell(header, parts, "委托价格", "成交价格", "成交均价")),
                "trade_price": _as_float(_cell(header, parts, "成交价格", "成交均价", "成交价")),
                "volume": order_volume,
                "trade_volume": trade_volume,
                "order_id": _cell(header, parts, "委托编号", "合同编号", "成交编号", "合同号"),
                "note": _cell(header, parts, "废单原因"),
            }
        )
    return out, "ok"


def _find_main():
    import win32gui

    found = []

    def top(hwnd, _):
        if win32gui.GetClassName(hwnd) != "TdxW_MainFrame_Class":
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if "国金" in title or "太阳" in title:
            found.append(hwnd)

    win32gui.EnumWindows(top, None)
    if found:
        return found[0]
    return win32gui.FindWindow("TdxW_MainFrame_Class", None)


def _output_dialog():
    import win32gui

    found = []

    def top(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and (win32gui.GetWindowText(hwnd) or "") == "输出":
            found.append(hwnd)

    win32gui.EnumWindows(top, None)
    return found[0] if found else None


def _click_button(hwnd) -> None:
    import win32gui

    win32gui.PostMessage(hwnd, 0x00F5, 0, 0)


def _cancel_output_dialog() -> None:
    import win32gui

    dlg = _output_dialog()
    if not dlg:
        return
    cancel = None

    def child(hwnd, _):
        nonlocal cancel
        title = (win32gui.GetWindowText(hwnd) or "").replace(" ", "")
        if win32gui.GetClassName(hwnd) == "Button" and title == "取消":
            cancel = hwnd

    win32gui.EnumChildWindows(dlg, child, None)
    if cancel:
        _click_button(cancel)


def _confirm_overwrite() -> bool:
    import win32gui

    clicked = False

    def top(hwnd, _):
        nonlocal clicked
        if clicked or not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetClassName(hwnd) != "#32770":
            return
        if (win32gui.GetWindowText(hwnd) or "") == "输出":
            return
        bits = []

        def child(ch, _):
            text = win32gui.GetWindowText(ch) or ""
            if text:
                bits.append(text)

        win32gui.EnumChildWindows(hwnd, child, None)
        blob = "".join(bits)
        if "已存在" not in blob and "替换" not in blob and "覆盖" not in blob:
            return
        yes = []

        def find_yes(ch, _):
            text = (win32gui.GetWindowText(ch) or "").replace(" ", "")
            if win32gui.GetClassName(ch) == "Button" and text in ("是", "是(&Y)"):
                yes.append(ch)

        win32gui.EnumChildWindows(hwnd, find_yes, None)
        if yes:
            _click_button(yes[0])
            clicked = True

    win32gui.EnumWindows(top, None)
    return clicked


def _hold_fp(positions: Dict[str, Dict]) -> str:
    """只比较代码、数量和成本。现价、市值变了不算持仓变化。"""
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
                round(float(row.get("price") or 0), 4),
                round(float(row.get("trade_price") or 0), 4),
                int(row.get("volume") or 0),
                int(row.get("trade_volume") or 0),
            )
        )
    return repr(sorted(rows))


def _visible_grid(main):
    import win32gui

    grids = []

    def collect(hwnd, _):
        if win32gui.GetClassName(hwnd) != "SysListView32" or not win32gui.IsWindowVisible(hwnd):
            return
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right - left > 200 and bottom - top > 80:
            grids.append((hwnd, left, top, right, bottom))

    win32gui.EnumChildWindows(main, collect, None)
    if not grids:
        return None
    return max(grids, key=lambda g: (g[3] - g[1]) * (g[4] - g[2]))[0]


def _grab_grid(hwnd):
    """把可见表格画下来，用来比较有没有变化。不写文件。"""
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width < 50 or height < 40:
        return None
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 1)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        return Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
    except Exception:
        return None
    finally:
        try:
            win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def _header_spans(img) -> List[Tuple[int, int]]:
    width, height = img.size
    px = img.load()
    y1 = min(20, height)
    ink = [False] * width
    for x in range(width):
        dark = 0
        for y in range(2, y1):
            red, green, blue = px[x, y][:3]
            if red < 180 and green < 180 and blue < 180:
                dark += 1
        if dark > 2:
            for k in range(max(0, x - 2), min(width, x + 3)):
                ink[k] = True
    runs: List[Tuple[int, int]] = []
    start = None
    for x, on in enumerate(ink + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            if x - start >= 15:
                runs.append((start, x))
            start = None
    return runs


def _holdings_cut(img) -> int:
    """当前价及右边的市值、盈亏会跟着行情变，不拿来判断持仓有没有变。"""
    spans = _header_spans(img)
    if len(spans) >= 9:
        return spans[8][0]
    return 0


def _grid_row_count(main) -> int:
    """可见表格的数据行数。表头不算在内。读不到时返回 -1。"""
    import win32gui

    hwnd = _visible_grid(main)
    if not hwnd:
        return -1
    try:
        return int(win32gui.SendMessage(hwnd, 0x1004, 0, 0))
    except Exception:
        return -1


def _grid_digest(main, page_name: str) -> str:
    hwnd = _visible_grid(main)
    if not hwnd:
        return ""
    img = _grab_grid(hwnd)
    if img is None:
        return ""
    width, height = img.size
    px = img.load()
    sample = 0
    bright = 0
    for y in range(0, height, 8):
        for x in range(0, width, 8):
            sample += 1
            red, green, blue = px[x, y][:3]
            if red > 245 and green > 245 and blue > 245:
                bright += 1
            if red < 8 and green < 8 and blue < 8:
                bright += 1
    if sample == 0 or bright / sample > 0.92:
        return ""
    cut = width
    if page_name == "资金股份":
        cut = _holdings_cut(img)
        if cut < 80:
            return ""
    digest = hashlib.md5()
    for y in range(0, height, 2):
        for x in range(0, cut, 2):
            digest.update(bytes(px[x, y][:3]))
    return digest.hexdigest()


def _notepad_pids() -> set:
    try:
        import psutil
    except ImportError:
        return set()
    found = set()
    for proc in psutil.process_iter(["name", "pid"]):
        if (proc.info.get("name") or "").lower() == "notepad.exe":
            found.add(proc.info["pid"])
    return found


def _hide_pid_windows(pid: int) -> None:
    import win32gui
    import win32process

    def top(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, owner = win32process.GetWindowThreadProcessId(hwnd)
        if owner != pid:
            return
        win32gui.ShowWindow(hwnd, 0)
        win32gui.PostMessage(hwnd, 0x0010, 0, 0)

    win32gui.EnumWindows(top, None)


def _silence_new_notepads(before: set, stop, filename: str) -> None:
    """保存过程中新开的记事本就是国金太阳打开的导出文件，立刻藏起来并关掉。"""
    try:
        import psutil
    except ImportError:
        return
    while not stop.is_set():
        for proc in psutil.process_iter(["name", "pid"]):
            if (proc.info.get("name") or "").lower() != "notepad.exe":
                continue
            pid = proc.info.get("pid")
            if not pid or pid in before:
                continue
            _hide_pid_windows(pid)
            try:
                proc.terminate()
            except Exception:
                pass
        _close_export_notepad(filename)
        stop.wait(0.02)


def _close_export_notepad(filename: str) -> bool:
    """国金太阳存完文本后会用记事本打开。关掉这个窗口，不关用户自己的记事本。"""
    import win32gui

    closed = False

    def top(hwnd, _):
        nonlocal closed
        if not win32gui.IsWindowVisible(hwnd):
            return
        if win32gui.GetClassName(hwnd) != "Notepad":
            return
        title = win32gui.GetWindowText(hwnd) or ""
        name = title.split(" - ")[0].lstrip("*").strip()
        if filename not in title and filename not in name:
            return
        win32gui.PostMessage(hwnd, 0x0010, 0, 0)
        closed = True

    win32gui.EnumWindows(top, None)
    return closed


def _save_text(out_path: str) -> bool:
    """在已经弹出的「输出」对话框里，把「输出到文本文件」存到 out_path。"""
    import win32con
    import win32gui

    dlg = None
    for _ in range(30):
        dlg = _output_dialog()
        if dlg:
            break
        time.sleep(0.1)
    if not dlg:
        return False
    text_radio = None
    ok_btn = None
    edits = []

    def child(hwnd, _):
        nonlocal text_radio, ok_btn
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd) or ""
        if cls == "Edit":
            edits.append((win32gui.GetWindowRect(hwnd)[1], hwnd))
        if cls != "Button":
            return
        compact = title.replace(" ", "")
        if compact == "输出到文本文件":
            text_radio = hwnd
        elif compact == "确定":
            ok_btn = hwnd

    win32gui.EnumChildWindows(dlg, child, None)
    if text_radio:
        _click_button(text_radio)
    if not edits or not ok_btn:
        _cancel_output_dialog()
        return False
    edits.sort()
    win32gui.SendMessage(edits[0][1], win32con.WM_SETTEXT, 0, out_path)
    if os.path.isfile(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass
    name = os.path.basename(out_path)
    before = _notepad_pids()
    stop = threading.Event()
    watcher = threading.Thread(target=_silence_new_notepads, args=(before, stop, name), daemon=True)
    watcher.start()
    try:
        _click_button(ok_btn)
        for _ in range(40):
            _confirm_overwrite()
            if os.path.isfile(out_path) and os.path.getsize(out_path) > 0 and _output_dialog() is None:
                time.sleep(0.6)
                return True
            time.sleep(0.1)
        _cancel_output_dialog()
        return False
    finally:
        time.sleep(0.4)
        stop.set()
        watcher.join(1.0)
        _close_export_notepad(name)


def _read_export(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="replace")


def _query_tree(main):
    import win32gui
    from pywinauto import Application

    app = Application(backend="win32").connect(handle=main)
    best = None
    best_n = 0
    for cand in app.window(handle=main).descendants(class_name="SysTreeView32"):
        if not win32gui.IsWindowVisible(cand.handle):
            continue
        try:
            n = cand.item_count()
        except Exception:
            continue
        if n > best_n:
            best = cand
            best_n = n
    return best


def _selected_page(tree) -> str:
    try:
        item = tree.get_selection()
        return (item.text() or "").strip()
    except Exception:
        return ""


def _open_query_page(tree, page_name: str) -> bool:
    try:
        tree.get_item(["查询", page_name]).click_input()
        return True
    except Exception:
        return False


_CURSOR_DEPTH = 0
_CURSOR_POS = None


def _remember_cursor() -> None:
    """记下光标。刷新会去点国金太阳，嵌套调用只记最外层那一次。"""
    global _CURSOR_DEPTH, _CURSOR_POS
    if _CURSOR_DEPTH == 0:
        try:
            import win32api

            _CURSOR_POS = win32api.GetCursorPos()
        except Exception:
            _CURSOR_POS = None
    _CURSOR_DEPTH += 1


def _restore_cursor() -> None:
    """回到刷新前的位置。里层读完先不挪，等整次刷新结束再放回去。"""
    global _CURSOR_DEPTH, _CURSOR_POS
    if _CURSOR_DEPTH <= 0:
        return
    _CURSOR_DEPTH -= 1
    if _CURSOR_DEPTH > 0:
        return
    pos = _CURSOR_POS
    _CURSOR_POS = None
    if not pos:
        return
    try:
        import win32api

        win32api.SetCursorPos((int(pos[0]), int(pos[1])))
    except Exception:
        pass


class _CursorHold:
    def __enter__(self):
        _remember_cursor()
        return self

    def __exit__(self, *_exc):
        _restore_cursor()
        return False


def hold_cursor():
    """一次刷新里读持仓和委托时包住，光标只在全部点完后回到原处。"""
    return _CursorHold()


def _click_output(main) -> bool:
    import win32gui
    from pywinauto import Application

    hits = []

    def child(hwnd, _):
        if win32gui.GetClassName(hwnd) != "Button" or not win32gui.IsWindowVisible(hwnd):
            return
        title = (win32gui.GetWindowText(hwnd) or "").replace(" ", "")
        if title != "输出":
            return
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right - left < 10 or bottom - top < 8:
            return
        hits.append(hwnd)

    win32gui.EnumChildWindows(main, child, None)
    if len(hits) != 1:
        return False
    Application(backend="win32").connect(handle=main).window(handle=main).child_window(handle=hits[0]).click_input()
    return True


def _export_page(page_name: str, filename: str, skip_hash: str = "") -> Optional[str]:
    """读一页。返回空字符串表示画面没变，或当日委托本身就是空表。"""
    _remember_cursor()
    try:
        return _export_page_work(page_name, filename, skip_hash)
    finally:
        _restore_cursor()


def _export_page_work(page_name: str, filename: str, skip_hash: str = "") -> Optional[str]:
    global _LAST_NOTE, _PEEK_HASH, _PEEK_ROWS
    import win32con
    import win32gui

    main = _find_main()
    if not main:
        _LAST_NOTE = "no-window"
        return None
    was_iconic = bool(win32gui.IsIconic(main))
    if was_iconic:
        win32gui.ShowWindow(main, win32con.SW_RESTORE)
        time.sleep(0.25)
    _cancel_output_dialog()
    previous = ""
    opened = False
    try:
        tree = _query_tree(main)
        if tree is None:
            _LAST_NOTE = "no-tree"
            return None
        previous = _selected_page(tree)
        if previous != page_name:
            if not _open_query_page(tree, page_name):
                _LAST_NOTE = "no-page"
                return None
            opened = True
            time.sleep(0.45)
        digest = _grid_digest(main, page_name)
        _PEEK_HASH = digest
        _PEEK_ROWS = _grid_row_count(main)
        if skip_hash and digest and digest == skip_hash:
            _LAST_NOTE = "unchanged"
            return ""
        # 早上还没有委托，或券商晚上清空当日委托后，表是空的。不要点输出。
        if page_name == "当日委托" and _PEEK_ROWS == 0:
            _LAST_NOTE = "empty"
            return ""
        if not _click_output(main):
            _LAST_NOTE = "no-output"
            return None
        out_path = os.path.join(_export_dir(), filename)
        if not _save_text(out_path):
            _LAST_NOTE = "save-failed"
            return None
        _LAST_NOTE = "ok"
        return _read_export(out_path)
    finally:
        try:
            time.sleep(0.15)
            tree = _query_tree(main)
            restore = previous if previous in ("资金股份", "当日委托", "当日成交", "历史委托", "历史成交") else "资金股份"
            if opened and tree is not None and restore != page_name and _selected_page(tree) != restore:
                _open_query_page(tree, restore)
        except Exception:
            pass
        if was_iconic and win32gui.IsWindow(main):
            win32gui.ShowWindow(main, win32con.SW_MINIMIZE)


def read_holdings() -> Optional[Dict[str, Dict]]:
    """读取资金股份。数量和成本没变就不再保存。失败返回 None。"""
    global _LAST_NOTE, _LAST_ASSET, _LAST_HEADER, _LAST_HOLD_FP, _LAST_HOLD_POS, _LAST_HOLD_HASH
    text = _export_page("资金股份", "holdings.txt", _LAST_HOLD_HASH)
    if text is None:
        return None
    if text == "" and _LAST_NOTE == "unchanged":
        return _LAST_HOLD_POS
    _LAST_ASSET = _parse_asset(text)
    header, _rows = _table(text)
    _LAST_HEADER = " ".join(header)[:240]
    if "可卖数量" not in header:
        _LAST_NOTE = "bad-header"
        return None
    positions = parse_holdings_text(text)
    fp = _hold_fp(positions)
    if fp == _LAST_HOLD_FP and _LAST_HOLD_POS is not None:
        if _PEEK_HASH:
            _LAST_HOLD_HASH = _PEEK_HASH
        _LAST_NOTE = "unchanged"
        return _LAST_HOLD_POS
    _LAST_HOLD_FP = fp
    _LAST_HOLD_POS = positions
    if _PEEK_HASH:
        _LAST_HOLD_HASH = _PEEK_HASH
    _LAST_NOTE = "ok"
    return positions


def read_fills() -> Optional[List[Dict]]:
    """读取当日委托。委托状态没变就不再保存。失败返回 None；今天没有委托时返回空列表。"""
    global _LAST_NOTE, _LAST_HEADER, _LAST_ORDER_FP, _LAST_ORDER_ROWS, _LAST_ORDER_HASH
    text = _export_page("当日委托", "orders.txt", _LAST_ORDER_HASH)
    if text is None:
        return None
    if text == "" and _LAST_NOTE == "unchanged":
        return _LAST_ORDER_ROWS
    if text == "" and _LAST_NOTE == "empty":
        if _LAST_ORDER_ROWS == []:
            _LAST_NOTE = "unchanged"
            return []
        _LAST_ORDER_FP = _order_fp([])
        _LAST_ORDER_ROWS = []
        if _PEEK_HASH:
            _LAST_ORDER_HASH = _PEEK_HASH
        return []
    header, _rows = _table(text)
    _LAST_HEADER = " ".join(header)[:240]
    fills, note = parse_fills_text(text)
    if note == "bad-header":
        _LAST_NOTE = "bad-header"
        return None
    fp = _order_fp(fills)
    if fp == _LAST_ORDER_FP and _LAST_ORDER_ROWS is not None:
        if _PEEK_HASH:
            _LAST_ORDER_HASH = _PEEK_HASH
        _LAST_NOTE = "unchanged"
        return _LAST_ORDER_ROWS
    _LAST_ORDER_FP = fp
    _LAST_ORDER_ROWS = fills
    if _PEEK_HASH:
        _LAST_ORDER_HASH = _PEEK_HASH
    _LAST_NOTE = "ok" if fills else "empty"
    return fills
