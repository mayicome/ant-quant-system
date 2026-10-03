import os
import json
import warnings
import sys
import subprocess
import re
from datetime import datetime, date, time as dt_time

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
    QProgressDialog,
    QDialog,
    QComboBox,
    QFileDialog,
    QDialogButtonBox,
)
from PyQt5.QtCore import Qt, QSize, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon

# 项目根：exe 旁 / 源码旁。更新模块始终从磁盘加载，便于 git pull 生效且不依赖打进包。
_BOOT_ROOT = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
if _BOOT_ROOT and _BOOT_ROOT not in sys.path:
    sys.path.insert(0, _BOOT_ROOT)


def _load_py_from_disk(rel_path: str, module_name: str):
    """从项目根加载 .py（打包 exe 也读磁盘，便于 git pull 生效）。

    每次从磁盘重新 exec，避免「检查更新」拉到新文件后仍用内存里的旧模块
    （例如仍走 C:\\Temp + os.replace，触发 WinError 17）。
    """
    import importlib.util
    import sys as _sys

    path = os.path.join(_BOOT_ROOT, *rel_path.replace("\\", "/").split("/"))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"找不到模块文件：{path}")
    _sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    # dataclass 等依赖 sys.modules[模块名]，必须在 exec_module 前注册
    _sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git_head_short(root: str) -> str:
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if int(cp.returncode or 0) == 0:
            return (cp.stdout or "").strip()
    except Exception:
        pass
    return ""


def _load_repo_updater():
    """从项目根 utils/repo_updater.py 加载（打包 exe 也读磁盘文件）。"""
    return _load_py_from_disk("utils/repo_updater.py", "ant_repo_updater")


def _display_version() -> str:
    try:
        mod = _load_py_from_disk("utils/product_version.py", "ant_product_version")
        return str(mod.display_version())
    except Exception:
        return ""


def _version_hint() -> str:
    ver = _display_version()
    return f"当前版本：{ver}" if ver else ""


# 定时任务一般在 17:30 拉起盘后 bat；启动器晚一分钟看它有没有起来。
_POST_MARKET_WATCH_AT = dt_time(17, 31)
_DEFAULT_POST_MARKET_BAT = r"D:\run_all_if_trading_day.bat"

_DEFAULT_UPDATE_MIRRORS = [
    "https://gitclone.com/{github_path}",
    "https://ghproxy.net/https://{github_path}",
    "https://mirror.ghproxy.com/https://{github_path}",
    "https://ghfast.top/https://{github_path}",
]


class _UpdateWorker(QThread):
    """后台检查 / 应用仓库更新，避免卡住启动器界面。"""

    finished_ok = pyqtSignal(object)  # UpdateCheckResult

    def __init__(self, root: str, mirrors: list, *, apply: bool = False, parent=None):
        super().__init__(parent)
        self.root = root
        self.mirrors = list(mirrors or [])
        self.apply = bool(apply)

    def run(self) -> None:
        try:
            ru = _load_repo_updater()
            if self.isInterruptionRequested():
                self.finished_ok.emit(
                    ru.UpdateCheckResult(False, "更新已取消。", repo_root=self.root)
                )
                return
            if self.apply:
                result = ru.apply_fast_forward_update(
                    self.root, mirror_templates=self.mirrors
                )
            else:
                result = ru.check_for_updates(self.root, mirror_templates=self.mirrors)
        except Exception as e:
            try:
                ru = _load_repo_updater()
                result = ru.UpdateCheckResult(
                    False, f"更新检查异常：{e}", repo_root=self.root
                )
            except Exception:
                result = type(
                    "R",
                    (),
                    {
                        "ok": False,
                        "message": f"更新检查异常：{e}",
                        "behind": 0,
                        "dirty": False,
                        "can_update": False,
                        "updated": False,
                        "fetch_url": "",
                    },
                )()
        if not self.isInterruptionRequested():
            self.finished_ok.emit(result)


class _RuntimeIndexWorker(QThread):
    """本地缺股票池/板块索引/日线近窗时从 COS 拉取底稿，不覆盖已有完整目录。"""

    finished_ok = pyqtSignal(object)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.root = root

    def run(self) -> None:
        try:
            boot = _load_py_from_disk(
                "utils/runtime_index_bootstrap.py", "ant_runtime_index_bootstrap"
            )
            if not boot.missing_runtime(self.root):
                self.finished_ok.emit({"fetched": [], "skipped": True, "errors": {}})
                return
            result = boot.fetch_missing(
                self.root,
                should_abort=self.isInterruptionRequested,
                include_daily_cache=True,
            )
            self.finished_ok.emit(result)
        except Exception as e:
            self.finished_ok.emit({"fetched": [], "errors": {"_": str(e)}, "aborted": False})


class _QmtSetupDialog(QDialog):
    """新机补齐大 QMT python 目录与资金账号。"""

    def __init__(self, parent, *, detect, installs, missing):
        super().__init__(parent)
        self._detect = detect
        self._installs = list(installs or [])
        self.setWindowTitle("配置大 QMT")
        self.setMinimumWidth(560)
        self.setModal(True)

        n_inst = len(self._installs)
        if "qmt_python_dir" in (missing or ()) and n_inst == 0:
            py_hint = "未找到大 QMT。请先安装「国金证券 QMT 交易端」，或手动选择其 python 目录。"
        elif n_inst == 1:
            py_hint = "已检测到 1 个大 QMT 安装，请确认 python 目录。"
        else:
            py_hint = "检测到多个 QMT 安装，请选择正在使用的大 QMT（投研/交易端）python 目录。"

        n_acc0 = 0
        if self._installs:
            n_acc0 = len(detect.discover_accounts(self._installs[0].get("root") or ""))
        if "account_id" in (missing or ()) and n_inst == 0:
            acc_hint = "找到安装后再自动列出资金账号；也可直接填写。"
        elif n_acc0 == 1:
            acc_hint = "已检测到 1 个资金账号，请确认。"
        elif n_acc0 > 1:
            acc_hint = "该安装下有多个资金账号，请选择（按最近使用排序）。"
        else:
            acc_hint = "该目录下未找到资金账号。请先在大 QMT 登录一次，或手动填写。"

        layout = QVBoxLayout(self)
        tip = QLabel(
            "首次使用需要指定大 QMT 的 python 目录和资金账号，无需改 config.ini。\n"
            + py_hint
            + "\n"
            + acc_hint
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        layout.addWidget(QLabel("大 QMT python 目录"))
        py_row = QHBoxLayout()
        self.py_combo = QComboBox()
        self.py_combo.setEditable(True)
        self.py_combo.setMinimumWidth(360)
        for it in self._installs:
            py = str(it.get("python_dir") or "")
            self.py_combo.addItem("%s  —  %s" % (it.get("label") or py, py), py)
        if n_inst == 1:
            self.py_combo.setCurrentIndex(0)
        py_row.addWidget(self.py_combo, 1)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse_python_dir)
        py_row.addWidget(browse)
        layout.addLayout(py_row)

        layout.addWidget(QLabel("资金账号"))
        self.acc_combo = QComboBox()
        self.acc_combo.setEditable(True)
        layout.addWidget(self.acc_combo)
        self.py_combo.currentTextChanged.connect(self._reload_accounts)
        self._reload_accounts()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("稍后再说")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _python_dir_text(self) -> str:
        raw = str(self.py_combo.currentText() or "").strip()
        if "  —  " in raw:
            raw = raw.split("  —  ", 1)[-1].strip()
        if raw and os.path.isdir(raw):
            return raw
        data = self.py_combo.currentData()
        if data:
            return str(data).strip()
        return raw

    def _browse_python_dir(self) -> None:
        start = self._python_dir_text() or "D:/"
        chosen = QFileDialog.getExistingDirectory(self, "选择大 QMT 的 python 目录", start)
        if chosen:
            self.py_combo.setCurrentText(chosen)
            self._reload_accounts()

    def _reload_accounts(self, *_args) -> None:
        py = self._python_dir_text()
        root = ""
        try:
            root = self._detect.install_root_from_python_dir(py)
        except Exception:
            root = ""
        prev = str(self.acc_combo.currentText() or "").strip()
        self.acc_combo.blockSignals(True)
        self.acc_combo.clear()
        accounts = []
        if root:
            try:
                accounts = list(self._detect.discover_accounts(root) or [])
            except Exception:
                accounts = []
        for acc in accounts:
            aid = str(acc.get("account_id") or "")
            self.acc_combo.addItem(aid, aid)
        if accounts:
            self.acc_combo.setCurrentIndex(0)
        elif prev:
            self.acc_combo.setEditText(prev)
        self.acc_combo.blockSignals(False)

    def _on_ok(self) -> None:
        py = self._python_dir_text()
        acc = str(self.acc_combo.currentText() or "").strip()
        if not py or not os.path.isdir(py):
            QMessageBox.warning(
                self,
                "目录无效",
                "请选择大 QMT 安装目录下的 python 文件夹。\n找不到时请先安装并打开一次国金证券 QMT 交易端。",
            )
            return
        if not acc:
            QMessageBox.warning(
                self,
                "缺少资金账号",
                "请选择或填写资金账号。\n可在大 QMT 登录一次后再打开启动器自动识别。",
            )
            return
        self._result = {"python_dir": py, "account_id": acc}
        self.accept()

    def result_values(self) -> dict:
        return dict(getattr(self, "_result", {}) or {})


class AntLauncherWindow(QMainWindow):
    """蚂蚁量化系统启动器：统一入口，启动三个子系统。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        ver = _display_version()
        self.setWindowTitle(
            f"蚂蚁量化系统启动器 {ver}".strip()
            if ver
            else "蚂蚁量化系统启动器"
        )
        self._post_market_settings = self._load_post_market_settings()
        self._update_worker = None
        self._bootstrap_worker = None
        self._setup_icon()
        self._setup_ui()
        self._arm_post_market_watch()
        self._ensure_app_config_ini()
        head = _git_head_short(self._project_root())
        self._append_launcher_log(
            "launcher start: version=%s head=%s root=%s"
            % (_display_version() or "?", head or "?", self._project_root())
        )
        QTimer.singleShot(0, self._start_runtime_index_bootstrap)

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
                "order": 40,
                "enabled": True,
            },
            {
                "id": "qmt_status_monitor",
                "name": "大QMT状态监控器",
                "script": "tools/qmt_status_monitor.py",
                "category": "tool",
                "description": "大 QMT 在线状态与活动监控（可开关盘后分时暂停、日线回填、量能待跑）",
                "order": 45,
                "enabled": True,
            },
            {
                "id": "tick_viewer",
                "name": "分时/Tick数据查看器",
                "script": "tools/tick_viewer.py",
                "category": "tool",
                "description": "本地分时/Tick 缓存只读查看",
                "order": 50,
                "enabled": True,
            },
            {
                "id": "post_market_batch_manual",
                "name": "盘后批跑",
                "script": "run_all_if_trading_day_gui.py",
                "category": "tool",
                "description": "手动盘后批跑；可配置启动器 17:31 补启动用的 bat 路径。"
                "有 data/qmt_live_only.flag 时自动仅实盘步骤；否则全量。"
                "目标日按15:00规则，先看齐全再重跑",
                "order": 70,
                "enabled": True,
            },
        ]
        # 启动器不再展示的旧监控入口（即便 json 里还留着也过滤掉）
        hidden_ids = {
            "ma10_regime_monitor",
            "zt12_strict_bull_monitor",
            "em_hot_clip_monitor",
            "ma_zong_meet_monitor",
            "bb_pctb_pullback_monitor",
        }
        if not os.path.isfile(cfg_path):
            return sorted(
                [
                    a
                    for a in default_apps
                    if a.get("enabled", True)
                    and str(a.get("id") or "") not in hidden_ids
                ],
                key=lambda x: x.get("order", 0),
            )

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                return sorted(
                    [
                        a
                        for a in default_apps
                        if a.get("enabled", True)
                        and str(a.get("id") or "") not in hidden_ids
                    ],
                    key=lambda x: x.get("order", 0),
                )
            apps = []
            seen = set()
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if item.get("enabled", True) is False:
                    continue
                if item.get("category") != "tool":
                    continue
                aid = str(item.get("id") or "")
                if aid in hidden_ids:
                    continue
                apps.append(item)
                if aid:
                    seen.add(aid)
            # 配置文件缺项时补上源码默认（避免加了新工具但旧 json 看不到）
            for item in default_apps:
                if item.get("enabled", True) is False:
                    continue
                if item.get("category") != "tool":
                    continue
                aid = str(item.get("id") or "")
                if aid in hidden_ids:
                    continue
                if aid and aid not in seen:
                    apps.append(item)
            apps.sort(key=lambda x: x.get("order", 0))
            return apps
        except Exception:
            return sorted(
                [
                    a
                    for a in default_apps
                    if a.get("enabled", True)
                    and str(a.get("id") or "") not in hidden_ids
                ],
                key=lambda x: x.get("order", 0),
            )

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
        self.resize(1040, 640)
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
        hint_label = QLabel(
            "提示：启动子系统后可以把本窗口最小化。"
            "每天 17:31 会按「盘后批跑」里配置的 bat 补启动（关掉本窗口则不会）。",
            self,
        )
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
        # 末尾追加「检查更新」（非脚本入口）
        n_tools = len(tool_apps) + 1
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

        # 「检查更新」放进常用小工具
        self.btn_check_update = QPushButton("检查更新", self)
        self.btn_check_update.setStyleSheet(tool_btn_style)
        self.btn_check_update.setToolTip(
            "从 GitHub / 国内镜像检查并快进更新本机代码。\n"
            "仅当本地改动与远程更新文件重叠时才会禁止自动更新。"
        )
        self.btn_check_update.clicked.connect(
            lambda: self._start_update_check(silent_if_latest=False)
        )
        upd_idx = len(tool_apps)
        if single_row:
            grid.addWidget(self.btn_check_update, 0, upd_idx + col_offset)
        else:
            grid.addWidget(
                self.btn_check_update, upd_idx // max_cols, upd_idx % max_cols
            )

        scroll.setWidget(tools_container)
        main_layout.addWidget(scroll, 1)

        self.update_status = QLabel("", self)
        self.update_status.setStyleSheet("color: #666666;")
        self.update_status.setWordWrap(True)
        self.update_status.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.update_status.hide()
        main_layout.addWidget(self.update_status)

    def _project_root(self) -> str:
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable or "")
        return os.path.dirname(os.path.abspath(__file__))

    def _post_market_settings_path(self) -> str:
        return os.path.join(self._project_root(), "data", "launcher_settings.json")

    def _load_post_market_settings(self) -> dict:
        path = self._post_market_settings_path()
        data = {
            "post_market_bat": _DEFAULT_POST_MARKET_BAT,
            "post_market_watch_date": "",
            "auto_update_check_on_start": True,
            "update_mirrors": list(_DEFAULT_UPDATE_MIRRORS),
        }
        if not os.path.isfile(path):
            return data
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return data
        if isinstance(raw, dict):
            bat = str(raw.get("post_market_bat") or "").strip()
            if bat:
                data["post_market_bat"] = bat
            data["post_market_watch_date"] = str(raw.get("post_market_watch_date") or "").strip()
            if "auto_update_check_on_start" in raw:
                data["auto_update_check_on_start"] = bool(raw.get("auto_update_check_on_start"))
            mirrors = raw.get("update_mirrors")
            if isinstance(mirrors, list) and mirrors:
                data["update_mirrors"] = [str(x).strip() for x in mirrors if str(x).strip()]
        return data

    def _save_post_market_settings(self) -> None:
        path = self._post_market_settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = dict(self._post_market_settings)
        payload.setdefault("auto_update_check_on_start", True)
        payload.setdefault("update_mirrors", list(_DEFAULT_UPDATE_MIRRORS))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _update_mirrors(self) -> list:
        mirrors = self._post_market_settings.get("update_mirrors")
        if isinstance(mirrors, list) and mirrors:
            return [str(x).strip() for x in mirrors if str(x).strip()]
        return list(_DEFAULT_UPDATE_MIRRORS)

    def _ensure_app_config_ini(self) -> None:
        try:
            mod = _load_py_from_disk("utils/app_config.py", "ant_app_config")
            path, created, added = mod.ensure_app_config_ini(self._project_root())
            if created:
                self._append_launcher_log("config.ini: created %s" % path)
            elif added:
                self._append_launcher_log("config.ini: filled %s" % ",".join(added))
        except Exception as e:
            self._append_launcher_log("config.ini: ensure failed %s" % e)

    def _maybe_prompt_qmt_setup(self) -> None:
        try:
            cfg = _load_py_from_disk("utils/app_config.py", "ant_app_config")
            missing = cfg.missing_qmt_setup(self._project_root())
        except Exception as e:
            self._append_launcher_log("qmt setup: check failed %s" % e)
            return
        if not missing:
            return
        try:
            detect = _load_py_from_disk("utils/qmt_detect.py", "ant_qmt_detect")
            installs = detect.discover_qmt_installs()
        except Exception as e:
            self._append_launcher_log("qmt setup: detect failed %s" % e)

            class _EmptyDetect:
                def discover_accounts(self, root):
                    return []

                def install_root_from_python_dir(self, py):
                    return ""

            detect = _EmptyDetect()
            installs = []
        dlg = _QmtSetupDialog(
            self, detect=detect, installs=installs, missing=missing
        )
        if dlg.exec_() != QDialog.Accepted:
            self._append_launcher_log("qmt setup: skipped")
            return
        vals = dlg.result_values()
        py = str(vals.get("python_dir") or "").strip()
        acc = str(vals.get("account_id") or "").strip()
        try:
            cfg.update_config_values(
                {
                    ("Account", "account_id"): acc,
                    ("Account", "qmt_mode"): "builtin",
                    ("qmt_builtin", "qmt_python_dir"): py.replace("\\", "/"),
                },
                self._project_root(),
            )
            self._append_launcher_log("qmt setup: saved account=%s python=%s" % (acc, py))
            self._set_update_status("已保存大 QMT 目录与资金账号。", auto_clear_ms=4000)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", "写入配置失败：%s" % e)

    def _start_runtime_index_bootstrap(self) -> None:
        if self._bootstrap_worker is not None and self._bootstrap_worker.isRunning():
            return
        try:
            boot = _load_py_from_disk(
                "utils/runtime_index_bootstrap.py", "ant_runtime_index_bootstrap"
            )
            needed = boot.missing_runtime(self._project_root())
            paths = boot.dest_paths(self._project_root())
        except Exception as e:
            self._append_launcher_log("runtime index: check failed %s" % e)
            self._after_runtime_index_bootstrap()
            return
        if not needed:
            sizes = []
            for name, p in paths.items():
                try:
                    sizes.append("%s=%s" % (name, os.path.getsize(p)))
                except OSError:
                    sizes.append("%s=missing" % name)
            try:
                sizes.append("daily_cache_csv=%s" % boot.count_daily_cache_csv(self._project_root()))
            except Exception:
                sizes.append("daily_cache_csv=?")
            self._append_launcher_log(
                "runtime index: skip (already present) %s" % "; ".join(sizes)
            )
            self._after_runtime_index_bootstrap()
            return
        first = needed[0]
        if first == getattr(boot, "DAILY_CACHE_ZIP_NAME", "daily_cache.zip"):
            dest_hint = boot.daily_cache_dir(self._project_root())
        else:
            dest_hint = os.path.dirname(paths[first])
        self._append_launcher_log(
            "runtime index: need %s dest=%s url0=%s"
            % (
                ",".join(needed),
                dest_hint,
                boot.public_object_url(first),
            )
        )
        self._set_update_status("正在从云端获取股票池、板块索引或日线缓存…")
        worker = _RuntimeIndexWorker(self._project_root(), parent=self)
        self._bootstrap_worker = worker
        worker.finished_ok.connect(self._on_runtime_index_bootstrap_done)
        worker.start()

    def _on_runtime_index_bootstrap_done(self, result) -> None:
        self._bootstrap_worker = None
        try:
            boot = _load_py_from_disk(
                "utils/runtime_index_bootstrap.py", "ant_runtime_index_bootstrap"
            )
            ok, tip = boot.summarize_fetch(result if isinstance(result, dict) else {})
        except Exception as e:
            ok, tip = False, "未能从云端获取板块索引（%s）。" % e
        fetched = list((result or {}).get("fetched") or []) if isinstance(result, dict) else []
        errors = dict((result or {}).get("errors") or {}) if isinstance(result, dict) else {}
        self._append_launcher_log(
            "runtime index: fetched=%s skipped=%s errors=%s aborted=%s"
            % (
                fetched,
                list((result or {}).get("skipped") or []) if isinstance(result, dict) else [],
                errors,
                bool((result or {}).get("aborted")) if isinstance(result, dict) else False,
            )
        )
        if tip:
            self._set_update_status(tip, auto_clear_ms=4000 if ok else 6000)
        self._after_runtime_index_bootstrap()

    def _after_runtime_index_bootstrap(self) -> None:
        self._maybe_prompt_qmt_setup()
        if self._post_market_settings.get("auto_update_check_on_start", True):
            QTimer.singleShot(800, lambda: self._start_update_check(silent_if_latest=True))

    def _set_update_status(self, text: str, *, auto_clear_ms: int = 0) -> None:
        """启动器底部短提示；auto_clear_ms>0 时到期自动清空。"""
        lbl = getattr(self, "update_status", None)
        if lbl is None:
            return
        timer = getattr(self, "_update_status_clear_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            self._update_status_clear_timer = None
        msg = (text or "").strip()
        lbl.setText(msg)
        lbl.setVisible(bool(msg))
        if msg and auto_clear_ms > 0:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._clear_update_status)
            t.start(int(auto_clear_ms))
            self._update_status_clear_timer = t

    def _clear_update_status(self) -> None:
        self._set_update_status("")

    def _close_update_progress(self) -> None:
        prog = getattr(self, "_update_progress", None)
        if prog is not None:
            try:
                prog.close()
            except Exception:
                pass
            self._update_progress = None

    def _show_update_progress(self, text: str) -> None:
        self._close_update_progress()
        prog = QProgressDialog(text, None, 0, 0, self)
        prog.setWindowTitle("检查更新")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setCancelButton(None)
        prog.show()
        QApplication.processEvents()
        self._update_progress = prog

    def _start_update_check(self, *, silent_if_latest: bool = False) -> None:
        if self._update_worker is not None and self._update_worker.isRunning():
            if silent_if_latest:
                self._set_update_status("正在检查更新…")
            return
        self.btn_check_update.setEnabled(False)
        if silent_if_latest:
            # 启动自动检查：底部短暂提示
            self._set_update_status("正在检查更新…")
        else:
            # 手动检查：进度弹窗，结果也用弹窗，不在启动器上常驻文案
            self._clear_update_status()
            self._show_update_progress("正在通过 GitHub/国内镜像检查更新…")
        worker = _UpdateWorker(
            self._project_root(),
            self._update_mirrors(),
            apply=False,
            parent=self,
        )
        self._update_worker = worker
        worker.finished_ok.connect(
            lambda r: self._on_update_check_done(r, silent_if_latest=silent_if_latest)
        )
        worker.start()

    def _on_update_check_done(self, result, *, silent_if_latest: bool = False) -> None:
        self.btn_check_update.setEnabled(True)
        self._update_worker = None
        self._close_update_progress()

        ver_hint = _version_hint()
        ver = _display_version()

        if result is None:
            if silent_if_latest:
                self._set_update_status("更新检查无结果。", auto_clear_ms=2000)
            else:
                self._clear_update_status()
                msg = "更新检查无结果。"
                if ver_hint:
                    msg = f"{msg}\n\n{ver_hint}"
                QMessageBox.warning(self, "检查更新", msg)
            return

        self._append_launcher_log(
            f"update check: ok={result.ok} behind={result.behind} "
            f"dirty={result.dirty} via={result.fetch_url} msg={result.message}"
        )

        if not result.ok:
            if silent_if_latest:
                self._set_update_status(
                    result.message.split("\n")[0], auto_clear_ms=2000
                )
            else:
                self._clear_update_status()
                msg = result.message
                if ver_hint:
                    msg = f"{msg}\n\n{ver_hint}"
                QMessageBox.warning(self, "检查更新", msg)
            return

        if result.behind <= 0:
            sha = (getattr(result, "local_sha", None) or "")[:10]
            if silent_if_latest:
                bits = [x for x in (ver, sha) if x]
                tip = (
                    "代码已是最新（%s）。" % " ".join(bits)
                    if bits
                    else "代码已是最新。"
                )
                self._set_update_status(tip, auto_clear_ms=4000)
            else:
                self._clear_update_status()
                msg = result.message
                extra = []
                if ver_hint:
                    extra.append(ver_hint)
                if sha:
                    extra.append("本地提交：%s" % sha)
                if extra:
                    msg = f"{msg}\n\n" + "\n".join(extra)
                QMessageBox.information(self, "检查更新", msg)
            return

        # 有新版本：无论自动/手动，都用弹窗确认（手动不写底部状态）
        summary = result.message
        if result.fetch_url:
            summary += f"\n通道：{result.fetch_url}"
        if ver_hint:
            summary += f"\n{ver_hint}"
        if silent_if_latest:
            self._set_update_status(
                f"发现 {result.behind} 个新提交"
                + ("（本地有改动）" if result.dirty else ""),
                auto_clear_ms=2000,
            )
        else:
            self._clear_update_status()

        # 仅当 can_update=False（分叉 / 脏文件与远程重叠等）才禁止；
        # 本地有改动但与本次更新无重叠时仍可快进。
        if not result.can_update:
            QMessageBox.warning(
                self,
                "发现更新但未自动拉取",
                summary
                + "\n\n处理完冲突的本地改动后，再点「检查更新」即可。",
            )
            return

        reply = QMessageBox.question(
            self,
            "发现新版本",
            summary + "\n\n是否立即快进更新到最新？\n（不会覆盖与本次更新冲突的本地修改）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            if silent_if_latest:
                self._set_update_status(
                    f"有 {result.behind} 个新提交，已跳过更新。",
                    auto_clear_ms=2000,
                )
            return
        self._start_apply_update(from_manual=not silent_if_latest)

    def _start_apply_update(self, *, from_manual: bool = True) -> None:
        if self._update_worker is not None and self._update_worker.isRunning():
            return
        self.btn_check_update.setEnabled(False)
        if from_manual:
            self._clear_update_status()
            self._show_update_progress("正在拉取更新…")
        else:
            self._set_update_status("正在拉取更新…")
        worker = _UpdateWorker(
            self._project_root(),
            self._update_mirrors(),
            apply=True,
            parent=self,
        )
        self._update_worker = worker
        worker.finished_ok.connect(
            lambda r: self._on_apply_update_done(r, from_manual=from_manual)
        )
        worker.start()

    def _on_apply_update_done(self, result, *, from_manual: bool = True) -> None:
        self.btn_check_update.setEnabled(True)
        self._update_worker = None
        self._close_update_progress()
        if result is None:
            if from_manual:
                self._clear_update_status()
                QMessageBox.warning(self, "更新未完成", "更新失败：无结果。")
            else:
                self._set_update_status("更新失败：无结果。", auto_clear_ms=2000)
            return
        self._append_launcher_log(f"update apply: {result.message}")
        if getattr(result, "updated", False):
            try:
                self.setWindowTitle(
                    f"蚂蚁量化系统启动器 {_display_version()}".strip()
                )
            except Exception:
                pass
            if from_manual:
                self._clear_update_status()
            else:
                self._set_update_status(
                    "更新完成，正在补齐股票池/板块索引…", auto_clear_ms=4000
                )
            QMessageBox.information(self, "更新完成", result.message)
            # 拉完代码立刻用磁盘上的新模块再下 COS，不必等重启启动器
            QTimer.singleShot(0, self._start_runtime_index_bootstrap)
            return
        if result.ok and result.behind <= 0:
            if from_manual:
                self._clear_update_status()
            else:
                self._set_update_status("代码已是最新。", auto_clear_ms=2000)
            QMessageBox.information(self, "检查更新", result.message)
            return
        if from_manual:
            self._clear_update_status()
        else:
            self._set_update_status(
                result.message.split("\n")[0], auto_clear_ms=2000
            )
        QMessageBox.warning(self, "更新未完成", result.message)

    def _set_bat_watch_status(self, text: str) -> None:
        # 盘后 bat 配置已挪到「盘后批跑」；启动器仅写日志，避免占界面
        msg = (text or "").strip()
        if msg:
            self._append_launcher_log(f"post-market watch: {msg}")

    def closeEvent(self, event) -> None:
        """退出前停掉定时器与更新线程，便于干净退出。"""
        try:
            if getattr(self, "_watch_timer", None) is not None:
                self._watch_timer.stop()
                self._watch_timer.deleteLater()
                self._watch_timer = None
        except Exception:
            pass
        for attr in ("_bootstrap_worker", "_update_worker"):
            worker = getattr(self, attr, None)
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    worker.requestInterruption()
                    if not worker.wait(3000):
                        worker.terminate()
                        worker.wait(1500)
            except Exception:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass
            setattr(self, attr, None)
        try:
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
        except Exception:
            pass
        try:
            super().closeEvent(event)
        except Exception:
            event.accept()

    def _current_bat_path(self) -> str:
        # 每次从磁盘重读，便于在「盘后批跑」改路径后启动器立刻用上
        try:
            self._post_market_settings = self._load_post_market_settings()
        except Exception:
            pass
        return str(self._post_market_settings.get("post_market_bat") or "").strip()

    def _arm_post_market_watch(self) -> None:
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(30_000)
        self._watch_timer.timeout.connect(self._maybe_start_post_market_bat)
        self._watch_timer.start()
        QTimer.singleShot(2000, self._maybe_start_post_market_bat)

    def _watch_already_done_today(self) -> bool:
        return self._post_market_settings.get("post_market_watch_date") == date.today().isoformat()

    def _mark_watch_done_today(self) -> None:
        bat = self._current_bat_path()
        self._post_market_settings["post_market_watch_date"] = date.today().isoformat()
        if bat:
            self._post_market_settings["post_market_bat"] = bat
        self._save_post_market_settings()

    def _append_launcher_log(self, text: str) -> None:
        try:
            logs_dir = os.path.join(self._project_root(), "logs")
            os.makedirs(logs_dir, exist_ok=True)
            path = os.path.join(logs_dir, "launcher_run.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
        except Exception:
            pass

    def _post_market_already_running(self, bat_path: str) -> bool:
        import psutil

        markers = [
            "run_all_if_trading_day_gui.py",
            "run_all_if_trading_day_launch.py",
            "run_all_if_trading_day.py",
        ]
        base = os.path.basename(bat_path).lower()
        if base:
            markers.append(base)
        for proc in psutil.process_iter(["cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
            except (psutil.Error, OSError):
                continue
            low = cmd.lower()
            if any(m.lower() in low for m in markers):
                return True
        return False

    def _maybe_start_post_market_bat(self) -> None:
        now = datetime.now()
        if now.time() < _POST_MARKET_WATCH_AT:
            return
        # 重读设置（含今日是否已补启动、bat 路径）
        try:
            self._post_market_settings = self._load_post_market_settings()
        except Exception:
            pass
        if self._watch_already_done_today():
            return
        if now.time() > dt_time(21, 0):
            self._set_bat_watch_status("已过 21:00，今日不再自动补启动。需要的话用「盘后批跑」手动开。")
            self._mark_watch_done_today()
            return
        bat_path = self._current_bat_path()
        if not bat_path or not os.path.isfile(bat_path):
            self._set_bat_watch_status(f"17:31 未补启动：找不到 bat {bat_path or '（路径为空）'}")
            if not getattr(self, "_missing_bat_logged", False):
                self._missing_bat_logged = True
            return
        try:
            running = self._post_market_already_running(bat_path)
        except Exception as exc:
            self._set_bat_watch_status(f"17:31 未能检查盘后进程：{exc}")
            return
        if running:
            self._set_bat_watch_status("17:31 盘后批跑已在运行，未重复启动。")
            self._mark_watch_done_today()
            return
        try:
            os.startfile(bat_path)
        except Exception as exc:
            self._set_bat_watch_status(f"17:31 启动失败：{exc}")
            return
        self._set_bat_watch_status(f"17:31 定时任务未启动，已补启动：{bat_path}")
        self._mark_watch_done_today()

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
            QMessageBox.warning(self, "启动失败", f"脚本不存在：\n{script_path}")
            return
        try:
            popen_kwargs = {"cwd": root_dir}
            # Windows 下不显示控制台窗口
            if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            env = os.environ.copy()
            # 子进程 stdout 写到日志文件，默认 GBK 遇到 ✓ 等字符会直接退出，窗口起不来
            env.setdefault("PYTHONUTF8", "1")
            env.setdefault("PYTHONIOENCODING", "utf-8")
            popen_kwargs["env"] = env

            logs_dir = os.path.join(root_dir, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            log_path = os.path.join(logs_dir, "launcher_run.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n=== Launch {script_rel_path} with {python_exe} ===\n")
                popen_kwargs["stdout"] = f
                popen_kwargs["stderr"] = f
                subprocess.Popen([python_exe, script_path], **popen_kwargs)
        except Exception as exc:
            QMessageBox.warning(self, "启动失败", f"{script_rel_path}\n\n{exc}")

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
    code = app.exec_()
    try:
        window.deleteLater()
        app.processEvents()
    except Exception:
        pass
    sys.exit(code)


if __name__ == "__main__":
    main()

