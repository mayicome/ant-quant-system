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
_LAST_HB = 0.0
_LAST_SNAP = ""
_LAST_SNAP_EMIT_AT = 0.0
_SNAP_PERIODIC_SEC = 60.0
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
    "at",
)

# 本地委托行超这个长度时先丢掉展示字段。身份字段仍要打出，不能整行扔掉。
_ORDER_LINE_MAX = 280
_ORDER_SLIM_KEYS = (
    "detail",
    "true_breakthrough_detail",
    "strategy_name",
    "leg_key",
    "true_breakthrough_passed",
    "cash_wait",
    "msg",
    "cash_block",
    "early_order",
    "grid_index",
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



def emit_armed_ack(n_tasks, sig="", parents=None, subscribe_count=None):
    """规则重装后打一拍，GUI 用它确认 QMT 已吃到 rules_armed。"""
    row = {"n": int(n_tasks or 0)}
    if sig:
        row["sig"] = str(sig)[:40]
    ids = []
    for item in parents or []:
        tid = str(item or "").strip()
        if not tid:
            continue
        ids.append(tid[:40])
        if len(ids) >= 16:
            break
    if ids:
        row["ids"] = ids
    if subscribe_count is not None:
        try:
            row["s"] = int(subscribe_count)
        except (TypeError, ValueError):
            row["s"] = 0
    emit("armed", row)
    try:
        heartbeat(
            n_tasks,
            "",
            force=True,
            subscribe_count=subscribe_count if subscribe_count is not None else n_tasks,
        )
    except Exception:
        pass


def emit_asset(account, force=False):
    """仅可用资金变化或 force 时打 asset；总市值/持仓市值抖动不打。"""
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
    if cur == (0.0, 0.0, 0.0) and _LAST_ASSET is None:
        return
    cash = cur[0]
    if not force and _LAST_ASSET is not None and cash == _LAST_ASSET[0]:
        return
    _LAST_ASSET = cur
    _LAST_ASSET_AT = time.time()
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


_LAST_PX = {}


def emit_price(code, price, tick_time="", bucket=None):
    """不再打 ANTRES px。交易界面现价走东财；策略仍用 QMT tick 触发，成交价以委托/成交日志为准。"""
    return


def _order_line(row):
    payload = _dump(row)
    if not payload:
        return ""
    return "ANTRES ord %s" % payload


def emit_order(record):
    """打一行本地委托。展示字段可以减，这笔单不能整行丢掉。"""
    if not isinstance(record, dict):
        return
    row = {}
    for key in _ORDER_KEYS:
        if key not in record:
            continue
        val = record.get(key)
        if val in (None, ""):
            continue
        if key in ("msg", "detail", "true_breakthrough_detail", "strategy_name", "cash_block"):
            val = str(val)[:80]
        if key == "at":
            val = str(val)[:19]
        row[key] = val
    if not row:
        return
    line = _order_line(row)
    if len(line) > _ORDER_LINE_MAX:
        for key in _ORDER_SLIM_KEYS:
            if key not in row:
                continue
            row.pop(key, None)
            line = _order_line(row)
            if line and len(line) <= _ORDER_LINE_MAX:
                break
    if line:
        print(line)


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
    row = {
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
    at = str(rec.get("order_at") or "")[:19]
    if at:
        row["at"] = at
    return row


def _snap_stable_fingerprint(pos_rows, ord_rows, asset):
    """决定是否打 snap：持仓看数量/可用；资金看可用；委托看全字段。"""
    pos_sig = []
    for row in sorted(pos_rows or [], key=lambda r: str(r.get("c") or "")):
        pos_sig.append(
            {
                "c": row.get("c"),
                "v": int(row.get("v") or 0),
                "u": int(row.get("u") or 0),
            }
        )
    ord_sig = sorted(
        ord_rows or [],
        key=lambda r: (str(r.get("id") or ""), str(r.get("c") or "")),
    )
    cash = 0.0
    if isinstance(asset, dict):
        try:
            cash = round(float(asset.get("cash") or 0), 2)
        except (TypeError, ValueError):
            cash = 0.0
    return _dump({"p": pos_sig, "o": ord_sig, "cash": cash})


def request_snapshot(positions, orders, account):
    """实质变化时打 snap；另每 60 秒强制打一包全量（含市值/总资产）。"""
    global _LAST_SNAP, _LAST_SNAP_EMIT_AT
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
    blob = _snap_stable_fingerprint(pos_rows, ord_rows, asset)
    now = time.time()
    periodic = (now - float(_LAST_SNAP_EMIT_AT or 0)) >= _SNAP_PERIODIC_SEC
    if blob == _LAST_SNAP and not periodic:
        return
    _LAST_SNAP = blob
    _LAST_SNAP_EMIT_AT = now
    _emit_snapshot(pos_rows, ord_rows, asset)


_LAST_LEG = {}
_LEG_CLEARED = set()
_LAST_ENT = {}
_LAST_LBU = {}
_POS_CLEARED = set()


def _code6(code):
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def emit_filled_leg(code, leg_id, side="", volume=0, filled_at="", task_id="", name=""):
    """已成交的一条腿。同一内容不重复打。"""
    c6 = _code6(code)
    lid = str(leg_id or "").strip()
    if ":" in lid:
        lid = lid.split(":", 1)[1].strip()
    if not c6 or not lid:
        return
    try:
        vol = int(volume or 0)
    except (TypeError, ValueError):
        vol = 0
    sig = "%s|%s|%s|%s" % (str(side or ""), vol, str(filled_at or "")[:19], str(task_id or "")[:40])
    key = "%s:%s" % (c6, lid)
    if _LAST_LEG.get(key) == sig:
        return
    _LAST_LEG[key] = sig
    _LEG_CLEARED.discard(c6)
    row = {"c": c6, "id": lid[:24]}
    if side:
        row["sd"] = str(side)[:8]
    if vol > 0:
        row["v"] = vol
    if filled_at:
        row["at"] = str(filled_at)[:19]
    if task_id:
        row["task"] = str(task_id)[:40]
    if name:
        row["n"] = str(name)[:40]
    emit("leg", row)


def emit_leg_clear(code):
    """某代码已执行腿全部作废。"""
    c6 = _code6(code)
    if not c6 or c6 in _LEG_CLEARED:
        return
    _LEG_CLEARED.add(c6)
    for key in list(_LAST_LEG.keys()):
        if key.split(":", 1)[0] == c6:
            _LAST_LEG.pop(key, None)
    emit("legx", {"c": c6})


def emit_entry_date(code, day):
    """首次建仓日。同一天不重复打。"""
    c6 = _code6(code)
    ds = str(day or "")[:10]
    if not c6 or len(ds) < 10:
        return
    if _LAST_ENT.get(c6) == ds:
        return
    _LAST_ENT[c6] = ds
    _POS_CLEARED.discard(c6)
    emit("ent", {"c": c6, "d": ds})


def emit_last_buy(code, day):
    """末笔买入日。同一天不重复打。"""
    c6 = _code6(code)
    ds = str(day or "")[:10]
    if not c6 or len(ds) < 10:
        return
    if _LAST_LBU.get(c6) == ds:
        return
    _LAST_LBU[c6] = ds
    _POS_CLEARED.discard(c6)
    emit("lbu", {"c": c6, "d": ds})


_LAST_CX = {}


def emit_cancel(req_id, sysid, code="", status="", msg="", at=""):
    """撤单处理结果。同一请求、同一状态不重复打。"""
    rid = str(req_id or "").strip()[:16]
    st = str(status or "").strip()[:16]
    if not rid or not st:
        return
    sig = st + "|" + str(msg or "")[:80]
    if _LAST_CX.get(rid) == sig:
        return
    _LAST_CX[rid] = sig
    row = {"id": rid, "sys": str(sysid or "")[:32], "st": st}
    if code:
        row["c"] = str(code)[:12]
    if msg:
        row["msg"] = str(msg)[:80]
    if at:
        row["at"] = str(at)[:19]
    emit("cx", row)


def emit_pos_clear(code):
    """建仓日和末笔买入日一起清掉。同一代码连续清只打一次。"""
    c6 = _code6(code)
    if not c6 or c6 in _POS_CLEARED:
        return
    _POS_CLEARED.add(c6)
    _LAST_ENT.pop(c6, None)
    _LAST_LBU.pop(c6, None)
    emit("posx", {"c": c6})


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
