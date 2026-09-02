# -*- coding: utf-8 -*-
"""安装策略：买：马总盘中-满足条件单点

点击策略生成时：
  1) 东财 push2 按净流入>=3500万早停 -> 板块/摸板/涨幅 cheap 过滤
  2) 仅对幸存者拉日线，复用 logic2 五项软门槛（满足条件=True）
  3) 各 10 万，最近 tick 卖一 + 1 跳滑点，open_buy_ask 单点买入

用法:
  python tools/install_ma_zong_logic2_buy_strategy.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"

STRATEGY_NAME = "买：马总盘中-满足条件单点"
PREFERRED_ID = "strategy_mz_logic2_buy"

STRATEGY_CODE = r'''# 买：马总盘中 — 满足条件即单点买入（卖一+滑点，金额见 params）
# 生成时全市场扫描（不依赖预选股票池）；仅 满足条件=True 才下单。
# params：buy_amount_per_stock（默认100000）, min_order_amount, min_inflow_wan(=3500)

NAME_BUY = "马总盘中-满足条件单点"


def run(codes, prices, get_name, account, params):
    result = []
    params = params or {}
    account = account or {}
    try:
        amount = float(
            params.get("buy_amount_per_stock")
            or params.get("buy_amount_per_leg")
            or 100000
        )
    except (TypeError, ValueError):
        amount = 100000.0
    try:
        min_order = float(params.get("min_order_amount") or 5000)
    except (TypeError, ValueError):
        min_order = 5000.0
    try:
        min_inflow = float(params.get("min_inflow_wan") or 3500)
    except (TypeError, ValueError):
        min_inflow = 3500.0

    from datetime import date as _date

    def _code6(c):
        s = str(c or "").strip()
        if "." in s:
            s = s.split(".", 1)[0]
        if s.isdigit():
            return s.zfill(6)
        return s

    def _vol_for(px):
        if px is None or px <= 0:
            return 0
        v = max(100, int(float(amount) / float(px) / 100) * 100)
        if v * float(px) < min_order:
            return 0
        return int(v)

    try:
        from utils.ma_zong_logic2_scan import (
            ask1_plus_slippage,
            resolve_ask1_price,
            scan_logic2_meet_candidates,
        )
    except Exception as e:
        print("[马总盘中买] 扫描模块导入失败:", e)
        return result

    trade_d = _date.today()
    try:
        from datetime import datetime as _dt
        raw = params.get("backtest_trade_date")
        if raw:
            if isinstance(raw, _dt):
                trade_d = raw.date()
            elif hasattr(raw, "isoformat"):
                trade_d = raw
            else:
                trade_d = _date.fromisoformat(str(raw).strip()[:10])
    except Exception:
        pass

    force_live = trade_d == _date.today()
    print(
        "[马总盘中买] 开始扫描 as_of=%s live=%s inflow>=%.0f万 amount=%.0f"
        % (trade_d.isoformat(), force_live, min_inflow, amount)
    )
    scan = scan_logic2_meet_candidates(
        trade_d,
        force_live=force_live,
        min_inflow_wan=min_inflow,
        skip_em_hot=True,
    )
    err = str(scan.get("error") or "").strip()
    if err and not scan.get("candidates"):
        print("[马总盘中买] 扫描失败:", err)
        return result
    stats = scan.get("stats") or {}
    print(
        "[马总盘中买] 统计 inflow池=%s 硬涨幅通过=%s 硬门槛入选=%s meet=%s near_miss=%s"
        % (
            stats.get("quotes_inflow_pool"),
            stats.get("cheap_pass"),
            stats.get("hard_pass"),
            stats.get("meet"),
            stats.get("near_miss"),
        )
    )

    def _print_scan_row(prefix, row):
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        cond_txt = str(row.get("conditions_text") or "").strip()
        qual_txt = str(row.get("qualifying_board_text") or "").strip()
        board_txt = str(row.get("board_text") or "").strip()
        if not cond_txt or not qual_txt or not board_txt:
            try:
                from utils.ma_zong_logic2_scan import (
                    format_board_rank_line,
                    format_qualifying_board_line,
                    format_soft_conditions,
                )

                if not cond_txt:
                    cond_txt = format_soft_conditions(extra)
                if not qual_txt:
                    qual_txt = format_qualifying_board_line(extra)
                if not board_txt:
                    board_txt = format_board_rank_line(extra)
            except Exception:
                pass
        meet_tag = "满足" if row.get("meet") else "未全满足"
        print(
            "%s %s %s [%s] 涨幅=%s%% 净流入=%s万"
            % (
                prefix,
                row.get("code"),
                row.get("name") or "",
                meet_tag,
                row.get("pct"),
                row.get("inflow_wan"),
            )
        )
        if qual_txt:
            print("    满足板块: %s" % qual_txt)
        elif board_txt:
            print("    满足板块: （无，所属最高 %s）" % board_txt)
        else:
            print("    满足板块: （无）")
        if board_txt and qual_txt:
            print("    所属最高: %s" % board_txt)
        if cond_txt:
            print("    软门槛: %s" % cond_txt)
        reason = str(row.get("reason") or extra.get("不满足的原因") or "").strip()
        if reason:
            print("    不满足: %s" % reason)

    _hard_all = sorted(
        list(scan.get("hard_pass_all") or []),
        key=lambda r: str(r.get("code") or ""),
    )
    if _hard_all:
        _meet_n = sum(1 for r in _hard_all if r.get("meet"))
        try:
            from utils.ma_zong_logic2_scan import summarize_qualifying_boards

            _qb = summarize_qualifying_boards(_hard_all)
        except Exception:
            _qb = []
        print(
            "[马总盘中买] 硬门槛通过票中，满足排名门槛的板块（行业前32/概念前8）："
        )
        if _qb:
            _ind = [x for x in _qb if x[0] == "行业"]
            _con = [x for x in _qb if x[0] == "概念"]
            if _ind:
                print("  行业（%d）：" % len(_ind))
                for kind, name, rk, cnt in _ind:
                    print("    #%d %s（硬过票 %d 只）" % (rk, name, cnt))
            if _con:
                print("  概念（%d）：" % len(_con))
                for kind, name, rk, cnt in _con:
                    print("    #%d %s（硬过票 %d 只）" % (rk, name, cnt))
        else:
            print("  （无）")
        print(
            "[马总盘中买] 硬门槛通过（共 %d 只，其中满足条件 %d 只）："
            % (len(_hard_all), _meet_n)
        )
        for row in _hard_all:
            _print_scan_row("  ✓" if row.get("meet") else "  ·", row)

    try:
        cash = float(account.get("cash") or 0)
    except (TypeError, ValueError):
        cash = 0.0
    if cash > 1e-6 and amount > cash and scan.get("candidates"):
        print(
            "[马总盘中买] 警告: 可用现金 %.0f < 单笔 %.0f，仍按意图生成（实盘可能截断）"
            % (cash, amount)
        )

    prices = prices or {}
    for row in scan.get("candidates") or []:
        c6 = _code6(row.get("code"))
        if not c6:
            continue
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        name = (
            str(row.get("name") or "")
            or (get_name(c6) if get_name else "")
            or str(extra.get("股票名称_行情") or "")
        )

        ask1, ask_src = resolve_ask1_price(
            c6, prices=prices, quote=quote, trade_date=trade_d
        )
        if ask1 <= 0:
            print("[马总盘中买] %s 跳过: 无卖一/现价" % c6)
            continue
        est_fill = ask1_plus_slippage(c6, ask1)
        if est_fill <= 0:
            print("[马总盘中买] %s 跳过: 预估成交价无效" % c6)
            continue

        try:
            pct = float(quote.get("pct"))
            thr = float(extra.get("涨幅门槛") or 0)
            if thr > 0 and pct + 1e-9 < thr:
                print(
                    "[马总盘中买] %s 跳过: 涨幅回落 %.2f%% < %.2f%%"
                    % (c6, pct, thr)
                )
                continue
        except (TypeError, ValueError):
            pass

        p = prices.get(c6) or {}
        limit_up = 0.0
        try:
            limit_up = float(p.get("涨停板") or p.get("limit_up") or 0)
        except (TypeError, ValueError):
            limit_up = 0.0
        if limit_up <= 0:
            try:
                lu = float(extra.get("涨停价") or 0)
                if lu > 0:
                    limit_up = lu
            except (TypeError, ValueError):
                pass

        # 触发价用上沿（涨停价）：生成→加载有空窗，避免卖一+滑点作触发价导致涨一点就不触发。
        # 实际委托价：open_buy_ask + tick 侧按触发时刻卖一+1跳。
        if limit_up > 0:
            trig = float(limit_up)
        else:
            try:
                trig = round(float(est_fill) * 1.05, 4)
            except (TypeError, ValueError):
                trig = est_fill

        v = _vol_for(est_fill)
        if v <= 0:
            print("[马总盘中买] %s 跳过: 金额不足1手 (est=%.2f)" % (c6, est_fill))
            continue

        print(
            "[马总盘中买] %s %s ask1=%.2f(%s) 触发上沿=%.2f 预估卖一成交=%.2f vol=%d inflow=%s万"
            % (
                c6,
                name,
                ask1,
                ask_src,
                trig,
                est_fill,
                v,
                extra.get("主力净流入_万元") or quote.get("inflow_wan"),
            )
        )
        intent = {
            "stock_code": c6,
            "stock_name": name,
            "rule_type": "single_buy",
            "name": NAME_BUY,
            "price": round(float(trig), 4),
            "volume": int(v),
            "open_buy_ask": True,
            "early_order_enabled": False,
        }
        if limit_up > 0:
            intent["limit_up"] = round(float(limit_up), 4)
        result.append(intent)

    print("[马总盘中买] 生成 %d 条买入意图" % len(result))
    return result
'''


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rid = "strategy_" + uuid.uuid4().hex[:8]
    existing_id = None
    prev: dict = {}
    preferred = OUT_DIR / ("%s.json" % PREFERRED_ID)
    if preferred.is_file():
        try:
            raw = json.loads(preferred.read_text(encoding="utf-8"))
            existing_id = str(raw.get("id") or PREFERRED_ID)
            prev = raw if isinstance(raw, dict) else {}
        except Exception:
            pass
    if not existing_id:
        for p in OUT_DIR.glob("strategy_*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(raw.get("name") or "") == STRATEGY_NAME:
                existing_id = str(raw.get("id") or "")
                prev = raw if isinstance(raw, dict) else {}
                break
    sid = existing_id or PREFERRED_ID or rid
    prev_params = prev.get("strategy_params") if isinstance(prev.get("strategy_params"), dict) else {}
    sp = {
        "buy_amount_per_stock": float(prev_params.get("buy_amount_per_stock", 100000) or 100000),
        "min_order_amount": float(prev_params.get("min_order_amount", 5000) or 5000),
        "min_inflow_wan": float(prev_params.get("min_inflow_wan", 3500) or 3500),
        "sizing_mode": str(prev_params.get("sizing_mode") or "fixed"),
    }
    for k, v in prev_params.items():
        if k not in sp:
            sp[k] = v
    out = {
        "id": sid,
        "name": STRATEGY_NAME,
        "enabled": bool(prev.get("enabled", True)),
        "stock_codes": list(prev.get("stock_codes") or []),
        "strategy_params": sp,
        "strategy_code": STRATEGY_CODE,
        "scheduled_generate_at": prev.get("scheduled_generate_at") or "09:25:00",
    }
    path_by_id = OUT_DIR / ("%s.json" % sid)
    path_by_id.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for p in OUT_DIR.glob("strategy_*.json"):
        if p.resolve() == path_by_id.resolve():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(raw.get("name") or "") == STRATEGY_NAME:
            p.unlink()
            print("removed duplicate", p.name)
    print("wrote", path_by_id)
    print("name", STRATEGY_NAME)


if __name__ == "__main__":
    main()
