# 策略生成系统 — 回测功能建议

## 一、现状简要

- **策略生成**：用户编写 `run(codes, prices, get_name, account, params)` 或使用表单策略，产出「意图列表」（stock_code、rule_type、price、volume 等），再由 `task_builder` 转为标准任务。
- **行情**：实时用 `price_provider.get_prices_with_key_points(codes)`，内部用 xtdata + `KeyPriceCalculator` 得到最新价、昨收、均线、涨跌停等。
- **已有回测**：`core/backtest_engine.py` 是**单标的、tick 级**回测，策略接口是 `on_tick(tick)`，与策略生成器的 `run(codes, prices, ...)` 不一致，且未与「策略配置 + 股票池」打通。

因此建议：**为策略生成系统单独做一套「多标的、基于 tick 数据」的回测**，复用现有策略接口与任务结构，便于在生成实盘任务前先做历史验证。

---

## 二、设计目标

1. **复用策略逻辑**：不改用户写的 `run(codes, prices, get_name, account, params)`，在回测里按「每个交易日」构造当日的 `prices`（日线截止当日）并调用同一段代码，得到当日意图。
2. **与实盘一致**：回测用的 `prices` 结构（current、pre_close、5日、10日、涨跌停等）与 `get_prices_with_key_points` 一致，避免前视偏差。
3. **基于 tick 的成交模拟**：策略每日产出的意图，在**下一交易日**用该日的 **tick 数据** 按时间顺序模拟成交——突破价在首次触及该价时成交、笼子在价格进入区间时成交、定时清仓在指定时刻成交，更新现金与持仓，再算收益曲线与基础指标。若无 tick 数据可退化为「下一日开盘价」统一成交。

---

## 三、架构建议

### 3.1 模块划分

```
strategy_generator_app/
├── backtest/                    # 新建：策略生成系统专用回测
│   ├── __init__.py
│   ├── data_provider.py         # 历史行情：按「截止日期」提供与实盘一致的 prices
│   ├── simulator.py             # 订单模拟：意图 → 成交（下一日开盘等）
│   ├── engine.py                # 回测主循环：按日跑策略 → 模拟 → 记权益
│   └── metrics.py               # 收益、回撤、胜率等
```

- **data_provider**：① 日线：按 `as_of_date` 用 xtdata 日线取各标的到该日为止的数据，计算均线、涨跌停等，作为当日 `prices` 传入 `run(...)`。② **Tick**：`load_tick_data_for_date` / `load_ticks_for_codes` 按交易日加载 xtdata tick 数据（仅交易时段），供 simulator 使用。
- **simulator**：① **Tick 模式（默认）**：`simulate_fills_with_ticks` 将意图与当日 tick 按时间合并，逐 tick 检查是否满足规则（突破价触及、笼子进入区间、定时到点），首次满足即成交并记录成交时间与价格。② 日频模式：`simulate_fills` 用下一日开盘价统一成交（无 tick 或 `use_tick_level=False` 时）。
- **engine**：在 [start_date, end_date] 内按日遍历；每日用 data_provider 取日线 `prices`，调用策略得到意图；对「下一日」加载 tick（若 `use_tick_level=True`），调用 `simulate_fills_with_ticks`，否则用开盘价 `simulate_fills`；记录每日权益、成交明细（含 tick 级时间）。
- **metrics**：根据权益序列和成交明细计算总收益、年化、最大回撤、胜率、交易次数等。

### 3.2 数据流（简要，tick 级回测）

1. 用户选择：策略（含股票池与代码/参数）、回测区间、初始资金、是否使用 tick 级成交（默认 True）、滑点/手续费（可选）。
2. 对每个交易日 T：  
   - **策略生成**：用 **get_historical_prices_for_morning(T)** 取早盘视角（今开、昨收、均线用上一日及之前），调用 **run(...)** 得到意图。  
   - **策略运行**：同一日 T 用 T 日 **tick 数据** 模拟成交；无 tick 时用当日开盘价成交。  
   - **当日末权益**：用 T 日**收盘价**盯市，total = cash + 持仓×收盘价。  
3. 回测结束后用 **metrics** 算指标，并输出交易明细（含 date+time）、权益曲线（可导出 CSV/图表）。

### 3.3 与现有组件的对接

- **策略**：直接使用 `strategy_runner.run_user_strategy(code_str, codes, prices, get_name, account, params)`；`prices` 由回测的 data_provider 按日提供，`account` 为回测虚拟账户（total_asset, cash）。
- **股票池**：使用策略配置里的 `stock_codes`，与「预览/生成任务」一致。
- **get_name**：可沿用主项目 `utils.stock_info_manager.get_stock_name` 或回测内维护一份 code→name 映射（从 xtdata 或 all_a_stock_info.json 取）。
- **KeyPriceCalculator**：建议在 data_provider 内复用其「按日线算均线、涨跌停」的逻辑，但输入改为「截止 as_of_date 的日线」，避免使用 T 日之后的数据。

---

## 四、实现要点

### 4.1 历史 prices 的构造（防前视）

- 对日期 T，只使用 T 日及之前的日线。
- 「当前价」用 T 日收盘价；「昨收」用 T-1 收盘；均线用 T 日及之前收盘价计算；涨跌停用 T 日昨收×1.1/0.9（或与 KeyPriceCalculator 一致）。
- 这样策略在 T 日「收盘后」生成的任务，与实盘「当日收盘后跑策略、次日下单」一致。

### 4.2 成交规则（tick 级，可配置）

- **默认（tick 级）**：意图在 T 日产生 → 在 T+1 日用 **tick 数据** 按时间顺序模拟：  
  - **breakthrough_buy / single_buy**：当 tick 的 lastPrice ≥ 设定价时，在该 tick 价格成交一次。  
  - **cage_buy**：当 lastPrice 首次落入 [price_low, price_high] 时成交。  
  - **breakthrough_sell / cage_sell**：同理（卖出为 lastPrice ≤ 价或落入区间）。  
  - **scheduled_clear**：在 intent 指定时间（如 14:56:00）及之后的第一个 tick 成交。  
- **退化（日频）**：无 tick 或 `use_tick_level=False` 时，意图在 T+1 日开盘价处统一成交（扣手续费）。
- 滑点/手续费：可在 simulator 中配置（如万三）。

### 4.3 仓位与资金

- 初始资金由用户输入；每次买入扣减 cash，卖出增加 cash；持仓用 `{stock_code: volume}` 记录，成本用加权平均。
- 单标的/总仓位上限可在 simulator 中做简单约束（例如单只不超过总资产 X%，总仓位不超过 Y%），与实盘风控对齐。

### 4.4 规则类型与 tick 模拟

- **breakthrough_buy / single_buy**：在 T+1 日 tick 序列中，当 lastPrice ≥ price 时在该 tick 价格成交。
- **cage_buy**：当 lastPrice 首次满足 price_low ≤ lastPrice ≤ price_high 时成交。
- **breakthrough_sell / cage_sell**：卖出同理（≤ 价或落入区间）。
- **scheduled_clear**：在 scheduled_clear_time 及之后首个 tick 成交。
- **best_buy / best_sell**：可按 trigger_price 作突破处理。  
若某日无 tick 数据，则该日意图退化为下一日开盘价统一成交。

---

## 五、界面与入口建议

1. **策略生成器主界面**  
   - 在「策略」Tab 或单独增加「回测」Tab：选择策略、回测开始/结束日期、初始资金、可选滑点/手续费。  
   - 按钮：「运行回测」→ 调用 `backtest/engine`，完成后弹窗或子页展示：总收益、年化、最大回撤、胜率、交易笔数；表格：每笔成交（日期、标的、买卖、数量、价格、盈亏）；权益曲线图（可选，用 matplotlib 或导出 CSV 在 Excel 中画）。

2. **命令行**（可选）  
   - `python -m strategy_generator_app.backtest_run --strategy <id> --start 2025-01-01 --end 2025-12-31 --capital 1000000`，便于批量或定时跑回测。

---

## 六、与现有 core/backtest_engine 的关系

- **core/backtest_engine**：保留用于「单标的、逐 tick 驱动 on_tick 策略」的图表/规则回测。  
- **strategy_generator_app/backtest**：专门服务「策略生成系统」——信号仍由 `run(codes, prices, ...)` 按日产生，**成交模拟基于 tick 数据**（多标的、按 tick 时间顺序在首次满足条件时成交）；不替代原有 on_tick 回测，二者可并存。

---

## 七、实现优先级建议

| 优先级 | 内容 | 说明 |
|--------|------|------|
| P0 | data_provider：按日提供 prices + 按日加载 tick | 信号与成交均依赖数据 |
| P0 | simulator：tick 级成交（突破/笼子/定时） | 按 tick 时间顺序、首次满足即成交 |
| P0 | engine：按日调用策略 + tick simulator 更新持仓 | 回测主流程，默认 use_tick_level=True |
| P1 | metrics：收益、回撤、胜率、交易明细（含时间） | 用于评估与导出 |
| P1 | 策略生成器内「回测」入口 + 结果展示 | 表格 + 简单图表或导出 |
| P2 | 滑点、手续费、仓位上限 | 更贴近实盘 |
| P2 | 无 tick 时退化为开盘价成交 | 已实现 |
| P2 | 命令行入口 | 方便批量回测 |

按上述顺序可实现一个「能跑、可评估、与实盘逻辑一致」的初版回测，再逐步加细节与展示。
