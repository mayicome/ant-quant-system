#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
版权信息管理器
管理不同端口的版权信息显示
"""

import os
import json

class CopyrightManager:
    """版权信息管理器"""
    
    def __init__(self):
        self.copyright_dir = "copyrights"
        self.ensure_copyright_dir()
    
    def ensure_copyright_dir(self):
        """确保版权信息目录存在"""
        if not os.path.exists(self.copyright_dir):
            os.makedirs(self.copyright_dir)
    
    def get_copyright_html(self, port):
        """获取指定端口的版权信息HTML"""
        copyright_file = os.path.join(self.copyright_dir, f"{port}.html")
        
        if os.path.exists(copyright_file):
            try:
                with open(copyright_file, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception as e:
                print(f"读取版权文件 {copyright_file} 失败: {e}")
        
        # 返回默认版权信息
        return self.get_default_copyright(port)
    
    def get_default_copyright(self, port):
        """获取默认版权信息"""
        if port == 8080:
            return '''<div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
                <p>注：数据来源于市场公开信息，仅供参考，不作投资建议，若有误差，请以市场数据为准</p>
                <p>© 2025 蚂蚁量化乐园（公众号）</p>
            </div>'''
        elif port == 10000:
            return '''
            <div style="text-align: center; margin-top: 20px;">
                <img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgdmlld0JveD0iMCAwIDEwMCAxMDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxyZWN0IHdpZHRoPSIxMDAiIGhlaWdodD0iMTAwIiBmaWxsPSIjNjY3ZWVhIi8+Cjx0ZXh0IHg9IjUwIiB5PSI1NSIgZm9udC1mYW1pbHk9IkFyaWFsIiBmb250LXNpemU9IjE0IiBmaWxsPSJ3aGl0ZSIgdGV4dC1hbmNob3I9Im1pZGRsZSI+5bCP5Lq65L+h5oGv5Lit5paHPC90ZXh0Pgo8L3N2Zz4K" alt="蚂蚁量化乐园" style="width: 60px; height: 60px; margin-bottom: 10px;">
                <br>
                <span style="color: #666; font-size: 12px;">蚂蚁量化乐园（公众号）提供技术支持</span>
            </div>
            '''
        else:
            return f"2025 关键价格计算器 - 端口 {port}"
    
    def create_copyright_file(self, port, content, force=False):
        """创建版权信息文件"""
        copyright_file = os.path.join(self.copyright_dir, f"{port}.html")
        
        # 如果文件已存在且不强制覆盖，则跳过
        if os.path.exists(copyright_file) and not force:
            print(f"⚠️  版权文件已存在，跳过创建: {copyright_file}")
            return True
            
        try:
            with open(copyright_file, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"✅ 创建版权文件: {copyright_file}")
            return True
        except Exception as e:
            print(f"❌ 创建版权文件失败: {e}")
            return False
    
    def list_copyright_files(self):
        """列出所有版权信息文件"""
        if not os.path.exists(self.copyright_dir):
            return []
        
        files = []
        for filename in os.listdir(self.copyright_dir):
            if filename.endswith('.html'):
                port = filename.replace('.html', '')
                files.append(port)
        return sorted(files, key=int)
    
    def delete_copyright_file(self, port):
        """删除版权信息文件"""
        copyright_file = os.path.join(self.copyright_dir, f"{port}.html")
        try:
            if os.path.exists(copyright_file):
                os.remove(copyright_file)
                print(f"✅ 删除版权文件: {copyright_file}")
                return True
            else:
                print(f"⚠️  版权文件不存在: {copyright_file}")
                return False
        except Exception as e:
            print(f"❌ 删除版权文件失败: {e}")
            return False

def init_default_copyrights():
    """初始化默认版权信息"""
    manager = CopyrightManager()
    
    # 创建8080端口的版权信息
    manager.create_copyright_file(8080, '''<div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
        <p>注：数据来源于市场公开信息，仅供参考，不作投资建议，若有误差，请以市场数据为准</p>
        <p>© 2025 蚂蚁量化乐园（公众号）</p>
    </div>''')
    
    
    # 创建其他端口的默认版权信息
    for port in [8081, 8082, 8083]:
        manager.create_copyright_file(port, f"2025 关键价格计算器 - 端口 {port} - 蚂蚁量化乐园")

if __name__ == "__main__":
    print("初始化默认版权信息...")
    init_default_copyrights()
    print("✅ 默认版权信息初始化完成")
