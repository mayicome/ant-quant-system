#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
外网访问详细诊断工具
"""

import requests
import socket
import time
from urllib.parse import urlparse

def test_port_connectivity(host, port, timeout=5):
    """测试端口连通性"""
    try:
        print(f"🔍 测试端口连通性: {host}:{port}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result == 0:
            print(f"✅ 端口 {port} 可连接")
            return True
        else:
            print(f"❌ 端口 {port} 不可连接 (错误代码: {result})")
            return False
    except Exception as e:
        print(f"❌ 端口测试失败: {e}")
        return False

def test_http_request(url, timeout=10):
    """测试HTTP请求"""
    try:
        print(f"🔍 测试HTTP请求: {url}")
        response = requests.get(url, timeout=timeout)
        print(f"✅ HTTP请求成功: {response.status_code}")
        return True
    except requests.exceptions.ConnectTimeout:
        print(f"❌ HTTP连接超时")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"❌ HTTP连接错误: {e}")
        return False
    except Exception as e:
        print(f"❌ HTTP请求失败: {e}")
        return False

def test_different_ports(host):
    """测试不同端口"""
    print(f"\n🔍 测试 {host} 的不同端口...")
    
    ports_to_test = [80, 443, 8080, 8081, 3000, 5000, 8000, 9000]
    accessible_ports = []
    
    for port in ports_to_test:
        if test_port_connectivity(host, port, timeout=3):
            accessible_ports.append(port)
        time.sleep(0.5)
    
    return accessible_ports

def check_isp_restrictions():
    """检查ISP限制"""
    print("\n🔍 检查ISP限制...")
    
    # 测试常用端口
    test_hosts = [
        ("httpbin.org", 80),
        ("google.com", 80),
        ("baidu.com", 80),
    ]
    
    for host, port in test_hosts:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()
            
            if result == 0:
                print(f"✅ {host}:{port} 可连接")
            else:
                print(f"❌ {host}:{port} 不可连接")
        except Exception as e:
            print(f"❌ {host}:{port} 测试失败: {e}")

def main():
    """主函数"""
    print("=" * 60)
    print("外网访问详细诊断工具")
    print("=" * 60)
    
    # 目标服务器
    target_host = "124.129.69.19"
    target_port = 8080
    
    print(f"🎯 目标服务器: {target_host}:{target_port}")
    print(f"🌐 当前环境: 外网")
    print()
    
    # 1. 测试端口连通性
    port_ok = test_port_connectivity(target_host, target_port)
    
    # 2. 测试HTTP请求
    http_ok = test_http_request(f"http://{target_host}:{target_port}")
    
    # 3. 测试其他端口
    accessible_ports = test_different_ports(target_host)
    
    # 4. 检查ISP限制
    check_isp_restrictions()
    
    print("\n" + "=" * 60)
    print("诊断结果汇总")
    print("=" * 60)
    
    print(f"端口 {target_port} 连通性: {'✅' if port_ok else '❌'}")
    print(f"HTTP请求: {'✅' if http_ok else '❌'}")
    
    if accessible_ports:
        print(f"可访问的端口: {accessible_ports}")
    else:
        print("❌ 没有可访问的端口")
    
    print("\n💡 问题分析:")
    if not port_ok:
        print("1. 路由器端口转发未正确配置")
        print("2. ISP可能阻止了8080端口")
        print("3. 服务器防火墙阻止了外网访问")
        print("4. 服务器可能未运行")
    
    if not http_ok and port_ok:
        print("1. 端口可连接但HTTP服务异常")
        print("2. 可能是Web应用配置问题")
    
    print("\n🔧 建议解决方案:")
    print("1. 检查路由器端口转发配置:")
    print("   - 外部端口: 8080")
    print("   - 内部IP: 192.168.3.21")
    print("   - 内部端口: 8080")
    print("2. 尝试使用其他端口 (如80, 443)")
    print("3. 联系ISP确认端口限制")
    print("4. 考虑使用内网穿透工具")

if __name__ == "__main__":
    main()
