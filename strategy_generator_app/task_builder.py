"""
将引擎输出的「待生成任务」转为符合主程序 PERSIST 格式的 task 字典，并支持写入 Excel。
"""

import os
import json
import uuid
from datetime import date, datetime, timedelta, time as dt_time
from typing import Dict, List, Any, Optional

try:
    from repo_path import ensure_repo_root_on_sys_path
except ImportError:
    import sys

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

# 与 core.task_manager.PERSIST_TASK_COLUMNS 保持一致
PERSIST_TASK_COLUMNS = [
    "task_id", "stock_code", "stock_name", "strategy", "buy_date",
    "init_volume", "init_cost", "params", "create_time", "status", "order_id",
]

# 与量化交易系统一致的规则缺省值（笼子壁厚、弹性反弹/回落比例等）
DEFAULT_CAGE_WALL_THICKNESS = 0.03  # 股票缺省壁厚；ETF 见 _cage_wall_thickness_for_code
DEFAULT_CAGE_WALL_THICKNESS_ETF = 0.003
DEFAULT_BEST_RISE_PERCENT = 0.3     # 弹性买入反弹比例，与 trading_rules.BestBuyRule 一致
DEFAULT_BEST_DROP_PERCENT = 0.3     # 弹性卖出回落比例，与 trading_rules.BestSellRule 一致
STRATEGY_DISPLAY_NAME = "规则任务"


def _cage_wall_thickness_for_code(stock_code: str, raw: Any = None) -> float:
    """按标的精度圆整壁厚；ETF 缺省 0.003，股票缺省 0.03。"""
    try:
        from core.utils.security_type import SecurityTypeUtil

        code = str(stock_code or "")
        precision = SecurityTypeUtil.get_price_precision(code)
        is_etf = SecurityTypeUtil.is_fund(code)
    except Exception:
        precision = 2
        is_etf = False
    default = DEFAULT_CAGE_WALL_THICKNESS_ETF if is_etf else DEFAULT_CAGE_WALL_THICKNESS
    if raw is None:
        return round(float(default), precision)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = float(default)
    return round(val, precision)


def _parse_buy_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    if not s:
        return None
    s10 = s[:10].replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s10.replace("-", "") if fmt == "%Y%m%d" else s10, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s[:19]).date()
    except ValueError:
        return None


def _nth_trading_day_from_buy(buy_date: date, n: int) -> Optional[date]:
    """从 buy_date 起第 n 个交易日（含 buy 所在或之后首个交易日为第 1 日）。"""
    if n < 1:
        return None
    try:
        from trading_calendar import first_trading_day_on_or_after, get_trading_dates_in_range_sorted
    except Exception:
        try:
            from utils.trading_day import is_tradeday
        except ImportError:
            def is_tradeday(d: date) -> bool:
                return d.weekday() < 5

        start = buy_date
        for _ in range(14):
            if is_tradeday(start):
                break
            start += timedelta(days=1)
        count = 0
        d = start
        for _ in range(400):
            if is_tradeday(d):
                count += 1
                if count == n:
                    return d
            d += timedelta(days=1)
        return None

    start = first_trading_day_on_or_after(buy_date)
    if not start:
        return None
    lst = get_trading_dates_in_range_sorted(start, start + timedelta(days=400))
    if len(lst) < n:
        return None
    return lst[n - 1]


def _effective_date_from_generation_anchor(when: Optional[datetime] = None) -> str:
    """
    按「生成时刻」锚定清仓生效交易日（用于非末日出清、或未提供 buy_date/N 的规则）：
    - 交易日 15:00 前生成 → 当日；
    - 交易日 15:00 后生成 → 下一交易日；
    - 非交易日生成 → 下一交易日。
    """
    when = when or datetime.now()
    today = when.date()
    try:
        from utils.trading_day import is_tradeday
    except ImportError:
        is_td = today.weekday() < 5
    else:
        is_td = is_tradeday(today)

    def _next_after(d: date) -> date:
        try:
            from utils.trading_day import is_tradeday as _itd
        except ImportError:
            nd = d + timedelta(days=1)
            while nd.weekday() >= 5:
                nd += timedelta(days=1)
            return nd
        nd = d + timedelta(days=1)
        for _ in range(60):
            if _itd(nd):
                return nd
            nd += timedelta(days=1)
        return d + timedelta(days=1)

    if is_td and when.time() >= dt_time(15, 0):
        return _next_after(today).strftime("%Y-%m-%d")
    if is_td:
        return today.strftime("%Y-%m-%d")
    return _next_after(today).strftime("%Y-%m-%d")


def resolve_scheduled_clear_effective_date(
    intent: Dict[str, Any],
    *,
    buy_date: Optional[date] = None,
    generated_at: Optional[datetime] = None,
) -> str:
    """
    解析定时清仓生效交易日：
    1) 意图/规则已显式指定 scheduled_clear_effective_date；
    2) 末日出清：buy_date + 第 N 个交易日（与策略 scheduled_clear_on_sell_day 一致）；
    3) 否则按生成时刻锚定（15:00 前后 / 是否交易日）。
    """
    explicit = (intent.get("scheduled_clear_effective_date") or "").strip()
    if explicit:
        return explicit

    when = generated_at or datetime.now()
    sell_n = intent.get("scheduled_clear_sell_day_index")
    if sell_n is None:
        sell_n = intent.get("scheduled_clear_on_sell_day") or intent.get("sell_hold_trading_days")

    bd = buy_date or _parse_buy_date(intent.get("buy_date"))
    if bd is not None and sell_n is not None:
        try:
            n = int(sell_n)
            if n >= 1:
                sell_td = _nth_trading_day_from_buy(bd, n)
                if sell_td:
                    return sell_td.strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass

    return _effective_date_from_generation_anchor(when)


def _scheduled_clear_effective_date_str() -> str:
    """兼容旧调用：等价于按当前生成时刻锚定。"""
    return _effective_date_from_generation_anchor(datetime.now())


def _full_stock_code(code_6: str) -> str:
    """6 位代码转带后缀代码：60xxxx -> .SH，其余 -> .SZ"""
    code_6 = (code_6 or "").strip()
    if len(code_6) < 6:
        code_6 = code_6.zfill(6)
    if code_6.startswith("6"):
        return f"{code_6}.SH"
    return f"{code_6}.SZ"


def _normalize_stock_code(stock_code: str) -> str:
    """提取 6 位股票代码便于同股比对（与 core.task_manager 一致，仅保留数字以免 Excel 浮点串扰）"""
    s = (stock_code or "").strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    s = "".join(c for c in s if c.isdigit())
    if not s:
        return ""
    return s.zfill(6) if len(s) >= 6 else s[:6].zfill(6)


def _rule_content_key(rule: Dict[str, Any]) -> str:
    """生成规则的内容键，用于去重（忽略 id 等运行时字段）。完全相同的规则得到相同 key。"""
    if not isinstance(rule, dict):
        return str(id(rule))
    r = dict(rule)
    r.pop("id", None)
    return json.dumps(r, sort_keys=True, ensure_ascii=False)


def _dedupe_rules(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """规则列表去重：内容完全相同的多条只保留第一条。"""
    if not rules:
        return []
    seen = set()
    out = []
    for r in rules:
        key = _rule_content_key(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _make_rule_dict(
    intent: Dict[str, Any],
    *,
    buy_date: Optional[date] = None,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """根据意图构造单条规则 dict，与 core.trading_rules 格式一致"""
    rule_id = f"rule_{uuid.uuid4().hex[:8]}"
    rule_type = (intent.get("rule_type") or "single_buy").strip()

    def f(key: str, default: float = 0) -> float:
        return float(intent.get(key, default) or 0)

    def i(key: str, default: int = 0) -> int:
        return int(intent.get(key, default) or 0)

    if rule_type == "breakthrough_buy":
        out = {
            "id": rule_id,
            "type": "breakthrough_buy",
            "enabled": True,
            "name": intent.get("name") or "突破买入",
            "price": f("price"),
            "volume": i("volume"),
        }
        if intent.get("require_true_breakthrough"):
            out["require_true_breakthrough"] = True
        if intent.get("require_break_below_trigger"):
            out["require_break_below_trigger"] = True
        if intent.get("break_below_trigger_done"):
            out["break_below_trigger_done"] = True
        mode = str(intent.get("true_breakthrough_cond1_mode") or "").strip()
        if mode:
            out["true_breakthrough_cond1_mode"] = mode
        try:
            wsec = intent.get("true_breakthrough_window_sec")
            if wsec is not None and str(wsec).strip() != "":
                out["true_breakthrough_window_sec"] = int(float(wsec))
        except (TypeError, ValueError):
            pass
        for bk in (
            "band_low",
            "band_high",
            "band_accept_low",
            "accept_band_low",
            "price_low",
            "price_high",
        ):
            if intent.get(bk) is None or str(intent.get(bk)).strip() == "":
                continue
            try:
                out[bk] = float(intent.get(bk))
            except (TypeError, ValueError):
                pass
        # 统一有效下沿字段名，便于回测/实盘读取
        if "band_accept_low" not in out and out.get("accept_band_low") is not None:
            out["band_accept_low"] = float(out["accept_band_low"])
        # 分析用均线：写入规则，便于对照；不参与触发
        for src, dst in (
            ("analysis_ma5", "analysis_ma5"),
            ("analysis_ma10", "analysis_ma10"),
            ("analysis_ma20", "analysis_ma20"),
            ("analysis_ma30", "analysis_ma30"),
            ("analysis_ma60", "analysis_ma60"),
            ("analysis_ma120", "analysis_ma120"),
            ("debug_ma5", "analysis_ma5"),
            ("debug_ma10", "analysis_ma10"),
            ("debug_ma20", "analysis_ma20"),
            ("debug_ma30", "analysis_ma30"),
            ("debug_ma60", "analysis_ma60"),
            ("debug_ma120", "analysis_ma120"),
        ):
            if dst in out:
                continue
            if intent.get(src) is None or str(intent.get(src)).strip() == "":
                continue
            try:
                out[dst] = float(intent.get(src))
            except (TypeError, ValueError):
                pass
        return out
    if rule_type == "single_sell":
        out = {
            "id": rule_id,
            "type": "single_sell",
            "enabled": bool(intent.get("enabled", True)),
            "name": intent.get("name") or "单点卖出",
            "price": f("price"),
            "volume": i("volume"),
        }
        if isinstance(intent.get("activation"), dict):
            out["activation"] = dict(intent["activation"])
        return out
    if rule_type == "breakthrough_sell":
        out = {
            "id": rule_id,
            "type": "breakthrough_sell",
            "enabled": bool(intent.get("enabled", True)),
            "name": intent.get("name", "突破卖出"),
            "price": f("price"),
            "volume": i("volume"),
        }
        if isinstance(intent.get("activation"), dict):
            out["activation"] = dict(intent["activation"])
        return out
    if rule_type == "scheduled_clear":
        return {
            "id": rule_id,
            "type": "scheduled_clear",
            "enabled": True,
            "name": intent.get("name", "定时清仓"),
            "price": f("price"),
            "volume": i("volume"),
            "scheduled_clear_time": str(intent.get("scheduled_clear_time", "14:56:00")),
            "scheduled_clear_executed": False,
            "scheduled_clear_effective_date": resolve_scheduled_clear_effective_date(
                intent, buy_date=buy_date, generated_at=generated_at
            ),
        }
    if rule_type == "cage_buy":
        return {
            "id": rule_id,
            "type": "cage_buy",
            "enabled": True,
            "name": intent.get("name") or "笼子买入",
            "price_low": f("price_low"),
            "price_high": f("price_high"),
            "volume": i("volume"),
            "cage_entered": False,
            "wall_thickness": _cage_wall_thickness_for_code(
                str(intent.get("stock_code") or ""),
                intent.get("wall_thickness", DEFAULT_CAGE_WALL_THICKNESS),
            ),
        }
    if rule_type == "cage_sell":
        return {
            "id": rule_id,
            "type": "cage_sell",
            "enabled": True,
            "name": intent.get("name") or "笼子卖出",
            "price_low": f("price_low"),
            "price_high": f("price_high"),
            "volume": i("volume"),
            "cage_entered": False,
            "wall_thickness": _cage_wall_thickness_for_code(
                str(intent.get("stock_code") or ""),
                intent.get("wall_thickness", DEFAULT_CAGE_WALL_THICKNESS),
            ),
        }
    if rule_type == "best_buy":
        out = {
            "id": rule_id,
            "type": "best_buy",
            "enabled": True,
            "name": intent.get("name") or "弹性买入",
            "trigger_price": f("trigger_price"),
            "rise_percent": f("rise_percent", DEFAULT_BEST_RISE_PERCENT),
            "volume": i("volume"),
        }
        # 可选：动态反弹阈值 / 确认参数（不传则走全局 Elastic 配置）
        for key in (
            "rise_scale",
            "max_rise_percent",
            "dynamic_thresholds",
            "confirm_ticks",
            "cooldown_after_extreme_ticks",
        ):
            if key in intent and intent.get(key) is not None and intent.get(key) != "":
                try:
                    if key in ("dynamic_thresholds", "confirm_ticks", "cooldown_after_extreme_ticks"):
                        out[key] = int(intent.get(key))
                    else:
                        out[key] = float(intent.get(key))
                except (TypeError, ValueError):
                    pass
        try:
            lu = float(intent.get("limit_up") or 0)
            if lu > 0:
                out["limit_up"] = round(lu, 4)
        except (TypeError, ValueError):
            pass
        return out
    if rule_type == "best_sell":
        out = {
            "id": rule_id,
            "type": "best_sell",
            "enabled": True,
            "name": intent.get("name") or "弹性卖出",
            "trigger_price": f("trigger_price"),
            "drop_percent": f("drop_percent", DEFAULT_BEST_DROP_PERCENT),
            "volume": i("volume", 0),
        }
        pp = intent.get("pullback_price")
        if pp is not None and float(pp or 0) > 0:
            out["pullback_price"] = round(float(pp), 4)
        rbs = intent.get("room_blend_start")
        if rbs is not None:
            try:
                out["room_blend_start"] = float(rbs)
            except (TypeError, ValueError):
                pass
        return out
    # single_buy 默认（含开盘买入：wait_unseal / fill_at_limit_up）
    out = {
        "id": rule_id,
        "type": "single_buy",
        "enabled": True,
        "name": intent.get("name") or "单点买入",
        "price": f("price"),
        "volume": i("volume"),
    }
    if intent.get("wait_unseal"):
        out["wait_unseal"] = True
    if intent.get("fill_at_limit_up"):
        out["fill_at_limit_up"] = True
    if intent.get("open_buy_ask"):
        out["open_buy_ask"] = True
    if intent.get("early_order_enabled") is not None:
        out["early_order_enabled"] = bool(intent.get("early_order_enabled"))
    try:
        lu = float(intent.get("limit_up") or 0)
        if lu > 0:
            out["limit_up"] = round(lu, 4)
    except (TypeError, ValueError):
        pass
    return out


def _init_volume_and_cost(intent: Dict[str, Any]) -> tuple:
    """根据规则类型返回 (init_volume, init_cost) 用于 task 表"""
    rule_type = (intent.get("rule_type") or "single_buy").strip()

    def f(key: str, default: float = 0) -> float:
        return float(intent.get(key, default) or 0)

    def i(key: str, default: int = 0) -> int:
        return int(intent.get(key, default) or 0)

    if rule_type in ("single_buy", "breakthrough_buy", "single_sell", "breakthrough_sell"):
        return i("volume"), f("price")
    if rule_type == "scheduled_clear":
        return i("volume"), f("price")
    if rule_type in ("cage_buy", "cage_sell"):
        vol = i("volume")
        low, high = f("price_low"), f("price_high")
        cost = (low + high) / 2 if (low or high) else 0
        return vol, cost
    if rule_type == "best_buy":
        return i("volume"), f("trigger_price")
    if rule_type == "best_sell":
        return i("volume", 0), f("trigger_price")
    return i("volume"), f("price")


def build_task_dict(
    intent: Dict[str, Any],
    *,
    buy_date: Optional[date] = None,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    将引擎输出的一条意图转为完整 task 字典。
    intent 需含: stock_code, stock_name, rule_type，及各类规则对应字段（price/volume、price_low/price_high、trigger_price/rise_percent 等）。
    """
    code_6 = (intent.get("stock_code") or "").strip()
    if len(code_6) < 6:
        code_6 = code_6.zfill(6)
    full_code = _full_stock_code(code_6)
    stock_name = intent.get("stock_name") or "未知名称"
    init_vol, init_cost = _init_volume_and_cost(intent)
    when = generated_at or datetime.now()
    bd = buy_date or _parse_buy_date(intent.get("buy_date")) or when.date()
    rule = _make_rule_dict(intent, buy_date=bd, generated_at=when)
    task_id = str(uuid.uuid4())
    return {
        "task_id": task_id,
        "stock_code": full_code,
        "stock_name": stock_name,
        "strategy": STRATEGY_DISPLAY_NAME,
        "buy_date": bd.strftime("%Y-%m-%d"),
        "init_volume": init_vol,
        "init_cost": init_cost,
        "params": {
            "rules": [rule],
            "clear_time": "00:00:00",
            "cycle_times": 0,
        },
        "create_time": when.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "待审核",
        "order_id": "",
    }


def build_tasks_from_intents(
    intents: List[Dict[str, Any]],
    *,
    buy_dates_by_code: Optional[Dict[str, date]] = None,
    sell_hold_trading_days: Optional[int] = None,
    generated_at: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    将同一只股票的多条意图合并为一条任务记录（params.rules 含多条规则）。
    按 stock_code 分组，每组生成一个 task，保证一只股票只对应一条记录。
    """
    if not intents:
        return []
    # 按 6 位代码分组
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in intents:
        code_6 = (it.get("stock_code") or "").strip()
        if len(code_6) < 6:
            code_6 = code_6.zfill(6)
        if code_6 not in groups:
            groups[code_6] = []
        groups[code_6].append(it)
    tasks = []
    when = generated_at or datetime.now()
    buy_dates_by_code = buy_dates_by_code or {}
    for code_6, group in groups.items():
        full_code = _full_stock_code(code_6)
        stock_name = (group[0].get("stock_name") or "未知名称").strip()
        bd = _parse_buy_date(group[0].get("buy_date")) or buy_dates_by_code.get(code_6) or when.date()
        enriched_group = []
        for it in group:
            row = dict(it)
            if (row.get("rule_type") or "").strip() == "scheduled_clear":
                if sell_hold_trading_days is not None and row.get("scheduled_clear_sell_day_index") is None:
                    row["scheduled_clear_sell_day_index"] = sell_hold_trading_days
                if row.get("buy_date") is None:
                    row["buy_date"] = bd.strftime("%Y-%m-%d")
            enriched_group.append(row)
        rules = [
            _make_rule_dict(i, buy_date=bd, generated_at=when) for i in enriched_group
        ]
        total_vol = 0
        cost_sum = 0.0
        for i in group:
            vol, cost = _init_volume_and_cost(i)
            total_vol += vol
            cost_sum += cost * vol
        init_vol = total_vol
        init_cost = round(cost_sum / total_vol, 2) if total_vol > 0 else 0
        tasks.append({
            "task_id": str(uuid.uuid4()),
            "stock_code": full_code,
            "stock_name": stock_name,
            "strategy": STRATEGY_DISPLAY_NAME,
            "buy_date": bd.strftime("%Y-%m-%d"),
            "init_volume": init_vol,
            "init_cost": init_cost,
            "params": {
                "rules": rules,
                "clear_time": "00:00:00",
                "cycle_times": 0,
            },
            "create_time": when.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "待审核",
            "order_id": "",
        })
    return tasks


def get_tasks_file_path(project_root: str) -> str:
    """获取当日任务文件路径。project_root 为项目根目录（含 data 的上一级）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(project_root, "data", f"current_tasks_{today}.xlsx")


def write_tasks_to_excel(
    new_tasks: List[Dict[str, Any]],
    project_root: str,
    append: bool = True,
    drop_scheduled_clear_on_merge: bool = False,
) -> str:
    """
    将新任务写入 data/current_tasks_YYYY-MM-DD.xlsx。
    若 append=True 且文件存在，则先读取已有任务，合并后再写入；否则仅写入 new_tasks。
    drop_scheduled_clear_on_merge：合并同股任务时剔除 scheduled_clear（用于买入策略覆盖写入时不保留旧清仓规则）。
    返回任务文件路径。
    """
    import pandas as pd

    path = get_tasks_file_path(project_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing = []
    if append and os.path.isfile(path):
        try:
            df = pd.read_excel(path)
            for c in PERSIST_TASK_COLUMNS:
                if c not in df.columns:
                    df[c] = None
            df = df[PERSIST_TASK_COLUMNS]
            for _, row in df.iterrows():
                task = row.to_dict()
                if isinstance(task.get("params"), str):
                    try:
                        task["params"] = json.loads(task["params"])
                    except Exception:
                        task["params"] = {}
                existing.append(task)
        except Exception:
            pass

    # 按股票代码合并：若当日任务里已有该股，则把新任务的规则并入已有任务，避免同股多任务多画面
    final_tasks = list(existing)
    code_to_idx = {}
    for i, t in enumerate(final_tasks):
        c = _normalize_stock_code(t.get("stock_code"))
        if c:
            code_to_idx[c] = i
    for new_task in new_tasks:
        c = _normalize_stock_code(new_task.get("stock_code"))
        if not c:
            final_tasks.append(new_task)
            continue
        if c in code_to_idx:
            idx = code_to_idx[c]
            old = final_tasks[idx]
            old_params = old.get("params") or {}
            old_rules = list(old_params.get("rules") or [])
            new_rules = (new_task.get("params") or {}).get("rules") or []
            merged_rules = _dedupe_rules(old_rules + new_rules)
            if drop_scheduled_clear_on_merge:
                merged_rules = [
                    r for r in merged_rules
                    if (r.get("type") or "").strip() != "scheduled_clear"
                ]
            old_params["rules"] = merged_rules
            old["params"] = old_params
        else:
            final_tasks.append(new_task)
            code_to_idx[c] = len(final_tasks) - 1

    rows = []
    for task in final_tasks:
        row = {k: task.get(k) for k in PERSIST_TASK_COLUMNS}
        if isinstance(row.get("params"), dict):
            row["params"] = json.dumps(row["params"], ensure_ascii=False)
        rows.append(row)

    df = pd.DataFrame(rows, columns=PERSIST_TASK_COLUMNS)
    df.to_excel(path, index=False)
    return path
