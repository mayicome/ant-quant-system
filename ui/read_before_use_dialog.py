"""启动必读弹窗（独立轻量模块，避免经 dialogs.py 拉入 pandas 等重依赖）。"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class ReadBeforeUseDialog(QDialog):
    """使用前必读对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用前必读")
        self.setFixedSize(500, 350)
        self.setWindowFlags(
            (self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            | Qt.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.ApplicationModal)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        font = self.font()
        font.setPointSize(12)

        warning_text = """
        <div style="margin: 20px; line-height: 1.8; text-align: center;">
        <p style="font-size: 14pt; font-weight: bold; color: #D32F2F;">
        ⚠️ 重要声明 ⚠️
        </p>
        <p style="font-size: 12pt; color: #333333;">
        本程序代码开源，仅供参考。
        </p>
        <p style="font-size: 12pt; color: #333333;">
        继续使用本程序，代表您同意使用本程序的后果完全由您本人自行承担。
        </p>
        </div>
        """

        warning_label = QLabel(warning_text)
        warning_label.setFont(font)
        warning_label.setStyleSheet("color: #333333;")
        warning_label.setWordWrap(True)
        layout.addWidget(warning_label)

        agree_button = QPushButton("我同意")
        agree_button.setFont(font)
        agree_button.setStyleSheet("""
            QPushButton {
                background-color: #D32F2F;
                color: white;
                border: none;
                padding: 10px 25px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 12pt;
            }
            QPushButton:hover {
                background-color: #B71C1C;
            }
            QPushButton:pressed {
                background-color: #8E0000;
            }
        """)
        agree_button.clicked.connect(self.accept)

        disagree_button = QPushButton("我不同意")
        disagree_button.setFont(font)
        disagree_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 10px 25px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 12pt;
            }
            QPushButton:hover {
                background-color: #45A049;
            }
            QPushButton:pressed {
                background-color: #3D8B40;
            }
        """)
        disagree_button.clicked.connect(self.disagree_and_exit)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(agree_button)
        button_layout.addWidget(disagree_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def disagree_and_exit(self):
        """我不同意，关闭对话框"""
        self.reject()
