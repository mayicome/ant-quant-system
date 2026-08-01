# -*- coding: utf-8 -*-
import csv
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daily_cache"


def em_klines(code6: str):
    m = 1 if code6.startswith("6") else 0
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={m}.{code6}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1&end=20260717&lmt=5"
    )
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read().decode())
    out = {}
    for line in (data.get("data") or {}).get("klines") or []:
        p = line.split(",")
        out[p[0]] = {
            "open": float(p[1]),
            "close": float(p[2]),
            "high": float(p[3]),
            "low": float(p[4]),
            "vol": float(p[5]),
        }
    return out


def main():
    suspects = ["002759.SZ", "301520.SZ", "603669.SH", "600488.SH"]
    for stem in suspects:
        code6 = stem.split(".")[0]
        path = CACHE / f"{stem}.csv"
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        local = {r["date"]: r for r in rows}
        try:
            em = em_klines(code6)
        except Exception as e:
            print(stem, "em fail", e)
            continue
        for d in ["2026-07-15", "2026-07-16", "2026-07-17"]:
            L = local.get(d)
            E = em.get(d)
            if not L and not E:
                continue
            if L:
                loc = (
                    f"O={L['open']} H={L['high']} L={L['low']} "
                    f"C={L['close']} V={L['volume']}"
                )
            else:
                loc = "MISSING"
            mark = ""
            if E:
                ems = (
                    f"O={E['open']} H={E['high']} L={E['low']} "
                    f"C={E['close']} V={E['vol']}"
                )
                if L:
                    try:
                        if (
                            abs(float(L["close"]) - E["close"]) > 0.011
                            or abs(float(L["high"]) - E["high"]) > 0.011
                        ):
                            mark = " !!"
                    except Exception:
                        mark = " !!"
            else:
                ems = "MISSING"
            print(f"{stem} {d}: local[{loc}] em[{ems}]{mark}")


if __name__ == "__main__":
    main()
