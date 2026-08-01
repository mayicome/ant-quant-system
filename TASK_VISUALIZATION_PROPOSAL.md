# 任务列表图形化改进方案

## 需求背景
当前的GUI程序任务列表是表格形式，不够直观。想要：
1. 用图形化形式显示每只股票的设置好的买点、买量、卖点、卖量
2. 显示分时图，清楚看到离买卖点的距离有多远
3. 可以方便地移动买卖点的横线来修改它们

## 实现方案

### 1. 核心组件：StockChartWidget

已经创建了 `ui/stock_chart_widget.py`，包含以下功能：

#### 主要特性：
- **分时图显示**：支持实时价格线和成交量柱状图
- **买卖点横线**：
  - 买点用绿色横线表示
  - 卖点用红色横线表示
  - 横线可以拖动修改价格
- **距离显示**：实时显示当前价格距离买卖点的百分比
- **价格控件**：使用 `QDoubleSpinBox` 精确设置买卖点和数量
- **实时更新**：支持实时价格数据更新

#### 使用方法：
```python
from ui.stock_chart_widget import StockChartWidget

# 创建图表组件
chart = StockChartWidget("000001", "平安银行")
chart.set_prices(buy_price=10.5, sell_price=11.5, buy_volume=1000, sell_volume=1000)

# 更新实时数据
chart.update_price_data(time_data, price_data, volume_data)

# 连接价格改变信号
chart.price_changed.connect(on_price_changed)
```

### 2. 集成到主界面

有两个集成方案：

#### 方案A：替换现有任务列表（完整重构）
- 将 `tableWidget_2` 替换为可滚动的图表区域
- 每个任务显示为一个 StockChartWidget
- 适合：想要完全图形化的界面

#### 方案B：双击任务行打开图表窗口（推荐）
- 保持现有任务列表不变
- 双击任务行，弹出图表窗口
- 图表窗口显示分时图、买卖点等
- 适合：渐进式改进，不破坏现有功能

### 3. 实现步骤

#### 步骤1：创建图表窗口
在 `ui/main_window_ext.py` 中添加图表窗口：

```python
def show_chart_window(self, stock_code):
    """显示股票图表窗口"""
    from ui.stock_chart_widget import StockChartWidget
    
    # 获取任务信息
    task = self.task_manager.get_task_by_stock_code(stock_code)
    
    # 创建图表
    chart = StockChartWidget(stock_code, task['stock_name'])
    chart.set_prices(
        buy_price=task['params'].get('buy_price', 0),
        sell_price=task['params'].get('sell_price', 0),
        buy_volume=task['params'].get('buy_volume', 0),
        sell_volume=task['params'].get('sell_volume', 0)
    )
    
    # 连接价格改变信号
    chart.price_changed.connect(self.on_chart_price_changed)
    
    # 创建独立窗口
    window = QDialog(self.window)
    window.setWindowTitle(f"{task['stock_name']} - 任务监控")
    layout = QVBoxLayout()
    layout.addWidget(chart)
    window.setLayout(layout)
    window.resize(1200, 800)
    window.exec_()
```

#### 步骤2：连接实时数据更新
在 `QMTManager` 的回调中更新图表数据：

```python
def update_stock_chart(self, stock_code, price_data):
    """更新股票图表"""
    if hasattr(self, 'chart_windows') and stock_code in self.chart_windows:
        chart = self.chart_windows[stock_code]
        chart.update_current_price(price_data.get('last_price', 0))
```

#### 步骤3：添加双击事件
在 `setup_task_list` 中添加：

```python
self.tableWidget_2.cellDoubleClicked.connect(self.on_task_double_clicked)

def on_task_double_clicked(self, row, col):
    """双击任务行，打开图表"""
    stock_code_item = self.tableWidget_2.item(row, 0)
    if stock_code_item:
        stock_code = stock_code_item.text()
        self.show_chart_window(stock_code)
```

### 4. 数据流设计

```
任务管理器 → 实时价格数据
    ↓
QMT适配器
    ↓
主窗口（任务列表）
    ↓
双击任务
    ↓
图表窗口（StockChartWidget）
    ├─ 分时图（价格线 + 成交量）
    ├─ 买卖点横线（可拖动）
    ├─ 距离显示
    └─ 价格控件（精确设置）
```

### 5. 优点

1. **直观性**：图形化显示，一目了然
2. **交互性**：拖动横线即可修改买卖点
3. **实时性**：实时更新价格和距离
4. **兼容性**：不影响现有功能
5. **扩展性**：易于添加新功能（如指标线、K线图等）

### 6. 后续优化方向

- 添加多个时间周期（1分钟、5分钟、日K线等）
- 添加技术指标（MA、MACD等）
- 添加买卖点提示（接近目标价格时提醒）
- 添加策略回测可视化
- 支持多股票对比

## 总结

这个方案通过创建独立的 `StockChartWidget` 组件，实现了图形化的任务监控界面。用户可以通过直观的图表查看每只股票的实时情况，并通过拖动横线方便地调整买卖点设置。

建议先采用方案B（双击打开图表窗口），这样可以在保持现有系统稳定的同时，为用户提供更好的可视化体验。

