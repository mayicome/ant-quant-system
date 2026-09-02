# -*- coding: utf-8 -*-
"""偷懒补丁：在已有马总次日MA10选股结果上补算涨停日布林上轨，并重算「满足条件」。

不重跑选股引擎。软门槛五项 = 原四项条件列 AND (收盘 > 布林上轨)。
默认同时回写 history_data/马总选股逻辑/ 下 *_收盘上MA10*_latest.xlsx 的「满足条件」。

用法:
  python tools/patch_ma_zong_meet_boll.py
  python tools/patch_ma_zong_meet_boll.py --no-backtest
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "history_data" / "马总选股逻辑"
RULE_NAME = "马总选股逻辑-次日MA10"
BOLL_PERIOD = 20
BOLL_K = 2.0

COND_COLS = (
    "条件_行业或概念排名达标",
    "条件_主力净流入>=3000万",
    "条件_前10日无大涨",
    "条件_收盘站上MA5且MA20",
)


def _parse_d(v) -> Optional[date]:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    s = s.replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        ts = pd.to_datetime(s, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "是", "真"):
        return True
    if s in ("0", "false", "no", "n", "否", "假", "", "nan", "none"):
        return False
    try:
        return bool(int(float(s)))
    except Exception:
        return False


def _code6(v) -> str:
    s = str(v or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else ""


def _boll_upper(closes: List[float], period: int = BOLL_PERIOD, k: float = BOLL_K) -> Optional[float]:
    p = max(2, int(period))
    if len(closes) < p:
        return None
    window = closes[-p:]
    if any(x != x or x <= 0 for x in window):
        return None
    mean = sum(window) / float(p)
    var = sum((x - mean) ** 2 for x in window) / float(p - 1)
    if var != var or var < 0:
        return None
    return mean + float(k) * (var ** 0.5)


def _closes_through(code6: str, through: date) -> Optional[List[float]]:
    from utils.daily_cache_reader import load_daily_from_cache, to_full_stock_code

    full = to_full_stock_code(code6)
    df = load_daily_from_cache(full, through_date=through)
    if df is None or getattr(df, "empty", True):
        return None
    if "date" not in df.columns or "close" not in df.columns:
        return None
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date"])
    d = d[d["date"].dt.date <= through]
    if d.empty:
        return None
    d = d.sort_values("date")
    out: List[float] = []
    for v in d["close"].tolist():
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f != f or f <= 0:
            return None
        out.append(f)
    return out if out else None


def _read_sel(path: Path) -> pd.DataFrame:
    try:
        return pd.read_excel(path, engine="xlrd")
    except Exception:
        return pd.read_excel(path)


def collect_selection_rows() -> Tuple[pd.DataFrame, List[str]]:
    """合并各选股文件；(选股日,代码) 保留较新文件行。"""
    files = sorted(
        OUT_DIR.glob(f"选股结果_{RULE_NAME}_*.xls"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(f"未找到选股结果: {OUT_DIR}/选股结果_{RULE_NAME}_*.xls")
    parts: List[pd.DataFrame] = []
    used: List[str] = []
    for p in files:
        try:
            df = _read_sel(p)
        except Exception as e:
            print(f"跳过 {p.name}: {e}")
            continue
        if df is None or df.empty or "选股日" not in df.columns or "股票代码" not in df.columns:
            continue
        df = df.copy()
        df["_src"] = p.name
        parts.append(df)
        used.append(p.name)
    if not parts:
        raise RuntimeError("选股文件均不可读")
    out = pd.concat(parts, ignore_index=True)
    out["股票代码"] = out["股票代码"].map(_code6)
    out["_sel_d"] = out["选股日"].map(_parse_d)
    out = out[out["股票代码"].astype(str).str.len() == 6]
    out = out[out["_sel_d"].notna()]
    # parts 已按文件新→旧；keep first
    out = out.drop_duplicates(subset=["_sel_d", "股票代码"], keep="first")
    return out, used


def patch_frame(df: pd.DataFrame) -> pd.DataFrame:
    cache: Dict[Tuple[str, date], Tuple[Optional[float], Optional[float]]] = {}
    boll_list: List[Any] = []
    cond_boll_list: List[bool] = []
    meet_list: List[bool] = []
    close_l_list: List[Any] = []
    n_ok = n_miss = 0

    for _, row in df.iterrows():
        code = _code6(row.get("股票代码"))
        lu = _parse_d(row.get("涨停锚点日") or row.get("涨停日期"))
        key = (code, lu) if lu is not None else (code, date.min)
        if lu is None or not code:
            boll_list.append("")
            cond_boll_list.append(False)
            close_l_list.append("")
            n_miss += 1
        elif key in cache:
            close_l, boll = cache[key]
            close_l_list.append("" if close_l is None else round(float(close_l), 4))
            boll_list.append("" if boll is None else round(float(boll), 4))
            cond_boll_list.append(
                bool(close_l is not None and boll is not None and float(close_l) > float(boll))
            )
            if boll is None:
                n_miss += 1
            else:
                n_ok += 1
        else:
            closes = _closes_through(code, lu)
            close_l = closes[-1] if closes else None
            boll = _boll_upper(closes) if closes else None
            cache[key] = (close_l, boll)
            close_l_list.append("" if close_l is None else round(float(close_l), 4))
            boll_list.append("" if boll is None else round(float(boll), 4))
            cond_boll_list.append(
                bool(close_l is not None and boll is not None and float(close_l) > float(boll))
            )
            if boll is None:
                n_miss += 1
            else:
                n_ok += 1

        base = all(_as_bool(row.get(c)) for c in COND_COLS if c in df.columns)
        meet_list.append(bool(base and cond_boll_list[-1]))

    out = df.copy()
    out["条件_收盘站上布林上轨"] = cond_boll_list
    out["布林上轨"] = boll_list
    out["涨停日收盘_布林核对"] = close_l_list
    out["满足条件"] = meet_list
    if "不满足的原因" in out.columns:
        new_reasons = []
        for i in range(len(out)):
            r = str(out.iloc[i].get("不满足的原因") or "").strip()
            if r.lower() in ("nan", "none"):
                r = ""
            if not cond_boll_list[i]:
                extra = (
                    "收盘站上布林上轨不满足，要求收盘>布林上轨(MA20+2*STD20)，"
                    "实际收盘=%s 上轨=%s"
                    % (
                        close_l_list[i] if close_l_list[i] != "" else "无",
                        boll_list[i] if boll_list[i] != "" else "无",
                    )
                )
                if "布林上轨" not in r:
                    r = (r + "；" + extra) if r else extra
            new_reasons.append(r)
        out["不满足的原因"] = new_reasons

    print(
        f"布林计算: 成功 {n_ok} 行 / 缺数据或不足20根 {n_miss} 行；"
        f"满足条件 True={sum(meet_list)} False={len(meet_list) - sum(meet_list)}"
    )
    return out


def write_selection(df: pd.DataFrame) -> Path:
    from sector_stock_filter import save_xls_with_text_code

    out = df.drop(columns=[c for c in ("_src", "_sel_d") if c in df.columns], errors="ignore")
    sel = out["选股日"].map(_parse_d)
    start = min(d for d in sel if d is not None)
    end = max(d for d in sel if d is not None)
    out = out.copy()
    out["选股日"] = sel.map(lambda d: d.isoformat() if d else "")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"选股结果_{RULE_NAME}_{start.isoformat()}_{end.isoformat()}_boll.xls"
    save_xls_with_text_code(str(path), out)
    print(f"已写选股: {path.name}  rows={len(out)}")
    return path


def patch_backtest_files(sel_df: pd.DataFrame) -> int:
    """按 (选股日,代码) 回写按票回测里的满足条件 / 布林列。"""
    key_df = sel_df.copy()
    key_df["股票代码"] = key_df["股票代码"].map(_code6)
    key_df["_sel"] = key_df["选股日"].map(_parse_d).map(lambda d: d.isoformat() if d else "")
    lookup: Dict[Tuple[str, str], Tuple[bool, bool, Any]] = {}
    for _, row in key_df.iterrows():
        k = (str(row["_sel"]), str(row["股票代码"]))
        if not k[0] or len(k[1]) != 6:
            continue
        lookup[k] = (
            _as_bool(row.get("满足条件")),
            _as_bool(row.get("条件_收盘站上布林上轨")),
            row.get("布林上轨", ""),
        )

    pats = (
        "*收盘上MA10_latest.xlsx",
        "*收盘上MA10.xlsx",
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_latest.xlsx",
    )
    seen = set()
    files: List[Path] = []
    for pat in pats:
        for p in OUT_DIR.glob(pat):
            key = p.resolve()
            if key in seen:
                continue
            seen.add(key)
            files.append(p)

    n_files = 0
    for p in files:
        try:
            bt = pd.read_excel(p)
        except Exception as e:
            print(f"跳过回测 {p.name}: {e}")
            continue
        if bt.empty or "选股日" not in bt.columns or "股票代码" not in bt.columns:
            continue
        if "满足条件" not in bt.columns:
            continue
        bt = bt.copy()
        if "条件_收盘站上布林上轨" not in bt.columns:
            bt["条件_收盘站上布林上轨"] = False
        if "布林上轨" not in bt.columns:
            bt["布林上轨"] = ""

        changed = matched = 0
        for i in bt.index:
            sel = _parse_d(bt.at[i, "选股日"])
            code = _code6(bt.at[i, "股票代码"])
            if sel is None or not code:
                continue
            hit = lookup.get((sel.isoformat(), code))
            if hit is None:
                continue
            matched += 1
            meet, cond_b, boll = hit
            if _as_bool(bt.at[i, "满足条件"]) != meet:
                changed += 1
            bt.at[i, "满足条件"] = meet
            bt.at[i, "条件_收盘站上布林上轨"] = cond_b
            bt.at[i, "布林上轨"] = boll

        try:
            bt.to_excel(p, index=False)
        except Exception as e:
            print(f"写回失败 {p.name}: {e}")
            continue
        n_files += 1
        print(
            f"已回写回测 {p.name}: 匹配 {matched}/{len(bt)}，满足条件变更 {changed}"
        )
    return n_files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="补丁：选股结果加布林上轨并重算满足条件")
    ap.add_argument("--no-backtest", action="store_true", help="只改选股 xls，不回写按票回测")
    args = ap.parse_args(argv)

    print(f"目录: {OUT_DIR}")
    raw, used = collect_selection_rows()
    print(f"合并选股文件 {len(used)} 个 → 去重后 {len(raw)} 行")
    for name in used[:8]:
        print(f"  - {name}")
    if len(used) > 8:
        print(f"  … 另有 {len(used) - 8} 个")

    patched = patch_frame(raw)
    sel_path = write_selection(patched)

    if not args.no_backtest:
        n = patch_backtest_files(patched)
        print(f"回写回测文件数: {n}")
    print(f"完成。选股输出: {sel_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
