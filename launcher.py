import os
import json
import warnings
import sys
import subprocess
import re
from datetime import datetime

# 避免 PyQt/SIP 的弃用警告刷屏（不影响功能）
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r".*sipPyTypeDict.*",
)

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGridLayout,
    QScrollArea,
    QMessageBox,
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QFont, QIcon


class AntLauncherWindow(QMainWindow):
    """蚂蚁量化系统启动器：统一入口，启动三个子系统。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("蚂蚁量化系统启动器")
        self._setup_icon()
        self._setup_ui()

    def _get_apps_config_path(self) -> str:
        root_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(root_dir, "data", "launcher_apps.json")

    def _load_tool_apps(self) -> list:
        """
        从 data/launcher_apps.json 读取小程序配置。
        返回仅包含 category='tool' 的启用项，按 order 排序。
        """
        cfg_path = self._get_apps_config_path()
        default_apps = [
            {
                "id": "profit_index_gui",
                "name": "赚钱指数",
                "script": "profit_index_gui.py",
                "category": "tool",
                "description": "赚钱指数统计与图表",
                "order": 10,
                "enabled": True,
            },
            {
                "id": "main_line_group_gui",
                "name": "主线分析",
                "script": "main_line_group_gui.py",
                "category": "tool",
                "description": "主线分组与核心标的分析（独立版）",
                "order": 11,
                "enabled": True,
            },
            {
                "id": "limit_up_structure_analysis_gui",
                "name": "涨停结构分析",
                "script": "limit_up_structure_analysis_gui.py",
                "category": "tool",
                "description": "涨停结构统计与评级分析（独立版）",
                "order": 15,
                "enabled": True,
            },
            {
                "id": "limit_up_gene_analysis_gui",
                "name": "涨停基因分析",
                "script": "limit_up_gene_analysis_gui.py",
                "category": "tool",
                "description": "涨停基因统计（独立版）",
                "order": 20,
                "enabled": True,
            },
            {
                "id": "main_force_net_inflow_gui",
                "name": "主力净流入分析",
                "script": "main_force_net_inflow_gui.py",
                "category": "tool",
                "description": "主力净流入统计（独立版）",
                "order": 30,
                "enabled": True,
            },
            {
                "id": "longhubang",
                "name": "机构净买净卖排行",
                "script": "inst_net_rank_gui.py",
                "category": "tool",
                "description": "机构当日/三日净买净卖排行（独立版）",
                "order": 35,
                "enabled": True,
            },
            {
                "id": "lhb_analysis_gui",
                "name": "龙虎榜解析",
                "script": "lhb_analysis_gui.py",
                "category": "tool",
                "description": "龙虎榜挖掘分析（机构+北向+游资）",
                "order": 99,
                "enabled": True,
            },
        ]
        if not os.path.isfile(cfg_path):
            return sorted([a for a in default_apps if a.get("enabled", True)], key=lambda x: x.get("order", 0))

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                return []
            apps = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if item.get("enabled", True) is False:
                    continue
                if item.get("category") != "tool":
                    continue
                apps.append(item)
            apps.sort(key=lambda x: x.get("order", 0))
            return apps
        except Exception:
            return sorted([a for a in default_apps if a.get("enabled", True)], key=lambda x: x.get("order", 0))

    def _setup_icon(self) -> None:
        """设置窗口图标，与主程序/其他系统保持一致。"""
        search_dirs = []
        if getattr(sys, "frozen", False):
            search_dirs.append(getattr(sys, "_MEIPASS", ""))
            search_dirs.append(os.path.dirname(sys.executable or ""))
        # 开发/兜底：从源码目录加载
        search_dirs.append(os.path.dirname(os.path.abspath(__file__)))

        for fname in ("ant.ico", "ant.png"):
            for d in search_dirs:
                if not d:
                    continue
                p = os.path.join(d, fname)
                if os.path.exists(p):
                    self.setWindowIcon(QIcon(p))
                    return

    def _setup_ui(self) -> None:
        self.resize(1040, 560)
        self.setMinimumSize(780, 480)

        central = QWidget(self)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(30)

        # 顶部说明区（窗口标题栏已包含“启动器”，这里不重复显示）
        subtitle_label = QLabel("请选择要启动的子系统", self)
        subtitle_font = QFont()
        subtitle_font.setPointSize(11)
        subtitle_label.setFont(subtitle_font)
        subtitle_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        subtitle_label.setStyleSheet("color: #666666;")

        main_layout.addWidget(subtitle_label)

        # 中间三个大按钮
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(30)

        btn_style = """
            QPushButton {
                background-color: #C62828;
                color: white;
                border-radius: 10px;
                padding: 14px 28px;
                font-size: 18px;
                font-weight: bold;
                min-width: 190px;
                min-height: 78px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
            QPushButton:pressed {
                background-color: #B71C1C;
            }
        """

        # 为三个入口准备各自的小图标：
        # - ant_trade.png        → 交易系统
        # - ant_strategy.png     → 策略生成系统
        # - ant_picker.png       → 选股系统
        # 若不存在，则退化为 ant.ico；再没有则仅文字按钮。
        icon_size = QSize(32, 32)

        search_dirs = []
        if getattr(sys, "frozen", False):
            search_dirs.append(getattr(sys, "_MEIPASS", ""))
            search_dirs.append(os.path.dirname(sys.executable or ""))
        search_dirs.append(os.path.dirname(os.path.abspath(__file__)))

        def load_icon(*names: str) -> QIcon:
            for name in names:
                for d in search_dirs:
                    if not d:
                        continue
                    p = os.path.join(d, name)
                    if os.path.exists(p):
                        return QIcon(p)
            return QIcon()

        icon_trade = load_icon("ant_trade.png", "trade.png", "ant.ico", "ant.png")
        icon_strategy = load_icon("ant_strategy.png", "strategy.png", "ant.ico", "ant.png")
        icon_picker = load_icon("ant_picker.png", "picker.png", "ant.ico", "ant.png")

        self.btn_trade = QPushButton("交易系统", self)
        self.btn_trade.setToolTip("启动主盘面交易系统")
        self.btn_trade.setStyleSheet(btn_style)
        if not icon_trade.isNull():
            self.btn_trade.setIcon(icon_trade)
            self.btn_trade.setIconSize(icon_size)
        self.btn_trade.clicked.connect(self.launch_trade_system)

        self.btn_strategy = QPushButton("策略生成系统", self)
        self.btn_strategy.setToolTip("启动策略生成与任务导出系统")
        self.btn_strategy.setStyleSheet(btn_style)
        if not icon_strategy.isNull():
            self.btn_strategy.setIcon(icon_strategy)
            self.btn_strategy.setIconSize(icon_size)
        self.btn_strategy.clicked.connect(self.launch_strategy_generator)

        self.btn_picker = QPushButton("选股系统", self)
        self.btn_picker.setToolTip("启动板块与模式筛选的选股系统")
        self.btn_picker.setStyleSheet(btn_style)
        if not icon_picker.isNull():
            self.btn_picker.setIcon(icon_picker)
            self.btn_picker.setIconSize(icon_size)
        self.btn_picker.clicked.connect(self.launch_sector_filter)

        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self.btn_picker)    # 选股系统（最前）
        buttons_layout.addWidget(self.btn_strategy) # 策略生成系统（中间）
        buttons_layout.addWidget(self.btn_trade)     # 交易系统（最后）
        buttons_layout.addStretch(1)

        main_layout.addLayout(buttons_layout)
        main_layout.addSpacing(10)

        # 底部小提示（提示主系统启动行为，不放在“小程序工具箱”内部）
        hint_label = QLabel("提示：启动子系统后，本启动器可以最小化保留，也可以直接关闭。", self)
        hint_font = QFont()
        hint_font.setPointSize(9)
        hint_label.setFont(hint_font)
        hint_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        hint_label.setStyleSheet("color: #999999;")
        main_layout.addWidget(hint_label)

        # --- 小程序：分析工具箱（从配置读取，便于后续扩展）---
        tools_title = QLabel("常用小工具", self)
        tools_font = QFont()
        tools_font.setPointSize(13)
        tools_font.setBold(True)
        tools_title.setFont(tools_font)
        tools_title.setStyleSheet("color: #333333;")
        tools_title.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        main_layout.addWidget(tools_title)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(180)

        tools_container = QWidget(scroll)
        grid = QGridLayout(tools_container)
        grid.setSpacing(12)
        grid.setContentsMargins(0, 0, 0, 0)

        tool_apps = self._load_tool_apps()
        # 过滤掉缺少脚本路径的配置项
        valid_apps = []
        for app in tool_apps:
            script_rel = str(app.get("script") or "").strip()
            if script_rel:
                valid_apps.append(app)
        tool_apps = valid_apps

        tool_btn_style = """
            QPushButton {
                background-color: #1976D2;
                color: white;
                border: 1px solid #DDDDDD;
                border-radius: 8px;
                padding: 6px 8px;
                font-size: 15px;
                min-width: 145px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #1E88E5;
            }
            QPushButton:pressed {
                background-color: #1565C0;
            }
        """

        # 小工具脚本通常放在“launcher.exe 所在目录”作为项目根目录；
        # PyInstaller 冻结后，__file__ 位置可能在临时解压目录，导致误判脚本不存在从而 setEnabled(False)。
        if getattr(sys, "frozen", False):
            root_dir = os.path.dirname(sys.executable or "")
        else:
            root_dir = os.path.dirname(os.path.abspath(__file__))
        max_cols = 5  # 默认 5 个一行
        n_tools = len(tool_apps)
        # 只有一行时，把按钮居中显示，避免“后面空两个格”
        single_row = 0 < n_tools <= max_cols
        col_offset = (max_cols - n_tools) // 2 if single_row else 0

        for idx, app in enumerate(tool_apps):
            name = str(app.get("name") or "").strip() or str(app.get("id") or "未知")
            script_rel = str(app.get("script") or "").strip()
            desc = str(app.get("description") or "").strip()

            script_path = os.path.join(root_dir, script_rel)
            btn = QPushButton(f"{name}", self)
            btn.setStyleSheet(tool_btn_style)
            btn.setToolTip(f"{name}\n{desc}\n{script_rel}")
            btn.setEnabled(os.path.isfile(script_path))
            btn.clicked.connect(lambda checked=False, s=script_rel: self._on_tool_clicked(s))

            if single_row:
                row = 0
                col = idx + col_offset
            else:
                row = idx // max_cols
                col = idx % max_cols
            grid.addWidget(btn, row, col)

        scroll.setWidget(tools_container)
        main_layout.addWidget(scroll, 1)

    # --- 启动三个子系统 ---

    def _on_tool_clicked(self, script_rel_path: str) -> None:
        self._launch_python(script_rel_path)

    def _run_longhubang_and_show_result(self, script_rel_path: str) -> None:
        """执行机构净买净卖排行脚本，并弹窗提示导出文件。"""
        if getattr(sys, "frozen", False):
            root_dir = os.path.dirname(sys.executable)
            python_exe = os.path.join(root_dir, "python.exe")
            if not os.path.isfile(python_exe):
                python_exe = "python"
        else:
            root_dir = os.path.dirname(os.path.abspath(__file__))
            python_exe = sys.executable or "python"
        script_path = os.path.join(root_dir, script_rel_path)
        if not os.path.exists(script_path):
            QMessageBox.warning(self, "机构净买净卖排行", f"脚本不存在：\n{script_path}")
            return
        try:
            run_env = os.environ.copy()
            run_env["PYTHONIOENCODING"] = "utf-8"
            run_env["PYTHONUTF8"] = "1"
            cp = subprocess.run(
                [python_exe, "-X", "utf8", script_path],
                cwd=root_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=run_env,
                timeout=300,
                creationflags=(subprocess.CREATE_NO_WINDOW if (sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW")) else 0),
            )
        except subprocess.TimeoutExpired:
            QMessageBox.warning(self, "机构净买净卖排行", "运行超时（>300秒），请稍后重试。")
            return
        except Exception as e:
            QMessageBox.warning(self, "机构净买净卖排行", f"运行失败：{e}")
            return

        output = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip()
        exported_paths = []
        data_date_lines = []
        for line in output.splitlines():
            s = line.strip()
            if "已导出：" in s:
                exported_paths.append(s.split("已导出：", 1)[1].strip())
            elif s.startswith("数据日期："):
                data_date_lines.append(s)

        if cp.returncode == 0:
            if exported_paths:
                names = [os.path.basename(p) for p in exported_paths if p]
                msg = "导出完成：\n" + "\n".join(f"- {n}" for n in names)
                if data_date_lines:
                    msg += "\n\n" + "\n".join(data_date_lines)

                # 兜底：即使脚本没打印“数据日期”，也从文件名解析日期判断是否为昨日数据
                today = datetime.now().strftime("%Y%m%d")
                file_dates = []
                for n in names:
                    m = re.search(r"(\d{8})", n)
                    if m:
                        file_dates.append(m.group(1))
                stale_dates = sorted({d for d in file_dates if d < today})
                if stale_dates:
                    msg += (
                        "\n\n提示：当前导出数据日期早于今天，数据源可能尚未更新到今日。"
                        f"\n识别到的数据日期：{', '.join(stale_dates)}"
                        f"\n今天日期：{today}"
                    )

                msg += "\n\n完整路径：\n" + "\n".join(exported_paths)
            else:
                msg = "脚本运行完成，但未识别到“已导出”文件路径。"
            QMessageBox.information(self, "机构净买净卖排行", msg)
        else:
            tail = "\n".join(output.splitlines()[-20:]) if output else "(无输出)"
            QMessageBox.warning(
                self,
                "机构净买净卖排行",
                f"脚本运行失败，返回码：{cp.returncode}\n\n最近输出：\n{tail}",
            )

    def _launch_python(self, script_rel_path: str) -> None:
        """用当前 Python 解释器启动一个独立子进程运行脚本。打包成 exe 时以 exe 所在目录为项目根，并用本机 Python 运行脚本。"""
        if getattr(sys, "frozen", False):
            # PyInstaller 打包后：项目根 = exe 所在目录
            root_dir = os.path.dirname(sys.executable)
            # 优先用同目录下的 python.exe（便携环境）；用 CREATE_NO_WINDOW 隐藏控制台窗口
            python_exe = os.path.join(root_dir, "python.exe")
            if not os.path.isfile(python_exe):
                python_exe = "python"
        else:
            root_dir = os.path.dirname(os.path.abspath(__file__))
            python_exe = sys.executable or "python"
        script_path = os.path.join(root_dir, script_rel_path)
        if not os.path.exists(script_path):
            return
        try:
            popen_kwargs = {"cwd": root_dir}
            # Windows 下不显示控制台窗口
            if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            logs_dir = os.path.join(root_dir, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            log_path = os.path.join(logs_dir, "launcher_run.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n=== Launch {script_rel_path} with {python_exe} ===\n")
                popen_kwargs["stdout"] = f
                popen_kwargs["stderr"] = f
                subprocess.Popen([python_exe, script_path], **popen_kwargs)
        except Exception:
            pass

    def launch_trade_system(self) -> None:
        # 主交易系统入口在 main.py
        self._launch_python("main.py")

    def launch_strategy_generator(self) -> None:
        # 策略生成系统入口在 strategy_generator_app/main.py
        self._launch_python(os.path.join("strategy_generator_app", "main.py"))

    def launch_sector_filter(self) -> None:
        # 选股系统入口在 sector_stock_filter.py
        self._launch_python("sector_stock_filter.py")


def main() -> None:
    app = QApplication(sys.argv)
    window = AntLauncherWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

