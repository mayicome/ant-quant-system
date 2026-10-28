# -*- coding: utf-8 -*-
"""QMT 探针拉日线：路径、协议解析、股票池与 job 落盘。

协议（FormulaOutput）——QMT 策略内禁止 FileIO，K 线只能打日志：
  ANT_DAILY|START|{"run_tag":"...","trade_date":"YYYY-MM-DD",...}
  ANT_DAILY|ROW|{code,date,open,high,low,close,volume,amount}
  ANT_DAILY|BATCH|{"run_tag":"...","rows":[{...},...]}
  ANT_DAILY|PROGRESS|{"run_tag":"...","done":100,"total":5000,...}
  ANT_DAILY|SUMMARY|{"run_tag":"...","trade_date":"...","ok":N,"miss":M,
                     "out_path":"","error":"","source":"qmt_probe_formula"}
  ANT_DAILY|ERROR|短文本或 JSON

job.json 仍可由本机 CLI 旁路写出（给人看 / 调试）；探针脚本必须内嵌 JOB，禁止 open。
若 SUMMARY.out_path 指向存在的 jsonl（旧路径），ingest 仍可读；否则从 ROW/BATCH 入库。
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

RESULT_PREFIX = "ANT_DAILY|"
DEFAULT_QMT_USERDATA = Path(r"D:\国金证券QMT交易端2\userdata")
DEFAULT_WATCH_DIR = DEFAULT_QMT_USERDATA / "qmt_daily_watch"
DEFAULT_WATCH_PATH = DEFAULT_WATCH_DIR / "current.py"
DEFAULT_JOB_PATH = DEFAULT_WATCH_DIR / "job.json"
DEFAULT_OUT_DIR = DEFAULT_QMT_USERDATA / "qmt_daily_out"
DEFAULT_FORMULA_LOG_DIR = DEFAULT_QMT_USERDATA / "log"

# 项目侧镜像（ingest 可选复制），不依赖探针可写项目盘
PROJECT_OUT_DIR = ROOT / "data" / "qmt_daily_out"
PROJECT_WATCH_BACKUP = ROOT / "data" / "qmt_daily_watch_backup"

_LINE_RE = re.compile(r"ANT_DAILY\|([A-Z_]+)\|(.*)$")
_JOB_JSON_PLACEHOLDER = "__ANT_DAILY_JOB_JSON__"


def qmt_userdata() -> Path:
    return DEFAULT_QMT_USERDATA


def watch_dir() -> Path:
    return DEFAULT_WATCH_DIR


def watch_path() -> Path:
    return DEFAULT_WATCH_PATH


def job_path() -> Path:
    return DEFAULT_JOB_PATH


def out_dir() -> Path:
    return DEFAULT_OUT_DIR


def formula_log_dir() -> Path:
    return DEFAULT_FORMULA_LOG_DIR


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(str(tmp), str(path))
    return path


def atomic_write_json(path: Path, data: Any) -> Path:
    return atomic_write_text(
        path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def resolve_today_formula_output(
    *, as_of: Optional[datetime] = None, log_dir: Optional[Path] = None
) -> Tuple[Optional[Path], str]:
    """定位当天 XtClient_FormulaOutput_YYYYMMDD.log。"""
    log_dir = Path(log_dir) if log_dir else formula_log_dir()
    if not log_dir.is_dir():
        return None, "未找到 QMT userdata/log 目录: %s" % log_dir
    day = (as_of or datetime.now()).strftime("%Y%m%d")
    today = log_dir / ("XtClient_FormulaOutput_%s.log" % day)
    if today.is_file():
        return today, ""
    cands = sorted(
        log_dir.glob("XtClient_FormulaOutput_%s*.log" % day),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if cands:
        return cands[0], ""
    return None, "当天还没有 FormulaOutput 日志: %s" % today


def load_universe_codes(*, include_etf: bool = True, include_bj: bool = True) -> List[str]:
    """全 A（a_share_universe.json）+ 可选 ETF。返回 CODE.SZ/SH/BJ。"""
    codes: List[str] = []
    seen = set()

    def _add(raw: str) -> None:
        c = str(raw or "").strip().upper()
        if not c:
            return
        if "." not in c:
            c6 = "".join(ch for ch in c if ch.isdigit())[-6:].zfill(6)
            if len(c6) != 6:
                return
            if c6.startswith(("5", "6", "9")):
                c = c6 + ".SH"
            elif c6.startswith(("0", "1", "2", "3")):
                c = c6 + ".SZ"
            else:
                c = c6 + ".BJ"
        if not include_bj and c.endswith(".BJ"):
            return
        if c in seen:
            return
        seen.add(c)
        codes.append(c)

    ashare = ROOT / "data" / "a_share_universe.json"
    if ashare.is_file():
        data = json.loads(ashare.read_text(encoding="utf-8"))
        for c in data.get("codes") or []:
            _add(c)
    else:
        csv_path = ROOT / "data" / "all_a_stocks.csv"
        if csv_path.is_file():
            for i, line in enumerate(csv_path.read_text(encoding="utf-8", errors="replace").splitlines()):
                if i == 0 and ("code" in line.lower() or "代码" in line):
                    continue
                part = line.split(",")[0].strip()
                _add(part)

    if include_etf:
        etf = ROOT / "data" / "etf_universe.json"
        if etf.is_file():
            data = json.loads(etf.read_text(encoding="utf-8"))
            for c in data.get("codes") or []:
                _add(c)

    return codes


def make_run_tag(when: Optional[datetime] = None) -> str:
    return (when or datetime.now()).strftime("%Y%m%d_%H%M%S")


def build_job(
    *,
    trade_date: str,
    codes: List[str],
    run_tag: str,
    batch_size: int = 80,
    include_etf: bool = True,
    download_first: bool = True,
    emit_batch: int = 40,
) -> Dict[str, Any]:
    day = trade_date[:10]
    ymd = day.replace("-", "")
    return {
        "run_tag": run_tag,
        "trade_date": day,
        "start_ymd": ymd,
        "end_ymd": ymd,
        "codes": list(codes),
        "batch_size": int(batch_size),
        "include_etf": bool(include_etf),
        "download_first": bool(download_first),
        "emit_batch": int(emit_batch or 40),
        "out_dir": str(out_dir()),
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }


def render_watch_script(template_text: str, job: Dict[str, Any]) -> str:
    """把 JOB JSON 嵌入模板；替换 __ANT_DAILY_JOB_JSON__ 为 repr(json 字符串)。"""
    source = template_text
    if not source.lstrip().startswith("#coding") and not source.lstrip().startswith("# -*-"):
        source = "#coding:utf-8\n" + source
    if _JOB_JSON_PLACEHOLDER not in source:
        raise RuntimeError(
            "模板缺少占位符 %s，无法内嵌 JOB（禁止探针 open job.json）"
            % _JOB_JSON_PLACEHOLDER
        )
    payload = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
    return source.replace(_JOB_JSON_PLACEHOLDER, repr(payload))


def _normalize_row(rec: Any, *, default_date: str = "", run_tag: str = "") -> Optional[Dict[str, Any]]:
    if not isinstance(rec, dict):
        return None
    code = str(rec.get("code") or "").strip().upper()
    day = str(rec.get("date") or default_date or "")[:10]
    if not code or not day:
        return None
    try:
        return {
            "code": code,
            "date": day,
            "open": float(rec["open"]),
            "high": float(rec["high"]),
            "low": float(rec["low"]),
            "close": float(rec["close"]),
            "volume": float(rec.get("volume") or 0),
            "amount": rec.get("amount"),
            "run_tag": str(rec.get("run_tag") or run_tag or ""),
        }
    except Exception:
        return None


def parse_ant_daily_text(text: str) -> Dict[str, Any]:
    """解析 FormulaOutput 中 ANT_DAILY 行；取最后一次带 SUMMARY 的 run。"""
    runs: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    all_errors: List[str] = []

    def _open(start_payload: Optional[dict] = None) -> Dict[str, Any]:
        sp = start_payload if isinstance(start_payload, dict) else {}
        run = {
            "start": sp,
            "run_tag": str(sp.get("run_tag") or ""),
            "progress": [],
            "summary": {},
            "errors": [],
            "rows": [],
        }
        runs.append(run)
        return run

    def _append_row(run: Dict[str, Any], rec: Any) -> None:
        row = _normalize_row(
            rec,
            default_date=str((run.get("start") or {}).get("trade_date") or ""),
            run_tag=str(run.get("run_tag") or ""),
        )
        if row is not None:
            run["rows"].append(row)

    for raw in (text or "").splitlines():
        line = raw.strip()
        idx = line.find("ANT_DAILY|")
        if idx < 0:
            continue
        line = line[idx:]
        m = _LINE_RE.match(line)
        if not m:
            continue
        kind, body = m.group(1), m.group(2)
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"raw": body}
        if kind == "START":
            current = _open(payload if isinstance(payload, dict) else {})
        elif kind == "PROGRESS" and isinstance(payload, dict):
            if current is None:
                current = _open(payload)
            current["progress"].append(payload)
            tag = str(payload.get("run_tag") or "")
            if tag and not current.get("run_tag"):
                current["run_tag"] = tag
        elif kind == "ROW":
            if current is None:
                current = _open(payload if isinstance(payload, dict) else {})
            _append_row(current, payload)
        elif kind == "BATCH" and isinstance(payload, dict):
            if current is None:
                current = _open(payload)
            tag = str(payload.get("run_tag") or "")
            if tag and not current.get("run_tag"):
                current["run_tag"] = tag
            for rec in payload.get("rows") or []:
                _append_row(current, rec)
        elif kind == "SUMMARY" and isinstance(payload, dict):
            if current is None:
                current = _open(payload)
            current["summary"] = payload
            tag = str(payload.get("run_tag") or "")
            if tag:
                current["run_tag"] = tag
        elif kind == "ERROR":
            err = body if not isinstance(payload, dict) else (
                str(payload.get("error") or payload.get("raw") or payload)
            )
            all_errors.append(err)
            if current is not None:
                current["errors"].append(err)

    chosen: Optional[Dict[str, Any]] = None
    for run in runs:
        if run.get("summary") or run.get("start"):
            chosen = run

    summary = (chosen or {}).get("summary") or {}
    start = (chosen or {}).get("start") or {}
    errors = list((chosen or {}).get("errors") or [])
    if not errors and all_errors:
        errors = all_errors[-5:]

    return {
        "start": start,
        "summary": summary,
        "progress": list((chosen or {}).get("progress") or []),
        "errors": errors,
        "rows": list((chosen or {}).get("rows") or []),
        "run_tag": str(
            (chosen or {}).get("run_tag")
            or summary.get("run_tag")
            or start.get("run_tag")
            or ""
        ),
        "runs_in_log": len(runs),
    }


def parse_ant_daily_file(path: Path) -> Dict[str, Any]:
    path = Path(path)
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8", "gbk", "utf-16", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    result = parse_ant_daily_text(text)
    result["source_path"] = str(path)
    return result


def collect_rows_for_run_tag(text: str, run_tag: str) -> List[Dict[str, Any]]:
    """从全文收集指定 run_tag 的 ROW/BATCH（按出现顺序；同 code 后者覆盖）。"""
    want = str(run_tag or "").strip()
    if not want:
        return []
    by_code: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    active = False
    active_date = ""

    for raw in (text or "").splitlines():
        line = raw.strip()
        idx = line.find("ANT_DAILY|")
        if idx < 0:
            continue
        line = line[idx:]
        m = _LINE_RE.match(line)
        if not m:
            continue
        kind, body = m.group(1), m.group(2)
        try:
            payload = json.loads(body)
        except Exception:
            continue
        if kind == "START" and isinstance(payload, dict):
            active = str(payload.get("run_tag") or "") == want
            if active:
                active_date = str(payload.get("trade_date") or "")[:10]
                by_code.clear()
                order.clear()
            continue
        if kind == "SUMMARY" and isinstance(payload, dict):
            if str(payload.get("run_tag") or "") == want:
                active = False
            continue
        if not active and kind in ("ROW", "BATCH"):
            # 容忍无 START、仅靠 BATCH.run_tag / 行内字段
            if kind == "BATCH" and isinstance(payload, dict):
                if str(payload.get("run_tag") or "") != want:
                    continue
            elif kind == "ROW" and isinstance(payload, dict):
                if str(payload.get("run_tag") or "") not in ("", want):
                    continue
                # ROW 通常无 run_tag；无 active 时跳过（除非 BATCH 已标）
                if str(payload.get("run_tag") or "") != want:
                    continue
            else:
                continue
        elif not active:
            continue

        rows_in = []
        if kind == "ROW":
            rows_in = [payload]
        elif kind == "BATCH" and isinstance(payload, dict):
            if str(payload.get("run_tag") or "") not in ("", want):
                continue
            rows_in = list(payload.get("rows") or [])
        else:
            continue

        for rec in rows_in:
            row = _normalize_row(rec, default_date=active_date, run_tag=want)
            if row is None:
                continue
            code = row["code"]
            if code not in by_code:
                order.append(code)
            by_code[code] = row

    return [by_code[c] for c in order]


def find_summary_for_run_tag(text: str, run_tag: str) -> Optional[Dict[str, Any]]:
    """在全文中找指定 run_tag 的最后一条 SUMMARY。"""
    want = str(run_tag or "").strip()
    if not want:
        return None
    last = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        idx = line.find("ANT_DAILY|SUMMARY|")
        if idx < 0:
            continue
        body = line[idx + len("ANT_DAILY|SUMMARY|") :]
        try:
            payload = json.loads(body)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("run_tag") or "") == want:
            last = payload
    return last


def read_log_tail_text(path: Path, *, from_offset: int = 0) -> Tuple[str, int]:
    """从 offset 读到文件末尾；返回 (text, new_offset)。"""
    path = Path(path)
    size = path.stat().st_size
    if from_offset > size:
        from_offset = 0
    with path.open("rb") as handle:
        handle.seek(from_offset)
        chunk = handle.read()
        new_off = handle.tell()
    text = None
    for enc in ("utf-8", "gbk", "utf-16", "latin-1"):
        try:
            text = chunk.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = chunk.decode("utf-8", errors="replace")
    return text, int(new_off)


def decode_formula_log_bytes(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")
