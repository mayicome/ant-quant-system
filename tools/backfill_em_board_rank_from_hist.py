# -*- coding: utf-8 -*-
"""从东财板块日 K 重建历史涨跌幅全榜（替代 LU-proxy）。

背景
----
东财 push2 clist 只能拿到「当前交易日」点位，无法直接回填历史热榜。
本脚本对**当前**概念/行业板块列表逐个拉取日 K（push2his），再按交易日用
「当日涨跌幅」降序重建全榜 CSV，列与 ``snapshot_eastmoney_board_rank.py`` 对齐。

重要说明（请读）
----------------
1. **重建的是 EOD 涨跌幅排名**，近似东财日终涨跌幅榜，**不等于**盘中热榜/人气榜。
2. 板块列表取自**今天**的 name_em / push2 clist（或最近一次真实快照 CSV 回退）：
   存在存活者偏差 / survivorship——历史退市/更名板块可能缺失。
3. hist 能提供：最新价(收盘)、涨跌额、涨跌幅、换手率；总市值/涨跌家数/领涨股等列留空。
4. 默认只写 ``--start`` .. ``--end``；区间外文件（如 ``2026-08-03`` 真实快照）不触及。

网络说明
--------
部分环境对 ``*.push2his.eastmoney.com`` 直连会 RST；可用：
  - 本脚本显式走 ``EM_HIST_PROXY``（默认 ``http://127.0.0.1:7078``），并清除 ``NO_PROXY=*``
  - 浏览器 JSONP（``tools/em_board_hist_browser_server.py`` 或 CDP）拉数后 ``--ingest-json``
  - 然后 ``--build-only`` 只从缓存出 CSV

缓存与断点续跑
--------------
``data/eastmoney_board_rank/_hist_cache/{kind}_{BK}.parquet``（失败时同名 .csv / .json）
写入时按日期合并，短窗口回填不会冲掉已有区间。

用法::

  python tools/backfill_em_board_rank_from_hist.py
  python tools/backfill_em_board_rank_from_hist.py --start 2026-06-09 --end 2026-07-31
  python tools/backfill_em_board_rank_from_hist.py --build-only
  python tools/backfill_em_board_rank_from_hist.py --ingest-json path/to/batch.json --kind industry
  python tools/backfill_em_board_rank_from_hist.py --export-pending
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.snapshot_eastmoney_board_rank import (  # noqa: E402
    _BOARD_OUT_COLS,
    _clear_proxy_env,
    fetch_board_rank_direct,
)

OUT_DIR = os.path.join(ROOT, "data", "eastmoney_board_rank")
CACHE_DIR = os.path.join(OUT_DIR, "_hist_cache")

DEFAULT_START = "2026-06-09"
DEFAULT_END = "2026-07-31"

_HIST_HOSTS = (
    "https://91.push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://92.push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://79.push2his.eastmoney.com/api/qt/stock/kline/get",
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/center/boardlist.html",
    "Accept": "*/*",
}

_KLINE_COLS = [
    "日期",
    "开盘",
    "收盘",
    "最高",
    "最低",
    "成交量",
    "成交额",
    "振幅",
    "涨跌幅",
    "涨跌额",
    "换手率",
]


def _parse_ymd(raw: str) -> date:
    s = str(raw or "").strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return datetime.strptime(s, "%Y-%m-%d").date()


def _ymd(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _ymd_compact(d: date) -> str:
    return d.strftime("%Y%m%d")


def _cache_path(kind: str, board_code: str, ext: str = "parquet") -> str:
    code = str(board_code or "").strip().upper()
    return os.path.join(CACHE_DIR, f"{kind}_{code}.{ext}")


def _klines_to_df(klines: Sequence[Any]) -> pd.DataFrame:
    if not klines:
        return pd.DataFrame(columns=_KLINE_COLS)
    rows = [str(x).split(",") for x in klines]
    df = pd.DataFrame(rows)
    if df.shape[1] < len(_KLINE_COLS):
        for i in range(df.shape[1], len(_KLINE_COLS)):
            df[i] = ""
    df = df.iloc[:, : len(_KLINE_COLS)].copy()
    df.columns = _KLINE_COLS
    for c in ("开盘", "收盘", "最高", "最低", "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["日期"] = df["日期"].astype(str).str.strip()
    return df


def _merge_hist_dfs(old: Optional[pd.DataFrame], new: pd.DataFrame) -> pd.DataFrame:
    """按日期合并 hist，新行覆盖同日旧行；避免短窗口回填冲掉已有区间。"""
    if new is None or new.empty:
        return old if old is not None else pd.DataFrame(columns=_KLINE_COLS)
    if old is None or old.empty:
        out = new.copy()
    else:
        out = pd.concat([old, new], ignore_index=True)
    out["日期"] = out["日期"].astype(str).str.strip().str[:10]
    out = out[out["日期"].str.len() >= 10]
    out = out.drop_duplicates(subset=["日期"], keep="last")
    out = out.sort_values("日期", kind="mergesort").reset_index(drop=True)
    return out


def _save_hist_df(df: pd.DataFrame, kind: str, board_code: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    merged = _merge_hist_dfs(_load_hist_cache(kind, board_code), df)
    path = _cache_path(kind, board_code, "parquet")
    try:
        merged.to_parquet(path, index=False)
        return path
    except Exception:
        path = _cache_path(kind, board_code, "csv")
        merged.to_csv(path, index=False, encoding="utf-8-sig")
        return path


def _load_hist_cache(kind: str, board_code: str) -> Optional[pd.DataFrame]:
    for ext in ("parquet", "csv", "json"):
        path = _cache_path(kind, board_code, ext)
        if not os.path.isfile(path):
            continue
        try:
            if ext == "parquet":
                df = pd.read_parquet(path)
            elif ext == "csv":
                df = pd.read_csv(path)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                df = _klines_to_df(payload.get("klines") or [])
            if isinstance(df, pd.DataFrame) and not df.empty and "日期" in df.columns:
                return df
        except Exception:
            continue
    return None


def _list_boards_from_snapshot(kind: str) -> Optional[pd.DataFrame]:
    """从最近真实快照 CSV 取板块列表（clist 不可用时回退）。"""
    prefix = "concept_rank_" if kind == "concept" else "industry_rank_"
    if not os.path.isdir(OUT_DIR):
        return None
    cands = []
    for name in os.listdir(OUT_DIR):
        if name.startswith(prefix) and name.endswith(".csv"):
            # 优先非回填区间的真实快照
            cands.append(name)
    if not cands:
        return None
    cands.sort(reverse=True)
    # 优先 08-03 及之后
    preferred = [n for n in cands if n >= f"{prefix}2026-08-01.csv"]
    pick = preferred[0] if preferred else cands[0]
    path = os.path.join(OUT_DIR, pick)
    df = pd.read_csv(path, dtype=str)
    if "板块名称" not in df.columns or "板块代码" not in df.columns:
        return None
    out = df[["板块名称", "板块代码"]].copy()
    out["板块名称"] = out["板块名称"].astype(str).str.strip()
    out["板块代码"] = out["板块代码"].astype(str).str.strip().str.upper()
    out = out[(out["板块名称"] != "") & (out["板块代码"].str.startswith("BK"))]
    out = out.drop_duplicates(subset=["板块代码"], keep="first").reset_index(drop=True)
    print(f"[info] board list from snapshot: {pick} rows={len(out)}")
    return out


def _list_boards(kind: str) -> pd.DataFrame:
    """当前概念/行业全榜 → 名称+代码。"""
    try:
        df = fetch_board_rank_direct(kind)
        out = df[["板块名称", "板块代码"]].copy()
        out["板块名称"] = out["板块名称"].astype(str).str.strip()
        out["板块代码"] = out["板块代码"].astype(str).str.strip().str.upper()
        out = out[(out["板块名称"] != "") & (out["板块代码"].str.startswith("BK"))]
        out = out.drop_duplicates(subset=["板块代码"], keep="first").reset_index(drop=True)
        print(f"[info] board list via push2 clist: kind={kind} rows={len(out)}")
        return out
    except Exception as e:
        print(f"[warn] clist failed ({type(e).__name__}: {e}), fallback to snapshot CSV")
        snap = _list_boards_from_snapshot(kind)
        if snap is None or snap.empty:
            raise RuntimeError(f"无法获取 {kind} 板块列表") from e
        return snap


def _fetch_board_hist_urllib(
    board_code: str,
    beg: str,
    end: str,
    *,
    retries: int = 4,
    timeout: int = 30,
) -> pd.DataFrame:
    code = str(board_code or "").strip().upper()
    if not code.startswith("BK"):
        return pd.DataFrame(columns=_KLINE_COLS)
    # _clear_proxy_env 会设 NO_PROXY=*，导致显式 ProxyHandler 也被绕过
    os.environ.pop("NO_PROXY", None)
    os.environ.pop("no_proxy", None)
    params = {
        "secid": f"90.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": beg,
        "end": end,
        "smplmt": "10000",
        "lmt": "1000000",
    }
    qs = urllib.parse.urlencode(params)
    last_err: Optional[BaseException] = None
    # push2his 直连常 RST；本机 7078 代理可用。_clear_proxy_env 后系统 opener 无效，须显式挂代理。
    local_proxy = os.environ.get("EM_HIST_PROXY", "http://127.0.0.1:7078").strip()
    openers = [
        urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": local_proxy, "https": local_proxy})
        ),
        urllib.request.build_opener(),  # 系统/环境代理
        urllib.request.build_opener(urllib.request.ProxyHandler({})),  # 直连
    ]
    for attempt in range(max(1, retries)):
        base = _HIST_HOSTS[attempt % len(_HIST_HOSTS)]
        url = f"{base}?{qs}"
        opener = openers[attempt % len(openers)]
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                return pd.DataFrame(columns=_KLINE_COLS)
            return _klines_to_df(data.get("klines") or [])
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1) + random.random() * 0.3)
    raise RuntimeError(f"urllib hist failed for {code}: {last_err}")


def _fetch_board_hist_curl_cffi(
    board_code: str,
    beg: str,
    end: str,
    *,
    retries: int = 3,
    timeout: int = 30,
) -> pd.DataFrame:
    try:
        from curl_cffi import requests as creq
    except Exception as e:
        raise RuntimeError(f"curl_cffi unavailable: {e}") from e
    code = str(board_code or "").strip().upper()
    params = {
        "secid": f"90.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": beg,
        "end": end,
        "smplmt": "10000",
        "lmt": "1000000",
    }
    last_err: Optional[BaseException] = None
    local_proxy = os.environ.get("EM_HIST_PROXY", "http://127.0.0.1:7078").strip()
    proxy_modes = (
        {"http": local_proxy, "https": local_proxy},
        {"http": None, "https": None},
    )
    for attempt in range(max(1, retries)):
        base = _HIST_HOSTS[attempt % len(_HIST_HOSTS)]
        proxies = proxy_modes[attempt % len(proxy_modes)]
        try:
            r = creq.get(
                base,
                params=params,
                headers=_HEADERS,
                impersonate="chrome120",
                timeout=timeout,
                proxies=proxies,
            )
            payload = r.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                return pd.DataFrame(columns=_KLINE_COLS)
            return _klines_to_df(data.get("klines") or [])
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"curl_cffi hist failed for {code}: {last_err}")


def _fetch_board_hist(board_code: str, beg: str, end: str) -> pd.DataFrame:
    """优先 urllib，失败再试 curl_cffi。"""
    try:
        return _fetch_board_hist_urllib(board_code, beg, end)
    except Exception as e1:
        try:
            return _fetch_board_hist_curl_cffi(board_code, beg, end)
        except Exception as e2:
            raise RuntimeError(f"hist failed for {board_code}: urllib={e1}; curl_cffi={e2}") from e2


def _cache_covers(df: pd.DataFrame, fetch_beg: date, fetch_end: date) -> bool:
    if df is None or df.empty or "日期" not in df.columns:
        return False
    dmin = str(df["日期"].min())[:10]
    dmax = str(df["日期"].max())[:10]
    # 目标区间内至少有交易日数据即可（缓冲日可不齐）
    # 用较松条件：缓存最大日 >= end-缓冲、最小日 <= start+缓冲 在调用侧用 target 判断
    return dmin <= _ymd(fetch_beg + timedelta(days=10)) and dmax >= _ymd(fetch_end - timedelta(days=10))


def _load_or_fetch_hist(
    kind: str,
    board_code: str,
    fetch_beg: date,
    fetch_end: date,
    target_start: date,
    target_end: date,
    *,
    force: bool,
    pause_s: float,
    allow_network: bool,
) -> pd.DataFrame:
    cached = None if force else _load_hist_cache(kind, board_code)
    if cached is not None and not force:
        dmin = str(cached["日期"].min())[:10]
        dmax = str(cached["日期"].max())[:10]
        if dmin <= _ymd(target_start) and dmax >= _ymd(target_end):
            return cached
        # build-only：部分覆盖也先用；缺日在建榜时自然跳过
        if not allow_network and dmin <= _ymd(target_end) and dmax >= _ymd(target_start):
            return cached

    if not allow_network:
        return cached if cached is not None else pd.DataFrame(columns=_KLINE_COLS)

    df = _fetch_board_hist(
        board_code,
        _ymd_compact(fetch_beg),
        _ymd_compact(fetch_end),
    )
    if not df.empty:
        _save_hist_df(df, kind, board_code)
        cached = _load_hist_cache(kind, board_code)
    if pause_s > 0:
        time.sleep(pause_s + random.random() * min(0.15, pause_s))
    return cached if cached is not None else df


def ingest_json_batch(path: str, kind: str) -> int:
    """
    摄入浏览器 JSONP 批次。
    支持格式::
      {"kind":"industry","items":[{"code":"BK1318","klines":["2026-06-01,...", ...]}, ...]}
      或 [{"code":"BK1318","klines":[...]}, ...]
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        kind = str(payload.get("kind") or kind)
        items = payload.get("items") or payload.get("results") or []
    else:
        items = payload
    n = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("板块代码") or "").strip().upper()
        if not code.startswith("BK"):
            continue
        if item.get("ok") is False:
            continue
        klines = item.get("klines") or []
        df = _klines_to_df(klines)
        if df.empty:
            continue
        _save_hist_df(df, kind, code)
        # 也写一份 json 便于排查
        jpath = _cache_path(kind, code, "json")
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump({"code": code, "klines": list(klines)}, f, ensure_ascii=False)
        n += 1
    print(f"[ok] ingested {n} boards into cache from {path} (kind={kind})")
    return n


def export_pending(
    kind: str,
    start: date,
    end: date,
    *,
    limit: int = 0,
) -> str:
    boards = _list_boards(kind)
    pending: List[Dict[str, str]] = []
    for _, row in boards.iterrows():
        code = str(row["板块代码"])
        name = str(row["板块名称"])
        cached = _load_hist_cache(kind, code)
        ok = False
        if cached is not None and not cached.empty:
            dmin = str(cached["日期"].min())[:10]
            dmax = str(cached["日期"].max())[:10]
            if dmin <= _ymd(start) and dmax >= _ymd(end):
                ok = True
        if not ok:
            pending.append({"板块代码": code, "板块名称": name})
        if limit and len(pending) >= limit:
            break
    os.makedirs(CACHE_DIR, exist_ok=True)
    out_path = os.path.join(CACHE_DIR, f"_pending_{kind}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "kind": kind,
                "start": _ymd(start),
                "end": _ymd(end),
                "beg": _ymd_compact(start - timedelta(days=5)),
                "end_compact": _ymd_compact(end + timedelta(days=5)),
                "pending": pending,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"[ok] pending {kind}: {len(pending)} / {len(boards)} -> {out_path}")
    return out_path


def _hist_row_to_rank_fields(name: str, code: str, row: pd.Series) -> Dict[str, Any]:
    return {
        "板块名称": name,
        "板块代码": code,
        "最新价": row.get("收盘"),
        "涨跌额": row.get("涨跌额"),
        "涨跌幅": row.get("涨跌幅"),
        "总市值": pd.NA,
        "换手率": row.get("换手率"),
        "上涨家数": pd.NA,
        "下跌家数": pd.NA,
        "领涨股票": "",
        "领涨股票-涨跌幅": pd.NA,
    }


def _build_day_rank(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_BOARD_OUT_COLS)
    df = pd.DataFrame(rows)
    df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
    df = df.dropna(subset=["涨跌幅", "板块名称"]).copy()
    df = df.drop_duplicates(subset=["板块代码"], keep="first")
    df = df.sort_values("涨跌幅", ascending=False, kind="mergesort").reset_index(drop=True)
    df.insert(0, "排名", range(1, len(df) + 1))
    return df[_BOARD_OUT_COLS]


def _save_csv(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    try:
        os.replace(tmp, path)
    except PermissionError:
        # 文件被占用时写旁路名，仍尽量覆盖
        alt = path + ".new.csv"
        os.replace(tmp, alt)
        print(f"[warn] target locked, wrote {alt}; please close lock and rename")
        raise


def backfill_kind(
    kind: str,
    start: date,
    end: date,
    *,
    buffer_days: int = 5,
    pause_s: float = 0.2,
    force_fetch: bool = False,
    write_csv: bool = True,
    allow_network: bool = True,
) -> Tuple[int, List[str]]:
    assert kind in ("concept", "industry")
    fetch_beg = start - timedelta(days=max(0, buffer_days))
    fetch_end = end + timedelta(days=max(0, buffer_days))

    print(f"[info] === {kind} ===")
    boards = _list_boards(kind)
    print(f"[info] {kind}: boards={len(boards)} fetch_window={_ymd(fetch_beg)}..{_ymd(fetch_end)}")

    by_day: Dict[str, List[Dict[str, Any]]] = {}
    ok = 0
    empty = 0
    fail = 0
    t0 = time.time()

    for i, row in boards.iterrows():
        name = str(row["板块名称"])
        code = str(row["板块代码"])
        n = int(i) + 1
        try:
            hist = _load_or_fetch_hist(
                kind,
                code,
                fetch_beg,
                fetch_end,
                start,
                end,
                force=force_fetch,
                pause_s=pause_s,
                allow_network=allow_network,
            )
        except Exception as e:
            fail += 1
            if fail <= 10 or fail % 50 == 0:
                print(f"[warn] {kind} {n}/{len(boards)} {code} {name}: {e}")
            continue

        if hist is None or hist.empty:
            empty += 1
        else:
            ok += 1
            for _, hrow in hist.iterrows():
                d = str(hrow.get("日期") or "").strip()[:10]
                if len(d) < 10:
                    continue
                if d < _ymd(start) or d > _ymd(end):
                    continue
                by_day.setdefault(d, []).append(_hist_row_to_rank_fields(name, code, hrow))

        if n % 50 == 0 or n == len(boards):
            elapsed = time.time() - t0
            print(
                f"[progress] {kind} {n}/{len(boards)} ok={ok} empty={empty} fail={fail} "
                f"days_seen={len(by_day)} elapsed={elapsed:.0f}s"
            )

    written_dates: List[str] = []
    if write_csv:
        for d in sorted(by_day.keys()):
            rank_df = _build_day_rank(by_day[d])
            path = os.path.join(OUT_DIR, f"{kind}_rank_{d}.csv")
            try:
                _save_csv(rank_df, path)
            except PermissionError:
                # 尽量继续其它日期
                continue
            written_dates.append(d)
            top = ", ".join(str(x) for x in rank_df["板块名称"].head(3).tolist())
            print(f"[ok] {kind}_rank_{d}.csv rows={len(rank_df)} top3={top}")

    print(
        f"[done] {kind}: hist_ok={ok} empty={empty} fail={fail} "
        f"csv_days={len(written_dates)}"
    )
    return len(written_dates), written_dates


def main() -> None:
    parser = argparse.ArgumentParser(
        description="用东财板块日 K 重建历史涨跌幅全榜（替代 LU-proxy）",
    )
    parser.add_argument("--start", default=DEFAULT_START, help="起始日 YYYY-MM-DD")
    parser.add_argument("--end", default=DEFAULT_END, help="结束日 YYYY-MM-DD")
    parser.add_argument(
        "--kind",
        default="both",
        choices=("both", "concept", "industry"),
        help="回填概念 / 行业 / 两者",
    )
    parser.add_argument("--buffer-days", type=int, default=5)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="不访问网络，仅用 _hist_cache 生成 rank CSV",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="只拉/补齐 hist 缓存，不写 rank CSV",
    )
    parser.add_argument(
        "--ingest-json",
        default="",
        help="摄入浏览器 JSONP 批次 JSON，写入 _hist_cache",
    )
    parser.add_argument(
        "--export-pending",
        action="store_true",
        help="导出尚未缓存的板块列表到 _hist_cache/_pending_{kind}.json",
    )
    args = parser.parse_args()

    start = _parse_ymd(args.start)
    end = _parse_ymd(args.end)
    if end < start:
        raise SystemExit("--end 不能早于 --start")

    _clear_proxy_env()
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    kinds: Sequence[str]
    if args.kind == "both":
        kinds = ("concept", "industry")
    else:
        kinds = (args.kind,)

    if args.ingest_json:
        # 文件内可自带 kind；否则用 --kind（both 时要求文件含 kind）
        with open(args.ingest_json, "r", encoding="utf-8") as f:
            peek = json.load(f)
        file_kind = ""
        if isinstance(peek, dict):
            file_kind = str(peek.get("kind") or "").strip()
        use_kind = file_kind or (args.kind if args.kind != "both" else "")
        if not use_kind:
            raise SystemExit("--ingest-json 文件未含 kind，请同时指定 --kind concept|industry")
        ingest_json_batch(args.ingest_json, use_kind)
        return

    if args.export_pending:
        for kind in kinds:
            export_pending(kind, start, end)
        return

    print(f"[info] out_dir={OUT_DIR}")
    print(f"[info] cache_dir={CACHE_DIR}")
    print(f"[info] target={_ymd(start)} .. {_ymd(end)}")
    print(
        "[info] NOTE: EOD 涨跌幅重建榜 ≈ 东财日终涨跌幅；≠ 盘中热榜；"
        "板块列表为今日存活者（survivorship）"
    )
    print(f"[info] started_at={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    summary: Dict[str, Any] = {}
    for kind in kinds:
        n_days, dates = backfill_kind(
            kind,
            start,
            end,
            buffer_days=int(args.buffer_days),
            pause_s=float(args.pause),
            force_fetch=bool(args.force_fetch),
            write_csv=not bool(args.cache_only),
            allow_network=not bool(args.build_only),
        )
        summary[kind] = {"days": n_days, "dates": dates}

    print(f"[done] all kinds finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for kind, info in summary.items():
        print(f"       {kind}: {info['days']} days")


if __name__ == "__main__":
    main()
