import sys

# Windows：避免第三方库隐式创建的 ProactorEventLoop 在 GC 阶段 __del__→close()
# 时访问已不存在的 _ssock，触发 “Exception ignored … AttributeError: '_ssock'”
# （CPython 3.10+ 在异常关闭/资源耗尽时较易出现）
if sys.platform == "win32":
    try:
        from asyncio import proactor_events

        _orig_close_self_pipe = proactor_events.BaseProactorEventLoop._close_self_pipe

        def _safe_close_self_pipe(self):
            if not hasattr(self, "_ssock"):
                self._ssock = None
            if not hasattr(self, "_csock"):
                self._csock = None
            try:
                return _orig_close_self_pipe(self)
            except AttributeError:
                pass

        proactor_events.BaseProactorEventLoop._close_self_pipe = _safe_close_self_pipe
    except Exception:
        pass

import warnings
import signal
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtCore import pyqtSlot
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtGui import QIcon
import time
from datetime import datetime

import traceback  # 添加这行

# 重依赖（xtquant / main_window_ext 等）延后到「使用前必读」同意后再加载，
# 否则启动后要等数秒才弹出确认窗，用户会以为程序没起来。
Ui_mainWindow = None
MainWindowExt = None
Config = None
create_broker_gateway = None
Logger = None
TaskManager = None
xtconstant = None


def _load_trading_deps():
    """加载主窗口与交易相关依赖（仅在用户同意使用条款后调用）。"""
    global Ui_mainWindow, MainWindowExt, Config, create_broker_gateway
    global Logger, TaskManager, xtconstant
    if Ui_mainWindow is not None:
        return
    from ui.main_window import Ui_mainWindow as _Ui_mainWindow
    from ui.main_window_ext import MainWindowExt as _MainWindowExt
    from utils.config import Config as _Config
    from brokers.broker_gateway import create_broker_gateway as _create_broker_gateway
    from utils.logger import Logger as _Logger
    from core.task_manager import TaskManager as _TaskManager
    import xtquant.xtconstant as _xtconstant

    Ui_mainWindow = _Ui_mainWindow
    MainWindowExt = _MainWindowExt
    Config = _Config
    create_broker_gateway = _create_broker_gateway
    Logger = _Logger
    TaskManager = _TaskManager
    xtconstant = _xtconstant

# Windows multiprocessing 配置
if sys.platform.startswith('win'):
    import multiprocessing
    # 设置multiprocessing启动方法为spawn，避免Windows下的权限问题
    try:
        multiprocessing.set_start_method('spawn', force=True)
    except RuntimeError:
        # 如果已经设置过，忽略错误
        pass

# 设置控制台编码，解决中文乱码问题
import os
if sys.platform.startswith('win'):
    # 不调用 chcp，避免部分终端在切换代码页时出现输出被清空/重绘的问题
    # 仅设置 Python 进程内的标准输出编码。
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# 忽略特定的弃用警告
warnings.filterwarnings("ignore", category=DeprecationWarning)

# 全局变量存储主窗口引用
_main_window = None

def get_icon_path():
    """获取图标文件路径"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ant.ico')

def set_windows_taskbar_icon(window, icon_path):
    """在 Windows 上设置任务栏图标"""
    if sys.platform.startswith('win'):
        try:
            import ctypes
            from ctypes import wintypes
            
            # 确保图标路径是绝对路径
            if not os.path.isabs(icon_path):
                icon_path = os.path.abspath(icon_path)
            
            if os.path.exists(icon_path):
                # 获取窗口句柄
                hwnd = int(window.winId())
                
                # 加载图标
                # 使用 LoadImage 加载图标
                IMAGE_ICON = 1
                LR_LOADFROMFILE = 0x00000010
                LR_DEFAULTSIZE = 0x00000040
                
                # 尝试加载图标
                hicon = ctypes.windll.user32.LoadImageW(
                    None,
                    icon_path,
                    IMAGE_ICON,
                    0, 0,
                    LR_LOADFROMFILE | LR_DEFAULTSIZE
                )
                
                if hicon:
                    # 设置窗口类图标
                    GCL_HICON = -14
                    GCL_HICONSM = -34
                    
                    # 设置大图标和小图标
                    ctypes.windll.user32.SetClassLongPtrW(
                        hwnd,
                        GCL_HICON,
                        hicon
                    )
                    ctypes.windll.user32.SetClassLongPtrW(
                        hwnd,
                        GCL_HICONSM,
                        hicon
                    )
                    
                    return True
        except Exception as e:
            # 如果设置失败，不影响程序运行
            print(f"设置 Windows 任务栏图标失败: {e}")
            return False
    return False

def signal_handler(signum, frame):
    """信号处理器，处理Ctrl-C等中断信号"""
    global _main_window
    print("\n收到中断信号，正在退出程序...")
    
    # 关闭所有日志处理器
    try:
        from utils.logger import Logger
        Logger.close_all()
    except Exception:
        pass
    
    if _main_window:
        try:
            # 强制关闭主窗口
            _main_window.close()
        except Exception as e:
            print(f"关闭主窗口失败: {e}")
    
    # 强制退出程序
    sys.exit(0)

# 设置信号处理器
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# 设置全局异常处理器
def exception_hook(exctype, value, traceback_obj):
    """全局异常处理器"""
    import sys
    
    # 创建临时logger记录异常
    try:
        from utils.logger import Logger as TempLogger
        temp_logger = TempLogger()
        temp_logger.error(f"未捕获的异常: {exctype.__name__}: {value}")
        temp_logger.error(f"异常堆栈: {traceback.format_exception(exctype, value, traceback_obj)}")
    except Exception as logger_e:
        print(f"创建Logger失败: {logger_e}")
        print(f"未捕获的异常: {exctype.__name__}: {value}")
    
    # 记录到文件
    try:
        with open('logs/crash.log', 'a', encoding='utf-8') as f:
            f.write(f"\n=== 程序崩溃 {datetime.now()} ===\n")
            f.write(f"异常类型: {exctype.__name__}\n")
            f.write(f"异常信息: {value}\n")
            f.write("异常堆栈:\n")
            f.write(''.join(traceback.format_exception(exctype, value, traceback_obj)))
            f.write("\n" + "="*50 + "\n")
    except Exception as e:
        print(f"写入崩溃日志失败: {e}")
    
    # 调用原始的异常处理器
    sys.__excepthook__(exctype, value, traceback_obj)

# 设置全局异常处理器
sys.excepthook = exception_hook

class TradingApp(QMainWindow):
    def __init__(self):
        try:
            super().__init__()

            # 初始化基本logger
            self.logger = Logger()
            
            # 主动初始化股票信息管理器，确保在获取持仓信息前已加载完成
            try:
                from utils.stock_info_manager import get_stock_info_manager
                stock_manager = get_stock_info_manager()
                #self.logger.info("股票信息管理器初始化完成")
            except Exception as e:
                self.logger.error(f"股票信息管理器初始化失败: {str(e)}")
            
            # 初始化UI
            self.ui = Ui_mainWindow()
            self.ui.setupUi(self)
            
            # 设置窗口图标
            icon_path = get_icon_path()
            if os.path.exists(icon_path):
                # 使用绝对路径确保图标正确加载
                abs_icon_path = os.path.abspath(icon_path)
                self.setWindowIcon(QIcon(abs_icon_path))
                # 在 Windows 上额外设置任务栏图标
                set_windows_taskbar_icon(self, abs_icon_path)
            
            # 初始化扩展功能
            self.ext = MainWindowExt()
            self.ext.setup_ui(self)            
            
            # 获取QMT配置
            self.config = Config()
            account_config = self.config['Account']
            self.qmt_path = account_config['path_qmt']
            self.account_id = account_config['account_id']
            
            # 初始化券商网关（mini 模式透传 QMTManager）
            self.qmt_manager = create_broker_gateway(self.qmt_path, self.account_id)
            
            
            # 初始化任务管理器
            self.task_manager = TaskManager()
            self.task_manager.set_qmt_adapter(self.qmt_manager)
            
            # 设置扩展功能的QMT适配器和任务管理器
            self.ext.set_task_manager(self.task_manager)  # 先设置task_manager
            self.ext.set_qmt_adapter(self.qmt_manager)    # 再设置qmt_adapter

            try:
                self.qmt_manager.start_builtin_price_feed(
                    self.task_manager,
                    self.ext,
                    parent=self,
                )
            except Exception as e:
                self.logger.warning(f"内置现价轮询启动失败: {e}")
            
            # 连接信号
            try:
                self.qmt_manager.position_updated.connect(self.handle_position_update)
                self.qmt_manager.order_updated.connect(self.handle_order_update)
                self.qmt_manager.connection_restored_signal.connect(self.handle_connection_restored)
                # 策略一产生交易/撤单/失败就立即刷新交易列表，不依赖QMT订单回调延迟
                self.task_manager.trade_record_updated.connect(lambda code, info: self.ext.add_trade_record(code, info))
                
                # 连接任务管理器的UI更新信号
                self.task_manager.update_task_ui.connect(self.ext.update_task_field)
                # 连接tasks_updated信号到精确更新方法，避免不必要的全表刷新
                self.task_manager.tasks_updated.connect(self.handle_tasks_updated)
                
                # 连接定时器准备信号，在主线程中创建定时器
                self.qmt_manager.timer_ready_signal.connect(self.qmt_manager.create_timer_in_main_thread)
            except Exception as e:
                self.logger.error(f"连接信号失败: {str(e)}", exc_info=True)
            
            # 加载扩展功能
            self.ext.setup_position_slots(self)
            
            try:
                from utils.qmt_execution_config import get_qmt_mode

                mode = get_qmt_mode()
            except Exception:
                mode = "mini"
            self.logger.info(f"蚂蚁量化交易系统初始化完成（qmt_mode={mode}）")
            
        except Exception as e:
            # 创建临时logger记录异常
            try:
                from utils.logger import Logger as TempLogger
                temp_logger = TempLogger()
                temp_logger.error(f"蚂蚁量化交易系统初始化失败: {str(e)}", exc_info=True)
                
                # 记录到文件
                try:
                    with open('logs/crash.log', 'a', encoding='utf-8') as f:
                        f.write(f"\n=== 蚂蚁量化交易系统初始化失败 {datetime.now()} ===\n")
                        f.write(f"异常信息: {str(e)}\n")
                        f.write("异常堆栈:\n")
                        import traceback
                        f.write(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                        f.write("\n" + "="*50 + "\n")
                except Exception as log_e:
                    print(f"写入崩溃日志失败: {log_e}")
            except Exception as logger_e:
                print(f"创建Logger失败: {logger_e}")
                print(f"蚂蚁量化交易系统初始化失败: {str(e)}")
            
            # 重新抛出异常，让全局异常处理器处理
            raise
        
    def showEvent(self, event):
        """窗口显示时触发"""
        try:
            super().showEvent(event)
            from utils.qmt_execution_config import requires_path_qmt
            if not self.account_id:
                self.logger.info("请先在配置文件中设置 account_id")
                return
            if requires_path_qmt() and not self.qmt_path:
                self.logger.info("请先在配置文件中设置 path_qmt（mini 模式必填）")
                return
                
            # 设置关联
            self.qmt_manager.set_task_manager(self.task_manager)
            
            # 启动线程
            self.qmt_manager.start()
            
            # 立即初始化图表视图（不等待QMT连接）
            if hasattr(self.ext, 'enable_charts_view_mode'):
                self.ext.enable_charts_view_mode()
            
            # 使用线程异步处理QMT连接，不阻塞UI显示
            import threading
            self.connection_thread = threading.Thread(target=self._handle_qmt_connection, daemon=True)
            self.connection_thread.start()
                
        except Exception as e:
            self.logger.error(f"showEvent处理失败: {str(e)}", exc_info=True)
            # 记录到文件
            try:
                with open('logs/crash.log', 'a', encoding='utf-8') as f:
                    f.write(f"\n=== showEvent处理失败 {datetime.now()} ===\n")
                    f.write(f"异常信息: {str(e)}\n")
                    f.write("异常堆栈:\n")
                    import traceback
                    f.write(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    f.write("\n" + "="*50 + "\n")
            except Exception as log_e:
                print(f"写入崩溃日志失败: {log_e}")
    
    def _handle_qmt_connection(self):
        """异步处理QMT连接"""
        try:
            # 设置QMT管理器对主窗口的引用，用于更新状态栏
            self.qmt_manager.main_window = self.ext
            
            # 设置任务管理器对主窗口的引用
            self.task_manager.main_window = self.ext
            
            # 等待QMT管理器初始化完成（现在会立即完成）
            max_wait_time = 10  # 最多等待10次，实际等待5秒 (10次 × 0.5秒)
            wait_count = 0
            while not self.qmt_manager.is_ready() and wait_count < max_wait_time:
                time.sleep(0.5)
                wait_count += 1
                
                # 每2秒显示一次连接状态
                if wait_count % 4 == 0:
                    self.logger.info(f"正在等待QMT管理器初始化... ({wait_count/2}秒)")
            
            if wait_count >= max_wait_time:
                self.logger.warning("QMT管理器初始化超时")
                return
            #else:
            #    self.logger.info("QMT管理器初始化完成")
            
            # 确保订阅线程正确启动
            max_retries = 5
            retry_count = 0
            while retry_count < max_retries:
                if self.qmt_manager.is_quote_feed_alive():
                    break
                else:
                    self.logger.warning(f"订阅线程未启动，第{retry_count + 1}次尝试重新启动...")
                    # 强制重新设置task_manager以启动订阅线程
                    self.qmt_manager.set_task_manager(self.task_manager)
                    time.sleep(2)
                    retry_count += 1
            
            if retry_count >= max_retries:
                self.logger.error("订阅线程启动失败，可能影响行情显示")
            
            # 查询并显示当日所有订单
            try:
                from utils.qmt_execution_config import use_builtin_price_feed
                from PyQt5.QtCore import QTimer

                if use_builtin_price_feed():
                    # builtin：订单来自 results.json，禁止清空后走 xt_trader（会一直空）
                    poller = getattr(self.qmt_manager, "_builtin_price_poller", None)

                    def _refill_builtin_orders():
                        try:
                            if poller is None:
                                return
                            poller._order_status_seen = {}
                            poller._orders_ui_bootstrapped = False
                            poller._apply_orders_snapshot()
                        except Exception as e:
                            self.logger.error(f"builtin 回填订单列表失败: {e}", exc_info=True)

                    QTimer.singleShot(0, _refill_builtin_orders)
                else:
                    def _refresh_mini_orders():
                        try:
                            if (
                                hasattr(self, "ext")
                                and self.ext
                                and hasattr(self.ext, "tableWidget_3")
                                and self.ext.tableWidget_3
                            ):
                                self.ext.tableWidget_3.setRowCount(0)
                            self.qmt_manager.get_today_orders()
                        except Exception as e:
                            self.logger.error(f"查询当日订单失败: {e}", exc_info=True)

                    QTimer.singleShot(0, _refresh_mini_orders)
            except Exception as e:
                self.logger.error(f"查询当日订单失败: {str(e)}", exc_info=True)
            
            # 立即加载持仓信息，让持仓信息与其他信息一起出现
            try:
                asset, positions = self.qmt_manager.get_asset_positions()
                if asset is not None and positions is not None and positions:
                    # 使用信号机制在主线程中更新UI
                    self.qmt_manager.position_updated.emit(asset, positions)
            except Exception as e:
                self.logger.error(f"加载持仓信息失败: {str(e)}")
            
            # 手动触发一次任务列表更新，确保所有任务都能显示
            if hasattr(self.ext, 'task_manager') and self.ext.task_manager and self.ext.task_manager.tasks:
                # 使用信号机制在主线程中更新UI
                self.task_manager.tasks_updated.emit()
            else:
                self.logger.info("任务管理器中没有任务")
                
        except Exception as e:
            self.logger.error(f"QMT连接处理失败: {str(e)}", exc_info=True)

    def closeEvent(self, event):
        """窗口关闭时触发"""
        try:
            # 首先检查是否有正在运行的任务
            if hasattr(self, 'ext') and self.ext:
                # 委托给扩展类处理关闭事件，包括任务检查
                self.ext.handle_close_event(event)
                # 如果事件被忽略（有运行中的任务），直接返回
                if event.isAccepted() == False:
                    return
            
            # 如果没有运行中的任务或检查通过，继续关闭流程
            if hasattr(self, 'qmt_manager'):
                self.qmt_manager.stop()  # 停止线程
                self.logger.info("QMT管理器已停止")
            
            # 确保所有任务都已停止
            if hasattr(self, 'task_manager') and self.task_manager:
                # 强制清理所有运行中的任务
                if self.task_manager.running_tasks:
                    self.logger.warning(f"发现{len(self.task_manager.running_tasks)}个运行中任务，强制停止")
                    for task_id in list(self.task_manager.running_tasks.keys()):
                        try:
                            self.task_manager.stop_task(task_id)
                        except Exception as e:
                            self.logger.error(f"强制停止任务{task_id}失败: {str(e)}")
                    
                    # 清空运行中任务列表
                    self.task_manager.running_tasks.clear()
                    self.logger.info("所有运行中任务已清理")
            
            # 强制清理所有子进程
            import psutil
            current_process = psutil.Process()
            children = current_process.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                    child.wait(timeout=2)
                except:
                    try:
                        child.kill()
                    except:
                        pass
            
            self.logger.info("程序正常退出")
            
            # 关闭所有日志处理器
            try:
                from utils.logger import Logger
                Logger.close_all()
                # 关闭root logger的所有处理器
                import logging
                for handler in logging.root.handlers[:]:
                    try:
                        handler.flush()
                        handler.close()
                    except Exception:
                        pass
                logging.root.handlers.clear()
            except Exception:
                pass
            
            super().closeEvent(event)
            
        except Exception as e:
            self.logger.error(f"处理窗口关闭事件失败: {str(e)}")
            # 关闭所有日志处理器
            try:
                from utils.logger import Logger
                Logger.close_all()
            except Exception:
                pass
            # 发生异常时也允许关闭，避免程序卡死
            super().closeEvent(event)
        
    @pyqtSlot(dict, dict)
    def handle_position_update(self, updated_asset, updated_positions):
        """处理持仓更新信号"""
        import time
        slot_start = time.time()
        
        # 检查参数是否为None，避免传递None给update_position_list
        if updated_asset is None or updated_positions is None:
            self.logger.warning(f"收到无效的持仓更新信号: asset={updated_asset}, positions={updated_positions}")
            return
        
        # 1. 更新持仓列表
        step1_start = time.time()
        self.ext.update_position_list(updated_asset, updated_positions)
        step1_time = time.time() - step1_start
        
        # 2. 更新所有图表的持仓和余额信息
        step2_time = 0
        if hasattr(self.ext, 'tasks_charts_view') and self.ext.tasks_charts_view:
            if hasattr(self.ext.tasks_charts_view, 'update_charts_position_and_cash'):
                try:
                    step2_start = time.time()
                    self.ext.tasks_charts_view.update_charts_position_and_cash(updated_asset, updated_positions)
                    step2_time = time.time() - step2_start
                except Exception as e:
                    self.logger.error(f"更新图表持仓和余额失败: {str(e)}", exc_info=True)
            else:
                self.logger.warning("TasksChartsView 缺少 update_charts_position_and_cash 方法，可能需要重启程序")
        
        # 3. 同时更新任务列表，确保持仓变化能反映到任务列表中
        step3_time = 0
        try:
            step3_start = time.time()
            # 优先使用增量更新，避免完全刷新导致状态丢失
            if hasattr(self.ext, '_incremental_update_task_table'):
                if self.ext._incremental_update_task_table():
                    pass
                else:
                    # 如果增量更新失败，才进行完全刷新
                    self.ext.refresh_task_table()
            else:
                # 如果没有增量更新方法，使用完全刷新
                self.ext.refresh_task_table()
            step3_time = time.time() - step3_start
        except Exception as e:
            self.logger.error(f"持仓更新时刷新任务列表失败: {str(e)}")

        try:
            from utils.qmt_execution_config import use_builtin_price_feed
            if use_builtin_price_feed():
                from utils.rules_armed_sync import sync_rules_armed
                sync_rules_armed(self.task_manager, self.qmt_manager, logger=self.logger)
        except Exception:
            pass
        
        # 输出槽函数总耗时（每次都输出，便于持续观察）
        slot_total = time.time() - slot_start
        if slot_total > 0.2:
            self.logger.warning(
                f"[性能监控] handle_position_update槽函数耗时: {slot_total:.3f}秒 "
                f"(持仓列表: {step1_time:.3f}秒, 图表更新: {step2_time:.3f}秒, 任务表格: {step3_time:.3f}秒)"
            )

    @pyqtSlot(object)
    def handle_order_update(self, order):
        _slot_start = time.time()
        try:
            # 获取订单信息（不记录日志，减少输出）
            order_sysid = getattr(order, 'order_sysid', None)
            stock_code = getattr(order, 'stock_code', None)
            strategy_name = getattr(order, 'strategy_name', '')
            if hasattr(self, 'qmt_manager') and self.qmt_manager:
                try:
                    strategy_name = self.qmt_manager.resolve_order_display_strategy_name(order)
                except Exception:
                    pass
            # 只在DEBUG模式下记录，或只对提前下单订单记录INFO
            # self.logger.debug(f"[订单更新] handle_order_update被调用: order_sysid={order_sysid}, stock_code={stock_code}, strategy_name={strategy_name}")
            
            # 检查关键属性
            if not hasattr(order, 'order_sysid') or not order.order_sysid:
                self.logger.warning(f"收到无效订单对象，跳过处理: {order}")
                try:
                    self.logger.warning(f"订单对象内容: {order.__dict__}")
                except Exception:
                    self.logger.warning(f"订单对象无法转为dict: {order}")
                return

            # 丢弃跨日残留委托（QMT query 有时带回历史单）
            try:
                from utils.order_session import is_current_session_order

                ot = getattr(order, 'order_time', None)
                at = getattr(order, 'order_date', None) or getattr(order, 'insert_time', None)
                if not is_current_session_order(order_time=ot, at=at):
                    return
            except Exception:
                pass
            
            # 从订单对象中提取必要信息
            if not stock_code:
                self.logger.warning(f"订单对象缺少stock_code，跳过: {order}")
                return
            
            # 获取订单状态
            status_map = {
                48: '未报',
                49: '待报',
                50: '已报',
                51: '已报待撤',
                52: '部成待撤',
                53: '部撤',
                54: '已撤',
                55: '部成',
                56: '已成',
                57: '废单',
                86: '已确认',
                255: '未知'
            }
            order_status = status_map.get(getattr(order, 'order_status', 255), '未知')
            if order_status == '未知':
                try:
                    tv = int(getattr(order, 'traded_volume', 0) or 0)
                    ov = int(getattr(order, 'order_volume', 0) or 0)
                except (TypeError, ValueError):
                    tv, ov = 0, 0
                if ov > 0 and tv >= ov:
                    order_status = '已成'
                elif tv > 0:
                    order_status = '部成'
                else:
                    order_status = '已报'
            
            # 获取策略名称，如果为空则默认为"常规"
            if not strategy_name:
                strategy_name = "常规"
            
            # 修正QMT返回的错误策略名称（QMT会根据订单类型自动修改策略名称）
            if strategy_name:
                if getattr(order, 'order_type', None) == xtconstant.STOCK_SELL:  # 卖出订单
                    # 如果策略名称包含"买入"，将其改为"卖出"
                    if '买入' in strategy_name:
                        strategy_name = strategy_name.replace('买入', '卖出')
                elif getattr(order, 'order_type', None) == xtconstant.STOCK_BUY:  # 买入订单
                    # 如果策略名称包含"卖出"，将其改为"买入"
                    if '卖出' in strategy_name:
                        strategy_name = strategy_name.replace('卖出', '买入')
            
            # 构建真实订单信息
            stock_name = ""
            for attr in (
                "instrument_name",
                "InstrumentName",
                "stock_name",
                "m_strInstrumentName",
            ):
                val = getattr(order, attr, None)
                if val:
                    stock_name = str(val).strip()
                    break
            if not stock_name or stock_name in ("未知名称", "未知"):
                try:
                    from utils.stock_info_manager import get_stock_name as _gsn

                    stock_name = str(_gsn(stock_code) or "").strip()
                except Exception:
                    stock_name = ""
            if stock_name in ("未知名称", "未知"):
                stock_name = ""
            if not stock_name:
                code_disp = str(stock_code or "").strip()
                stock_name = code_disp.split(".")[0] if "." in code_disp else code_disp

            trade_info = {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'order_id': str(order.order_sysid),  # 使用真实的订单系统ID
                'type': '卖出' if getattr(order, 'order_type', None) == xtconstant.STOCK_SELL else '买入',
                # 委托/均价里的「委托价」必须使用订单的委托价：order.price
                # 否则一旦成交后 traded_price 存在，会把委托价替换成成交均价，导致 UI 显示成“委托价=均价”
                'price': getattr(order, 'price', 0) if getattr(order, 'price', 0) is not None else 0,
                # 委托/均价里的「数量」应显示为委托数量：order_volume
                'volume': getattr(order, 'order_volume', 0) if getattr(order, 'order_volume', 0) is not None else 0,
                'order_time': getattr(order, 'order_time', datetime.now().strftime('%H:%M:%S')),  # 使用订单的真实时间
                'reason': strategy_name,
                'order_status': order_status,
                'trade_volume': getattr(order, 'traded_volume', 0),
                'trade_price': getattr(order, 'traded_price', 0) if getattr(order, 'traded_price', 0) > 0 else 0.0,
                'strategy_name': strategy_name,
                'is_real_order': True  # 标记为真实订单
            }
            
            # 直接调用UI更新，因为已经在主线程中
            if hasattr(self, 'ext') and self.ext:
                try:
                    self.ext.add_trade_record(stock_code, trade_info)
                except Exception as e:
                    self.logger.error(f"调用add_trade_record失败: {str(e)}", exc_info=True)
            
            # 提前下单：用委托回报更新真实 order_sysid（不依赖策略名是否含「提前下单」）
            tasks_charts_view = None
            if hasattr(self, 'ext') and self.ext:
                if hasattr(self.ext, 'tasks_charts_view'):
                    tasks_charts_view = self.ext.tasks_charts_view
            if not tasks_charts_view and hasattr(self, 'tasks_charts_view'):
                tasks_charts_view = self.tasks_charts_view

            if tasks_charts_view:
                order_price = getattr(order, 'price', 0)
                order_type = 'buy' if getattr(order, 'order_type', None) == xtconstant.STOCK_BUY else 'sell'
                order_status_code = getattr(order, 'order_status', 255)

                tasks_charts_view.update_early_order_id(
                    stock_code,
                    str(order.order_sysid),
                    order_price,
                    order_type,
                )

                if order_status_code in (53, 54, 56, 57):
                    tasks_charts_view.handle_early_order_status_from_callback(
                        stock_code,
                        str(order.order_sysid),
                        order_status_code,
                        order_price,
                        order_type,
                    )
            
            # 如果是夜市委托的订单，根据订单状态更新规则
            if strategy_name and '夜市委托' in strategy_name:
                # 尝试从TasksChartsView中找到对应的StockChartWidget并更新规则状态
                tasks_charts_view = None
                if hasattr(self, 'ext') and self.ext:
                    if hasattr(self.ext, 'tasks_charts_view'):
                        tasks_charts_view = self.ext.tasks_charts_view
                if not tasks_charts_view and hasattr(self, 'tasks_charts_view'):
                    tasks_charts_view = self.tasks_charts_view
                
                if tasks_charts_view:
                    order_price = getattr(order, 'price', 0)
                    order_type = 'buy' if getattr(order, 'order_type', None) == xtconstant.STOCK_BUY else 'sell'
                    order_status_code = getattr(order, 'order_status', 255)
                    
                    # 调用TasksChartsView的方法更新夜市委托规则状态
                    tasks_charts_view.update_night_market_rule_from_order(
                        stock_code,
                        str(order.order_sysid),
                        order_status_code,
                        order_price,
                        order_type
                    )
            
            _slot_total = time.time() - _slot_start
            if _slot_total > 0.1:
                self.logger.warning(f"[性能监控] handle_order_update槽函数耗时: {_slot_total:.3f}秒")
                
        except Exception as e:
            import traceback
            self.logger.error(f"处理真实订单更新失败: {str(e)}\n{traceback.format_exc()}")

    @pyqtSlot()
    def handle_tasks_updated(self):
        """处理tasks_updated信号，优先使用增量更新避免状态丢失"""
        try:
            # 优先使用增量更新，避免完全刷新导致状态丢失
            if hasattr(self.ext, '_incremental_update_task_table'):
                if self.ext._incremental_update_task_table():
                    pass
                else:
                    # 如果增量更新失败，才进行完全刷新
                    self.ext.refresh_task_table()
            else:
                # 如果没有增量更新方法，使用完全刷新
                self.ext.refresh_task_table()
        except Exception as e:
            self.logger.error(f"处理tasks_updated信号失败: {str(e)}")
    
    @pyqtSlot()
    def handle_connection_restored(self):
        """处理QMT连接恢复信号"""
        try:
            self.logger.info("收到QMT连接恢复信号，重新启动订单列表定时器")
            
            # 重新启动订单列表定时器
            if hasattr(self.ext, 'order_refresh_timer'):
                # 先停止定时器
                self.ext.order_refresh_timer.stop()
                # 重新启动定时器
                self.ext.order_refresh_timer.start(6000)  # 6秒刷新一次订单列表
                self.logger.info("订单列表定时器已重新启动")
            else:
                self.logger.warning("订单列表定时器不存在")
                
        except Exception as e:
            self.logger.error(f"处理连接恢复信号失败: {str(e)}")

def _try_start_singleton_server(server_name: str, max_attempts: int = 10):
    """
    启动 QLocalServer 单实例监听；失败时反复 removeServer + 短暂等待（清理崩溃残留）。
    返回 (server 或 None, 最后一次错误描述)。
    """
    last_err = ""
    for attempt in range(max_attempts):
        try:
            QLocalServer.removeServer(server_name)
        except Exception:
            pass
        srv = QLocalServer()
        if srv.listen(server_name):
            return srv, None
        last_err = srv.errorString() or "unknown"
        try:
            srv.close()
        except Exception:
            pass
        del srv
        # 失败后再等，避免首次启动也白白睡一截
        time.sleep(0.06 + 0.05 * attempt)
    return None, last_err


def global_exception_handler(exc_type, exc_value, exc_traceback):
    """全局异常处理器"""
    try:
        # 记录异常信息
        error_msg = f"未处理的异常: {exc_type.__name__}: {exc_value}"
        print(error_msg)
        
        # 写入日志文件
        try:
            with open('logs/crash.log', 'a', encoding='utf-8') as f:
                f.write(f"\n=== 全局异常 {datetime.now()} ===\n")
                f.write(f"异常类型: {exc_type.__name__}\n")
                f.write(f"异常信息: {exc_value}\n")
                f.write("异常堆栈:\n")
                f.write(''.join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
                f.write("\n" + "="*50 + "\n")
        except Exception as log_e:
            print(f"写入崩溃日志失败: {log_e}")
            
    except Exception as e:
        print(f"全局异常处理器出错: {e}")

def main():
    try:
        # Windows multiprocessing 保护
        if sys.platform.startswith('win'):
            import multiprocessing
            # 确保在Windows下使用正确的启动方法
            if __name__ == '__main__':
                multiprocessing.freeze_support()
        
        # 设置全局异常处理器
        sys.excepthook = global_exception_handler
        
        app = QApplication(sys.argv)
        # 显式开启“最后一个窗口关闭即退出”，避免窗口关闭后主进程残留。
        app.setQuitOnLastWindowClosed(True)

        # --- 单实例控制：蚂蚁量化交易系统只允许一个副本 ---
        # 与备份目录（原版 AntTradingSystemSingleton）并存，便于 A/B 对比
        server_name = "AntTradingSystemSingletonV2"

        # 先尝试作为“第二实例”：连到已有的本地服务器，若成功则发送激活请求并退出
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if socket.waitForConnected(200):
            try:
                socket.write(b"activate")
                socket.flush()
                socket.waitForBytesWritten(200)
            except Exception:
                pass
            socket.disconnectFromServer()
            # 单实例：已有主程序在跑时本会立即退出；仅控制台提示，避免阻塞式弹窗。
            msg = (
                "检测到蚂蚁量化交易系统已在运行（单实例），已向前台实例发送激活请求。\n"
                "若任务栏已有本程序，请切到该窗口；本进程将退出。\n"
                "（若需再开一例，请先完全退出已运行的实例。）"
            )
            print(msg, flush=True)
            sys.exit(0)
        socket.abort()

        # 没有已运行实例：创建本地服务器，供后续实例发送“activate”指令
        server, listen_err = _try_start_singleton_server(server_name)
        if server is None:
            print(
                f"单实例监听多次重试仍失败（{listen_err}）。将以「非单实例模式」启动本进程，"
                "避免残留套接字导致无法打开程序；下次正常退出后单实例会恢复。",
                flush=True,
            )
            server = None
        else:
            setattr(app, "_singleton_server_name", server_name)

        def _cleanup_singleton_server():
            srv = getattr(app, "_singleton_server", None)
            name = getattr(app, "_singleton_server_name", None)
            if srv is not None:
                try:
                    srv.close()
                except Exception:
                    pass
            if name:
                try:
                    QLocalServer.removeServer(name)
                except Exception:
                    pass

        if server is not None:
            app._singleton_server = server
            app.aboutToQuit.connect(_cleanup_singleton_server)

        # 设置应用程序图标（用于Windows任务栏）
        icon_path = get_icon_path()
        if os.path.exists(icon_path):
            # 使用绝对路径确保图标正确加载
            abs_icon_path = os.path.abspath(icon_path)
            app.setWindowIcon(QIcon(abs_icon_path))
        
        # 先弹出使用前必读；弹出后立刻在后台加载重依赖，与用户阅读重叠
        load_err = []
        loader = None

        def _bg_load():
            try:
                _load_trading_deps()
            except Exception as e:
                load_err.append(e)

        try:
            from ui.read_before_use_dialog import ReadBeforeUseDialog
            from PyQt5.QtWidgets import QDialog
            import threading

            read_dialog = ReadBeforeUseDialog()
            read_dialog.show()
            app.processEvents()

            loader = threading.Thread(target=_bg_load, name="startup-deps", daemon=True)
            loader.start()

            result = read_dialog.exec_()
            if result != QDialog.Accepted:
                print("用户未同意使用条款，程序退出")
                sys.exit(0)
        except Exception as e:
            print(f"显示使用前必读对话框失败: {e}")
            if loader is None:
                _load_trading_deps()

        # 若点「我同意」时后台还未载完，短暂提示并等齐
        splash = None
        if loader is not None and loader.is_alive():
            try:
                from PyQt5.QtWidgets import QLabel
                from PyQt5.QtCore import Qt as _Qt
                splash = QLabel("正在加载主程序，请稍候…")
                splash.setWindowTitle("蚂蚁量化交易系统")
                splash.setAlignment(_Qt.AlignCenter)
                splash.setFixedSize(360, 80)
                splash.setWindowFlags(_Qt.WindowStaysOnTopHint | _Qt.SplashScreen)
                splash.setStyleSheet(
                    "background:#FFF8E1; color:#333; font-size:12pt; border:1px solid #FFCC80;"
                )
                splash.show()
                app.processEvents()
            except Exception:
                splash = None

        if loader is not None:
            while loader.is_alive():
                loader.join(0.05)
                app.processEvents()
        elif Ui_mainWindow is None:
            _load_trading_deps()

        if load_err:
            raise load_err[0]

        # 创建主窗口
        global _main_window
        _main_window = TradingApp()

        if splash is not None:
            try:
                splash.close()
            except Exception:
                pass
            splash = None

        # 处理来自后续实例的“激活”请求：将主窗口前置显示
        if server is not None:
            def handle_new_connection():
                client = server.nextPendingConnection()
                if not client:
                    return
                try:
                    if client.waitForReadyRead(200):
                        _ = client.readAll()
                        if _main_window is not None:
                            _main_window.showNormal()
                            _main_window.raise_()
                            _main_window.activateWindow()
                except Exception:
                    pass
                finally:
                    client.disconnectFromServer()

            server.newConnection.connect(handle_new_connection)

        # 在显示窗口后再次设置 Windows 任务栏图标（确保窗口句柄已创建）
        icon_path = get_icon_path()
        if os.path.exists(icon_path):
            abs_icon_path = os.path.abspath(icon_path)
            set_windows_taskbar_icon(_main_window, abs_icon_path)
        
        _main_window.show()
        
        # 运行应用
        sys.exit(app.exec_())
        
    except Exception as e:
        print(f"程序启动失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()