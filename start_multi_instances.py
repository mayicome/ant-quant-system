#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多实例Web应用启动脚本
通过修改端口号启动多个Web应用实例
"""

import os
import sys
import subprocess
import time
import threading

def start_web_instance(port):
    """启动单个Web应用实例"""
    print(f"🚀 启动端口 {port}...")
    
    # 修改环境变量设置端口
    env = os.environ.copy()
    env['FLASK_PORT'] = str(port)
    
    try:
        # 启动web_app.py并传递端口参数
        cmd = [sys.executable, 'web_app.py', str(port)]
        process = subprocess.Popen(cmd, env=env)
        
        print(f"✅ 端口 {port} 启动成功 (PID: {process.pid})")
        return process
        
    except Exception as e:
        print(f"❌ 端口 {port} 启动失败: {e}")
        return None

def main():
    """主函数"""
    print("=" * 60)
    print("🌐 多实例Web应用启动器")
    print("=" * 60)
    
    # 默认端口列表
    default_ports = [8080, 8081, 8082, 8083]
    
    # 从命令行参数获取端口
    if len(sys.argv) > 1:
        try:
            ports = [int(p) for p in sys.argv[1:]]
        except ValueError:
            print("❌ 端口号必须是数字")
            print("用法: python start_multi_instances.py [端口1] [端口2] ...")
            print("示例: python start_multi_instances.py 8080 8081 8082")
            return
    else:
        ports = default_ports
        print(f"使用默认端口: {', '.join(map(str, ports))}")
    
    print(f"\n🎯 将在以下端口启动Web服务:")
    for port in ports:
        print(f"   - http://localhost:{port}")
    
    print(f"\n📱 局域网访问地址:")
    try:
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        for port in ports:
            print(f"   - http://{local_ip}:{port}")
    except:
        print("   无法获取局域网IP")
    
    print(f"\n⏳ 正在启动服务...")
    
    # 启动多个实例
    processes = []
    for port in ports:
        process = start_web_instance(port)
        if process:
            processes.append((port, process))
        time.sleep(2)  # 避免端口冲突
    
    if not processes:
        print("❌ 没有成功启动任何服务")
        return
    
    print(f"\n✅ 成功启动 {len(processes)} 个服务!")
    print(f"按 Ctrl+C 停止所有服务")
    
    try:
        # 监控进程状态
        while True:
            time.sleep(5)
            for port, process in processes[:]:  # 使用切片避免修改列表时出错
                if process.poll() is not None:  # 进程已结束
                    print(f"⚠️  端口 {port} 服务意外停止")
                    processes.remove((port, process))
            
            if not processes:
                print("❌ 所有服务都已停止")
                break
                
    except KeyboardInterrupt:
        print(f"\n\n🛑 正在停止所有服务...")
        
        # 停止所有进程
        for port, process in processes:
            try:
                process.terminate()
                print(f"🛑 停止端口 {port} 服务")
            except:
                pass
        
        # 等待进程结束
        for port, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        
        print("✅ 所有服务已停止")

if __name__ == '__main__':
    main()
