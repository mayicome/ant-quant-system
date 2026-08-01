import os
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from PyQt5.QtCore import QMetaObject, Q_ARG, Qt, QObject, pyqtSignal
import sys
from PyQt5.QtWidgets import QTextEdit
import traceback

class TextEditHandler(logging.Handler):
    """自定义的日志处理器，用于将日志输出到QTextEdit"""
    def __init__(self, text_edit):
        super().__init__()
        self.text_edit = text_edit
        self.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', datefmt='%H:%M:%S'))

    def emit(self, record):
        try:
            msg = self.format(record)
            if self.text_edit is not None:
                try:
                    # 首先尝试使用信号机制
                    QMetaObject.invokeMethod(self.text_edit, 
                                           "_handle_text_appended",
                                           Qt.QueuedConnection,
                                           Q_ARG(str, msg))
                except Exception as e:
                    # 如果信号机制失败，直接调用append方法
                    print(f"信号机制失败，使用直接调用: {str(e)}")
                    try:
                        self.text_edit.append(msg)
                        # 滚动到底部
                        self.text_edit.verticalScrollBar().setValue(self.text_edit.verticalScrollBar().maximum())
                    except Exception as append_error:
                        print(f"直接调用append也失败: {str(append_error)}")
        except Exception as e:
            # 如果UI操作失败，记录到控制台
            print(f"UI更新失败: {str(e)}")
            # 不要调用handleError，避免循环
            return

class Logger(QObject):
    """日志管理器"""
    log_signal = pyqtSignal(str)
    _instances = {}  # 使用字典存储不同模式的实例
    _initialized = {}  # 使用字典存储不同模式的初始化状态
    
    @staticmethod
    def _normalize_mode_key(mode):
        """统一日志模式：回测单独一套，其余（live/real/默认）共用实盘日志。"""
        return 'backtest' if str(mode) == 'backtest' else 'live'

    def __new__(cls, mode='live'):
        mode_key = cls._normalize_mode_key(mode)
        if mode_key not in cls._instances:
            instance = super(Logger, cls).__new__(cls)
            instance._mode = mode_key
            cls._instances[mode_key] = instance
        return cls._instances[mode_key]
    
    def __init__(self, mode='live'):
        mode_key = self.__class__._normalize_mode_key(mode)
        # 如果这个 mode 已经初始化过，直接返回
        if mode_key in self.__class__._initialized:
            return
            
        # 确保QObject正确初始化
        super().__init__()
        
        # 根据模式设置logger名称
        logger_name = 'backtest_trade' if mode_key == 'backtest' else 'live_trade'
        self.logger = logging.getLogger(logger_name)
        
        # 如果logger已经有handler，说明已经初始化过，直接返回
        if self.logger.handlers:
            self.__class__._initialized[mode_key] = True
            return
            
        # 清除所有已存在的handler
        self.logger.handlers.clear()
        
        # 设置日志级别（与历史行为一致：INFO 正常输出，DEBUG 仍可通过 handler 默认 NOTSET 输出）
        self.logger.setLevel(logging.INFO)
        
        # 重要：设置propagate为False，避免日志向上传播到root logger
        self.logger.propagate = False
        
        # 确保root logger也不会干扰
        logging.root.setLevel(logging.WARNING)
        
        # 创建logs目录（如果不存在）
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # 保存日志目录路径
        self.log_dir = log_dir
            
        # 根据模式设置日志文件名
        log_file = os.path.join(log_dir, f'{mode_key}_trade.log')
        
        # 添加控制台处理器（不设 handler 级别：配合下方手动 dispatch，与旧版一致）
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
        ))
        self.logger.addHandler(console_handler)
        
        try:
            # 添加自定义的轮转处理器，处理Windows权限问题
            class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
                def doRollover(self):
                    """重写轮转方法，添加异常处理"""
                    try:
                        super().doRollover()
                    except PermissionError:
                        # 如果遇到权限错误，记录错误但不中断程序
                        print(f"日志轮转失败（权限问题），继续使用当前日志文件")
                    except Exception as e:
                        print(f"日志轮转失败：{str(e)}")
            
            # 使用安全的轮转处理器
            file_handler = SafeTimedRotatingFileHandler(
                log_file,
                when='midnight',
                interval=1,
                backupCount=3650,  # 保留10年的日志
                encoding='utf-8',  # 使用utf-8编码
                delay=True  # 延迟打开文件，避免权限问题
            )
            file_handler.suffix = "%Y-%m-%d.log"  # 设置日期后缀格式
            file_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
            ))
            self.logger.addHandler(file_handler)
        except Exception as e:
            print(f"创建日志文件失败: {str(e)}")
            # 如果轮转处理器失败，尝试使用普通文件处理器
            try:
                file_handler = logging.FileHandler(log_file, encoding='utf-8', delay=True)
                file_handler.setFormatter(logging.Formatter(
                    '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
                ))
                self.logger.addHandler(file_handler)
                print(f"使用普通文件处理器作为备选方案")
            except Exception as e2:
                print(f"备选日志处理器也失败: {str(e2)}")
        
        # 连接信号
        self.log_signal.connect(self._handle_log)
        
        # 标记这个 mode 已经初始化
        self.__class__._initialized[mode_key] = True
        
    def _handle_log(self, msg):
        """处理日志信号"""
        # 必须走自定义 info()：直接调用 self.logger.info 会绕过 Logger.info 的手动分发逻辑
        self.info(msg)
    
    def info(self, msg):
        """记录信息级别的日志"""
        # 获取调用者的文件名和行号
        caller_frame = sys._getframe(1)
        filename = os.path.basename(caller_frame.f_code.co_filename)
        lineno = caller_frame.f_lineno
        
        # 创建LogRecord对象，手动设置文件名和行号
        record = logging.LogRecord(
            name=self.logger.name,
            level=logging.INFO,
            pathname=caller_frame.f_code.co_filename,
            lineno=lineno,
            msg=msg,
            args=(),
            exc_info=None
        )
        
        # 手动调用所有handler
        for handler in self.logger.handlers:
            if record.levelno >= handler.level:
                handler.handle(record)
    
    def error(self, msg, exc_info=False):
        """记录错误级别的日志"""
        # 获取调用者的文件名和行号
        caller_frame = sys._getframe(1)
        filename = os.path.basename(caller_frame.f_code.co_filename)
        lineno = caller_frame.f_lineno
        
        # 创建LogRecord对象，手动设置文件名和行号
        record = logging.LogRecord(
            name=self.logger.name,
            level=logging.ERROR,
            pathname=caller_frame.f_code.co_filename,
            lineno=lineno,
            msg=msg,
            args=(),
            exc_info=exc_info
        )
        
        # 手动调用所有handler
        for handler in self.logger.handlers:
            if record.levelno >= handler.level:
                handler.handle(record)
    
    def warning(self, msg):
        """记录警告级别的日志"""
        # 获取调用者的文件名和行号
        caller_frame = sys._getframe(1)
        filename = os.path.basename(caller_frame.f_code.co_filename)
        lineno = caller_frame.f_lineno
        
        # 创建LogRecord对象，手动设置文件名和行号
        record = logging.LogRecord(
            name=self.logger.name,
            level=logging.WARNING,
            pathname=caller_frame.f_code.co_filename,
            lineno=lineno,
            msg=msg,
            args=(),
            exc_info=None
        )
        
        # 手动调用所有handler
        for handler in self.logger.handlers:
            if record.levelno >= handler.level:
                handler.handle(record)
    
    def debug(self, msg):
        """记录调试级别的日志"""
        # 获取调用者的文件名和行号
        caller_frame = sys._getframe(1)
        filename = os.path.basename(caller_frame.f_code.co_filename)
        lineno = caller_frame.f_lineno
        
        # 创建LogRecord对象，手动设置文件名和行号
        record = logging.LogRecord(
            name=self.logger.name,
            level=logging.DEBUG,
            pathname=caller_frame.f_code.co_filename,
            lineno=lineno,
            msg=msg,
            args=(),
            exc_info=None
        )
        
        # 手动调用所有handler
        for handler in self.logger.handlers:
            if record.levelno >= handler.level:
                handler.handle(record)
    
    def critical(self, msg):
        """记录严重错误级别的日志"""
        # 获取调用者的文件名和行号
        caller_frame = sys._getframe(1)
        filename = os.path.basename(caller_frame.f_code.co_filename)
        lineno = caller_frame.f_lineno
        
        # 创建LogRecord对象，手动设置文件名和行号
        record = logging.LogRecord(
            name=self.logger.name,
            level=logging.CRITICAL,
            pathname=caller_frame.f_code.co_filename,
            lineno=lineno,
            msg=msg,
            args=(),
            exc_info=None
        )
        
        # 手动调用所有handler
        for handler in self.logger.handlers:
            if record.levelno >= handler.level:
                handler.handle(record)
    
    def add_text_edit_handler(self, text_edit):
        """添加textEdit的handler"""
        if text_edit:
            # 检查是否已经存在相同的 TextEditHandler
            for handler in self.logger.handlers:
                if isinstance(handler, TextEditHandler) and handler.text_edit == text_edit:
                    return  # 如果已存在相同的处理器，直接返回
            # 如果不存在，才添加新的处理器
            text_handler = TextEditHandler(text_edit)
            self.logger.addHandler(text_handler)
    
    def close(self):
        """关闭所有日志处理器并刷新缓冲区"""
        try:
            # 刷新并关闭所有handler
            for handler in self.logger.handlers[:]:  # 使用切片复制列表，避免迭代时修改
                try:
                    handler.flush()
                    handler.close()
                except Exception as e:
                    # 忽略关闭时的错误，避免影响程序退出
                    pass
            # 清空handlers列表
            self.logger.handlers.clear()
        except Exception as e:
            # 忽略所有错误，确保程序能正常退出
            pass
    
    @classmethod
    def close_all(cls):
        """关闭所有Logger实例的日志处理器"""
        for instance in cls._instances.values():
            if hasattr(instance, 'close'):
                try:
                    instance.close()
                except Exception:
                    pass

    def _setup_logger(self):
        """遗留占位：历史上可能用于二次初始化；当前工程未调用。"""
        return
