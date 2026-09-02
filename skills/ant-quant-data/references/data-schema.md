# ant-quant cos/ 字段与样例

详细目录说明与元数据以数据包内 `README.md` 为准；本文件供 skill 按需加载。

## 公共字段

| 字段 | 含义 | 示例 |
|---|---|---|
| `trade_date` | 交易日 | `2026-08-28` |
| `trade_date_ymd` | 紧凑日期 | `20260828` |

## 目录树

```
cos/
├── README.md
├── after_hours/after_hours_top.jsonl
├── board_rank/{YYYYMMDD}.board_{industry|concept}.jsonl
├── lhb/lhb_base_{main|total|seat}.jsonl
├── lhb/{YYYYMMDD}.lhb_{main|total|seat}.jsonl
├── main_flow/{YYYYMMDD}.main_flow.jsonl
└── stock_info/all_a_stock_info.jsonl
```

## after_hours

合并文件；一行 = 某日入选一只股票。

| 字段 | 说明 |
|---|---|
| 代码 / code6 / 名称 | 代码（可含市场后缀）、六位、简称 |
| 盘后量 / 全天量 / 收盘竞价量 | 量 |
| 流通股万股 | 流通股本 |
| 盘后占全天 / 盘后相对竞价 / 盘后占流通 | 占比或比值 |
| 入选 | 规则标签 |

样例：`2026-07-14` · `688758.SH` 赛分科技 · 盘后量 410 · 盘后占全天 `0.284%`

## board_rank

日分片；涨跌幅全榜左连涨停家数、资金流。`board_type` = `行业` / `概念`。

重要字段：涨跌幅排名、板块名称、板块代码、涨跌幅、上涨/下跌家数、领涨股票、涨停家数、资金流排名、主力/超大单/大单/中单/小单净流入（净额·净占比）、主力净流入最大股。

样例（行业）：氮肥 · 涨跌幅 7.33 · 涨停 3 · 资金流排名 24 · 主力净流入 303817936

## lhb

| 类型 | base | 日分片 |
|---|---|---|
| 主力情报 | `lhb_base_main.jsonl` | `{日}.lhb_main.jsonl` |
| 总榜 | `lhb_base_total.jsonl` | `{日}.lhb_total.jsonl` |
| 席位 | `lhb_base_seat.jsonl` | `{日}.lhb_seat.jsonl` |

- 历史回看优先 base；日更读当日分片
- 不提供「主力强度」「次日溢价预测」
- main 的 base 多为拆列（机构买/净、北向买/净…）；日分片可能是 `机构买/净(万)` 合并字符串

总榜要点：上榜原因、龙虎榜买卖成交与占比、上榜后1/2/5/10日（可 null）  
席位要点：营业部、买/卖/净（元与万）、是否机构、是否北向

## main_flow

仅 `{YYYYMMDD}.main_flow.jsonl`；全日约 5200+ 行。金额多为展示字符串。

字段含：序号、代码、名称、最新价、今日涨跌幅、今日主力净流入-净额/净占比、流通市值、净流入占流通%、超大单/大单/中单/小单净流入。

## stock_info

- 主：`all_a_stock_info.jsonl`（全量覆盖）→ `stock_code,name,industry,concepts[],plates[]`
- 可选：`{日}.stock_info_delta.jsonl`
- 勿默认读 `all_a_stock_info.snapshot.json`

样例：`000001` 平安银行 · industry 银行 · concepts 含互联网金融等
