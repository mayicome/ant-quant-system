#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内网穿透解决方案
使用ngrok或类似工具实现外网访问
"""

import subprocess
import os
import time

def check_ngrok():
    """检查ngrok是否安装"""
    try:
        result = subprocess.run(['ngrok', 'version'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ ngrok已安装")
            return True
        else:
            print("❌ ngrok未安装")
            return False
    except FileNotFoundError:
        print("❌ ngrok未安装")
        return False

def install_ngrok_guide():
    """ngrok安装指南"""
    print("\n" + "=" * 50)
    print("ngrok安装指南")
    print("=" * 50)
    
    print("📋 安装步骤:")
    print("1. 访问: https://ngrok.com/")
    print("2. 注册账号并获取authtoken")
    print("3. 下载ngrok")
    print("4. 解压到任意目录")
    print("5. 运行: ngrok config add-authtoken YOUR_TOKEN")
    print("6. 运行: ngrok http 8080")
    print()
    print("💡 优势:")
    print("- 无需配置路由器")
    print("- 自动获得公网域名")
    print("- 支持HTTPS")
    print("- 简单易用")

def create_ngrok_script():
    """创建ngrok启动脚本"""
    script_content = '''@echo off
echo 启动ngrok内网穿透...
echo 请确保Web应用正在运行在8080端口
echo.
ngrok http 8080
pause
'''
    
    with open('start_ngrok.bat', 'w', encoding='utf-8') as f:
        f.write(script_content)
    
    print("✅ 已创建ngrok启动脚本: start_ngrok.bat")

def alternative_solutions():
    """其他解决方案"""
    print("\n" + "=" * 50)
    print("其他解决方案")
    print("=" * 50)
    
    print("🔧 方案1: 使用frp")
    print("- 需要一台有公网IP的服务器")
    print("- 配置相对复杂")
    print("- 免费但需要服务器")
    print()
    
    print("🔧 方案2: 使用花生壳")
    print("- 国内内网穿透服务")
    print("- 有免费版本")
    print("- 配置简单")
    print()
    
    print("🔧 方案3: 使用TeamViewer")
    print("- 远程桌面方式访问")
    print("- 需要安装TeamViewer")
    print("- 不是Web访问方式")
    print()
    
    print("🔧 方案4: 云服务器部署")
    print("- 租用云服务器")
    print("- 将Web应用部署到云端")
    print("- 获得稳定的公网访问")

def main():
    """主函数"""
    print("=" * 60)
    print("内网穿透解决方案")
    print("=" * 60)
    
    print("🎯 当前问题: 路由器端口转发不生效")
    print("💡 推荐解决方案: 使用内网穿透工具")
    print()
    
    # 检查ngrok
    if check_ngrok():
        print("\n🚀 ngrok已就绪，可以直接使用")
        print("运行命令: ngrok http 8080")
        print("然后使用ngrok提供的公网地址")
    else:
        install_ngrok_guide()
        create_ngrok_script()
    
    alternative_solutions()
    
    print("\n" + "=" * 60)
    print("推荐操作步骤")
    print("=" * 60)
    print("1. 安装ngrok (最简单)")
    print("2. 在服务器端运行: ngrok http 8080")
    print("3. 复制ngrok提供的公网地址")
    print("4. 生成新的二维码")
    print("5. 分享给外网用户")

if __name__ == "__main__":
    main()
