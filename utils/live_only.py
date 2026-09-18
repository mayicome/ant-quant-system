# -*- coding: utf-8 -*-
"""实盘机标记：有 data/qmt_live_only.flag（或环境变量）则走仅实盘流程。

与 qmt_builtin.ant_daily_sync_runner.is_qmt_live_only 判定一致：
- ANT_QMT_LIVE_ONLY=1 / ANT_LIVE_ONLY=1
- 文件 data/qmt_live_only.flag
"""
from __future__ import annotations

import os
from pathlib import Path


def _env_truthy(name: str) -> bool:
    v = str(os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


from typing import Optional, Union

def live_only_flag_path(project_root: Optional[Union[str, Path]] = None) -> Path:
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
    return root / "data" / "qmt_live_only.flag"


def is_live_only_machine(project_root: Optional[Union[str, Path]] = None) -> bool:
    if _env_truthy("ANT_QMT_LIVE_ONLY") or _env_truthy("ANT_LIVE_ONLY"):
        return True
    try:
        return live_only_flag_path(project_root).is_file()
    except Exception:
        return False
