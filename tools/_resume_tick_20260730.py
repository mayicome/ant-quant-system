# -*- coding: utf-8 -*-
"""DEPRECATED — do not use as primary catch-up.

This script ran outside the trading client and forced
ENABLE_XTDATA_TICK_DOWNLOAD=True (miniQMT / xtdata / port 58610).

大 QMT-only policy: tick catch-up must run inside the trading client
with a live ContextInfo (strategy 「蚂蚁量化规则」), via:
  - daily_sync serial trigger after 15:35, or
  - data/tick_full_sync/manual_request.json
    e.g. {"day":"20260730","force":false}
    (do NOT set enable_xtdata_download=true)

If you still need this one-off for archaeology, exit is intentional.
"""
from __future__ import print_function
import sys

print(
    "[DEPRECATED] tools/_resume_tick_20260730.py hard-depends on miniQMT/xtdata.\n"
    "Use ContextInfo inside 大 QMT (manual_request.json or daily_sync chain).\n"
    "Refusing to start.",
    file=sys.stderr,
)
sys.exit(2)
