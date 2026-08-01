#coding:gbk
"""QMT �� tick ��ȡ�����̣��������� Python 3.6+����

����ʽ��data/ticks/{YYYYMMDD}/{code}.parquet���嵵չƽ��
�ɸ�ʽ��.pkl ������������ȡ���ˣ���ģ����д��ֻд parquet��
"""
import os
import time as time_module
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple, Union

TICK_CACHE_IO_VERSION = "20260731.3"

try:
    from ant_qmt_paths import PROJECT_ROOT
except ImportError:
    from qmt_builtin.ant_qmt_paths import PROJECT_ROOT

TradeDateInput = Union[date, datetime, str]

_DISK_BASE_COLS = (
    "time_ts",
    "lastPrice",
    "open",
    "high",
    "low",
    "lastClose",
    "amount",
    "volume",
)


def norm_code6(code):
    # type: (str) -> str
    c = (code or "").strip().replace(".", "")
    if len(c) < 6:
        c = c.zfill(6) if c else ""
    else:
        c = c[:6]
    return c


def qmt_result_empty(raw):
    # type: (Any) -> bool
    if raw is None:
        return True
    try:
        import pandas as pd

        if isinstance(raw, pd.DataFrame):
            return raw.empty
    except ImportError:
        pass
    try:
        return len(raw) == 0
    except Exception:
        return True


def parse_trade_date(trade_date):
    # type: (TradeDateInput) -> Tuple[date, str]
    if isinstance(trade_date, datetime):
        d = trade_date.date()
        return d, d.strftime("%Y%m%d")
    if isinstance(trade_date, date):
        return trade_date, trade_date.strftime("%Y%m%d")
    s = str(trade_date or "").strip().replace("-", "").replace("/", "")[:8]
    if len(s) == 8 and s.isdigit():
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8])), s
    raise ValueError("invalid trade_date: %r" % (trade_date,))


def tick_cache_path(code_6, trade_date):
    # type: (str, TradeDateInput) -> str
    c6 = norm_code6(code_6)
    _, ymd = parse_trade_date(trade_date)
    root = PROJECT_ROOT.rstrip("\\/")
    return os.path.join(root, "data", "ticks", ymd, c6 + ".parquet")


def tick_cache_path_legacy_pkl(code_6, trade_date):
    # type: (str, TradeDateInput) -> str
    c6 = norm_code6(code_6)
    _, ymd = parse_trade_date(trade_date)
    root = PROJECT_ROOT.rstrip("\\/")
    return os.path.join(root, "data", "ticks", ymd, c6 + ".pkl")


def tick_cache_file_ready(code_6, trade_date):
    # type: (str, TradeDateInput) -> bool
    for path in (
        tick_cache_path(code_6, trade_date),
        tick_cache_path_legacy_pkl(code_6, trade_date),
    ):
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 32:
                return True
        except OSError:
            continue
    return False


def full_stock_code(code_6):
    # type: (str) -> str
    c6 = norm_code6(code_6)
    if c6.startswith("6"):
        return c6 + ".SH"
    if c6.startswith(("0", "3")):
        return c6 + ".SZ"
    if c6.startswith(("4", "8", "920")):
        return c6 + ".BJ"
    return c6 + ".SZ"


def _level_scalar(raw, idx):
    # type: (Any, int) -> float
    try:
        if isinstance(raw, (list, tuple)):
            if idx >= len(raw):
                return 0.0
            v = raw[idx]
        else:
            return 0.0
        if v is None:
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def flatten_depth_columns(df):
    # type: (Any) -> Any
    try:
        import pandas as pd
    except ImportError:
        return df
    if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0:
        return df
    data = df
    already = True
    for i in range(1, 6):
        if ("ask%d" % i) not in data.columns or ("bid%d" % i) not in data.columns:
            already = False
            break
    if already:
        return data

    ap = data["askPrice"] if "askPrice" in data.columns else None
    bp = data["bidPrice"] if "bidPrice" in data.columns else None
    av = None
    if "askVol" in data.columns:
        av = data["askVol"]
    elif "askVolume" in data.columns:
        av = data["askVolume"]
    bv = None
    if "bidVol" in data.columns:
        bv = data["bidVol"]
    elif "bidVolume" in data.columns:
        bv = data["bidVolume"]

    for i in range(1, 6):
        idx = i - 1
        ask_c = "ask%d" % i
        bid_c = "bid%d" % i
        ask_v = "ask%d_vol" % i
        bid_v = "bid%d_vol" % i
        if ap is not None and ask_c not in data.columns:
            data[ask_c] = ap.map(lambda x, j=idx: _level_scalar(x, j))
        if bp is not None and bid_c not in data.columns:
            data[bid_c] = bp.map(lambda x, j=idx: _level_scalar(x, j))
        if av is not None and ask_v not in data.columns:
            data[ask_v] = av.map(lambda x, j=idx: _level_scalar(x, j))
        if bv is not None and bid_v not in data.columns:
            data[bid_v] = bv.map(lambda x, j=idx: _level_scalar(x, j))
        for src, dst in (
            ("askPrice%d" % i, ask_c),
            ("bidPrice%d" % i, bid_c),
            ("askVol%d" % i, ask_v),
            ("bidVol%d" % i, bid_v),
            ("askVolume%d" % i, ask_v),
            ("bidVolume%d" % i, bid_v),
        ):
            if src in data.columns and dst not in data.columns:
                data[dst] = pd.to_numeric(data[src], errors="coerce").fillna(0.0)
    return data


def normalize_tick_dataframe(raw):
    # type: (Any) -> Optional[Any]
    if raw is None:
        return None
    try:
        import pandas as pd
    except ImportError:
        return None

    try:
        data = pd.DataFrame(raw) if not isinstance(raw, pd.DataFrame) else raw.copy()
    except Exception:
        return None
    if len(data) == 0 or "time" not in data.columns:
        return None

    data = data.sort_values("time")
    if "lastPrice" in data.columns:
        data = data[data["lastPrice"] > 0]
    if len(data) == 0:
        return None

    data["time_ts"] = data["time"]
    if hasattr(data["time"].dtype, "kind") and data["time"].dtype.kind in ("i", "u", "f"):
        data["datetime"] = pd.to_datetime(data["time"], unit="ms")
    else:
        data["datetime"] = pd.to_datetime(data["time"])

    dt = data["datetime"]
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize("UTC").dt.tz_convert("Asia/Shanghai")
    else:
        dt = dt.dt.tz_convert("Asia/Shanghai")
    data["datetime"] = dt
    # �� 15:00 ���̾��ۼ�Լ 15:05�C15:30 �̺󣻹��̺����ܶ���
    mask = (
        ((dt.dt.hour == 9) & (dt.dt.minute >= 15))
        | (dt.dt.hour == 10)
        | ((dt.dt.hour == 11) & (dt.dt.minute <= 30))
        | ((dt.dt.hour >= 13) & (dt.dt.hour < 15))
        | ((dt.dt.hour == 15) & (dt.dt.minute <= 31))
    )
    data = data[mask].copy()
    if len(data) == 0:
        return None
    return flatten_depth_columns(data)


def coerce_tick_dataframe(df):
    # type: (Any) -> Optional[Any]
    if df is None:
        return None
    try:
        import pandas as pd
    except ImportError:
        return None

    try:
        if not isinstance(df, pd.DataFrame):
            data = pd.DataFrame(df)
        else:
            data = df.copy()
    except Exception:
        return None

    if len(data) == 0:
        return None

    if "time" not in data.columns and "time_ts" in data.columns:
        data["time"] = data["time_ts"]

    if "datetime" not in data.columns and "time" in data.columns:
        return normalize_tick_dataframe(data)

    if "datetime" in data.columns:
        try:
            data["datetime"] = pd.to_datetime(data["datetime"], errors="coerce")
        except Exception:
            return None
        data = data[data["datetime"].notna()]
        if len(data) == 0:
            return None
        dt = data["datetime"]
        if getattr(dt.dt, "tz", None) is None:
            if "time" in data.columns:
                try:
                    t0 = data["time"].iloc[0]
                    if hasattr(t0, "item"):
                        t0 = t0.item()
                    if isinstance(t0, (int, float)) and float(t0) > 1e12:
                        data["datetime"] = pd.to_datetime(data["time"], unit="ms", errors="coerce")
                        data["datetime"] = (
                            data["datetime"].dt.tz_localize("UTC").dt.tz_convert("Asia/Shanghai")
                        )
                    else:
                        data["datetime"] = data["datetime"].dt.tz_localize("Asia/Shanghai")
                except Exception:
                    try:
                        data["datetime"] = data["datetime"].dt.tz_localize("Asia/Shanghai")
                    except Exception:
                        pass
            else:
                try:
                    data["datetime"] = data["datetime"].dt.tz_localize("Asia/Shanghai")
                except Exception:
                    pass
        else:
            try:
                data["datetime"] = data["datetime"].dt.tz_convert("Asia/Shanghai")
            except Exception:
                pass

    if "lastPrice" not in data.columns:
        for alt in ("last_price", "price", "last", "matchPrice"):
            if alt in data.columns:
                data["lastPrice"] = data[alt]
                break

    if "lastPrice" not in data.columns or "datetime" not in data.columns:
        return None

    try:
        data = data[data["lastPrice"] > 0]
    except Exception:
        return None
    return data if len(data) > 0 else None


def is_full_day_ticks(df):
    # type: (Any) -> bool
    try:
        if df is None or len(df) == 0:
            return False
        data = coerce_tick_dataframe(df)
        if data is None or len(data) == 0 or "datetime" not in data.columns:
            return False
        dt = data["datetime"]
        if hasattr(dt.dt, "tz") and dt.dt.tz is not None:
            dt = dt.dt.tz_convert("Asia/Shanghai")
        has_open = bool(((dt.dt.hour == 9) & (dt.dt.minute <= 35)).any()) or bool(
            ((dt.dt.hour == 9) & (dt.dt.minute >= 15) & (dt.dt.minute <= 25)).any()
        )
        has_close = bool(((dt.dt.hour == 14) & (dt.dt.minute >= 55)).any()) or bool(
            ((dt.dt.hour == 15) & (dt.dt.minute == 0)).any()
        )
        return has_open and has_close
    except Exception:
        return False


def prepare_tick_for_disk(df):
    # type: (Any) -> Optional[Any]
    try:
        import pandas as pd
    except ImportError:
        return None
    data = coerce_tick_dataframe(df)
    if data is None or len(data) == 0:
        return None
    if "time_ts" not in data.columns:
        if "time" in data.columns:
            data["time_ts"] = data["time"]
        else:
            return None
    data = flatten_depth_columns(data)
    keep = []  # type: List[str]
    for c in _DISK_BASE_COLS:
        if c in data.columns:
            keep.append(c)
    for i in range(1, 6):
        for c in ("ask%d" % i, "bid%d" % i, "ask%d_vol" % i, "bid%d_vol" % i):
            if c in data.columns:
                keep.append(c)
    out = data.loc[:, [c for c in keep if c in data.columns]].copy()
    for i in range(1, 6):
        for c in ("ask%d" % i, "bid%d" % i, "ask%d_vol" % i, "bid%d_vol" % i):
            if c not in out.columns:
                out[c] = 0.0
    for c in out.columns:
        if c == "time_ts":
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("int64")
        else:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0).astype("float64")
    return out


def _write_parquet_file(df, path):
    # type: (Any, str) -> bool
    tmp = path + ".tmp"
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except Exception:
        pass

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(df, preserve_index=False)
        for comp in ("snappy", "gzip", "zstd"):
            try:
                pq.write_table(table, tmp, compression=comp)
                if os.path.exists(path):
                    os.remove(path)
                os.rename(tmp, path)
                return True
            except Exception:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
    except Exception:
        pass

    for comp in ("snappy", "gzip", "zstd"):
        try:
            df.to_parquet(tmp, compression=comp, index=False)
            if os.path.exists(path):
                os.remove(path)
            os.rename(tmp, path)
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    return False


def write_tick_cache(code_6, trade_date, df):
    # type: (str, TradeDateInput, Any) -> bool
    if df is None or len(df) == 0:
        return False
    if not is_full_day_ticks(df):
        return False
    disk_df = prepare_tick_for_disk(df)
    if disk_df is None or len(disk_df) == 0:
        return False
    try:
        path = tick_cache_path(code_6, trade_date)
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        if not _write_parquet_file(disk_df, path):
            return False
        legacy = tick_cache_path_legacy_pkl(code_6, trade_date)
        if os.path.isfile(legacy):
            try:
                os.remove(legacy)
            except Exception:
                pass
        return True
    except Exception:
        return False


# �� QMT-only��Ĭ�Ϲر� xtdata.download_history_*������ miniQMT 58610����
# download ������ YYYYMMDD���������� YYYYMMDD������ 091500-153100��
ENABLE_XTDATA_TICK_DOWNLOAD = False
# �ٷ���ȷ·�������� python download_history_data(code,"tick",YYYYMMDD,YYYYMMDD)
# �� get_market_data_ex(subscribe=False)��subscribe�ٲ���ʷ��
ENABLE_BUILTIN_TICK_DOWNLOAD = True
# ����+builtin download �Կ�ʱ���Ƿ����� subscribe=True��ƫʵʱ/���գ�����ʷ������
ENABLE_CTX_TICK_SUBSCRIBE = True

# ���һ�� tick API ʧ��ԭ������ʧ�ܵȣ��������̽������ empty vs ������
_LAST_TICK_API_ERROR = ""  # type: str
# ģ�ͽ���ע������� download_history_data���� xtdata��
_BUILTIN_DOWNLOAD_HISTORY_DATA = None  # type: Any


def bind_download_history_data(g=None):
    # type: (Optional[Dict[str, Any]]) -> bool
    """�Ӳ��� globals()/builtins ������ download_history_data���� QMT ģ�ͽ��ף���

    �Ѱ���ֱ�ӷ��� True������ timer/reload ��·������д�롣
    """
    global _BUILTIN_DOWNLOAD_HISTORY_DATA
    if callable(_BUILTIN_DOWNLOAD_HISTORY_DATA):
        return True
    sources = []  # type: List[Any]
    if isinstance(g, dict):
        sources.append(g.get("download_history_data"))
    try:
        import builtins

        sources.append(getattr(builtins, "download_history_data", None))
    except Exception:
        builtins = None  # type: ignore
    try:
        import __main__ as main_mod

        sources.append(getattr(main_mod, "download_history_data", None))
    except Exception:
        pass
    for fn in sources:
        if callable(fn):
            _BUILTIN_DOWNLOAD_HISTORY_DATA = fn
            try:
                if builtins is not None:
                    builtins.download_history_data = fn
            except Exception:
                pass
            return True
    return False


def resolve_download_history_data():
    # type: () -> Any
    """�������� download_history_data������ xtdata.download_history_data ������"""
    global _BUILTIN_DOWNLOAD_HISTORY_DATA
    if callable(_BUILTIN_DOWNLOAD_HISTORY_DATA):
        return _BUILTIN_DOWNLOAD_HISTORY_DATA
    if bind_download_history_data(None):
        return _BUILTIN_DOWNLOAD_HISTORY_DATA
    return None


def _tick_time_window(ymd):
    # type: (str) -> Tuple[str, str]
    # ���ݾɵ��ã��������е��̺󴰣�fetch ��ͬʱ�Դ� YYYYMMDD
    return ymd + "091500", ymd + "153100"


def _tick_time_windows(ymd):
    # type: (str) -> List[Tuple[str, str]]
    day = str(ymd or "").replace("-", "").replace("/", "")[:8]
    if len(day) != 8:
        return []
    # �ٷ�ʾ�����ô� YYYYMMDD����ʱ�������ڶ���ѡ
    return [(day, day), (day + "091500", day + "153100")]


def xtdata_service_status(xtdata=None):
    # type: (Any) -> Tuple[bool, str]
    """������ xtquant �ܷ������������� QMT��Ͷ�ж�/miniQMT����

    ���� (ok, detail)��ʧ��ʱ detail �ԡ��޷�����������񡹿�ͷ��
    """
    xt = xtdata
    if xt is None:
        try:
            import xtquant.xtdata as xt  # type: ignore
        except ImportError as e:
            return False, "�޷������������: xtdata δ��װ (%s)" % e
    try:
        client = xt.get_client()
    except Exception as e:
        return False, "�޷������������: %s" % e
    if client is None:
        return False, "�޷������������: get_client()=None���������� QMT Ͷ�жˣ�"
    try:
        ds = date.today().strftime("%Y%m%d")
        xt.get_trading_dates("SH", ds, ds)
    except Exception as e:
        return False, "�޷������������: %s" % e
    return True, "ok"


def _extract_tick_payload(data, full_code):
    # type: (Any, str) -> Optional[Any]
    if data is None:
        return None
    if isinstance(data, dict):
        if full_code in data:
            return data.get(full_code)
        c6 = norm_code6(full_code)
        for k, v in data.items():
            if norm_code6(str(k)) == c6:
                return v
        return None
    return data


def _payload_nrows(payload):
    # type: (Any) -> int
    if payload is None:
        return 0
    try:
        return int(len(payload))
    except Exception:
        return 0


def _call_ctx_local_data(ContextInfo, codes, start_str, end_str):
    # type: (Any, List[str], str, str) -> Optional[Dict[str, Any]]
    """ContextInfo.get_local_data ǩ���� stock_code:str������ stock_list��"""
    fn = getattr(ContextInfo, "get_local_data", None)
    if not callable(fn):
        return None
    out = {}  # type: Dict[str, Any]
    global _LAST_TICK_API_ERROR
    last_err = ""
    for fc in codes:
        raw = None
        attempts = (
            {
                "stock_code": fc,
                "start_time": start_str,
                "end_time": end_str,
                "period": "tick",
                "divid_type": "none",
                "count": -1,
            },
            {
                "stock_code": fc,
                "start_time": start_str,
                "end_time": end_str,
                "period": "tick",
                "count": -1,
            },
        )
        for kwargs in attempts:
            try:
                raw = fn(**kwargs)
                break
            except TypeError:
                continue
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                raw = None
                break
        if raw is None:
            try:
                raw = fn(fc, start_str, end_str, "tick", "none", -1)
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                continue
        if raw is None or qmt_result_empty(raw):
            continue
        if isinstance(raw, dict) and fc in raw:
            out[fc] = raw.get(fc)
        else:
            # ��Ʊ���� {timetag: field_dict}�������� payload
            out[fc] = raw
    if last_err and not out:
        _LAST_TICK_API_ERROR = last_err
    return out if out else None


def _call_tick_api(owner, fn_name, codes, start_str, end_str, subscribe=False):
    # type: (Any, str, List[str], str, str, bool) -> Optional[Any]
    """Call get_market_data_ex / get_local_data with period=tick."""
    if owner is None:
        return None
    code_list = [str(c).strip().upper() for c in (codes or []) if str(c or "").strip()]
    if not code_list:
        return None

    if fn_name == "get_local_data":
        # �Ȱ� ContextInfo ǩ����stock_code:str����ʧ������ xtdata stock_list
        ctx_data = _call_ctx_local_data(owner, code_list, start_str, end_str)
        if ctx_data:
            return ctx_data

    fn = getattr(owner, fn_name, None)
    if not callable(fn):
        return None
    su = getattr(owner, "set_universe", None)
    if callable(su):
        try:
            su(code_list)
        except Exception:
            pass
    fields = []  # type: List[str]
    attempts = []  # type: List[Any]
    if fn_name == "get_market_data_ex":
        attempts = [
            {
                "fields": fields,
                "stock_code": code_list,
                "period": "tick",
                "start_time": start_str,
                "end_time": end_str,
                "count": -1,
                "dividend_type": "none",
                "fill_data": False,
                "subscribe": bool(subscribe),
            },
            {
                "field_list": fields,
                "stock_list": code_list,
                "period": "tick",
                "start_time": start_str,
                "end_time": end_str,
                "count": -1,
                "dividend_type": "none",
                "fill_data": False,
                "subscribe": bool(subscribe),
            },
        ]
    else:
        # xtdata.get_local_data
        attempts = [
            {
                "field_list": fields,
                "stock_list": code_list,
                "period": "tick",
                "start_time": start_str,
                "end_time": end_str,
                "count": -1,
                "dividend_type": "none",
                "fill_data": False,
            },
            {
                "field_list": fields,
                "stock_list": code_list,
                "period": "tick",
                "start_time": start_str,
                "end_time": end_str,
                "count": -1,
            },
        ]
    global _LAST_TICK_API_ERROR
    last_err = ""
    for kwargs in attempts:
        try:
            return fn(**kwargs)
        except TypeError as te:
            if "subscribe" in str(te) and "subscribe" in kwargs:
                kwargs2 = dict(kwargs)
                kwargs2.pop("subscribe", None)
                try:
                    return fn(**kwargs2)
                except Exception as e2:
                    last_err = "%s: %s" % (type(e2).__name__, e2)
            try:
                return fn(fields, code_list, "tick", start_str, end_str, -1)
            except Exception as e3:
                last_err = "%s: %s" % (type(e3).__name__, e3)
                continue
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
            continue
    if last_err:
        _LAST_TICK_API_ERROR = last_err
    return None


def download_ticks_via_builtin(codes, ymd, download_fn=None):
    # type: (List[str], str, Any) -> Tuple[int, str]
    """�� QMT ���� download_history_data ������ tick���� xtdata/58610����

    �ٷ��ĵ���ѸͶ֪ʶ��-���麯����ʾ����
      download_history_data("000001.SZ", "tick", "20260730", "20260730")
    ���������� YYYYMMDD�������ճɹ��� xtdata ����һ�£����ʱ���룩��
    ���� (�ɹ����ô���, ������)��
    """
    fn = download_fn if callable(download_fn) else resolve_download_history_data()
    if not callable(fn):
        return 0, "no_builtin_download_history_data"
    day_s = str(ymd or "").replace("-", "").replace("/", "")[:8]
    if len(day_s) != 8:
        return 0, "bad_ymd"
    ok = 0
    last_err = ""
    for c in codes or []:
        fc = str(c or "").strip().upper()
        if not fc:
            continue
        if "." not in fc:
            fc = full_stock_code(norm_code6(fc))
        try:
            fn(fc, "tick", day_s, day_s)
            ok += 1
        except TypeError:
            try:
                # ���ְ汾֧�� incrementally
                fn(fc, "tick", day_s, day_s, None)
                ok += 1
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
    return ok, last_err


def _absorb_batch(out, remaining, data):
    # type: (Dict[str, Any], List[str], Any) -> List[str]
    if data is None:
        return remaining
    still = []  # type: List[str]
    for fc in remaining:
        if fc in out:
            continue
        payload = _extract_tick_payload(data, fc)
        if payload is not None and not qmt_result_empty(payload):
            out[fc] = payload
        else:
            still.append(fc)
    return still


def fetch_ticks_batch(
    codes,
    trade_date,
    ContextInfo=None,
    xtdata=None,
    allow_subscribe=None,
    allow_builtin_download=None,
):
    # type: (List[str], TradeDateInput, Any, Any, Optional[bool], Optional[bool]) -> Dict[str, Any]
    """���������� tick��

    �ٷ���ȷ˳��ѸͶ֪ʶ�⣩��
    1) ���� get_local_data / get_market_data_ex(subscribe=False)
    2) ���� download_history_data(code,"tick",YYYYMMDD,YYYYMMDD) ������
    3) �� get_market_data_ex(subscribe=False)
    4) ��ѡ subscribe=True��ƫʵʱ��������ʷ������·����
    Ĭ�ϲ��� xtdata.download_history_*��ENABLE_XTDATA_TICK_DOWNLOAD����

    ���� {full_code: raw_df}�������ǿա�
    """
    out = {}  # type: Dict[str, Any]
    if not codes:
        return out
    _, ymd = parse_trade_date(trade_date)
    windows = _tick_time_windows(ymd)
    full_list = []  # type: List[str]
    for c in codes:
        s = str(c or "").strip().upper()
        if not s:
            continue
        if "." not in s:
            s = full_stock_code(norm_code6(s))
        full_list.append(s)
    if not full_list:
        return out

    do_sub = (
        ENABLE_CTX_TICK_SUBSCRIBE
        if allow_subscribe is None
        else bool(allow_subscribe)
    )
    do_builtin = (
        ENABLE_BUILTIN_TICK_DOWNLOAD
        if allow_builtin_download is None
        else bool(allow_builtin_download)
    )

    owners = []  # type: List[Tuple[str, Any]]
    if ContextInfo is not None:
        owners.append(("ctx", ContextInfo))
    if xtdata is not None:
        owners.append(("xt", xtdata))

    remaining = list(full_list)
    global _LAST_TICK_API_ERROR

    def _read_local(rem):
        # type: (List[str]) -> List[str]
        cur = list(rem)
        for _label, owner in owners:
            if not cur:
                break
            for start_str, end_str in windows:
                if not cur:
                    break
                for fn_name in ("get_local_data", "get_market_data_ex"):
                    if not cur:
                        break
                    data = _call_tick_api(
                        owner, fn_name, cur, start_str, end_str, subscribe=False
                    )
                    cur = _absorb_batch(out, cur, data)
        return cur

    remaining = _read_local(remaining)

    # 2) ���� download_history_data �� �ٶ����أ��� QMT ��ȷ����ʷ��ʽ��
    if do_builtin and remaining:
        n_ok, dl_err = download_ticks_via_builtin(remaining, ymd)
        if n_ok > 0:
            time_module.sleep(0.05)
            remaining = _read_local(remaining)
        elif dl_err and not _LAST_TICK_API_ERROR:
            _LAST_TICK_API_ERROR = dl_err

    # 3) subscribe=True ���ף��ٷ������Ƕ��ģ����� supply ��ʷ��
    if do_sub and remaining and ContextInfo is not None:
        for start_str, end_str in windows:
            if not remaining:
                break
            data = _call_tick_api(
                ContextInfo,
                "get_market_data_ex",
                remaining,
                start_str,
                end_str,
                subscribe=True,
            )
            remaining = _absorb_batch(out, remaining, data)
    return out


def fetch_tick_from_qmt(
    code_6,
    trade_date,
    ContextInfo=None,
    xtdata=None,
    allow_xtdata_download=None,
    allow_subscribe=None,
    allow_builtin_download=None,
):
    # type: (str, TradeDateInput, Any, Any, Optional[bool], Optional[bool], Optional[bool]) -> Optional[Any]
    """��Ʊ tick������ �� ���� download_history_data �� �ٶ���xtdata download Ĭ�Ϲء�"""
    c6 = norm_code6(code_6)
    if not c6:
        return None
    _, ymd = parse_trade_date(trade_date)
    full_code = full_stock_code(c6)

    do_dl = (
        ENABLE_XTDATA_TICK_DOWNLOAD
        if allow_xtdata_download is None
        else bool(allow_xtdata_download)
    )

    xt = xtdata
    if xt is None:
        try:
            import xtquant.xtdata as xt  # type: ignore
        except ImportError:
            xt = None

    if do_dl and xt is not None:
        try:
            # ��·��xtdata �� 58610��download �� YYYYMMDD
            xt.download_history_data(full_code, "tick", ymd, ymd)
            time_module.sleep(0.05)
        except Exception:
            pass

    batch = fetch_ticks_batch(
        [full_code],
        trade_date,
        ContextInfo=ContextInfo,
        xtdata=xt,
        allow_subscribe=allow_subscribe,
        allow_builtin_download=allow_builtin_download,
    )
    raw = batch.get(full_code)
    if raw is not None and not qmt_result_empty(raw):
        return raw
    return None


def run_tick_api_probe(ContextInfo, day="20260730", codes=None):
    # type: (Any, str, Optional[List[str]]) -> List[Dict[str, Any]]
    """�ڲ�����̽����� ContextInfo tick ȡ�����壬д��־�����ؽ������

    �÷���ģ�ͽ��ײ��Ժ��� tick_probe ��һ�μ��ɡ�
    """
    day_s = str(day or "").replace("-", "").replace("/", "")[:8]
    if codes is None:
        codes = ["000001.SZ", "600000.SH", "300750.SZ"]
    full_codes = []  # type: List[str]
    for c in codes:
        s = str(c or "").strip().upper()
        if not s:
            continue
        if "." not in s:
            s = full_stock_code(norm_code6(s))
        full_codes.append(s)

    rows = []  # type: List[Dict[str, Any]]
    log_lines = []  # type: List[str]

    def _log(msg):
        # type: (str) -> None
        line = "[tick_probe] %s" % msg
        print(line)
        log_lines.append(line)

    _log(
        "begin day=%s codes=%s io=%s"
        % (day_s, ",".join(full_codes), TICK_CACHE_IO_VERSION)
    )
    bind_ok = bind_download_history_data(None)
    dl_fn = resolve_download_history_data()
    _log("builtin download_history_data bound=%s callable=%s" % (bind_ok, bool(callable(dl_fn))))

    windows = _tick_time_windows(day_s)
    variants = []  # type: List[Tuple[str, str, bool, bool]]
    # name, time_mode, subscribe, do_download_first
    for start_str, end_str in windows:
        tmode = "ymd" if len(start_str) == 8 else "hms"
        variants.append(("local_%s" % tmode, tmode, False, False))
        variants.append(("ex_sub0_%s" % tmode, tmode, False, False))
        variants.append(("ex_sub1_%s" % tmode, tmode, True, False))
    variants.append(("dl_builtin_then_ex_ymd", "ymd", False, True))

    for name, tmode, subscribe, do_dl in variants:
        start_str, end_str = windows[0] if tmode == "ymd" else windows[1]
        for fc in full_codes:
            t0 = time_module.time()
            err = ""
            nrows = 0
            try:
                if do_dl:
                    n_ok, err0 = download_ticks_via_builtin([fc], day_s, dl_fn)
                    if n_ok <= 0 and err0:
                        err = "download:" + err0
                    time_module.sleep(0.05)
                if name.startswith("local_"):
                    data = _call_ctx_local_data(ContextInfo, [fc], start_str, end_str)
                else:
                    data = _call_tick_api(
                        ContextInfo,
                        "get_market_data_ex",
                        [fc],
                        start_str,
                        end_str,
                        subscribe=subscribe,
                    )
                payload = _extract_tick_payload(data, fc) if data is not None else None
                if payload is None and isinstance(data, dict) and fc in (data or {}):
                    payload = data.get(fc)
                nrows = _payload_nrows(payload)
            except Exception as e:
                err = "%s: %s" % (type(e).__name__, e)
            ms = int((time_module.time() - t0) * 1000)
            row = {
                "variant": name,
                "code": fc,
                "start": start_str,
                "end": end_str,
                "nrows": nrows,
                "ms": ms,
                "err": err,
            }
            rows.append(row)
            _log(
                "%s %s rows=%d ms=%d err=%s"
                % (name, fc, nrows, ms, err or "-")
            )

    # ���ܣ��ĸ� variant ������
    hit = [r for r in rows if int(r.get("nrows") or 0) > 0]
    if hit:
        _log("HIT count=%d best=%s" % (len(hit), hit[0].get("variant")))
    else:
        _log("ALL_EMPTY �� �����������/��������Ȩ�ޣ���Ĭ�ϸ��� miniQMT")

    try:
        root = PROJECT_ROOT.rstrip("\\/")
        out_dir = os.path.join(root, "data", "tick_full_sync")
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        path = os.path.join(out_dir, "tick_probe_%s.log" % day_s)
        with open(path, "w") as f:
            f.write("\n".join(log_lines) + "\n")
        _log("wrote %s" % path)
    except Exception as e:
        _log("write log fail: %s" % e)
    return rows


def read_tick_volume_frame(code_6, trade_date):
    # type: (str, TradeDateInput) -> Optional[Any]
    """������ parquet/pkl�����غ� time/volume/amount �� DataFrame�����̺����ܣ���"""
    try:
        import pandas as pd
    except ImportError:
        return None

    c6 = norm_code6(code_6)
    if not c6:
        return None

    pq_path = tick_cache_path(c6, trade_date)
    if os.path.isfile(pq_path) and os.path.getsize(pq_path) > 32:
        try:
            try:
                df = pd.read_parquet(pq_path, columns=["time_ts", "volume", "amount"])
            except Exception:
                df = pd.read_parquet(pq_path)
            if df is None or len(df) == 0:
                return None
            if "time" not in df.columns and "time_ts" in df.columns:
                df = df.rename(columns={"time_ts": "time"})
            need = ("time", "volume", "amount")
            if not all(c in df.columns for c in need):
                return None
            return df.loc[:, list(need)].copy()
        except Exception:
            pass

    pkl_path = tick_cache_path_legacy_pkl(c6, trade_date)
    if os.path.isfile(pkl_path) and os.path.getsize(pkl_path) > 32:
        try:
            df = pd.read_pickle(pkl_path)
            if df is None or len(df) == 0:
                return None
            if "time" not in df.columns and "time_ts" in df.columns:
                df = df.rename(columns={"time_ts": "time"})
            need = ("time", "volume", "amount")
            if not all(c in df.columns for c in need):
                return None
            return df.loc[:, list(need)].copy()
        except Exception:
            pass
    return None
