#coding:gbk
"""大 QMT 内：盘后量能排名（不依赖 MiniQMT）。

由「日线 → 全 A tick 落盘」完成后串行触发；优先读 data/ticks 本地 parquet，
缺失票才走大 QMT 内置 download_history_data + ContextInfo 读盘（同 tick_full_sync）。
默认关闭 xtdata.download_history_* / get_market_data_ex（会打 miniQMT 58610，
刷「无法连接行情服务」）。不再单独注册定时。
兼容大 QMT 内置 Python 3.6（禁止 from __future__ import annotations）。
"""
import gc
import os
import time
from datetime import date, datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

AFTER_HOURS_RANK_VERSION = "20260801.03"
# 与日线同点：仅作 catch-up 判断；正式启动由 tick 落盘串行触发
RANK_HOUR = 15
RANK_MINUTE = 35
BATCH_SIZE = 80
TOP_N = 10
# 大 QMT-only：默认关闭 xtdata 行情 RPC（miniQMT）；缺票用内置 download
ENABLE_XTDATA_TICK_DOWNLOAD = False
# 落盘就绪：detail 至少这么多行才算当日已完成（避免空/半成品当成功）
_MIN_DETAIL_ROWS = 2000
_FULL_SYNC_DONE = "_full_sync_done.json"
_MANUAL_REQUEST_NAME = "manual_request.json"

_BUSY = False
_LAST_DONE_DAY = ""
_CATCHUP_LOG_TS = 0.0


def _log(msg: str) -> None:
    print("[盘后排名] %s" % msg)


def _data_dir() -> str:
    try:
        from ant_qmt_paths import DATA_DIR

        return str(DATA_DIR)
    except Exception:
        try:
            from qmt_builtin.ant_qmt_paths import DATA_DIR

            return str(DATA_DIR)
        except Exception:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _out_dir(day: str) -> str:
    path = os.path.join(_data_dir(), "after_hours_rank", day)
    os.makedirs(path, exist_ok=True)
    return path


def _detail_csv_path(day: str) -> str:
    return os.path.join(_data_dir(), "after_hours_rank", day, "detail.csv")


def _run_log_path(day: str) -> str:
    return os.path.join(_data_dir(), "after_hours_rank", "%s_run.log" % day)


def _append_run_log(day: str, msg: str) -> None:
    try:
        parent = os.path.dirname(_run_log_path(day))
        os.makedirs(parent, exist_ok=True)
        with open(_run_log_path(day), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _detail_ready(day: str) -> bool:
    """磁盘上已有足量 detail.csv，则视为当日已完成（进程重启也能识别）。"""
    path = _detail_csv_path(day)
    if not os.path.isfile(path):
        return False
    try:
        n = 0
        with open(path, "r", encoding="utf-8-sig") as f:
            for i, _line in enumerate(f):
                if i == 0:
                    continue  # header
                n += 1
                if n >= _MIN_DETAIL_ROWS:
                    return True
        return n >= _MIN_DETAIL_ROWS
    except Exception:
        return False


def is_rank_done(day: str) -> bool:
    """内存或磁盘已完成当日量能。"""
    if day and day == _LAST_DONE_DAY:
        return True
    return _detail_ready(day)


def _ticks_day_dir(day: str) -> str:
    return os.path.join(_data_dir(), "ticks", day)


def _tick_full_sync_ready(day: str) -> bool:
    """全 A tick 落盘完成标记（量能读盘前应就绪；需含盘后时段的新版本）。"""
    path = os.path.join(_ticks_day_dir(day), _FULL_SYNC_DONE)
    if not os.path.isfile(path):
        return False
    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ver = str(data.get("version") or "")
        if ver < "20260728.04":
            return False
        total = int(data.get("total") or 0)
        ok = int(data.get("ok") or 0)
        return total > 0 and ok >= max(1000, int(total * 0.5))
    except Exception:
        return False


def _import_tick_io():
    try:
        import ant_tick_cache_io as mod

        return mod
    except ImportError:
        import qmt_builtin.ant_tick_cache_io as mod

        return mod


def _code6(code: str) -> str:
    s = (code or "").strip().replace(".", "")
    if len(s) >= 6:
        return s[:6]
    return s.zfill(6) if s else ""


def _load_batch_from_cache(codes: List[str], day: str) -> Tuple[Dict[str, Any], List[str]]:
    """从 data/ticks 读盘；返回 (已命中 dict, 缺失 full_code 列表)。"""
    tick_io = _import_tick_io()
    hit: Dict[str, Any] = {}
    missing: List[str] = []
    for code in codes:
        c6 = _code6(code)
        df = None
        try:
            df = tick_io.read_tick_volume_frame(c6, day)
        except Exception:
            df = None
        if df is not None and len(df) > 0:
            hit[code] = df
        else:
            missing.append(code)
    return hit, missing


def _get_xtdata():
    """仅 ENABLE_XTDATA_TICK_DOWNLOAD=True 时加载；默认不用 miniQMT RPC。"""
    if not ENABLE_XTDATA_TICK_DOWNLOAD:
        return None
    try:
        import xtquant.xtdata as xtdata

        try:
            xtdata.enable_hello = False
        except Exception:
            pass
        return xtdata
    except Exception as e:
        _log("xtdata导入失败: %s" % e)
        return None


def _is_tradeday(day: date, ContextInfo=None, xtdata=None) -> bool:
    """交易日判断：ContextInfo / 可选 xtdata / 工作日回退。不强制连行情 RPC。"""
    ds = day.strftime("%Y%m%d")
    for owner in (ContextInfo, xtdata):
        if owner is None:
            continue
        fn = getattr(owner, "get_trading_dates", None)
        if not callable(fn):
            continue
        try:
            arr = fn("SH", ds, ds) or []
            return bool(arr)
        except Exception:
            continue
    # 仅「落盘完成」可证明是交易日；空目录不能当交易日
    # （分笔 catch-up 探测曾误建空 ticks/{今日}/，导致周末一直「等待分笔」）
    if _tick_full_sync_ready(ds):
        return True
    return day.weekday() < 5


def _load_universe_from_file() -> List[str]:
    path = os.path.join(_data_dir(), "a_share_universe.json")
    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        raw = list(payload.get("codes") or [])
        out = [str(c).strip() for c in raw if str(c).strip()]
        if out:
            _log("股票池回退文件 n=%d" % len(out))
        return out
    except Exception as e:
        _log("股票池文件失败: %s" % e)
        return []


def _load_universe(xtdata, limit: int = 0, ContextInfo=None) -> List[str]:
    sectors = (
        "\u6caa\u6df1A\u80a1",  # 沪深A股
        "\u4e0a\u8bc1A\u80a1",  # 上证A股
        "\u6df1\u8bc1A\u80a1",  # 深证A股
    )
    owners = []  # type: List[Tuple[str, Any]]
    if ContextInfo is not None:
        owners.append(("ctx", ContextInfo))
    if xtdata is not None:
        owners.append(("xt", xtdata))

    codes: List[str] = []
    src = "none"
    for label, owner in owners:
        fn = getattr(owner, "get_stock_list_in_sector", None)
        if not callable(fn):
            continue
        got: List[str] = []
        for sector in sectors:
            try:
                part = fn(sector) or []
                got.extend([str(c).strip() for c in part if c])
            except Exception:
                continue
        if got:
            codes = got
            src = label
            break

    if not codes:
        codes = _load_universe_from_file()
        if codes:
            src = "file"

    seen = set()
    out: List[str] = []
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    if limit and limit > 0:
        out = out[:limit]
    if out:
        _log("股票池来源=%s n=%d" % (src, len(out)))
    return out


def _to_bj_series(series):
    import pandas as pd

    t = pd.to_datetime(series, unit="ms", errors="coerce")
    return t + pd.Timedelta(hours=8)


def extract_session_volumes(df) -> Optional[Dict[str, float]]:
    import pandas as pd

    if df is None or len(df) == 0:
        return None
    need = {"time", "volume", "amount"}
    if not need.issubset(set(df.columns)):
        return None

    work = df[["time", "volume", "amount"]].copy()
    work["bj"] = _to_bj_series(work["time"])
    work = work.dropna(subset=["bj"]).sort_values("bj")
    if work.empty:
        return None

    vol = work["volume"].astype(float)
    bj = work["bj"]

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
        day_phase = work
        after_phase = work.iloc[0:0]
    else:
        day_phase = work.iloc[:after_start_idx]
        after_raw = work.iloc[after_start_idx:]
        day_ref = float(day_phase["volume"].iloc[-1]) if len(day_phase) else 0.0
        keep = []
        for j in range(len(after_raw)):
            v = float(after_raw["volume"].iloc[j])
            if day_ref > 0 and v >= day_ref * 0.9:
                break
            keep.append(j)
        after_phase = after_raw.iloc[keep] if keep else after_raw.iloc[0:0]

    def _last_before(phase, cut: dt_time) -> Tuple[float, float]:
        if phase is None or phase.empty:
            return 0.0, 0.0
        sub = phase[phase["bj"].dt.time < cut]
        if sub.empty:
            return 0.0, 0.0
        return float(sub["volume"].iloc[-1]), float(sub["amount"].iloc[-1])

    v_pre, a_pre = _last_before(day_phase, dt_time(14, 57))
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

    return {
        "day_vol": v_15 + after_v,
        "day_amount": a_15 + after_a,
        "close_auc_vol": close_auc_v,
        "close_auc_amount": close_auc_a,
        "after_vol": after_v,
        "after_amount": after_a,
        "vol_at_1500": v_15,
        "amount_at_1500": a_15,
    }


def _code_parts(code: str) -> Tuple[str, str]:
    c = (code or "").strip().upper()
    if "." in c:
        num, mkt = c.split(".", 1)
        return mkt, num
    if c.startswith(("5", "6", "9")):
        return "SH", c
    return "SZ", c


def _purge_qmt_tick_day(xtdata, codes: List[str], day: str) -> Tuple[int, int]:
    if xtdata is None:
        return 0, 0
    try:
        data_dir = str(xtdata.get_data_dir() or "")
    except Exception:
        data_dir = ""
    if not data_dir:
        return 0, 0
    removed = 0
    freed = 0
    for code in codes:
        mkt, num = _code_parts(code)
        path = os.path.join(data_dir, mkt, "0", num, "%s.dat" % day)
        try:
            if os.path.isfile(path):
                freed += int(os.path.getsize(path) or 0)
                os.remove(path)
                removed += 1
                parent = os.path.dirname(path)
                if os.path.isdir(parent) and not os.listdir(parent):
                    try:
                        os.rmdir(parent)
                    except Exception:
                        pass
        except Exception:
            continue
    return removed, freed


def _raw_to_volume_frame(tick_io, raw: Any) -> Optional[Any]:
    """把 QMT raw tick 转成 extract_session_volumes 所需 time/volume/amount。"""
    if raw is None:
        return None
    try:
        df = tick_io.normalize_tick_dataframe(raw)
    except Exception:
        df = None
    if df is None:
        try:
            import pandas as pd

            df = pd.DataFrame(raw) if not hasattr(raw, "columns") else raw
        except Exception:
            return None
    if df is None or len(df) == 0:
        return None
    if "time" not in df.columns and "time_ts" in df.columns:
        try:
            df = df.rename(columns={"time_ts": "time"})
        except Exception:
            pass
    need = ("time", "volume", "amount")
    if not all(c in df.columns for c in need):
        return None
    try:
        return df.loc[:, list(need)].copy()
    except Exception:
        return None


def _fill_missing_from_qmt(
    codes: List[str],
    day: str,
    ContextInfo=None,
    xtdata=None,
) -> Dict[str, Any]:
    """缺本地 parquet 时：大 QMT 内置 download + ContextInfo 读盘（同 tick_full_sync）。

    默认不走 xtdata.download_history_* / get_market_data_ex（miniQMT）。
    """
    if not codes:
        return {}
    tick_io = _import_tick_io()
    try:
        if hasattr(tick_io, "bind_download_history_data"):
            tick_io.bind_download_history_data(None)
    except Exception:
        pass

    xt = xtdata if ENABLE_XTDATA_TICK_DOWNLOAD else None
    try:
        raw_map = tick_io.fetch_ticks_batch(
            codes,
            day,
            ContextInfo=ContextInfo,
            xtdata=xt,
            allow_subscribe=False,
            allow_builtin_download=True,
        )
    except Exception as e:
        _log("补缺失败: %s: %s" % (type(e).__name__, e))
        return {}

    out = {}  # type: Dict[str, Any]
    if not isinstance(raw_map, dict):
        return out
    for code in codes:
        raw = raw_map.get(code)
        if raw is None:
            # full_code 大小写/格式兼容
            c6 = _code6(code)
            for k, v in raw_map.items():
                if _code6(str(k)) == c6:
                    raw = v
                    break
        if raw is None:
            continue
        frame = _raw_to_volume_frame(tick_io, raw)
        if frame is None or len(frame) == 0:
            continue
        out[code] = frame
        # 顺手落盘，下次直接读 cache
        try:
            norm = tick_io.normalize_tick_dataframe(raw)
            if norm is not None and len(norm) > 0:
                tick_io.write_tick_cache(_code6(code), day, norm)
        except Exception:
            pass
    return out


def _stock_name(ContextInfo, xtdata, code: str) -> str:
    name, _float_shares = _instrument_meta(ContextInfo, xtdata, code)
    return name


def _instrument_meta(ContextInfo, xtdata, code: str) -> Tuple[str, float]:
    """返回 (名称, 流通股本股数)。优先 ContextInfo；xtdata 仅 ENABLE 时尝试。"""
    name = ""
    float_shares = 0.0
    owners = []  # type: List[Any]
    if ContextInfo is not None:
        owners.append(ContextInfo)
    if ENABLE_XTDATA_TICK_DOWNLOAD and xtdata is not None:
        owners.append(xtdata)
    for owner in owners:
        if owner is None:
            continue
        det = None
        for fn_name in ("get_instrumentdetail", "get_instrument_detail"):
            fn = getattr(owner, fn_name, None)
            if not callable(fn):
                continue
            try:
                det = fn(code) or {}
            except Exception:
                det = None
            if isinstance(det, dict) and det:
                break
        if not isinstance(det, dict) or not det:
            continue
        try:
            name = str(
                det.get("InstrumentName")
                or det.get("InstrumentNameCN")
                or det.get("instrumentName")
                or ""
            )
            fv = det.get("FloatVolume")
            if fv is None:
                fv = det.get("TotalVolume")
            try:
                float_shares = float(fv or 0)
            except Exception:
                float_shares = 0.0
            if float_shares < 0:
                float_shares = 0.0
            if name or float_shares > 0:
                return name, float_shares
        except Exception:
            continue
    return name, float_shares


def _after_vol_to_shares(
    after_vol: float, after_amount: float = 0.0, last_price: float = 0.0
) -> float:
    """把盘后量统一成「股」。QMT tick 累计 volume 多为「手」。"""
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
        # ~1 → 已是股；~100 → 手
        if ratio >= 10.0:
            return vol * 100.0
        return vol
    return vol * 100.0


def _write_csv(path: str, rows: List[Dict[str, Any]], fields: List[str]) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _fmt_pct(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        return "%.3f%%" % (100.0 * float(v))
    except Exception:
        return ""


def _fmt_num(v: Any, digits: int = 3) -> str:
    if v is None or v == "":
        return ""
    try:
        return ("%." + str(digits) + "f") % float(v)
    except Exception:
        return ""


def _fmt_vol(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        x = float(v)
        if abs(x - round(x)) < 1e-6:
            return str(int(round(x)))
        return "%.2f" % x
    except Exception:
        return str(v)


DETAIL_FIELDS_CN = [
    "代码",
    "名称",
    "全天量",
    "全天额",
    "收盘竞价量",
    "收盘竞价额",
    "盘后量",
    "盘后额",
    "流通股万股",
    "盘后占全天",
    "盘后相对竞价",
    "盘后占流通",
]

TOP10_UNION_FIELDS_CN = [
    "代码",
    "名称",
    "盘后量",
    "全天量",
    "收盘竞价量",
    "流通股万股",
    "盘后占全天",
    "盘后相对竞价",
    "盘后占流通",
    "入选",
]


def _fmt_float_wan(v: Any) -> str:
    """流通股（股）→ 万股展示。"""
    if v is None or v == "":
        return ""
    try:
        x = float(v)
        if x <= 0:
            return ""
        return "%.2f" % (x / 10000.0)
    except Exception:
        return ""


def _rows_to_detail_cn(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        out.append(
            {
                "代码": r.get("code", ""),
                "名称": r.get("name", ""),
                "全天量": _fmt_vol(r.get("day_vol")),
                "全天额": _fmt_vol(r.get("day_amount")),
                "收盘竞价量": _fmt_vol(r.get("close_auc_vol")),
                "收盘竞价额": _fmt_vol(r.get("close_auc_amount")),
                "盘后量": _fmt_vol(r.get("after_vol")),
                "盘后额": _fmt_vol(r.get("after_amount")),
                "流通股万股": _fmt_float_wan(r.get("float_shares")),
                "盘后占全天": _fmt_pct(r.get("after_vs_day")),
                "盘后相对竞价": _fmt_num(r.get("after_vs_close_auc")),
                "盘后占流通": _fmt_pct(r.get("after_vs_float")),
            }
        )
    return out


def _build_top10_union_cn(
    top_day: List[Dict[str, Any]],
    top_auc: List[Dict[str, Any]],
    top_float: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """三榜并集：任一榜进前 N 即入表；同代码只保留一行。"""
    if top_float is None:
        top_float = []
    day_codes = set()
    for r in top_day:
        c = str(r.get("code") or "").strip()
        if c:
            day_codes.add(c)
    auc_codes = set()
    for r in top_auc:
        c = str(r.get("code") or "").strip()
        if c:
            auc_codes.add(c)
    float_codes = set()
    for r in top_float:
        c = str(r.get("code") or "").strip()
        if c:
            float_codes.add(c)

    by_code = {}
    for r in list(top_day) + list(top_auc) + list(top_float):
        c = str(r.get("code") or "").strip()
        if not c:
            continue
        if c not in by_code:
            by_code[c] = dict(r)
        else:
            # 补全空字段
            cur = by_code[c]
            for k, v in r.items():
                if cur.get(k) in (None, "") and v not in (None, ""):
                    cur[k] = v

    out = []
    for c, r in by_code.items():
        tags = []
        if c in day_codes:
            tags.append("盘后占全天")
        if c in auc_codes:
            tags.append("盘后相对竞价")
        if c in float_codes:
            tags.append("盘后占流通")
        out.append(
            {
                "代码": c,
                "名称": r.get("name", ""),
                "盘后量": _fmt_vol(r.get("after_vol")),
                "全天量": _fmt_vol(r.get("day_vol")),
                "收盘竞价量": _fmt_vol(r.get("close_auc_vol")),
                "流通股万股": _fmt_float_wan(r.get("float_shares")),
                "盘后占全天": _fmt_pct(r.get("after_vs_day")),
                "盘后相对竞价": _fmt_num(r.get("after_vs_close_auc")),
                "盘后占流通": _fmt_pct(r.get("after_vs_float")),
                "入选": "+".join(tags),
            }
        )

    # 先按入选榜数，再按盘后占流通、盘后占全天降序
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

    out.sort(key=_sort_key)
    return out


def run_after_hours_rank(
    ContextInfo=None,
    day: str = "",
    limit: int = 0,
    batch_size: int = BATCH_SIZE,
    top_n: int = TOP_N,
    purge_qmt_tick: bool = True,
    force: bool = False,
) -> bool:
    """全市场排名；成功返回 True。优先 data/ticks，不依赖 miniQMT。"""
    global _BUSY, _LAST_DONE_DAY

    if _BUSY:
        _log("忙碌中，跳过")
        return False

    # 默认不加载 xtdata（避免误触 miniQMT RPC）
    xtdata = _get_xtdata()

    now = datetime.now()
    day = (day or now.strftime("%Y%m%d")).strip()
    try:
        day_d = datetime.strptime(day, "%Y%m%d").date()
    except Exception:
        day_d = now.date()
        day = day_d.strftime("%Y%m%d")

    if not force and (day == _LAST_DONE_DAY or _detail_ready(day)):
        _LAST_DONE_DAY = day
        _log("已完成: %s（磁盘或内存）" % day)
        return True

    if not _is_tradeday(day_d, ContextInfo=ContextInfo, xtdata=xtdata):
        _log("非交易日: %s" % day)
        _LAST_DONE_DAY = day
        return True

    # 交易日未到 15:30 不跑（手动 force 除外）
    if (not force) and day_d == now.date() and now.time() < dt_time(15, 30):
        _log("未到15:30，跳过")
        return False

    # 默认等全 A tick 落盘完成后再读盘；force 可跳过等待
    if (not force) and not _tick_full_sync_ready(day):
        _log("等待分笔同步完成: %s" % day)
        return False

    codes = _load_universe(xtdata, limit=limit, ContextInfo=ContextInfo)
    if not codes:
        _log("股票池为空")
        return False

    _BUSY = True
    t_all = time.time()
    purged_files = 0
    purged_bytes = 0
    cache_hits = 0
    cache_miss = 0
    rows: List[Dict[str, Any]] = []
    try:
        # 尽早建目录 + 写进度日志，避免跑到一半失败时目录都不存在
        out = _out_dir(day)
        start_msg = (
            "开始 day=%s 股票池=%s 版本=%s batch=%s 读缓存=1 xtdata_dl=%s"
            % (
                day,
                len(codes),
                AFTER_HOURS_RANK_VERSION,
                batch_size,
                "on" if ENABLE_XTDATA_TICK_DOWNLOAD else "off",
            )
        )
        _log(start_msg)
        _append_run_log(
            day,
            "START n=%s batch=%s read_cache=1 xtdata_dl=%s version=%s"
            % (
                len(codes),
                batch_size,
                "on" if ENABLE_XTDATA_TICK_DOWNLOAD else "off",
                AFTER_HOURS_RANK_VERSION,
            ),
        )
        for i in range(0, len(codes), batch_size):
            batch = codes[i : i + batch_size]
            t0 = time.time()
            data, missing = _load_batch_from_cache(batch, day)
            cache_hits += len(data)
            cache_miss += len(missing)
            if missing:
                fetched = _fill_missing_from_qmt(
                    missing, day, ContextInfo=ContextInfo, xtdata=xtdata
                )
                data.update(fetched)
                if purge_qmt_tick and xtdata is not None:
                    n, b = _purge_qmt_tick_day(xtdata, missing, day)
                    purged_files += n
                    purged_bytes += b
            for code in batch:
                rec = extract_session_volumes(data.get(code))
                if not rec or rec["day_vol"] <= 0:
                    rows.append(
                        {
                            "code": code,
                            "name": "",
                            "day_vol": 0.0,
                            "day_amount": 0.0,
                            "close_auc_vol": 0.0,
                            "close_auc_amount": 0.0,
                            "after_vol": 0.0,
                            "after_amount": 0.0,
                            "float_shares": 0.0,
                            "after_vs_day": "",
                            "after_vs_close_auc": "",
                            "after_vs_float": "",
                        }
                    )
                    continue
                after_vs_day = (
                    rec["after_vol"] / rec["day_vol"] if rec["day_vol"] > 0 else None
                )
                after_vs_auc = (
                    rec["after_vol"] / rec["close_auc_vol"]
                    if rec["close_auc_vol"] > 0
                    else None
                )
                # 仅盘后量>0 取合约详情（名称/流通股）；避免全市场扫详情拖慢读盘
                _name, float_shares = "", 0.0
                if rec["after_vol"] > 0:
                    _name, float_shares = _instrument_meta(
                        ContextInfo, xtdata, code
                    )
                after_shares = _after_vol_to_shares(
                    rec["after_vol"], rec.get("after_amount", 0.0)
                )
                after_vs_float = (
                    after_shares / float_shares if float_shares > 0 else None
                )
                rows.append(
                    {
                        "code": code,
                        "name": _name or "",
                        "day_vol": rec["day_vol"],
                        "day_amount": rec["day_amount"],
                        "close_auc_vol": rec["close_auc_vol"],
                        "close_auc_amount": rec["close_auc_amount"],
                        "after_vol": rec["after_vol"],
                        "after_amount": rec["after_amount"],
                        "float_shares": float_shares,
                        "after_vs_day": after_vs_day if after_vs_day is not None else "",
                        "after_vs_close_auc": after_vs_auc
                        if after_vs_auc is not None
                        else "",
                        "after_vs_float": after_vs_float
                        if after_vs_float is not None
                        else "",
                    }
                )
            del data
            gc.collect()
            progress = (
                "进度 %s/%s %.1fs 缓存命中=%s 缺失=%s 已清理文件=%s"
                % (
                    min(i + batch_size, len(codes)),
                    len(codes),
                    time.time() - t0,
                    cache_hits,
                    cache_miss,
                    purged_files,
                )
            )
            _log(progress)
            # 每 5 批写一次盘，减轻 IO
            if ((i // batch_size) % 5) == 0:
                _append_run_log(day, progress)

        # rank
        with_after = [r for r in rows if float(r.get("after_vol") or 0) > 0]
        top_day = sorted(
            [r for r in with_after if r.get("after_vs_day") != ""],
            key=lambda r: float(r["after_vs_day"]),
            reverse=True,
        )[:top_n]
        top_auc = sorted(
            [
                r
                for r in with_after
                if r.get("after_vs_close_auc") != ""
                and float(r.get("close_auc_vol") or 0) > 0
            ],
            key=lambda r: float(r["after_vs_close_auc"]),
            reverse=True,
        )[:top_n]
        top_float = sorted(
            [
                r
                for r in with_after
                if r.get("after_vs_float") != ""
                and float(r.get("float_shares") or 0) > 0
            ],
            key=lambda r: float(r["after_vs_float"]),
            reverse=True,
        )[:top_n]

        name_codes = set()
        for r in top_day + top_auc + top_float:
            name_codes.add(r["code"])
        for c in list(name_codes):
            name, float_shares = _instrument_meta(ContextInfo, xtdata, c)
            for r in rows:
                if r["code"] == c:
                    if name:
                        r["name"] = name
                    if float_shares > 0 and not float(r.get("float_shares") or 0):
                        r["float_shares"] = float_shares
            for pool in (top_day, top_auc, top_float):
                for r in pool:
                    if r["code"] == c:
                        if name:
                            r["name"] = name
                        if float_shares > 0 and not float(r.get("float_shares") or 0):
                            r["float_shares"] = float_shares

        # 明细 + 三榜并集 Top（中文表头）
        _write_csv(
            os.path.join(out, "detail.csv"),
            _rows_to_detail_cn(rows),
            DETAIL_FIELDS_CN,
        )
        union_rows = _build_top10_union_cn(top_day, top_auc, top_float)
        _write_csv(os.path.join(out, "top10.csv"), union_rows, TOP10_UNION_FIELDS_CN)
        # 清理旧版分榜文件（若存在）
        for legacy in ("top10_after_vs_day.csv", "top10_after_vs_close_auc.csv"):
            lp = os.path.join(out, legacy)
            try:
                if os.path.isfile(lp):
                    os.remove(lp)
            except Exception:
                pass

        _log("===== Top%s 三榜并集 %s 只 =====" % (top_n, len(union_rows)))
        for r in union_rows:
            _log(
                "%s %s 占全天=%s 相对竞价=%s 占流通=%s [%s]"
                % (
                    r["代码"],
                    r["名称"],
                    r["盘后占全天"],
                    r["盘后相对竞价"],
                    r["盘后占流通"],
                    r["入选"],
                )
            )

        n_day = sum(1 for r in rows if float(r.get("day_vol") or 0) > 0)
        n_after = sum(1 for r in rows if float(r.get("after_vol") or 0) > 0)
        _log(
            "覆盖 day_vol>0=%s/%s after_vol>0=%s"
            % (n_day, len(rows), n_after)
        )
        # 覆盖过低多半是异步下载未等完；不标记 done，便于盘后再手动 force 补跑
        low = n_day < max(200, int(len(rows) * 0.15))
        if low:
            _log(
                "覆盖过低 LOW COVERAGE — 拒绝标记完成；请检查本地ticks / 内置下载"
            )
            _append_run_log(
                day,
                "LOW COVERAGE day_vol>0=%s/%s — 未标记完成"
                % (n_day, len(rows)),
            )
            try:
                try:
                    import ant_server_chan as sct
                except ImportError:
                    import qmt_builtin.ant_server_chan as sct
                sct.notify_alert(
                    "盘后量能覆盖过低",
                    "日=%s\nday_vol>0=%s/%s after_vol>0=%s\n请检查 data/ticks 与大QMT后重跑量能。"
                    % (day, n_day, len(rows), n_after),
                    alert_key="after_rank_low_%s" % day,
                    cooldown_sec=3600,
                )
            except Exception:
                pass
        else:
            _LAST_DONE_DAY = day
        done_msg = (
            "完成 耗时=%.0fs 输出=%s 缓存命中=%s 缺失=%s 已清理~%.1fMB"
            % (
                time.time() - t_all,
                out,
                cache_hits,
                cache_miss,
                purged_bytes / 1024.0 / 1024.0,
            )
        )
        _log(done_msg)
        _append_run_log(day, done_msg)
        return not low
    except Exception as e:
        _log("失败: %s: %s" % (type(e).__name__, e))
        _append_run_log(day, "FAILED: %s: %s" % (type(e).__name__, e))
        try:
            try:
                import ant_server_chan as sct
            except ImportError:
                import qmt_builtin.ant_server_chan as sct
            sct.notify_alert(
                "盘后量能运行失败",
                "日=%s\n%s: %s\n请查看 after_hours_rank 日志。"
                % (day, type(e).__name__, e),
                alert_key="after_rank_fail_%s" % day,
                cooldown_sec=3600,
            )
        except Exception:
            pass
        return False
    finally:
        _BUSY = False


def after_hours_volume_rank(ContextInfo):
    """QMT run_time 回调入口（兼容旧 timer；正常由 tick_full_sync 串行触发）。"""
    return run_after_hours_rank(ContextInfo, force=False)


def _manual_request_path() -> str:
    return os.path.join(_data_dir(), "after_hours_rank", _MANUAL_REQUEST_NAME)


def _normalize_manual_days(req: Dict[str, Any]) -> List[str]:
    days = req.get("days")
    if days is None and req.get("day"):
        days = [req.get("day")]
    out: List[str] = []
    if not isinstance(days, list):
        return out
    for d in days:
        s = str(d or "").strip()
        if len(s) == 8 and s.isdigit():
            out.append(s)
    return out


def process_manual_request(ContextInfo=None) -> Optional[bool]:
    """处理 data/after_hours_rank/manual_request.json；无请求返回 None。

    外挂 / 工具写入，大 QMT 策略 periodic_sync 用真实 ContextInfo 执行。

    单日::
        {"day":"20260730","force":true}

    多日队列（每次只跑队首一日）::
        {"days":["20260728","20260730"],"force":true}

    force=true 可重跑已有 detail / 跳过 tick 就绪等待。默认不走 miniQMT。
    """
    import json

    path = _manual_request_path()
    if not os.path.isfile(path):
        return None
    if _BUSY:
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            req = json.load(f) or {}
    except Exception as e:
        _log("手动请求读取失败: %s" % e)
        return False
    if not isinstance(req, dict):
        return False
    days = _normalize_manual_days(req)
    if not days:
        _log("手动请求日期无效: %s" % (req.get("days") or req.get("day")))
        try:
            os.remove(path)
        except Exception:
            pass
        return False
    force = bool(req.get("force"))
    day_s = days[0]
    remaining = days[1:]
    _log(
        "手动请求 day=%s 剩余=%d force=%s src=%s"
        % (day_s, len(remaining), force, str(req.get("source") or "")[:40])
    )
    _append_run_log(
        day_s,
        "MANUAL_REQUEST force=%s remain=%d version=%s src=%s"
        % (
            force,
            len(remaining),
            AFTER_HOURS_RANK_VERSION,
            str(req.get("source") or "")[:40],
        ),
    )
    try:
        done_path = path + ".done.json"
        with open(done_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "day": day_s,
                    "remaining": remaining,
                    "force": force,
                    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "version": AFTER_HOURS_RANK_VERSION,
                    "source": str(req.get("source") or ""),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as e:
        _log("手动请求归档失败: %s" % e)
        return False

    ok = run_after_hours_rank(ContextInfo, day=day_s, force=force)
    try:
        if remaining:
            payload = dict(req)
            payload["days"] = remaining
            payload.pop("day", None)
            payload["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            payload["version"] = AFTER_HOURS_RANK_VERSION
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        else:
            if os.path.isfile(path):
                os.remove(path)
    except Exception as e:
        _log("手动请求队列更新失败: %s" % e)
        return False
    return bool(ok)


def maybe_catch_up_after_hours_rank(ContextInfo=None) -> bool:
    """盘后补跑：落盘已就绪且 detail 未就绪时触发。

    注意：会阻塞当前线程至跑完；仅应在盘后调用。优先由 tick_full_sync 串行触发。
    """
    global _CATCHUP_LOG_TS

    if _BUSY:
        return False

    now = datetime.now()
    day = now.strftime("%Y%m%d")
    if now.time() < dt_time(RANK_HOUR, RANK_MINUTE):
        return False
    # 周末/节假日勿用「今天」去等分笔（空 ticks 目录曾导致假交易日死等）
    try:
        day_d = datetime.strptime(day, "%Y%m%d").date()
    except Exception:
        day_d = now.date()
    if not _is_tradeday(day_d, ContextInfo=ContextInfo):
        return False
    if is_rank_done(day):
        return True
    if not _tick_full_sync_ready(day):
        return False

    # 避免 periodic 里每秒刷日志；真正开跑前再打 start
    ts = time.time()
    if ts - _CATCHUP_LOG_TS >= 120.0:
        _CATCHUP_LOG_TS = ts
        _log(
            "需补跑: %s（分笔已就绪，尚无detail）"
            % day
        )

    return run_after_hours_rank(ContextInfo, day=day, force=False)


def register_after_hours_rank_timer(ContextInfo) -> None:
    """不再单独注册定时：由 daily→tick 流水线串行触发。"""
    return
