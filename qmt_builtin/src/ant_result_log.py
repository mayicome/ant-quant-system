# -*- coding: utf-8 -*-
"""把原来写入 results.json 的变化打成短行。

QMT 自己把 print 收进 userdata/log/XtClient_FormulaOutput_YYYYMMDD.log。
这里不写文件。每行以 ANTRES 开头，后面是一个短 JSON，避免被日志折成没有时间戳的续行。
"""
import json
import time

_LAST_EL = {}
_LAST_CAGE = {}
_LAST_EARLY = {}
_LAST_DONE = set()
_LAST_ASSET = None
_LAST_ASSET_AT = 0.0
_ASSET_LOG_INTERVAL_SEC = 60.0
_LAST_HB = 0.0
_LAST_SNAP = ""
_SNAP_SEQ = 0

_ORDER_KEYS = (
    "event_type",
    "stock_code",
    "price",
    "volume",
    "status",
    "cash_block",
    "task_id",
    "order_sysid",
    "side",
    "msg",
    "leg_key",
    "detail",
    "true_breakthrough_detail",
    "true_breakthrough_passed",
    "early_order",
    "grid_index",
    "broker_status",
    "cash_wait",
    "strategy_name",
)


def _dump(obj):
    try:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return ""
    return text.replace("\r", " ").replace("\n", " ")


def emit(kind, obj):
    payload = _dump(obj) if not isinstance(obj, str) else str(obj).replace("\r", " ").replace("\n", " ")
    if not payload:
        return
    line = "ANTRES %s %s" % (kind, payload)
    if len(line) > 240 and isinstance(obj, dict):
        slim = dict(obj)
        for key in ("msg", "detail", "true_breakthrough_detail", "strategy_name", "n"):
            slim.pop(key, None)
        payload = _dump(slim)
        line = "ANTRES %s %s" % (kind, payload)
    if not payload or len(line) > 240:
        return
    print(line)


def heartbeat(watch_count, tick_time, force=False, subscribe_count=None):
    """有行情订阅时约 60 秒一行。订阅数为 0 时只在名单变空时打一拍，不代表柜台通断。"""
    global _LAST_HB
    now = time.time()
    if not force and now - _LAST_HB < 60.0:
        return
    _LAST_HB = now
    row = {"n": int(watch_count or 0), "t": str(tick_time or "")[:32]}
    if subscribe_count is not None:
        try:
            row["s"] = int(subscribe_count)
        except (TypeError, ValueError):
            row["s"] = 0
    emit("hb", row)


def emit_asset(account, force=False):
    """市值随行情抖动时大约 60 秒一行。可用资金变化，或成交快照 force，立刻打。"""
    global _LAST_ASSET, _LAST_ASSET_AT
    if not isinstance(account, dict):
        return
    try:
        cur = (
            round(float(account.get("cash") or 0), 2),
            round(float(account.get("total_asset") or 0), 2),
            round(float(account.get("market_value") or 0), 2),
        )
    except (TypeError, ValueError):
        return
    if cur == _LAST_ASSET:
        return
    if cur == (0.0, 0.0, 0.0) and _LAST_ASSET is None:
        return
    now = time.time()
    cash_changed = _LAST_ASSET is None or cur[0] != _LAST_ASSET[0]
    if (
        not force
        and not cash_changed
        and now - _LAST_ASSET_AT < _ASSET_LOG_INTERVAL_SEC
    ):
        return
    _LAST_ASSET = cur
    _LAST_ASSET_AT = now
    emit("asset", {"cash": cur[0], "total": cur[1], "mkt": cur[2]})


def _sync_map(bucket, current, emit_row):
    cur = current if isinstance(current, dict) else {}
    for key, value in cur.items():
        kid = str(key or "").strip()
        if not kid or not isinstance(value, dict):
            continue
        blob = _dump(value)
        if bucket.get(kid) == blob:
            continue
        bucket[kid] = blob
        emit_row(kid, value)
    for kid in list(bucket.keys()):
        if kid not in cur:
            bucket.pop(kid, None)
            emit_row(kid, None)


def _el_row(tid, st):
    if not isinstance(st, dict):
        return {"id": tid, "gone": 1}
    kind = str(st.get("kind") or "")
    if kind == "best_buy":
        px = st.get("lowest_price")
        xi = st.get("lowest_tick_idx")
        hc = st.get("rebound_hit_count")
    else:
        px = st.get("highest_price")
        xi = st.get("highest_tick_idx")
        hc = st.get("pullback_hit_count")
    return {
        "id": tid,
        "k": kind,
        "tr": 1 if st.get("triggered") else 0,
        "px": px,
        "ti": int(st.get("tick_idx") or 0),
        "xi": int(xi or 0),
        "hc": int(hc or 0),
    }


def _sync_elastic(current):
    cur = current if isinstance(current, dict) else {}
    for key, value in cur.items():
        kid = str(key or "").strip()
        if not kid or not isinstance(value, dict):
            continue
        row = _el_row(kid, value)
        sig = _dump({"id": kid, "k": row.get("k"), "tr": row.get("tr"), "px": row.get("px"), "hc": row.get("hc")})
        if _LAST_EL.get(kid) == sig:
            continue
        _LAST_EL[kid] = sig
        emit("el", row)
    for kid in list(_LAST_EL.keys()):
        if kid not in cur:
            _LAST_EL.pop(kid, None)
            emit("el", {"id": kid, "gone": 1})


def publish_runner_state(elastic, cage, early, done_ids):
    _sync_elastic(elastic)

    def emit_cage(tid, st):
        if not isinstance(st, dict):
            emit("cage", {"id": tid, "gone": 1})
            return
        emit("cage", {"id": tid, "k": str(st.get("kind") or ""), "on": 1})

    def emit_early(key, st):
        if not isinstance(st, dict):
            emit("early", {"id": key, "gone": 1})
            return
        emit(
            "early",
            {
                "id": key,
                "task": str(st.get("task_id") or ""),
                "k": str(st.get("kind") or ""),
                "px": st.get("price"),
                "vol": int(st.get("volume") or 0),
                "gi": st.get("grid_index"),
                "uid": str(st.get("user_order_id") or ""),
                "on": 1,
            },
        )

    _sync_map(_LAST_CAGE, cage, emit_cage)
    _sync_map(_LAST_EARLY, early, emit_early)
    cur_done = set()
    for tid in done_ids or []:
        text = str(tid or "").strip()
        if text:
            cur_done.add(text)
    for tid in sorted(cur_done - _LAST_DONE):
        emit("done", {"id": tid})
        _LAST_DONE.add(tid)


def emit_event(ev):
    if not isinstance(ev, dict):
        return
    row = {}
    for key in ("type", "stock_code", "tick_time", "trigger_price", "msg", "detail", "task_id"):
        val = ev.get(key)
        if val in (None, ""):
            continue
        row[key] = val
    if "detail" in row:
        row["detail"] = str(row["detail"])[:120]
    if "msg" in row:
        row["msg"] = str(row["msg"])[:80]
    metrics = ev.get("metrics")
    if isinstance(metrics, dict) and "passed" in metrics:
        row["passed"] = 1 if metrics.get("passed") else 0
    if row.get("type"):
        emit("ev", row)


def emit_order(record):
    if not isinstance(record, dict):
        return
    row = {}
    for key in _ORDER_KEYS:
        if key not in record:
            continue
        val = record.get(key)
        if val in (None, ""):
            continue
        if key in ("msg", "detail", "true_breakthrough_detail", "strategy_name"):
            val = str(val)[:80]
        row[key] = val
    if row:
        emit("ord", row)


def _position_row(code, rec):
    if not isinstance(rec, dict):
        return None
    return {
        "c": str(rec.get("stock_code") or code or ""),
        "n": str(rec.get("stock_name") or "")[:16],
        "v": int(rec.get("volume") or 0),
        "u": int(rec.get("can_use_volume") or 0),
        "p": float(rec.get("open_price") or 0),
        "m": float(rec.get("market_value") or 0),
    }


def _broker_row(rec):
    if not isinstance(rec, dict):
        return None
    code = str(rec.get("stock_code") or "").strip()
    sysid = str(rec.get("order_sysid") or "").strip()
    if not code and not sysid:
        return None
    return {
        "c": code,
        "n": str(rec.get("stock_name") or "")[:16],
        "sd": str(rec.get("side") or ""),
        "st": str(rec.get("broker_status_text") or rec.get("status") or ""),
        "px": float(rec.get("price") or 0),
        "v": int(rec.get("volume") or 0),
        "tv": int(rec.get("traded_volume") or 0),
        "tp": float(rec.get("traded_price") or 0),
        "id": sysid,
        "tm": str(rec.get("order_time") or "")[:16],
    }


def request_snapshot(positions, orders, account):
    """委托/成交/持仓回调之后打一批。同一内容 1 秒内不重复。"""
    global _LAST_SNAP
    pos_rows = []
    for code, rec in (positions or {}).items():
        row = _position_row(code, rec)
        if row and row.get("c"):
            pos_rows.append(row)
    ord_rows = []
    for rec in orders or []:
        row = _broker_row(rec)
        if row:
            ord_rows.append(row)
    asset = {}
    if isinstance(account, dict):
        try:
            asset = {
                "cash": round(float(account.get("cash") or 0), 2),
                "total": round(float(account.get("total_asset") or 0), 2),
                "mkt": round(float(account.get("market_value") or 0), 2),
            }
        except (TypeError, ValueError):
            asset = {}
    blob = _dump({"p": pos_rows, "o": ord_rows, "a": asset})
    if blob == _LAST_SNAP:
        return
    _LAST_SNAP = blob
    _emit_snapshot(pos_rows, ord_rows, asset)


def _emit_snapshot(pos_rows, ord_rows, asset):
    global _SNAP_SEQ
    _SNAP_SEQ += 1
    seq = _SNAP_SEQ
    emit("snap", {"op": "begin", "seq": seq, "npos": len(pos_rows), "nord": len(ord_rows)})
    if asset:
        body = dict(asset)
        body["op"] = "asset"
        body["seq"] = seq
        emit("snap", body)
    for row in pos_rows:
        body = dict(row)
        body["op"] = "pos"
        body["seq"] = seq
        emit("snap", body)
    for row in ord_rows:
        body = dict(row)
        body["op"] = "ord"
        body["seq"] = seq
        emit("snap", body)
    emit("snap", {"op": "end", "seq": seq})
