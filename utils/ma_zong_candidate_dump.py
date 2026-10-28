# -*- coding: utf-8 -*-
"""马总盘中硬门槛候选落盘：扁平化软门槛诊断列 + 读写配置。"""
from __future__ import annotations

import configparser
import csv
import json
import os
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SOFT_SPECS: Tuple[Tuple[str, str, str], ...] = (
    # (prefix_in_fail_reasons, ok_key, label)
    ("行业或概念排名不满足", "条件_行业或概念排名达标", "板块排名"),
    ("主力净流入不满足", "条件_主力净流入>=3500万", "主力净流入"),
    ("前", "条件_前10日无大涨", "前10日无大涨"),  # 前N日无大涨不满足…
    ("价站上MA5且MA20不满足", "条件_价站上MA5且MA20", "价>MA5且MA20"),
    ("当天未涨停过不满足", "条件_当天未涨停过", "当天未涨停"),
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_output_dir(root: Optional[Path] = None) -> Path:
    return (root or project_root()) / "data" / "ma_zong_intraday_candidates"


def _yes_no(v: Any) -> str:
    return "是" if bool(v) else "否"


def _s(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


DEFAULT_DUMP_TIMES = ["11:00", "14:00", "14:30", "14:40", "14:50", "14:55"]


def parse_dump_time(s: str) -> dt_time:
    text = str(s or "14:40").strip()
    parts = text.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    sec = int(parts[2]) if len(parts) > 2 else 0
    return dt_time(h, m, sec)


def format_dump_time(t: dt_time) -> str:
    return "%02d:%02d" % (int(t.hour), int(t.minute))


def parse_dump_times(raw: Any) -> List[str]:
    """解析逗号分隔的 HH:MM 列表；去重保序。空则默认 DEFAULT_DUMP_TIMES。"""
    if isinstance(raw, (list, tuple)):
        pieces = [str(x).strip() for x in raw if str(x).strip()]
        text = ",".join(pieces)
    else:
        text = str(raw or "").strip()
    if not text:
        return list(DEFAULT_DUMP_TIMES)
    # 兼容中文逗号/分号/空白分隔
    for sep in ("，", ";", "；", "|", "\n", "\t"):
        text = text.replace(sep, ",")
    out: List[str] = []
    seen = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        # 允许 "11:00 14:00" 这种空格串在同一段
        sub_parts = chunk.split() if " " in chunk and ":" in chunk else [chunk]
        for part in sub_parts:
            part = part.strip()
            if not part:
                continue
            try:
                hm = format_dump_time(parse_dump_time(part))
            except Exception:
                continue
            if hm in seen:
                continue
            seen.add(hm)
            out.append(hm)
    return out or list(DEFAULT_DUMP_TIMES)


def load_dump_config(root: Optional[Path] = None) -> Dict[str, Any]:
    """读取 [MaZongIntradayDump]；优先 dump_times，兼容旧 dump_time。"""
    root = root or project_root()
    path = root / "data" / "config.ini"
    default_times = list(DEFAULT_DUMP_TIMES)
    cfg: Dict[str, Any] = {
        "enabled": True,
        "dump_times": default_times,
        "dump_time": default_times[0],
        "dump_time_obj": parse_dump_time(default_times[0]),
        "dump_time_objs": [parse_dump_time(x) for x in default_times],
        # grace must be < min slot gap (14:50->14:55 = 5min); else prior slot delays next
        "grace_minutes": 4,
        "output_dir": str(default_output_dir(root)),
        "min_inflow_wan": 3500.0,
        "config_path": str(path),
    }
    if not path.is_file():
        return cfg
    try:
        # 保留大小写 section 名；ini 键默认大小写不敏感
        p = configparser.ConfigParser()
        p.read(str(path), encoding="utf-8")
        sec = "MaZongIntradayDump"
        if not p.has_section(sec):
            return cfg
        if p.has_option(sec, "enabled"):
            cfg["enabled"] = str(p.get(sec, "enabled")).strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
                "",
            )
        raw_times = ""
        if p.has_option(sec, "dump_times"):
            raw_times = str(p.get(sec, "dump_times") or "").strip()
        if not raw_times and p.has_option(sec, "dump_time"):
            # 旧配置：单个 dump_time
            raw_times = str(p.get(sec, "dump_time") or "").strip()
        if raw_times:
            cfg["dump_times"] = parse_dump_times(raw_times)
        cfg["dump_time"] = cfg["dump_times"][0]
        cfg["dump_time_obj"] = parse_dump_time(cfg["dump_time"])
        cfg["dump_time_objs"] = [parse_dump_time(x) for x in cfg["dump_times"]]
        if p.has_option(sec, "grace_minutes"):
            try:
                cfg["grace_minutes"] = max(0, int(p.get(sec, "grace_minutes")))
            except (TypeError, ValueError):
                pass
        if p.has_option(sec, "output_dir"):
            raw = str(p.get(sec, "output_dir") or "").strip()
            if raw:
                out = Path(raw)
                if not out.is_absolute():
                    out = root / out
                cfg["output_dir"] = str(out)
        if p.has_option(sec, "min_inflow_wan"):
            try:
                cfg["min_inflow_wan"] = float(p.get(sec, "min_inflow_wan"))
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    return cfg

def _split_fail_reasons(fail: str) -> Dict[str, str]:
    """把「不满足的原因」按分号拆到各软门槛。"""
    out: Dict[str, str] = {}
    text = str(fail or "").strip()
    if not text:
        return out
    parts = [x.strip() for x in text.split("；") if x.strip()]
    for part in parts:
        matched = False
        for prefix, _ok_key, label in SOFT_SPECS:
            if label == "前10日无大涨":
                if part.startswith("前") and "无大涨不满足" in part:
                    out[label] = part
                    matched = True
                    break
            elif part.startswith(prefix):
                out[label] = part
                matched = True
                break
        if not matched:
            out.setdefault("_other", "")
            out["_other"] = (out.get("_other") + "；" + part).lstrip("；")
    return out


def soft_value_text(label: str, extra: Dict[str, Any]) -> str:
    """软门槛「当时取值」摘要。"""
    e = extra or {}
    if label == "板块排名":
        from utils.ma_zong_logic2_scan import format_board_rank_line, format_qualifying_board_line

        qual = format_qualifying_board_line(e)
        board = format_board_rank_line(e)
        ind_n = e.get("条件_行业前N") or e.get("BOARD_TOP_N_INDUSTRY") or 32
        con_n = e.get("条件_概念前N") or e.get("BOARD_TOP_N_CONCEPT") or 8
        bits = ["门槛=行业前%s/概念前%s" % (ind_n, con_n)]
        if qual:
            bits.append("达标:%s" % qual)
        if board:
            bits.append("所属最高:%s" % board)
        hit = str(e.get("命中前8标签") or "").strip()
        if hit:
            bits.append("命中:%s" % hit)
        return " | ".join(bits)
    if label == "主力净流入":
        thr = e.get("MIN_INFLOW_WAN") or 3500
        val = e.get("主力净流入_万元")
        return "实际=%s万 门槛>=%s万" % (_s(val) if val != "" else "无", thr)
    if label == "前10日无大涨":
        mx = e.get("前十个交易日最高涨幅")
        ds = e.get("前十个交易日最高涨幅日期")
        thr = e.get("前10日大涨阈值")
        return "最高涨幅=%s%%(日=%s) 门槛<%s%%" % (_s(mx), _s(ds), _s(thr))
    if label == "价>MA5且MA20":
        return "价=%s MA5=%s MA20=%s" % (
            _s(e.get("当时最新价")),
            _s(e.get("MA5")),
            _s(e.get("MA20")),
        )
    if label == "当天未涨停":
        return "涨幅=%s%% 最高价=%s 涨停价=%s" % (
            _s(e.get("当时涨跌幅")),
            _s(e.get("今日最高价")),
            _s(e.get("涨停价")),
        )
    return ""


def flatten_hard_pass_row(
    row: Dict[str, Any],
    *,
    as_of: str = "",
    mode: str = "",
    fetched_at: str = "",
    dump_clock: str = "",
) -> Dict[str, Any]:
    """把 scan 的 hard_pass 行展成落盘宽表一行。"""
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    q = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    fail = str(extra.get("不满足的原因") or row.get("reason") or "").strip()
    fail_map = _split_fail_reasons(fail)

    out: Dict[str, Any] = {
        "选股日": as_of,
        "落盘时钟": dump_clock,
        "数据模式": mode or _s(extra.get("数据模式")),
        "数据抓取时间": fetched_at or _s(extra.get("数据抓取时间")),
        "股票代码": _s(row.get("code") or extra.get("股票代码")),
        "股票名称": _s(row.get("name") or extra.get("股票名称_行情")),
        "硬_当时涨跌幅%": row.get("pct") if row.get("pct") is not None else extra.get("当时涨跌幅"),
        "硬_涨幅门槛%": extra.get("涨幅门槛"),
        "硬_主力净流入_万元": row.get("inflow_wan")
        if row.get("inflow_wan") is not None
        else extra.get("主力净流入_万元"),
        "满足全部软门槛": _yes_no(row.get("meet") if "meet" in row else extra.get("满足条件")),
        "不满足的原因_汇总": fail,
        "软门槛摘要": row.get("conditions_text") or "",
        "达标板块": row.get("qualifying_board_text") or "",
        "所属最高板块": row.get("board_text") or "",
    }

    for _prefix, ok_key, label in SOFT_SPECS:
        ok = extra.get(ok_key)
        out["软_%s_值" % label] = soft_value_text(label, extra)
        out["软_%s_满足" % label] = _yes_no(ok)
        reason = ""
        if not ok:
            reason = fail_map.get(label) or ""
            if not reason:
                # 兜底：从汇总结取含 label 关键字的一段
                for k, v in fail_map.items():
                    if k == label:
                        reason = v
                        break
        out["软_%s_不满足原因" % label] = reason

    # 额外便于回测跟踪的字段
    out["流通市值_亿"] = extra.get("流通市值_亿") or q.get("float_mv_yi")
    out["当时最新价"] = extra.get("当时最新价") or q.get("price")
    out["MA5"] = extra.get("MA5")
    out["MA20"] = extra.get("MA20")
    out["前十个交易日最高涨幅"] = extra.get("前十个交易日最高涨幅")
    out["前十个交易日最高涨幅日期"] = extra.get("前十个交易日最高涨幅日期")
    out["所属行业最高排名名称"] = extra.get("所属行业最高排名名称")
    out["所属行业最高排名名次"] = extra.get("所属行业最高排名名次")
    out["所属概念最高排名名称"] = extra.get("所属概念最高排名名称")
    out["所属概念最高排名名次"] = extra.get("所属概念最高排名名次")
    return out


def flatten_scan_result(
    scan: Dict[str, Any],
    *,
    dump_clock: str = "",
) -> List[Dict[str, Any]]:
    rows = scan.get("hard_pass_all") or []
    as_of = _s(scan.get("as_of"))
    mode = _s(scan.get("mode"))
    fetched_at = _s(scan.get("fetched_at"))
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            flatten_hard_pass_row(
                row,
                as_of=as_of,
                mode=mode,
                fetched_at=fetched_at,
                dump_clock=dump_clock,
            )
        )
    # 满足的排前，再按代码
    out.sort(
        key=lambda r: (
            0 if r.get("满足全部软门槛") == "是" else 1,
            str(r.get("股票代码") or ""),
        )
    )
    return out


CSV_COLUMNS: Sequence[str] = (
    "选股日",
    "落盘时钟",
    "数据模式",
    "数据抓取时间",
    "股票代码",
    "股票名称",
    "硬_当时涨跌幅%",
    "硬_涨幅门槛%",
    "硬_主力净流入_万元",
    "满足全部软门槛",
    "不满足的原因_汇总",
    "软门槛摘要",
    "达标板块",
    "所属最高板块",
    "软_板块排名_值",
    "软_板块排名_满足",
    "软_板块排名_不满足原因",
    "软_主力净流入_值",
    "软_主力净流入_满足",
    "软_主力净流入_不满足原因",
    "软_前10日无大涨_值",
    "软_前10日无大涨_满足",
    "软_前10日无大涨_不满足原因",
    "软_价>MA5且MA20_值",
    "软_价>MA5且MA20_满足",
    "软_价>MA5且MA20_不满足原因",
    "软_当天未涨停_值",
    "软_当天未涨停_满足",
    "软_当天未涨停_不满足原因",
    "流通市值_亿",
    "当时最新价",
    "MA5",
    "MA20",
    "前十个交易日最高涨幅",
    "前十个交易日最高涨幅日期",
    "所属行业最高排名名称",
    "所属行业最高排名名次",
    "所属概念最高排名名称",
    "所属概念最高排名名次",
)


def output_stem(as_of: str, dump_clock: str) -> str:
    clock = str(dump_clock or "0000").replace(":", "")
    if len(clock) >= 4:
        clock = clock[:4]
    return "%s_%s" % (as_of, clock)


def write_candidate_files(
    rows: List[Dict[str, Any]],
    *,
    out_dir: Path,
    as_of: str,
    dump_clock: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(as_of, dump_clock)
    csv_path = out_dir / ("%s.csv" % stem)
    jsonl_path = out_dir / ("%s.jsonl" % stem)
    meta_path = out_dir / ("%s_meta.json" % stem)

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_COLUMNS})

    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    payload = dict(meta or {})
    payload.update(
        {
            "as_of": as_of,
            "dump_clock": dump_clock,
            "row_count": len(rows),
            "hard_pass": len(rows),
            "soft_pass": sum(1 for r in rows if r.get("满足全部软门槛") == "是"),
            "csv": str(csv_path),
            "jsonl": str(jsonl_path),
            "written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"csv": str(csv_path), "jsonl": str(jsonl_path), "meta": str(meta_path)}
