import configparser
import os
import sys

from PyQt5.QtWidgets import QMessageBox

from utils.app_config import config_ini_path, ensure_app_config_ini
from utils.qmt_execution_config import get_qmt_mode, requires_path_qmt

class Config:
    def __init__(self):
        self._config = configparser.ConfigParser()
        self.load_config()
        
    def load_config(self):
        """加载配置文件"""
        config_path, _, _ = ensure_app_config_ini()
        self._config.read(config_path, encoding="utf-8")
            
        # 检查配置是否有效
        account_config = self._config['Account']
        account_id = str(account_config.get('account_id', '') or '').strip()
        path_qmt = str(account_config.get('path_qmt', '') or '').strip()
        missing = []
        if not account_id:
            missing.append('account_id')
        if requires_path_qmt() and not path_qmt:
            missing.append('path_qmt')
        if missing:
            mode = get_qmt_mode()
            hint = (
                f"请先填写配置文件\n{config_path}\n\n"
                f"缺少: {', '.join(missing)}\n"
                f"当前 qmt_mode={mode}"
            )
            if mode in ('builtin', 'standalone'):
                hint += (
                    "\n\nbuiltin 模式下 path_qmt 可留空，"
                    "资金/持仓/现价由大 QMT 内置策略写入 data/results.json。"
                    "\n也可关掉本窗口后重新打开启动器，按提示选择目录和账号。"
                )
            QMessageBox.warning(None, "警告", hint)
            sys.exit(1)

    def save_config(self):
        """保存配置到文件"""
        config_path = config_ini_path()
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            self._config.write(f)
    
    def __getitem__(self, section):
        """支持使用 config['section'] 的方式访问配置"""
        return self._config[section]
    
    def __setitem__(self, section, value):
        """支持使用 config['section'] = {...} 的方式设置配置"""
        self._config[section] = value