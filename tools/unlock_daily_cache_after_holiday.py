# -*- coding: utf-8 -*-
"""After QMT reload (20261002.02+), unlock daily_cache CSVs and set manifest to 2026-09-30."""
import json
import os
import stat
from datetime import datetime
from pathlib import Path

root = Path(r"D:/蚂蚁量化系统/data/daily_cache")
for p in root.glob("*.csv"):
    try:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass
mp = root / "manifest.json"
try:
    os.chmod(mp, stat.S_IWRITE | stat.S_IREAD)
except Exception:
    pass
m = json.loads(mp.read_text(encoding="utf-8"))
m["sync_trade_date"] = "2026-09-30"
m["status"] = "completed"
m["finished_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
m["correction_note"] = "unlocked after holiday lock; last real bar 2026-09-30"
m.pop("pause_reason", None)
mp.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("unlocked + manifest -> 2026-09-30")
