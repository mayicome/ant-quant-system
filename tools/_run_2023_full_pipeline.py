# -*- coding: utf-8 -*-
"""2023 全年流水线：板块排名(cons-synth) → 马总 MA10 选股+回测 → 归档到 2023/"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "data" / "eastmoney_board_rank"
LOG = LOG_DIR / "_pipeline_2023.log"
PY = sys.executable
START = "2023-01-03"
END = "2023-12-29"
FORCE_DAYS = "242"
ARCHIVE = ROOT / "history_data" / "马总选股逻辑" / "2023"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_calendar() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from utils.trading_day import (
        _build_trade_date_cache,
        get_trading_dates_in_range_sorted,
        invalidate_trading_day_cache,
    )

    invalidate_trading_day_cache()
    ok = _build_trade_date_cache(date(2023, 1, 1), date(2023, 12, 31), date.today())
    tds = list(get_trading_dates_in_range_sorted(date(2023, 1, 1), date(2023, 12, 31)) or [])
    log(f"calendar ok={ok} trading_days={len(tds)} first={tds[0] if tds else None} last={tds[-1] if tds else None}")
    if len(tds) < 230:
        raise RuntimeError(f"2023 trading calendar incomplete: {len(tds)}")


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
    tds = list(get_trading_dates_in_range_sorted(date(2023, 1, 1), date(2023, 12, 31)) or [])
    ind = {p.name[len("industry_rank_") : -4] for p in d.glob("industry_rank_2023-*.csv")}
    con = {p.name[len("concept_rank_") : -4] for p in d.glob("concept_rank_2023-*.csv")}
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


def archive_ma10_outputs() -> None:
    """把根目录里 2023 窗口产物挪到 2023/，避免冲掉最终/2024 latest。"""
    src = ROOT / "history_data" / "马总选股逻辑"
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    moved = 0
    for p in list(src.glob("*")):
        if not p.is_file():
            continue
        name = p.name
        # 选股结果含 2023；回测 latest 刚生成也归档
        keep = (
            ("2023-01" in name or "2023-12" in name or "_2023" in name)
            or (
                name.startswith("各日选股收益汇总_日线-ma10-sell_half")
                and ("latest" in name or datetime.now().strftime("%Y%m%d") in name)
            )
            or (
                name.startswith("回测成交明细_日线-ma10-sell_half")
                and ("latest" in name or datetime.now().strftime("%Y%m%d") in name)
            )
            or (name.startswith("选股结果_马总选股逻辑-次日MA10_") and "2023" in name)
        )
        if not keep:
            continue
        dest = ARCHIVE / name
        if dest.exists():
            dest.unlink()
        shutil.move(str(p), str(dest))
        moved += 1
        log(f"archive {name} -> 2023/")
    log(f"archived files={moved} -> {ARCHIVE}")


def main() -> int:
    log("==== pipeline 2023 (cons-synth + ma10) start ====")
    ensure_calendar()

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
            "--skip-fetch-cons",  # 2024 已拉过成份
        ]
    )
    if rc != 0:
        log("cons-synth/build failed, abort")
        return rc

    n, mi, mc = verify_board_ranks()
    if n <= 0 or mi > 5 or mc > 5:
        log("board rank coverage incomplete, abort prepare")
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

    archive_ma10_outputs()
    log("==== pipeline 2023 done OK ====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
