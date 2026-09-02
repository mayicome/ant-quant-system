# 市场行情日度描绘（本地分析）

与 COS 智能体包无关。

## 输出文件

| 文件 | 内容 | 数据源 |
|------|------|--------|
| `data/market_regime/backdrop_daily.csv` | 人工趋势底色（可先于宽度导出） | 中证全指日历 + `backdrop_manual_ranges` |
| `data/market_regime/pulse_daily.csv` | 两年交易日脉冲形态（含诊断列） | **`data/daily_full`** 宽度 |
| `data/market_regime/market_regime_daily.csv` | 完整宽度 + 标签 | 同上 |

## 口径

- 宽度：沪深 A 股；剔 ST、停牌；新股按本地日线根数 &lt; 60 剔除
- 个股日线：**用 `daily_full`，不用残缺的 `daily_cache`**
- 价格：仅中证全指 **000985** 收盘价（禁止上证综指）
- 趋势底色（因子分组）：当前为 **人工区间**（`backdrop_mode=manual`，见 `backdrop_manual_ranges`）；亦可切回 `swing_breakout` 自动高低点突破
- 指标：ADR / UDR / UDV / TRIN 日值 + ma5 + ma10（宽度与脉冲）
- 标签：`(趋势底色，脉冲形态) +【背离】` → `label_zh`  
  （短期情绪 ADR+TRIN 并入脉冲；`sentiment` 列仅诊断用）


## 命令

```bash
python tools/fetch_csi_all_share_daily.py
# 底色（不依赖个股日线）
python tools/export_backdrop_manual_csv.py --from-date 2025-01-01
# 脉冲 / 完整行情：须等 daily_full 补齐
python tools/export_pulse_daily_csv.py --from-date 2025-01-01
python tools/export_market_regime_to_csv.py --from-date 2025-01-01
# 日更增量（已挂在 run_all_if_trading_day.py 末尾）
python tools/export_market_regime_to_csv.py --incremental
```

规则：`config/market_regime_rules.json`  
指数缓存：`data/index_cache/000985.SH.csv`

下游按 `trade_date` join 其它技术因子表，再按 `backdrop` / `pulse` / `label_zh` 分组看暴露。

## 可视化

```bash
python tools/plot_market_regime.py
python tools/plot_index_cut_chart.py --year 2025 --overlay-backdrop
```

输出：`data/market_regime/market_regime_chart.png`（上证指数、涨跌家数、底色/脉冲色带；▼顶背离 ▲底背离）。
