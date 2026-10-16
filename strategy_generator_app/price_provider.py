"""
行情获取：为策略生成器提供 当前价、昨收 及关键价格点（涨跌停、均线重合点、前高前低、布林带、今开最高最低等）。

实盘 builtin：东财现价 + daily_cache 关键价；不再等 results.json。mini 仍走 xtdata tick。
回测日线仅 data/daily_cache（builtin 无 xtdata 回退，见 backtest/data_provider.py）。"""

import os
import sys
import time
import math
from datetime import datetime, time as dt_time
from typing import List, Dict, Any

try:
    from repo_path import ensure_repo_root_on_sys_path
except ImportError:
    def ensure_repo_root_on_sys_path() -> str:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root in sys.path:
            try:
                sys.path.remove(root)
            except ValueError:
                pass
        sys.path.insert(0, root)
        return root

ensure_repo_root_on_sys_path()

_PRICE_PROVIDER_DEBUG_PRINTED = False
_LIVE_SOURCE_LOGGED = False


def _today_open_from_full_tick(xtdata: Any, full_code: str) -> float:
    """与 key_price_calculator 一致：从快照取开盘价字段。"""
    try:
        tmap = xtdata.get_full_tick([full_code])
        tk = tmap.get(full_code) if isinstance(tmap, dict) else None
        cand = None
        if isinstance(tk, dict):
            cand = (
                tk.get("open")
                or tk.get("openPrice")
                or tk.get("open_price")
                or tk.get("todayOpen")
            )
        elif tk is not None:
            cand = (
                getattr(tk, "open", None)
                or getattr(tk, "openPrice", None)
                or getattr(tk, "open_price", None)
                or getattr(tk, "todayOpen", None)
            )
        if cand is not None:
            f = float(cand)
            if f > 0 and not math.isnan(f):
                return f
    except Exception:
        pass
    return 0.0


def _ensure_live_today_open(result_row: Dict[str, Any], full_code: str) -> None:
    """
    实盘策略生成：KeyPriceCalculator 已尽量填「今开盘」，若仍为缺失/0（QMT 偶发），
    此处对 full_tick 做短暂重试，最后用昨收兜底，避免生成策略时今开盘为空。
    """
    try:
        cur = float(result_row.get("今开盘") or 0)
    except (TypeError, ValueError):
        cur = 0.0
    if cur > 0:
        return

    ensure_repo_root_on_sys_path()
    try:
        import xtquant.xtdata as xtdata
    except Exception:
        xtdata = None
    if xtdata is None:
        _fill_open_from_pre_close(result_row)
        return

    for attempt in range(3):
        v = _today_open_from_full_tick(xtdata, full_code)
        if v > 0:
            result_row["今开盘"] = v
            return
        time.sleep(0.12 + 0.08 * attempt)

    _fill_open_from_pre_close(result_row)


def _fill_open_from_pre_close(result_row: Dict[str, Any]) -> None:
    try:
        pc = result_row.get("昨收盘")
        if pc is None or pc == "":
            pc = result_row.get("pre_close")
        pc = float(pc or 0)
        if pc > 0:
            result_row["今开盘"] = pc
    except (TypeError, ValueError):
        pass


def _code_with_suffix(code_6: str) -> str:
    code_6 = (code_6 or "").strip()
    if len(code_6) < 6:
        code_6 = code_6.zfill(6)
    if code_6.startswith("6"):
        return f"{code_6}.SH"
    if code_6.startswith(("0", "3")):
        return f"{code_6}.SZ"
    return f"{code_6}.SH"


def get_prices(stock_codes_6: List[str]) -> Dict[str, Dict[str, float]]:
    """
    批量获取股票的当前价、昨收。
    stock_codes_6: 6 位代码列表。
    返回: { "000001": {"current": float, "pre_close": float}, ... }
    若某只股票取不到，则无该 key 或 current/pre_close 为 0。
    """
    result = {}
    for code in stock_codes_6:
        code = (code or "").strip()
        if len(code) < 6:
            code = code.zfill(6)
        if code:
            result[code] = {"current": 0.0, "pre_close": 0.0}

    if not result:
        return result

    try:
        ensure_repo_root_on_sys_path()
        import xtquant.xtdata as xtdata
    except Exception:
        return result

    full_codes = [_code_with_suffix(c) for c in result.keys()]
    try:
        tick_map = xtdata.get_full_tick(full_codes)
    except Exception:
        return result

    if not tick_map:
        return result

    for code_6, full_code in zip(list(result.keys()), full_codes):
        tick = tick_map.get(full_code) if isinstance(tick_map, dict) else None
        if not tick:
            continue
        current = 0.0
        pre_close = 0.0
        if isinstance(tick, dict):
            current = float(tick.get("lastPrice") or tick.get("last_price") or 0)
            pre_close = float(tick.get("lastClose") or tick.get("pre_close") or 0)
            # 集合竞价 lastPrice 常为 0：用买卖一档参考价
            if current <= 0:
                try:
                    from utils.ant_rules_io_ext import extract_tick_price

                    current = float(extract_tick_price(tick) or 0)
                except Exception:
                    bid = tick.get("bidPrice")
                    ask = tick.get("askPrice")
                    try:
                        b0 = float(bid[0]) if isinstance(bid, (list, tuple)) and bid else 0.0
                    except (TypeError, ValueError, IndexError):
                        b0 = 0.0
                    try:
                        a0 = float(ask[0]) if isinstance(ask, (list, tuple)) and ask else 0.0
                    except (TypeError, ValueError, IndexError):
                        a0 = 0.0
                    if b0 > 0 and a0 > 0:
                        current = b0 if abs(b0 - a0) < 1e-9 else (b0 + a0) / 2.0
                    elif b0 > 0:
                        current = b0
                    elif a0 > 0:
                        current = a0
        else:
            current = getattr(tick, "lastPrice", None) or getattr(tick, "last_price", 0) or 0
            pre_close = getattr(tick, "lastClose", None) or getattr(tick, "pre_close", 0) or 0
            current = float(current)
            pre_close = float(pre_close)
        result[code_6]["current"] = current
        result[code_6]["pre_close"] = pre_close

    return result


def _use_builtin_live_feed() -> bool:
    try:
        from qmt_mode_config import use_builtin_price_feed
    except ImportError:
        try:
            from strategy_generator_app.qmt_mode_config import use_builtin_price_feed
        except ImportError:
            return False
    return use_builtin_price_feed()


def _opening_auction_wait_timeout_sec() -> float:
    """集合竞价/开盘附近：冷订阅后 QMT 出快照更慢，拉长等待。"""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        now = datetime.now()
    t = now.time()
    # 09:15–09:30：竞价参考价/开盘价逐步就绪；给足时间让 shadow 订阅+seed
    if dt_time(9, 15) <= t < dt_time(9, 30):
        return 90.0
    return 30.0


def _tencent_symbol(full: str) -> str:
    num, _, market = str(full or "").partition(".")
    if market == "SH":
        prefix = "sh"
    elif market == "BJ":
        prefix = "bj"
    else:
        prefix = "sz"
    return prefix + num


def _fetch_tencent_spots(fulls: List[str]) -> Dict[str, Dict[str, float]]:
    """腾讯 qt.gtimg.cn。字段：3现价 4昨收 5今开 33最高 34最低。"""
    import requests

    os.environ.setdefault("NO_PROXY", "*")
    out: Dict[str, Dict[str, float]] = {}
    if not fulls:
        return out
    sess = requests.Session()
    sess.trust_env = False
    sess.headers["User-Agent"] = "Mozilla/5.0"
    for i in range(0, len(fulls), 40):
        chunk = fulls[i : i + 40]
        url = "https://qt.gtimg.cn/q=" + ",".join(_tencent_symbol(c) for c in chunk)
        try:
            resp = sess.get(url, timeout=5.0)
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="replace")
        except Exception:
            continue
        for line in text.split(";"):
            if "~" not in line or "=" not in line:
                continue
            body = line.split("=", 1)[1].strip().strip('"')
            parts = body.split("~")
            if len(parts) < 35:
                continue
            num = str(parts[2] or "").zfill(6)
            full = next((c for c in chunk if c.split(".")[0] == num), "")
            if not full:
                continue
            try:
                price = float(parts[3] or 0)
                pre = float(parts[4] or 0)
                opn = float(parts[5] or 0)
                high = float(parts[33] or 0)
                low = float(parts[34] or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            out[full] = {
                "price": price,
                "pre_close": pre,
                "open": opn,
                "high": high,
                "low": low,
            }
    return out


def _load_public_live_prices(codes: List[str]) -> Dict[str, Dict[str, float]]:
    """东财现价，缺的用腾讯补。QMT 不再写 results.json。"""
    from utils.eastmoney_spot import fetch_spot_quotes
    from utils.strategy_pool_watch import code_6_to_full

    fulls = []
    for c6 in codes:
        full = code_6_to_full(c6)
        if full:
            fulls.append(full)
    quotes: Dict[str, Dict[str, float]] = {}
    for _attempt in range(2):
        for i in range(0, len(fulls), 80):
            try:
                quotes.update(fetch_spot_quotes(fulls[i : i + 80], timeout=5.0))
            except Exception:
                continue
        if len(quotes) >= len(fulls):
            break
        time.sleep(0.4)
    missing_fulls = [c for c in fulls if c not in quotes]
    if missing_fulls:
        try:
            quotes.update(_fetch_tencent_spots(missing_fulls))
        except Exception:
            pass
    out: Dict[str, Dict[str, float]] = {}
    for c6 in codes:
        q = quotes.get(code_6_to_full(c6)) or {}
        try:
            price = float(q.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            continue
        try:
            pre = float(q.get("pre_close") or 0)
        except (TypeError, ValueError):
            pre = 0.0
        row = {
            "current": price,
            "pre_close": pre,
            "最新价": price,
            "今开盘": float(q.get("open") or 0),
            "今日最高": float(q.get("high") or 0),
            "今日最低": float(q.get("low") or 0),
        }
        if pre > 0:
            row["昨收盘"] = pre
        out[c6] = row
    return out


def _get_prices_with_key_points_builtin(
    stock_codes_6: List[str],
    on_progress=None,
) -> tuple:
    """builtin：东财现价 → KeyPriceCalculator(daily_cache)。不等 results.json。"""
    from utils.strategy_pool_watch import code_6_to_full

    def _progress(msg: str) -> None:
        if callable(on_progress):
            try:
                on_progress(str(msg))
            except Exception:
                pass

    errors: List[str] = []
    codes = [
        (c or "").strip().zfill(6)[:6]
        for c in (stock_codes_6 or [])
        if (c or "").strip()
    ]
    if not codes:
        return {}, errors

    from datetime import date as _date
    from utils.data_sync_request import (
        submit_daily_requests,
        wait_daily_cache_pool,
        _pump_ui_events,
    )

    _progress(f"正在取公开行情现价 {len(codes)} 只…")
    live = _load_public_live_prices(codes)
    missing = [code_6_to_full(c) for c in codes if c not in live]
    if not live:
        miss_show = ", ".join(missing[:12])
        if len(missing) > 12:
            miss_show += f", ...（共 {len(missing)} 只）"
        errors.append(f"[行情未就绪] 东财现价未取到: {miss_show}")
        return {}, errors
    if missing:
        miss_show = ", ".join(missing[:12])
        if len(missing) > 12:
            miss_show += f", ...（共 {len(missing)} 只）"
        errors.append(
            f"[行情部分就绪] 东财有价 {len(live)}/{len(codes)}，跳过: {miss_show}"
        )
        codes = [c for c in codes if c in live]
    _progress(f"现价就绪 {len(live)}/{len(codes) + len(missing)}，等待日线…")

    full_codes = [_code_with_suffix(c) for c in codes]
    submit_daily_requests(full_codes, through_date=_date.today())

    def _daily_prog(ready_n: int, total_n: int, label: str) -> None:
        _progress(f"等待{label}缓存 {ready_n}/{total_n}")

    _, missing_daily = wait_daily_cache_pool(
        codes, through_date=_date.today(), on_progress=_daily_prog
    )

    if missing_daily:
        # 有任意日线即可算昨收/涨跌停；仅真正无 CSV 的才阻塞开盘买入
        still_missing: List[str] = []
        short_ok: List[str] = []
        for fc in missing_daily:
            try:
                from utils.daily_cache_reader import load_daily_from_cache

                df = load_daily_from_cache(fc, through_date=_date.today())
            except Exception:
                df = None
            if df is not None and len(df) >= 1:
                short_ok.append(fc)
            else:
                still_missing.append(fc)
        if short_ok:
            errors.append(
                f"[daily_cache] {len(short_ok)} 只历史不足120日但仍有日线，"
                f"已用现有K线计算昨收/涨跌停（均线点可能缺失）"
            )
        if still_missing:
            miss_show = ", ".join(still_missing[:8])
            if len(still_missing) > 8:
                miss_show += f", ...（共 {len(still_missing)} 只）"
            errors.append(
                f"[daily_cache] {len(still_missing)} 只日线仍缺失: {miss_show}\n"
                f"  已提交大 QMT 按需同步；若持续为空请确认模型交易已运行"
            )
            # 重新提交，清掉误标 short_history(vol=0) / soft miss
            submit_daily_requests(still_missing, through_date=_date.today())

    result = {c: dict(live.get(c) or {}) for c in codes}
    ensure_repo_root_on_sys_path()
    try:
        from key_price_calculator import KeyPriceCalculator
    except Exception as e:
        errors.append(f"关键价格计算器加载失败: {e}")
        return result, errors

    calculator = KeyPriceCalculator()
    code_keys = list(result.keys())
    total_keys = len(code_keys)
    # 池级 wait 已做过；大股票池禁止逐票再按需同步，否则主线程会假死数十分钟
    for i, code_6 in enumerate(code_keys):
        if i == 0 or (i + 1) % 25 == 0 or (i + 1) == total_keys:
            _progress(f"计算关键价 {i + 1}/{total_keys}")
            _pump_ui_events()
        full_code = _code_with_suffix(code_6)
        live_row = dict(result.get(code_6) or {})
        key_list = calculator.calculate_key_points(
            full_code, error_out=errors, allow_on_demand_sync=False
        )
        for item in key_list:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            price = item.get("price")
            if not name or price is None or price == "-":
                continue
            try:
                result[code_6][name] = float(price)
            except (ValueError, TypeError):
                continue
        # 昨收兜底：关键价失败时若 daily_cache 有任意一根K，直接取最近收盘
        try:
            pc = float(result[code_6].get("昨收盘") or result[code_6].get("pre_close") or 0)
        except (TypeError, ValueError):
            pc = 0.0
        if pc <= 0:
            try:
                from utils.daily_cache_reader import load_daily_from_cache

                df = load_daily_from_cache(full_code, through_date=_date.today())
                if df is not None and len(df) > 0 and "close" in df.columns:
                    prev = float(df["close"].iloc[-1] or 0)
                    if prev > 0:
                        result[code_6]["昨收盘"] = prev
                        result[code_6]["pre_close"] = prev
            except Exception:
                pass
        live_px = float(live_row.get("current") or 0)
        if live_px > 0:
            result[code_6]["current"] = live_px
            result[code_6]["最新价"] = live_px
        for fld in ("今开盘", "今日最高", "今日最低"):
            v = float(live_row.get(fld) or 0)
            if v > 0:
                result[code_6][fld] = v
        if float(result[code_6].get("今开盘") or 0) <= 0:
            _fill_open_from_pre_close(result[code_6])

    return result, errors


def get_prices_with_key_points(stock_codes_6: List[str], on_progress=None) -> tuple:
    """
    获取当前价、昨收，并实时计算关键价格点，一并返回给策略代码。
    返回: (result_dict, error_messages)
    result_dict 结构: { "000001": { "current", "pre_close", "涨停板", "跌停板", ... }, ... }
    error_messages: 计算关键价格点时的错误信息列表，供调用方写入运行日志。
    on_progress: 可选回调 str -> None，用于大股票池进度日志。
    """
    global _LIVE_SOURCE_LOGGED
    errors: List[str] = []
    if _use_builtin_live_feed():
        if not _LIVE_SOURCE_LOGGED:
            _LIVE_SOURCE_LOGGED = True
            errors.append(
                "[配置] 实盘行情来源: 东财现价/今开 + daily_cache 关键价（不再等 results.json）"
            )
        return _get_prices_with_key_points_builtin(
            stock_codes_6, on_progress=on_progress
        )

    if not _LIVE_SOURCE_LOGGED:
        _LIVE_SOURCE_LOGGED = True
        try:
            from qmt_mode_config import get_qmt_mode
        except ImportError:
            try:
                from strategy_generator_app.qmt_mode_config import get_qmt_mode
            except ImportError:
                get_qmt_mode = lambda default="mini": default  # type: ignore[assignment]
        mode = get_qmt_mode()
        errors.append(
            f"[配置] 实盘行情来源: xtdata tick + KeyPriceCalculator（qmt_mode={mode}）"
        )

    # 先拉 tick 得到最新价、昨收（实时）
    result = get_prices(stock_codes_6)
    if not result:
        return result, errors

    ensure_repo_root_on_sys_path()

    try:
        from key_price_calculator import KeyPriceCalculator
        try:
            import key_price_calculator as _kpc
            global _PRICE_PROVIDER_DEBUG_PRINTED
            if not _PRICE_PROVIDER_DEBUG_PRINTED:
                _PRICE_PROVIDER_DEBUG_PRINTED = True
                print(f"[PRICE_PROVIDER_DEBUG] key_price_calculator.file={getattr(_kpc, '__file__', '')}")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"关键价格计算器加载失败: {e}")
        return result, errors

    calculator = KeyPriceCalculator()
    for code_6 in list(result.keys()):
        full_code = _code_with_suffix(code_6)
        key_list = calculator.calculate_key_points(full_code, error_out=errors)
        for item in key_list:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            price = item.get("price")
            if not name or price is None or price == "-":
                continue
            try:
                result[code_6][name] = float(price)
            except (ValueError, TypeError):
                continue
        # 最新价：用 tick 的 current 覆盖（更实时）。pre_close 仍保留自 get_prices()，供兼容。
        if result[code_6].get("current", 0) > 0:
            result[code_6]["最新价"] = result[code_6]["current"]
        # 昨收盘：不再用 tick 的 pre_close 覆盖。计算器按日线与交易日逻辑算出的昨收更稳定，
        # 避免 QMT 未就绪时 tick 返回前一日的错误昨收（如周四的收），与量化交易系统不一致。
        # 今开盘：KeyPriceCalculator 已用日线 open + full_tick；实盘仍偶发拿不到，再对快照重试后用昨收兜底。
        _ensure_live_today_open(result[code_6], full_code)

    return result, errors
