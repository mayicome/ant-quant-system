#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路由器端口转发配置指南
"""

def print_router_guide():
    """打印路由器配置指南"""
    print("=" * 60)
    print("路由器端口转发配置指南")
    print("=" * 60)
    
    print("📋 请按以下步骤配置路由器:")
    print()
    print("1. 登录路由器管理界面:")
    print("   - 打开浏览器访问: http://192.168.3.1")
    print("   - 输入管理员账号密码")
    print()
    print("2. 找到端口转发/虚拟服务器设置:")
    print("   - TP-Link: 高级功能 → NAT转发 → 虚拟服务器")
    print("   - 华为: 高级功能 → NAT → 端口映射")
    print("   - 小米: 高级设置 → 端口转发")
    print("   - 华硕: 高级设置 → 端口转发")
    print("   - 网件: 高级 → 端口转发/端口触发")
    print()
    print("3. 添加端口转发规则:")
    print("   - 服务名称: Flask Web App")
    print("   - 外部端口: 5000")
    print("   - 内部IP: 192.168.3.6")
    print("   - 内部端口: 5000")
    print("   - 协议: TCP")
    print("   - 状态: 启用")
    print()
    print("4. 保存设置并重启路由器")
    print()
    print("⚠️  注意事项:")
    print("- 确保内部IP (192.168.3.6) 是静态IP")
    print("- 如果使用DHCP，IP可能会变化")
    print("- 某些ISP可能阻止5000端口")

if __name__ == "__main__":
    print_router_guide()
