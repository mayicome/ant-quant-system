# -*- coding: utf-8 -*-
"""把 QMT 探针日线写入 data/daily_cache。

优先：FormulaOutput 的 ANT_DAILY|ROW / ANT_DAILY|BATCH（QMT 策略禁 FileIO）。
兼容：SUMMARY.out_path 指向的 jsonl（若存在）。

用法：
  python tools/ingest_qmt_daily_cache.py --summary-json '{"run_tag":...,"out_path":...}'
  python tools/ingest_qmt_daily_cache.py --jsonl D:\\...\\daily_....jsonl --date 2026-10-27
  python tools/ingest_qmt_daily_cache.py --from-formula-log --run-tag 20261027_190501
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.qmt_daily_probe import (  # noqa: E402
    PROJECT_OUT_DIR,
    collect_rows_for_run_tag,
    decode_formula_log_bytes,
    find_summary_for_run_tag,
    parse_ant_daily_file,
    resolve_today_formula_output,
)

CACHE = ROOT / "data" / "daily_cache"
MANIFEST = CACHE / "manifest.json"


def _fmt_px(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_vol(value: float) -> str:
    return str(int(round(float(value))))


def _fmt_amt(value) -> str:
    if value is None:
        return ""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text


def _bar_line(bar: dict) -> str:
    return ",".join(
        [
            str(bar["date"])[:10],
            _fmt_px(bar["open"]),
            _fmt_px(bar["high"]),
            _fmt_px(bar["low"]),
            _fmt_px(bar["close"]),
            _fmt_vol(bar["volume"]),
            _fmt_amt(bar.get("amount")),
        ]
    )


def _read_rows(path: Path) -> Dict[str, dict]:
    rows: Dict[str, dict] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line.strip() or line.lower().startswith("date,"):
            continue
        parts = line.split(",")
        if len(parts) < 6:
            continue
        d = parts[0][:10]
        try:
            rows[d] = {
                "date": d,
                "open": float(parts[1]),
                "high": float(parts[2]),
                "low": float(parts[3]),
                "close": float(parts[4]),
                "volume": float(parts[5]),
                "amount": float(parts[6]) if len(parts) > 6 and parts[6] != "" else None,
            }
        except (TypeError, ValueError):
            continue
    return rows


def _write_rows(path: Path, rows: Dict[str, dict]) -> None:
    buf = ["date,open,high,low,close,volume,amount\n"]
    for d in sorted(rows.keys()):
        buf.append(_bar_line(rows[d]) + "\n")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes("".join(buf).encode("utf-8"))
    tmp.replace(path)


def _last_date(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 240))
            tail = handle.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return ""
    if not tail:
        return ""
    return tail[-1].split(",")[0][:10]


def _append(path: Path, bar: dict) -> None:
    line = _bar_line(bar)
    data = path.read_bytes() if path.is_file() else b""
    if data and not data.endswith(b"\n"):
        data += b"\n"
    if not data:
        data = b"date,open,high,low,close,volume,amount\n"
    data += (line + "\n").encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def upsert_bar(path: Path, bar: dict) -> str:
    """写入/覆盖目标日。返回 appended|updated|same|created。"""
    day = str(bar["date"])[:10]
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_rows(path, {day: bar})
        return "created"
    last = _last_date(path)
    if last != day and day not in _read_rows(path):
        if last < day or last == "":
            _append(path, bar)
            return "appended"
    rows = _read_rows(path)
    old = rows.get(day)
    rows[day] = {
        "date": day,
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "volume": float(bar.get("volume") or 0),
        "amount": bar.get("amount"),
    }
    if old is not None:
        same = (
            abs(float(old["open"]) - float(bar["open"])) < 1e-6
            and abs(float(old["high"]) - float(bar["high"])) < 1e-6
            and abs(float(old["low"]) - float(bar["low"])) < 1e-6
            and abs(float(old["close"]) - float(bar["close"])) < 1e-6
        )
        if same:
            return "same"
        _write_rows(path, rows)
        return "updated"
    _write_rows(path, rows)
    return "upserted"


def _update_manifest(day: str, ok: int, miss: int, universe: int) -> None:
    if not MANIFEST.is_file():
        return
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    covered = ok
    base = max(universe, int(data.get("universe_count") or 0), 1)
    success = (universe > 0 and ok >= int(universe * 0.8)) or (
        ok >= int(base * 0.8)
    )
    if not success:
        print(
            "[ingest] manifest 未更新：ok=%d universe/job_total=%d base=%d (<80%%)"
            % (ok, universe, base),
            flush=True,
        )
        return
    data["sync_trade_date"] = day
    data["status"] = "completed"
    data["ok_count"] = ok
    data["fail_count"] = miss
    data["skip_count"] = 0
    data["finished_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    data["trigger"] = "qmt_probe"
    data["primary_source"] = "qmt"
    data["primary_path"] = "qmt_probe/formula_or_jsonl"
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[ingest] manifest 已更新 sync_trade_date=%s ok=%d" % (day, ok), flush=True)


def ingest_rows(
    rows: Iterable[dict],
    *,
    trade_date: str = "",
    update_manifest: bool = True,
    summary: Optional[dict] = None,
    mirror_name: str = "",
) -> int:
    counts = {"appended": 0, "updated": 0, "same": 0, "created": 0, "upserted": 0, "bad": 0}
    day_seen = trade_date[:10] if trade_date else ""
    n = 0
    mirrored: List[str] = []

    for rec in rows:
        code = str((rec or {}).get("code") or "").strip().upper()
        day = str((rec or {}).get("date") or day_seen or "")[:10]
        if not code or not day:
            counts["bad"] += 1
            continue
        if not day_seen:
            day_seen = day
        try:
            bar = {
                "date": day,
                "open": float(rec["open"]),
                "high": float(rec["high"]),
                "low": float(rec["low"]),
                "close": float(rec["close"]),
                "volume": float(rec.get("volume") or 0),
                "amount": rec.get("amount"),
            }
        except Exception:
            counts["bad"] += 1
            continue
        path = CACHE / ("%s.csv" % code)
        try:
            kind = upsert_bar(path, bar)
            counts[kind] = counts.get(kind, 0) + 1
        except Exception:
            counts["bad"] += 1
            continue
        n += 1
        mirrored.append(
            json.dumps(
                {
                    "code": code,
                    "date": day,
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"],
                    "amount": bar.get("amount"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        if n % 1000 == 0:
            print("[ingest] 进度 %d %s" % (n, counts), flush=True)

    if mirror_name and mirrored:
        try:
            PROJECT_OUT_DIR.mkdir(parents=True, exist_ok=True)
            dest = PROJECT_OUT_DIR / mirror_name
            dest.write_text("\n".join(mirrored) + "\n", encoding="utf-8")
            print("[ingest] 已镜像 Formula 行到 %s (%d)" % (dest, len(mirrored)), flush=True)
        except Exception as e:
            print("[ingest] 镜像失败（忽略）: %s" % e, flush=True)

    ok = (
        counts.get("appended", 0)
        + counts.get("updated", 0)
        + counts.get("same", 0)
        + counts.get("created", 0)
        + counts.get("upserted", 0)
    )
    miss = int((summary or {}).get("miss") or 0)
    total = int((summary or {}).get("total") or 0)
    print(
        "[ingest] 完成 day=%s rows=%d counts=%s summary_ok=%s miss=%s"
        % (day_seen, n, counts, (summary or {}).get("ok"), miss),
        flush=True,
    )
    if update_manifest and day_seen:
        _update_manifest(day_seen, ok=ok, miss=miss, universe=total or ok)
    return 0 if ok > 0 else 1


def ingest_jsonl(
    jsonl_path: Path,
    *,
    trade_date: str = "",
    mirror: bool = True,
    update_manifest: bool = True,
    summary: Optional[dict] = None,
) -> int:
    jsonl_path = Path(jsonl_path)
    if not jsonl_path.is_file():
        print("[ingest] 找不到 jsonl: %s" % jsonl_path, flush=True)
        return 2

    if mirror:
        try:
            PROJECT_OUT_DIR.mkdir(parents=True, exist_ok=True)
            dest = PROJECT_OUT_DIR / jsonl_path.name
            dest.write_bytes(jsonl_path.read_bytes())
            print("[ingest] 已镜像到 %s" % dest, flush=True)
        except Exception as e:
            print("[ingest] 镜像失败（忽略）: %s" % e, flush=True)

    rows = []
    with jsonl_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return ingest_rows(
        rows,
        trade_date=trade_date,
        update_manifest=update_manifest,
        summary=summary,
        mirror_name="",
    )


def ingest_from_formula(
    *,
    run_tag: str,
    trade_date: str = "",
    summary: Optional[dict] = None,
    update_manifest: bool = True,
) -> int:
    path, err = resolve_today_formula_output()
    if path is None:
        print("[ingest] %s" % err, flush=True)
        return 2
    text = decode_formula_log_bytes(path.read_bytes())
    if not summary and run_tag:
        summary = find_summary_for_run_tag(text, run_tag)
    rows = collect_rows_for_run_tag(text, run_tag) if run_tag else []
    if not rows and summary:
        # 兜底：解析整份取最后 run
        parsed = parse_ant_daily_file(path)
        if str(parsed.get("run_tag") or "") in ("", run_tag):
            rows = list(parsed.get("rows") or [])
    if not rows:
        print(
            "[ingest] FormulaOutput 无 run_tag=%s 的 ROW/BATCH（path=%s）"
            % (run_tag, path),
            flush=True,
        )
        return 2
    day = trade_date or str((summary or {}).get("trade_date") or "")
    stamp = run_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    return ingest_rows(
        rows,
        trade_date=day,
        update_manifest=update_manifest,
        summary=summary if isinstance(summary, dict) else None,
        mirror_name="daily_formula_%s.jsonl" % stamp,
    )


def ingest_from_summary(
    summary: dict,
    *,
    trade_date: str = "",
    run_tag: str = "",
    update_manifest: bool = True,
) -> int:
    """优先 jsonl（若 out_path 存在），否则 FormulaOutput ROW/BATCH。"""
    summary = summary if isinstance(summary, dict) else {}
    if summary.get("error"):
        print("[ingest] SUMMARY 含 error，跳过入库: %s" % summary.get("error"), flush=True)
        return 4
    tag = str(run_tag or summary.get("run_tag") or "").strip()
    day = (trade_date or str(summary.get("trade_date") or ""))[:10]
    out_path = str(summary.get("out_path") or "").strip()
    if out_path:
        p = Path(out_path)
        if p.is_file() and p.stat().st_size > 0:
            print("[ingest] 使用 jsonl: %s" % p, flush=True)
            return ingest_jsonl(
                p,
                trade_date=day,
                update_manifest=update_manifest,
                summary=summary,
            )
        print("[ingest] out_path 不可用，改走 FormulaOutput ROW/BATCH: %s" % out_path, flush=True)
    if not tag:
        print("[ingest] 无 run_tag，无法从 FormulaOutput 收集行", flush=True)
        return 2
    return ingest_from_formula(
        run_tag=tag,
        trade_date=day,
        summary=summary,
        update_manifest=update_manifest,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="QMT 探针日线 → data/daily_cache")
    ap.add_argument("--jsonl", default="", help="jsonl 路径")
    ap.add_argument("--date", default="", help="目标日 YYYY-MM-DD（可从记录推断）")
    ap.add_argument("--summary-json", default="", help="SUMMARY JSON 字符串")
    ap.add_argument("--summary-file", default="", help="SUMMARY JSON 文件")
    ap.add_argument("--from-formula-log", action="store_true", help="从当天 FormulaOutput 取 SUMMARY + ROW/BATCH")
    ap.add_argument("--run-tag", default="", help="指定 run_tag（配合 --from-formula-log）")
    ap.add_argument("--no-mirror", action="store_true")
    ap.add_argument("--no-manifest", action="store_true")
    args = ap.parse_args()

    summary = None
    if args.summary_json:
        summary = json.loads(args.summary_json)
    elif args.summary_file:
        summary = json.loads(Path(args.summary_file).read_text(encoding="utf-8"))
    elif args.from_formula_log:
        path, err = resolve_today_formula_output()
        if path is None:
            print("[ingest] %s" % err, flush=True)
            return 2
        text = decode_formula_log_bytes(path.read_bytes())
        if args.run_tag:
            summary = find_summary_for_run_tag(text, args.run_tag)
            if not summary:
                print("[ingest] FormulaOutput 无 run_tag=%s 的 SUMMARY" % args.run_tag, flush=True)
                return 2
        else:
            parsed = parse_ant_daily_file(path)
            summary = parsed.get("summary") or {}
            if not summary:
                print("[ingest] FormulaOutput 无 ANT_DAILY SUMMARY: %s" % path, flush=True)
                return 2
            if not args.run_tag:
                args.run_tag = str(parsed.get("run_tag") or summary.get("run_tag") or "")

    if args.jsonl:
        return ingest_jsonl(
            Path(args.jsonl),
            trade_date=args.date,
            mirror=not args.no_mirror,
            update_manifest=not args.no_manifest,
            summary=summary if isinstance(summary, dict) else None,
        )

    if summary is not None:
        return ingest_from_summary(
            summary,
            trade_date=args.date or str(summary.get("trade_date") or ""),
            run_tag=args.run_tag or str(summary.get("run_tag") or ""),
            update_manifest=not args.no_manifest,
        )

    print("[ingest] 请提供 --jsonl / --from-formula-log / SUMMARY", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
