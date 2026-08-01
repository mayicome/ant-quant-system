import sys
import warnings
from PyQt5.QtWidgets import QApplication, QMainWindow, QTextEdit, QMessageBox, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QProgressBar
from PyQt5.QtCore import pyqtSlot, QThread, pyqtSignal
from ui.backtest_window_ext import BacktestWindowExt
from ui.backtest_window import Ui_MainWindow
from utils.config import Config
from brokers.qmt_adapter import QMTManager
from utils.logger import Logger
from core.task_manager import TaskManager
import my_function as myf
import logging
import os
from datetime import datetime

# 忽略特定的弃用警告
warnings.filterwarnings("ignore", category=DeprecationWarning)

class BacktestApp(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        # 先初始化配置对象
        self.config = Config()
        
        # 初始化日志系统（只初始化一次）
        self.logger = Logger(mode='backtest')
        self.logger.info("回测日志系统初始化完成")
        
        # 创建主窗口
        self.window = QMainWindow()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self.window)
        
        # 创建扩展UI，传入 logger
        self.ui_ext = BacktestWindowExt(logger=self.logger)
        self.ui_ext.setup_ui(self.window)
        
        # 使用 ui_ext 中的 task_manager
        self.task_manager = self.ui_ext.task_manager
        
        # 显示窗口
        self.window.show()
        
        # 初始化日志
        textEdit = self.ui_ext.textEdit  # 使用 ui_ext 中的 textEdit
        textEdit.setReadOnly(True)
        self.logger.add_text_edit_handler(textEdit)  # 添加文本框处理器
        
        # 加载扩展功能
        self.ui_ext.setup_position_slots(self.window)  # 传入 window 而不是 self

        self.qmt_manager = QMTManager(path=None, account=None, mode='backtest')  # 回测模式下不需要实际的 QMT 连接
        # 回测模式下不需要连接信号

def main():
    app = QApplication(sys.argv)
    window = BacktestApp(sys.argv)
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()