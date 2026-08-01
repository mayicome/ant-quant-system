import os
import json
import re
import uuid
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Any, Optional


CONFIG_DIR_NAME = "config"
STRATEGIES_DIR_NAME = "strategies"


def _normalize_code(s: str) -> str:
    """提取6位股票代码（不足6位左补零）。"""
    raw = str(s or "").strip()
    if not raw:
        return ""
    m = re.match(r"^(\d+)\.0+$", raw)
    if m:
        num_str = m.group(1)
    else:
        num_str = re.sub(r"[^\d]", "", raw)
    if not num_str:
        return ""
    if len(num_str) > 6:
        num_str = num_str[:6]
    return num_str.zfill(6)


def parse_codes_text(text: str) -> List[str]:
    """从多行文本解析股票代码（每行一个或逗号/空格分隔），返回6位代码列表（去重）"""
    codes = []
    seen = set()
    for line in (text or "").strip().splitlines():
        for part in re.split(r"[\s,，]+", line.strip()):
            c = _normalize_code(part)
            if c and c not in seen:
                seen.add(c)
                codes.append(c)
    return codes


@dataclass
class StrategyConfig:
    """单个策略的配置；股票池为该策略专属属性（stock_codes）；策略逻辑由 strategy_code（Python 脚本）或 strategy_params（表单）提供"""
    id: str
    name: str
    enabled: bool = True
    stock_codes: List[str] = field(default_factory=list)  # 本策略的股票池，6位代码
    strategy_params: Dict[str, Any] = field(default_factory=dict)  # 表单模式下的参数（代码模式可忽略）
    strategy_code: str = ""  # 策略逻辑 Python 代码；非空时预览/生成任务时执行此代码得到意图列表
    # 定时生成策略：本地时间 ISO 格式字符串，如 "2026-04-23T09:25:00"；到点由主窗口串行触发「运行 + 生成任务」后清除
    scheduled_generate_at: Optional[str] = None

    @staticmethod
    def new_default(name: str) -> "StrategyConfig":
        return StrategyConfig(
            id=f"strategy_{uuid.uuid4().hex[:8]}",
            name=name or "未命名策略",
            enabled=True,
            stock_codes=[],
            strategy_params=_default_strategy_params(),
            strategy_code=_default_strategy_code(),
        )


# 策略参数中供界面编辑的键（客户可配置，无需改代码）；仅保留这些
PARAM_BUY_AMOUNT_PER_STOCK = "buy_amount_per_stock"   # 单股拟买入金额(元)
PARAM_MIN_ORDER_AMOUNT = "min_order_amount"           # 每笔最小交易金额(元)
ALLOWED_PARAM_KEYS = (PARAM_BUY_AMOUNT_PER_STOCK, PARAM_MIN_ORDER_AMOUNT)
# 可选扩展键：仅当 JSON/导入里显式出现时保留；不写进默认表，避免影响未配置策略的回测行为
OPTIONAL_STRATEGY_PARAM_KEYS = (
    "limit_up_clear_on_sell_day",
    "limit_up_clear_defer_next_day",
    "limit_up_clear_defer_days",
    # 卖出策略 tp10（如「卖：止盈-28开」）与日内止损 n（如「卖：止盈止损」）
    "tp10_ratio_low",
    "tp10_up_low",
    "tp10_up_high",
    "tp10_drop_low",
    "tp10_drop_high",
    "tp10_blend_low",
    "tp10_blend_high",
    "intraday_loss_stop_pct",
    "scheduled_clear_on_sell_day",
    "scheduled_clear_time",
    "sell_hold_trading_days",
)


def normalize_strategy_label(name: str) -> str:
    """去掉「时段1·」等前缀，得到策略显示名。"""
    n = (name or "").strip()
    if "·" in n:
        n = n.split("·", 1)[-1].strip()
    return n


def strategy_name_looks_like_buy(name: str) -> bool:
    n = normalize_strategy_label(name)
    return n.startswith("买") or n.startswith("买：")


def strategy_name_looks_like_sell(name: str) -> bool:
    n = normalize_strategy_label(name)
    return n.startswith("卖") or n.startswith("卖：") or "持仓" in n


def strategy_uses_positions(
    strategy_code: str,
    strategy_params: Optional[Dict[str, Any]] = None,
    strategy_name: str = "",
) -> bool:
    """策略 run() 是否依赖 params['positions']（持仓卖出等）；买入策略返回 False。"""
    if strategy_name_looks_like_buy(strategy_name):
        return False
    code = strategy_code or ""
    if re.search(r"""params\.get\(['"]positions['"]""", code):
        return True
    if "持仓卖出" in code or "可用持仓" in code:
        return True
    if strategy_name_looks_like_sell(strategy_name):
        return True
    return False


def strategy_uses_scheduled_clear(
    strategy_code: str,
    strategy_params: Optional[Dict[str, Any]] = None,
    strategy_name: str = "",
) -> bool:
    """策略 run() 是否会生成 scheduled_clear 规则（实盘/回测注入判定）。"""
    if strategy_name_looks_like_buy(strategy_name):
        return False
    code = strategy_code or ""
    if re.search(r"""['"]rule_type['"]\s*:\s*['"]scheduled_clear['"]""", code):
        return True
    if re.search(r"""rule_type\s*=\s*['"]scheduled_clear['"]""", code):
        return True
    sp = strategy_params or {}
    return any(
        k in sp
        for k in ("scheduled_clear_time", "scheduled_clear_on_sell_day", "sell_hold_trading_days")
    )


def strip_unwanted_scheduled_clear_intents(
    intents: List[Dict[str, Any]],
    strategy_code: str,
    strategy_params: Optional[Dict[str, Any]] = None,
    strategy_name: str = "",
) -> List[Dict[str, Any]]:
    if strategy_uses_scheduled_clear(strategy_code, strategy_params, strategy_name):
        return intents
    return [
        it for it in (intents or [])
        if (it.get("rule_type") or "").strip() != "scheduled_clear"
    ]


def strip_scheduled_clear_params(params: Optional[Dict[str, Any]]) -> None:
    """非定时清仓策略：移除误注入/历史残留的末日出清参数。"""
    if not isinstance(params, dict):
        return
    for k in ("scheduled_clear_on_sell_day", "sell_hold_trading_days", "scheduled_clear_time"):
        params.pop(k, None)


def _default_strategy_params() -> Dict[str, Any]:
    """默认策略参数：客户可在「策略参数」Tab 中修改，传入 run(..., params)"""
    return {
        PARAM_BUY_AMOUNT_PER_STOCK: 50000,
        PARAM_MIN_ORDER_AMOUNT: 5000,
    }


def filter_strategy_params(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    """只保留当前有效的参数键，丢弃历史废弃字段（如 rule_type、price_source 等）"""
    defaults = _default_strategy_params()
    if raw is None:
        return dict(defaults)
    if not isinstance(raw, dict):
        return dict(defaults)
    if not raw:
        return dict(defaults)
    out = {k: raw.get(k, defaults[k]) for k in ALLOWED_PARAM_KEYS if k in defaults}
    for k in OPTIONAL_STRATEGY_PARAM_KEYS:
        if k in raw:
            out[k] = raw[k]
    return out


def _default_strategy_code() -> str:
    """默认策略代码：5/10 日均线明显多头且 5 日上方无其他均线时，按最新价与 5 日/10 日关系生成笼子买入或突破买入"""
    return '''# 策略逻辑：5日与10日明显多头排列，且5日均线上方无其他均线；按最新价与5日/10日关系生成两笔买入
# params：buy_amount_per_stock（单股拟买入金额）、min_order_amount（每笔最小交易金额）

def run(codes, prices, get_name, account, params):
    result = []
    amount_per_stock = float(params.get("buy_amount_per_stock", 50000))
    min_order_amount = float(params.get("min_order_amount", 5000))
    tol = 0.003  # 明显多头排列容差 0.3%

    for code in codes:
        p = prices.get(code, {})
        cur = float(p.get("current") or p.get("最新价") or 0)
        pre_close = float(p.get("昨收盘") or p.get("pre_close") or cur or 1)
        ma5 = p.get("5日")
        ma10 = p.get("10日")
        ma20, ma30, ma60, ma120 = p.get("20日"), p.get("30日"), p.get("60日"), p.get("120日")
        limit_down = p.get("跌停板")
        if ma5 is None or ma10 is None or cur <= 0:
            continue
        ma5 = float(ma5)
        ma10 = float(ma10)
        others = []
        for x in (ma20, ma30, ma60, ma120):
            if x is not None:
                try:
                    others.append(float(x))
                except (TypeError, ValueError):
                    pass
        limit_down_f = float(limit_down) if limit_down is not None else round(pre_close * 0.9, 2)

        # 明显多头排列：5日 > 10日，且差距超过容差
        threshold = tol * pre_close
        if ma5 - ma10 < threshold:
            continue
        # 5日均线上方没有其他均线
        if any(o > ma5 for o in others):
            continue

        name = (get_name(code) if get_name else "") or ""

        def vol_for(amt, price):
            if price <= 0:
                return 0
            v = max(100, (int(amt / price / 100) * 100))
            if v * price < min_order_amount:
                return 0
            return v

        half = amount_per_stock / 2
        r5, r10 = round(ma5, 2), round(ma10, 2)

        if cur > ma5:
            # 最新价 > 5日：两笔笼子买入，低点跌停板，高点分别为5日、10日；每笔金额一半
            v1 = vol_for(half, ma5)
            v2 = vol_for(half, ma10)
            if v1 > 0:
                result.append({"stock_code": code, "stock_name": name, "rule_type": "cage_buy",
                              "price_low": limit_down_f, "price_high": r5, "volume": v1})
            if v2 > 0:
                result.append({"stock_code": code, "stock_name": name, "rule_type": "cage_buy",
                              "price_low": limit_down_f, "price_high": r10, "volume": v2})
        elif cur > ma10:
            # 5日 >= 最新价 > 10日：买入1 笼子(低跌停板高10日)一半金额；买入2 突破(5日)一半金额
            v1 = vol_for(half, ma10)
            v2 = vol_for(half, ma5)
            if v1 > 0:
                result.append({"stock_code": code, "stock_name": name, "rule_type": "cage_buy",
                              "price_low": limit_down_f, "price_high": r10, "volume": v1})
            if v2 > 0:
                result.append({"stock_code": code, "stock_name": name, "rule_type": "breakthrough_buy",
                              "price": r5, "volume": v2})
        else:
            # 最新价 <= 10日：两笔突破买入，价格分别为10日、5日；每笔金额一半
            v1 = vol_for(half, ma10)
            v2 = vol_for(half, ma5)
            if v1 > 0:
                result.append({"stock_code": code, "stock_name": name, "rule_type": "breakthrough_buy",
                              "price": r10, "volume": v1})
            if v2 > 0:
                result.append({"stock_code": code, "stock_name": name, "rule_type": "breakthrough_buy",
                              "price": r5, "volume": v2})
    return result
'''


def get_position_sell_strategy_code() -> str:
    """持仓卖出策略：读取可用持仓，生成突破卖出70%（min(开盘,昨收)-3%）、笼子(5日/10日/20日-涨停)30%/45%/60%、定时清仓100%。params 中需传入 positions={code: 可用数量}（运行时会自动注入）。"""
    return '''# 持仓卖出策略：仅对有可用持仓的股票生成卖出任务
# 突破卖出70%（min(今开盘,昨收盘)下跌3%触发）；笼子卖出（5日/10日/20日-涨停）30%/45%/60%；定时清仓100%（清仓价=max(20日线, min(今开盘, 昨收盘)*0.7)）
# params["positions"] 由运行前自动注入：{ "000001": 1000, ... } 表示各股票可用持仓

def run(codes, prices, get_name, account, params):
    positions = params.get("positions") or {}
    result = []
    default_clear_time = "14:56:00"
    for code in codes:
        code_6 = (code or "").strip()
        if len(code_6) < 6:
            code_6 = code_6.zfill(6)
        avail = positions.get(code_6, 0)
        if not isinstance(avail, (int, float)):
            avail = 0
        avail = int(avail)
        if avail < 100:
            continue
        avail = (avail // 100) * 100
        p = prices.get(code_6, {})
        open_price = float(p.get("今开盘") or p.get("昨收盘") or 0)
        pre_close = float(p.get("昨收盘") or p.get("pre_close") or 0)
        limit_up = float(p.get("涨停板") or 0)
        limit_down = float(p.get("跌停板") or 0)
        ma5 = p.get("5日")
        ma10 = p.get("10日")
        ma20 = p.get("20日")
        if not open_price or not limit_up:
            continue
        if ma5 is None or ma10 is None or ma20 is None:
            continue
        ma5, ma10, ma20 = float(ma5), float(ma10), float(ma20)
        name = (get_name(code_6) if get_name else "") or "未知"
        break_price = round(min(open_price, pre_close) * 0.97, 2)
        clear_price = round(max(ma20, min(open_price, pre_close) * 0.7), 2)
        v70 = max(100, (int(avail * 0.7 / 100) * 100))
        v30 = max(100, (int(avail * 0.3 / 100) * 100))
        v45 = max(100, (int(avail * 0.45 / 100) * 100))
        v60 = max(100, (int(avail * 0.6 / 100) * 100))
        # 仅当突破价在 [跌停, 涨停] 内才添加：否则当日价格到不了，规则无意义
        if limit_down <= break_price <= limit_up:
            result.append({"stock_code": code_6, "stock_name": name, "rule_type": "breakthrough_sell",
                           "name": "突破卖出（min(开盘,昨收)-3%）", "price": break_price, "volume": v70})
        # 笼子上限：以行情中的涨停板为准，向下取整；并硬性不超过昨收*1.30（防止取到今日最高/布林上轨等误用）
        if not pre_close or pre_close <= 0:
            cage_high = int(limit_up * 100) / 100
        else:
            cap = round(pre_close * 1.30, 2)
            cage_high = min(int(limit_up * 100) / 100, cap)
        for low_name, low_val, vol in [("5日线", ma5, v30), ("10日线", ma10, v45), ("20日线", ma20, v60)]:
            low_val = round(low_val, 2)
            if limit_up <= low_val:
                continue
            # 下沿低于跌停板则当日价格区间无效，该笼子无意义，跳过
            if limit_down and low_val < limit_down:
                continue
            result.append({"stock_code": code_6, "stock_name": name, "rule_type": "cage_sell",
                           "name": "笼子卖出（%s-涨停）" % low_name,
                           "price_low": low_val, "price_high": cage_high, "volume": vol, "wall_thickness": 0.03})
        # 定时清仓价若低于跌停板则按跌停板，否则规则仍有效
        clear_price = max(clear_price, limit_down) if limit_down else clear_price
        result.append({"stock_code": code_6, "stock_name": name, "rule_type": "scheduled_clear",
                       "name": "定时清仓", "price": round(clear_price, 2), "volume": avail,
                       "scheduled_clear_time": default_clear_time})
    return result
'''


def _get_strategies_root() -> str:
    """获取存放策略配置的目录路径"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_dir = os.path.join(base_dir, CONFIG_DIR_NAME)
    strategies_dir = os.path.join(cfg_dir, STRATEGIES_DIR_NAME)
    os.makedirs(strategies_dir, exist_ok=True)
    return strategies_dir


def _strategy_path(strategy_id: str) -> str:
    root = _get_strategies_root()
    filename = f"{strategy_id}.json"
    return os.path.join(root, filename)


def _strategy_config_from_file_data(data: Dict[str, Any], file_stem: str) -> StrategyConfig:
    """由磁盘 JSON 解析出的 dict 构建 StrategyConfig（与 load_all_strategies 单文件逻辑一致）。"""
    raw_codes = data.get("stock_codes") or []
    codes = [c for c in (_normalize_code(x) for x in raw_codes) if c]
    raw_sched = data.get("scheduled_generate_at")
    sched = (str(raw_sched).strip() if raw_sched is not None else "") or None
    return StrategyConfig(
        id=data.get("id") or file_stem,
        name=data.get("name", "未命名策略"),
        enabled=bool(data.get("enabled", True)),
        stock_codes=codes,
        strategy_params=filter_strategy_params(data.get("strategy_params")),
        strategy_code=data.get("strategy_code") or "",
        scheduled_generate_at=sched,
    )


def load_strategy_by_id(strategy_id: str) -> Optional[StrategyConfig]:
    """从磁盘读取单个策略（供回测前再读一遍 JSON，与启动时 load 规则一致）。"""
    sid = (strategy_id or "").strip()
    if not sid:
        return None
    path = _strategy_path(sid)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        stem = os.path.splitext(os.path.basename(path))[0]
        return _strategy_config_from_file_data(data, stem)
    except Exception:
        return None


def load_all_strategies() -> List[StrategyConfig]:
    """加载所有策略配置"""
    root = _get_strategies_root()
    strategies: List[StrategyConfig] = []
    for fname in os.listdir(root):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(root, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            strategies.append(_strategy_config_from_file_data(data, os.path.splitext(fname)[0]))
        except Exception:
            # 解析失败时跳过该文件，避免影响整体
            continue
    # 按名称排序，方便浏览
    strategies.sort(key=lambda s: s.name)
    return strategies


def strategy_from_import_data(data: Dict[str, Any]) -> StrategyConfig:
    """从导出文件读入的 dict 构建策略配置，分配新 id，过滤参数。用于「导入策略」。"""
    raw_params = data.get("strategy_params") or _default_strategy_params()
    raw_codes = data.get("stock_codes") or []
    codes = [c for c in (_normalize_code(x) for x in raw_codes) if c]
    return StrategyConfig(
        id=f"strategy_{uuid.uuid4().hex[:8]}",
        name=data.get("name", "未命名策略"),
        enabled=bool(data.get("enabled", True)),
        stock_codes=codes,
        strategy_params=filter_strategy_params(raw_params),
        strategy_code=data.get("strategy_code") or "",
        scheduled_generate_at=None,
    )


def save_strategy(cfg: StrategyConfig) -> None:
    """保存单个策略到磁盘；strategy_params 只保留当前有效键"""
    path = _strategy_path(cfg.id)
    data: Dict = asdict(cfg)
    data["strategy_params"] = filter_strategy_params(data.get("strategy_params"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def delete_strategy(strategy_id: str) -> None:
    """删除某个策略配置文件"""
    path = _strategy_path(strategy_id)
    if os.path.exists(path):
        os.remove(path)

