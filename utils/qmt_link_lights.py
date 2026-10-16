# -*- coding: utf-8 -*-
"""看 QMT 窗口右下角「交易」「行情」两个指示灯。

两个都是绿色表示和券商服务器连着。变成红色表示这条连接断了。
认不出窗口、或这一块没有足够的颜色时返回 None，不当成断线。
"""
from __future__ import annotations

from typing import Dict, Optional


def _color_at(pixel) -> str:
    red, green, blue = pixel[:3]
    if green > 140 and green > red + 40 and green > blue + 40:
        return "green"
    if red > 140 and red > green + 40 and red > blue + 40:
        return "red"
    return ""


def _signal_bar_left(px, width: int, height: int) -> Optional[int]:
    """时钟左边那几根蓝色信号柱的左边界。两个灯就贴在它左边。"""
    cols = []
    y0 = max(0, height - 40)
    y1 = max(y0 + 1, height - 16)
    for x in range(max(0, width - 160), max(0, width - 40)):
        count = 0
        for y in range(y0, y1):
            red, green, blue = px[x, y][:3]
            if blue > 160 and blue > red + 50 and blue > green + 20:
                count += 1
        if count >= 3:
            cols.append(x)
    if not cols:
        return None
    start = cols[0]
    prev = cols[0]
    for x in cols[1:]:
        if x - prev > 8:
            start = x
        prev = x
    return start


def lights_from_image(image) -> Optional[Dict[str, str]]:
    """从一张 QMT 窗口截图里读两个灯。返回 trade / quote，取值 green 或 red。"""
    width, height = image.size
    if width < 400 or height < 80:
        return None
    px = image.load()
    bar_left = _signal_bar_left(px, width, height)
    if bar_left is None:
        return None
    x0 = max(0, bar_left - 78)
    x1 = max(x0 + 1, bar_left - 4)
    y0 = max(0, height - 48)
    y1 = max(y0 + 1, height - 12)
    colored = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            kind = _color_at(px[x, y])
            if kind:
                colored.append((x, kind))
    if len(colored) < 40:
        return None
    xs = [item[0] for item in colored]
    left, right = min(xs), max(xs)
    if right - left < 24:
        return None
    mid = (left + right) / 2.0
    sides = {"trade": [], "quote": []}
    for x, kind in colored:
        sides["trade" if x < mid else "quote"].append(kind)
    out: Dict[str, str] = {}
    for name, kinds in sides.items():
        greens = kinds.count("green")
        reds = kinds.count("red")
        if reds >= 15 and reds > greens:
            out[name] = "red"
        elif greens >= 15 and greens > reds:
            out[name] = "green"
        else:
            return None
    return out


def read_link_lights() -> Optional[Dict[str, str]]:
    from utils.qmt_model_trade_readonly import _find_main, _grab

    hwnd = _find_main()
    if not hwnd:
        return None
    try:
        image = _grab(hwnd)
    except Exception:
        return None
    return lights_from_image(image)
