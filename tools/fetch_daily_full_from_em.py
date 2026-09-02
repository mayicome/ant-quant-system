# -*- coding: utf-8 -*-
"""从东财第三方接口拉取 A 股全历史日线，写入 data/daily_full/。

与 rolling 的 data/daily_cache/ 分离；补齐现有 daily_full 中偏短/过期文件。

用法：
  # 先扫一眼缺多少
  python tools/fetch_daily_full_from_em.py --scan-only

  # 试跑 20 只
  python tools/fetch_daily_full_from_em.py --limit 20

  # 全市场增量补齐（缺文件 / 末日偏旧 / 历史起点过晚）
  python tools/fetch_daily_full_from_em.py

  # 强制重拉
  python tools/fetch_daily_full_from_em.py --force --codes 000001.SZ,600000.SH
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.daily_cache_reader import to_full_stock_code  # noqa: E402

DAILY_FULL = ROOT / "data" / "daily_full"
DAILY_CACHE = ROOT / "data" / "daily_cache"
ALL_A_CSV = ROOT / "data" / "all_a_stocks.csv"
PROGRESS_PATH = DAILY_FULL / "fetch_em_progress.json"

# 东财 K 线（与 akshare stock_zh_a_hist 同源）
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
UT = "7eea3edcaed734bea9cbfc24409ed989"

OUT_COLS = ["date", "open", "high", "low", "close", "volume", "amount"]


def _session() -> requests.Session:
    os.environ.setdefault("NO_PROXY", "*")
    s = requests.Session()
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
    )
    return s


def _secid(full_code: str) -> str:
    code, mkt = full_code.split(".")
    # 东财：1=沪 0=深；北交所按深市通道
    if mkt == "SH":
        return f"1.{code}"
    return f"0.{code}"


def _adjust_flag(adjust: str) -> str:
    a = (adjust or "").strip().lower()
    if a in ("qfq", "前复权"):
        return "1"
    if a in ("hfq", "后复权"):
        return "2"
    return "0"


def fetch_hist_em(
    full_code: str,
    *,
    start: str = "19900101",
    end: str = "",
    adjust: str = "qfq",
    session: Optional[requests.Session] = None,
    timeout: float = 30.0,
) -> pd.DataFrame:
    """拉取单票日线，返回标准列 date/open/high/low/close/volume/amount。"""
    end = end or datetime.now().strftime("%Y%m%d")
    start = start.replace("-", "")[:8]
    end = end.replace("-", "")[:8]
    sess = session or _session()
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": UT,
        "klt": "101",
        "fqt": _adjust_flag(adjust),
        "secid": _secid(full_code),
        "beg": start,
        "end": end,
    }
    r = sess.get(KLINE_URL, params=params, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    data = (payload or {}).get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        return pd.DataFrame(columns=OUT_COLS)
    rows = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        rows.append(
            {
                "date": parts[0],
                "open": parts[1],
                "close": parts[2],
                "high": parts[3],
                "low": parts[4],
                "volume": parts[5],
                "amount": parts[6],
            }
        )
    df = pd.DataFrame(rows)
    for c in ("open", "high", "low", "close", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return df[OUT_COLS]


def list_universe_codes() -> List[str]:
    codes: set = set()
    if DAILY_CACHE.is_dir():
        for p in DAILY_CACHE.glob("*.csv"):
            name = p.stem.upper()
            if "." in name:
                codes.add(name)
    if ALL_A_CSV.is_file():
        try:
            raw = pd.read_csv(ALL_A_CSV, dtype=str)
            col = "证券代码" if "证券代码" in raw.columns else raw.columns[0]
            for v in raw[col].astype(str):
                full = to_full_stock_code(v.strip())
                if full and not full.startswith(("5",)):  # 跳过纯基金代码开头的可选
                    # 保留 60/00/30/68 等 A 股；ETF 5 开头可跳过全历史
                    code = full.split(".")[0]
                    if code.startswith(("5",)):
                        continue
                    codes.add(full)
        except Exception as e:
            print("[warn] 读取 all_a_stocks 失败:", e)
    if DAILY_FULL.is_dir():
        for p in DAILY_FULL.glob("*.csv"):
            if p.stem.upper().count(".") == 1:
                codes.add(p.stem.upper())
    # 只要沪深 A；去掉明显指数
    out = []
    for c in sorted(codes):
        num = c.split(".")[0]
        if num in ("000001",) and c.endswith(".SH"):
            continue  # 上证指数若误入
        if num.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689")):
            out.append(c)
        elif num.startswith(("8", "4", "920")):
            out.append(c)
    return out


def _peek_range(path: Path) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp], int]:
    if not path.is_file():
        return None, None, 0
    try:
        df = pd.read_csv(path, usecols=["date"])
        d = pd.to_datetime(df["date"], errors="coerce").dropna()
        if d.empty:
            return None, None, 0
        return d.min(), d.max(), int(len(d))
    except Exception:
        try:
            df = pd.read_csv(path)
            if "date" not in df.columns:
                return None, None, 0
            d = pd.to_datetime(df["date"], errors="coerce").dropna()
            if d.empty:
                return None, None, 0
            return d.min(), d.max(), int(len(d))
        except Exception:
            return None, None, 0


def needs_fetch(
    full_code: str,
    *,
    through: date,
    force: bool,
    max_start: Optional[date],
    stale_days: int,
) -> Tuple[bool, str]:
    path = DAILY_FULL / f"{full_code}.csv"
    if force or not path.is_file():
        return True, "missing" if not path.is_file() else "force"
    d0, d1, n = _peek_range(path)
    if d1 is None or d0 is None:
        return True, "empty"
    if d1.date() < through - timedelta(days=stale_days):
        return True, "stale_end"
    if max_start is not None and d0.date() > max_start:
        return True, "late_start"
    if n < 30:
        return True, "too_short"
    return False, "ok"


def merge_save(path: Path, new_df: pd.DataFrame) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            old = pd.read_csv(path)
            if "date" in old.columns and not old.empty:
                old["date"] = pd.to_datetime(old["date"], errors="coerce")
                old = old.dropna(subset=["date"])
                for c in ("open", "high", "low", "close", "volume", "amount"):
                    if c in old.columns:
                        old[c] = pd.to_numeric(old[c], errors="coerce")
                merged = pd.concat([old[OUT_COLS], new_df[OUT_COLS]], ignore_index=True)
            else:
                merged = new_df.copy()
        except Exception:
            merged = new_df.copy()
    else:
        merged = new_df.copy()
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce")
    merged = merged.dropna(subset=["date"]).sort_values("date")
    merged = merged.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    out = merged[OUT_COLS].copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return int(len(out))


def _load_progress() -> Dict[str, Any]:
    if not PROGRESS_PATH.is_file():
        return {"ok": [], "fail": {}}
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": [], "fail": {}}


def _save_progress(prog: Dict[str, Any]) -> None:
    DAILY_FULL.mkdir(parents=True, exist_ok=True)
    prog["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    PROGRESS_PATH.write_text(json.dumps(prog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process_one(
    full_code: str,
    *,
    start: str,
    end: str,
    adjust: str,
    sleep_s: float,
) -> Tuple[str, bool, str]:
    sess = _session()
    try:
        df = fetch_hist_em(full_code, start=start, end=end, adjust=adjust, session=sess)
        time.sleep(max(0.0, sleep_s))
        if df.empty:
            return full_code, False, "empty_response"
        n = merge_save(DAILY_FULL / f"{full_code}.csv", df)
        return full_code, True, f"rows={n}"
    except Exception as e:
        return full_code, False, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="东财拉取全历史日线 → data/daily_full")
    ap.add_argument("--start", default="19900101", help="开始 YYYYMMDD")
    ap.add_argument("--end", default="", help="结束 YYYYMMDD，默认今天")
    ap.add_argument("--adjust", default="qfq", choices=["qfq", "hfq", ""], help="复权：qfq/hfq/空=不复权")
    ap.add_argument("--codes", default="", help="逗号分隔代码，如 000001.SZ,600000.SH")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 只（调试）")
    ap.add_argument("--workers", type=int, default=1, help="并发（建议 1～3，防封）")
    ap.add_argument("--sleep", type=float, default=0.25, help="每只请求后休眠秒")
    ap.add_argument("--force", action="store_true", help="强制重拉")
    ap.add_argument(
        "--max-start",
        default="2020-01-01",
        help="若本地首根晚于该日则视为历史不全需补（空=不检查）",
    )
    ap.add_argument("--stale-days", type=int, default=5, help="末日早于 through 超过 N 日历日则补")
    ap.add_argument("--scan-only", action="store_true", help="只统计待补数量")
    ap.add_argument("--retries", type=int, default=3, help="失败重试次数")
    args = ap.parse_args()

    end_s = args.end or datetime.now().strftime("%Y%m%d")
    through = datetime.strptime(end_s[:8], "%Y%m%d").date()
    max_start = None
    if str(args.max_start or "").strip():
        max_start = datetime.strptime(str(args.max_start)[:10], "%Y-%m-%d").date()

    if args.codes.strip():
        codes = [to_full_stock_code(x.strip()) for x in args.codes.split(",") if x.strip()]
        codes = [c for c in codes if c]
    else:
        codes = list_universe_codes()

    todo: List[Tuple[str, str]] = []
    for c in codes:
        need, reason = needs_fetch(
            c, through=through, force=bool(args.force), max_start=max_start, stale_days=int(args.stale_days)
        )
        if need:
            todo.append((c, reason))

    print(
        "[scan] universe=%d need_fetch=%d through=%s adjust=%s out=%s"
        % (len(codes), len(todo), through, args.adjust or "none", DAILY_FULL)
    )
    from collections import Counter

    print("[scan] reasons", dict(Counter(r for _, r in todo)))
    if args.scan_only:
        return 0

    if args.limit and args.limit > 0:
        todo = todo[: int(args.limit)]
        print("[run] limit ->", len(todo))

    if not todo:
        print("[done] nothing to fetch")
        return 0

    prog = _load_progress()
    ok_list = list(prog.get("ok") or [])
    fail_map = dict(prog.get("fail") or {})
    workers = max(1, int(args.workers))
    done = 0
    ok_n = 0
    fail_n = 0

    def _run_with_retry(code: str) -> Tuple[str, bool, str]:
        last = ""
        for i in range(max(1, int(args.retries))):
            c, ok, msg = process_one(
                code,
                start=args.start,
                end=end_s,
                adjust=args.adjust,
                sleep_s=float(args.sleep),
            )
            if ok:
                return c, True, msg
            last = msg
            time.sleep(0.5 * (i + 1))
        return code, False, last

    if workers == 1:
        for code, reason in todo:
            c, ok, msg = _run_with_retry(code)
            done += 1
            if ok:
                ok_n += 1
                ok_list.append(c)
                fail_map.pop(c, None)
            else:
                fail_n += 1
                fail_map[c] = msg
            if done % 20 == 0 or done == len(todo):
                print("[%d/%d] ok=%d fail=%d last=%s %s" % (done, len(todo), ok_n, fail_n, c, msg))
                prog["ok"] = sorted(set(ok_list))
                prog["fail"] = fail_map
                _save_progress(prog)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_run_with_retry, code): code for code, _ in todo}
            for fut in as_completed(futs):
                c, ok, msg = fut.result()
                done += 1
                if ok:
                    ok_n += 1
                    ok_list.append(c)
                    fail_map.pop(c, None)
                else:
                    fail_n += 1
                    fail_map[c] = msg
                if done % 20 == 0 or done == len(todo):
                    print("[%d/%d] ok=%d fail=%d last=%s %s" % (done, len(todo), ok_n, fail_n, c, msg))
                    prog["ok"] = sorted(set(ok_list))
                    prog["fail"] = fail_map
                    _save_progress(prog)

    prog["ok"] = sorted(set(ok_list))
    prog["fail"] = fail_map
    _save_progress(prog)
    print("[done] ok=%d fail=%d progress=%s" % (ok_n, fail_n, PROGRESS_PATH))
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
