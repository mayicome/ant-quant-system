#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成Web应用访问二维码
支持内网IP和公网IP
"""

import qrcode
from PIL import Image
import os
import sys

def generate_qr_code(url, filename="web_app_qr.png"):
    """生成二维码"""
    try:
        # 创建二维码
        qr = qrcode.QRCode(
            version=1,  # 控制二维码的大小
            error_correction=qrcode.constants.ERROR_CORRECT_L,  # 错误纠正级别
            box_size=10,  # 每个小方块的像素数
            border=4,  # 边框的厚度
        )
        
        # 添加数据
        qr.add_data(url)
        qr.make(fit=True)
        
        # 创建二维码图片
        img = qr.make_image(fill_color="black", back_color="white")
        
        # 保存图片
        img.save(filename)
        
        print(f"✅ 二维码已生成: {filename}")
        print(f"📱 扫描二维码访问: {url}")
        
        return True
        
    except ImportError:
        print("❌ 缺少依赖库，请安装:")
        print("pip install qrcode[pil]")
        return False
    except Exception as e:
        print(f"❌ 生成二维码失败: {e}")
        return False

def get_network_info():
    """获取网络信息"""
    import socket
    
    # 获取内网IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        local_ip = "192.168.3.6"  # 从ipconfig获取的IP
    
    return local_ip

def main():
    """主函数"""
    print("=" * 50)
    print("Web应用二维码生成器")
    print("=" * 50)
    
    # 获取网络信息
    local_ip = get_network_info()
    
    print(f"📍 检测到内网IP: {local_ip}")
    print()
    
    # 生成选项
    print("请选择要生成的二维码类型:")
    print("1. 内网访问 (局域网内设备可访问)")
    print("2. 自定义URL")
    print("3. 退出")
    
    choice = input("\n请输入选择 (1-3): ").strip()
    
    if choice == "1":
        url = f"http://{local_ip}:5000"
        filename = f"web_app_local_{local_ip.replace('.', '_')}.png"
        print(f"\n🌐 内网访问地址: {url}")
        print("📝 说明: 只有同一局域网内的设备才能访问")
        
    elif choice == "2":
        url = input("请输入完整的URL (如: http://your-domain.com:5000): ").strip()
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url
        filename = "web_app_custom.png"
        print(f"\n🌐 自定义访问地址: {url}")
        
    elif choice == "3":
        print("👋 再见!")
        return
        
    else:
        print("❌ 无效选择")
        return
    
    print()
    
    # 生成二维码
    if generate_qr_code(url, filename):
        print()
        print("📋 使用说明:")
        print("1. 将生成的二维码图片发送给其他人")
        print("2. 使用微信扫描二维码即可访问Web应用")
        print("3. 确保Web应用正在运行 (python web_app.py)")
        
        if choice == "1":
            print()
            print("⚠️  内网访问注意事项:")
            print("- 确保防火墙允许5000端口访问")
            print("- 确保Web应用绑定到0.0.0.0 (已配置)")
            print("- 只有同一WiFi/局域网内的设备才能访问")
            print("- 如需外网访问，请配置路由器端口转发")

if __name__ == "__main__":
    main()
