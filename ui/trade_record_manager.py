import os
import pandas as pd
from datetime import datetime, timedelta
from PyQt5.QtWidgets import (QTableWidgetItem, QPushButton, QWidget, QHBoxLayout, 
                             QVBoxLayout, QLabel, QMessageBox, QSizePolicy)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QColor, QBrush
from utils.logger import Logger
from my_function import get_stock_name, load_all_stocks_info
import traceback

# 订单列表操作按钮统一字体（与表格 12pt 雅黑一致，避免「已结束」变小字）
_OP_BTN_FONT = "font-family: 'Microsoft YaHei'; font-size: 11pt; font-weight: bold;"

# 订单状态文字色：已报蓝 / 已成黑 / 已撤灰
_ORDER_STATUS_COLOR_REPORTED = QColor(21, 101, 192)   # 已报
_ORDER_STATUS_COLOR_FILLED = QColor(33, 33, 33)       # 已成（近黑，保证可读）
_ORDER_STATUS_COLOR_CANCELLED = QColor(158, 158, 158) # 已撤


def _order_status_text_color(status_text: str) -> QColor:
    """按委托状态返回文字颜色。"""
    s = str(status_text or "")
    if any(k in s for k in ("已撤", "部撤", "废单")):
        return _ORDER_STATUS_COLOR_CANCELLED
    if "已成" in s:
        return _ORDER_STATUS_COLOR_FILLED
    if any(k in s for k in ("已报", "未报", "待报", "已确认", "部成")):
        return _ORDER_STATUS_COLOR_REPORTED
    return _ORDER_STATUS_COLOR_FILLED


def _op_btn_style(bg: str, hover: str = "", pressed: str = "") -> str:
    parts = [
        "QPushButton {",
        f"  background-color: {bg};",
        "  color: white;",
        f"  border: 1px solid {bg};",
        "  border-radius: 3px;",
        f"  {_OP_BTN_FONT}",
        "  padding: 0px;",
        "}",
    ]
    if hover:
        parts.extend(
            [
                "QPushButton:hover {",
                f"  background-color: {hover};",
                f"  border: 1px solid {hover};",
                "}",
            ]
        )
    if pressed:
        parts.extend(
            [
                "QPushButton:pressed {",
                f"  background-color: {pressed};",
                f"  border: 1px solid {pressed};",
                "}",
            ]
        )
    parts.extend(
        [
            "QPushButton:disabled {",
            "  background-color: #9E9E9E;",
            "  border: 1px solid #9E9E9E;",
            "  color: white;",
            "}",
        ]
    )
    return "\n".join(parts)


_STYLE_CANCEL = _op_btn_style("#FF5722", "#E64A19", "#D84315")
_STYLE_MONITOR = _op_btn_style("#4CAF50", "#45A049", "#3D8B40")
_STYLE_MONITORING = _op_btn_style("#FF9800", "#F57C00", "#E65100")
_STYLE_ENDED = _op_btn_style("#9E9E9E")

class TradeRecordManager:
    """交易记录管理模块"""
    
    def __init__(self):
        self.logger = Logger()
        self.order_monitors = {}  # 订单监控字典
        self.qmt_adapter = None
        
    def setup_trade_record_table(self, table):
        """设置交易记录表格"""
        # 设置表格样式，添加标题和边框
        table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #CCCCCC;
                background-color: white;
                font-family: "Microsoft YaHei";
                font-size: 12pt;
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
        
        # 设置表头
        headers = ['合同号', '代码', '名称', '委托时间', '委托/均价', 
                  '数量/成交', '状态', '说明', '操作']
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        
        # 设置表格属性
        table.setShowGrid(True)  # 显示网格线
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(table.NoEditTriggers)
        
        # 设置列宽：名称加宽可拖；「说明」Stretch 铺满剩余宽度（平铺）
        header = table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(48)

        for i in range(len(headers)):
            if i == 8:  # 操作列
                header.setSectionResizeMode(i, header.Fixed)
                table.setColumnWidth(i, 160)
            elif i == 7:  # 说明：拉伸填满，表格横向平铺
                header.setSectionResizeMode(i, header.Stretch)
            else:
                header.setSectionResizeMode(i, header.Interactive)
                if i == 0:  # 合同号
                    table.setColumnWidth(i, 90)
                elif i == 1:  # 代码
                    table.setColumnWidth(i, 100)
                elif i == 2:  # 名称（ETF 全称较长）
                    table.setColumnWidth(i, 200)
                elif i == 3:  # 委托时间
                    table.setColumnWidth(i, 90)
                elif i == 4:  # 委托/均价
                    table.setColumnWidth(i, 120)
                elif i == 5:  # 数量/成交
                    table.setColumnWidth(i, 110)
                elif i == 6:  # 状态
                    table.setColumnWidth(i, 130)

        # 设置表格属性
        table.setShowGrid(True)  # 显示网格线
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(table.NoEditTriggers)
        # 截断时悬停仍可看全名
        table.setTextElideMode(Qt.ElideRight)
        table.setWordWrap(False)
        # 行高固定，操作按钮才能在单元格里垂直居中
        table.verticalHeader().setDefaultSectionSize(36)
        table.verticalHeader().setMinimumSectionSize(32)
    
    def add_trade_record(self, table, stock_code, trade_info, qmt_adapter=None, ui_callback=None):
        """添加或更新交易记录（只处理真实订单）"""
        try:
            
            # 只处理真实订单（查询得到的订单信息而不是委托回报的订单信息）
            if not trade_info.get('is_real_order', False):
                return

            # UI 兜底：跨日委托不进表
            # - session_strict=True（大 QMT 柜台快照）：无完整日期也丢弃
            # - 默认（MiniQMT 当日 query）：仅 HH:MM:SS 时放行
            try:
                from utils.order_session import is_current_session_order

                strict = bool(trade_info.get("session_strict"))
                if not is_current_session_order(
                    order_time=trade_info.get('order_time'),
                    at=trade_info.get('at') or trade_info.get('order_at'),
                    order_at=trade_info.get('order_at'),
                    allow_undated=not strict,
                    use_updated_at=False,
                ):
                    return
            except Exception:
                pass
            
            # 检查订单号
            if not trade_info.get('order_id'):
                self.logger.warning(f"无效订单号，跳过: {trade_info}")
                return
            
            # 检查股票代码
            if not stock_code:
                self.logger.warning(f"无效股票代码，跳过: {trade_info}")
                return
            
            # 检查表格对象
            if not table:
                self.logger.error("表格对象为空")
                return
            
            # 1. 准备基础数据
            strategy_name = trade_info.get('strategy_name', '') if trade_info else ''
            if not strategy_name:
                strategy_name = '真实订单'
            
            # 简化时间处理
            order_time = trade_info.get('order_time', '')
            
            if isinstance(order_time, datetime):
                order_time = order_time.strftime('%H:%M:%S')
            elif isinstance(order_time, (int, float)):
                dt = datetime.fromtimestamp(order_time / 1000 if order_time > 1000000000000 else order_time)
                order_time = dt.strftime('%H:%M:%S')
            else:
                order_time = str(order_time) if order_time else ''
            
            price = trade_info.get('price', 0)
            
            # 获取股票名称：优先订单自带，否则走全局管理器（CSV + QMT InstrumentName）
            stock_name = ""
            try:
                stock_name = str(trade_info.get("stock_name") or "").strip()
            except Exception:
                stock_name = ""
            if not stock_name or stock_name in ("未知名称", "未知"):
                try:
                    from utils.stock_info_manager import get_stock_name as get_stock_name_global

                    code_key = str(stock_code or "")
                    if "." in code_key:
                        code_key = code_key.split(".", 1)[0]
                    stock_name = get_stock_name_global(code_key)
                except Exception as e:
                    self.logger.warning(f"获取股票名称失败: {stock_code}, 错误: {e}")
                    stock_name = ""
            if not stock_name or stock_name in ("未知名称", "未知"):
                # 新股/漏网：显示代码，避免「未知名称」
                code_disp = str(stock_code or "").strip()
                stock_name = code_disp.split(".")[0] if "." in code_disp else (code_disp or "")
            
            # 2. 构建表格项数据
            reason = trade_info.get('reason', '真实订单')
            try:
                from core.smart_sell import localize_order_display_text
                side = (trade_info.get('type') if trade_info else '') or ''
                reason = localize_order_display_text(reason, side=side)
            except Exception:
                pass
            order_status = str(trade_info.get('order_status') or "").strip()
            if not order_status or order_status in ("未知", "None", "none"):
                # 兜底：按成交量推断，避免「买入-未知」
                try:
                    tv = int(trade_info.get("trade_volume") or 0)
                    ov = int(trade_info.get("volume") or 0)
                except (TypeError, ValueError):
                    tv, ov = 0, 0
                if ov > 0 and tv >= ov:
                    order_status = "已成"
                elif tv > 0:
                    order_status = "部成"
                else:
                    order_status = "已报"
            table_items = [
                str(trade_info.get('order_id', '')),  # 确保订单号是字符串
                stock_code,
                stock_name,  # 添加股票名称
                order_time,
                f"{price:.3f}" +"/"+f"{trade_info.get('trade_price', 0.0):.3f}",
                str(trade_info.get('volume', 0)) +"/"+str(trade_info.get('trade_volume', 0)),
                trade_info.get('type', '未知类型')+" - "+order_status,
                reason
            ]

            # 3. 查找已存在行
            existing_row = self.find_existing_row(table, stock_code, trade_info, order_time, price)
            
            # 4. 更新或插入数据
            try:
                if existing_row >= 0:
                    #self.logger.info(f"更新现有行: {existing_row}, 订单号: {trade_info.get('order_id')}")
                    self.update_existing_row(table, existing_row, table_items)
                    # 更新撤单按钮状态 - 使用 UI 回调
                    if ui_callback:
                        #self.logger.info(f"调用UI回调更新按钮: {existing_row}")
                        ui_callback.update_cancel_button(table, existing_row, trade_info.get('order_status', "未知"), trade_info.get('order_id', ''))
                    else:
                        self.logger.warning(f"没有UI回调，使用默认更新方法")
                        self.update_cancel_button(table, existing_row, trade_info.get('order_status', "未知"), trade_info.get('order_id', ''))
                else:
                    #self.logger.info(f"插入新行，订单号: {trade_info.get('order_id')}")
                    # 新订单插入到第一行
                    table.insertRow(0)
                    status_col = trade_info.get('type', '未知类型')+" - "+order_status
                    for col, value in enumerate(table_items):
                        item = self.create_table_item(value, status_text=status_col)
                        table.setItem(0, col, item)
                    #self.logger.info(f"新行索引: 0")
                    
                    # 添加撤单按钮 - 使用 UI 回调
                    if ui_callback:
                        #self.logger.info(f"调用UI回调添加按钮: 0")
                        ui_callback.add_cancel_button(table, 0, trade_info.get('order_status', "未知"), trade_info.get('order_id', ''))
                        #self.logger.info(f"UI回调添加按钮完成: 0")
                        
                        # 验证按钮是否真的被设置了
                        operation_container = table.cellWidget(0, 8)
                        if operation_container:
                            #self.logger.info(f"验证成功：操作控件已设置到行 0")
                            cancel_button = operation_container.findChild(QPushButton, "cancel_button")
                            monitor_button = operation_container.findChild(QPushButton, "monitor_button")
                            if cancel_button and monitor_button:
                                #self.logger.info(f"验证成功：撤单和监控按钮都存在")
                                pass
                            else:
                                self.logger.warning(f"验证失败：撤单按钮={cancel_button}, 监控按钮={monitor_button}")
                        else:
                            self.logger.error(f"验证失败：行 0 没有操作控件")
                    else:
                        self.logger.warning(f"没有UI回调，直接创建操作控件")
                        # 如果没有UI回调，直接创建操作控件并设置到表格中
                        operation_container = self.create_operation_widget(trade_info.get('order_status', "未知"))
                        if operation_container:
                            table.setCellWidget(0, 8, operation_container)
                            #self.logger.info(f"直接设置操作控件到表格行 0")
                        else:
                            self.logger.error(f"创建操作控件失败")
            except Exception as e:
                self.logger.error(f"更新或插入数据失败: {str(e)}")
                return
            
            # 5. 启用自动排序，按委托时间倒序
            try:
                self.sort_order_table(table, ui_callback)
            except Exception as e:
                self.logger.error(f"排序表格失败: {str(e)}")
            
            # 6. 滚动到顶部（最新订单在第一行）
            try:
                table.scrollToTop()
            except Exception as e:
                self.logger.error(f"滚动到顶部失败: {str(e)}")
            
        except Exception as e:
            self.logger.error(f"add_trade_record失败: {str(e)}", exc_info=True)
            # 记录到文件
            try:
                with open('logs/crash.log', 'a', encoding='utf-8') as f:
                    f.write(f"\n=== add_trade_record失败 {datetime.now()} ===\n")
                    f.write(f"异常信息: {str(e)}\n")
                    f.write("异常堆栈:\n")
                    f.write(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    f.write("\n" + "="*50 + "\n")
            except Exception as log_e:
                print(f"写入崩溃日志失败: {log_e}")

    def sort_order_table(self, table, ui_callback=None):
        """对订单表格进行排序：按委托时间循环倒序（根据当前时间）"""
        try:
            if table.rowCount() <= 1:
                return
            
            # 获取当前时间（只取时分秒部分）
            now = datetime.now()
            current_time = now.hour * 3600 + now.minute * 60 + now.second  # 当前时间转换为秒数
            
            # 获取所有行的数据
            rows_data = []
            for row in range(table.rowCount()):
                order_time = table.item(row, 3).text() if table.item(row, 3) else ''  # 第4列是委托时间
                # 将时间字符串转换为秒数
                try:
                    order_time_seconds = None
                    # 处理时间格式，支持 HH:MM:SS 和 YYYY-MM-DD HH:MM:SS 格式
                    if len(order_time) == 8:  # HH:MM:SS 格式
                        time_obj = datetime.strptime(order_time, '%H:%M:%S')
                        order_time_seconds = time_obj.hour * 3600 + time_obj.minute * 60 + time_obj.second
                    elif len(order_time) > 8:  # YYYY-MM-DD HH:MM:SS 格式
                        time_obj = datetime.strptime(order_time, '%Y-%m-%d %H:%M:%S')
                        order_time_seconds = time_obj.hour * 3600 + time_obj.minute * 60 + time_obj.second
                    else:
                        order_time_seconds = -1  # 无效时间
                except (ValueError, TypeError):
                    order_time_seconds = -1  # 解析失败
                
                # 计算循环排序键
                # 如果订单时间 <= 当前时间：认为是今天的订单，排序键 = 当前时间 - 订单时间（越小越新）
                # 如果订单时间 > 当前时间：认为是昨天的订单，排序键 = 当前时间 + (24小时 - 订单时间)（越小越新）
                if order_time_seconds == -1:
                    sort_key = float('inf')  # 无效时间排在最后
                elif order_time_seconds <= current_time:
                    # 今天的订单：从当前时间倒序到0点
                    sort_key = current_time - order_time_seconds
                else:
                    # 昨天的订单：从23:59:59倒序到当前时间之后
                    sort_key = current_time + (24 * 3600 - order_time_seconds)
                
                rows_data.append({
                    'row': row,
                    'order_time': order_time,
                    'sort_key': sort_key,
                    'time_seconds': order_time_seconds,
                })
            
            # 按排序键正序排序（sort_key越小越新，排在最前面）
            rows_data.sort(key=lambda x: x['sort_key'])
            self.reorder_table_rows(table, rows_data, ui_callback)
        except Exception as e:
            self.logger.error(f"排序订单表格失败: {str(e)}")

    def reorder_table_rows(self, table, sorted_rows_data, ui_callback=None):
        """重新排列表格行，排序后每行都重新创建操作控件并连接信号"""
        try:
            # 保存当前滚动位置
            scrollbar = table.verticalScrollBar()
            current_scroll_position = scrollbar.value()
            
            # 阻塞信号
            table.blockSignals(True)
            
            # 保存所有行的数据
            all_rows_data = []
            for row_data in sorted_rows_data:
                row = row_data['row']
                row_items = []
                for col in range(table.columnCount()):
                    item = table.item(row, col)
                    if item:
                        row_items.append(item.text())
                    else:
                        row_items.append('')
                all_rows_data.append({
                    'items': row_items
                })
            
            # 清空表格
            table.setRowCount(0)
            
            # 按新顺序重新添加行
            for i, row_data in enumerate(all_rows_data):
                new_row = table.rowCount()
                table.insertRow(new_row)
                # 添加数据项
                status_text = row_data['items'][6] if len(row_data['items']) > 6 else ''
                for col, value in enumerate(row_data['items']):
                    item = self.create_table_item(value, status_text=status_text)
                    table.setItem(new_row, col, item)
                # 重新创建操作控件
                order_id = row_data['items'][0]
                order_status = row_data['items'][6].split(' - ')[-1] if len(row_data['items']) > 6 else '未知'
                if ui_callback:
                    ui_callback.add_cancel_button(table, new_row, order_status, order_id)
                else:
                    self.add_cancel_button(table, new_row, order_status, order_id)
            
            # 恢复滚动位置
            scrollbar.setValue(current_scroll_position)
            # 重新启用信号
            table.blockSignals(False)
        except Exception as e:
            self.logger.error(f"重新排列表格行失败: {str(e)}")
            table.blockSignals(False)

    def find_existing_row(self, table, stock_code, trade_info, order_time, price):
        """查找已存在的行 - 区分不同日期的相同合同号。
        柜台合同号到位时，合并此前插入的 PO…「待查」占位行（同一笔提前单/passorder）。
        """
        order_id = str(trade_info.get('order_id', ''))
        
        # 检查是否为夜市委托
        strategy_name = trade_info.get('strategy_name', '') or trade_info.get('reason', '')
        is_night_market = '夜市' in strategy_name
        
        if not order_id:  # 如果没有订单号，则根据股票代码、时间和价格查找
            for row in range(table.rowCount()):
                if (table.item(row, 1).text() == stock_code and  # 股票代码（第1列）
                    table.item(row, 3).text() == order_time and  # 委托时间（第3列）
                    table.item(row, 4).text() == f"{price:.3f}"):  # 委托价格（第4列）
                    return row
        else:  # 如果有订单号
            for row in range(table.rowCount()):
                existing_order_id = table.item(row, 0).text() if table.item(row, 0) else ''
                existing_stock_code = table.item(row, 1).text() if table.item(row, 1) else ''
                existing_time = table.item(row, 3).text() if table.item(row, 3) else ''
                
                if is_night_market:
                    # 夜市委托：只有当委托时间和合同号都相同时，才认为是同一订单
                    if existing_time == order_time and existing_order_id == order_id:
                        return row
                else:
                    # 普通订单：按合同号+股票代码匹配，避免策略先插入与QMT回调时间格式不一致导致重复行
                    if existing_order_id == order_id and existing_stock_code == stock_code:
                        return row

            # 真实合同号 ↔ 本地 PO 占位行合并（时间可能不同，不能比 order_time）
            if (not is_night_market) and order_id and not order_id.startswith("PO"):
                replace_id = str(trade_info.get("replace_order_id") or "").strip()
                want_type = str(trade_info.get("type") or "").strip()
                try:
                    want_vol = int(trade_info.get("volume") or 0)
                except (TypeError, ValueError):
                    want_vol = 0
                try:
                    want_price = float(price if price is not None else trade_info.get("price") or 0)
                except (TypeError, ValueError):
                    want_price = 0.0
                for row in range(table.rowCount()):
                    existing_order_id = table.item(row, 0).text() if table.item(row, 0) else ""
                    existing_stock_code = table.item(row, 1).text() if table.item(row, 1) else ""
                    if existing_stock_code != stock_code:
                        continue
                    if replace_id and existing_order_id == replace_id:
                        return row
                    if not existing_order_id.startswith("PO"):
                        continue
                    status_text = table.item(row, 6).text() if table.item(row, 6) else ""
                    # 仅吸收尚未落到柜台终态的占位行
                    if not any(k in status_text for k in ("待查", "未报", "待报", "已报", "已确认")):
                        continue
                    if any(k in status_text for k in ("已成", "已撤", "废单", "部成")):
                        continue
                    if want_type and want_type not in status_text:
                        continue
                    qty_text = table.item(row, 5).text() if table.item(row, 5) else ""
                    try:
                        ordered_vol = int(str(qty_text).split("/")[0].strip())
                    except (TypeError, ValueError):
                        ordered_vol = -1
                    if want_vol and ordered_vol >= 0 and ordered_vol != want_vol:
                        continue
                    px_text = table.item(row, 4).text() if table.item(row, 4) else ""
                    try:
                        ordered_px = float(str(px_text).split("/")[0].strip())
                    except (TypeError, ValueError):
                        ordered_px = None
                    if want_price and ordered_px is not None and abs(ordered_px - want_price) > 0.011:
                        continue
                    return row
        
        return -1  # 没有找到匹配的行，需要插入新行

    def create_table_item(self, value, align_center=True, status_text=None):
        """创建表格项并设置对齐；悬停显示全文（名称列过长时用）。

        status_text: 若提供，按订单状态着色（已报蓝 / 已成黑 / 已撤灰）。
        """
        text = str(value)
        item = QTableWidgetItem(text)
        if align_center:
            item.setTextAlignment(Qt.AlignCenter)
        if text:
            item.setToolTip(text)
        if status_text is not None:
            item.setForeground(QBrush(_order_status_text_color(status_text)))
        return item

    def update_existing_row(self, table, row, items):
        """更新现有行（名称列：已有正常简称时不被「未知名称」覆盖）"""
        status_text = ""
        try:
            if len(items) > 6:
                status_text = str(items[6] or "")
        except Exception:
            status_text = ""
        for col, value in enumerate(items):
            if col == 2:
                try:
                    old_item = table.item(row, 2)
                    old_name = old_item.text().strip() if old_item else ""
                    new_name = str(value or "").strip()
                    if (
                        old_name
                        and old_name not in ("未知名称", "未知", "")
                        and new_name in ("未知名称", "未知", "")
                    ):
                        continue
                except Exception:
                    pass
            item = self.create_table_item(value, status_text=status_text)
            table.setItem(row, col, item)

    def insert_new_row(self, table, items):
        """插入新行"""
        new_row = table.rowCount()
        table.insertRow(new_row)
        status_text = ""
        try:
            if len(items) > 6:
                status_text = str(items[6] or "")
        except Exception:
            status_text = ""
        for col, value in enumerate(items):
            item = self.create_table_item(value, status_text=status_text)
            table.setItem(new_row, col, item)

    def create_operation_widget(self, order_status):
        """创建操作按钮控件，只返回 QWidget，不做 setCellWidget 和信号连接"""
        try:
            operation_container = QWidget()
            operation_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            operation_layout = QHBoxLayout(operation_container)
            # 上下边距对称 + AlignCenter：按钮在单元格内垂直居中
            operation_layout.setContentsMargins(4, 0, 4, 0)
            operation_layout.setSpacing(4)
            operation_layout.setAlignment(Qt.AlignCenter)

            btn_font = QFont("Microsoft YaHei", 11)
            btn_font.setBold(True)

            cancel_button = QPushButton("撤单")
            cancel_button.setFixedSize(62, 24)
            cancel_button.setFont(btn_font)
            cancel_button.setStyleSheet(_STYLE_CANCEL)
            cancel_button.setCursor(Qt.PointingHandCursor)

            monitor_button = QPushButton("监控")
            monitor_button.setFixedSize(62, 24)
            monitor_button.setFont(btn_font)
            monitor_button.setStyleSheet(_STYLE_MONITOR)
            monitor_button.setCursor(Qt.PointingHandCursor)

            if order_status in ['已成', '已撤', '废单']:
                cancel_button.setEnabled(False)
                cancel_button.setText("已结束")
                cancel_button.setStyleSheet(_STYLE_ENDED)
                monitor_button.setEnabled(False)
                monitor_button.setText("已结束")
                monitor_button.setStyleSheet(_STYLE_ENDED)

            operation_layout.addWidget(cancel_button)
            operation_layout.addWidget(monitor_button)

            cancel_button.setObjectName("cancel_button")
            monitor_button.setObjectName("monitor_button")

            return operation_container

        except Exception as e:
            self.logger.error(f"创建操作按钮控件失败: {e}")
            return None

    def add_cancel_button(self, table, row, order_status, order_id, callback=None):
        """添加撤单按钮（保留兼容性，但建议使用 create_operation_widget）"""
        try:
            operation_container = self.create_operation_widget(order_status)
            if operation_container:
                table.setCellWidget(row, 8, operation_container)
                
                # 添加调试信息
                #self.logger.info(f"设置操作控件到表格 - 行: {row}, 列: 8")
                #self.logger.info(f"表格单元格控件: {table.cellWidget(row, 8)}")
                #self.logger.info(f"操作列宽度: {table.columnWidth(8)}")
                
                # 检查按钮是否真的在表格中
                cancel_button = operation_container.findChild(QPushButton, "cancel_button")
                monitor_button = operation_container.findChild(QPushButton, "monitor_button")
                if cancel_button and monitor_button:
                    #self.logger.info(f"按钮设置成功 - 撤单按钮: {cancel_button.text()}, 监控按钮: {monitor_button.text()}")
                    #self.logger.info(f"按钮位置 - 撤单: {cancel_button.geometry()}, 监控: {monitor_button.geometry()}")
                    pass
                else:
                    self.logger.warning(f"按钮设置失败 - 撤单按钮: {cancel_button}, 监控按钮: {monitor_button}")
                
                # 如果有回调函数或ui_callback对象，连接信号
                if callback:
                    cancel_button = operation_container.findChild(QPushButton, "cancel_button")
                    monitor_button = operation_container.findChild(QPushButton, "monitor_button")
                    
                    # 检查callback是否是ui_callback对象（有_handle_cancel_click和_handle_monitor_click方法）
                    if hasattr(callback, '_handle_cancel_click') and hasattr(callback, '_handle_monitor_click'):
                        # 这是ui_callback对象，直接连接信号到它的处理方法
                        if cancel_button:
                            cancel_button.clicked.connect(lambda checked, r=row, oid=order_id: callback._handle_cancel_click(r, oid))
                        if monitor_button:
                            monitor_button.clicked.connect(lambda checked, r=row, oid=order_id: callback._handle_monitor_click(r, oid))
                    elif callable(callback):
                        # 这是普通的回调函数
                        if cancel_button:
                            cancel_button.clicked.connect(lambda: callback('cancel', row, order_id))
                        if monitor_button:
                            monitor_button.clicked.connect(lambda: callback('monitor', row, order_id))
            
        except Exception as e:
            self.logger.error(f"添加撤单按钮失败: {e}")

    def update_operation_widget(self, operation_container, order_status):
        """更新操作按钮状态"""
        try:
            if not operation_container:
                return
            
            cancel_button = operation_container.findChild(QPushButton, "cancel_button")
            monitor_button = operation_container.findChild(QPushButton, "monitor_button")
            
            if not cancel_button or not monitor_button:
                return
            
            # 根据订单状态更新按钮
            if order_status in ['已成', '已撤', '废单']:
                cancel_button.setEnabled(False)
                cancel_button.setText("已结束")
                cancel_button.setStyleSheet(_STYLE_ENDED)
                monitor_button.setEnabled(False)
                monitor_button.setText("已结束")
                monitor_button.setStyleSheet(_STYLE_ENDED)
            else:
                cancel_button.setEnabled(True)
                cancel_button.setText("撤单")
                cancel_button.setStyleSheet(_STYLE_CANCEL)
                monitor_button.setEnabled(True)
                monitor_button.setText("监控")
                monitor_button.setStyleSheet(_STYLE_MONITOR)
            
        except Exception as e:
            self.logger.error(f"更新操作按钮失败: {e}")

    def update_cancel_button(self, table, row, order_status, order_id):
        """更新撤单按钮状态（保留兼容性）"""
        try:
            operation_container = table.cellWidget(row, 8)
            if operation_container:
                self.update_operation_widget(operation_container, order_status)
        except Exception as e:
            self.logger.error(f"更新撤单按钮失败: {e}")

    def set_qmt_adapter(self, qmt_adapter):
        """设置QMT适配器"""
        self.qmt_adapter = qmt_adapter

    def get_order_monitors(self):
        """获取订单监控字典"""
        return self.order_monitors

    def add_order_monitor(self, order_id, monitor_data):
        """添加订单监控"""
        self.order_monitors[order_id] = monitor_data

    def remove_order_monitor(self, order_id):
        """移除订单监控"""
        if order_id in self.order_monitors:
            del self.order_monitors[order_id] 
    
    def get_order_count(self):
        """获取订单数量"""
        try:
            # 这里需要访问表格来获取订单数量
            # 由于这个方法可能在没有表格引用的情况下被调用，我们返回一个默认值
            # 或者可以通过其他方式获取订单数量
            return 0  # 暂时返回0，后续可以根据需要修改
        except Exception as e:
            self.logger.error(f"获取订单数量失败: {str(e)}")
            return 0 