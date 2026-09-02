---
name: ant-quant-data
description: >-
  Maps ant-quant A-share JSONL under cos/ (board rank, LHB, main flow,
  after-hours, stock info). Use when reading ant-quant-data.zip, COS cos/
  prefix, offline trial data, or answering questions about these datasets.
license: MIT
compatibility: >-
  AgentSkills (Cursor / OpenClaw龙虾 / WorkBuddy / Hermes). Needs local cos/
  tree or unpacked ant-quant-data.zip; Python 3 for sample scripts optional.
metadata:
  author: ant-quant
  version: "1.0.0"
  tags: "quant,a-share,jsonl,cos,lhb,board-rank"
---

# ant-quant-data

教智能体如何定位、读取、过滤蚂蚁量化提供的 A 股 JSONL 数据包（`cos/`）。
与离线包 `ant-quant-data.zip` 同名，便于对应。

## When to Use

- 用户提到：`cos/`、`ant-quant-data.zip`、盘后量、板块排名、龙虎榜、主力净流入、全 A 股票信息
- 需要按交易日查行业/概念涨跌幅与资金流、龙虎榜席位、个股主力流入
- 连接器日更目录或离线试用包解压后的同一套文件

**Don't use for:** 实盘下单、QMT 行情 tick、非本包内的 CSV/Excel 原始抓取文件。

## Data root

按优先级解析数据根目录 `COS_ROOT`：

1. 环境变量 `ANT_QUANT_COS_ROOT`（若已设置）
2. 工作区里的 `data/cos/`（开发机）
3. 当前目录下的 `cos/`（离线 zip 解压后常见布局）
4. 本 skill 旁若存在 `../../` 指向仓库时的 `data/cos/`

离线包解压后根内直接是 `after_hours/`、`board_rank/` 等时，把该根当作 `COS_ROOT`。

## Format rules

- UTF-8 **JSONL**：一行一个 JSON；禁止当整文件 JSON 解析
- 公共日期字段：`trade_date`（`YYYY-MM-DD`）、`trade_date_ymd`（`YYYYMMDD`）
- 日分片文件名：`{YYYYMMDD}.…jsonl`
- 缺榜/未算溢价等可为 `null`
- 金额有时是东财展示字符串（如 `4.60亿`、`-5182.40万`），勿假设全是 number

## Dataset map (quick)

| 目录 | 文件模式 | 一行是什么 | 怎么取某日 |
|---|---|---|---|
| `after_hours/` | `after_hours_top.jsonl`（合并） | 当日盘后量 Top 一只股票 | 过滤 `trade_date_ymd` |
| `board_rank/` | `{日}.board_industry.jsonl` / `{日}.board_concept.jsonl` | 一个板块（涨跌幅+涨停家数+资金流） | 打开对应日文件 |
| `lhb/` | `lhb_base_{main,total,seat}.jsonl` | 历史合并 | 过滤日期；回看优先 base |
| `lhb/` | `{日}.lhb_{main,total,seat}.jsonl` | 当日分片 | 打开对应日文件 |
| `main_flow/` | `{日}.main_flow.jsonl` | 一只股票当日资金流（全市场） | 仅全日样本日；无大合并文件 |
| `stock_info/` | `all_a_stock_info.jsonl` | 一只股票标签（最新全量） | 整文件；delta 可选 |

字段级说明与表格样例见 [references/data-schema.md](references/data-schema.md)。

## Procedure

1. 确认 `COS_ROOT` 存在且含上述子目录（可先读 `COS_ROOT/README.md`）。
2. 按问题选表：板块→`board_rank`；龙虎榜→`lhb`；个股资金→`main_flow`；盘后异动→`after_hours`；行业/概念归属→`stock_info`。
3. 确定交易日 `YYYYMMDD`；日分片直接打开文件，合并文件按行过滤日期。
4. 流式逐行 `json.loads`；大文件（`main_flow`、`lhb_base_seat`）不要一次性 `read().splitlines()` 全进内存除非必要。
5. 回答时注明所用文件路径与 `trade_date`；金额单位写清（元 / 万 / 展示字符串）。

### 读行示例（Python）

```python
import json
from pathlib import Path

def iter_jsonl(path):
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)

# 日分片
rows = list(iter_jsonl(f"{COS_ROOT}/board_rank/20260828.board_industry.jsonl"))

# 合并文件按日过滤
day = "20260828"
hits = [r for r in iter_jsonl(f"{COS_ROOT}/after_hours/after_hours_top.jsonl")
        if r.get("trade_date_ymd") == day]
```

可选脚本（有 Python 时）：[scripts/peek_jsonl.py](scripts/peek_jsonl.py)

## Pitfalls

- `main_flow` 早期「净流入≥约3000万」截断 CSV **未导出**；只有行数≥约4000的全日才有分片
- `lhb` 主力情报：`lhb_base_main` 多为拆列；日分片可能是「买/净」合并列——以文件内键名为准
- `stock_info` 的 `all_a_stock_info.snapshot.json` 是导出快照，一般不给业务分析读
- `board_rank` 涨停家数/资金流字段可能为 `null`（该日缺旁榜）
- 本包不含 OHLCV 日线/分钟线

## Install (portable)

目录内须含 `SKILL.md`。装 skill 时**指定本目录** `ant-quant-data/`（不要指定更外层的数据包根）。

| 环境 | 做法 |
|---|---|
| WorkBuddy | 「添加技能」选文件夹 → 解压后的 `skills/ant-quant-data`；或拷到 `~/.workbuddy/skills/ant-quant-data/` |
| Cursor | `.cursor/skills/ant-quant-data/` |
| OpenClaw（龙虾） | `~/.openclaw/skills/ant-quant-data/` 或工作区 skills |
| Hermes | `~/.hermes/skills/ant-quant-data/` |

离线包只需下载 `ant-quant-data.zip`；解压后 skill 已在 `skills/ant-quant-data/`。

## Verification

- [ ] 能列出 `COS_ROOT` 下五个业务子目录
- [ ] 对任一 `.jsonl` 成功解析首行 JSON
- [ ] 按用户日期打开了正确的日分片或过滤到正确 `trade_date_ymd`
