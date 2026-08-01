# -*- coding: utf-8 -*-
"""
WPS/Excel COM：将工作簿中指定表导出为 PNG。

解决「UsedRange 虚大」导致 CopyPicture 右侧大片空白：先按单元格值裁剪到
真正有内容的矩形，再 CopyPicture；最后再按与纯白背景的差异裁边一次。
"""

from __future__ import annotations

import os
import time
from typing import Any, List, Optional, Tuple


def _cell_nonempty(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, float):
        try:
            import math

            if math.isnan(val):
                return False
        except Exception:
            pass
        return True
    if isinstance(val, str):
        return bool(val.strip())
    return True


def _value_to_2d_rows(val: Any) -> List[List[Any]]:
    """COM Range.Value -> 行列表（每行为 list）。"""
    if val is None:
        return []
    if not isinstance(val, (list, tuple)):
        return [[val]]
    if len(val) == 0:
        return []
    first = val[0]
    if isinstance(first, (list, tuple)):
        return [list(r) for r in val]
    return [list(val)]


def _pad_grid(rows: List[List[Any]], n_rows: int, n_cols: int) -> List[List[Any]]:
    out: List[List[Any]] = []
    for i in range(n_rows):
        src = rows[i] if i < len(rows) else []
        row = []
        for j in range(n_cols):
            row.append(src[j] if j < len(src) else None)
        out.append(row)
    return out


def _content_bounds(grid: List[List[Any]]) -> Optional[Tuple[int, int, int, int]]:
    """返回 (min_row, min_col, max_row, max_col) 均为 0-based 闭区间；无内容返回 None。"""
    if not grid:
        return None
    ncols = max((len(r) for r in grid), default=0)
    min_r, min_c = len(grid), ncols
    max_r, max_c = -1, -1
    for i, row in enumerate(grid):
        for j in range(max(ncols, len(row))):
            v = row[j] if j < len(row) else None
            if _cell_nonempty(v):
                min_r = min(min_r, i)
                max_r = max(max_r, i)
                min_c = min(min_c, j)
                max_c = max(max_c, j)
    if max_r < 0:
        return None
    return min_r, min_c, max_r, max_c


def _crop_white_margins_pil(img: Any, *, threshold: int = 10) -> Any:
    """裁掉四周与纯白 (255,255,255) 几乎无差别的边（处理 COM 仍带的窄白边）。"""
    try:
        from PIL import Image, ImageChops, ImageFilter
    except Exception:
        return img
    try:
        if getattr(img, "mode", "") != "RGB":
            rgb = img.convert("RGB")
        else:
            rgb = img
        bg = Image.new("RGB", rgb.size, (255, 255, 255))
        diff = ImageChops.difference(rgb, bg)
        # 放大差分，避免抗锯齿灰边漏裁
        if threshold > 0:
            diff = diff.filter(ImageFilter.MaxFilter(3))
        bbox = diff.getbbox()
        if bbox:
            return rgb.crop(bbox)
        return rgb
    except Exception:
        return img


def excel_sheet_to_png_via_wps(
    xlsx_path: str,
    png_path: str,
    *,
    sheet_index: int = 1,
    sheet_name: Optional[str] = None,
    close_workbook_save: bool = True,
) -> Tuple[bool, str]:
    """
    打开 xlsx，对首表或指定表：UsedRange 列 AutoFit → 按值收缩 → CopyPicture → 保存 PNG。
    返回 (成功, 错误信息)。
    """
    xlsx_abs = os.path.abspath(xlsx_path)
    png_abs = os.path.abspath(png_path)
    if not os.path.isfile(xlsx_abs):
        return False, f"文件不存在: {xlsx_abs}"

    try:
        import pythoncom  # type: ignore
        import win32com.client as win32  # type: ignore
        from PIL import Image, ImageGrab  # type: ignore
    except Exception as e:
        return False, f"缺少依赖（pywin32/Pillow）: {e}"

    app = None
    wb = None
    inited = False
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        except Exception:
            pythoncom.CoInitialize()
        inited = True

        for progid in ("ket.Application", "et.Application", "KET.Application", "ET.Application"):
            try:
                app = win32.DispatchEx(progid)
                if app is not None:
                    break
            except Exception:
                app = None
        if app is None:
            return False, "未找到 WPS COM（ket/et.Application）"

        app.Visible = False
        app.DisplayAlerts = False
        wb = app.Workbooks.Open(xlsx_abs)

        ws = None
        if sheet_name:
            try:
                ws = wb.Worksheets(sheet_name)
            except Exception:
                ws = None
        if ws is None:
            try:
                ws = wb.Worksheets(int(sheet_index))
            except Exception:
                ws = wb.Worksheets(1)

        ws.Activate()
        used = ws.UsedRange
        if used is None:
            return False, "工作表 UsedRange 为空"

        try:
            used.EntireColumn.AutoFit()
        except Exception:
            pass
        try:
            app.ScreenUpdating = False
        except Exception:
            pass

        r0, c0 = int(used.Row), int(used.Column)
        n_rows, n_cols = int(used.Rows.Count), int(used.Columns.Count)
        vals = used.Value
        rows_2d = _value_to_2d_rows(vals)
        grid = _pad_grid(rows_2d, n_rows, n_cols)
        bounds = _content_bounds(grid)

        if bounds is None:
            rng = used
        else:
            min_r, min_c, max_r, max_c = bounds
            rng = ws.Range(
                ws.Cells(r0 + min_r, c0 + min_c),
                ws.Cells(r0 + max_r, c0 + max_c),
            )

        copied = False
        try:
            rng.CopyPicture(1, 2)
            copied = True
        except Exception:
            pass
        if not copied:
            try:
                rng.CopyPicture()
                copied = True
            except Exception as e2:
                return False, f"CopyPicture 失败: {e2}"

        img = None
        for _ in range(12):
            time.sleep(0.15)
            try:
                img = ImageGrab.grabclipboard()
            except Exception:
                img = None
            if img is not None and not isinstance(img, list):
                break
        if img is None or isinstance(img, list):
            return False, "剪贴板未取到图片"

        if getattr(img, "mode", "") in ("RGBA", "LA"):
            white_bg = Image.new("RGB", img.size, "white")
            alpha = img.split()[-1]
            white_bg.paste(img, mask=alpha)
            img = white_bg
        elif getattr(img, "mode", "") != "RGB":
            img = img.convert("RGB")

        img = _crop_white_margins_pil(img)
        os.makedirs(os.path.dirname(png_abs) or ".", exist_ok=True)
        img.save(png_abs, "PNG")
        if not os.path.isfile(png_abs):
            return False, "PNG 保存后文件不存在"
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        try:
            if wb is not None:
                wb.Close(SaveChanges=bool(close_workbook_save))
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        if inited:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
