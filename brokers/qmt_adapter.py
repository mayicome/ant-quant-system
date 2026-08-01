import xtquant.xttrader as xttrader
from xtquant.xttype import StockAccount
import xtquant.xtdata as xtdata
from xtquant import xtconstant
from xtquant.xttrader import XtQuantTraderCallback
from PyQt5.QtCore import QObject, pyqtSignal, QMetaObject, Q_ARG, Qt, QTimer
from PyQt5.QtWidgets import QApplication
import logging
import time
from PyQt5.QtCore import QThread
from threading import Thread
import sys
import os
# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import my_function as myf
import threading
from datetime import datetime, timezone, timedelta
from utils.trading_day import is_tradeday, invalidate_trading_day_cache
from core.utils.security_type import SecurityTypeUtil
from utils.logger import Logger
import queue
import json
import subprocess

xtdata.enable_hello = False


def _is_continuous_auction_trading(now: datetime = None) -> bool:
    """连续竞价时段且为 A 股交易日（周六日/节假日不算「交易时间内」）。"""
    from datetime import time as dt_time

    if now is None:
        now = datetime.now()
    if not is_tradeday(now.date()):
        return False
    t = now.time()
    return (dt_time(9, 30) <= t <= dt_time(11, 30)) or (dt_time(13, 0) <= t <= dt_time(15, 0))


class QMTManager(QThread):
    position_updated = pyqtSignal(dict, dict)  # 添加持仓更新信号
    order_updated = pyqtSignal(object)  # 添加信号
    reconnect_signal = pyqtSignal()  # 添加重连信号
    connection_restored_signal = pyqtSignal()  # 连接恢复信号
    cancel_error_signal = pyqtSignal(str, str)  # 添加撤单失败信号: (order_id, error_msg)
    timer_ready_signal = pyqtSignal()  # 添加定时器准备信号
    tick_data_signal = pyqtSignal(dict)  # 添加tick数据信号: {stock_code, lastPrice, lastClose, askPrice, bidPrice, askVol, bidVol, time}
    
    def __init__(self, path, account, mode='live'):  # 添加 mode 参数
        super().__init__()
        self.path = path
        self.account = account
        self.session = int(time.time())
        self.signals = StatusSignals()
        # 根据 mode 参数决定使用哪个 logger
        self.logger = Logger(mode=mode)  # 确保传递 mode 参数
        self.running = True  # 控制线程运行的标志        
        # 使用全局股票信息管理器，不再需要加载all_a_stocks
        self.all_a_stocks = None
        self.positions = {}
        
        # 初始化 task_manager 为 None
        self.task_manager = None
        
        # 初始化订阅线程
        self.subscribe_thread = SubscribeThread(self)
        
        self._is_initialized = False
        self._xt_trader_connected = False  # xt_trader是否真正连接成功（connect_result == 0 且 subscribe_result == 0）
        self.cached_positions = {}  # 添加缓存
        # 持仓刷新时股票名称只查一次并缓存（key: 纯6位代码，如 '600746'）
        self._stock_name_cache = {}

        # 添加已处理订单集合 - 只保留上一次查询的订单
        self.processed_orders = set()
        # 下单时记录的完整策略说明（QMT 回报名可能被截断）
        self._order_display_strategy_names = {}
        
        # 清空已处理持仓集合
        if hasattr(self, 'callback') and hasattr(self.callback, 'processed_positions'):
            self.callback.processed_positions.clear()

        # 初始化时设置为当前日期，避免首次运行时误判为新交易日
        self._last_trading_day = datetime.now().strftime('%Y-%m-%d')

        # 移除定时器初始化，将在主线程中创建
        self._timer_started = False
        
        # 添加重连保护机制
        self.is_reconnecting = False  # 是否正在重连
        self.connection_check_counter = 0  # 连接检测计数器
        
        # 缓存股票代码到任务的索引，避免每次更新持仓时都重新构建
        self._stock_to_tasks_cache = None  # {stock_code: [task1, task2, ...]}
        self._tasks_cache_version = 0  # 任务缓存版本号，用于检测任务是否变化
        
        # 信号发送防抖：避免频繁发送信号导致UI卡顿
        self._last_signal_time = 0  # 上次发送信号的时间
        self._signal_debounce_interval = 2.0  # 信号防抖间隔（秒），2秒内最多发送一次，减少UI更新频率（从1.0秒增加到2.0秒以优化性能）
        self._pending_positions = None  # 待发送的持仓数据
        self._pending_asset = None  # 待发送的资产数据
        self.last_log_time = 0  # 记录上次输出日志的时间
        self._reconnect_start_time = 0  # 重连开始时间
        self._max_reconnect_time = 120  # 最大重连时间120秒（2分钟）
        self._restart_in_progress = False  # 是否正在重启QMT
        self._last_qmt_restart_time = 0  # 最近一次自动重启时间
        self._qmt_restart_cooldown = 120  # 自动重启冷却时间（秒）
        self._tick_restart_threshold = 30  # 超过该秒数未收到tick则触发自动重启
        self._first_tick_wait_start = time.time()  # 启动后等待首个tick的起始时间
        self._waiting_for_data_since = None  # 状态栏等待数据计时
        # 连接检测防抖：避免单次资产查询超时就触发重连风暴
        self._asset_none_streak = 0
        self._asset_none_reconnect_threshold = 3
        # QMT 重启宽限：脚本执行期间及结束后短暂抑制「None→重连→再自动重启」风暴
        self._restart_grace_until = 0.0
        self._qmt_restart_script_timeout = 240  # 登录脚本 subprocess 超时（秒）
        self._qmt_restart_grace_seconds = 120  # 脚本启动后预留的宽限（秒）
        self._last_restart_none_log_time = 0.0
        self._builtin_trader_hint_logged = False
        # 初始化全局tick时间与缓存
        self.latest_tick_time = 0
        self.latest_tick_time_str = "等待数据"
        self.last_tick_warning_time = {}
        self.stock_last_tick_time = {}
        
        # 已显示的警告记录（用于去重，每个警告最多显示一次）
        self.shown_warnings = set()  # 记录已显示的警告标识

        # 每个交易日早上定时重启 QMT（避免长时间运行导致行情/连接异常）
        self._daily_qmt_restart_hour = 9
        self._daily_qmt_restart_minute = 0
        self._daily_qmt_restart_done_date = None

    def _can_restart_qmt(self):
        """检查是否允许自动重启QMT"""
        if self._restart_in_progress:
            self.logger.info("[自动重启] 跳过：正在重启进行中")
            return False
        cooldown_left = self._qmt_restart_cooldown - (time.time() - self._last_qmt_restart_time)
        if cooldown_left > 0:
            self.logger.info(f"[自动重启] 跳过：冷却中，剩余 {int(cooldown_left)} 秒")
            return False
        return True

    def _in_qmt_restart_grace(self) -> bool:
        """QMT 重启脚本执行中或宽限期内，抑制连接检测触发的重连/自动重启。"""
        return bool(self._restart_in_progress) or time.time() < float(self._restart_grace_until or 0)

    def _relax_xt_trader_health(self) -> bool:
        try:
            from utils.qmt_execution_config import relax_xt_trader_health_check
            return bool(relax_xt_trader_health_check())
        except Exception:
            return False

    def _has_xt_trader_path(self) -> bool:
        return bool(str(getattr(self, "path", "") or "").strip())

    def _load_builtin_account_snapshot(self):
        """builtin 下从 results.json 读取资金/持仓。"""
        try:
            from utils.ant_rules_io_ext import default_paths, load_account_positions_snapshot
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _, results_path = default_paths(root)
            return load_account_positions_snapshot(results_path)
        except Exception:
            return None, {}

    def _log_builtin_trader_optional_once(self) -> None:
        if self._builtin_trader_hint_logged:
            return
        self._builtin_trader_hint_logged = True

    def _ensure_stock_account(self) -> bool:
        """保证 self.account 为 StockAccount，避免 query 时 account_type 报错。"""
        if isinstance(self.account, StockAccount):
            return True
        raw = str(self.account or "").strip()
        if not raw:
            return False
        self.account = StockAccount(raw, "STOCK")
        return True

    def _schedule_post_restart_reconnect(self, tag: str) -> None:
        """登录脚本成功后，延迟重新连接 xt_trader（QMT 进程已就绪但交易通道需重建）。"""
        def _deferred():
            time.sleep(8)
            if self._restart_in_progress:
                return
            self._asset_none_streak = 0
            self.logger.info(f"{tag} 登录脚本已完成，开始重新连接交易通道")
            if not self.is_reconnecting:
                self._start_reconnect()

        threading.Thread(target=_deferred, daemon=True).start()

    def _restart_qmt(self, reason: str, *, bypass_cooldown: bool = False):
        """调用定时器目录下的重启脚本重新登录QMT"""
        try:
            from utils.qmt_execution_config import allow_qmt_client_auto_restart
        except Exception:
            allow_qmt_client_auto_restart = lambda: True  # type: ignore[assignment,misc]
        if not allow_qmt_client_auto_restart():
            tag = "[定时重启]" if bypass_cooldown else "[自动重启]"
            self.logger.info(
                f"{tag} 已跳过（qmt_mode=builtin/standalone，不调用 qmt_login 杀大 QMT）：{reason}"
            )
            return
        if self._restart_in_progress:
            tag = "[定时重启]" if bypass_cooldown else "[自动重启]"
            self.logger.info(f"{tag} 跳过：正在重启进行中")
            return
        if not bypass_cooldown and not self._can_restart_qmt():
            return

        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "定时器",
            "qmt_login.py"
        )

        if not os.path.exists(script_path):
            tag = "[定时重启]" if bypass_cooldown else "[自动重启]"
            self.logger.error(f"{tag} 未找到重启脚本: {script_path}")
            return

        self._restart_in_progress = True
        self._last_qmt_restart_time = time.time()
        self._asset_none_streak = 0
        self._restart_grace_until = (
            time.time() + self._qmt_restart_script_timeout + self._qmt_restart_grace_seconds
        )
        tag = "[定时重启]" if bypass_cooldown else "[自动重启]"
        self.logger.warning(f"{tag} 由于{reason}，准备重新启动QMT（脚本: {script_path}）")

        def _run_restart_script():
            try:
                script_dir = os.path.dirname(script_path)
                self.logger.info(f"{tag} 启动重启脚本：python {os.path.basename(script_path)}，cwd={script_dir}")
                env = os.environ.copy()
                env["AUTO_RESTART"] = "1"
                result = subprocess.run(
                    [sys.executable, script_path],
                    cwd=script_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self._qmt_restart_script_timeout,
                )
                if result.returncode == 0:
                    self.logger.info(f"{tag} 重启脚本执行完成，stdout:\n{result.stdout.strip()}")
                    self._restart_grace_until = time.time() + 90
                    self._schedule_post_restart_reconnect(tag)
                else:
                    self.logger.error(
                        f"{tag} 重启脚本执行失败，返回码 {result.returncode}\n"
                        f"stdout: {result.stdout}\nstderr: {result.stderr}"
                    )
                    self._restart_grace_until = time.time() + 60
            except subprocess.TimeoutExpired:
                self.logger.error(
                    f"{tag} 重启脚本执行超时（>{self._qmt_restart_script_timeout}秒）"
                )
                self._restart_grace_until = time.time() + 60
            except Exception as e:
                self.logger.error(f"{tag} 执行重启脚本失败: {str(e)}")
                self._restart_grace_until = time.time() + 60
            finally:
                self._restart_in_progress = False
                if bypass_cooldown:
                    invalidate_trading_day_cache()

        threading.Thread(target=_run_restart_script, daemon=True).start()

    def _check_daily_qmt_restart(self):
        """每个交易日 9:00 定时重启 QMT（需程序在 9:00 前已运行）。mini 模式专用。"""
        try:
            from utils.qmt_execution_config import allow_qmt_client_auto_restart
        except Exception:
            allow_qmt_client_auto_restart = lambda: True  # type: ignore[assignment,misc]
        if not allow_qmt_client_auto_restart():
            return
        now = datetime.now()
        if (
            now.hour != self._daily_qmt_restart_hour
            or now.minute != self._daily_qmt_restart_minute
        ):
            return

        today = now.date()
        if self._daily_qmt_restart_done_date == today:
            return

        try:
            is_td = is_tradeday(today)
        except Exception:
            is_td = today.weekday() < 5

        if not is_td:
            self._daily_qmt_restart_done_date = today
            return

        if self._restart_in_progress:
            return

        self._daily_qmt_restart_done_date = today
        self.logger.info(
            f"[定时重启] 交易日 {today} "
            f"{self._daily_qmt_restart_hour:02d}:{self._daily_qmt_restart_minute:02d}，"
            f"开始定时重启 QMT"
        )
        self._restart_qmt("交易日早上9点定时重启", bypass_cooldown=True)

    def set_task_manager(self, task_manager):
        """设置任务管理器"""
        #self.logger.info(f"[QMTManager] set_task_manager被调用，task_manager: {task_manager}")
        self.task_manager = task_manager
        
        # 检查订阅线程状态
        #self.logger.info(f"[QMTManager] 检查订阅线程状态...")
        #self.logger.info(f"[QMTManager] subscribe_thread存在: {hasattr(self, 'subscribe_thread')}")
        #if hasattr(self, 'subscribe_thread'):
        #    self.logger.info(f"[QMTManager] subscribe_thread对象: {self.subscribe_thread}")
        #    self.logger.info(f"[QMTManager] subscribe_thread是否存活: {self.subscribe_thread.is_alive()}")
        
        # 确保订阅线程正确启动
        # 如果订阅线程不存在或已停止，重新创建并启动
        if (not hasattr(self, 'subscribe_thread') or 
            not self.subscribe_thread or 
            not self.subscribe_thread.is_alive()):
            
            #self.logger.info("[诊断] [QMTManager] 创建新的订阅线程")
            self.subscribe_thread = SubscribeThread(self)
            #self.logger.info("[诊断] [QMTManager] 启动订阅线程")
            self.subscribe_thread.start()
            #self.logger.info(f"[诊断] [QMTManager] 订阅线程启动完成，is_alive={self.subscribe_thread.is_alive()}")
        else:
            #self.logger.info(f"[诊断] [QMTManager] 订阅线程已存在且运行中，is_alive={self.subscribe_thread.is_alive()}")
            pass
            
        # 检查task_manager是否有任务
        if self.task_manager and hasattr(self.task_manager, 'tasks'):
            #self.logger.info(f"[QMTManager] task_manager任务数量: {len(self.task_manager.tasks)}")
            if self.task_manager.tasks:
                task_stocks = [task.get('stock_code') for task in self.task_manager.tasks.values() if task.get('stock_code')]
                #self.logger.info(f"[QMTManager] 任务股票列表: {task_stocks}")
        else:
            self.logger.warning("[QMTManager] task_manager没有tasks属性或为空")

    def run(self):
        # 绑定重连信号到重连处理方法（在线程启动后绑定）
        self.reconnect_signal.connect(self.handle_reconnect)
        #self.logger.info("[QMTManager] 重连信号已绑定到handle_reconnect方法")
        
        # 初始化重连相关属性
        self.is_reconnecting = False  # 是否正在重连
        self.connection_check_counter = 0  # 连接检测计数器
        self.last_log_time = 0  # 记录上次输出日志的时间
        self._reconnect_completed_time = 0  # 重连完成时间，用于防止重连后立即触发第二次重连
        
        #self.logger.info("[QMTManager] 开始初始化QMT连接...")
        if isinstance(self.account, str):
            self.account = StockAccount(self.account, 'STOCK')

        skip_xt = self._relax_xt_trader_health() and not self._has_xt_trader_path()
        if skip_xt:
            self.xt_trader = None
            self._is_initialized = True
            self._xt_trader_connected = False
            self._log_builtin_trader_optional_once()
        else:
            # 初始化连接
            self.xt_trader = xttrader.XtQuantTrader(self.path, self.session)
            time.sleep(1)
            self.xt_trader.start()

            # 立即设置初始化标志，不阻塞等待连接
            self._is_initialized = True

            # 尝试建立连接，但不阻塞
            try:
                connect_result = self.xt_trader.connect()
                if connect_result == 0:
                    self.logger.info("交易连接建立成功")
                    invalidate_trading_day_cache()
                    # 订阅交易回调
                    subscribe_result = self.xt_trader.subscribe(self.account)
                    if subscribe_result != 0:
                        self.logger.error(f"订阅交易回调失败，错误码: {subscribe_result}")
                        self._xt_trader_connected = False
                    else:
                        # 创建并注册回调对象
                        callback = MyXtQuantTraderCallback(self)
                        self.callback = callback  # 保存引用
                        self.xt_trader.register_callback(callback)

                        # 清空已处理持仓集合
                        self.callback.processed_positions.clear()

                        # 标记xt_trader已真正连接成功
                        self._xt_trader_connected = True
                        #self.logger.info("✅ xt_trader连接并订阅账户成功，可以订阅行情数据")

                        # 图表视图已在main.py的showEvent中初始化，这里不再重复初始化
                else:
                    #self.logger.warning(f"初始连接失败，错误码: {connect_result}，将在后台继续尝试重连")
                    self._xt_trader_connected = False
            except Exception as e:
                self.logger.error(f"初始连接异常: {str(e)}")
                self._xt_trader_connected = False

        # 获取当前持仓的股票，但不主动订阅
        try:
            _, positions = self.get_asset_positions()
            if positions is not None and positions:
                stock_codes = list(positions.keys())
                #self.logger.info(f"当前持仓股票: {stock_codes}")
        except Exception as e:
            self.logger.error(f"获取持仓股票失败: {str(e)}")

        #调试用开始
        # 获取今天的日期
        today = datetime.now().strftime('%Y-%m-%d')
        # 组合成今天9:30:00的时间字符串
        time_str = f"{today} 09:30:00"
        # 转换为毫秒时间戳
        self._debug_timestamp = int(datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
        #调试用结束
        
        # 持续更新持仓信息
        self.last_positions = None  # 用于存储上一次的持仓数据
        
        # 发送信号通知主线程创建定时器
        self.timer_ready_signal.emit()

        # 持续运行，保持线程活跃
        while self.running:
            time.sleep(1)

    def create_timer_in_main_thread(self):
        """在主线程中创建定时器"""
        try:
            self.timer = QTimer()
            self.timer.setInterval(5000)  # 5秒，减少频率
            self.timer.timeout.connect(self.on_timer)
            self.timer.start()
            self._timer_started = True
            try:
                from utils.qmt_execution_config import (
                    allow_qmt_client_auto_restart,
                )
            except Exception:
                allow_qmt_client_auto_restart = lambda: True  # type: ignore[assignment,misc]
            if allow_qmt_client_auto_restart():
                self.logger.info(
                    f"[定时重启] 已启用：每个交易日 "
                    f"{self._daily_qmt_restart_hour:02d}:{self._daily_qmt_restart_minute:02d} "
                    f"自动重启 QMT（需程序在该时刻前已运行）"
                )
        except Exception as e:
            self.logger.error(f"在主线程中创建定时器失败: {str(e)}")

    def _start_timer_safe(self):
        """线程安全的定时器启动方法"""
        try:
            # 创建一个新的定时器对象，确保在主线程中
            from PyQt5.QtCore import QTimer
            self.timer = QTimer()
            self.timer.setInterval(5000)  # 5秒，减少频率
            self.timer.timeout.connect(self.on_timer)
            
            # 使用QMetaObject.invokeMethod在主线程中启动
            QMetaObject.invokeMethod(
                self.timer,
                "start",
                Qt.ConnectionType.QueuedConnection
            )
            self._timer_started = True
            self.logger.info("线程安全定时器启动成功")
        except Exception as e:
            self.logger.error(f"线程安全定时器启动失败: {str(e)}")

    def on_timer(self):
        # 在最开始就记录日志，确保能看到方法是否被调用（仅用于性能诊断）
        import time
        import json
        
        try:
            if not self.running:
                self.logger.warning("[定时器调试] 定时器被停止，running=False")
                return
        except Exception as e:
            self.logger.error(f"[定时器调试] 检查running状态时出错: {str(e)}")
            return
            
        try:
            # 性能监控：开始计时
            timer_start = time.time()
            
            # 检查上次定时器是否超时
            if hasattr(self, '_last_timer_time'):
                time_since_last = time.time() - self._last_timer_time
                if time_since_last > 10:  # 如果距离上次定时器超过10秒，说明上次超时了
                    self.logger.warning(f"[性能监控] 上次定时器超时，间隔: {time_since_last:.1f}秒")
            
            self._last_timer_time = time.time()
            self.connection_check_counter += 1
            current_time = time.time()

            self._check_daily_qmt_restart()

            if self._restart_in_progress:
                return
            
            # 每隔30秒（10个周期）清空一次警告记录，允许重新输出性能警告（便于持续观察）
            if self.connection_check_counter % 10 == 0:
                self.shown_warnings.clear()
            
            # 添加定时器对象状态检查
            if hasattr(self, 'timer'):
                timer_active = self.timer.isActive()
                #self.logger.info(f"[定时器调试] 定时器对象状态: isActive={timer_active}")
                if not timer_active:
                    self.logger.warning("[定时器调试] 定时器对象已停止，尝试重新启动")
                    try:
                        self.timer.start()
                        self.logger.info("[定时器调试] 定时器重新启动成功")
                    except Exception as e:
                        self.logger.error(f"[定时器调试] 定时器重新启动失败: {str(e)}")
            
            # 连接检测 - 每次定时器回调都执行（实际连接检测逻辑会检查超时等）
            # 首先检查重连是否超时
            check_reconnect_start = time.time()
            if self._check_reconnect_timeout():
                self.logger.info("[连接检测] 重连超时，跳过本次检测")
                return
            check_reconnect_time = time.time() - check_reconnect_start

            relax_health = self._relax_xt_trader_health()
            asset = None
            positions = {}
            file_asset = None
            file_pos = {}
            if relax_health:
                self._log_builtin_trader_optional_once()
                try:
                    from utils.ant_rules_io_ext import default_paths, load_account_positions_snapshot
                    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    _, results_path = default_paths(root)
                    file_asset, file_pos = load_account_positions_snapshot(results_path)
                    if file_asset or file_pos:
                        asset = file_asset
                        positions = dict(file_pos or {})
                        self.cached_asset = file_asset or getattr(self, "cached_asset", None)
                        self.cached_positions = positions
                except Exception:
                    pass
                if file_asset or file_pos:
                    relax_health = True
                elif not getattr(self, "xt_trader", None):
                    relax_health = True
                else:
                    relax_health = False

            if not relax_health:
                try:
                    # 资产查询耗时监控
                    asset_start = time.time()

                    # 使用线程执行资产查询，避免阻塞定时器
                    asset_queue = queue.Queue()

                    def query_asset():
                        try:
                            asset = self.xt_trader.query_stock_asset(self.account)
                            asset_queue.put(asset)
                        except Exception as e:
                            asset_queue.put(e)

                    query_thread = threading.Thread(target=query_asset, daemon=True)
                    query_thread.start()

                    # 等待查询结果，最多等待0.8秒，减少阻塞时间
                    try:
                        asset = asset_queue.get(timeout=0.8)
                        if isinstance(asset, Exception):
                            raise asset
                    except queue.Empty:
                        self.logger.debug("[连接检测] 资产查询超时（这是正常的，避免阻塞）")
                        asset = None

                    asset_time = time.time() - asset_start
                    if asset_time > 0.5:
                        self.logger.warning(f"[性能监控] query_stock_asset耗时: {asset_time:.3f}秒")

                    if asset is None:
                        if not self._in_qmt_restart_grace():
                            self._asset_none_streak += 1
                        if self._reconnect_completed_time > 0:
                            time_since_reconnect = time.time() - self._reconnect_completed_time
                            if time_since_reconnect < 3.0:
                                pass
                            else:
                                self._reconnect_completed_time = 0
                            if (
                                not self.is_reconnecting
                                and not self._in_qmt_restart_grace()
                                and self._asset_none_streak >= self._asset_none_reconnect_threshold
                            ):
                                self.logger.warning(
                                    f"[连接检测] 连续{self._asset_none_streak}次资产查询返回None，触发重连"
                                )
                                self._start_reconnect()
                        elif (
                            not self.is_reconnecting
                            and not self._in_qmt_restart_grace()
                            and self._asset_none_streak >= self._asset_none_reconnect_threshold
                        ):
                            self.logger.warning(
                                f"[连接检测] 连续{self._asset_none_streak}次资产查询返回None，触发重连"
                            )
                            self._start_reconnect()
                    else:
                        self._asset_none_streak = 0
                        if current_time - self.last_log_time >= 120:
                            self.last_log_time = current_time
                        if self.is_reconnecting:
                            self.is_reconnecting = False
                            self._reconnect_start_time = 0
                        self._reconnect_completed_time = time.time()
                except Exception as e:
                    self.logger.warning(f"[连接检测] QMT连接检测失败: {str(e)}")
                    if not self._relax_xt_trader_health():
                        if not self.is_reconnecting:
                            self._start_reconnect()
                        else:
                            self.logger.info("[连接检测] 已在重连中，跳过重连")

            if not relax_health:
                # 持仓更新 - 只有在连接正常时才执行
                try:
                    get_pos_start = time.time()
                    asset, positions = self.get_asset_positions()
                    get_pos_time = time.time() - get_pos_start
                    if get_pos_time > 0.5:  # 如果获取持仓耗时超过0.5秒，记录警告
                        self.logger.warning(f"[性能监控] get_asset_positions耗时过长: {get_pos_time:.3f}秒")
                except Exception as e:
                    self.logger.warning(f"[连接检测] 获取资产持仓失败: {str(e)}")
                    return
                
            # 检查positions是否为None，避免NoneType错误
            if positions is None:
                self.logger.warning("[持仓更新] positions为None，跳过持仓更新处理")
                return
            
            # 持仓更新：优化性能，只在持仓真正变化时才更新UI
            step_start = time.time()
            
            # 检查持仓是否有变化（包括价格、市值等变化）
            positions_changed = False
            change_detect_start = time.time()
            if self.task_manager:
                # 优化：建立股票代码到任务的索引，避免嵌套循环 O(n*m) -> O(n+m)
                # 只处理持仓真正变化的股票
                changed_stocks = set()
                if self.last_positions is not None:
                    # 找出持仓变化的股票（新增或数量变化）
                    for stock_code in positions.keys():
                        if stock_code not in self.last_positions:
                            changed_stocks.add(stock_code)  # 新增持仓
                            positions_changed = True
                        else:
                            last_pos = self.last_positions[stock_code]
                            curr_pos = positions[stock_code]
                            # 检查持仓数量、价格、市值等是否有变化
                            if (last_pos.get('can_use_volume', 0) != curr_pos.get('can_use_volume', 0) or
                                last_pos.get('volume', 0) != curr_pos.get('volume', 0) or
                                last_pos.get('market_value', 0) != curr_pos.get('market_value', 0) or
                                last_pos.get('current_price', 0) != curr_pos.get('current_price', 0)):
                                changed_stocks.add(stock_code)  # 持仓数量或价格变化
                                positions_changed = True
                else:
                    # 首次运行，处理所有持仓
                    changed_stocks = set(positions.keys())
                    positions_changed = True
                
                # 检查是否有持仓被清空
                if self.last_positions is not None:
                    for stock_code in self.last_positions.keys():
                        if stock_code not in positions:
                            changed_stocks.add(stock_code)
                            positions_changed = True
                
                # 优化：缓存股票代码到任务的索引，避免每次更新持仓时都重新构建
                # 检查任务是否变化（通过比较任务数量或版本号）
                cache_build_start = time.time()
                current_tasks_count = len(self.task_manager.tasks)
                cache_valid = (self._stock_to_tasks_cache is not None and 
                              self._tasks_cache_version == current_tasks_count)
                
                if not cache_valid:
                    # 重新构建索引
                    stock_to_tasks = {}
                    for task_id, task in self.task_manager.tasks.items():
                        stock_code = task.get('stock_code')
                        if stock_code:
                            if stock_code not in stock_to_tasks:
                                stock_to_tasks[stock_code] = []
                            stock_to_tasks[stock_code].append(task)
                    # 更新缓存
                    self._stock_to_tasks_cache = stock_to_tasks
                    self._tasks_cache_version = current_tasks_count
                else:
                    # 使用缓存的索引
                    stock_to_tasks = self._stock_to_tasks_cache
                cache_build_time = time.time() - cache_build_start
                change_detect_time = time.time() - change_detect_start
                # #region agent log
                try:
                    log_path = os.devnull
                    log_entry = {
                        'sessionId': 'debug-session',
                        'runId': 'run1',
                        'hypothesisId': 'B',
                        'location': 'qmt_adapter.py:478',
                        'message': 'position change detection completed',
                        'data': {'change_detect_time': change_detect_time, 'cache_build_time': cache_build_time, 'changed_stocks_count': len(changed_stocks), 'timestamp': int(time.time() * 1000)}
                    }
                    with open(log_path, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                except: pass
                # #endregion
                
                # 只更新持仓变化的股票对应的任务
                task_update_start = time.time()
                for stock_code in changed_stocks:
                    if stock_code not in positions:
                        continue
                    
                    pos_info = positions[stock_code]
                    current_can_use_volume = pos_info['can_use_volume']
                    
                    # 获取该股票对应的所有任务
                    tasks_for_stock = stock_to_tasks.get(stock_code, [])
                    
                    for task in tasks_for_stock:
                        # 获取上一次的可用持仓数量
                        last_can_use_volume = 0
                        if self.last_positions is not None and stock_code in self.last_positions:
                            last_can_use_volume = self.last_positions[stock_code].get('can_use_volume', 0)
                        
                        # 更新任务的可用持仓数量
                        task['volume'] = current_can_use_volume
                        
                        # 只有在last_positions不为None时才进行持仓变化检测
                        # 避免在首次运行时错误地增加初始持仓
                        if self.last_positions is not None:
                            # 当收到的可用持仓大于当前初始持仓时，说明有新持仓释放
                            # 这时同时更新初始持仓和当前可用持仓
                            if current_can_use_volume > task['init_volume']:
                                old_init_volume = task['init_volume']
                                task['init_volume'] = current_can_use_volume
                                task['volume'] = current_can_use_volume
                                self.logger.info(f"[{stock_code}] 可用持仓增加，初始持仓从{old_init_volume}更新为{current_can_use_volume}")
                            # 当收到的可用持仓小于当前初始持仓时，只更新当前可用持仓
                            elif current_can_use_volume < task['init_volume']:
                                task['volume'] = current_can_use_volume
                                #self.logger.info(f"[{stock_code}] 可用持仓减少，初始持仓保持不变({task['init_volume']})")
                            # 当收到的可用持仓等于当前初始持仓时，只更新当前可用持仓
                            else:
                                task['volume'] = current_can_use_volume
                        else:
                            # 首次运行，只更新当前可用持仓，不修改初始持仓
                            #self.logger.debug(f"[{stock_code}] 首次运行，更新可用持仓为{current_can_use_volume}股")
                            pass
                task_update_time = time.time() - task_update_start
                # #region agent log
                try:
                    log_path = os.devnull
                    log_entry = {
                        'sessionId': 'debug-session',
                        'runId': 'run1',
                        'hypothesisId': 'B',
                        'location': 'qmt_adapter.py:541',
                        'message': 'task update completed',
                        'data': {'task_update_time': task_update_time, 'timestamp': int(time.time() * 1000)}
                    }
                    with open(log_path, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                except: pass
                # #endregion
            
            # 只在持仓真正变化时才发送更新信号，避免不必要的UI更新
            # 使用防抖机制，减少信号发送频率，避免UI卡顿
            # 注意：PyQt信号默认使用QueuedConnection异步发送，但信号接收端（UI更新）处理较慢
            # 因此增加防抖间隔到2.0秒，减少信号发送频率，从而减少UI更新次数
            signal_emit_start = time.time()
            current_time = time.time()
            if positions_changed and asset is not None and positions is not None:
                # 检查是否有模态对话框打开，如果有则跳过信号发送，避免信号积压
                # 当对话框打开时，主线程被阻塞，无法处理信号，会导致信号积压和性能问题
                # 注意：QApplication.activeModalWidget() 是一个轻量级查询，开销很小（<0.1ms）
                active_modal = QApplication.activeModalWidget()
                if active_modal is not None:
                    # 有模态对话框打开，跳过信号发送，保存待发送的数据
                    self._pending_positions = positions
                    self._pending_asset = asset
                    # #region agent log
                    try:
                        log_path = os.devnull
                        log_entry = {
                            'sessionId': 'debug-session',
                            'runId': 'run1',
                            'hypothesisId': 'E',
                            'location': 'qmt_adapter.py:632',
                            'message': 'skipped signal emit due to modal dialog',
                            'data': {'timestamp': int(time.time() * 1000)}
                        }
                        with open(log_path, 'a', encoding='utf-8') as f:
                            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                    except: pass
                    # #endregion
                else:
                    # 检查是否需要发送信号（防抖）
                    time_since_last_signal = current_time - self._last_signal_time
                    if time_since_last_signal >= self._signal_debounce_interval:
                        # 直接发送信号（PyQt信号默认使用QueuedConnection，已经是异步的）
                        # 信号发送本身很快，但接收端（UI更新）处理较慢，因此通过防抖减少发送频率
                        self.position_updated.emit(asset, positions)
                        self._last_signal_time = current_time
                        self._pending_positions = None
                        self._pending_asset = None
                    else:
                        # 保存待发送的数据（总是保存最新的数据）
                        self._pending_positions = positions
                        self._pending_asset = asset
            
            # 检查是否有待发送的信号（防抖间隔已过）
            if self._pending_positions is not None and self._pending_asset is not None:
                current_time = time.time()
                time_since_last_signal = current_time - self._last_signal_time
                # 检查是否有模态对话框打开，如果没有且防抖间隔已过，则发送待发送的信号
                active_modal = QApplication.activeModalWidget()
                if active_modal is None and time_since_last_signal >= self._signal_debounce_interval:
                    # 发送待发送的信号
                    self.position_updated.emit(self._pending_asset, self._pending_positions)
                    self._last_signal_time = current_time
                    self._pending_positions = None
                    self._pending_asset = None
            
            # 优化：使用浅拷贝替代深拷贝，只在持仓变化时才更新last_positions
            # 浅拷贝已经足够，因为持仓信息字典的值（数字、字符串）是不可变类型
            # 对于值中的字典（持仓信息），也使用浅拷贝
            copy_start = time.time()
            if positions_changed or self.last_positions is None:
                if positions:
                    # 使用字典推导式创建浅拷贝，对值中的字典也做浅拷贝
                    self.last_positions = {k: (v.copy() if isinstance(v, dict) else v) for k, v in positions.items()}
                else:
                    self.last_positions = {}
            self.positions = positions
            copy_time = time.time() - copy_start
            signal_emit_time = time.time() - signal_emit_start
            
            step_time = time.time() - step_start
            # 详细记录持仓更新各阶段耗时
            if step_time > 0.3:  # 如果更新任务耗时超过0.3秒，记录警告
                warning_key = "position_update_slow"
                if warning_key not in self.shown_warnings:
                    self.shown_warnings.add(warning_key)
                    self.logger.warning(
                        f"[性能监控] 更新任务持仓耗时过长: {step_time:.3f}秒 "
                        f"(变更检测+索引构建: {change_detect_time:.3f}秒, "
                        f"持仓复制: {copy_time:.3f}秒, 信号发送: {signal_emit_time:.3f}秒, "
                        f"索引构建耗时: {cache_build_time:.3f}秒)"
                    )
            
            # 不再定时查询订单，因为：
            # 1. 订单回报(on_stock_order)已经能实时快速获取订单ID
            # 2. 撤单/下单时会主动查询订单列表
            # 3. 周期性查询会产生大量历史订单回调，导致不必要的处理和日志
            # 改为按需查询：仅在需要时（如撤单、下单）主动调用get_today_orders()
            # if self.connection_check_counter % 10 == 0:  # 每30秒执行一次（3秒间隔 * 10）
            #     try:
            #         self.get_today_orders()
            #     except Exception as e:
            #         self.logger.error(f"[定时器] 查询当日订单失败: {str(e)}")
            
            # 记录定时器总耗时
            timer_total = time.time() - timer_start
            if timer_total > 1.0:  # 如果定时器总耗时超过1秒，记录严重警告
                warning_key = "timer_callback_timeout"
                if warning_key not in self.shown_warnings:
                    self.shown_warnings.add(warning_key)
                    self.logger.error(f"[性能监控] ⚠️ 定时器回调严重超时: {timer_total:.3f}秒")
            elif timer_total > 0.5:  # 如果定时器总耗时超过0.5秒，记录警告
                warning_key = "timer_callback_slow"
                if warning_key not in self.shown_warnings:
                    self.shown_warnings.add(warning_key)
                    self.logger.warning(f"[性能监控] 定时器回调耗时过长: {timer_total:.3f}秒")
                
        except Exception as e:
            self.logger.error(f"更新持仓信息失败: {str(e)}")
        
        # 在定时器周期内也执行一次tick超时检查（即使没有收到任何tick回调也会触发）
        try:
            if hasattr(self, 'subscribe_thread') and self.subscribe_thread:
                self.subscribe_thread._check_tick_timeout()
        except Exception as e:
            self.logger.error(f"[定时器] tick超时检查失败: {str(e)}")

    def get_asset_positions(self):
        """获取资产和持仓信息"""
        # 性能监控：开始计时
        import time
        method_start = time.time()
        
        try:
            # 检查account类型
            if not isinstance(self.account, StockAccount):
                if not self._ensure_stock_account():
                    self.logger.error(f"account类型错误: {type(self.account)}，期望类型: StockAccount")
                    return None, {}
                
            # 检查xt_trader是否初始化
            if not hasattr(self, 'xt_trader') or not self.xt_trader:
                if self._relax_xt_trader_health():
                    asset, positions = self._load_builtin_account_snapshot()
                    if asset or positions:
                        return asset, dict(positions or {})
                self.logger.error("xt_trader未初始化")
                return None, {}
                
            # 获取资产信息
            step_start = time.time()
            
            # 使用线程执行资产查询，避免阻塞
            import queue
            
            asset_queue = queue.Queue()
            def query_asset():
                try:
                    asset = self.xt_trader.query_stock_asset(self.account)
                    asset_queue.put(asset)
                except Exception as e:
                    asset_queue.put(e)
            
            query_thread = threading.Thread(target=query_asset, daemon=True)
            query_thread.start()
            
            # 等待查询结果，最多等待1.5秒，避免长时间阻塞
            try:
                asset = asset_queue.get(timeout=1.5)
                if isinstance(asset, Exception):
                    raise asset
            except queue.Empty:
                self.logger.debug("[get_asset_positions] 资产查询超时（避免阻塞）")
                asset = None
            
            step_time = time.time() - step_start
            if step_time > 1.0:  # 如果查询资产耗时超过1秒，记录警告
                self.logger.warning(f"[性能监控] query_stock_asset耗时过长: {step_time:.3f}秒")
            
            if not asset:
                #self.logger.error("获取资产信息失败: query_stock_asset返回None")
                # 不在这里触发重连，让定时器回调统一处理
                # 这样避免重复触发和可能的死锁
                if self._in_qmt_restart_grace():
                    now = time.time()
                    if now - float(self._last_restart_none_log_time or 0) >= 60:
                        self._last_restart_none_log_time = now
                        self.logger.debug(
                            "[get_asset_positions] QMT重启宽限期内，资产暂不可用"
                        )
                else:
                    self.logger.debug(
                        "[get_asset_positions] 资产查询返回None，将在定时器检测时处理"
                    )
                return None, {}
                
            if not hasattr(asset, 'total_asset'):
                self.logger.error(f"资产信息对象缺少total_asset属性: {asset}")
                return None, {}
                
            # 缓存资产信息，保留所有字段
            self.cached_asset = {
                'total_asset': asset.total_asset,
                'cash': asset.cash,
                'frozen_cash': asset.frozen_cash,
                'market_value': asset.market_value,
                'account_id': asset.account_id
            }
            
            # 获取持仓信息（工作线程 + 超时，避免阻塞 Qt 主线程导致界面卡死）
            step_start = time.time()
            positions = {}
            position_queue = queue.Queue()

            def query_positions():
                try:
                    position_queue.put(self.xt_trader.query_stock_positions(self.account))
                except Exception as e:
                    position_queue.put(e)

            threading.Thread(target=query_positions, daemon=True).start()
            try:
                position_list = position_queue.get(timeout=1.5)
                if isinstance(position_list, Exception):
                    raise position_list
            except queue.Empty:
                self.logger.debug("[get_asset_positions] 持仓查询超时（避免阻塞）")
                cached = getattr(self, "cached_positions", None)
                return self.cached_asset, cached if isinstance(cached, dict) else {}
            except Exception as e:
                self.logger.error(f"获取持仓信息失败: {str(e)}")
                return self.cached_asset, positions

            step_time = time.time() - step_start
            if step_time > 0.2:  # 如果查询持仓耗时超过0.2秒，记录警告
                self.logger.warning(f"[性能监控] query_stock_positions耗时过长: {step_time:.3f}秒")
            
            # 检查新增持仓 - 已禁用自动创建持仓任务功能
            # 用户应该主动决定哪些股票需要任务，而不是系统自动创建
            step_start = time.time()
            if self.task_manager:
                # 注释掉自动创建持仓任务的逻辑
                # 持仓股票不一定需要任务，也可能有多个任务，自动创建一个任务意义不大
                # 用户应该通过UI主动创建需要的任务
                pass
                        #self.logger.info(f"[持仓生成任务] 订阅列表已更新，股票: {stock_codes}")
            else:
                self.logger.warning(f"[持仓检查] task_manager未设置，无法生成任务")
            
            step_time = time.time() - step_start
            if step_time > 0.3:  # 如果处理新增持仓耗时超过0.3秒，记录警告
                #self.logger.warning(f"[性能监控] 处理新增持仓耗时过长: {step_time:.3f}秒")
                pass
            
            # 更新持仓信息
            step_start = time.time()
            for pos in position_list:
                if pos.open_price == float('inf') or pos.open_price == float('-inf'):
                    pos.open_price = 0
                
                # 股票名称只在缓存未命中时查询一次，避免主线程 on_timer 被高频名称解析拖慢
                clean_code = pos.stock_code.split('.')[0] if '.' in pos.stock_code else pos.stock_code
                stock_name = self._stock_name_cache.get(clean_code)
                if not stock_name or stock_name in ("未知名称", "未知"):
                    try:
                        from utils.stock_info_manager import get_stock_name
                        stock_name = get_stock_name(clean_code)
                    except Exception:
                        stock_name = "未知名称"
                    self._stock_name_cache[clean_code] = stock_name
                
                positions[pos.stock_code] = {
                    'account_id': pos.account_id,
                    'stock_code': pos.stock_code,
                    'stock_name': stock_name,
                    'volume': pos.volume,
                    'can_use_volume': pos.can_use_volume,
                    'open_price': pos.open_price,
                    'market_value': pos.market_value
                }
            
            step_time = time.time() - step_start
            if step_time > 0.1:  # 如果更新持仓信息耗时超过0.1秒，记录警告
                self.logger.warning(f"[性能监控] 更新持仓信息耗时过长: {step_time:.3f}秒")
            
            # 缓存持仓信息
            self.cached_positions = positions
            
            # 记录方法总耗时
            method_total = time.time() - method_start
            if method_total > 1.0:  # 如果方法总耗时超过1秒，记录严重警告
                self.logger.error(f"[性能监控] ⚠️ get_asset_positions严重超时: {method_total:.3f}秒")
            elif method_total > 0.5:  # 如果方法总耗时超过0.5秒，记录警告
                self.logger.warning(f"[性能监控] get_asset_positions耗时过长: {method_total:.3f}秒")
            
            return self.cached_asset, positions
        
        except Exception as e:
            self.logger.error(f"获取资产持仓信息失败: {str(e)}")
            return None, {}
    
    def stop(self):
        """停止线程"""
        self.running = False
        
        # 停止订阅线程
        if hasattr(self, 'subscribe_thread'):
            try:
                self.subscribe_thread.stop()
                # 等待订阅线程结束，最多等待3秒
                import time
                start_time = time.time()
                while hasattr(self, 'subscribe_thread') and self.subscribe_thread.is_alive() and time.time() - start_time < 3:
                    time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"停止订阅线程失败: {str(e)}")
        
        # 如果已经初始化，则断开连接
        if self._is_initialized and hasattr(self, 'xt_trader'):
            try:
                self.xt_trader.stop()
                self.logger.info("QMT交易连接已断开")
            except Exception as e:
                self.logger.error(f"断开QMT交易连接失败: {str(e)}")
        
        # 等待线程结束，最多等待5秒
        import time
        start_time = time.time()
        while self.isRunning() and time.time() - start_time < 5:
            time.sleep(0.1)
        
        if self.isRunning():
            self.logger.warning("QMT管理器线程停止超时，强制退出")
            # 强制退出线程
            self.quit()
            self.wait(2000)  # 等待2秒
            if self.isRunning():
                self.terminate()
                self.wait(1000)  # 再等待1秒

    def on_order_callback(self, order):
        """委托回报推送 - 简化处理，直接忽略"""
        try:
            # 只记录基本日志，不做任何处理
            if hasattr(order, 'order_sysid'):
                self.logger.debug(f"[订单回调] 收到订单回调: {order.order_sysid}")
            else:
                self.logger.debug("[订单回调] 收到订单回调，但订单对象无效")
        except Exception as e:
            self.logger.error(f"[订单回调] 处理订单回调时出错: {str(e)}")

    def on_trade_callback(self, trade):
        """成交回报推送"""
        #try:
        #    # 发送信号，让主程序处理成交更新
        #    self.order_updated.emit(trade)
        #except Exception as e:
        #    self.logger.error(f"处理成交回报时出错: {str(e)}")
        pass

    def update_subscribe_stocks(self, stock_codes, force_update=True):
        """更新订阅股票列表"""
        if not hasattr(self, 'subscribe_thread') or self.subscribe_thread is None:
            self.logger.error("订阅线程不存在，无法更新订阅")
            return
        
        # 检查订阅线程是否运行，如果未运行则重新启动
        if not self.subscribe_thread.is_alive():
            try:
                self.subscribe_thread = SubscribeThread(self)
                self.subscribe_thread.start()
                import time
                time.sleep(2)  # 等待线程初始化
            except Exception as e:
                self.logger.error(f"重新启动订阅线程失败: {str(e)}")
                return
        
        # 更新订阅列表
        self.subscribe_thread.update_subscribe_list(stock_codes, force_update=force_update)

    def _order_strategy_cache_key(self, stock_code, order_type, price, volume):
        side = "sell" if order_type == xtconstant.STOCK_SELL else "buy"
        try:
            px = float(price or 0)
        except (TypeError, ValueError):
            px = 0.0
        try:
            vol = int(volume or 0)
        except (TypeError, ValueError):
            vol = 0
        return f"{stock_code}|{side}|{px:.4f}|{vol}"

    def _remember_order_strategy_name(
        self,
        stock_code,
        order_type,
        price,
        volume,
        entrust_id,
        display_name,
    ) -> None:
        if not display_name:
            return
        cache = self._order_display_strategy_names
        key = self._order_strategy_cache_key(stock_code, order_type, price, volume)
        cache[key] = display_name
        if entrust_id:
            cache[f"entrust:{entrust_id}"] = display_name

    def resolve_order_display_strategy_name(self, order) -> str:
        """将 QMT 回报策略名还原为完整「规则类型-交易模式」。"""
        from core.smart_sell import localize_order_display_text, restore_order_display_from_qmt_remark

        qmt_name = getattr(order, "strategy_name", "") or ""
        order_type = getattr(order, "order_type", None)
        side = "sell" if order_type == xtconstant.STOCK_SELL else "buy"
        cache = self._order_display_strategy_names

        sysid = str(getattr(order, "order_sysid", "") or "").strip()
        if sysid.startswith("xt"):
            sysid = sysid[2:]
        for attr in ("order_id", "entrust_no"):
            entrust = getattr(order, attr, None)
            if entrust is not None:
                hit = cache.get(f"entrust:{entrust}")
                if hit:
                    if sysid:
                        cache[f"sysid:{sysid}"] = hit
                    return hit
        if sysid:
            hit = cache.get(f"sysid:{sysid}")
            if hit:
                return hit

        match_key = self._order_strategy_cache_key(
            getattr(order, "stock_code", ""),
            order_type,
            getattr(order, "price", 0),
            getattr(order, "order_volume", 0),
        )
        hit = cache.get(match_key)
        if hit:
            if sysid:
                cache[f"sysid:{sysid}"] = hit
            return hit

        restored = restore_order_display_from_qmt_remark(qmt_name)
        if restored:
            return restored
        return localize_order_display_text(qmt_name, side=side) or qmt_name or "常规"

    def trade(self, stock_code, order_type, price, volume, strategy_name='未知策略'):
        """
        限价单交易（使用同步方式）
        注意：order_stock返回的order_id可能是委托编号，真实的系统订单号需要通过委托回报获取
        """
        try:
            from utils.qmt_execution_config import use_builtin_order_execution

            if use_builtin_order_execution():
                self.logger.warning(
                    f"[{stock_code}] [builtin] 已拒绝 xt_trader 下单"
                    f"（类型={order_type}, 价格={price}, 数量={volume}, 策略={strategy_name}）；"
                    f"请由大 QMT 内置策略 passorder 执行"
                )
                return ""
        except Exception:
            pass

        from core.smart_sell import compact_order_strategy_remark

        account = self.account
        stock_code = stock_code
        order_type = xtconstant.STOCK_SELL if order_type == 'sell' else xtconstant.STOCK_BUY
        order_volume = volume
        price_type = xtconstant.FIX_PRICE
        price = price
        display_strategy_name = str(strategy_name or "")
        strategy_name = compact_order_strategy_remark(display_strategy_name) or display_strategy_name
        order_remark = stock_code

        if not getattr(self, "xt_trader", None):
            self.logger.error(
                f"[{stock_code}] xt_trader未初始化，无法下单"
                f"（类型={'买入' if order_type == xtconstant.STOCK_BUY else '卖出'}, "
                f"价格={price}, 数量={order_volume}）"
            )
            return ""

        self.logger.debug(f"[{stock_code}] 调用同步下单接口: 类型={'买入' if order_type == xtconstant.STOCK_BUY else '卖出'}, 价格={price}, 数量={order_volume}, 策略={strategy_name}")
        
        order_id = self.xt_trader.order_stock(account, stock_code, order_type, order_volume, price_type, price, strategy_name, order_remark)
        
        # 确保order_id是有效的
        if order_id is None:
            order_id = 0

        if order_id > 0 and display_strategy_name:
            self._remember_order_strategy_name(
                stock_code,
                order_type,
                price,
                order_volume,
                str(order_id),
                display_strategy_name,
            )
            
        if order_id > 0:
            self.logger.info(f"[{stock_code}] ✅ 同步下单接口返回成功: 订单号={order_id}, 类型={'买入' if order_type == xtconstant.STOCK_BUY else '卖出'}, 价格={price}, 数量={order_volume}")
            # 注意：这里的order_id可能是委托编号，真实的系统订单号(order_sysid)需要通过委托回报获取
        else:
            self.logger.error(f"[{stock_code}] ❌ 同步下单接口返回失败: 订单号={order_id}, 类型={'买入' if order_type == xtconstant.STOCK_BUY else '卖出'}, 价格={price}, 数量={order_volume}, 策略={strategy_name}")
            # 如果返回-1或其他错误值，说明下单失败，可能原因：
            # 1. 非交易时段下单
            # 2. 资金/持仓不足
            # 3. 下单参数错误
            # 4. QMT连接问题
            
        # 返回订单编号，确保是字符串类型
        # 注意：如果order_id是-1，返回"-1"字符串，调用方需要检查并处理
        return str(order_id) if order_id else ''

    def inspect_order_for_cancel(
        self,
        stock_code: str,
        stored_id=None,
        *,
        price=None,
        volume=None,
        is_sell: bool = True,
        orders=None,
    ):
        """
        查询当日委托，判断订单是否已成交/可撤/已结束。
        返回 (status, order_sysid, traded_volume)，status 为 filled | cancelable | gone | unknown。
        """
        from xtquant import xtconstant

        if orders is None:
            orders = self.get_today_orders() or []

        stored_id = str(stored_id or "").strip()
        price_candidates = []
        if price is not None:
            try:
                px = float(price)
                if px > 0:
                    price_candidates.append(px)
            except (TypeError, ValueError):
                pass

        for order in orders:
            try:
                if getattr(order, "stock_code", "") != stock_code:
                    continue

                order_sysid = getattr(order, "order_sysid", None)
                order_sysid_str = str(order_sysid).strip() if order_sysid else ""
                order_status = getattr(order, "order_status", None)
                order_price = float(getattr(order, "price", 0) or 0)
                order_volume = int(getattr(order, "order_volume", 0) or 0)
                order_type = getattr(order, "order_type", None)
                order_is_sell = order_type == xtconstant.STOCK_SELL
                if order_is_sell != is_sell:
                    continue

                id_fields = [order_sysid_str]
                for attr in ("order_id", "entrust_no", "entrust_id"):
                    val = getattr(order, attr, None)
                    if val is not None:
                        id_fields.append(str(val).strip())

                id_matched = bool(
                    stored_id
                    and any(
                        stored_id == fid
                        or (fid and fid.endswith(stored_id))
                        or (fid and stored_id.endswith(fid))
                        for fid in id_fields
                        if fid
                    )
                )
                price_matched = (
                    any(abs(order_price - px) <= 0.01 for px in price_candidates)
                    if price_candidates
                    else False
                )
                volume_matched = volume is not None and order_volume == int(volume)

                if not (id_matched or (price_matched and volume_matched)):
                    continue

                if order_status == 56:
                    traded = int(getattr(order, "traded_volume", 0) or 0)
                    if traded <= 0:
                        traded = order_volume
                    return "filled", order_sysid_str, traded
                if order_status in (50, 55):
                    return "cancelable", order_sysid_str, 0
                if order_status in (53, 54, 57):
                    return "gone", order_sysid_str, 0
            except Exception:
                continue

        return "unknown", None, 0

    def cancel_order(
        self,
        order_id,
        stock_code=None,
        *,
        price=None,
        volume=None,
        is_sell=None,
        resolve_sysid: bool = True,
    ):
        """撤单。同步下单返回的可能是委托编号，撤单 API 需要 order_sysid（有 stock_code 时会自动解析）。"""
        try:
            # 确保order_id是字符串（券商柜台的合同编号）
            if not isinstance(order_id, str):
                order_id = str(order_id)

            # 验证订单ID有效性（不应该为-1、0或空）
            order_id_stripped = order_id.strip()
            if not order_id_stripped or order_id_stripped == '-1' or order_id_stripped == '0':
                self.logger.error(f"❌ [撤单验证失败] 订单ID无效: {order_id} (类型: {type(order_id)}), 股票={stock_code}")
                return False

            stored_id = order_id_stripped

            # builtin/standalone：无 xt_trader，改写 cancel_requests 由大 QMT passorder.cancel 处理
            try:
                from utils.qmt_execution_config import use_builtin_order_execution

                if use_builtin_order_execution():
                    from utils.cancel_request import enqueue_cancel

                    req_id = enqueue_cancel(
                        order_id_stripped,
                        stock_code=str(stock_code or ""),
                    )
                    if req_id:
                        self.logger.info(
                            f"[builtin] 撤单已写入队列: sysid={order_id_stripped} "
                            f"股票={stock_code} req={req_id}（待大 QMT 执行）"
                        )
                        return True
                    self.logger.error(
                        f"[builtin] 撤单入队失败: sysid={order_id_stripped} 股票={stock_code}"
                    )
                    return False
            except Exception as e:
                self.logger.error(f"[builtin] 撤单入队异常: {e}")
                return False

            if resolve_sysid and stock_code:
                status, sysid, _traded = self.inspect_order_for_cancel(
                    stock_code,
                    stored_id,
                    price=price,
                    volume=volume,
                    is_sell=True if is_sell is None else bool(is_sell),
                )
                if status == "filled":
                    self.logger.info(
                        f"撤单跳过: 委托已成交 stored={stored_id} sysid={sysid or stored_id} 股票={stock_code}"
                    )
                    return True
                if status == "gone":
                    self.logger.info(
                        f"撤单跳过: 委托已结束 stored={stored_id} sysid={sysid or stored_id} 股票={stock_code}"
                    )
                    return True
                if sysid:
                    order_id_stripped = str(sysid).strip()
                    if order_id_stripped != stored_id:
                        self.logger.info(
                            f"撤单ID解析: {stored_id} -> {order_id_stripped} 股票={stock_code}"
                        )

            self.logger.info(f"🔄 [撤单调用] 订单ID: {order_id_stripped} (类型: {type(order_id_stripped)}), 股票: {stock_code}")

            if not getattr(self, "xt_trader", None):
                self.logger.error(
                    f"xt_trader未初始化，无法撤单: 订单号={order_id_stripped}, 股票={stock_code}"
                )
                return False

            # 根据股票代码确定市场类型（支持 513xxx.SH 等 ETF 后缀）
            market = xtconstant.SH_MARKET
            if stock_code:
                code_u = str(stock_code).strip().upper()
                bare = code_u.split(".")[0]
                if code_u.endswith(".SZ") or bare.startswith(("0", "1", "2", "3")):
                    market = xtconstant.SZ_MARKET
                elif code_u.endswith((".SH", ".SS")) or bare.startswith(("5", "6", "9")):
                    market = xtconstant.SH_MARKET
                else:
                    self.logger.warning(f"无法确定股票{stock_code}的市场类型，默认使用上海市场")
            else:
                self.logger.warning(f"未提供股票代码，默认使用上海市场进行撤单")

            # 调用QMT的撤单接口
            result = self.xt_trader.cancel_order_stock_sysid(self.account, market, order_id_stripped)

            if result == 0:
                self.logger.info(f"撤单接口调用成功: 订单号={order_id_stripped}, 市场={market}, 股票={stock_code}，等待撤单结果回调")
                # 注意：这里返回True表示撤单接口调用成功，但实际撤单结果需要等待回调
                return True
            else:
                self.logger.error(f"撤单接口调用失败: 订单号={order_id_stripped}, 市场={market}, 股票={stock_code}, 错误码={result}")
                return False

        except Exception as e:
            self.logger.error(f"撤单异常: 订单号={order_id}, 股票={stock_code}, 错误={str(e)}")
            return False

    def get_stock_position(self, stock_code):
        """获取指定股票的持仓信息"""
        if stock_code in self.cached_positions:
            pos = self.cached_positions[stock_code]
            return {
                'volume': pos['volume'],
                'can_use_volume': pos['can_use_volume'],
                'open_price': pos['open_price']
            }

        try:
            if self._relax_xt_trader_health() and self.cached_positions:
                pos = self.cached_positions.get(stock_code)
                if isinstance(pos, dict):
                    return {
                        'volume': pos.get('volume'),
                        'can_use_volume': pos.get('can_use_volume'),
                        'open_price': pos.get('open_price'),
                    }
        except Exception:
            pass

        try:
            if hasattr(self, 'xt_trader') and self.xt_trader and self._ensure_stock_account():
                position_list = self.xt_trader.query_stock_positions(self.account)
                for pos in position_list:
                    if pos.stock_code == stock_code:
                        self.logger.info(f"[{stock_code}] 从QMT重新获取到持仓信息: volume={pos.volume}, can_use_volume={pos.can_use_volume}, open_price={pos.open_price}")
                        return {
                            'volume': pos.volume,
                            'can_use_volume': pos.can_use_volume,
                            'open_price': pos.open_price
                        }
        except Exception as e:
            self.logger.error(f"[{stock_code}] 重新获取持仓信息失败: {str(e)}")
        
        return None

    def get_asset(self):
        """从缓存获取资产信息"""
        if hasattr(self, 'cached_asset'):
            return self.cached_asset
        return None
    
    def _check_connection_status(self):
        """检测QMT连接状态"""
        try:
            if not hasattr(self, 'xt_trader') or not self.xt_trader:
                self.logger.debug("[连接检测] xt_trader未初始化")
                return False
            
            # 检查xt_trader是否有效
            if self.xt_trader is None:
                self.logger.debug("[连接检测] xt_trader为None")
                return False
            
            # 检查account是否有效
            if not hasattr(self, 'account') or self.account is None:
                self.logger.debug("[连接检测] account无效")
                return False
            
            # 尝试获取资产信息来检测连接状态，添加超时机制
            result_queue = queue.Queue()
            
            def check_connection():
                try:
                    asset = self.xt_trader.query_stock_asset(self.account)
                    result_queue.put(asset is not None)
                except Exception as e:
                    self.logger.debug(f"[连接检测] 检测连接状态时出错: {str(e)}")
                    result_queue.put(False)
            
            # 启动检测线程
            check_thread = threading.Thread(target=check_connection, daemon=True)
            check_thread.start()
            
            # 等待结果，最多等待2秒
            try:
                result = result_queue.get(timeout=2)
                if result:
                    self.logger.debug("[连接检测] 连接正常，能获取到资产信息")
                else:
                    self.logger.debug("[连接检测] 连接异常，无法获取资产信息")
                return result
            except queue.Empty:
                self.logger.debug("[连接检测] 检测超时，认为连接异常")
                return False
        except Exception as e:
            self.logger.debug(f"[连接检测] 检测连接状态时出错: {str(e)}")
            return False
    
    def _handle_connection_lost(self):
        """处理连接断开"""
        try:
            self.logger.warning("[连接检测] QMT连接断开，重置连接状态")
            self._is_initialized = False
            
            # 处理订阅线程的重连
            if hasattr(self, 'subscribe_thread'):
                self.subscribe_thread.handle_reconnection()
                
        except Exception as e:
            self.logger.error(f"[连接检测] 处理连接断开失败: {str(e)}")
    
    def _handle_connection_restored(self):
        """处理连接恢复"""
        try:
            self.logger.info("[连接检测] QMT连接恢复，开始重新初始化")
            
            # 重新初始化xt_trader
            if not hasattr(self, 'xt_trader') or self.xt_trader is None:
                self.logger.info("[连接检测] 重新创建xt_trader对象")
                self.xt_trader = xttrader.XtQuantTrader(self.path, self.session)
                time.sleep(1)
                self.xt_trader.start()
                
                # 重新建立连接
                connect_result = -1
                retry_count = 0
                while connect_result != 0 and retry_count < 10:
                    connect_result = self.xt_trader.connect()
                    if connect_result != 0:
                        self.logger.warning(f"[连接检测] 重新连接失败，错误码: {connect_result}")
                    time.sleep(1)
                    retry_count += 1
                
                if connect_result == 0:
                    self.logger.info("[连接检测] 重新连接成功")
                    # 重新订阅交易回调
                    subscribe_result = self.xt_trader.subscribe(self.account)
                    if subscribe_result != 0:
                        self.logger.error(f"[连接检测] 重新订阅交易回调失败，错误码: {subscribe_result}")
                        self._xt_trader_connected = False
                    else:
                        # 重新注册回调
                        if hasattr(self, 'callback'):
                            self.xt_trader.register_callback(self.callback)
                        # 标记xt_trader已真正连接成功
                        self._xt_trader_connected = True
                        self.logger.info("[连接检测] ✅ xt_trader重新连接并订阅账户成功")
                else:
                    self.logger.error("[连接检测] 重新连接失败，放弃重连")
                    return
            
            self._is_initialized = True
            
            # 重新加载任务
            if self.task_manager:
                # 移除重复的任务加载，因为任务已经在初始化时加载过了
                # self.task_manager.load_tasks()
                # 触发任务恢复
                self.task_manager.handle_trading_reconnection()
                
                # 重新订阅行情数据
                if hasattr(self, 'subscribe_thread'):
                    try:
                        self.logger.info("[连接检测] 开始重新订阅行情数据")
                        self.subscribe_thread.handle_reconnection()
                        
                        # 获取需要订阅的股票代码
                        stock_codes = set()
                        
                        # 添加持仓股票
                        if hasattr(self, 'positions') and self.positions:
                            stock_codes.update(self.positions.keys())
                        
                        # 添加任务中的股票（包括夜市任务）
                        for task in self.task_manager.tasks.values():
                            stock_code = task.get('stock_code')
                            if stock_code:
                                stock_codes.add(stock_code)
                        
                        if stock_codes:
                            self.logger.info(f"[连接检测] 重新订阅行情: {stock_codes}")
                            self.subscribe_thread.update_subscribe_list(list(stock_codes), force_update=True)
                        else:
                            self.logger.warning("[连接检测] 没有需要订阅的股票")
                    except Exception as e:
                        self.logger.error(f"[连接检测] 重新订阅行情失败: {str(e)}")
                        
            # 查询并显示当日所有订单
            self.logger.info("连接恢复后查询当日订单...")
            self.get_today_orders()
            
            # 通知主窗口重新启动订单列表定时器
            if hasattr(self, 'connection_restored_signal'):
                self.connection_restored_signal.emit()
            
        except Exception as e:
            self.logger.error(f"[连接检测] 处理连接恢复失败: {str(e)}")

    def _try_reconnect(self):
        """尝试重连"""
        try:
            self.logger.info("[重连] 开始重连流程")
            
            # 添加重连超时保护
            reconnect_start_time = time.time()
            max_reconnect_time = self._max_reconnect_time
            
            def reconnect_watchdog():
                """重连看门狗，防止重连过程卡住"""
                while time.time() - reconnect_start_time < max_reconnect_time:
                    time.sleep(2)
                    if not self.is_reconnecting:
                        return  # 重连已完成
                
                # 超时强制恢复
                self.logger.error(f"[重连] 重连超时{max_reconnect_time}秒，强制恢复")
                self.is_reconnecting = False
                self._is_initialized = True  # 强制设置为已初始化，避免UI卡死
                # 统一触发QMT重启
                try:
                    if self._in_qmt_restart_grace():
                        self.logger.info("[自动重启] 跳过：QMT重启宽限期内")
                    else:
                        self._restart_qmt(f"重连超时{max_reconnect_time}秒")
                except Exception as e:
                    self.logger.error(f"[重连] 触发QMT重启失败: {str(e)}")
            
            watchdog_thread = threading.Thread(target=reconnect_watchdog, daemon=True)
            watchdog_thread.start()
            
            # 先停止现有连接
            try:
                if hasattr(self, 'xt_trader') and self.xt_trader:
                    # 使用线程执行stop操作，避免阻塞
                    stop_queue = queue.Queue()
                    def stop_connection():
                        try:
                            self.xt_trader.stop()
                            stop_queue.put(True)
                        except Exception as e:
                            self.logger.error(f"[重连] 停止连接异常: {str(e)}")
                            stop_queue.put(e)
                    
                    stop_thread = threading.Thread(target=stop_connection, daemon=True)
                    stop_thread.start()
                    
                    # 等待停止操作完成，最多等待3秒
                    try:
                        result = stop_queue.get(timeout=3)
                        if isinstance(result, Exception):
                            self.logger.warning(f"[重连] 停止连接时出错: {str(result)}")
                    except queue.Empty:
                        self.logger.warning("[重连] 停止连接超时，继续重连流程")
                    
                    time.sleep(0.5)  # 等待连接完全停止
            except Exception as e:
                self.logger.warning(f"[重连] 停止现有连接时出错: {str(e)}")
            
            # 重新生成session
            self.session = int(time.time())
            
            # 重置连接
            try:
                self.xt_trader = xttrader.XtQuantTrader(self.path, self.session)
                time.sleep(0.2)
                self.xt_trader.start()
                time.sleep(0.2)
            except Exception as e:
                self.logger.error(f"[重连] 创建xt_trader对象失败: {str(e)}")
                return
            
            # 重新建立连接
            connect_result = -1
            retry_count = 0
            max_retries = 5
            
            while connect_result != 0 and retry_count < max_retries:
                retry_count += 1
                
                # 使用线程执行连接操作，避免阻塞
                connect_queue = queue.Queue()
                def connect_operation():
                    try:
                        result = self.xt_trader.connect()
                        connect_queue.put(result)
                    except Exception as e:
                        self.logger.error(f"[重连] connect()调用异常: {str(e)}")
                        connect_queue.put(e)
                
                connect_thread = threading.Thread(target=connect_operation, daemon=True)
                connect_thread.start()
                
                # 等待连接结果，最多等待5秒
                try:
                    result = connect_queue.get(timeout=5)
                    if isinstance(result, Exception):
                        connect_result = -1
                    else:
                        connect_result = result
                except queue.Empty:
                    self.logger.warning(f"[重连] 连接操作超时 (第{retry_count}次)")
                    connect_result = -1
                
                if connect_result != 0:
                    if retry_count < max_retries:
                        time.sleep(1)
                    else:
                        self.logger.error(f"[重连] 已达到最大重试次数{max_retries}，退出重试循环")
                else:
                    self.logger.info(f"[重连] 连接成功 (第{retry_count}次尝试)")
                    break
            
            if connect_result == 0:
                # 重新订阅交易回调
                try:
                    subscribe_result = self.xt_trader.subscribe(self.account)
                    if subscribe_result != 0:
                        self.logger.error(f"[重连] 重新订阅交易回调失败，错误码: {subscribe_result}")
                        self._xt_trader_connected = False
                    else:
                        # 重新注册回调
                        try:
                            if hasattr(self, 'callback'):
                                self.xt_trader.register_callback(self.callback)
                        except Exception as e:
                            self.logger.error(f"[重连] 注册回调异常: {str(e)}")
                        
                        # 标记xt_trader已真正连接成功
                        self._xt_trader_connected = True
                        self.logger.info("[重连] ✅ xt_trader重新连接并订阅账户成功")
                        invalidate_trading_day_cache()
                except Exception as e:
                    self.logger.error(f"[重连] 订阅交易回调异常: {str(e)}")
                    self._xt_trader_connected = False
                
                # 设置初始化标志
                self._is_initialized = True
                
                # 重连成功后，在后台线程中更新持仓信息和UI，避免阻塞
                def update_after_reconnect():
                    try:
                        asset, positions = self.get_asset_positions()
                        if asset is not None and positions is not None:
                            self.logger.info(f"[重连] 获取到持仓信息，股票数量: {len(positions)}")
                            self.position_updated.emit(asset, positions)
                        else:
                            self.logger.warning("[重连] 获取持仓信息失败")
                    except Exception as e:
                        self.logger.error(f"[重连] 更新持仓信息失败: {str(e)}")
                    
                    # 查询当日订单
                    try:
                        self.get_today_orders()
                    except Exception as e:
                        self.logger.error(f"[重连] 查询当日订单失败: {str(e)}")
                    
                    # 更新订阅线程
                    if hasattr(self, 'subscribe_thread'):
                        try:
                            self.subscribe_thread.handle_reconnection()
                        except Exception as e:
                            self.logger.error(f"[重连] 更新订阅线程失败: {str(e)}")
                
                # 在后台线程中执行更新操作
                update_thread = threading.Thread(target=update_after_reconnect, daemon=True)
                update_thread.start()
                
                self.logger.info("[重连] 重连成功，恢复流程完成")
            else:
                elapsed = time.time() - reconnect_start_time
                self.logger.error(f"[重连] 重连失败，错误码: {connect_result}，重试次数: {retry_count}/{max_retries}，耗时: {elapsed:.1f}秒")
                self.logger.info("[重连] 将在下次定时器检测时继续尝试重连")
                # 统一触发QMT重启（与状态栏/无tick策略对齐）
                try:
                    if self._in_qmt_restart_grace():
                        self.logger.info("[自动重启] 跳过：QMT重启宽限期内")
                    else:
                        self._restart_qmt(f"重连失败，错误码 {connect_result}")
                except Exception as e:
                    self.logger.error(f"[重连] 触发QMT重启失败: {str(e)}")
        except Exception as e:
            self.logger.error(f"[重连] 重连异常: {str(e)}")
            import traceback
            self.logger.error(f"[重连] 详细错误: {traceback.format_exc()}")
        finally:
            self.is_reconnecting = False
            self._reconnect_start_time = 0
            self._reconnect_completed_time = time.time()

    def handle_reconnect(self):
        """处理重连信号，失败后持续重试，不退出程序"""
        # 在后台线程中执行重连，避免阻塞UI
        def reconnect_in_background():
            try:
                # 先停止现有连接
                try:
                    if hasattr(self, 'xt_trader') and self.xt_trader:
                        self.logger.info("[重连信号] 停止现有连接...")
                        self.xt_trader.stop()
                        time.sleep(2)  # 等待连接完全停止
                        self.logger.info("[重连信号] 现有连接已停止")
                except Exception as e:
                    self.logger.warning(f"[重连信号] 停止现有连接时出错: {str(e)}")
                
                # 重新创建xt_trader对象
                try:
                    self.logger.info("[重连信号] 重新创建xt_trader对象...")
                    self.xt_trader = xttrader.XtQuantTrader(self.path, self.session)
                    time.sleep(1)
                    self.xt_trader.start()
                    time.sleep(1)
                    self.logger.info("[重连信号] xt_trader对象创建成功")
                except Exception as e:
                    self.logger.error(f"[重连信号] 重新创建xt_trader失败: {str(e)}")
                    return
                
                # 重新建立连接
                self.logger.info("[重连信号] 开始尝试连接...")
                connect_result = -1
                retry_count = 0
                max_retries = 5
                
                while connect_result != 0 and retry_count < max_retries:
                    try:
                        self.logger.info(f"[重连信号] 尝试连接 (第{retry_count + 1}次)...")
                        connect_result = self.xt_trader.connect()
                        self.logger.info(f"[重连信号] connect()调用完成，返回值: {connect_result}")
                    except Exception as e:
                        self.logger.error(f"[重连信号] connect()调用异常: {str(e)}")
                        connect_result = -1
                    
                    if connect_result != 0:
                        self.logger.warning(f"[重连信号] 连接失败，错误码: {connect_result}")
                        time.sleep(3)
                        retry_count += 1
                    else:
                        self.logger.info("[重连信号] 连接成功")
                        break
                
                if connect_result == 0:
                    self.logger.info("[重连信号] 连接建立成功，开始后续处理...")
                    
                    # 重新订阅交易回调
                    try:
                        subscribe_result = self.xt_trader.subscribe(self.account)
                        if subscribe_result != 0:
                            self.logger.error(f"[重连信号] 订阅交易回调失败，错误码: {subscribe_result}")
                            self._xt_trader_connected = False
                        else:
                            self.logger.info("[重连信号] 订阅交易回调成功")
                            # 重新注册回调
                            try:
                                if hasattr(self, 'callback'):
                                    self.xt_trader.register_callback(self.callback)
                                    self.logger.info("[重连信号] 注册回调成功")
                            except Exception as e:
                                self.logger.error(f"[重连信号] 注册回调异常: {str(e)}")
                            
                            # 标记xt_trader已真正连接成功
                            self._xt_trader_connected = True
                            self.logger.info("[重连信号] ✅ xt_trader重新连接并订阅账户成功")
                    except Exception as e:
                        self.logger.error(f"[重连信号] 订阅交易回调异常: {str(e)}")
                        self._xt_trader_connected = False
                    
                    # 设置初始化标志
                    self._is_initialized = True
                    self.logger.info("[重连信号] 重连处理完成")
                else:
                    self.logger.error("[重连信号] 连接失败，重连处理结束")
                    self._is_initialized = False
                    
            except Exception as e:
                self.logger.error(f"[重连信号] 重连处理失败: {str(e)}")
                import traceback
                self.logger.error(f"[重连信号] 详细错误信息: {traceback.format_exc()}")
                self._is_initialized = False
        
        # 在后台线程中执行重连
        reconnect_thread = threading.Thread(target=reconnect_in_background, daemon=True)
        reconnect_thread.start()

    def get_today_orders(self):
        """查询当日所有订单（过滤掉跨日残留）"""
        try:
            #self.logger.info("开始查询当日所有订单...")
            
            # 检查xt_trader是否初始化
            if not hasattr(self, 'xt_trader') or not self.xt_trader:
                self.logger.warning("xt_trader未初始化，无法查询订单")
                return []
            
            # 确保account是StockAccount对象
            if isinstance(self.account, str):
                from xtquant.xttype import StockAccount
                self.account = StockAccount(self.account, 'STOCK')
                #self.logger.info(f"已将account字符串转换为StockAccount对象: {self.account}")
            
            
            # 获取当日所有订单
            orders = self.xt_trader.query_stock_orders(self.account)
            
            # 先处理订单并发送信号（用于显示订单列表）
            kept = []
            if orders:
                try:
                    from utils.order_session import is_current_session_order
                except Exception:
                    is_current_session_order = None

                # 处理每个订单
                for i, order in enumerate(orders):
                    try:
                        if is_current_session_order is not None:
                            ot = getattr(order, 'order_time', None)
                            # 部分字段可能带完整日期
                            at = getattr(order, 'order_date', None) or getattr(order, 'insert_time', None)
                            if not is_current_session_order(order_time=ot, at=at):
                                continue
                        
                        # 构建订单信息，与main.py中的handle_order_update保持一致
                        stock_code = getattr(order, 'stock_code', None)
                        if not stock_code:
                            self.logger.warning(f"订单对象缺少stock_code，跳过: {order}")
                            continue
                        
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
                        strategy_name = getattr(order, 'strategy_name', '')
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
                        
                        # 发送订单更新信号（仅用于显示订单列表）
                        self.order_updated.emit(order)
                        kept.append(order)
                        
                        # 注：已删除订单匹配任务和更新任务状态的逻辑
                        # 现在任务在执行时就已经标记为完成，不需要根据订单状态来匹配任务
                        
                        #self.logger.info(f"处理订单: {order.order_sysid}, 状态: {order_status}")
                        
                    except Exception as e:
                        self.logger.error(f"处理订单 {getattr(order, 'order_sysid', '?')} 时出错: {str(e)}")
                        continue
            #else:
            #    self.logger.info("当日没有订单")
            
            # 返回当日订单对象列表（供_find_order_by_info等使用）
            return kept
                
        except Exception as e:
            self.logger.error(f"查询当日订单失败: {str(e)}")
            import traceback
            self.logger.error(f"查询订单错误详情: {traceback.format_exc()}")
            return []  # 异常时返回空列表

    def _update_task_status_from_order(self, order):
        """根据委托回报更新任务状态 - 使用A+A方案匹配任务和订单"""
        try:
            # 添加调试信息，显示订单对象的详细属性
            # 尝试获取订单时间的不同方式
            order_time_attr = getattr(order, 'order_time', None)
            
            # 将XtOrder对象转换为字典格式
            order_dict = {
                'order_id': getattr(order, 'order_sysid', ''),
                'stock_code': getattr(order, 'stock_code', ''),
                'order_type': getattr(order, 'order_type', 0),
                'price': getattr(order, 'price', 0),
                'order_volume': getattr(order, 'order_volume', 0),
                'order_status': getattr(order, 'order_status', ''),
                'order_time': getattr(order, 'order_time', '')
            }
            
            
            # 解析订单信息
            order_sysid = order_dict.get('order_id', '')
            stock_code = order_dict.get('stock_code', '')
            order_type = order_dict.get('order_type', 0)
            order_price = order_dict.get('price', 0)
            order_volume = order_dict.get('order_volume', 0)
            order_status_code = order_dict.get('order_status', '')
            
            # 状态映射
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
            status_text = status_map.get(order_status_code, '未知')
            if status_text == '未知':
                try:
                    tv = int(order_dict.get('traded_volume') or 0)
                    ov = int(order_dict.get('order_volume') or 0)
                except (TypeError, ValueError):
                    tv, ov = 0, 0
                if ov > 0 and tv >= ov:
                    status_text = '已成'
                elif tv > 0:
                    status_text = '部成'
                else:
                    status_text = '已报'
            
            # 解析订单时间
            order_time = None
            if 'order_time' in order_dict and order_dict['order_time']:
                try:
                    # 检查是否是整数时间戳
                    if isinstance(order_dict['order_time'], int):
                        # 整数时间戳，转换为datetime
                        order_time = datetime.fromtimestamp(order_dict['order_time'])
                        #self.logger.info(f"[{stock_code}] 成功解析整数时间戳: {order_dict['order_time']} -> {order_time}")
                    else:
                        # 字符串格式，尝试解析
                        order_time = datetime.fromisoformat(order_dict['order_time'].replace('Z', '+00:00'))
                        #self.logger.info(f"[{stock_code}] 成功解析字符串时间: {order_dict['order_time']} -> {order_time}")
                except Exception as e:
                    self.logger.warning(f"[{stock_code}] 解析订单时间失败: {order_dict['order_time']}, 错误: {e}")
            else:
                self.logger.warning(f"[{stock_code}] 订单时间为空或不存在: order_dict['order_time'] = {order_dict.get('order_time', 'None')}")
            
            #self.logger.info(f"[{stock_code}] 收到委托回报: 订单号={order_sysid}, 类型={order_type}, 价格={order_price}, 数量={order_volume}, 状态={status_text}({order_status_code})")
            
            # 使用A+A方案匹配任务和订单
            matched_task_id = None
            #self.logger.info(f"[{stock_code}] 开始匹配订单，订单信息: 类型={order_type}, 价格={order_price}, 数量={order_volume}, 状态={status_text}")
            
            for task_id, task in self.task_manager.tasks.items():
                # 条件1: 修改为检查运行中或已委托的夜市任务
                if task.get('status') not in ['运行中', '已委托'] or '夜市' not in task.get('strategy', ''):
                    #self.logger.info(f"[{stock_code}] 任务{task_id}不匹配条件1: 状态={task.get('status')}, 策略={task.get('strategy')}")
                    continue
                #self.logger.info(f"[DEBUG+++++++++++++++++++++++++++++] 任务: {task}")
                # 条件2: 股票代码必须匹配
                if task.get('stock_code') != stock_code:
                    #self.logger.info(f"[{stock_code}] 任务{task_id}不匹配条件2: 任务股票={task.get('stock_code')}, 订单股票={stock_code}")
                    continue
                #self.logger.info(f"[DEBUG+++++++++++++++++++++++++++++] 股票代码匹配{stock_code}")
                '''# 条件3: 委托时间在任务开始前的不用考虑
                # 从running_tasks中获取start_time，而不是从tasks中获取
                task_start_time = None
                if task_id in self.task_manager.running_tasks:
                    task_start_time = self.task_manager.running_tasks[task_id].get('start_time')
                
                self.logger.info(f"[DEBUG？？？？？？？？？？？] 任务开始时间: {task_start_time},订单时间：{order_time}")

                if task_start_time and order_time:
                    try:
                        if isinstance(task_start_time, str):
                            task_start_time = datetime.fromisoformat(task_start_time.replace('Z', '+00:00'))
                        
                        # 允许1秒的误差，避免微秒级别的差异导致误判
                        time_diff = (task_start_time - order_time).total_seconds()
                        if time_diff > 1:  # 只有当任务开始时间比订单时间晚1秒以上时才跳过
                            #self.logger.info(f"[{stock_code}] 订单时间 {order_time} 早于任务开始时间 {task_start_time} ({time_diff:.3f}秒)，跳过")
                            continue
                        else:
                            self.logger.info(f"[|||||||||||||||||||||||{stock_code}] 时间比较通过: 订单时间 {order_time}, 任务开始时间 {task_start_time}, 时间差 {time_diff:.3f}秒")
                            pass
                    except Exception as e:
                        self.logger.warning(f"[{stock_code}] 时间比较失败: {e}")
                elif not order_time:
                    self.logger.info(f"[{stock_code}] 订单时间为空，跳过时间比较")
                elif not task_start_time:
                    self.logger.info(f"[{stock_code}] 任务开始时间为空，跳过时间比较")
                '''    
                # 条件4: 比较买卖类型
                task_type = task.get('params', {}).get('task_type', '')
                if not task_type:
                    # 如果params中没有task_type，根据策略名称推断
                    strategy = task.get('strategy', '')
                    if '夜市买入' in strategy:
                        task_type = 'buy'
                    elif '夜市卖出' in strategy:
                        task_type = 'sell'
                
                #self.logger.info(f"[{stock_code}] 任务{task_id}的task_type: {task_type}, 订单类型: {order_type}")
                
                if task_type == 'buy' and order_type != 23:  # 买入
                    self.logger.info(f"[{stock_code}] 买卖类型不匹配: 任务{task_type} vs 订单{order_type}")
                    continue
                elif task_type == 'sell' and order_type != 24:  # 卖出
                    self.logger.info(f"[{stock_code}] 买卖类型不匹配: 任务{task_type} vs 订单{order_type}")
                    continue
                
                # 条件5: 比较数量（允许一定的误差）
                task_volume = 0
                if task_type == 'buy':
                    params = task.get('params', {})
                    task_volume = params.get('buy_volume', 0)
                elif task_type == 'sell':
                    params = task.get('params', {})
                    task_volume = params.get('sell_volume', 0)
                
                if abs(task_volume - order_volume) > 100:  # 允许100股的误差
                    self.logger.info(f"[{stock_code}] 数量不匹配: 任务{task_volume} vs 订单{order_volume}")
                    continue
                
                # 条件6: 比较价格（允许一定的误差）
                task_price = 0
                if task_type == 'buy':
                    params = task.get('params', {})
                    # 对于夜市任务，优先使用base_price，如果没有则使用params中的buy_price
                    if '夜市' in task.get('strategy', ''):
                        task_price = task.get('base_price', 0)
                        if task_price == 0:
                            task_price = params.get('buy_price', 0)
                    else:
                        task_price = params.get('buy_price', 0)
                elif task_type == 'sell':
                    params = task.get('params', {})
                    # 对于夜市任务，优先使用base_price，如果没有则使用params中的sell_price
                    if '夜市' in task.get('strategy', ''):
                        task_price = task.get('base_price', 0)
                        if task_price == 0:
                            task_price = params.get('sell_price', 0)
                    else:
                        task_price = params.get('sell_price', 0)
                
                if abs(task_price - order_price) > 0.01:  # 允许1分钱的误差
                    self.logger.info(f"[{stock_code}] 价格不匹配: 任务{task_price} vs 订单{order_price}")
                    continue
                
                # 所有条件都匹配，认为是这个任务的订单
                matched_task_id = task_id
                #self.logger.info(f"[{stock_code}] A+A方案匹配成功: 任务{task_id} -> 订单{order_sysid}")
                break
            
            # 如果找到了匹配的任务，更新任务状态
            if matched_task_id:
                task = self.task_manager.tasks[matched_task_id]
                
                # 检查状态是否真的发生了变化
                old_status = task.get('status', '')
                
                # 修复逻辑：只要查询到订单就应该将任务状态改为"已委托"
                # 因为收到订单就说明任务已经委托了
                if '夜市' in task.get('strategy', ''):
                    # 检查是否是交易日
                    is_trading_day_today = is_tradeday(datetime.now().date())
                    
                    if is_trading_day_today:
                        # 交易日的夜市任务：设为已委托
                        new_status = '已委托'
                    else:
                        # 非交易日的夜市任务：设为可能已委托
                        new_status = '可能已委托'
                        #self.logger.info(f"[{stock_code}] 非交易日夜市委托，设为可能已委托状态")
                else:
                    # 普通任务：保持原有逻辑
                    new_status = '已委托' if status_text in ['已报', '已成'] else '运行中'
                
                # 只在状态真正发生变化时才保存
                if old_status != new_status:
                    task['status'] = new_status
                    task['order_sysid'] = order_sysid  # 保存柜台合同号
                    task['order_id'] = order_sysid  # 兼容停止/删除任务时的撤单字段
                    self.logger.info(f"[{stock_code}] 任务{matched_task_id}状态更新为{new_status}，柜台合同号={order_sysid}，订单状态={status_text}")
                    
                    # 保存任务状态
                    self.task_manager.save_tasks(list(self.task_manager.tasks.values()))
                    
                    # 检查是否是夜市且非交易日，如果是则不更新UI状态
                    is_night_market = '夜市' in task.get('strategy', '')
                    is_trading_day_today = is_tradeday(datetime.now().date())
                    
                    if is_night_market and not is_trading_day_today:
                        # 非交易日的夜市委托：不更新UI状态，保持运行中
                        #self.logger.info(f"[{stock_code}] 非交易日夜市委托，保持任务运行状态，不更新UI")
                        pass
                    else:
                        # 其他情况：正常更新UI状态
                        self.task_manager.update_task_ui.emit(matched_task_id, 'status', task['status'])
                    
                    # 如果是夜市任务，通知策略进程真实的订单状态
                    if '夜市' in task.get('strategy', ''):
                        # 通知策略进程真实的订单状态
                        self._notify_strategy_order_status(matched_task_id, order_dict, status_text)
                        
                        # 检查是否需要停止任务
                        if matched_task_id in self.task_manager.running_tasks:
                            # 检查是否是交易日
                            is_trading_day_today = is_tradeday(datetime.now().date())
                            
                            if is_trading_day_today:
                                # 交易日：委托成功后停止任务
                                self.logger.info(f"[{stock_code}] 交易日夜市委托成功，停止任务：{matched_task_id}")
                                self.task_manager.stop_task(matched_task_id)
                            else:
                                # 非交易日：不停止任务，继续虚拟委托模式
                                self.logger.info(f"[{stock_code}] 非交易日委托可能成功，继续运行：{matched_task_id}")
                else:
                    # 状态没变，不保存，只记录日志
                    self.logger.info(f"[{stock_code}] 任务{matched_task_id}状态未发生变化，跳过保存")
                    pass
            #else:
            #    self.logger.warning(f"[{stock_code}] 未找到匹配的任务，订单信息: 类型={order_type}, 价格={order_price}, 数量={order_volume}")
                    
        except Exception as e:
            self.logger.error(f"更新任务状态失败: {str(e)}")
            import traceback
            self.logger.error(f"更新任务状态异常堆栈: {traceback.format_exc()}")

    def _notify_strategy_order_status(self, task_id, order_dict, status_text):
        """通知策略进程真实的订单状态"""
        try:
            if task_id not in self.task_manager.running_tasks:
                self.logger.warning(f"任务 {task_id} 不在运行中，无法通知策略进程")
                return
            
            # 获取任务信息
            task = self.task_manager.tasks.get(task_id)
            if not task:
                self.logger.warning(f"任务 {task_id} 不存在，无法通知策略进程")
                return
            
            stock_code = task.get('stock_code', '')
            strategy = task.get('strategy', '')
            
            # 只处理夜市任务
            if '夜市' not in strategy:
                return
            
            # 构建订单状态信息
            order_status_info = {
                'order_id': order_dict.get('order_id', ''),
                'status': status_text,  # 使用真实的订单状态（已报/未报等）
                'price': order_dict.get('price', 0),
                'volume': order_dict.get('order_volume', 0),
                'type': 'buy' if '买入' in strategy else 'sell',
                'time': datetime.now()
            }
            
            # 获取控制管道
            task_info = self.task_manager.running_tasks[task_id]
            control_pipe = task_info.get('control_pipe')
            
            if control_pipe:
                try:
                    # 发送订单状态到策略进程
                    control_pipe.send(('order_status', order_status_info))
                    self.logger.info(f"[{stock_code}] 已通知策略进程真实订单状态：{order_status_info}")
                except Exception as e:
                    self.logger.warning(f"[{stock_code}] 通知策略进程订单状态失败：{str(e)}")
            else:
                self.logger.warning(f"[{stock_code}] 任务 {task_id} 没有控制管道，无法通知策略进程")
                
        except Exception as e:
            self.logger.error(f"通知策略进程订单状态失败：{str(e)}")
            import traceback
            self.logger.error(f"通知策略进程订单状态异常堆栈：{traceback.format_exc()}")

    def _check_reconnect_timeout(self):
        """检查重连是否超时"""
        if self.is_reconnecting and self._reconnect_start_time > 0:
            elapsed_time = time.time() - self._reconnect_start_time
            if elapsed_time > self._max_reconnect_time:
                self.logger.error(f"[重连保护] 重连超时{self._max_reconnect_time}秒，强制恢复")
                self.is_reconnecting = False
                self._is_initialized = True  # 强制设置为已初始化，避免UI卡死
                self._reconnect_start_time = 0
                return True
        return False

    def _start_reconnect(self):
        """开始重连，设置保护机制"""
        if self._relax_xt_trader_health():
            return
        if self._restart_in_progress:
            self.logger.info("[重连] 跳过：QMT重启进行中")
            return
        if not self.is_reconnecting:
            self.is_reconnecting = True
            self._reconnect_start_time = time.time()
            # 在后台线程中执行重连
            reconnect_thread = threading.Thread(target=self._try_reconnect, daemon=True)
            reconnect_thread.start()

class MyXtQuantTraderCallback(XtQuantTraderCallback):
    # 实测只有on_stock_order, on_stock_trade有回调，持仓和资金定期通过update_asset_positions()更新
    # on_account_status，on_disconnected有回调
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.processed_positions = set()  # 添加已处理持仓的集合
        self.logger = engine.logger  # 使用 engine 的 logger，确保使用正确的 mode
        self.stock_list = None  # 默认不过滤任何股票，接收所有订单回报
        
    def on_stock_order(self, order):
        """委托回报推送 - QMT实际会调用的回调函数"""
        try:
            # 检查订单是否有有效的 order_sysid
            if not hasattr(order, 'order_sysid') or not order.order_sysid:
                self.engine.logger.debug(f"[订单回报] on_stock_order收到订单回报，但order_sysid无效: {order}")
                return
            
            # 检查股票代码
            if not hasattr(order, 'stock_code') or not order.stock_code:
                self.engine.logger.debug(f"[订单回报] on_stock_order收到订单回报，但stock_code无效: {order}")
                return
            
            # 如果有stock_list过滤，检查是否在列表中（如果stock_list为None，则不过滤）
            if self.stock_list is not None and order.stock_code not in self.stock_list:
                self.engine.logger.debug(f"[订单回报] on_stock_order订单被stock_list过滤: {order.stock_code}")
                return
            
            # 发送订单更新信号，让主程序快速处理（特别是提前下单的订单ID更新）
            # 这比等待15秒的查询周期快得多
            self.engine.order_updated.emit(order)
            
            # 记录日志
            # 处理order_sysid可能是字符串格式（如"xt1090662917"）的情况
            order_sysid_str = str(order.order_sysid)
            # 如果以"xt"开头，去掉前缀
            if order_sysid_str.startswith('xt'):
                order_sysid_str = order_sysid_str[2:]
            
            # 尝试转换为整数，如果失败则保持字符串格式
            try:
                order_id = int(order_sysid_str)
            except (ValueError, TypeError):
                order_id = order_sysid_str  # 保持原始字符串格式
            
            order_dict = {
                'order_id': order_id,
                'stock_code': order.stock_code,
                'order_type': getattr(order, 'order_type', 'unknown'),
                'price': getattr(order, 'price', 0),
                'order_volume': getattr(order, 'order_volume', 0),
                'order_status': getattr(order, 'order_status', 'unknown'),
                'strategy_name': getattr(order, 'strategy_name', '')
            }
            self.engine.logger.info(f"[订单回报] ✅ on_stock_order收到委托回报并发送信号: {order_dict}")
            
        except Exception as e:
            self.engine.logger.error(f"[订单回报] on_stock_order处理委托回报时出错: {str(e)}")
            import traceback
            self.engine.logger.error(f"[订单回报] 错误详情: {traceback.format_exc()}")
        
    def on_stock_trade(self, trade):
        """成交回报推送"""
        # 创建线程处理成交回报
        Thread(target=self.engine.on_trade_callback, args=(trade,), daemon=True).start()
        
    def on_stock_position(self, position):
        """持仓变动推送"""
        try:
            stock_code = position.stock_code
            if position.volume > 0:
                self.engine.logger.info(f"[{stock_code}] 持仓变动：数量={position.volume}，成本={position.open_price}")
            else:
                self.engine.logger.info(f"[{stock_code}] 持仓已清空")
            
        except Exception as e:
            self.engine.logger.error(f"处理持仓变动失败：{stock_code} - {str(e)}")
        
    def on_asset_change(self, asset):
        """资金变动推送"""
        self.engine.logger.info(f"XT-资金变动: 可用资金={asset.cash}, 总资产={asset.total_asset}")

    def on_order_stock_async_response(self, response):
        """
        异步下单回报推送
        :param response: XtOrderResponse 对象
        :return:
        """
        self.engine.logger.info(f"XT-异步下单回报推送: {response.account_id}, 订单号={response.order_id}, 序号={response.seq}, 策略名称={response.strategy_name}")

    def on_order_status(self, order):
        """委托回报推送"""
        try:
            # 检查订单是否有有效的 order_sysid
            if not hasattr(order, 'order_sysid') or not order.order_sysid:
                self.engine.logger.debug(f"[订单回报] 收到订单回报，但order_sysid无效: {order}")
                return
            
            # 检查股票代码
            if not hasattr(order, 'stock_code') or not order.stock_code:
                self.engine.logger.debug(f"[订单回报] 收到订单回报，但stock_code无效: {order}")
                return
            
            # 如果有stock_list过滤，检查是否在列表中（如果stock_list为None，则不过滤）
            if self.stock_list is not None and order.stock_code not in self.stock_list:
                return
            
            # 发送订单更新信号，让主程序快速处理（特别是提前下单的订单ID更新）
            # 这比等待15秒的查询周期快得多
            self.engine.order_updated.emit(order)
            
            # 记录日志（仅在调试模式下）
            if self.engine.logger.level <= 10:  # DEBUG级别
                # 处理order_sysid可能是字符串格式（如"xt1090662917"）的情况
                order_sysid_str = str(order.order_sysid)
                # 如果以"xt"开头，去掉前缀
                if order_sysid_str.startswith('xt'):
                    order_sysid_str = order_sysid_str[2:]
                
                # 尝试转换为整数，如果失败则保持字符串格式
                try:
                    order_id = int(order_sysid_str)
                except (ValueError, TypeError):
                    order_id = order_sysid_str  # 保持原始字符串格式
                
                order_dict = {
                    'order_id': order_id,
                    'stock_code': order.stock_code,
                    'order_type': order.order_type,
                    'price': order.price,
                    'order_volume': order.order_volume,
                    'order_status': order.order_status,
                    'traded_volume': order.traded_volume
                }
                self.engine.logger.debug(f"[订单回报] 收到委托回报并发送信号: {order_dict}")
            
        except Exception as e:
            self.engine.logger.error(f"[订单回报] 处理委托回报时出错: {str(e)}")
            import traceback
            self.engine.logger.error(f"[订单回报] 错误详情: {traceback.format_exc()}")

    def on_disconnected(self):
        """连接断开"""
        self.engine.logger.info("[断开回调] ========== 收到QMT断开回调 ==========")
        self.engine.logger.info("[断开回调] 当前线程: " + str(threading.current_thread().name))
        self.engine.logger.warning("QMT连接断开，可能是由于网络波动或QMT服务重启，任务将保持状态等待重连")
        self.engine.signals.status.emit("账户连接状态：断开【请检查网络连接，启动miniQMT，任务将在重连后自动恢复】")
        
        # 确保定时器继续运行
        if not self.engine.running:
            self.engine.logger.warning("[断开回调] 发现running=False，重新设置为True")
            self.engine.running = True
        
        # 不发送重连信号，让定时器自动检测并重连
        self.engine.logger.info("[断开回调] 断开回调处理完成，等待定时器检测重连")

    def on_stock_asset(self, asset):
        """股票资产回调"""
        self.engine.logger.info(f"收到股票资产回调: {asset}")
        # 只做资产信息更新
        self.engine.assets = asset

    def on_order_error(self, order_error):
        """委托失败推送"""
        self.engine.logger.info(f"XT-委托报错回调 {order_error.order_remark} {order_error.error_msg}")
        
    def on_cancel_error(self, cancel_error):
        """撤单失败推送"""
        error_msg = cancel_error.error_msg
        order_id = str(cancel_error.order_id)
        
        self.engine.logger.info(f"XT-撤单失败推送{datetime.now()} {sys._getframe().f_code.co_name}，{order_id}，{error_msg}")
        
        # 发送撤单失败信号
        self.engine.cancel_error_signal.emit(order_id, error_msg)

    def on_cancel_order_stock_async_response(self, response):
        """异步撤单回报推送"""
        self.engine.logger.info(f"XT-异步撤单回报{datetime.now()} {sys._getframe().f_code.co_name}")

    def on_account_status(self, status):
        """账户状态推送"""
        self.engine.logger.info(f"[账户状态] 收到状态推送: status={status.status}")
        status_messages = {
            0: "账户连接状态：已断开",
            1: "账户连接状态：连接中【如果长时间处于连接中状态，请检查网络连接。非交易时段，可忽略】",
            2: "账户连接状态：登录中",
            3: "账户连接状态：失败【请检查网络连接，并确保miniQMT已经正常登录运行。如果仍然失败，请重启miniQMT】",
            4: "账户连接状态：初始化中",
            5: "账户连接状态：登录成功【将重新加载任务和订阅行情】",  # 添加重连提示
            6: "账户连接状态：收盘后"
        }
        message = status_messages.get(
            status.status, 
            f"账户连接状态：未知，状态号{status.status}"
        )
        self.engine.signals.status.emit(message)
        
        # 处理断开连接的情况
        if status.status == 0:  # status=0 可能是断开连接
            self.engine.logger.warning("[账户状态] 检测到 status=0，可能是断开连接，尝试重新连接...")
            if not self.engine.is_reconnecting:
                self.engine.logger.info("[账户状态] 开始执行重连...")
                self.engine._start_reconnect()
            else:
                self.engine.logger.info("[账户状态] 已在重连中，跳过...")
        
        # 在连接成功时重置状态
        if status.status == 5:  # 登录成功
            self.engine.logger.info("[账户状态] 检测到登录成功，开始重连处理")
            invalidate_trading_day_cache()
            self.processed_positions.clear()  # 重置已处理持仓集合
            self.engine.logger.info("QMT连接成功，重置持仓处理状态")
            
            # 查询并显示当日所有订单
            self.engine.logger.info("连接恢复后查询当日订单...")
            self.engine.get_today_orders()
            
            # 通知主窗口重新启动订单列表定时器
            if hasattr(self.engine, 'connection_restored_signal'):
                self.engine.connection_restored_signal.emit()
            
            if self.engine.task_manager:
                # 移除重复的任务加载，因为任务已经在初始化时加载过了
                # self.task_manager.load_tasks()
                # 触发任务恢复
                self.engine.task_manager.handle_trading_reconnection()
                
                # 重新订阅行情数据
                if hasattr(self.engine, 'subscribe_thread'):
                    try:
                        self.engine.logger.info("[账户状态] 开始重新订阅行情数据")
                        # 先处理订阅线程的重连恢复
                        self.engine.subscribe_thread.handle_reconnection()
                        
                        # 获取需要订阅的股票代码
                        stock_codes = set()
                        
                        # 添加持仓股票
                        if hasattr(self.engine, 'positions') and self.engine.positions:
                            stock_codes.update(self.engine.positions.keys())
                        
                        # 添加任务中的股票（包括夜市任务）
                        for task in self.engine.task_manager.tasks.values():
                            stock_code = task.get('stock_code')
                            if stock_code:
                                stock_codes.add(stock_code)
                        
                        if stock_codes:
                            self.engine.logger.info(f"QMT重连后重新订阅行情: {stock_codes}")
                            self.engine.subscribe_thread.update_subscribe_list(list(stock_codes), force_update=True)
                        else:
                            self.engine.logger.warning("QMT重连后没有需要订阅的股票")
                    except Exception as e:
                        self.engine.logger.error(f"QMT重连后重新订阅行情失败: {str(e)}")

    def on_order_change(self, order):
        """委托变动推送"""
        self.engine.logger.info(f'XT-委托变动回调')

    def on_position_change(self, position):
        """持仓变动推送"""
        self.engine.logger.info(f'XT-持仓变动回调')

class StatusSignals(QObject):
    """单独的信号类"""
    status = pyqtSignal(str)

class SubscribeThread(Thread):
    """订阅线程"""
    def __init__(self, qmt_adapter):
        super().__init__()
        self.qmt_adapter = qmt_adapter
        self._running = True
        self._stock_codes = set()
        self._lock = threading.Lock()
        self.logger = qmt_adapter.logger  # 使用 QMTManager 的 logger
        # 初始化时不设置_last_trading_day，这样首次运行时会检测为新交易日
        self._last_trading_day = None
        self.seq = 0
        self.latest_tick_time = 0  # 添加属性存储最新的tick时间
        self.latest_tick_time_str = "等待数据"  # 添加属性存储转换后的北京时间字符串
        self.last_tick_warning_time = {}  # 记录每个股票最后一次tick数据警告的时间
        self.stock_last_tick_time = {}  # 记录每个股票最后一次收到tick数据的时间
        # 重订阅后缓冲：cancel+resubscribe 期间 quote_callback 会短暂中断，勿误报「从未收到 tick」
        self._RESUBSCRIBE_GRACE_SECONDS = 45
        self._resubscribe_grace_until = 0.0
        # 会话昨收缓存已迁至 utils.session_prev_close（早盘/盘后 REFERENCE_SWITCH 共用）
        self._pre_close_before_930_cache = {}  # 兼容旧引用；实际解析不再依赖此字段

    def _mark_resubscribe_grace(self):
        """重订阅成功后启动缓冲，避免订阅层在首条 callback 到达前误报警。"""
        self._resubscribe_grace_until = time.time() + self._RESUBSCRIBE_GRACE_SECONDS

    def _in_resubscribe_grace(self) -> bool:
        until = getattr(self, '_resubscribe_grace_until', 0.0)
        return until > 0 and time.time() < until

    def _has_active_subscriptions(self) -> bool:
        """当前是否有已订阅、应收到 tick 的标的（空仓且无任务时为 False）。"""
        return bool(self._stock_codes)

    def _get_pre_close_price(self, stock_code, qmt_last_close, last_price=0.0):
        """获取会话基准昨收。

        - 交易日 9:30～REFERENCE_SWITCH 前：用 QMT lastClose
        - 盘后 REFERENCE_SWITCH 后 / 早盘 9:30 前 / 非交易日：用 session_prev_close
          （key_price_calculator 或 last_price），避免盘后仍把上一交易日当昨收。
        计算器结果带缓存，回调路径不致每 tick 重算。
        """
        from utils.session_prev_close import resolve_session_prev_close

        lp = float(last_price or 0)
        if lp <= 0 and hasattr(self.qmt_adapter, "task_manager") and self.qmt_adapter.task_manager:
            try:
                lp = float(
                    (getattr(self.qmt_adapter.task_manager, "latest_prices", {}) or {}).get(
                        stock_code, 0
                    )
                    or 0
                )
            except Exception:
                lp = 0.0
        return resolve_session_prev_close(
            stock_code,
            qmt_last_close=float(qmt_last_close or 0),
            last_price=lp,
        )

    @staticmethod
    def _quote_field(source, name, default=None):
        if isinstance(source, dict):
            return source.get(name, default)
        return getattr(source, name, default)

    def _build_tick_data(self, stock_code, source, tick_time):
        """构建 tick 字典；含成交量与五档量，供真突破判定使用。"""
        tick_data = {
            'stock_code': stock_code,
            'lastPrice': self._quote_field(source, 'lastPrice', 0) or 0,
            'lastClose': self._quote_field(source, 'lastClose', 0) or 0,
            'open': self._quote_field(source, 'open', 0) or 0,
            'high': self._quote_field(source, 'high', 0) or 0,
            'low': self._quote_field(source, 'low', 0) or 0,
            'askPrice': self._quote_field(source, 'askPrice', []) or [],
            'bidPrice': self._quote_field(source, 'bidPrice', []) or [],
            'askVol': self._quote_field(source, 'askVol', []) or [],
            'bidVol': self._quote_field(source, 'bidVol', []) or [],
            'time': tick_time,
        }
        for vol_key in ('volume', 'cumVol', 'totalVol', 'cum_volume', 'dealVol', 'pvolume'):
            raw = self._quote_field(source, vol_key, None)
            if raw is None:
                continue
            try:
                tick_data['volume'] = float(raw)
                break
            except (TypeError, ValueError):
                continue
        for inc_key in (
            'lastVol', 'tradeVol', 'tradeVolume', 'tickVol',
            'singleVol', 'matchQty', 'qty', 'volume_delta',
        ):
            raw = self._quote_field(source, inc_key, None)
            if raw is None:
                continue
            try:
                tick_data[inc_key] = float(raw)
            except (TypeError, ValueError):
                continue
        return tick_data
    
    def _preload_pre_close_prices(self, stock_codes):
        """预加载会话基准昨收（早盘 9:30 前，或盘后 REFERENCE_SWITCH 后）。"""
        from utils.trading_day import is_after_reference_switch
        from utils.session_prev_close import warm_session_prev_close_cache

        beijing_now = datetime.now(timezone(timedelta(hours=8)))
        today = beijing_now.date()
        # 盘中不需要预热
        if is_tradeday(today) and not is_after_reference_switch(beijing_now):
            from datetime import time as dt_time
            if beijing_now.time() >= dt_time(9, 30):
                return

        phase = "盘后次日基准" if is_after_reference_switch(beijing_now) else "早盘昨收"
        self.logger.info(f"[预加载] 开始预加载 {len(stock_codes)} 只股票的{phase}...")
        try:
            n = warm_session_prev_close_cache(stock_codes, now=beijing_now)
            self.logger.info(f"[预加载] {phase}预加载完成，缓存 {n} 只")
        except Exception as e:
            self.logger.warning(f"[预加载] 预加载昨收盘价失败: {e}")

    def start(self):
        """启动线程"""
        super().start()

    def run(self):
        """线程运行函数"""
        
        # 获取当前持仓股票并立即订阅
        stock_codes = set()
        
        # 添加任务中的股票（包括夜市任务）
        if hasattr(self.qmt_adapter, 'task_manager') and self.qmt_adapter.task_manager:
            task_stocks = []
            for task in self.qmt_adapter.task_manager.tasks.values():
                stock_code = task.get('stock_code')
                if stock_code:
                    # 确保stock_code是字符串类型
                    stock_code = str(stock_code).strip()
                    if stock_code:
                        stock_codes.add(stock_code)
                        task_stocks.append(stock_code)
        # 尝试获取持仓股票（如果xt_trader已连接）
        try:
            # 等待交易连接建立（最多等待10秒）
            wait_count = 0
            while self._running and not self.qmt_adapter._is_initialized and wait_count < 10:
                time.sleep(1)
                wait_count += 1
            
            if self.qmt_adapter._is_initialized:
                try:
                    _, positions = self.qmt_adapter.get_asset_positions()
                    # 添加持仓股票
                    if positions is not None and positions:
                        stock_codes.update(positions.keys())
                except Exception as e:
                    pass  # 获取持仓失败不影响订阅
        except Exception as e:
            pass  # 获取持仓信息失败不影响订阅
        
        # 如果有股票需要订阅，立即订阅
        if stock_codes:
            try:
                self.update_subscribe_list(list(stock_codes))
            except Exception as e:
                self.logger.error(f"初始化订阅失败: {str(e)}")
        
        # 记录上次收到行情数据的时间（秒级时间戳）
        last_tick_received_time = time.time()
        no_tick_warning_interval = 60  # 每60秒检查一次是否收到行情数据
        last_warning_time = 0  # 上次警告的时间
        # 记录上次检查的时间点，用于检测是否进入新的交易时段
        from datetime import datetime
        last_check_datetime = datetime.now()
        last_check_hour = last_check_datetime.hour
        last_check_minute = last_check_datetime.minute
        # 记录关键时间点的开始时间，用于在开盘缓冲期内检查是否收到数据
        critical_time_start = None  # 关键时间点的开始时间（秒级时间戳）
        critical_time_check_interval = 90  # 9:30/13:00 开盘后缓冲秒数，避免过早重订阅打断 QMT 推送
        
        while self._running:
            try:
                # 每30秒检查一次是否需要重新初始化订阅
                check_counter = 0
                for _ in range(30):  # 30秒检查一次
                    if not self._running:
                        break
                    time.sleep(1)
                    check_counter += 1
                    
                    # 检查是否收到行情数据
                    current_time = time.time()
                    now = datetime.now()
                    hour = now.hour
                    minute = now.minute
                    
                    # 检测是否刚进入新的交易时段（9:30、13:00），若是则启动关键时间点检查
                    # 9:15-9:30 多为集合竞价/开盘前静默，QMT 常无连续 tick，不在 9:15 触发「10 秒内必须有数据」
                    if (hour != last_check_hour or minute != last_check_minute):
                        is_new_trading_period = False
                        if is_tradeday(now.date()):
                            if hour == 9 and minute == 30:  # 连续竞价开始
                                is_new_trading_period = True
                            elif hour == 13 and minute == 0:  # 下午开盘
                                is_new_trading_period = True
                        
                        if is_new_trading_period:
                            # 无订阅标的时不启动「10秒内必须有 tick」检查（空仓/无任务时 9:15 无推送属正常）
                            if self._has_active_subscriptions():
                                critical_time_start = current_time
                                last_tick_received_time = current_time  # 重置计时器
                                last_warning_time = 0
                            else:
                                critical_time_start = None
                                last_tick_received_time = current_time
                                last_warning_time = 0
                        
                        last_check_hour = hour
                        last_check_minute = minute
                    
                    # 9:15-9:30 开盘前静默：不应触发关键时间点重连
                    if hour == 9 and minute < 30 and critical_time_start is not None:
                        critical_time_start = None
                    
                    # 检查latest_tick_time是否更新（转换为秒级比较）
                    if hasattr(self, 'latest_tick_time') and self.latest_tick_time > 0:
                        tick_time_seconds = self.latest_tick_time / 1000.0
                        # 如果tick时间在最近5秒内，认为收到了新数据
                        if current_time - tick_time_seconds < 5:
                            last_tick_received_time = current_time
                            # 如果有关键时间点检查，收到数据后清除标记
                            if critical_time_start is not None:
                                critical_time_start = None
                    
                    # 关键时间点检查：开盘缓冲期过后仍无数据，再重新订阅
                    if critical_time_start is not None:
                        time_since_critical = current_time - critical_time_start
                        if time_since_critical > critical_time_check_interval:
                            critical_time_start = None  # 清除标记，避免重复触发
                            if not self._has_active_subscriptions():
                                self.logger.info(
                                    "[订阅线程] 开盘缓冲期后无行情推送，当前无订阅标的，跳过重订阅"
                                )
                                continue
                            # 开盘缓冲期后仍无数据，重新订阅
                            self.logger.error(
                                f"[订阅线程] ⚠️ 开盘缓冲期({critical_time_check_interval}秒)后仍未收到行情数据，立即重新订阅"
                            )
                            try:
                                self.update_subscribe_list(list(self._stock_codes))
                                self.logger.info("[订阅线程] 已重新订阅股票列表")
                            except Exception as e:
                                self.logger.error(f"[订阅线程] 重新订阅失败: {str(e)}")
                            continue  # 跳过后续检查，等待重连完成
                    
                    # 如果长时间没有收到行情数据，输出警告（每60秒一次）
                    # 注意：如果正在关键时间点检查窗口内（10秒），跳过常规检查，优先使用关键时间点检查
                    if critical_time_start is None or (current_time - critical_time_start > critical_time_check_interval):
                        if current_time - last_tick_received_time > no_tick_warning_interval:
                            if current_time - last_warning_time > no_tick_warning_interval:
                                # 长时间没有收到行情数据，输出警告
                                is_trading_time = _is_continuous_auction_trading(now)
                                
                                if is_trading_time and self._has_active_subscriptions():
                                    self.logger.warning(
                                        f"[诊断] [订阅线程] ⚠️ 已{int(current_time - last_tick_received_time)}秒未收到行情数据（交易时间内），请检查QMT行情服务"
                                    )
                                last_warning_time = current_time  # 记录警告时间，避免重复警告
                    
                    # 每30秒检查一次任务股票订阅
                    if check_counter % 30 == 0:
                        self._check_and_update_task_subscriptions()
                
                # 如果是新的交易日，使用当前的订阅列表重新订阅
                if self._stock_codes and self._is_new_trading_day():
                    #self.logger.info(f"[订阅线程] 检测到新交易日，重新订阅")
                    self.update_subscribe_list(list(self._stock_codes))
            except Exception as e:
                #self.logger.error(f"行情订阅线程错误: {str(e)}")
                time.sleep(5)
    
    def _check_and_update_task_subscriptions(self):
        """检查并更新任务股票的订阅"""
        try:
            #self.logger.info(f"[订阅检查] 开始检查任务股票订阅")
            if hasattr(self.qmt_adapter, 'task_manager') and self.qmt_adapter.task_manager:
                # 收集所有任务中的股票
                task_stocks = set()
                for task in self.qmt_adapter.task_manager.tasks.values():
                    stock_code = task.get('stock_code')
                    if stock_code:
                        task_stocks.add(stock_code)

                desired = set(task_stocks)
                try:
                    _, positions = self.qmt_adapter.get_asset_positions()
                    if positions:
                        desired |= set(positions.keys())
                except Exception:
                    pass

                if desired != self._stock_codes:
                    self.update_subscribe_list(list(desired))
            else:
                #self.logger.info(f"[订阅检查] task_manager未设置，跳过检查")
                pass
                    
        except Exception as e:
            #self.logger.error(f"[订阅检查] 检查任务股票订阅失败: {str(e)}")
            pass
    
    def _is_new_trading_day(self):
        """检查是否是新的交易日"""
        current_date = datetime.now().strftime('%Y-%m-%d')
        
        # 检查是否有task_manager
        if not hasattr(self.qmt_adapter, 'task_manager') or not self.qmt_adapter.task_manager:
            return False
        
        # 首先检查是否是交易日
        try:
            from utils.trading_day import is_tradeday
            from datetime import date
            current_date_obj = date.today()
            is_trading = is_tradeday(current_date_obj)
            if not is_trading:
                # 非交易日也输出日志，但只在日期变化时输出一次
                if self._last_trading_day != current_date:
                    self.logger.info(f"处理非交易日: {current_date}")
                    self._last_trading_day = current_date
                return False
        except ImportError:
            # 如果无法导入chncal，使用简单的周末检查
            if datetime.now().weekday() >= 5:  # 5=周六, 6=周日
                if self._last_trading_day != current_date:
                    self.logger.info(f"处理非交易日: {current_date}")
                    self._last_trading_day = current_date
                return False
        
        # 只有当有任务且最后交易日不同时才处理
        if self._last_trading_day != current_date and self.qmt_adapter.task_manager.tasks:
            #self.logger.info(f"检测到新交易日: {current_date} (上次: {self._last_trading_day})")
            pass
            
            # 更加保守的检查：如果有运行中的任务，且最近5秒内有任务启动，则延迟处理
            running_tasks = self.qmt_adapter.task_manager.running_tasks
            if running_tasks:
                # 检查是否有任务在最近5秒内启动
                current_time = time.time()
                recent_startup = any(
                    hasattr(task, 'start_time') and (current_time - task.get('start_time', 0)) < 5
                    for task in running_tasks.values()
                )
                
                if recent_startup:
                    #self.logger.info("检测到最近有任务启动，延迟新交易日处理")
                    pass
                    # 使用定时器延迟5秒后重新检查
                    from PyQt5.QtCore import QTimer
                    timer = QTimer()
                    timer.singleShot(5000, self._process_new_trading_day)
                    return True
                else:
                    #self.logger.info("有运行中的任务，跳过新交易日的任务保存")
                    pass
                    # 只更新最后交易日，不保存任务
                    self._last_trading_day = current_date
                    return True
            else:
                # 没有运行中的任务，安全执行新交易日处理
                self._process_new_trading_day()
                return True
        
        return False
    
    def _process_new_trading_day(self):
        """处理新交易日的逻辑"""
        current_date = datetime.now().strftime('%Y-%m-%d')
        
        # 检查是否有task_manager
        if not hasattr(self.qmt_adapter, 'task_manager') or not self.qmt_adapter.task_manager:
            return
        
        # 再次检查是否有运行中的任务
        if self.qmt_adapter.task_manager.running_tasks:
            self.logger.info("仍有运行中的任务，跳过新交易日的任务保存")
            self._last_trading_day = current_date
            return
        
        self.logger.info(f"处理新交易日: {current_date}")
        
        # 更新任务文件路径为当前日期
        if self.qmt_adapter.task_manager.update_tasks_file_path():
            self.logger.info(f"新交易日 {current_date} 任务文件路径已更新")
            
            # 检查新文件是否存在，如果不存在则迁移前一天的任务
            import os
            if not os.path.exists(self.qmt_adapter.task_manager.tasks_file):
                self.logger.info(f"新交易日文件不存在，开始迁移前一天的任务")
                self.qmt_adapter.task_manager._check_and_migrate_previous_day_tasks()
        
        # 更新最后交易日
        self._last_trading_day = current_date
        
        # 保存任务并发送更新信号
        self.qmt_adapter.task_manager.save_tasks(list(self.qmt_adapter.task_manager.tasks.values()))
        # 只有在信号未被阻止时才发送
        #if not getattr(self.qmt_adapter.task_manager, '_block_tasks_updated_signal', False):
        #    self.qmt_adapter.task_manager.tasks_updated.emit()

    def _check_tick_timeout(self):
        """检查tick数据超时（仅 mini/xt_trader 行情路径；builtin 读 results.json，不检查）。"""
        try:
            from utils.qmt_execution_config import use_builtin_price_feed

            if use_builtin_price_feed():
                return
        except Exception:
            pass
        try:
            from datetime import datetime, timezone, timedelta
            from datetime import time as dt_time
            
            # 获取当前北京时间
            beijing_tz = timezone(timedelta(hours=8))
            now = datetime.now(beijing_tz)
            current_time = now.time()
            current_date = now.date()
            
            # 仅在连续竞价时段内进行 tick 超时检查与自动重启（9:25-9:30 无连续 tick 属正常）
            trading_sessions = [
                (dt_time(9, 30, 0), dt_time(11, 30, 0)),
                (dt_time(13, 0, 0), dt_time(15, 0, 0)),
            ]
            is_trading_time = (
                any(start <= current_time <= end for start, end in trading_sessions)
                and is_tradeday(current_date)
            )
            open_am_grace_start = dt_time(9, 30, 0)
            open_am_grace_end = dt_time(9, 31, 0)
            open_pm_grace_start = dt_time(13, 0, 0)
            open_pm_grace_end = dt_time(13, 1, 0)
            in_open_grace = (
                open_am_grace_start <= current_time <= open_am_grace_end
                or open_pm_grace_start <= current_time <= open_pm_grace_end
            )
            in_resubscribe_grace = self._in_resubscribe_grace()
            
            # 检查状态栏是否处于“等待行情数据...”状态（price_displays为空）
            is_waiting_for_data = False
            has_running_tasks = False
            task_manager = getattr(self.qmt_adapter, 'task_manager', None)
            if task_manager is not None:
                try:
                    running_tasks = getattr(task_manager, 'running_tasks', {}) or {}
                    has_running_tasks = len(running_tasks) > 0
                    price_displays = getattr(task_manager, 'price_displays', {}) or {}
                    is_waiting_for_data = has_running_tasks and not price_displays
                except Exception:
                    pass
            
            if is_waiting_for_data:
                if self._waiting_for_data_since is None:
                    self._waiting_for_data_since = time.time()
            else:
                self._waiting_for_data_since = None
            
            # 非交易时段的开机等待计时（即使没有运行中的任务也计时）
            if not is_trading_time:
                if not hasattr(self, '_non_trading_wait_start') or self._non_trading_wait_start is None:
                    self._non_trading_wait_start = time.time()
            else:
                # 交易时段内不记录该计时
                if hasattr(self, '_non_trading_wait_start'):
                    self._non_trading_wait_start = None
            
            restart_reason = None

            # 检查运行中的任务
            if (hasattr(self.qmt_adapter, 'task_manager') and 
                self.qmt_adapter.task_manager and 
                hasattr(self.qmt_adapter.task_manager, 'running_tasks')):
                
                for task_id, task_info in self.qmt_adapter.task_manager.running_tasks.items():
                    stock_code = task_info.get('stock_code')
                    if not stock_code:
                        continue
                    
                    # 检查该股票是否在task_processes中
                    if (hasattr(self.qmt_adapter.task_manager, 'task_processes') and
                        stock_code in self.qmt_adapter.task_manager.task_processes):
                        
                        # 检查是否超过15秒没有收到tick数据
                        last_warning_time = self.last_tick_warning_time.get(stock_code, 0)
                        current_timestamp = now.timestamp()
                        
                        if current_timestamp - last_warning_time > 15:  # 15秒检查一次
                            # 检查该股票的最新tick时间
                            stock_last_tick_time = self.stock_last_tick_time.get(stock_code, 0)
                            if stock_last_tick_time > 0:
                                last_tick_timestamp = stock_last_tick_time / 1000
                                time_since_last_tick = current_timestamp - last_tick_timestamp
                                
                                if is_trading_time and time_since_last_tick > 30:  # 仅交易时段判断超时
                                    # 转换时间显示
                                    from datetime import datetime, timezone, timedelta
                                    beijing_tz = timezone(timedelta(hours=8))
                                    last_tick_dt = datetime.fromtimestamp(last_tick_timestamp, beijing_tz)
                                    last_tick_str = last_tick_dt.strftime('%H:%M:%S')
                                    
                                    # 检查当前时间是否在特殊时段（集合竞价、午休），如果是则不触发重启
                                    current_dt = datetime.fromtimestamp(current_timestamp, beijing_tz)
                                    current_time = current_dt.time()
                                    
                                    # 集合竞价时间：9:25:00 - 9:30:00（无tick数据是正常的）
                                    call_auction_start = dt_time(9, 25, 0)
                                    call_auction_end = dt_time(9, 30, 0)
                                    # 午休时间：11:30:00 - 13:00:00
                                    lunch_start = dt_time(11, 30, 0)
                                    lunch_end = dt_time(13, 0, 0)
                                    # 开盘后缓冲期：部分标的在开盘初段仍可能暂无tick，不应立即判定异常
                                    open_am_grace_start = dt_time(9, 30, 0)
                                    open_am_grace_end = dt_time(9, 31, 0)
                                    open_pm_grace_start = dt_time(13, 0, 0)
                                    open_pm_grace_end = dt_time(13, 1, 0)
                                    
                                    # 如果不在特殊时段，才打印警告（不据此重启，避免冷门股拖死会话）
                                    in_call_auction = call_auction_start <= current_time <= call_auction_end
                                    in_lunch = lunch_start <= current_time <= lunch_end
                                    in_open_grace = (
                                        open_am_grace_start <= current_time <= open_am_grace_end
                                        or open_pm_grace_start <= current_time <= open_pm_grace_end
                                    )
                                    
                                    if not (in_call_auction or in_lunch or in_open_grace or in_resubscribe_grace):
                                        self.logger.warning(f"[{stock_code}] 交易时段超过30秒未收到tick数据，最后tick时间: {last_tick_str}")
                                    
                                    self.last_tick_warning_time[stock_code] = current_timestamp
                                    # 单票长时间无 tick 仅打日志，不设置 restart_reason。
                                    # 全进程是否重启只看「任意股票」行情回调更新的 latest_tick_time（见下方）。
                            else:
                                # 从未收到过tick数据（可能是刚启动或刚订阅）
                                if (
                                    is_trading_time
                                    and not in_open_grace
                                    and not in_resubscribe_grace
                                    and current_timestamp - self.last_tick_warning_time.get(stock_code, 0) > 60
                                ):
                                    self.logger.warning(f"[{stock_code}] 交易时段从未收到tick数据")
                                    self.last_tick_warning_time[stock_code] = current_timestamp
                        
        except Exception as e:
            self.logger.error(f"检查tick数据超时失败: {str(e)}")
            return

        try:
            if not self._has_active_subscriptions():
                if hasattr(self, '_first_tick_wait_start_cb'):
                    self._first_tick_wait_start_cb = None
                return

            # 检查是否刚进入开盘时间，如果是则重置latest_tick_time
            # 9:30开盘：因为9:25-9:30之间不应该检测tick更新，所以9:30开盘时应该重置计时器
            # 13:00开盘：因为11:30-13:00之间是午休时间，不应该检测tick更新，所以13:00开盘时应该重置计时器
            
            # 初始化重置时间记录字典
            if not hasattr(self, '_market_open_reset_times'):
                self._market_open_reset_times = {}
            
            # 处理9:30开盘
            market_open_am = dt_time(9, 30, 0)
            market_open_am_end = dt_time(9, 30, 10)  # 给10秒的缓冲时间
            if market_open_am <= current_time <= market_open_am_end:
                reset_key = "09:30"
                # 检查是否已经重置过（避免重复重置）
                if self._market_open_reset_times.get(reset_key) != current_date:
                    self._market_open_reset_times[reset_key] = current_date
                    if hasattr(self, '_first_tick_wait_start_cb'):
                        self._first_tick_wait_start_cb = None
                    latest_tick_ms = getattr(self, 'latest_tick_time', 0)
                    if latest_tick_ms > 0:
                        # 检查最后一次tick是否在9:30之前
                        latest_tick_timestamp = latest_tick_ms / 1000
                        latest_tick_dt = datetime.fromtimestamp(latest_tick_timestamp, beijing_tz)
                        latest_tick_time_obj = latest_tick_dt.time()
                        
                        # 如果最后一次tick在9:30之前，说明是集合竞价前的时间，需要重置
                        # 这样计时器从9:30开始计算，避免因为9:25-9:30之间没有tick而误判超时
                        if latest_tick_time_obj < market_open_am:
                            # 重置为当前时间，这样计时器从9:30开始计算
                            self.latest_tick_time = int(time.time() * 1000)
            
            # 处理13:00开盘
            market_open_pm = dt_time(13, 0, 0)
            market_open_pm_end = dt_time(13, 0, 10)  # 给10秒的缓冲时间
            if market_open_pm <= current_time <= market_open_pm_end:
                reset_key = "13:00"
                # 检查是否已经重置过（避免重复重置）
                if self._market_open_reset_times.get(reset_key) != current_date:
                    self._market_open_reset_times[reset_key] = current_date
                    if hasattr(self, '_first_tick_wait_start_cb'):
                        self._first_tick_wait_start_cb = None
                    latest_tick_ms = getattr(self, 'latest_tick_time', 0)
                    if latest_tick_ms > 0:
                        # 检查最后一次tick是否在13:00之前
                        latest_tick_timestamp = latest_tick_ms / 1000
                        latest_tick_dt = datetime.fromtimestamp(latest_tick_timestamp, beijing_tz)
                        latest_tick_time_obj = latest_tick_dt.time()
                        
                        # 如果最后一次tick在13:00之前，说明是午休前的时间，需要重置
                        # 这样计时器从13:00开始计算，避免因为11:30-13:00之间没有tick而误判超时
                        if latest_tick_time_obj < market_open_pm:
                            # 重置为当前时间，这样计时器从13:00开始计算
                            self.latest_tick_time = int(time.time() * 1000)
            
            if restart_reason is None:
                latest_tick_ms = getattr(self, 'latest_tick_time', 0)
                if latest_tick_ms:
                    if is_trading_time:
                        latest_tick_timestamp = latest_tick_ms / 1000
                        time_since_last_tick = time.time() - latest_tick_timestamp
                        tick_threshold = getattr(self.qmt_adapter, '_tick_restart_threshold', 30)
                        
                        # 检查当前时间是否在集合竞价时段（9:25-9:30），如果是则不触发重启
                        call_auction_start = dt_time(9, 25, 0)
                        call_auction_end = dt_time(9, 30, 0)
                        in_call_auction = call_auction_start <= current_time <= call_auction_end
                        
                        if (
                            time_since_last_tick > tick_threshold
                            and not in_call_auction
                            and not in_open_grace
                            and not in_resubscribe_grace
                        ):
                            restart_reason = f"行情回调已超过{int(time_since_last_tick)}秒未更新"
                    else:
                        # 非交易时段（包括午休时段），如果状态栏显示"等待行情数据"，也要检查
                        # 已取消：非交易时段不再因为未收到tick数据而自动重启
                        # if self._waiting_for_data_since is not None:
                        #     tick_threshold = getattr(self.qmt_adapter, '_tick_restart_threshold', 30)
                        #     waited = time.time() - self._waiting_for_data_since
                        #     self.logger.info(f"[自动重启] 非交易时段等待行情数据 {int(waited)}s，阈值 {tick_threshold}s")
                        #     if waited > tick_threshold:
                        #         restart_reason = f"非交易时段仍未获取行情数据，已等待{int(waited)}秒"
                        pass
                else:
                    # 启动以来从未收到过任何tick
                    if is_trading_time:
                        # 检查当前时间是否在集合竞价/开盘缓冲期，如果是则不触发重启
                        call_auction_start = dt_time(9, 25, 0)
                        call_auction_end = dt_time(9, 30, 0)
                        open_am_grace_start = dt_time(9, 30, 0)
                        open_am_grace_end = dt_time(9, 31, 0)
                        open_pm_grace_start = dt_time(13, 0, 0)
                        open_pm_grace_end = dt_time(13, 1, 0)
                        in_call_auction = call_auction_start <= current_time <= call_auction_end
                        in_open_grace = (
                            open_am_grace_start <= current_time <= open_am_grace_end
                            or open_pm_grace_start <= current_time <= open_pm_grace_end
                        )
                        
                        if not (in_call_auction or in_open_grace or in_resubscribe_grace):
                            # 使用回调对象内的持久化计时，避免每次调用被重置
                            if not hasattr(self, '_first_tick_wait_start_cb') or self._first_tick_wait_start_cb is None:
                                self._first_tick_wait_start_cb = time.time()
                            tick_threshold = getattr(self.qmt_adapter, '_tick_restart_threshold', 30)
                            waited = time.time() - self._first_tick_wait_start_cb
                            # 开盘缓冲期内不因「无首条 tick」触发重启
                            if waited > tick_threshold and not in_open_grace:
                                restart_reason = f"连续竞价开始后超过{int(tick_threshold)}秒仍未收到任何tick数据"
                    else:
                        # 非交易时段无tick通常是正常现象，禁止仅凭“无tick”触发自动重启。
                        pass
        except Exception as e:
            self.logger.error(f"[自动重启] 检查全局tick状态失败: {str(e)}")

        if (
            restart_reason
            and hasattr(self.qmt_adapter, '_can_restart_qmt')
            and self.qmt_adapter._can_restart_qmt()
            and not self.qmt_adapter._in_qmt_restart_grace()
        ):
            try:
                from utils.qmt_execution_config import allow_qmt_client_auto_restart
            except Exception:
                allow_qmt_client_auto_restart = lambda: True  # type: ignore[assignment,misc]
            if allow_qmt_client_auto_restart():
                self.qmt_adapter._restart_qmt(restart_reason)

    def quote_callback(self, quote):
        """行情数据回调"""
        # 性能监控：开始计时
        import time
        from datetime import datetime, timezone, timedelta
        callback_start = time.time()
        
        # 立即输出日志，确保能看到回调是否被调用
        if not hasattr(self, '_quote_callback_count'):
            self._quote_callback_count = 0
        self._quote_callback_count += 1
        if self._quote_callback_count == 1:
            self.logger.info("[行情回调] ✅ 已收到首条行情推送，quote_callback 正常工作")
        
        # 检查tick数据超时
        self._check_tick_timeout()
        
        # 性能汇总：每60秒输出一次统计（便于明日开盘后分析）
        if not hasattr(self, '_perf_summary_last_time'):
            self._perf_summary_last_time = time.time()
            self._perf_callback_count_60s = 0
            self._perf_max_callback_60s = 0.0
            self._perf_slow_stock_count_60s = 0
        if time.time() - self._perf_summary_last_time >= 60:
            # 降级为 DEBUG：性能汇总用于诊断，不希望实盘/截图时刷屏。
            # 注意：utils/logger.py 里 Logger.debug() 被重写为“无条件转发”，
            # 即使 logger 级别是 INFO 也可能在控制台显示。
            # 这里直接调用底层 python logger 的 debug，确保 DEBUG 会被正确过滤。
            self.logger.logger.debug(
                f"[性能汇总] 过去60秒 行情回调次数={self._perf_callback_count_60s} "
                f"最大回调耗时={self._perf_max_callback_60s:.3f}s 单股>50ms次数={self._perf_slow_stock_count_60s}"
            )
            self._perf_summary_last_time = time.time()
            self._perf_callback_count_60s = 0
            self._perf_max_callback_60s = 0.0
            self._perf_slow_stock_count_60s = 0
        
        try:
            
            if isinstance(quote, dict):
                # 处理多个股票的行情数据
                #self.logger.info(f"[行情回调] 处理dict模式行情数据，股票数量: {len(quote)}")
                for stock_code, stock_data in quote.items():
                    try:
                        t_stock_begin = time.time()
                        step_start = time.time()
                        status_bar_time = 0.0
                        pipe_send_time = 0.0
                        #self.logger.info(f"[行情回调] dict模式: {stock_code}, 数据: {stock_data}")
                        # 获取并转换时间
                        time_convert_start = time.time()
                        tick_time = stock_data.get('time', 0)
                        arrival_ms = int(time.time() * 1000)
                        if tick_time > 0:
                            # 转换为北京时间
                            dt = datetime.fromtimestamp(tick_time/1000, timezone(timedelta(hours=8)))
                            self.latest_tick_time = arrival_ms
                            self.latest_tick_time_str = dt.strftime('%H:%M:%S')
                            tick_time = dt  # 使用转换后的时间对象
                            #self.logger.info(f"[行情回调] 时间转换: {tick_time} -> {self.latest_tick_time_str}")
                        else:
                            self.latest_tick_time = arrival_ms
                            self.latest_tick_time_str = datetime.fromtimestamp(arrival_ms/1000, timezone(timedelta(hours=8))).strftime('%H:%M:%S')
                        time_convert_time = time.time() - time_convert_start
                        
                        tick_data_build_start = time.time()
                        tick_data = self._build_tick_data(stock_code, stock_data, tick_time)
                        tick_data_build_time = time.time() - tick_data_build_start
                        
                        # 更新该股票的最后 tick 到达时间（用本地时刻；QMT 的 time 字段常为空/0，不能作为唯一依据）
                        self.stock_last_tick_time[stock_code] = arrival_ms
                        if hasattr(self, '_first_tick_wait_start_cb'):
                            self._first_tick_wait_start_cb = None
                        if hasattr(self, '_non_trading_wait_start'):
                            self._non_trading_wait_start = None
                        
                        # 更新任务管理器的价格存储
                        price_update_start = time.time()
                        if (hasattr(self.qmt_adapter, 'task_manager') and 
                            self.qmt_adapter.task_manager is not None):
                            try:
                                current_price = tick_data.get('lastPrice', 0)
                                qmt_last_close = tick_data.get('lastClose', 0)
                                pre_close_price = self._get_pre_close_price(
                                    stock_code, qmt_last_close, last_price=current_price
                                )
                                
                                # 更新实时价格
                                if hasattr(self.qmt_adapter.task_manager, 'latest_prices'):
                                    self.qmt_adapter.task_manager.latest_prices[stock_code] = current_price
                                
                                # 更新昨收盘价（盘后已切至次日基准）
                                if hasattr(self.qmt_adapter.task_manager, 'pre_close_prices'):
                                    self.qmt_adapter.task_manager.pre_close_prices[stock_code] = pre_close_price
                                    
                                #self.logger.debug(f"[行情回调] 更新 {stock_code} 价格: 当前价={current_price}, 昨收={pre_close_price}")
                            except Exception as e:
                                self.logger.error(f"[行情回调] 更新任务管理器价格失败 {stock_code}: {str(e)}")
                        price_update_time = time.time() - price_update_start
                        
                        # 发射tick数据信号（用于触发任务执行）
                        signal_emit_start = time.time()
                        try:
                            if not hasattr(self, '_tick_signal_count'):
                                self._tick_signal_count = {}
                            if stock_code not in self._tick_signal_count:
                                self._tick_signal_count[stock_code] = 0
                            self._tick_signal_count[stock_code] += 1
                            
                            # 直接发送信号（PyQt信号默认使用QueuedConnection，已经是异步的）
                            # 信号发送本身很快，但接收端处理可能较慢，这是正常的
                            self.qmt_adapter.tick_data_signal.emit(tick_data)
                        except Exception as e:
                            self.logger.error(f"[行情回调] 发射tick信号失败 {stock_code}: {str(e)}")
                        signal_emit_time = time.time() - signal_emit_start
                        
                        #self.logger.info(f"[行情回调] 处理股票 {stock_code}: 当前价格={tick_data.get('lastPrice', 0)}")
                        
                        step_time = time.time() - step_start
                        # #region agent log
                        # 该日志写入频率极高（每个tick、每个股票），会显著拖慢行情回调。
                        # 仅在“明显卡顿”时落盘，避免把磁盘IO变成性能瓶颈。
                        try:
                            if step_time > 0.35:
                                log_path = os.devnull
                                log_entry = {
                                    'sessionId': 'debug-session',
                                    'runId': 'run1',
                                    'hypothesisId': 'C',
                                    'location': 'qmt_adapter.py:2741',
                                    'message': 'quote_callback stock processing completed',
                                    'data': {
                                        'stock_code': stock_code,
                                        'total_time': step_time,
                                        'time_convert_time': time_convert_time,
                                        'tick_data_build_time': tick_data_build_time,
                                        'price_update_time': price_update_time,
                                        'signal_emit_time': signal_emit_time,
                                        'timestamp': int(time.time() * 1000),
                                    },
                                }
                                with open(log_path, 'a', encoding='utf-8') as f:
                                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                        except:
                            pass
                        # #endregion
                        if step_time > 0.15:  # 单股总耗时>0.15秒再打一行强调
                            self.logger.warning(f"[性能监控] 处理股票{stock_code}耗时过长: {step_time:.3f}秒")
                        
                        # 更新状态栏显示
                        status_bar_start = time.time()
                        try:
                            # 检查是否有main_window（主程序环境）
                            # 如果没有main_window，说明是测试环境，直接返回，不输出日志
                            if not hasattr(self.qmt_adapter, 'main_window') or self.qmt_adapter.main_window is None:
                                continue
                            
                            # 检查task_manager是否存在（只在主程序环境中检查）
                            if not hasattr(self.qmt_adapter, 'task_manager') or self.qmt_adapter.task_manager is None:
                                self.logger.debug(f"[{stock_code}] task_manager未初始化，跳过状态栏更新")
                                return
                                
                            # 获取任务信息和参数
                            task_info = self.qmt_adapter.task_manager.get_task_by_stock_code(stock_code)
                            current_price = tick_data.get('lastPrice', 0)
                            precision = SecurityTypeUtil.get_price_precision(stock_code)
                            format_str = f'.{precision}f'
                            
                            #self.logger.debug(f"[行情回调] 处理股票 {stock_code}: 当前价格={current_price}, task_info={'存在' if task_info else '不存在'}")
                            
                            # 确保task_info是字典，如果不是则视为无任务
                            if task_info and not isinstance(task_info, dict):
                                task_info = None
                            
                            if not task_info:
                                # 如果没有找到任务，检查是否是订单监控的股票
                                is_order_monitoring = False
                                if hasattr(self.qmt_adapter, 'main_window') and self.qmt_adapter.main_window:
                                    #self.logger.debug(f"[行情回调] 检查 {stock_code} 是否为订单监控股票，main_window存在")
                                    if hasattr(self.qmt_adapter.main_window, 'order_monitors'):
                                        #self.logger.debug(f"[行情回调] order_monitors存在，监控列表: {list(self.qmt_adapter.main_window.order_monitors.keys())}")
                                        for order_id, monitor in self.qmt_adapter.main_window.order_monitors.items():
                                            monitor_stock = monitor.get('stock_code')
                                            monitor_status = monitor.get('is_monitoring', False)
                                            #self.logger.debug(f"[行情回调] 检查监控 {order_id}: stock_code={monitor_stock}, is_monitoring={monitor_status}")
                                            if monitor_stock == stock_code and monitor_status:
                                                is_order_monitoring = True
                                                break
                                    else:
                                        #self.logger.debug(f"[行情回调] main_window没有order_monitors属性")
                                        pass
                                else:
                                    #self.logger.debug(f"[行情回调] main_window不存在")
                                    pass
                                
                                if is_order_monitoring:
                                    # 订单监控的股票，只显示当前价格和监控标识
                                    display_text = f"{current_price:{format_str}} (监控中)"
                                else:
                                    # 只显示当前价格
                                    display_text = f"{current_price:{format_str}}"
                                
                                self.qmt_adapter.task_manager.price_displays[stock_code] = display_text  # 普通股票正常更新
                                #self.logger.info(f"[行情回调] 设置普通股票 {stock_code} 状态栏显示: {display_text}")
                                
                                # 主动调用状态栏更新
                                if hasattr(self.qmt_adapter, 'main_window') and self.qmt_adapter.main_window:
                                    try:
                                        # 使用信号机制更新状态栏，避免直接调用
                                        if hasattr(self.qmt_adapter.main_window, 'update_status_bar'):
                                            # 限制更新频率，避免过于频繁的更新
                                            current_time = time.time()
                                            if not hasattr(self, '_last_status_update') or current_time - self._last_status_update > 0.5:
                                                self._last_status_update = current_time
                                                self.qmt_adapter.main_window.update_status_bar()
                                        #self.logger.debug(f"[行情回调] 已触发状态栏更新")
                                    except Exception as e:
                                        self.logger.error(f"[行情回调] 触发状态栏更新失败: {str(e)}")
                            else:
                                # task_info存在，处理任务股票
                                # 添加类型检查，确保task_info是字典
                                if not isinstance(task_info, dict):
                                    self.logger.error(f"[行情回调] task_info类型错误: {type(task_info)}, stock_code={stock_code}")
                                    continue
                                
                                # 获取params，确保是字典类型
                                params_raw = task_info.get('params', {})
                                # 如果params是字符串（可能是JSON字符串），尝试解析
                                if isinstance(params_raw, str):
                                    try:
                                        params = json.loads(params_raw) if params_raw else {}
                                    except (json.JSONDecodeError, TypeError, ValueError) as e:
                                        self.logger.warning(f"[{stock_code}] params是字符串但无法解析为JSON ({str(e)})，使用默认值")
                                        params = {}
                                elif isinstance(params_raw, dict):
                                    params = params_raw
                                else:
                                    self.logger.warning(f"[{stock_code}] params类型异常: {type(params_raw)}，使用默认值")
                                    params = {}
                                
                                base_price = task_info.get('base_price', 0)
                                
                                # 状态栏统一显示实时价格（不再显示阈值，不区分任务是否运行）
                                display_text = f"{current_price:{format_str}}"
                                self.qmt_adapter.task_manager.price_displays[stock_code] = display_text
                                
                                # self.qmt_adapter.task_manager.price_displays[stock_code] = display_text  # 注释掉，避免与策略进程数据冲突
                                #self.logger.info(f"[行情回调] 设置任务股票 {stock_code} 状态栏显示: {display_text}")
                                
                                # 主动调用状态栏更新
                                if hasattr(self.qmt_adapter, 'main_window') and self.qmt_adapter.main_window:
                                    try:
                                        # 使用信号机制更新状态栏，避免直接调用
                                        if hasattr(self.qmt_adapter.main_window, 'update_status_bar'):
                                            # 限制更新频率，避免过于频繁的更新
                                            current_time = time.time()
                                            if not hasattr(self, '_last_status_update') or current_time - self._last_status_update > 0.5:
                                                self._last_status_update = current_time
                                                self.qmt_adapter.main_window.update_status_bar()
                                        #self.logger.debug(f"[行情回调] 已触发状态栏更新")
                                    except Exception as e:
                                        self.logger.error(f"[行情回调] 触发状态栏更新失败: {str(e)}")
                        except Exception as e:
                            self.logger.error(f"[{stock_code}] 更新状态栏显示失败: {str(e)}")
                        
                        step_time = time.time() - status_bar_start
                        status_bar_time = step_time
                        if step_time > 0.1:  # 如果更新状态栏耗时超过0.1秒，记录警告
                            self.logger.warning(f"[性能监控] 更新状态栏耗时过长: {step_time:.3f}秒")
                        
                        # 如果股票在任务进程中，发送行情数据到任务进程
                        pipe_send_start = time.time()
                        if (hasattr(self.qmt_adapter, 'task_manager') and 
                            self.qmt_adapter.task_manager and 
                            hasattr(self.qmt_adapter.task_manager, 'task_processes') and
                            stock_code in self.qmt_adapter.task_manager.task_processes):
                            # 获取该股票的所有任务进程
                            task_list = self.qmt_adapter.task_manager.task_processes[stock_code]
                            
                            # 发送行情数据到所有任务进程
                            for task_id, process, control_pipe, _ in task_list:
                                try:
                                    control_pipe.send(('tick', tick_data))
                                    # 正常发送tick数据，不需要记录日志
                                except Exception as e:
                                    self.logger.error(f"[{stock_code}] 发送行情数据到任务进程 {task_id} 失败: {str(e)}")
                                    pass
                        
                        # 注意：即使没有正在处理的任务，tick数据也已经通过上面的状态栏更新机制
                        # 被处理了，所以不需要额外的警告信息
                        
                        pipe_send_time = time.time() - pipe_send_start
                        step_total = time.time() - t_stock_begin
                        if step_total > 0.05:
                            self._perf_slow_stock_count_60s += 1
                            self.logger.warning(
                                f"[性能监控] 单股耗时 {stock_code}: 总={step_total:.3f}s "
                                f"时间转换={time_convert_time:.3f}s 组包={tick_data_build_time:.3f}s "
                                f"更新价格={price_update_time:.3f}s 发信号={signal_emit_time:.3f}s "
                                f"状态栏={status_bar_time:.3f}s 发进程={pipe_send_time:.3f}s"
                            )
                        step_time = step_total
                        if step_time > 0.15:  # 如果处理单个股票耗时超过0.15秒，记录警告
                            self.logger.warning(f"[性能监控] 处理股票{stock_code}耗时过长: {step_time:.3f}秒")
                                
                    except Exception as e:
                        self.logger.error(f"处理股票 {stock_code} 的行情数据失败: {str(e)}")
            else:
                # 处理单个股票的行情数据
                # self.logger.info(f"[行情回调] 单票模式，数据: {repr(quote)}")
                
                # 尝试多种方式获取股票代码
                stock_code = None
                if hasattr(quote, 'stock_code'):
                    stock_code = quote.stock_code
                elif hasattr(quote, 'code'):
                    stock_code = quote.code
                elif isinstance(quote, dict):
                    stock_code = quote.get('stock_code') or quote.get('code')
                
                # self.logger.info(f"[行情回调] 提取的股票代码: {stock_code}")
                
                if not stock_code:
                    self.logger.error(f"无法获取股票代码: {quote}")
                    return
                
                # 获取并转换时间
                tick_time = 0
                if hasattr(quote, 'time'):
                    tick_time = quote.time
                elif isinstance(quote, dict):
                    tick_time = quote.get('time', 0)
                
                arrival_ms = int(time.time() * 1000)
                if tick_time > 0:
                    # 转换为北京时间
                    dt = datetime.fromtimestamp(tick_time/1000, timezone(timedelta(hours=8)))
                    self.latest_tick_time = arrival_ms
                    self.latest_tick_time_str = dt.strftime('%H:%M:%S')
                    tick_time = dt  # 使用转换后的时间对象
                    self.logger.info(f"[行情回调] 单票时间转换: {tick_time} -> {self.latest_tick_time_str}")
                else:
                    self.latest_tick_time = arrival_ms
                    self.latest_tick_time_str = datetime.fromtimestamp(arrival_ms/1000, timezone(timedelta(hours=8))).strftime('%H:%M:%S')
                
                # 构建tick数据（含成交量/五档量，供真突破判定）
                tick_data = self._build_tick_data(stock_code, quote, tick_time)
                
                # self.logger.info(f"[行情回调] 构建的tick数据: {tick_data}")
                
                self.stock_last_tick_time[stock_code] = arrival_ms
                if hasattr(self, '_first_tick_wait_start_cb'):
                    self._first_tick_wait_start_cb = None
                if hasattr(self, '_non_trading_wait_start'):
                    self._non_trading_wait_start = None
                
                # 更新任务管理器的价格存储
                if (hasattr(self.qmt_adapter, 'task_manager') and 
                    self.qmt_adapter.task_manager is not None):
                    try:
                        current_price = tick_data.get('lastPrice', 0)
                        qmt_last_close = tick_data.get('lastClose', 0)
                        pre_close_price = self._get_pre_close_price(
                            stock_code, qmt_last_close, last_price=current_price
                        )
                        
                        # 更新实时价格
                        if hasattr(self.qmt_adapter.task_manager, 'latest_prices'):
                            self.qmt_adapter.task_manager.latest_prices[stock_code] = current_price
                        
                        # 更新昨收盘价（盘后已切至次日基准）
                        if hasattr(self.qmt_adapter.task_manager, 'pre_close_prices'):
                            self.qmt_adapter.task_manager.pre_close_prices[stock_code] = pre_close_price
                            
                        #self.logger.debug(f"[行情回调] 更新 {stock_code} 价格: 当前价={current_price}, 昨收={pre_close_price}")
                    except Exception as e:
                        self.logger.error(f"[行情回调] 更新任务管理器价格失败 {stock_code}: {str(e)}")
                
                # 发射tick数据信号（用于触发任务执行）
                try:
                    self.qmt_adapter.tick_data_signal.emit(tick_data)
                except Exception as e:
                    self.logger.error(f"[行情回调] 发射tick信号失败 {stock_code}: {str(e)}")
                
                self.logger.info(f"[行情回调] 处理单票 {stock_code}: 当前价格={tick_data.get('lastPrice', 0)}")
                
                # 更新状态栏显示
                try:
                    # 检查是否有main_window（主程序环境）
                    # 如果没有main_window，说明是测试环境，直接返回，不输出日志
                    if not hasattr(self.qmt_adapter, 'main_window') or self.qmt_adapter.main_window is None:
                        return
                    
                    # 检查task_manager是否存在（只在主程序环境中检查）
                    if not hasattr(self.qmt_adapter, 'task_manager') or self.qmt_adapter.task_manager is None:
                        self.logger.debug(f"[{stock_code}] task_manager未初始化，跳过状态栏更新")
                        return
                                
                    # 获取任务信息和参数
                    task_info = self.qmt_adapter.task_manager.get_task_by_stock_code(stock_code)
                    current_price = tick_data.get('lastPrice', 0)
                    precision = SecurityTypeUtil.get_price_precision(stock_code)
                    format_str = f'.{precision}f'
                    
                    self.logger.debug(f"[行情回调] 处理股票 {stock_code}: 当前价格={current_price}, task_info={'存在' if task_info else '不存在'}")
                    
                    if not task_info:
                        # 如果没有找到任务，检查是否是订单监控的股票
                        is_order_monitoring = False
                        if hasattr(self.qmt_adapter, 'main_window') and self.qmt_adapter.main_window:
                            self.logger.debug(f"[行情回调] 检查 {stock_code} 是否为订单监控股票，main_window存在")
                            if hasattr(self.qmt_adapter.main_window, 'order_monitors'):
                                self.logger.debug(f"[行情回调] order_monitors存在，监控列表: {list(self.qmt_adapter.main_window.order_monitors.keys())}")
                                for order_id, monitor in self.qmt_adapter.main_window.order_monitors.items():
                                    monitor_stock = monitor.get('stock_code')
                                    monitor_status = monitor.get('is_monitoring', False)
                                    self.logger.debug(f"[行情回调] 检查监控 {order_id}: stock_code={monitor_stock}, is_monitoring={monitor_status}")
                                    if monitor_stock == stock_code and monitor_status:
                                        is_order_monitoring = True
                                        break
                            else:
                                self.logger.debug(f"[行情回调] main_window没有order_monitors属性")
                        else:
                            self.logger.debug(f"[行情回调] main_window不存在")
                        
                        if is_order_monitoring:
                            # 订单监控的股票，只显示当前价格和监控标识
                            display_text = f"{current_price:{format_str}} (监控中)"
                        else:
                            # 只显示当前价格
                            display_text = f"{current_price:{format_str}}"
                        
                        self.qmt_adapter.task_manager.price_displays[stock_code] = display_text  # 普通股票正常更新
                        self.logger.info(f"[行情回调] 设置普通股票 {stock_code} 状态栏显示: {display_text}")
                        
                        # 主动调用状态栏更新
                        if hasattr(self.qmt_adapter, 'main_window') and self.qmt_adapter.main_window:
                            try:
                                # 使用信号机制更新状态栏，避免直接调用
                                if hasattr(self.qmt_adapter.main_window, 'update_status_bar'):
                                    # 限制更新频率，避免过于频繁的更新
                                    current_time = time.time()
                                    if not hasattr(self, '_last_status_update') or current_time - self._last_status_update > 0.5:
                                        self._last_status_update = current_time
                                        self.qmt_adapter.main_window.update_status_bar()
                                    #self.logger.debug(f"[行情回调] 已触发状态栏更新")
                            except Exception as e:
                                self.logger.error(f"[行情回调] 触发状态栏更新失败: {str(e)}")
                    else:
                        # task_info存在，处理任务股票
                        # 添加类型检查，确保task_info是字典
                        if not isinstance(task_info, dict):
                            self.logger.error(f"[行情回调] task_info类型错误: {type(task_info)}, stock_code={stock_code}")
                            return
                        
                        params = task_info.get('params', {})
                        base_price = task_info.get('base_price', 0)
                        
                        # 计算触发价格
                        up_threshold = float(params.get('up_threshold', 5.0)) / 100
                        down_threshold = float(params.get('down_threshold', 3.0)) / 100
                        up_price = base_price * (1 + up_threshold) if base_price > 0 else 0
                        down_price = base_price * (1 - down_threshold) if base_price > 0 else 0
                        
                        # 更新状态栏显示
                        # 始终显示实时价格，根据任务是否启动决定是否显示阈值
                        if (hasattr(self.qmt_adapter.task_manager, 'task_processes') and
                            stock_code in self.qmt_adapter.task_manager.task_processes):
                            # 任务已启动，显示完整信息（价格+阈值）
                            display_text = f"{current_price:{format_str}} [{up_price:{format_str}}/{down_price:{format_str}}]"
                            self.logger.info(f"[行情回调] {stock_code} 任务运行中，阈值: [{up_price:{format_str}}/{down_price:{format_str}}]")
                        else:
                            # 任务未启动或已暂停，只显示当前价格
                            display_text = f"{current_price:{format_str}}"
                            self.logger.info(f"[行情回调] {stock_code} 任务未运行或已暂停，只显示价格: {display_text}")
                            
                        self.qmt_adapter.task_manager.price_displays[stock_code] = display_text  # 普通股票正常更新
                        self.logger.info(f"[行情回调] 设置任务股票 {stock_code} 状态栏显示: {display_text}")
                        
                        # 主动调用状态栏更新
                        if hasattr(self.qmt_adapter, 'main_window') and self.qmt_adapter.main_window:
                            try:
                                # 使用信号机制更新状态栏，避免直接调用
                                if hasattr(self.qmt_adapter.main_window, 'update_status_bar'):
                                    # 限制更新频率，避免过于频繁的更新
                                    current_time = time.time()
                                    if not hasattr(self, '_last_status_update') or current_time - self._last_status_update > 0.5:
                                        self._last_status_update = current_time
                                        self.qmt_adapter.main_window.update_status_bar()
                                    #self.logger.debug(f"[行情回调] 已触发状态栏更新")
                            except Exception as e:
                                self.logger.error(f"[行情回调] 触发状态栏更新失败: {str(e)}")
                                
                except Exception as e:
                    self.logger.error(f"[{stock_code}] 更新状态栏显示失败: {str(e)}")
                
                # 如果股票在任务进程中，发送行情数据
                if (hasattr(self.qmt_adapter, 'task_manager') and 
                    self.qmt_adapter.task_manager and 
                    hasattr(self.qmt_adapter.task_manager, 'task_processes') and
                    stock_code in self.qmt_adapter.task_manager.task_processes):
                    # 获取进程和管道
                    process, control_pipe, _ = self.qmt_adapter.task_manager.task_processes[stock_code]
                    
                    # 发送行情数据
                    try:
                        control_pipe.send(('tick', tick_data))
                    except Exception as e:
                        self.logger.error(f"[{stock_code}] 发送行情数据失败: {str(e)}")
                    
            # 记录回调总耗时
            callback_total = time.time() - callback_start
            if hasattr(self, '_perf_callback_count_60s'):
                self._perf_callback_count_60s += 1
                if callback_total > self._perf_max_callback_60s:
                    self._perf_max_callback_60s = callback_total
            if callback_total > 0.5:  # 如果回调总耗时超过0.5秒，记录严重警告
                self.logger.error(f"[性能监控] ⚠️ 行情回调严重超时: {callback_total:.3f}秒")
            elif callback_total > 0.2:  # 如果回调总耗时超过0.2秒，记录警告
                self.logger.warning(f"[性能监控] 行情回调耗时过长: {callback_total:.3f}秒")
                    
        except Exception as e:
            self.logger.error(f"处理行情数据失败: {str(e)}")

    def update_subscribe_list(self, stock_codes, force_update=False):
        """更新订阅列表"""
        try:
            from utils.qmt_execution_config import skip_external_quote_subscribe
            if skip_external_quote_subscribe():
                return
        except Exception:
            pass
        with self._lock:
            # 清理和验证股票代码：确保都是字符串类型，过滤掉无效代码
            new_codes = set()
            for code in stock_codes:
                if code is None:
                    continue
                # 转换为字符串
                code_str = str(code).strip()
                # 过滤掉空字符串、纯数字（如2715）等无效代码
                if not code_str or code_str.isdigit() and len(code_str) < 6:
                    # 如果是6位数字，可能是有效的股票代码，但需要添加市场后缀
                    # 这里先跳过，因为无法确定市场
                    if len(code_str) == 6:
                        # 6位数字代码，尝试根据开头判断市场并添加后缀
                        if code_str.startswith(('0', '1', '3')):
                            code_str = f"{code_str}.SZ"
                        elif code_str.startswith(('5', '6')):
                            code_str = f"{code_str}.SH"
                        elif code_str.startswith(('4', '8', '920')):
                            code_str = f"{code_str}.BJ"
                        else:
                            # 无法判断市场，跳过
                            continue
                    else:
                        # 不是6位数字，跳过
                        continue
                # 确保代码包含市场后缀（.SH、.SZ、.BJ）
                if '.' not in code_str:
                    # 如果没有后缀，尝试添加
                    if len(code_str) == 6 and code_str.isdigit():
                        if code_str.startswith(('0', '1', '3')):
                            code_str = f"{code_str}.SZ"
                        elif code_str.startswith(('5', '6')):
                            code_str = f"{code_str}.SH"
                        elif code_str.startswith(('4', '8', '920')):
                            code_str = f"{code_str}.BJ"
                        else:
                            continue
                    else:
                        continue
                new_codes.add(code_str)
            
            is_new_day = self._is_new_trading_day()
            codes_changed = new_codes != self._stock_codes
            
            # 只在新的交易日、股票列表有变化或强制更新时更新订阅
            if not (force_update or is_new_day or codes_changed):
                return
            
            # 取消现有订阅
            if self.seq > 0:
                try:
                    cancel_queue = queue.Queue()
                    def cancel_subscription():
                        try:
                            xtdata.unsubscribe_quote(self.seq)
                            cancel_queue.put(True)
                        except Exception as e:
                            cancel_queue.put(e)
                    
                    cancel_thread = threading.Thread(target=cancel_subscription, daemon=True)
                    cancel_thread.start()
                    
                    try:
                        result = cancel_queue.get(timeout=5)
                        if isinstance(result, Exception):
                            self.logger.warning(f"取消订阅时出错: {str(result)}")
                    except queue.Empty:
                        self.logger.warning("取消订阅超时，继续执行")
                except Exception as e:
                    self.logger.error(f"取消订阅失败: {str(e)}")
                time.sleep(1)
            
            # 订阅新的股票列表
            if new_codes:
                try:
                    stock_codes_list = list(new_codes)
                    
                    # 9:30前预加载昨收盘价，避免在行情回调中重计算
                    self._preload_pre_close_prices(stock_codes_list)
                    
                    try:
                        result = xtdata.subscribe_whole_quote(stock_codes_list, callback=self.quote_callback)
                        
                        if isinstance(result, Exception):
                            self.logger.error(f"订阅失败: {str(result)}")
                            # 如果是认证错误，提示用户检查QMT登录状态
                            if "not authenticated" in str(result) or "未认证" in str(result):
                                self.logger.error("行情服务认证失败，请检查QMT客户端登录状态")
                            self.seq = -1
                        else:
                            self.seq = result
                            if self.seq > 0:
                                self._mark_resubscribe_grace()
                                if hasattr(self, '_first_tick_wait_start_cb'):
                                    self._first_tick_wait_start_cb = None
                                self.logger.info(
                                    f"✅ 订阅请求已受理（订阅号={self.seq}, 股票数量={len(new_codes)}）；"
                                    f"实际推送需等 quote_callback 收到首条行情"
                                )
                                self._stock_codes = new_codes
                            else:
                                self.logger.error(f"订阅失败，订阅号无效: {self.seq}")
                    except Exception as e:
                        # 这里记录异常信息即可，避免传递 exc_info=True 触发自定义 Logger 的兼容性问题
                        import traceback
                        self.logger.error(
                            f"订阅异常: {str(e)}\n{traceback.format_exc()}"
                        )
                        self.seq = -1
                except Exception as e:
                    import traceback
                    self.logger.error(
                        f"订阅股票失败: {str(e)}\n{traceback.format_exc()}"
                    )
            else:
                self._stock_codes = set()
                self.seq = 0
                if hasattr(self, '_first_tick_wait_start_cb'):
                    self._first_tick_wait_start_cb = None
                self.latest_tick_time = 0
                self.latest_tick_time_str = "无订阅"
                self.logger.info("无订阅标的（空仓且无任务），已取消行情订阅")

    def stop(self):
        """停止线程"""
        self._running = False
        
        # 等待线程结束，最多等待3秒
        import time
        start_time = time.time()
        while self.is_alive() and time.time() - start_time < 3:
            time.sleep(0.1)
        
        if self.is_alive():
            self.logger.warning("订阅线程停止超时，强制退出")

    def handle_reconnection(self):
        """处理QMT重连后的订阅恢复"""
        try:
            #self.logger.info("[重连] 进入handle_reconnection，准备重置订阅状态")
            # 重置订阅状态
            self.seq = 0
            self._stock_codes = set()
            self.latest_tick_time = 0
            self.latest_tick_time_str = "等待数据"
            
            # 清空价格显示
            if hasattr(self.qmt_adapter, 'task_manager'):
                self.qmt_adapter.task_manager.price_displays.clear()
            # 重置首个tick等待计时
            self.qmt_adapter._first_tick_wait_start = time.time()
            
            #self.logger.info("[重连] 订阅线程已重置，准备重新订阅")
            
            # 获取需要订阅的股票代码
            stock_codes = set()
            
            # 获取持仓股票 - 添加超时机制
            try:                
                positions_queue = queue.Queue()
                def get_positions():
                    try:
                        _, positions = self.qmt_adapter.get_asset_positions()
                        positions_queue.put(positions)
                    except Exception as e:
                        positions_queue.put(e)
                
                positions_thread = threading.Thread(target=get_positions, daemon=True)
                positions_thread.start()
                
                # 等待获取持仓信息，最多等待5秒
                try:
                    result = positions_queue.get(timeout=5)
                    if isinstance(result, Exception):
                        self.logger.error(f"[重连] 获取持仓股票失败: {str(result)}")
                        positions = None
                    else:
                        positions = result
                except queue.Empty:
                    self.logger.warning("[重连] 获取持仓股票超时")
                    positions = None
                
                if positions is not None and positions:
                    stock_codes.update(positions.keys())
                    #self.logger.info(f"[重连] 发现持仓股票: {list(positions.keys())}")
            except Exception as e:
                self.logger.error(f"[重连] 获取持仓股票失败: {str(e)}")
            
            # 获取任务中的股票
            task_stock_codes = []
            if hasattr(self.qmt_adapter, 'task_manager') and self.qmt_adapter.task_manager:
                task_stock_codes = [task.get('stock_code') for task in self.qmt_adapter.task_manager.tasks.values() if task.get('stock_code')]
                stock_codes.update(task_stock_codes)
                #self.logger.info(f"[重连] 发现任务股票: {task_stock_codes}")
            
            # 立即重新订阅 - 添加超时机制
            if stock_codes:
                #self.logger.info(f"[重连] 立即重新订阅股票: {list(stock_codes)}")
                
                # 使用线程执行订阅操作，避免阻塞
                subscribe_queue = queue.Queue()
                def subscribe_operation():
                    try:
                        self.update_subscribe_list(list(stock_codes), force_update=True)
                        subscribe_queue.put(True)
                    except Exception as e:
                        subscribe_queue.put(e)
                
                subscribe_thread = threading.Thread(target=subscribe_operation, daemon=True)
                subscribe_thread.start()
                
                # 等待订阅操作完成，最多等待15秒
                try:
                    result = subscribe_queue.get(timeout=15)
                    if isinstance(result, Exception):
                        self.logger.error(f"[重连] 重新订阅失败: {str(result)}")
                    else:
                        #self.logger.info("[重连] 重新订阅完成")
                        pass
                except queue.Empty:
                    self.logger.error("[重连] 重新订阅超时，可能是QMT行情服务未完全恢复")
            else:
                self.logger.info("[重连] 没有需要订阅的股票")
                
        except Exception as e:
            self.logger.error(f"处理重连订阅恢复失败: {str(e)}")
            import traceback
            self.logger.error(f"详细错误信息: {traceback.format_exc()}")

    
        
            