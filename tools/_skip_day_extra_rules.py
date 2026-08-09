# -*- coding: utf-8 -*-
import pandas as pd
from pathlib import Path

feat = pd.read_excel(
    Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门\跳过日规则扫描_七月.xlsx"),
    sheet_name="日特征与结果",
)
TARGET = {"2026-07-03", "2026-07-06", "2026-07-09"}


def ev(mask, name):
    mask = mask.fillna(False)
    skipped = feat.loc[mask, "选股日"].tolist()
    catch = len(TARGET & set(skipped))
    good = feat["mean"] >= 0
    false = int((mask & good).sum())
    kept = feat.loc[~mask]
    print(
        "%s: skip=%d catch=%d/3 false_good=%d kept_mean=%+.3f"
        % (name, int(mask.sum()), catch, false, kept["mean"].mean())
    )
    print("  skip:", ",".join(sorted(skipped)))


ev(feat["buy_open_up_pct"] > 40, "高开%>40")
ev(feat["buy_open_up_pct"] > 35, "高开%>35")
ev(feat["buy_open_gap_mean"] > 0, "缺口均>0")
ev(
    (feat["buy_open_up_pct"] > 40) | (feat["mkt_up_pct_sel"] < 35),
    "高开%>40 或 选股涨%<35",
)
ev(
    (feat["buy_open_up_pct"] > 42) | (feat["mkt_mean_sel"] < -1),
    "高开%>42 或 选股等权<-1",
)
ev(
    (feat["mkt_up_pct_sel"] > 65) & (feat["buy_open_up_pct"] > 40),
    "选股涨%>65且高开%>40",
)
ev(
    (feat["mkt_mean_sel"] > 0.8) & (feat["buy_open_up_pct"] > 40),
    "选股等权>0.8且高开%>40",
)
ev(
    ((feat["mkt_mean_sel"] > 0.8) & (feat["buy_open_up_pct"] > 40))
    | (feat["mkt_mean_sel"] < -1),
    "(强选股+高开%>40) 或 选股等权<-1",
)
print(
    feat[feat["选股日"].isin(TARGET)][
        [
            "选股日",
            "mean",
            "mkt_mean_sel",
            "mkt_up_pct_sel",
            "buy_open_up_pct",
            "buy_open_gap_mean",
            "mkt_mean_prev",
        ]
    ].to_string(index=False)
)
