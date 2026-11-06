# -*- coding: utf-8 -*-
"""QMT side: one seal-structure job at a time.

Pending list lives in ant_seal_local.py (hot-reload by mtime).
Do not write project disks; emit ANTRES seal only.
"""
import json
import math
import os
import sys
from datetime import datetime

_SEAL_MTIME = 0.0
_SEAL_MOD = None
_DONE = {}
_BUSY = False


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _local_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ant_seal_local.py")


def _load_local():
    global _SEAL_MTIME, _SEAL_MOD
    path = _local_path()
    if not os.path.isfile(path):
        return None
    try:
        mtime = float(os.path.getmtime(path))
    except OSError:
        return None
    if _SEAL_MOD is not None and mtime == _SEAL_MTIME:
        return _SEAL_MOD
    import importlib.util

    mod_name = "ant_seal_local_%d" % int(mtime)
    existing = sys.modules.get(mod_name)
    if existing is not None:
        _SEAL_MOD = existing
        _SEAL_MTIME = mtime
        return existing
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    _SEAL_MOD = mod
    _SEAL_MTIME = mtime
    return mod


def _pending_rows():
    mod = _load_local()
    if mod is None:
        return []
    raw = getattr(mod, "SEALS", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw:
        if isinstance(it, dict):
            out.append(it)
    return out


def _to_float(x):
    if x is None:
        return None
    if isinstance(x, (list, tuple)):
        if not x:
            return None
        return _to_float(x[0])
    if hasattr(x, "__len__") and hasattr(x, "__getitem__") and not isinstance(x, (str, bytes, dict)):
        try:
            if len(x) > 0:
                return _to_float(x[0])
        except Exception:
            pass
    try:
        return float(x)
    except Exception:
        return None


def _pick(row, names):
    for n in names:
        if n in row:
            v = row.get(n)
            if v is not None:
                return v
    return None


def _infer_bidvol_to_shares_multiplier(df):
    if df is None or getattr(df, "empty", True):
        return 100.0
    need_cols = {"amount", "volume", "lastPrice"}
    cols = set(str(c) for c in list(getattr(df, "columns", [])))
    if not need_cols.issubset(cols):
        return 100.0
    try:
        probe = df.tail(400)
        samples = []
        prev_amt = None
        prev_vol = None
        for _, row in probe.iterrows():
            amt = _to_float(row.get("amount"))
            vol = _to_float(row.get("volume"))
            px = _to_float(row.get("lastPrice"))
            if amt is None or vol is None or px is None or px <= 0:
                prev_amt, prev_vol = amt, vol
                continue
            if prev_amt is None or prev_vol is None:
                prev_amt, prev_vol = amt, vol
                continue
            d_amt = amt - prev_amt
            d_vol = vol - prev_vol
            prev_amt, prev_vol = amt, vol
            if d_amt <= 0 or d_vol <= 0:
                continue
            ratio = (d_amt / d_vol) / px
            if ratio > 0:
                samples.append(ratio)
        if not samples:
            return 100.0
        samples.sort()
        mid = samples[len(samples) // 2]
        return 1.0 if abs(mid - 1.0) <= abs(mid - 100.0) else 100.0
    except Exception:
        return 100.0


def _calc_close_order_amount_yi(raw_df):
    if raw_df is None or getattr(raw_df, "empty", True):
        return None
    amount_names = [
        "bidAmount1", "bidAmt1", "buyAmount1", "bid_amount1", "b1_amt",
        "bidAmount", "buyAmount",
    ]
    price_names = [
        "bidPrice1", "bid1", "buyPrice1", "b1_p",
        "bidPrice", "buyPrice",
    ]
    vol_names = [
        "bidVol1", "bidVolume1", "buyVol1", "b1_v", "bid_size1",
        "bidVol", "buyVol", "bidVolume",
    ]
    vol_to_shares = _infer_bidvol_to_shares_multiplier(raw_df)
    probe_df = raw_df.tail(200)
    try:
        iter_rows = list(probe_df.iterrows())
    except Exception:
        return None
    for _, row in reversed(iter_rows):
        amt_f = _to_float(_pick(row, amount_names))
        if amt_f is not None and amt_f > 0:
            return round(amt_f / 1e8, 4)
        p_f = _to_float(_pick(row, price_names))
        v_f = _to_float(_pick(row, vol_names))
        if p_f is None or v_f is None or p_f <= 0 or v_f <= 0:
            continue
        shares = v_f * vol_to_shares
        amt_yuan = p_f * shares
        if amt_yuan > 0:
            return round(amt_yuan / 1e8, 4)
    return None


def _calc_day_turnover_yuan(raw_df):
    if raw_df is None or getattr(raw_df, "empty", True):
        return None
    cols = [str(c) for c in list(getattr(raw_df, "columns", []))]
    cum_cols = ["amount", "totalAmount", "turnover", "accAmount", "cumAmount"]
    per_trade_cols = ["tradeAmount", "lastAmount"]

    def _pick_existing(candidates):
        lower_map = {c.lower(): c for c in cols}
        for name in candidates:
            if name in cols:
                return name
            k = name.lower()
            if k in lower_map:
                return lower_map[k]
        return None

    ccol = _pick_existing(cum_cols)
    if ccol:
        try:
            mx = None
            for v in raw_df[ccol]:
                fv = _to_float(v)
                if fv is None:
                    continue
                if mx is None or fv > mx:
                    mx = fv
            if mx is not None and mx > 0:
                return mx
        except Exception:
            pass
    pcol = _pick_existing(per_trade_cols)
    if pcol:
        try:
            s = 0.0
            ok = False
            for v in raw_df[pcol]:
                fv = _to_float(v)
                if fv is None:
                    continue
                s += fv
                ok = True
            if ok and s > 0:
                return s
        except Exception:
            pass
    return None


def _calc_float_market_cap_yuan(code6, raw_df, ContextInfo=None):
    info = None
    full = code6
    if len(code6) == 6:
        if code6.startswith(("5", "6", "9")):
            full = code6 + ".SH"
        elif code6.startswith(("4", "8")):
            full = code6 + ".BJ"
        else:
            full = code6 + ".SZ"
    # 禁止 xtquant.xtdata：会连 MiniQMT 58610 并弹「非法IP」。只用 ContextInfo 内置接口。
    info = None
    if ContextInfo is not None:
        for name in ("get_instrument_detail", "get_instrumentdetail"):
            fn = getattr(ContextInfo, name, None)
            if callable(fn):
                try:
                    info = fn(full)
                except Exception:
                    info = None
                if isinstance(info, dict):
                    break
    if not isinstance(info, dict):
        return None
    float_volume = _to_float(info.get("FloatVolume"))
    if float_volume is None or float_volume <= 0:
        return None
    px = None
    if raw_df is not None and not getattr(raw_df, "empty", True):
        try:
            for _, row in reversed(list(raw_df.tail(200).iterrows())):
                v = _to_float(row.get("lastPrice"))
                if v is not None and v > 0:
                    px = v
                    break
        except Exception:
            px = None
    if px is None:
        px = _to_float(info.get("PreClose"))
    if px is None or px <= 0:
        return None
    cap = float_volume * px
    return cap if cap > 0 else None


def _calc_order_volatility_coeff(raw_df):
    if raw_df is None or getattr(raw_df, "empty", True):
        return None
    bidvol_fields = ["bidVol", "bidVol1", "bidVolume1", "buyVol1", "b1_v", "bid_size1"]
    cols = set(str(c) for c in list(getattr(raw_df, "columns", [])))
    use_field = None
    for f in bidvol_fields:
        if f in cols:
            use_field = f
            break
    if use_field is None:
        return None
    vols = []
    try:
        for v in raw_df[use_field]:
            fv = _to_float(v)
            if fv is None or fv <= 0:
                continue
            vols.append(fv)
    except Exception:
        return None
    n = len(vols)
    if n < 2:
        return None
    mean_v = sum(vols) / n
    if mean_v <= 0:
        return None
    var = sum((x - mean_v) ** 2 for x in vols) / n
    return (var ** 0.5) / mean_v


def _classify_order_stability(vol_coeff):
    if vol_coeff is None:
        return "", None
    if vol_coeff < 0.40:
        return "stable", 10
    if vol_coeff <= 0.80:
        return "mild", 5
    return "choppy", 0


def _parse_hhmm_from_time_val(v):
    try:
        s = str(int(float(v)))
    except Exception:
        s = str(v)
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 12:
        hh = int(digits[-6:-4])
        mm = int(digits[-4:-2])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh * 100 + mm
    try:
        iv = int(float(v))
        if iv > 1000000000000:
            dt = datetime.fromtimestamp(iv / 1000.0)
        elif iv > 1000000000:
            dt = datetime.fromtimestamp(iv)
        else:
            return None
        return dt.hour * 100 + dt.minute
    except Exception:
        return None


def _calc_order_trend_and_score(raw_df):
    if raw_df is None or getattr(raw_df, "empty", True):
        return "", None
    bidvol_fields = ["bidVol", "bidVol1", "bidVolume1", "buyVol1", "b1_v", "bid_size1"]
    cols = set(str(c) for c in list(getattr(raw_df, "columns", [])))
    use_field = None
    for f in bidvol_fields:
        if f in cols:
            use_field = f
            break
    if use_field is None:
        return "", None
    start_vals = []
    mid_vals = []
    end_vals = []
    try:
        for idx, row in raw_df.iterrows():
            fv = _to_float(row.get(use_field))
            if fv is None or fv <= 0:
                continue
            tval = row.get("time") if "time" in row else idx
            hhmm = _parse_hhmm_from_time_val(tval)
            if hhmm is None:
                continue
            if 930 <= hhmm < 1000:
                start_vals.append(fv)
            elif 1000 <= hhmm < 1430:
                mid_vals.append(fv)
            elif 1430 <= hhmm <= 1500:
                end_vals.append(fv)
    except Exception:
        return "", None
    if not start_vals or not mid_vals or not end_vals:
        all_vals = []
        try:
            for v in raw_df[use_field]:
                fv = _to_float(v)
                if fv is not None and fv > 0:
                    all_vals.append(fv)
        except Exception:
            return "", None
        n = len(all_vals)
        if n < 6:
            return "", None
        a = max(1, n // 6)
        start_vals = all_vals[:a]
        end_vals = all_vals[-a:]
        mid_vals = all_vals[a:n - a] if (n - 2 * a) > 0 else all_vals[a:2 * a]
    if not start_vals or not mid_vals or not end_vals:
        return "", None
    f_start = sum(start_vals) / len(start_vals)
    f_mid = sum(mid_vals) / len(mid_vals)
    f_end = sum(end_vals) / len(end_vals)
    if f_start <= 0 or f_mid <= 0:
        return "", None
    chg_mid = (f_mid - f_start) / f_start * 100.0
    chg_end = (f_end - f_mid) / f_mid * 100.0
    if chg_mid >= 5.0 and chg_end >= 5.0:
        return "up", 10
    if chg_mid <= -5.0 and chg_end <= -5.0:
        return "down", 0
    if 0.0 <= chg_mid < 5.0 and 0.0 <= chg_end < 5.0:
        return "mild_up", 7
    if -5.0 < chg_mid <= 0.0 and -5.0 < chg_end <= 0.0:
        return "mild_down", 3
    if -5.0 <= chg_mid <= 5.0 and -5.0 <= chg_end <= 5.0:
        return "flat", 5
    return "flat", 5


def _score_seal_hardness(seal_ratio):
    if seal_ratio is None or seal_ratio <= 0:
        return 0
    if seal_ratio < 0.03:
        return 0
    if seal_ratio <= 0.08:
        return 15
    if seal_ratio <= 0.15:
        return 35
    return 50


def _score_rush_intensity(rush_ratio):
    if rush_ratio is None or rush_ratio <= 0:
        return 0
    if rush_ratio < 0.002:
        return 0
    if rush_ratio <= 0.005:
        return 15
    return 30


def _score_close_order_amount(close_amt_yi):
    if close_amt_yi is None or close_amt_yi <= 0:
        return 0
    if close_amt_yi < 0.3:
        return 0
    if close_amt_yi <= 1.0:
        return 10
    return 20


def _normalize_df(raw_df):
    if raw_df is None:
        return None
    try:
        import ant_tick_cache_io as tio
    except ImportError:
        try:
            import qmt_builtin.ant_tick_cache_io as tio
        except ImportError:
            tio = None
    if tio is not None:
        try:
            if hasattr(tio, "flatten_depth_columns"):
                raw_df = tio.flatten_depth_columns(raw_df)
            if hasattr(tio, "normalize_tick_dataframe"):
                return tio.normalize_tick_dataframe(raw_df)
            if hasattr(tio, "coerce_tick_dataframe"):
                return tio.coerce_tick_dataframe(raw_df)
        except Exception:
            pass
    return raw_df


def _fetch_tick_df(code6, day, ContextInfo=None):
    try:
        import ant_tick_cache_io as tio
    except ImportError:
        try:
            import qmt_builtin.ant_tick_cache_io as tio
        except ImportError:
            tio = None
    if tio is not None and hasattr(tio, "fetch_tick_from_qmt"):
        try:
            # memory only; never write_tick_cache
            raw = tio.fetch_tick_from_qmt(
                code6,
                day,
                ContextInfo=ContextInfo,
                allow_xtdata_download=False,
                allow_subscribe=False,
                allow_builtin_download=True,
            )
            return _normalize_df(raw)
        except Exception as exc:
            print("[seal] fetch_tick_from_qmt err: %s" % exc)
    if ContextInfo is None:
        return None
    full = code6
    if "." not in code6:
        if code6.startswith(("5", "6", "9")):
            full = code6 + ".SH"
        elif code6.startswith(("4", "8")):
            full = code6 + ".BJ"
        else:
            full = code6 + ".SZ"
    start = day + "093000"
    end = day + "150100"
    for fn_name in ("get_local_data", "get_market_data_ex"):
        fn = getattr(ContextInfo, fn_name, None)
        if not callable(fn):
            continue
        try:
            data = fn(
                field=[],
                stock_code=[full],
                period="tick",
                start_time=start,
                end_time=end,
                count=-1,
                dividend_type="none",
                fill_data=False,
                subscribe=False,
            )
        except TypeError:
            try:
                data = fn([full], "tick", start, end)
            except Exception:
                continue
        except Exception:
            continue
        if isinstance(data, dict):
            raw = data.get(full) or data.get(code6)
        else:
            raw = data
        if raw is not None and not getattr(raw, "empty", True):
            return _normalize_df(raw)
    return None


def _emit(row):
    try:
        import ant_result_log as rl
    except ImportError:
        try:
            import qmt_builtin.ant_result_log as rl
        except ImportError:
            rl = None
    if rl is not None and hasattr(rl, "emit_seal"):
        try:
            rl.emit_seal(row)
            return
        except Exception:
            pass
    try:
        print("ANTRES seal " + json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        pass


def _round_or_none(v, nd=4):
    if v is None:
        return None
    try:
        fv = float(v)
    except Exception:
        return None
    if not math.isfinite(fv):
        return None
    return round(fv, nd)


def analyze_one(code6, day, ContextInfo=None):
    """Return metrics dict (no id/st)."""
    df = _fetch_tick_df(code6, day, ContextInfo=ContextInfo)
    n = 0
    try:
        n = int(len(df)) if df is not None else 0
    except Exception:
        n = 0
    if df is None or n <= 0:
        return {"ok": False, "msg": "no_tick", "n": 0}
    close_amt_yi = _calc_close_order_amount_yi(df)
    day_turnover = _calc_day_turnover_yuan(df)
    float_cap = _calc_float_market_cap_yuan(code6, df, ContextInfo=ContextInfo)
    seal_ratio = None
    if close_amt_yi is not None and day_turnover is not None and day_turnover > 0:
        seal_ratio = (close_amt_yi * 1e8) / day_turnover
    rush_ratio = None
    if close_amt_yi is not None and float_cap is not None and float_cap > 0:
        rush_ratio = (close_amt_yi * 1e8) / float_cap
    vol_coeff = _calc_order_volatility_coeff(df)
    stab_label, stab_score = _classify_order_stability(vol_coeff)
    trend_label, trend_score = _calc_order_trend_and_score(df)
    return {
        "ok": True,
        "msg": "",
        "n": n,
        "ca": _round_or_none(close_amt_yi, 4),
        "ty": _round_or_none(day_turnover, 2),
        "sr": _round_or_none(seal_ratio, 6),
        "rr": _round_or_none(rush_ratio, 6),
        "vc": _round_or_none(vol_coeff, 4),
        "ts": int(trend_score or 0),
        "tl": str(trend_label or "")[:12],
        "sh": int(_score_seal_hardness(seal_ratio)),
        "ri": int(_score_rush_intensity(rush_ratio)),
        "sa": int(_score_close_order_amount(close_amt_yi)),
        "ss": int(stab_score or 0),
        "sl": str(stab_label or "")[:12],
    }


def process_pending_seals(ContextInfo=None, max_items=1):
    """Process at most max_items pending seal jobs. Safe under live-only.

    Explicitly allowed on live-only machines: this is after-hours analysis,
    not the paused full-A tick dump. Does not write project disks.
    """
    global _BUSY
    if _BUSY:
        return 0
    rows = _pending_rows()
    if not rows:
        return 0
    done_n = 0
    limit = max(1, int(max_items or 1))
    _BUSY = True
    try:
        for it in rows:
            if done_n >= limit:
                break
            rid = str(it.get("id") or "").strip()
            day = str(it.get("d") or "").replace("-", "").replace("/", "")[:8]
            code = str(it.get("c") or "").strip().upper()
            if code.endswith((".SH", ".SZ", ".BJ")):
                code = code.split(".", 1)[0]
            code = "".join(ch for ch in code if ch.isdigit())[:6]
            if code:
                code = code.zfill(6)
            if not rid:
                continue
            if _DONE.get(rid):
                continue
            at = _now_iso()
            if len(day) != 8 or len(code) != 6:
                _DONE[rid] = "error"
                _emit({"id": rid[:16], "d": day, "c": code, "st": "error", "msg": "bad_args", "at": at})
                done_n += 1
                continue
            try:
                metrics = analyze_one(code, day, ContextInfo=ContextInfo)
            except Exception as exc:
                metrics = {"ok": False, "msg": "%s:%s" % (type(exc).__name__, exc), "n": 0}
            row = {"id": rid[:16], "d": day, "c": code, "at": at}
            if metrics.get("ok"):
                row["st"] = "ok"
                for k in ("ca", "ty", "sr", "rr", "vc", "ts", "tl", "sh", "ri", "sa", "ss", "sl", "n"):
                    if k in metrics and metrics.get(k) is not None and metrics.get(k) != "":
                        row[k] = metrics.get(k)
            else:
                row["st"] = "error"
                row["msg"] = str(metrics.get("msg") or "fail")[:80]
                if metrics.get("n") is not None:
                    row["n"] = int(metrics.get("n") or 0)
            _DONE[rid] = row["st"]
            _emit(row)
            done_n += 1
            print("[seal] %s %s %s st=%s n=%s" % (day, code, rid[:8], row.get("st"), row.get("n")))
    finally:
        _BUSY = False
    return done_n
