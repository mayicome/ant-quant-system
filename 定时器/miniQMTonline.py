# -*- coding: utf-8 -*-
"""
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

# 从文件读取账号密码credentials.txt存放账户和密码，还可以用加密方式存放（请自行使用加密算法）
def read_credentials(file_path="credentials.txt"):
    # 获取脚本所在目录的绝对路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 构建credentials.txt的完整路径
    full_path = os.path.join(script_dir, file_path)
    credentials = {}
    with open(full_path, 'r', encoding='utf-8') as file:
        for line in file:
            line = line.strip()
            if line and '=' in line:
                key, value = line.split('=', 1)
            credentials[key] = value
    return credentials

def find_input_fields(user_login_window):
    """
    动态查找登录窗口中的用户名和密码输入框
    返回: (username_field, password_field)
    """
    username_field = None
    password_field = None
    
    try:
        # 打印窗口结构用于调试
        print("正在分析登录窗口结构...")
        
        # 方法1: 查找所有Edit控件（输入框通常是Edit类）
        try:
            # 在win32后端，Edit控件的类名通常是"Edit"
            edit_controls = user_login_window.descendants(class_name="Edit")
            print(f"找到 {len(edit_controls)} 个Edit控件")
            
            if len(edit_controls) >= 2:
                # 通常第一个是用户名，第二个是密码
                # 但我们需要根据位置来判断（用户名在上，密码在下）
                controls_with_pos = []
                for edit in edit_controls:
                    try:
                        rect = edit.rectangle()
                        controls_with_pos.append((edit, rect.top))
                    except:
                        controls_with_pos.append((edit, 0))
                
                # 按Y坐标排序（从上到下）
                controls_with_pos.sort(key=lambda x: x[1])
                
                username_field = controls_with_pos[0][0]
                password_field = controls_with_pos[1][0]
                print(f"✅ 通过位置排序找到输入框: 用户名框Y={controls_with_pos[0][1]}, 密码框Y={controls_with_pos[1][1]}")
            elif len(edit_controls) == 1:
                # 只有一个输入框，可能是用户名，密码可能是Password类型的控件
                username_field = edit_controls[0]
                print("⚠️ 只找到一个Edit控件，尝试查找密码框...")
        except Exception as e:
            print(f"方法1失败: {e}")
        
        # 方法2: 如果方法1失败，尝试查找所有可编辑的控件
        if username_field is None or password_field is None:
            try:
                # 尝试查找所有可编辑的控件（包括Edit、RichEdit等）
                all_controls = user_login_window.descendants()
                # 查找所有可能包含文本输入的控件
                edit_controls = []
                for ctrl in all_controls:
                    try:
                        class_name = ctrl.class_name()
                        # Edit控件或RichEdit控件都可以输入文本
                        if class_name in ["Edit", "RichEdit", "RichEdit20W", "RichEdit50W"]:
                            edit_controls.append(ctrl)
                    except:
                        # 如果无法获取类名，检查是否有type_keys方法
                        if hasattr(ctrl, 'type_keys'):
                            edit_controls.append(ctrl)
                
                if len(edit_controls) >= 2:
                    # 按位置排序
                    controls_with_pos = []
                    for ctrl in edit_controls:
                        try:
                            rect = ctrl.rectangle()
                            controls_with_pos.append((ctrl, rect.top, rect.left))
                        except:
                            continue
                    
                    if len(controls_with_pos) >= 2:
                        # 按Y坐标排序，如果Y相同则按X坐标排序
                        controls_with_pos.sort(key=lambda x: (x[1], x[2]))
                        username_field = controls_with_pos[0][0]
                        password_field = controls_with_pos[1][0]
                        print(f"✅ 通过方法2找到输入框（找到{len(edit_controls)}个可编辑控件）")
            except Exception as e:
                print(f"方法2失败: {e}")
        
        # 方法3: 如果还是找不到，尝试通过窗口坐标和常见位置查找
        if username_field is None or password_field is None:
            try:
                # 获取窗口位置和大小
                window_rect = user_login_window.rectangle()
                window_width = window_rect.width()
                window_height = window_rect.height()
                
                # 常见的登录窗口布局：用户名在中间偏上，密码在用户名下方
                # 尝试在常见位置查找
                username_y = window_height * 0.35  # 大约35%的位置
                password_y = window_height * 0.50   # 大约50%的位置
                center_x = window_width * 0.5
                
                # 查找最接近这些位置的Edit控件
                all_edits = user_login_window.descendants(class_name="Edit")
                if len(all_edits) >= 2:
                    edits_with_distance = []
                    for edit in all_edits:
                        try:
                            rect = edit.rectangle()
                            edit_center_y = rect.top + rect.height() / 2
                            # 计算到预期位置的距离
                            dist_to_username = abs(edit_center_y - username_y)
                            dist_to_password = abs(edit_center_y - password_y)
                            edits_with_distance.append((edit, dist_to_username, dist_to_password))
                        except:
                            continue
                    
                    if edits_with_distance:
                        # 找到最接近用户名位置的
                        username_field = min(edits_with_distance, key=lambda x: x[1])[0]
                        # 找到最接近密码位置的（排除用户名框）
                        password_candidates = [e for e in edits_with_distance if e[0] != username_field]
                        if password_candidates:
                            password_field = min(password_candidates, key=lambda x: x[2])[0]
                        print(f"✅ 通过位置估算找到输入框")
            except Exception as e:
                print(f"方法3失败: {e}")
        
        # 如果还是找不到，打印窗口结构用于调试
        if username_field is None or password_field is None:
            print("\n⚠️ 无法自动定位输入框，打印窗口结构用于调试:")
            try:
                user_login_window.print_control_identifiers(depth=3)
            except:
                print("无法打印窗口结构")
        
    except Exception as e:
        print(f"查找输入框时出错: {e}")
        import traceback
        traceback.print_exc()
    
    return username_field, password_field

def input_credentials(user_login_window, username, pwd):
    """
    输入用户名和密码的辅助函数
    根据窗口实际位置动态定位输入框
    """
    # 确保登录窗口获得焦点
    try:
        user_login_window.set_focus()
        time.sleep(0.5)
        
        # 验证当前活动窗口是否是登录窗口
        active_window = win32gui.GetForegroundWindow()
        login_window_handle = user_login_window.handle if hasattr(user_login_window, 'handle') else None
        if login_window_handle and active_window != login_window_handle:
            print("⚠️ 警告: 活动窗口不是登录窗口，重新设置焦点...")
            user_login_window.set_focus()
            time.sleep(0.5)
    except Exception as e:
        print(f"设置焦点时出错: {e}")
    
    # 动态查找输入框
    username_field, password_field = find_input_fields(user_login_window)
    
    if username_field is None or password_field is None:
        print("❌ 无法找到输入框，尝试使用备用方法（直接输入）...")
        # 备用方法：直接在整个窗口上输入（可能不够准确）
        user_login_window.type_keys("{HOME}")  # 移动到开头
        time.sleep(0.1)
        user_login_window.type_keys("^a")  # 全选
        time.sleep(0.1)
        user_login_window.type_keys(username)
        time.sleep(0.2)
        user_login_window.type_keys('{TAB}')
        time.sleep(0.2)
        user_login_window.type_keys(pwd)
        time.sleep(0.2)
        user_login_window.type_keys('{ENTER}')
        return
    
    # 输入用户名
    try:
        print("正在输入用户名...")
        username_field.set_focus()
        time.sleep(0.3)
        
        # 清空现有内容
        username_field.type_keys("^a")  # Ctrl+A 全选
        time.sleep(0.1)
        username_field.type_keys(username)
        time.sleep(0.2)
        print(f"✅ 已输入用户名: {username}")
    except Exception as e:
        print(f"❌ 输入用户名失败: {e}")
        return
    
    # 输入密码
    try:
        print("正在输入密码...")
        password_field.set_focus()
        time.sleep(0.3)
        
        # 清空现有内容
        password_field.type_keys("^a")  # Ctrl+A 全选
        time.sleep(0.1)
        password_field.type_keys(pwd)
        time.sleep(0.2)
        print("✅ 已输入密码")
    except Exception as e:
        print(f"❌ 输入密码失败: {e}")
        return
    
    # 按回车键登录
    try:
        print("正在提交登录...")
        password_field.type_keys('{ENTER}')
        time.sleep(0.2)
        print("✅ 已提交登录")
    except Exception as e:
        print(f"❌ 提交登录失败: {e}")
        # 尝试在窗口上按回车
        try:
            user_login_window.type_keys('{ENTER}')
        except:
            pass

def list_all_windows():
    """
    列出所有可见窗口，用于调试
    """
    print("\n当前所有可见窗口列表:")
    try:
        windows = pywinauto.findwindows.find_windows()
        for i, hwnd in enumerate(windows[:20], 1):  # 显示前20个
            try:
                title = win32gui.GetWindowText(hwnd)
                class_name = win32gui.GetClassName(hwnd)
                if title:
                    print(f"  {i}. 标题: '{title}' | 类名: '{class_name}'")
            except:
                pass
        print()
    except Exception as e:
        print(f"无法列出窗口: {e}\n")

def wait_for_window(title_re=None, class_name=None, timeout=30, interval=0.5):
    """
    等待窗口出现，带重试机制
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            if class_name:
                window = pywinauto.findwindows.find_window(title_re=title_re, class_name=class_name)
            else:
                window = pywinauto.findwindows.find_window(title_re=title_re)
            return window
        except (pywinauto.findwindows.ElementNotFoundError, pywinauto.findwindows.WindowNotFoundError):
            time.sleep(interval)
    raise TimeoutError(f"等待窗口超时: title_re={title_re}, class_name={class_name}")

def find_window_flexible(title_keywords=None, class_name=None, exclude_keywords=None, process_path=None):
    """
    灵活查找窗口，支持关键词匹配
    参数:
        title_keywords: 标题关键词列表
        class_name: 窗口类名（必须匹配）
        exclude_keywords: 排除的关键词列表（如果标题包含这些词则跳过）
        process_path: 进程路径（如果提供，只匹配该进程的窗口）
    """
    # 默认排除编辑器和其他无关窗口
    if exclude_keywords is None:
        exclude_keywords = [
            'Cursor', 'VS Code', 'Visual Studio', 'PyCharm', 'IntelliJ', 
            'trade_record', 'main_window', '编辑器', 'Editor', 
            '.py', '.js', '.html', '记事本', 'Notepad', 
            '蚂蚁量化', 'Chrome', 'Edge', 'Firefox', '浏览器',
            '微信', 'QQ', '钉钉', 'Telegram'
        ]
    
    try:
        windows = pywinauto.findwindows.find_windows()
        for hwnd in windows:
            try:
                title = win32gui.GetWindowText(hwnd)
                win_class = win32gui.GetClassName(hwnd)
                
                if not title:
                    continue
                
                # 排除编辑器和其他无关窗口
                if exclude_keywords and any(exclude in title for exclude in exclude_keywords):
                    continue
                
                # 如果提供了进程路径，检查窗口是否属于该进程
                if process_path:
                    try:
                        process_id = win32process.GetWindowThreadProcessId(hwnd)[1]
                        import psutil
                        try:
                            proc = psutil.Process(process_id)
                            proc_exe = proc.exe()
                            # 检查进程路径是否匹配
                            if process_path.lower() not in proc_exe.lower() and os.path.basename(process_path).lower() not in os.path.basename(proc_exe).lower():
                                continue
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            # 如果无法获取进程信息，跳过进程检查
                            pass
                    except:
                        pass
                
                # 如果提供了类名，必须匹配
                if class_name and win_class != class_name:
                    continue
                
                # 如果提供了关键词，检查标题是否包含关键词
                if title_keywords:
                    if any(keyword in title for keyword in title_keywords):
                        # 额外验证：确保标题主要包含QMT相关关键词，而不是偶然匹配
                        # 例如，"蚂蚁量化"不应该匹配到包含"QMT"的编辑器窗口
                        if 'QMT' in title_keywords or '国金' in title_keywords:
                            # 如果标题包含排除关键词，即使有QMT关键词也不匹配
                            if any(exclude in title for exclude in ['蚂蚁量化', 'Cursor', '编辑器', 'Editor']):
                                continue
                        return hwnd
                elif title:  # 如果没有关键词但有标题，返回第一个有标题的窗口
                    if not class_name or win_class == class_name:
                        return hwnd
            except:
                continue
    except:
        pass
    return None

def login_qmt(username, pwd, path, softname):
    """
    登录QMT量化交易平台客户端的主函数
    """
    app = None
    login_window = None
    
    # 首先列出所有窗口，帮助调试
    list_all_windows()
    
    try:
        # 尝试连接已运行的实例
        try:
            app = pywinauto.Application().connect(path=path, timeout=1)
            print("检测到QMT进程已在运行")
        except (pywinauto.application.ProcessNotFoundError, pywinauto.timings.TimeoutError):
            print("未检测到QMT进程，将启动新实例")
            app = None
        
        # 尝试多种方式查找登录窗口
        print("正在查找登录窗口...")
        
        # 方法1: 使用正则表达式匹配（原始方法）
        try:
            login_window = wait_for_window(title_re=softname, class_name='#32770', timeout=3)
            print(f"方法1成功: 使用正则表达式 '{softname}' 和类名 '#32770' 找到窗口")
        except:
            pass
        
        # 方法2: 只使用正则表达式，不限制类名
        if login_window is None:
            try:
                login_window = wait_for_window(title_re=softname, timeout=3)
                print(f"方法2成功: 使用正则表达式 '{softname}' 找到窗口")
            except:
                pass
        
        # 方法3: 如果QMT进程已运行，直接查找该进程的对话框窗口
        if login_window is None and app is not None:
            try:
                # 获取QMT进程的所有窗口
                process_id = None
                try:
                    import psutil
                    process_name = os.path.basename(path)
                    for proc in psutil.process_iter(['pid', 'name', 'exe']):
                        try:
                            if proc.info['name'] == process_name or (proc.info['exe'] and path.lower() in proc.info['exe'].lower()):
                                process_id = proc.info['pid']
                                break
                        except:
                            continue
                except:
                    pass
                
                if process_id:
                    # 查找该进程的所有窗口，特别是对话框窗口
                    windows = pywinauto.findwindows.find_windows()
                    for hwnd in windows:
                        try:
                            hwnd_pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                            if hwnd_pid == process_id:
                                title = win32gui.GetWindowText(hwnd)
                                win_class = win32gui.GetClassName(hwnd)
                                # 查找对话框窗口（#32770）或包含登录关键词的窗口
                                if win_class == '#32770' or ('登录' in title and 'QMT' in title):
                                    # 排除编辑器窗口
                                    if not any(exclude in title for exclude in ['Cursor', '蚂蚁量化', '编辑器', 'Editor', '.py']):
                                        login_window = hwnd
                                        print(f"方法3成功: 通过QMT进程找到登录窗口 '{title}'")
                                        break
                        except:
                            continue
            except Exception as e:
                print(f"方法3失败: {e}")
        
        # 方法4: 使用关键词灵活匹配，限制为对话框窗口
        if login_window is None:
            keywords = ['QMT', '国金', '登录', '交易']
            login_window = find_window_flexible(
                title_keywords=keywords, 
                class_name='#32770',
                process_path=path
            )
            if login_window:
                print(f"方法4成功: 使用关键词 {keywords} 和类名 '#32770' 找到窗口")
        
        # 方法5: 只使用关键词，但限制为QMT进程的窗口
        if login_window is None:
            keywords = ['QMT', '国金', '登录']
            login_window = find_window_flexible(
                title_keywords=keywords,
                process_path=path
            )
            if login_window:
                print(f"方法5成功: 使用关键词 {keywords} 找到窗口（限制为QMT进程）")
        
        # 验证找到的窗口是否是真正的QMT登录窗口
        if login_window:
            try:
                window_title = win32gui.GetWindowText(login_window)
                window_class = win32gui.GetClassName(login_window)
                process_id = win32process.GetWindowThreadProcessId(login_window)[1]
                
                # 登录窗口必须是对话框窗口（#32770），不能是主窗口
                is_dialog = (window_class == '#32770')
                is_main_window = (window_class in ['Qt5QWindowIcon', 'Qt5QWindowToolSaveBits', 'Qt5QWindowOwnDCIcon'])
                
                # 检查进程是否匹配
                process_matches = False
                try:
                    import psutil
                    proc = psutil.Process(process_id)
                    proc_exe = proc.exe()
                    process_name = os.path.basename(path)
                    if process_name.lower() in os.path.basename(proc_exe).lower() or path.lower() in proc_exe.lower():
                        process_matches = True
                except Exception as e:
                    print(f"检查进程时出错: {e}")
                    pass
                
                # 检查是否是编辑器窗口
                editor_keywords = ['Cursor', 'VS Code', 'Visual Studio', 'PyCharm', '蚂蚁量化', '编辑器', 'Editor', '.py']
                is_editor = any(keyword in window_title for keyword in editor_keywords)
                
                # 如果找到的是主窗口而不是对话框，说明可能已经登录了
                if is_main_window:
                    print(f"\n⚠️ 找到的是QMT主窗口，不是登录窗口:")
                    print(f"  窗口标题: '{window_title}'")
                    print(f"  窗口类名: '{window_class}'")
                    print(f"  进程ID: {process_id}")
                    print("  这表示QMT可能已经登录，将检查登录状态...")
                    login_window = None
                elif is_editor or not process_matches or not is_dialog:
                    print(f"\n⚠️ 警告: 找到的窗口可能不是QMT登录窗口:")
                    print(f"  窗口标题: '{window_title}'")
                    print(f"  窗口类名: '{window_class}'")
                    print(f"  进程ID: {process_id}")
                    print(f"  是否对话框: {is_dialog}")
                    print(f"  是否编辑器: {is_editor}")
                    print(f"  进程匹配: {process_matches}")
                    print("  将重新查找...")
                    login_window = None
                else:
                    print(f"✅ 窗口验证通过: '{window_title}' (类名: {window_class}, 进程ID: {process_id})")
            except Exception as e:
                print(f"验证窗口时出错: {e}")
                import traceback
                traceback.print_exc()
                login_window = None
        
        # 如果还是找不到，先检查是否已经登录（主窗口已存在）
        if login_window is None:
            print("\n未找到登录窗口，检查是否已经登录...")
            # 检查QMT主窗口是否已存在
            main_window_keywords = ['QMT', '国金', '国金证券']
            exclude_keywords = ['Cursor', 'VS Code', 'Visual Studio', 'PyCharm', 'trade_record', 'main_window', 
                               '编辑器', 'Editor', '.py', '.js', '.html', '记事本', 'Notepad', '蚂蚁量化']
            
            main_window_found = False
            try:
                windows = pywinauto.findwindows.find_windows()
                for hwnd in windows:
                    try:
                        title = win32gui.GetWindowText(hwnd)
                        if not title:
                            continue
                        
                        # 排除编辑器和其他无关窗口
                        if any(exclude in title for exclude in exclude_keywords):
                            continue
                        
                        # 检查是否包含QMT相关关键词
                        if any(keyword in title for keyword in main_window_keywords):
                            # 检查窗口是否属于QMT进程
                            try:
                                hwnd_pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                                import psutil
                                proc = psutil.Process(hwnd_pid)
                                proc_exe = proc.exe()
                                process_name = os.path.basename(path)
                                if process_name.lower() in os.path.basename(proc_exe).lower():
                                    main_window_found = True
                                    print(f"✅ 检测到QMT主窗口已存在: '{title}'，QMT已经登录")
                                    print("   跳过登录步骤")
                                    return  # 已经登录，直接返回
                            except:
                                pass
                    except:
                        continue
            except:
                pass
            
            # 如果主窗口不存在，等待登录窗口出现（可能需要一些时间）
            if not main_window_found:
                print("QMT主窗口不存在，等待登录窗口出现...")
                try:
                    # 等待登录对话框窗口出现
                    login_window = wait_for_window(title_re=softname, class_name='#32770', timeout=10)
                    print(f"✅ 等待后找到登录窗口")
                except:
                    # 如果还是找不到，尝试更宽松的匹配
                    try:
                        keywords = ['QMT', '国金', '登录']
                        login_window = find_window_flexible(
                            title_keywords=keywords,
                            class_name='#32770',
                            process_path=path
                        )
                        if login_window:
                            print(f"✅ 使用宽松匹配找到登录窗口")
                    except:
                        pass
                
                if login_window is None:
                    print("\n无法自动找到登录窗口，请检查以下信息：")
                    list_all_windows()
                    print(f"当前使用的窗口匹配规则: title_re='{softname}', class_name='#32770'")
                    print("请根据上面的窗口列表，修改代码中的 softname 变量以匹配正确的窗口标题")
                    print("\n提示: 如果QMT已经登录，程序会跳过登录步骤")
                    raise Exception("无法找到登录窗口")
        
        # 如果app为None，尝试通过窗口句柄连接
        if app is None:
            try:
                # 尝试通过进程ID连接
                process_id = win32process.GetWindowThreadProcessId(login_window)[1]
                app = pywinauto.Application().connect(process=process_id)
                print("通过进程ID连接到应用程序")
            except Exception as e1:
                try:
                    # 如果通过进程ID连接失败，尝试通过窗口句柄连接
                    app = pywinauto.Application().connect(handle=login_window)
                    print("通过窗口句柄连接到应用程序")
                except Exception as e2:
                    # 如果都失败，尝试通过窗口标题连接
                    try:
                        window_title = win32gui.GetWindowText(login_window)
                        app = pywinauto.Application().connect(title=window_title)
                        print(f"通过窗口标题 '{window_title}' 连接到应用程序")
                    except Exception as e3:
                        # 最后尝试：通过窗口句柄直接操作
                        print("尝试直接使用窗口句柄...")
                        app = pywinauto.Application(backend='win32')
                        # 使用 connect 方法连接到窗口
                        try:
                            app.connect(handle=login_window)
                        except:
                            # 如果还是失败，尝试通过进程连接
                            process_id = win32process.GetWindowThreadProcessId(login_window)[1]
                            app.connect(process=process_id)
        
        user_login_window = app.window(handle=login_window)
        time.sleep(1)  # 确保窗口完全就绪
        
        # 记录登录窗口标题，用于后续验证
        login_window_title = win32gui.GetWindowText(login_window)
        print(f"找到登录窗口: '{login_window_title}'")
        
        # 动态定位输入框并输入凭证
        input_credentials(user_login_window, username, pwd)
        print("已输入用户名和密码，等待登录处理...")
        
        # 等待登录窗口关闭（表示登录处理中）
        print("等待登录窗口关闭...")
        max_wait = 30  # 最多等待30秒
        wait_count = 0
        while wait_count < max_wait:
            try:
                # 检查窗口是否还存在
                current_title = win32gui.GetWindowText(login_window)
                if current_title != login_window_title:
                    print("登录窗口已关闭")
                    break
            except:
                # 窗口句柄无效，说明窗口已关闭
                print("登录窗口已关闭")
                break
            time.sleep(0.5)
            wait_count += 1
        
        # 检查进程是否在运行（可能是后台/静默登录）
        print("检查QMT进程是否在运行...")
        process_running = False
        process_name = os.path.basename(path)
        process_pid = None
        
        # 方法1: 使用psutil（如果可用）
        try:
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    if proc.info['name'] == process_name or (proc.info['exe'] and path.lower() in proc.info['exe'].lower()):
                        process_running = True
                        process_pid = proc.info['pid']
                        print(f"✅ 检测到QMT进程正在运行: PID={process_pid}, 名称={proc.info['name']}")
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except NameError:
            # psutil未安装，使用备用方法
            print("psutil未安装，使用备用方法检查进程...")
        except Exception as e:
            print(f"使用psutil检查进程时出错: {e}")
        
        # 方法2: 如果psutil不可用，尝试通过窗口句柄获取进程ID
        if not process_running:
            try:
                process_id = win32process.GetWindowThreadProcessId(login_window)[1]
                # 检查进程是否还在运行
                try:
                    import win32api
                    handle = win32api.OpenProcess(0x1000, False, process_id)  # PROCESS_QUERY_INFORMATION
                    if handle:
                        process_running = True
                        process_pid = process_id
                        print(f"✅ 通过窗口句柄检测到QMT进程: PID={process_pid}")
                        win32api.CloseHandle(handle)
                except:
                    pass
            except:
                pass
        
        # 方法3: 尝试通过pywinauto连接进程
        if not process_running:
            try:
                test_app = pywinauto.Application().connect(path=path, timeout=1)
                if test_app:
                    process_running = True
                    print("✅ 通过pywinauto检测到QMT进程正在运行")
            except:
                pass
        
        # 等待主程序窗口出现
        print("等待主程序窗口出现...")
        main_window = None
        # 更精确的关键词匹配，排除编辑器和其他程序
        main_window_keywords = ['QMT', '国金', '国金证券']
        exclude_keywords = ['Cursor', 'VS Code', 'Visual Studio', 'PyCharm', 'trade_record', 'main_window', 
                           '编辑器', 'Editor', '.py', '.js', '.html', '记事本', 'Notepad', '蚂蚁量化']
        
        for attempt in range(20):  # 最多尝试20次，每次等待1秒
            # 尝试查找主程序窗口（包括最小化的窗口）
            try:
                # 查找所有窗口，包括不可见的
                windows = pywinauto.findwindows.find_windows()
                for hwnd in windows:
                    try:
                        title = win32gui.GetWindowText(hwnd)
                        if not title:
                            continue
                        
                        # 排除登录窗口
                        if '登录' in title or title == login_window_title:
                            continue
                        
                        # 排除编辑器和其他无关窗口
                        if any(exclude in title for exclude in exclude_keywords):
                            continue
                        
                        # 检查是否包含QMT相关关键词
                        if any(keyword in title for keyword in main_window_keywords):
                            # 检查窗口是否可见（可能被最小化）
                            if win32gui.IsWindowVisible(hwnd):
                                main_window = hwnd
                                main_title = title
                                print(f"找到主程序窗口: '{main_title}'")
                                break
                            else:
                                # 窗口存在但不可见（可能是最小化或隐藏）
                                main_window = hwnd
                                main_title = title
                                print(f"找到主程序窗口（可能最小化）: '{main_title}'")
                                break
                    except:
                        continue
                if main_window:
                    break
            except:
                pass
            
            time.sleep(1)
            if attempt % 5 == 0 and attempt > 0:
                print(f"  已等待 {attempt} 秒，继续等待主程序窗口...")
        
        # 如果还是没找到，尝试更宽松的匹配（但排除编辑器）
        if main_window is None:
            print("使用更宽松的匹配方式查找主程序窗口...")
            try:
                windows = pywinauto.findwindows.find_windows()
                for hwnd in windows:
                    try:
                        title = win32gui.GetWindowText(hwnd)
                        if not title:
                            continue
                        
                        # 排除登录窗口和编辑器
                        if ('登录' in title or title == login_window_title or 
                            any(exclude in title for exclude in exclude_keywords)):
                            continue
                        
                        # 检查窗口类名，QMT通常有特定的窗口类
                        class_name = win32gui.GetClassName(hwnd)
                        # 排除常见的编辑器类名
                        editor_classes = ['Chrome_WidgetWin_1', 'Notepad', 'Vim', 'CodeWindow']
                        if class_name in editor_classes:
                            continue
                        
                        # 如果窗口标题较长且不包含排除关键词，可能是主窗口
                        if len(title) > 5 and 'QMT' in title:
                            main_window = hwnd
                            main_title = title
                            print(f"找到可能的主程序窗口: '{main_title}' (类名: {class_name})")
                            break
                    except:
                        continue
            except:
                pass
        
        # 检查是否有错误提示窗口
        error_keywords = ['错误', '失败', '提示', '警告', 'Error', 'Failed']
        error_window = find_window_flexible(title_keywords=error_keywords)
        if error_window:
            error_title = win32gui.GetWindowText(error_window)
            print(f"\n⚠️ 检测到可能的错误窗口: '{error_title}'")
            print("请检查登录是否成功，可能需要手动处理")
            list_all_windows()
        
        # 验证登录结果
        if main_window:
            main_title = win32gui.GetWindowText(main_window)
            class_name = win32gui.GetClassName(main_window)
            
            # 再次验证，确保不是编辑器窗口
            if any(exclude in main_title for exclude in exclude_keywords):
                print(f"\n⚠️ 警告: 找到的窗口可能是编辑器窗口: '{main_title}'")
                print("将重新查找真正的QMT主窗口...")
                main_window = None
            else:
                print(f"\n✅ 登录成功！主程序窗口已出现: '{main_title}' (类名: {class_name})")
                # 尝试恢复并置顶窗口
                try:
                    # 恢复窗口（如果被最小化）
                    win32gui.ShowWindow(main_window, 9)  # SW_RESTORE
                    time.sleep(0.5)
                    win32gui.SetForegroundWindow(main_window)
                    print("已将主程序窗口恢复并置顶")
                except Exception as e:
                    print(f"恢复窗口时出错: {e}")
        
        # 如果验证失败，重新查找或提示用户
        if main_window is None:
            print("\n⚠️ 未找到QMT主程序窗口")
            print("可能的情况:")
            print("  1. QMT主窗口标题不包含 'QMT' 或 '国金'")
            print("  2. 窗口标题被其他关键词匹配到了编辑器")
            print("  3. 主程序窗口需要更长时间才能出现")
            print("\n当前所有窗口列表（用于调试）:")
            list_all_windows()
            print("\n💡 提示: 请查看上面的窗口列表，找到真正的QMT主窗口标题")
            print("   然后可以修改代码中的 main_window_keywords 或 exclude_keywords 来精确匹配")
        elif process_running:
            print(f"\n✅ 登录成功（后台/静默模式）")
            print(f"QMT进程正在运行，程序在系统托盘运行")
            print("\n💡 QMT程序已成功登录并在后台运行")
            print("   如果需要显示主窗口，请从系统托盘图标中恢复窗口")
            
            # 尝试通过进程ID查找并恢复窗口
            try:
                if process_pid:
                    # 尝试查找该进程的所有窗口
                    windows = pywinauto.findwindows.find_windows()
                    for hwnd in windows:
                        try:
                            hwnd_pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                            if hwnd_pid == process_pid:
                                title = win32gui.GetWindowText(hwnd)
                                if title and not any(exclude in title for exclude in exclude_keywords):
                                    # 尝试恢复窗口
                                    try:
                                        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                                        time.sleep(0.3)
                                        win32gui.SetForegroundWindow(hwnd)
                                        print(f"   已尝试恢复窗口: '{title}'")
                                        break
                                    except:
                                        pass
                        except:
                            continue
            except:
                pass
        else:
            print("\n⚠️ 警告: 未检测到主程序窗口，且进程可能未运行")
            print("可能的原因:")
            print("  1. 登录失败（用户名或密码错误）")
            print("  2. 主程序窗口标题不包含预期关键词")
            print("  3. 主程序需要更长时间启动")
            print("  4. 程序启动失败")
            print("\n当前所有窗口列表:")
            list_all_windows()
        
    except Exception as e:
        print(f"\n❌ 登录失败: {e}")
        import traceback
        traceback.print_exc()
        list_all_windows()
        raise

credentials = read_credentials()
username = credentials.get('username')
pwd = credentials.get('password')
path = r'D:\\国金证券QMT交易端 - 副本\\bin.x64\\XtItClient.exe'
softname = "国金QMT交易端.*"
dotime = '10:05'  # 启动时间格式 09:01

def job():
    login_qmt(username, pwd, path, softname)

# 直接安排每天 dotime 执行登录任务
schedule.every().day.at(dotime).do(job)

job()  #这里去掉job()前的"#"立即运行不等待,job()前面加#按照预定时间等待执行
# 保持脚本运行以执行定时任务
while True:
    schedule.run_pending()
    time.sleep(1)
