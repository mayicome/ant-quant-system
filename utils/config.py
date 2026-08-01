import configparser
import os
import sys

from PyQt5.QtWidgets import QMessageBox

from utils.qmt_execution_config import get_qmt_mode, requires_path_qmt

class Config:
    def __init__(self):
        self._config = configparser.ConfigParser()
        self.load_config()
        
    def load_config(self):
        """加载配置文件"""
        #config_path为当前目录的上一级目录的data子目录的config.ini文件
        #如果data子目录不存在，则创建data子目录
        data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        config_path = os.path.join(data_dir, 'config.ini')
        #重新获取config_path的绝对路径
        config_path = os.path.abspath(config_path)
        if not os.path.exists(config_path):
            # 如果配置文件不存在，创建默认配置
            self._config['Account'] = {
                'path_qmt': '',
                'account_id': ''
            }
            # 保存默认配置到文件
            self.save_config()
        else:
            self._config.read(config_path, encoding='utf-8')
            
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
                )
            QMessageBox.warning(None, "警告", hint)
            sys.exit(1)

    def save_config(self):
        """保存配置到文件"""
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config.ini')
        with open(config_path, 'w', encoding='utf-8') as f:
            self._config.write(f)
    
    def __getitem__(self, section):
        """支持使用 config['section'] 的方式访问配置"""
        return self._config[section]
    
    def __setitem__(self, section, value):
        """支持使用 config['section'] = {...} 的方式设置配置"""
        self._config[section] = value