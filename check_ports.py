#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端口检查工具
检查端口占用情况和Web服务状态
"""

import socket
import requests
import subprocess
import sys
import time

def check_port_available(port):
    """检查端口是否可用"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', port))
        sock.close()
        return result != 0  # 0表示端口被占用
    except Exception as e:
        print(f"检查端口 {port} 时出错: {e}")
        return False

def check_web_service(port):
    """检查Web服务是否正常"""
    try:
        response = requests.get(f'http://localhost:{port}', timeout=5)
        return response.status_code == 200
    except Exception as e:
        return False

def get_port_process(port):
    """获取占用端口的进程信息"""
    try:
        if sys.platform == "win32":
            # Windows系统
            result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
            lines = result.stdout.split('\n')
            
            for line in lines:
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        # 获取进程名
                        try:
                            task_result = subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], 
                                                       capture_output=True, text=True)
                            task_lines = task_result.stdout.split('\n')
                            for task_line in task_lines:
                                if pid in task_line:
                                    return f"PID: {pid}, 进程: {task_line.strip()}"
                        except:
                            pass
                        return f"PID: {pid}"
        else:
            # Linux/Mac系统
            result = subprocess.run(['lsof', '-i', f':{port}'], capture_output=True, text=True)
            if result.stdout:
                return result.stdout.strip()
    except Exception as e:
        print(f"获取进程信息失败: {e}")
    
    return "未知进程"

def main():
    """主函数"""
    print("=" * 60)
    print("🔍 端口检查工具")
    print("=" * 60)
    
    # 检查常用端口
    ports_to_check = [8080, 10000, 8081, 8082, 8083, 5000]
    
    print("📊 端口状态检查:")
    print("-" * 60)
    
    for port in ports_to_check:
        print(f"端口 {port:5d}: ", end="")
        
        # 检查端口是否被占用
        if not check_port_available(port):
            print("🔴 被占用", end="")
            
            # 获取占用进程信息
            process_info = get_port_process(port)
            print(f" ({process_info})", end="")
            
            # 检查是否是Web服务
            if check_web_service(port):
                print(" ✅ Web服务正常")
            else:
                print(" ❌ Web服务异常")
        else:
            print("🟢 可用")
    
    print("\n" + "=" * 60)
    print("💡 建议:")
    print("1. 如果端口被占用，请先停止占用进程")
    print("2. 如果Web服务异常，请检查应用是否正常启动")
    print("3. 可以使用以下命令停止占用端口的进程:")
    
    # 提供Windows命令示例
    if sys.platform == "win32":
        print("   netstat -ano | findstr :端口号")
        print("   taskkill /PID 进程ID /F")
    else:
        print("   lsof -i :端口号")
        print("   kill -9 进程ID")
    
    print("\n🔧 测试Web服务:")
    print("请在浏览器中访问以下地址测试:")
    for port in [8080, 10000]:
        if not check_port_available(port):
            print(f"   http://localhost:{port}")

if __name__ == "__main__":
    main()
