#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为花生壳外网访问生成二维码
"""

import qrcode
import os

def generate_qr_code(url, filename):
    """生成二维码并保存为图片文件"""
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        img.save(filename)
        print(f"✅ 二维码已生成: {filename}")
        print(f"📱 扫描二维码访问: {url}")
        return True
    except Exception as e:
        print(f"❌ 生成二维码失败: {e}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("花生壳外网访问二维码生成器")
    print("=" * 50)
    
    # 花生壳外网地址
    peanuthull_url = "http://423jgos88782.vicp.fun/"
    qr_filename = "peanuthull_web_app_qr.png"
    
    print(f"🌐 花生壳外网地址: {peanuthull_url}")
    
    if generate_qr_code(peanuthull_url, qr_filename):
        print("\n📋 使用说明:")
        print("1. 将生成的二维码图片发送给其他人")
        print("2. 使用微信扫描二维码即可访问Web应用")
        print("3. 确保Web应用正在运行")
        print("\n⚠️  注意事项:")
        print("- 确保花生壳服务正常运行")
        print("- 确保Web应用绑定到0.0.0.0")
        print("- 确保防火墙允许相应端口访问")
        print("- 花生壳免费版可能有流量限制")
    else:
        print("❌ 二维码生成失败")
