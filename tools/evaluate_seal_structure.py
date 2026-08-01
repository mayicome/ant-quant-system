#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评估「封单结构」结论与「昨日涨停次日表现」的一致性。"""

import argparse
import os
import re
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd


RATING_ORDER = {
    "🔴 虚封高危": 1,
    "🟠 弱势封板": 2,
    "🟡 中等封板": 3,
    "🟢 强势封板": 4,
    "🔥 超强极致封板": 5,
}


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def history_dir() -> str:
    return os.path.join(repo_root(), "history_data")


def normalize_code(v: object) -> str:
    s = str(v or "").strip().upper()
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s.zfill(6)[:6]


SEAL_STRICT_NAME_RE = re.compile(r"^封单结构_(\d{8})\.xlsx$", re.IGNORECASE)


def _mtime(fp: str) -> float:
    try:
        return os.path.getmtime(fp)
    except OSError:
        return 0.0


def newest_standard_seal_file() -> Optional[str]:
    """仅「封单结构_YYYYMMDD.xlsx」单日导出，排除含次日/滚动检验等派生文件。"""
    hd = history_dir()
    if not os.path.isdir(hd):
        return None
    cands: List[Tuple[float, str]] = []
    for name in os.listdir(hd):
        if not name.lower().endswith(".xlsx") or name.startswith("~$"):
            continue
        if not SEAL_STRICT_NAME_RE.match(name):
            continue
        fp = os.path.join(hd, name)
        cands.append((_mtime(fp), fp))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0], reverse=True)
    return cands[0][1]


def seal_yyyymmdd_from_path(path: str) -> Optional[str]:
    base = os.path.basename(path)
    m = SEAL_STRICT_NAME_RE.match(base)
    if m:
        return m.group(1)
    m2 = re.search(r"(\d{8})", base)
    return m2.group(1) if m2 else None


def _ymd_from_month_day(month: int, day: int, year_hint: int) -> str:
    return f"{year_hint:04d}{month:02d}{day:02d}"


def dates_in_yesterday_filename(name: str, year_hint: int) -> List[str]:
    """从「昨日涨停」导出文件名中解析可能表示封板日的 YYYYMMDD（可多值）。"""
    out: List[str] = []
    for m in re.finditer(r"(\d{8})", name):
        out.append(m.group(1))
    for m in re.finditer(r"(\d{4})年(\d{1,2})月(\d{1,2})日", name):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        out.append(_ymd_from_month_day(mo, d, y))
    for m in re.finditer(r"(?<!\d)(\d{1,2})月(\d{1,2})日", name):
        mo, d = int(m.group(1)), int(m.group(2))
        out.append(_ymd_from_month_day(mo, d, year_hint))
    # 去重保序
    seen: Set[str] = set()
    uniq: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def find_yesterday_file_for_seal_day(seal_day: str) -> Optional[str]:
    """在 history_data 中选文件名含「昨日涨停」且日期与封板日 seal_day 一致的最新 xlsx。"""
    if len(seal_day) != 8 or not seal_day.isdigit():
        return None
    year_hint = int(seal_day[:4])
    hd = history_dir()
    if not os.path.isdir(hd):
        return None
    cands: List[Tuple[float, str]] = []
    for name in os.listdir(hd):
        if not name.lower().endswith(".xlsx") or name.startswith("~$"):
            continue
        if "昨日涨停" not in name:
            continue
        parsed = dates_in_yesterday_filename(name, year_hint)
        if seal_day not in parsed:
            continue
        fp = os.path.join(hd, name)
        cands.append((_mtime(fp), fp))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0], reverse=True)
    return cands[0][1]


def yesterday_filename_implies_seal_day(path: str, seal_day: str) -> Tuple[bool, List[str]]:
    """若文件名能解析出日期，则 seal_day 须出现在解析结果中。"""
    name = os.path.basename(path)
    if len(seal_day) != 8 or not seal_day.isdigit():
        return True, []
    year_hint = int(seal_day[:4])
    parsed = dates_in_yesterday_filename(name, year_hint)
    if not parsed:
        return True, []
    return seal_day in parsed, parsed


def find_column(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    col_map = {str(c).strip().lower(): c for c in df.columns}
    for a in aliases:
        k = a.strip().lower()
        if k in col_map:
            return col_map[k]
    return None


def load_seal_df(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    code_col = find_column(df, ["股票代码", "代码", "code"])
    rating_col = find_column(df, ["封单评级", "order_rating"])
    name_col = find_column(df, ["股票名称", "名称", "name"])
    if code_col is None or rating_col is None:
        raise ValueError(f"封单结构文件缺少必要列（代码/封单评级）: {path}")
    out = pd.DataFrame()
    out["code"] = df[code_col].map(normalize_code)
    out["seal_rating"] = df[rating_col].astype(str).str.strip()
    out["name"] = df[name_col].astype(str).str.strip() if name_col is not None else ""
    out = out[(out["code"] != "") & (out["seal_rating"] != "")]
    return out.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)


def load_yesterday_limitup_df(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    code_col = find_column(df, ["代码", "股票代码", "code"])
    ret_col = find_column(df, ["今日表现%", "次日表现%", "涨跌幅", "涨跌幅%", "return", "ret"])
    name_col = find_column(df, ["名称", "股票名称", "name"])
    if code_col is None or ret_col is None:
        raise ValueError(f"昨日涨停文件缺少必要列（代码/今日表现%）: {path}")
    out = pd.DataFrame()
    out["code"] = df[code_col].map(normalize_code)
    out["next_day_ret"] = pd.to_numeric(df[ret_col], errors="coerce")
    out["name"] = df[name_col].astype(str).str.strip() if name_col is not None else ""
    out = out.dropna(subset=["next_day_ret"])
    out = out[out["code"] != ""]
    return out.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)


def evaluate(merged: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, object]]:
    x = merged.copy()
    x["rating_score"] = x["seal_rating"].map(RATING_ORDER)
    x = x.dropna(subset=["rating_score"])
    x["rating_score"] = x["rating_score"].astype(int)

    grouped = (
        x.groupby("seal_rating", as_index=False)
        .agg(
            N=("code", "count"),
            mean_ret=("next_day_ret", "mean"),
            median_ret=("next_day_ret", "median"),
            up_ratio=("next_day_ret", lambda s: (s > 0).mean()),
            limit_up_ratio=("next_day_ret", lambda s: (s >= 9.8).mean()),
        )
    )
    grouped["rating_score"] = grouped["seal_rating"].map(RATING_ORDER)
    grouped = grouped.sort_values("rating_score").reset_index(drop=True)

    if len(x) >= 3:
        spearman = x["rating_score"].corr(x["next_day_ret"], method="spearman")
    else:
        spearman = float("nan")

    # 高低评级对比（用于快速结论）
    high = x[x["rating_score"] >= 4]
    low = x[x["rating_score"] <= 2]
    high_median = high["next_day_ret"].median() if not high.empty else float("nan")
    low_median = low["next_day_ret"].median() if not low.empty else float("nan")
    high_up = (high["next_day_ret"] > 0).mean() if not high.empty else float("nan")
    low_up = (low["next_day_ret"] > 0).mean() if not low.empty else float("nan")

    ok_count = 0
    if pd.notna(high_median) and pd.notna(low_median) and high_median >= low_median:
        ok_count += 1
    if pd.notna(high_up) and pd.notna(low_up) and high_up >= low_up:
        ok_count += 1
    if pd.notna(spearman) and spearman > 0:
        ok_count += 1

    if ok_count >= 2:
        verdict = "初步成立：封单评级对次日表现有正向区分能力。"
    else:
        verdict = "证据偏弱：评级与次日表现区分不明显，建议扩大样本期再评估。"

    summary = {
        "sample_size": int(len(x)),
        "spearman": spearman,
        "high_median": high_median,
        "low_median": low_median,
        "high_up_ratio": high_up,
        "low_up_ratio": low_up,
        "verdict": verdict,
    }
    return grouped, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="评估封单结构结论是否与次日表现一致",
        epilog=(
            "默认在 history_data 下：\n"
            "  - 取最新「封单结构_YYYYMMDD.xlsx」（单日导出，不含含次日/滚动检验等）\n"
            "  - 再自动匹配文件名含「昨日涨停」且日期与该 YYYYMMDD 一致的表（支持文件名中的 8 位日期或「M月D日」）\n"
            "也可手动指定 --seal-file / --yesterday-file；若文件名能解析日期且与封板日不一致，将报错退出"
            "（可用 --allow-date-mismatch 强行评估）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--seal-file", help="封单结构 xlsx 路径（可省略）")
    parser.add_argument("--yesterday-file", help="昨日涨停表现 xlsx 路径（可省略）")
    parser.add_argument("--out", help="输出评估表路径（可省略，默认 history_data/封单结构评估_YYYYMMDD.xlsx）")
    parser.add_argument(
        "--allow-date-mismatch",
        action="store_true",
        help="文件名解析出的封板日与昨日涨停表日期不一致时仍继续（不推荐）",
    )
    args = parser.parse_args()

    seal_file = os.path.abspath(args.seal_file) if args.seal_file else newest_standard_seal_file()
    seal_day = seal_yyyymmdd_from_path(seal_file) if seal_file and os.path.isfile(seal_file) else None

    if args.yesterday_file:
        y_file = os.path.abspath(args.yesterday_file)
    elif seal_day:
        y_file = find_yesterday_file_for_seal_day(seal_day)
    else:
        y_file = None

    if not seal_file or not os.path.isfile(seal_file):
        print("未找到标准封单结构文件「封单结构_YYYYMMDD.xlsx」，请用 --seal-file 指定。")
        return 1
    if not seal_day:
        print(
            "无法从封单结构文件名解析封板日 YYYYMMDD（建议命名为 封单结构_YYYYMMDD.xlsx）。\n"
            "请改用标准文件名，或同时用 --yesterday-file 手动指定昨日涨停表。"
        )
        return 1
    if not y_file or not os.path.isfile(y_file):
        print(
            f"未找到与封板日 {seal_day} 对应的「昨日涨停」xlsx（文件名需含「昨日涨停」且含该日，如 …20260424… 或 4月24日…）。\n"
            "请用 --yesterday-file 指定，或把导出文件改名后放入 history_data。"
        )
        return 1

    ok_dates, parsed_y = yesterday_filename_implies_seal_day(y_file, seal_day)
    if not ok_dates and not args.allow_date_mismatch:
        print(
            f"封单结构封板日: {seal_day}\n"
            f"昨日涨停文件: {y_file}\n"
            f"从昨日涨停文件名解析到的日期: {parsed_y}，与封板日不一致，评估无意义。\n"
            "请换用同封板日的表，或加 --allow-date-mismatch 强行运行。"
        )
        return 1
    if not parsed_y:
        print(
            f"[提示] 无法从昨日涨停文件名解析日期，请自行确认该表对应封板日 {seal_day}；"
            "若不确定请用 --yesterday-file 指定正确文件。"
        )

    seal_df = load_seal_df(seal_file)
    y_df = load_yesterday_limitup_df(y_file)
    merged = seal_df.merge(y_df[["code", "next_day_ret"]], on="code", how="inner")

    grouped, summary = evaluate(merged)

    print(f"封单结构文件: {seal_file}")
    print(f"昨日涨停文件: {y_file}")
    print(f"匹配样本数: {summary['sample_size']}")
    print("\n分层统计（按评级由弱到强）：")
    print(grouped.to_string(index=False))
    print("\n核心结论：")
    print(f"- Spearman(评级序数, 次日表现): {summary['spearman']:.4f}" if pd.notna(summary["spearman"]) else "- Spearman: 样本不足")
    print(f"- 高评级中位数(>=🟢): {summary['high_median']:.2f}% | 低评级中位数(<=🟠): {summary['low_median']:.2f}%")
    print(f"- 高评级上涨比例: {summary['high_up_ratio']:.2%} | 低评级上涨比例: {summary['low_up_ratio']:.2%}")
    print(f"- 判断: {summary['verdict']}")

    out_path = args.out
    if not out_path:
        base = os.path.basename(seal_file)
        m = re.search(r"(\d{8})", base)
        day = m.group(1) if m else "latest"
        out_path = os.path.join(history_dir(), f"封单结构评估_{day}.xlsx")
    out_path = os.path.abspath(out_path)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        grouped.to_excel(writer, index=False, sheet_name="评级分层统计")
        merged.to_excel(writer, index=False, sheet_name="匹配样本明细")
        pd.DataFrame([summary]).to_excel(writer, index=False, sheet_name="结论摘要")
    print(f"\n已输出评估文件: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
