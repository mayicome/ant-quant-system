# 买：开盘夹档 + Cond2（开盘相对早盘MA5∈[0%,2%]）
# - 均线夹档：只读 昨MA5 / 昨MA10；缺字段报错跳过，禁止回退
# - 开盘：只读 今开盘；缺失报错跳过；开盘涨停不买
# - 条件一：min(昨MA5,昨MA10) ≤ 今开 ≤ max(昨MA5,昨MA10)
# - Cond2（本策略强制开）：开盘相对早盘MA5 ∈ [0%, 2%]
#     早盘MA5 = 行情「5日」= 前 4 日收盘均（不含当日开盘；非 昨MA5 五根真均线）
#     缺「5日」/无效 → 跳过买入（fail closed）
# - Cond3（默认关）：昨MA5 < 昨MA10 < 昨MA20；已迁到选股规则，避免双重过滤
# - 仓位：params.sizing_mode
#     fixed = buy_amount_per_stock
#     clip_equity（实盘推荐）= 每笔=min(总权益/clip(S,L,U), 当时现金)；进档最多买 U 只
# - 建议生成时刻 09:25（Cond2 需要今开盘；隔夜选股无法做 Cond2）
#
# params：sizing_mode, buy_amount_per_stock, min_order_amount, clip_L, clip_U,
#         require_open_rel_ma5, open_rel_ma5_lo, open_rel_ma5_hi,
#         require_ma5_lt_ma10_lt_ma20

LIMIT_OPEN_EPS = 0.011

# 策略常量（可被 params 同名键覆盖）
REQUIRE_OPEN_REL_MA5 = True
OPEN_REL_MA5_LO = 0.0
OPEN_REL_MA5_HI = 0.02
REQUIRE_MA5_LT_MA10_LT_MA20 = False


def run(codes, prices, get_name, account, params):
    result = []
    params = params or {}
    account = account or {}
    amount_fixed = float(params.get("buy_amount_per_stock", 50000))
    min_order_amount = float(params.get("min_order_amount", 5000))
    sizing_mode = str(params.get("sizing_mode") or "clip_equity").strip().lower()
    if sizing_mode in ("clip", "account_clip", "clip_s"):
        sizing_mode = "clip_equity"

    def _param_bool(key, default):
        if key not in params:
            return bool(default)
        v = params.get(key)
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("1", "true", "yes", "y", "on"):
            return True
        if s in ("0", "false", "no", "n", "off", ""):
            return False
        return bool(default)

    def _param_float(key, default):
        if key not in params or params.get(key) is None or params.get(key) == "":
            return float(default)
        try:
            return float(params.get(key))
        except (TypeError, ValueError):
            return float(default)

    # Cond2 本策略强制开启（fail closed）；区间仍可读 params 覆盖
    req_open_rel = True
    open_rel_lo = _param_float("open_rel_ma5_lo", OPEN_REL_MA5_LO)
    open_rel_hi = _param_float("open_rel_ma5_hi", OPEN_REL_MA5_HI)
    req_ma_lt = _param_bool("require_ma5_lt_ma10_lt_ma20", REQUIRE_MA5_LT_MA10_LT_MA20)

    def vol_for(amt, price):
        """按金额算整手；买不起 1 手则返回 0（禁止强行 100 股）。"""
        if price <= 0 or amt <= 0:
            return 0
        v = int(amt / price / 100) * 100
        if v < 100:
            return 0
        if v * price < min_order_amount:
            return 0
        return v

    def _require_float(p, key, code_6):
        if not isinstance(p, dict) or key not in p:
            keys = sorted(p.keys()) if isinstance(p, dict) else type(p)
            return None, (
                f"缺少必填行情字段 {key!r}（禁止回退其它字段） keys={keys}"
            )
        raw = p.get(key)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None, f"字段 {key!r} 无法转为浮点: {raw!r}"
        if not (v == v) or v <= 0:
            return None, f"字段 {key!r} 无效: {raw!r}"
        return v, None

    def _f(p, *keys):
        for k in keys:
            try:
                v = float(p.get(k) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                return v
        return 0.0

    def _limits(code_6, p):
        lu = _f(p, "涨停板", "limit_up")
        ld = _f(p, "跌停板", "limit_down")
        if lu > 0 and ld > 0 and lu >= ld:
            return ld, lu
        pre = _f(p, "昨收盘", "pre_close")
        if pre <= 0:
            return 0.0, 0.0
        if code_6.startswith(("300", "301", "688", "689")):
            up_r, down_r = 1.2, 0.8
        else:
            up_r, down_r = 1.1, 0.9
        return round(pre * down_r, 2), round(pre * up_r, 2)

    if not codes:
        print("[开盘买入-夹档+Cond2] 股票池为空：请先导入选股/填写股票代码")
        return result

    top_n = params.get("generate_top_n")
    try:
        top_n_i = int(top_n) if top_n is not None else 0
    except (TypeError, ValueError):
        top_n_i = 0
    if sizing_mode == "clip_equity" and top_n_i > 0:
        print(
            f"[开盘买入-夹档+Cond2] 提示: generate_top_n={top_n_i}，S 以截断后池子为准；"
            f"当 N>=U 时通常不影响 clip 单票比例"
        )

    print(
        f"[开盘买入-夹档+Cond2] Cond2开盘相对早盘MA5="
        f"{'开' if req_open_rel else '关'}[{open_rel_lo:.2%},{open_rel_hi:.2%}] "
        f"Cond3 MA5<MA10<MA20={'开' if req_ma_lt else '关'}"
    )

    n_skip_field = 0
    n_skip_band = 0
    n_skip_amt = 0
    n_skip_limit = 0
    n_skip_limit_open = 0
    n_skip_cap = 0
    n_skip_open_rel = 0
    n_skip_ma_lt = 0
    hits = []

    for code in codes:
        code_6 = (code or "").strip()
        if len(code_6) < 6:
            code_6 = code_6.zfill(6)
        p = prices.get(code_6) or prices.get(code) or {}
        if not isinstance(p, dict):
            p = {}

        name = (get_name(code_6) if get_name else "") or ""

        open_px, err = _require_float(p, "今开盘", code_6)
        if err:
            n_skip_field += 1
            print(f"[开盘买入-夹档+Cond2] {code_6} 报错跳过: {err}")
            continue

        ma5, err = _require_float(p, "昨MA5", code_6)
        if err:
            n_skip_field += 1
            print(f"[开盘买入-夹档+Cond2] {code_6} 报错跳过: {err}")
            continue

        ma10, err = _require_float(p, "昨MA10", code_6)
        if err:
            n_skip_field += 1
            print(f"[开盘买入-夹档+Cond2] {code_6} 报错跳过: {err}")
            continue

        if req_ma_lt:
            ma20, err = _require_float(p, "昨MA20", code_6)
            if err:
                n_skip_field += 1
                print(f"[开盘买入-夹档+Cond2] {code_6} 报错跳过: {err}")
                continue
            if not (float(ma5) < float(ma10) < float(ma20)):
                n_skip_ma_lt += 1
                print(
                    f"[开盘买入-夹档+Cond2] {code_6} 跳过: Cond3非MA5<MA10<MA20 "
                    f"昨MA5={ma5:.2f} 昨MA10={ma10:.2f} 昨MA20={ma20:.2f}"
                )
                continue

        if req_open_rel:
            # 早盘 MA5 重合点：「5日」= 前 4 日收盘均（见 data_provider._build_morning_row_from_df）
            ma5_am, err = _require_float(p, "5日", code_6)
            if err:
                n_skip_field += 1
                print(f"[开盘买入-夹档+Cond2] {code_6} 报错跳过(Cond2需早盘MA5): {err}")
                continue
            rel = open_px / ma5_am - 1.0
            if rel < float(open_rel_lo) or rel > float(open_rel_hi):
                n_skip_open_rel += 1
                print(
                    f"[开盘买入-夹档+Cond2] {code_6} 跳过: Cond2开盘相对早盘MA5="
                    f"{rel * 100:.3f}% 不在[{open_rel_lo * 100:.1f}%,{open_rel_hi * 100:.1f}%] "
                    f"open={open_px:.2f} 早盘MA5(5日)={ma5_am:.2f}"
                )
                continue

        lo = min(ma5, ma10)
        hi = max(ma5, ma10)
        if not (lo <= open_px <= hi):
            n_skip_band += 1
            print(
                f"[开盘买入-夹档+Cond2] {code_6} 跳过: 开盘不在夹档 "
                f"open={open_px:.2f} lo={lo:.2f} hi={hi:.2f} "
                f"(昨MA5={ma5:.2f} 昨MA10={ma10:.2f})"
            )
            continue

        ld, lu = _limits(code_6, p)
        if lu <= 0 or ld <= 0:
            n_skip_limit += 1
            print(f"[开盘买入-夹档+Cond2] {code_6} 跳过: 无涨跌停 (昨收={p.get('昨收盘')!r})")
            continue

        is_limit_open = open_px + 1e-9 >= lu - LIMIT_OPEN_EPS
        if is_limit_open:
            n_skip_limit_open += 1
            print(
                f"[开盘买入-夹档+Cond2] {code_6} 跳过: 开盘涨停 open={open_px:.2f} lu={lu:.2f}"
            )
            continue

        trig = max(ld, min(lu, round(float(lu), 2)))
        hits.append({
            "code_6": code_6,
            "name": name,
            "open_px": open_px,
            "lo": lo,
            "hi": hi,
            "ma5": ma5,
            "ma10": ma10,
            "trig": trig,
            "lu": lu,
        })

    S = len(codes)
    if sizing_mode == "clip_equity":
        try:
            L = max(1, int(params.get("clip_L", 2)))
        except (TypeError, ValueError):
            L = 2
        try:
            U = max(1, int(params.get("clip_U", 4)))
        except (TypeError, ValueError):
            U = 4
        if U < L:
            U = L
        S_eff = min(U, max(L, int(S)))
        try:
            eq = float(account.get("total_asset") or 0)
        except (TypeError, ValueError):
            eq = 0.0
        try:
            cash = float(account.get("cash") or 0)
        except (TypeError, ValueError):
            cash = 0.0

        print(
            f"[开盘买入-夹档+Cond2] 账户 total_asset={eq:.2f} cash={cash:.2f} "
            f"S={S} L={L} U={U} S_eff={S_eff} 进档候选={len(hits)}"
        )

        # 实盘硬约束：没有可用资金就不生成买入（禁止回退固定金额）
        if cash <= 1e-6:
            print("[开盘买入-夹档+Cond2] 可用资金为 0 或无效，不生成买入任务")
            return result

        if len(hits) > S_eff:
            n_skip_cap = len(hits) - S_eff
            print(
                f"[开盘买入-夹档+Cond2] 进档{len(hits)}只 > U/S_eff={S_eff}，"
                f"按股票池顺序只保留前 {S_eff} 只"
            )
            hits = hits[:S_eff]

        # 目标格：总权益/S_eff；实际可花：min(目标, 现金/只数)
        target = (eq if eq > 0 else cash) / float(S_eff)
        amount_per = min(target, cash) if hits else 0.0
        print(
            f"[开盘买入-夹档+Cond2] 仓位 clip_equity target={target:.0f} "
            f"amt={amount_per:.0f} (min目标与当时现金，不平分)"
        )
    else:
        amount_per = amount_fixed
        print(f"[开盘买入-夹档+Cond2] 仓位 fixed amt={amount_per:.0f}")

    for h in hits:
        code_6 = h["code_6"]
        v = vol_for(amount_per, h["open_px"])
        if v <= 0:
            n_skip_amt += 1
            print(
                f"[开盘买入-夹档+Cond2] {code_6} 跳过: 金额不足买1手 "
                f"(amt={amount_per:.0f} open={h['open_px']:.2f})"
            )
            continue
        print(
            f"[开盘买入-夹档+Cond2] {code_6} 夹档命中 open={h['open_px']:.2f} "
            f"[{h['lo']:.2f},{h['hi']:.2f}] 昨MA5={h['ma5']:.2f} 昨MA10={h['ma10']:.2f} "
            f"→ 卖一买入 触发价={h['trig']:.2f} 量={v} 金额约={amount_per:.0f}"
        )
        result.append({
            "stock_code": code_6,
            "stock_name": h["name"],
            "rule_type": "single_buy",
            "name": "开盘买入-夹档+Cond2",
            "price": h["trig"],
            "volume": v,
            "limit_up": round(float(h["lu"]), 2),
            "wait_unseal": False,
            "open_buy_ask": True,
            "early_order_enabled": False,
        })

    print(
        f"[开盘买入-夹档+Cond2] 合计生成 {len(result)} 条 | 池={S} "
        f"字段报错跳过={n_skip_field} Cond3跳过={n_skip_ma_lt} "
        f"Cond2跳过={n_skip_open_rel} 不在夹档={n_skip_band} "
        f"无涨跌停={n_skip_limit} 开盘涨停跳过={n_skip_limit_open} "
        f"超U截断={n_skip_cap} 金额不足={n_skip_amt}"
    )
    return result
