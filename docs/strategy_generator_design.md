# 策略生成系统 — 模块设计草图

## 一、目标与定位

- **策略生成系统**：根据「策略配置 + 股票池」产出**待执行任务**，写入现有任务文件或通过 TaskManager 写入内存并保存。
- **不替代**现有图表/任务/执行系统，只负责「选股 + 生成任务」；人工审核与运行仍在主程序的任务表 + 图表中完成。

## 二、与现有系统的对接方式

### 2.1 任务结构（与当前完全一致）

策略生成层产出的每条任务，必须符合现有持久化格式，便于直接写入 `data/current_tasks_YYYY-MM-DD.xlsx` 或通过 `TaskManager` 写入。

**持久化列（`PERSIST_TASK_COLUMNS`）：**

| 字段 | 说明 | 策略生成系统填写 |
|------|------|------------------|
| task_id | UUID | 可由 TaskManager.generate_task_id() 或自生成 UUID |
| stock_code | 带后缀，如 000001.SZ | 必填 |
| stock_name | 股票名称 | 必填（可后续由主程序补全） |
| strategy | 固定为 `'万能策略'` | 必填 |
| buy_date | 日期 YYYY-MM-DD | 建议为运行日 |
| init_volume | 初始/目标股数 | 按策略逻辑填（0 表示仅卖出或待定） |
| init_cost | 成本/基准价 | 建议为当前价或策略基准价 |
| params | 字典，需含 rules 等 | 见下 |
| create_time | 创建时间字符串 | 建议填写 |
| status | 未运行 / 待审核 / 运行中 等 | 建议新增任务为「待审核」 |
| order_id | 委托号 | 留空 |

**params 结构（万能策略）：**

- `rules`: `List[dict]`，每个元素为一条规则，格式与图表侧一致（见 `core/trading_rules.py`）。
- 可选通用参数（与现有 create_universal_strategy 对齐）：  
  `up_threshold`, `down_threshold`, `up_operation`, `down_operation`,  
  `trade_volume`, `cycle_times`, `enable_smart_sell`, `sell_drop_threshold`,  
  `sell_timeout`, `enable_smart_buy`, `buy_rebound_threshold`, `buy_timeout`。

策略生成系统只需生成「合法的一批任务字典」，即可交给下面两种方式之一落盘。

### 2.2 对接方式（二选一或并存）

**方式 A：只写任务文件（离线/独立进程）**

- 策略生成系统独立运行（脚本或单独窗口），不依赖主程序已启动。
- 输出：直接生成或追加 `data/current_tasks_YYYY-MM-DD.xlsx`，列与 `PERSIST_TASK_COLUMNS` 一致，`params` 列存 JSON 字符串。
- 主程序启动或刷新时通过现有 `TaskManager.load_tasks()` 读入，用户在任务表中看到「待审核」任务，审核后点运行。

**方式 B：通过 TaskManager 写入（主程序已启动）**

- 主程序已运行，策略生成系统作为内置模块或子界面调用 `TaskManager`。
- 步骤：  
  1）用 `task_manager.generate_task_id()` 为每条任务生成 ID；  
  2）构造完整 task 字典（含 params）；  
  3）`task_manager.tasks[task_id] = task`，`task_manager.task_params[task_id] = task['params']`；  
  4）`task_manager.save_tasks(list(task_manager.tasks.values()))`。  
- 这样任务表会通过现有 `tasks_updated` 信号刷新，用户立即在任务表与图表中看到新任务。

建议：**首版用方式 A（只写文件）**，实现简单、可独立回测与调试；后续再在主程序里加「从策略生成系统导入任务」按钮，内部走方式 B。

---

## 三、策略生成系统模块划分

```
项目根目录/
├── strategy_generator/          # 策略生成系统（新建包）
│   ├── __init__.py
│   ├── config/                   # 策略与股票池配置
│   │   ├── __init__.py
│   │   ├── strategy_config.py    # 策略配置数据结构与读写
│   │   └── stock_pool.py        # 股票池定义与解析（名单/筛选条件）
│   ├── engine/                   # 策略引擎
│   │   ├── __init__.py
│   │   ├── base.py              # 策略基类：输入股票池+行情，输出「待生成任务列表」
│   │   └── runners/             # 各策略具体实现（可选：按策略类型分子模块）
│   │       ├── __init__.py
│   │       ├── example_single_buy.py
│   │       └── ...
│   ├── task_builder.py           # 将引擎输出的「信号/意图」转成 PERSIST 任务字典
│   ├── runner.py                # 主入口：加载配置 → 跑策略 → 写文件或调 TaskManager
│   └── data/                    # 策略生成系统自己的数据（可选）
│       ├── strategies/          # 策略配置 JSON/YAML
│       └── pools/               # 股票池定义（代码列表或筛选条件）
├── core/
│   ├── task_manager.py          # 已有，不修改接口，仅被策略生成系统调用
│   └── trading_rules.py         # 已有，规则结构以此处为准
└── data/
    └── current_tasks_YYYY-MM-DD.xlsx   # 任务文件，策略生成系统可写
```

---

## 四、配置与数据流

### 4.1 策略配置（每个策略一份）

建议格式（JSON 示例）：

```json
{
  "id": "strategy_breakthrough_01",
  "name": "突破买入示例",
  "enabled": true,
  "stock_pool_id": "pool_etf_300",
  "run_mode": "manual",
  "schedule": null,
  "approval_mode": "manual",
  "params": {
    "rule_type": "breakthrough_buy",
    "volume_per_stock": 1000,
    "price_offset_percent": 0.0,
    "max_positions": 10,
    "filters": {}
  }
}
```

- `stock_pool_id`：关联一个股票池（见下）。
- `run_mode`：`manual`（手动触发）/ `scheduled`（按 schedule 定时）。
- `approval_mode`：`manual`（生成任务为待审核）/ `auto`（生成后直接可自动运行，由主程序侧决定是否真自动启任务）。
- `params`：由具体策略解释（如规则类型、每股数量、价格偏移、最大持仓数、过滤条件等）。

### 4.2 股票池

- **方式 1**：静态名单 — 如 `pools/pool_etf_300.txt` 或 JSON 数组，每行或每元素一个代码（可带后缀，或由系统补全）。
- **方式 2**：筛选条件 — 如行业、市值区间、涨跌幅、自定义因子等；由 `stock_pool.py` 在运行时解析，并调用现有行情/因子数据接口得到代码列表。

策略配置里只存 `stock_pool_id`，运行时有引擎根据 id 解析出「当前应使用的股票列表」。

### 4.3 数据流（最小闭环）

1. **输入**：策略配置 + 股票池定义 + 当前行情（或历史日线，若做盘前选股）。
2. **策略引擎**（如 `engine/runners/example_single_buy.py`）：  
   - 对股票池中每只股票判断是否满足该策略的建仓/加仓/止盈止损等条件；  
   - 输出「待生成任务」列表，每项包含：`stock_code`、`stock_name`（可空）、规则类型、价格、数量、附加参数等（**不包含 task_id、status 等，由 task_builder 统一加**）。
3. **task_builder**：  
   - 将每条「待生成任务」转成符合 `PERSIST_TASK_COLUMNS` 的 task 字典；  
   - 调用 `core.trading_rules` 的规则类或直接手写 dict，构造 `params['rules']`；  
   - 设置 `strategy='万能策略'`、`status='待审核'`、`create_time`、`buy_date` 等。
4. **runner**：  
   - 若为「只写文件」：将 task_builder 输出的列表写成 `data/current_tasks_YYYY-MM-DD.xlsx`（新文件或追加，需保留当日已有任务，避免覆盖用户已有任务）；  
   - 若为「调 TaskManager」：在主进程内调用 `task_manager.save_tasks(...)`。

---

## 五、最小可行功能（V1）

| 序号 | 功能 | 说明 |
|------|------|------|
| 1 | 单策略 + 静态股票池 | 一个策略配置，对应一个静态代码列表文件；运行即对该列表跑一次策略逻辑。 |
| 2 | 策略逻辑：简单突破买入 | 对股票池中每只股票，若满足「当前价 > 某基准（如昨收）的 N%」，则生成一条「万能策略」任务，params 中仅含一条突破买入规则；价格/数量由策略 params 决定。 |
| 3 | 只写任务文件 | 运行后生成/追加 `data/current_tasks_YYYY-MM-DD.xlsx`，不依赖主程序已启动。 |
| 4 | 命令行入口 | 如 `python -m strategy_generator.runner --strategy strategy_breakthrough_01`，便于定时任务或手动执行。 |
| 5 | 可选：主程序「导入」按钮 | 主程序里增加「从策略生成系统导入今日任务」：读同一份策略配置与股票池，生成任务后调用 `TaskManager.save_tasks()`，并刷新任务表。 |

V1 不实现：多策略并行、定时调度、股票池动态筛选、回测界面、组合风控层。

---

## 六、与 trading_rules、task_manager 的接口约定

- **规则结构**：以 `core/trading_rules.py` 中各 `Rule.from_dict` / `to_dict` 为准；策略生成系统只需产出「可被 from_dict 接受的 dict」放入 `params['rules']`。
- **任务写入**：要么按 `PERSIST_TASK_COLUMNS` 写 Excel（params 列 JSON 序列化），要么只调用 `TaskManager.save_tasks(list_of_task_dict)`，不再直接改 TaskManager 内部其他状态。
- **任务 ID**：若走 TaskManager，必须用 `task_manager.generate_task_id()`；若只写文件，可用 `str(uuid.uuid4())` 保持一致。

---

## 七、后续可扩展点

- 多策略并行运行，每个策略独立配置与独立股票池。  
- 股票池改为「条件筛选」+ 定时刷新。  
- 策略运行结果写日志/数据库，便于回溯与简单绩效统计。  
- 主程序内嵌「策略生成」Tab：配置策略与股票池、一键生成任务并写入 TaskManager、表格中标记来源策略。  
- 组合层：单股/单策略/总仓位上限、简单回撤控制，再写回任务或只生成「允许执行」的任务子集。

---

本设计保证：**策略生成系统只产出标准任务；现有量化可视化与执行系统无需改核心逻辑，仅多了一种「任务来源」**。实现时可按 V1 列表从「单策略 + 静态池 + 写文件」开始，再逐步加接口与功能。
