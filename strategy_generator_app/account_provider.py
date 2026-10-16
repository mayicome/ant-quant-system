"""
账户资金查询：为策略生成器提供 总资金、可用资金、持仓列表。
builtin 模式的持仓优先读今天的 data/results.json（交易系统从策略日志写入）；
不是今天的快照时，再从 QMT 模型交易导出。资金概览仍可读 results.json。
mini 模式需 path_qmt + account_id 连接 xt_trader。
"""

import configparser
import os
import sys
import time
from typing import Dict, Any, List, Optional, Tuple

try:
    from repo_path import ensure_repo_root_on_sys_path, repo_root
except ImportError:
    def repo_root() -> str:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def ensure_repo_root_on_sys_path() -> str:
        root = repo_root()
        if root in sys.path:
            try:
                sys.path.remove(root)
            except ValueError:
                pass
        sys.path.insert(0, root)
        return root

ensure_repo_root_on_sys_path()


_QMT_MIN_COOLDOWN_SEC = 1.0
_QMT_START_SLEEP_SEC = 1.0
_QMT_CONNECT_RETRIES = 5
_LAST_TRADER_STOP_TS = 0.0


def _maybe_cooldown_before_start() -> None:
    """QMT Trader stop 后，避免立刻重建连接导致偶发 rc=-1。"""
    global _LAST_TRADER_STOP_TS
    elapsed = time.time() - _LAST_TRADER_STOP_TS
    if elapsed < _QMT_MIN_COOLDOWN_SEC:
        time.sleep(_QMT_MIN_COOLDOWN_SEC - elapsed)


def _safe_stop(trader) -> None:
    """尽量 stop，避免残留连接；记录 stop 时间用于下一次冷却。"""
    global _LAST_TRADER_STOP_TS
    if trader is None:
        return
    try:
        trader.stop()
    except Exception:
        pass
    _LAST_TRADER_STOP_TS = time.time()


def _read_account_ini() -> Tuple[Optional[configparser.ConfigParser], str, str, str]:
    """返回 (cfg, path_qmt, account_id, qmt_mode)；cfg 为 None 表示读失败。"""
    import configparser

    root = repo_root()
    try:
        from utils.app_config import config_ini_path, ensure_app_config_ini

        ensure_app_config_ini(root)
        config_path = config_ini_path(root)
    except Exception:
        config_path = os.path.join(root, "data", "config.ini")
    if not os.path.isfile(config_path):
        return None, "", "", "builtin"
    cfg = configparser.ConfigParser()
    cfg.read(config_path, encoding="utf-8")
    if "Account" not in cfg:
        return cfg, "", "", "builtin"
    path_qmt = (cfg["Account"].get("path_qmt") or "").strip()
    account_id = (cfg["Account"].get("account_id") or "").strip()
    qmt_mode = (cfg["Account"].get("qmt_mode") or "builtin").strip().lower()
    return cfg, path_qmt, account_id, qmt_mode


_GJZQ_POS_CACHE: Dict[str, Any] = {"at": 0.0, "ok": False, "avail": {}, "total": {}, "note": ""}


def _code6(raw: Any) -> str:
    text = str(raw or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        return digits[:6]
    return digits.zfill(6) if digits else ""


def _load_today_formula_positions() -> Optional[Dict[str, Any]]:
    """今天由交易系统写入的策略日志持仓。文件缺失、来源不对或不是今天，返回 None。"""
    try:
        from datetime import date

        from utils.ant_rules_io_ext import default_paths, load_json

        _, results_path = default_paths(repo_root())
        if not results_path or not os.path.isfile(results_path):
            return None
        data = load_json(results_path)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("source") or "") != "formula_log":
        return None
    updated = str(data.get("updated_at") or "")[:10]
    if updated != date.today().isoformat():
        return None
    positions = data.get("positions")
    if not isinstance(positions, dict):
        return None
    return positions


def _gjzq_position_tables() -> Tuple[Optional[Dict[str, int]], Optional[Dict[str, int]], Optional[Dict[str, Dict[str, Any]]], str]:
    """读持仓。优先今天的策略日志快照，否则导出 QMT 模型交易。短时间内复用上一次结果。"""
    now = time.time()
    cached = _GJZQ_POS_CACHE
    if cached.get("ok") and now - float(cached.get("at") or 0) < 20.0:
        return cached["avail"], cached["total"], cached["backtest"], str(cached.get("note") or "")
    positions = _load_today_formula_positions()
    from_log = positions is not None
    note = ""
    if not from_log:
        try:
            from utils.qmt_model_trade_readonly import last_note, read_holdings

            positions = read_holdings()
            note = last_note() or ""
        except Exception as e:
            note = "%s: %s" % (type(e).__name__, e)
            positions = None
    if positions is None:
        cached.update(at=now, ok=False, note=note)
        return None, None, None, "QMT未读到持仓: %s" % (note or "失败")
    avail: Dict[str, int] = {}
    total: Dict[str, int] = {}
    backtest: Dict[str, Dict[str, Any]] = {}
    for raw, row in (positions or {}).items():
        code = _code6(raw)
        if not code or not isinstance(row, dict):
            continue
        can_use = int(row.get("can_use_volume") or 0)
        volume = int(row.get("volume") or 0)
        if volume < can_use:
            volume = can_use
        cost = float(row.get("open_price") or row.get("cost") or 0)
        avail[code] = avail.get(code, 0) + can_use
        if volume > 0:
            total[code] = total.get(code, 0) + volume
        if can_use > 0 or volume > 0:
            prev = backtest.get(code)
            if prev:
                ov, oc = int(prev["volume"]), float(prev["cost"])
                nv = ov + can_use
                backtest[code] = {
                    "volume": nv,
                    "cost": round((ov * oc + can_use * cost) / nv, 2) if nv else cost,
                }
            else:
                backtest[code] = {"volume": can_use, "cost": round(cost, 2)}
    label = "策略日志持仓" if from_log else "QMT持仓"
    note = "%s %d 只，可卖>=100 的 %d 只" % (
        label,
        len(total),
        sum(1 for v in avail.values() if v >= 100),
    )
    cached.update(at=now, ok=True, avail=avail, total=total, backtest=backtest, note=note)
    return avail, total, backtest, note


def _builtin_snapshot_positions() -> Tuple[Dict[str, int], Dict[str, Dict[str, Any]], str]:
    """builtin 下从 QMT 模型交易读持仓，返回 (可用股数, 回测用持仓, debug)。"""
    avail, _total, backtest, note = _gjzq_position_tables()
    if avail is None or backtest is None:
        return {}, {}, note
    return avail, backtest, note


def _get_trader_and_account():
    """
    读取主项目配置并连接 QMT，返回 (trader, account) 或 (None, None)。
    builtin 且未配置 path_qmt 时直接返回 (None, None)，由 results.json 路径处理。
    调用方负责在完成后调用 trader.stop()。
    """
    _, path_qmt, account_id, qmt_mode = _read_account_ini()
    if not account_id:
        return None, None
    if qmt_mode in ("builtin", "standalone") and not path_qmt:
        return None, None
    if not path_qmt:
        return None, None
    root = repo_root()
    config_path = os.path.join(root, "data", "config.ini")
    if not os.path.isfile(config_path):
        return None, None
    try:
        ensure_repo_root_on_sys_path()
        import xtquant.xttrader as xttrader
        from xtquant.xttype import StockAccount
        session = int(time.time())
        _maybe_cooldown_before_start()
        trader = xttrader.XtQuantTrader(path_qmt, session)
        trader.start()
        time.sleep(_QMT_START_SLEEP_SEC)
        # 连接可能因 QMT 尚未就绪、网络抖动等暂时失败；做小范围重试避免“经常失败”
        last_rc = None
        for attempt in range(_QMT_CONNECT_RETRIES):
            try:
                rc = trader.connect()
                last_rc = rc
            except Exception:
                rc = -999
                last_rc = rc
            if rc == 0:
                break
            if attempt < _QMT_CONNECT_RETRIES - 1:
                time.sleep(min(1.5, 0.6 * (attempt + 1)))
        if last_rc != 0:
            _safe_stop(trader)
            return None, None
        account = StockAccount(account_id, "STOCK")
        return trader, account
    except Exception:
        return None, None


def get_account_info() -> Dict[str, float]:
    """
    读取主项目配置并尝试连接 QMT 查询资产。
    返回: {"total_asset": float, "cash": float}，即总资金、可用资金；
    取不到或未配置时键为 0.0。
    """
    out = {"total_asset": 0.0, "cash": 0.0}
    _, path_qmt, account_id, qmt_mode = _read_account_ini()
    if qmt_mode in ("builtin", "standalone") and not path_qmt:
        try:
            from utils.ant_rules_io_ext import default_paths, load_account_positions_snapshot

            _, results_path = default_paths(repo_root())
            asset, _ = load_account_positions_snapshot(results_path)
            if isinstance(asset, dict):
                out["total_asset"] = float(asset.get("total_asset") or 0)
                out["cash"] = float(asset.get("cash") or 0)
            elif asset is not None:
                out["total_asset"] = float(getattr(asset, "total_asset", 0) or 0)
                out["cash"] = float(getattr(asset, "cash", 0) or 0)
        except Exception:
            pass
        return out
    trader, account = _get_trader_and_account()
    if trader is None or account is None:
        return out
    try:
        asset = trader.query_stock_asset(account)
        if asset is not None and hasattr(asset, "total_asset") and hasattr(asset, "cash"):
            out["total_asset"] = float(asset.total_asset)
            out["cash"] = float(asset.cash)
    except Exception:
        pass
    finally:
        _safe_stop(trader)
    return out


def get_positions() -> List[str]:
    """
    读取主项目配置并连接 QMT 查询当前持仓，返回 6 位股票代码列表（去重）。
    未配置或取不到时返回 []。
    """
    codes = []
    _, path_qmt, account_id, qmt_mode = _read_account_ini()
    if qmt_mode in ("builtin", "standalone") and not path_qmt:
        vol_map, _, _ = _builtin_snapshot_positions()
        return sorted(vol_map.keys())
    trader, account = _get_trader_and_account()
    if trader is None or account is None:
        return codes
    try:
        position_list = trader.query_stock_positions(account)
        if not position_list:
            return codes
        seen = set()
        for pos in position_list:
            raw = getattr(pos, "stock_code", None) or ""
            # 转为 6 位代码
            c = (raw.split(".")[0] if "." in raw else raw).strip()
            if len(c) >= 6:
                c = c[:6]
            elif len(c) > 0:
                c = c.zfill(6)
            if c and c not in seen:
                seen.add(c)
                codes.append(c)
    except Exception:
        pass
    finally:
        _safe_stop(trader)
    return codes


def get_positions_with_volume() -> Dict[str, int]:
    """
    读取主项目配置并连接 QMT 查询当前持仓，返回 { 6位代码: 可用数量 }。
    未配置或取不到时返回 {}。
    若首次查询为空，会短时重试 2 次（间隔 1 秒），避免 QMT 尚未就绪导致误报 0 只。
    """
    out = {}
    _, path_qmt, account_id, qmt_mode = _read_account_ini()
    if qmt_mode in ("builtin", "standalone") and not path_qmt:
        vol_map, _, _ = _builtin_snapshot_positions()
        return vol_map
    trader, account = _get_trader_and_account()
    if trader is None or account is None:
        return out
    try:
        position_list = None
        for attempt in range(3):
            position_list = trader.query_stock_positions(account)
            if position_list:
                break
            if attempt < 2:
                time.sleep(1)
        if not position_list:
            return out
        for pos in position_list:
            raw = getattr(pos, "stock_code", None) or ""
            c = (raw.split(".")[0] if "." in raw else raw).strip()
            if len(c) >= 6:
                c = c[:6]
            elif len(c) > 0:
                c = c.zfill(6)
            vol = int(getattr(pos, "can_use_volume", 0) or 0)
            if c:
                out[c] = out.get(c, 0) + vol
    except Exception:
        pass
    finally:
        _safe_stop(trader)
    return out


def get_positions_baseline() -> Dict[str, int]:
    """
    返回 { 6位代码: 总仓位基准 }（本轮累计买入；半仓用）。
    按当前总持仓相对上次快照增量维护，见 utils.position_baseline。
    """
    try:
        from utils.position_baseline import sync_baselines_from_volumes
    except ImportError:
        from position_baseline import sync_baselines_from_volumes  # type: ignore
    return sync_baselines_from_volumes(get_positions_total_volume())


def get_positions_total_volume() -> Dict[str, int]:
    """
    返回 { 6位代码: 当前持股数量 }（含当日买入、T+1 尚不可卖）。
    取不到 volume 字段时回退 can_use_volume。
    """
    out: Dict[str, int] = {}
    _, path_qmt, account_id, qmt_mode = _read_account_ini()
    if qmt_mode in ("builtin", "standalone") and not path_qmt:
        _avail, total, _bt, _note = _gjzq_position_tables()
        return dict(total or {})
    trader, account = _get_trader_and_account()
    if trader is None or account is None:
        return out
    try:
        position_list = None
        for attempt in range(3):
            position_list = trader.query_stock_positions(account)
            if position_list:
                break
            if attempt < 2:
                time.sleep(1)
        if not position_list:
            return out
        for pos in position_list:
            raw = getattr(pos, "stock_code", None) or ""
            c = (raw.split(".")[0] if "." in raw else raw).strip()
            if len(c) >= 6:
                c = c[:6]
            elif len(c) > 0:
                c = c.zfill(6)
            avail = int(getattr(pos, "can_use_volume", 0) or 0)
            total = int(getattr(pos, "volume", 0) or 0)
            if total < avail:
                total = avail
            if c and total > 0:
                out[c] = out.get(c, 0) + total
    except Exception:
        pass
    finally:
        _safe_stop(trader)
    return out


def get_positions_with_volume_debug() -> Tuple[Dict[str, int], str]:
    """
    带诊断信息的注入持仓查询。
    返回: (positions_dict, debug_info)
    用途：当策略生成系统显示“注入持仓：共 0 只”时，定位究竟是配置未就绪、QMT未连接、还是查询返回空。
    """
    out: Dict[str, int] = {}
    debug_lines: List[str] = []

    _, path_qmt, account_id, qmt_mode = _read_account_ini()
    root = repo_root()
    config_path = os.path.join(root, "data", "config.ini")
    debug_lines.append(f"config_path={config_path} exists={os.path.isfile(config_path)}")
    debug_lines.append(f"qmt_mode={qmt_mode} path_qmt_set={bool(path_qmt)} account_id_set={bool(account_id)}")

    if not os.path.isfile(config_path):
        return out, "配置文件不存在，无法连接QMT。"

    if not account_id:
        return out, "account_id 未配置。"

    if qmt_mode in ("builtin", "standalone") and not path_qmt:
        vol_map, _, snap_dbg = _builtin_snapshot_positions()
        debug_lines.append(snap_dbg)
        return vol_map, "\n".join(debug_lines)

    if not path_qmt:
        return out, "mini 模式下 path_qmt 未配置。"

    try:
        ensure_repo_root_on_sys_path()

        import xtquant.xttrader as xttrader
        from xtquant.xttype import StockAccount

        session = int(time.time())
        _maybe_cooldown_before_start()
        trader = xttrader.XtQuantTrader(path_qmt, session)
        debug_lines.append("XtQuantTrader created, calling start()...")
        trader.start()
        time.sleep(_QMT_START_SLEEP_SEC)
        # 连接可能因 QMT 尚未就绪等原因暂时失败；重试并把每次 rc 都写进 debug
        last_rc = None
        for attempt in range(_QMT_CONNECT_RETRIES):
            try:
                rc = trader.connect()
            except Exception as e:
                rc = -999
                debug_lines.append(f"trader.connect() attempt={attempt+1} exception={type(e).__name__}:{e}")
            debug_lines.append(f"trader.connect() attempt={attempt+1} rc={rc}")
            last_rc = rc
            if rc == 0:
                break
            if attempt < _QMT_CONNECT_RETRIES - 1:
                time.sleep(min(1.5, 0.6 * (attempt + 1)))

        if last_rc != 0:
            _safe_stop(trader)
            return out, "\n".join(debug_lines + [f"QMT连接失败，返回码 rc={last_rc}。"])

        account = StockAccount(account_id, "STOCK")

        position_list = None
        for attempt in range(3):
            position_list = trader.query_stock_positions(account)
            debug_lines.append(f"query_stock_positions attempt={attempt+1} len={len(position_list) if position_list else 0}")
            if position_list:
                break
            if attempt < 2:
                time.sleep(1)

        if not position_list:
            return out, "\n".join(debug_lines + ["查询返回空持仓。"])

        total_items = len(position_list)
        nonzero_vol_codes = 0
        total_can_use_volume = 0

        for pos in position_list:
            raw = getattr(pos, "stock_code", None) or ""
            c = (raw.split(".")[0] if "." in raw else raw).strip()
            if len(c) >= 6:
                c = c[:6]
            elif len(c) > 0:
                c = c.zfill(6)

            vol = int(getattr(pos, "can_use_volume", 0) or 0)
            if c:
                out[c] = out.get(c, 0) + vol
                total_can_use_volume += vol
                if vol != 0:
                    nonzero_vol_codes += 1

        # out 里是聚合后的代码数量
        debug_lines.append(f"total_position_items={total_items}")
        debug_lines.append(f"aggregated_codes={len(out)} nonzero_vol_codes={nonzero_vol_codes}")
        debug_lines.append(f"total_can_use_volume_sum={total_can_use_volume}")
        # 贴几个例子方便定位
        if out:
            top = sorted(out.items(), key=lambda kv: kv[1], reverse=True)[:5]
            debug_lines.append("top5_vol=" + ", ".join([f"{k}:{v}" for k, v in top]))

        return out, "\n".join(debug_lines)

    except Exception as e:
        debug_lines.append(f"Exception: {type(e).__name__}: {e}")
        return out, "\n".join(debug_lines)

    finally:
        # 无论成功失败，都尽量 stop，避免残留连接
        if "trader" in locals() and trader is not None:
            _safe_stop(trader)


def get_positions_for_backtest() -> Dict[str, Dict[str, Any]]:
    """
    读取 QMT 当前持仓，返回回测用初始持仓：{ 6位代码: {"volume": 可用数量, "cost": 成本价} }。
    成本价优先用 QMT 的 open_price，取不到则为 0（需在回测界面手动填写或忽略）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    _, path_qmt, account_id, qmt_mode = _read_account_ini()
    if qmt_mode in ("builtin", "standalone") and not path_qmt:
        _, bt_map, _ = _builtin_snapshot_positions()
        return bt_map
    trader, account = _get_trader_and_account()
    if trader is None or account is None:
        return out
    try:
        position_list = trader.query_stock_positions(account)
        if not position_list:
            return out
        for pos in position_list:
            raw = getattr(pos, "stock_code", None) or ""
            c = (raw.split(".")[0] if "." in raw else raw).strip()
            if len(c) >= 6:
                c = c[:6]
            elif len(c) > 0:
                c = c.zfill(6)
            vol = int(getattr(pos, "can_use_volume", 0) or 0)
            cost = float(getattr(pos, "open_price", 0) or 0)
            if c:
                if c in out:
                    # 同代码多笔合并：数量相加，成本按数量加权
                    ov, oc = out[c]["volume"], out[c]["cost"]
                    nv = ov + vol
                    out[c]["volume"] = nv
                    out[c]["cost"] = round((ov * oc + vol * cost) / nv, 2) if nv else cost
                else:
                    out[c] = {"volume": vol, "cost": round(cost, 2)}
    except Exception:
        pass
    finally:
        _safe_stop(trader)
    return out
