# -*- coding: utf-8 -*-
"""Clone 综合卖出 -> 综合卖出-强止损 (two-tier hard stops). One-shot helper."""
from __future__ import print_function

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "strategy_generator_app" / "config" / "strategies" / "strategy_e9c83928.json"
OUT_DIR = SRC.parent


def main():
    d = json.loads(SRC.read_text(encoding="utf-8"))
    code = d["strategy_code"]

    old_sl_helper = """        # 跌破昨收止损参数：取消下一行注释并改为 dict(...) 可覆盖 JSON；保持 _loss_stop_ov 为 None 则用 strategy_params
        # _loss_stop_ov = dict(intraday_loss_stop_pct=5.0)
        _loss_stop_ov = dict(intraday_loss_stop_pct=9.9)
        #_loss_stop_ov = None

        def _loss_stop(key, default):
            if isinstance(_loss_stop_ov, dict) and key in _loss_stop_ov:
                return _loss_stop_ov[key]
            v = params.get(key, default)
            return default if v is None else v
"""

    new_sl_helper = """        # 两档强止损参数（主板10%档基准；ST×0.5、20%×2）。_sl_ov 可本地覆盖；None 则用 strategy_params
        # _sl_ov = dict(sl_ratio_low=0.25, sl_down_low=5.0, sl_down_high=7.5, sl_blend_high=3.0)
        _sl_ov = dict(sl_ratio_low=0.25, sl_down_low=5.0, sl_down_high=7.5, sl_blend_high=3.0)

        def _sl(key, default):
            if isinstance(_sl_ov, dict) and key in _sl_ov:
                return _sl_ov[key]
            v = params.get(key, default)
            return default if v is None else v
"""

    if old_sl_helper not in code:
        raise SystemExit("loss_stop helper block not found")

    code = code.replace(old_sl_helper, new_sl_helper, 1)

    a = code.find("        # 跌破昨收止损（突破卖出）：")
    b = code.find("        take_profit_levels = []")
    if a < 0 or b < 0 or b <= a:
        raise SystemExit("breakthrough gen block bounds not found a=%s b=%s" % (a, b))

    new_gen = '''        # 两档强止损（突破卖出）：低档昨收-5%×scale 卖25%；高档昨收-7.5%×scale 卖75%；
        # 高档近跌停收紧：room_pp=(最新价-跌停)/昨收×100；≥sl_blend_high×scale 用满档跌幅；
        # ≤1.0pp 收到约 9%×scale（且不破跌停+0.01）；中间线性过渡。
        sl_r_low = float(_sl("sl_ratio_low", 0.25))
        sl_r_low = max(0.0, min(1.0, sl_r_low))
        sl_r_high = 1.0 - sl_r_low
        sl_down_low = float(_sl("sl_down_low", 5.0))
        sl_down_high = float(_sl("sl_down_high", 7.5))
        if sl_down_high < sl_down_low:
            sl_down_high = sl_down_low
        sl_blend_high = float(_sl("sl_blend_high", 3.0))
        vol_sl_low_alloc = 0
        stop_levels = []
        if sl_r_low > 0 and sl_down_low > 0:
            stop_levels.append((sl_down_low * float(scale), sl_r_low, False, "突破卖出-止损低档"))
        if sl_r_high > 0 and sl_down_high > 0:
            stop_levels.append((sl_down_high * float(scale), sl_r_high, True, "突破卖出-止损高档"))
        for sidx, (down_eff, sratio, do_blend, sname) in enumerate(stop_levels):
            if pre_close <= 0 or down_eff <= 0:
                continue
            use_down = float(down_eff)
            if do_blend and limit_down and pre_close > 0 and latest_price > 0:
                room_pp = (float(latest_price) - float(limit_down)) / float(pre_close) * 100.0
                blend_pp = float(sl_blend_high) * float(scale)
                tight_pp = 1.0
                target_down = 9.0 * float(scale)
                if room_pp <= tight_pp:
                    use_down = max(float(down_eff), target_down)
                elif room_pp < blend_pp:
                    span = max(1e-6, blend_pp - tight_pp)
                    t = (blend_pp - room_pp) / span
                    t = max(0.0, min(1.0, t))
                    use_down = float(down_eff) + t * (target_down - float(down_eff))
            thr_px = pre_close * (1.0 - use_down / 100.0)
            break_px = round(thr_px + 0.01, 2)
            if limit_up and break_px > float(limit_up):
                break_px = round(float(limit_up), 2)
            if limit_down and break_px < float(limit_down):
                break_px = round(float(limit_down) + 0.01, 2)
            n_sl = len(stop_levels)
            if n_sl >= 2 and sidx == n_sl - 1:
                v_stop = avail - vol_sl_low_alloc
            else:
                # sratio 为 0~1 比例；与「卖25%」语义一致（整百股）
                v_stop = max(100, (int(avail * float(sratio)) // 100) * 100)
                if n_sl >= 2 and sidx == 0:
                    vol_sl_low_alloc = v_stop
            v_stop = max(0, (int(v_stop) // 100) * 100)
            if v_stop < 100:
                print(f"[持仓止盈] {code_6}   {sname} 跳过: 量={v_stop}<100")
                continue
            print(
                f"[持仓止盈] {code_6} 挂{sname}: {limit_type} "
                f"名义跌{down_eff:g}% 有效跌{use_down:g}% break_px={break_px} 量={v_stop}"
            )
            result.append({
                "stock_code": code_6,
                "stock_name": name,
                "rule_type": "breakthrough_sell",
                "name": f"{sname}(有效{use_down:g}%)",
                "price": break_px,
                "volume": v_stop,
                "debug_pre_close": pre_close,
                "debug_latest": latest_price,
                "debug_base_high": base_high,
                "debug_limit_up": limit_up,
                "debug_limit_down": limit_down,
                "debug_mult": f"sl_down{use_down}_blend{int(do_blend)}",
            })

'''

    code = code[:a] + new_gen + code[b:]

    # Header comment tweak for description
    code = code.replace(
        "#   - 跌破昨收止损：breakthrough_sell，10%档=昨收-5%、20%档=-10%、5%档=-2.5%\n"
        "#     方式1：strategy_params 的 intraday_loss_stop_pct（默认5；≤0 关闭）\n"
        "#     方式2：run() 内 _loss_stop_ov = dict(intraday_loss_stop_pct=...) 本地覆盖；None 则用方式1\n",
        "#   - 两档强止损：breakthrough_sell×2；主板昨收-5%卖25%、-7.5%卖75%（scale 同止盈）\n"
        "#     strategy_params: sl_ratio_low / sl_down_low / sl_down_high / sl_blend_high\n"
        "#     方式2：run() 内 _sl_ov = dict(...) 本地覆盖\n",
        1,
    )

    new_id = "strategy_" + uuid.uuid4().hex[:8]
    out = {
        "id": new_id,
        "name": "综合卖出-强止损",
        "enabled": True,
        "stock_codes": [],
        "strategy_params": dict(d.get("strategy_params") or {}),
        "strategy_code": code,
        "scheduled_generate_at": None,
    }
    # Replace single loss param with sl_* ; keep others
    sp = out["strategy_params"]
    sp.pop("intraday_loss_stop_pct", None)
    sp["sl_ratio_low"] = 0.25
    sp["sl_down_low"] = 5.0
    sp["sl_down_high"] = 7.5
    sp["sl_blend_high"] = 3.0

    out_path = OUT_DIR / (new_id + ".json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # sanity
    assert "intraday_loss_stop_pct=9.9" not in code
    assert "_sl_ov" in code
    assert code.count('rule_type": "breakthrough_sell"') >= 1
    assert "止损低档" in code and "止损高档" in code
    # original untouched
    orig = json.loads(SRC.read_text(encoding="utf-8"))
    assert orig["strategy_code"].count("intraday_loss_stop_pct=9.9") >= 1

    print("OK wrote", out_path)
    print("id", new_id)
    print("name", out["name"])


if __name__ == "__main__":
    main()
