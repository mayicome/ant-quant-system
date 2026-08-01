from PyQt5.QtWidgets import QTextEdit
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot

class AutoScrollTextEdit(QTextEdit):
    """自动滚动的文本编辑器"""
    text_appended = pyqtSignal(str)  # 添加信号
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # 设置垂直滚动条始终显示
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        # 设置水平滚动条始终显示
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        # 设置只读
        self.setReadOnly(True)
        # 连接信号
        self.text_appended.connect(self._handle_text_appended)
    
    def append(self, text):
        """重写append方法，使用信号机制"""
        try:
            self.text_appended.emit(str(text))
        except Exception as e:
            print(f"Error in append: {str(e)}")
            # 如果信号机制失败，直接调用父类的append方法
            super().append(str(text))
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
    
    @pyqtSlot(str)
    def _handle_text_appended(self, text):
        """处理文本追加的信号"""
        try:
            super().append(text)
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        except Exception as e:
            print(f"Error in _handle_text_appended: {str(e)}")
            # 如果处理失败，尝试直接调用父类的append方法
            try:
                super().append(text)
                self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            except Exception as append_error:
                print(f"Error in fallback append: {str(append_error)}") 