# -*- coding: utf-8 -*-
"""主力净流入 CSV：按「净流入/流通市值」重排。

全量落盘后默认不再按 3000 万过滤；仍可按需传入 min_inflow_wan。
若 CSV 已带「流通市值」列（接口抓取），优先解析该列，避免逐票 xtdata。
"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

import pandas as pd

# 兼容旧调用；全量模式请传 0
MIN_INFLOW_WAN = 0.0


def _zfill_code(raw: object) -> str:
    s = re.sub(r"\D", "", str(raw or ""))
    if not s:
        return ""
    return (s.zfill(6) if len(s) < 6 else s)[-6:]


def _to_full_stock_code(code6: str) -> str:
    c = _zfill_code(code6)
    if not c:
        return ""
    if "." in str(code6):
        return str(code6).strip()
    if c.startswith(("0", "1", "3")):
        return f"{c}.SZ"
    if c.startswith("6"):
        return f"{c}.SH"
    if c.startswith(("8", "4")) or c.startswith("920"):
        return f"{c}.BJ"
    return c


def parse_inflow_to_yuan(text: object) -> Optional[float]:
    """将「21.52亿」「2432.77万」等转为元；失败返回 None。"""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    if isinstance(text, (int, float)):
        # 无单位时按「元」不靠谱；数值列少见，保守当作已是元仅当绝对值极大
        v = float(text)
        return v if abs(v) > 0 else None
    s = str(text).strip().replace(",", "").replace(" ", "").replace("\xa0", "")
    if not s or s in ("--", "-", "nan", "None"):
        return None
    neg = 1.0
    if s.startswith("-"):
        neg = -1.0
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    m = re.match(r"^(\d+\.?\d*)\s*亿", s)
    if m:
        return neg * float(m.group(1)) * 1e8
    m = re.match(r"^(\d+\.?\d*)\s*万", s)
    if m:
        return neg * float(m.group(1)) * 1e4
    m = re.match(r"^(\d+\.?\d*)$", s)
    if m:
        # 无单位：东方财富偶发纯数字，按万元理解（与抓取页一致）
        return neg * float(m.group(1)) * 1e4
    return None


def yuan_to_display(yuan: float) -> str:
    a = abs(yuan)
    sign = "-" if yuan < 0 else ""
    if a >= 1e8:
        return f"{sign}{a / 1e8:.2f}亿"
    if a >= 1e4:
        return f"{sign}{a / 1e4:.2f}万"
    if a > 0:
        return f"{sign}{a:.2f}元"
    return "0"


def _find_col(df: pd.DataFrame, *needles: str) -> Optional[str]:
    for c in df.columns:
        s = str(c).replace("\n", "").replace("\xa0", "").replace(" ", "")
        if all(n in s for n in needles):
            return c
    return None


def _parse_price(val: object) -> Optional[float]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        v = float(str(val).strip().replace("%", "").replace(",", ""))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


_xtdata = None
_xtdata_tried = False


def _get_xtdata():
    global _xtdata, _xtdata_tried
    if _xtdata_tried:
        return _xtdata
    _xtdata_tried = True
    try:
        from xtquant import xtdata as xt

        _xtdata = xt
    except Exception:
        _xtdata = None
    return _xtdata


def float_market_cap_yuan(code6: str, last_price: Optional[float] = None) -> Optional[float]:
    """流通市值（元）= FloatVolume × 最新价（缺则 PreClose）。"""
    xt = _get_xtdata()
    if xt is None:
        return None
    full = _to_full_stock_code(code6)
    if not full:
        return None
    try:
        info = xt.get_instrument_detail(full)
    except Exception:
        info = None
    if not isinstance(info, dict):
        return None
    try:
        float_vol = float(info.get("FloatVolume") or 0)
    except (TypeError, ValueError):
        return None
    if float_vol <= 0:
        return None
    px = last_price
    if px is None or px <= 0:
        try:
            px = float(info.get("PreClose") or 0)
        except (TypeError, ValueError):
            px = 0.0
    if px is None or px <= 0:
        return None
    cap = float_vol * px
    return cap if cap > 0 else None


def enrich_and_rank_by_inflow_ratio(
    df: pd.DataFrame,
    *,
    min_inflow_wan: float = MIN_INFLOW_WAN,
) -> Tuple[pd.DataFrame, dict]:
    """
    可选过滤净流入≥门槛，补流通市值与占比，按「净流入/流通市值」降序。

    返回 (新 DataFrame, 统计 dict)。
    """
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame(), {"in": 0, "out": 0, "no_cap": 0}

    work = df.copy()
    work.columns = [str(c).replace("\xa0", " ") for c in work.columns]

    code_col = _find_col(work, "代码") or ("代码" if "代码" in work.columns else None)
    inflow_col = _find_col(work, "今日主力净流入")
    if inflow_col is None:
        inflow_col = _find_col(work, "主力净流入")
    price_col = _find_col(work, "最新价")
    cap_col = _find_col(work, "流通市值")
    pct_col = _find_col(work, "净流入占流通")

    if not code_col or not inflow_col:
        return work, {"in": len(work), "out": len(work), "no_cap": 0, "error": "缺少代码或净流入列"}

    # 若已有流通市值/占比，先读出再删旧列，避免重复
    pre_cap = {}
    pre_pct = {}
    if cap_col or pct_col:
        for _, row in work.iterrows():
            code = _zfill_code(row.get(code_col))
            if not code:
                continue
            if cap_col:
                cap_v = parse_inflow_to_yuan(row.get(cap_col))
                # 流通市值列也可能是纯数字「元」
                if cap_v is None:
                    try:
                        raw = row.get(cap_col)
                        if raw is not None and str(raw).strip() not in ("", "--"):
                            cap_v = float(str(raw).strip().replace(",", ""))
                    except (TypeError, ValueError):
                        cap_v = None
                if cap_v is not None and cap_v > 0:
                    # parse_inflow 把无单位数字当「万元」；接口落盘是「xx亿/万」展示串，通常可解析
                    # 若解析值小得离谱而原文是大数字元，上面 float 分支已覆盖
                    pre_cap[code] = cap_v
            if pct_col:
                try:
                    v = row.get(pct_col)
                    if v is not None and str(v).strip() not in ("", "--", "nan", "None"):
                        pre_pct[code] = float(str(v).strip().replace("%", ""))
                except (TypeError, ValueError):
                    pass

    for drop_c in ("流通市值", "净流入占流通%", "_ratio", "_inflow_yuan", "_cap_yuan"):
        if drop_c in work.columns:
            work = work.drop(columns=[drop_c])

    min_yuan = float(min_inflow_wan) * 1e4
    rows = []
    no_cap = 0
    dropped = 0
    for _, row in work.iterrows():
        code = _zfill_code(row.get(code_col))
        yuan = parse_inflow_to_yuan(row.get(inflow_col))
        if yuan is None:
            dropped += 1
            continue
        if min_yuan > 0 and yuan < min_yuan:
            dropped += 1
            continue
        px = _parse_price(row.get(price_col)) if price_col else None
        cap = pre_cap.get(code)
        if cap is None or cap <= 0:
            cap = float_market_cap_yuan(code, px)
        if cap is None or cap <= 0:
            no_cap += 1
            ratio = float("-inf")
            cap_disp = ""
            pct_disp = ""
        else:
            ratio = yuan / cap
            cap_disp = yuan_to_display(cap)
            if code in pre_pct:
                pct_disp = round(float(pre_pct[code]), 4)
            else:
                pct_disp = round(ratio * 100, 4)
        d = row.to_dict()
        d["流通市值"] = cap_disp
        d["净流入占流通%"] = pct_disp
        d["_ratio"] = ratio
        rows.append(d)

    if not rows:
        out = work.iloc[0:0].copy()
        return out, {"in": len(work), "out": 0, "no_cap": no_cap, "dropped": dropped}

    out = pd.DataFrame(rows)
    out = out.sort_values("_ratio", ascending=False, kind="mergesort").reset_index(drop=True)
    out = out.drop(columns=["_ratio"], errors="ignore")

    seq_col = _find_col(out, "序号")
    if seq_col:
        out[seq_col] = range(1, len(out) + 1)
    else:
        out.insert(0, "序号", range(1, len(out) + 1))

    cols = list(out.columns)
    for c in ("流通市值", "净流入占流通%"):
        if c in cols:
            cols.remove(c)
    if inflow_col in cols:
        i = cols.index(inflow_col) + 1
        cols[i:i] = ["流通市值", "净流入占流通%"]
    else:
        cols.extend(["流通市值", "净流入占流通%"])
    out = out[cols]

    return out, {
        "in": len(work),
        "out": len(out),
        "no_cap": no_cap,
        "dropped": dropped,
    }


def reprocess_flow_csv(
    filepath: str,
    *,
    min_inflow_wan: float = MIN_INFLOW_WAN,
    inplace: bool = True,
) -> dict:
    """读取单个 CSV，重排后写回（或返回统计）。"""
    encodings = ["utf-8-sig", "utf-8", "gbk", "gb2312", "gb18030"]
    df = None
    last_err = None
    for enc in encodings:
        try:
            df = pd.read_csv(filepath, encoding=enc)
            break
        except Exception as e:
            last_err = e
            continue
    if df is None:
        return {"file": filepath, "ok": False, "error": str(last_err)}

    ranked, stats = enrich_and_rank_by_inflow_ratio(df, min_inflow_wan=min_inflow_wan)
    if inplace:
        tmp_path = filepath + ".tmp"
        try:
            ranked.to_csv(tmp_path, index=False, encoding="utf_8_sig")
            os.replace(tmp_path, filepath)
        except Exception as e:
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            return {
                "file": filepath,
                "ok": False,
                "error": str(e),
                **stats,
            }
    return {"file": filepath, "ok": True, **stats}


def reprocess_flow_dir(
    history_dir: str = "history_data",
    *,
    min_inflow_wan: float = MIN_INFLOW_WAN,
) -> list:
    """重跑 history_data/个股主力净流入/ 下全部 CSV。"""
    from utils.main_force_inflow_path import flow_data_dir

    d = flow_data_dir(history_dir)
    if not os.path.isdir(d):
        return []
    results = []
    for fn in sorted(os.listdir(d)):
        if not fn.startswith("个股主力净流入_") or not fn.endswith(".csv"):
            continue
        path = os.path.join(d, fn)
        results.append(reprocess_flow_csv(path, min_inflow_wan=min_inflow_wan))
    return results


if __name__ == "__main__":
    import sys

    hist = sys.argv[1] if len(sys.argv) > 1 else "history_data"
    rows = reprocess_flow_dir(hist)
    ok = sum(1 for r in rows if r.get("ok"))
    print(f"处理 {len(rows)} 个文件，成功 {ok}")
    for r in rows[-5:]:
        print(r)
