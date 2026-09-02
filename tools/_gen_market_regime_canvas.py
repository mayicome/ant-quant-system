# -*- coding: utf-8 -*-
"""Generate canvases/market-regime-2026.canvas.tsx from local CSV."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "market_regime" / "market_regime_daily.csv"
SSE = ROOT / "data" / "index_cache" / "000001.SH.csv"
OUT = Path(r"C:\Users\Administrator\.cursor\projects\d\canvases\market-regime-2026.canvas.tsx")


def main() -> None:
    df = pd.read_csv(CSV, encoding="utf-8-sig")
    sse = pd.read_csv(SSE)
    sse["date"] = sse["date"].astype(str).str[:10]
    m = df.merge(sse[["date", "close"]], left_on="trade_date", right_on="date", how="left")
    full_dates = list(m["trade_date"].astype(str))
    data = {
        "range": [full_dates[0], full_dates[-1]],
        "categories": [d[5:] for d in full_dates],
        "full_dates": full_dates,
        "sse": [round(float(x), 2) for x in m["close"]],
        "csi": [round(float(x), 2) for x in m["csi_close"]],
        "n_up": [int(x) for x in m["n_up"]],
        "n_down": [int(x) for x in m["n_down"]],
        "backdrop": list(m["backdrop"]),
        "sentiment": list(m["sentiment"]),
        "pulse": list(m["pulse"]),
        "divergence": list(m["divergence"]),
        "label_zh": list(m["label_zh"]),
        "label_counts": [
            {"label": str(k), "n": int(v)}
            for k, v in m["label_zh"].value_counts().items()
        ],
    }
    js = json.dumps(data, ensure_ascii=False)
    tsx = f"""import {{
  BarChart,
  Card,
  CardBody,
  CardHeader,
  Divider,
  Grid,
  H1,
  LineChart,
  Pill,
  Stack,
  Stat,
  Table,
  Text,
}} from "cursor/canvas";

const DATA = {js} as const;

const toneOfBackdrop = (v: string) =>
  v.includes("多头") ? "success" : v.includes("空头") ? "danger" : "warning";

export default function MarketRegime2026() {{
  const n = DATA.categories.length;
  const lastLabel = DATA.label_zh[n - 1];
  const lastSse = DATA.sse[n - 1];
  const lastUp = DATA.n_up[n - 1];
  const lastDn = DATA.n_down[n - 1];
  const start = Math.max(0, n - 24);

  const recentRows = DATA.full_dates.slice(start).map((_d, idx) => {{
    const ii = start + idx;
    return {{
      date: DATA.full_dates[ii],
      sse: DATA.sse[ii],
      up: DATA.n_up[ii],
      down: DATA.n_down[ii],
      backdrop: DATA.backdrop[ii],
      sentiment: DATA.sentiment[ii],
      pulse: DATA.pulse[ii],
      divergence: DATA.divergence[ii],
    }};
  }});

  const countRows = DATA.label_counts.slice(0, 15);

  return (
    <Stack gap={{20}}>
      <Stack gap={{6}}>
        <H1>2026 市场行情日度描绘</H1>
        <Text tone="secondary">
          Source: market_regime_daily.csv + 上证指数 sh000001 · {{DATA.range[0]}} ~ {{DATA.range[1]}} · {{n}} 个交易日
        </Text>
        <Text tone="secondary">
          标签基于中证全指宽度规则；图中另绘上证指数便于直观对照。高清静态图：data/market_regime/market_regime_chart.png
        </Text>
      </Stack>

      <Grid columns={{4}} gap={{12}}>
        <Stat value={{String(n)}} label="交易日数" />
        <Stat value={{String(lastSse)}} label="上证最新收盘" />
        <Stat value={{`${{lastUp}}/${{lastDn}}`}} label="最新涨/跌家数" />
        <Stat value={{String(DATA.label_counts.length)}} label="标签组合数" />
      </Grid>

      <Card>
        <CardHeader>上证指数 vs 中证全指收盘</CardHeader>
        <CardBody>
          <LineChart
            height={{280}}
            categories={{[...DATA.categories]}}
            series={{[
              {{ name: "上证指数", data: [...DATA.sse], tone: "info" }},
              {{ name: "中证全指", data: [...DATA.csi], tone: "neutral" }},
            ]}}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader>上涨 / 下跌家数</CardHeader>
        <CardBody>
          <BarChart
            height={{240}}
            categories={{[...DATA.categories]}}
            series={{[
              {{ name: "上涨家数", data: [...DATA.n_up], tone: "success" }},
              {{ name: "下跌家数", data: [...DATA.n_down], tone: "danger" }},
            ]}}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader>最近 24 个交易日</CardHeader>
        <CardBody>
          <Table
            stickyHeader
            columns={{[
              {{ key: "date", header: "日期", width: 100 }},
              {{ key: "sse", header: "上证", align: "right", width: 80 }},
              {{ key: "up", header: "涨", align: "right", width: 56 }},
              {{ key: "down", header: "跌", align: "right", width: 56 }},
              {{ key: "backdrop", header: "底色", width: 120 }},
              {{ key: "sentiment", header: "情绪", width: 110 }},
              {{ key: "pulse", header: "脉冲", width: 90 }},
              {{ key: "divergence", header: "背离", width: 100 }},
            ]}}
            rows={{recentRows.map((r) => ({{
              key: r.date,
              cells: [
                r.date,
                r.sse,
                r.up,
                r.down,
                <Pill key="b" tone={{toneOfBackdrop(r.backdrop) as "success" | "danger" | "warning"}} size="sm">{{r.backdrop}}</Pill>,
                r.sentiment,
                r.pulse,
                r.divergence,
              ],
              tone: r.divergence.includes("顶")
                ? "danger"
                : r.divergence.includes("底")
                  ? "info"
                  : undefined,
            }}))}}
          />
          <Divider />
          <Text weight="semibold">最新组合</Text>
          <Text>{{lastLabel}}</Text>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>标签组合频次 Top 15</CardHeader>
        <CardBody>
          <Table
            columns={{[
              {{ key: "n", header: "天数", align: "right", width: 64 }},
              {{ key: "label", header: "组合" }},
            ]}}
            rows={{countRows.map((r, i) => ({{
              key: String(i),
              cells: [r.n, r.label],
            }}))}}
          />
        </CardBody>
      </Card>
    </Stack>
  );
}}
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(tsx, encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
