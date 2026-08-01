import os
import pandas as pd
from datetime import datetime, timedelta
from PyQt5.QtWidgets import QTableWidgetItem, QProgressBar, QWidget, QHBoxLayout, QMenu, QLabel, QSizePolicy
from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QPainter, QColor
from utils.logger import Logger


class PositionBarWidget(QWidget):
    """100% 灰色框固定宽度，框内从左对齐绘制绿(可用)/红(不可用)，各行总宽一致便于百分比对齐"""
    def __init__(self, can_use_volume=0, total_volume=0, total_ratio_pct=0, full_width=0, parent=None):
        super().__init__(parent)
        self.can_use = max(0, int(can_use_volume))
        self.total_vol = max(0, int(total_volume))
        self.total_ratio_pct = max(0.0, min(100.0, float(total_ratio_pct)))
        self.full_width = max(0, int(full_width))  # 100% 灰色框宽度，每行统一
        self.setMinimumHeight(20)
        self.setMinimumWidth(0)
        self.setFixedWidth(self.full_width)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

    def set_bar_data(self, can_use_volume, total_volume, total_ratio_pct, full_width):
        self.can_use = max(0, int(can_use_volume))
        self.total_vol = max(0, int(total_volume))
        self.total_ratio_pct = max(0.0, min(100.0, float(total_ratio_pct)))
        self.full_width = max(0, int(full_width))
        self.setFixedWidth(self.full_width)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        # 先画满灰色框（100% 参考）
        painter.fillRect(QRect(0, 0, w, h), QColor('#e0e0e0'))
        if self.total_vol <= 0:
            painter.end()
            return
        # 框内从左开始：有色部分总长 = 总仓位% * 框宽，其中绿=可用/持仓、红=不可用/持仓
        colored_w = int(w * self.total_ratio_pct / 100.0)
        if colored_w <= 0:
            painter.end()
            return
        frac = min(1.0, max(0.0, self.can_use / self.total_vol))
        green_w = int(colored_w * frac)
        red_w = colored_w - green_w
        if green_w > 0:
            painter.fillRect(QRect(0, 0, green_w, h), QColor('#4CAF50'))
        if red_w > 0:
            painter.fillRect(QRect(green_w, 0, red_w, h), QColor('#f44336'))
        painter.end()


class PositionManager:
    """持仓管理模块"""
    
    def __init__(self, qmt_adapter=None, main_window_ext=None):
        self.logger = Logger()
        self.positions = {}
        self.saved_stocks = set()  # 添加已保存股票的集合
        self.current_date = None   # 添加当前日期记录
        self.qmt_adapter = qmt_adapter
        self.main_window_ext = main_window_ext
        
    def setup_position_table(self, table):
        """设置持仓表格的基本属性"""
        # 设置表头
        headers = ['仓位', '代码', '名称', '持仓', '可用', '摊薄成本', '市值']
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        
        # 设置表格属性
        table.setShowGrid(True)  # 显示网格线
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(table.NoEditTriggers)
        
        # 设置表格选择模式：点击任意单元格选中整行
        table.setSelectionBehavior(table.SelectRows)
        table.setSelectionMode(table.SingleSelection)
        
        # 取消右键菜单（已禁用）
        # table.setContextMenuPolicy(Qt.CustomContextMenu)
        # table.customContextMenuRequested.connect(lambda pos: self.show_position_context_menu(table, pos))
        
        # 设置双击事件
        table.itemDoubleClicked.connect(self.handle_position_double_click)
        
        # 设置列宽 - 第一列和最后一列可拉伸，中间列固定宽度
        # 固定宽度的列不会被压缩，拉伸列会随窗口大小调整
        header = table.horizontalHeader()
        
        # 设置全局最小列宽为100
        header.setMinimumSectionSize(100)
        
        # 第一列（仓位）使用拉伸模式，最小宽度为150像素
        header.setSectionResizeMode(0, header.Stretch)
        table.setColumnWidth(0, 150)
        
        # 中间5列使用固定宽度，不会被压缩
        column_widths = [100, 120, 100, 100, 120]  # 代码、名称、持仓、可用、摊薄成本
        for i, width in enumerate(column_widths, start=1):  # 从索引1开始
            header.setSectionResizeMode(i, header.Fixed)
            table.setColumnWidth(i, width)
        
        # 最后一列（市值）使用拉伸模式，最小宽度150
        last_col = len(column_widths) + 1  # 市值列索引
        header.setSectionResizeMode(last_col, header.Stretch)
        table.setColumnWidth(last_col, 150)
        
        # 保存市值列索引和最小宽度到表格对象
        table._market_value_col = last_col
        table._market_value_min_width = 150
        
        # 重写resizeEvent来确保市值列的最小宽度
        original_resize_event = table.resizeEvent
        def custom_resize_event(event):
            original_resize_event(event)
            # 在resize后检查市值列宽度，如果小于150则调整
            if hasattr(table, '_market_value_col'):
                current_width = table.columnWidth(table._market_value_col)
                if current_width < table._market_value_min_width:
                    # 临时改为Fixed模式设置最小宽度，然后改回Stretch
                    header.setSectionResizeMode(table._market_value_col, header.Fixed)
                    table.setColumnWidth(table._market_value_col, table._market_value_min_width)
                    header.setSectionResizeMode(table._market_value_col, header.Stretch)
        
        table.resizeEvent = custom_resize_event
        
        # 禁用默认的最后一列拉伸（我们已经手动设置了）
        header.setStretchLastSection(False)
        
        # 设置样式
        table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #CCCCCC;
                background-color: white;
                font-family: "Microsoft YaHei";
                font-size: 12pt;
                gridline-color: #CCCCCC;
            }
            QHeaderView::section {
                background-color: #F5F5F5;
                padding: 5px;
                border: none;
                font-weight: bold;
                border-bottom: 1px solid #CCCCCC;
                font-family: "Microsoft YaHei";
                font-size: 12pt;
            }
            QTableWidget::item {
                padding: 5px;
            }
        """)
    
    def update_position_list(self, table, asset, positions):
        """更新持仓列表"""
        try:
            # 确保股票信息管理器已加载完成
            try:
                from utils.stock_info_manager import get_stock_info_manager
                stock_manager = get_stock_info_manager()
                if not stock_manager._stock_info_cache:
                    self.logger.warning("股票信息管理器尚未加载完成，等待加载...")
                    # 等待一小段时间让股票信息管理器加载完成
                    import time
                    time.sleep(0.1)
            except Exception as e:
                self.logger.warning(f"股票信息管理器检查失败: {e}")
            
            # 保存数据
            self.positions = positions
            
            # 将字典转换为列表（如果是字典的话）
            positions_list = []
            if isinstance(positions, dict):
                positions_list = [positions[code] for code in positions]
            else:
                positions_list = positions

            # 设置表格行数
            table.setRowCount(len(positions_list))
            
            # 100% 灰框宽度：在循环外只算一次，保证每行一致
            col_w = max(50, table.columnWidth(0))
            full_width = max(col_w, 200)
            
            # 填充数据
            for row, stock_data in enumerate(positions_list):
                # 计算总仓位、可用仓位比例（占账户总资产%），避免除以0
                if asset and asset.get('total_asset', 0) > 0:
                    total_ratio = stock_data['volume'] * stock_data['open_price'] / asset['total_asset'] * 100
                    available_ratio = stock_data['can_use_volume'] * stock_data['open_price'] / asset['total_asset'] * 100
                    total_ratio_int = int(total_ratio)
                    available_ratio_int = int(available_ratio)
                else:
                    total_ratio = 0.0
                    available_ratio = 0.0
                    total_ratio_int = 0
                    available_ratio_int = 0
                
                # 仓位条：统一宽度的 100% 灰框（full_width 在循环外已统一），框内左对齐画绿/红
                vol = int(stock_data.get('volume', 0) or 0)
                can_use = int(stock_data.get('can_use_volume', 0) or 0)
                bar_widget = PositionBarWidget(
                    can_use_volume=can_use,
                    total_volume=vol if vol > 0 else 1,
                    total_ratio_pct=total_ratio,
                    full_width=full_width
                )
                bar_widget.setStyleSheet("border: 1px solid #ccc; border-radius: 3px;")
                bar_container = QWidget()
                bar_container.setFixedWidth(full_width + 10)  # 固定宽度，避免因右侧百分比字数不同被压缩成不同宽
                bar_layout = QHBoxLayout(bar_container)
                bar_layout.setContentsMargins(5, 0, 5, 0)
                bar_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                bar_layout.addWidget(bar_widget)

                percent_label = QLabel(f"{total_ratio_int}%")
                percent_label.setStyleSheet("font-size: 12pt;")
                position_container = QWidget()
                position_layout = QHBoxLayout(position_container)
                position_layout.setContentsMargins(0, 0, 5, 0)
                position_layout.setSpacing(8)
                position_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                position_layout.addWidget(bar_container)
                position_layout.addWidget(percent_label)
                
                # 设置仓位条到表格
                table.setCellWidget(row, 0, position_container)
                
                # 填充其他数据
                # 去掉股票代码后缀，只显示6位数字
                raw_code = str(stock_data.get('stock_code') or '')
                stock_code_display = raw_code.split('.')[0] if '.' in raw_code else raw_code
                stock_name = str(stock_data.get('stock_name') or '').strip()
                if not stock_name or stock_name in ('未知名称', '未知'):
                    try:
                        from utils.stock_info_manager import get_stock_name
                        stock_name = get_stock_name(stock_code_display) or ''
                        if stock_name in ('未知名称', '未知'):
                            stock_name = ''
                    except Exception:
                        stock_name = stock_name if stock_name not in ('未知名称', '未知') else ''
                data = [
                    stock_code_display,
                    stock_name,
                    str(stock_data['volume']),
                    str(stock_data['can_use_volume']),
                    f"{stock_data['open_price']:.3f}",  # 修改这里，成本保留3位小数
                    f"{stock_data['market_value']:.2f}"
                ]
                
                # 添加数据到表格
                for col, value in enumerate(data):
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    table.setItem(row, col + 1, item)
            
        except Exception as e:
            self.logger.error(f"更新持仓列表失败：{str(e)}")

    def save_daily_positions(self, positions):
        """保存每日初始持仓数据"""
        try:
            # 获取当前日期
            today = datetime.now().strftime('%Y-%m-%d')
            
            # 如果是新的一天，重置已保存股票集合
            if self.current_date != today:
                self.saved_stocks.clear()
                self.current_date = today
            
            # 构建保存路径
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(current_dir, 'data', 'positions')
            os.makedirs(data_dir, exist_ok=True)
            
            # 构建文件名
            file_name = f'positions_{today}.csv'
            file_path = os.path.join(data_dir, file_name)
            
            # 读取现有数据（如果文件存在）
            existing_data = []
            if os.path.exists(file_path):
                try:
                    df = pd.read_csv(file_path)
                    existing_data = df.to_dict('records')
                    # 更新已保存股票集合
                    self.saved_stocks.update(pos['stock_code'] for pos in existing_data)
                except Exception as e:
                    self.logger.error(f"读取现有持仓数据失败：{str(e)}")
            
            # 将持仓数据转换为列表
            positions_list = []
            if isinstance(positions, dict):
                positions_list = [positions[code] for code in positions]
            else:
                positions_list = positions
            
            # 只添加新的股票数据
            new_positions = []
            for pos in positions_list:
                if pos['stock_code'] not in self.saved_stocks:
                    pos['date'] = today
                    new_positions.append(pos)
                    self.saved_stocks.add(pos['stock_code'])
            
            # 合并现有数据和新数据
            all_positions = existing_data + new_positions
            
            # 只在有新数据时保存
            if new_positions:
                df = pd.DataFrame(all_positions)
                df.to_csv(file_path, index=False, encoding='utf-8')
                self.logger.info(f"保存{today}新增持仓数据成功，新增{len(new_positions)}只股票")
            
        except Exception as e:
            self.logger.error(f"保存每日持仓数据失败：{str(e)}")

    def get_previous_trading_day(self):
        """获取上一个交易日"""
        current_date = datetime.now()
        previous_date = current_date
        
        while True:
            previous_date = previous_date - timedelta(days=1)
            if self._is_tradeday(previous_date):
                return previous_date.strftime('%Y-%m-%d')

    def _is_tradeday(self, date):
        """判断是否为交易日（简化版本）"""
        # 这里使用简化的交易日判断逻辑
        # 实际应用中可能需要更复杂的交易日历
        weekday = date.weekday()
        # 周一到周五为交易日
        return weekday < 5

    def determine_buy_date(self, stock_data, task_manager):
        """确定股票的买入日期"""
        stock_code = stock_data['stock_code']
        
        # 查找该股票的任务，支持夜市任务的时间戳格式
        for task_id, task in task_manager.tasks.items():
            if task.get('stock_code') == stock_code:
                return task.get('buy_date')
        
        # 对于新增的股票，直接使用当天日期
        return datetime.now().strftime('%Y-%m-%d')

    def get_position_data(self, stock_code):
        """获取指定股票的持仓数据"""
        return self.positions.get(stock_code, {})

    def get_all_positions(self):
        """获取所有持仓数据"""
        return self.positions

    def has_position(self, stock_code):
        """检查是否有指定股票的持仓"""
        return stock_code in self.positions and self.positions[stock_code].get('volume', 0) > 0

    def get_position_volume(self, stock_code):
        """获取指定股票的持仓数量"""
        position = self.positions.get(stock_code, {})
        return position.get('volume', 0)

    def get_available_volume(self, stock_code):
        """获取指定股票的可用数量"""
        position = self.positions.get(stock_code, {})
        return position.get('can_use_volume', 0)
    
    def show_position_context_menu(self, table, pos):
        """显示持仓表格的右键菜单"""
        try:
            
            # 获取点击位置对应的行
            item = table.itemAt(pos)
            if not item:
                return
            
            row = item.row()
            
            # 获取该行的股票代码
            stock_code_item = table.item(row, 1)  # 代码列是第1列
            if not stock_code_item:
                return
            
            stock_code = stock_code_item.text()
            
            # 创建右键菜单
            context_menu = QMenu(table)
            self._context_menu = context_menu  # 保存菜单引用
            
            # 显示菜单
            context_menu.exec_(table.mapToGlobal(pos))
            
        except Exception as e:
            self.logger.error(f"显示持仓右键菜单失败: {e}")
    
    def _close_menu_and_execute(self, menu, action_func):
        """关闭菜单并执行动作"""
        try:
            from PyQt5.QtCore import QTimer
            menu.close()
            # 使用QTimer延迟执行，确保菜单完全关闭
            QTimer.singleShot(10, action_func)
        except Exception as e:
            self.logger.error(f"关闭菜单并执行动作失败: {e}")
    
    def handle_position_double_click(self, item):
        """处理仓位表格双击事件"""
        try:
            # 获取点击的行
            table = item.tableWidget()
            row = item.row()
            
            # 获取该行的股票代码（代码列是第1列，索引从0开始）
            stock_code_item = table.item(row, 1)
            if not stock_code_item:
                return
            
            stock_code = stock_code_item.text().strip()
            if not stock_code:
                return
            
            # 统一股票代码格式：将6位数字转换为完整格式（带后缀），以便与任务中的格式一致
            # 持仓表格显示的是去掉后缀的代码（如 600000），但任务中存储的是完整格式（如 600000.SH）
            full_stock_code = stock_code
            if len(stock_code) == 6 and stock_code.isdigit() and '.' not in stock_code:
                # 如果是6位数字且没有后缀，补充后缀
                if stock_code.startswith(('0', '1', '3')):
                    full_stock_code = f"{stock_code}.SZ"
                elif stock_code.startswith(('5', '6')):
                    full_stock_code = f"{stock_code}.SH"
                elif stock_code.startswith(('4', '8')):
                    full_stock_code = f"{stock_code}.BJ"
            
            # 获取该行的股票名称（名称列是第2列，索引从0开始）
            stock_name_item = table.item(row, 2)
            stock_name = stock_name_item.text().strip() if stock_name_item else None
            
            # 如果表格中没有名称，尝试从仓位数据中获取
            if not stock_name or stock_name == "":
                position_data = self.get_position_data(full_stock_code)
                if not position_data:
                    # 如果完整格式找不到，尝试用原始格式
                    position_data = self.get_position_data(stock_code)
                stock_name = position_data.get('stock_name', '未知') if position_data else '未知'
            
            # 通过 main_window_ext 访问 tasks_charts_view 和 task_manager
            if not self.main_window_ext:
                self.logger.warning("main_window_ext 未设置，无法处理双击事件")
                return
            
            # 检查 tasks_charts_view 是否存在
            if not hasattr(self.main_window_ext, 'tasks_charts_view') or not self.main_window_ext.tasks_charts_view:
                self.logger.warning("tasks_charts_view 未初始化，无法处理双击事件")
                return
            
            tasks_charts_view = self.main_window_ext.tasks_charts_view
            task_manager = self.main_window_ext.task_manager
            
            # 检查该股票是否已有任务（需要同时检查完整格式和去掉后缀的格式）
            has_task = False
            existing_task_stock_code = None
            if task_manager and hasattr(task_manager, 'tasks'):
                for task_id, task in task_manager.tasks.items():
                    if isinstance(task, dict):
                        task_stock_code = task.get('stock_code', '')
                        # 比较完整格式
                        if task_stock_code == full_stock_code:
                            has_task = True
                            existing_task_stock_code = task_stock_code
                            break
                        # 比较去掉后缀的格式（兼容性检查）
                        task_code_no_suffix = task_stock_code.split('.')[0] if '.' in task_stock_code else task_stock_code
                        stock_code_no_suffix = full_stock_code.split('.')[0] if '.' in full_stock_code else full_stock_code
                        if task_code_no_suffix == stock_code_no_suffix and len(task_code_no_suffix) == 6:
                            has_task = True
                            existing_task_stock_code = task_stock_code
                            break
            
            if has_task:
                # 如果有任务，切换到1列视图并定位到该股票（使用任务中存储的完整格式）
                tasks_charts_view.switch_to_single_column_and_show_stock(existing_task_stock_code or full_stock_code)
            else:
                # 如果没有任务，创建新任务并切换到1列视图（使用完整格式）
                self._create_task_and_show(full_stock_code, stock_name, tasks_charts_view, task_manager)
                
        except Exception as e:
            self.logger.error(f"处理仓位双击事件失败: {e}", exc_info=True)
    
    def _create_task_and_show(self, stock_code, stock_name, tasks_charts_view, task_manager):
        """创建新任务并切换到1列视图显示
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称（从表格中获取，如果为None则尝试查询）
        """
        try:
            import uuid
            from datetime import datetime
            from PyQt5.QtWidgets import QMessageBox
            
            # 如果股票名称为空或未提供，尝试查询
            if not stock_name or stock_name == "未知" or stock_name == "":
                # 优先从仓位数据中获取
                position_data = self.get_position_data(stock_code)
                if position_data and position_data.get('stock_name'):
                    stock_name = position_data.get('stock_name')
                else:
                    # 如果仓位数据中也没有，则查询股票信息管理器
                    try:
                        from utils.stock_info_manager import get_stock_info_manager
                        stock_manager = get_stock_info_manager()
                        stock_info = stock_manager.get_stock_info(stock_code)
                        if stock_info and stock_info.get('stock_name'):
                            stock_name = stock_info.get('stock_name')
                        else:
                            stock_name = "未知"
                    except Exception as e:
                        self.logger.warning(f"获取股票名称失败: {e}")
                        stock_name = "未知"
            
            # 确保切换到1列布局
            if tasks_charts_view.columns != 1:
                tasks_charts_view.columns = 1
                if hasattr(tasks_charts_view, 'column_button_group'):
                    button = tasks_charts_view.column_button_group.button(1)
                    if button:
                        button.setChecked(True)
            
            # 创建新任务
            task_id = str(uuid.uuid4())
            
            # 计算新任务的order_index（插入到最前面）
            task_data = {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'init_volume': 0,
                'volume': 0,
                'init_cost': 0,
                'buy_date': datetime.now().strftime('%Y-%m-%d'),
                'hold_days': 0,
                'base_price': 0,
                'strategy': '规则任务',
                'status': '未运行',
                'task_id': task_id,
                'create_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'order_index': 0,  # 插入到最前面
                'params': {
                    'rules': [],
                    'up_threshold': 5.0,
                    'down_threshold': 3.0,
                    'sell_times': 3,
                    'clear_time': '00:00:00',
                    'cycle_times': 0
                }
            }
            
            # 添加到任务管理器
            task_manager.tasks[task_id] = task_data
            task_manager.task_params[task_id] = task_data['params']
            
            # 重新获取任务列表
            all_tasks_dict = task_manager.tasks
            current_tasks_list = list(all_tasks_dict.items())
            
            # 将新任务移到最前面
            new_task_item = (task_id, task_data)
            current_tasks_list = [(tid, t) for tid, t in current_tasks_list if tid != task_id]
            current_tasks_list.insert(0, new_task_item)
            
            # 更新所有任务的 order_index
            for idx, (tid, task) in enumerate(current_tasks_list):
                task['order_index'] = idx
            
            # 保存到文件
            task_manager.save_tasks([task for _, task in current_tasks_list])
            
            # 重新加载任务管理器中的任务
            task_manager.load_tasks()
            
            self.logger.info(f"从仓位管理创建任务: {stock_code}")
            
            # 重新加载图表视图（会按照保存的顺序加载）
            tasks_charts_view.current_page = 0  # 切换到第一页
            tasks_charts_view.load_tasks()
            
            # 延迟滚动到该图表（确保加载完成）
            from PyQt5.QtCore import QTimer
            def scroll_to_chart():
                if stock_code in tasks_charts_view.chart_widgets:
                    chart_data = tasks_charts_view.chart_widgets[stock_code]
                    chart_data['container'].raise_()
            QTimer.singleShot(200, scroll_to_chart)
            
        except Exception as e:
            self.logger.error(f"创建任务并显示失败: {e}", exc_info=True)