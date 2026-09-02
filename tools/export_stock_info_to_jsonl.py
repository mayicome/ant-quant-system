# -*- coding: utf-8 -*-
"""导出 all_a_stock_info 供智能体使用。

主文件（全量覆盖最新）：
  data/cos/stock_info/all_a_stock_info.jsonl

旁路增量（相对上次导出快照）：
  data/cos/stock_info/{YYYYMMDD}.stock_info_delta.jsonl

用法：
  python tools/export_stock_info_to_jsonl.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC_JSON = ROOT / "data" / "all_a_stock_info.json"
OUT_DIR = ROOT / "data" / "cos" / "stock_info"
OUT_JSONL = OUT_DIR / "all_a_stock_info.jsonl"
SNAPSHOT_JSON = OUT_DIR / "all_a_stock_info.snapshot.json"


def _code6(v: Any) -> str:
    s = str(v or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _tag_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    if isinstance(v, (list, tuple, set)):
        out: List[str] = []
        seen: Set[str] = set()
        for x in v:
            t = str(x or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out
    t = str(v).strip()
    return [t] if t else []


def _tag_set(v: Any) -> Set[str]:
    return set(_tag_list(v))


def _load_stock_map(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("stocks"), dict):
        raw = raw["stocks"]
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        c6 = _code6(v.get("stock_code") or k)
        if not c6:
            continue
        out[c6] = {
            "stock_code": c6,
            "name": str(v.get("name") or "").strip(),
            "industry": str(v.get("industry") or "").strip(),
            "concepts": _tag_list(v.get("concepts")),
            "plates": _tag_list(v.get("plates")),
        }
    return out


def _row_obj(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stock_code": rec.get("stock_code"),
        "name": rec.get("name") or "",
        "industry": rec.get("industry") or "",
        "concepts": list(rec.get("concepts") or []),
        "plates": list(rec.get("plates") or []),
    }


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
    return len(rows)


def _diff_one(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if old is None:
        return {
            "change": "added",
            "stock_code": new.get("stock_code"),
            "name": new.get("name") or "",
            "industry": new.get("industry") or "",
            "concepts": list(new.get("concepts") or []),
            "plates": list(new.get("plates") or []),
        }
    changes: Dict[str, Any] = {}
    if str(old.get("name") or "") != str(new.get("name") or ""):
        changes["name"] = {"from": old.get("name") or "", "to": new.get("name") or ""}
    if str(old.get("industry") or "") != str(new.get("industry") or ""):
        changes["industry"] = {
            "from": old.get("industry") or "",
            "to": new.get("industry") or "",
        }
    old_c, new_c = _tag_set(old.get("concepts")), _tag_set(new.get("concepts"))
    if old_c != new_c:
        changes["concepts"] = {
            "added": sorted(new_c - old_c),
            "removed": sorted(old_c - new_c),
        }
    old_p, new_p = _tag_set(old.get("plates")), _tag_set(new.get("plates"))
    if old_p != new_p:
        changes["plates"] = {
            "added": sorted(new_p - old_p),
            "removed": sorted(old_p - new_p),
        }
    if not changes:
        return None
    return {
        "change": "updated",
        "stock_code": new.get("stock_code"),
        "name": new.get("name") or "",
        "changes": changes,
    }


def build_delta(
    prev: Dict[str, Dict[str, Any]],
    cur: Dict[str, Dict[str, Any]],
    *,
    trade_ymd: str,
) -> List[Dict[str, Any]]:
    trade_date = f"{trade_ymd[0:4]}-{trade_ymd[4:6]}-{trade_ymd[6:8]}"
    rows: List[Dict[str, Any]] = []
    for c6, rec in sorted(cur.items()):
        d = _diff_one(prev.get(c6), rec)
        if d:
            d["trade_date"] = trade_date
            d["trade_date_ymd"] = trade_ymd
            rows.append(d)
    for c6, rec in sorted(prev.items()):
        if c6 in cur:
            continue
        rows.append(
            {
                "trade_date": trade_date,
                "trade_date_ymd": trade_ymd,
                "change": "removed",
                "stock_code": c6,
                "name": rec.get("name") or "",
                "industry": rec.get("industry") or "",
                "concepts": list(rec.get("concepts") or []),
                "plates": list(rec.get("plates") or []),
            }
        )
    return rows


def export_stock_info_jsonl(
    *,
    src_json: Path | str | None = None,
    out_dir: Path | str | None = None,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    src = Path(src_json) if src_json else SRC_JSON
    dest = Path(out_dir) if out_dir else OUT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    out_jsonl = dest / "all_a_stock_info.jsonl"
    snapshot = dest / "all_a_stock_info.snapshot.json"
    day = as_of or date.today()
    ymd = day.strftime("%Y%m%d")

    cur = _load_stock_map(src)
    if not cur:
        raise FileNotFoundError("empty or missing %s" % src)

    rows = [_row_obj(cur[c]) for c in sorted(cur)]
    n_full = write_jsonl(out_jsonl, rows)

    prev = _load_stock_map(snapshot) if snapshot.is_file() else {}
    delta_rows = build_delta(prev, cur, trade_ymd=ymd) if prev else []
    delta_path = dest / ("%s.stock_info_delta.jsonl" % ymd)
    n_delta = 0
    if prev and delta_rows:
        n_delta = write_jsonl(delta_path, delta_rows)
    elif delta_path.is_file():
        try:
            delta_path.unlink()
        except Exception:
            pass

    # 更新快照供下次增量
    snapshot.write_text(
        json.dumps(cur, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": day.isoformat(),
        "source": str(src),
        "full_path": str(out_jsonl),
        "full_lines": n_full,
        "delta_path": str(delta_path) if n_delta else "",
        "delta_lines": n_delta,
        "delta_skipped_first_run": not bool(prev),
    }
    (dest / "stock_info_export_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "[stock_info jsonl] full=%d delta=%s"
        % (n_full, n_delta if prev else "skipped(first)")
    )
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="导出 all_a_stock_info 全量 JSONL + 可选增量")
    ap.add_argument("--src", default=str(SRC_JSON))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--date", default="", help="增量文件日期戳 YYYY-MM-DD，默认今天")
    args = ap.parse_args()
    as_of = None
    if args.date:
        s = str(args.date).strip()
        if len(s) == 8 and s.isdigit():
            as_of = date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
        else:
            as_of = date.fromisoformat(s[:10])
    meta = export_stock_info_jsonl(src_json=args.src, out_dir=args.out_dir, as_of=as_of)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
