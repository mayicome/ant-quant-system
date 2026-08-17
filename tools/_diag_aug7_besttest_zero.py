# -*- coding: utf-8 -*-
"""诊断：东财热门-besttest-无涨停均线差0.5to2-MA空头排列 在 2026-08-07 为何 0 只。"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.daily_cache_reader import load_daily_from_cache  # noqa: E402
from utils.eastmoney_board_rank_ctx import load_em_board_hot_map  # noqa: E402

ASOF = date(2026, 8, 7)
RULE_GLOB = "*de4a81d4*.json"
OUT = ROOT / "history_data" / f"_diag_besttest_zero_{ASOF.isoformat()}.txt"

# 漏斗阶段顺序（与规则一致，便于阅读）
STAGE_ORDER = [
    "无东财热门上下文",
    "东财热门error",
    "不在今日热门池(含RS)",
    "无热门命中明细",
    "合格序位不在类型档内(板块1-30/概念1-40)",
    "缺合格榜标签内RS排名（禁止用全局A/B中的RS）",
    "无合格榜标签内RS排名",
    "缺合格榜标签RS样本数",
    "合格榜标签内RS未进min(50,ceil(样本/2))",
    "流通市值缺失或<120亿",
    "均线不足(无MA5/MA10)",
    "均线差无效",
    "均线差不在[0.5%,2.0%]",
    "均线不足(无MA5/MA10/MA20)",
    "非MA5<MA10<MA20",
    "近10日有涨停",
    "通过",
    "其他",
]


def load_rule():
    paths = list((ROOT / "data" / "sector_rules").glob(RULE_GLOB))
    if not paths:
        raise FileNotFoundError(RULE_GLOB)
    data = json.loads(paths[0].read_text(encoding="utf-8"))
    ns: dict = {}
    exec(compile(data["code"], str(paths[0]), "exec"), ns, ns)
    return data["name"], ns["select"], paths[0]


def main() -> None:
    name, select_fn, rule_path = load_rule()
    lines = []
    def log(s=""):
        print(s)
        lines.append(s)

    log(f"规则: {name}")
    log(f"文件: {rule_path.name}")
    log(f"选股日: {ASOF.isoformat()}")
    log("")

    em = load_em_board_hot_map(
        ASOF,
        top_n=50,
        rs_top_k=50,
        min_members=10,
        arms=["today"],
        elig_bands=[(1, 40)],  # 与规则 ELIG_HI=40 探测一致；规则内再按板块30/概念40收紧
    )
    err = str(em.get("error") or "").strip()
    if err:
        log(f"[致命] 东财热门上下文 error: {err}")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    pool = {str(c).zfill(6) for c in (em.get("today_pool_codes") or set()) if c}
    hits = em.get("today_code_hits") or {}
    log(f"今日热门池(today_pool_codes)只数: {len(pool)}")
    log(f"today_code_hits 只数: {len(hits) if isinstance(hits, dict) else 0}")
    log(f"as_of={em.get('as_of')} prev={em.get('prev_date')}")
    log("")

    # 先按命中明细做「热门侧」粗分（未跑日线前）
    elig_ok = 0
    elig_fail = 0
    rs_ok = 0
    rs_fail = 0
    for c6, hit in (hits.items() if isinstance(hits, dict) else []):
        if not isinstance(hit, dict):
            continue
        try:
            elig = int(hit.get("合格榜内序位") or 0)
        except (TypeError, ValueError):
            elig = 0
        kind = str(hit.get("合格榜标签类型") or "")
        hi = 40 if ("概念" in kind or kind.lower() == "concept") else 30
        if 1 <= elig <= hi:
            elig_ok += 1
        else:
            elig_fail += 1
            continue
        try:
            rs = int(hit.get("合格榜标签内RS排名") or 0)
            n = int(hit.get("合格榜标签RS样本数") or 0)
        except (TypeError, ValueError):
            rs, n = 0, 0
        if n > 0 and rs > 0:
            cut = min(50, max(1, (n + 1) // 2))
            if 1 <= rs <= cut:
                rs_ok += 1
            else:
                rs_fail += 1
        else:
            rs_fail += 1
    log(f"[热门粗筛] Elig进档(板≤30/概≤40): {elig_ok}  未进档: {elig_fail}")
    log(f"[热门粗筛] 在Elig进档内 RS过半档: {rs_ok}  未过: {rs_fail}")
    log("")

    # 候选：与引擎一致优先用 hits 中 Elig1-40，否则用 pool
    candidates = sorted(
        {
            str(c).zfill(6)
            for c, hit in (hits.items() if isinstance(hits, dict) else [])
            if isinstance(hit, dict)
        }
        | pool
    )
    log(f"逐只跑 select 的候选数: {len(candidates)}")

    skip_counter: Counter[str] = Counter()
    pass_n = 0
    examples: dict[str, list] = {}
    daily_cache = {}

    for c6 in candidates:
        if c6 not in daily_cache:
            daily_cache[c6] = load_daily_from_cache(c6)
        dd = daily_cache[c6]
        # 名称尽量从 hit 取
        hit = hits.get(c6) if isinstance(hits, dict) else None
        sname = ""
        if isinstance(hit, dict):
            sname = str(hit.get("名称") or hit.get("股票名称") or "")
        try:
            ok, extra = select_fn(c6, sname, [], dd, ASOF, {"em_board_hot": em})
        except Exception as e:
            ok, extra = False, {"_skip": f"select异常:{e}"}
        if ok:
            pass_n += 1
            skip_counter["通过"] += 1
            examples.setdefault("通过", []).append(c6)
            continue
        reason = str((extra or {}).get("_skip") or "其他")
        # 归一到 STAGE_ORDER
        key = reason
        for s in STAGE_ORDER:
            if s in reason or reason == s:
                key = s
                break
        else:
            if reason not in STAGE_ORDER:
                key = reason
        skip_counter[key] += 1
        if len(examples.setdefault(key, [])) < 8:
            ex = c6
            if isinstance(extra, dict):
                bits = []
                for k in ("合格榜内序位", "合格榜标签类型", "合格榜标签内RS排名", "均线差占比", "MA5", "MA10", "MA20"):
                    if k in extra and extra[k] not in ("", None):
                        bits.append(f"{k}={extra[k]}")
                if bits:
                    ex = f"{c6}({', '.join(bits)})"
            examples[key].append(ex)

    log("======== 卡点漏斗（按淘汰原因计数；同一票只计最后失败原因）========")
    total_fail = sum(v for k, v in skip_counter.items() if k != "通过")
    log(f"候选 {len(candidates)} | 通过 {pass_n} | 淘汰 {total_fail}")
    log("")
    # 按 STAGE_ORDER 再补上实际出现的其他原因
    shown = set()
    for s in STAGE_ORDER:
        if s in skip_counter:
            log(f"  {skip_counter[s]:5d}  {s}")
            shown.add(s)
            if s in examples and s != "通过":
                log(f"         例: {', '.join(examples[s][:5])}")
    for s, n in skip_counter.most_common():
        if s in shown:
            continue
        log(f"  {n:5d}  {s}")
        if s in examples:
            log(f"         例: {', '.join(examples[s][:5])}")

    log("")
    log("======== 解读提示 ========")
    # 找出最大卡点（非通过）
    fails = [(k, v) for k, v in skip_counter.items() if k != "通过"]
    fails.sort(key=lambda x: -x[1])
    if pass_n == 0 and fails:
        top = fails[0]
        log(f"主卡点: 「{top[0]}」淘汰 {top[1]} 只（占淘汰 {top[1]/max(total_fail,1)*100:.0f}%）")
        if len(fails) > 1:
            log(f"次卡点: 「{fails[1][0]}」淘汰 {fails[1][1]} 只")
        if len(fails) > 2:
            log(f"再次: 「{fails[2][0]}」淘汰 {fails[2][1]} 只")
    elif pass_n > 0:
        log(f"注意：本诊断复跑得到通过 {pass_n} 只；若 GUI 为 0，请核对是否同一规则/同一选股日/热门快照。")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    log("")
    log(f"已写入: {OUT}")


if __name__ == "__main__":
    main()
