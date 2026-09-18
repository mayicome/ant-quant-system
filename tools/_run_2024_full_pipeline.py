# -*- coding: utf-8 -*-
"""2024 全年流水线：

1) 补齐板块成份股（clist）+ daily_full 等权合成 hist + 写 rank CSV
2) 马总 MA10 选股+回测（sell_half / hold8 / 无破MA20）
3) 布林%b 选股+回测

合成榜仅为排名近似（等权成份 + 当前成份存活者偏差）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "data" / "eastmoney_board_rank"
LOG = LOG_DIR / "_pipeline_2024.log"
PY = sys.executable
START = "2024-01-02"
END = "2024-12-31"
FORCE_DAYS = "242"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd: list[str], *, env: dict | None = None) -> int:
    log("RUN " + " ".join(str(x) for x in cmd))
    e = os.environ.copy()
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUNBUFFERED"] = "1"
    if env:
        e.update(env)
    p = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=e,
    )
    assert p.stdout is not None
    for line in p.stdout:
        s = line.rstrip()
        if s:
            print(s, flush=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(s + "\n")
    rc = p.wait()
    log(f"EXIT {rc}")
    return rc


def verify_board_ranks() -> tuple[int, int, int]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from utils.trading_day import get_trading_dates_in_range_sorted

    d = ROOT / "data" / "eastmoney_board_rank"
    tds = list(get_trading_dates_in_range_sorted(date(2024, 1, 1), date(2024, 12, 31)) or [])
    ind = {p.name[len("industry_rank_") : -4] for p in d.glob("industry_rank_2024-*.csv")}
    con = {p.name[len("concept_rank_") : -4] for p in d.glob("concept_rank_2024-*.csv")}
    miss_i = [x.isoformat() for x in tds if x.isoformat() not in ind]
    miss_c = [x.isoformat() for x in tds if x.isoformat() not in con]
    log(
        f"verify board ranks: trading={len(tds)} "
        f"industry={len(ind)} miss_i={len(miss_i)} "
        f"concept={len(con)} miss_c={len(miss_c)}"
    )
    if miss_i[:5]:
        log(f"miss industry sample: {miss_i[:5]}")
    if miss_c[:5]:
        log(f"miss concept sample: {miss_c[:5]}")
    return len(tds), len(miss_i), len(miss_c)


def main() -> int:
    log("==== pipeline 2024 (cons-synth) start ====")
    rc = run(
        [
            PY,
            "-u",
            "-X",
            "utf8",
            str(ROOT / "tools" / "_fill_missing_via_cons_synth.py"),
            "--start",
            START,
            "--end",
            END,
            "--kind",
            "both",
        ]
    )
    if rc != 0:
        log("cons-synth/build failed, abort")
        return rc

    n, mi, mc = verify_board_ranks()
    if n <= 0 or mi > 5 or mc > 5:
        log("board rank coverage incomplete, abort prepares")
        return 2
    if mi or mc:
        log(f"WARN: small gaps miss_i={mi} miss_c={mc}, continue")

    env_bt = {"BACKTEST_FILL_ADJUST": "qfq"}
    rc = run(
        [
            PY,
            "-u",
            "-X",
            "utf8",
            str(ROOT / "tools" / "prepare_ma10_regime_data.py"),
            "--force-days",
            FORCE_DAYS,
            "--end",
            END,
            "--no-reuse",
            "--sell-hold",
            "8",
        ],
        env=env_bt,
    )
    if rc != 0:
        log("ma10 prepare failed")
        return rc

    rc = run(
        [
            PY,
            "-u",
            "-X",
            "utf8",
            str(ROOT / "tools" / "prepare_bb_pctb_pullback_data.py"),
            "--force-days",
            FORCE_DAYS,
            "--end",
            END,
            "--no-reuse",
        ],
        env=env_bt,
    )
    if rc != 0:
        log("bb_pctb prepare failed")
        return rc

    log("==== pipeline 2024 done OK ====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
