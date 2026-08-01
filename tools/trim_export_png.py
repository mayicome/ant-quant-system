# -*- coding: utf-8 -*-
"""
导出 PNG 后裁边：去掉右侧/下方大块空白。

适用：WPS CopyPicture 铺宽画布、Qt QTableWidget 渲染后右侧留白等。
用列/行「墨色能量」相对峰值判定内容区，避免仅靠「非纯白」被表格线干扰。
"""

from __future__ import annotations

from typing import Any, Optional


def trim_export_png_margins(
    img: Any,
    *,
    rel_energy: float = 0.008,
    abs_floor: float = 220.0,
) -> Any:
    if img is None:
        return img
    im = img.convert("RGB")
    w, h = im.size
    if w <= 4 or h <= 4:
        return im

    def _numpy_crop() -> Optional[Any]:
        try:
            import numpy as np
        except Exception:
            return None
        arr = np.asarray(im, dtype=np.int16)
        dev = (255 - arr).clip(0, 255)
        ink = dev.sum(axis=2).astype(np.float64)
        col_e = ink.sum(axis=0)
        row_e = ink.sum(axis=1)
        cmax = float(col_e.max()) or 1.0
        rmax = float(row_e.max()) or 1.0
        th_c = max(rel_energy * cmax, abs_floor)
        th_r = max(rel_energy * rmax, abs_floor)
        col_ok = col_e > th_c
        row_ok = row_e > th_r
        xs = np.flatnonzero(col_ok)
        ys = np.flatnonzero(row_ok)
        if xs.size == 0 or ys.size == 0:
            return im
        x0, x1 = int(xs[0]), int(xs[-1])
        y0, y1 = int(ys[0]), int(ys[-1])
        pad = 4
        x0 = max(0, x0 - pad)
        y0 = max(0, y0 - pad)
        x1 = min(w - 1, x1 + pad)
        y1 = min(h - 1, y1 + pad)
        cr = im.crop((x0, y0, x1 + 1, y1 + 1))
        if cr.width < 150 or cr.height < 80:
            return im
        return cr

    out = _numpy_crop()
    if out is not None:
        return out

    px = im.load()

    def col_energy(x: int) -> float:
        s = 0.0
        for y in range(h):
            r, g, b = px[x, y]
            s += max(0, 255 - r) + max(0, 255 - g) + max(0, 255 - b)
        return s

    def row_energy(y: int) -> float:
        s = 0.0
        for x in range(w):
            r, g, b = px[x, y]
            s += max(0, 255 - r) + max(0, 255 - g) + max(0, 255 - b)
        return s

    ce = [col_energy(x) for x in range(w)]
    re = [row_energy(y) for y in range(h)]
    cmax = max(ce) or 1.0
    rmax = max(re) or 1.0
    th_c = max(rel_energy * cmax, abs_floor)
    th_r = max(rel_energy * rmax, abs_floor)
    xs = [x for x in range(w) if ce[x] > th_c]
    ys = [y for y in range(h) if re[y] > th_r]
    if not xs or not ys:
        return im
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    pad = 4
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(w - 1, x1 + pad)
    y1 = min(h - 1, y1 + pad)
    cr = im.crop((x0, y0, x1 + 1, y1 + 1))
    if cr.width < 150 or cr.height < 80:
        return im
    return cr
