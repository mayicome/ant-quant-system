# -*- coding: utf-8 -*-
"""QMT 行情页「沪深京A股」右键导出。

落盘：data/qmt_hsj_quote.txt、data/qmt_hsj_quote.meta.json、data/a_share_universe.json。
今开、最高、最低、最新是当天开高低收。
成交额用「总额」。表里没有成交量，能折算才写；折不出来的留给东财、腾讯、同花顺。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
QUOTE_PATH = ROOT / "data" / "qmt_hsj_quote.txt"
META_PATH = ROOT / "data" / "qmt_hsj_quote.meta.json"
UNIVERSE_PATH = ROOT / "data" / "a_share_universe.json"

_EMPTY = {"", "-", "—", "--"}


def parse_display_number(text: Any) -> Optional[float]:
    """把行情表里的 17.20亿、7884.92万、0.78% 收成数字。百分数返回小数。"""
    raw = str(text or "").strip().replace(",", "").replace(" ", "")
    if raw in _EMPTY:
        return None
    is_pct = raw.endswith("%")
    if is_pct:
        raw = raw[:-1]
    mul = 1.0
    if raw.endswith("亿"):
        mul = 1e8
        raw = raw[:-1]
    elif raw.endswith("万"):
        mul = 1e4
        raw = raw[:-1]
    try:
        value = float(raw) * mul
    except ValueError:
        return None
    if is_pct:
        value = value / 100.0
    return value


def to_full_code(code: str) -> str:
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(digits) < 6:
        return ""
    code_6 = digits[:6]
    if code_6[0] not in "034689":
        return ""
    if code_6.startswith("6"):
        return code_6 + ".SH"
    if code_6.startswith(("4", "8")) or code_6.startswith("920"):
        return code_6 + ".BJ"
    return code_6 + ".SZ"


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("gbk", "replace")


def read_table(path: Path) -> List[Dict[str, str]]:
    text = _read_text(path)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    delim = "\t" if "\t" in lines[0] else ","
    header = [cell.strip() for cell in lines[0].split(delim)]
    rows: List[Dict[str, str]] = []
    for line in lines[1:]:
        cells = line.split(delim)
        if len(cells) < len(header):
            cells = cells + [""] * (len(header) - len(cells))
        rows.append({header[i]: cells[i].strip() for i in range(len(header))})
    return rows


def _cell(row: Dict[str, str], *names: str) -> str:
    for name in names:
        if name in row:
            return row[name]
    return ""


def row_to_bar(row: Dict[str, str], day: str) -> Optional[Dict[str, Any]]:
    """能凑齐当天一根才返回。价格对不上、没有成交量的返回 None。"""
    full = to_full_code(_cell(row, "代码"))
    if not full:
        return None
    open_ = parse_display_number(_cell(row, "今开"))
    high = parse_display_number(_cell(row, "最高"))
    low = parse_display_number(_cell(row, "最低"))
    close = parse_display_number(_cell(row, "最新"))
    if not all(v is not None and v > 0 for v in (open_, high, low, close)):
        return None
    if high + 1e-6 < max(open_, close, low) or low - 1e-6 > min(open_, close, high):
        return None
    amount = parse_display_number(_cell(row, "总额"))
    turnover = parse_display_number(_cell(row, "换手率"))
    float_mv = parse_display_number(_cell(row, "流通市值"))
    vol_turn = None
    if turnover and turnover > 0 and float_mv and float_mv > 0:
        vol_turn = turnover * (float_mv / close) / 100.0
    vol_amt = None
    if amount and amount > 0:
        vol_amt = amount / close / 100.0
    if vol_turn and vol_amt:
        base = max(vol_turn, vol_amt)
        if abs(vol_turn - vol_amt) / base > 0.20:
            return None
        volume = vol_turn
    elif vol_turn:
        volume = vol_turn
    elif vol_amt:
        volume = vol_amt
    else:
        return None
    if volume <= 0:
        return None
    return {
        "code": full,
        "date": day[:10],
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(round(volume)),
        "amount": None if not amount or amount <= 0 else float(amount),
        "source": "qmt_quote",
    }


def bars_for_day(rows: List[Dict[str, str]], day: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        bar = row_to_bar(row, day)
        if bar:
            out[bar["code"]] = bar
    return out


def universe_codes(rows: List[Dict[str, str]]) -> List[str]:
    seen = set()
    for row in rows:
        full = to_full_code(_cell(row, "代码"))
        if full:
            seen.add(full)
    return sorted(seen)


def file_covers_day(day: str, path: Path = QUOTE_PATH, meta_path: Path = META_PATH) -> bool:
    """导出要落在目标交易日 15:00 之后，最新价才是收盘价。"""
    day = day[:10]
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        if str(meta.get("trade_date") or "")[:10] == day:
            return path.is_file()
    if not path.is_file():
        return False
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return False
    return mtime.date().isoformat() == day and mtime.hour >= 15


def load_bars(day: str) -> Dict[str, Dict[str, Any]]:
    if not file_covers_day(day):
        return {}
    return bars_for_day(read_table(QUOTE_PATH), day)


def write_meta(day: str, count: int, source: str) -> None:
    payload = {
        "trade_date": day[:10],
        "exported_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "count": int(count),
        "source": source,
    }
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_universe(codes: List[str], day: str) -> None:
    payload = {
        "version": 1,
        "trade_date": day[:10],
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(codes),
        "codes": codes,
    }
    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = UNIVERSE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, UNIVERSE_PATH)


def install_table(src: Path, day: str, source: str) -> int:
    """把一份导出收成标准路径，并写出股票池。返回代码数。"""
    rows = read_table(src)
    codes = universe_codes(rows)
    if len(codes) < 1000:
        raise RuntimeError(f"导出里只有 {len(codes)} 只代码，不像沪深京A股")
    QUOTE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != QUOTE_PATH.resolve():
        QUOTE_PATH.write_bytes(src.read_bytes())
    write_meta(day, len(codes), source)
    write_universe(codes, day)
    return len(codes)
