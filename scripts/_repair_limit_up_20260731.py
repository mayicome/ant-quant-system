# -*- coding: utf-8 -*-
"""One-off wrapper: rebuild 2026-07-31.json (delegates to _repair_limit_up_day)."""
from _repair_limit_up_day import repair

if __name__ == "__main__":
    repair("2026-07-31")
