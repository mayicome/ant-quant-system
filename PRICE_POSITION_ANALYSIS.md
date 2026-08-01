# 股价位置分析模块

## 概述

股价位置分析模块是一个独立的分析工具，用于判断当前股价所处的位置（高位/低位/中位）。该模块已集成到主力行为分析中，显著提高了吸筹和出货判断的准确性。

## 功能特点

### 1. 多维度位置判断
- **历史价格分位数分析**：基于历史价格数据计算当前价格的分位数
- **近期价格趋势分析**：分析最近60天的价格趋势
- **支撑阻力位分析**：计算支撑位和阻力位
- **成交量加权价格分析**：考虑成交量的价格分析
- **移动平均线分析**：MA5、MA10、MA20均线分析
- **布林带分析**：基于布林带的位置判断

### 2. 综合评分机制
采用加权评分机制，综合考虑多个指标：
- 价格分位数分析 (权重: 30%)
- 近期趋势分析 (权重: 20%)
- 支撑阻力位分析 (权重: 20%)
- 成交量加权价格分析 (权重: 15%)
- 移动平均线分析 (权重: 10%)
- 布林带分析 (权重: 5%)

### 3. 置信度评估
为每个位置判断提供置信度评分（0-1），帮助评估判断的可靠性。

## 核心类

### PricePositionAnalyzer

主要的股价位置分析器类。

#### 初始化
```python
from core.price_position_analyzer import PricePositionAnalyzer

# 使用默认配置
analyzer = PricePositionAnalyzer()

# 使用自定义配置
config = {
    'position_lookback_days': 60,  # 回看天数
    'high_position_threshold': 0.8,  # 高位阈值
    'low_position_threshold': 0.2,   # 低位阈值
}
analyzer = PricePositionAnalyzer(config)
```

#### 主要方法

##### analyze_price_position()
分析股价所处位置。

```python
result = analyzer.analyze_price_position(
    stock_code="000001",
    current_price=10.5,
    historical_data=df,  # 可选，历史数据DataFrame
    analysis_date="2024-01-15"  # 可选，分析日期
)
```

返回结果格式：
```python
{
    'position': 'low',  # 'high', 'low', 'medium'
    'position_chinese': '低位',  # '高位', '低位', '中位'
    'confidence': 0.75,  # 置信度 0-1
    'indicators': {
        'price_percentile': {...},
        'recent_trend': {...},
        'support_resistance': {...},
        'volume_weighted_price': {...},
        'moving_averages': {...},
        'bollinger_bands': {...}
    },
    'current_price': 10.5,
    'analysis_date': '2024-01-15',
    'stock_code': '000001'
}
```

##### 位置判断方法
```python
# 判断是否为低位
is_low = analyzer.is_low_position(result)

# 判断是否为高位
is_high = analyzer.is_high_position(result)

# 判断是否为中位
is_medium = analyzer.is_medium_position(result)
```

## 集成到主力行为分析

### 改进前的问题
- 吸筹判断不考虑股价位置，可能在股价高位时误判为吸筹
- 出货判断不考虑股价位置，可能在股价低位时误判为出货
- 缺乏对股价相对位置的准确判断

### 改进后的逻辑

#### 吸筹判断
```python
# 只有股价在低位时的吸筹才判断为吸筹
if (not curr_data['is_limit_up'] and  # 排除涨停板情况
    is_low_level and  # 股价处于低位
    volume_pulse and  # 脉冲放量
    price_suppressed and  # 价格被压制
    buy_pressure_strong and  # 买盘压力强
    bid_order_size_strong):  # 买盘单量大
    action_type = "吸筹"
```

#### 出货判断
```python
# 只有股价在高位时的出货才判断为出货
elif (not curr_data['is_limit_up'] and  # 排除涨停板情况
      is_high_level and  # 股价处于高位
      price_change < -0.02 and  # 价格下跌超过2%
      volume_change > avg_volume * 3 and  # 成交量放大
      pressure_ratio < 0.5 and  # 卖盘压力大
      ask_vol_change > bid_vol_change * 2):  # 主动卖出
    action_type = "出货"
```

## 配置参数

### 默认配置
```python
default_config = {
    'position_lookback_days': 60,  # 回看天数，用于计算历史价格区间
    'high_position_threshold': 0.8,  # 高位阈值（80%分位数）
    'low_position_threshold': 0.2,   # 低位阈值（20%分位数）
    'recent_days_weight': 0.7,       # 近期数据权重
    'volume_weight': 0.3,            # 成交量权重
    'price_change_weight': 0.4,      # 价格变化权重
    'support_resistance_weight': 0.3  # 支撑阻力位权重
}
```

### 自定义配置
可以通过修改配置文件或在代码中传入自定义配置来调整分析参数。

## 使用示例

### 基本使用
```python
from core.price_position_analyzer import PricePositionAnalyzer

# 初始化分析器
analyzer = PricePositionAnalyzer()

# 分析股价位置
result = analyzer.analyze_price_position(
    stock_code="000001",
    current_price=10.5
)

print(f"股价位置: {result['position_chinese']}")
print(f"置信度: {result['confidence']:.2f}")
```

### 在主力行为分析中使用
```python
from core.stock_analyzer import StockAnalyzer

# 初始化股票分析器
stock_analyzer = StockAnalyzer()

# 分析股票（自动包含股价位置判断）
analysis_result = stock_analyzer.analyze_stock("000001", analysis_date)

# 获取主力行为分析结果
main_force_analysis = analysis_result['main_force_analysis']

# 查看股价位置信息
if 'price_position' in main_force_analysis:
    position_info = main_force_analysis['price_position']
    print(f"股价位置: {position_info['position_chinese']}")
```

## 测试

### 运行测试脚本
```bash
python test_price_position_analyzer.py
```

### 运行集成示例
```bash
python example_price_position_integration.py
```

## 优势

1. **提高准确性**：通过股价位置判断，显著提高吸筹和出货判断的准确性
2. **避免误判**：避免在错误位置判断主力行为的误判
3. **多维度分析**：综合考虑多个技术指标，提供更全面的位置判断
4. **置信度评估**：提供置信度评分，帮助评估判断的可靠性
5. **易于集成**：模块化设计，易于集成到现有系统中
6. **可配置性**：支持自定义配置参数，适应不同的分析需求

## 注意事项

1. **历史数据要求**：需要足够的历史数据来支持位置分析（建议至少60天）
2. **数据质量**：历史数据的质量直接影响位置判断的准确性
3. **市场环境**：在不同市场环境下，可能需要调整配置参数
4. **实时性**：位置分析基于历史数据，需要定期更新以保持准确性

## 更新日志

- **v1.0.0**: 初始版本，实现基本的股价位置分析功能
- **v1.1.0**: 集成到主力行为分析中，提高判断准确性
- **v1.2.0**: 添加多维度指标分析和置信度评估
