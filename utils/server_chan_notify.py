# -*- coding: utf-8 -*-
"""主程序侧 Server酱推送（与 qmt_builtin/ant_server_chan 同一配置）。"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_DEFAULT_NAME = "马毅"
_DEFAULT_DB = r"D:\蚂蚁量化信息采集系统\data\data.db"
_LAST_ALERT_TS: Dict[str, float] = {}
_ALERT_COOLDOWN_SEC = 3600.0


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config_path() -> str:
    return os.path.join(_project_root(), "data", "notify_server_chan.json")


def _load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "name": _DEFAULT_NAME,
        "collector_db": _DEFAULT_DB,
        "send_key": "",
        "enabled": True,
    }
    path = _config_path()
    if not os.path.isfile(path):
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    return cfg


def _lookup_key_from_db(db_path: str, name: str) -> Optional[str]:
    if not db_path or not name or not os.path.isfile(db_path):
        return None
    try:
        con = sqlite3.connect(db_path, timeout=3.0)
        try:
            row = con.execute(
                "SELECT server_id FROM server_configs "
                "WHERE name = ? AND enabled = 1 "
                "AND (valid_until IS NULL OR valid_until > datetime('now')) "
                "LIMIT 1",
                (name,),
            ).fetchone()
            if row and row[0]:
                return str(row[0]).strip()
            row = con.execute(
                "SELECT server_id FROM server_configs WHERE name = ? LIMIT 1",
                (name,),
            ).fetchone()
            if row and row[0]:
                return str(row[0]).strip()
        finally:
            con.close()
    except Exception:
        return None
    return None


def resolve_send_key(cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    cfg = cfg or _load_config()
    direct = str(cfg.get("send_key") or "").strip()
    if direct:
        return direct, "config.send_key"
    name = str(cfg.get("name") or _DEFAULT_NAME).strip() or _DEFAULT_NAME
    db_path = str(cfg.get("collector_db") or _DEFAULT_DB).strip() or _DEFAULT_DB
    key = _lookup_key_from_db(db_path, name)
    if key:
        return key, "collector:%s" % name
    return "", "missing"


def send_server_chan(title: str, content: str, send_key: Optional[str] = None) -> Dict[str, Any]:
    cfg = _load_config()
    if cfg.get("enabled") is False:
        return {"success": False, "message": "disabled"}

    key = (send_key or "").strip()
    source = "arg"
    if not key:
        key, source = resolve_send_key(cfg)
    if not key:
        return {"success": False, "message": "no send_key (%s)" % source}

    title = (title or "")[:256]
    desp = content or ""
    if len(desp) > 20000:
        desp = desp[:19900] + "\n\n...（已截断）"

    url = "https://sctapi.ftqq.com/%s.send" % key
    body = urlencode({"title": title, "desp": desp}).encode("utf-8")
    req = Request(url, data=body)
    try:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        resp = urlopen(req, timeout=10)
        raw = resp.read()
        try:
            result = json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            return {"success": False, "message": "bad json", "source": source}
        if result.get("code") == 0:
            return {"success": True, "message": "ok", "source": source}
        return {
            "success": False,
            "message": "code=%s %s" % (result.get("code"), result.get("message")),
            "source": source,
        }
    except Exception as e:
        return {"success": False, "message": "%s" % e, "source": source}


def notify_alert(
    title: str,
    content: str,
    alert_key: str = "default",
    cooldown_sec: Optional[float] = None,
) -> Dict[str, Any]:
    cool = float(_ALERT_COOLDOWN_SEC if cooldown_sec is None else cooldown_sec)
    now = time.time()
    last = float(_LAST_ALERT_TS.get(alert_key) or 0.0)
    if cool > 0 and (now - last) < cool:
        return {
            "success": False,
            "message": "cooldown %.0fs" % (cool - (now - last)),
            "skipped": True,
        }
    result = send_server_chan(title, content)
    if result.get("success"):
        _LAST_ALERT_TS[alert_key] = now
    return result
