#coding:utf-8
# NOTE: QMT python\探针.py is now the MZ1 fine-backtest hot-reload watcher (see tools/mz1_qmt_fine_probe.py).
# This file remains the one-shot tick-depth probe; backups under data/probe_tick_history_depth/.
"""QMT builtin probe: download_history_data(period=tick) history depth.

Usage: 模型交易 -> new strategy -> paste this file -> save -> live run.
Results primarily via print into userdata/log/XtClient_FormulaOutput_YYYYMMDD.log.
File writes prefer QMT userdata (project dir often hits Forbidden FileIO).
"""
from __future__ import print_function

import csv
import io
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta

# ===== config =====
CODES = ["000001.SZ", "600000.SH"]
CALENDAR_DAYS = 90
ALSO_1M = False
ALSO_1D_SANITY = True
USE_SUBSCRIBE = False  # subscribe=True needs _socket; local history should not
SLEEP_SEC = 0.15
MAX_EMPTY_STREAK_ABORT = 0
# ====================


def _ensure_unicodedata():
    """Guojin QMT 2.1 python often misses unicodedata; stub before API calls."""
    try:
        import unicodedata  # noqa: F401
        return "ok"
    except Exception:
        pass
    import sys
    import types

    m = types.ModuleType("unicodedata")

    def normalize(form, unistr):
        return unistr

    def category(c):
        return "Lo"

    def name(chr, default=None):
        if default is not None:
            return default
        raise ValueError("no name")

    def decimal(chr, default=None):
        if default is not None:
            return default
        raise ValueError("no decimal")

    def digit(chr, default=None):
        if default is not None:
            return default
        raise ValueError("no digit")

    def numeric(chr, default=None):
        if default is not None:
            return default
        raise ValueError("no numeric")

    def combining(chr):
        return 0

    def bidirectional(chr):
        return ""

    def east_asian_width(chr):
        return "N"

    def mirrored(chr):
        return False

    def decomposition(chr):
        return ""

    m.normalize = normalize
    m.category = category
    m.name = name
    m.decimal = decimal
    m.digit = digit
    m.numeric = numeric
    m.combining = combining
    m.bidirectional = bidirectional
    m.east_asian_width = east_asian_width
    m.mirrored = mirrored
    m.decomposition = decomposition
    m.unidata_version = "9.0.0"
    m.ucd_3_2_0 = m
    sys.modules["unicodedata"] = m
    return "stub"


_UNICODEDATA_STATUS = _ensure_unicodedata()


def _datadir_roots():
    roots = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        here = ""
    if here:
        parent = os.path.dirname(here)
        roots.append(os.path.join(parent, "datadir"))
        roots.append(os.path.join(os.path.dirname(parent), "datadir"))
    roots.append(r"D:\国金证券QMT交易端2\datadir")
    seen = set()
    out = []
    for r in roots:
        if r and r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def _file_tick_exists(full_code, ymd):
    """Cross-check QMT local tick file: datadir/{SZ|SH}/0/{code6}/{ymd}.dat"""
    parts = str(full_code or "").upper().split(".")
    if len(parts) != 2:
        return False, ""
    code6, market = parts[0].zfill(6), parts[1]
    if market not in ("SZ", "SH"):
        return False, ""
    for root in _datadir_roots():
        path = os.path.join(root, market, "0", code6, ymd + ".dat")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return True, path
    return False, ""


def _file_1d_exists(full_code, ymd):
    parts = str(full_code or "").upper().split(".")
    if len(parts) != 2:
        return False, ""
    code6, market = parts[0].zfill(6), parts[1]
    if market not in ("SZ", "SH"):
        return False, ""
    for root in _datadir_roots():
        # daily is usually one big DAT; presence of day inside unknown — just note file
        path = os.path.join(root, market, "86400", code6 + ".DAT")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return True, path
    return False, ""




def _now_tag():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ymd(d):
    return d.strftime("%Y%m%d")


def _candidate_out_dirs():
    dirs = []
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        here = ""
    if here:
        parent = os.path.dirname(here)
        dirs.append(os.path.join(here, "probe_tick_history_depth"))
        dirs.append(os.path.join(parent, "userdata", "probe_tick_history_depth"))
        dirs.append(os.path.join(parent, "userdata", "log", "probe_tick_history_depth"))
    dirs.append(r"D:\蚂蚁量化系统\data\probe_tick_history_depth")
    seen = set()
    out = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _pick_out_dir():
    last_err = ""
    for d in _candidate_out_dirs():
        try:
            if not os.path.isdir(d):
                os.makedirs(d)
            probe = os.path.join(d, "_write_probe.tmp")
            with open(probe, "w") as f:
                f.write("ok")
            try:
                os.remove(probe)
            except Exception:
                pass
            return d, ""
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
            continue
    return None, last_err or "no_writable_outdir"


def _safe_write_text(path, text, mode="w"):
    if not path:
        return False
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        try:
            with open(path, mode, encoding="utf-8") as f:
                f.write(text)
            return True
        except TypeError:
            with open(path, mode) as f:
                f.write(text)
            return True
    except Exception:
        return False


def _safe_write_json(path, obj):
    try:
        return _safe_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))
    except Exception:
        return False


def _log(msg, log_path=None):
    line = "[qmt-probe] %s" % msg
    try:
        print(line)
    except Exception:
        pass
    if log_path:
        _safe_write_text(log_path, line + "\n", mode="a")


def _resolve_fn(name):
    if name in globals() and callable(globals().get(name)):
        return globals().get(name)
    try:
        import builtins
    except ImportError:
        builtins = None
    if builtins is not None:
        fn = getattr(builtins, name, None)
        if callable(fn):
            return fn
    try:
        import __main__ as main_mod

        fn = getattr(main_mod, name, None)
        if callable(fn):
            return fn
    except Exception:
        pass
    return None


def _payload_nrows(payload):
    if payload is None:
        return 0
    try:
        return int(len(payload))
    except Exception:
        return 0


def _extract_payload(data, full_code):
    if data is None:
        return None
    if isinstance(data, dict):
        if full_code in data:
            return data.get(full_code)
        c6 = full_code.split(".")[0]
        for k, v in data.items():
            if str(k).split(".")[0].zfill(6) == c6.zfill(6):
                return v
        return None
    return data


def _ts_list_to_yyyymmdd(ts_list):
    out = []
    for ts in ts_list or []:
        if isinstance(ts, (int, float)):
            v = float(ts)
            if v > 1e12:
                v = v / 1000.0
            out.append(datetime.fromtimestamp(v).strftime("%Y%m%d"))
        elif isinstance(ts, str):
            s = ts.replace("-", "").replace("/", "")[:8]
            if len(s) == 8 and s.isdigit():
                out.append(s)
        else:
            s = str(ts).replace("-", "")[:8]
            if len(s) == 8 and s.isdigit():
                out.append(s)
    return sorted(set([d for d in out if d]))


def _trading_days(start, end, log_path):
    start_s, end_s = _ymd(start), _ymd(end)
    fn = _resolve_fn("get_trading_dates")
    if callable(fn):
        for args in (("SH", start_s, end_s), ("SH",)):
            try:
                if len(args) == 3:
                    raw = fn(args[0], start_time=start_s, end_time=end_s)
                else:
                    raw = fn("SH", start_s, end_s)
                days = _ts_list_to_yyyymmdd(raw)
                if days:
                    _log("trading_dates via get_trading_dates n=%d" % len(days), log_path)
                    return days
            except TypeError:
                try:
                    raw = fn("SH", start_s, end_s)
                    days = _ts_list_to_yyyymmdd(raw)
                    if days:
                        _log("trading_dates via get_trading_dates/pos n=%d" % len(days), log_path)
                        return days
                except Exception as e:
                    _log("get_trading_dates err: %s" % e, log_path)
            except Exception as e:
                _log("get_trading_dates err: %s" % e, log_path)
    out = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(_ymd(cur))
        cur += timedelta(days=1)
    _log("trading_dates fallback weekdays n=%d" % len(out), log_path)
    return out


def _call_ctx_ex(ContextInfo, full_code, period, start_s, end_s, subscribe):
    fn = getattr(ContextInfo, "get_market_data_ex", None) if ContextInfo is not None else None
    if not callable(fn):
        return None, "no_ctx_get_market_data_ex"
    su = getattr(ContextInfo, "set_universe", None)
    if callable(su):
        try:
            su([full_code])
        except Exception:
            pass
    attempts = [
        dict(
            fields=[],
            stock_code=[full_code],
            period=period,
            start_time=start_s,
            end_time=end_s,
            count=-1,
            dividend_type="none",
            fill_data=False,
            subscribe=bool(subscribe),
        ),
        dict(
            field_list=[],
            stock_list=[full_code],
            period=period,
            start_time=start_s,
            end_time=end_s,
            count=-1,
            dividend_type="none",
            fill_data=False,
            subscribe=bool(subscribe),
        ),
    ]
    last_err = ""
    for kwargs in attempts:
        try:
            return fn(**kwargs), ""
        except TypeError as te:
            last_err = "TypeError: %s" % te
            if "subscribe" in kwargs:
                kwargs2 = dict(kwargs)
                kwargs2.pop("subscribe", None)
                try:
                    return fn(**kwargs2), ""
                except Exception as e2:
                    last_err = "%s: %s" % (type(e2).__name__, e2)
            try:
                return fn([], [full_code], period, start_s, end_s, -1), ""
            except Exception as e3:
                last_err = "%s: %s" % (type(e3).__name__, e3)
                continue
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
            continue
    return None, last_err


def _call_ctx_local(ContextInfo, full_code, period, start_s, end_s):
    fn = getattr(ContextInfo, "get_local_data", None) if ContextInfo is not None else None
    if not callable(fn):
        return None, "no_ctx_get_local_data"
    attempts = [
        dict(
            stock_code=full_code,
            start_time=start_s,
            end_time=end_s,
            period=period,
            divid_type="none",
            count=-1,
        ),
        dict(
            stock_code=full_code,
            start_time=start_s,
            end_time=end_s,
            period=period,
            count=-1,
        ),
    ]
    last_err = ""
    for kwargs in attempts:
        try:
            return fn(**kwargs), ""
        except TypeError as te:
            last_err = "TypeError: %s" % te
            continue
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
            break
    try:
        return fn(full_code, start_s, end_s, period, "none", -1), ""
    except Exception as e:
        last_err = "%s: %s" % (type(e).__name__, e)
    return None, last_err


def _read_local(full_code, period, ymd, ContextInfo, log_path):
    windows = [
        (ymd, ymd),
        (ymd + "091500", ymd + "153100"),
        (ymd + "000000", ymd + "235959"),
    ]
    errs = []
    for start_s, end_s in windows:
        raw, err = _call_ctx_ex(ContextInfo, full_code, period, start_s, end_s, False)
        if err:
            errs.append("ex0:%s" % err)
        elif raw is None:
            errs.append("ex0:None")
        payload = _extract_payload(raw, full_code)
        n = _payload_nrows(payload)
        if n > 0:
            return n, ""
        if raw is not None and n == 0:
            errs.append("ex0:empty")
        raw2, err2 = _call_ctx_local(ContextInfo, full_code, period, start_s, end_s)
        if err2:
            errs.append("local:%s" % err2)
        payload2 = _extract_payload(raw2, full_code)
        n2 = _payload_nrows(payload2)
        if n2 > 0:
            return n2, ""
        if raw2 is not None and n2 == 0:
            errs.append("local:empty")
        if USE_SUBSCRIBE:
            raw3, err3 = _call_ctx_ex(ContextInfo, full_code, period, start_s, end_s, True)
            if err3:
                errs.append("ex1:%s" % err3)
            payload3 = _extract_payload(raw3, full_code)
            n3 = _payload_nrows(payload3)
            if n3 > 0:
                return n3, ""
    # keep short
    last = "|".join(errs[-4:]) if errs else ""
    return 0, last[:160]


def _download_one(full_code, period, ymd, log_path):
    fn = _resolve_fn("download_history_data")
    if not callable(fn):
        return "no_download_history_data"
    variants = [
        (full_code, period, ymd, ymd),
        (full_code, period, ymd + "000000", ymd + "235959"),
    ]
    last_err = ""
    for args in variants:
        try:
            fn(*args)
            return ""
        except TypeError:
            try:
                fn(args[0], args[1], args[2], args[3], None)
                return ""
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
    return last_err or "download_failed"


def _probe_one(full_code, period, ymd, ContextInfo, log_path):
    before, before_err = _read_local(full_code, period, ymd, ContextInfo, log_path)
    dl_err = _download_one(full_code, period, ymd, log_path)
    if SLEEP_SEC > 0:
        time.sleep(SLEEP_SEC)
    after, after_err = _read_local(full_code, period, ymd, ContextInfo, log_path)
    if period == "tick":
        file_ok, file_path = _file_tick_exists(full_code, ymd)
    elif period == "1d":
        file_ok, file_path = _file_1d_exists(full_code, ymd)
    else:
        file_ok, file_path = False, ""
    api_ok = bool(after > 0)
    return {
        "code": full_code,
        "period": period,
        "day": ymd,
        "rows_before_download": before,
        "rows_after_download": after,
        "download_error": dl_err,
        "read_error_before": before_err,
        "read_error_after": after_err,
        "file_ok": bool(file_ok),
        "file_path": file_path,
        "ok": bool(api_ok or file_ok),
        "api_ok": api_ok,
    }


def _summarize(rows, period, code):
    subset = [r for r in rows if r["period"] == period and r["code"] == code]
    subset_sorted = sorted(subset, key=lambda r: r["day"], reverse=True)
    ok_days = [r["day"] for r in subset_sorted if r.get("ok")]
    empty_days = [r["day"] for r in subset_sorted if not r.get("ok")]
    first_empty_after_ok = None
    seen_ok = False
    for r in subset_sorted:
        if r.get("ok"):
            seen_ok = True
            continue
        if seen_ok and first_empty_after_ok is None:
            first_empty_after_ok = r["day"]
            break
    return {
        "code": code,
        "period": period,
        "probed_days": len(subset),
        "ok_days": len(ok_days),
        "empty_days": len(empty_days),
        "newest_ok": max(ok_days) if ok_days else None,
        "farthest_ok": min(ok_days) if ok_days else None,
        "first_empty_going_older": first_empty_after_ok,
        "newest_empty": max(empty_days) if empty_days else None,
    }


def _run_probe(ContextInfo):
    out_dir, out_err = _pick_out_dir()
    tag = _now_tag()
    log_path = os.path.join(out_dir, "run_qmt_%s.log" % tag) if out_dir else None
    status_path = os.path.join(out_dir, "_qmt_probe_status.json") if out_dir else None

    _log("start python=%s" % sys.version.replace("\n", " "), log_path)
    _log("unicodedata=%s" % _UNICODEDATA_STATUS, log_path)
    try:
        import unicodedata as _ud

        _log("unicodedata_file=%s" % getattr(_ud, "__file__", "builtin-or-stub"), log_path)
    except Exception as e:
        _log("unicodedata_import_err=%s" % e, log_path)
    _log("out_dir=%s err=%s" % (out_dir or "", out_err), log_path)
    _log(
        "codes=%s calendar_days=%s also_1m=%s also_1d=%s"
        % (CODES, CALENDAR_DAYS, ALSO_1M, ALSO_1D_SANITY),
        log_path,
    )

    if status_path:
        _safe_write_json(
            status_path,
            {"status": "running", "started_at": tag, "log": log_path, "out_dir": out_dir},
        )

    dl = _resolve_fn("download_history_data")
    ex = _resolve_fn("get_market_data_ex")
    _log("has download_history_data=%s" % bool(callable(dl)), log_path)
    _log("has get_market_data_ex=%s" % bool(callable(ex)), log_path)
    _log(
        "ctx get_market_data_ex=%s get_local_data=%s"
        % (
            callable(getattr(ContextInfo, "get_market_data_ex", None)),
            callable(getattr(ContextInfo, "get_local_data", None)),
        ),
        log_path,
    )

    if not callable(dl):
        _log("FATAL: no download_history_data", log_path)
        if status_path:
            _safe_write_json(
                status_path,
                {"status": "fatal", "reason": "no_download_history_data", "log": log_path},
            )
        return

    end_d = date.today()
    start_d = end_d - timedelta(days=max(1, int(CALENDAR_DAYS)))
    days = _trading_days(start_d, end_d, log_path)

    periods = []
    if ALSO_1D_SANITY:
        periods.append("1d")
    periods.append("tick")
    if ALSO_1M:
        periods.append("1m")

    rows = []
    days_1d = list(reversed(days))[:5]
    days_tick = list(reversed(days))

    plan = []
    for period in periods:
        use_days = days_1d if period == "1d" else days_tick
        for ymd in use_days:
            for code in CODES:
                plan.append((ymd, code, period))

    total = len(plan)
    done = 0
    empty_streak = 0
    t0 = time.time()
    for ymd, code, period in plan:
        done += 1
        _log("(%d/%d) %s %s %s ..." % (done, total, ymd, code, period), log_path)
        try:
            rec = _probe_one(code, period, ymd, ContextInfo, log_path)
        except Exception:
            rec = {
                "code": code,
                "period": period,
                "day": ymd,
                "rows_before_download": 0,
                "rows_after_download": 0,
                "download_error": "exception",
                "read_error_before": "",
                "read_error_after": traceback.format_exc().replace("\n", " | ")[:300],
                "ok": False,
            }
        rows.append(rec)
        flag = "OK" if rec.get("ok") else "EMPTY"
        _log(
            "         -> after=%d %s api=%s file=%s dl=%s read=%s"
            % (
                int(rec.get("rows_after_download") or 0),
                flag,
                1 if rec.get("api_ok") else 0,
                1 if rec.get("file_ok") else 0,
                (rec.get("download_error") or "")[:60],
                (rec.get("read_error_after") or "")[:100],
            ),
            log_path,
        )
        try:
            slim = {
                "c": rec.get("code"),
                "p": rec.get("period"),
                "d": rec.get("day"),
                "b": rec.get("rows_before_download"),
                "a": rec.get("rows_after_download"),
                "ok": bool(rec.get("ok")),
                "api": bool(rec.get("api_ok")),
                "file": bool(rec.get("file_ok")),
                "de": rec.get("download_error") or "",
                "re": (rec.get("read_error_after") or "")[:80],
            }
            _log("ROW %s" % json.dumps(slim, ensure_ascii=False), log_path)
        except Exception:
            pass

        if rec.get("ok"):
            empty_streak = 0
        else:
            empty_streak += 1
            if MAX_EMPTY_STREAK_ABORT and empty_streak >= MAX_EMPTY_STREAK_ABORT:
                _log("abort: empty_streak=%d" % empty_streak, log_path)
                break

        if status_path and done % 5 == 0:
            _safe_write_json(
                status_path,
                {
                    "status": "running",
                    "done": done,
                    "total": total,
                    "elapsed_sec": round(time.time() - t0, 1),
                    "log": log_path,
                    "out_dir": out_dir,
                },
            )

    summaries = []
    for period in periods:
        for code in CODES:
            summaries.append(_summarize(rows, period, code))

    _log("=== summary ===", log_path)
    for s in summaries:
        _log(
            "%s %s probed=%s ok=%s farthest_ok=%s newest_ok=%s"
            % (
                s.get("code"),
                s.get("period"),
                s.get("probed_days"),
                s.get("ok_days"),
                s.get("farthest_ok"),
                s.get("newest_ok"),
            ),
            log_path,
        )

    json_path = csv_path = None
    if out_dir:
        json_path = os.path.join(out_dir, "probe_qmt_%s.json" % tag)
        csv_path = os.path.join(out_dir, "probe_qmt_%s.csv" % tag)
        _safe_write_json(
            json_path,
            {"started_at": tag, "out_dir": out_dir, "rows": rows, "summaries": summaries},
        )
        try:
            buf = io.StringIO()
            fieldnames = [
                "code",
                "period",
                "day",
                "rows_before_download",
                "rows_after_download",
                "download_error",
                "read_error_before",
                "read_error_after",
                "ok",
            ]
            w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
            _safe_write_text(csv_path, "\ufeff" + buf.getvalue())
        except Exception as e:
            _log("csv write err: %s" % e, log_path)

    _log("json=%s" % (json_path or ""), log_path)
    _log("csv=%s" % (csv_path or ""), log_path)
    _log("DONE elapsed=%.1fs" % (time.time() - t0), log_path)
    if status_path:
        _safe_write_json(
            status_path,
            {
                "status": "done",
                "json": json_path,
                "csv": csv_path,
                "log": log_path,
                "out_dir": out_dir,
                "summaries": summaries,
            },
        )


def init(ContextInfo):
    try:
        try:
            ContextInfo.set_universe(list(CODES))
        except Exception:
            pass
        _run_probe(ContextInfo)
    except Exception:
        msg = traceback.format_exc().replace("\n", " | ")
        try:
            print("[qmt-probe] FATAL %s" % msg[:500])
        except Exception:
            pass
        out_dir, _ = _pick_out_dir()
        if out_dir:
            err_path = os.path.join(out_dir, "crash_qmt_%s.log" % _now_tag())
            _safe_write_text(err_path, traceback.format_exc())
            try:
                print("[qmt-probe] FATAL see %s" % err_path)
            except Exception:
                pass


def handlebar(ContextInfo):
    return
