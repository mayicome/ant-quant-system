# -*- coding: utf-8 -*-
"""大 QMT 内置 passorder 封装（股票/ETF 限价买入/卖出）。"""
import math
import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

try:
    from ant_qmt_paths import PROJECT_ROOT
except Exception:
    PROJECT_ROOT = ""

PASSORDER_VERSION = "20260803.01"

# QMT 注入的 passorder（由入口文件 bind_runtime_globals 绑定）
_PASSORDER = None

OP_STOCK_BUY = 23
OP_STOCK_SELL = 24
ORDER_TYPE_SHARES = 1101  # 单股单账号按股数
PR_LIMIT = 11  # 指定价


_CANCEL = None


def bind_runtime_globals(g: Optional[Dict[str, Any]] = None) -> bool:
    """从入口策略 globals()/builtins 绑定 passorder / cancel。"""
    global _PASSORDER, _CANCEL
    sources = []
    cancel_sources = []
    if isinstance(g, dict):
        sources.append(g.get("passorder"))
        cancel_sources.append(g.get("cancel"))
    try:
        import builtins

        sources.append(getattr(builtins, "passorder", None))
        cancel_sources.append(getattr(builtins, "cancel", None))
    except Exception:
        builtins = None  # type: ignore
    bound = False
    for fn in sources:
        if callable(fn):
            _PASSORDER = fn
            try:
                if builtins is not None:
                    builtins.passorder = fn
            except Exception:
                pass
            bound = True
            break
    for fn in cancel_sources:
        if callable(fn):
            _CANCEL = fn
            try:
                if builtins is not None:
                    builtins.cancel = fn
            except Exception:
                pass
            break
    if bound and not getattr(bind_runtime_globals, "_logged_ok", False):
        print("[下单] 绑定成功 cancel=%s" % bool(callable(_CANCEL)))
        bind_runtime_globals._logged_ok = True  # type: ignore[attr-defined]
    if not bound:
        print("[下单] 绑定失败: 策略 globals 中未找到 passorder")
    return bound


def is_bound() -> bool:
    if callable(_PASSORDER):
        return True
    try:
        import builtins

        return callable(getattr(builtins, "passorder", None))
    except Exception:
        return False


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _price_precision(stock_code: str) -> int:
    c6 = str(stock_code or "").split(".")[0]
    if len(c6) == 6 and (c6.startswith("5") or c6.startswith(("15", "16", "18"))):
        return 3
    return 2


def _round_price(stock_code: str, price: float) -> float:
    prec = _price_precision(stock_code)
    mul = 10 ** prec
    return math.floor(float(price) * mul + 0.5) / mul


def _min_tick(stock_code: str) -> float:
    return 10 ** (-_price_precision(stock_code))


def _ask1(tick_row: Optional[Dict[str, Any]]) -> float:
    if not isinstance(tick_row, dict):
        return 0.0
    raw = tick_row.get("askPrice")
    try:
        if isinstance(raw, (list, tuple)) and raw:
            v = float(raw[0])
            return v if v > 0 else 0.0
        if raw is not None:
            v = float(raw)
            return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def _bid1(tick_row: Optional[Dict[str, Any]]) -> float:
    if not isinstance(tick_row, dict):
        return 0.0
    raw = tick_row.get("bidPrice")
    try:
        if isinstance(raw, (list, tuple)) and raw:
            v = float(raw[0])
            return v if v > 0 else 0.0
        if raw is not None:
            v = float(raw)
            return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def resolve_buy_limit_price(
    stock_code: str,
    *,
    last_price: float = 0.0,
    trigger_price: float = 0.0,
    tick_row: Optional[Dict[str, Any]] = None,
) -> float:
    """买入限价：优先卖一+1跳，否则现价+1跳，否则触发价。"""
    tick = _min_tick(stock_code)
    ask = _ask1(tick_row)
    if ask > 0:
        return _round_price(stock_code, ask + tick)
    lp = float(last_price or 0)
    if lp > 0:
        return _round_price(stock_code, lp + tick)
    trig = float(trigger_price or 0)
    if trig > 0:
        return _round_price(stock_code, trig)
    return 0.0


def resolve_sell_limit_price(
    stock_code: str,
    *,
    last_price: float = 0.0,
    trigger_price: float = 0.0,
    tick_row: Optional[Dict[str, Any]] = None,
) -> float:
    """卖出限价：优先买一-1跳，否则现价-1跳，否则触发价。"""
    tick = _min_tick(stock_code)
    bid = _bid1(tick_row)
    if bid > 0:
        return _round_price(stock_code, max(tick, bid - tick))
    lp = float(last_price or 0)
    if lp > 0:
        return _round_price(stock_code, max(tick, lp - tick))
    trig = float(trigger_price or 0)
    if trig > 0:
        return _round_price(stock_code, trig)
    return 0.0


def _resolve_account_id(ContextInfo) -> str:
    try:
        from ant_account_snapshot import resolve_account_id

        return str(resolve_account_id(ContextInfo) or "").strip()
    except Exception:
        pass
    try:
        import configparser

        ini_path = os.path.join(str(PROJECT_ROOT).rstrip("\\/"), "data", "config.ini")
        if PROJECT_ROOT and os.path.isfile(ini_path):
            cfg = configparser.ConfigParser()
            cfg.read(ini_path, encoding="utf-8")
            if cfg.has_option("Account", "account_id"):
                return str(cfg.get("Account", "account_id") or "").strip()
    except Exception:
        pass
    return ""


def _norm_volume(volume: int) -> int:
    vol = int(volume or 0)
    if vol <= 0:
        return 0
    # A 股/ETF 通常 100 股整数倍
    if vol < 100:
        return 0
    return (vol // 100) * 100


def place_limit_buy(
    ContextInfo,
    stock_code: str,
    price: float,
    volume: int,
    *,
    strategy_name: str = "蚂蚁-单点买入",
    user_order_id: str = "",
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    限价买入。返回 (ok, reason, record)。
    ok=True 表示已调用 passorder（不代表一定成交）。
    """
    code = str(stock_code or "").strip().upper()
    px = _round_price(code, float(price or 0))
    vol = _norm_volume(int(volume or 0))
    record: Dict[str, Any] = {
        "side": "buy",
        "stock_code": code,
        "price": px,
        "volume": vol,
        "strategy_name": str(strategy_name or "蚂蚁-单点买入"),
        "user_order_id": str(user_order_id or ""),
        "at": _now_iso(),
        "status": "error",
        "msg": "",
    }
    if not code or px <= 0 or vol <= 0:
        record["msg"] = "bad_params"
        return False, record["msg"], record
    if ContextInfo is None:
        record["msg"] = "no_context"
        return False, record["msg"], record
    fn = _PASSORDER
    if not callable(fn):
        try:
            import builtins

            fn = getattr(builtins, "passorder", None)
        except Exception:
            fn = None
    if not callable(fn):
        record["msg"] = "passorder_unbound"
        return False, record["msg"], record
    aid = _resolve_account_id(ContextInfo)
    if not aid:
        record["msg"] = "no_account_id"
        return False, record["msg"], record
    record["account_id"] = aid
    raw_uid = str(user_order_id or "")
    # QMT userOrderId 限长 32：保留末尾（含 rule_id），避免多规则撞车
    pass_uid = raw_uid[-32:] if len(raw_uid) > 32 else raw_uid
    record["user_order_id"] = raw_uid
    record["pass_uid"] = pass_uid
    try:
        # passorder(opType, orderType, accountid, orderCode, prType, price, volume,
        #           strategyName, quickTrade, userOrderId, ContextInfo)
        fn(
            OP_STOCK_BUY,
            ORDER_TYPE_SHARES,
            aid,
            code,
            PR_LIMIT,
            float(px),
            int(vol),
            str(strategy_name or "蚂蚁-单点买入"),
            1,
            pass_uid,
            ContextInfo,
        )
        record["status"] = "submitted"
        record["msg"] = "passorder_called"
        print(
            "[下单] 买入 %s px=%s vol=%s account=%s uid=%s"
            % (code, px, vol, aid, user_order_id)
        )
        return True, "ok", record
    except Exception as e:
        record["msg"] = "%s: %s" % (type(e).__name__, e)
        print("[下单] 买入失败 %s: %s" % (code, record["msg"]))
        return False, record["msg"], record


def place_limit_sell(
    ContextInfo,
    stock_code: str,
    price: float,
    volume: int,
    *,
    strategy_name: str = "蚂蚁-单点卖出",
    user_order_id: str = "",
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    限价卖出。返回 (ok, reason, record)。
    ok=True 表示已调用 passorder（不代表一定成交）。
    """
    code = str(stock_code or "").strip().upper()
    px = _round_price(code, float(price or 0))
    vol = _norm_volume(int(volume or 0))
    record: Dict[str, Any] = {
        "side": "sell",
        "stock_code": code,
        "price": px,
        "volume": vol,
        "strategy_name": str(strategy_name or "蚂蚁-单点卖出"),
        "user_order_id": str(user_order_id or ""),
        "at": _now_iso(),
        "status": "error",
        "msg": "",
    }
    if not code or px <= 0 or vol <= 0:
        record["msg"] = "bad_params"
        return False, record["msg"], record
    if ContextInfo is None:
        record["msg"] = "no_context"
        return False, record["msg"], record
    fn = _PASSORDER
    if not callable(fn):
        try:
            import builtins

            fn = getattr(builtins, "passorder", None)
        except Exception:
            fn = None
    if not callable(fn):
        record["msg"] = "passorder_unbound"
        return False, record["msg"], record
    aid = _resolve_account_id(ContextInfo)
    if not aid:
        record["msg"] = "no_account_id"
        return False, record["msg"], record
    record["account_id"] = aid
    raw_uid = str(user_order_id or "")
    pass_uid = raw_uid[-32:] if len(raw_uid) > 32 else raw_uid
    record["user_order_id"] = raw_uid
    record["pass_uid"] = pass_uid
    try:
        fn(
            OP_STOCK_SELL,
            ORDER_TYPE_SHARES,
            aid,
            code,
            PR_LIMIT,
            float(px),
            int(vol),
            str(strategy_name or "蚂蚁-单点卖出"),
            1,
            pass_uid,
            ContextInfo,
        )
        record["status"] = "submitted"
        record["msg"] = "passorder_called"
        print(
            "[下单] 卖出 %s px=%s vol=%s account=%s uid=%s"
            % (code, px, vol, aid, user_order_id)
        )
        return True, "ok", record
    except Exception as e:
        record["msg"] = "%s: %s" % (type(e).__name__, e)
        print("[下单] 卖出失败 %s: %s" % (code, record["msg"]))
        return False, record["msg"], record


def cancel_order_sysid(
    ContextInfo,
    order_sysid: str,
    *,
    account_type: str = "STOCK",
) -> Tuple[bool, str, Dict[str, Any]]:
    """调用 QMT cancel(orderId, accountId, accountType, ContextInfo)。"""
    sysid = str(order_sysid or "").strip()
    record: Dict[str, Any] = {
        "side": "cancel",
        "order_sysid": sysid,
        "at": _now_iso(),
        "status": "error",
        "msg": "",
    }
    if not sysid or sysid in ("0", "-1"):
        record["msg"] = "bad_sysid"
        return False, record["msg"], record
    if ContextInfo is None:
        record["msg"] = "no_context"
        return False, record["msg"], record
    fn = _CANCEL
    if not callable(fn):
        try:
            import builtins

            fn = getattr(builtins, "cancel", None)
        except Exception:
            fn = None
    if not callable(fn):
        record["msg"] = "cancel_unbound"
        return False, record["msg"], record
    aid = _resolve_account_id(ContextInfo)
    if not aid:
        record["msg"] = "no_account_id"
        return False, record["msg"], record
    record["account_id"] = aid
    try:
        fn(sysid, aid, str(account_type or "STOCK"), ContextInfo)
        record["status"] = "cancel_sent"
        record["msg"] = "cancel_called"
        print("[下单] 撤单 sysid=%s account=%s" % (sysid, aid))
        return True, "ok", record
    except Exception as e:
        record["msg"] = "%s: %s" % (type(e).__name__, e)
        print("[下单] 撤单失败: %s" % record["msg"])
        return False, record["msg"], record


def append_order_record(results: Dict[str, Any], record: Dict[str, Any]) -> None:
    if not isinstance(results, dict) or not isinstance(record, dict):
        return
    orders = results.setdefault("orders", [])
    if not isinstance(orders, list):
        orders = []
        results["orders"] = orders
    orders.append(dict(record))
    if len(orders) > 200:
        results["orders"] = orders[-200:]
    results["updated_at"] = _now_iso()
