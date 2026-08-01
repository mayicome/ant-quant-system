# -*- coding: utf-8 -*-
"""生成/更新 data/all_a_stock_info.json（代码→概念/板块标签）。

默认模式 f10：逐只请求东财 F10「核心题材」接口（稳定；push2 易被掐断）。
可选模式 boards：概念/行业板块→成分股反转（akshare push2，易 ConnectionError）。

写入：若目标文件已存在，按股票合并（concepts/plates 去重并集追加，不删旧标签）。

用法：
  python tools/build_stock_info_from_em_boards.py
  python tools/build_stock_info_from_em_boards.py --mode f10 --limit-stocks 20
  python tools/build_stock_info_from_em_boards.py --mode boards --limit-concepts 5
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, "data")
DEFAULT_OUT = os.path.join(DATA_DIR, "all_a_stock_info.json")
DEFAULT_PROGRESS = os.path.join(DATA_DIR, "all_a_stock_info.em_boards.progress.json")
STOCK_LIST_CSV = os.path.join(DATA_DIR, "all_a_stocks.csv")
F10_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax"

_DYNAMIC_BOARD_EXACT = frozenset(
    {
        "最近多板",
        "昨日涨停",
        "昨日连板",
        "昨日连板_含一字",
        "昨日首板",
        "今日涨停",
        "今日连板",
        "涨停",
        "跌停",
        "炸板",
        "题材股",
        "东方财富热股",
    }
)
_DYNAMIC_BOARD_KEYWORDS = (
    "昨日",
    "今日",
    "连板",
    "涨停",
    "跌停",
    "炸板",
    "自然涨停",
    "一字板",
    "新高",
    "高振幅",
    "热股",
    "预增",
    "预减",
    "业绩增",
    "打二板",
    "首亏",
    "中报",
    "年报",
    "季报",
)


def _is_dynamic_market_board(name: str) -> bool:
    n = str(name or "").strip()
    if not n:
        return True
    if n in _DYNAMIC_BOARD_EXACT:
        return True
    return any(k in n for k in _DYNAMIC_BOARD_KEYWORDS)


def _clear_proxy_env() -> None:
    for k in list(os.environ.keys()):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")


def _code6(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _em_secid(code6: str) -> str:
    c = _code6(code6)
    if c.startswith(("5", "6", "9")):
        return f"SH{c}"
    return f"SZ{c}"


def _load_name_map(csv_path: str = STOCK_LIST_CSV) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not os.path.isfile(csv_path):
        return out
    rows: List[dict] = []
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(csv_path, "r", encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except Exception:
            rows = []
    for row in rows:
        code = ""
        name = ""
        for k, v in row.items():
            ks = str(k or "")
            if "代码" in ks or ks.lower() in ("code", "symbol"):
                code = _code6(v)
            if "简称" in ks or "名称" in ks or ks.lower() in ("name",):
                name = str(v or "").strip()
        if code:
            out[code] = name or out.get(code, "")
    return out


def _load_universe_codes(csv_path: str = STOCK_LIST_CSV) -> List[str]:
    mp = _load_name_map(csv_path)
    return sorted(mp.keys())


def _save_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _as_str_list(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for x in v:
        s = str(x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _load_existing_stock_info(path: str) -> Dict[str, dict]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[warn] 读取已有 {path} 失败，将仅写入本次结果: {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, dict] = {}
    for k, row in data.items():
        if str(k).startswith("_"):
            continue
        c6 = _code6(k)
        if not c6 or not isinstance(row, dict):
            continue
        out[c6] = {
            "stock_code": c6,
            "name": str(row.get("name") or "").strip(),
            "concepts": _as_str_list(row.get("concepts")),
            "industry": str(row.get("industry") or "").strip(),
            "plates": _as_str_list(row.get("plates")),
        }
    return out


def _merge_tag_lists(*lists: Any) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for lst in lists:
        for s in _as_str_list(lst):
            if _is_dynamic_market_board(s):
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
    return sorted(out)


def _merge_stock_info_maps(
    base: Dict[str, dict],
    incoming: Dict[str, dict],
) -> Tuple[Dict[str, dict], dict]:
    merged: Dict[str, dict] = {c: dict(r) for c, r in base.items()}
    added_codes = 0
    touched_codes = 0
    added_concept_tags = 0
    added_plate_tags = 0
    for code, nrow in incoming.items():
        c6 = _code6(code)
        if not c6 or not isinstance(nrow, dict):
            continue
        old = merged.get(c6)
        if old is None:
            merged[c6] = {
                "stock_code": c6,
                "name": str(nrow.get("name") or "").strip(),
                "concepts": _merge_tag_lists(nrow.get("concepts")),
                "industry": str(nrow.get("industry") or "").strip(),
                "plates": _merge_tag_lists(nrow.get("plates")),
            }
            added_codes += 1
            continue
        before_c = len(old.get("concepts") or [])
        before_p = len(old.get("plates") or [])
        new_concepts = _merge_tag_lists(old.get("concepts"), nrow.get("concepts"))
        new_plates = _merge_tag_lists(old.get("plates"), nrow.get("plates"))
        new_name = str(old.get("name") or "").strip() or str(nrow.get("name") or "").strip()
        new_industry = str(old.get("industry") or "").strip() or str(
            nrow.get("industry") or ""
        ).strip()
        changed = (
            new_concepts != list(old.get("concepts") or [])
            or new_plates != list(old.get("plates") or [])
            or new_name != str(old.get("name") or "")
            or new_industry != str(old.get("industry") or "")
        )
        if changed:
            touched_codes += 1
        added_concept_tags += max(0, len(new_concepts) - before_c)
        added_plate_tags += max(0, len(new_plates) - before_p)
        merged[c6] = {
            "stock_code": c6,
            "name": new_name,
            "concepts": new_concepts,
            "industry": new_industry,
            "plates": new_plates,
        }
    stats = {
        "base_stocks": len(base),
        "incoming_stocks": len(incoming),
        "merged_stocks": len(merged),
        "added_codes": added_codes,
        "touched_codes": touched_codes,
        "added_concept_tags": added_concept_tags,
        "added_plate_tags": added_plate_tags,
    }
    return merged, stats


def _load_progress(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _http_session():
    import requests

    s = requests.Session()
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://emweb.securities.eastmoney.com/",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return s


def _retry_get(session, url: str, *, params: dict, retries: int, label: str):
    last = None
    for i in range(max(1, retries)):
        try:
            r = session.get(url, params=params, timeout=25)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            print(f"[warn] {label} fail ({i+1}/{retries}): {type(e).__name__}: {e}")
            if i + 1 < retries:
                time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


def _parse_f10_ssbk(ssbk: Any) -> Tuple[str, List[str], List[str]]:
    """返回 (industry, concepts, plates)。"""
    concepts: List[str] = []
    plates: List[str] = []
    industry = ""
    if not isinstance(ssbk, list):
        return industry, concepts, plates
    rows = [x for x in ssbk if isinstance(x, dict)]
    rows.sort(key=lambda x: int(x.get("BOARD_RANK") or 9999))
    for row in rows:
        name = str(row.get("BOARD_NAME") or "").strip()
        if not name or _is_dynamic_market_board(name):
            continue
        precise = row.get("IS_PRECISE")
        is_concept = precise in (1, "1", True)
        if is_concept:
            if name not in concepts:
                concepts.append(name)
            if name not in plates:
                plates.append(name)
        else:
            if name not in plates:
                plates.append(name)
            if not industry:
                industry = name
    return industry, concepts, plates


def build_f10(
    *,
    out_path: str,
    progress_path: str,
    limit_stocks: Optional[int],
    sleep_sec: float,
    retries: int,
    resume: bool,
    save_every: int,
) -> dict:
    _clear_proxy_env()
    name_map = _load_name_map()
    codes = _load_universe_codes()
    if limit_stocks is not None:
        codes = codes[: max(0, int(limit_stocks))]
    print(f"[info] mode=f10 universe={len(codes)} name_map={len(name_map)}")

    progress = _load_progress(progress_path) if resume else {}
    if progress.get("mode") not in (None, "f10"):
        print("[info] 进度文件模式不匹配，忽略旧进度")
        progress = {}
    done_codes: Set[str] = set(progress.get("done_codes") or [])
    stocks: Dict[str, Dict[str, Any]] = {}
    raw_stocks = progress.get("stocks") or {}
    if isinstance(raw_stocks, dict):
        for code, row in raw_stocks.items():
            c6 = _code6(code)
            if not c6 or not isinstance(row, dict):
                continue
            stocks[c6] = {
                "name": str(row.get("name") or ""),
                "concepts": set(
                    t
                    for t in (row.get("concepts") or [])
                    if t and not _is_dynamic_market_board(str(t))
                ),
                "plates": set(
                    t
                    for t in (row.get("plates") or [])
                    if t and not _is_dynamic_market_board(str(t))
                ),
                "industry": str(row.get("industry") or ""),
            }

    session = _http_session()
    fail_streak = 0

    def _snapshot(extra: Optional[dict] = None) -> None:
        serial = {
            c: {
                "name": r.get("name") or "",
                "concepts": sorted(r.get("concepts") or []),
                "plates": sorted(r.get("plates") or []),
                "industry": r.get("industry") or "",
            }
            for c, r in stocks.items()
        }
        payload = {
            "mode": "f10",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "done_codes": sorted(done_codes),
            "stock_count": len(serial),
            "stocks": serial,
        }
        if extra:
            payload.update(extra)
        _save_json(progress_path, payload)

    pending = [c for c in codes if c not in done_codes]
    print(f"[info] pending={len(pending)} already_done={len(done_codes)}")
    for i, code in enumerate(pending, 1):
        try:
            resp = _retry_get(
                session,
                F10_URL,
                params={"code": _em_secid(code)},
                retries=retries,
                label=f"f10:{code}",
            )
            data = resp.json()
            industry, concepts, plates = _parse_f10_ssbk(data.get("ssbk"))
            name = name_map.get(code, "")
            if not name and isinstance(data.get("ssbk"), list) and data["ssbk"]:
                name = str(data["ssbk"][0].get("SECURITY_NAME_ABBR") or "")
            stocks[code] = {
                "name": name,
                "concepts": set(concepts),
                "plates": set(plates),
                "industry": industry,
            }
            done_codes.add(code)
            fail_streak = 0
            if i % 20 == 0 or i == len(pending):
                print(
                    f"[f10 {i}/{len(pending)}] {code} {name} "
                    f"concepts={len(concepts)} plates={len(plates)} industry={industry}"
                )
        except Exception as e:
            fail_streak += 1
            print(f"[f10 {i}/{len(pending)}] FAIL {code}: {e}")
            if fail_streak >= 8:
                print("[circuit] 连续失败过多，暂停 45 秒…")
                time.sleep(45)
                fail_streak = 0
                try:
                    session.close()
                except Exception:
                    pass
                session = _http_session()

        if i % max(1, save_every) == 0 or i == len(pending):
            _snapshot({"phase": "f10", "last_code": code})
        if sleep_sec > 0:
            time.sleep(sleep_sec)

    return _finalize(out_path, progress_path, stocks, name_map, mode="f10", extra_meta={
        "done_codes": len(done_codes),
        "limit_stocks": limit_stocks,
    })


def _board_names(df) -> List[Tuple[str, str]]:
    if df is None or getattr(df, "empty", True):
        return []
    name_col = "板块名称" if "板块名称" in df.columns else df.columns[0]
    code_col = "板块代码" if "板块代码" in df.columns else None
    out: List[Tuple[str, str]] = []
    for _, row in df.iterrows():
        name = str(row.get(name_col) or "").strip()
        if not name:
            continue
        code = str(row.get(code_col) or "").strip() if code_col else ""
        out.append((name, code))
    return out


def _cons_codes(df) -> List[Tuple[str, str]]:
    if df is None or getattr(df, "empty", True):
        return []
    code_col = "代码" if "代码" in df.columns else None
    name_col = "名称" if "名称" in df.columns else None
    if code_col is None:
        for c in df.columns:
            if "代码" in str(c):
                code_col = c
                break
    if name_col is None:
        for c in df.columns:
            if "名称" in str(c) or "简称" in str(c):
                name_col = c
                break
    if code_col is None:
        return []
    out: List[Tuple[str, str]] = []
    for _, row in df.iterrows():
        code = _code6(row.get(code_col))
        if not code:
            continue
        name = str(row.get(name_col) or "").strip() if name_col else ""
        out.append((code, name))
    return out


def build_boards(
    *,
    out_path: str,
    progress_path: str,
    limit_concepts: Optional[int],
    limit_industries: Optional[int],
    sleep_sec: float,
    retries: int,
    resume: bool,
) -> dict:
    _clear_proxy_env()
    import akshare as ak

    name_map = _load_name_map()
    print(f"[info] mode=boards name_map={len(name_map)}")
    progress = _load_progress(progress_path) if resume else {}
    if progress.get("mode") not in (None, "boards"):
        print("[info] 进度文件模式不匹配，忽略旧进度")
        progress = {}
    done_concepts: Set[str] = set(progress.get("done_concepts") or [])
    done_industries: Set[str] = set(progress.get("done_industries") or [])
    skipped_dynamic: Set[str] = set(progress.get("skipped_dynamic") or [])
    stocks: Dict[str, Dict[str, Any]] = {}
    raw_stocks = progress.get("stocks") or {}
    if isinstance(raw_stocks, dict):
        for code, row in raw_stocks.items():
            c6 = _code6(code)
            if not c6 or not isinstance(row, dict):
                continue
            stocks[c6] = {
                "name": str(row.get("name") or ""),
                "concepts": {
                    t
                    for t in (row.get("concepts") or [])
                    if t and not _is_dynamic_market_board(str(t))
                },
                "plates": {
                    t
                    for t in (row.get("plates") or [])
                    if t and not _is_dynamic_market_board(str(t))
                },
                "industry": str(row.get("industry") or ""),
            }

    def _ensure(code: str, name: str = "") -> Dict[str, Any]:
        row = stocks.get(code)
        if row is None:
            row = {
                "name": name_map.get(code, "") or name or "",
                "concepts": set(),
                "plates": set(),
                "industry": "",
            }
            stocks[code] = row
        elif name and not row.get("name"):
            row["name"] = name
        elif not row.get("name") and name_map.get(code):
            row["name"] = name_map[code]
        return row

    def _snapshot(extra: Optional[dict] = None) -> None:
        serial = {
            c: {
                "name": r.get("name") or "",
                "concepts": sorted(r.get("concepts") or []),
                "plates": sorted(r.get("plates") or []),
                "industry": r.get("industry") or "",
            }
            for c, r in stocks.items()
        }
        payload = {
            "mode": "boards",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "done_concepts": sorted(done_concepts),
            "done_industries": sorted(done_industries),
            "skipped_dynamic": sorted(skipped_dynamic),
            "stock_count": len(serial),
            "stocks": serial,
        }
        if extra:
            payload.update(extra)
        _save_json(progress_path, payload)

    def _retry_call(fn, label: str = ""):
        last = None
        for i in range(max(1, retries)):
            try:
                return fn()
            except Exception as e:
                last = e
                print(f"[warn] {label or fn} fail ({i+1}/{retries}): {type(e).__name__}: {e}")
                if i + 1 < retries:
                    time.sleep(2.0 * (i + 1))
        raise last  # type: ignore[misc]

    print("[info] fetching concept board list...")
    concept_df = _retry_call(lambda: ak.stock_board_concept_name_em(), label="concept_name")
    concept_boards = _board_names(concept_df)
    if limit_concepts is not None:
        concept_boards = concept_boards[: max(0, int(limit_concepts))]
    print(f"[info] concept boards={len(concept_boards)}")

    print("[info] fetching industry board list...")
    industry_df = _retry_call(lambda: ak.stock_board_industry_name_em(), label="industry_name")
    industry_boards = _board_names(industry_df)
    if limit_industries is not None:
        industry_boards = industry_boards[: max(0, int(limit_industries))]
    print(f"[info] industry boards={len(industry_boards)}")

    fail_streak = 0
    for i, (board_name, board_code) in enumerate(concept_boards, 1):
        if board_name in done_concepts or board_name in skipped_dynamic:
            continue
        if _is_dynamic_market_board(board_name):
            print(f"[concept {i}/{len(concept_boards)}] SKIP dynamic: {board_name}")
            skipped_dynamic.add(board_name)
            _snapshot({"phase": "concept_skip", "last_board": board_name})
            continue
        print(f"[concept {i}/{len(concept_boards)}] {board_name} ({board_code})")
        try:
            cons_df = _retry_call(
                lambda bn=board_name: ak.stock_board_concept_cons_em(symbol=bn),
                label=f"concept_cons:{board_name}",
            )
            members = _cons_codes(cons_df)
            for code, nm in members:
                row = _ensure(code, nm)
                row["concepts"].add(board_name)
                row["plates"].add(board_name)
            print(f"  members={len(members)}")
            done_concepts.add(board_name)
            fail_streak = 0
        except Exception as e:
            fail_streak += 1
            print(f"  FAIL: {e}")
            if fail_streak >= 5:
                print("[circuit] push2 连续失败，暂停 60 秒…")
                time.sleep(60)
                fail_streak = 0
        _snapshot({"phase": "concept", "last_board": board_name})
        if sleep_sec > 0:
            time.sleep(max(sleep_sec, 0.8))

    for i, (board_name, board_code) in enumerate(industry_boards, 1):
        if board_name in done_industries or board_name in skipped_dynamic:
            continue
        if _is_dynamic_market_board(board_name):
            print(f"[industry {i}/{len(industry_boards)}] SKIP dynamic: {board_name}")
            skipped_dynamic.add(board_name)
            _snapshot({"phase": "industry_skip", "last_board": board_name})
            continue
        print(f"[industry {i}/{len(industry_boards)}] {board_name} ({board_code})")
        try:
            cons_df = _retry_call(
                lambda bn=board_name: ak.stock_board_industry_cons_em(symbol=bn),
                label=f"industry_cons:{board_name}",
            )
            members = _cons_codes(cons_df)
            for code, nm in members:
                row = _ensure(code, nm)
                row["plates"].add(board_name)
                if not row.get("industry"):
                    row["industry"] = board_name
            print(f"  members={len(members)}")
            done_industries.add(board_name)
            fail_streak = 0
        except Exception as e:
            fail_streak += 1
            print(f"  FAIL: {e}")
            if fail_streak >= 5:
                print("[circuit] push2 连续失败，暂停 60 秒…")
                time.sleep(60)
                fail_streak = 0
        _snapshot({"phase": "industry", "last_board": board_name})
        if sleep_sec > 0:
            time.sleep(max(sleep_sec, 0.8))

    return _finalize(
        out_path,
        progress_path,
        stocks,
        name_map,
        mode="boards",
        extra_meta={
            "concept_boards_done": len(done_concepts),
            "industry_boards_done": len(done_industries),
            "skipped_dynamic_boards": sorted(skipped_dynamic),
            "limit_concepts": limit_concepts,
            "limit_industries": limit_industries,
        },
    )


def _finalize(
    out_path: str,
    progress_path: str,
    stocks: Dict[str, Dict[str, Any]],
    name_map: Dict[str, str],
    *,
    mode: str,
    extra_meta: Optional[dict] = None,
) -> dict:
    incoming: Dict[str, dict] = {}
    for code, row in stocks.items():
        incoming[code] = {
            "stock_code": code,
            "name": row.get("name") or name_map.get(code) or "",
            "concepts": sorted(row.get("concepts") or []),
            "industry": row.get("industry") or "",
            "plates": sorted(row.get("plates") or []),
        }

    existing = _load_existing_stock_info(out_path)
    if existing:
        out, merge_stats = _merge_stock_info_maps(existing, incoming)
        print(
            "[merge] base={base_stocks} incoming={incoming_stocks} -> {merged_stocks} "
            "(+codes={added_codes}, touched={touched_codes}, "
            "+concept_tags={added_concept_tags}, +plate_tags={added_plate_tags})".format(
                **merge_stats
            )
        )
        write_mode = "merge_add"
    else:
        out = incoming
        merge_stats = {
            "base_stocks": 0,
            "incoming_stocks": len(incoming),
            "merged_stocks": len(out),
            "added_codes": len(out),
            "touched_codes": 0,
            "added_concept_tags": 0,
            "added_plate_tags": 0,
        }
        write_mode = "new_file"
        print(f"[write] 无已有文件，直接写入 {len(out)} 只")

    meta = {
        "source": f"eastmoney {mode}",
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "stock_count": len(out),
        "incoming_stock_count": len(incoming),
        "write_mode": write_mode,
        "merge_stats": merge_stats,
        "note": "目标已存在则并集合并；排除昨日连板等动态板",
    }
    if extra_meta:
        meta.update(extra_meta)
    _save_json(out_path, out)
    _save_json(out_path.replace(".json", ".meta.json"), meta)
    # mark progress done
    prog = _load_progress(progress_path)
    prog["phase"] = "done"
    prog["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prog["mode"] = mode
    _save_json(progress_path, prog)
    print(f"[done] stocks={len(out)} mode={write_mode}/{mode} -> {out_path}")
    return out


def _print_preview(out: dict, n: int = 5) -> None:
    rich = [(c, r) for c, r in out.items() if r.get("concepts")]
    rich.sort(key=lambda x: (-len(x[1].get("concepts") or []), x[0]))
    sample = rich[:n] if rich else list(out.items())[:n]
    print("\n===== 预览样本 =====")
    for code, row in sample:
        print(
            json.dumps(
                {
                    code: {
                        "name": row.get("name"),
                        "industry": row.get("industry"),
                        "concepts": row.get("concepts"),
                        "plates_head": (row.get("plates") or [])[:12],
                        "plates_count": len(row.get("plates") or []),
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="东财股票概念/板块标签更新")
    ap.add_argument(
        "--mode",
        choices=("f10", "boards"),
        default="f10",
        help="f10=逐只F10(默认,稳); boards=板块成分反转(push2,易断)",
    )
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出 JSON 路径")
    ap.add_argument("--progress", default=DEFAULT_PROGRESS, help="断点进度文件")
    ap.add_argument("--limit-stocks", type=int, default=None, help="f10 模式只处理前 N 只")
    ap.add_argument("--limit-concepts", type=int, default=None, help="boards 模式前 N 个概念板")
    ap.add_argument("--limit-industries", type=int, default=None, help="boards 模式前 N 个行业板")
    ap.add_argument("--sleep", type=float, default=None, help="请求间隔秒（f10默认0.25, boards默认0.8）")
    ap.add_argument("--retries", type=int, default=3, help="单次请求失败重试次数")
    ap.add_argument("--save-every", type=int, default=50, help="f10 每 N 只存一次进度")
    ap.add_argument("--no-resume", action="store_true", help="忽略进度，从头拉")
    ap.add_argument("--preview", type=int, default=5, help="结束后打印样本条数")
    ap.add_argument(
        "--force",
        action="store_true",
        help="允许试跑(limit)写入正式 all_a_stock_info.json",
    )
    args = ap.parse_args()

    out_path = os.path.abspath(args.out)
    limited = (
        args.limit_stocks is not None
        or args.limit_concepts is not None
        or args.limit_industries is not None
    )
    if limited and os.path.abspath(out_path) == os.path.abspath(DEFAULT_OUT) and not args.force:
        alt = os.path.join(DATA_DIR, "all_a_stock_info.trial.json")
        print(
            f"[safe] 试跑带 limit，避免覆盖正式文件，改写到: {alt}\n"
            f"       全量请去掉 --limit-*；若坚持写正式文件请加 --force"
        )
        out_path = alt

    if args.mode == "f10":
        sleep_sec = 0.25 if args.sleep is None else float(args.sleep)
        out = build_f10(
            out_path=out_path,
            progress_path=args.progress,
            limit_stocks=args.limit_stocks,
            sleep_sec=sleep_sec,
            retries=int(args.retries),
            resume=not args.no_resume,
            save_every=max(1, int(args.save_every)),
        )
    else:
        sleep_sec = 0.8 if args.sleep is None else float(args.sleep)
        out = build_boards(
            out_path=out_path,
            progress_path=args.progress,
            limit_concepts=args.limit_concepts,
            limit_industries=args.limit_industries,
            sleep_sec=sleep_sec,
            retries=int(args.retries),
            resume=not args.no_resume,
        )

    if args.preview > 0:
        _print_preview(out, n=int(args.preview))


if __name__ == "__main__":
    main()
