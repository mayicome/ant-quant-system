# -*- coding: utf-8 -*-
"""选股日严多头判定（MA5 相对其他日线）。

供「涨停的第P到N天-严多头 / 实盘用 / 全部 / 主力净流入 B档」等规则共用：
MA5 > MA10 且 MA5 > max(MA20, MA30, MA60[, MA120])。
无 MA120（缺失/NaN/无法解析）时忽略 120 日线，仅用 MA20/30/60。
"""
from __future__ import annotations

import math
from typing import Any, Optional, Tuple


def apply_bool_tri_state(flag: Any, positive_ok: bool) -> bool:
    """True=须满足 positive_ok；False=须不满足；None=忽略此条件。"""
    if flag is None:
        return True
    if flag:
        return bool(positive_ok)
    return not bool(positive_ok)


def _finite_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x):
        return None
    return x


def apply_strict_bull_requirement(
    require_flag: Any,
    ma5: Any,
    ma10: Any,
    ma20: Any,
    ma30: Any,
    ma60: Any,
    ma120: Any,
) -> Optional[Tuple[bool, bool]]:
    """按 REQUIRE_STRICT_BULL 三态过滤选股日均线。

    Returns:
        None: MA5/10/20/30/60 无法解析为有效 float（调用方应跳过该锚定候选）
        (passed, is_strict_bull): 是否通过开关；以及实际是否严多头（供 diag）

    MA120 可选：缺失时不参与 max，等价于「忽略 120 日线」。
    """
    ma5v = _finite_float(ma5)
    ma10v = _finite_float(ma10)
    ma20v = _finite_float(ma20)
    ma30v = _finite_float(ma30)
    ma60v = _finite_float(ma60)
    if None in (ma5v, ma10v, ma20v, ma30v, ma60v):
        # 缺必备均线：视为非严多头；若要求 True 则不通过
        # 与历史「缺线则非严多头」一致，但不再因缺 MA120 整段返回 None
        if require_flag is True:
            return False, False
        if require_flag is False:
            # 须非严多头：缺线算非严多头 → 通过
            return True, False
        return True, False

    longer_vals = [ma20v, ma30v, ma60v]
    ma120v = _finite_float(ma120)
    if ma120v is not None:
        longer_vals.append(ma120v)

    longer = max(longer_vals)  # type: ignore[type-var]
    is_strict_bull = (ma5v > ma10v) and (ma5v > longer)
    return apply_bool_tri_state(require_flag, is_strict_bull), is_strict_bull
