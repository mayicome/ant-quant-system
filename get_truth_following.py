"""
Truth Social 转发监控（默认宽松）：连接已登录的 X（Chrome 远程调试），打开指定账号时间线；
程序启动后立刻解析一次，再按间隔刷新。默认不限 A 股交易日与 9:00–15:00（便于调试），
凡尚未记入状态文件的帖子即打印【原文】与【中文】译文（需 pip install deep-translator）。
加 --strict-session 可恢复「仅交易日 + 指定时段内发帖才提示」；--no-translate 仅原文。

环境变量 TRUTH_X_PROFILE_URL：要监控的 X 主页 URL（默认示例见代码内常量）。

单次抓取 X Following 页并导出 CSV：加参数 --following（沿用 get_x_following 旧逻辑）。
"""

import time
import random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from datetime import datetime, timedelta, timezone, date, time as dt_time
import re
import csv
import os
import warnings
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import urllib.request
import urllib.parse
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import traceback

# Windows 窗口管理（仅在 Windows 系统上）
if sys.platform.startswith('win'):
    try:
        import win32gui
        import win32con
        import win32api
        WINDOWS_API_AVAILABLE = True
    except ImportError:
        WINDOWS_API_AVAILABLE = False
        print("⚠️ 警告: 未安装 pywin32，无法管理浏览器窗口状态")
        print("  建议安装: pip install pywin32")
else:
    WINDOWS_API_AVAILABLE = False


def check_port_accessible(host, port, timeout=2):
    """
    检查端口是否可访问
    
    参数:
        host: 主机地址
        port: 端口号
        timeout: 超时时间（秒）
    
    返回:
        bool: 端口是否可访问
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False


def check_chrome_debug_port(port=9222):
    """
    检查Chrome调试端口是否在运行
    
    参数:
        port: 调试端口号
    
    返回:
        bool: 端口是否可访问
    """
    return check_port_accessible('127.0.0.1', port)


def check_chrome_debug_endpoint(debug_port=9222):
    """
    检查Chrome调试端点的实际响应
    
    参数:
        debug_port: 调试端口号
    
    返回:
        bool: 端点是否正常响应
    """
    try:
        import urllib.request
        import json
        url = f"http://127.0.0.1:{debug_port}/json"
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.loads(response.read().decode())
            if isinstance(data, list) and len(data) > 0:
                print(f"  ✓ 调试端点正常，发现 {len(data)} 个标签页")
                return True
            else:
                print(f"  ⚠️ 调试端点响应异常")
                return False
    except Exception as e:
        print(f"  ⚠️ 无法访问调试端点: {e}")
        return False


def connect_to_existing_browser(debug_port=9222):
    """
    连接到已打开的Chrome浏览器实例
    
    参数:
        debug_port: Chrome远程调试端口，默认9222
    
    返回:
        driver: Selenium WebDriver实例
    """
    print(f"正在连接到浏览器 (端口 {debug_port})...")
    print()
    
    # 首先检查端口是否可访问
    print("步骤1: 检查调试端口是否可访问...")
    if not check_chrome_debug_port(debug_port):
        print(f"✗ 端口 {debug_port} 不可访问！")
        print("\n可能的原因：")
        print("  1. Chrome未以调试模式启动")
        print("  2. Chrome已关闭或崩溃")
        print("  3. 端口被其他程序占用")
        print("\n解决方案：")
        print("  请确保Chrome已以调试模式启动，启动命令：")
        print(f'  chrome.exe --remote-debugging-port={debug_port} --user-data-dir="C:/temp/chrome_debug"')
        print("\n或者使用完整路径：")
        print(f'  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port={debug_port} --user-data-dir="C:/temp/chrome_debug"')
        print("\n检查方法：")
        print(f"  1. 打开浏览器访问: http://127.0.0.1:{debug_port}/json")
        print("  2. 如果能看到JSON数据，说明调试端口正常")
        print("  3. 如果无法访问，说明Chrome未以调试模式启动")
        return None
    else:
        print(f"✓ 端口 {debug_port} 可访问")
    
    # 检查调试端点是否正常响应
    print("\n步骤1.5: 检查调试端点响应...")
    check_chrome_debug_endpoint(debug_port)
    
    print("\n步骤2: 初始化WebDriver连接...")
    
    # 设置环境变量，禁用Selenium的自动管理功能
    os.environ['SE_SESSION_REQUEST_TIMEOUT'] = '0'
    os.environ['WDM_LOG_LEVEL'] = '0'
    os.environ['WDM_PRINT_FIRST_LINE'] = 'False'
    
    chrome_options = Options()
    # 连接到已存在的浏览器时，只需要设置 debuggerAddress
    # 其他选项（如 useAutomationExtension, excludeSwitches）在连接已存在浏览器时不被支持
    chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
    
    # 禁用ChromeDriver自动管理，避免网络请求错误
    # 抑制Selenium的自动管理警告和异常
    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", message=".*chrome.*")
    warnings.filterwarnings("ignore", message=".*ChromeDriver.*")
    warnings.filterwarnings("ignore", message=".*request.*")
    
    # 定义连接函数
    def try_connect_with_service():
        """尝试使用Service连接"""
        service = Service()
        service.service_log_path = os.devnull
        # 尝试禁用ChromeDriver的自动启动检查
        service.service_args = ['--log-level=OFF']
        return webdriver.Chrome(service=service, options=chrome_options)
    
    def try_connect_without_service():
        """尝试不使用Service连接"""
        return webdriver.Chrome(options=chrome_options)
    
    # 方法1：尝试使用Service连接（增加超时时间到30秒）
    print("  尝试方法1：使用Service连接（30秒超时）...")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(try_connect_with_service)
            try:
                driver = future.result(timeout=30)  # 增加到30秒超时
                print(f"✓ 成功连接到已打开的浏览器 (端口 {debug_port})")
                return driver
            except FutureTimeoutError:
                print("  ⚠️ 方法1连接超时（30秒），尝试方法2...")
                # 取消任务（虽然可能无法真正中断，但至少尝试）
                future.cancel()
    except Exception as e1:
        error_str = str(e1).lower()
        # 如果是超时异常，继续尝试方法2
        if 'timeout' in error_str or '超时' in error_str:
            print("  ⚠️ 方法1超时，尝试方法2...")
        else:
            print(f"  方法1失败: {e1}")
            print("  尝试方法2...")
    
    # 方法2：尝试不使用Service连接（增加超时时间到30秒）
    print("  尝试方法2：不使用Service连接（30秒超时）...")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(try_connect_without_service)
            try:
                driver = future.result(timeout=30)  # 增加到30秒超时
                print(f"✓ 成功连接到已打开的浏览器 (端口 {debug_port})")
                return driver
            except FutureTimeoutError:
                print("  ⚠️ 方法2连接超时（30秒）")
                future.cancel()
    except Exception as e2:
        error_str = str(e2).lower()
        if 'timeout' in error_str or '超时' in error_str:
            print("  ⚠️ 方法2也超时")
        else:
            print(f"  方法2失败: {e2}")
    
    # 如果两种方法都失败，尝试直接连接（不带超时，作为最后尝试）
    print("  尝试方法3：直接连接（最后尝试，可能较慢）...")
    try:
        # 直接连接，不设置超时，但给用户提示
        print("  提示：如果长时间无响应，请按 Ctrl+C 中断，然后检查Chrome是否正常")
        driver = webdriver.Chrome(options=chrome_options)
        print(f"✓ 成功连接到已打开的浏览器 (端口 {debug_port})")
        return driver
    except KeyboardInterrupt:
        print("\n  用户中断连接")
        return None
    except Exception as e3:
        print(f"\n✗ 所有连接方法都失败了")
        print(f"最后错误: {e3}")
        print("\n诊断信息：")
        print(f"  端口 {debug_port} 可访问: ✓")
        print("  但Selenium无法建立连接")
        print("\n可能的原因：")
        print("  1. Chrome版本与ChromeDriver版本不匹配（最常见）")
        print("  2. ChromeDriver未正确安装或不在PATH中")
        print("  3. ChromeDriver尝试启动新实例而非连接已存在的浏览器")
        print("  4. 防火墙或安全软件阻止了连接")
        print("\n建议操作：")
        print("  1. 检查Chrome版本: chrome://version/")
        print("  2. 检查ChromeDriver版本是否与Chrome匹配")
        print("  3. 尝试安装/更新ChromeDriver:")
        print("     pip install webdriver-manager")
        print("     或手动下载匹配的ChromeDriver: https://chromedriver.chromium.org/")
        print("  4. 确保ChromeDriver在系统PATH中，或使用webdriver-manager自动管理")
        print("  5. 关闭所有Chrome窗口，重新以调试模式启动")
        print("  6. 确保使用正确的启动命令（包含 --remote-debugging-port 参数）")
        print("  7. 检查是否有多个Chrome进程在运行")
        print("\n快速测试：")
        print(f"  在浏览器中访问: http://127.0.0.1:{debug_port}/json")
        print("  如果能看到JSON数据，说明调试端口正常，问题在ChromeDriver")
        return None


def wake_up_screen():
    """
    唤醒屏幕（防止休眠导致浏览器无法正常渲染）
    仅在 Windows 系统上有效
    """
    if not WINDOWS_API_AVAILABLE:
        return
    
    try:
        # 方法1：模拟鼠标移动（最小幅度，不会影响用户）
        # 移动到当前位置（实际上不移动）
        win32api.SetCursorPos(win32api.GetCursorPos())
        
        # 方法2：发送一个虚拟按键（不会影响用户操作）
        # 发送一个不常用的虚拟键码（VK_SCROLL，Scroll Lock键）
        # 这个键通常不会影响程序运行
        win32api.keybd_event(0x91, 0, 0, 0)  # VK_SCROLL down
        win32api.keybd_event(0x91, 0, win32con.KEYEVENTF_KEYUP, 0)  # VK_SCROLL up
        
        # 方法3：调用 SetThreadExecutionState 防止系统休眠（更可靠）
        # 需要导入 ctypes
        try:
            import ctypes
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            # 保持系统唤醒，防止屏幕关闭和系统休眠
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        except:
            pass
        
    except Exception:
        # 静默失败，不影响主流程
        pass


def ensure_browser_window_visible(driver, force_activate=False):
    """
    确保浏览器窗口在前台且可见（如果被最小化则恢复）
    
    参数:
        driver: Selenium WebDriver实例
        force_activate: 是否强制激活窗口（用于定时执行时确保窗口在前台）
    """
    if not WINDOWS_API_AVAILABLE:
        return  # 非 Windows 系统或未安装 pywin32，跳过
    
    try:
        # 获取当前窗口句柄
        current_handle = driver.current_window_handle
        
        # 通过窗口类名和标题查找 Chrome 窗口
        # Chrome 主窗口的类名通常是 "Chrome_WidgetWin_1"
        def find_chrome_window():
            """查找 Chrome 浏览器主窗口"""
            def callback(hwnd, windows):
                try:
                    # 检查窗口是否可见（包括最小化的窗口）
                    if not win32gui.IsWindowVisible(hwnd):
                        # 也检查最小化的窗口
                        placement = win32gui.GetWindowPlacement(hwnd)
                        if placement[1] != win32con.SW_SHOWMINIMIZED:
                            return True  # 跳过不可见且非最小化的窗口
                    
                    # 获取窗口类名
                    class_name = win32gui.GetClassName(hwnd)
                    title = win32gui.GetWindowText(hwnd)
                    
                    # Chrome 主窗口的类名通常是 "Chrome_WidgetWin_1"
                    # 排除开发者工具窗口（类名不同）和其他子窗口
                    if class_name == "Chrome_WidgetWin_1" and title:
                        # 检查是否是主窗口（没有父窗口）
                        parent = win32gui.GetParent(hwnd)
                        if parent == 0:  # 顶级窗口
                            # 排除一些特殊窗口（如扩展程序的弹窗等）
                            if not any(exclude in title.lower() for exclude in ['extension', 'devtools', 'popup']):
                                windows.append((hwnd, title, class_name))
                except Exception:
                    pass
                return True
            
            windows = []
            win32gui.EnumWindows(callback, windows)
            return windows
        
        # 查找所有 Chrome 主窗口
        chrome_windows = find_chrome_window()
        
        if chrome_windows:
            # 尝试找到最可能的浏览器主窗口
            # 优先选择标题较长的窗口（通常是包含页面标题的窗口，而不是空白或简短标题）
            chrome_windows.sort(key=lambda x: len(x[1]), reverse=True)
            hwnd, title, class_name = chrome_windows[0]
            
            # 检查窗口是否最小化
            placement = win32gui.GetWindowPlacement(hwnd)
            is_minimized = placement[1] == win32con.SW_SHOWMINIMIZED
            
            if is_minimized:
                # 恢复窗口
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.3)  # 增加等待时间，确保窗口恢复
            
            # 将窗口置于前台（使用多种方法确保成功）
            if force_activate:
                # 强制激活模式：使用多种方法确保窗口在前台
                try:
                    # 方法1：先显示窗口
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                    time.sleep(0.1)
                    
                    # 方法2：尝试置前
                    win32gui.BringWindowToTop(hwnd)
                    time.sleep(0.1)
                    
                    # 方法3：尝试设置为前台窗口（可能失败，但不影响）
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    except:
                        pass
                    
                    # 方法4：再次显示窗口，确保可见
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOWMAXIMIZED if not is_minimized else win32con.SW_RESTORE)
                    time.sleep(0.2)  # 增加等待时间，确保窗口激活完成
                except Exception:
                    pass
            else:
                # 普通模式：只尝试基本激活
                try:
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.1)  # 短暂等待，确保窗口切换完成
                except Exception:
                    # 如果 SetForegroundWindow 失败（Windows 限制），尝试其他方法
                    try:
                        # 先显示窗口，再尝试置前
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                        win32gui.BringWindowToTop(hwnd)
                        time.sleep(0.1)
                    except Exception:
                        pass  # 如果都失败，至少尝试过了
        
    except Exception as e:
        # 窗口管理失败不影响主流程，只打印警告
        pass  # 静默失败，不干扰主程序


def is_following_tab_active(driver):
    """
    检查Following标签是否已激活
    
    参数:
        driver: WebDriver实例
    
    返回:
        bool: Following标签是否已激活
    """
    try:
        # 通过JavaScript检查Following标签是否已激活
        # X的标签页通常使用aria-selected="true"或特定的class来标识激活状态
        is_active = driver.execute_script("""
            // 查找所有包含"Following"文本的tab元素
            var tabs = document.querySelectorAll('div[role="tab"]');
            for (var i = 0; i < tabs.length; i++) {
                var tab = tabs[i];
                if (tab.textContent && tab.textContent.includes('Following')) {
                    // 检查是否已激活（通过aria-selected或class）
                    var ariaSelected = tab.getAttribute('aria-selected');
                    var hasActiveClass = tab.classList.contains('active') || 
                                        tab.classList.contains('selected') ||
                                        tab.getAttribute('data-state') === 'active';
                    if (ariaSelected === 'true' || hasActiveClass) {
                        return true;
                    }
                }
            }
            return false;
        """)
        return is_active
    except:
        return False


def click_following_tab(driver):
    """
    点击Following标签，切换到Following页面
    
    参数:
        driver: WebDriver实例
    
    返回:
        bool: 是否成功点击并切换到Following页面
    """
    try:
        print("正在查找并点击Following标签...")
        
        # 等待页面加载完成
        time.sleep(2)

        # 如果已经在Following标签，避免再次点击触发下拉菜单
        if is_following_tab_active(driver):
            print("✓ 当前已在Following标签，跳过点击")
            return True

        def click_element_left(elem, x_ratio=0.2):
            """在元素左侧偏移位置点击，避免点到右侧下拉选项"""
            try:
                driver.execute_script(
                    """
                    const elem = arguments[0];
                    const ratio = arguments[1];
                    const rect = elem.getBoundingClientRect();
                    const x = rect.left + rect.width * ratio;
                    const y = rect.top + rect.height * 0.5;
                    const opts = {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y};
                    elem.dispatchEvent(new MouseEvent('mousedown', opts));
                    elem.dispatchEvent(new MouseEvent('mouseup', opts));
                    elem.dispatchEvent(new MouseEvent('click', opts));
                    """,
                    elem,
                    x_ratio,
                )
                return True
            except Exception:
                return False
        
        # 尝试多种选择器来定位Following标签
        following_selectors = [
            # 通过role="tab"定位（X的标签页通常使用这个）
            ("xpath", "//div[@role='tab' and contains(., 'Following')]"),
            ("xpath", "//div[@role='tab']//span[contains(text(), 'Following')]/ancestor::div[@role='tab'][1]"),
            ("xpath", "//div[@role='tab']//*[contains(text(), 'Following')]/ancestor::div[@role='tab'][1]"),
            # 通过链接文本定位（CSS选择器）
            ("css", "a[href*='/following']"),
            ("css", "a[href*='/Following']"),
            # 通过data-testid定位（如果X有的话）
            ("css", "a[data-testid*='following']"),
            ("css", "a[data-testid*='Following']"),
            ("css", "div[data-testid*='following']"),
            ("css", "div[data-testid*='Following']"),
            # 通过aria-label定位
            ("css", "a[aria-label*='Following']"),
            ("css", "a[aria-label*='following']"),
            ("css", "div[aria-label*='Following']"),
            ("css", "div[aria-label*='following']"),
            # 通过文本内容定位（使用XPath）
            ("xpath", "//a[contains(text(), 'Following')]"),
            ("xpath", "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'following')]"),
            ("xpath", "//span[contains(text(), 'Following')]/ancestor::a[1]"),
            ("xpath", "//span[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'following')]/ancestor::a[1]"),
            ("xpath", "//span[contains(text(), 'Following')]/ancestor::div[@role='tab'][1]"),
            ("xpath", "//div[contains(text(), 'Following')]"),
            # 通过href属性定位
            ("xpath", "//a[contains(@href, '/following')]"),
            ("xpath", "//a[contains(translate(@href, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '/following')]"),
        ]
        
        # 先尝试各种选择器
        for idx, (selector_type, selector) in enumerate(following_selectors, 1):
            try:
                if selector_type == "xpath":
                    # XPath选择器
                    elements = driver.find_elements(By.XPATH, selector)
                else:
                    # CSS选择器
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                
                if elements:
                    print(f"  选择器 {idx} 找到 {len(elements)} 个元素: {selector[:50]}...")
                    # 找到元素后，选择最可能的一个（通常是第一个）
                    for elem_idx, elem in enumerate(elements):
                        try:
                            # 检查元素是否可见和可点击
                            if elem.is_displayed():
                                # 获取元素文本用于调试
                                try:
                                    elem_text = elem.text[:50] if elem.text else "无文本"
                                    print(f"    尝试元素 {elem_idx+1}: {elem_text}")
                                except:
                                    pass
                                
                                # 滚动到元素位置
                                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", elem)
                                time.sleep(0.5)
                                
                                # 尝试点击元素
                                try:
                                    if not click_element_left(elem):
                                        elem.click()
                                    time.sleep(2)  # 等待页面切换
                                    # 验证Following标签是否已激活
                                    if is_following_tab_active(driver):
                                        print(f"  ✓ 已点击Following标签并验证激活（使用选择器 {idx}，元素 {elem_idx+1}）")
                                        time.sleep(1)  # 额外等待确保内容加载
                                        return True
                                    else:
                                        print(f"    点击后Following标签未激活，尝试JavaScript点击...")
                                        # 如果点击后未激活，尝试JavaScript点击
                                        if not click_element_left(elem):
                                            driver.execute_script("arguments[0].click();", elem)
                                        time.sleep(2)
                                        if is_following_tab_active(driver):
                                            print(f"  ✓ 已通过JavaScript点击Following标签并验证激活（使用选择器 {idx}，元素 {elem_idx+1}）")
                                            time.sleep(1)
                                            return True
                                except Exception as click_error:
                                    # 如果普通点击失败，尝试JavaScript点击
                                    try:
                                        if not click_element_left(elem):
                                            driver.execute_script("arguments[0].click();", elem)
                                        time.sleep(2)
                                        if is_following_tab_active(driver):
                                            print(f"  ✓ 已通过JavaScript点击Following标签并验证激活（使用选择器 {idx}，元素 {elem_idx+1}）")
                                            time.sleep(1)
                                            return True
                                    except Exception as js_error:
                                        print(f"    点击失败: {js_error}")
                                        continue
                        except Exception as e:
                            print(f"    处理元素 {elem_idx+1} 时出错: {e}")
                            continue
            except Exception as e:
                print(f"  选择器 {idx} 执行失败: {e}")
                continue
        
        # 如果所有选择器都失败，尝试通过JavaScript查找并点击
        print("  尝试通过JavaScript查找并点击Following标签...")
        try:
            # 使用JavaScript查找所有包含"Following"的元素
            following_elements = driver.execute_script("""
                // 查找所有可能的Following标签元素
                var candidates = [];
                
                // 1. 查找包含"Following"文本的div[role="tab"]
                var tabs = document.querySelectorAll('div[role="tab"]');
                for (var i = 0; i < tabs.length; i++) {
                    var tab = tabs[i];
                    if (tab.textContent && tab.textContent.includes('Following') && tab.offsetParent !== null) {
                        candidates.push(tab);
                    }
                }
                
                // 2. 查找包含"Following"的链接
                var links = document.querySelectorAll('a[href*="/following"], a[href*="/Following"]');
                for (var i = 0; i < links.length; i++) {
                    var link = links[i];
                    if (link.offsetParent !== null) {
                        candidates.push(link);
                    }
                }
                
                // 3. 查找包含"Following"文本的所有元素
                var allElements = document.querySelectorAll('*');
                for (var i = 0; i < allElements.length; i++) {
                    var el = allElements[i];
                    if (el.textContent && el.textContent.trim() === 'Following' && el.offsetParent !== null) {
                        // 优先选择可点击的元素
                        if (el.tagName === 'A' || el.tagName === 'BUTTON' || el.getAttribute('role') === 'tab' || el.onclick) {
                            candidates.push(el);
                        }
                    }
                }
                
                return candidates.length > 0 ? candidates[0] : null;
            """)
            
            if following_elements:
                driver.execute_script("arguments[0].click();", following_elements)
                time.sleep(2)
                # 验证Following标签是否已激活
                if is_following_tab_active(driver):
                    print(f"  ✓ 已通过JavaScript点击Following标签并验证激活")
                    time.sleep(1)
                    return True
                else:
                    print(f"  JavaScript点击后Following标签未激活")
            else:
                print(f"  JavaScript未找到Following标签元素")
        except Exception as e:
            print(f"  JavaScript点击失败: {e}")
            import traceback
            traceback.print_exc()
        
        print("  ✗ 未能找到或点击Following标签")
        return False
        
    except Exception as e:
        print(f"  ✗ 点击Following标签时出错: {e}")
        import traceback
        traceback.print_exc()
        return False


def find_following_page(driver):
    """
    查找X的Following页面
    
    参数:
        driver: WebDriver实例
    
    返回:
        bool: 是否找到Following页面
    """
    try:
        # 获取所有窗口句柄
        handles = driver.window_handles
        
        for handle in handles:
            driver.switch_to.window(handle)
            current_url = driver.current_url
            
            # 检查是否是X的following页面
            if 'x.com' in current_url.lower() or 'twitter.com' in current_url.lower():
                if 'following' in current_url.lower():
                    print(f"✓ 找到Following页面: {current_url}")
                    return True
        
        # 如果没有找到，尝试在当前标签页中导航
        print("正在查找Following页面...")
        for handle in handles:
            driver.switch_to.window(handle)
            current_url = driver.current_url
            
            if 'x.com' in current_url.lower() or 'twitter.com' in current_url.lower():
                # 尝试导航到following页面
                following_url = current_url.split('/')[0] + '//' + current_url.split('/')[2] + '/following'
                driver.get(following_url)
                time.sleep(3)
                print(f"✓ 已导航到Following页面: {following_url}")
                return True
        
        print("✗ 未找到X的Following页面")
        return False
        
    except Exception as e:
        print(f"✗ 查找Following页面时出错: {e}")
        return False


def check_cloudflare_error(driver):
    """
    检查当前页面是否是Cloudflare错误页面
    
    参数:
        driver: WebDriver实例
    
    返回:
        bool: 如果是Cloudflare错误页面返回True，否则返回False
    """
    try:
        page_source = driver.page_source.lower()
        # 检测多种Cloudflare错误标识
        cloudflare_indicators = [
            "cloudflare" in page_source and "error" in page_source,
            "cloudflare" in page_source and "500" in page_source,
            "internal server error" in page_source and "error code 500" in page_source,
            "cloudflare.com" in page_source and "error" in page_source
        ]
        return any(cloudflare_indicators)
    except:
        return False


def refresh_page(driver):
    """
    刷新页面（强制刷新，绕过缓存，获取最新推文），并等待页面加载完成
    刷新前会先检查是否有Cloudflare错误，如果有则跳过刷新
    刷新前会先滚动到页面顶部，刷新后也会确保滚动到顶部
    
    参数:
        driver: WebDriver实例
    
    抛出:
        Exception: 如果检测到Cloudflare错误，抛出异常
    """
    # 刷新前先检查当前页面是否已经是Cloudflare错误页面
    if check_cloudflare_error(driver):
        raise Exception("Cloudflare错误：当前页面已是错误页面，跳过刷新以避免触发保护机制")
    
    try:
        # 先滚动到页面顶部
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)  # 等待滚动完成，增加延迟模拟人类行为
        
        # 获取当前URL，用于重新导航（这样可以绕过缓存，获取最新内容）
        current_url = driver.current_url
        print(f"当前页面URL: {current_url}")
        
        # 方法1：使用JavaScript强制刷新（清除缓存并重新加载）
        print("尝试方法1：使用JavaScript强制刷新（清除缓存）...")
        try:
            # 先清除可能的缓存相关存储（可选，避免影响登录状态）
            # 注意：不清除localStorage和sessionStorage，因为可能包含登录信息
            # 只清除缓存相关的HTTP缓存
            
            # 使用JavaScript强制刷新，绕过HTTP缓存
            # 通过添加时间戳参数来强制浏览器获取最新内容
            driver.execute_script("""
                // 强制刷新，绕过缓存
                var url = new URL(window.location.href);
                // 更新时间戳参数（如果已存在）或添加新的时间戳参数
                url.searchParams.set('_t', Date.now());
                window.location.href = url.toString();
            """)
            print("  ✓ JavaScript强制刷新已执行")
        except Exception as e1:
            print(f"  方法1失败: {e1}")
            # 方法2：使用driver.get()重新导航（这会绕过缓存）
            print("尝试方法2：使用driver.get()重新导航（绕过缓存）...")
            try:
                # 添加时间戳参数确保获取最新内容
                if '?' in current_url:
                    refresh_url = current_url + '&_t=' + str(int(time.time() * 1000))
                else:
                    refresh_url = current_url + '?_t=' + str(int(time.time() * 1000))
                driver.get(refresh_url)
                print(f"  ✓ 已重新导航到: {refresh_url}")
            except Exception as e2:
                print(f"  方法2失败: {e2}")
                # 方法3：使用driver.refresh()作为最后备选
                print("尝试方法3：使用driver.refresh()...")
                try:
                    driver.refresh()
                    print("  ✓ driver.refresh()已执行")
                except Exception as e3:
                    print(f"  方法3失败: {e3}")
                    # 方法4：尝试使用键盘快捷键（最后备选）
                    print("尝试方法4：使用键盘快捷键Ctrl+F5...")
                    try:
                        body = driver.find_element(By.TAG_NAME, "body")
                        body.click()
                        time.sleep(0.5)
                        body.send_keys(Keys.CONTROL + Keys.F5)
                        print("  ✓ Ctrl+F5已执行")
                    except Exception as e4:
                        print(f"  方法4失败: {e4}")
                        raise Exception("所有刷新方法都失败了")
        
        # 等待页面开始加载（增加延迟，模拟人类行为）
        print("等待页面开始加载...")
        time.sleep(4)  # 延长等待时间，模拟正常浏览
        
        # 检查是否出现Cloudflare错误页面
        if check_cloudflare_error(driver):
            raise Exception("Cloudflare错误：刷新后页面出现错误，停止操作以避免触发保护机制")
        
        # 等待页面加载完成 - 等待推文元素出现
        print("等待页面加载完成...")
        wait = WebDriverWait(driver, 20)  # 增加等待时间到20秒
        
        # 尝试等待推文元素出现（表示页面已加载）
        try:
            # 等待至少一个推文元素出现
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-testid="tweet"], article[role="article"], div[data-testid="tweet"]')))
            print("✓ 页面已刷新并加载完成")
        except TimeoutException:
            # 如果等待超时，检查是否是Cloudflare错误
            if check_cloudflare_error(driver):
                raise Exception("Cloudflare错误：页面加载失败")
            else:
                # 如果等待超时，至少等待一段时间确保页面有响应
                print("  ⚠️ 等待推文元素超时，继续等待...")
                time.sleep(5)
        
        # 确保滚动条在页面顶部
        print("确保滚动条在页面顶部...")
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)  # 延长延迟，模拟正常浏览
        
        # 额外等待一段时间，确保动态内容加载完成，并模拟阅读时间
        # 这对于X（Twitter）特别重要，因为推文是动态加载的
        print("等待动态内容加载...")
        time.sleep(6)  # 延长等待时间，确保最新推文加载完成
        print("✓ 页面刷新完成，滚动条已回到顶部，最新推文应已加载")
    except Exception as e:
        print(f"✗ 刷新页面时出错: {e}")
        # 即使出错，也尝试滚动到顶部
        try:
            driver.execute_script("window.scrollTo(0, 0);")
        except:
            pass
        # 如果是Cloudflare错误，重新抛出异常
        if "Cloudflare" in str(e):
            raise


def parse_time_string(time_str):
    """
    解析X的时间格式，转换为实际日期时间（北京时间）
    
    支持的格式：
    - "55m" -> 55分钟前
    - "3h" -> 3小时前
    - "Nov 14" -> 11月14日（当年）
    - "Nov 14, 2023" -> 2023年11月14日
    
    参数:
        time_str: 时间字符串
    
    返回:
        datetime: 解析后的日期时间对象（北京时间，无时区信息），如果解析失败返回None
    """
    if not time_str:
        return None
    
    time_str = time_str.strip()
    
    # 如果字符串太长，明显不是时间格式，直接返回None（不打印警告）
    if len(time_str) > 50:
        return None
    
    # 使用北京时间（UTC+8）的当前时间
    beijing_tz = timezone(timedelta(hours=8))
    now = datetime.now(beijing_tz).replace(tzinfo=None)
    
    try:
        # 处理分钟格式：如 "55m", "5m"
        if time_str.endswith('m') and time_str[:-1].isdigit():
            minutes = int(time_str[:-1])
            return now - timedelta(minutes=minutes)
        
        # 处理小时格式：如 "3h", "12h"
        if time_str.endswith('h') and time_str[:-1].isdigit():
            hours = int(time_str[:-1])
            return now - timedelta(hours=hours)
        
        # 处理天数格式：如 "2d", "5d"
        if time_str.endswith('d') and time_str[:-1].isdigit():
            days = int(time_str[:-1])
            return now - timedelta(days=days)
        
        # 处理日期格式：如 "Nov 14", "Nov 14, 2023"
        # 月份映射
        month_map = {
            'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
            'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
        }
        
        # 匹配 "Nov 14" 或 "Nov 14, 2023"
        date_pattern = r'^([A-Za-z]{3})\s+(\d{1,2})(?:,\s+(\d{4}))?$'
        match = re.match(date_pattern, time_str)
        
        if match:
            month_name = match.group(1)
            day = int(match.group(2))
            year = int(match.group(3)) if match.group(3) else now.year
            
            if month_name in month_map:
                month = month_map[month_name]
                return datetime(year, month, day)
        
        # 如果无法解析，且字符串较短（可能是时间格式但解析失败），才打印警告
        if len(time_str) <= 20:
            print(f"警告：无法解析时间格式: {time_str}")
        return None
        
    except Exception as e:
        # 只在字符串较短时打印错误（避免打印推文内容）
        if len(time_str) <= 20:
            print(f"解析时间时出错: {time_str}, 错误: {e}")
        return None


# ---------------------------------------------------------------------------
# Truth 转发监控：指定 X 主页 + A 股交易日 + 北京时间时段内轮询
# ---------------------------------------------------------------------------

DEFAULT_TRUTH_PROFILE_URL = os.environ.get(
    "TRUTH_X_PROFILE_URL",
    "https://x.com/TrumpDailyPosts",
)


def beijing_now_naive() -> datetime:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def is_likely_cn_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    try:
        import chinese_calendar as cc  # type: ignore

        return bool(cc.is_workday(d))
    except Exception:
        return True


def wall_time_in_session(t: dt_time, start_h: int, start_m: int, end_h: int, end_m: int) -> bool:
    start = dt_time(start_h, start_m)
    end = dt_time(end_h, end_m)
    return start <= t <= end


def is_post_in_session(
    parsed: Optional[datetime],
    session_calendar_date: date,
    start_h: int,
    start_m: int,
    end_h: int,
    end_m: int,
) -> bool:
    if parsed is None:
        return False
    if parsed.date() != session_calendar_date:
        return False
    return wall_time_in_session(parsed.time(), start_h, start_m, end_h, end_m)


def seconds_until_next_morning_session(now_bj: datetime, sh: int, sm: int) -> float:
    """次日（或之后）首个「疑似交易日」的 session 开始时刻（北京），与 now 的秒差（下限 60）。"""
    next_d = now_bj.date() + timedelta(days=1)
    for _ in range(14):
        if is_likely_cn_trading_day(next_d):
            target = datetime.combine(next_d, dt_time(sh, sm))
            return max(60.0, (target - now_bj).total_seconds())
        next_d += timedelta(days=1)
    return 3600.0


def sleep_until_session_or_poll(now_bj: datetime, poll_sec: int, start_h: int, start_m: int) -> float:
    """返回建议休眠秒数：未到当日开始则睡到开始；否则为 poll_sec。"""
    start_today = datetime.combine(now_bj.date(), dt_time(start_h, start_m))
    if now_bj < start_today:
        return max(10.0, (start_today - now_bj).total_seconds())
    return float(poll_sec)


def _get_status_id_from_article(article) -> Optional[str]:
    try:
        for link in article.find_elements(By.CSS_SELECTOR, 'a[href*="/status/"]'):
            href = link.get_attribute("href") or ""
            m = re.search(r"/status/(\d+)", href)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _parse_article_time_beijing(article) -> Tuple[Optional[datetime], str]:
    for time_elem in article.find_elements(By.CSS_SELECTOR, "time"):
        datetime_attr = time_elem.get_attribute("datetime")
        if datetime_attr:
            try:
                dt_str = datetime_attr.replace("Z", "+00:00")
                utc_time = datetime.fromisoformat(dt_str)
                bj = timezone(timedelta(hours=8))
                parsed = utc_time.astimezone(bj).replace(tzinfo=None)
                return parsed, parsed.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
        ts = (time_elem.text or "").strip()
        if ts:
            pt = parse_time_string(ts)
            if pt:
                return pt, pt.strftime("%Y-%m-%d %H:%M:%S")
    return None, "未知时间"


def _expand_article_show_more(article) -> None:
    """
    只在「主推文正文」容器内点击 show more / 显示更多，避免误点页面其它 show more。
    该函数为尽力而为：找不到就跳过，点击失败也不抛异常。
    """
    try:
        # 先定位主推文正文节点（引用推文也可能有 tweetText，但通常主推文在前）
        text_nodes = article.find_elements(By.CSS_SELECTOR, 'div[data-testid="tweetText"]')
        if not text_nodes:
            return
        main_text = text_nodes[0]

        # 在正文节点的较小范围内找“显示更多/Show more”
        # X 上该控件可能是 span/div/button，且经常带 role="button"
        candidates = []
        try:
            candidates.extend(
                main_text.find_elements(
                    By.XPATH,
                    ".//*[(@role='button' or self::button) and (contains(., 'Show more') or contains(., 'show more') or contains(., '显示更多'))]",
                )
            )
        except Exception:
            pass
        if not candidates:
            # 退一步：在正文所在的父容器里找（仍然限制在推文内部，不扫整篇 article）
            try:
                parent = main_text.find_element(By.XPATH, "./ancestor::div[1]")
                candidates = parent.find_elements(
                    By.XPATH,
                    ".//*[(@role='button' or self::button) and (contains(., 'Show more') or contains(., 'show more') or contains(., '显示更多'))]",
                )
            except Exception:
                candidates = []
        if not candidates:
            return

        btn = candidates[0]
        try:
            btn.click()
        except Exception:
            try:
                # JS 点击兜底（有时被遮挡）
                drv = getattr(btn, "_parent", None)
                if drv is not None:
                    drv.execute_script("arguments[0].click();", btn)
            except Exception:
                return
        time.sleep(0.35)
    except Exception:
        return


def _quick_tweet_text(article) -> str:
    _expand_article_show_more(article)
    for sel in ('div[data-testid="tweetText"]', 'span[data-testid="tweetText"]'):
        try:
            el = article.find_element(By.CSS_SELECTOR, sel)
            t = el.text.strip()
            if t:
                return t
        except Exception:
            continue
    try:
        return (article.text or "").strip()[:800]
    except Exception:
        return ""


def extract_truth_timeline_posts(driver, max_items: int = 40) -> List[dict]:
    """从当前 X 页面可见时间线提取帖子（含 status id、北京时间、正文片段）。"""
    selectors = [
        'article[data-testid="tweet"]',
        'article[role="article"]',
        'div[data-testid="tweet"]',
    ]
    articles = []
    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                articles = els
                break
        except Exception:
            continue
    by_id: Dict[str, dict] = {}
    for article in articles[:max_items]:
        sid = _get_status_id_from_article(article)
        if not sid:
            continue
        parsed, time_display = _parse_article_time_beijing(article)
        content = _quick_tweet_text(article)
        by_id[sid] = {
            "status_id": sid,
            "parsed_time": parsed,
            "time_display": time_display,
            "content": content,
        }
    return list(by_id.values())


def _load_seen_ids(path: Path) -> set:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
        if isinstance(data, dict) and "ids" in data:
            return {str(x) for x in data["ids"]}
    except Exception:
        pass
    return set()


def _save_seen_ids(path: Path, ids: set) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(sorted(ids), ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"⚠️ 写入状态文件失败: {e}")


_translate_lib_warned = False


def translate_text_to_zh(text: str, chunk_size: int = 3800) -> Tuple[Optional[str], str]:
    """
    将贴文译为简体中文（Google 翻译，经 deep_translator，无需 API Key）。

    Returns:
        (译文, 状态): 状态为 'ok' | 'empty' | 'no_lib' | 'error'
    """
    if not text or not str(text).strip():
        return None, "empty"
    try:
        from deep_translator import GoogleTranslator  # type: ignore[import-untyped]
    except ImportError:
        return None, "no_lib"
    tr = GoogleTranslator(source="auto", target="zh-CN")
    s = str(text).strip()
    try:
        if len(s) <= chunk_size:
            return tr.translate(s), "ok"
        parts: List[str] = []
        for i in range(0, len(s), chunk_size):
            seg = s[i : i + chunk_size]
            parts.append(tr.translate(seg))
            time.sleep(0.25)
        return "\n".join(parts), "ok"
    except Exception as e:
        return None, f"error:{e}"


def navigate_truth_profile(driver, profile_url: str) -> None:
    sep = "&" if "?" in profile_url else "?"
    url = f"{profile_url}{sep}_t={int(time.time() * 1000)}"
    driver.get(url)
    time.sleep(4)
    wait = WebDriverWait(driver, 20)
    try:
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'article[data-testid="tweet"], article[role="article"], div[data-testid="tweet"]')
            )
        )
    except TimeoutException:
        print("⚠️ 等待推文元素超时，仍尝试解析当前页面")
    time.sleep(2)


def _truth_monitor_apply_new_posts(
    posts: List[dict],
    seen: set,
    state_path: Path,
    today: date,
    session_start: Tuple[int, int],
    session_end: Tuple[int, int],
    strict_session: bool,
    enable_translate: bool = True,
) -> int:
    """根据 strict_session 过滤后打印新帖、写入 seen，返回本批新帖数量。"""
    global _translate_lib_warned
    sh, sm = session_start
    eh, em = session_end
    new_count = 0
    for p in posts:
        sid = p["status_id"]
        if sid in seen:
            continue
        pt = p["parsed_time"]
        if strict_session:
            if not is_post_in_session(pt, today, sh, sm, eh, em):
                continue
        seen.add(sid)
        new_count += 1
        print("\n" + "=" * 72)
        print(f"【新帖】北京时间 {p['time_display']}  status={sid}")
        print("-" * 72)
        raw = p.get("content") or "(无正文)"
        print("【原文】")
        print(raw)
        if enable_translate and raw != "(无正文)":
            zh, st = translate_text_to_zh(raw)
            print("-" * 72)
            print("【中文】")
            if zh:
                print(zh)
            elif st == "no_lib":
                if not _translate_lib_warned:
                    print("（未安装 deep-translator，无法自动翻译。请执行: pip install deep-translator）")
                    _translate_lib_warned = True
                else:
                    print("（未安装翻译依赖，见上文提示）")
            elif st.startswith("error:"):
                print(f"（翻译失败: {st[6:]}）")
            else:
                print("（跳过翻译）")
        print("=" * 72 + "\n")
        _save_seen_ids(state_path, seen)
    return new_count


def run_truth_monitor_loop(
    profile_url: str,
    debug_port: int = 9222,
    poll_interval_sec: int = 900,
    session_start: Tuple[int, int] = (9, 0),
    session_end: Tuple[int, int] = (15, 0),
    state_path: Optional[Path] = None,
    strict_session: bool = False,
    enable_translate: bool = True,
) -> None:
    """
    默认 strict_session=False：调试友好——不限交易日与时段，程序一运行就先解析一次时间线，之后按间隔刷新。
    strict_session=True：仅在 A 股疑似交易日、北京时间 [session_start, session_end] 内轮询，且只提示该时段内发出的帖。
    """
    sh, sm = session_start
    eh, em = session_end
    if state_path is None:
        state_path = Path(__file__).resolve().parent / "truth_monitor_seen_ids.json"
    seen = _load_seen_ids(state_path)
    print("=" * 72)
    print("Truth 转发监控（X 时间线）")
    print(f"  主页: {profile_url}")
    if strict_session:
        print(f"  模式: 严格（A 股疑似交易日 + 北京 {sh:02d}:{sm:02d}–{eh:02d}:{em:02d} 内发帖才提示）")
    else:
        print("  模式: 宽松（调试）— 不限交易日/时段，凡未记录过的帖子即提示")
    print(f"  中文翻译: {'开启（deep_translator → 简体）' if enable_translate else '关闭'}")
    print(f"  轮询间隔: {poll_interval_sec // 60} 分钟")
    print(f"  已记住帖子数: {len(seen)}  （状态文件: {state_path}）")
    print("=" * 72)

    driver = connect_to_existing_browser(debug_port=debug_port)
    if not driver:
        return
    wake_up_screen()
    ensure_browser_window_visible(driver, force_activate=True)
    time.sleep(0.5)

    try:
        navigate_truth_profile(driver, profile_url)
    except Exception as e:
        print(f"⚠️ 首次打开主页失败: {e}")

    # 启动后立即搜索一次（不先走 refresh_page，使用刚加载的主页）
    now_bj = beijing_now_naive()
    print(f"\n[启动] 立即搜索一次（{now_bj:%Y-%m-%d %H:%M} 北京）…")
    try:
        posts0 = extract_truth_timeline_posts(driver, max_items=40)
        n0 = _truth_monitor_apply_new_posts(
            posts0,
            seen,
            state_path,
            now_bj.date(),
            session_start,
            session_end,
            strict_session,
            enable_translate,
        )
        if n0 == 0:
            print(
                f"[启动] 本轮无新增（共解析 {len(posts0)} 条可见"
                + ("，严格模式下须落在当日监控时段内" if strict_session else "")
                + "）"
            )
    except Exception as e:
        print(f"[启动] 首次解析失败: {e}")

    while True:
        try:
            now_bj = beijing_now_naive()
            if strict_session:
                if not is_likely_cn_trading_day(now_bj.date()):
                    print(f"[{now_bj:%Y-%m-%d %H:%M}] 非疑似 A 股交易日，休眠 1 小时")
                    time.sleep(3600)
                    continue

                if not wall_time_in_session(now_bj.time(), sh, sm, eh, em):
                    if now_bj.time() > dt_time(eh, em):
                        sec = seconds_until_next_morning_session(now_bj, sh, sm)
                        print(f"[{now_bj:%Y-%m-%d %H:%M}] 已过当日监控结束时间，约休眠 {sec / 3600:.1f} 小时")
                        time.sleep(min(sec, 3600))
                    else:
                        sec = sleep_until_session_or_poll(now_bj, min(poll_interval_sec, 300), sh, sm)
                        print(f"[{now_bj:%Y-%m-%d %H:%M}] 未到监控开始时间，休眠 {sec / 60:.1f} 分钟")
                        time.sleep(sec)
                    continue

            wake_up_screen()
            ensure_browser_window_visible(driver, force_activate=False)
            try:
                if check_cloudflare_error(driver):
                    print(f"[{now_bj:%H:%M}] Cloudflare 拦截页，跳过刷新，{poll_interval_sec}s 后再试")
                    time.sleep(poll_interval_sec)
                    continue
                refresh_page(driver)
            except Exception as e:
                if "Cloudflare" in str(e) or check_cloudflare_error(driver):
                    print(f"[{now_bj:%H:%M}] 刷新被 Cloudflare 拦截: {e}")
                else:
                    print(f"[{now_bj:%H:%M}] 刷新失败，尝试直接打开主页: {e}")
                    try:
                        navigate_truth_profile(driver, profile_url)
                    except Exception as e2:
                        print(f"  打开主页仍失败: {e2}")
                time.sleep(min(300, poll_interval_sec))
                continue

            posts = extract_truth_timeline_posts(driver, max_items=40)
            today = now_bj.date()
            new_count = _truth_monitor_apply_new_posts(
                posts,
                seen,
                state_path,
                today,
                session_start,
                session_end,
                strict_session,
                enable_translate,
            )

            if new_count == 0:
                hint = "时段内且未提示过的帖子" if strict_session else "未提示过的帖子"
                print(f"[{now_bj:%Y-%m-%d %H:%M}] 本轮无新增（{hint}）共解析 {len(posts)} 条可见")

            time.sleep(poll_interval_sec)

        except KeyboardInterrupt:
            print("\n用户中断，已退出监控")
            _save_seen_ids(state_path, seen)
            break
        except Exception as e:
            print(f"监控循环异常: {e}\n{traceback.format_exc()}")
            time.sleep(min(120, poll_interval_sec))


def normalize_image_url(image_url):
    """
    规范化图片URL，移除尺寸参数，只保留核心URL用于去重
    
    参数:
        image_url: 原始图片URL
    
    返回:
        str: 规范化后的URL
    """
    if not image_url:
        return ""
    
    try:
        # 解析URL
        parsed = urllib.parse.urlparse(image_url)
        
        # 对于Twitter的图片URL，移除name和format参数，只保留核心部分
        # 例如: https://pbs.twimg.com/media/xxx?format=jpg&name=large
        # 规范化后: https://pbs.twimg.com/media/xxx
        if 'pbs.twimg.com' in parsed.netloc and '/media/' in parsed.path:
            # 只保留协议、域名和路径，移除查询参数
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            return normalized
        
        # 对于其他URL，也移除查询参数
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return normalized
    except:
        return image_url


def download_image(image_url, save_dir="x_images", tweet_id=None, downloaded_urls=None):
    """
    下载图片并保存到指定目录（支持URL去重）
    
    参数:
        image_url: 图片URL
        save_dir: 保存目录，默认为 "x_images"
        tweet_id: 推文ID（用于生成文件名），如果为None则使用时间戳
        downloaded_urls: 已下载URL的映射字典 {规范化URL: 文件路径}，用于去重
    
    返回:
        str: 保存的文件名（相对路径），如果下载失败返回None
    """
    if not image_url:
        return None
    
    # 规范化URL用于去重检查
    normalized_url = normalize_image_url(image_url)
    
    # 如果提供了已下载URL映射，先检查是否已下载过
    if downloaded_urls is not None and normalized_url in downloaded_urls:
        existing_file = downloaded_urls[normalized_url]
        # 检查文件是否仍然存在
        if os.path.exists(existing_file):
            return existing_file
        else:
            # 文件不存在了，从映射中移除
            del downloaded_urls[normalized_url]
    
    try:
        # 确保保存目录存在
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        
        # 生成文件名：使用URL的hash生成文件名（基于规范化URL）
        # 这样相同图片总是有相同的文件名，不依赖于推文ID
        url_hash = hashlib.md5(normalized_url.encode()).hexdigest()[:12]  # 使用12位hash提高唯一性
        parsed_url = urllib.parse.urlparse(image_url)
        path = parsed_url.path
        ext = os.path.splitext(path)[1] or '.jpg'
        # 清理扩展名，只保留常见图片格式
        if ext.lower() not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            ext = '.jpg'
        filename = f"{url_hash}{ext}"
        
        filepath = os.path.join(save_dir, filename)
        
        # 如果文件已存在，跳过下载
        if os.path.exists(filepath):
            # 更新已下载URL映射
            if downloaded_urls is not None:
                downloaded_urls[normalized_url] = filepath
            return filepath
        
        # 下载图片
        # 设置User-Agent，模拟浏览器请求
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        request = urllib.request.Request(image_url, headers=headers)
        
        with urllib.request.urlopen(request, timeout=10) as response:
            image_data = response.read()
            
            # 保存图片
            with open(filepath, 'wb') as f:
                f.write(image_data)
        
        # 更新已下载URL映射
        if downloaded_urls is not None:
            downloaded_urls[normalized_url] = filepath
        
        return filepath
    
    except Exception as e:
        print(f"  下载图片失败 ({image_url[:50]}...): {e}")
        return None


def capture_video_screenshot(driver, video_element, save_dir="x_images", tweet_id=None, skip_scroll=False):
    """
    截取视频元素的截图并保存
    
    参数:
        driver: WebDriver实例
        video_element: 视频元素（WebElement）
        save_dir: 保存目录，默认为 "x_images"
        tweet_id: 推文ID（用于生成文件名），如果为None则使用时间戳
        skip_scroll: 是否跳过滚动操作（避免干扰主滚动逻辑），默认False
    
    返回:
        str: 保存的文件名（相对路径），如果截图失败返回None
    """
    if not video_element:
        return None
    
    try:
        # 确保保存目录存在
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        
        # 生成文件名
        if tweet_id:
            filename = f"{tweet_id}_video.jpg"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_video.jpg"
        
        filepath = os.path.join(save_dir, filename)
        
        # 如果文件已存在，跳过截图
        if os.path.exists(filepath):
            return filepath
        
        # 检查视频元素是否在视口内（避免不必要的滚动）
        try:
            # 获取视频元素位置
            video_location = video_element.location
            video_size = video_element.size
            # 获取视口大小
            viewport_height = driver.execute_script("return window.innerHeight;")
            viewport_width = driver.execute_script("return window.innerWidth;")
            scroll_y = driver.execute_script("return window.pageYOffset;")
            scroll_x = driver.execute_script("return window.pageXOffset;")
            
            # 计算视频是否在视口内
            video_top = video_location['y']
            video_bottom = video_top + video_size['height']
            viewport_top = scroll_y
            viewport_bottom = scroll_y + viewport_height
            
            is_in_viewport = (video_top >= viewport_top and video_top < viewport_bottom) or \
                           (video_bottom > viewport_top and video_bottom <= viewport_bottom) or \
                           (video_top < viewport_top and video_bottom > viewport_bottom)
            
            # 如果不在视口内且允许滚动，才滚动
            if not is_in_viewport and not skip_scroll:
                # 使用instant滚动，避免平滑滚动干扰
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});", video_element)
                time.sleep(0.3)  # 减少等待时间
            elif skip_scroll:
                # 如果跳过滚动且不在视口内，直接返回None
                return None
        except:
            # 如果检查失败，尝试直接截图（可能已经在视口内）
            pass
        
        # 截取视频元素的截图
        screenshot_data = video_element.screenshot_as_png
        
        # 保存截图
        with open(filepath, 'wb') as f:
            f.write(screenshot_data)
        
        return filepath
    
    except Exception as e:
        # 静默失败，避免影响主流程
        return None


def _extract_tweets_from_page(driver, downloaded_urls=None, seen_contents=None):
    """
    从当前页面提取所有推文（辅助函数）
    
    参数:
        driver: WebDriver实例
        downloaded_urls: 已下载URL的映射字典 {规范化URL: 文件路径}，用于去重
        seen_contents: 已见过的推文内容hash集合，用于跳过重复推文的图片处理
    
    返回:
        list: 推文列表，每个元素为 (账号名, 时间, 内容, 图片文件列表)
    """
    tweets = []
    if seen_contents is None:
        seen_contents = set()
    
    # X的推文结构可能使用article标签或特定的class
    # 尝试多种选择器
    selectors = [
        'article[data-testid="tweet"]',
        'article[role="article"]',
        'div[data-testid="tweet"]',
        'article',
    ]
    
    tweet_elements = []
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements and len(elements) > 0:
                tweet_elements = elements
                break
        except Exception as e:
            continue
    
    if not tweet_elements:
        return tweets
    
    # 不再在页面级批量点击 "show more"：页面上（含主栏内）的 show more 可能包含会切换 For You/Following 或离开首页的控件，容易误触。只在下文每条推文内部展开推文内容的 show more。
    
    for tweet_element in tweet_elements:
        try:
            # 提取账号名
            username = "未知"
            username_selectors = [
                'div[data-testid="User-Name"] span',
                'span[data-testid="User-Name"]',
                'a[role="link"] span',
                'div[dir="ltr"] span',
            ]
            
            for selector in username_selectors:
                try:
                    username_elems = tweet_element.find_elements(By.CSS_SELECTOR, selector)
                    for elem in username_elems:
                        text = elem.text.strip()
                        # 账号名通常不包含@符号，且长度合理
                        if text and len(text) > 0 and len(text) < 50 and '@' not in text:
                            username = text
                            break
                    if username != "未知":
                        break
                except:
                    continue
            
            # 如果还是没找到，尝试从链接中提取
            if username == "未知":
                try:
                    links = tweet_element.find_elements(By.CSS_SELECTOR, 'a[href*="/"]')
                    for link in links:
                        href = link.get_attribute('href')
                        if href and ('/status/' in href or '/following' in href):
                            # 从URL中提取用户名
                            parts = href.split('/')
                            if len(parts) > 1:
                                potential_username = parts[-2] if '/status/' in href else parts[-1]
                                if potential_username and not potential_username.startswith('http'):
                                    username = potential_username
                                    break
                except:
                    pass
            
            # 提取时间
            time_str = ""
            parsed_time = None
            
            time_selectors = [
                'time',
                'a[role="link"] time',
                'a[href*="/status/"] time',
                'span[data-testid="User-Name"] + span',
            ]
            
            for selector in time_selectors:
                try:
                    time_elems = tweet_element.find_elements(By.CSS_SELECTOR, selector)
                    for time_elem in time_elems:
                        # 优先获取datetime属性（ISO格式）
                        datetime_attr = time_elem.get_attribute('datetime')
                        if datetime_attr:
                            # 如果是ISO格式，直接解析
                            try:
                                # 处理ISO格式时间（X/Twitter返回的是UTC时间）
                                dt_str = datetime_attr.replace('Z', '+00:00')
                                # 解析为带时区的datetime对象（UTC）
                                utc_time = datetime.fromisoformat(dt_str)
                                # 转换为北京时间（UTC+8）
                                beijing_tz = timezone(timedelta(hours=8))
                                parsed_time = utc_time.astimezone(beijing_tz).replace(tzinfo=None)
                                time_str = datetime_attr  # 保存原始值用于显示
                                break
                            except:
                                time_str = datetime_attr
                        
                        # 尝试title属性
                        if not time_str:
                            time_str = time_elem.get_attribute('title')
                        
                        # 最后尝试文本内容
                        if not time_str:
                            time_str = time_elem.text.strip()
                        
                        if time_str:
                            break
                    if time_str:
                        break
                except:
                    continue
            
            # 如果没有找到时间，尝试从文本中提取（但要避免匹配到推文内容）
            if not time_str:
                try:
                    # 查找包含时间信息的span，但限制在用户名区域附近
                    # 避免从推文内容区域提取
                    user_name_area = tweet_element.find_elements(By.CSS_SELECTOR, 'span[data-testid="User-Name"]')
                    if user_name_area:
                        # 只在用户名区域附近查找时间
                        parent = user_name_area[0].find_element(By.XPATH, './ancestor::div[1]')
                        time_spans = parent.find_elements(By.CSS_SELECTOR, 'span')
                    else:
                        # 如果没有找到用户名区域，则从整个元素中查找，但要更严格
                        time_spans = tweet_element.find_elements(By.CSS_SELECTOR, 'span')
                    
                    for span in time_spans:
                        text = span.text.strip()
                        # 更严格的时间格式检查：
                        # 1. 必须是短字符串（时间通常很短，推文内容很长）
                        # 2. 必须完全匹配时间格式，而不是部分匹配
                        if len(text) <= 20:  # 时间字符串通常很短
                            # 检查是否完全匹配时间格式（相对时间或日期格式）
                            if re.match(r'^\d+[mhd]$', text):  # 完全匹配 "55m", "3h", "2d"
                                time_str = text
                                break
                            elif re.match(r'^[A-Za-z]{3}\s+\d{1,2}(?:,\s+\d{4})?$', text):  # 完全匹配 "Nov 14" 或 "Nov 14, 2023"
                                time_str = text
                                break
                except:
                    pass
            
            # 解析时间
            if parsed_time:
                # 已经从ISO格式解析成功
                time_display = parsed_time.strftime("%Y-%m-%d %H:%M:%S")
            elif time_str and re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', time_str):
                # 已经是格式化后的时间字符串
                time_display = time_str
            elif time_str:
                # 验证 time_str 是否真的是时间格式（避免推文内容被误识别为时间）
                # 时间字符串应该很短，且符合特定格式
                is_valid_time = False
                if len(time_str) <= 20:  # 时间字符串通常很短
                    # 检查是否符合时间格式
                    if re.match(r'^\d+[mhd]$', time_str) or \
                       re.match(r'^[A-Za-z]{3}\s+\d{1,2}(?:,\s+\d{4})?$', time_str) or \
                       re.match(r'^\d{4}-\d{2}-\d{2}', time_str):
                        is_valid_time = True
                
                if is_valid_time:
                    # 需要解析相对时间或日期格式
                    parsed_time = parse_time_string(time_str)
                    if parsed_time:
                        time_display = parsed_time.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        time_display = time_str
                else:
                    # time_str 不是有效的时间格式，可能是推文内容，使用默认值
                    time_display = "未知时间"
            else:
                time_display = "未知时间"
            
            # 在提取推文内容之前，只点击主推文正文里的"show more"以展开完整内容（不点引用推文内的，点了会进入该 post 页面）
            try:
                # 仅在主推文正文容器内查找：第一个 div[data-testid="tweetText"] 为主推文，引用推文内也有该节点，只在主推文内点
                main_text_container = None
                for sel in ['div[data-testid="tweetText"]', 'span[data-testid="tweetText"]']:
                    try:
                        elems = tweet_element.find_elements(By.CSS_SELECTOR, sel)
                        for el in elems:
                            # 排除在引用推文卡片内的：若其某祖先为带 card 的引用区域或为嵌套的 tweet 容器则跳过
                            try:
                                in_quote = driver.execute_script("""
                                    var el = arguments[0];
                                    var root = arguments[1];
                                    while (el && el !== root) {
                                        var tid = el.getAttribute && el.getAttribute('data-testid');
                                        if (tid && tid.indexOf('card') >= 0) return true;
                                        if (tid === 'tweet' && el !== root) return true;
                                        if (el.tagName === 'A' && el.href && el.href.indexOf('/status/') >= 0) return true;
                                        el = el.parentElement;
                                    }
                                    return false;
                                """, el, tweet_element)
                                if not in_quote:
                                    main_text_container = el
                                    break
                            except Exception:
                                main_text_container = el
                                break
                        if main_text_container is not None:
                            break
                    except Exception:
                        continue
                if main_text_container is None:
                    main_text_container = tweet_element

                show_more_selectors = [
                    ("xpath", ".//span[contains(text(), 'Show more')]"),
                    ("xpath", ".//span[contains(text(), 'show more')]"),
                    ("xpath", ".//span[contains(text(), '显示更多')]"),
                    ("xpath", ".//span[contains(text(), '展开')]"),
                    ("css", 'span[aria-label*="Show more"]'),
                    ("css", 'span[aria-label*="show more"]'),
                    ("css", 'span[aria-label*="显示更多"]'),
                    ("css", 'span[aria-label*="展开"]'),
                    ("css", 'button[aria-label*="Show more"]'),
                    ("css", 'button[aria-label*="show more"]'),
                    ("css", 'a[aria-label*="Show more"]'),
                    ("css", 'a[aria-label*="show more"]'),
                    ("css", 'span[data-testid*="show"]'),
                    ("css", 'button[data-testid*="show"]'),
                ]

                show_more_clicked = False
                for selector_type, selector in show_more_selectors:
                    try:
                        if selector_type == "xpath":
                            show_more_elements = main_text_container.find_elements(By.XPATH, selector)
                        else:
                            show_more_elements = main_text_container.find_elements(By.CSS_SELECTOR, selector)

                        for show_more_elem in show_more_elements:
                            try:
                                if not show_more_elem.is_displayed():
                                    continue
                                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", show_more_elem)
                                time.sleep(0.3)
                                try:
                                    show_more_elem.click()
                                    time.sleep(0.5)
                                    show_more_clicked = True
                                    break
                                except Exception:
                                    try:
                                        driver.execute_script("arguments[0].click();", show_more_elem)
                                        time.sleep(0.5)
                                        show_more_clicked = True
                                        break
                                    except Exception:
                                        continue
                            except Exception:
                                continue

                        if show_more_clicked:
                            break
                    except Exception:
                        continue

                if show_more_clicked:
                    time.sleep(0.5)
            except Exception:
                pass
            
            # 提取推文内容
            content = ""
            content_selectors = [
                'div[data-testid="tweetText"]',
                'div[lang]',
                'span[data-testid="tweetText"]',
            ]
            
            for selector in content_selectors:
                try:
                    content_elem = tweet_element.find_element(By.CSS_SELECTOR, selector)
                    content = content_elem.text.strip()
                    if content and len(content) > 10:  # 确保内容不为空
                        break
                except:
                    continue
            
            # 如果还是没找到内容，尝试获取所有文本
            if not content:
                try:
                    content = tweet_element.text.strip()
                    # 移除账号名和时间部分
                    if username in content:
                        content = content.replace(username, "", 1).strip()
                    if time_str in content:
                        content = content.replace(time_str, "", 1).strip()
                except:
                    pass
            
            # 生成推文ID（用于文件名，统一生成一次）
            tweet_id_base = time_display.replace(' ', '_').replace(':', '-') if time_display != "未知时间" else datetime.now().strftime("%Y%m%d_%H%M%S")
            content_hash_short = hashlib.md5(content.encode()).hexdigest()[:6]
            tweet_id = f"{tweet_id_base}_{content_hash_short}"
            tweet_id = re.sub(r'[<>:"/\\|?*]', '_', tweet_id)
            
            # 检查推文内容是否重复（使用完整hash）
            content_hash_full = hashlib.md5(content.encode('utf-8')).hexdigest()
            is_duplicate_tweet = content_hash_full in seen_contents
            
            # 提取图片和视频（如果推文重复，跳过图片处理）
            image_files = []
            image_urls = []
            
            # 如果推文内容重复，仍然提取图片URL（但不下载），用于在去重时关联已下载的图片
            if is_duplicate_tweet:
                # 推文重复，但仍然提取图片URL，用于在去重时检查是否有已下载的图片需要关联
                # 提取图片URL（但不下载）
                image_selectors = [
                    'img[data-testid="tweetPhoto"]',
                    'img[alt*="Image"]',
                    'div[data-testid="tweetPhoto"] img',
                    'article img[src*="pbs.twimg.com"]',
                    'article img[src*="pbs.twimg.com/media"]',
                ]
                
                for selector in image_selectors:
                    try:
                        img_elements = tweet_element.find_elements(By.CSS_SELECTOR, selector)
                        for img_elem in img_elements:
                            try:
                                img_url = img_elem.get_attribute('src') or img_elem.get_attribute('data-src')
                                if img_url and 'pbs.twimg.com' in img_url and '/media/' in img_url and '/ext_tw_video_thumb' not in img_url:
                                    # 规范化URL并检查是否已下载
                                    normalized_url = normalize_image_url(img_url)
                                    if downloaded_urls and normalized_url in downloaded_urls:
                                        existing_file = downloaded_urls[normalized_url]
                                        if os.path.exists(existing_file):
                                            # 已下载过，添加到image_files中
                                            image_files.append(existing_file)
                            except:
                                continue
                        if image_files:
                            break
                    except:
                        continue
                
                # 对于重复推文，不下载新图片，只关联已下载的图片
                pass
            else:
            
                # X/Twitter的图片选择器
                image_selectors = [
                    'img[data-testid="tweetPhoto"]',
                    'img[alt*="Image"]',
                    'div[data-testid="tweetPhoto"] img',
                    'article img[src*="pbs.twimg.com"]',
                    'article img[src*="pbs.twimg.com/media"]',
                ]
                
                for selector in image_selectors:
                    try:
                        img_elements = tweet_element.find_elements(By.CSS_SELECTOR, selector)
                        for img_elem in img_elements:
                            try:
                                # 优先获取src属性
                                img_url = img_elem.get_attribute('src')
                                if not img_url:
                                    # 尝试获取data-src属性（懒加载）
                                    img_url = img_elem.get_attribute('data-src')
                                
                                if img_url and img_url not in image_urls:
                                    # 过滤掉头像、图标等非推文图片
                                    # 推文图片通常包含 "pbs.twimg.com/media" 或 "pbs.twimg.com/ext_tw_video_thumb"
                                    # 注意：视频缩略图也会被这里捕获，但我们会单独处理视频
                                    if 'pbs.twimg.com' in img_url and '/media/' in img_url:
                                        # 排除视频缩略图（视频会单独处理）
                                        if '/ext_tw_video_thumb' not in img_url:
                                            # 尝试获取原始尺寸图片（移除尺寸参数）
                                            # X的图片URL格式通常是: https://pbs.twimg.com/media/xxx?format=jpg&name=small
                                            # 我们尝试获取大尺寸或原始尺寸
                                            if 'name=' in img_url:
                                                # 替换为large或orig尺寸
                                                img_url = re.sub(r'name=[^&]+', 'name=large', img_url)
                                            elif 'format=' in img_url and 'name=' not in img_url:
                                                img_url = img_url + '&name=large'
                                            
                                            # 提前检查是否已下载过，避免无效处理
                                            normalized_url = normalize_image_url(img_url)
                                            if downloaded_urls and normalized_url in downloaded_urls:
                                                existing_file = downloaded_urls[normalized_url]
                                                if os.path.exists(existing_file):
                                                    # 已下载过，跳过此图片
                                                    continue
                                            
                                            image_urls.append(img_url)
                            except:
                                continue
                        if image_urls:
                            break
                    except:
                        continue
                
                # 检测视频元素（优先使用预览图，如果没有则截图）
                video_selectors = [
                    'video[data-testid="videoComponent"]',
                    'div[data-testid="videoComponent"]',
                    'video',
                    'div[aria-label*="Video"]',
                ]
                
                video_elements = []
                for selector in video_selectors:
                    try:
                        elements = tweet_element.find_elements(By.CSS_SELECTOR, selector)
                        if elements:
                            video_elements = elements
                            break
                    except:
                        continue
                
                # 处理视频：优先使用预览图URL，如果没有则截图
                if video_elements:
                    # 先尝试获取视频预览图URL
                    video_thumb_url = None
                    try:
                        # 查找视频缩略图
                        thumb_selectors = [
                            'img[src*="ext_tw_video_thumb"]',
                            'img[src*="video_thumb"]',
                            'div[data-testid="videoComponent"] img',
                        ]
                        for thumb_selector in thumb_selectors:
                            try:
                                thumb_imgs = tweet_element.find_elements(By.CSS_SELECTOR, thumb_selector)
                                for thumb_img in thumb_imgs:
                                    thumb_url = thumb_img.get_attribute('src') or thumb_img.get_attribute('data-src')
                                    if thumb_url and 'pbs.twimg.com' in thumb_url:
                                        video_thumb_url = thumb_url
                                        # 尝试获取大尺寸
                                        if 'name=' in video_thumb_url:
                                            video_thumb_url = re.sub(r'name=[^&]+', 'name=large', video_thumb_url)
                                        elif 'format=' in video_thumb_url and 'name=' not in video_thumb_url:
                                            video_thumb_url = video_thumb_url + '&name=large'
                                        break
                                if video_thumb_url:
                                    break
                            except:
                                continue
                    except:
                        pass
                    
                    # 如果有预览图URL，先检查是否已下载过
                    if video_thumb_url:
                        # 提前检查是否已下载过，避免无效处理
                        normalized_video_url = normalize_image_url(video_thumb_url)
                        is_duplicate = False
                        if downloaded_urls and normalized_video_url in downloaded_urls:
                            existing_file = downloaded_urls[normalized_video_url]
                            if os.path.exists(existing_file):
                                # 已下载过，直接跳过整个视频处理（静默跳过，不打印）
                                video_file = None
                                is_duplicate = True
                            else:
                                # 记录存在但文件不存在，重新下载
                                video_file = download_image(video_thumb_url, tweet_id=f"{tweet_id}_video", downloaded_urls=downloaded_urls)
                        else:
                            # 未下载过，下载预览图
                            print(f"    发现视频，使用预览图...")
                            video_file = download_image(video_thumb_url, tweet_id=f"{tweet_id}_video", downloaded_urls=downloaded_urls)
                        
                        # 只有在不是重复的情况下才处理
                        if not is_duplicate and video_file:
                            image_files.append(video_file)
                            print(f"      ✓ 已保存视频预览图: {os.path.basename(video_file)}")
                        elif not is_duplicate and not video_file:
                            # 如果预览图下载失败，在滚动过程中静默跳过截图（避免干扰滚动）
                            # 优先保证滚动流畅，视频截图可以后续处理
                            pass
                    else:
                        # 没有预览图时，在滚动过程中跳过截图（避免干扰滚动）
                        # 只在视频元素在视口内时才尝试截图
                        try:
                            video_file = capture_video_screenshot(driver, video_elements[0], tweet_id=f"{tweet_id}_video", skip_scroll=True)
                            if video_file:
                                image_files.append(video_file)
                                print(f"      ✓ 已截图: {os.path.basename(video_file)}")
                        except:
                            # 静默跳过，不打印错误
                            pass
                
                # 下载图片（image_urls 中已经过滤掉已下载的URL）
                if image_urls:
                    print(f"    发现 {len(image_urls)} 张图片，开始下载...")
                    for idx, img_url in enumerate(image_urls):
                        # 为每张图片生成唯一ID
                        if len(image_urls) > 1:
                            current_tweet_id = f"{tweet_id}_{idx+1}"
                        else:
                            current_tweet_id = tweet_id
                        
                        # 下载图片（在收集阶段已经过滤掉已下载的，这里直接下载）
                        downloaded_file = download_image(img_url, tweet_id=current_tweet_id, downloaded_urls=downloaded_urls)
                        
                        if downloaded_file:
                            image_files.append(downloaded_file)
                            print(f"      ✓ 已下载: {os.path.basename(downloaded_file)}")
                        else:
                            print(f"      ✗ 下载失败: 图片 {idx+1}/{len(image_urls)}")
            
            # 将推文添加到列表（无论是否重复）
            if content:
                tweets.append((username, time_display, content, image_files))
        
        except Exception as e:
            continue
    
    return tweets


def extract_tweets(driver, max_tweets=100):
    """
    从Following页面提取推文内容（每次滚动后提取，累积去重）
    
    参数:
        driver: WebDriver实例
        max_tweets: 最大提取推文数量
    
    返回:
        list: 推文列表，每个元素为 (账号名, 时间, 内容, 图片文件列表)
    
    注意:
        推文去重基于完整内容的MD5 hash值，相同内容的推文只保存一次（即使发推时间不同）
    """
    all_tweets = []
    seen_contents = set()  # 用于去重，基于推文内容的完整hash值（相同内容只保存一次，即使时间不同）
    downloaded_urls = {}  # 用于图片URL去重，格式: {规范化URL: 文件路径}
    content_hash_to_tweet = {}  # 用于存储已保存推文的hash到推文元组的映射，用于关联重复推文的图片
    refreshed_once = False  # 避免重复刷新导致循环
    
    try:
        print("正在提取推文内容...")
        
        # 等待页面加载完成 - 确保推文元素已出现
        print("等待页面加载完成...")
        wait = WebDriverWait(driver, 15)
        try:
            # 等待至少一个推文元素出现
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-testid="tweet"], article[role="article"], div[data-testid="tweet"]')))
            print("✓ 推文元素已加载")
        except TimeoutException:
            print("⚠️ 等待推文元素超时，但继续尝试提取...")
        
        # 额外等待一段时间，确保动态内容加载完成，并模拟正常浏览
        time.sleep(5)  # 延长等待时间，模拟正常浏览
        
        # 再次确保页面在顶部（防止在等待期间页面状态发生变化）
        try:
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.5)
        except:
            pass
        
        # 先提取一次初始推文
        print("\n[初始提取]")
        initial_tweets = _extract_tweets_from_page(driver, downloaded_urls=downloaded_urls, seen_contents=seen_contents)
        initial_hashes = {hashlib.md5(t[2].encode('utf-8')).hexdigest() for t in initial_tweets}
        # #region agent log
        try:
            with open(os.devnull, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "C", "location": "get_x_following.py:1503", "message": "initial extraction", "data": {"timestamp": int(time.time() * 1000), "initial_tweets_count": len(initial_tweets), "initial_hashes_count": len(initial_hashes), "scroll_y": driver.execute_script("return window.pageYOffset;")}, "timestamp": int(time.time() * 1000)}) + '\n')
        except: pass
        # #endregion
        for tweet in initial_tweets:
            username, time_display, content, image_files = tweet
            # 使用完整内容的hash值作为唯一标识（确保相同内容只保存一次）
            content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
            if content_hash not in seen_contents:
                seen_contents.add(content_hash)
                all_tweets.append(tweet)
                content_hash_to_tweet[content_hash] = tweet  # 保存hash到推文的映射
        print(f"  提取到 {len(initial_tweets)} 条，新增 {len(all_tweets)} 条（去重后）")
        
        # 滚动并提取，直到达到目标数量或滚动次数上限
        max_scrolls = 40  # 最多滚动40次（提高获取数量）
        scroll_count = 0
        consecutive_no_new = 0  # 连续没有新增推文的次数
        max_consecutive_no_new = 5  # 连续5次没有新增才退出（提高容忍度）
        
        print(f"\n[开始滚动加载，目标: {max_tweets} 条]")
        
        # 在开始滚动前，唤醒屏幕并确保浏览器窗口可见（强制激活，用于定时执行）
        wake_up_screen()  # 先唤醒屏幕
        ensure_browser_window_visible(driver, force_activate=True)
        time.sleep(0.5)  # 等待窗口激活完成
        
        while len(all_tweets) < max_tweets and scroll_count < max_scrolls:
            scroll_count += 1
            print(f"\n[第 {scroll_count} 次滚动周期]")
            
            # 每次滚动周期开始时，唤醒屏幕并确保浏览器窗口可见（强制激活，防止被最小化）
            wake_up_screen()  # 先唤醒屏幕
            ensure_browser_window_visible(driver, force_activate=True)
            time.sleep(0.3)  # 等待窗口激活完成
            
            # 记录滚动前页面上的推文总数和内容hash集合（用于检测新内容）
            tweets_on_page_before_list = _extract_tweets_from_page(driver, downloaded_urls=downloaded_urls, seen_contents=seen_contents)
            tweets_on_page_before = len(tweets_on_page_before_list)
            # 记录滚动前页面上的推文内容hash集合（用于检测新内容）
            tweets_before_hashes = {hashlib.md5(t[2].encode('utf-8')).hexdigest() for t in tweets_on_page_before_list}
            
            # #region agent log
            try:
                before_hashes = [hashlib.md5(t[2].encode('utf-8')).hexdigest() for t in tweets_on_page_before_list]
                new_in_before = [h for h in before_hashes if h not in seen_contents]
                with open(os.devnull, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "E", "location": "get_x_following.py:1501", "message": "before scroll cycle", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "tweets_on_page_before": tweets_on_page_before, "new_hashes_in_before": len(new_in_before), "seen_contents_size": len(seen_contents)}, "timestamp": int(time.time() * 1000)}) + '\n')
            except: pass
            # #endregion
            
            # 模拟真人浏览：分步滚动，每次滚动一小段距离
            # 如果连续多次没有新内容，增加滚动力度（可能卡在视频上）
            # 关键改进：确保滚动距离足够大，能够触发X/Twitter的虚拟滚动加载新内容
            if consecutive_no_new > 0:
                # 增加滚动力度，尝试跳过视频区域
                steps_in_cycle = random.randint(6, 10)  # 增加滚动次数
                base_scroll_distance = random.randint(1200, 1800)  # 大幅度增加滚动距离，确保触发虚拟滚动
                print(f"  检测到可能卡住，将进行 {steps_in_cycle} 次大幅度滚动（跳过视频区域）...")
            else:
                steps_in_cycle = random.randint(4, 8)  # 增加滚动次数，确保能够触发虚拟滚动
                base_scroll_distance = random.randint(600, 1000)  # 增加滚动距离，确保能够触发虚拟滚动
                print(f"  将进行 {steps_in_cycle} 次滚动（确保触发虚拟滚动）...")
            
            scroll_y_current = driver.execute_script("return window.pageYOffset;")
            scroll_y_start = scroll_y_current
            
            for step in range(steps_in_cycle):
                # 在每次滚动前，唤醒屏幕并确保窗口激活（用于定时执行时确保滚动操作能够触发虚拟滚动）
                if step > 0 and step % 2 == 0:  # 每隔2次滚动检查一次窗口状态
                    wake_up_screen()  # 先唤醒屏幕
                    ensure_browser_window_visible(driver, force_activate=True)
                    time.sleep(0.2)
                
                # 根据是否卡住调整滚动距离
                if consecutive_no_new > 0:
                    scroll_distance = base_scroll_distance + random.randint(0, 500)  # 大幅度滚动
                else:
                    scroll_distance = base_scroll_distance + random.randint(0, 200)  # 增加随机性
                
                # 使用即时滚动，确保立即生效（平滑滚动可能导致scrollY不立即更新）
                # 但为了更接近真实用户行为，在滚动之间添加随机延迟
                driver.execute_script(f"window.scrollBy(0, {scroll_distance});")
                
                # 滚动后等待，确保平滑滚动完成（如果浏览器支持）和内容加载
                # 定时执行时增加等待时间，确保虚拟滚动有足够时间加载新内容
                if consecutive_no_new > 0:
                    read_time = random.uniform(1.0, 2.0)  # 增加等待时间，确保滚动完成
                else:
                    read_time = random.uniform(2.0, 3.5)  # 增加等待时间，确保虚拟滚动加载完成
                time.sleep(read_time)
                
                # 更新当前滚动位置（用于下次循环）
                scroll_y_current = driver.execute_script("return window.pageYOffset;")
                
                # 如果卡住，不进行回滚操作
                if consecutive_no_new == 0 and random.random() < 0.2:  # 20%的概率
                    back_scroll = random.randint(50, 150)
                    driver.execute_script(f"window.scrollBy(0, -{back_scroll});")
                    time.sleep(random.uniform(0.5, 1.0))  # 增加等待时间
                    scroll_y_current = driver.execute_script("return window.pageYOffset;")
            
            # 滚动周期结束后，等待一段时间确保所有滚动操作完成，再检查总滚动距离
            time.sleep(0.5)  # 额外等待，确保所有滚动操作完成
            scroll_y_end = driver.execute_script("return window.pageYOffset;")
            total_scroll_distance = scroll_y_end - scroll_y_start
            print(f"  本次滚动总距离: {total_scroll_distance:.0f}px")
            
            # #region agent log
            try:
                with open(os.devnull, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H", "location": "get_x_following.py:1595", "message": "scroll distance check", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "scroll_y_start": scroll_y_start, "scroll_y_end": scroll_y_end, "total_scroll_distance": total_scroll_distance, "steps_in_cycle": steps_in_cycle}, "timestamp": int(time.time() * 1000)}) + '\n')
            except: pass
            # #endregion
            
            # 如果滚动距离太小（可能没有真正滚动），尝试强制滚动到底部再回到当前位置
            if total_scroll_distance < 500:
                print(f"  警告：滚动距离过小，尝试强制滚动触发虚拟滚动...")
                # #region agent log
                try:
                    with open(os.devnull, 'a', encoding='utf-8') as f:
                        f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H", "location": "get_x_following.py:1602", "message": "forcing scroll to bottom", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "total_scroll_distance": total_scroll_distance}, "timestamp": int(time.time() * 1000)}) + '\n')
                except: pass
                # #endregion
                # 先滚动到底部，触发虚拟滚动加载所有内容
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                # 再滚动回当前位置附近
                target_scroll = scroll_y_end + random.randint(500, 1000)
                driver.execute_script(f"window.scrollTo(0, {target_scroll});")
                time.sleep(1)
            
            # 滚动周期结束后，等待新内容加载
            print("  等待新内容加载...")
            # 在等待前，唤醒屏幕并再次确保窗口激活（用于定时执行时确保页面处于活动状态）
            wake_up_screen()  # 先唤醒屏幕
            ensure_browser_window_visible(driver, force_activate=True)
            time.sleep(0.2)
            
            # #region agent log
            try:
                # 检查滚动后页面上有多少推文元素
                tweet_elements_after_scroll = len(driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"], article[role="article"], div[data-testid="tweet"]'))
                scroll_y_after = driver.execute_script("return window.pageYOffset;")
                body_height = driver.execute_script("return document.body.scrollHeight;")
                with open(os.devnull, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "J", "location": "get_x_following.py:1615", "message": "after scroll, before wait", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "tweet_elements_count": tweet_elements_after_scroll, "scroll_y": scroll_y_after, "body_height": body_height, "total_scroll_distance": total_scroll_distance}, "timestamp": int(time.time() * 1000)}) + '\n')
            except: pass
            # #endregion
            time.sleep(3.5)  # 增加基础等待时间，确保虚拟滚动有时间加载新内容（定时执行时需要更长时间）
            
            # 尝试检测是否有新内容加载（通过检查是否有新的推文内容hash，而不是仅检查数量）
            # 增加等待时间，确保虚拟滚动有足够时间加载新内容（定时执行时需要更长时间）
            max_wait_time = 30  # 增加到30秒，确保定时执行时有足够时间
            check_interval = 0.5
            waited_time = 0
            new_content_loaded = False
            final_tweets_count = tweets_on_page_before
            
            while waited_time < max_wait_time:
                # 每隔5秒唤醒屏幕并检查一次窗口状态（用于定时执行时确保窗口始终激活）
                if waited_time > 0 and int(waited_time) % 5 == 0:
                    wake_up_screen()  # 先唤醒屏幕
                    ensure_browser_window_visible(driver, force_activate=True)
                    time.sleep(0.2)
                
                try:
                    # 检查页面上是否有新的推文内容（通过比较内容hash）
                    current_tweets_on_page = _extract_tweets_from_page(driver, downloaded_urls=downloaded_urls, seen_contents=seen_contents)
                    final_tweets_count = len(current_tweets_on_page)
                    
                    # 计算当前页面上推文的内容hash集合
                    current_hashes = {hashlib.md5(t[2].encode('utf-8')).hexdigest() for t in current_tweets_on_page}
                    
                    # 检查是否有新的推文内容
                    # 新内容 = 当前页面上的hash - 已见过的hash - 滚动前就在页面上的hash（但只考虑当前仍在页面上的）
                    # 由于虚拟滚动，current_hashes可能不包含所有tweets_before_hashes中的hash
                    # 所以我们需要检查：current_hashes中是否有hash不在seen_contents中，且不在tweets_before_hashes中
                    # 但是，如果current_hashes中的hash都在tweets_before_hashes中，即使它们不在seen_contents中，也不应该被认为是新内容
                    # 因为它们在滚动前就在页面上了，只是还没有被提取
                    # 所以，正确的逻辑是：检查是否有hash不在seen_contents中，且不在tweets_before_hashes中
                    unseen_hashes = current_hashes - seen_contents
                    before_hashes_in_current = current_hashes & tweets_before_hashes
                    new_content_hashes = unseen_hashes - before_hashes_in_current
                    
                    # #region agent log
                    try:
                        with open(os.devnull, 'a', encoding='utf-8') as f:
                            f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "B", "location": "get_x_following.py:1573", "message": "during content detection", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "waited_time": waited_time, "final_tweets_count": final_tweets_count, "tweets_on_page_before": tweets_on_page_before, "new_hashes_count": len(new_content_hashes), "new_hashes": list(new_content_hashes)[:5], "tweets_before_hashes_size": len(tweets_before_hashes), "seen_contents_size": len(seen_contents), "current_hashes_size": len(current_hashes)}, "timestamp": int(time.time() * 1000)}) + '\n')
                    except: pass
                    # #endregion
                    
                    if len(new_content_hashes) > 0:
                        new_content_loaded = True
                        break
                except Exception as e:
                    # #region agent log
                    try:
                        with open(os.devnull, 'a', encoding='utf-8') as f:
                            f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "B", "location": "get_x_following.py:1578", "message": "content detection exception", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "waited_time": waited_time, "error": str(e)}, "timestamp": int(time.time() * 1000)}) + '\n')
                    except: pass
                    # #endregion
                    pass
                
                time.sleep(check_interval)
                waited_time += check_interval
            
            if new_content_loaded:
                # 计算实际新增的推文数量（用于提示）
                try:
                    current_tweets_for_count = _extract_tweets_from_page(driver, downloaded_urls=downloaded_urls, seen_contents=seen_contents)
                    current_hashes_for_count = {hashlib.md5(t[2].encode('utf-8')).hexdigest() for t in current_tweets_for_count}
                    new_count = len(current_hashes_for_count - tweets_before_hashes - seen_contents)
                    print(f"  ✓ 检测到新内容已加载（页面推文数: {tweets_on_page_before} -> {final_tweets_count}，预计新增: {new_count} 条）")
                except:
                    print(f"  ✓ 检测到新内容已加载（页面推文数: {tweets_on_page_before} -> {final_tweets_count}）")
                consecutive_no_new = 0  # 重置计数器
                # 检测到新内容后，再等待一段时间，模拟阅读新内容
                time.sleep(2)  # 模拟阅读新加载的内容（优化后更快）
            else:
                print(f"  ⚠️ 未检测到新内容（页面推文数: {tweets_on_page_before} -> {final_tweets_count}），但继续提取...")
                # 如果推文数量没有增加，可能是虚拟滚动没有触发，尝试强制触发
                if final_tweets_count == tweets_on_page_before:
                    print(f"  推文数量未增加，尝试强制触发虚拟滚动...")
                    # #region agent log
                    try:
                        with open(os.devnull, 'a', encoding='utf-8') as f:
                            f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "I", "location": "get_x_following.py:1670", "message": "forcing virtual scroll trigger", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "final_tweets_count": final_tweets_count, "tweets_on_page_before": tweets_on_page_before, "scroll_y": driver.execute_script("return window.pageYOffset;")}, "timestamp": int(time.time() * 1000)}) + '\n')
                    except: pass
                    # #endregion
                    # 滚动到底部，强制触发虚拟滚动
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(3)  # 等待虚拟滚动加载
                    # 滚动回中间位置
                    current_scroll = driver.execute_script("return window.pageYOffset;")
                    mid_scroll = current_scroll // 2
                    driver.execute_script(f"window.scrollTo(0, {mid_scroll});")
                    time.sleep(2)
                    
                    # 再次检查推文数量
                    try:
                        after_force_tweets = _extract_tweets_from_page(driver, downloaded_urls=downloaded_urls, seen_contents=seen_contents)
                        after_force_count = len(after_force_tweets)
                        # #region agent log
                        try:
                            with open(os.devnull, 'a', encoding='utf-8') as f:
                                f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "I", "location": "get_x_following.py:1683", "message": "after forcing scroll", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "after_force_count": after_force_count, "tweets_on_page_before": tweets_on_page_before}, "timestamp": int(time.time() * 1000)}) + '\n')
                        except: pass
                        # #endregion
                        if after_force_count > tweets_on_page_before:
                            print(f"  ✓ 强制滚动后推文数量增加: {tweets_on_page_before} -> {after_force_count}")
                    except: pass
                # 即使没有检测到新内容，也等待一段时间，模拟正常浏览
                time.sleep(2.0)  # 增加等待时间
            
            # 提取当前页面的推文
            # 注意：这里传入seen_contents是为了跳过重复推文的图片下载，但推文本身仍会被提取
            # 在去重时，我们会检查是否有已下载的图片需要关联到已保存的推文
            new_tweets = _extract_tweets_from_page(driver, downloaded_urls=downloaded_urls, seen_contents=seen_contents)
            
            # #region agent log
            try:
                extraction_hashes = [hashlib.md5(t[2].encode('utf-8')).hexdigest() for t in new_tweets]
                new_in_extraction = [h for h in extraction_hashes if h not in seen_contents]
                # 统计提取到的推文中有图片的数量
                tweets_with_images = sum(1 for t in new_tweets if len(t) == 4 and t[3])
                total_images_in_extraction = sum(len(t[3]) if len(t) == 4 and t[3] else 0 for t in new_tweets)
                with open(os.devnull, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "C", "location": "get_x_following.py:1576", "message": "after extraction, before deduplication", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "new_tweets_count": len(new_tweets), "final_tweets_count": final_tweets_count, "tweets_on_page_before": tweets_on_page_before, "new_hashes_count": len(new_in_extraction), "new_hashes": new_in_extraction[:5], "seen_contents_size_before": len(seen_contents), "tweets_with_images": tweets_with_images, "total_images_in_extraction": total_images_in_extraction}, "timestamp": int(time.time() * 1000)}) + '\n')
            except: pass
            # #endregion
            
            # 如果在提取阶段检测到了新内容，但检测阶段没有检测到，更新new_content_loaded标志
            # 这样可以确保提示信息准确（虽然提示信息已经在检测阶段打印了，但至少标志是正确的）
            if not new_content_loaded:
                # 检查是否有新内容不在滚动前的hash集合中，且不在已见过的hash集合中
                extraction_hashes = {hashlib.md5(t[2].encode('utf-8')).hexdigest() for t in new_tweets}
                new_extraction_hashes = extraction_hashes - tweets_before_hashes - seen_contents
                if len(new_extraction_hashes) > 0:
                    new_content_loaded = True
            
            # 去重并添加到总列表
            added_count = 0
            added_hashes = []
            skipped_with_images = 0  # 统计跳过的推文中有图片的数量
            for tweet in new_tweets:
                username, time_display, content, image_files = tweet
                # 使用完整内容的hash值作为唯一标识（确保相同内容只保存一次）
                content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
                if content_hash not in seen_contents:
                    seen_contents.add(content_hash)
                    all_tweets.append(tweet)
                    content_hash_to_tweet[content_hash] = tweet  # 保存hash到推文的映射
                    added_count += 1
                    added_hashes.append(content_hash)
                else:
                    # 推文重复，但检查是否有新图片需要关联到已保存的推文
                    if image_files:
                        skipped_with_images += len(image_files)
                        # 尝试将图片关联到已保存的推文
                        if content_hash in content_hash_to_tweet:
                            existing_tweet = content_hash_to_tweet[content_hash]
                            existing_username, existing_time, existing_content, existing_image_files = existing_tweet
                            # 合并图片列表（去重）
                            merged_images = list(existing_image_files) if existing_image_files else []
                            for img_file in image_files:
                                if img_file not in merged_images:
                                    merged_images.append(img_file)
                            # 更新已保存的推文
                            updated_tweet = (existing_username, existing_time, existing_content, merged_images)
                            # 找到并更新all_tweets中的推文
                            for idx, t in enumerate(all_tweets):
                                t_hash = hashlib.md5(t[2].encode('utf-8')).hexdigest()
                                if t_hash == content_hash:
                                    all_tweets[idx] = updated_tweet
                                    content_hash_to_tweet[content_hash] = updated_tweet
                                    # #region agent log
                                    try:
                                        with open(os.devnull, 'a', encoding='utf-8') as f:
                                            f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "G", "location": "get_x_following.py:1715", "message": "merged images to existing tweet", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "content_hash": content_hash, "existing_images_count": len(existing_image_files) if existing_image_files else 0, "new_images_count": len(image_files), "merged_images_count": len(merged_images)}, "timestamp": int(time.time() * 1000)}) + '\n')
                                    except: pass
                                    # #endregion
                                    break
                        else:
                            # #region agent log
                            try:
                                with open(os.devnull, 'a', encoding='utf-8') as f:
                                    f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "G", "location": "get_x_following.py:1720", "message": "duplicate tweet with images but not in content_hash_to_tweet", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "content_hash": content_hash, "image_count": len(image_files)}, "timestamp": int(time.time() * 1000)}) + '\n')
                            except: pass
                            # #endregion
                        # #region agent log
                        try:
                            with open(os.devnull, 'a', encoding='utf-8') as f:
                                f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "F", "location": "get_x_following.py:1655", "message": "skipped duplicate tweet with images", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "content_hash": content_hash, "image_count": len(image_files), "image_files": image_files[:3]}, "timestamp": int(time.time() * 1000)}) + '\n')
                        except: pass
                        # #endregion
            
            # #region agent log
            try:
                with open(os.devnull, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "D", "location": "get_x_following.py:1590", "message": "after deduplication", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "added_count": added_count, "added_hashes": added_hashes[:5], "seen_contents_size_after": len(seen_contents), "new_content_loaded": new_content_loaded}, "timestamp": int(time.time() * 1000)}) + '\n')
            except: pass
            # #endregion
            
            if skipped_with_images > 0:
                print(f"  本次提取到 {len(new_tweets)} 条，新增 {added_count} 条（去重后），累计 {len(all_tweets)} 条")
                print(f"  ⚠️ 跳过了 {skipped_with_images} 张已下载但未关联到CSV的图片（推文重复）")
            else:
                print(f"  本次提取到 {len(new_tweets)} 条，新增 {added_count} 条（去重后），累计 {len(all_tweets)} 条")
            
            # 如果本次没有新增推文，增加计数器
            if added_count == 0:
                consecutive_no_new += 1
                # 如果还没达到目标数量，允许更多次无新增
                allowed_no_new = max_consecutive_no_new if len(all_tweets) >= max_tweets else (max_consecutive_no_new + 3)
                print(f"  连续 {consecutive_no_new} 次未发现新推文（最多允许 {allowed_no_new} 次）")
                # #region agent log
                try:
                    # 检查页面上有多少推文，以及有多少是新的
                    current_tweets_on_page = _extract_tweets_from_page(driver, downloaded_urls=downloaded_urls, seen_contents=seen_contents)
                    current_hashes = {hashlib.md5(t[2].encode('utf-8')).hexdigest() for t in current_tweets_on_page}
                    new_hashes_on_page = current_hashes - seen_contents
                    with open(os.devnull, 'a', encoding='utf-8') as f:
                        f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "B", "location": "get_x_following.py:1751", "message": "consecutive_no_new incremented", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "consecutive_no_new": consecutive_no_new, "tweets_on_page": len(current_tweets_on_page), "new_hashes_on_page": len(new_hashes_on_page), "seen_contents_size": len(seen_contents), "scroll_y": driver.execute_script("return window.pageYOffset;")}, "timestamp": int(time.time() * 1000)}) + '\n')
                except: pass
                # #endregion
                # 连续多次没有新增推文，可能已经到底了
                if consecutive_no_new >= allowed_no_new:
                    # 如果还没达到目标数量，尝试刷新一次再继续
                    if len(all_tweets) < max_tweets and not refreshed_once:
                        print("  连续多次未发现新推文，尝试刷新Following页面后继续...")
                        refreshed_once = True
                        try:
                            refresh_page(driver)
                        except Exception as refresh_error:
                            print(f"  刷新页面失败: {refresh_error}")
                        consecutive_no_new = 0
                        continue
                    print("  连续多次未发现新推文，可能已加载完所有内容")
                    # #region agent log
                    try:
                        with open(os.devnull, 'a', encoding='utf-8') as f:
                            f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "B", "location": "get_x_following.py:1755", "message": "stopping due to consecutive_no_new", "data": {"timestamp": int(time.time() * 1000), "scroll_count": scroll_count, "consecutive_no_new": consecutive_no_new, "total_tweets": len(all_tweets)}, "timestamp": int(time.time() * 1000)}) + '\n')
                    except: pass
                    # #endregion
                    break
            else:
                consecutive_no_new = 0  # 重置计数器
        
        # 限制最终数量
        all_tweets = all_tweets[:max_tweets]
        
        print(f"\n✓ 成功提取 {len(all_tweets)} 条推文（共滚动 {scroll_count} 次）")
        
        return all_tweets
        
    except Exception as e:
        print(f"✗ 提取推文时出错: {e}")
        return all_tweets


def save_to_file(tweets, filename=None):
    """
    保存推文到文件
    
    参数:
        tweets: 推文列表，每个元素为 (账号名, 时间, 内容, 图片文件列表)
        filename: 输出文件名，如果为None则自动生成
    """
    if not tweets:
        print("✗ 没有推文可保存")
        return
    
    # 确保 history_data 目录存在
    output_dir = "history_data"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"✓ 已创建目录: {output_dir}")
    
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"x_following_{timestamp}.csv"
    
    # 构建完整文件路径
    csv_path = os.path.join(output_dir, filename)
    
    try:
        # 保存为CSV文件
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            # 写入表头
            writer.writerow(['账号名称', '发推时间', '推文内容', '图片文件'])
            # 写入数据
            for tweet in tweets:
                if len(tweet) == 4:
                    username, time_str, content, image_files = tweet
                else:
                    # 兼容旧格式（没有图片）
                    username, time_str, content = tweet[:3]
                    image_files = []
                
                # 将图片文件列表转换为字符串（用分号分隔）
                image_files_str = '; '.join(image_files) if image_files else ''
                writer.writerow([username, time_str, content, image_files_str])
        
        print(f"✓ 推文已保存到: {csv_path}")
        
        # 统计图片数量
        total_images = sum(len(tweet[3]) if len(tweet) == 4 else 0 for tweet in tweets)
        if total_images > 0:
            print(f"✓ 共保存 {total_images} 张图片到 x_images/ 目录")
        
    except Exception as e:
        print(f"✗ 保存文件时出错: {e}")


def main_following_legacy():
    """单次运行：X Following 时间线提取并保存 CSV（原 get_x_following 逻辑）。"""
    print("=" * 80)
    print("X Following 推文提取工具")
    print("=" * 80)
    print()
    
    # #region agent log
    try:
        with open(os.devnull, 'a', encoding='utf-8') as f:
            f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "A", "location": "get_x_following.py:1828", "message": "main function started", "data": {"timestamp": int(time.time() * 1000)}, "timestamp": int(time.time() * 1000)}) + '\n')
    except: pass
    # #endregion
    
    # 连接到已打开的浏览器
    driver = connect_to_existing_browser(debug_port=9222)
    if not driver:
        return
    
    # 唤醒屏幕并确保浏览器窗口可见（强制激活，用于定时执行时确保窗口在前台）
    wake_up_screen()  # 先唤醒屏幕
    ensure_browser_window_visible(driver, force_activate=True)
    time.sleep(0.5)  # 等待窗口激活完成
    
    # 在开始处理前，先重置页面状态（防止上次执行后页面停留在底部）
    # 关键：每次执行前都必须确保页面在Following页面且滚动位置在顶部
    # 对于定时任务，每次执行都强制重置，确保页面状态完全刷新
    try:
        current_url = driver.current_url
        scroll_y = driver.execute_script("return window.pageYOffset;")
        
        # #region agent log
        try:
            # 检查页面上有多少推文元素
            tweet_elements_count = len(driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"], article[role="article"], div[data-testid="tweet"]'))
            with open(os.devnull, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "A", "location": "get_x_following.py:1845", "message": "initial page state", "data": {"timestamp": int(time.time() * 1000), "current_url": current_url, "scroll_y": scroll_y, "tweet_elements_count": tweet_elements_count}, "timestamp": int(time.time() * 1000)}) + '\n')
        except: pass
        # #endregion
        
        # 对于定时任务，每次执行都强制重置页面状态，确保获取最新内容
        # 这样可以避免页面保留旧推文DOM元素导致去重逻辑误判
        needs_reset = True  # 改为总是重置，确保页面状态完全刷新
        if scroll_y > 500:
            print(f"检测到页面滚动位置异常（{scroll_y:.0f}），需要重置")
        else:
            print(f"执行页面状态重置（当前滚动位置: {scroll_y:.0f}），确保获取最新内容")
        
        if needs_reset:
            print(f"正在重置页面状态（当前URL: {current_url}，滚动位置: {scroll_y:.0f}）...")
            # 导航到X首页（home页面），然后点击Following标签
            # 提取基础URL（协议+域名）
            url_parts = current_url.split('/')
            base_url = url_parts[0] + '//' + url_parts[2]
            home_url = base_url + '/home'
            
            print(f"导航到X首页: {home_url}")
            # 使用强制刷新，绕过缓存，确保获取最新内容
            driver.get(home_url + '?_t=' + str(int(time.time() * 1000)))
            time.sleep(4)  # 增加等待时间，确保页面加载完成
            
            # 点击Following标签切换到Following页面
            print("点击Following标签...")
            if click_following_tab(driver):
                print("✓ 已切换到Following页面")
                # 验证Following标签是否已激活
                if is_following_tab_active(driver):
                    print("✓ Following标签已激活，可以开始提取")
                else:
                    print("⚠️ 点击后Following标签未激活，可能需要等待...")
                    time.sleep(2)  # 额外等待
            else:
                print("⚠️ 点击Following标签失败，但继续执行...")
            
            # 确保滚动到顶部（多次尝试，确保成功）
            for attempt in range(3):
                driver.execute_script("window.scrollTo({behavior: 'instant', top: 0});")
                time.sleep(0.5)
                scroll_y_after = driver.execute_script("return window.pageYOffset;")
                if scroll_y_after < 10:
                    break
                print(f"  尝试 {attempt + 1}/3: 滚动位置 {scroll_y_after:.0f}，继续重置...")
            
            # 等待页面完全加载，确保新推文已加载
            print("等待页面完全加载...")
            time.sleep(5)  # 增加等待时间，确保虚拟滚动初始化完成
            
            # 触发一次小滚动，确保虚拟滚动系统激活
            driver.execute_script("window.scrollBy(0, 100);")
            time.sleep(1)
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(2)
            
            # 检查页面上的推文元素数量，确认虚拟滚动是否正常工作
            try:
                tweet_elements_after_init = len(driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"], article[role="article"], div[data-testid="tweet"]'))
                body_height = driver.execute_script("return document.body.scrollHeight;")
                viewport_height = driver.execute_script("return window.innerHeight;")
                # #region agent log
                try:
                    with open(os.devnull, 'a', encoding='utf-8') as f:
                        f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "K", "location": "get_x_following.py:1900", "message": "after page reset and init scroll", "data": {"timestamp": int(time.time() * 1000), "tweet_elements_count": tweet_elements_after_init, "body_height": body_height, "viewport_height": viewport_height}, "timestamp": int(time.time() * 1000)}) + '\n')
                except: pass
                # #endregion
                print(f"  页面初始化后：推文元素 {tweet_elements_after_init} 个，页面高度 {body_height:.0f}px，视口高度 {viewport_height:.0f}px")
            except: pass
            
            # #region agent log
            try:
                # 检查重置后页面上有多少推文元素
                tweet_elements_after_reset = len(driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"], article[role="article"], div[data-testid="tweet"]'))
                final_url = driver.current_url
                final_scroll_y = driver.execute_script("return window.pageYOffset;")
                with open(os.devnull, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "A", "location": "get_x_following.py:1890", "message": "after page reset", "data": {"timestamp": int(time.time() * 1000), "final_url": final_url, "final_scroll_y": final_scroll_y, "tweet_elements_count": tweet_elements_after_reset}, "timestamp": int(time.time() * 1000)}) + '\n')
                print(f"页面状态已重置（URL: {final_url}，滚动位置: {final_scroll_y:.0f}，推文元素: {tweet_elements_after_reset}）")
            except:
                final_url = driver.current_url
                final_scroll_y = driver.execute_script("return window.pageYOffset;")
                print(f"页面状态已重置（URL: {final_url}，滚动位置: {final_scroll_y:.0f}）")
            # #endregion
    except Exception as e:
        print(f"重置页面状态时出错: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        # 刷新前先检查是否有Cloudflare错误
        if check_cloudflare_error(driver):
            # 如果当前页面已经是Cloudflare错误页面，完全跳过刷新，直接提取当前页面
            print("检测到Cloudflare错误页面，跳过刷新以避免触发保护机制")
        else:
            # 尝试刷新页面，如果失败（特别是Cloudflare错误），则自动跳过刷新
            # 刷新前先记录当前URL，确保刷新后仍在Following页面
            url_before_refresh = driver.current_url
            is_following_before = 'following' in url_before_refresh.lower()
            
            try:
                refresh_page(driver)
                
                # 刷新后检查URL是否改变
                url_after_refresh = driver.current_url
                is_following_after = 'following' in url_after_refresh.lower()
                
                # 如果刷新后URL变成了非Following页面，导航到首页然后点击Following标签
                if is_following_before and not is_following_after:
                    print(f"⚠️ 刷新后URL从Following页面变成了: {url_after_refresh}，导航到首页然后点击Following标签")
                    url_parts = url_after_refresh.split('/')
                    base_url = url_parts[0] + '//' + url_parts[2]
                    home_url = base_url + '/home'
                    driver.get(home_url)
                    time.sleep(3)
                    # 点击Following标签
                    if click_following_tab(driver):
                        print("✓ 已切换到Following页面")
                    # 确保滚动到顶部
                    driver.execute_script("window.scrollTo({behavior: 'instant', top: 0});")
                    time.sleep(1)
                else:
                    # 刷新后直接点击Following标签（即使已经在Following页面，点击一下也没关系）
                    print("\n刷新后点击Following标签...")
                    click_following_tab(driver)
                    # 等待页面加载完成
                    time.sleep(3)
                    
            except Exception as refresh_error:
                # 检查是否是Cloudflare错误
                if "Cloudflare错误" in str(refresh_error) or check_cloudflare_error(driver):
                    print("刷新时检测到Cloudflare错误，跳过刷新以避免触发保护机制")
                # 刷新失败时，导航到首页然后点击Following标签
                # 这样可以确保页面状态真正重置，避免因为页面停留在旧状态而导致无法获取新内容
                try:
                    current_url = driver.current_url
                    # 如果当前URL包含x.com或twitter.com，导航到首页然后点击Following标签
                    if 'x.com' in current_url.lower() or 'twitter.com' in current_url.lower():
                        # 提取基础URL（协议+域名）
                        url_parts = current_url.split('/')
                        base_url = url_parts[0] + '//' + url_parts[2]
                        home_url = base_url + '/home'
                        print(f"刷新失败，导航到首页: {home_url}")
                        driver.get(home_url)
                        time.sleep(3)
                        
                        # 确保滚动到顶部
                        driver.execute_script("window.scrollTo({behavior: 'instant', top: 0});")
                        time.sleep(1)
                        
                        # 点击Following标签切换到Following页面
                        print("尝试点击Following标签...")
                        if click_following_tab(driver):
                            print("✓ 已切换到Following页面")
                        time.sleep(3)
                    else:
                        # 如果不在X/Twitter页面，只滚动到顶部
                        driver.execute_script("window.scrollTo({behavior: 'instant', top: 0});")
                        time.sleep(1)
                except Exception as nav_error:
                    print(f"导航到首页并点击Following标签失败: {nav_error}")
                    try:
                        driver.execute_script("window.scrollTo({behavior: 'instant', top: 0});")
                        time.sleep(1)
                        click_following_tab(driver)
                        time.sleep(3)
                    except:
                        pass
        
        # 再次检查是否有Cloudflare错误，如果有则跳过提取
        if check_cloudflare_error(driver):
            print("当前页面是Cloudflare错误页面，无法提取推文，请稍后再试")
            return
        
        # 在提取推文之前，确保点击了Following标签（无论是否刷新，都确保在正确的页面）
        print("\n确保在Following页面...")
        # 先检查Following标签是否已激活
        if not is_following_tab_active(driver):
            print("Following标签未激活，点击Following标签...")
            if click_following_tab(driver):
                print("✓ Following标签已激活")
            else:
                print("⚠️ 点击Following标签失败，但继续执行...")
        else:
            print("✓ Following标签已激活")
        
        time.sleep(2)  # 等待页面加载
        
        # 确保页面滚动到顶部（重要：避免因为上次执行后页面停留在底部而导致无法获取新内容）
        try:
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)
            # 再次确认滚动位置
            scroll_y = driver.execute_script("return window.pageYOffset;")
            if scroll_y > 100:  # 如果还在底部，强制重置
                print(f"警告：页面滚动位置异常（{scroll_y}），强制重置到顶部")
                driver.execute_script("window.scrollTo({behavior: 'instant', top: 0});")
                time.sleep(1)
        except Exception as e:
            print(f"重置滚动位置时出错: {e}")
        
        # #region agent log
        try:
            # 提取前检查页面状态
            scroll_y_before_extract = driver.execute_script("return window.pageYOffset;")
            tweet_elements_before = len(driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"], article[role="article"], div[data-testid="tweet"]'))
            with open(os.devnull, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "A", "location": "get_x_following.py:2011", "message": "before extract_tweets", "data": {"timestamp": int(time.time() * 1000), "scroll_y": scroll_y_before_extract, "tweet_elements_count": tweet_elements_before}, "timestamp": int(time.time() * 1000)}) + '\n')
        except: pass
        # #endregion
        
        # 提取推文
        tweets = extract_tweets(driver, max_tweets=50)
        
        # #region agent log
        try:
            with open(os.devnull, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "A", "location": "get_x_following.py:2012", "message": "after extract_tweets", "data": {"timestamp": int(time.time() * 1000), "tweets_count": len(tweets)}, "timestamp": int(time.time() * 1000)}) + '\n')
        except: pass
        # #endregion
        
        if tweets:
            # 保存到文件
            save_to_file(tweets)
        
    except KeyboardInterrupt:
        print("\n\n用户中断操作")
    except Exception as e:
        print(f"\n✗ 程序执行出错: {e}")
    finally:
        # 注意：不要关闭浏览器，因为这是连接到已存在的浏览器
        print("\n程序执行完成（浏览器保持打开状态）")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="默认：Truth 转发监控（启动立即搜一次；宽松模式不限交易日/时段，每 15 分钟刷新）"
    )
    ap.add_argument(
        "--following",
        action="store_true",
        help="单次抓取 X「Following」页并写 CSV（旧版行为）",
    )
    ap.add_argument(
        "--profile-url",
        default=DEFAULT_TRUTH_PROFILE_URL,
        help="要监控的 X 用户主页 URL（也可用环境变量 TRUTH_X_PROFILE_URL）",
    )
    ap.add_argument("--debug-port", type=int, default=9222, help="Chrome 远程调试端口")
    ap.add_argument("--interval-minutes", type=int, default=15, help="监控时刷新间隔（分钟）")
    ap.add_argument(
        "--session-start",
        default="9:0",
        help="监控开始 北京时间 时:分，如 9:0",
    )
    ap.add_argument(
        "--session-end",
        default="15:0",
        help="监控结束 北京时间 时:分（含该时刻），如 15:0",
    )
    ap.add_argument(
        "--state-file",
        default="",
        help="已提示过的 status id 记录文件，默认脚本目录 truth_monitor_seen_ids.json",
    )
    ap.add_argument(
        "--strict-session",
        action="store_true",
        help="恢复严格模式：仅 A 股疑似交易日、且在 --session-start/--session-end 内才轮询，且只提示该时段内发出的帖",
    )
    ap.add_argument(
        "--no-translate",
        action="store_true",
        help="不调用在线翻译，只打印原文",
    )

    args = ap.parse_args()

    if args.following:
        main_following_legacy()
    else:

        def _parse_hm(s: str) -> Tuple[int, int]:
            parts = s.replace("：", ":").split(":")
            h = int(parts[0].strip())
            m = int(parts[1].strip()) if len(parts) > 1 else 0
            return h, m

        sh, sm = _parse_hm(args.session_start)
        eh, em = _parse_hm(args.session_end)
        st = Path(args.state_file) if args.state_file.strip() else None
        no_tr = args.no_translate or os.environ.get("TRUTH_MONITOR_NO_TRANSLATE", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        run_truth_monitor_loop(
            profile_url=args.profile_url.strip(),
            debug_port=args.debug_port,
            poll_interval_sec=max(60, args.interval_minutes * 60),
            session_start=(sh, sm),
            session_end=(eh, em),
            state_path=st,
            strict_session=bool(args.strict_session),
            enable_translate=not no_tr,
        )

