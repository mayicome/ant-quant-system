# MiniQMT → 大 QMT（投研端 / builtin）移植要点

> 文档日期：2026-07-30  
> 范围：蚂蚁量化主系统（`qmt_mode=builtin`）+ 岳教授 T+0 盘后填表（`fillt0` / `timed`）  
> 性质：移植总结与运维备忘，非接口说明书

---

## 1. 架构怎么变了

| 维度 | 旧（MiniQMT） | 新（大 QMT / builtin） |
|------|----------------|-------------------------|
| 行情/交易宿主 | 迷你 QMT + `xtquant` 客户端连本机服务 | **投研端模型交易**跑「蚂蚁量化规则」；主程序读 `results.json`、下单意图写规则文件 |
| 主程序角色 | 自己订行情、下单、拉历史 | UI / 选股 / 规则生成；**执行与全 A 日线·tick 落盘在大 QMT 内** |
| 配置开关 | `path_qmt` 指 Mini 路径 | `data/config.ini`：`qmt_mode = builtin`，`[qmt_builtin] qmt_python_dir=...` |
| 日线 | 各处现拉或 Mini 缓存 | 统一 **`data/daily_cache/{code}.SZ\|SH.csv`** + `manifest.json` |
| Tick | Mini / 临时缓存 | **`data/ticks/{YYYYMMDD}/{code6}.parquet`**，盘后全 A 落盘 |
| 岳教授填表 | `xtdata.connect()` 连 Mini | **增量读蚂蚁 daily_cache**；全历史/缺票回退大 QMT `xtdata`（不再 `connect` Mini） |

核心原则：**大 QMT 是行情与执行的权威进程；主程序与岳教授尽量读本地落盘，少在热路径硬连 xtquant。**

---

## 2. 代码与部署链路（必记）

大 QMT 内置 Python 要求 **GBK** 源文件，仓库维护用 UTF-8：

1. 改 `qmt_builtin/src/*.py`（UTF-8）
2. `python tools/sync_qmt_gbk.py` → 生成 `qmt_builtin/*.py`（GBK）
3. `python tools/deploy_to_qmt.py` → 复制到 `qmt_python_dir`（当前为 `D:/国金证券QMT交易端/python`）
4. 模型交易里 **停止 → 再运行**「蚂蚁量化规则」（必要时重载模型）

注意：

- 只改 `src` 不 sync/deploy，**线上策略不会变**。
- 确认本机只有一套在用的 `...\python\蚂蚁量化规则.py`；部署目标以 `config.ini` 为准。
- 策略以 `<string>` 方式加载时可能没有可靠 `__file__`，路径依赖 `ant_qmt_paths` / `sys.path`。

---

## 3. 盘后数据流水线

```
15:35  daily_bar_sync（全 A 日线 → daily_cache + manifest）
   │
   ├─ 预约 tick（默认延迟 TICK_CHAIN_DELAY_SEC=900，约 15 分钟错峰）
   ▼
约 15 分钟后  tick_full_sync（全 A 当日 tick → data/ticks/{日}/）
   │
   ▼
盘后量能 after_rank（读本地 parquet）
   │
   ▼
板块同步（后台）
```

**manifest 就绪条件（岳教授 timed / 盘后批跑常用）：**

- `status == "completed"`
- `sync_trade_date == 今天（YYYY-MM-DD）`

日线失败或超时应告警（Server酱），不要假装用昨日缓存当今日增量。

---

## 4. 岳教授侧（`D:\岳教授`）

### 4.1 `fillt0.py`

- 去掉 Mini `xtdata.connect()`。
- **增量**：优先 `D:\蚂蚁量化系统\data\daily_cache\{code}.SZ|SH.csv`。
- **新建/全历史**（如从 `19910102`）：走大 QMT `xtdata.download_history_data`（需投研端已开，不必为新建票单独重启，但进程要活着）。
- 工作日若 `enddate` 还不在缓存里（日线未写完当日）→ 回退 xtdata，避免只刷到昨天。
- 成交额仍按原逻辑 `/10000`；列结构保持不变。
- 批处理包在 `if __name__ == "__main__"`，避免被 import 误跑全量。

### 4.2 `timed.py`

- 交易日先 **轮询 manifest**，完成后再 `fillt0` → `summary`。
- 建议计划任务约 **15:50** 启动（日线 15:35 起跑，少空等）。
- Server酱：等待过久 / 同步失败 / 超时发告警；成功结束仍由 `summary` 发。
- 卡住告警默认约 90 分钟（日线 sleep 缩短后常态更快；可按环境变量调）。

### 4.3 耗时体感

- fillt0 本身约 **2 小时量级**（单票数秒～十余秒 × 数百文件）；开工晚（等日线）则整段后移。
- 结束微信在 **summary 跑完** 后，不在 fillt0 中途。

---

## 5. 全 A Tick 落盘与历史补数

### 5.0 正确取数方式（易踩坑，2026-07-31）

用户直觉「大 QMT 不可能拿不到最近 tick」是对的——问题多半是**方法用错**，不是客户端做不到。

| 错误理解 | 官方正确路径（模型交易 / 内置 python） |
|----------|----------------------------------------|
| `get_market_data_ex(..., subscribe=True)` 会从服务器补历史 tick | `subscribe` 只表示是否订阅；**历史要先 download** |
| 只能靠 miniQMT `xtdata.download_history_data2`（58610） | 策略里直接调**内置** `download_history_data`（迅投知识库「行情函数」） |

**可工作的官方调用（近 1 个月分笔）：**

```python
# 1) 补本地（参数用纯 YYYYMMDD；与成功过的 xtdata 下载一致）
download_history_data("000001.SZ", "tick", "20260730", "20260730")
# 2) 读本地（subscribe=False）
data = ContextInfo.get_market_data_ex(
    [], ["000001.SZ"], period="tick",
    start_time="20260730", end_time="20260730",
    subscribe=False,
)
```

UI：数据管理 → 补充数据 → 勾选「分笔」→ 下载后，同一套 `get_market_data_ex/get_local_data` 即可读。

探测：模型交易调用策略函数 **`tick_probe`**（默认测 20260730 三只流动性票），看日志/`data/tick_full_sync/tick_probe_*.log` 哪组变体有 rows。

### 5.1 当日正式落盘（大 QMT 内）

- 由日线完成后串行触发（现已 **延迟约 15 分钟** 再开，试验错峰）。
- 主路径：内置 `download_history_data` → `get_market_data_ex(subscribe=False)`；`ENABLE_XTDATA_TICK_DOWNLOAD=False`。
- 批次约 20 只；批间 sleep 仅 **0.05s**；xtdata 下载回调最长等约 **120s**（默认关）。
- 落盘含至约 **15:31** 盘后时段（量能依赖）；`_full_sync_done.json` 为完成标记。

### 5.2 历史补数（经策略 ContextInfo，系统 Python 只写请求）

外挂没有 `ContextInfo`。默认把队列写入 `data/tick_full_sync/manual_request.json`，
由「蚂蚁量化规则」`periodic_sync` → `process_manual_request(ContextInfo)` 用大 QMT C 端拉数。

```bat
cd /d D:\蚂蚁量化系统
rem 先确保大 QMT 策略「蚂蚁量化规则」已运行
python tools/backfill_tick_history.py --dry-run
python tools/backfill_tick_history.py --wait-today --max-days 2
python tools/backfill_tick_history.py --wait-today --max-days 0   rem 周末扫窗口
python tools/backfill_tick_history.py --submit-only --max-days 2  rem 只投递不轮询
```

- 多日：`{"days":["YYYYMMDD",...]}` 队列，策略每次跑队首一日，剩余写回文件。
- **不要**默认 `--allow-xtdata-download`（那是 miniQMT 旁路）。
- `--wait-today`：今日官方 tick **未开跑则立刻放行**；正在跑才等；`--skip-wait` 强制不等。
- 心跳：看 `data/results.json` mtime；领取：看 `manual_request.done.json` / run.log。

---

## 6. 性能与节流参数（易踩坑）

| 参数 | 位置 | 说明 |
|------|------|------|
| `FULL_POST_DOWNLOAD_SLEEP_SEC` | 日线 runner | 曾用 **1.5s** → 全市场空等约 **2.2h+**；已改 **0.2s**。过小可能抬高 fail，需盯 `manifest.fail_count`。 |
| `TICK_CHAIN_DELAY_SEC` | 日线 runner | 日线完成后延迟开 tick（默认 **900**）。改完需 deploy + 重跑策略。 |
| 池子盘中 sleep 3s/5s | 日线 runner | 与盘后全市场增量不是一路，勿混改。 |

日线 CSV 体积不大；「高负载」更多来自 **同进程连续狂下行情 + 内存占用（投研端十余 GB 常见）**，不全是日线文件本身。

---

## 7. 避坑清单

1. **不要对 Mini 再 `xtdata.connect()`**  
   builtin / 岳教授增量应读本地缓存；全历史才用大 QMT 的 xtdata。

2. **主程序盯市/均线优先 `daily_cache`**  
   硬连 xtquant 会在大 QMT 未开或通道忙时刷屏失败。

3. **改策略必须 sync → deploy → 停止再运行**  
   仅「重启」有时不重载脚本、不跑 `init`。

4. **QMT 内 `print` 常全缓冲**  
   启动日志需 `flush=True`，否则像「重启无任何输出」。看模型交易策略输出面板。

5. **timed 等的是 manifest，不是「日线大概好了」**  
   `sync_trade_date` 必须是今天。

6. **补历史 tick 不要与当日全 A tick 抢通道**  
   盘中今日未开跑可补；盘后今日正在跑应用 `--wait-today` 或等 done。

7. **完成标记**  
   当日正式：`_full_sync_done.json`。补数脚本勿再「无 done 就永久死等」——未开跑应放行。

8. **内存**  
   全 A tick 时 `XtItClient` 十几 GB 正常；32G 机器易顶到 27G+。必要时等落盘结束或重启投研端再补历史。

9. **Numpy 双 OpenBLAS 警告**  
   `loaded more than 1 DLL from .libs` 多为残留 DLL，警告级，一般可忽略。

10. **编码**  
    仓库 UTF-8、`qmt_builtin` 根目录 GBK；混存会导致 QMT 内乱码或语法错。

11. **账户 / 下单**  
    builtin 下单与回写走规则文件 + `results.json`；图表侧勿再假设 Mini `xt_trader` 通道始终可用。

---

## 8. 运维日常检查

- **日线**：`data/daily_cache/manifest.json` 的 `sync_trade_date` / `status` / `ok_count` / `fail_count` / `finished_at`
- **Tick 当日**：`data/ticks/{YYYYMMDD}/_full_sync_done.json` 与 parquet 数量（全 A 约五千级）
- **岳教授**：`logs/fillt0_YYYYMMDD.log`；汇总 `共享文件夹/T+0/0Summary_YYYYMMDD.xlsx`
- **策略版本日志**：`[ant] init begin`、`[daily_sync] timer registered`、`tick pipeline scheduled in 900s`
- **部署路径**：`config.ini` → `[qmt_builtin] qmt_python_dir`

---

## 9. 建议保留的临时/工具脚本

| 脚本 | 用途 | 备注 |
|------|------|------|
| `tools/backfill_tick_history.py` | 补近 1 个月缺失全 A tick | 补完可删或归档 |
| `tools/sync_qmt_gbk.py` / `deploy_to_qmt.py` | 日常发布 | 长期保留 |
| `wait_daily_cache_ready.py` | 盘后批跑等日线 | 与 timed 语义类似 |

---

## 10. 移植验收可对照

- [ ] `qmt_mode=builtin`，大 QMT 模型交易「蚂蚁量化规则」常开  
- [ ] 15:35 后 manifest 今日 `completed`  
- [ ] 延迟后 tick 落盘，`_full_sync_done.json` 出现，parquet 覆盖接近全市场  
- [ ] 盘后量能能读本地 tick（含盘后时段）  
- [ ] 岳教授 15:50 timed：等 manifest → fillt0（cache 命中日志）→ summary → Server酱  
- [ ] 新建票全历史在大 QMT 开启时可拉通  
- [ ] 改参数/策略后走完整 deploy，且 `init` 日志可见  

---

## 11. 一句话收束

**Mini 时代：程序连 Mini 要数据、要下单。**  
**现在：大 QMT 负责行情、执行与全市场落盘；其它程序读盘、写规则、做批处理——并在日线/tick/填表之间用 manifest 与错峰把节奏对齐。**
