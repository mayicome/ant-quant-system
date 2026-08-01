#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
版权信息管理工具
用于创建、编辑和管理不同端口的版权信息
"""

import os
import sys
from copyright_manager import CopyrightManager

def show_menu():
    """显示菜单"""
    print("\n" + "=" * 50)
    print("📝 版权信息管理工具")
    print("=" * 50)
    print("1. 查看所有版权信息")
    print("2. 创建/编辑版权信息")
    print("3. 删除版权信息")
    print("4. 初始化默认版权信息")
    print("5. 退出")
    print("=" * 50)

def list_copyrights(manager):
    """列出所有版权信息"""
    print("\n📋 当前版权信息文件:")
    files = manager.list_copyright_files()
    
    if not files:
        print("   (无版权信息文件)")
        return
    
    for port in files:
        content = manager.get_copyright_html(port)
        print(f"\n   端口 {port}:")
        # 显示前50个字符
        preview = content.replace('\n', ' ').replace('\r', '')[:50]
        if len(content) > 50:
            preview += "..."
        print(f"   {preview}")

def create_edit_copyright(manager):
    """创建/编辑版权信息"""
    try:
        port = input("\n请输入端口号: ").strip()
        if not port.isdigit():
            print("❌ 端口号必须是数字")
            return
        
        port = int(port)
        
        print(f"\n当前端口 {port} 的版权信息:")
        current_content = manager.get_copyright_html(port)
        print(f"'{current_content}'")
        
        print(f"\n请输入新的版权信息 (支持HTML):")
        print("提示: 可以输入纯文本或HTML代码")
        print("例如: 2025 关键价格计算器 - 蚂蚁量化乐园（公众号）")
        print("或者: <div>2025 关键价格计算器 - <strong>蚂蚁量化乐园</strong></div>")
        
        new_content = input("> ").strip()
        
        if not new_content:
            print("❌ 版权信息不能为空")
            return
        
        if manager.create_copyright_file(port, new_content, force=True):
            print(f"✅ 端口 {port} 的版权信息已更新")
        else:
            print(f"❌ 更新失败")
            
    except KeyboardInterrupt:
        print("\n操作已取消")
    except Exception as e:
        print(f"❌ 操作失败: {e}")

def delete_copyright(manager):
    """删除版权信息"""
    try:
        port = input("\n请输入要删除的端口号: ").strip()
        if not port.isdigit():
            print("❌ 端口号必须是数字")
            return
        
        port = int(port)
        
        # 确认删除
        confirm = input(f"确认删除端口 {port} 的版权信息? (y/N): ").strip().lower()
        if confirm != 'y':
            print("操作已取消")
            return
        
        if manager.delete_copyright_file(port):
            print(f"✅ 端口 {port} 的版权信息已删除")
        else:
            print(f"❌ 删除失败")
            
    except KeyboardInterrupt:
        print("\n操作已取消")
    except Exception as e:
        print(f"❌ 操作失败: {e}")

def init_defaults(manager):
    """初始化默认版权信息"""
    try:
        confirm = input("确认初始化默认版权信息? (y/N): ").strip().lower()
        if confirm != 'y':
            print("操作已取消")
            return
        
        from copyright_manager import init_default_copyrights
        init_default_copyrights()
        print("✅ 默认版权信息初始化完成")
        
    except KeyboardInterrupt:
        print("\n操作已取消")
    except Exception as e:
        print(f"❌ 操作失败: {e}")

def main():
    """主函数"""
    manager = CopyrightManager()
    
    while True:
        show_menu()
        
        try:
            choice = input("请选择操作 (1-5): ").strip()
            
            if choice == '1':
                list_copyrights(manager)
            elif choice == '2':
                create_edit_copyright(manager)
            elif choice == '3':
                delete_copyright(manager)
            elif choice == '4':
                init_defaults(manager)
            elif choice == '5':
                print("退出程序")
                break
            else:
                print("❌ 无效选择，请输入 1-5")
                
        except KeyboardInterrupt:
            print("\n\n退出程序")
            break
        except Exception as e:
            print(f"❌ 操作失败: {e}")

if __name__ == "__main__":
    main()
