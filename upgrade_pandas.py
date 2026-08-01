#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""升级pandas到2.2.3版本"""

import subprocess
import sys

def upgrade_pandas():
    """升级pandas到指定版本"""
    print("正在升级pandas到2.2.3版本...")
    try:
        # 执行pip升级命令
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pandas==2.2.3", "--upgrade"],
            check=True,
            capture_output=True,
            text=True
        )
        print(result.stdout)
        print("\n升级成功！")
        
        # 验证版本
        import pandas as pd
        print(f"当前pandas版本: {pd.__version__}")
        
    except subprocess.CalledProcessError as e:
        print(f"升级失败: {e}")
        print(f"错误信息: {e.stderr}")
        sys.exit(1)
    except Exception as e:
        print(f"发生错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    upgrade_pandas()

