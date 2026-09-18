# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(r"d:\蚂蚁量化系统\ma_zong_meet_monitor_gui.py")
text = p.read_text(encoding="utf-8")

old1 = """        lines: List[str] = [f\"【开买日 {asof_s} · 对比】\"]
        try:
            asof_d = date.fromisoformat(asof_s)
            prev = zb.prev_trading_day(asof_d)
            meet_map = _sel_count_map(self._reports, zb.VARIANT_MEET)
            not_map = _sel_count_map(self._reports, zb.VARIANT_NOT_MEET)
            n_meet = meet_map.get(prev) if prev is not None else None
            n_not = not_map.get(prev) if prev is not None else None
            lines.append(
                f\"前一日选股 {prev or '—'}: 满足 {n_meet if n_meet is not None else '—'} 只 / \"
                f\"不满足 {n_not if n_not is not None else '—'} 只\"
            )
        except Exception:
            pass
        win = None
        for v in zb.VARIANTS:"""

new1 = """        v_pos, v_neg = (
            (self._variants[0], self._variants[1])
            if len(self._variants) >= 2
            else _variant_pair(self._reports, self._mode)
        )
        lines: List[str] = [f\"【开买日 {asof_s} · 对比】\"]
        try:
            asof_d = date.fromisoformat(asof_s)
            prev = zb.prev_trading_day(asof_d)
            meet_map = _sel_count_map(self._reports, v_pos)
            not_map = _sel_count_map(self._reports, v_neg)
            n_meet = meet_map.get(prev) if prev is not None else None
            n_not = not_map.get(prev) if prev is not None else None
            lines.append(
                f\"前一日选股 {prev or '—'}: {_short_variant(v_pos)} \"
                f\"{n_meet if n_meet is not None else '—'} 只 / \"
                f\"{_short_variant(v_neg)} {n_not if n_not is not None else '—'} 只\"
            )
        except Exception:
            pass
        win = None
        for v in (v_pos, v_neg):"""

assert old1 in text, "block1 missing"
text = text.replace(old1, new1, 1)
print("block1 ok")

old2 = """        for v in zb.VARIANTS:
            rep = self._reports.get(v) or {}
            lines.append("")
            lines.append(f\"—— {v} 票明细 ——\")"""
new2 = """        for v in (v_pos, v_neg):
            rep = self._reports.get(v) or {}
            lines.append("")
            lines.append(f\"—— {v} 票明细 ——\")"""
assert old2 in text, "block2 missing"
text = text.replace(old2, new2, 1)
print("block2 ok")

old3 = """        lines.append("")
        for v in zb.VARIANTS:
            rep = self._reports.get(v) or {}
            full = rep.get("full") or {}
            lines.append(
                f\"{v} 全样本 asof={rep.get('last_close')}: \"
                f\"票均{_fmt_pct(full.get('mean'))} 胜率{full.get('win')}% \"
                f\"笔数{full.get('n_known')} 未完成{full.get('n_open')} | \"
                f\"{rep.get('trade_dir') or ''}\"
            )
        lines.append(
            \"口径：横轴=开买日；蓝=满足、橙=不满足；累计=开买日票均简单加总；\"
            \"分组=监控端重算 \"
            + getattr(
                zb,
                \"MEET_DISPLAY_DESC\",
                \"前10日无大涨∧均线∧布林∧(行业名次∈[9,32]∨概念名次∈[9,32])\",
            )
            + \"（不改选股/回测文件）。\"
        )
        self.detail.setPlainText(\"\\n\".join(lines))"""

new3 = """        lines.append("")
        for v in (v_pos, v_neg):
            rep = self._reports.get(v) or {}
            full = rep.get("full") or {}
            lines.append(
                f\"{v} 全样本 asof={rep.get('last_close')}: \"
                f\"票均{_fmt_pct(full.get('mean'))} 胜率{full.get('win')}% \"
                f\"笔数{full.get('n_known')} 未完成{full.get('n_open')} | \"
                f\"{rep.get('trade_dir') or ''}\"
            )
        desc = (
            zb.RANK_MV_DISPLAY_DESC
            if self._mode == zb.MODE_RANK_MV
            else zb.MEET_DISPLAY_DESC
        )
        lines.append(
            \"口径：横轴=开买日；蓝=过滤命中、橙=对照侧；累计=开买日票均简单加总；\"
            \"数据合并 2024/+最终/；展示过滤=\"
            + desc
            + \"（不改选股/回测文件）。\"
        )
        self.detail.setPlainText(\"\\n\".join(lines))"""

assert old3 in text, "block3 missing"
text = text.replace(old3, new3, 1)
print("block3 ok")

# docstring update
text = text.replace(
    "- 三个图叠画两组：满足条件 / 不满足条件（同一回测池按展示口径拆分）\n"
    "- 「满足条件」= 无大涨 ∧ 均线 ∧ 布林 ∧ (行业∈[5,38] ∨ 概念∈[5,38])\n"
    "  （与次日MA10规则写入列一致；旧表仍可按分项重算，便于再缩区间）",
    "- 三个图叠画两组；顶部「对比口径」可切换：\n"
    "  · 排名5-20·市值<50 vs 其余（默认）\n"
    "  · 满足条件 vs 不满足\n"
    "- 按票数据合并 history_data/马总选股逻辑 下 2024/、最终/ 等子目录 latest",
)

p.write_text(text, encoding="utf-8")
print("remaining zb.VARIANTS:", text.count("zb.VARIANTS"))
