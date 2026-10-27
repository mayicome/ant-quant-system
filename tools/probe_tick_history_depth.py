# -*- coding: utf-8 -*-
"""探测 QMT download_history_data(period=tick) 服务端可下历史深度。

前提：国金 QMT 已打开并登录。
2.1 解释器是 bin.x64\\pythonw.exe（Python 3.6），本脚本按 3.6 语法编写。

用法（在项目根目录）:
  D:\\国金证券QMT交易端2\\bin.x64\\pythonw.exe tools\\probe_tick_history_depth.py
  D:\\国金证券QMT交易端2\\bin.x64\\pythonw.exe tools\\probe_tick_history_depth.py --also-1m
  D:\\国金证券QMT交易端2\\bin.x64\\pythonw.exe tools\\probe_tick_history_depth.py --dry-run

流程:
  1) 先 get_market_data_ex(subscribe=False) 看本地
  2) download_history_data(code, period, YYYYMMDD, YYYYMMDD)
  3) 再 get_market_data_ex(subscribe=False) 统计行数

输出:
  data/probe_tick_history_depth/run_*.log
  data/probe_tick_history_depth/probe_*.json
  data/probe_tick_history_depth/probe_*.csv
"""
from __future__ import print_function

import argparse
import configparser
import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

# typing 仅作注释兼容；3.6 不依赖 future annotations
try:
    from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: F401
except ImportError:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DEFAULT_CODES = ("000001.SZ", "600000.SH")
DEFAULT_CALENDAR_DAYS = 90
DEFAULT_SLEEP = 0.08


def _now_tag():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ymd(d):
    return d.strftime("%Y%m%d")


def _full_code(code):
    s = str(code or "").strip().upper()
    if not s:
        return ""
    if "." in s:
        num, mkt = s.split(".", 1)
        return "%s.%s" % (num.zfill(6)[:6], mkt)
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    c6 = digits.zfill(6)[:6]
    if c6.startswith(("5", "6", "9")):
        return c6 + ".SH"
    if c6.startswith(("4", "8")) or c6.startswith("920"):
        return c6 + ".BJ"
    return c6 + ".SZ"


def _ensure_qmt_python_on_path():
    cfg_path = os.path.join(_ROOT, "data", "config.ini")
    qmt_py = ""
    try:
        cp = configparser.ConfigParser()
        cp.read(cfg_path, encoding="utf-8")
        qmt_py = (cp.get("qmt_builtin", "qmt_python_dir", fallback="") or "").strip()
    except Exception:
        # 3.6 ConfigParser 可能无 fallback 关键字
        try:
            cp = configparser.ConfigParser()
            cp.read(cfg_path, encoding="utf-8")
            if cp.has_option("qmt_builtin", "qmt_python_dir"):
                qmt_py = (cp.get("qmt_builtin", "qmt_python_dir") or "").strip()
        except Exception:
            qmt_py = ""
    if not qmt_py:
        return None
    candidates = [
        qmt_py,
        os.path.join(qmt_py, "Lib", "site-packages"),
        os.path.join(qmt_py, "lib", "site-packages"),
        os.path.dirname(qmt_py.rstrip("\\/")),
        r"D:\国金证券QMT交易端2\bin.x64\Lib\site-packages",
        r"D:\国金证券QMT交易端2\bin.x64",
    ]
    for p in candidates:
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    return qmt_py


def _load_xtdata():
    try:
        import xtquant.xtdata as xtdata  # type: ignore
    except ImportError:
        _ensure_qmt_python_on_path()
        import xtquant.xtdata as xtdata  # type: ignore
    try:
        xtdata.enable_hello = False
    except Exception:
        pass
    return xtdata


def _ts_list_to_yyyymmdd(ts_list):
    out = []
    for ts in ts_list or []:
        if isinstance(ts, (int, float)):
            v = float(ts)
            if v > 1e12:
                v = v / 1000.0
            dt = datetime.fromtimestamp(v)
            out.append(dt.strftime("%Y%m%d"))
        elif isinstance(ts, str):
            s = ts.replace("-", "").replace("/", "")[:8]
            if len(s) == 8 and s.isdigit():
                out.append(s)
        else:
            s = str(ts).replace("-", "")[:8]
            if len(s) == 8 and s.isdigit():
                out.append(s)
    return sorted(set(d for d in out if d))


def _trading_days(xtdata, start, end):
    start_s, end_s = _ymd(start), _ymd(end)
    try:
        raw = xtdata.get_trading_dates("SH", start_time=start_s, end_time=end_s)
        days = _ts_list_to_yyyymmdd(raw)
        if days:
            return days
    except TypeError:
        try:
            raw = xtdata.get_trading_dates("SH", start_s, end_s)
            days = _ts_list_to_yyyymmdd(raw)
            if days:
                return days
        except Exception:
            pass
    except Exception:
        pass
    out = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(_ymd(cur))
        cur += timedelta(days=1)
    return out


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
            if str(k).split(".")[0].zfill(6) == c6:
                return v
        return None
    return data


def _read_local(xtdata, full_code, period, ymd):
    windows = [(ymd, ymd), (ymd + "091500", ymd + "153100")]
    last_err = ""
    for start_s, end_s in windows:
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
                subscribe=False,
            ),
            dict(
                field_list=[],
                stock_list=[full_code],
                period=period,
                start_time=start_s,
                end_time=end_s,
                count=-1,
            ),
        ]
        for kwargs in attempts:
            try:
                raw = xtdata.get_market_data_ex(**kwargs)
                payload = _extract_payload(raw, full_code)
                n = _payload_nrows(payload)
                if n > 0:
                    return n, ""
            except TypeError as te:
                last_err = "%s: %s" % (type(te).__name__, te)
                if "subscribe" in kwargs:
                    kwargs2 = dict(kwargs)
                    kwargs2.pop("subscribe", None)
                    try:
                        raw = xtdata.get_market_data_ex(**kwargs2)
                        payload = _extract_payload(raw, full_code)
                        n = _payload_nrows(payload)
                        if n > 0:
                            return n, ""
                    except Exception as e2:
                        last_err = "%s: %s" % (type(e2).__name__, e2)
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
    return 0, last_err


def _download_one(xtdata, full_code, period, ymd):
    try:
        xtdata.download_history_data(full_code, period, ymd, ymd)
        return ""
    except TypeError:
        try:
            xtdata.download_history_data(full_code, period, ymd, ymd, None)
            return ""
        except Exception as e:
            return "%s: %s" % (type(e).__name__, e)
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)


def _probe_one(xtdata, full_code, period, ymd, sleep_sec):
    before, before_err = _read_local(xtdata, full_code, period, ymd)
    dl_err = _download_one(xtdata, full_code, period, ymd)
    if sleep_sec > 0:
        time.sleep(sleep_sec)
    after, after_err = _read_local(xtdata, full_code, period, ymd)
    return {
        "code": full_code,
        "period": period,
        "day": ymd,
        "rows_before_download": before,
        "rows_after_download": after,
        "download_error": dl_err,
        "read_error_before": before_err,
        "read_error_after": after_err,
        "ok": bool(after > 0),
    }


def _summarize(rows, period, code):
    subset = [r for r in rows if r["period"] == period and r["code"] == code]
    subset_sorted = sorted(subset, key=lambda r: r["day"], reverse=True)
    ok_days = [r["day"] for r in subset_sorted if r.get("ok")]
    empty_days = [r["day"] for r in subset_sorted if not r.get("ok")]
    farthest = min(ok_days) if ok_days else None
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
        "farthest_ok": farthest,
        "first_empty_going_older": first_empty_after_ok,
        "newest_empty": max(empty_days) if empty_days else None,
    }


def _print_table(rows):
    ordered = sorted(rows, key=lambda r: (r["day"], r["code"], r["period"]), reverse=True)
    print("")
    print(
        "%s  %s  %s  %8s  %8s  %s"
        % ("day".ljust(8), "code".ljust(10), "period".ljust(5), "before", "after", "note")
    )
    print("-" * 72)
    for r in ordered:
        if r.get("download_error"):
            note = "dl:" + str(r["download_error"])[:40]
        elif r.get("ok"):
            note = "OK"
        else:
            note = "EMPTY"
        print(
            "%s  %s  %s  %8d  %8d  %s"
            % (
                r["day"],
                r["code"].ljust(10),
                r["period"].ljust(5),
                int(r.get("rows_before_download") or 0),
                int(r.get("rows_after_download") or 0),
                note,
            )
        )


class _Tee(object):
    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary

    def write(self, s):
        try:
            if self.primary is not None:
                self.primary.write(s)
                self.primary.flush()
        except Exception:
            pass
        try:
            self.secondary.write(s)
            self.secondary.flush()
        except Exception:
            pass
        return len(s) if isinstance(s, str) else 0

    def flush(self):
        for stream in (self.primary, self.secondary):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return bool(self.primary is not None and self.primary.isatty())
        except Exception:
            return False


def _install_tee_log(out_dir):
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    log_path = os.path.join(out_dir, "run_%s.log" % _now_tag())
    log_f = open(log_path, "a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, log_f)
    sys.stderr = _Tee(sys.stderr, log_f)
    return log_path


def _crash_log(msg):
    """语法通过后、tee 安装前若失败，也尽量落盘。"""
    try:
        out_dir = os.path.join(_ROOT, "data", "probe_tick_history_depth")
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        path = os.path.join(out_dir, "crash_%s.log" % _now_tag())
        with open(path, "w", encoding="utf-8") as f:
            f.write(msg)
            f.write("\n")
        return path
    except Exception:
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="探测 QMT tick/1m 可下历史深度")
    ap.add_argument("--codes", default=",".join(DEFAULT_CODES), help="逗号分隔代码")
    ap.add_argument(
        "--calendar-days",
        type=int,
        default=DEFAULT_CALENDAR_DAYS,
        help="向前自然日跨度，默认 90",
    )
    ap.add_argument("--also-1m", action="store_true", help="同时探测 1m")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP, help="download 后休眠秒")
    ap.add_argument(
        "--out-dir",
        default=os.path.join(_ROOT, "data", "probe_tick_history_depth"),
        help="结果目录",
    )
    ap.add_argument("--dry-run", action="store_true", help="只列交易日")
    args = ap.parse_args(list(argv) if argv is not None else None)

    log_path = _install_tee_log(str(args.out_dir))
    print("[probe] log=%s" % log_path)
    print("[probe] python=%s" % sys.version.replace("\n", " "))
    print("[probe] root=%s" % _ROOT)

    codes = [_full_code(c) for c in str(args.codes).split(",") if str(c).strip()]
    codes = [c for c in codes if c]
    if not codes:
        print("[probe] 无有效代码")
        return 2

    periods = ["tick"]
    if args.also_1m:
        periods.append("1m")

    print(
        "[probe] codes=%s periods=%s calendar_days=%d"
        % (codes, periods, int(args.calendar_days))
    )

    try:
        xtdata = _load_xtdata()
    except ImportError as e:
        print("[probe] 无法 import xtquant.xtdata: %s" % e)
        print(
            "[probe] 请用: D:\\国金证券QMT交易端2\\bin.x64\\pythonw.exe tools\\probe_tick_history_depth.py"
        )
        return 2

    try:
        client = xtdata.get_client()
        print("[probe] get_client=%s" % ("ok" if client is not None else "None"))
        if client is None:
            print("[probe] 提示：请先打开并登录国金 QMT 交易端")
    except Exception as e:
        print("[probe] get_client 异常: %s（仍继续尝试）" % e)

    end_d = date.today()
    start_d = end_d - timedelta(days=max(1, int(args.calendar_days)))
    days = _trading_days(xtdata, start_d, end_d)
    print(
        "[probe] trading_days=%d  range=%s..%s"
        % (len(days), days[0] if days else "-", days[-1] if days else "-")
    )
    if args.dry_run:
        for d in reversed(days):
            print("  ", d)
        return 0

    rows = []
    total = len(days) * len(codes) * len(periods)
    done = 0
    t0 = time.time()
    for ymd in reversed(days):
        for code in codes:
            for period in periods:
                done += 1
                print(
                    "[probe] (%d/%d) %s %s %s ..."
                    % (done, total, ymd, code, period)
                )
                sys.stdout.flush()
                rec = _probe_one(xtdata, code, period, ymd, float(args.sleep))
                rows.append(rec)
                mark = "OK" if rec.get("ok") else "EMPTY"
                print(
                    "         -> after=%d %s"
                    % (int(rec.get("rows_after_download") or 0), mark)
                )
                sys.stdout.flush()

    if not os.path.isdir(args.out_dir):
        os.makedirs(args.out_dir)
    tag = _now_tag()
    json_path = os.path.join(args.out_dir, "probe_%s.json" % tag)
    csv_path = os.path.join(args.out_dir, "probe_%s.csv" % tag)

    summaries = []
    for code in codes:
        for period in periods:
            summaries.append(_summarize(rows, period, code))

    report = {
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "calendar_days": int(args.calendar_days),
        "codes": codes,
        "periods": periods,
        "trading_day_count": len(days),
        "elapsed_sec": round(time.time() - t0, 1),
        "python": sys.version,
        "summaries": summaries,
        "rows": rows,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    fieldnames = [
        "day",
        "code",
        "period",
        "rows_before_download",
        "rows_after_download",
        "ok",
        "download_error",
        "read_error_before",
        "read_error_after",
    ]
    with open(csv_path, "w", encoding="utf-8") as f:
        # 3.6 csv 无 newline=；utf-8-sig 手写 BOM
        f.write("\ufeff")
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["day"], x["code"], x["period"]), reverse=True):
            w.writerow(r)

    _print_table(rows)
    print("")
    print("[probe] === 摘要 ===")
    for s in summaries:
        print(
            "  %s %s: ok=%d/%d  farthest_ok=%s  first_empty_going_older=%s"
            % (
                s["code"],
                s["period"],
                s["ok_days"],
                s["probed_days"],
                s["farthest_ok"],
                s["first_empty_going_older"],
            )
        )
    print("[probe] json: %s" % json_path)
    print("[probe] csv : %s" % csv_path)
    print(
        "[probe] 说明：farthest_ok 即实测还能下到的最远交易日；"
        "若 first_empty_going_older 紧挨其后，大致就是服务端窗口边界。"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback

        msg = traceback.format_exc()
        path = _crash_log(msg)
        try:
            sys.stderr.write(msg)
            if path:
                sys.stderr.write("crash_log=%s\n" % path)
        except Exception:
            pass
        raise SystemExit(1)
