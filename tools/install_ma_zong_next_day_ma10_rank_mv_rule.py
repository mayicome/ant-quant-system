# -*- coding: utf-8 -*-
"""安装选股规则：马总选股逻辑-次日MA10-排名市值

在「次日MA10」硬过滤之上，再强制：
  最佳板块排名 ∈ [5,20]  ∧  流通市值 < 50 亿（优先选股日市值）

不覆盖「马总选股逻辑-次日MA10」「马总选股逻辑-盘后」。

用法:
  python tools/install_ma_zong_next_day_ma10_rank_mv_rule.py
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_SRC = Path(__file__).resolve().parent / "_rule_src_ma_zong_next_day_ma10.py"
OUT_SRC = Path(__file__).resolve().parent / "_rule_src_ma_zong_next_day_ma10_rank_mv.py"
OUT_DIR = ROOT / "data" / "sector_rules"
NAME = "马总选股逻辑-次日MA10-排名市值"
HOT_MODE = "ma_zong_next_day_ma10_rank_mv"

HEADER = '''# 马总选股逻辑 · 次日MA10 · 排名市值硬过滤
# 在「次日MA10」硬过滤（近涨停、未触MA10、L后未冲高、除权排查）之上，再强制入池：
#   最佳板块排名 ∈ [RANK_MV_LO, RANK_MV_HI]（默认[5,20]）
#   ∧ 流通市值 < RANK_MV_MAX_YI 亿（默认50；优先选股日流通市值，缺则用涨停日市值）
# 「满足条件」仍为旧软诊断（无大涨∧均线∧布林∧名次∈[5,38]），不挡进池。
# 引擎：关闭热门池收窄，全市场扫描
'''

HARD_GATE = '''
    # ---- 硬过滤：最佳板块排名∈[RANK_MV_LO,RANK_MV_HI] ∧ 市值<RANK_MV_MAX_YI ----
    mv_gate = contrast.get("流通市值_亿_选股日")
    if mv_gate in ("", None):
        mv_gate = mv
    rk_i = None
    if best_rk not in (None, ""):
        try:
            rk_i = int(best_rk)
        except (TypeError, ValueError):
            try:
                rk_i = int(float(best_rk))
            except (TypeError, ValueError):
                rk_i = None
    mv_f = None
    if mv_gate not in (None, ""):
        try:
            mv_f = float(mv_gate)
        except (TypeError, ValueError):
            mv_f = None
    rank_mv_ok = (
        rk_i is not None
        and mv_f is not None
        and int(RANK_MV_LO) <= int(rk_i) <= int(RANK_MV_HI)
        and float(mv_f) < float(RANK_MV_MAX_YI)
    )
    if not rank_mv_ok:
        lu_s = ""
        try:
            lu_s = lu_date.strftime("%Y-%m-%d")
        except Exception:
            lu_s = str(lu_date)
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "非最佳排名[%d,%d]或市值>=%.0f亿"
            % (int(RANK_MV_LO), int(RANK_MV_HI), float(RANK_MV_MAX_YI)),
            "涨停锚点日": lu_s,
            "涨停日期": lu_s,
            "最佳板块排名": "" if rk_i is None else int(rk_i),
            "最佳板块名称": best_name,
            "最佳板块类型": best_kind,
            "流通市值_亿": "" if mv is None else round(float(mv), 2),
            "流通市值_亿_选股日": "" if mv_f is None else round(float(mv_f), 2),
            "条件_硬过滤_排名市值": False,
            "RANK_MV_LO": int(RANK_MV_LO),
            "RANK_MV_HI": int(RANK_MV_HI),
            "RANK_MV_MAX_YI": float(RANK_MV_MAX_YI),
        }
'''


def build_source() -> str:
    raw = BASE_SRC.read_text(encoding="utf-8")
    if "def select(" not in raw:
        raise SystemExit("base rule missing select()")

    # 去掉原文件头注释块（到 USE_EM... 之前），换新头
    m = re.search(r"^USE_EM_CANDIDATE_POOL\s*=", raw, flags=re.M)
    if not m:
        raise SystemExit("cannot find USE_EM_CANDIDATE_POOL")
    body = raw[m.start() :]

    body = body.replace(
        'HOT_MODE = "ma_zong_next_day_ma10"',
        'HOT_MODE = "%s"\n'
        "RANK_MV_LO = 5\n"
        "RANK_MV_HI = 20\n"
        "RANK_MV_MAX_YI = 50.0" % HOT_MODE,
        1,
    )
    if HOT_MODE not in body:
        raise SystemExit("HOT_MODE patch failed")

    anchor = "    contrast = _sel_day_contrast_fields(stock_code, sectors, daily_data, as_of_date)\n"
    if anchor not in body:
        raise SystemExit("cannot find contrast assignment")
    if "条件_硬过滤_排名市值" not in body:
        body = body.replace(anchor, anchor + HARD_GATE, 1)

    # 写入结果表附加字段
    if '"条件_硬过滤_排名市值"' not in body.split("extra = {", 1)[-1][:2500]:
        body = body.replace(
            '        "条件_L后最高价未超阈值": not bool(hi_exceeded),\n',
            '        "条件_L后最高价未超阈值": not bool(hi_exceeded),\n'
            '        "条件_硬过滤_排名市值": True,\n'
            '        "RANK_MV_LO": int(RANK_MV_LO),\n'
            '        "RANK_MV_HI": int(RANK_MV_HI),\n'
            '        "RANK_MV_MAX_YI": float(RANK_MV_MAX_YI),\n',
            1,
        )

    return HEADER + body


def main() -> None:
    code = build_source()
    if "def select(" not in code:
        raise SystemExit("generated rule missing select()")
    if HOT_MODE not in code:
        raise SystemExit("unexpected HOT_MODE")
    if "rank_mv_ok" not in code:
        raise SystemExit("hard gate missing")

    OUT_SRC.write_text(code, encoding="utf-8")
    print("wrote source", OUT_SRC)

    rid = str(uuid.uuid4())
    short = rid.split("-")[0]
    out = {
        "id": rid,
        "name": NAME,
        "enabled": True,
        "code": code,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("%s__*.json" % NAME):
        old.unlink()
        print("removed", old.name)
    path = OUT_DIR / ("%s__%s.json" % (NAME, short))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)
    print("id", rid)
    print("enabled True")
    print("未改动 马总选股逻辑-次日MA10 / 盘后")


if __name__ == "__main__":
    raise SystemExit(main())
