#coding:utf-8
"""QMT 探针热加载的日线拉取脚本（写入 userdata/qmt_daily_watch/current.py）。

由 tools/run_qmt_daily_cache.py 原子覆盖本文件（内嵌 JOB）+ 同目录 job.json 旁路副本。
探针加载后调用 run_backtest(ContextInfo)。

约束：
- Python 3.6.8；禁止 xtquant.xtdata（58610）
- ContextInfo.get_market_data_ex 用位置参数 fields+stock_code
- QMT 策略内禁止 FileIO（open 读/写均 Forbidden）→ 不读 job.json、不写 jsonl
- K 线经 FormulaOutput：ANT_DAILY|ROW / ANT_DAILY|BATCH；SUMMARY 不依赖 out_path
"""
from __future__ import print_function

import json
import time
import gc
import traceback
from datetime import datetime

RESULT_PREFIX = "ANT_DAILY|"

# 由 tools/run_qmt_daily_cache.py 在投递时替换为真实 JSON 字符串字面量。
JOB = json.loads(__ANT_DAILY_JOB_JSON__)


def _plog(msg):
    line = "[qmt-daily] %s" % msg
    try:
        print(line, flush=True)
    except TypeError:
        try:
            print(line)
        except Exception:
            pass
    except Exception:
        try:
            print(line)
        except Exception:
            pass


def _emit(kind, payload):
    if isinstance(payload, dict) or isinstance(payload, list):
        try:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            body = str(payload)
    else:
        body = str(payload)
    line = "%s%s|%s" % (RESULT_PREFIX, kind, body)
    try:
        print(line, flush=True)
    except TypeError:
        print(line)
    except Exception:
        pass


def _load_job():
    job = JOB
    if not isinstance(job, dict) or not job:
        raise RuntimeError("embedded JOB missing or empty")
    return job


def _code6(full_code):
    s = str(full_code or "")
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _resolve_fn(name):
    try:
        import __main__ as main_mod
        fn = getattr(main_mod, name, None)
        if callable(fn):
            return fn
    except Exception:
        pass
    return None


def _call_download(ContextInfo, full_code, period, start_s, end_s):
    fn = None
    if ContextInfo is not None:
        fn = getattr(ContextInfo, "download_history_data", None)
    if not callable(fn):
        fn = _resolve_fn("download_history_data")
    if not callable(fn):
        return "no_download_history_data"
    try:
        fn(full_code, period, start_s, end_s)
        return ""
    except TypeError:
        try:
            fn(full_code, period, start_s, end_s, None)
            return ""
        except Exception as e:
            return str(e)[:200]
    except Exception as e:
        return str(e)[:200]


def _extract_payload(data, full_code):
    if data is None:
        return None
    if isinstance(data, dict):
        if full_code in data:
            return data.get(full_code)
        c6 = _code6(full_code)
        if c6 in data:
            return data.get(c6)
        for k in list(data.keys()):
            if str(k).upper() == str(full_code).upper():
                return data.get(k)
        if len(data) == 1:
            return list(data.values())[0]
    return data


def _get_market_batch(ContextInfo, stocks, period, start_s, end_s):
    """Batch get_market_data_ex; returns (dict_or_none, err)."""
    if ContextInfo is None:
        return None, "no_ContextInfo"
    fn = getattr(ContextInfo, "get_market_data_ex", None)
    if not callable(fn):
        return None, "no_ctx_get_market_data_ex"
    fields = []
    last_err = ""
    try:
        data = fn(
            fields,
            list(stocks),
            period=period,
            start_time=start_s,
            end_time=end_s,
            count=-1,
            dividend_type="none",
            fill_data=False,
            subscribe=False,
        )
        return data, ""
    except Exception as e:
        last_err = str(e)[:200]
    try:
        data = fn(fields, list(stocks), period, start_s, end_s, -1, "none", False, False)
        return data, ""
    except Exception as e:
        last_err = str(e)[:200]
    if len(stocks) == 1:
        try:
            data = fn(fields, stocks[0], period, start_s, end_s, -1, "none", False, False)
            return {stocks[0]: _extract_payload(data, stocks[0])}, ""
        except Exception as e:
            last_err = str(e)[:200]
    return None, last_err or "get_market_failed"


def _series(payload, *names):
    if payload is None:
        return []
    cols = getattr(payload, "columns", None)
    if cols is not None and hasattr(payload, "__getitem__"):
        for n in names:
            try:
                if n in cols:
                    return list(payload[n].values)
            except Exception:
                pass
        if "time" in names or "stamp" in names:
            try:
                return list(payload.index)
            except Exception:
                pass
        return []
    if isinstance(payload, dict):
        for n in names:
            v = payload.get(n)
            if isinstance(v, (list, tuple)) and len(v) > 0:
                return list(v)
            try:
                if hasattr(v, "tolist"):
                    out = list(v.tolist())
                    if out:
                        return out
            except Exception:
                pass
        return []
    return []


def _as_ymd_list(ts_list):
    out = []
    for t in ts_list:
        try:
            if isinstance(t, datetime):
                out.append(t.strftime("%Y%m%d"))
                continue
            if hasattr(t, "year") and hasattr(t, "month") and hasattr(t, "day") and not hasattr(t, "hour"):
                out.append("%04d%02d%02d" % (t.year, t.month, t.day))
                continue
            try:
                v = float(t.item() if hasattr(t, "item") else t)
            except Exception:
                v = None
            if v is not None and v == v:
                if v >= 1e17:
                    out.append(datetime.fromtimestamp(v / 1e9).strftime("%Y%m%d"))
                    continue
                if v >= 1e14:
                    out.append(datetime.fromtimestamp(v / 1e6).strftime("%Y%m%d"))
                    continue
                if v >= 1e12:
                    out.append(datetime.fromtimestamp(v / 1e3).strftime("%Y%m%d"))
                    continue
                if v >= 1e9:
                    out.append(datetime.fromtimestamp(v).strftime("%Y%m%d"))
                    continue
                if v >= 1e7:
                    out.append(str(int(v))[:8])
                    continue
            s = str(t)
            digits = "".join(ch for ch in s if ch.isdigit())
            if len(digits) >= 8:
                out.append(digits[:8])
                continue
            out.append("")
        except Exception:
            out.append("")
    return out


def _to_float(v, default=None):
    try:
        if v is None:
            return default
        x = float(v)
        if x != x:  # NaN
            return default
        return x
    except Exception:
        return default


def _bar_for_day(payload, want_ymd):
    """从单票 payload 取出 want_ymd 那根 K；返回 dict 或 None。"""
    if payload is None:
        return None
    times = _series(payload, "time", "stamp")
    if not times:
        try:
            times = list(payload.index)
        except Exception:
            times = []
    ymds = _as_ymd_list(times)
    opens = _series(payload, "open", "Open", "openPrice")
    highs = _series(payload, "high", "High", "highPrice")
    lows = _series(payload, "low", "Low", "lowPrice")
    closes = _series(payload, "close", "Close", "lastPrice", "last")
    vols = _series(payload, "volume", "vol", "Volume")
    amts = _series(payload, "amount", "Amount", "turnover")
    n = min(len(ymds), len(opens), len(highs), len(lows), len(closes))
    if n <= 0:
        return None
    hit = None
    for i in range(n):
        if ymds[i] == want_ymd:
            hit = i
            break
    if hit is None:
        return None
    o = _to_float(opens[hit])
    h = _to_float(highs[hit])
    l = _to_float(lows[hit])
    c = _to_float(closes[hit])
    v = _to_float(vols[hit] if hit < len(vols) else None, 0.0)
    a = _to_float(amts[hit] if hit < len(amts) else None, None)
    if o is None or h is None or l is None or c is None:
        return None
    if v is None or v <= 0:
        return None
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "amount": a,
    }


def _chunks(seq, size):
    size = max(1, int(size or 80))
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _flush_rows(run_tag, rows_buf, emit_batch):
    """把累计 K 线打到 FormulaOutput（BATCH 优先，降低日志行数）。"""
    if not rows_buf:
        return
    emit_batch = max(1, int(emit_batch or 40))
    if len(rows_buf) == 1 and emit_batch == 1:
        _emit("ROW", rows_buf[0])
        return
    for i in range(0, len(rows_buf), emit_batch):
        chunk = rows_buf[i : i + emit_batch]
        if len(chunk) == 1 and emit_batch == 1:
            _emit("ROW", chunk[0])
        else:
            _emit("BATCH", {"run_tag": run_tag, "rows": chunk})


def run_backtest(ContextInfo):
    """探针入口：用内嵌 JOB → 批量拉 1d → FormulaOutput ROW/BATCH → SUMMARY。"""
    t0 = time.time()
    job = None
    run_tag = ""
    trade_date = ""
    try:
        job = _load_job()
        run_tag = str(job.get("run_tag") or make_fallback_tag())
        trade_date = str(job.get("trade_date") or "")[:10]
        start_ymd = str(job.get("start_ymd") or trade_date.replace("-", ""))[:8]
        end_ymd = str(job.get("end_ymd") or start_ymd)[:8]
        codes = [str(c).strip().upper() for c in (job.get("codes") or []) if str(c).strip()]
        batch_size = int(job.get("batch_size") or 80)
        download_first = bool(job.get("download_first", True))
        emit_batch = int(job.get("emit_batch") or 40)
        if not trade_date or len(start_ymd) != 8:
            raise RuntimeError("bad trade_date/start_ymd in embedded JOB")
        if not codes:
            raise RuntimeError("empty codes in embedded JOB")

        _emit("START", {
            "run_tag": run_tag,
            "trade_date": trade_date,
            "start_ymd": start_ymd,
            "n_codes": len(codes),
            "batch_size": batch_size,
            "emit_batch": emit_batch,
            "out_path": "",
            "source": "qmt_probe_formula",
        })
        _plog("start run_tag=%s day=%s codes=%d (formula emit, no FileIO)" % (
            run_tag, trade_date, len(codes)
        ))

        ok = 0
        miss = 0
        err_samples = []
        total = len(codes)
        done = 0
        want_ymd = start_ymd
        pending = []

        for batch in _chunks(codes, batch_size):
            if download_first:
                for code in batch:
                    _call_download(ContextInfo, code, "1d", start_ymd, end_ymd)

            data, gerr = _get_market_batch(ContextInfo, batch, "1d", start_ymd, end_ymd)
            if data is None:
                for code in batch:
                    _call_download(ContextInfo, code, "1d", start_ymd, end_ymd)
                data, gerr = _get_market_batch(ContextInfo, batch, "1d", start_ymd, end_ymd)

            for code in batch:
                payload = None
                if isinstance(data, dict):
                    payload = data.get(code)
                    if payload is None:
                        payload = data.get(_code6(code))
                    if payload is None:
                        cu = code.upper()
                        for k in data.keys():
                            if str(k).upper() == cu:
                                payload = data.get(k)
                                break
                elif data is not None and len(batch) == 1:
                    payload = _extract_payload(data, code)

                bar = _bar_for_day(payload, want_ymd)
                if bar is None:
                    _call_download(ContextInfo, code, "1d", start_ymd, end_ymd)
                    one, _e2 = _get_market_batch(ContextInfo, [code], "1d", start_ymd, end_ymd)
                    payload2 = None
                    if isinstance(one, dict):
                        payload2 = one.get(code)
                        if payload2 is None:
                            payload2 = one.get(_code6(code))
                        if payload2 is None and len(one) == 1:
                            payload2 = list(one.values())[0]
                    else:
                        payload2 = _extract_payload(one, code)
                    bar = _bar_for_day(payload2, want_ymd)

                if bar is None:
                    miss += 1
                    if gerr and len(err_samples) < 8:
                        err_samples.append("%s:%s" % (code, gerr[:80]))
                else:
                    rec = {
                        "code": code,
                        "date": trade_date,
                        "open": bar["open"],
                        "high": bar["high"],
                        "low": bar["low"],
                        "close": bar["close"],
                        "volume": bar["volume"],
                        "amount": bar.get("amount"),
                    }
                    pending.append(rec)
                    ok += 1
                    if len(pending) >= emit_batch:
                        _flush_rows(run_tag, pending, emit_batch)
                        pending = []

            done += len(batch)
            if done % (batch_size * 2) < batch_size or done >= total:
                _emit("PROGRESS", {
                    "run_tag": run_tag,
                    "done": done,
                    "total": total,
                    "ok": ok,
                    "miss": miss,
                })
                _plog("progress %d/%d ok=%d miss=%d" % (done, total, ok, miss))
                try:
                    gc.collect()
                except Exception:
                    pass
                time.sleep(0.08)

        if pending:
            _flush_rows(run_tag, pending, emit_batch)
            pending = []

        summary = {
            "run_tag": run_tag,
            "trade_date": trade_date,
            "ok": ok,
            "miss": miss,
            "total": total,
            "out_path": "",
            "elapsed_sec": int(time.time() - t0),
            "error": "",
            "err_samples": err_samples[:5],
            "source": "qmt_probe_formula",
        }
        _emit("SUMMARY", summary)
        _plog("done ok=%d miss=%d (formula rows)" % (ok, miss))
        return summary
    except Exception:
        tb = traceback.format_exc()
        short = tb[-1200:].replace("\n", " | ")
        _emit("ERROR", {"run_tag": run_tag, "error": short, "out_path": ""})
        _emit("SUMMARY", {
            "run_tag": run_tag or "unknown",
            "trade_date": trade_date,
            "ok": ok,
            "miss": miss,
            "total": total,
            "out_path": "",
            "elapsed_sec": int(time.time() - t0),
            "error": short,
            "source": "qmt_probe_formula",
        })
        _plog("error %s" % short)
        return None


def make_fallback_tag():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def init(ContextInfo):
    # 探针优先调 run_backtest；保留 init 兜底
    return run_backtest(ContextInfo)
