#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web应用外网访问诊断工具
"""

import socket
import requests
import subprocess
import sys

def check_port_binding():
    """检查端口绑定"""
    print("🔍 检查端口5000绑定状态...")
    try:
        # 检查是否有进程监听5000端口
        result = subprocess.run(['netstat', '-an'], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        
        port_5000_found = False
        for line in lines:
            if ':5000' in line and 'LISTENING' in line:
                print(f"✅ 发现端口5000监听: {line.strip()}")
                port_5000_found = True
                
                # 检查是否绑定到0.0.0.0
                if '0.0.0.0:5000' in line:
                    print("✅ 端口已绑定到0.0.0.0 (允许外网访问)")
                elif '127.0.0.1:5000' in line:
                    print("❌ 端口只绑定到127.0.0.1 (仅本地访问)")
                elif '192.168.' in line:
                    print("⚠️  端口绑定到内网IP (可能限制外网访问)")
        
        if not port_5000_found:
            print("❌ 未发现端口5000监听")
            print("请确保Web应用正在运行: python web_app.py")
            
        return port_5000_found
        
    except Exception as e:
        print(f"❌ 检查端口绑定失败: {e}")
        return False

def check_firewall():
    """检查防火墙设置"""
    print("\n🔍 检查Windows防火墙设置...")
    try:
        # 检查防火墙规则
        result = subprocess.run(['netsh', 'advfirewall', 'firewall', 'show', 'rule', 'name=Flask Web App'], 
                              capture_output=True, text=True)
        
        if 'Flask Web App' in result.stdout:
            print("✅ 发现防火墙规则: Flask Web App")
        else:
            print("❌ 未发现防火墙规则")
            print("建议运行: netsh advfirewall firewall add rule name=\"Flask Web App\" dir=in action=allow protocol=TCP localport=5000")
            
    except Exception as e:
        print(f"❌ 检查防火墙失败: {e}")

def test_local_access():
    """测试本地访问"""
    print("\n🔍 测试本地访问...")
    try:
        response = requests.get('http://localhost:5000', timeout=5)
        if response.status_code == 200:
            print("✅ 本地访问正常")
            return True
        else:
            print(f"❌ 本地访问异常: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 本地访问失败: {e}")
        return False

def test_lan_access():
    """测试局域网访问"""
    print("\n🔍 测试局域网访问...")
    try:
        response = requests.get('http://192.168.3.6:5000', timeout=5)
        if response.status_code == 200:
            print("✅ 局域网访问正常")
            return True
        else:
            print(f"❌ 局域网访问异常: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 局域网访问失败: {e}")
        return False

def test_public_access():
    """测试公网访问"""
    print("\n🔍 测试公网访问...")
    try:
        response = requests.get('http://124.129.69.19:5000', timeout=10)
        if response.status_code == 200:
            print("✅ 公网访问正常")
            return True
        else:
            print(f"❌ 公网访问异常: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 公网访问失败: {e}")
        return False

def get_router_info():
    """获取路由器信息"""
    print("\n🔍 路由器配置建议...")
    print("📋 请检查以下路由器设置:")
    print("1. 端口转发规则:")
    print("   - 外部端口: 5000")
    print("   - 内部IP: 192.168.3.6")
    print("   - 内部端口: 5000")
    print("   - 协议: TCP")
    print("2. UPnP设置: 确保已启用")
    print("3. DMZ设置: 可考虑将192.168.3.6设为DMZ主机")

def main():
    """主函数"""
    print("=" * 60)
    print("Web应用外网访问诊断工具")
    print("=" * 60)
    
    # 检查各项配置
    port_ok = check_port_binding()
    check_firewall()
    
    local_ok = test_local_access()
    lan_ok = test_lan_access()
    public_ok = test_public_access()
    
    print("\n" + "=" * 60)
    print("诊断结果汇总")
    print("=" * 60)
    
    print(f"端口绑定: {'✅' if port_ok else '❌'}")
    print(f"本地访问: {'✅' if local_ok else '❌'}")
    print(f"局域网访问: {'✅' if lan_ok else '❌'}")
    print(f"公网访问: {'✅' if public_ok else '❌'}")
    
    if not public_ok:
        print("\n❌ 公网访问失败的可能原因:")
        print("1. 路由器端口转发未正确配置")
        print("2. ISP运营商阻止了5000端口")
        print("3. 路由器防火墙阻止了外网访问")
        print("4. Web应用未绑定到0.0.0.0")
        
        get_router_info()
        
        print("\n💡 建议解决方案:")
        print("1. 检查路由器端口转发设置")
        print("2. 尝试使用其他端口 (如8080)")
        print("3. 联系ISP确认端口是否被阻止")
        print("4. 考虑使用内网穿透工具 (如ngrok)")
    else:
        print("\n🎉 所有测试通过！外网访问正常")

if __name__ == "__main__":
    main()
