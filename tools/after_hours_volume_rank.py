#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全 A 尾盘量拆分与排名（交易日 15:30 后跑）

从 QMT tick 拆出：
  - day_vol / day_amount：全日（收盘累计 + 盘后会话）
  - close_auc_vol / close_auc_amount：收盘集合竞价 14:57–15:00
  - after_vol / after_amount：盘后固定价格 15:05–15:30

**不落盘项目内 tick**：默认「下载一批 → 内存计算 → 删除 QMT 本地该日 tick 文件」，
只保留排名 CSV。QMT 临时文件在 userdata_mini/datadir/{SH|SZ}/0/{代码}/{日}.dat。

输出：
  data/after_hours_rank/{YYYYMMDD}/detail.csv   （中文表头）
  data/after_hours_rank/{YYYYMMDD}/top10.csv    （三榜 TopN 并集，中文表头）

口径：
  - 盘后占全天 = after_vol / day_vol
  - 盘后相对竞价 = after_vol / close_auc_vol
  - 盘后占流通 = 盘后量(股) / 流通股本(股)；tick 量多为「手」，按额/价自动判断后换算

用法：
  python tools/after_hours_volume_rank.py
  python tools/after_hours_volume_rank.py --day 20260714 --limit 200
  python tools/after_hours_volume_rank.py --keep-qmt-tick   # 调试：算完不删 QMT tick
  python tools/after_hours_volume_rank.py --skip-download   # 仅用已有本地 tick（仍可 --purge）

全市场约 5200 只，通常 2～3 小时；峰值磁盘仅约一批 tick，不算长期堆积。

生产路径：大 QMT 内置策略会在交易日 15:40 自动跑
  qmt_builtin/src/ant_after_hours_rank_runner.py
本脚本仅作调试 / Mini 环境备用。
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _china_now() -> datetime:
    return datetime.now()


def _project_out_dir(day: str) -> str:
    path = os.path.join(ROOT, "data", "after_hours_rank", day)
    os.makedirs(path, exist_ok=True)
    return path


def _load_universe(limit: int = 0) -> List[str]:
    import xtquant.xtdata as xtdata

    codes: List[str] = []
    for sector in ("沪深A股", "上证A股", "深证A股"):
        try:
            part = xtdata.get_stock_list_in_sector(sector) or []
            codes.extend([str(c).strip() for c in part if c])
        except Exception:
            continue
    # 去重保序
    seen = set()
    out: List[str] = []
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    if not out:
        uni_path = os.path.join(ROOT, "data", "a_share_universe.json")
        if os.path.isfile(uni_path):
            import json

            with open(uni_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                out = [str(c).strip() for c in data if c]
            elif isinstance(data, dict):
                out = [str(c).strip() for c in (data.get("codes") or data.get("stocks") or []) if c]
    if limit and limit > 0:
        out = out[:limit]
    return out


def _stock_name(code: str) -> str:
    name, _fs = _instrument_meta(code)
    return name


def _instrument_meta(code: str) -> Tuple[str, float]:
    """返回 (名称, 流通股本股数)。"""
    name = ""
    float_shares = 0.0
    try:
        from utils.stock_info_manager import StockInfoManager

        mgr = StockInfoManager()
        info = mgr.get_stock_info(code) if hasattr(mgr, "get_stock_info") else None
        if isinstance(info, dict):
            if info.get("name"):
                name = str(info["name"])
            for key in ("FloatVolume", "float_volume", "流通股本"):
                if info.get(key) not in (None, ""):
                    try:
                        float_shares = float(info[key])
                        break
                    except Exception:
                        pass
        if not name:
            for attr in ("get_name", "get_stock_name"):
                fn = getattr(mgr, attr, None)
                if callable(fn):
                    n = fn(code)
                    if n:
                        name = str(n)
                        break
    except Exception:
        pass
    try:
        import xtquant.xtdata as xtdata

        det = xtdata.get_instrument_detail(code) or {}
        if isinstance(det, dict):
            if not name:
                name = str(det.get("InstrumentName") or det.get("InstrumentNameCN") or "")
            if float_shares <= 0:
                fv = det.get("FloatVolume")
                if fv is None:
                    fv = det.get("TotalVolume")
                try:
                    float_shares = float(fv or 0)
                except Exception:
                    float_shares = 0.0
    except Exception:
        pass
    if float_shares < 0:
        float_shares = 0.0
    return name, float_shares


def _after_vol_to_shares(
    after_vol: float, after_amount: float = 0.0, last_price: float = 0.0
) -> float:
    """盘后量统一为股；QMT tick volume 多为手。"""
    if after_vol is None or after_vol <= 0:
        return 0.0
    vol = float(after_vol)
    try:
        amt = float(after_amount or 0)
        px = float(last_price or 0)
    except Exception:
        amt, px = 0.0, 0.0
    if amt > 0 and px > 0 and vol > 0:
        ratio = (amt / vol) / px
        if ratio >= 10.0:
            return vol * 100.0
        return vol
    return vol * 100.0


def _to_bj_time(series: pd.Series) -> pd.Series:
    """QMT tick time 一般为毫秒 UTC，+8 为北京时间。"""
    t = pd.to_datetime(series, unit="ms", errors="coerce")
    return t + pd.Timedelta(hours=8)


def extract_session_volumes(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    """
    从单票 tick DataFrame 拆三段量额。
    返回 None 表示无效。
    """
    if df is None or len(df) == 0:
        return None
    need = {"time", "volume", "amount"}
    if not need.issubset(set(df.columns)):
        return None

    work = df[["time", "volume", "amount"]].copy()
    work["bj"] = _to_bj_time(work["time"])
    work = work.dropna(subset=["bj"]).sort_values("bj")
    if work.empty:
        return None

    vol = work["volume"].astype(float)
    amt = work["amount"].astype(float)
    bj = work["bj"]

    # 日累计阶段：15:05 前，或 15:05 后尚未重置（极少见）
    # 盘后会话：15:05 起出现 volume 相对前值陡降（或置 0）
    after_start_idx = None
    for i in range(1, len(work)):
        ti = bj.iloc[i].time()
        if ti < dt_time(15, 5):
            continue
        prev_v = float(vol.iloc[i - 1])
        cur_v = float(vol.iloc[i])
        if prev_v > 0 and cur_v <= max(prev_v * 0.2, 1.0):
            after_start_idx = i
            break
        if ti >= dt_time(15, 5) and cur_v == 0 and prev_v > 0:
            after_start_idx = i
            break

    if after_start_idx is None:
        # 无盘后重置：仍可算收盘竞价；盘后记 0
        day_phase = work
        after_phase = work.iloc[0:0]
    else:
        day_phase = work.iloc[:after_start_idx]
        after_raw = work.iloc[after_start_idx:]
        # 去掉末尾回写全日累计的 tick
        day_ref = float(day_phase["volume"].iloc[-1]) if len(day_phase) else 0.0
        keep = []
        for j in range(len(after_raw)):
            v = float(after_raw["volume"].iloc[j])
            if day_ref > 0 and v >= day_ref * 0.9:
                break  # snapback 及之后丢弃
            keep.append(j)
        after_phase = after_raw.iloc[keep] if keep else after_raw.iloc[0:0]

    def _last_before(phase: pd.DataFrame, cut: dt_time) -> Tuple[float, float]:
        if phase is None or phase.empty:
            return 0.0, 0.0
        sub = phase[phase["bj"].dt.time < cut]
        if sub.empty:
            return 0.0, 0.0
        return float(sub["volume"].iloc[-1]), float(sub["amount"].iloc[-1])

    def _last_at_or_before(phase: pd.DataFrame, cut: dt_time) -> Tuple[float, float]:
        if phase is None or phase.empty:
            return 0.0, 0.0
        sub = phase[phase["bj"].dt.time <= cut]
        if sub.empty:
            return 0.0, 0.0
        return float(sub["volume"].iloc[-1]), float(sub["amount"].iloc[-1])

    v_pre, a_pre = _last_before(day_phase, dt_time(14, 57))
    # 收盘集合竞价：日累计在「盘后重置前」相对 14:57 前的增量
    # （撮合回写有时略晚于 15:00:00）
    if len(day_phase):
        v_15 = float(day_phase["volume"].iloc[-1])
        a_15 = float(day_phase["amount"].iloc[-1])
    else:
        v_15, a_15 = 0.0, 0.0

    close_auc_v = max(0.0, v_15 - v_pre)
    close_auc_a = max(0.0, a_15 - a_pre)

    if after_phase is not None and len(after_phase):
        after_v = float(after_phase["volume"].max())
        after_a = float(after_phase["amount"].max())
    else:
        after_v = 0.0
        after_a = 0.0

    day_v = v_15 + after_v
    day_a = a_15 + after_a

    return {
        "day_vol": day_v,
        "day_amount": day_a,
        "close_auc_vol": close_auc_v,
        "close_auc_amount": close_auc_a,
        "after_vol": after_v,
        "after_amount": after_a,
        "vol_at_1500": v_15,
        "amount_at_1500": a_15,
    }


def _download_ticks_batch(codes: List[str], day: str) -> None:
    import threading
    import xtquant.xtdata as xtdata

    start = f"{day}145500"
    end = f"{day}153100"
    if not codes:
        return

    done = threading.Event()
    finished_n = [0]

    def _cb(info):
        try:
            finished_n[0] += 1
            if isinstance(info, dict) and (info.get("finished") or info.get("is_finished")):
                done.set()
                return
            if finished_n[0] >= len(codes):
                done.set()
        except Exception:
            pass

    try:
        xtdata.download_history_data2(codes, "tick", start, end, _cb)
        if not done.wait(180):
            print(
                f"[download] wait timeout 180s (cb={finished_n[0]}/{len(codes)}); continue"
            )
        else:
            time.sleep(0.3)
        return
    except TypeError:
        try:
            xtdata.download_history_data2(codes, "tick", start, end)
            time.sleep(min(8.0, 0.08 * len(codes)))
            return
        except Exception as e:
            print(f"[download] batch error: {e}; fallback single")
    except Exception as e:
        print(f"[download] batch error: {e}; fallback single")

    for c in codes:
        try:
            xtdata.download_history_data(c, "tick", start, end)
        except Exception:
            pass


def _fetch_ticks(codes: List[str], day: str) -> Dict[str, pd.DataFrame]:
    import xtquant.xtdata as xtdata

    raw = xtdata.get_market_data_ex(
        ["time", "volume", "amount"],
        codes,
        period="tick",
        start_time=f"{day}145500",
        end_time=f"{day}153100",
        count=-1,
    )
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if v is not None and len(v) > 0}


def _download_and_fetch(codes: List[str], day: str) -> Dict[str, pd.DataFrame]:
    import xtquant.xtdata as xtdata

    _download_ticks_batch(codes, day)
    data = _fetch_ticks(codes, day)
    need = max(1, int(len(codes) * 0.25))
    if len(data) >= need:
        return data
    missing = [c for c in codes if c not in data]
    print(f"[download] fetch thin {len(data)}/{len(codes)}; single missing={len(missing)}")
    start = f"{day}145500"
    end = f"{day}153100"
    for c in missing:
        try:
            xtdata.download_history_data(c, "tick", start, end)
        except Exception:
            pass
    time.sleep(0.4)
    data2 = _fetch_ticks(codes, day)
    return data2 if len(data2) > len(data) else data



def _code_parts(code: str) -> Tuple[str, str]:
    c = (code or "").strip().upper()
    if "." in c:
        num, mkt = c.split(".", 1)
        return mkt, num
    if c.startswith(("5", "6", "9")):
        return "SH", c
    return "SZ", c


def _qmt_tick_day_file(data_dir: str, code: str, day: str) -> str:
    """QMT 本地 tick：{datadir}/{SH|SZ}/0/{6位}/{YYYYMMDD}.dat"""
    mkt, num = _code_parts(code)
    return os.path.join(data_dir, mkt, "0", num, f"{day}.dat")


def _purge_qmt_tick_day(codes: List[str], day: str, data_dir: str) -> Tuple[int, int]:
    """删除本批股票在 QMT datadir 中该日的 tick 文件。返回 (删除数, 释放字节近似)。"""
    removed = 0
    freed = 0
    for code in codes:
        path = _qmt_tick_day_file(data_dir, code, day)
        try:
            if os.path.isfile(path):
                freed += int(os.path.getsize(path) or 0)
                os.remove(path)
                removed += 1
            # 空目录可顺带清掉（可选）
            parent = os.path.dirname(path)
            if os.path.isdir(parent) and not os.listdir(parent):
                try:
                    os.rmdir(parent)
                except Exception:
                    pass
        except Exception:
            continue
    return removed, freed


def _empty_row(code: str) -> Dict[str, Any]:
    return {
        "code": code,
        "name": "",
        "day_vol": 0.0,
        "day_amount": 0.0,
        "close_auc_vol": 0.0,
        "close_auc_amount": 0.0,
        "after_vol": 0.0,
        "after_amount": 0.0,
        "float_shares": 0.0,
        "after_vs_day": None,
        "after_vs_close_auc": None,
        "after_vs_float": None,
    }


def _row_from_rec(code: str, rec: Dict[str, float]) -> Dict[str, Any]:
    after_vs_day = rec["after_vol"] / rec["day_vol"] if rec["day_vol"] > 0 else None
    after_vs_auc = (
        rec["after_vol"] / rec["close_auc_vol"] if rec["close_auc_vol"] > 0 else None
    )
    name, float_shares = _instrument_meta(code)
    after_shares = _after_vol_to_shares(rec["after_vol"], rec.get("after_amount", 0.0))
    after_vs_float = after_shares / float_shares if float_shares > 0 else None
    return {
        "code": code,
        "name": name or "",
        "day_vol": rec["day_vol"],
        "day_amount": rec["day_amount"],
        "close_auc_vol": rec["close_auc_vol"],
        "close_auc_amount": rec["close_auc_amount"],
        "after_vol": rec["after_vol"],
        "after_amount": rec["after_amount"],
        "float_shares": float_shares,
        "after_vs_day": after_vs_day,
        "after_vs_close_auc": after_vs_auc,
        "after_vs_float": after_vs_float,
    }


def run(
    day: str,
    limit: int = 0,
    batch_size: int = 80,
    skip_download: bool = False,
    top_n: int = 10,
    purge_qmt_tick: bool = True,
) -> str:
    import xtquant.xtdata as xtdata

    xtdata.enable_hello = False

    codes = _load_universe(limit=limit)
    if not codes:
        raise RuntimeError("无法获取沪深A股列表，请确认 QMT 已连接")

    data_dir = ""
    try:
        data_dir = str(xtdata.get_data_dir() or "")
    except Exception:
        data_dir = ""

    print(
        f"[rank] day={day} universe={len(codes)} batch_size={batch_size} "
        f"purge_qmt_tick={purge_qmt_tick} (不写项目 data/ticks)"
    )
    if data_dir:
        print(f"[rank] QMT datadir={data_dir}")
    out_dir = _project_out_dir(day)

    rows: List[Dict[str, Any]] = []
    t_all = time.time()
    purged_files = 0
    purged_bytes = 0

    for i in range(0, len(codes), batch_size):
        batch = codes[i : i + batch_size]
        t0 = time.time()
        if not skip_download:
            data = _download_and_fetch(batch, day)
        else:
            data = _fetch_ticks(batch, day)
        for code in batch:
            df = data.get(code)
            rec = extract_session_volumes(df) if df is not None else None
            if not rec or rec["day_vol"] <= 0:
                rows.append(_empty_row(code))
            else:
                rows.append(_row_from_rec(code, rec))
        # 立刻丢掉本批 DataFrame，避免全市场 tick 堆在内存
        del data
        gc.collect()

        if purge_qmt_tick and data_dir:
            n, b = _purge_qmt_tick_day(batch, day, data_dir)
            purged_files += n
            purged_bytes += b

        print(
            f"[batch] {min(i + batch_size, len(codes))}/{len(codes)} "
            f"{time.time() - t0:.1f}s"
            + (f" purged={purged_files} files ~{purged_bytes / 1024 / 1024:.1f}MB" if purge_qmt_tick else "")
        )

    detail = pd.DataFrame(rows)
    # 名称 / 流通股：给有盘后量及三榜候选补全
    interesting = detail[
        (detail["after_vol"] > 0)
        | (detail["after_vs_day"].fillna(0) > 0)
    ].copy()
    name_codes = set(
        interesting.nlargest(max(top_n * 5, 50), "after_vol")["code"].tolist()
        if len(interesting)
        else []
    )
    for col in ("after_vs_day", "after_vs_close_auc", "after_vs_float"):
        sub = detail[detail[col].notna() & (detail["after_vol"] > 0)]
        if len(sub):
            name_codes.update(sub.nlargest(max(top_n * 5, 50), col)["code"].tolist())

    name_map = {}
    float_map = {}
    for c in name_codes:
        n, fs = _instrument_meta(c)
        name_map[c] = n
        float_map[c] = fs
    detail["name"] = detail.apply(
        lambda r: r["name"] or name_map.get(r["code"], ""), axis=1
    )
    detail["float_shares"] = detail.apply(
        lambda r: float(r["float_shares"] or 0) or float(float_map.get(r["code"], 0) or 0),
        axis=1,
    )
    # 缺流通股时重算占流通
    need_recalc = detail["after_vol"].fillna(0) > 0
    for idx in detail.index[need_recalc]:
        fs = float(detail.at[idx, "float_shares"] or 0)
        if fs <= 0:
            continue
        if pd.isna(detail.at[idx, "after_vs_float"]):
            after_shares = _after_vol_to_shares(
                float(detail.at[idx, "after_vol"] or 0),
                float(detail.at[idx, "after_amount"] or 0),
            )
            detail.at[idx, "after_vs_float"] = after_shares / fs

    detail_path = os.path.join(out_dir, "detail.csv")
    detail_cn = detail.rename(
        columns={
            "code": "代码",
            "name": "名称",
            "day_vol": "全天量",
            "day_amount": "全天额",
            "close_auc_vol": "收盘竞价量",
            "close_auc_amount": "收盘竞价额",
            "after_vol": "盘后量",
            "after_amount": "盘后额",
            "float_shares": "流通股万股",
            "after_vs_day": "盘后占全天",
            "after_vs_close_auc": "盘后相对竞价",
            "after_vs_float": "盘后占流通",
        }
    ).copy()
    if "流通股万股" in detail_cn.columns:
        detail_cn["流通股万股"] = detail_cn["流通股万股"].map(
            lambda x: f"{float(x) / 10000.0:.2f}" if pd.notna(x) and float(x) > 0 else ""
        )
    if "盘后占全天" in detail_cn.columns:
        detail_cn["盘后占全天"] = detail_cn["盘后占全天"].map(
            lambda x: f"{100 * float(x):.3f}%" if pd.notna(x) else ""
        )
    if "盘后相对竞价" in detail_cn.columns:
        detail_cn["盘后相对竞价"] = detail_cn["盘后相对竞价"].map(
            lambda x: f"{float(x):.3f}" if pd.notna(x) else ""
        )
    if "盘后占流通" in detail_cn.columns:
        detail_cn["盘后占流通"] = detail_cn["盘后占流通"].map(
            lambda x: f"{100 * float(x):.3f}%" if pd.notna(x) else ""
        )
    detail_cn.to_csv(detail_path, index=False, encoding="utf-8-sig")

    n_day = int((detail["day_vol"].fillna(0) > 0).sum())
    n_after = int((detail["after_vol"].fillna(0) > 0).sum())
    print(
        f"[rank] coverage day_vol>0={n_day}/{len(detail)} after_vol>0={n_after}"
    )
    if n_day < max(200, int(len(detail) * 0.15)):
        print("[rank] WARN LOW COVERAGE — 多为空 tick，结果不可靠")

    ranked_day = detail[
        detail["after_vol"].fillna(0) > 0
    ].sort_values("after_vs_day", ascending=False).head(top_n)
    ranked_auc = detail[
        (detail["after_vol"].fillna(0) > 0) & (detail["close_auc_vol"].fillna(0) > 0)
    ].sort_values("after_vs_close_auc", ascending=False).head(top_n)
    ranked_float = detail[
        (detail["after_vol"].fillna(0) > 0)
        & (detail["float_shares"].fillna(0) > 0)
        & detail["after_vs_float"].notna()
    ].sort_values("after_vs_float", ascending=False).head(top_n)

    # 补名称
    for df_ in (ranked_day, ranked_auc, ranked_float):
        for idx, row in df_.iterrows():
            if not row.get("name"):
                n, fs = _instrument_meta(row["code"])
                df_.at[idx, "name"] = n
                if fs > 0 and not float(row.get("float_shares") or 0):
                    df_.at[idx, "float_shares"] = fs

    day_list = ranked_day.to_dict("records")
    auc_list = ranked_auc.to_dict("records")
    float_list = ranked_float.to_dict("records")
    day_codes = {str(r.get("code") or "").strip() for r in day_list if r.get("code")}
    auc_codes = {str(r.get("code") or "").strip() for r in auc_list if r.get("code")}
    float_codes = {str(r.get("code") or "").strip() for r in float_list if r.get("code")}
    by_code = {}
    for r in day_list + auc_list + float_list:
        c = str(r.get("code") or "").strip()
        if not c:
            continue
        if c not in by_code:
            by_code[c] = dict(r)
        else:
            cur = by_code[c]
            for k, v in r.items():
                if (cur.get(k) is None or (isinstance(cur.get(k), float) and pd.isna(cur.get(k)))) and v is not None:
                    cur[k] = v

    def _pct(v):
        return f"{100 * float(v):.3f}%" if v is not None and pd.notna(v) else ""

    def _num(v):
        return f"{float(v):.3f}" if v is not None and pd.notna(v) else ""

    def _float_wan(v):
        try:
            x = float(v)
            return f"{x / 10000.0:.2f}" if x > 0 else ""
        except Exception:
            return ""

    merged_rows = []
    for c, r in by_code.items():
        tags = []
        if c in day_codes:
            tags.append("盘后占全天")
        if c in auc_codes:
            tags.append("盘后相对竞价")
        if c in float_codes:
            tags.append("盘后占流通")
        merged_rows.append(
            {
                "代码": c,
                "名称": r.get("name", ""),
                "盘后量": r.get("after_vol", ""),
                "全天量": r.get("day_vol", ""),
                "收盘竞价量": r.get("close_auc_vol", ""),
                "流通股万股": _float_wan(r.get("float_shares")),
                "盘后占全天": _pct(r.get("after_vs_day")),
                "盘后相对竞价": _num(r.get("after_vs_close_auc")),
                "盘后占流通": _pct(r.get("after_vs_float")),
                "入选": "+".join(tags),
            }
        )

    def _sort_key(row):
        tags = str(row.get("入选") or "")
        n_tags = tags.count("+") + (1 if tags else 0)
        try:
            float_pct = float(str(row.get("盘后占流通") or "").replace("%", "") or 0)
        except Exception:
            float_pct = 0.0
        try:
            day_pct = float(str(row.get("盘后占全天") or "").replace("%", "") or 0)
        except Exception:
            day_pct = 0.0
        return (-n_tags, -float_pct, -day_pct)

    merged_rows.sort(key=_sort_key)
    merged = pd.DataFrame(merged_rows)
    p_top = os.path.join(out_dir, "top10.csv")
    merged.to_csv(p_top, index=False, encoding="utf-8-sig")
    for legacy in ("top10_after_vs_day.csv", "top10_after_vs_close_auc.csv"):
        lp = os.path.join(out_dir, legacy)
        if os.path.isfile(lp):
            try:
                os.remove(lp)
            except Exception:
                pass

    print("\n===== Top{} 三榜并集（{} 只）=====".format(top_n, len(merged)))
    if merged.empty:
        print("(无数据)")
    else:
        print(merged.to_string(index=False))

    print(f"\n[rank] done in {time.time() - t_all:.0f}s → {out_dir}")
    if purge_qmt_tick:
        print(
            f"  purged QMT tick files: {purged_files}, ~{purged_bytes / 1024 / 1024:.1f} MB"
        )
    print(f"  detail: {detail_path}")
    print(f"  top10 union: {p_top}")
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="全A盘后/收盘竞价量排名（默认不算完不长期存 tick）")
    parser.add_argument(
        "--day",
        default=_china_now().strftime("%Y%m%d"),
        help="交易日 YYYYMMDD，默认今天",
    )
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 只（调试用）")
    parser.add_argument("--batch-size", type=int, default=80, help="每批股票数（下完即算即删）")
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="跳过 download，直接读本地已有 QMT tick",
    )
    parser.add_argument(
        "--keep-qmt-tick",
        action="store_true",
        help="算完后保留 QMT datadir 里该日 tick（默认删除以省磁盘）",
    )
    parser.add_argument("--top", type=int, default=10, help="排名条数，默认 10")
    args = parser.parse_args()

    try:
        run(
            day=str(args.day).strip(),
            limit=int(args.limit or 0),
            batch_size=max(10, int(args.batch_size or 80)),
            skip_download=bool(args.skip_download),
            top_n=max(1, int(args.top or 10)),
            purge_qmt_tick=not bool(args.keep_qmt_tick),
        )
    except Exception as e:
        print(f"[rank] FAILED: {e}")
        import traceback

        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
