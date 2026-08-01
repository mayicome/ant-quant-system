#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键价格计算Web应用启动脚本
"""

import os
import sys
import subprocess

def check_dependencies():
    """检查依赖是否安装"""
    try:
        import flask
        print("✓ Flask已安装")
        return True
    except ImportError:
        print("✗ Flask未安装")
        return False

def install_dependencies():
    """安装依赖"""
    print("正在安装依赖...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✓ 依赖安装完成")
        return True
    except subprocess.CalledProcessError:
        print("✗ 依赖安装失败")
        return False

def main():
    """主函数"""
    print("=" * 50)
    print("关键价格计算Web应用")
    print("=" * 50)
    
    # 检查依赖
    if not check_dependencies():
        print("\n正在安装依赖...")
        if not install_dependencies():
            print("依赖安装失败，请手动运行: pip install -r requirements.txt")
            return
    
    # 检查项目文件
    if not os.path.exists("web_app.py"):
        print("✗ 找不到web_app.py文件")
        return
    
    if not os.path.exists("templates/index.html"):
        print("✗ 找不到templates/index.html文件")
        return
    
    print("\n✓ 所有文件检查完成")
    print("\n启动Web应用...")
    print("访问地址: http://localhost:5000")
    print("按 Ctrl+C 停止服务")
    print("=" * 50)
    
    # 启动应用
    try:
        import web_app
    except KeyboardInterrupt:
        print("\n\n服务已停止")
    except Exception as e:
        print(f"\n启动失败: {e}")

if __name__ == "__main__":
    main()
