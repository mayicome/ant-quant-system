# 主力行为分析性能优化建议

## 当前计算流程

### 1. 核心流程（multi_port_web.py 4388-4411行）
```python
# 每个comprehensive函数都会：
high_level_distribution_analysis = analyze_high_level_distribution_comprehensive()  # 调用3个formula
low_level_accumulation_analysis = analyze_low_level_accumulation_comprehensive()    # 调用3个formula
main_force_lift_analysis = analyze_main_force_lift_comprehensive()                 # 调用3个formula
main_force_wash_analysis = analyze_main_force_wash_comprehensive()                # 调用3个formula
main_force_sweep_analysis = analyze_main_force_sweep_comprehensive()              # 调用3个formula
```

**总计：5个分析 × 3个formula = 15个公式函数**

## 性能瓶颈分析

### 1. 重复的日K线数据计算（15次）
每个formula1函数都重复计算：
- MA5, MA10, MA20等移动平均线
- volume_ma5成交量移动平均
- daily_return收益率
- daily_amplitude振幅
- recent_60d, recent_30d等切片操作

**优化建议：** 在comprehensive函数中统一计算一次，传递给各formula函数

### 2. 重复的Tick数据分组（多次）
每个formula2/formula3都重复：
- 按分钟分组（1分钟、30秒）
- 提取bidVol、askVol数组
- 转换index类型
- groupby操作

**优化建议：** 预先对tick_data进行预处理，生成分钟级、30秒级的数据

### 3. 重复的数组提取（数十次）
每个函数都要提取bidVol、askVol数组：
```python
def extract_bid_vol(row):
    bid_vol = row.get('bidVol', [])
    if isinstance(bid_vol, list) and len(bid_vol) >= 3:
        return bid_vol[0], bid_vol[1], bid_vol[2]
    ...
```

**优化建议：** 在数据预处理阶段统一提取，避免重复计算

### 4. 重复的index类型检查（多次）
```python
if not isinstance(tick_data.index, pd.DatetimeIndex):
    tick_data.index = pd.to_datetime(tick_data.index)
```

**优化建议：** 在数据加载时统一处理

## 优化方案

### 方案1：添加预处理层（推荐）
在comprehensive函数中添加通用预处理：
```python
def _preprocess_daily_data(daily_data, analysis_date):
    """预处理日K线数据 - 统一计算一次"""
    if 'ma5' not in daily_data.columns:
        daily_data['ma5'] = daily_data['close'].rolling(window=5).mean()
        daily_data['ma10'] = daily_data['close'].rolling(window=10).mean()
        daily_data['ma20'] = daily_data['close'].rolling(window=20).mean()
        daily_data['volume_ma5'] = daily_data['volume'].rolling(window=5).mean()
        daily_data['daily_return'] = daily_data['close'].pct_change()
        daily_data['daily_amplitude'] = (daily_data['high'] - daily_data['low']) / daily_data['close']
    return daily_data

def _preprocess_tick_data(tick_data):
    """预处理Tick数据 - 统一处理一次"""
    # 统一提取数组
    if 'ask1_vol' not in tick_data.columns:
        tick_data['ask1_vol'] = tick_data['askVol'].apply(lambda x: x[0] if isinstance(x, list) and len(x) > 0 else 0)
        tick_data['ask2_vol'] = tick_data['askVol'].apply(lambda x: x[1] if isinstance(x, list) and len(x) > 1 else 0)
        # ... 类似处理bidVol等
```

### 方案2：并行计算各formula（可选）
使用multiprocessing并行计算5个comprehensive函数（需要处理数据共享问题）

## 预估优化效果

- **当前耗时**：5个分析 × 平均0.5秒 = **2.5秒**
- **优化后耗时**：预处理0.5秒 + 15个formula × 0.3秒 = **5秒**（可能慢）
- **最佳方案**：预处理0.2秒 + 公式优化0.1秒 × 15 = **1.7秒**

**预期提升：** ~32%的性能提升

## 建议

1. **短期优化**：在comprehensive函数中添加预处理，避免重复计算技术指标
2. **中期优化**：重构数组提取逻辑，统一预处理
3. **长期优化**：考虑并行计算或缓存预处理结果

