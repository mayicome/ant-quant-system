#coding:gbk
"""�� QMT ģ�ͽ����ڣ��� get_trade_detail_data / �ص�������ȡ�ʽ�/�ֲ�д�� results.json��"""
import os
import time
from datetime import datetime, timedelta, date, time as dt_time

try:
    from ant_qmt_paths import PROJECT_ROOT
except Exception:
    PROJECT_ROOT = ""

ACCOUNT_SNAPSHOT_VERSION = "20260811.04"

_CACHED_ACCOUNT = None
_CACHED_POSITIONS = {}
_CACHED_ORDERS = {}  # order_sysid -> parsed
_BOUND_ACCOUNT_ID = ""
_DIAG_DONE = False
_BJ_SECTOR_PROBE_DONE = False

# �ֲֿյ���Ʊ��ֵ����ƫ�� �� ���ɣ���ղ֣���ֵ��0�����澯��
_POSITION_ALERT_MV_THRESHOLD = 5000.0
_POSITION_ALERT_LOG_INTERVAL_SEC = 300.0  # ��־������Լÿ 5 ����
_POSITION_ALERT_NOTIFY_COOLDOWN_SEC = 3600.0  # ����ʱ�� Server����Լ 1 Сʱһ��
_POSITION_ALERT_NOTIFY_COOLDOWN_OFFHOURS_SEC = 28800.0  # �ǽ���ʱ�Σ�Լ 8 Сʱһ�Σ�ҹ��/��ĩ��ˢ����
_LAST_POSITION_ALERT_LOG_TS = 0.0
_POSITION_ALERT_ACTIVE = False

# �� XtQuant / �� QMT ί��״̬��һ�£�86=��̨����ȷ�ϡ���������ģ�ͽ��ף�
# ע�⣺ί������ IPO_SUBSCRIBE Ҳ�� 86������ö��ֵͬ��������ֶΡ�
ORDER_STATUS_TEXT = {
    48: "δ��",
    49: "����",
    50: "�ѱ�",
    51: "�ѱ�����",
    52: "���ɴ���",
    53: "����",
    54: "�ѳ�",
    55: "����",
    56: "�ѳ�",
    57: "�ϵ�",
    86: "��ȷ��",
    255: "δ֪",
}

# xtconstant ί��ҵ�����ͣ�order_type����86=�����¹��깺����״̬�� 86 �޹�
ORDER_TYPE_TEXT = {
    23: "��ͨ����",
    24: "��ͨ����",
    86: "�¹��깺",
}

_ORDER_FIELD_DIAG_DONE = False


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _pick(row, *keys, **kwargs):
    default = kwargs.get("default")
    for k in keys:
        if isinstance(row, dict):
            if k in row and row[k] is not None and row[k] != "":
                return row[k]
        else:
            val = getattr(row, k, None)
            if val is not None and val != "":
                return val
    return default


# SWIG / �� QMT ������ʱ���� dir() �ﱩ¶ m_*������ʽ getattr
_KNOWN_DETAIL_ATTRS = (
    "m_strAccountID",
    "m_dBalance",
    "m_dAvailable",
    "m_dFrozenCash",
    "m_dMarketValue",
    "m_dStockValue",
    "m_dAsset",
    "m_dCash",
    "m_strInstrumentID",
    "m_strStockCode",
    "m_strExchangeID",
    "m_strInstrumentName",
    "m_nVolume",
    "m_nCanUseVolume",
    "m_nFrozenVolume",
    "m_nYesterdayVolume",
    "m_nOnRoadVolume",
    "m_nPosition",
    "m_nCanUsePosition",
    "m_dOpenPrice",
    "m_dAvgPrice",
    "m_dCostPrice",
    "m_strOrderSysID",
    "m_nOrderStatus",
    "m_nVolumeTotalOriginal",
    "m_nVolumeTraded",
    "m_dLimitPrice",
    "m_dTradedPrice",
    "m_strRemark",
    "m_strInsertTime",
    "m_strOrderTime",
    "m_nOrderTime",
    "m_nInsertTime",
    "m_strInsertDate",
    "m_strOrderDate",
    "m_nOrderDate",
    "m_nInsertDate",
    "m_strTradeTime",
    "m_nTradeTime",
    "m_nOffsetFlag",
    "m_nDirection",
    "m_strOptName",
    "m_nOrderType",
    "m_nEntrustType",
    "m_eEntrustType",
    "m_nOrderPriceType",
    "m_nPriceType",
    "account_id",
    "stock_code",
    "exchange_id",
    "stock_name",
    "volume",
    "can_use_volume",
    "open_price",
    "market_value",
    "total_asset",
    "cash",
    "InstrumentID",
    "ExchangeID",
    "InstrumentName",
    "Position",
    "CanUseVolume",
    "order_type",
    "offset_flag",
    "direction",
    "opt_name",
    "price_type",
    "order_date",
    "insert_date",
)


def _object_row(item):
    if item is None:
        return {}
    if isinstance(item, dict):
        return item
    row = {}
    try:
        for name in dir(item):
            if not name.startswith("m_"):
                continue
            try:
                row[name] = getattr(item, name)
            except Exception:
                pass
    except Exception:
        pass
    for name in _KNOWN_DETAIL_ATTRS:
        if name in row:
            continue
        try:
            val = getattr(item, name, None)
        except Exception:
            continue
        if val is not None and val != "":
            row[name] = val
    mapping = (
        ("m_strAccountID", "account_id"),
        ("m_dBalance", "total_asset"),
        ("m_dAvailable", "cash"),
        ("m_dFrozenCash", "frozen_cash"),
        ("m_dMarketValue", "market_value"),
        ("m_dStockValue", "market_value"),
        ("m_strInstrumentID", "stock_code"),
        ("InstrumentID", "stock_code"),
        ("m_strExchangeID", "exchange_id"),
        ("ExchangeID", "exchange_id"),
        ("m_strInstrumentName", "stock_name"),
        ("InstrumentName", "stock_name"),
        ("m_nVolume", "volume"),
        ("m_nPosition", "volume"),
        ("Position", "volume"),
        ("m_nYesterdayVolume", "yesterday_volume"),
        ("m_nOnRoadVolume", "on_road_volume"),
        ("m_strStockCode", "stock_code"),
        ("m_nCanUseVolume", "can_use_volume"),
        ("m_nCanUsePosition", "can_use_volume"),
        ("CanUseVolume", "can_use_volume"),
        ("m_dOpenPrice", "open_price"),
        ("m_dAvgPrice", "open_price"),
        ("m_dCostPrice", "open_price"),
        ("m_strOrderSysID", "order_sysid"),
        ("m_nOrderStatus", "order_status"),
        ("m_nVolumeTotalOriginal", "order_volume"),
        ("m_nVolumeTraded", "traded_volume"),
        ("m_dLimitPrice", "price"),
        ("m_dTradedPrice", "traded_price"),
        ("m_strRemark", "remark"),
        ("m_strInsertTime", "order_time"),
        ("m_strOrderTime", "order_time"),
        ("m_nOrderTime", "order_time"),
        ("m_nInsertTime", "order_time"),
        ("m_strTradeTime", "order_time"),
        ("m_nTradeTime", "order_time"),
        ("m_strInsertDate", "order_date"),
        ("m_strOrderDate", "order_date"),
        ("m_nOrderDate", "order_date"),
        ("m_nInsertDate", "order_date"),
        ("m_nOffsetFlag", "offset_flag"),
        ("m_nDirection", "direction"),
        ("m_strOptName", "opt_name"),
        ("m_nOrderType", "order_type"),
        ("m_nEntrustType", "order_type"),
        ("m_eEntrustType", "order_type"),
        ("m_nOrderPriceType", "price_type"),
        ("m_nPriceType", "price_type"),
    )
    for src, dst in mapping:
        if src in row and dst not in row:
            row[dst] = row[src]
    exch = str(row.get("exchange_id") or row.get("m_strExchangeID") or "").strip().upper()
    code = str(row.get("stock_code") or row.get("m_strInstrumentID") or "").strip().upper()
    if code and "." not in code and exch:
        row["stock_code"] = "%s.%s" % (code, exch)
    return row


def _is_detail_row_obj(item):
    """�ж��Ƿ�Ϊ�����ʽ�/�ֲ�/ί�ж��󣨶��ǿɵ�����������"""
    if item is None or isinstance(item, (str, bytes, int, float, bool)):
        return False
    if isinstance(item, dict):
        return True
    markers = (
        "m_strInstrumentID",
        "m_nVolume",
        "m_nPosition",
        "m_nYesterdayVolume",
        "m_nOnRoadVolume",
        "m_dBalance",
        "m_dAvailable",
        "m_strAccountID",
        "m_dMarketValue",
        "stock_code",
        "volume",
        "account_id",
        "InstrumentID",
        "Position",
    )
    for attr in markers:
        try:
            if getattr(item, attr, None) is not None:
                return True
        except Exception:
            continue
    return False


def _rows(raw):
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [_object_row(raw)]
    if isinstance(raw, (list, tuple)):
        out = []
        for item in raw:
            row = _object_row(item)
            if row:
                out.append(row)
        return out
    # �� QMT �����ط� list �� Vector ��װ���������������лᶪ��ֲ�
    if _is_detail_row_obj(raw):
        row = _object_row(raw)
        return [row] if row else []
    if hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes)):
        out = []
        try:
            for item in raw:
                row = _object_row(item)
                if row:
                    out.append(row)
            if out:
                return out
        except Exception:
            pass
    row = _object_row(raw)
    return [row] if row else []


def _raw_len(raw):
    if raw is None:
        return None
    try:
        return len(raw)
    except Exception:
        return None


def _diagnose_position_parse_miss(pos_raw, pos_rows, parsed_n):
    """raw �������ڽ������ʱ��ӡ����ԭ��"""
    raw_n = _raw_len(pos_raw)
    if not raw_n:
        return
    if parsed_n >= int(raw_n or 0) and parsed_n > 0:
        return
    drops = list(getattr(_parse_position_rows, "last_drops", None) or [])
    print(
        "[���׺���] �ֲֽ�����ȫ: raw_len=%s rows=%s parsed=%s drops=%s"
        % (raw_n, len(pos_rows or []), parsed_n, drops[:8])
    )
    if parsed_n > 0:
        return
    item = None
    try:
        item = pos_raw[0]
    except Exception:
        try:
            item = next(iter(pos_raw))
        except Exception:
            item = None
    names = []
    sample = {}
    if item is not None:
        try:
            names = [n for n in dir(item) if not str(n).startswith("__")][:50]
        except Exception:
            names = []
        for a in (
            "m_strInstrumentID",
            "InstrumentID",
            "stock_code",
            "m_nVolume",
            "m_nPosition",
            "volume",
            "Position",
            "m_nCanUseVolume",
            "m_strExchangeID",
            "��Ʊ���",
            "�ֲ�����",
        ):
            try:
                sample[a] = getattr(item, a, None)
            except Exception as e:
                sample[a] = "err:%s" % e
    print(
        "[���׺���] �ֲֽ���δ����: raw_type=%s raw_len=%s rows=%s attrs=%s sample=%s"
        % (type(pos_raw).__name__, raw_n, len(pos_rows or []), names, sample)
    )


def _norm_code(raw):
    code = str(raw or "").strip().upper()
    if not code:
        return ""
    if "." not in code and len(code) >= 6:
        if code.startswith("6"):
            return code[:6] + ".SH"
        if code.startswith(("4", "8", "920")):
            return code[:6] + ".BJ"
        return code[:6] + ".SZ"
    return code


def _resolve_account_id(ContextInfo, explicit=""):
    """�˺����ȣ���ʽ���� �� config.ini �� ContextInfo��

    ���˺ź���ֻ�� config.ini�������ŵĲ��� ContextInfo ���ܻ��Ǿɺţ�
    ������Ϊ׼������ results.json ����д�ɳֲ֡�
    """
    if explicit:
        return str(explicit).strip()
    try:
        import configparser

        ini_path = os.path.join(str(PROJECT_ROOT).rstrip("\\/"), "data", "config.ini")
        if PROJECT_ROOT and os.path.isfile(ini_path):
            cfg = configparser.ConfigParser()
            cfg.read(ini_path, encoding="utf-8")
            if cfg.has_option("Account", "account_id"):
                aid = str(cfg.get("Account", "account_id") or "").strip()
                if aid:
                    return aid
    except Exception:
        pass
    if ContextInfo is not None:
        for attr in ("accountID", "account_id", "account", "accid"):
            val = getattr(ContextInfo, attr, None)
            if val is not None and str(val).strip():
                return str(val).strip()
    return ""


def _trade_detail_fn(ContextInfo):
    cached = getattr(ContextInfo, "_ant_trade_detail_fn", None)
    if callable(cached):
        return cached

    candidates = []
    ctx_fn = getattr(ContextInfo, "ant_get_trade_detail_data", None)
    if callable(ctx_fn):
        candidates.append(("ctx.ant_get_trade_detail_data", ctx_fn))

    try:
        import sys

        for name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            mod_name = str(name)
            if "������������" not in mod_name:
                continue
            for attr in ("ant_get_trade_detail_data", "get_trade_detail_data"):
                fn = getattr(mod, attr, None)
                if callable(fn):
                    candidates.append(("entry.%s.%s" % (mod_name, attr), fn))
    except Exception:
        pass

    try:
        import builtins

        fn = getattr(builtins, "get_trade_detail_data", None)
        if callable(fn):
            candidates.append(("builtins.get_trade_detail_data", fn))
    except Exception:
        pass

    try:
        fn = get_trade_detail_data
        if callable(fn):
            candidates.append(("local.get_trade_detail_data", fn))
    except NameError:
        pass

    for attr in ("get_trade_detail_data", "ant_get_trade_detail_data"):
        fn = getattr(ContextInfo, attr, None)
        if callable(fn):
            candidates.append(("ctx.%s" % attr, fn))

    if candidates:
        src, fn = candidates[0]
        try:
            setattr(ContextInfo, "_ant_trade_detail_fn", fn)
            setattr(ContextInfo, "_ant_trade_detail_src", src)
            setattr(ContextInfo, "ant_get_trade_detail_data", fn)
        except Exception:
            pass
        return fn
    return None


def bind_trading_account(ContextInfo, account_id=""):
    """init �а󶨽����˺ţ�get_trade_detail_data ���ܷ����ʽ�/�ֲ֡�"""
    aid = _resolve_account_id(ContextInfo, account_id)
    if not aid:
        return False, "no_account_id"
    # �̳�Ҫ��ͬʱ���� account_type��ȱʡ����ͨ��Ʊ�˻�
    acct_type = ""
    for attr in ("account_type", "accountType", "acc_type"):
        val = getattr(ContextInfo, attr, None)
        if val is not None and str(val).strip():
            acct_type = str(val).strip()
            break
    if not acct_type:
        acct_type = "STOCK"
    try:
        setattr(ContextInfo, "account_type", acct_type)
        setattr(ContextInfo, "accountType", acct_type)
    except Exception:
        pass
    setter = getattr(ContextInfo, "set_account", None)
    if not callable(setter):
        return False, "no_set_account"
    try:
        setter(aid)
    except Exception as e:
        return False, "set_account_failed:%s" % e
    try:
        setattr(ContextInfo, "account_id", aid)
        setattr(ContextInfo, "accountID", aid)
        setattr(ContextInfo, "accid", aid)
    except Exception:
        pass
    return True, "%s/%s" % (aid, acct_type)


def _fetch_trade_detail(ContextInfo, account_id, data_type, strategy_names=None):
    """��ѯ������ϸ��ORDER/DEAL ���� strategyName=""������˵�ȫ���в�������ί�У���"""
    fn = _trade_detail_fn(ContextInfo)
    if not callable(fn):
        return []
    dtype_variants = []
    for val in (data_type, str(data_type).upper(), str(data_type).lower()):
        if val and val not in dtype_variants:
            dtype_variants.append(val)
    account_types = ("stock", "STOCK", "Stock", "credit", "CREDIT")
    account_ids = []
    for val in (account_id, str(account_id).strip()):
        if val and val not in account_ids:
            account_ids.append(val)
    strategies = []
    if strategy_names:
        for s in strategy_names:
            s = str(s or "").strip()
            if s and s not in strategies:
                strategies.append(s)
    best_rows = []
    for aid in account_ids:
        for account_type in account_types:
            for dtype in dtype_variants:
                calls = [(aid, account_type, dtype)]
                for sn in strategies:
                    calls.append((aid, account_type, dtype, sn))
                for args in calls:
                    try:
                        raw = fn(*args)
                        rows = _rows(raw)
                        if len(rows) > len(best_rows):
                            best_rows = rows
                        if rows and len(args) == 3:
                            return rows
                    except TypeError:
                        continue
                    except Exception:
                        continue
    return best_rows


def _diagnose_trade_detail(ContextInfo, account_id):
    global _DIAG_DONE
    if _DIAG_DONE:
        return
    _DIAG_DONE = True
    fn = _trade_detail_fn(ContextInfo)
    src = getattr(ContextInfo, "_ant_trade_detail_src", "")
    parts = [
        "aid=%s" % account_id,
        "src=%s" % (src or "none"),
        "fn=%s" % bool(fn),
        "do_back_test=%s" % getattr(ContextInfo, "do_back_test", "?"),
        "cache_account=%s" % bool(_CACHED_ACCOUNT),
        "cache_positions=%d" % len(_CACHED_POSITIONS or {}),
    ]
    if callable(fn):
        for args in (
            (account_id, "stock", "account"),
            (account_id, "STOCK", "ACCOUNT"),
            (account_id, "stock", "position"),
            (account_id, "STOCK", "POSITION"),
        ):
            try:
                raw = fn(*args)
                n = len(raw) if raw is not None else 0
                parts.append("%s->len=%s type=%s" % (args, n, type(raw).__name__))
            except Exception as e:
                parts.append("%s->err=%s" % (args, e))
    parts.append(
        "hint=ģ�ͽ�������ʵ��ģʽ;��QMT���׶����ѵ�¼���ʽ��˺�(�ǽ�����MiniQMT)"
    )
    print("[���׺���] �˻����: %s" % "; ".join(parts))


def _parse_account_row(row, account_id):
    total = float(_pick(row, "total_asset", "totalAsset", "m_dBalance", "m_dAsset", default=0) or 0)
    cash = float(_pick(row, "cash", "m_dAvailable", "m_dCash", "available", default=0) or 0)
    stock_mv = float(
        _pick(row, "stock_market_value", "market_value_stock", "m_dStockValue", "stock_value", default=0)
        or 0
    )
    market = float(
        _pick(row, "market_value", "marketValue", "m_dMarketValue", "m_dInstrumentValue", default=0)
        or 0
    )
    # ����ʽ��Ʊ��ֵʱ��֮Ϊ׼�����������˻� market_value
    if stock_mv > 0:
        market = stock_mv
    frozen = float(_pick(row, "frozen_cash", "frozenCash", "m_dFrozenCash", default=0) or 0)
    if market <= 0 and total > 0 and cash >= 0:
        market = max(0.0, total - cash)
    out = {
        "account_id": str(account_id),
        "total_asset": total,
        "cash": cash,
        "frozen_cash": frozen,
        "market_value": market,
        "updated_at": _now_iso(),
    }
    if stock_mv > 0:
        out["stock_market_value"] = stock_mv
    return out


def _int_qty(row, *keys, default=0):
    try:
        return int(float(_pick(row, *keys, default=default) or 0))
    except (TypeError, ValueError):
        return int(default or 0)


def _parse_position_rows(rows, account_id):
    out = {}
    drops = []
    for idx, row in enumerate(rows or []):
        if not isinstance(row, dict):
            row = _object_row(row)
        code = _norm_code(
            _pick(
                row,
                "stock_code",
                "instrumentID",
                "InstrumentID",
                "m_strInstrumentID",
                "m_strStockCode",
                "code",
                "֤ȯ����",
                "����",
            )
        )
        vol = _int_qty(
            row,
            "volume",
            "m_nVolume",
            "m_nPosition",
            "Position",
            "current_qty",
            "�ֲ�����",
            "��Ʊ���",
            "�ɷ����",
            "��ǰ�ֲ�",
            "nVolume",
            "qty",
        )
        if not code:
            drops.append(
                {
                    "i": idx,
                    "reason": "no_code",
                    "keys": list(row.keys())[:18] if isinstance(row, dict) else [],
                }
            )
            continue
        # ���Ϊ 0 Ҳ�������� QMT �ֲ�ҳ�����µ���������У��������Լ� 0���������ð���ֲ�
        yest = _int_qty(row, "yesterday_volume", "m_nYesterdayVolume", "���")
        on_road = _int_qty(row, "on_road_volume", "m_nOnRoadVolume", "��;����", "��;�ɷ�")
        can_use = _int_qty(
            row,
            "can_use_volume",
            "m_nCanUseVolume",
            "m_nCanUsePosition",
            "CanUseVolume",
            "enable_amount",
            "��������",
            "�������",
            default=vol,
        )
        open_px = float(
            _pick(
                row,
                "open_price",
                "cost_price",
                "m_dOpenPrice",
                "m_dAvgPrice",
                "m_dCostPrice",
                "m_dCost",
                "�ɱ���",
                default=0,
            )
            or 0
        )
        mv = float(_pick(row, "market_value", "m_dMarketValue", "��ֵ", default=0) or 0)
        name = str(
            _pick(
                row,
                "stock_name",
                "m_strInstrumentName",
                "InstrumentName",
                "instrument_name",
                "֤ȯ����",
                "֤ȯ���",
                default="",
            )
            or ""
        ).strip()
        prev = out.get(code)
        if prev:
            prev["volume"] = int(prev.get("volume") or 0) + int(vol)
            prev["can_use_volume"] = int(prev.get("can_use_volume") or 0) + int(can_use)
            if mv:
                prev["market_value"] = float(prev.get("market_value") or 0) + float(mv)
            if name and not prev.get("stock_name"):
                prev["stock_name"] = name
            drops.append({"i": idx, "reason": "merged", "code": code, "add_vol": vol})
            continue
        out[code] = {
            "account_id": str(account_id),
            "stock_code": code,
            "stock_name": name,
            "volume": vol,
            "can_use_volume": can_use,
            "open_price": open_px,
            "market_value": mv,
            "yesterday_volume": yest,
            "on_road_volume": on_road,
        }
    _parse_position_rows.last_drops = drops
    return out


def _status_text(code, traded_volume=0, volume=0):
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "δ֪"
    text = ORDER_STATUS_TEXT.get(c)
    if text:
        return text
    # δ��¼״̬�룺�óɽ����ƶϣ�������桸����-δ֪��
    try:
        tv = int(traded_volume or 0)
        ov = int(volume or 0)
    except (TypeError, ValueError):
        tv, ov = 0, 0
    if ov > 0 and tv >= ov:
        return "�ѳ�"
    if tv > 0:
        return "����"
    return "�ѱ�"


def _normalize_order_time(raw):
    """QMT ����Ϊ HHMMSS / HH:MM:SS / �������ַ�����ͳһ�� HH:MM:SS��"""
    if raw is None:
        return ""
    if isinstance(raw, (int, float)):
        try:
            n = int(raw)
            if n <= 0:
                return ""
            s = "%06d" % (n % 1000000)
            return "%s:%s:%s" % (s[0:2], s[2:4], s[4:6])
        except (TypeError, ValueError):
            return ""
    s = str(raw).strip()
    if not s or s in ("0", "None", "none"):
        return ""
    if "T" in s:
        return s.split("T", 1)[1][:8]
    if " " in s and ":" in s:
        # "2026-07-14 10:42:25" / "10:42:25"
        part = s.split(" ")[-1]
        return part[:8] if ":" in part else s
    if ":" in s:
        return s[:8]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 6:
        digits = digits[-6:]
        return "%s:%s:%s" % (digits[0:2], digits[2:4], digits[4:6])
    return s


def _normalize_order_date(raw):
    """QMT ί������ �� YYYY-MM-DD��ʧ�ܷ��ؿմ���"""
    if raw is None:
        return ""
    try:
        if isinstance(raw, datetime):
            return raw.strftime("%Y-%m-%d")
        if isinstance(raw, date):
            return raw.strftime("%Y-%m-%d")
        if isinstance(raw, (int, float)):
            n = int(raw)
            if n <= 0:
                return ""
            s = str(n)
            if len(s) >= 8:
                s = s[:8]
                return "%s-%s-%s" % (s[0:4], s[4:6], s[6:8])
            return ""
        s = str(raw).strip()
        if not s or s in ("0", "None", "none"):
            return ""
        if "T" in s:
            return s[:10]
        if " " in s and "-" in s:
            return s[:10]
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 8:
            digits = digits[:8]
            return "%s-%s-%s" % (digits[0:4], digits[4:6], digits[6:8])
    except Exception:
        return ""
    return ""


def _extract_order_at(raw, date_raw=None):
    """������������ί��ʱ�䣨ISO���������չ��ˣ�ʧ�ܷ��ؿմ���

    QMT ������� m_strInsertDate + m_strInsertTime����ϲ���
    """
    if raw is None and date_raw is None:
        return ""
    try:
        # ��������ʱ��� / �ַ���
        if isinstance(raw, (int, float)):
            n = float(raw)
            if n > 1e12:
                return datetime.fromtimestamp(n / 1000.0).strftime("%Y-%m-%dT%H:%M:%S")
            if n > 1e9:
                return datetime.fromtimestamp(n).strftime("%Y-%m-%dT%H:%M:%S")
            # �� HHMMSS�����ж���������ϲ�
            time_part = _normalize_order_time(raw)
            date_part = _normalize_order_date(date_raw)
            if date_part and time_part and ":" in time_part:
                return "%sT%s" % (date_part, time_part[:8])
            return ""
        s = str(raw).strip() if raw is not None else ""
        if s and s not in ("0", "None", "none"):
            if "T" in s:
                return s.replace("Z", "").split("+")[0][:19]
            if " " in s and "-" in s and ":" in s:
                return s[:19].replace(" ", "T")
            digits = "".join(ch for ch in s if ch.isdigit())
            if len(digits) >= 14:
                dt = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
            # ��ʱ�䣺�ϲ�����
            time_part = _normalize_order_time(s)
            date_part = _normalize_order_date(date_raw)
            if date_part and time_part and ":" in time_part:
                return "%sT%s" % (date_part, time_part[:8])
            return ""
        # ���������ֶ�
        date_part = _normalize_order_date(date_raw)
        if date_part:
            return "%sT00:00:00" % date_part
    except Exception:
        return ""
    return ""


def _parse_session_date(raw):
    """�� at/order_at �������ڣ��� HH:MM:SS ���� None��"""
    if raw is None:
        return None
    try:
        s = str(raw).strip()
        if not s:
            return None
        if "T" in s:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        if " " in s and "-" in s:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 8 and ("-" in s or len(digits) >= 14):
            return datetime.strptime(digits[:8], "%Y%m%d").date()
    except Exception:
        return None
    return None


def _is_session_order_rec(rec):
    """��ǰ�Ựί�У����գ�����һ������ 15:00 ���ҹ�е���������������"""
    if not isinstance(rec, dict):
        return False
    raw = rec.get("order_at") or rec.get("at") or ""
    d = _parse_session_date(raw)
    if d is None:
        # ��̨������ί���� �� �������ղ����������б�/����
        return False
    today = datetime.now().date()
    if d == today:
        return True
    # ҹ�д��ڣ���һ��Ȼ�� / ��һ������ 15:00 ������δ������һ����������
    try:
        tpart = str(raw)
        if "T" in tpart:
            hhmm = tpart.split("T", 1)[1][:8]
        elif " " in tpart:
            hhmm = tpart.split(" ", 1)[1][:8]
        else:
            hhmm = ""
        after_close = False
        if len(hhmm) >= 5 and hhmm[2] == ":":
            after_close = hhmm >= "15:00:00"
        if not after_close:
            return False
        now = datetime.now()
        if d == today - timedelta(days=1):
            return True
        # ����ĩ������������һ�����ա�ҹ�е������̺󶪵�
        last_td = None
        try:
            from utils.trading_day import last_tradeday_on_or_before, is_tradeday

            last_td = last_tradeday_on_or_before(today - timedelta(days=1))
            if last_td and d == last_td:
                if not is_tradeday(today) or now.time() < dt_time(9, 15):
                    return True
        except Exception:
            # QMT �ڿ����� utils�������ݵ������壨3 ����Ȼ�գ�
            if 0 < (today - d).days <= 3 and now.time() < dt_time(9, 15):
                return True
    except Exception:
        pass
    return False


def _prune_cached_orders():
    """�����ڴ��п���ί�л��档"""
    global _CACHED_ORDERS
    if not _CACHED_ORDERS:
        return
    keep = {}
    for sid, rec in list((_CACHED_ORDERS or {}).items()):
        if _is_session_order_rec(rec):
            keep[sid] = rec
    _CACHED_ORDERS = keep


def _prefer_richer_order(old, new):
    """�ϲ�����ͬ��ͬ��ί�У���������/ʱ����ֶΣ�״̬ȡ���µġ�

    DEAL �г�ȱ m_strInsertDate����ֱ�Ӹ��� ORDER �ᵼ�� order_at ��ʧ��
    ��󱻻Ự���˶�����UI �ڡ�ȫ�յ����롸��δ�ɵ���֮����˸��
    """
    if not isinstance(new, dict):
        return old if isinstance(old, dict) else {}
    if not isinstance(old, dict):
        return dict(new)
    out = dict(old)
    for k, v in new.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        out[k] = v
    # ��ʽ��ס����ʱ��
    for k in ("order_at", "at", "order_time", "order_date"):
        nv = new.get(k)
        ov = old.get(k)
        if (nv is None or (isinstance(nv, str) and not str(nv).strip())) and ov not in (None, ""):
            out[k] = ov
    try:
        ns = int(new.get("broker_status") or 0)
        os_ = int(old.get("broker_status") or 0)
        if ns >= os_:
            for k in ("broker_status", "broker_status_text", "traded_volume", "traded_price", "volume"):
                if new.get(k) is not None and new.get(k) != "":
                    out[k] = new[k]
        else:
            for k in ("broker_status", "broker_status_text", "traded_volume", "traded_price"):
                if old.get(k) is not None and old.get(k) != "":
                    out[k] = old[k]
    except (TypeError, ValueError):
        pass
    return out


def _upsert_cached_orders(parsed_list):
    """�����ֽ���������뻺�棨�����̨©���ض�ɾ�����е��յ�����"""
    global _CACHED_ORDERS
    for bo in parsed_list or []:
        if not isinstance(bo, dict):
            continue
        if not _is_session_order_rec(bo):
            # �����ڵ����У�����������ͬ��ͬ�Ŵ����ڰ汾���ϲ�����
            sid = str(bo.get("order_sysid") or "").strip()
            if not sid:
                continue
            old = _CACHED_ORDERS.get(sid)
            if old and _is_session_order_rec(old):
                merged = _prefer_richer_order(old, bo)
                if _is_session_order_rec(merged):
                    _CACHED_ORDERS[sid] = merged
            continue
        sid = str(bo.get("order_sysid") or "").strip()
        if not sid:
            continue
        _CACHED_ORDERS[sid] = _prefer_richer_order(_CACHED_ORDERS.get(sid), bo)
    _prune_cached_orders()
    return list((_CACHED_ORDERS or {}).values())


def _to_int(val, default=None):
    try:
        if val is None or val == "":
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def _order_type_text(code):
    c = _to_int(code)
    if c is None:
        return ""
    return ORDER_TYPE_TEXT.get(c, "")


def _is_ipo_subscribe(order_type=None, offset_flag=None, opt_name="", price_type=None):
    """�¹��깺��ҵ������ 86 / ���������깺 / OptName ���깺��

    ע�⣺ί��״̬ 86=��ȷ�ϣ����õ����깺���͡�
    """
    opt = str(opt_name or "")
    if "�깺" in opt:
        return True
    # xtconstant.IPO_SUBSCRIBE = 86��ҵ�����ͣ���״̬��
    if _to_int(order_type) == 86 or _to_int(offset_flag) == 86:
        return True
    # BROKER_PRICE_PROP_SUBSCRIBE = 54
    if _to_int(price_type) == 54:
        return True
    return False


def _resolve_order_side(row):
    """���� (side, order_type, offset_flag, direction, opt_name, price_type)��

    side: buy / sell / subscribe
    """
    opt = str(_pick(row, "opt_name", "m_strOptName", default="") or "")
    order_type = _pick(
        row,
        "order_type",
        "m_nOrderType",
        "m_nEntrustType",
        "m_eEntrustType",
        default=None,
    )
    offset_flag = _pick(row, "offset_flag", "m_nOffsetFlag", default=None)
    direction = _pick(row, "direction", "m_nDirection", default=None)
    price_type = _pick(
        row,
        "price_type",
        "m_nOrderPriceType",
        "m_nPriceType",
        default=None,
    )
    if _is_ipo_subscribe(order_type, offset_flag, opt, price_type):
        return "subscribe", order_type, offset_flag, direction, opt, price_type
    # OptName ���ȣ��ֻ�/�ⲿί�г��� direction=48 ȴʵΪ�������롸�޼�������ì��
    has_sell = "��" in opt
    has_buy = "��" in opt
    if has_sell and not has_buy:
        return "sell", order_type, offset_flag, direction, opt, price_type
    if has_buy and not has_sell:
        return "buy", order_type, offset_flag, direction, opt, price_type
    # ��ֵ������ STOCK_BUY/SELL(23/24)���� direction / offset
    ot = _to_int(order_type, -1)
    if ot == 24:
        return "sell", order_type, offset_flag, direction, opt, price_type
    if ot == 23:
        return "buy", order_type, offset_flag, direction, opt, price_type
    for cand in (direction, offset_flag):
        d = _to_int(cand, -1)
        if d in (49, 24, 1):
            return "sell", order_type, offset_flag, direction, opt, price_type
        if d in (48, 23, 0):
            return "buy", order_type, offset_flag, direction, opt, price_type
    return "buy", order_type, offset_flag, direction, opt, price_type


def _diag_order_fields_once(parsed):
    """�״ν���ί��ʱ��ѡ��ϣ�Ĭ�Ͼ�Ĭ������ˢһ���ѳɵ�����Ϣ������"""
    global _ORDER_FIELD_DIAG_DONE
    if _ORDER_FIELD_DIAG_DONE:
        return
    _ORDER_FIELD_DIAG_DONE = True
    # ��Ҫ�Ų�ί���ֶ�ӳ��ʱ�軷������ ANT_ORDER_FIELD_DIAG=1
    if str(os.environ.get("ANT_ORDER_FIELD_DIAG") or "").strip() not in ("1", "true", "TRUE"):
        return
    try:
        print(
            "[���׺���] ί���ֶ����: code=%s sysid=%s status=%s(%s) "
            "order_type=%s(%s) offset=%s direction=%s price_type=%s "
            "opt=%s side=%s remark=%s"
            % (
                parsed.get("stock_code"),
                parsed.get("order_sysid"),
                parsed.get("broker_status"),
                parsed.get("broker_status_text"),
                parsed.get("order_type"),
                parsed.get("order_type_text"),
                parsed.get("offset_flag"),
                parsed.get("direction"),
                parsed.get("price_type"),
                parsed.get("opt_name"),
                parsed.get("side"),
                parsed.get("remark"),
            )
        )
    except Exception:
        pass


def _parse_order_row(row, account_id=""):
    """��������ί��Ϊ�����л� dict��"""
    if not isinstance(row, dict):
        row = _object_row(row)
    # ״ֻ̬�� m_nOrderStatus / order_status�������� order_type(IPO=86) ����
    status = _pick(row, "order_status", "m_nOrderStatus", default=255)
    status = _to_int(status, 255)
    if status is None:
        status = 255
    code = _norm_code(
        _pick(
            row,
            "stock_code",
            "m_strInstrumentID",
            "instrumentID",
            "code",
            default="",
        )
    )
    sysid = str(_pick(row, "order_sysid", "m_strOrderSysID", default="") or "").strip()
    remark = str(_pick(row, "remark", "m_strRemark", "order_remark", default="") or "").strip()
    side, order_type, offset_flag, direction, opt, price_type = _resolve_order_side(row)
    order_type_i = _to_int(order_type)
    offset_i = _to_int(offset_flag)
    direction_i = _to_int(direction)
    price_type_i = _to_int(price_type)
    price = float(_pick(row, "price", "m_dLimitPrice", default=0) or 0)
    vol = int(float(_pick(row, "order_volume", "m_nVolumeTotalOriginal", "volume", default=0) or 0))
    traded_vol = int(float(_pick(row, "traded_volume", "m_nVolumeTraded", default=0) or 0))
    traded_px = float(_pick(row, "traded_price", "m_dTradedPrice", default=0) or 0)
    raw_time = _pick(
        row,
        "order_time",
        "m_strInsertTime",
        "m_strOrderTime",
        "m_nOrderTime",
        "m_nInsertTime",
        "m_strTradeTime",
        "m_nTradeTime",
        "InsertTime",
        "OrderTime",
        default="",
    )
    raw_date = _pick(
        row,
        "order_date",
        "insert_date",
        "m_strInsertDate",
        "m_strOrderDate",
        "m_nOrderDate",
        "m_nInsertDate",
        "InsertDate",
        "OrderDate",
        default="",
    )
    order_time = _normalize_order_time(raw_time)
    order_at = _extract_order_at(raw_time, raw_date)
    if not order_at:
        # ���ף���������ʱҲд�룬���ڻỰ����
        order_at = _extract_order_at(None, raw_date)
    stock_name = str(
        _pick(
            row,
            "stock_name",
            "m_strInstrumentName",
            "InstrumentName",
            "instrument_name",
            "֤ȯ����",
            "֤ȯ���",
            default="",
        )
        or ""
    ).strip()
    strategy_name = str(
        _pick(row, "strategy_name", "m_strStrategyName", "StrategyName", default="") or ""
    ).strip()
    if not strategy_name and side == "subscribe":
        strategy_name = "�¹��깺"
    elif not strategy_name and opt:
        # OptName �硸֤ȯ���롹����˵�������𸲸Ǳ��ز�����
        if "�깺" in opt:
            strategy_name = "�¹��깺"
    type_text = _order_type_text(order_type_i)
    if not type_text and side == "subscribe":
        type_text = "�¹��깺"
    parsed = {
        "account_id": str(account_id or _pick(row, "account_id", "m_strAccountID", default="") or ""),
        "order_sysid": sysid,
        "stock_code": code,
        "stock_name": stock_name,
        "side": side,
        "order_type": order_type_i if order_type_i is not None else "",
        "order_type_text": type_text,
        "offset_flag": offset_i if offset_i is not None else "",
        "direction": direction_i if direction_i is not None else "",
        "price_type": price_type_i if price_type_i is not None else "",
        "opt_name": opt,
        "price": price,
        "volume": vol,
        "traded_volume": traded_vol,
        "traded_price": traded_px,
        "broker_status": status,
        "broker_status_text": _status_text(status, traded_vol, vol),
        "remark": remark,
        "strategy_name": strategy_name,
        "order_time": order_time,
        "order_at": order_at,
        "updated_at": _now_iso(),
    }
    _diag_order_fields_once(parsed)
    return parsed


def _parse_order_rows(rows, account_id=""):
    out = []
    for row in rows or []:
        parsed = _parse_order_row(row, account_id)
        if parsed.get("order_sysid") or parsed.get("stock_code"):
            out.append(parsed)
    return out


# �����ս�̬�ڱ�����δ��ʵ passorder����ֹ�Ѻ�����̨�ɽ������Щ����
_LOCAL_SKIP_SYSIDS = frozenset(
    {
        "SKIPPED_MIN_BUY",
        "SKIPPED_BUY_WINDOW",
        "BAND_HARD_PASS",
        "ORDER_FAILED",
        "PO_BUILTIN",
        "NO_CASH",
        "MIN_BUY_AMOUNT",
        "NO_POSITION",
        "NOT_TRUE_BREAKTHROUGH",
        "CANCELLED",
        "PROBE_REMAIN_SKIPPED",
        "RETRY_NO_CASH",
    }
)


def _is_unique_broker_sysid(sysid) -> bool:
    s = str(sysid or "").strip()
    if not s or s in _LOCAL_SKIP_SYSIDS:
        return False
    if s.startswith("RETRY_NO_CASH"):
        return False
    if s.startswith("PO") and len(s) <= 24:
        return False
    return True


def _is_local_skip_order(local) -> bool:
    """�ʽ���/��С��������������������̨�ɽ��� task_id ����ϲ���"""
    if not isinstance(local, dict):
        return True
    if str(local.get("cash_block") or "").strip():
        return True
    if bool(local.get("buy_block_window")) or bool(local.get("cash_wait")):
        return True
    status = str(local.get("status") or "").strip().lower()
    if status in ("skipped", "waiting_cash"):
        return True
    sysid = str(local.get("order_sysid") or "").strip()
    if sysid and not _is_unique_broker_sysid(sysid):
        return True
    msg = str(local.get("msg") or "").strip().lower()
    if msg in (
        "no_cash",
        "order_below_min",
        "min_buy_amount",
        "band_hard_pass",
        "skipped",
    ):
        return True
    return False


def _remark_matches_local(remark, local):
    rem = str(remark or "").strip()
    if not rem:
        return False
    candidates = []
    for k in ("pass_uid", "user_order_id", "task_id"):
        v = str(local.get(k) or "").strip().replace(":", "_")
        if v:
            candidates.append(v)
            if len(v) > 32:
                candidates.append(v[-32:])
    for uid in candidates:
        if rem == uid or uid.endswith(rem) or rem.endswith(uid) or rem in uid or uid in rem:
            return True
    if "rule_" in rem:
        for uid in candidates:
            if rem in uid or uid.endswith(rem.split("rule_")[-1]):
                return True
    return False


def _match_broker_order(local, broker_orders):
    """�� remark(userOrderId) ���ȣ���� ����+����+���� ���뱾�� passorder ��¼��"""
    if not isinstance(local, dict) or not broker_orders:
        return None
    # ��������δ�µ���̨����ֹ�� remark/task_id �̵���������ʵ�ɽ�
    if _is_local_skip_order(local):
        return None
    for bo in broker_orders:
        if _remark_matches_local(bo.get("remark"), local):
            code_l = str(local.get("stock_code") or "").upper()
            code_b = str(bo.get("stock_code") or "").upper()
            if code_l and code_b and code_l.split(".")[0] != code_b.split(".")[0]:
                continue
            return bo
    code_l = str(local.get("stock_code") or "").upper().split(".")[0]
    side_l = str(local.get("side") or "buy").lower()
    px_l = float(local.get("price") or 0)
    vol_l = int(local.get("volume") or 0)
    for bo in broker_orders:
        code_b = str(bo.get("stock_code") or "").upper().split(".")[0]
        if code_l and code_b != code_l:
            continue
        if str(bo.get("side") or "").lower() != side_l:
            continue
        if vol_l and int(bo.get("volume") or 0) != vol_l:
            continue
        px_b = float(bo.get("price") or 0)
        if px_l > 0 and px_b > 0 and abs(px_l - px_b) > 1e-6:
            continue
        return bo
    return None


def _note_filled_leg_from_local_order(loc):
    """��̨����Ϊ�ѳ�ʱ������д�� filled_legs.json��"""
    if not isinstance(loc, dict):
        return
    # ��ȫ leg_key / rule_name���µ�ʱ����ֻ�� task_id��
    if not loc.get("leg_key") or not loc.get("rule_name"):
        try:
            import ant_filled_legs as _fl

            info = _fl.lookup_leg_from_armed(loc.get("task_id"))
            if info.get("leg_key") and not loc.get("leg_key"):
                loc["leg_key"] = info["leg_key"]
            if info.get("rule_name") and not loc.get("rule_name"):
                loc["rule_name"] = info["rule_name"]
        except Exception:
            try:
                from qmt_builtin.src import ant_filled_legs as _fl

                info = _fl.lookup_leg_from_armed(loc.get("task_id"))
                if info.get("leg_key") and not loc.get("leg_key"):
                    loc["leg_key"] = info["leg_key"]
                if info.get("rule_name") and not loc.get("rule_name"):
                    loc["rule_name"] = info["rule_name"]
            except Exception:
                pass
    try:
        import ant_filled_legs as _fl

        _fl.note_from_order_record(loc)
    except Exception:
        try:
            from qmt_builtin.src import ant_filled_legs as _fl

            _fl.note_from_order_record(loc)
        except Exception:
            pass


def merge_broker_orders_into_results(results, broker_orders):
    """д�� broker_orders��������� passorder ��¼����ʵ״̬��"""
    if not isinstance(results, dict):
        return False
    broker_orders = [bo for bo in list(broker_orders or []) if _is_session_order_rec(bo)]
    results["broker_orders"] = broker_orders
    local = results.get("orders")
    if not isinstance(local, list):
        local = []
        results["orders"] = local
    # ���� passorder ��¼Ҳ���Ự�ü������� UI ������������ǰ�ĵ�
    pruned_local = []
    for loc in local:
        if not isinstance(loc, dict):
            continue
        if _is_session_order_rec(loc):
            pruned_local.append(loc)
    if len(pruned_local) != len(local):
        results["orders"] = pruned_local
        local = pruned_local
    changed = bool(broker_orders is not None)
    used = set()
    by_sys = {}
    for bo in broker_orders:
        if not isinstance(bo, dict):
            continue
        sid = str(bo.get("order_sysid") or "").strip()
        if sid:
            by_sys[sid] = bo
    for loc in local:
        if not isinstance(loc, dict):
            continue
        # ��������/�ʽ����ڱ������������̨�ɽ����������� no_cash ���������ͬ�ţ�
        if _is_local_skip_order(loc):
            continue
        # ������ʵ��ͬ�ţ������¹�̨����ˢ��״̬��ҹ����� �ѱ���
        cur_sys = str(loc.get("order_sysid") or "").strip()
        if cur_sys and _is_unique_broker_sysid(cur_sys):
            used.add(cur_sys)
            bo = by_sys.get(cur_sys)
            if bo:
                for k in (
                    "broker_status",
                    "broker_status_text",
                    "traded_volume",
                    "traded_price",
                    "order_time",
                    "remark",
                ):
                    val = bo.get(k)
                    if val is None:
                        continue
                    if k == "order_time" and not str(val).strip():
                        continue
                    if loc.get(k) != val:
                        loc[k] = val
                        changed = True
                st = int(bo.get("broker_status") or 255)
                if st == 56:
                    internal = "filled"
                elif st == 57:
                    internal = "error"
                elif st in (54, 53):
                    internal = "cancelled"
                elif st in (50, 51, 52, 55, 48, 49):
                    internal = "submitted"
                else:
                    internal = str(loc.get("status") or "submitted")
                prev_status = str(loc.get("status") or "")
                if loc.get("status") != internal:
                    loc["status"] = internal
                    changed = True
                if internal == "filled" and prev_status != "filled":
                    _note_filled_leg_from_local_order(loc)
            continue
        pool = []
        for bo in broker_orders:
            sid = str(bo.get("order_sysid") or "").strip()
            if sid and sid in used:
                continue
            pool.append(bo)
        bo = _match_broker_order(loc, pool)
        if not bo:
            continue
        sid = str(bo.get("order_sysid") or "").strip()
        if sid:
            used.add(sid)
        for k in (
            "order_sysid",
            "broker_status",
            "broker_status_text",
            "traded_volume",
            "traded_price",
            "order_time",
            "remark",
        ):
            val = bo.get(k)
            if val is None:
                continue
            if k == "order_time" and not str(val).strip():
                continue
            if loc.get(k) != val:
                loc[k] = val
                changed = True
        # ��̨ȱʱ��ʱ�ñ��� passorder ��¼ʱ������д�� broker �й� UI չʾ
        # ע�⣺�й�̨ order_time ʱ��Ҫ�ñ��ؾ� at ����
        broker_ot = _normalize_order_time(bo.get("order_time") or "")
        if broker_ot:
            if loc.get("order_time") != broker_ot:
                loc["order_time"] = broker_ot
                changed = True
        else:
            loc_time = _normalize_order_time(loc.get("order_time") or loc.get("at") or "")
            if loc_time:
                if loc.get("order_time") != loc_time:
                    loc["order_time"] = loc_time
                    changed = True
                if not str(bo.get("order_time") or "").strip():
                    bo["order_time"] = loc_time
                    changed = True
        st = int(bo.get("broker_status") or 255)
        if st == 56:
            internal = "filled"
        elif st == 57:
            internal = "error"
        elif st in (54, 53):
            internal = "cancelled"
        elif st in (50, 51, 52, 55, 48, 49):
            internal = "submitted"
        else:
            internal = str(loc.get("status") or "submitted")
        prev_status = str(loc.get("status") or "")
        if loc.get("status") != internal:
            loc["status"] = internal
            changed = True
        if internal == "filled" and prev_status != "filled":
            _note_filled_leg_from_local_order(loc)
    if changed:
        results["updated_at"] = _now_iso()
    return changed



def apply_deals_to_results(results, deal_raw, account_id=""):
    """�ɽ���ϸ���ף�����ƥ�䵽�ı��ص���Ϊ�ѳɡ�"""
    if not isinstance(results, dict):
        return False
    deal_rows = _rows(deal_raw)
    if not deal_rows:
        return False
    like_orders = []
    for row in deal_rows:
        parsed = _parse_order_row(row, account_id)
        tv = _pick(row, "traded_volume", "m_nVolume", "volume", default=parsed.get("traded_volume") or 0)
        try:
            parsed["traded_volume"] = int(float(tv or 0))
        except (TypeError, ValueError):
            parsed["traded_volume"] = int(parsed.get("traded_volume") or 0)
        if not parsed.get("volume"):
            parsed["volume"] = parsed.get("traded_volume") or 0
        tp = _pick(row, "traded_price", "m_dPrice", "m_dTradePrice", "price", default=0)
        try:
            parsed["traded_price"] = float(tp or 0)
            if not parsed.get("price"):
                parsed["price"] = parsed["traded_price"]
        except (TypeError, ValueError):
            pass
        parsed["broker_status"] = 56
        parsed["broker_status_text"] = "�ѳ�"
        like_orders.append(parsed)
    # merge with existing broker_orders������ ORDER �ϵ� order_at������ DEAL ȱ���ڰѵ�ˢû��
    existing = list(results.get("broker_orders") or [])
    by_sys = {}
    for bo in existing + like_orders:
        if not isinstance(bo, dict):
            continue
        sid = str(bo.get("order_sysid") or "").strip()
        key = sid or ("tmp|%s|%s|%s" % (bo.get("stock_code"), bo.get("price"), bo.get("volume")))
        by_sys[key] = _prefer_richer_order(by_sys.get(key), bo)
    # ͬ�����ڴ滺�棬��ֹ��һ�� ORDER ©����ʱ���ѳɵ�
    try:
        _upsert_cached_orders(list(by_sys.values()))
    except Exception:
        pass
    return merge_broker_orders_into_results(results, list(by_sys.values()))


def apply_deal_callback_to_results(results, dealInfo, account_id=""):
    return apply_deals_to_results(results, dealInfo, account_id)


def on_order_callback(ContextInfo, orderInfo):
    """QMT order_callback������ί�п��ա�"""
    global _CACHED_ORDERS
    try:
        aid = str(_resolve_account_id(ContextInfo) or "").strip()
        parsed = _parse_order_row(orderInfo, aid)
        sysid = str(parsed.get("order_sysid") or "").strip()
        if sysid and _is_session_order_rec(parsed):
            _CACHED_ORDERS[sysid] = parsed
        elif sysid:
            # ���ղ�����ȷ�������ڻ���
            _CACHED_ORDERS.pop(sysid, None)
    except Exception as e:
        print("[���׺���] order_callback ����: %s" % e)


def apply_order_callback_to_results(results, orderInfo, account_id=""):
    """order_callback �� ���� results.broker_orders �뱾�� orders ״̬��"""
    if not isinstance(results, dict):
        return False
    aid = str(account_id or "").strip()
    parsed = _parse_order_row(orderInfo, aid)
    sysid = str(parsed.get("order_sysid") or "").strip()
    if sysid and _is_session_order_rec(parsed):
        _CACHED_ORDERS[sysid] = parsed
    elif sysid:
        _CACHED_ORDERS.pop(sysid, None)
    _prune_cached_orders()
    broker_list = list((_CACHED_ORDERS or {}).values())
    found = False
    for i, bo in enumerate(broker_list):
        if str(bo.get("order_sysid") or "") == sysid:
            broker_list[i] = parsed
            found = True
            break
    if not found and (sysid or parsed.get("stock_code")) and _is_session_order_rec(parsed):
        broker_list.append(parsed)
    return merge_broker_orders_into_results(results, broker_list)


def on_account_callback(ContextInfo, accountInfo):
    """QMT account_callback ��ڣ������ʽ���ա�"""
    global _CACHED_ACCOUNT
    try:
        row = _object_row(accountInfo)
        aid = str(
            _pick(row, "account_id", "m_strAccountID", default="")
            or _resolve_account_id(ContextInfo)
        ).strip()
        if not aid:
            return
        _CACHED_ACCOUNT = _parse_account_row(row, aid)
    except Exception as e:
        print("[���׺���] account_callback ����: %s" % e)


def on_position_callback(ContextInfo, positionInfo):
    """QMT position_callback ��ڣ�����ֲֿ��ա�"""
    global _CACHED_POSITIONS
    try:
        row = _object_row(positionInfo)
        aid = str(
            _pick(row, "account_id", "m_strAccountID", default="")
            or _resolve_account_id(ContextInfo)
        ).strip()
        code = _norm_code(
            _pick(
                row,
                "stock_code",
                "m_strInstrumentID",
                "InstrumentID",
                "instrumentID",
                "code",
                default="",
            )
        )
        vol = int(
            float(
                _pick(row, "volume", "m_nVolume", "m_nPosition", "Position", default=0)
                or 0
            )
        )
        if not code:
            return
        parsed = _parse_position_rows([row], aid)
        if not parsed:
            return
        rec = parsed.get(code) or next(iter(parsed.values()), None)
        if rec is None:
            return
        if vol <= 0:
            rec["volume"] = 0
            rec["can_use_volume"] = 0
            rec["market_value"] = 0.0
        _CACHED_POSITIONS[code] = rec
        try:
            import ant_position_entry_dates as _ped

            _ped.sync_from_positions(_CACHED_POSITIONS)
        except Exception:
            pass
        try:
            import ant_filled_legs as _fl

            _fl.sync_clear_from_positions(_CACHED_POSITIONS)
        except Exception:
            pass
    except Exception as e:
        print("[���׺���] position_callback ����: %s" % e)


def resolve_account_id(ContextInfo, explicit=""):
    return _resolve_account_id(ContextInfo, explicit)


def _apply_parsed_positions(results, positions):
    """�� trade_detail ��������������ǳֲ֣���ͬ���ڴ滺�棨���ղ���գ���"""
    global _CACHED_POSITIONS
    if not isinstance(results, dict):
        return False
    pos = positions if isinstance(positions, dict) else {}
    results["positions"] = pos
    _CACHED_POSITIONS.clear()
    if pos:
        _CACHED_POSITIONS.update(pos)
    # �����գ���ֲֿ���ά�����������ⲿ������
    try:
        import ant_position_entry_dates as _ped

        _ped.sync_from_positions(pos)
    except Exception:
        try:
            from qmt_builtin.src import ant_position_entry_dates as _ped

            _ped.sync_from_positions(pos)
        except Exception:
            pass
    # �ֲֹ���ʱ�����ִ���ȣ������´���������
    try:
        import ant_filled_legs as _fl

        _fl.sync_clear_from_positions(pos)
    except Exception:
        try:
            from qmt_builtin.src import ant_filled_legs as _fl

            _fl.sync_clear_from_positions(pos)
        except Exception:
            pass
    return True


def _account_stock_market_value(account):
    """ȡ��Ʊ����ֵ��������ʽ��Ʊ��ֵ�ֶΣ������� market_value���� QMT �˻��г����ھ�����"""
    if not isinstance(account, dict):
        return 0.0
    for key in (
        "stock_market_value",
        "market_value_stock",
        "m_dStockValue",
        "stock_value",
    ):
        try:
            v = float(account.get(key) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v
    try:
        return float(account.get("market_value") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_suspicious_empty_positions(account, positions_parsed):
    """
    ��ղ֣��ֲֿ��ҹ�Ʊ��ֵ��0�����д����ֽ𣩡� ���澯��
    ���ɣ��ֲֿյ���ֵ����ƫ�ߣ����ʽ���Ҫ����ֵ�࣬��ȫ�ֽ𣩡� �澯��
    �� market_value �������Ƶȷǹ�Ʊ�ʲ��������ֵ���á��ֽ� << ��ֵ���ս��������󱨡�
    """
    if positions_parsed:
        return False, "has_positions"
    acc = account if isinstance(account, dict) else {}
    if not acc:
        return False, "no_account"
    mv = _account_stock_market_value(acc)
    try:
        cash = float(acc.get("cash") or 0) + float(acc.get("frozen_cash") or 0)
    except (TypeError, ValueError):
        cash = 0.0
    try:
        total = float(acc.get("total_asset") or 0)
    except (TypeError, ValueError):
        total = 0.0
    # ��ղ� / ���ֽ���ֵ������ֵ
    if mv < _POSITION_ALERT_MV_THRESHOLD:
        return False, "flat_or_low_mv"
    # ȫ�ֽ�ղ�������ֵʱ���ֽ�ӽ����ʲ��򲻵�������
    if total > 0 and cash >= total * 0.85 and mv < total * 0.2:
        return False, "cash_dominant"
    # ���ɣ������Թ�Ʊ��ֵ���ֲ���Ϊ�գ�����ǰ bug ��̬���ֽ��� + ��ֵ�� + positions=[]��
    if cash >= mv:
        # �ֽ𲻵�����ֵʱ����ھ��������Լ��ֶε���ǿ��Ϊ�澯�����Ը澯����ֵ�ѳ���ֵ
        return True, "empty_pos_high_mv"
    return True, "empty_pos_high_mv_low_cash"


def _in_cn_equity_session(now=None):
    """A �ɳ��潻��ʱ�Σ������� 09:00�C15:30�������ݣ���������/ҹ�̲��㣩��

    ������ utils.trading_day.is_tradeday������ֻ����һ�����塪����������
    �����ռ��ջᰴ���� 1 Сʱ��ȴˢ Server����
    """
    now = now or datetime.now()
    try:
        from utils.trading_day import is_tradeday

        if not is_tradeday(now.date()):
            return False
    except Exception:
        # QMT ��ż���� utils���˻���ĩ�жϣ��Կ������з������գ�
        if now.weekday() >= 5:
            return False
    t = now.time()
    return dt_time(9, 0) <= t <= dt_time(15, 30)


def _position_alert_notify_cooldown_sec(now=None):
    if _in_cn_equity_session(now):
        return float(_POSITION_ALERT_NOTIFY_COOLDOWN_SEC)
    return float(_POSITION_ALERT_NOTIFY_COOLDOWN_OFFHOURS_SEC)


def _parse_iso_ts(raw):
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        if " " in s:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def _prev_notify_sent_at(results):
    """�� results.position_alert ȡ�ϴγɹ�����ʱ�䣨�������غ��Կɽ�������"""
    if not isinstance(results, dict):
        return None
    prev = results.get("position_alert")
    if not isinstance(prev, dict):
        return None
    return _parse_iso_ts(prev.get("notify_sent_at"))


def _clear_position_alert(results):
    global _POSITION_ALERT_ACTIVE
    _POSITION_ALERT_ACTIVE = False
    if not isinstance(results, dict):
        return
    prev = results.get("position_alert")
    keep_sent = None
    if isinstance(prev, dict):
        keep_sent = prev.get("notify_sent_at")
    if isinstance(prev, dict) and prev.get("active"):
        cleared = {
            "active": False,
            "cleared_at": _now_iso(),
            "reason": "positions_ok_or_flat",
        }
        if keep_sent:
            cleared["notify_sent_at"] = keep_sent
        results["position_alert"] = cleared
    elif "position_alert" in results and not (
        isinstance(prev, dict) and prev.get("active") is False
    ):
        cleared = {
            "active": False,
            "cleared_at": _now_iso(),
            "reason": "positions_ok_or_flat",
        }
        if keep_sent:
            cleared["notify_sent_at"] = keep_sent
        results["position_alert"] = cleared


def _notify_position_alert_once(title, body, results=None):
    cool = _position_alert_notify_cooldown_sec()
    # results ���̴����������ػ���� ant_server_chan �ڴ���ȴ��ҹ�����������
    last_dt = _prev_notify_sent_at(results)
    if last_dt is not None and cool > 0:
        age = (datetime.now() - last_dt).total_seconds()
        if age < cool:
            return "cooldown"
    try:
        try:
            import ant_server_chan as sct
        except ImportError:
            import qmt_builtin.ant_server_chan as sct
        r = sct.notify_alert(
            title,
            body,
            alert_key="qmt_position_empty_high_mv",
            cooldown_sec=cool,
        )
        if r.get("skipped"):
            return "cooldown"
        if r.get("success"):
            return "sent"
        return "fail:%s" % (r.get("message") or "")
    except Exception as e:
        return "err:%s" % e


def _update_position_alert(results, positions_parsed, extra=None):
    """
    д�� results.position_alert������ʱ�ղָ澯����־���� + ��ѡ Server������
    �ֲָֻ�����ղ�ʱ��� active��
    """
    global _LAST_POSITION_ALERT_LOG_TS, _POSITION_ALERT_ACTIVE
    if not isinstance(results, dict):
        return
    acc = results.get("account") if isinstance(results.get("account"), dict) else {}
    suspicious, reason = _is_suspicious_empty_positions(acc, positions_parsed)
    mv = _account_stock_market_value(acc)
    try:
        cash = float(acc.get("cash") or 0)
    except (TypeError, ValueError):
        cash = 0.0
    try:
        total = float(acc.get("total_asset") or 0)
    except (TypeError, ValueError):
        total = 0.0
    pos_n = len(positions_parsed or {})
    extra = extra if isinstance(extra, dict) else {}

    if not suspicious:
        if _POSITION_ALERT_ACTIVE or (
            isinstance(results.get("position_alert"), dict)
            and results["position_alert"].get("active")
        ):
            _clear_position_alert(results)
        return

    now = time.time()
    prev_alert = results.get("position_alert") if isinstance(results.get("position_alert"), dict) else {}
    keep_sent = prev_alert.get("notify_sent_at") if isinstance(prev_alert, dict) else None
    alert = {
        "active": True,
        "reason": reason,
        "market_value": mv,
        "cash": cash,
        "total_asset": total,
        "parsed_positions": pos_n,
        "threshold": _POSITION_ALERT_MV_THRESHOLD,
        "message": (
            "position empty but market_value=%.2f �� check QMT �ֲ�/����"
            % mv
        ),
        "updated_at": _now_iso(),
    }
    if keep_sent:
        alert["notify_sent_at"] = keep_sent
    for k, v in extra.items():
        if v is not None:
            alert[k] = v
    results["position_alert"] = alert
    _POSITION_ALERT_ACTIVE = True

    should_log = (now - float(_LAST_POSITION_ALERT_LOG_TS or 0)) >= float(
        _POSITION_ALERT_LOG_INTERVAL_SEC
    )
    if should_log:
        _LAST_POSITION_ALERT_LOG_TS = now
        print(
            "[�˻�] ���� �ֲ�Ϊ�յ���Ʊ��ֵ=%.2f cash=%.2f "
            "total=%.2f parsed=%d �� ���� QMT �ֲ�/���� (%s)"
            % (mv, cash, total, pos_n, reason)
        )
        notify_r = _notify_position_alert_once(
            "��QMT�ֲֲ�ѯ�쳣",
            "�ֲ�Ϊ�յ���Ʊ��ֵ=%.2f����ֵ>=%.0f��\n�ֽ�=%.2f ���ʲ�=%.2f\n"
            "���� QMT �ֲ���������ģ�ͽ��ס�\nԭ��=%s"
            % (mv, _POSITION_ALERT_MV_THRESHOLD, cash, total, reason),
            results=results,
        )
        alert["notify"] = notify_r
        if notify_r == "sent":
            alert["notify_sent_at"] = _now_iso()
        results["position_alert"] = alert



def _probe_bj_sectors_once(ContextInfo):
    """һ����̽�Ȿ�� QMT ����������Ƿ���ã�д�� data/bj_sector_probe.json��"""
    global _BJ_SECTOR_PROBE_DONE
    if _BJ_SECTOR_PROBE_DONE:
        return
    _BJ_SECTOR_PROBE_DONE = True
    try:
        import json

        data_dir = ""
        try:
            from ant_qmt_paths import DATA_DIR

            data_dir = str(DATA_DIR or "")
        except Exception:
            pass
        if not data_dir and PROJECT_ROOT:
            data_dir = os.path.join(str(PROJECT_ROOT).rstrip("\\/"), "data")
        if not data_dir:
            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

        owners = []
        if ContextInfo is not None:
            owners.append(("ctx", ContextInfo))
        try:
            import builtins

            owners.append(("builtins", builtins))
        except Exception:
            pass

        sector_candidates = (
            "\u4eac\u5e02A\u80a1",  # ����A��
            "\u6caa\u6df1\u4eacA\u80a1",  # ���A��
            "\u5317\u4ea4\u6240",  # ������
            "\u5317\u4ea4\u6240A\u80a1",  # ������A��
            "BJ",
            "\u4eacA\u80a1",  # ��A��
            "\u6caa\u6df1A\u80a1",  # ����A�ɣ����գ�
        )
        sector_counts = {}
        samples = {}
        for sec in sector_candidates:
            best_n = -1
            best_sample = []
            src = ""
            for label, owner in owners:
                fn = getattr(owner, "get_stock_list_in_sector", None)
                if not callable(fn):
                    continue
                try:
                    raw = fn(sec) or []
                except Exception:
                    continue
                try:
                    n = len(raw)
                except Exception:
                    n = 0
                if n > best_n:
                    best_n = n
                    src = label
                    try:
                        best_sample = [str(x) for x in list(raw)[:8]]
                    except Exception:
                        best_sample = []
            sector_counts[sec] = {"n": max(0, best_n), "source": src}
            samples[sec] = best_sample

        matched_names = []
        for label, owner in owners:
            fn = getattr(owner, "get_sector_list", None)
            if not callable(fn):
                continue
            try:
                sl = fn() or []
            except Exception:
                continue
            for s in sl:
                t = str(s)
                if any(k in t for k in ("\u4eac", "\u5317\u4ea4", "BJ", "bj")):
                    if t not in matched_names:
                        matched_names.append(t)
            if matched_names:
                break

        payload = {
            "probed_at": _now_iso(),
            "snapshot_version": ACCOUNT_SNAPSHOT_VERSION,
            "matched_sector_names": matched_names,
            "sector_counts": sector_counts,
            "samples": samples,
        }
        out_path = os.path.join(data_dir, "bj_sector_probe.json")
        parent = os.path.dirname(out_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, out_path)
        # ̽������д�� bj_sector_probe.json������ˢ������־
    except Exception:
        pass


def _positions_match_account(positions, account_id: str) -> dict:
    """ֻ�����뵱ǰ�˺�һ�µĳֲ֣�ȱ account_id �ľɼ�¼Ҳ������"""
    aid = str(account_id or "").strip()
    out = {}
    for code, rec in (positions or {}).items():
        if not isinstance(rec, dict):
            continue
        rid = str(rec.get("account_id") or "").strip()
        if aid and rid and rid != aid:
            continue
        if aid and not rid:
            continue
        out[code] = rec
    return out


def apply_trade_detail_raw(ContextInfo, results, acc_raw, pos_raw, account_id="", order_raw=None, deal_raw=None):
    """����ļ��ѵ��� get_trade_detail_data���˴�������д�� results��"""
    global _CACHED_ACCOUNT, _CACHED_ORDERS, _CACHED_POSITIONS
    try:
        _probe_bj_sectors_once(ContextInfo)
    except Exception:
        pass
    if not isinstance(results, dict):
        return False, "results_not_dict"
    aid = str(account_id or _resolve_account_id(ContextInfo)).strip()
    if not aid:
        return False, "no_account_id"

    switched = _clear_account_caches_if_switched(aid)
    if switched:
        try:
            bind_trading_account(ContextInfo, aid)
        except Exception:
            pass
        results["account"] = {
            "account_id": aid,
            "total_asset": 0.0,
            "cash": 0.0,
            "frozen_cash": 0.0,
            "market_value": 0.0,
            "updated_at": _now_iso(),
        }
        results["positions"] = {}

    wrote = False
    kept_pos_cache = False
    if _CACHED_ACCOUNT:
        cached_aid = str((_CACHED_ACCOUNT or {}).get("account_id") or "").strip()
        if cached_aid and cached_aid != aid:
            _CACHED_ACCOUNT = None
        else:
            results["account"] = dict(_CACHED_ACCOUNT)
            wrote = True

    # �κ�·��д��ǰ���ȶ��� results �����˺Ų����ֲ�
    cur_pos = results.get("positions") if isinstance(results.get("positions"), dict) else {}
    cleaned = _positions_match_account(cur_pos, aid)
    if cleaned != cur_pos:
        results["positions"] = cleaned
        wrote = True

    acc_rows = _rows(acc_raw)
    pos_rows = _rows(pos_raw)
    if acc_rows:
        results["account"] = _parse_account_row(acc_rows[0], aid)
        wrote = True
    # pos_raw �� None����ѯ�ѷ������ս��Ĭ���������ǣ�������ֵ��ʾ�в��ҽ���Ϊ 0�����ȱ������棬���� API/��������ʧ��Ĩ�֡�
    positions = {}
    alert_positions = {}
    if pos_raw is not None:
        positions = _parse_position_rows(pos_rows, aid)
        alert_positions = positions
        _diagnose_position_parse_miss(pos_raw, pos_rows, len(positions))
        acc = results.get("account") if isinstance(results.get("account"), dict) else {}
        market = _account_stock_market_value(acc)
        if (not positions) and market >= _POSITION_ALERT_MV_THRESHOLD and _CACHED_POSITIONS:
            kept = _positions_match_account(_CACHED_POSITIONS, aid)
            if kept:
                kept_pos_cache = True
                results["positions"] = dict(kept)
                wrote = True
            else:
                _apply_parsed_positions(results, positions)
                wrote = True
        else:
            _apply_parsed_positions(results, positions)
            wrote = True
    elif _CACHED_POSITIONS:
        kept = _positions_match_account(_CACHED_POSITIONS, aid)
        if kept:
            results["positions"] = dict(kept)
            kept_pos_cache = True
            wrote = True
            alert_positions = dict(kept)
        else:
            # �ֲֲ�ѯδ����ʱ����Ҫ����չʾ����˺žɲ�
            if results.get("positions"):
                results["positions"] = {}
                wrote = True
            alert_positions = {}

    order_rows = _rows(order_raw) if order_raw is not None else []
    _prune_cached_orders()
    if order_raw is None and _CACHED_ORDERS:
        broker_orders = list(_CACHED_ORDERS.values())
    else:
        parsed_orders = _parse_order_rows(order_rows, aid)
        if parsed_orders:
            # ���뻺�棬�������滻����̨ż��©����ʱ�����Ѽ����յ�
            broker_orders = _upsert_cached_orders(parsed_orders)
        else:
            _prune_cached_orders()
            broker_orders = list(_CACHED_ORDERS.values()) if _CACHED_ORDERS else []
    # ���б�Ҳд�룬��������������Ϊ����δ��ѯ��
    if merge_broker_orders_into_results(results, broker_orders):
        wrote = True
    results["order_query"] = {
        "queried": True,
        "order_raw_is_none": order_raw is None,
        "raw_len": _raw_len(order_raw) if order_raw is not None else None,
        "parsed": len(broker_orders or []),
        "deal_raw_is_none": deal_raw is None,
        "deal_raw_len": _raw_len(deal_raw) if deal_raw is not None else None,
        "updated_at": _now_iso(),
    }
    results["position_query"] = {
        "queried": True,
        "pos_raw_is_none": pos_raw is None,
        "raw_type": type(pos_raw).__name__ if pos_raw is not None else "none",
        "raw_len": _raw_len(pos_raw) if pos_raw is not None else None,
        "row_len": len(pos_rows or []),
        "parsed": len(positions or {}) if pos_raw is not None else len((_CACHED_POSITIONS or {})),
        "kept_cache": kept_pos_cache,
        "drops": list(getattr(_parse_position_rows, "last_drops", None) or [])[:8],
        "updated_at": _now_iso(),
    }
    if deal_raw is not None:
        if apply_deals_to_results(results, deal_raw, aid):
            wrote = True

    if not wrote:
        parts = [
            "aid=%s" % aid,
            "acc_type=%s" % type(acc_raw).__name__,
            "pos_type=%s" % type(pos_raw).__name__,
            "acc_len=%s" % (_raw_len(acc_raw) if acc_raw is not None else "none"),
            "pos_len=%s" % (_raw_len(pos_raw) if pos_raw is not None else "none"),
        ]
        print("[���׺���] �˻����(���): %s" % "; ".join(parts))
        return False, "trade_detail_empty"

    # �澯���ݣ����ν������ĳֲ֣�����չʾ�û��棩����ղ�+��ֵ��0 ���澯��
    if pos_raw is not None:
        _update_position_alert(
            results,
            alert_positions,
            extra={
                "raw_len": _raw_len(pos_raw),
                "kept_cache": kept_pos_cache,
                "source": "apply_trade_detail_raw",
            },
        )
    results["updated_at"] = _now_iso()
    return True, "ok"


_CACHED_ACCOUNT = None
_CACHED_POSITIONS = {}
_BOUND_ACCOUNT_ID = ""


def _clear_account_caches_if_switched(aid: str) -> bool:
    """���˺ź�����ɺ��ʽ�/�ֲֻ��棬�������д�� results.json��"""
    global _CACHED_ACCOUNT, _CACHED_POSITIONS, _BOUND_ACCOUNT_ID, _CACHED_ORDERS
    new_aid = str(aid or "").strip()
    old_aid = str(_BOUND_ACCOUNT_ID or "").strip()
    if not new_aid:
        return False
    if old_aid and old_aid != new_aid:
        _CACHED_ACCOUNT = None
        _CACHED_POSITIONS = {}
        try:
            _CACHED_ORDERS = {}
        except Exception:
            pass
        print("[�˻�] ���л� %s �� %s������վɳֲֻ���" % (old_aid, new_aid))
        _BOUND_ACCOUNT_ID = new_aid
        return True
    _BOUND_ACCOUNT_ID = new_aid
    return False


def sync_account_snapshot_to_results(ContextInfo, results, account_id=""):
    """���ʽ�/�ֲ�/ί��д�� results��"""
    global _CACHED_ACCOUNT, _CACHED_ORDERS
    if not isinstance(results, dict):
        return False, "results_not_dict"
    aid = _resolve_account_id(ContextInfo, account_id)
    if not aid:
        return False, "no_account_id"
    switched = _clear_account_caches_if_switched(aid)
    if switched:
        try:
            bind_trading_account(ContextInfo, aid)
        except Exception:
            pass
        # �ɺſ����������������ϵͳ��������ʾ��һ���ֲ�
        results["account"] = {
            "account_id": aid,
            "total_asset": 0.0,
            "cash": 0.0,
            "frozen_cash": 0.0,
            "market_value": 0.0,
            "updated_at": _now_iso(),
        }
        results["positions"] = {}

    wrote = False
    if _CACHED_ACCOUNT:
        cached_aid = str((_CACHED_ACCOUNT or {}).get("account_id") or "").strip()
        if cached_aid and cached_aid != aid:
            _CACHED_ACCOUNT = None
        else:
            results["account"] = dict(_CACHED_ACCOUNT)
            wrote = True

    acc_rows = _fetch_trade_detail(ContextInfo, aid, "account")
    pos_rows = _fetch_trade_detail(ContextInfo, aid, "position")
    order_rows = _fetch_trade_detail(
        ContextInfo,
        aid,
        "order",
        strategy_names=("����-��������", "����-��������", "����-ͻ������", "����-ͻ������", "����-��������", "����-��������", "����-��������", "����-��������", "����-��������", "����-��������", "����-��ʱ���", "����-ҹ������", "����-ҹ������", "����-��ǰ����", "����-��ǰ����", "����-��ǰȷ��", "����-��ǰ����", "����-�����µ�"),
    )
    deal_rows = _fetch_trade_detail(
        ContextInfo,
        aid,
        "deal",
        strategy_names=("����-��������", "����-��������", "����-ͻ������", "����-ͻ������", "����-��������", "����-��������", "����-��������", "����-��������", "����-��������", "����-��������", "����-��ʱ���", "����-ҹ������", "����-ҹ������", "����-��ǰ����", "����-��ǰ����", "����-��ǰȷ��", "����-��ǰ����", "����-�����µ�"),
    )

    if acc_rows:
        results["account"] = _parse_account_row(acc_rows[0], aid)
        wrote = True
    # �ֲֲ�ѯ����������ǣ��ղ� / ȫ 0 Ҳ��գ�����ͬ�����棬�������������ʾ�ɹ���
    positions = _parse_position_rows(pos_rows, aid)
    acc_probe = results.get("account") if isinstance(results.get("account"), dict) else {}
    market = _account_stock_market_value(acc_probe)
    kept_cache = False
    if not positions and market >= _POSITION_ALERT_MV_THRESHOLD and _CACHED_POSITIONS:
        kept = _positions_match_account(_CACHED_POSITIONS, aid)
        if kept:
            kept_cache = True
            results["positions"] = dict(kept)
        else:
            _apply_parsed_positions(results, positions)
    else:
        _apply_parsed_positions(results, positions)
    wrote = True
    results["position_query"] = {
        "queried": True,
        "raw_len": len(pos_rows or []),
        "parsed": len(positions or {}),
        "kept_cache": kept_cache,
        "updated_at": _now_iso(),
    }
    _update_position_alert(
        results,
        positions,
        extra={
            "raw_len": len(pos_rows or []),
            "kept_cache": kept_cache,
            "source": "sync_account_snapshot",
        },
    )

    broker_orders = _parse_order_rows(order_rows, aid)
    if broker_orders:
        broker_orders = _upsert_cached_orders(broker_orders)
    else:
        _prune_cached_orders()
        broker_orders = list(_CACHED_ORDERS.values()) if _CACHED_ORDERS else []
    if merge_broker_orders_into_results(results, broker_orders):
        wrote = True
    results["order_query"] = {
        "queried": True,
        "parsed": len(broker_orders or []),
        "updated_at": _now_iso(),
    }
    if deal_rows:
        if apply_deals_to_results(results, deal_rows, aid):
            wrote = True

    if not wrote:
        _diagnose_trade_detail(ContextInfo, aid)
        return False, "trade_detail_empty"

    results["updated_at"] = _now_iso()
    return True, "ok"
