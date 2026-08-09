# -*- coding: utf-8 -*-
"""Clone Cond2 open-clip strategy → fixed_n_equity N=5."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "strategy_generator_app" / "config" / "strategies" / "strategy_e6d1b97b.json"
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"


def main() -> None:
    src = json.loads(SRC.read_text(encoding="utf-8"))
    code = src["strategy_code"]

    old_header = (
        "# - 仓位：params.sizing_mode\n"
        "#     fixed = buy_amount_per_stock\n"
        "#     clip_equity（实盘推荐）= 每笔=min(总权益/clip(S,L,U), 当时现金)；进档最多买 U 只\n"
        "# - 建议生成时刻 09:25（Cond2 需要今开盘；隔夜选股无法做 Cond2）\n"
        "#\n"
        "# params：sizing_mode, buy_amount_per_stock, min_order_amount, clip_L, clip_U,\n"
        "#         require_open_rel_ma5, open_rel_ma5_lo, open_rel_ma5_hi,\n"
        "#         require_ma5_lt_ma10_lt_ma20"
    )
    new_header = (
        "# - 仓位：params.sizing_mode\n"
        "#     fixed = buy_amount_per_stock\n"
        "#     clip_equity = 每笔=min(总权益/clip(S,L,U), 当时现金)；进档最多买 U 只（强度分）\n"
        "#     fixed_n_equity（本策略默认）= 每笔=总权益/min(N,进档只数)；最多买 N 只（强度分）\n"
        "#       稀缺日进档 k<N 时按权益/k 打满，不再按死 N 摊薄\n"
        "# - 建议生成时刻 09:25（Cond2 需要今开盘；隔夜选股无法做 Cond2）\n"
        "#\n"
        "# params：sizing_mode, fixed_n, buy_amount_per_stock, min_order_amount, clip_L, clip_U,\n"
        "#         require_open_rel_ma5, open_rel_ma5_lo, open_rel_ma5_hi,\n"
        "#         require_ma5_lt_ma10_lt_ma20"
    )
    if old_header not in code:
        raise SystemExit("header block not found")
    code = code.replace(old_header, new_header, 1)

    anchor = "REQUIRE_MA5_LT_MA10_LT_MA20 = False\n\n\ndef run("
    insert = (
        "REQUIRE_MA5_LT_MA10_LT_MA20 = False\n"
        "\n"
        "# 与 sector_stock_filter.EXPORT_ELIG_WEIGHT 对齐（截断用；非收益优选）\n"
        "CLIP_ELIG_WEIGHT = 8\n"
        "\n"
        "\n"
        "def run("
    )
    if anchor not in code:
        raise SystemExit("anchor not found")
    code = code.replace(anchor, insert, 1)

    code = code.replace(
        'sizing_mode = str(params.get("sizing_mode") or "clip_equity").strip().lower()\n'
        '    if sizing_mode in ("clip", "account_clip", "clip_s"):\n'
        '        sizing_mode = "clip_equity"',
        'sizing_mode = str(params.get("sizing_mode") or "fixed_n_equity").strip().lower()\n'
        '    if sizing_mode in ("clip", "account_clip", "clip_s"):\n'
        '        sizing_mode = "clip_equity"\n'
        '    if sizing_mode in ("fixed_n", "fixedn", "n_equity", "fixed_n_full"):\n'
        '        sizing_mode = "fixed_n_equity"',
        1,
    )

    code = code.replace(
        'if sizing_mode == "clip_equity" and top_n_i > 0:\n'
        "        print(\n"
        '            f"[开盘买入-夹档+Cond2] 提示: generate_top_n={top_n_i}，S 以截断后池子为准；"\n'
        '            f"当 N>=U 时通常不影响 clip 单票比例"\n'
        "        )",
        'if sizing_mode in ("clip_equity", "fixed_n_equity") and top_n_i > 0:\n'
        "        print(\n"
        '            f"[开盘买入-夹档+Cond2] 提示: generate_top_n={top_n_i} 会截断股票池；"\n'
        '            f"fixed_n/clip 取票仍以进档结果为准，建议不要勾选只生成前N"\n'
        "        )",
        1,
    )

    old_sizing = '''    S = len(codes)
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
'''

    new_sizing = '''    def _rank_int(v, default=10**9):
        try:
            if v is None or v == "":
                return default
            return int(float(v))
        except (TypeError, ValueError):
            return default

    def _clip_strength_key(h):
        code_6 = h["code_6"]
        p = prices.get(code_6) or {}
        if not isinstance(p, dict):
            p = {}
        strength = (params.get("clip_strength_by_code") or {}) if isinstance(params, dict) else {}
        meta = strength.get(code_6) if isinstance(strength, dict) else None
        if not isinstance(meta, dict):
            meta = {}
        elig = _rank_int(meta.get("合格榜内序位", p.get("合格榜内序位", h.get("elig"))))
        rs = _rank_int(meta.get("合格榜标签内RS排名", p.get("合格榜标签内RS排名", h.get("rs"))))
        return (elig * CLIP_ELIG_WEIGHT + rs, elig, rs, code_6)

    S = len(codes)
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
            print("[开盘买入-夹档+Cond2] 可用资金为 0 或无效，不生成买入任务")
            return result

        if sizing_mode == "fixed_n_equity":
            try:
                N = max(1, int(params.get("fixed_n", 5)))
            except (TypeError, ValueError):
                N = 5
            n_avail = len(hits)
            S_eff = min(N, n_avail) if n_avail > 0 else 0
            print(
                f"[开盘买入-夹档+Cond2] 账户 total_asset={eq:.2f} cash={cash:.2f} "
                f"S={S} fixed_n={N} 进档候选={n_avail} S_eff={S_eff} (按实际进档)"
            )
            if S_eff <= 0:
                print("[开盘买入-夹档+Cond2] 无进档候选，不生成买入")
                return result
            if n_avail > S_eff:
                n_skip_cap = n_avail - S_eff
                hits = sorted(hits, key=_clip_strength_key)[:S_eff]
                print(
                    f"[开盘买入-夹档+Cond2] 进档{n_avail}只 > N={N}，"
                    f"按强度分(Elig*{CLIP_ELIG_WEIGHT}+标签内RS)保留前 {S_eff} 只"
                )
            else:
                hits = sorted(hits, key=_clip_strength_key)
            target = (eq if eq > 0 else cash) / float(S_eff)
            amount_per = min(target, cash) if hits else 0.0
            print(
                f"[开盘买入-夹档+Cond2] 仓位 fixed_n_equity N={N} S_eff={S_eff} "
                f"target={target:.0f} amt={amount_per:.0f}"
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
                f"[开盘买入-夹档+Cond2] 账户 total_asset={eq:.2f} cash={cash:.2f} "
                f"S={S} L={L} U={U} S_eff={S_eff} 进档候选={len(hits)}"
            )
            if len(hits) > S_eff:
                n_skip_cap = len(hits) - S_eff
                hits = sorted(hits, key=_clip_strength_key)[:S_eff]
                print(
                    f"[开盘买入-夹档+Cond2] 进档{len(hits) + n_skip_cap}只 > U/S_eff={S_eff}，"
                    f"按强度分(Elig*{CLIP_ELIG_WEIGHT}+标签内RS)保留前 {S_eff} 只"
                )
            target = (eq if eq > 0 else cash) / float(S_eff)
            amount_per = min(target, cash) if hits else 0.0
            print(
                f"[开盘买入-夹档+Cond2] 仓位 clip_equity target={target:.0f} "
                f"amt={amount_per:.0f} (min目标与当时现金，不平分)"
            )
    else:
        amount_per = amount_fixed
        print(f"[开盘买入-夹档+Cond2] 仓位 fixed amt={amount_per:.0f}")
'''

    if old_sizing not in code:
        raise SystemExit("sizing block not found")
    code = code.replace(old_sizing, new_sizing, 1)

    code = code.replace('"name": "开盘买入-夹档+Cond2"', '"name": "开盘买入-夹档+Cond2-固定N"', 1)
    code = code.replace("[开盘买入-夹档+Cond2]", "[开盘买入-夹档+Cond2-固定N]")

    compile(code, "<fixed_n5>", "exec")

    rid = "strategy_" + uuid.uuid4().hex[:8]
    out = {
        "id": rid,
        "name": "开盘夹档+Cond2-固定N5全仓",
        "enabled": True,
        "stock_codes": [],
        "strategy_params": {
            "buy_amount_per_stock": 50000.0,
            "min_order_amount": 5000.0,
            "sizing_mode": "fixed_n_equity",
            "fixed_n": 5,
            "clip_L": 2,
            "clip_U": 4,
            "require_open_rel_ma5": True,
            "open_rel_ma5_lo": 0.0,
            "open_rel_ma5_hi": 0.02,
            "require_ma5_lt_ma10_lt_ma20": False,
        },
        "strategy_code": code,
        "scheduled_generate_at": None,
    }
    path = OUT_DIR / (rid + ".json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)
    print("id", rid)


if __name__ == "__main__":
    main()
