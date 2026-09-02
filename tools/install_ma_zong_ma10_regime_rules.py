# -*- coding: utf-8 -*-
"""安装选股规则：马总 MA10 风格三包（CORE / 七月风 / 六月风）。

基于 tools/_rule_src_ma_zong_next_day_ma10.py（近10日最近涨停 + 涨停后日线未触 MA10），
硬门槛再叠加：
  收盘 > MA10，且落入对应风格包，且不在黑名单。

用法:
  python tools/install_ma_zong_ma10_regime_rules.py

盘后流程:
  1) python tools/ma10_regime_switch.py   → 今日建议哪一包
  2) 选股页只勾对应规则跑一遍
  3) 跌破 MA10 买 + entry_window=1 + sell-hold 2
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

SRC = Path(__file__).resolve().parent / "_rule_src_ma_zong_next_day_ma10.py"
OUT_DIR = ROOT / "data" / "sector_rules"

# name, PACK_MODE, brief
PACKS = [
    (
        "马总-MA10核-CORE",
        "CORE_ONLY",
        "次日MA10底池 ∩ 上MA10 ∩ (B∩市值≤91) - 黑名单",
    ),
    (
        "马总-MA10核-七月风",
        "CORE_PLUS_JULY",
        "次日MA10底池 ∩ 上MA10 ∩ B∩(市值≤91∨价≤18∨市值≤54) - 黑名单",
    ),
    (
        "马总-MA10核-六月风",
        "CORE_PLUS_JUNE",
        "次日MA10底池 ∩ 上MA10 ∩ [(B∩市值≤91)∪(市值170~353∩MA5>MA10)] - 黑名单",
    ),
]

INJECT_AFTER_HOT_MODE = '''
# --- MA10 regime pack (install_ma_zong_ma10_regime_rules) ---
PACK_MODE = "{pack_mode}"
MV_CORE = 91.0
MV_JULY_SMALL = 54.0
PX_JULY = 18.0
MV_JUNE_LO = 170.0
MV_JUNE_HI = 353.0
RS5_B = 0.135
RS10_B = 0.06
RS20_B = 0.25
RS20_BLACK = 0.40
'''

# Replaces from "    above_ma = (" through end of select()
NEW_TAIL = r'''
    # 次日MA10 软条件（诊断列；本规则入选不再依赖）
    above_ma = (
        close_price is not None
        and ma5 is not None
        and ma20 is not None
        and float(close_price) > float(ma5)
        and float(close_price) > float(ma20)
    )

    cond_board = bool(board_ok)
    cond_inflow = bool(inflow_ok)
    cond_prior = bool(no_big_move)
    cond_ma = bool(above_ma)
    meet_logic1 = cond_board and cond_inflow and cond_prior and cond_ma
    fail_reasons = _fail_reasons_logic1(
        cond_board=cond_board,
        cond_inflow=cond_inflow,
        cond_prior=cond_prior,
        cond_ma=cond_ma,
        owned_board=owned_board,
        inflow_wan=inflow_wan,
        max_prior=max_prior,
        thr=thr,
        prior_rets=prior_rets,
        close_price=close_price,
        ma5=ma5,
        ma20=ma20,
        board_err=board_err,
        inflow_err=bundle.get("inflow_err") or "",
    )

    try:
        lu_date_s = lu_date.strftime("%Y-%m-%d")
    except Exception:
        lu_date_s = str(lu_date)

    rs5 = _absolute_rs(closes, RS_LOOKBACK_5)
    rs10 = _absolute_rs(closes, RS_LOOKBACK)
    rs20 = _absolute_rs(closes, RS_LOOKBACK_20)
    px = None if close_price is None else float(close_price)

    cond_above_ma10 = (
        close_price is not None
        and ma10 is not None
        and float(close_price) > float(ma10)
    )
    cond_B = (
        rs5 is not None
        and rs10 is not None
        and rs20 is not None
        and float(rs5) > float(RS5_B)
        and float(rs10) > float(RS10_B)
        and float(rs20) < float(RS20_B)
    )
    cond_black = False
    if ma5 is not None and ma10 is not None and ma20 is not None:
        if float(ma5) <= float(ma10) and float(ma10) >= float(ma20):
            cond_black = True
    if rs20 is not None and float(rs20) > float(RS20_BLACK):
        cond_black = True

    core_ok = bool(
        cond_B
        and (mv is not None)
        and float(mv) <= float(MV_CORE)
        and (not cond_black)
    )
    july_sat = bool(
        (not cond_black)
        and (
            (px is not None and float(px) <= float(PX_JULY))
            or (mv is not None and float(mv) <= float(MV_JULY_SMALL))
        )
    )
    june_sat = bool(
        (not cond_black)
        and (mv is not None)
        and (float(MV_JUNE_LO) <= float(mv) <= float(MV_JUNE_HI))
        and (ma5 is not None)
        and (ma10 is not None)
        and (float(ma5) > float(ma10))
    )

    if PACK_MODE == "CORE_ONLY":
        pack_ok = bool(core_ok)
        pack_label = "CORE"
    elif PACK_MODE == "CORE_PLUS_JULY":
        pack_ok = bool((core_ok or (july_sat and cond_B)) and (not cond_black))
        pack_label = "七月风"
    elif PACK_MODE == "CORE_PLUS_JUNE":
        pack_ok = bool((core_ok or june_sat) and (not cond_black))
        pack_label = "六月风"
    else:
        pack_ok = False
        pack_label = str(PACK_MODE)

    # 底池已在 select 前段保证：近10日最近涨停 + 涨停后未触 MA10
    meet_all = bool(cond_above_ma10 and pack_ok)

    skip_reasons = []
    if not cond_above_ma10:
        skip_reasons.append("涨停日收盘未站上MA10")
    if cond_black:
        skip_reasons.append("黑名单(空头乱序或RS20>40%)")
    if cond_above_ma10 and (not pack_ok) and (not cond_black):
        skip_reasons.append("未进入风格包%s" % PACK_MODE)
    skip_text = "；".join(skip_reasons)

    extra = {
        "热门模式": HOT_MODE,
        "风格包": PACK_MODE,
        "风格包标签": pack_label,
        "满足条件": bool(meet_all),
        "不满足的原因": skip_text if (not meet_all) else "",
        "马总原满足条件": bool(meet_logic1),
        "马总原不满足原因": fail_reasons if (not meet_logic1) else "",
        "涨停锚点日": lu_date_s,
        "涨停日期": lu_date_s,  # 引擎补全主力净流入/概念排名认此列
        "距涨停交易日数": lu_days_ago if lu_days_ago != "" else 0,
        "条件_涨停锚点有效": True,
        "条件_涨停后未触MA10": True,
        "条件_收盘站上MA10": bool(cond_above_ma10),
        "条件_B因子": bool(cond_B),
        "条件_黑名单": bool(cond_black),
        "条件_核CORE": bool(core_ok),
        "条件_七月卫星": bool(july_sat and cond_B),
        "条件_六月卫星": bool(june_sat),
        "条件_风格包通过": bool(pack_ok),
        "前十个交易日最高涨幅": "" if max_prior is None else round(float(max_prior) * 100.0, 4),
        "前十个交易日最高涨幅日期": "" if max_prior_date is None else max_prior_date.strftime("%Y-%m-%d"),
        "条件_当日涨停": bool(is_lu_today),
        "条件_行业或概念排名达标": bool(cond_board),
        "条件_行业前N": int(BOARD_TOP_N_INDUSTRY),
        "条件_概念前N": int(BOARD_TOP_N_CONCEPT),
        "条件_主力净流入>=3000万": bool(cond_inflow),
        "条件_前10日无大涨": bool(cond_prior),
        "条件_收盘站上MA5且MA20": bool(cond_ma),
        "除权排查天数": int(EX_DIV_LOOKBACK),
        "除权缺口阈值加EPS": round(float(EX_DIV_EPS) * 100.0, 2),
        "前10日大涨阈值": round(float(thr) * 100.0, 2),
        "所属行业最高排名名次": owned_board["所属行业最高排名名次"],
        "所属行业最高排名名称": owned_board["所属行业最高排名名称"],
        "所属行业最高排名涨幅": owned_board["所属行业最高排名涨幅"],
        "所属行业最高排名净占比": owned_board["所属行业最高排名净占比"],
        "所属概念最高排名名次": owned_board["所属概念最高排名名次"],
        "所属概念最高排名名称": owned_board["所属概念最高排名名称"],
        "所属概念最高排名涨幅": owned_board["所属概念最高排名涨幅"],
        "所属概念最高排名净占比": owned_board["所属概念最高排名净占比"],
        "所属行业排名明细": owned_board.get("所属行业排名明细") or "",
        "所属概念排名明细": owned_board.get("所属概念排名明细") or "",
        "命中前30标签": hit_str,
        "最佳板块排名": "" if best_rk is None else int(best_rk),
        "最佳板块名称": best_name,
        "最佳板块类型": best_kind,
        "主力净流入_万元": "" if inflow_wan is None else round(float(inflow_wan), 2),
        "主力净流入": "" if inflow_wan is None else ("%s万" % round(float(inflow_wan), 2)),
        "主力净流入-净占比": "" if inflow_ratio is None else round(float(inflow_ratio), 4),
        "净流入占流通%": "" if inflow_pct_float is None else round(float(inflow_pct_float), 4),
        "板块排名备注": board_err,
        "主力净流入备注": "" if inflow_wan is not None else (bundle.get("inflow_err") or ""),
        "最近10个交易日内的涨停板数量": int(lu_count),
        "最近的涨停板是几日前": lu_days_ago if lu_days_ago != "" else 0,
        "收盘价": "" if close_price is None else round(float(close_price), 4),
        "MA5": "" if ma5 is None else round(ma5, 4),
        "MA10": "" if ma10 is None else round(ma10, 4),
        "MA20": "" if ma20 is None else round(ma20, 4),
        "均线差占比": "" if gap is None else round(gap, 6),
        "TOP_N": int(TOP_N),
        "RS_TOP_K": int(RS_TOP_K),
        "RS_LO": int(RS_LO),
        "RS_HI": RS_HI,
        "RS_TOP_FRAC_NUM": int(RS_TOP_FRAC_NUM),
        "RS_TOP_FRAC_DEN": int(RS_TOP_FRAC_DEN),
        "RS_LOOKBACK": int(RS_LOOKBACK),
        "RS_LOOKBACK_5": int(RS_LOOKBACK_5),
        "RS_LOOKBACK_20": int(RS_LOOKBACK_20),
        "MIN_MEMBERS": int(MIN_MEMBERS),
        "ELIG_LO": int(ELIG_LO),
        "ELIG_HI": int(ELIG_HI),
        "ELIG_HI_SECTOR": int(ELIG_HI_SECTOR),
        "ELIG_HI_CONCEPT": int(ELIG_HI_CONCEPT),
        "MIN_FLOAT_MV_YI": float(MIN_FLOAT_MV_YI),
        "MA_GAP_LO": float(MA_GAP_LO),
        "MA_GAP_HI": float(MA_GAP_HI),
        "ANY_TAG": bool(ANY_TAG),
        "REQUIRE_MIN_FLOAT_MV": bool(REQUIRE_MIN_FLOAT_MV),
        "REQUIRE_MA_GAP": bool(REQUIRE_MA_GAP),
        "REQUIRE_MA_LT_ALIGN": bool(REQUIRE_MA_LT_ALIGN),
        "REQUIRE_NO_RECENT_LU": bool(REQUIRE_NO_RECENT_LU),
        "APPLY_STOCK_FILTERS": bool(APPLY_STOCK_FILTERS),
        "BOARD_TOP_N_INDUSTRY": int(BOARD_TOP_N_INDUSTRY),
        "BOARD_TOP_N_CONCEPT": int(BOARD_TOP_N_CONCEPT),
        "BOARD_TOP_N": int(BOARD_TOP_N),
        "BOARD_SHOW_TOP_N": int(BOARD_SHOW_TOP_N),
        "MIN_INFLOW_WAN": float(MIN_INFLOW_WAN),
        "PRIOR_LOOKBACK": int(PRIOR_LOOKBACK),
        "LU_LOOKBACK": int(LU_LOOKBACK),
        "MA_TOUCH_PERIOD": int(MA_TOUCH_PERIOD),
        "EX_DIV_LOOKBACK": int(EX_DIV_LOOKBACK),
        "EX_DIV_EPS": float(EX_DIV_EPS),
        "PACK_MODE": str(PACK_MODE),
        "MV_CORE": float(MV_CORE),
        "MV_JULY_SMALL": float(MV_JULY_SMALL),
        "PX_JULY": float(PX_JULY),
        "MV_JUNE_LO": float(MV_JUNE_LO),
        "MV_JUNE_HI": float(MV_JUNE_HI),
        "RS5_B": float(RS5_B),
        "RS10_B": float(RS10_B),
        "RS20_B": float(RS20_B),
        "RS20_BLACK": float(RS20_BLACK),
    }
    extra.update(hot)
    for _key, _val in (
        ("近5日RS", rs5),
        ("近10日RS", rs10),
        ("近20日RS", rs20),
    ):
        if _val is not None:
            extra[_key] = round(float(_val), 6)
    if mv is not None and extra.get("流通市值_亿") in ("", None):
        extra["流通市值_亿"] = round(mv, 2)
    extra.update(contrast)

    if not meet_all:
        extra["_skip"] = skip_text or ("未过风格包%s" % PACK_MODE)
        return False, extra
    return True, extra
'''


HEADER_PREFIX = """# 马总选股逻辑 · MA10 风格包 {pack_mode}
# 说明：{brief}
# 底池同「马总选股逻辑-次日MA10」：近10日最近涨停 + 涨停后日线未触MA10（除权相对涨停日前）
# 硬入选门槛（本规则；收盘/MA/RS/B/黑名单均相对涨停锚点日）：
#   1) 涨停日收盘 > MA10
#   2) 落入风格包 {pack_mode}（与 tools/ma10_regime_switch.py 一致）
#   3) 非黑名单：(MA5≤MA10且MA10≥MA20) 或 RS20>40%
# B：RS5>13.5% 且 RS10>6% 且 RS20<25%（相对涨停日）
# 「马总原满足条件」仍输出供对照，但不作为入选门槛
# 另输出选股日对照列：收盘/MA/RS/净流入/板块排名/市值（后缀_选股日）
# 交易建议：跌破 MA10 买；挂单窗 1 日；卖出持有口径 sell-hold 2
#
"""


def build_code(pack_mode: str, brief: str) -> str:
    raw = SRC.read_text(encoding="utf-8")
    if "def select(" not in raw:
        raise SystemExit("rule source missing select()")

    lines = raw.splitlines(keepends=True)
    i0 = 0
    for i, ln in enumerate(lines):
        if ln.startswith("USE_EM_CANDIDATE_POOL"):
            i0 = i
            break
    body = "".join(lines[i0:])

    marker = 'HOT_MODE = "ma_zong_next_day_ma10"'
    if marker not in body:
        raise SystemExit("HOT_MODE marker not found in next_day source")
    body = body.replace(
        marker,
        'HOT_MODE = "ma_zong_ma10_%s"\n' % pack_mode
        + INJECT_AFTER_HOT_MODE.format(pack_mode=pack_mode).lstrip("\n"),
        1,
    )

    cut = "    above_ma = ("
    idx = body.find(cut)
    if idx < 0:
        raise SystemExit("above_ma block not found")
    body = body[:idx] + NEW_TAIL.lstrip("\n")
    if not body.endswith("\n"):
        body += "\n"
    return HEADER_PREFIX.format(pack_mode=pack_mode, brief=brief) + body


def _remove_same_name_files(name: str, keep_id: str | None = None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("%s__*.json" % name):
        try:
            import json

            data = json.loads(old.read_text(encoding="utf-8"))
            rid = str(data.get("id") or "")
            if keep_id and rid == keep_id:
                continue
        except Exception:
            pass
        old.unlink()
        print("removed", old.name)


def main() -> None:
    existing = {str(r.get("name") or ""): r for r in load_sector_rules()}
    for name, pack_mode, brief in PACKS:
        code = build_code(pack_mode, brief)
        compile(code, "<%s>" % name, "exec")
        old = existing.get(name)
        rid = str(old.get("id") or uuid.uuid4()) if old else str(uuid.uuid4())
        enabled = bool(old.get("enabled", True)) if old else True
        _remove_same_name_files(name, keep_id=rid)
        save_single_sector_rule(
            {
                "id": rid,
                "name": name,
                "enabled": enabled,
                "code": code,
            }
        )
        action = "更新" if old else "新增"
        print("%s: %s  id=%s  pack=%s" % (action, name, rid, pack_mode))
        print("  ", brief)
    print("完成。请在选股程序中只勾选今日对应的一条规则后运行。")
    print("先跑: python tools/ma10_regime_switch.py  看建议包。")


if __name__ == "__main__":
    main()
