# -*- coding: utf-8 -*-
"""
QMT自动登录脚本 - 简洁版
申请开通QMT请添加微信咨询gjquant
获取更多资料访问https://miniqmt.com/
此登录脚本仅用于软件测试，请勿用于实盘交易
"""
import pywinauto
import win32gui
import win32process
import psutil
import time
import schedule
import os
import subprocess
import getpass

# 尝试导入GUI组件用于密码输入
try:
    import tkinter as tk
    from tkinter import simpledialog, messagebox
    _GUI_AVAILABLE = True
except Exception:
    _GUI_AVAILABLE = False

def read_credentials(file_path="credentials.txt"):
    """从文件读取账号密码"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(script_dir, file_path)
    credentials = {}
    with open(full_path, 'r', encoding='utf-8') as file:
        for line in file:
            line = line.strip()
            if line and '=' in line:
                key, value = line.split('=', 1)
                credentials[key] = value
    return credentials

def is_qmt_running(process_path):
    """检查QMT进程是否在运行"""
    try:
        process_name = os.path.basename(process_path)
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                if proc.info['name'] == process_name:
                    return True, proc.info['pid']
            except:
                continue
    except:
        pass
    return False, None

def close_qmt(process_path):
    """关闭QMT进程"""
    try:
        process_name = os.path.basename(process_path)
        closed = False
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                if proc.info['name'] == process_name:
                    proc.terminate()  # 尝试正常关闭
                    closed = True
            except:
                continue
        
        if closed:
            # 等待进程关闭，最多等待5秒
            for _ in range(10):
                time.sleep(0.5)
                is_running, _ = is_qmt_running(process_path)
                if not is_running:
                    return True
            # 如果正常关闭失败，强制关闭
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    if proc.info['name'] == process_name:
                        proc.kill()  # 强制关闭
                except:
                    continue
            return True
        return False
    except Exception as e:
        print(f"关闭QMT进程时出错: {e}")
        return False

def start_qmt(process_path):
    """启动QMT程序"""
    try:
        subprocess.Popen([process_path])
        time.sleep(3)  # 等待程序启动
        return True
    except Exception as e:
        print(f"启动QMT失败: {e}")
        return False


def find_login_dialog(process_path, timeout=30):
    """查找登录对话框窗口 - 直接查找标题为 'XtMiniQmt' 的窗口"""
    process_name = os.path.basename(process_path)
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            # 方法1: 直接查找标题为 'XtMiniQmt' 的窗口
            try:
                login_window = pywinauto.findwindows.find_windows(title='XtMiniQmt')
                if login_window:
                    hwnd = login_window[0] if isinstance(login_window, list) else login_window
                    # 验证窗口是否属于QMT进程
                    try:
                        process_id = win32process.GetWindowThreadProcessId(hwnd)[1]
                        proc = psutil.Process(process_id)
                        proc_exe = proc.exe()
                        if process_name.lower() in os.path.basename(proc_exe).lower():
                            return hwnd
                    except:
                        pass
            except:
                pass
            
            # 方法2: 查找所有窗口，匹配标题
            all_windows = pywinauto.findwindows.find_windows()
            for hwnd in all_windows:
                try:
                    title = win32gui.GetWindowText(hwnd)
                    if title == 'XtMiniQmt':
                        # 验证窗口是否属于QMT进程
                        process_id = win32process.GetWindowThreadProcessId(hwnd)[1]
                        proc = psutil.Process(process_id)
                        proc_exe = proc.exe()
                        if process_name.lower() in os.path.basename(proc_exe).lower():
                            return hwnd
                except:
                    continue
        except:
            pass
        
        time.sleep(0.5)
    
    return None

def input_password_and_login(login_window, password):
    """输入密码并点击登录按钮"""
    try:
        app = pywinauto.Application().connect(handle=login_window)
        window = app.window(handle=login_window)
        
        # 设置焦点
        window.set_focus()
        time.sleep(0.5)
        
        # 查找所有输入框
        edit_controls = window.descendants(class_name="Edit")
        
        if len(edit_controls) >= 2:
            # 有多个输入框，按Y坐标排序
            controls_with_pos = []
            for edit in edit_controls:
                try:
                    rect = edit.rectangle()
                    controls_with_pos.append((edit, rect.top))
                except:
                    continue
            
            if len(controls_with_pos) >= 2:
                controls_with_pos.sort(key=lambda x: x[1])
                # 第一个是账号框，第二个是密码框
                # 先点击账号框，确保焦点在账号框
                account_field = controls_with_pos[0][0]
                account_field.set_focus()
                time.sleep(0.2)
                # 然后按Tab切换到密码框
                window.type_keys('{TAB}')
                time.sleep(0.3)
                # 现在应该在密码框了，输入密码
                window.type_keys("^a")  # 全选
                time.sleep(0.1)
                window.type_keys(password)
                time.sleep(0.3)
            else:
                # 备用方法：使用Tab键导航
                window.type_keys('{HOME}')  # 先回到第一个输入框
                time.sleep(0.2)
                window.type_keys('{TAB}')  # 切换到密码框
                time.sleep(0.2)
                window.type_keys("^a")  # 全选
                time.sleep(0.1)
                window.type_keys(password)
                time.sleep(0.3)
        else:
            # 只有一个输入框或找不到，使用Tab键导航（假设当前在账号框）
            window.type_keys('{HOME}')  # 先回到第一个输入框
            time.sleep(0.2)
            window.type_keys('{TAB}')  # 切换到密码框
            time.sleep(0.2)
            window.type_keys("^a")  # 全选
            time.sleep(0.1)
            window.type_keys(password)
            time.sleep(0.3)
        
        # 使用两次回车键提交登录
        window.type_keys('{ENTER}')
        time.sleep(0.2)
        window.type_keys('{ENTER}')
        time.sleep(0.5)
        return True
    except Exception as e:
        print(f"输入密码或登录失败: {e}")
        return False

def wait_for_main_window(process_path, timeout=30):
    """等待主窗口出现"""
    process_name = os.path.basename(process_path)
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            windows = pywinauto.findwindows.find_windows()
            for hwnd in windows:
                try:
                    title = win32gui.GetWindowText(hwnd)
                    if not title:
                        continue
                    
                    # 检查是否是QMT主窗口
                    if any(keyword in title for keyword in ['QMT', '国金', '国金证券']):
                        # 排除编辑器窗口
                        if any(exclude in title for exclude in ['Cursor', '蚂蚁量化', '编辑器', 'Editor', '.py']):
                            continue
                        
                        # 检查窗口是否属于QMT进程
                        process_id = win32process.GetWindowThreadProcessId(hwnd)[1]
                        proc = psutil.Process(process_id)
                        proc_exe = proc.exe()
                        
                        if process_name.lower() in os.path.basename(proc_exe).lower():
                            # 检查窗口类名（主窗口通常是Qt5QWindowIcon）
                            win_class = win32gui.GetClassName(hwnd)
                            if win_class == 'Qt5QWindowIcon':  # 是Qt窗口
                                return True
                except:
                    continue
        except:
            pass
        
        time.sleep(1)
    
    return False

def login_qmt(password, process_path):
    """主登录函数"""
    auto_restart = os.environ.get("AUTO_RESTART") == "1"
    # 1. 检查QMT是否已运行，如果运行则先关闭
    is_running, pid = is_qmt_running(process_path)
    if is_running:
        print("检测到QMT正在运行，正在关闭...")
        close_qmt(process_path)
        time.sleep(2)  # 等待进程完全关闭，可以看到关闭过程
    
    # 2. 启动QMT
    if not start_qmt(process_path):
        print("[ERROR] 启动QMT失败")
        return False
    
    # 3. 查找登录对话框
    login_window = find_login_dialog(process_path, timeout=15)
    
    if not login_window:
        print("[ERROR] 未找到登录窗口")
        return False
    
    # 4. 输入密码并登录（无密码则引导用户在QMT窗口手动输入）
    if password:
        if not input_password_and_login(login_window, password):
            print("[ERROR] 输入密码或登录失败")
            return False
    else:
        try:
            # 聚焦到登录窗口，让用户直接在QMT窗口输入密码
            app = pywinauto.Application().connect(handle=login_window)
            window = app.window(handle=login_window)
            window.set_focus()
        except Exception:
            pass
        manual_timeout = 120 if auto_restart else 300
        print(
            f"[INFO] 未配置密码：请直接在QMT登录窗口输入密码并登录"
            f"（最多等待{manual_timeout}秒）"
        )
        # 直接等待主窗口出现，不再在控制台/GUI收集密码
        if wait_for_main_window(process_path, timeout=manual_timeout):
            print("[INFO] 手动登录完成")
            return True
        else:
            print(f"[WARN] 等待手动登录超时（{manual_timeout}秒），请确认是否已登录")
            return False
    
    # 5. 等待登录完成
    time.sleep(2)
    
    # 6. 验证登录是否成功
    main_timeout = 90 if auto_restart else 20
    if wait_for_main_window(process_path, timeout=main_timeout):
        print("[INFO] 登录成功")
        return True

    if auto_restart and password:
        print("[WARN] 自动登录后主窗口未就绪，重试提交登录并延长等待...")
        login_window = find_login_dialog(process_path, timeout=10)
        if login_window:
            input_password_and_login(login_window, password)
            time.sleep(2)
        if wait_for_main_window(process_path, timeout=60):
            print("[INFO] 登录成功")
            return True
        print("[ERROR] 自动重启模式下登录失败（已用尽自动重试）")
        return False

    # 交互式运行：聚焦窗口并等待用户手动补救
    try:
        app = pywinauto.Application().connect(handle=login_window)
        window = app.window(handle=login_window)
        window.set_focus()
    except Exception:
        pass
    print("[WARN] 登录未成功（可能密码错误）。请在QMT登录窗口重新输入密码并点击登录（最多等待300秒）。")
    if wait_for_main_window(process_path, timeout=300):
        print("[INFO] 手动登录完成")
        return True
    print("[ERROR] 等待手动登录超时（300秒），本次登录失败")
    return False

# 主程序
if __name__ == "__main__":
    # 读取配置
    credentials = read_credentials()
    # 仅读取本地文件；若无则手动输入
    password = credentials.get('password')
    process_path = r'D:\国金证券QMT交易端 - 副本\bin.x64\XtMiniQmt.exe'
    login_time = '10:05'  # 定时登录时间
    
    # 无密码时不在控制台/弹窗获取，转由QMT窗口手动输入（在login_qmt里处理）
    
    def job():
        """定时任务"""
        return login_qmt(password, process_path)
    
    # 立即执行一次（用于测试）
    ok = job()
    raise SystemExit(0 if ok else 1)
    
    # 定时任务（可选）
    # schedule.every().day.at(login_time).do(job)
    # while True:
    #     schedule.run_pending()
    #     time.sleep(1)

