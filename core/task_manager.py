import os
import pandas as pd
from datetime import datetime, timedelta, date, time as datetime_time
import logging
from utils.trading_day import is_tradeday
from multiprocessing import Process, Pipe
import threading
import json
import time
import psutil
import ctypes
from threading import Thread, Lock
from core.strategy_engine import StrategyEngine
from PyQt5.QtCore import QObject, pyqtSignal, QTimer
from core.utils.security_type import SecurityTypeUtil
from strategies.conservative_strategy import ConservativeStrategy
from strategies.moderate_strategy import ModerateStrategy
from strategies.aggressive_strategy import AggressiveStrategy
from utils.logger import Logger
import traceback
import subprocess
import uuid

# 任务文件只持久化这些列，其余为运行时字段（加载时由 init_volume/init_cost 等推导）
PERSIST_TASK_COLUMNS = [
    'task_id', 'stock_code', 'stock_name', 'strategy', 'buy_date',
    'init_volume', 'init_cost', 'params', 'create_time', 'status', 'order_id',
]


class TaskManager(QObject):
    """任务管理器"""
    update_task_ui = pyqtSignal(str, str, object)  # 股票代码，字段名，新值
    tasks_updated = pyqtSignal()  # 添加这个信号，用于通知整个任务列表需要更新
    trade_record_updated = pyqtSignal(str, dict)  # 添加交易记录更新信号，参数为股票代码和交易信息

    @staticmethod
    def _is_rule_task_strategy(strategy: str) -> bool:
        """规则任务策略判断：仅保留「规则任务」口径。"""
        s = (strategy or "").strip()
        return s == "规则任务" or s.startswith("规则")

    @staticmethod
    def _json_default(obj):
        """params 落盘前 json.dumps 的 default：兼容 smart_sell 中的 time/datetime。"""
        if isinstance(obj, datetime):
            return obj.isoformat(sep=" ", timespec="seconds")
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, datetime_time):
            return obj.strftime("%H:%M:%S")
        return str(obj)
    
    def __init__(self, mode='real'):
        """初始化任务管理器
        Args:
            mode: 运行模式，'real'表示实盘，'backtest'表示回测
        """
        super().__init__()  # 确保调用父类的初始化方法
        
        # 初始化logger
        self.logger = Logger(mode=mode)
        #self.logger.info(f"TaskManager初始化: id={id(self)}")
        
        # 初始化任务字典
        self.tasks = {}
        
        # 初始化QMT适配器
        self.qmt_adapter = None
        
        # 设置任务文件路径 - 使用日期后缀
        current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        today = datetime.now().strftime('%Y-%m-%d')
        self.tasks_file = os.path.join(current_dir, 'data', f'current_tasks_{today}.xlsx')
        os.makedirs(os.path.dirname(self.tasks_file), exist_ok=True)
        
        # 设置其他属性
        self.mode = mode
        self.strategy_engine = StrategyEngine(mode=mode, logger=self.logger)
        self._trade_callbacks = {}
        self._pipe_locks = {}
        self.price_displays = {}  # 存储每个股票的任务显示信息列表
        self._last_trade_signals = {}
        self.task_params = {}
        self.task_processes = {}  # 存储每个股票的任务列表
        self.running_prices = {}

        # 添加价格存储字典
        self.latest_prices = {}  # 存储实时价格
        self.pre_close_prices = {}  # 存储昨收盘价
        
        # 添加缺失的属性
        self.running_tasks = {}  # 正在运行的任务
        self.max_tasks = 100     # 最大并发任务数（预约重载可能一次启动 20+）
        self.monitoring = True   # 监控状态属性，默认开启
        
        # 添加任务加载标志，避免重复加载
        self._tasks_loaded = False
        # 迁移任务重入保护：避免“迁移 -> save_tasks -> 再次迁移”递归刷屏
        self._is_migrating_tasks = False
        # 记录任务文件最后修改时间，用于检测策略生成系统等外部修改，避免退出时覆盖
        self._last_file_mtime = None
        # 重新加载后本次「新增」的股票代码集合（6 位），供界面区分显示；非重载时为 set()
        self._newly_loaded_stock_codes = set()
        # 重新加载时被置为暂停的任务显示名列表，供界面弹窗提示（与启动时一致）
        self._reload_paused_task_names = []
        
        # 添加重连处理标志，避免重复处理
        self._reconnection_processed = False
        
        # 添加信号阻止标志，用于避免删除任务时的无限循环
        
        # 检查是否可以使用multiprocessing
        self.use_multiprocessing = True
        try:
            # 测试multiprocessing是否可用
            test_parent, test_child = Pipe()
            test_parent.close()
            test_child.close()
        except Exception as e:
            self.logger.warning(f"multiprocessing不可用，将使用线程模式: {str(e)}")
            self.use_multiprocessing = False
        self._block_tasks_updated_signal = False
        
        # 加载任务（会在内部处理迁移逻辑）
        self.load_tasks()

        # 定时清仓集中调度（不依赖任务图表分页/暂停 UI）
        from core.scheduled_clear_manager import ScheduledClearManager
        from core.rule_activation_manager import RuleActivationManager
        self.scheduled_clear_manager = ScheduledClearManager(self)
        self.rule_activation_manager = RuleActivationManager(self)
        
        self.main_window = None  # 新增：主窗口引用
    
    def save_daily_tasks_backup(self):
        """保存每日任务备份"""
        try:
            # 获取当前日期
            today = datetime.now().strftime('%Y-%m-%d')
            
            # 构建备份文件路径 - 使用日期后缀
            backup_dir = os.path.join(os.path.dirname(self.tasks_file), 'tasks')
            os.makedirs(backup_dir, exist_ok=True)
            backup_file = os.path.join(backup_dir, f'tasks_{today}.xlsx')
            
            # 如果今天的备份文件已经存在，说明已经保存过，直接返回
            if os.path.exists(backup_file):
                return
            
            # 只保存持久化列，与主任务文件一致
            tasks_to_save = []
            for task in self.tasks.values():
                row = {k: task.get(k) for k in PERSIST_TASK_COLUMNS}
                if isinstance(row.get('params'), dict):
                    row['params'] = json.dumps(row['params'], default=self._json_default)
                tasks_to_save.append(row)
            df = pd.DataFrame(tasks_to_save, columns=PERSIST_TASK_COLUMNS)
            df.to_excel(backup_file, index=False)
            self.logger.info(f"保存{today}任务备份成功")
            
        except Exception as e:
            self.logger.error(f"保存每日任务备份失败：{str(e)}")

    def reset_load_flag(self):
        """重置任务加载标志，允许重新加载任务"""
        self._tasks_loaded = False
    
    def _check_and_migrate_previous_day_tasks(self):
        """检查并迁移任务（简化逻辑）"""
        if self._is_migrating_tasks:
            return
        # #region agent log
        log_path = os.devnull
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                import json
                log_entry = {
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "A",
                    "location": "task_manager.py:135",
                    "message": "开始检查并迁移任务",
                    "data": {"timestamp": datetime.now().isoformat()},
                    "timestamp": int(datetime.now().timestamp() * 1000)
                }
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except:
            pass
        # #endregion
        try:
            self._is_migrating_tasks = True
            # 检查当天是否已有任务文件
            if os.path.exists(self.tasks_file):
                self.logger.info("当天任务文件已存在，跳过迁移")
                # #region agent log
                try:
                    with open(log_path, 'a', encoding='utf-8') as f:
                        log_entry = {
                            "sessionId": "debug-session",
                            "runId": "run1",
                            "hypothesisId": "A",
                            "location": "task_manager.py:141",
                            "message": "当天任务文件已存在，跳过迁移",
                            "data": {"tasks_file": self.tasks_file},
                            "timestamp": int(datetime.now().timestamp() * 1000)
                        }
                        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                except:
                    pass
                # #endregion
                return
            
            # 从昨天开始往前查找最近的交易日
            yesterday = datetime.now().date() - timedelta(days=1)
            max_search_days = 10  # 最多往前查找10天（用于防止无限循环）
            search_count = 0
            found_trading_day = None
            
            # #region agent log
            try:
                with open(log_path, 'a', encoding='utf-8') as f:
                    log_entry = {
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "B",
                        "location": "task_manager.py:177",
                        "message": "开始查找最近的交易日",
                        "data": {"start_date": str(yesterday), "max_search_days": max_search_days},
                        "timestamp": int(datetime.now().timestamp() * 1000)
                    }
                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            except:
                pass
            # #endregion
            
            # 先找到最近的交易日
            current_date = yesterday
            while search_count < max_search_days:
                file_path = os.path.join(
                    os.path.dirname(self.tasks_file), 
                    f'current_tasks_{current_date.strftime("%Y-%m-%d")}.xlsx'
                )
                
                # #region agent log
                try:
                    with open(log_path, 'a', encoding='utf-8') as f:
                        log_entry = {
                            "sessionId": "debug-session",
                            "runId": "run1",
                            "hypothesisId": "B",
                            "location": "task_manager.py:200",
                            "message": "检查日期是否为交易日",
                            "data": {"current_date": str(current_date), "file_exists": os.path.exists(file_path), "is_trading_day": is_tradeday(current_date) if os.path.exists(file_path) else None},
                            "timestamp": int(datetime.now().timestamp() * 1000)
                        }
                        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                except:
                    pass
                # #endregion
                
                if os.path.exists(file_path):
                    # 检查是否是交易日
                    is_previous_day_trading = is_tradeday(current_date)
                    
                    if is_previous_day_trading:
                        # 找到交易日，记录并停止查找
                        found_trading_day = current_date
                        # #region agent log
                        try:
                            with open(log_path, 'a', encoding='utf-8') as f:
                                log_entry = {
                                    "sessionId": "debug-session",
                                    "runId": "run1",
                                    "hypothesisId": "C",
                                    "location": "task_manager.py:217",
                                    "message": "找到最近的交易日",
                                    "data": {"trading_day": str(found_trading_day), "file_path": file_path},
                                    "timestamp": int(datetime.now().timestamp() * 1000)
                                }
                                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                        except:
                            pass
                        # #endregion
                        break
                
                # 继续往前一天查找
                current_date -= timedelta(days=1)
                search_count += 1
            
            # 如果找到了交易日，从昨天开始到交易日之间的所有日期都需要检查
            if found_trading_day is not None:
                # #region agent log
                try:
                    with open(log_path, 'a', encoding='utf-8') as f:
                        log_entry = {
                            "sessionId": "debug-session",
                            "runId": "run1",
                            "hypothesisId": "D",
                            "location": "task_manager.py:240",
                            "message": "开始检查从昨天到最近交易日之间的所有日期",
                            "data": {"yesterday": str(yesterday), "found_trading_day": str(found_trading_day)},
                            "timestamp": int(datetime.now().timestamp() * 1000)
                        }
                        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                except:
                    pass
                # #endregion
                
                # 新的一天开始：先清空当前内存任务，仅保留“后续迁移进来”的任务。
                # 这样可以确保“不需要迁移”的旧任务不会残留在新一天的文件和UI里。
                self.tasks.clear()
                self.task_params.clear()

                total_migrated = 0
                # 从昨天开始，往前遍历到最近的交易日（包括）
                check_date = yesterday
                while check_date >= found_trading_day:
                    file_path = os.path.join(
                        os.path.dirname(self.tasks_file), 
                        f'current_tasks_{check_date.strftime("%Y-%m-%d")}.xlsx'
                    )
                    
                    if os.path.exists(file_path):
                        is_trading = is_tradeday(check_date)
                        
                        # #region agent log
                        try:
                            with open(log_path, 'a', encoding='utf-8') as f:
                                log_entry = {
                                    "sessionId": "debug-session",
                                    "runId": "run1",
                                    "hypothesisId": "D",
                                    "location": "task_manager.py:265",
                                    "message": "检查日期并迁移任务",
                                    "data": {"check_date": str(check_date), "is_trading": is_trading, "file_path": file_path},
                                    "timestamp": int(datetime.now().timestamp() * 1000)
                                }
                                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                        except:
                            pass
                        # #endregion
                        
                        if is_trading:
                            # 交易日：只迁移15点后的任务
                            self.logger.info(f"{check_date}是交易日，迁移15点后的任务")
                            migrated_count = self._migrate_tasks_from_file(file_path, only_after_15=True)
                            if migrated_count > 0:
                                self.logger.info(f"成功迁移 {migrated_count} 个任务从 {check_date}")
                                total_migrated += migrated_count
                        else:
                            # 非交易日：迁移所有任务（非夜市委托任务）
                            self.logger.info(f"{check_date}不是交易日，迁移所有非夜市委托任务")
                            migrated_count = self._migrate_tasks_from_file(file_path, only_after_15=False)
                            if migrated_count > 0:
                                self.logger.info(f"成功迁移 {migrated_count} 个任务从 {check_date}")
                                total_migrated += migrated_count
                    
                    # 继续往前一天
                    check_date -= timedelta(days=1)
                
                # #region agent log
                try:
                    with open(log_path, 'a', encoding='utf-8') as f:
                        log_entry = {
                            "sessionId": "debug-session",
                            "runId": "run1",
                            "hypothesisId": "D",
                            "location": "task_manager.py:295",
                            "message": "迁移任务完成",
                            "data": {"total_migrated": total_migrated, "found_trading_day": str(found_trading_day)},
                            "timestamp": int(datetime.now().timestamp() * 1000)
                        }
                        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                except:
                    pass
                # #endregion
                
                if total_migrated > 0:
                    self.logger.info(f"总共成功迁移 {total_migrated} 个任务")
                else:
                    self.logger.info(f"从 {yesterday} 到 {found_trading_day} 之间没有需要迁移的任务")
                    # 跨日无可迁移任务时，无条件落一份当日空任务文件并刷新UI，
                    # 覆盖掉当日文件中可能残留的“不需要迁移”旧任务。
                    self._create_empty_tasks_file_for_today()
            else:
                self.logger.info("往前查找10天都没有找到交易日")
            
        except Exception as e:
            self.logger.error(f"迁移任务失败: {str(e)}")
            # #region agent log
            try:
                with open(log_path, 'a', encoding='utf-8') as f:
                    log_entry = {
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "D",
                        "location": "task_manager.py:248",
                        "message": "迁移任务异常",
                        "data": {"error": str(e)},
                        "timestamp": int(datetime.now().timestamp() * 1000)
                    }
                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            except:
                pass
            # #endregion
            # 迁移失败不影响程序正常启动
        finally:
            self._is_migrating_tasks = False

    def _create_empty_tasks_file_for_today(self) -> None:
        """创建当日空任务文件（仅表头），并同步清空内存任务与UI。"""
        try:
            self.tasks.clear()
            self.task_params.clear()
            os.makedirs(os.path.dirname(self.tasks_file), exist_ok=True)
            df = pd.DataFrame([], columns=PERSIST_TASK_COLUMNS)
            df.to_excel(self.tasks_file, index=False)
            try:
                self._last_file_mtime = os.path.getmtime(self.tasks_file)
            except OSError:
                pass
            self.logger.info(f"已创建当日空任务文件: {self.tasks_file}")
            if not self._block_tasks_updated_signal:
                self.tasks_updated.emit()
        except Exception as e:
            self.logger.error(f"创建当日空任务文件失败：{str(e)}")
    
    def _migrate_tasks_from_file(self, source_file, only_after_15=False):
        """从指定文件迁移任务
        Args:
            source_file: 源文件路径
            only_after_15: 是否只迁移15点后创建的任务
        """
        try:
            # 加载源文件的任务
            df = pd.read_excel(source_file)
            source_tasks = df.to_dict('records')
            
            # 过滤需要迁移的任务
            tasks_to_migrate = []
            for task in source_tasks:
                # 根据参数决定迁移条件
                if only_after_15:
                    # 只迁移15点后创建的任务
                    if self._should_migrate_task_after_15(task):
                        tasks_to_migrate.append(self._prepare_task_for_migration(task))
                else:
                    # 迁移所有任务（非交易日）
                    if self._should_migrate_all_tasks(task):
                        tasks_to_migrate.append(self._prepare_task_for_migration(task))
            
            # 添加到当前任务列表
            for task in tasks_to_migrate:
                task_id = task['task_id']
                # 确保params是字典类型
                params = self._normalize_params(task.get('params', {}), task.get('stock_code', '未知'))
                task['params'] = params
                self.tasks[task_id] = task
                self.task_params[task_id] = params
            
            # 保存迁移后的任务
            if tasks_to_migrate:
                self.save_tasks(list(self.tasks.values()))
            
            return len(tasks_to_migrate)
            
        except Exception as e:
            self.logger.error(f"从文件迁移任务失败: {str(e)}")
            return 0
    
    def _normalize_params(self, params_value, stock_code='未知'):
        """规范化params字段，确保返回字典类型"""
        if params_value is None:
            return {}
        
        # 如果已经是字典，直接返回
        if isinstance(params_value, dict):
            return params_value
        
        # 如果是字符串，尝试解析为字典
        if isinstance(params_value, str):
            try:
                # 先尝试直接解析（标准JSON格式）
                params_value = json.loads(params_value)
            except (json.JSONDecodeError, TypeError, ValueError):
                try:
                    # 如果失败，尝试替换单引号为双引号（处理Python字典字符串格式）
                    # 注意：这个替换可能破坏包含单引号的字符串值，但为了兼容旧数据只能这样
                    params_value = json.loads(params_value.replace("'", '"'))
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    # 如果还是失败，记录警告并使用空字典
                    self.logger.warning(f"任务 {stock_code} params字段无法解析为JSON: {str(e)}, 原始值: {params_value[:100] if len(str(params_value)) > 100 else params_value}")
                    params_value = {}
        
        # 确保params是字典类型
        if not isinstance(params_value, dict):
            self.logger.warning(f"任务 {stock_code} params字段类型异常: {type(params_value)}, 使用空字典")
            params_value = {}
        
        return params_value
    
    def _prepare_task_for_migration(self, task):
        """准备任务用于迁移"""
        # 更新任务数据
        task['buy_date'] = datetime.now().strftime('%Y-%m-%d')
        task['status'] = '未运行'  # 重置状态
        
        # 处理旧任务：如果没有创建时间，添加默认值
        if 'create_time' not in task:
            task['create_time'] = task.get('buy_date', '') + ' 15:00:00'
            self.logger.info(f"旧任务 {task.get('stock_code')} 添加默认创建时间")
        
        # 生成新的任务ID（如果需要）
        if 'task_id' not in task or not task['task_id']:
            task['task_id'] = self.generate_task_id()
        
        # 处理 params - 确保始终是字典类型
        if 'params' in task:
            task['params'] = self._normalize_params(task['params'], task.get('stock_code', '未知'))
        
        return task
    
    def _should_migrate_task_after_15(self, task):
        """判断是否应该迁移15点后创建的任务"""
        # 不迁移已完成的夜市委托
        if (task.get('strategy', '').startswith('夜市') and 
            task.get('status') == '已委托'):
            return False
        
        # 如果有创建时间，检查是否为15点后创建
        if 'create_time' in task:
            try:
                create_time = datetime.strptime(task['create_time'], '%Y-%m-%d %H:%M:%S')
                return create_time.time() >= datetime_time(15, 0)
            except:
                # 解析失败，默认迁移
                return True
        
        # 没有创建时间的旧任务，默认迁移（兼容性考虑）
        return True
    
    def _should_migrate_all_tasks(self, task):
        """判断是否应该迁移所有任务（非交易日）"""
        # 不迁移已完成的夜市委托
        if (task.get('strategy', '').startswith('夜市') and 
            task.get('status') == '已委托'):
            return False
        
        # 非交易日：迁移所有任务
        return True
    
    def _should_migrate_task(self, task):
        """判断任务是否应该迁移"""
        # 不迁移已完成的夜市委托
        if (task.get('strategy', '').startswith('夜市') and 
            task.get('status') == '已委托'):
            return False
        
        # 如果有创建时间，检查迁移条件
        if 'create_time' in task:
            try:
                create_time = datetime.strptime(task['create_time'], '%Y-%m-%d %H:%M:%S')
                create_date = create_time.date()
                create_time_only = create_time.time()
                
                # 获取上一个交易日
                previous_trading_day_str = self.get_previous_trading_day()
                if not previous_trading_day_str:
                    return False
                
                previous_trading_day = datetime.strptime(previous_trading_day_str, '%Y-%m-%d').date()
                
                # 迁移条件（无论今天是否是交易日）：
                # 1. 上一个交易日15点后创建的任务
                # 2. 上一个交易日之后所有非交易日创建的任务
                if create_date == previous_trading_day:
                    # 上一个交易日：只迁移15点后创建的任务
                    return create_time_only >= datetime_time(15, 0)
                elif create_date > previous_trading_day:
                    # 上一个交易日之后：检查是否为非交易日
                    return not is_tradeday(create_date)
                else:
                    # 更早的日期：不迁移
                    return False
                    
            except Exception as e:
                # 解析失败，默认迁移（兼容性考虑）
                self.logger.warning(f"解析任务创建时间失败: {task.get('create_time')}, 默认迁移")
                return True
        
        # 没有创建时间的旧任务，默认迁移（兼容性考虑）
        return True
    
    
    def _clean_invalid_tasks(self):
        """清理无效的任务（非字典类型）"""
        invalid_tasks = []
        for task_id, task in self.tasks.items():
            if not isinstance(task, dict):
                invalid_tasks.append(task_id)
                self.logger.warning(f"发现无效任务: {task_id}, 类型: {type(task)}, 值: {task}")
        
        # 删除无效任务
        for task_id in invalid_tasks:
            del self.tasks[task_id]
            if task_id in self.task_params:
                del self.task_params[task_id]
        
        if invalid_tasks:
            self.logger.info(f"已清理{len(invalid_tasks)}个无效任务")
    
    def _task_persist_fingerprint_for_reload_compare(self, task):
        """重新加载前后对比用：忽略 task_id 与运行态字段，检测规则/持仓成本等持久化语义是否变化。"""
        if not isinstance(task, dict):
            return ""
        params = task.get("params") or {}
        if isinstance(params, dict):
            params = dict(params)
            for k in (
                "task_running",
                "task_paused",
                "pending_tick_execution",
                "scheduled_clear_executed",
            ):
                params.pop(k, None)
        key = {
            "stock_code": str(task.get("stock_code") or ""),
            "stock_name": str(task.get("stock_name") or ""),
            "strategy": str(task.get("strategy") or ""),
            "buy_date": str(task.get("buy_date") or ""),
            "init_volume": task.get("init_volume"),
            "init_cost": task.get("init_cost"),
            "params": params,
            "status": str(task.get("status") or ""),
            "order_id": task.get("order_id"),
            "create_time": str(task.get("create_time") or ""),
        }
        return json.dumps(key, sort_keys=True, ensure_ascii=False, default=str)
    
    def load_tasks(self, tasks_file=None, force_reload=False):
        """从文件加载任务
        Args:
            tasks_file: 可选的任务文件路径，如果不指定则使用self.tasks_file
            force_reload: 若为 True 则忽略已加载标志，从文件重新加载（用于“重新加载任务”）
        """
        if force_reload:
            self._tasks_loaded = False
        # 程序彻夜不关时，__init__ 里的 self.tasks_file 仍是「启动当日」；
        # 未显式指定文件时每次加载前对齐到当前自然日的 data/current_tasks_YYYY-MM-DD.xlsx
        if not tasks_file and self.update_tasks_file_path():
            self._tasks_loaded = False
        if self._tasks_loaded:
            return
        
        # 重新加载时：记录重载前的股票集合，用于本次加载后标记「新增」任务
        def _norm(sc):
            s = (sc or "").strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
            s = "".join(c for c in s if c.isdigit())
            return s.zfill(6) if len(s) >= 6 else (s[:6].zfill(6) if s else "")
        _old_stock_codes = set()
        _running_stock_codes_before = set()  # 重载前正在运行的股票（重载后将置为暂停，避免规则变更后仍“运行中”）
        _running_fingerprints_before = {}  # 重载前各运行中任务的持久化指纹（用于检测文件是否改了规则）
        if force_reload and self.tasks:
            _old_stock_codes = {_norm(t.get("stock_code")) for t in self.tasks.values()}
            _old_stock_codes = {c for c in _old_stock_codes if c}
            _running_stock_codes_before = {_norm(t.get("stock_code")) for t in self.tasks.values()
                if (t.get("params") or {}).get("task_running")}
            _running_stock_codes_before = {c for c in _running_stock_codes_before if c}
            for t in self.tasks.values():
                if not (t.get("params") or {}).get("task_running"):
                    continue
                norm = _norm(t.get("stock_code"))
                if norm:
                    _running_fingerprints_before[norm] = self._task_persist_fingerprint_for_reload_compare(t)

        # 重新加载时：为了不把“已执行过”的规则恢复成未执行，
        # 需要把旧内存中的规则 executed 信息合并到新从文件加载出来的同一规则上。
        _old_tasks_by_norm: dict = {}
        _old_any_executed_by_norm: dict = {}
        _old_rule_exec_payload_by_norm_and_key: dict = {}  # {norm: {rule_key: payload}}

        # 仅在 force_reload 时保留旧执行状态（否则会出现“跨天/跨版本”的误合并）
        if force_reload and self.tasks:
            for t in self.tasks.values():
                norm = _norm(t.get("stock_code"))
                if not norm:
                    continue
                _old_tasks_by_norm[norm] = t
                rules = ((t.get("params") or {}).get("rules") or [])
                if not isinstance(rules, list):
                    continue

                any_exec = False
                exec_payload_map: dict = {}

                # 执行状态相关字段：用于从旧规则拷贝到新规则
                copy_exec_keys = {
                    "executed",
                    "executed_time",
                    "executed_price",
                    "executed_volume",
                    "executed_grids",
                    "executed_grid_prices",
                    "executed_endpoint",
                    "scheduled_clear_executed",
                }

                def _rule_key_without_runtime_exec(rule: dict) -> str:
                    rr = dict(rule or {})
                    # 规则内容匹配用：忽略运行时 id 与执行相关字段
                    rr.pop("id", None)
                    for k in [
                        "executed",
                        "executed_time",
                        "executed_price",
                        "executed_volume",
                        "executed_grids",
                        "executed_grid_prices",
                        "executed_endpoint",
                        "scheduled_clear_executed",
                    ]:
                        rr.pop(k, None)
                    # enabled/cage_entered/early_order 等也属于运行时展示状态，忽略以提高匹配成功率
                    rr.pop("enabled", None)
                    rr.pop("cage_entered", None)
                    rr.pop("early_order", None)
                    return json.dumps(rr, sort_keys=True, ensure_ascii=False, default=str)

                for rule in rules:
                    if not isinstance(rule, dict):
                        continue
                    old_executed = bool(rule.get("executed", False) or rule.get("scheduled_clear_executed", False))
                    if old_executed:
                        any_exec = True
                    key = _rule_key_without_runtime_exec(rule)
                    payload = {k: rule.get(k) for k in copy_exec_keys if k in rule}
                    if payload:
                        exec_payload_map[key] = payload

                _old_any_executed_by_norm[norm] = any_exec
                if exec_payload_map:
                    _old_rule_exec_payload_by_norm_and_key[norm] = exec_payload_map
        
        self._newly_loaded_stock_codes = set()  # 默认无新增；仅 force_reload 且成功从文件加载后再更新
        
        # 使用新的迁移逻辑
        file_path = tasks_file or self.tasks_file
        
        # 如果指定文件不存在，先尝试迁移上一个交易日的任务
        if not tasks_file and not os.path.exists(file_path):
            self._check_and_migrate_previous_day_tasks()
            # 迁移后重新获取文件路径
            file_path = self.tasks_file
        
        if file_path and os.path.exists(file_path):
            try:
                df = pd.read_excel(file_path)
                # 记录文件修改时间，供保存时检测外部修改
                self._last_file_mtime = os.path.getmtime(file_path)
                # 只保留持久化列，多余列丢弃（后续会写回文件以物理删除多余列）
                file_had_extra_columns = bool(set(df.columns) - set(PERSIST_TASK_COLUMNS))
                for c in PERSIST_TASK_COLUMNS:
                    if c not in df.columns:
                        df[c] = None
                df = df[PERSIST_TASK_COLUMNS]
                tasks = df.to_dict('records')
                
                def _normalize_stock_code(sc):
                    """统一为 6 位代码便于同股合并"""
                    s = (sc or "").strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
                    s = "".join(c for c in s if c.isdigit())
                    return s.zfill(6) if len(s) >= 6 else (s[:6].zfill(6) if len(s) > 0 else "")
                
                def _rule_content_key(rule):
                    """规则内容键，用于去重（忽略 id）。完全相同的规则得到相同 key。"""
                    if not isinstance(rule, dict):
                        return id(rule)
                    r = dict(rule)
                    r.pop("id", None)
                    return json.dumps(r, sort_keys=True, ensure_ascii=False)
                
                def _dedupe_rules(rules_list):
                    """规则列表去重：内容完全相同的多条只保留第一条。"""
                    if not rules_list:
                        return []
                    seen = set()
                    out = []
                    for r in rules_list:
                        key = _rule_content_key(r)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append(r)
                    return out
                
                processed_tasks = []
                for task in tasks:
                    # 获取股票代码（仅持久化列，此处必有）
                    stock_code = task.get('stock_code')
                    if stock_code is None or pd.isna(stock_code) or str(stock_code).strip() == '':
                        continue
                    
                    # base_price 为运行时字段，统一由 init_cost 推导（不持久化）
                    init_cost_val = task.get('init_cost') or 0
                    if pd.notna(init_cost_val):
                        task['base_price'] = SecurityTypeUtil.round_price(stock_code, float(init_cost_val))
                    else:
                        task['base_price'] = 0.0
                    
                    # 处理 params - 确保始终是字典类型
                    if 'params' in task:
                        params_value = task['params']
                        
                        # 如果params是字符串，尝试解析为字典
                        if isinstance(params_value, str):
                            try:
                                # 先尝试直接解析（标准JSON格式）
                                params_value = json.loads(params_value)
                            except (json.JSONDecodeError, TypeError, ValueError):
                                try:
                                    # 如果失败，尝试替换单引号为双引号（处理Python字典字符串格式）
                                    # 注意：这个替换可能破坏包含单引号的字符串值，但为了兼容旧数据只能这样
                                    params_value = json.loads(params_value.replace("'", '"'))
                                except (json.JSONDecodeError, TypeError, ValueError) as e:
                                    # 如果还是失败，记录警告并使用空字典
                                    self.logger.warning(f"任务 {task.get('stock_code', '未知')} params字段无法解析为JSON: {str(e)}, 原始值: {params_value[:100] if len(str(params_value)) > 100 else params_value}")
                                    params_value = {}
                        
                        # 确保params是字典类型
                        if not isinstance(params_value, dict):
                            self.logger.warning(f"任务 {task.get('stock_code', '未知')} params字段类型异常: {type(params_value)}, 使用空字典")
                            params_value = {}
                        
                        # 保存解析后的params
                        task['params'] = params_value
                        
                        # 检查是否是夜市任务
                        if task.get('strategy', '').startswith('夜市'):
                            # 夜市任务，保留原有的params信息，只添加缺失的字段
                            task['params']['is_night_task'] = True
                            if 'task_type' not in task['params']:
                                task['params']['task_type'] = 'sell' if '卖出' in task.get('strategy', '') else 'buy'
                        elif len(task['params']) == 0:
                            # 普通策略任务，如果params为空，设置默认参数
                            strategy = task.get('strategy', '规则任务')
                            task['params'] = self.create_default_params(strategy)
                    else:
                        # 检查是否是夜市任务
                        if task.get('strategy', '').startswith('夜市'):
                            # 夜市任务，保留原有的params信息，只添加缺失的字段
                            if not isinstance(task['params'], dict):
                                task['params'] = {}
                            task['params']['is_night_task'] = True
                            if 'task_type' not in task['params']:
                                task['params']['task_type'] = 'sell' if '卖出' in task.get('strategy', '') else 'buy'
                        else:
                            # 普通策略任务，设置默认参数
                            strategy = task.get('strategy', '规则任务')
                            task['params'] = self.create_default_params(strategy)
                    
                    # 处理持仓字段 - 保持init_volume不变，只更新volume
                    if 'init_volume' in task:
                        # 处理NaN值
                        if pd.isna(task['init_volume']):
                            task['init_volume'] = 0
                        else:
                            # 确保是整数
                            task['init_volume'] = int(task['init_volume'])
                        
                        # 对于普通策略任务，从持仓更新volume字段
                        if not task.get('strategy', '').startswith('夜市'):
                            # 如果有QMT适配器，获取当前可用持仓
                            if self.qmt_adapter and self.qmt_adapter.is_ready():
                                position = self.qmt_adapter.get_stock_position(stock_code)
                                if position:
                                    task['volume'] = position.get('can_use_volume', task['init_volume'])
                                else:
                                    task['volume'] = task['init_volume']
                            else:
                                # QMT适配器未初始化，使用init_volume作为volume
                                task['volume'] = task['init_volume']
                    else:
                        # 如果没有初始股数，记录当前股数
                        volume = task.get('volume', 0)
                        # 处理NaN值
                        if pd.isna(volume):
                            volume = 0
                        else:
                            # 确保是整数
                            volume = int(volume)
                        task['init_volume'] = volume
                        task['volume'] = volume
                    
                    # 检查买入日期
                    if 'buy_date' not in task:
                        try:
                            # 如果没有买入日期，检查可用数量
                            # 检查QMT适配器是否已经初始化
                            if self.qmt_adapter and self.qmt_adapter.is_ready():
                                position = self.qmt_adapter.get_stock_position(stock_code)
                                if position and position.get('can_use_volume', 0) == 0:
                                    # 如果可用数量为0，说明是今天买入的
                                    task['buy_date'] = datetime.now().strftime('%Y-%m-%d')
                                else:
                                    # 否则使用上一个交易日
                                    task['buy_date'] = self.get_previous_trading_day()
                            else:
                                # QMT适配器未初始化，使用上一个交易日
                                task['buy_date'] = self.get_previous_trading_day()
                        except Exception as e:
                            # 如果获取持仓失败，默认使用今天
                            self.logger.warning(f"获取{stock_code}持仓失败，默认使用今天作为买入日期：{str(e)}")
                            task['buy_date'] = datetime.now().strftime('%Y-%m-%d')
                    else:
                        # 确保买入日期是字符串格式
                        if isinstance(task['buy_date'], (datetime, date)):
                            task['buy_date'] = task['buy_date'].strftime('%Y-%m-%d')
                        elif isinstance(task['buy_date'], str):
                            # 对于夜市任务，保留时间戳以确保唯一性
                            if task.get('strategy', '').startswith('夜市'):
                                # 夜市任务保留完整的时间戳
                                pass
                            else:
                                # 普通策略任务，如果buy_date包含时间戳，只取日期部分
                                if '_' in task['buy_date']:
                                    task['buy_date'] = task['buy_date'].split('_')[0]
                    
                    # 生成任务ID
                    if 'task_id' in task and task['task_id']:
                        # 如果任务已经有UUID格式的ID，使用它
                        task_id = task['task_id']
                    else:
                        # 否则生成新的UUID格式ID
                        task_id = self.generate_task_id()
                        task['task_id'] = task_id
                    
                    processed_tasks.append(task)
                
                # 一只股票只保留一个任务：按 6 位代码合并，同股多条只保留一条并合并 params.rules
                merged_by_stock = {}
                for task in processed_tasks:
                    norm = _normalize_stock_code(task.get('stock_code'))
                    if not norm:
                        continue
                    if norm in merged_by_stock:
                        existing = merged_by_stock[norm]
                        rules = (existing.get('params') or {}).get('rules') or []
                        rules = list(rules)
                        rules.extend((task.get('params') or {}).get('rules') or [])
                        if isinstance(existing.get('params'), dict):
                            existing['params']['rules'] = _dedupe_rules(rules)
                    else:
                        merged_by_stock[norm] = task
                
                self.tasks.clear()
                self.task_params.clear()
                for task in merged_by_stock.values():
                    task_id = task['task_id']
                    self.tasks[task_id] = task
                    self.task_params[task_id] = task['params']

                # 应用旧执行状态合并：避免重载后把 executed 规则恢复为未执行
                if force_reload and _old_rule_exec_payload_by_norm_and_key:
                    applied_rules = 0
                    applied_norms = set()
                    for task_id, task in self.tasks.items():
                        norm = _norm(task.get("stock_code"))
                        if not norm:
                            continue
                        exec_payload_map = _old_rule_exec_payload_by_norm_and_key.get(norm) or {}
                        if not exec_payload_map:
                            continue
                        params = task.get("params") or {}
                        rules = params.get("rules") or []
                        if not isinstance(rules, list):
                            continue
                        for rule in rules:
                            if not isinstance(rule, dict):
                                continue
                            # 用同一套 key 忽略运行时执行字段做匹配
                            # 这里复用上面同名闭包逻辑：把 executed 相关字段从 rule 中移除后做 key
                            rr = dict(rule)
                            rr.pop("id", None)
                            for k in [
                                "executed",
                                "executed_time",
                                "executed_price",
                                "executed_volume",
                                "executed_grids",
                                "executed_grid_prices",
                                "executed_endpoint",
                                "scheduled_clear_executed",
                            ]:
                                rr.pop(k, None)
                            rr.pop("enabled", None)
                            rr.pop("cage_entered", None)
                            rr.pop("early_order", None)
                            rule_key = json.dumps(rr, sort_keys=True, ensure_ascii=False, default=str)
                            payload = exec_payload_map.get(rule_key) or {}
                            if not payload:
                                continue
                            for k, v in payload.items():
                                rule[k] = v
                            applied_rules += 1
                            applied_norms.add(norm)
                    try:
                        self.logger.info(
                            f"重新加载任务：已合并保留执行状态 rules={applied_rules} 股票={len(applied_norms)}"
                        )
                    except Exception:
                        pass
                
                if force_reload:
                    # 本次重载中新出现的股票（任务文件里原来没有的）
                    self._newly_loaded_stock_codes = set(merged_by_stock.keys()) - _old_stock_codes
                    self._reload_paused_task_names = []
                    # 对「重载前正在运行」且「文件中的任务与内存中持久化内容不一致」的股票置为暂停。
                    # 旧逻辑仅用「新增股票代码 ∩ 运行中」几乎永远为空；应比较重载前后指纹（规则/成本等变化）。
                    affected = set()
                    if _running_stock_codes_before:
                        for norm in _running_stock_codes_before:
                            new_task = merged_by_stock.get(norm)
                            if not new_task:
                                continue
                            old_fp = _running_fingerprints_before.get(norm)
                            new_fp = self._task_persist_fingerprint_for_reload_compare(new_task)
                            if old_fp is not None and old_fp != new_fp:
                                affected.add(norm)
                        # 兼容：文件里新出现的股票代码若恰好在运行集合中（极少见，如规范化/合并边界）
                        affected |= _running_stock_codes_before & self._newly_loaded_stock_codes
                    if affected:
                        for task in self.tasks.values():
                            norm = _norm(task.get("stock_code"))
                            if norm in affected:
                                params = task.get("params") or {}
                                params["task_running"] = False
                                params["task_paused"] = True
                                task["params"] = params
                                self.task_params[task["task_id"]] = params
                                self._reload_paused_task_names.append(
                                    f"{task.get('stock_name', '未知')} ({task.get('stock_code', '')})"
                                )
                                self.logger.info(
                                    f"重新加载：检测到任务内容变化，已将正在运行的 {task.get('stock_code')} 置为暂停，请确认规则后手动启动"
                                )
                        try:
                            self._block_tasks_updated_signal = True
                            self.save_tasks(list(self.tasks.values()))
                        except Exception as save_err:
                            self.logger.warning(f"重新加载后保存暂停状态失败: {save_err}")
                        finally:
                            self._block_tasks_updated_signal = False
                else:
                    self._newly_loaded_stock_codes = set()
                
                if len(merged_by_stock) < len(processed_tasks):
                    self.logger.info(f"已按股票合并任务：{len(processed_tasks)} 条合并为 {len(merged_by_stock)} 条（一只股票只保留一个任务）")
                    try:
                        self._block_tasks_updated_signal = True
                        self.save_tasks(list(self.tasks.values()))
                    except Exception as save_err:
                        self.logger.warning(f"合并后写回任务文件失败：{save_err}")
                    finally:
                        self._block_tasks_updated_signal = False
                
                # 保存每日任务备份
                self.save_daily_tasks_backup()
                
                # 发送UI状态更新信号
                for task_id, task in self.tasks.items():
                    stock_code = task.get('stock_code')
                    status = task.get('status', '未运行')
                    if stock_code:
                        self.update_task_ui.emit(task_id, 'status', status)
                
                # 检查有委托号的任务
                tasks_with_orders = []
                for task_id, task in self.tasks.items():
                    order_id = task.get('order_id')
                    if ('夜市' in task.get('strategy', '') or task.get('strategy', '') in ['夜市卖出', '夜市买入']) and order_id and str(order_id).lower() != 'nan':
                        tasks_with_orders.append(task_id)
                
                if tasks_with_orders:
                    #self.logger.warning(f"加载任务时发现 {len(tasks_with_orders)} 个有委托号的任务，请手动启动以运行涨跌停板检测和撤单逻辑")
                    for task_id in tasks_with_orders:
                        task = self.tasks[task_id]
                        stock_code = task.get('stock_code')
                        order_id = task.get('order_id')
                        #self.logger.warning(f"[{stock_code}] 任务 {task_id} 有委托号 {order_id}，状态：{task.get('status', '未运行')}")
                
                # 不自动启动任务，让用户手动启动或通过重连恢复
                # 程序重启后，重置任务状态
                for task_id, task in self.tasks.items():
                    # 检查是否是夜市任务且有委托号
                    if task.get('strategy', '').startswith('夜市') and task.get('order_id'):
                        # 夜市任务且有委托号，检查原始状态
                        original_status = task.get('status', '未运行')
                        if original_status == '已委托':
                            # 原本就是已委托状态，保持
                            task['status'] = '已委托'
                        else:
                            # 原本不是已委托状态，重置为未运行
                            task['status'] = '未运行'
                            # 清除委托号，因为未运行的任务不应该有委托号
                            if 'order_id' in task:
                                del task['order_id']
                    else:
                        # 其他任务：
                        # 若旧任务已有 executed 规则，避免重载把其重置为未执行/未运行
                        norm = _norm(task.get('stock_code'))
                        if _old_any_executed_by_norm.get(norm):
                            old_task = _old_tasks_by_norm.get(norm)
                            if old_task:
                                task['status'] = old_task.get('status', task.get('status', '未运行'))
                                # 若旧任务仍有委托号，也保留（供 UI 判断已委托/已成交）
                                if old_task.get('order_id') is not None:
                                    task['order_id'] = old_task.get('order_id')
                        else:
                            task['status'] = '未运行'
                
                #self.logger.info(f"任务加载完成: id={id(self)}, 共加载 {len(self.tasks)} 个任务，状态：{[task.get('status', '未运行') for task in self.tasks.values()]}")
                
                # 清理无效任务
                self._clean_invalid_tasks()
                
                # 若文件中曾有多余列，写回仅含持久化列，物理删除多余列
                if file_had_extra_columns:
                    try:
                        self._block_tasks_updated_signal = True
                        self.save_tasks(list(self.tasks.values()))
                        self.logger.info("任务文件已清理多余列并重写")
                    except Exception as save_err:
                        self.logger.warning(f"清理任务文件多余列时保存失败：{save_err}")
                    finally:
                        self._block_tasks_updated_signal = False
                
                # 设置任务加载标志为True
                self._tasks_loaded = True
                if not self._block_tasks_updated_signal:
                    self.tasks_updated.emit()
                self._sync_rules_armed_if_builtin()
                return tasks
            except Exception as e:
                self.logger.error(f"加载任务文件失败：{str(e)}")
                # 即使加载失败也要标记为已加载，避免无限等待
                self._tasks_loaded = True
                return []
        else:
            # 当天任务文件不存在，返回空列表
            self.logger.info(f"当天任务文件不存在: {file_path}")
            self._tasks_loaded = True
            if not self._block_tasks_updated_signal:
                self.tasks_updated.emit()
            self._sync_rules_armed_if_builtin()
            return []
    
    def _sync_rules_armed_if_builtin(self) -> None:
        """builtin/standalone 模式下，将任务与持仓同步到 rules_armed.json。"""
        try:
            from utils.qmt_execution_config import use_builtin_price_feed
            if not use_builtin_price_feed():
                return
            from utils.rules_armed_sync import sync_rules_armed
            sync_rules_armed(self, self.qmt_adapter, logger=self.logger)
        except Exception as e:
            self.logger.debug(f"sync rules_armed skipped: {e}")
    
    @staticmethod
    def _normalize_stock_code(sc):
        """统一为 6 位股票代码，供合并/去重使用"""
        s = (sc or "").strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        s = "".join(c for c in s if c.isdigit())
        return s.zfill(6) if len(s) >= 6 else (s[:6].zfill(6) if len(s) > 0 else "")

    def _dedupe_rules_list(self, rules_list):
        """规则列表去重（忽略 id），与 load_tasks / 策略生成器 task_builder 一致。"""
        if not rules_list:
            return []
        seen = set()
        out = []
        for r in rules_list:
            if not isinstance(r, dict):
                continue
            rr = dict(r)
            rr.pop("id", None)
            key = json.dumps(rr, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    def _merge_task_rows_by_stock(self, tasks):
        """多行任务按股票合并为一条，params.rules 合并后去重（同 load_tasks）。"""
        merged_by_stock = {}
        for task in tasks:
            norm = self._normalize_stock_code(task.get("stock_code"))
            if not norm:
                continue
            if norm in merged_by_stock:
                existing = merged_by_stock[norm]
                r1 = list((existing.get("params") or {}).get("rules") or [])
                r2 = list((task.get("params") or {}).get("rules") or [])
                if not isinstance(existing.get("params"), dict):
                    existing["params"] = {}
                existing["params"]["rules"] = self._dedupe_rules_list(r1 + r2)
            else:
                merged_by_stock[norm] = task
        return merged_by_stock

    def _read_tasks_from_file(self):
        """从当前任务文件读取任务列表（仅解析为 dict 列表，不写入 self.tasks）。用于与内存合并。"""
        if not self.tasks_file or not os.path.exists(self.tasks_file):
            return []
        try:
            df = pd.read_excel(self.tasks_file)
            for c in PERSIST_TASK_COLUMNS:
                if c not in df.columns:
                    df[c] = None
            df = df[[c for c in PERSIST_TASK_COLUMNS if c in df.columns]]
            out = []
            for _, row in df.iterrows():
                task = row.to_dict()
                stock_code = task.get('stock_code')
                if stock_code is None or pd.isna(stock_code) or str(stock_code).strip() == '':
                    continue
                if 'params' in task and isinstance(task['params'], str):
                    try:
                        task['params'] = json.loads(task['params'])
                    except (json.JSONDecodeError, TypeError, ValueError):
                        try:
                            task['params'] = json.loads((task['params'] or "").replace("'", '"'))
                        except Exception:
                            task['params'] = {}
                if not isinstance(task.get('params'), dict):
                    task['params'] = {}
                out.append(task)
            return out
        except Exception as e:
            self.logger.warning(f"读取任务文件用于合并失败: {e}")
            return []
    
    def _merge_external_file_with_memory(self):
        """将磁盘任务与内存任务按股票合并后返回列表，供 save_tasks 写入。

        同一只股票：合并 params.rules（内存规则 + 文件规则，去重），其余字段以内存任务为准
        （保留 task_id、运行态等）。仅存在于文件中的股票整行保留。

        说明：旧逻辑「同股以内存为准」会在用户先用策略生成器写入买入、再追加止盈后，
        若主程序仍持有仅含买入的内存，一次保存会覆盖掉文件中已合并的止盈规则，表现为
        「后生成的卖出留下、先生成的买入消失」。现改为与 load_tasks 相同的规则合并。
        """
        file_tasks = self._read_tasks_from_file()
        file_by_stock = self._merge_task_rows_by_stock(file_tasks)
        our_by_stock = self._merge_task_rows_by_stock(list(self.tasks.values()))
        all_norms = set(file_by_stock.keys()) | set(our_by_stock.keys())
        merged = []
        for norm in sorted(all_norms):
            ot = our_by_stock.get(norm)
            ft = file_by_stock.get(norm)
            if ot and ft:
                r_mem = list((ot.get("params") or {}).get("rules") or [])
                r_file = list((ft.get("params") or {}).get("rules") or [])
                if not isinstance(ot.get("params"), dict):
                    ot["params"] = {}
                ot["params"]["rules"] = self._dedupe_rules_list(r_mem + r_file)
                merged.append(ot)
            elif ot:
                merged.append(ot)
            else:
                merged.append(ft)
        return merged
    
    def save_tasks(self, tasks):
        """保存任务到文件。若检测到任务文件已被外部（如策略生成系统）修改，则先与内存合并再保存，既保留新任务也保留手动修改。"""
        try:
            # 与 load_tasks 一致：跨天常驻时先把路径切到当日，避免整晚仍写入「启动日」任务表
            if self.update_tasks_file_path():
                self._tasks_loaded = False
                self.logger.info("保存任务：检测到自然日切换，已切换到当日任务文件路径")
            # 检测任务文件是否被外部修改（如策略生成系统写入新任务）
            if os.path.exists(self.tasks_file):
                try:
                    current_mtime = os.path.getmtime(self.tasks_file)
                    need_merge = False
                    if self._last_file_mtime is None:
                        need_merge = True
                        self.logger.info("任务文件存在但未由本进程加载过，将先与当前任务合并再保存")
                    elif current_mtime > self._last_file_mtime + 1:  # 1 秒容差
                        need_merge = True
                        self.logger.info("任务文件已被外部修改，将先与当前修改合并再保存，避免丢失策略生成系统新任务或手动修改")
                    if need_merge:
                        tasks = self._merge_external_file_with_memory()
                except OSError:
                    pass
            # 当日任务文件尚不存在时，走与加载相同的迁移逻辑（由策略生成器写入的新文件也会被后续合并逻辑处理）
            if (not os.path.exists(self.tasks_file)) and (not self._is_migrating_tasks):
                self._check_and_migrate_previous_day_tasks()
            
            # 如果有运行中的任务，保护它们的状态
            running_task_states = {}
            if self.running_tasks:
                #self.logger.info(f"保护{len(self.running_tasks)}个运行中任务的状态")
                for task_id in self.running_tasks:
                    if task_id in self.tasks:
                        task = self.tasks[task_id]
                        # 检查任务是否为字典类型
                        if isinstance(task, dict):
                            running_task_states[task_id] = task.copy()
                        else:
                            self.logger.warning(f"任务{task_id}不是字典类型，跳过状态保护: {type(task)}")
            
            # 清空当前任务
            self.tasks.clear()
            self.task_params.clear()
            
            valid_tasks_count = 0
            filtered_tasks_count = 0
            
            # 保存新任务
            for task in tasks:
                # 确保任务有必要的字段
                if 'stock_code' not in task or 'strategy' not in task or 'buy_date' not in task:
                    self.logger.warning(f"任务缺少必要字段，跳过保存: {task}")
                    filtered_tasks_count += 1
                    continue
                
                # 过滤掉无效任务：init_volume为负数的任务（除了夜市任务）
                init_volume = task.get('init_volume', 0)
                strategy = task.get('strategy', '')
                
                # 处理NaN值
                if pd.isna(init_volume):
                    init_volume = 0
                    task['init_volume'] = 0
                else:
                    # 确保是整数
                    init_volume = int(init_volume)
                    task['init_volume'] = init_volume
                
                # 添加调试信息
                #self.logger.info(f"[调试] 检查任务过滤: {task.get('stock_code', '未知')}, init_volume={init_volume}, strategy={strategy}")
                
                # 对于夜市任务，即使init_volume为0也保留（因为可能是买入任务）
                # 对于普通策略任务，只有当init_volume < 0时才过滤掉（允许init_volume为0）
                if init_volume < 0 and not strategy.startswith('夜市') and strategy not in ['夜市卖出', '夜市买入']:
                    self.logger.info(f"过滤无效任务: {task.get('stock_code', '未知')} {task.get('stock_name', '')} - init_volume为{init_volume}")
                    filtered_tasks_count += 1
                    continue
                else:
                    #self.logger.info(f"[调试] 任务通过过滤检查: {task.get('stock_code', '未知')}")
                    pass
                
                # 处理买入日期
                buy_date = task['buy_date']
                if isinstance(buy_date, str):
                    if '_' in buy_date:
                        buy_date = buy_date.split('_')[0]
                    buy_date = datetime.strptime(buy_date, '%Y-%m-%d').date()
                    task['buy_date'] = buy_date
                
                # 生成任务ID
                if 'task_id' in task and task['task_id']:
                    # 如果任务已经有UUID格式的ID，使用它
                    task_id = task['task_id']
                else:
                    # 否则生成新的UUID格式ID
                    task_id = self.generate_task_id()
                    task['task_id'] = task_id
                
                # 使用任务ID作为键保存任务
                self.tasks[task_id] = task
                self.task_params[task_id] = task.get('params', {})
                valid_tasks_count += 1
            
            # 恢复运行中任务的状态
            if running_task_states:
                for task_id, saved_task in running_task_states.items():
                    if task_id in self.tasks:
                        # 检查saved_task是否为字典类型
                        if isinstance(saved_task, dict):
                            # 恢复关键状态字段
                            self.tasks[task_id]['status'] = saved_task.get('status', '运行中')
                            # 恢复其他可能在运行中被修改的字段
                            for key in ['order_id', 'last_signal_time', 'trade_count']:
                                if key in saved_task:
                                    self.tasks[task_id][key] = saved_task[key]
                            #self.logger.info(f"恢复运行中任务状态: {task_id}, 状态: {saved_task.get('status')}")
                        else:
                            self.logger.warning(f"保存的任务{task_id}不是字典类型，跳过状态恢复: {type(saved_task)}")
                    else:
                        # 如果任务在新列表中不存在，重新添加
                        if isinstance(saved_task, dict):
                            self.tasks[task_id] = saved_task
                            self.task_params[task_id] = saved_task.get('params', {})
                            self.logger.info(f"重新添加运行中任务: {task_id}")
                        else:
                            self.logger.warning(f"无法重新添加任务{task_id}，不是字典类型: {type(saved_task)}")
            
            # 记录过滤结果
            if filtered_tasks_count > 0:
                self.logger.info(f"任务保存：过滤了{filtered_tasks_count}个无效任务，保存{valid_tasks_count}个有效任务")
            
            # 只保留持久化列，去掉运行时字段（volume、base_price 等加载时由 init_volume/init_cost 推导）
            rows = []
            for task in self.tasks.values():
                row = {k: task.get(k) for k in PERSIST_TASK_COLUMNS}
                rows.append(row)
            df = pd.DataFrame(rows, columns=PERSIST_TASK_COLUMNS)
            # 处理params字段，确保是有效的JSON字符串
            if 'params' in df.columns:
                df['params'] = df['params'].apply(
                    lambda x: json.dumps(x, default=self._json_default) if isinstance(x, dict) else x
                )
            # 保存到Excel文件
            df.to_excel(self.tasks_file, index=False)
            try:
                self._last_file_mtime = os.path.getmtime(self.tasks_file)
            except OSError:
                pass
            
            # 保存每日任务备份
            self.save_daily_tasks_backup()
            
            # 发送信号通知UI更新（只有在信号未被阻止时才发送）
            if not self._block_tasks_updated_signal:
                self.tasks_updated.emit()
            
            self._sync_rules_armed_if_builtin()
            
            #self.logger.info(f"任务保存成功，共{len(self.tasks)}个任务")
            return True
            
        except Exception as e:
            self.logger.error(f"保存任务失败：{str(e)}")
            
            # 即使保存失败，也要发送信号通知UI更新（只有在信号未被阻止时才发送）
            try:
                if not self._block_tasks_updated_signal:
                    self.tasks_updated.emit()
                    self.logger.info("虽然保存失败，但已发送UI更新信号")
            except Exception as signal_error:
                self.logger.error(f"发送UI更新信号失败：{str(signal_error)}")
            
            return False
    
    def get_task(self, task_id):
        """获取指定任务ID的任务"""
        return self.tasks.get(task_id)
    
    def get_task_params(self, task_id):
        """获取指定任务ID的任务参数"""
        return self.task_params.get(task_id, {})
    
    def update_task_params(self, task_id, new_params):
        """更新任务参数"""
        try:
            if task_id in self.tasks:
                # 获取旧基准价用于比较
                old_base_price = self.tasks[task_id].get('base_price')
                
                # 更新任务参数
                self.tasks[task_id]['params'].update(new_params)
                
                # 如果新参数中包含base_price，也要更新任务的base_price字段
                if 'base_price' in new_params:
                    new_base_price = new_params['base_price']
                    self.tasks[task_id]['base_price'] = new_base_price
                    
                    # 如果基准价发生变化，需要触发UI更新
                    if old_base_price != new_base_price:
                        stock_code = self.tasks[task_id]['stock_code']
                        self.logger.info(f"[{stock_code}] 通过参数对话框更新基准价: {old_base_price} -> {new_base_price}")
                        
                        # 调用update_base_price方法触发UI更新，但不重复保存任务
                        # 使用from_ui=True避免重复的UI更新信号
                        self.update_base_price(stock_code, new_base_price, from_ui=True)
                
                # 如果任务正在运行，通知策略进程更新参数
                stock_code = self.tasks[task_id]['stock_code']
                if stock_code in self.task_processes:
                    # 查找对应的任务
                    for task_info in self.task_processes[stock_code]:
                        if task_info[0] == task_id:  # task_id匹配
                            _, _, control_pipe, _ = task_info
                            control_pipe.send(('update_params', new_params))
                            break
                
                # 保存任务
                self.save_tasks(list(self.tasks.values()))
                return True
            return False
        except Exception as e:
            self.logger.error(f"更新任务参数失败：{str(e)}")
            return False
    
    def set_task_params(self, task_id, new_params):
        """设置任务参数（完全替换）"""
        try:
            if task_id in self.tasks:
                # 完全替换任务参数
                self.tasks[task_id]['params'] = new_params
                self.task_params[task_id] = new_params
                
                # 如果任务正在运行，通知策略进程更新参数
                stock_code = self.tasks[task_id]['stock_code']
                if stock_code in self.task_processes:
                    # 查找对应的任务
                    for task_info in self.task_processes[stock_code]:
                        if task_info[0] == task_id:  # task_id匹配
                            _, _, control_pipe, _ = task_info
                            control_pipe.send(('update_params', new_params))
                            break
                
                return True
            return False
        except Exception as e:
            self.logger.error(f"设置任务参数失败：{str(e)}")
            return False
    
    def delete_task(self, task_id):
        """删除任务"""
        if task_id in self.running_tasks:
            if task_id in self.tasks:
                self.stop_task(task_id)
            else:
                self._force_remove_running_task(task_id)

        stock_code = None
        if task_id in self.tasks:
            task = self.tasks[task_id]
            stock_code = task.get('stock_code')
            del self.tasks[task_id]
        if task_id in self.task_params:
            del self.task_params[task_id]
        
        # 清理price_displays中该任务的显示信息
        if stock_code and stock_code in self.price_displays:
            if isinstance(self.price_displays[stock_code], list):
                # 新格式：任务列表，移除该任务的显示信息
                self.price_displays[stock_code] = [
                    (existing_task_id, task_display) 
                    for existing_task_id, task_display in self.price_displays[stock_code]
                    if existing_task_id != task_id
                ]
                # 如果该股票没有其他任务了，完全移除
                if not self.price_displays[stock_code]:
                    del self.price_displays[stock_code]
            else:
                # 旧格式：直接删除
                del self.price_displays[stock_code]
        
        #self.logger.info(f"删除任务：{task_id}")
    
    def determine_buy_date(self, stock_data, saved_tasks):
        """确定股票的买入日期"""
        stock_code = stock_data['stock_code']
        
        # 如果可用数量为0，说明是今天买入的
        if stock_data.get('can_use_volume', 0) == 0:
            return datetime.now().strftime('%Y-%m-%d')
        
        # 如果可用数量大于0，且不在已保存的任务中，就当作是上一个交易日买入的
        if stock_code not in saved_tasks:
            return self.get_previous_trading_day()
        
        # 如果在已保存的任务中，使用保存的买入日期
        return saved_tasks[stock_code].get('buy_date')

    def get_previous_trading_day(self):
        """获取上一个交易日"""
        current_date = datetime.now()
        previous_date = current_date
        
        while True:
            previous_date = previous_date - timedelta(days=1)
            if is_tradeday(previous_date):
                return previous_date.strftime('%Y-%m-%d')

    def calculate_hold_days(self, buy_date):
        """计算持有天数"""
        try:
            #self.logger.info(f"\n=== 开始计算持有天数 ===")
            #self.logger.info(f"买入日期: {buy_date} (类型: {type(buy_date)})")
            
            # 如果买入日期是字符串，转换为datetime对象
            if isinstance(buy_date, str):
                if '_' in buy_date:
                    buy_date = buy_date.split('_')[0]
                try:
                    buy_date = datetime.strptime(buy_date, '%Y-%m-%d').date()
                    #self.logger.info(f"转换后的买入日期: {buy_date} (类型: {type(buy_date)})")
                except ValueError as e:
                    self.logger.error(f"日期格式转换失败: {str(e)}")
                    return 0
            
            # 获取当前日期
            current_date = datetime.now().date()
            #self.logger.info(f"当前日期: {current_date}")
            
            # 计算持有天数
            hold_days = 0
            current = buy_date + timedelta(days=1)  # 从买入日期的下一天开始计算
            
            while current <= current_date:  # 使用 <= 而不是 <
                if is_tradeday(current):
                    hold_days += 1
                    #self.logger.debug(f"交易日: {current}, 持有天数: {hold_days}")
                current += timedelta(days=1)
            
            #self.logger.info(f"最终持有天数: {hold_days}")
            #self.logger.info("=== 持有天数计算完成 ===\n")
            return hold_days
            
        except Exception as e:
            self.logger.error(f"计算持有天数时出错: {str(e)}", exc_info=True)
            return 0

    def start_task(self, task_id):
        """启动任务"""
        #self.logger.info(f"===== TaskManager.start_task 被调用 =====")
        #self.logger.info(f"请求启动任务ID: {task_id}")
        
        # 首先清理已退出的进程
        self._cleanup_dead_processes()
        
        #self.logger.info(f"启动前运行中任务数量: {len(self.running_tasks)}")
        #self.logger.info(f"启动前运行中任务列表: {list(self.running_tasks.keys())}")
        
        if task_id not in self.tasks:
            self.logger.error(f"任务 {task_id} 不存在")
            return False
            
        task = self.tasks[task_id]
        stock_code = task.get('stock_code', '')
        strategy = task.get('strategy', '')
        
        #self.logger.info(f"任务详情 - 股票: {stock_code}, 策略: {strategy}")
        
        if task_id in self.running_tasks:
            # UI/params 可能已显示「未运行」，但 running_tasks 仍挂着 → 对齐后视为已启动
            if self._reconcile_already_running_task(task_id):
                return True
            # 进程已死或登记残缺：清掉后继续走正常启动
            self.logger.warning(
                f"任务 {task_id} 在 running_tasks 中但进程已失效，清理后重新启动"
            )
            try:
                self._force_remove_running_task(task_id, send_stop=False)
            except Exception as e:
                self.logger.warning(f"清理失效运行记录失败: {e}")
            
        if not self.monitoring:
            self.logger.error("监控未开启，无法启动任务")
            return False
            
        if len(self.running_tasks) >= self.max_tasks:
            self.logger.error(f"已达到最大任务数限制 ({self.max_tasks})")
            return False
            
        # 检查交易时间（仅夜市任务可非交易时间启动）
        if not self._check_trading_time(datetime.now()) and not strategy.startswith('夜市'):
            self.logger.error("非交易时间，无法启动普通任务")
            return False
        
        # 对于规则任务（含历史“万能策略”），检查当前价格是否在阈值范围内
        if self._is_rule_task_strategy(strategy):
            wait_result = self._check_price_within_threshold(task)
            if wait_result is False:
                return False
            elif wait_result == 'wait':
                # 设置任务为等待状态
                task['waiting_for_threshold'] = True
            
        # 检查夜市任务冲突
        '''if strategy.startswith('夜市') or strategy in ['夜市卖出', '夜市买入']:
            self.logger.info(f"检查夜市任务冲突...")
            direction = '买入' if '买入' in strategy else '卖出'
            volume = task.get('params', {}).get('buy_volume' if '买入' in strategy else 'sell_volume', task.get('init_volume', 0))
            price = task.get('base_price', 0)
            
            self.logger.info(f"当前夜市任务 - 股票: {stock_code}, 方向: {direction}, 数量: {volume}, 价格: {price}")
            
            for running_id in self.running_tasks:
                if running_id == task_id:
                    continue
                    
                running_task = self.tasks.get(running_id)
                if not running_task:
                    continue
                    
                running_stock = running_task.get('stock_code')
                running_strategy = running_task.get('strategy', '')
                
                if (running_stock == stock_code and 
                    (running_strategy.startswith('夜市') or running_strategy in ['夜市卖出', '夜市买入'])):
                    
                    running_direction = '买入' if '买入' in running_strategy else '卖出'
                    running_volume = running_task.get('params', {}).get('buy_volume' if '买入' in running_strategy else 'sell_volume', running_task.get('init_volume', 0))
                    running_price = running_task.get('base_price', 0)
                    
                    self.logger.info(f"对比运行中夜市任务 {running_id} - 股票: {running_stock}, 方向: {running_direction}, 数量: {running_volume}, 价格: {running_price}")
                    
                    if (direction == running_direction and 
                        abs(float(volume) - float(running_volume)) < 1 and 
                        abs(float(price) - float(running_price)) < 0.01):
                        self.logger.error(f"夜市任务冲突！已有相同任务 {running_id} 在运行")
                        return False'''
        
        try:
            # 创建并启动任务进程
            #self.logger.info(f"创建任务进程...")
            
            # 检查持仓
            position = self.qmt_adapter.get_stock_position(stock_code)
            # 考虑到支持买入，所以不检查持仓
            #if not position:
            #    self.logger.error(f"未找到股票 {stock_code} 的持仓信息")
            #    return False
            
            # 添加调试信息
            #self.logger.info(f"[{stock_code}] 启动任务时获取到的持仓信息: {position}")
            #if position:
            #    self.logger.info(f"[{stock_code}] 持仓成本价: {position.get('open_price', 0)}, 当前任务基准价: {task.get('base_price', 0)}")
            
            # 更新任务中的持仓信息 - 只更新必要的字段
            if position:
                # 只更新当前可用数量，不修改初始数量
                task['volume'] = position.get('can_use_volume', 0)
            else:
                # 没有持仓时，只设置可用数量为0，不修改其他字段
                task['volume'] = 0
            
            # 对于非夜市任务，如果基准价格为0，优先使用昨收盘价，而不是持仓成本价
            if not strategy.startswith('夜市') and not strategy in ['夜市卖出', '夜市买入']:
                if not task.get('base_price') or task.get('base_price', 0) == 0:
                    # 优先从pre_close_prices获取昨收盘价
                    prev_close_price = self.get_pre_close_price(stock_code)
                    
                    # 如果pre_close_prices中没有，尝试通过key_price_calculator计算
                    if prev_close_price <= 0:
                        try:
                            from key_price_calculator import KeyPriceCalculator
                            calculator = KeyPriceCalculator()
                            key_points = calculator.calculate_key_points(stock_code)
                            # key_points是一个列表，格式为[{'name': '昨收盘', 'price': 9.35, ...}, ...]
                            if key_points:
                                for item in key_points:
                                    if isinstance(item, dict) and item.get('name') == '昨收盘':
                                        prev_close_price = item.get('price', 0)
                                        # 同时更新pre_close_prices，以便后续使用
                                        if prev_close_price > 0:
                                            self.update_pre_close_price(stock_code, prev_close_price)
                                        break
                        except Exception as e:
                            self.logger.warning(f"[{stock_code}] 无法通过key_price_calculator获取昨收盘价: {str(e)}")
                    
                    # 如果成功获取到昨收盘价，使用它作为基准价
                    if prev_close_price > 0:
                        task['base_price'] = prev_close_price
                        self.logger.info(f"[{stock_code}] 使用昨收盘价作为基准价: {prev_close_price:.3f}")
                    elif position:
                        # 只有在无法获取到昨收盘价时，才使用持仓成本价作为备选
                        task['base_price'] = position.get('open_price', 0)
                        self.logger.info(f"[{stock_code}] 无法获取昨收盘价，使用持仓成本价作为基准价: {position.get('open_price', 0):.3f}")
                    else:
                        # 没有持仓且无法获取昨收盘价，记录警告
                        self.logger.warning(f"[{stock_code}] 任务启动时没有持仓信息且无法获取昨收盘价，基准价格为0，可能需要手动设置基准价格")
            
            # 确保task包含params字段
            if 'params' not in task:
                task['params'] = self.task_params.get(task_id, {})
            
            # 处理参数字段
            if 'params' in task:
                # 确保params是字典类型
                if not isinstance(task['params'], dict):
                    task['params'] = {}
                
                # 添加缺失的clear_time参数
                if 'clear_time' not in task['params']:
                    task['params']['clear_time'] = '00:00:00'
                
                # 规则任务兼容补齐：仅保留仍在使用的字段。
                if self._is_rule_task_strategy(task.get('strategy', '')):
                    if 'trade_volume' not in task['params']:
                        task['params']['trade_volume'] = 1000
            else:
                # 如果没有params字段，创建默认参数
                strategy = task.get('strategy', '规则任务')
                task['params'] = self.create_default_params(strategy)
            
            # 创建控制管道
            try:
                parent_conn, child_conn = Pipe()
            except Exception as e:
                self.logger.error(f"创建控制管道失败: {str(e)}")
                raise
            
            # 创建日志管道
            try:
                log_parent_conn, log_child_conn = Pipe()
            except Exception as e:
                self.logger.error(f"创建日志管道失败: {str(e)}")
                # 清理已创建的管道
                try:
                    parent_conn.close()
                    child_conn.close()
                except:
                    pass
                raise
            
            # 启动策略进程
            try:
                if self.use_multiprocessing:
                    process = Process(
                        target=self._run_task_process,
                        args=(stock_code, task, child_conn, log_child_conn)
                    )
                    process.start()
                else:
                    # 使用线程作为fallback
                    self.logger.info(f"使用线程模式运行任务: {stock_code}")
                    process = threading.Thread(
                        target=self._run_task_thread,
                        args=(stock_code, task, child_conn, log_child_conn)
                    )
                    process.daemon = True
                    process.start()
            except Exception as e:
                self.logger.error(f"启动策略进程/线程失败: {str(e)}")
                # 清理已创建的管道
                try:
                    parent_conn.close()
                    child_conn.close()
                    log_parent_conn.close()
                    log_child_conn.close()
                except:
                    pass
                raise
            
            # 关闭子进程端的管道
            try:
                child_conn.close()
                log_child_conn.close()
            except Exception as e:
                self.logger.warning(f"关闭子进程端管道失败: {str(e)}")
            
            # 保存进程信息和任务信息
            task_info = task.copy()
            task_info.update({
                'process': process,
                'control_pipe': parent_conn,
                'log_pipe': log_parent_conn,
                'start_time': datetime.now()
            })
            self.running_tasks[task_id] = task_info
            
            # 支持同一股票的多个任务
            if stock_code not in self.task_processes:
                self.task_processes[stock_code] = []
            self.task_processes[stock_code].append((task_id, process, parent_conn, log_parent_conn))
            
            # 保存原始状态，用于停止时恢复
            original_status = task.get('status', '未运行')
            task['original_status'] = original_status
            
            # 更新任务状态为运行中
            task['status'] = '运行中'
            params = task.get('params') if isinstance(task.get('params'), dict) else {}
            params['task_running'] = True
            params['task_paused'] = False
            task['params'] = params
            self.tasks[task_id]['params'] = params
            self.task_params[task_id] = params
            
            # 先发送UI更新信号，立即显示运行状态
            self.update_task_ui.emit(task_id, 'status', '运行中')
            
            # 启动监控线程
            self._start_monitor_threads(task_id, parent_conn, log_parent_conn)
            
            # 记录任务启动
            #self.logger.info(f"[{stock_code}] 任务已启动，策略：{task['strategy']}")
            #self.logger.info(f"启动后运行中任务数量: {len(self.running_tasks)}")
            #self.logger.info(f"启动后运行中任务列表: {list(self.running_tasks.keys())}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"启动任务失败：{str(e)}")
            return False

    def _mark_task_stopped_params(self, task_id, *, paused=True, status=None):
        """停止后统一写回 params/status，并尽量同步 rules_armed。"""
        task = self.tasks.get(task_id)
        if not isinstance(task, dict):
            return
        stock_code = task.get("stock_code", "")
        params = task.get("params") if isinstance(task.get("params"), dict) else {}
        params["task_running"] = False
        params["task_paused"] = bool(paused)
        task["params"] = params
        self.tasks[task_id]["params"] = params
        self.task_params[task_id] = params
        if status is not None:
            task["status"] = status
            self.tasks[task_id]["status"] = status
            try:
                self.update_task_ui.emit(task_id, "status", status)
            except Exception:
                pass
        elif stock_code:
            try:
                self.update_task_ui.emit(task_id, "status", task.get("status") or "未运行")
            except Exception:
                pass
        try:
            self._sync_rules_armed_if_builtin()
        except Exception:
            pass

    def stop_task(self, task_id):
        """停止指定任务。

        进程缺失/已死时也必须清掉 running_tasks，避免「停不掉 / 退不出」。
        不在 running_tasks 但 params/status 仍显示运行中时，做幂等对齐。
        """
        try:
            if task_id in self.running_tasks:
                task = self.tasks.get(task_id)
                stock_code = None
                if isinstance(task, dict):
                    stock_code = task.get("stock_code")
                    current_status = task.get("status")
                    if current_status != "已委托":
                        original_status = task.get("original_status", "未运行")
                        has_order = (
                            (
                                str(task.get("strategy", "")).startswith("夜市")
                                or task.get("strategy", "") in ["夜市卖出", "夜市买入"]
                            )
                            and task.get("order_id")
                        )
                        if has_order and original_status == "已委托":
                            new_status = "已委托"
                            self.logger.info(
                                f"[{stock_code}] 停止原本已委托的任务，保持已委托状态，"
                                f"委托号：{task.get('order_id')}"
                            )
                        elif has_order:
                            new_status = original_status
                        else:
                            new_status = original_status
                        self._mark_task_stopped_params(
                            task_id, paused=True, status=new_status
                        )
                        try:
                            self.save_tasks(list(self.tasks.values()))
                        except Exception as e:
                            self.logger.warning(f"停止任务后保存失败: {e}")
                    else:
                        try:
                            self.save_tasks(list(self.tasks.values()))
                        except Exception as e:
                            self.logger.warning(f"停止任务后保存失败: {e}")

                # 无论 process/pipe 是否完整，都强制清掉运行登记
                try:
                    self._force_remove_running_task(task_id, send_stop=True)
                except Exception as e:
                    self.logger.warning(f"强制移除运行记录失败，尝试直接删除: {e}")
                    try:
                        if task_id in self.running_tasks:
                            del self.running_tasks[task_id]
                    except Exception:
                        pass
                    if stock_code and stock_code in self.task_processes:
                        self.task_processes[stock_code] = [
                            item
                            for item in self.task_processes[stock_code]
                            if item[0] != task_id
                        ]
                        if not self.task_processes[stock_code]:
                            del self.task_processes[stock_code]

                # price_displays 清理（_force_remove 未必处理）
                if stock_code and stock_code in self.price_displays:
                    if isinstance(self.price_displays[stock_code], list):
                        self.price_displays[stock_code] = [
                            (existing_task_id, task_display)
                            for existing_task_id, task_display in self.price_displays[
                                stock_code
                            ]
                            if existing_task_id != task_id
                        ]
                        if not self.price_displays[stock_code]:
                            del self.price_displays[stock_code]
                    else:
                        current_display = self.price_displays[stock_code]
                        if "[" in current_display and "]" in current_display:
                            self.price_displays[stock_code] = current_display.split("[")[
                                0
                            ].strip()

                try:
                    self._sync_rules_armed_if_builtin()
                except Exception:
                    pass
                return True

            if task_id in self.tasks:
                # 不在 running_tasks：幂等对齐「看起来像在跑」的状态
                task = self.tasks[task_id]
                stock_code = task.get("stock_code", "")
                current_status = task.get("status", "")
                params = task.get("params") if isinstance(task.get("params"), dict) else {}
                looks_running = (
                    current_status in ("运行中", "已委托", "可能已委托")
                    or bool(params.get("task_running"))
                )

                if current_status in ("已委托", "可能已委托") or looks_running:
                    self._mark_task_stopped_params(
                        task_id, paused=False, status="未运行"
                    )
                    self._block_tasks_updated_signal = True
                    try:
                        self.save_tasks(list(self.tasks.values()))
                    finally:
                        self._block_tasks_updated_signal = False
                    self.logger.info(
                        f"[{stock_code}] 任务未在 running_tasks，已对齐为未运行（幂等停止）"
                    )
                    return True

                self.logger.warning(
                    f"任务 {task_id} 状态为 {current_status}，无需停止"
                )
                return True

            self.logger.warning(f"任务 {task_id} 不存在")
            return False

        except Exception as e:
            self.logger.error(f"停止任务 {task_id} 失败：{str(e)}")
            import traceback

            self.logger.error(f"停止任务异常堆栈：{traceback.format_exc()}")
            # 失败也尽量清掉脏登记，避免永久卡死退出
            try:
                if task_id in self.running_tasks:
                    self._force_remove_running_task(task_id, send_stop=False)
            except Exception:
                self.running_tasks.pop(task_id, None)
            try:
                self._mark_task_stopped_params(task_id, paused=True, status="未运行")
            except Exception:
                pass
            return True

    @staticmethod
    def _run_task_process(stock_code, task_info, control_pipe, log_pipe):
        """运行任务进程"""
        try:
            # 创建策略实例
            strategy = None
            if TaskManager._is_rule_task_strategy(task_info.get('strategy', '')):
                from strategies.moderate_strategy import ModerateStrategy
                strategy = ModerateStrategy(task_info, log_pipe, control_pipe)
            elif task_info['strategy'] in ['夜市卖出', '夜市买入'] or task_info['strategy'].startswith('夜市'):
                from strategies.night_market_strategy import NightMarketStrategy
                strategy = NightMarketStrategy(task_info, log_pipe, control_pipe)
            else:
                print(f"[{stock_code}] 未找到策略 {task_info['strategy']}")
                return
            
            if strategy:
                # 运行策略
                strategy.run()
        except Exception as e:
            print(f"[{stock_code}] 任务进程错误：{str(e)}")
        finally:
            try:
                if control_pipe:
                    control_pipe.close()
                if log_pipe:
                    log_pipe.close()
            except:
                pass

    def _run_task_thread(self, stock_code, task_info, control_pipe, log_pipe):
        """运行任务线程（multiprocessing的fallback）"""
        try:
            # 创建策略实例
            strategy = None
            if TaskManager._is_rule_task_strategy(task_info.get('strategy', '')):
                from strategies.moderate_strategy import ModerateStrategy
                strategy = ModerateStrategy(task_info, log_pipe, control_pipe)
            elif task_info['strategy'] in ['夜市卖出', '夜市买入'] or task_info['strategy'].startswith('夜市'):
                from strategies.night_market_strategy import NightMarketStrategy
                strategy = NightMarketStrategy(task_info, log_pipe, control_pipe)
            else:
                print(f"[{stock_code}] 未找到策略 {task_info['strategy']}")
                return
            
            if strategy:
                # 运行策略
                strategy.run()
        except Exception as e:
            print(f"[{stock_code}] 任务线程错误：{str(e)}")
        finally:
            try:
                if control_pipe:
                    control_pipe.close()
                if log_pipe:
                    log_pipe.close()
            except:
                pass

    def _start_monitor_threads(self, task_id, control_pipe, log_pipe):
        """启动监控线程"""
        # 启动日志监听线程
        log_thread = Thread(
            target=self._monitor_logs,
            args=(task_id, log_pipe),
            daemon=True
        )
        log_thread.start()
        
        # 启动交易信号监听线程
        trade_thread = Thread(
            target=self._monitor_trade_signals,
            args=(task_id, control_pipe),
            daemon=True
        )
        trade_thread.start()

    def _monitor_logs(self, task_id, log_conn):
        """监听并处理子进程的日志"""
        while task_id in self.running_tasks:
            try:
                if log_conn.poll(1):
                    try:
                        msg = log_conn.recv()
                        # 调试：记录消息类型和内容
                        # self.logger.debug(f"任务 {task_id} 收到消息: 类型={type(msg)}, 内容={msg}")
                        if isinstance(msg, tuple):
                            if msg[0] == 'update_base_price':
                                # 更新基准价格，标记为来自策略进程，只更新当前任务
                                if isinstance(msg[1], dict):
                                    stock_code = msg[1]['stock_code']
                                    new_price = msg[1]['base_price']
                                    old_price = self.tasks.get(task_id, {}).get('base_price')
                                    self.logger.info(f"[任务管理器] 收到策略进程基准价更新消息: {stock_code} 从 {old_price} 更新为 {new_price}")
                                    #self.logger.info(f"[任务管理器] 调试：准备调用update_base_price方法")
                                    self.update_base_price(stock_code, new_price, from_strategy=True, target_task_id=task_id)
                                    #self.logger.info(f"[任务管理器] 调试：已调用update_base_price方法")
                                else:
                                    stock_code = self.tasks[task_id]['stock_code']
                                    new_price = msg[1]
                                    old_price = self.tasks.get(task_id, {}).get('base_price')
                                    self.logger.info(f"[任务管理器] 收到策略进程基准价更新消息: {stock_code} 从 {old_price} 更新为 {new_price}")
                                    #self.logger.info(f"[任务管理器] 调试：准备调用update_base_price方法")
                                    self.update_base_price(stock_code, new_price, from_strategy=True, target_task_id=task_id)
                                    #self.logger.info(f"[任务管理器] 调试：已调用update_base_price方法")
                            elif msg[0] == 'update_thresholds':
                                # 更新阈值信息给状态栏
                                #self.logger.info(f"[任务管理器] 收到策略进程阈值更新消息: {msg}")
                                threshold_data = msg[1]
                                stock_code = threshold_data['stock_code']
                                current_price = threshold_data['current_price']
                                up_threshold = threshold_data['up_threshold']
                                down_threshold = threshold_data['down_threshold']
                                
                                # 更新状态栏显示 - 支持多个任务
                                precision = SecurityTypeUtil.get_price_precision(stock_code)
                                display_text = f"{current_price:.{precision}f} [{up_threshold:.{precision}f}/{down_threshold:.{precision}f}]"
                                
                                # 初始化该股票的任务显示列表
                                if stock_code not in self.price_displays:
                                    self.price_displays[stock_code] = []
                                elif not isinstance(self.price_displays[stock_code], list):
                                    # 如果是旧格式（字符串），转换为新格式
                                    old_display = self.price_displays[stock_code]
                                    self.price_displays[stock_code] = []
                                
                                # 查找是否已存在该任务的显示信息
                                task_found = False
                                for i, item in enumerate(self.price_displays[stock_code]):
                                    # 检查是否是元组格式
                                    if isinstance(item, tuple) and len(item) == 2:
                                        existing_task_id, task_display = item
                                        if existing_task_id == task_id:
                                            # 更新现有任务的显示信息
                                            self.price_displays[stock_code][i] = (task_id, display_text)
                                            task_found = True
                                            break
                                    else:
                                        # 旧格式，直接替换
                                        self.price_displays[stock_code][i] = (task_id, display_text)
                                        task_found = True
                                        break
                                
                                if not task_found:
                                    # 添加新任务的显示信息
                                    self.price_displays[stock_code].append((task_id, display_text))
                                
                                #self.logger.info(f"[{stock_code}] 策略进程更新状态栏阈值: {display_text}")
                                
                                # 通知主窗口更新状态栏
                                if self.main_window:
                                    self.main_window.update_status_bar()
                            elif msg[0] == 'update_waiting_state':
                                # 处理策略进程发送的等待状态更新消息
                                waiting_data = msg[1]
                                stock_code = waiting_data.get('stock_code', '')
                                waiting_state = waiting_data.get('waiting_state', {})
                                
                                # 更新任务信息中的等待状态
                                if task_id in self.tasks:
                                    self.tasks[task_id].update(waiting_state)
                                    self.logger.info(f"[{stock_code}] 更新等待状态: {waiting_state}")
                            elif msg[0] == 'update_task_status':
                                # 处理策略进程发送的任务状态更新消息
                                status_data = msg[1]
                                stock_code = status_data.get('stock_code', '')
                                new_status = status_data.get('status', '')
                                reason = status_data.get('reason', '')
                                
                                if stock_code and new_status:
                                    # 查找对应的任务ID - 只更新运行中的任务
                                    target_task_id = None
                                    for tid, task in self.tasks.items():
                                        if (task.get('stock_code') == stock_code and 
                                            task.get('status') in ['运行中', '已委托']):
                                            target_task_id = tid
                                            break
                                    
                                    if target_task_id:
                                        # 更新任务状态
                                        self.tasks[target_task_id]['status'] = new_status
                                        self.logger.info(f"[{stock_code}] 策略进程通知更新任务{target_task_id}状态为: {new_status}, 原因: {reason}")
                                        
                                        # 保存任务状态到文件
                                        self.save_tasks(list(self.tasks.values()))
                                        
                                        # 发送UI更新信号
                                        self.update_task_ui.emit(target_task_id, 'status', new_status)
                                        
                                        # 如果状态是"已完成"，清理运行状态
                                        if new_status == '已完成':
                                            if target_task_id in self.running_tasks:
                                                self.logger.info(f"[{stock_code}] 任务已完成，清理运行状态")
                                                # 关闭管道 - 添加错误处理
                                                try:
                                                    task_info = self.running_tasks[target_task_id]
                                                    if 'control_pipe' in task_info:
                                                        control_pipe = task_info['control_pipe']
                                                        if hasattr(control_pipe, 'close') and not control_pipe.closed:
                                                            control_pipe.close()
                                                except (OSError, ValueError, AttributeError) as e:
                                                    # 管道可能已经关闭或无效，这是正常的
                                                    pass
                                                    
                                                try:
                                                    if 'log_pipe' in task_info:
                                                        log_pipe = task_info['log_pipe']
                                                        if hasattr(log_pipe, 'close') and not log_pipe.closed:
                                                            log_pipe.close()
                                                except (OSError, ValueError, AttributeError) as e:
                                                    # 管道可能已经关闭或无效，这是正常的
                                                    pass
                                                
                                                # 从运行中任务移除
                                                del self.running_tasks[target_task_id]
                                                
                                                # 清理task_processes中的对应条目
                                                if stock_code and stock_code in self.task_processes:
                                                    # 移除特定的任务，而不是整个股票的所有任务
                                                    self.task_processes[stock_code] = [
                                                        task_info for task_info in self.task_processes[stock_code] 
                                                        if task_info[0] != target_task_id
                                                    ]
                                                    # 如果该股票没有其他任务了，完全移除
                                                    if not self.task_processes[stock_code]:
                                                        del self.task_processes[stock_code]
                                                        self.logger.info(f"[{stock_code}] 已完成任务已从task_processes中移除，停止接收行情数据")
                                                    else:
                                                        self.logger.info(f"[{stock_code}] 任务 {target_task_id} 已完成已从task_processes中移除，该股票还有其他任务运行")
                                                
                                                # 清理price_displays中的阈值信息
                                                if stock_code and stock_code in self.price_displays:
                                                    # 移除特定任务的显示信息
                                                    if isinstance(self.price_displays[stock_code], list):
                                                        # 新格式：任务列表
                                                        self.price_displays[stock_code] = [
                                                            (existing_task_id, task_display) 
                                                            for existing_task_id, task_display in self.price_displays[stock_code]
                                                            if existing_task_id != target_task_id
                                                        ]
                                                        # 如果该股票没有其他任务了，完全移除
                                                        if not self.price_displays[stock_code]:
                                                            del self.price_displays[stock_code]
                                                            self.logger.info(f"[{stock_code}] 任务已完成，状态栏显示已为纯价格格式")
                                                        else:
                                                            self.logger.info(f"[{stock_code}] 任务 {target_task_id} 已完成，该股票还有其他任务运行")
                                                    else:
                                                        # 旧格式：直接字符串，转换为新格式
                                                        current_display = self.price_displays[stock_code]
                                                        if '[' in current_display and ']' in current_display:
                                                            price_part = current_display.split('[')[0].strip()
                                                            self.price_displays[stock_code] = price_part
                                                        self.logger.info(f"[{stock_code}] 任务已完成，清理阈值显示，保留价格: {price_part}")
                                    else:
                                        self.logger.warning(f"[{stock_code}] 收到状态更新消息但未找到对应任务")
                                else:
                                    self.logger.warning(f"收到无效的任务状态更新消息: {msg}")
                            else:
                                self.logger.info(msg)
                        else:
                            # 处理字符串格式的日志消息（子进程经 log_pipe 发来）
                            # 行情 tick 逐条刷屏：降为 DEBUG，默认 INFO 级别下不再输出
                            if isinstance(msg, str) and "处理tick" in msg:
                                self.logger.debug(msg)
                            else:
                                self.logger.info(msg)
                    except (EOFError, OSError, BrokenPipeError) as pipe_error:
                        # 管道已关闭，检查进程状态
                        self.logger.warning(f"任务 {task_id} 日志管道已关闭: {str(pipe_error)}")
                        self._handle_process_disconnection(task_id)
                        break
                    except Exception as e:
                        self.logger.error(f"任务 {task_id} 处理日志消息出错: {str(e)}")
                        break
                else:
                    # 检查进程是否仍然存活
                    if task_id in self.running_tasks:
                        process = self.running_tasks[task_id].get('process')
                        if process and not process.is_alive():
                            self.logger.warning(f"任务 {task_id} 进程已退出，清理运行状态")
                            self._handle_process_disconnection(task_id)
                            break
            except (EOFError, OSError, BrokenPipeError) as pipe_error:
                #self.logger.warning(f"任务 {task_id} 日志监听管道错误: {str(pipe_error)}")
                self._handle_process_disconnection(task_id)
                break
            except Exception as e:
                self.logger.error(f"任务 {task_id} 日志监听错误：{str(e)}")
                break
        
        #self.logger.info(f"任务 {task_id} 日志监听线程结束")

    def _handle_normal_task_disconnection(self, task, stock_code):
        """处理普通任务的进程断开"""
        strategy = task.get('strategy', '')
        current_status = task.get('status', '')
        
        # 对于夜市任务，如果进程断开，保持当前状态，等待订单列表更新
        if '夜市' in strategy or strategy in ['夜市卖出', '夜市买入']:
            # 夜市任务：保持当前状态，不强制修改
            self.logger.info(f"[{stock_code}] 夜市任务进程断开，保持当前状态：{current_status}")
        else:
            # 普通策略：没有委托号的任务设为"未运行"
            if not task.get('order_id'):
                task['status'] = '未运行'
                self.logger.info(f"[{stock_code}] 普通策略无委托号，状态设为未运行")
            else:
                # 有委托号的任务，检查是否有订单信息
                if task.get('orders'):
                    task['status'] = '连接断开'  # 新增状态，表示连接断开但可能有订单
                    self.logger.info(f"[{stock_code}] 普通策略有订单记录，状态设为连接断开")
                else:
                    task['status'] = '未运行'
                    self.logger.info(f"[{stock_code}] 普通策略无订单记录，状态设为未运行")

    def _handle_process_disconnection(self, task_id):
        """处理进程断开连接"""
        if task_id not in self.running_tasks:
            return
            
        try:
            task_info = self.running_tasks[task_id]
            process = task_info.get('process')
            stock_code = self.tasks[task_id]['stock_code'] if task_id in self.tasks else '未知'
            
            # 检查进程状态
            if process and process.is_alive():
                #self.logger.info(f"[{stock_code}] 任务 {task_id} 进程仍在运行，保持状态")
                return
            
            # 进程已退出，清理运行状态
            self.logger.warning(f"[{stock_code}] 任务 {task_id} 进程已退出，清理运行状态")
            
            # 关闭管道 - 添加错误处理
            try:
                if 'control_pipe' in task_info:
                    control_pipe = task_info['control_pipe']
                    if hasattr(control_pipe, 'close') and not control_pipe.closed:
                        control_pipe.close()
            except (OSError, ValueError, AttributeError) as e:
                # 管道可能已经关闭或无效，这是正常的
                pass
                
            try:
                if 'log_pipe' in task_info:
                    log_pipe = task_info['log_pipe']
                    if hasattr(log_pipe, 'close') and not log_pipe.closed:
                        log_pipe.close()
            except (OSError, ValueError, AttributeError) as e:
                # 管道可能已经关闭或无效，这是正常的
                pass
            
            # 从运行中任务移除
            del self.running_tasks[task_id]
            
            # 清理task_processes中的对应条目
            if stock_code and stock_code in self.task_processes:
                # 移除特定的任务，而不是整个股票的所有任务
                self.task_processes[stock_code] = [
                    task_info for task_info in self.task_processes[stock_code] 
                    if task_info[0] != task_id
                ]
                # 如果该股票没有其他任务了，完全移除
                if not self.task_processes[stock_code]:
                    del self.task_processes[stock_code]
                    self.logger.info(f"[{stock_code}] 进程断开已从task_processes中移除，停止接收行情数据")
                else:
                    self.logger.info(f"[{stock_code}] 任务 {task_id} 进程断开已从task_processes中移除，该股票还有其他任务运行")
            
            # 清理price_displays中的阈值信息
            if stock_code and stock_code in self.price_displays:
                # 移除特定任务的显示信息
                if isinstance(self.price_displays[stock_code], list):
                    # 新格式：任务列表
                    self.price_displays[stock_code] = [
                        (existing_task_id, task_display) 
                        for existing_task_id, task_display in self.price_displays[stock_code]
                        if existing_task_id != task_id
                    ]
                    # 如果该股票没有其他任务了，完全移除
                    if not self.price_displays[stock_code]:
                        del self.price_displays[stock_code]
                        self.logger.info(f"[{stock_code}] 进程断开，状态栏显示已为纯价格格式")
                    else:
                        self.logger.info(f"[{stock_code}] 任务 {task_id} 进程断开，该股票还有其他任务运行")
                else:
                    # 旧格式：直接字符串，转换为新格式
                    current_display = self.price_displays[stock_code]
                    if '[' in current_display and ']' in current_display:
                        price_part = current_display.split('[')[0].strip()
                        self.price_displays[stock_code] = price_part
                    self.logger.info(f"[{stock_code}] 进程断开，清理阈值显示，保留价格: {price_part}")
            
            # 更新任务状态
            if task_id in self.tasks:
                task = self.tasks[task_id]
                strategy = task.get('strategy', '')
                current_status = task.get('status', '')
                
                # 如果任务状态已经是"已完成"，说明策略进程已经正确通知了状态更新，保持该状态
                if current_status == '已完成':
                    self.logger.info(f"[{stock_code}] 任务状态已经是已完成，保持该状态")
                else:
                    # 检查是否是规则任务且循环次数为0的情况
                    if self._is_rule_task_strategy(strategy):
                        params = task.get('params', {})
                        cycle_times = params.get('cycle_times', 0)
                        if cycle_times == 0:
                            # 规则任务循环次数为0，进程退出说明任务已完成
                            task['status'] = '已完成'
                            self.logger.info(f"[{stock_code}] 规则任务循环次数为0，进程退出，标记为已完成")
                        else:
                            # 其他情况按原逻辑处理
                            self._handle_normal_task_disconnection(task, stock_code)
                    else:
                        # 其他策略按原逻辑处理
                        self._handle_normal_task_disconnection(task, stock_code)
                
                # 保存任务状态
                self.save_tasks(list(self.tasks.values()))
                
                # 发送UI更新信号
                self.update_task_ui.emit(task_id, 'status', task['status'])
                
                self.logger.info(f"[{stock_code}] 任务状态已更新为: {task['status']}")
            
        except Exception as e:
            self.logger.error(f"处理任务 {task_id} 进程断开失败: {str(e)}")

    def update_latest_price(self, stock_code, price):
        """更新实时价格"""
        if not hasattr(self, 'latest_prices'):
            self.latest_prices = {}
        self.latest_prices[stock_code] = price
        self.logger.debug(f"[价格管理] 更新 {stock_code} 实时价格: {price}")

    def get_latest_price(self, stock_code):
        """获取实时价格"""
        if not hasattr(self, 'latest_prices'):
            self.latest_prices = {}
        return self.latest_prices.get(stock_code, 0)
    
    def _check_price_within_threshold(self, task):
        """检查当前价格是否在阈值范围内（规则任务专用）"""
        try:
            stock_code = task.get('stock_code', '')
            base_price = task.get('base_price', 0)
            params = task.get('params', {})
            # 阈值延迟启动检查默认关闭：仅在任务显式声明 enable_threshold_check=True 时启用。
            # 这样可彻底避免历史 up/down 阈值残留导致的误弹窗。
            if not (isinstance(params, dict) and bool(params.get('enable_threshold_check', False))):
                return True
            
            if base_price <= 0:
                self.logger.warning(f"[{stock_code}] 基准价格为0，跳过价格检查")
                return True
            
            # 获取当前价格
            current_price = self.get_latest_price(stock_code)
            if current_price <= 0:
                self.logger.warning(f"[{stock_code}] 无法获取当前价格，跳过价格检查")
                return True
            
            # 获取阈值参数
            # 新规则任务已不再生成 up/down 阈值参数；
            # 若任务本身未携带这两个字段，则直接跳过“延迟启动确认”。
            has_up = isinstance(params, dict) and ('up_threshold' in params)
            has_down = isinstance(params, dict) and ('down_threshold' in params)
            if not has_up and not has_down:
                return True
            up_threshold = float(params.get('up_threshold', 3.0)) / 100
            down_threshold = float(params.get('down_threshold', 3.5)) / 100
            
            # 计算阈值价格
            from core.utils.security_type import SecurityTypeUtil
            price_precision = SecurityTypeUtil.get_price_precision(stock_code)
            
            if up_threshold == 0.0:
                up_threshold_price = round(base_price, price_precision)
            else:
                up_threshold_price = round(base_price * (1 + up_threshold), price_precision)
            
            if down_threshold == 0.0:
                down_threshold_price = round(base_price, price_precision)
            else:
                down_threshold_price = round(base_price * (1 - down_threshold), price_precision)
            
            # 检查价格是否在阈值范围内
            if current_price > up_threshold_price or current_price < down_threshold_price:
                # 价格超出阈值范围，显示确认对话框
                dialog_result = self._show_delayed_start_dialog(stock_code, current_price, base_price, 
                                                              up_threshold_price, down_threshold_price, 
                                                              up_threshold * 100, down_threshold * 100)
                if dialog_result:
                    return 'wait'  # 用户选择等待
                else:
                    return False   # 用户取消启动
            else:
                # 价格在阈值范围内，正常启动
                self.logger.info(f"[{stock_code}] 当前价格 {current_price:.{price_precision}f} 在阈值范围内 [{down_threshold_price:.{price_precision}f}, {up_threshold_price:.{price_precision}f}]，正常启动")
                return True
                
        except Exception as e:
            self.logger.error(f"检查价格阈值失败: {str(e)}")
            return True  # 出错时允许启动
    
    def _show_delayed_start_dialog(self, stock_code, current_price, base_price, 
                                 up_threshold_price, down_threshold_price, 
                                 up_threshold_percent, down_threshold_percent):
        """显示延迟启动确认对话框"""
        try:
            from PyQt5.QtWidgets import QMessageBox, QApplication
            from core.utils.security_type import SecurityTypeUtil
            
            price_precision = SecurityTypeUtil.get_price_precision(stock_code)
            
            # 确定价格超出情况
            if current_price > up_threshold_price:
                price_status = f"当前价格 {current_price:.{price_precision}f} 高于上涨阈值 {up_threshold_price:.{price_precision}f}"
                trigger_condition = f"当价格回到 {up_threshold_price:.{price_precision}f} 以下时开始监控"
            else:
                price_status = f"当前价格 {current_price:.{price_precision}f} 低于下跌阈值 {down_threshold_price:.{price_precision}f}"
                trigger_condition = f"当价格回到 {down_threshold_price:.{price_precision}f} 以上时开始监控"
            
            # 构建消息内容
            message = f"""
股票代码: {stock_code}
基准价格: {base_price:.{price_precision}f}
{price_status}

阈值范围: [{down_threshold_price:.{price_precision}f}, {up_threshold_price:.{price_precision}f}]
上涨阈值: {up_threshold_percent:.1f}%
下跌阈值: {down_threshold_percent:.1f}%

{trigger_condition}

确认将等待价格回到阈值范围内再开始监控吗？
"""
            
            # 显示确认对话框
            msg_box = QMessageBox()
            msg_box.setWindowTitle("规则任务延迟启动确认")
            msg_box.setText("当前价格超出阈值范围")
            msg_box.setInformativeText(message)
            msg_box.setIcon(QMessageBox.Warning)
            
            # 添加按钮
            wait_button = msg_box.addButton("等待价格回到阈值范围", QMessageBox.AcceptRole)
            cancel_button = msg_box.addButton("取消启动", QMessageBox.RejectRole)
            
            # 设置默认按钮
            msg_box.setDefaultButton(wait_button)
            
            # 显示对话框
            result = msg_box.exec_()
            
            if msg_box.clickedButton() == wait_button:
                self.logger.info(f"[{stock_code}] 用户选择等待价格回到阈值范围内再开始监控")
                return True
            else:
                self.logger.info(f"[{stock_code}] 用户取消启动任务")
                return False
                
        except Exception as e:
            self.logger.error(f"显示延迟启动对话框失败: {str(e)}")
            return True  # 出错时允许启动

    def update_pre_close_price(self, stock_code, price):
        """更新昨收盘价"""
        if not hasattr(self, 'pre_close_prices'):
            self.pre_close_prices = {}
        self.pre_close_prices[stock_code] = price
        self.logger.debug(f"[价格管理] 更新 {stock_code} 昨收盘价: {price}")

    def get_pre_close_price(self, stock_code):
        """获取会话基准昨收（盘后自动切到今日收盘作次日基准）。"""
        if not hasattr(self, 'pre_close_prices'):
            self.pre_close_prices = {}
        stored = float(self.pre_close_prices.get(stock_code, 0) or 0)
        last_px = 0.0
        try:
            last_px = float((getattr(self, "latest_prices", {}) or {}).get(stock_code, 0) or 0)
        except Exception:
            last_px = 0.0
        try:
            from utils.session_prev_close import resolve_session_prev_close

            resolved = resolve_session_prev_close(
                stock_code,
                qmt_last_close=stored,
                last_price=last_px,
            )
            if resolved > 0 and abs(resolved - stored) > 1e-9:
                self.pre_close_prices[stock_code] = resolved
                self.logger.debug(
                    f"[价格管理] 会话昨收修正 {stock_code}: {stored} -> {resolved}"
                )
            return resolved if resolved > 0 else stored
        except Exception:
            return stored

    def get_price_info(self, stock_code):
        """获取价格信息"""
        latest_price = self.get_latest_price(stock_code)
        pre_close_price = self.get_pre_close_price(stock_code)
        return {
            'latest_price': latest_price,
            'pre_close_price': pre_close_price
        }

    def update_base_price(self, stock_code, new_price, from_ui=False, from_strategy=False, target_task_id=None):
        """更新基准价格
        Args:
            stock_code: 股票代码
            new_price: 新的基准价格
            from_ui: 是否来自UI的更新
            from_strategy: 是否来自策略进程的更新
            target_task_id: 目标任务ID，如果指定则只更新该任务，否则更新该股票的所有任务
        """
        # 查找该股票的所有任务
        stock_tasks = []
        for task_id, task in self.tasks.items():
            if task.get('stock_code') == stock_code:
                stock_tasks.append((task_id, task))
        
        if not stock_tasks:
            self.logger.warning(f"[{stock_code}] 未找到该股票的任务，无法更新基准价格")
            return
        
        # 如果指定了目标任务ID，只更新该任务
        if target_task_id:
            target_task = None
            for task_id, task in stock_tasks:
                if task_id == target_task_id:
                    target_task = (task_id, task)
                    break
            
            if not target_task:
                self.logger.warning(f"[{stock_code}] 未找到指定的任务ID {target_task_id}，无法更新基准价格")
                return
            
            # 检查价格是否有变化
            old_price = target_task[1].get('base_price')
            if old_price is not None and abs(old_price - new_price) < 0.001:  # 允许小误差
                self.logger.info(f"[{stock_code}] 任务{target_task_id}基准价格无变化: {old_price:.3f} -> {new_price:.3f}")
                return
            
            # 更新指定任务的基准价格
            target_task[1]['base_price'] = new_price
            precision = 3 if SecurityTypeUtil.is_fund(stock_code) else 2
            self.logger.info(f"[{stock_code}] 任务{target_task_id}基准价从{old_price:.{precision}f}更新为{new_price:.{precision}f}")
            
            # 发出信号通知UI更新指定任务，包含价格变化信息
            price_info = {
                'new_price': new_price,
                'old_price': old_price if old_price is not None else new_price,
                'price_change': new_price - (old_price if old_price is not None else new_price)
            }
            self.logger.info(f"[{stock_code}] 发送UI更新信号: task_id={target_task_id}, price_info={price_info}")
            self.update_task_ui.emit(target_task_id, 'base_price', price_info)
            return
        
        # 如果没有指定目标任务ID，保持原有逻辑（更新所有任务）
        # 获取第一个任务的基准价格作为参考（用于检查是否有变化）
        first_task = stock_tasks[0][1]
        base_price = first_task.get('base_price')
        
        # 如果价格没有变化，直接返回
        if base_price is not None and abs(base_price - new_price) < 0.001:  # 允许小误差
            self.logger.info(f"[{stock_code}] 基准价格无变化: {base_price:.3f} -> {new_price:.3f}")
            return
        
        # 根据股票代码确定小数点位数
        precision = 3 if SecurityTypeUtil.is_fund(stock_code) else 2
        self.logger.info(f"[{stock_code}] 基准价从{base_price:.{precision}f}更新为{new_price:.{precision}f}")
        
        # 更新所有相关任务的基准价格
        for task_id, task in stock_tasks:
            task['base_price'] = new_price
            self.logger.info(f"[{stock_code}] 任务{task_id}的基准价已更新为: {new_price:.{precision}f}")
        
        # 通知子进程更新基准价（如果不是来自策略进程的更新）
        if not from_strategy:
            # 查找该股票对应的运行中任务
            for task_id in self.running_tasks:
                if self.tasks[task_id]['stock_code'] == stock_code:
                    try:
                        control_pipe = self.running_tasks[task_id]['control_pipe']
                        control_pipe.send(('update_base_price', new_price))
                        self.logger.info(f"[{stock_code}] 已发送基准价格更新消息到策略进程")
                    except Exception as e:
                        self.logger.error(f"[{stock_code}] 发送更新基准价消息失败：{str(e)}")
                    break
        
        # 发出信号通知UI更新（无论是否来自UI，都需要更新UI显示）
        # 为所有相关的任务发送UI更新信号，包含新旧价格信息用于颜色显示
        updated_count = 0
        for task_id, task in stock_tasks:
            # 传递包含新旧价格信息的字典
            price_info = {
                'new_price': new_price,
                'old_price': base_price if base_price is not None else new_price,
                'price_change': new_price - (base_price if base_price is not None else new_price)
            }
            self.logger.info(f"[{stock_code}] 发送UI更新信号: task_id={task_id}, price_info={price_info}")
            self.update_task_ui.emit(task_id, 'base_price', price_info)
            updated_count += 1
        
        self.logger.info(f"[{stock_code}] 基准价格更新完成，共更新了 {updated_count} 个任务的UI")
        
        # 新增：基准价变化后主动刷新状态栏
        if self.main_window:
            self.logger.info(f"[{stock_code}] 基准价变化为{new_price}，调用主窗口刷新状态栏")
            self.main_window.refresh_price_display_for_stock(stock_code)  # 启用状态栏刷新
        else:
            self.logger.warning(f"[{stock_code}] 基准价变化，但main_window为None，无法刷新状态栏")
        
        # 保存所有任务
        self.save_tasks(list(self.tasks.values()))

    def calculate_trade_price(self, stock_code, signal):
        """计算交易价格"""
        # 根据证券类型决定精度
        precision = 3 if SecurityTypeUtil.is_fund(stock_code) else 2
        
        # 根据精度设置滑点值
        slippage = 0.001 if precision == 3 else 0.01
        
        # 检查是否为夜市任务
        is_night_market = '夜市' in signal.get('reason', '')
        
        if signal['type'] == 'buy':
            # 买入时，以买一价为基准
            if 'askPrice' in signal and signal['askPrice']:
                base_price = signal['askPrice'][0]
            else:
                # 如果没有askPrice，使用signal中的price
                base_price = signal['price']
            # 夜市不使用滑点，其他委托向上调整一个最小单位
            if is_night_market:
                trade_price = round(base_price, precision)
            else:
                trade_price = round(base_price + slippage, precision)
            #self.logger.info(f"[{stock_code}] 买入，买一价：{signal['askPrice'][0]:.{precision}f}，卖一价：{signal['bidPrice'][0]:.{precision}f}，智能定价：{trade_price:.{precision}f}")
        else:
            # 卖出时，以卖一价为基准
            if 'bidPrice' in signal and signal['bidPrice']:
                base_price = signal['bidPrice'][0]
            else:
                # 如果没有bidPrice，使用signal中的price
                base_price = signal['price']
            # 夜市不使用滑点，其他委托向下调整一个最小单位
            if is_night_market:
                trade_price = round(base_price, precision)
            else:
                trade_price = round(base_price - slippage, precision)
            #self.logger.info(f"[{stock_code}] 卖出，买一价：{signal['askPrice'][0]:.{precision}f}，卖一价：{signal['bidPrice'][0]:.{precision}f}，智能定价：{trade_price:.{precision}f}")
        
        return trade_price

    def calculate_trade_volume(self, stock_code, signal, asset, position, trade_price):
        """计算交易量"""
        volume = 0
        volume_can_trade = 0
        
        if signal['type'] == 'buy':
            # 对于夜市买入任务，直接使用表格中的数量
            if '夜市' in signal.get('reason', ''):
                # 查找对应的夜市任务，直接使用表格中的数量
                for task_id, task in self.tasks.items():
                    if (task.get('stock_code') == stock_code and 
                        task.get('strategy', '').startswith('夜市') and 
                        '买入' in task.get('strategy', '')):
                        # 直接使用表格中的数量
                        volume = task.get('init_volume', signal['volume'])
                        self.logger.info(f"[{stock_code}] 夜市买入计算: 使用表格数量={volume}, final_volume={volume}")
                        break
                else:
                    # 如果没找到对应的任务，使用信号中的volume
                    volume = signal['volume']
                    self.logger.info(f"[{stock_code}] 夜市买入计算: 未找到对应任务，使用信号volume={volume}")
            else:
                # 普通买入逻辑
                cash_can_use = asset['cash']  # cash已经是可用现金，不需要再扣除frozen_cash
                volume_can_trade = int(cash_can_use / trade_price / 100) * 100
                volume = min(signal['volume'], volume_can_trade)
                
                self.logger.info(f"[{stock_code}] 买入计算: cash={asset['cash']}, frozen_cash={asset['frozen_cash']}, cash_can_use={cash_can_use}, trade_price={trade_price}, volume_can_trade={volume_can_trade}, signal_volume={signal['volume']}, final_volume={volume}")
        
        elif signal['type'] == 'sell':
            # 对于夜市卖出任务，直接使用表格中的数量
            if '夜市' in signal.get('reason', ''):
                # 查找对应的夜市任务，直接使用表格中的数量
                for task_id, task in self.tasks.items():
                    if (task.get('stock_code') == stock_code and 
                        task.get('strategy', '').startswith('夜市') and 
                        '卖出' in task.get('strategy', '')):
                        # 直接使用参数中设置的卖出数量（而不是 init_volume，全仓）
                        try:
                            params = task.get('params', {})
                            configured_volume = int(params.get('sell_volume', 0) or 0)
                        except Exception:
                            configured_volume = 0

                        if configured_volume > 0:
                            volume = configured_volume
                            self.logger.info(f"[{stock_code}] 夜市卖出计算: 使用配置卖出数量={volume}, final_volume={volume}")
                        else:
                            # 兜底：若参数缺失，则退回到signal中的数量
                            volume = signal['volume']
                            self.logger.warning(f"[{stock_code}] 夜市卖出计算: 未找到有效sell_volume，使用signal数量={volume}")
                        break
                else:
                    # 如果没找到对应的任务，使用信号中的volume
                    volume = signal['volume']
                    self.logger.info(f"[{stock_code}] 夜市卖出计算: 未找到对应任务，使用信号volume={volume}")
            else:
                # 普通卖出逻辑
                # 如果没有持仓信息，跳过卖出
                if not position:
                    return 0
                    
                volume_can_trade = position.get('can_use_volume', 0)
                
                # 特殊处理清仓信号
                if signal['volume'] == 0 and '今日清仓' in signal.get('reason', ''):
                    # 清仓信号，使用全部可用持仓
                    volume = volume_can_trade
                    self.logger.info(f"[{stock_code}] 清仓计算: 使用全部可用持仓={volume}")
                elif signal['reason'] == '超过最大持有天数':
                    volume = volume_can_trade
                else:
                    if volume_can_trade >= signal['volume']*1.5:
                        volume = signal['volume']
                    else:
                        volume = volume_can_trade

        direction = "买入" if signal['type'] == 'buy' else "卖出"        
        #self.logger.info(f"[{stock_code}] {direction}，交易量：{volume}, 可交易数量：{volume_can_trade}, 计划交易数量{signal['volume']}")

        return volume
    
    def risk_control(self, stock_code, signal):
        """风险控制"""
        price_threshold = 0.90 if SecurityTypeUtil.is_fund(stock_code) else 0.90
        
        if signal['type'] == 'buy':
            # 卖一价格异常监控
            if 'askPrice' in signal and signal['askPrice'] and signal['askPrice'][0] < signal['price']:
                self.logger.warning(
                    f"[{stock_code}] "
                    f"买入委托风险控制:"
                    f"卖1价={SecurityTypeUtil.round_price(stock_code, signal['askPrice'][0])}<=最新价{SecurityTypeUtil.round_price(stock_code, signal['price'])}, 中止市价买入"
                )
                return True
            return False
        elif signal['type'] == 'sell':
            # 买一价格异常监控
            if 'bidPrice' in signal and signal['bidPrice'] and signal['bidPrice'][0] < signal['price'] * price_threshold:
                self.logger.warning(
                    f"[{stock_code}] "
                    f"卖出委托风险控制:"
                    f"买1价={SecurityTypeUtil.round_price(stock_code, signal['bidPrice'][0])}<=最新价{SecurityTypeUtil.round_price(stock_code, signal['price'])}*{price_threshold}, 中止市价卖出"
                )
                return True
        return False

    def _check_trading_time(self, current_time):
        """检查是否在交易时段内"""
        # 考虑到要支持夜间交易，所以这里直接返回True
        return True
        
        # 以下代码保留但不执行，用于将来可能的交易时间检查
        # 处理不同类型的时间输入
        if isinstance(current_time, datetime):
            time_obj = current_time.time()
        elif hasattr(current_time, 'hour') and hasattr(current_time, 'minute'):
            time_obj = current_time
        elif isinstance(current_time, (int, float)):
            # 时间戳处理
            dt = pd.to_datetime(current_time, unit='ms').tz_localize('UTC').tz_convert('Asia/Shanghai')
            time_obj = dt.time()
        else:
            return False
        
        # 检查是否在交易时段内
        if (
            # 上午交易时段 9:30:00 - 11:30:00
            ((time_obj.hour == 9 and time_obj.minute >= 30) or
             (time_obj.hour == 10) or
             (time_obj.hour == 11 and time_obj.minute <= 30)) or
            # 下午交易时段 13:00:00 - 15:00:00
            (time_obj.hour >= 13 and time_obj.hour < 15)
        ):
            return True
        
        return False

    def _monitor_trade_signals(self, task_id, control_pipe):
        """监听并处理子进程的交易信号"""
        while task_id in self.running_tasks:
            try:
                if control_pipe.poll(1):
                    try:
                        message = control_pipe.recv()
                        
                        if isinstance(message, tuple) and len(message) == 2:
                            cmd, data = message
                            if cmd == 'trade_signal':
                                #self.logger.info(f"任务 {task_id} 收到交易信号: {data}")
                                # 处理交易信号
                                stock_code = self.tasks[task_id]['stock_code']
                                self.handle_trade_signal(stock_code, data)
                            elif cmd == 'order_response':
                                # 处理订单响应
                                stock_code = self.tasks[task_id]['stock_code']
                                self.logger.info(f"任务 {task_id} 收到订单响应: {data}")
                                # 保存订单信息到任务中
                                if task_id in self.tasks:
                                    if 'orders' not in self.tasks[task_id]:
                                        self.tasks[task_id]['orders'] = []
                                    self.tasks[task_id]['orders'].append(data)
                                    # 保存任务状态
                                    self.save_tasks(list(self.tasks.values()))
                                # 这里可以添加订单响应的处理逻辑
                            elif cmd == 'get_price':
                                # 处理价格请求
                                stock_code = data
                                current_price = self.get_latest_price(stock_code)
                                if current_price == 0 and self.qmt_adapter:
                                    # 如果缓存中没有价格，尝试从QMT获取
                                    try:
                                        tick_data = self.qmt_adapter.get_tick_data(stock_code)
                                        if tick_data and 'last_price' in tick_data:
                                            current_price = float(tick_data['last_price'])
                                            self.update_latest_price(stock_code, current_price)
                                    except:
                                        pass
                                
                                # 发送价格数据响应
                                try:
                                    control_pipe.send(('price_data', current_price))
                                except:
                                    pass
                    except (EOFError, OSError, BrokenPipeError) as pipe_error:
                        #self.logger.warning(f"任务 {task_id} 交易信号管道已关闭: {str(pipe_error)}")
                        self._handle_process_disconnection(task_id)
                        break
                    except Exception as e:
                        self.logger.error(f"处理交易信号错误：{task_id} - {str(e)}")
                        continue
                else:
                    # 检查进程是否仍然存活
                    if task_id in self.running_tasks:
                        process = self.running_tasks[task_id].get('process')
                        if process and not process.is_alive():
                            self.logger.warning(f"任务 {task_id} 进程已退出，清理运行状态")
                            self._handle_process_disconnection(task_id)
                            break

            except (EOFError, OSError, BrokenPipeError) as pipe_error:
                # 管道错误，这是正常的，当进程结束时会发生
                self.logger.warning(f"任务 {task_id} 交易信号管道监听错误: {str(pipe_error)}")
                self._handle_process_disconnection(task_id)
                break
            except Exception as e:
                self.logger.error(f"监听交易信号错误：{task_id} - {str(e)}")
                break

        #self.logger.info(f"任务 {task_id} 交易信号监听线程结束")

    def set_qmt_adapter(self, qmt_adapter):
        """设置 QMT 适配器"""
        #self.logger.info("QMT适配器重新连接，触发任务重载")
        self.qmt_adapter = qmt_adapter
        if hasattr(self, "scheduled_clear_manager") and self.scheduled_clear_manager:
            self.scheduled_clear_manager.connect_tick_signal(qmt_adapter)
        if hasattr(self, "rule_activation_manager") and self.rule_activation_manager:
            self.rule_activation_manager.connect_tick_signal(qmt_adapter)
        # 处理重连时恢复任务
        self.handle_trading_reconnection()

    def handle_trading_reconnection(self):
        """处理交易重连"""
        # 防止重复处理
        if self._reconnection_processed:
            return
            
        self._reconnection_processed = True
        
        try:
            #self.logger.info("开始处理交易重连恢复...")
            
            # 检查任务状态
            tasks_with_orders = []
            tasks_to_notify = []
            night_tasks_to_restart = []
            
            for task_id, task in self.tasks.items():
                if task.get('order_id'):
                    tasks_with_orders.append(task)
                elif task.get('status') in ['运行中', '连接断开']:
                    tasks_to_notify.append(task)
                
                # 检查夜市任务是否需要重启
                # 修改：同时检查"等待"和"未运行"状态的夜市任务
                if (task.get('strategy', '').startswith('夜市') and 
                    task.get('status') in ['等待'] and 
                    not task.get('order_id')):
                    night_tasks_to_restart.append(task_id)
            
            # 记录处理结果
            if tasks_with_orders:
                #self.logger.info(f"发现 {len(tasks_with_orders)} 个有订单的任务需要处理")
                pass
            #else:
            #    self.logger.info("没有有订单的任务需要处理")
                
            if tasks_to_notify:
                self.logger.info(f"发现 {len(tasks_to_notify)} 个任务需要通知")
            #else:
            #    self.logger.info("没有需要通知的任务")
            
            # 重启夜市任务
            if night_tasks_to_restart:
                self.logger.info(f"发现 {len(night_tasks_to_restart)} 个夜市任务需要重启: {night_tasks_to_restart}")
                
                # 按任务ID排序，确保执行顺序一致
                night_tasks_to_restart.sort()
                
                # 启动第一个等待中的夜市任务
                next_task_id = night_tasks_to_restart[0]
                self.logger.info(f"重启夜市任务: {next_task_id}")
                
                # 尝试启动任务
                if self.start_task(next_task_id):
                    self.logger.info(f"夜市任务 {next_task_id} 重启成功")
                else:
                    self.logger.warning(f"夜市任务 {next_task_id} 重启失败")
            #else:
            #    self.logger.info("没有需要重启的夜市任务")
                
        except Exception as e:
            self.logger.error(f"处理交易重连时出错: {e}")
        finally:
            # 延迟重置标志，避免短时间内重复处理
            def reset_flag():
                time.sleep(2)  # 等待2秒
                self._reconnection_processed = False
                
            # 在后台线程中重置标志
            import threading
            threading.Thread(target=reset_flag, daemon=True).start()

    def handle_trade_signal(self, stock_code, signals):
        """处理交易信号"""
        try:
            if not signals:
                return
            
            # 检查股票是否在任务列表中，如果不在则不处理
            stock_in_tasks = any(task.get('stock_code') == stock_code for task in self.tasks.values())
            if not stock_in_tasks:
                self.logger.warning(f"[{stock_code}] 股票不在任务列表中，跳过交易信号")
                return
                
            # 获取当前持仓
            position = self.qmt_adapter.get_stock_position(stock_code)
            
            # 对于买入操作、夜市买入任务和规则任务（含历史万能文案），不要求必须有持仓信息
            is_buy_operation = any(signal.get('type') == 'buy' for signal in signals)
            is_night_market = any('夜市' in signal.get('reason', '') for signal in signals)
            is_rule_strategy_signal = any(
                ('万能' in signal.get('reason', '')) or ('规则' in signal.get('reason', ''))
                for signal in signals
            )
            
            if not position and not is_buy_operation and not is_night_market and not is_rule_strategy_signal:
                self.logger.error(f"[{stock_code}] 没有持仓信息")
                return
                
            # 获取资产信息
            asset = self.qmt_adapter.get_asset()
            if not asset:
                self.logger.error(f"[{stock_code}] 没有资产信息")
                return
            
            #self.logger.info(f"[{stock_code}] 资产信息: {asset}")
            
            for signal in signals:
                try:
                    # 确保signal中包含stock_code
                    if 'stock_code' not in signal:
                        signal['stock_code'] = stock_code
                        
                    # 处理撤单信号
                    if signal['type'] == 'cancel':
                        order_id = signal.get('order_id')
                        if order_id:
                            self.logger.info(f"[{stock_code}] 准备撤单，订单ID: {order_id}")
                            
                            # 发送撤单请求
                            cancel_result = self.qmt_adapter.cancel_order(
                                order_id, stock_code=stock_code
                            )
                            
                            # 构建撤单记录信息
                            # 优先使用信号中的时间
                            signal_time = signal.get('time', '')
                            if signal_time:
                                if isinstance(signal_time, datetime):
                                    order_time_str = signal_time.strftime('%Y-%m-%d %H:%M:%S')
                                elif isinstance(signal_time, (int, float)):
                                    # 时间戳 - 判断是否为毫秒时间戳
                                    if signal_time > 1000000000000:  # 毫秒时间戳
                                        dt = datetime.fromtimestamp(signal_time / 1000)
                                    else:  # 秒时间戳
                                        dt = datetime.fromtimestamp(signal_time)
                                    order_time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
                                else:
                                    # 字符串格式
                                    order_time_str = str(signal_time)
                            else:
                                # 如果信号中没有时间，使用当前时间
                                order_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            
                            cancel_info = {
                                'order_id': str(order_id),
                                'type': '撤单',
                                'price': 0,
                                'volume': 0,
                                'order_time': order_time_str,  # 使用处理后的时间
                                'time': signal.get('time', datetime.now()),  # 保持time字段为datetime对象
                                'reason': signal.get('reason', '撤单'),
                                'strategy_name': signal.get('reason', '撤单'),
                                'order_status': '已撤' if cancel_result else '撤单失败',
                                'is_real_order': True,
                            }
                            
                            # 发送撤单记录
                            self.logger.info(f"[{stock_code}] 撤单结果: {cancel_info}")
                            self.trade_record_updated.emit(stock_code, cancel_info)
                            
                            # 撤单成功后，清除任务中的委托号
                            for task_id, task in self.tasks.items():
                                if task.get('stock_code') == stock_code and ('夜市' in task.get('strategy', '') or task.get('strategy', '') in ['夜市卖出', '夜市买入']):
                                    if 'order_id' in task:
                                        del task['order_id']
                                        task['status'] = '运行中'
                                        self.save_tasks(list(self.tasks.values()))
                                        self.update_task_ui.emit(task_id, 'status', '运行中')
                                        self.logger.info(f"[{stock_code}] 撤单成功，清除委托号，任务状态恢复为运行中")
                                    break
                        else:
                            self.logger.warning(f"[{stock_code}] 撤单信号缺少订单ID")
                        continue
                        
                    # 计算交易价格
                    trade_price = self.calculate_trade_price(stock_code, signal)
                    
                    # 计算交易量
                    trade_volume = self.calculate_trade_volume(stock_code, signal, asset, position, trade_price)
                    
                    # 如果计算出的交易量为0，跳过这个信号
                    if trade_volume <= 0:
                        self.logger.warning(f"[{stock_code}] 计算出的交易量为0，跳过信号: {signal}")
                        continue
                        
                    # 更新信号中的交易量
                    signal['volume'] = trade_volume
                    signal['price'] = trade_price
                    
                    #self.logger.info(f"[{stock_code}] 准备发送交易信号: {signal}")
                    
                    # 发送交易信号
                    from core.smart_sell import resolve_order_strategy_name

                    order_strategy_name = resolve_order_strategy_name(
                        {"type": signal.get("rule_type")},
                        signal,
                    )
                    order_id = self.qmt_adapter.trade(
                        stock_code=stock_code,
                        order_type=signal['type'],
                        price=signal['price'],
                        volume=signal['volume'],
                        strategy_name=order_strategy_name
                    )
                    
                    #self.logger.info(f"[{stock_code}] 交易返回的订单号: {order_id}")
                    
                    # 构建交易记录信息
                    # 优先使用信号中的时间作为委托时间
                    signal_time = signal.get('time', '')
                    if signal_time:
                        if isinstance(signal_time, datetime):
                            order_time_str = signal_time.strftime('%Y-%m-%d %H:%M:%S')
                        elif isinstance(signal_time, (int, float)):
                            # 时间戳 - 判断是否为毫秒时间戳
                            if signal_time > 1000000000000:  # 毫秒时间戳
                                dt = datetime.fromtimestamp(signal_time / 1000)
                            else:  # 秒时间戳
                                dt = datetime.fromtimestamp(signal_time)
                            order_time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            # 字符串格式
                            order_time_str = str(signal_time)
                    else:
                        # 如果信号中没有时间，使用当前时间
                        order_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    trade_info = {
                        'stock_code': stock_code,  # 添加股票代码字段
                        'order_id': str(order_id) if order_id else '',  # 确保订单号是字符串，如果为None则使用空字符串
                        'type': '卖出' if signal['type'] == 'sell' else '买入',
                        'price': signal['price'],
                        'volume': signal['volume'],
                        'order_time': order_time_str,  # 使用处理后的委托时间
                        'time': signal.get('time', datetime.now()),  # 保持time字段为datetime对象，用于UI显示
                        'reason': order_strategy_name,
                        'strategy_name': order_strategy_name,
                        'order_status': '已报' if order_id else '委托失败',  # 添加订单状态
                        'is_real_order': True,
                    }
                    
                    # 发送交易记录
                    #self.logger.info(f"[{stock_code}] 发送交易记录: {trade_info}")
                    self.trade_record_updated.emit(stock_code, trade_info)
                    
                    # 如果是夜市且委托成功，保存委托号到任务中
                    if '夜市' in signal.get('reason', '') and order_id:
                        # 从信号中获取交易方向和关键参数，用于精确匹配
                        signal_type = signal.get('type', '')  # 'buy' 或 'sell'
                        signal_price = signal.get('price', 0)  # 委托价格
                        signal_volume = signal.get('volume', 0)  # 委托数量
                        signal_reason = signal.get('reason', '')  # 包含委托类型信息
                        
                        # 优先查找正在运行的夜市任务，通过股票代码+交易方向+数量+价格精确匹配
                        matched_task_id = None
                        for task_id in self.running_tasks:
                            task = self.tasks.get(task_id)
                            if task and task.get('stock_code') == stock_code and ('夜市' in task.get('strategy', '') or task.get('strategy', '') in ['夜市卖出', '夜市买入']):
                                # 通过交易方向匹配
                                task_strategy = task.get('strategy', '')
                                direction_match = False
                                if signal_type == 'buy' and '买入' in task_strategy:
                                    direction_match = True
                                elif signal_type == 'sell' and '卖出' in task_strategy:
                                    direction_match = True
                                
                                if direction_match:
                                    # 进一步通过数量和价格精确匹配
                                    task_volume = task.get('params', {}).get('buy_volume' if signal_type == 'buy' else 'sell_volume', task.get('init_volume', 0))
                                    task_price = task.get('base_price', 0)
                                    
                                    # 检查数量和价格是否匹配（允许小误差）
                                    if (abs(float(signal_volume) - float(task_volume)) < 1 and 
                                        abs(float(signal_price) - float(task_price)) < 0.01):
                                        matched_task_id = task_id
                                        #self.logger.info(f"[{stock_code}] 精确匹配到任务：{task_id}，方向={signal_type}，数量={signal_volume}，价格={signal_price}")
                                        break
                        
                        # 如果在运行任务中没找到精确匹配，再查找所有任务（兜底逻辑，但仍要求精确匹配）
                        if not matched_task_id:
                            for task_id, task in self.tasks.items():
                                if task.get('stock_code') == stock_code and ('夜市' in task.get('strategy', '') or task.get('strategy', '') in ['夜市卖出', '夜市买入']) and task.get('status') == '运行中':
                                    # 通过交易方向匹配
                                    task_strategy = task.get('strategy', '')
                                    direction_match = False
                                    if signal_type == 'buy' and '买入' in task_strategy:
                                        direction_match = True
                                    elif signal_type == 'sell' and '卖出' in task_strategy:
                                        direction_match = True
                                    
                                    if direction_match:
                                        # 进一步通过数量和价格精确匹配
                                        task_volume = task.get('params', {}).get('buy_volume' if signal_type == 'buy' else 'sell_volume', task.get('init_volume', 0))
                                        task_price = task.get('base_price', 0)
                                        
                                        # 检查数量和价格是否匹配（允许小误差）
                                        if (abs(float(signal_volume) - float(task_volume)) < 1 and 
                                            abs(float(signal_price) - float(task_price)) < 0.01):
                                            matched_task_id = task_id
                                            self.logger.info(f"[{stock_code}] 在所有任务中精确匹配到：{task_id}，方向={signal_type}，数量={signal_volume}，价格={signal_price}")
                                            break
                        
                        if matched_task_id:
                            task = self.tasks[matched_task_id]
                            # 不再从交易信号中保存委托号，改为通过订单列表变化来判断
                            # task['order_id'] = str(order_id)
                            # 委托成功后状态变为"已委托"并停止任务
                            #task['status'] = '已委托'
                            # self.save_tasks(list(self.tasks.values()))
                            # 发送UI更新信号，传递task_id确保精确更新
                            #self.update_task_ui.emit(matched_task_id, 'status', '已委托')
                            self.logger.info(f"[{stock_code}] 夜市{signal_type}下单信号已发送，等待订单列表确认委托状态，任务ID：{matched_task_id}")
                            
                            # 不再停止任务，让订单列表变化来驱动状态更新
                            #if matched_task_id in self.running_tasks:
                            #    self.logger.info(f"[{stock_code}] 夜市{signal_type}成功，停止任务：{matched_task_id}")
                            #    self.stop_task(matched_task_id)
                        else:
                            self.logger.warning(f"[{stock_code}] 夜市{signal_type}成功但未找到精确匹配的任务，订单ID：{order_id}，数量：{signal_volume}，价格：{signal_price}")
                        
                        # 不再立即通知策略进程订单状态，而是等待订单列表更新
                        # 这样可以确保获取到真实的订单状态（已报/未报等）
                        self.logger.info(f"[{stock_code}] 夜市{signal_type}下单信号已发送，等待订单列表更新后通知策略进程真实状态")
                
                except Exception as e:
                    self.logger.error(f"处理单个交易信号失败: {str(e)}", exc_info=True)
                    continue
                
        except Exception as e:
            self.logger.error(f"处理交易信号失败: {str(e)}", exc_info=True)
            # 发送失败记录
            # 优先使用信号中的时间
            signal_time = signals[0].get('time', '') if signals else ''
            if signal_time:
                if isinstance(signal_time, datetime):
                    order_time_str = signal_time.strftime('%Y-%m-%d %H:%M:%S')
                elif isinstance(signal_time, (int, float)):
                    # 时间戳
                    dt = datetime.fromtimestamp(int(signal_time))
                    order_time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    # 字符串格式
                    order_time_str = str(signal_time)
            else:
                # 如果信号中没有时间，使用当前时间
                order_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            trade_info = {
                'stock_code': stock_code,  # 添加股票代码字段
                'order_id': '',  # 失败时订单号为空
                'type': '卖出' if signals[0]['type'] == 'sell' else '买入',
                'price': signals[0]['price'],
                'volume': 0,
                'order_time': order_time_str,  # 使用处理后的时间
                'time': signals[0].get('time', datetime.now()),  # 保持time字段为datetime对象
                'reason': f'委托失败-{str(e)}',
                'order_status': '委托失败',
                'is_real_order': True,
            }
            self.trade_record_updated.emit(stock_code, trade_info)

    def is_trading_day(self, date):
        """判断是否为交易日"""
        if isinstance(date, str):
            try:
                if '_' in date:
                    date = date.split('_')[0]
                date = datetime.strptime(date, '%Y-%m-%d')
            except ValueError:
                return False
        return is_tradeday(date) 

    def _start_next_waiting_night_task(self):
        """启动下一个等待中的夜市任务"""
        try:
            # 查找所有等待中的夜市任务
            # 修改：同时检查"等待"和"未运行"状态的夜市任务
            waiting_tasks = [
                task_id for task_id, task_data in self.tasks.items() 
                if task_data.get('status') in ['等待', '未运行'] and task_data.get('strategy', '').startswith('夜市')
            ]
            
            self.logger.info(f"发现 {len(waiting_tasks)} 个等待中的夜市任务: {waiting_tasks}")
            
            if waiting_tasks:
                # 按任务ID排序，确保执行顺序一致
                waiting_tasks.sort()
                
                # 启动第一个等待中的任务
                next_task_id = waiting_tasks[0]
                self.logger.info(f"启动下一个夜市任务: {next_task_id}")
                
                # 尝试启动任务
                if self.start_task(next_task_id):
                    self.logger.info(f"夜市任务 {next_task_id} 启动成功")
                else:
                    self.logger.warning(f"夜市任务 {next_task_id} 启动失败")
            else:
                self.logger.info("没有等待中的夜市任务")
                
        except Exception as e:
            self.logger.error(f"启动下一个等待中的夜市任务失败: {str(e)}")

    def _start_order_monitor_task(self, task_id):
        """启动订单监控任务（已废弃）"""
        pass

    @staticmethod
    def _run_order_monitor_process(stock_code, task_info, control_pipe, log_pipe):
        """运行订单监控进程（已废弃）"""
        pass 

    def get_task_by_stock_code(self, stock_code):
        """根据股票代码获取任务
        Args:
            stock_code: 股票代码
        Returns:
            task: 找到的任务，如果没找到返回None
        """
        for task_id, task in self.tasks.items():
            if task.get('stock_code') == stock_code:
                # 确保params是字典类型（双重保险，防止某些情况下params仍然是字符串）
                if 'params' in task:
                    params_value = task['params']
                    if isinstance(params_value, str):
                        try:
                            # 先尝试直接解析（标准JSON格式）
                            task['params'] = json.loads(params_value)
                        except (json.JSONDecodeError, TypeError, ValueError):
                            try:
                                # 如果失败，尝试替换单引号为双引号（处理Python字典字符串格式）
                                task['params'] = json.loads(params_value.replace("'", '"'))
                            except (json.JSONDecodeError, TypeError, ValueError):
                                self.logger.warning(f"任务 {stock_code} params字段无法解析，使用空字典")
                                task['params'] = {}
                    elif not isinstance(params_value, dict):
                        self.logger.warning(f"任务 {stock_code} params字段类型异常: {type(params_value)}，使用空字典")
                        task['params'] = {}
                
                return task
        
        # 如果没有找到，不记录调试信息（避免日志过多）
        
        return None

    def toggle_task_monitor(self, task_id):
        """切换任务的监控状态
        Args:
            task_id: 任务ID
        Returns:
            bool: 切换后的监控状态
        """
        if task_id not in self.tasks:
            self.logger.error(f"任务 {task_id} 不存在")
            return False
        
        task = self.tasks[task_id]
        stock_code = task.get('stock_code', '')
        
        # 初始化监控状态
        if 'is_monitoring' not in task:
            task['is_monitoring'] = False
        
        # 切换监控状态
        task['is_monitoring'] = not task['is_monitoring']
        
        # 保存任务状态
        self.save_tasks(list(self.tasks.values()))
        
        # 发送UI更新信号
        self.update_task_ui.emit(task_id, 'is_monitoring', task['is_monitoring'])
        
        status_text = "开启" if task['is_monitoring'] else "关闭"
        self.logger.info(f"[{stock_code}] 任务监控已{status_text}")
        
        return task['is_monitoring']

    def get_task_monitor_status(self, task_id):
        """获取任务的监控状态
        Args:
            task_id: 任务ID
        Returns:
            bool: 监控状态
        """
        if task_id not in self.tasks:
            return False
        
        task = self.tasks[task_id]
        return task.get('is_monitoring', False) 

    def _force_remove_running_task(self, task_id, send_stop=True):
        """强制移除 running_tasks 中的任务（任务可能已从 tasks 删除）"""
        if task_id not in self.running_tasks:
            return False

        task_info = self.running_tasks[task_id]
        process = task_info.get('process')
        control_pipe = task_info.get('control_pipe')
        stock_code = None
        if task_id in self.tasks:
            stock_code = self.tasks[task_id].get('stock_code')
        if not stock_code:
            stock_code = task_info.get('stock_code')

        if send_stop and control_pipe:
            try:
                control_pipe.send('stop')
            except Exception as e:
                self.logger.warning(f"发送停止信号失败：{str(e)}")

        if process:
            try:
                process.join(timeout=2)
            except Exception as e:
                self.logger.warning(f"等待进程退出时出错：{str(e)}")

            if process.is_alive():
                try:
                    process.terminate()
                    process.join(timeout=1)
                    if process.is_alive():
                        process.kill()
                        self.logger.warning(f"强制终止任务 {task_id} 的进程")
                except Exception as e:
                    self.logger.error(f"强制终止进程时出错：{str(e)}")

        for pipe_key in ('control_pipe', 'log_pipe'):
            try:
                pipe = task_info.get(pipe_key)
                if pipe and hasattr(pipe, 'close') and not pipe.closed:
                    pipe.close()
            except (OSError, ValueError, AttributeError):
                pass

        del self.running_tasks[task_id]

        if stock_code and stock_code in self.task_processes:
            self.task_processes[stock_code] = [
                existing for existing in self.task_processes[stock_code]
                if existing[0] != task_id
            ]
            if not self.task_processes[stock_code]:
                del self.task_processes[stock_code]

        return True

    def _cleanup_orphan_running_tasks(self):
        """清理 tasks 中已不存在但 running_tasks 仍登记的任务"""
        orphans = [tid for tid in list(self.running_tasks.keys()) if tid not in self.tasks]
        if not orphans:
            return

        self.logger.warning(f"发现 {len(orphans)} 个孤立运行记录，清理中...")
        for task_id in orphans:
            self._force_remove_running_task(task_id)

    def _running_process_alive(self, task_info) -> bool:
        """running_tasks 登记的进程/线程是否仍存活。"""
        if not isinstance(task_info, dict):
            return False
        process = task_info.get("process")
        if process is None:
            return False
        try:
            return bool(process.is_alive())
        except Exception:
            return False

    def _reconcile_already_running_task(self, task_id) -> bool:
        """若 running_tasks 中进程仍活着，把 params/UI 拉回「运行中」。

        解决：图表/列表显示未运行，点启动却提示已经在运行中。
        返回 True 表示已对齐、无需重新拉起进程。
        """
        task_info = self.running_tasks.get(task_id)
        if not self._running_process_alive(task_info):
            return False
        task = self.tasks.get(task_id)
        if not isinstance(task, dict):
            return False
        stock_code = task.get("stock_code", "")
        params = task.get("params") if isinstance(task.get("params"), dict) else {}
        params["task_running"] = True
        params["task_paused"] = False
        task["params"] = params
        task["status"] = "运行中"
        self.tasks[task_id]["params"] = params
        self.tasks[task_id]["status"] = "运行中"
        self.task_params[task_id] = params
        try:
            self.update_task_ui.emit(task_id, "status", "运行中")
        except Exception:
            pass
        try:
            self._sync_rules_armed_if_builtin()
        except Exception:
            pass
        self.logger.info(
            f"[{stock_code}] 任务已在运行中，已同步 UI/params 为运行中（幂等启动）"
        )
        return True

    def _cleanup_dead_processes(self):
        """清理已退出的进程"""
        dead_tasks = []
        for task_id, task_info in self.running_tasks.items():
            process = task_info.get('process')
            # 无 process 的残缺登记也清掉，避免永久卡在「已经在运行中」
            if process is None or not self._running_process_alive(task_info):
                dead_tasks.append(task_id)
        
        if dead_tasks:
            self.logger.info(f"发现 {len(dead_tasks)} 个已退出/无效的进程，清理中...")
            for task_id in dead_tasks:
                try:
                    self._handle_process_disconnection(task_id)
                except Exception:
                    try:
                        self._force_remove_running_task(task_id, send_stop=False)
                    except Exception as e:
                        self.logger.warning(f"清理失效任务 {task_id} 失败: {e}")

        self._cleanup_orphan_running_tasks()

    def generate_task_id(self):
        """统一生成任务ID - 使用UUID"""
        return str(uuid.uuid4())

    def create_default_params(self, strategy):
        """创建默认参数"""
        if self._is_rule_task_strategy(strategy):
            return {
                'trade_volume': 1000,  # 每笔操作股数，默认1000股
                'cycle_times': 0,
                'clear_time': '00:00:00',
                'up_threshold': 5.0,
                'down_threshold': 3.0,
                'up_operation': '卖出',
                'down_operation': '买入'
            }
        else:
            return {
                'trade_volume': 1000,  # 其他策略也使用每笔操作股数
                'cycle_times': 0,
                'clear_time': '00:00:00',
                'up_threshold': 5.0,
                'down_threshold': 3.0,
                'up_operation': '卖出',
                'down_operation': '买入'
            }

    def update_tasks_file_path(self):
        """更新任务文件路径为当前日期"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            today = datetime.now().strftime('%Y-%m-%d')
            new_tasks_file = os.path.join(current_dir, 'data', f'current_tasks_{today}.xlsx')
            
            # 如果文件路径已经是最新的，不需要更新
            if self.tasks_file == new_tasks_file:
                return False
            
            # 更新文件路径
            old_tasks_file = self.tasks_file
            self.tasks_file = new_tasks_file
            os.makedirs(os.path.dirname(self.tasks_file), exist_ok=True)
            
            self.logger.info(f"任务文件路径已更新: {old_tasks_file} -> {self.tasks_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"更新任务文件路径失败：{str(e)}")
            return False