#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取公网IP地址
"""

import requests
import json

def get_public_ip():
    """获取公网IP地址"""
    try:
        # 使用多个服务获取公网IP，提高成功率
        services = [
            "https://api.ipify.org?format=json",
            "https://ipapi.co/json/",
            "https://ipinfo.io/json",
            "https://api.myip.com"
        ]
        
        for service in services:
            try:
                response = requests.get(service, timeout=5)
                data = response.json()
                
                if 'ip' in data:
                    return data['ip']
                elif 'ipAddress' in data:
                    return data['ipAddress']
                elif 'query' in data:
                    return data['query']
                    
            except Exception as e:
                print(f"尝试服务 {service} 失败: {e}")
                continue
        
        return None
        
    except Exception as e:
        print(f"获取公网IP失败: {e}")
        return None

def main():
    """主函数"""
    print("=" * 50)
    print("公网IP获取工具")
    print("=" * 50)
    
    print("正在获取公网IP地址...")
    public_ip = get_public_ip()
    
    if public_ip:
        print(f"✅ 公网IP地址: {public_ip}")
        print(f"🌐 外网访问地址: http://{public_ip}:5000")
        
        # 询问是否生成二维码
        choice = input("\n是否生成外网访问二维码? (y/n): ").strip().lower()
        
        if choice == 'y':
            from generate_qr_code import generate_qr_code
            url = f"http://{public_ip}:5000"
            filename = f"web_app_public_{public_ip.replace('.', '_')}.png"
            
            if generate_qr_code(url, filename):
                print(f"\n📱 外网访问二维码已生成: {filename}")
                print("📋 使用说明:")
                print("1. 将二维码发送给任何人")
                print("2. 用微信扫描即可访问Web应用")
                print("3. 确保路由器端口转发已配置")
                print("4. 确保Web应用正在运行")
    else:
        print("❌ 无法获取公网IP地址")
        print("请检查网络连接或手动输入公网IP")

if __name__ == "__main__":
    main()
