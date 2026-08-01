import argparse
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

import pandas as pd

HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history_data")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ALL_STOCK_INFO_PATH = os.path.join(DATA_DIR, "all_a_stock_info.json")

# 噪声标签：不参与“主线”聚合，避免融资融券/地域等把结果冲歪
NOISE_TAGS = {
    "融资融券", "沪股通", "深股通", "上证", "深成", "HS", "标准普尔", "富时罗素", "机构重仓",
    "QFII重仓", "转债标的", "小盘股", "微盘股", "百元股", "长期破净", "破净股",
    "北京板块", "上海板块", "深圳板块", "广东板块", "浙江板块", "江苏板块", "湖北板块",
    "山东板块", "四川板块", "长江三角", "央国企改革",
}


def _pick_latest_history_file() -> str:
    from utils.limit_up_day_path import list_limit_up_day_json_files

    pairs = list_limit_up_day_json_files(HISTORY_DIR)
    if not pairs:
        raise FileNotFoundError("history_data/涨停日数据 下未找到 YYYY-MM-DD.json")
    return pairs[-1][1]


def _load_limit_up_stocks(path: str) -> Tuple[str, List[Dict[str, Any]]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    dt = str(data.get("date") or os.path.basename(path)[:10])
    rows = data.get("limit_up_stocks") or []
    if not isinstance(rows, list):
        raise ValueError("limit_up_stocks 结构异常，不是列表")
    return dt, rows


def _extract_sector_tags(stock: Dict[str, Any]) -> List[Tuple[str, str]]:
    tags: List[Tuple[str, str]] = []
    industry = str(stock.get("industry") or "").strip()
    if industry:
        tags.append(("行业", industry))

    for c in stock.get("concepts") or []:
        c = str(c).strip()
        if c and c not in NOISE_TAGS:
            tags.append(("概念", c))

    for p in stock.get("plates") or []:
        p = str(p).strip()
        if p and p not in NOISE_TAGS and not p.endswith("板块"):
            tags.append(("板块", p))

    # 去重
    return list(dict.fromkeys(tags))


def build_sector_frame(limit_up_stocks: List[Dict[str, Any]]) -> pd.DataFrame:
    agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for s in limit_up_stocks:
        code = str(s.get("code", "")).strip()
        name = str(s.get("name", "")).strip()
        price = float(s.get("price") or 0.0)
        change = float(s.get("change_pct") or 0.0)
        tags = _extract_sector_tags(s)
        if not tags:
            continue
        for sector_type, sector_name in tags:
            key = (sector_type, sector_name)
            if key not in agg:
                agg[key] = {
                    "板块名称": sector_name,
                    "板块类型": sector_type,
                    "样本数": 0,
                    "涨停数": 0,
                    "平均涨幅": 0.0,
                    "龙头候选": [],
                }
            rec = agg[key]
            rec["样本数"] += 1
            rec["涨停数"] += 1 if change >= 9.8 else 0
            rec["平均涨幅"] += change
            rec["龙头候选"].append({"代码": code, "名称": name, "涨跌幅": change, "最新价": price})

    rows: List[Dict[str, Any]] = []
    for _, rec in agg.items():
        if rec["样本数"] <= 0:
            continue
        rec["平均涨幅"] = rec["平均涨幅"] / rec["样本数"]
        rows.append(rec)

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["板块名称", "板块类型", "样本数", "涨停数", "平均涨幅", "综合得分", "龙头候选"])

    # 评分：样本覆盖 + 涨停强度 + 平均涨幅
    df["覆盖得分"] = df["样本数"].rank(pct=True) * 100
    df["封板得分"] = df["涨停数"].rank(pct=True) * 100
    df["动量得分"] = df["平均涨幅"].rank(pct=True) * 100
    df["综合得分"] = df["覆盖得分"] * 0.45 + df["封板得分"] * 0.35 + df["动量得分"] * 0.20
    df = df.sort_values("综合得分", ascending=False).reset_index(drop=True)
    return df


def pick_leader_info(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not candidates:
        return {"总龙头": None, "补涨龙": None}
    cdf = pd.DataFrame(candidates)
    cdf["涨跌幅"] = pd.to_numeric(cdf["涨跌幅"], errors="coerce").fillna(0.0)
    cdf["最新价"] = pd.to_numeric(cdf["最新价"], errors="coerce").fillna(0.0)
    cdf = cdf.sort_values(["涨跌幅", "最新价"], ascending=[False, False]).reset_index(drop=True)
    leader = cdf.iloc[0].to_dict()
    # 补涨龙：非第一名且价格偏低，通常更有“补涨”特征
    follow = cdf.iloc[1:].copy()
    if follow.empty:
        return {"总龙头": leader, "补涨龙": None}
    low_price = follow[follow["最新价"] <= follow["最新价"].median()]
    if low_price.empty:
        buzhang = follow.iloc[0].to_dict()
    else:
        buzhang = low_price.sort_values("涨跌幅", ascending=False).iloc[0].to_dict()
    return {"总龙头": leader, "补涨龙": buzhang}


def build_concept_stock_map(path: str = ALL_STOCK_INFO_PATH) -> pd.DataFrame:
    """
    从 all_a_stock_info.json 反向构建：概念 -> 个股列表
    仅保留个股数在 [15, 120] 的概念。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("all_a_stock_info.json 结构异常：顶层不是对象")

    concept_map: Dict[str, Dict[str, str]] = {}
    for stock_code, info in data.items():
        if not isinstance(info, dict):
            continue
        code = str(info.get("stock_code") or stock_code or "").strip()
        name = str(info.get("name") or "").strip()
        concepts = info.get("concepts") or []
        if not isinstance(concepts, list):
            continue
        for c in concepts:
            concept = str(c or "").strip()
            if not concept:
                continue
            if concept not in concept_map:
                concept_map[concept] = {}
            # 用 dict 去重（同概念下 code 唯一）
            if code:
                concept_map[concept][code] = name

    rows: List[Dict[str, Any]] = []
    for concept, stocks in concept_map.items():
        cnt = len(stocks)
        if cnt < 15 or cnt > 120:
            continue
        sample = list(stocks.items())[:10]
        sample_text = ", ".join([f"{k}({v})" if v else k for k, v in sample])
        rows.append(
            {
                "概念": concept,
                "个股数": cnt,
                "样例(前10)": sample_text,
                "个股代码列表": ",".join(stocks.keys()),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["概念", "个股数", "样例(前10)", "个股代码列表"])
    df = df.sort_values(["个股数", "概念"], ascending=[False, True]).reset_index(drop=True)
    return df


def print_concept_summary(path: str = ALL_STOCK_INFO_PATH) -> None:
    df = build_concept_stock_map(path)
    print("========== 概念反算结果（个股数 15~120）==========")
    print(f"数据来源: {path}")
    print(f"概念数量: {len(df)}")
    if df.empty:
        print("无符合条件的概念")
        return
    for _, row in df.iterrows():
        print(f"{row['概念']} | 个股数:{int(row['个股数'])} | 样例:{row['样例(前10)']}")


def main():
    parser = argparse.ArgumentParser(description="A股主线/次主线/轮动板块识别（纯本地数据）")
    parser.add_argument("--date", help="指定日期，如 2026-04-15；默认读取 history_data 最新日期文件")
    parser.add_argument("--concepts-only", action="store_true", help="仅打印 all_a_stock_info 概念反算结果")
    args = parser.parse_args()

    print_concept_summary()
    print()
    if args.concepts_only:
        return

    if args.date:
        from utils.limit_up_day_path import resolve_limit_up_day_json_path

        file_path = resolve_limit_up_day_json_path(args.date, HISTORY_DIR)
        if not file_path:
            raise FileNotFoundError(f"未找到日期文件: {args.date}.json")
    else:
        file_path = _pick_latest_history_file()

    date_str, limit_up_stocks = _load_limit_up_stocks(file_path)
    print(f"========== A股主线板块识别（{date_str}）==========")
    print(f"数据来源: {file_path}")
    print(f"涨停样本数: {len(limit_up_stocks)}\n")

    sector_df = build_sector_frame(limit_up_stocks)
    if sector_df.empty:
        print("本地文件中无可用板块数据")
        return

    main_sectors = sector_df.iloc[:2]
    sub_sectors = sector_df.iloc[2:5]
    rotate_sectors = sector_df.iloc[5:10]

    print("【最强主线（Top2）】")
    for _, row in main_sectors.iterrows():
        print(
            f"{row['板块名称']}({row['板块类型']}) | 样本:{int(row['样本数'])} | 涨停:{int(row['涨停数'])} "
            f"| 平均涨幅:{row['平均涨幅']:.2f}% | 得分:{row['综合得分']:.1f}"
        )
        leaders = pick_leader_info(row["龙头候选"])
        if leaders["总龙头"]:
            l = leaders["总龙头"]
            print(f"  总龙头: {l['名称']}({l['代码']}) | 涨幅:{l['涨跌幅']:.2f}% | 价格:{l['最新价']:.2f}")
        if leaders["补涨龙"]:
            b = leaders["补涨龙"]
            print(f"  补涨龙: {b['名称']}({b['代码']}) | 涨幅:{b['涨跌幅']:.2f}% | 价格:{b['最新价']:.2f}")
    print()

    print("【次主线（Top3-5）】")
    for _, row in sub_sectors.iterrows():
        print(
            f"{row['板块名称']}({row['板块类型']}) | 样本:{int(row['样本数'])} | 涨停:{int(row['涨停数'])} "
            f"| 平均涨幅:{row['平均涨幅']:.2f}% | 得分:{row['综合得分']:.1f}"
        )
    print()

    print("【轮动板块（Top6-10）】")
    for _, row in rotate_sectors.iterrows():
        print(
            f"{row['板块名称']}({row['板块类型']}) | 样本:{int(row['样本数'])} | 涨停:{int(row['涨停数'])} "
            f"| 平均涨幅:{row['平均涨幅']:.2f}% | 得分:{row['综合得分']:.1f}"
        )


if __name__ == "__main__":
    main()