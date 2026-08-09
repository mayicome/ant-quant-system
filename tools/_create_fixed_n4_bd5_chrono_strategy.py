# -*- coding: utf-8 -*-
"""Create/update strategy: Cond2 open-clip pool → best_buy after 昨MA5 break, fixed N=4 race.

- Keep ALL open-clip+Cond2 hits (no strength truncate).
- Size each watch = equity / min(N, n_hits).
- best_buy @ 昨MA5: break → track low → rebound base 0.3% + dynamic threshold.
- First ~N fills win via cash (chrono race).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"

STRATEGY_ID = "strategy_a2817f2c"
STRATEGY_NAME = "开盘夹档+Cond2-跌破昨MA5-动态反弹0.3%-固定N4全池赛跑"

STRATEGY_CODE = r'''# 买：开盘夹档+Cond2 进档后，盘中跌破昨MA5 → 动态反弹再买（全池时间序赛跑）
# - 资格与「开盘夹档+Cond2」相同：昨MA5/昨MA10 夹档 + 开盘相对早盘MA5∈[0%,2%] + 开盘非涨停
# - 买入：不对强度序截断；全池挂 best_buy@昨MA5
#   跌破触发价后跟踪最低价，反弹达到有效阈值再买：
#     eff_rise = min(max_rise, rise_percent + 跌破深度% * rise_scale)
#   默认 rise_percent=0.3, rise_scale=0.35, max_rise=4, dynamic_thresholds=1
# - 仓位 fixed_n_equity：每笔=总权益/min(N,进档只数)；N 只限制「最多成交几只」
#   实盘靠现金自然截断：先成交者占坑，满 N 份后其余买不起
# - 建议生成时刻 09:25（需要今开盘做 Cond2）
#
# params：sizing_mode, fixed_n, buy_amount_per_stock, min_order_amount,
#         rise_percent, rise_scale, max_rise_percent, dynamic_thresholds,
#         require_open_rel_ma5, open_rel_ma5_lo, open_rel_ma5_hi,
#         require_ma5_lt_ma10_lt_ma20

LIMIT_OPEN_EPS = 0.011

REQUIRE_OPEN_REL_MA5 = True
OPEN_REL_MA5_LO = 0.0
OPEN_REL_MA5_HI = 0.02
REQUIRE_MA5_LT_MA10_LT_MA20 = False

DEFAULT_RISE_PERCENT = 0.3
DEFAULT_RISE_SCALE = 0.35
DEFAULT_MAX_RISE_PERCENT = 4.0
DEFAULT_DYNAMIC_THRESHOLDS = 1


def run(codes, prices, get_name, account, params):
    result = []
    params = params or {}
    account = account or {}
    amount_fixed = float(params.get("buy_amount_per_stock", 50000))
    min_order_amount = float(params.get("min_order_amount", 5000))
    sizing_mode = str(params.get("sizing_mode") or "fixed_n_equity").strip().lower()
    if sizing_mode in ("clip", "account_clip", "clip_s"):
        sizing_mode = "clip_equity"
    if sizing_mode in ("fixed_n", "fixedn", "n_equity", "fixed_n_full"):
        sizing_mode = "fixed_n_equity"

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

    def _param_int(key, default):
        if key not in params or params.get(key) is None or params.get(key) == "":
            return int(default)
        try:
            return int(params.get(key))
        except (TypeError, ValueError):
            return int(default)

    req_open_rel = True
    open_rel_lo = _param_float("open_rel_ma5_lo", OPEN_REL_MA5_LO)
    open_rel_hi = _param_float("open_rel_ma5_hi", OPEN_REL_MA5_HI)
    req_ma_lt = _param_bool("require_ma5_lt_ma10_lt_ma20", REQUIRE_MA5_LT_MA10_LT_MA20)
    rise_percent = _param_float("rise_percent", DEFAULT_RISE_PERCENT)
    rise_scale = _param_float("rise_scale", DEFAULT_RISE_SCALE)
    max_rise_percent = _param_float("max_rise_percent", DEFAULT_MAX_RISE_PERCENT)
    dynamic_thresholds = _param_int("dynamic_thresholds", DEFAULT_DYNAMIC_THRESHOLDS)

    def vol_for(amt, price):
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
        print("[跌破昨MA5-动态反弹N4] 股票池为空：请先导入选股/填写股票代码")
        return result

    top_n = params.get("generate_top_n")
    try:
        top_n_i = int(top_n) if top_n is not None else 0
    except (TypeError, ValueError):
        top_n_i = 0
    if sizing_mode in ("clip_equity", "fixed_n_equity") and top_n_i > 0:
        print(
            f"[跌破昨MA5-动态反弹N4] 提示: generate_top_n={top_n_i} 会截断股票池；"
            f"本策略需全池赛跑，建议不要勾选只生成前N"
        )

    print(
        f"[跌破昨MA5-动态反弹N4] Cond2开盘相对早盘MA5="
        f"{'开' if req_open_rel else '关'}[{open_rel_lo:.2%},{open_rel_hi:.2%}] "
        f"Cond3 MA5<MA10<MA20={'开' if req_ma_lt else '关'} "
        f"rise={rise_percent}% dyn={dynamic_thresholds} scale={rise_scale} max={max_rise_percent}%"
    )

    n_skip_field = 0
    n_skip_band = 0
    n_skip_amt = 0
    n_skip_limit = 0
    n_skip_limit_open = 0
    n_skip_open_rel = 0
    n_skip_ma_lt = 0
    n_skip_trig = 0
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
            print(f"[跌破昨MA5-动态反弹N4] {code_6} 报错跳过: {err}")
            continue

        ma5, err = _require_float(p, "昨MA5", code_6)
        if err:
            n_skip_field += 1
            print(f"[跌破昨MA5-动态反弹N4] {code_6} 报错跳过: {err}")
            continue

        ma10, err = _require_float(p, "昨MA10", code_6)
        if err:
            n_skip_field += 1
            print(f"[跌破昨MA5-动态反弹N4] {code_6} 报错跳过: {err}")
            continue

        if req_ma_lt:
            ma20, err = _require_float(p, "昨MA20", code_6)
            if err:
                n_skip_field += 1
                print(f"[跌破昨MA5-动态反弹N4] {code_6} 报错跳过: {err}")
                continue
            if not (float(ma5) < float(ma10) < float(ma20)):
                n_skip_ma_lt += 1
                print(
                    f"[跌破昨MA5-动态反弹N4] {code_6} 跳过: Cond3非MA5<MA10<MA20 "
                    f"昨MA5={ma5:.2f} 昨MA10={ma10:.2f} 昨MA20={ma20:.2f}"
                )
                continue

        if req_open_rel:
            ma5_am, err = _require_float(p, "5日", code_6)
            if err:
                n_skip_field += 1
                print(f"[跌破昨MA5-动态反弹N4] {code_6} 报错跳过(Cond2需早盘MA5): {err}")
                continue
            rel = open_px / ma5_am - 1.0
            if rel < float(open_rel_lo) or rel > float(open_rel_hi):
                n_skip_open_rel += 1
                print(
                    f"[跌破昨MA5-动态反弹N4] {code_6} 跳过: Cond2开盘相对早盘MA5="
                    f"{rel * 100:.3f}% 不在[{open_rel_lo * 100:.1f}%,{open_rel_hi * 100:.1f}%] "
                    f"open={open_px:.2f} 早盘MA5(5日)={ma5_am:.2f}"
                )
                continue

        lo = min(ma5, ma10)
        hi = max(ma5, ma10)
        if not (lo <= open_px <= hi):
            n_skip_band += 1
            print(
                f"[跌破昨MA5-动态反弹N4] {code_6} 跳过: 开盘不在夹档 "
                f"open={open_px:.2f} lo={lo:.2f} hi={hi:.2f} "
                f"(昨MA5={ma5:.2f} 昨MA10={ma10:.2f})"
            )
            continue

        ld, lu = _limits(code_6, p)
        if lu <= 0 or ld <= 0:
            n_skip_limit += 1
            print(f"[跌破昨MA5-动态反弹N4] {code_6} 跳过: 无涨跌停 (昨收={p.get('昨收盘')!r})")
            continue

        is_limit_open = open_px + 1e-9 >= lu - LIMIT_OPEN_EPS
        if is_limit_open:
            n_skip_limit_open += 1
            print(
                f"[跌破昨MA5-动态反弹N4] {code_6} 跳过: 开盘涨停 open={open_px:.2f} lu={lu:.2f}"
            )
            continue

        # 跌破触发价 = 昨MA5，钳到涨跌停带
        trig = max(ld, min(lu, round(float(ma5), 2)))
        if trig <= 0:
            n_skip_trig += 1
            print(f"[跌破昨MA5-动态反弹N4] {code_6} 跳过: 触发价无效 ma5={ma5!r}")
            continue

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
    n_avail = len(hits)
    if sizing_mode in ("clip_equity", "fixed_n_equity"):
        try:
            eq = float(account.get("total_asset") or 0)
        except (TypeError, ValueError):
            eq = 0.0
        try:
            cash = float(account.get("cash") or 0)
        except (TypeError, ValueError):
            cash = 0.0

        if cash <= 1e-6:
            print("[跌破昨MA5-动态反弹N4] 可用资金为 0 或无效，不生成买入任务")
            return result

        if sizing_mode == "fixed_n_equity":
            try:
                N = max(1, int(params.get("fixed_n", 4)))
            except (TypeError, ValueError):
                N = 4
            # 关键：不按强度截断；S_eff 只用于仓位分母与「最多成交 N 只」
            S_eff = min(N, n_avail) if n_avail > 0 else 0
            print(
                f"[跌破昨MA5-动态反弹N4] 账户 total_asset={eq:.2f} cash={cash:.2f} "
                f"S={S} fixed_n={N} 进档候选={n_avail} S_eff={S_eff} "
                f"(全池挂单，不截断；先成交先占坑)"
            )
            if S_eff <= 0:
                print("[跌破昨MA5-动态反弹N4] 无进档候选，不生成买入")
                return result
            target = (eq if eq > 0 else cash) / float(S_eff)
            amount_per = min(target, cash) if hits else 0.0
            print(
                f"[跌破昨MA5-动态反弹N4] 仓位 fixed_n_equity N={N} S_eff={S_eff} "
                f"target={target:.0f} amt={amount_per:.0f} 挂单={n_avail}只"
            )
        else:
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
            print(
                f"[跌破昨MA5-动态反弹N4] 账户 total_asset={eq:.2f} cash={cash:.2f} "
                f"S={S} L={L} U={U} S_eff={S_eff} 进档候选={n_avail} "
                f"(clip 模式仍不截断挂单，仅用 S_eff 分仓)"
            )
            target = (eq if eq > 0 else cash) / float(S_eff)
            amount_per = min(target, cash) if hits else 0.0
            print(
                f"[跌破昨MA5-动态反弹N4] 仓位 clip_equity target={target:.0f} "
                f"amt={amount_per:.0f}"
            )
    else:
        amount_per = amount_fixed
        print(f"[跌破昨MA5-动态反弹N4] 仓位 fixed amt={amount_per:.0f}")

    for h in hits:
        code_6 = h["code_6"]
        v = vol_for(amount_per, h["trig"])
        if v <= 0:
            n_skip_amt += 1
            print(
                f"[跌破昨MA5-动态反弹N4] {code_6} 跳过: 金额不足买1手 "
                f"(amt={amount_per:.0f} trig={h['trig']:.2f})"
            )
            continue
        print(
            f"[跌破昨MA5-动态反弹N4] {code_6} 进档 open={h['open_px']:.2f} "
            f"[{h['lo']:.2f},{h['hi']:.2f}] 昨MA5={h['ma5']:.2f} "
            f"→ 弹性买入 触发价={h['trig']:.2f} rise={rise_percent}% "
            f"dyn={dynamic_thresholds} 量={v} 金额约={amount_per:.0f}"
        )
        result.append({
            "stock_code": code_6,
            "stock_name": h["name"],
            "rule_type": "best_buy",
            "name": "跌破昨MA5-动态反弹-全池赛跑",
            "trigger_price": h["trig"],
            "rise_percent": rise_percent,
            "rise_scale": rise_scale,
            "max_rise_percent": max_rise_percent,
            "dynamic_thresholds": dynamic_thresholds,
            "volume": v,
            "limit_up": round(float(h["lu"]), 2),
            "wait_unseal": False,
            "open_buy_ask": False,
            "early_order_enabled": False,
        })

    print(
        f"[跌破昨MA5-动态反弹N4] 合计生成 {len(result)} 条挂单 | 池={S} "
        f"字段报错跳过={n_skip_field} Cond3跳过={n_skip_ma_lt} "
        f"Cond2跳过={n_skip_open_rel} 不在夹档={n_skip_band} "
        f"无涨跌停={n_skip_limit} 开盘涨停跳过={n_skip_limit_open} "
        f"触发价无效={n_skip_trig} 金额不足={n_skip_amt}"
    )
    return result
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default=STRATEGY_ID, help="strategy id to write/overwrite")
    args = ap.parse_args()
    sid = str(args.id).strip() or STRATEGY_ID
    if not sid.startswith("strategy_"):
        sid = "strategy_" + sid

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = {
        "id": sid,
        "name": STRATEGY_NAME,
        "enabled": True,
        "stock_codes": [],
        "strategy_params": {
            "buy_amount_per_stock": 50000.0,
            "min_order_amount": 5000.0,
            "sizing_mode": "fixed_n_equity",
            "fixed_n": 4,
            "clip_L": 2,
            "clip_U": 4,
            "rise_percent": 0.3,
            "rise_scale": 0.35,
            "max_rise_percent": 4.0,
            "dynamic_thresholds": 1,
            "require_open_rel_ma5": True,
            "open_rel_ma5_lo": 0.0,
            "open_rel_ma5_hi": 0.02,
            "require_ma5_lt_ma10_lt_ma20": False,
        },
        "strategy_code": STRATEGY_CODE,
        "scheduled_generate_at": None,
    }
    path = OUT_DIR / f"{sid}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    print(f"id={sid}")
    print(f"name={doc['name']}")
    print(
        f"fixed_n={doc['strategy_params']['fixed_n']} "
        f"rise={doc['strategy_params']['rise_percent']} "
        f"dyn={doc['strategy_params']['dynamic_thresholds']}"
    )


if __name__ == "__main__":
    main()
