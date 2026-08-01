import time
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import pandas as pd

def find_file_path(d_path, c_path=None):
    """
    查找文件路径，如果 D 盘找不到就找 C 盘的同名文件
    
    Args:
        d_path: D 盘的路径
        c_path: C 盘的路径（可选，如果不提供则自动从 d_path 转换）
    
    Returns:
        找到的文件路径，如果都找不到则返回原始 d_path
    """
    if c_path is None:
        # 自动将 D: 替换为 C:
        c_path = d_path.replace('D:', 'C:', 1) if d_path.startswith('D:') else d_path.replace('d:', 'c:', 1)
    
    # 先检查 D 盘
    if os.path.exists(d_path):
        print(f"找到文件: {d_path}")
        return d_path
    
    # 如果 D 盘找不到，检查 C 盘
    if os.path.exists(c_path):
        print(f"D 盘未找到，使用 C 盘路径: {c_path}")
        return c_path
    
    # 都找不到，返回原始路径（让 Selenium 报错）
    print(f"警告：D 盘和 C 盘都未找到文件")
    print(f"  D 盘路径: {d_path}")
    print(f"  C 盘路径: {c_path}")
    return d_path

def clean_dataframe(df):
    """
    清理DataFrame，移除无效行和空列
    """
    if df is None or df.empty:
        return df
    
    # 创建副本以避免修改原始数据
    df_clean = df.copy()
    
    # 1. 删除包含"无更多数据"的行
    mask = df_clean.apply(lambda row: row.astype(str).str.contains('无更多数据', na=False).any(), axis=1)
    if mask.any():
        rows_removed = mask.sum()
        print(f"删除 {rows_removed} 行包含'无更多数据'的行")
        df_clean = df_clean[~mask]
    
    # 2. 删除完全空白的行（所有列都为空或只包含空白字符）
    mask = df_clean.apply(lambda row: row.astype(str).str.strip().eq('').all() | 
                          row.astype(str).str.strip().eq('nan').all(), axis=1)
    if mask.any():
        rows_removed = mask.sum()
        print(f"删除 {rows_removed} 行完全空白的行")
        df_clean = df_clean[~mask]
    
    # 3. 删除末尾完全为空的列（从右往左检查）
    while len(df_clean.columns) > 0 and len(df_clean) > 0:
        try:
            # 使用 iloc 直接访问最后一列，更安全
            last_col_index = len(df_clean.columns) - 1
            last_col_series = df_clean.iloc[:, last_col_index]
            
            # 检查最后一列是否完全为空或只包含空白字符
            if len(last_col_series) > 0:
                is_empty = last_col_series.astype(str).str.strip().eq('').all()
                is_nan = last_col_series.astype(str).str.strip().eq('nan').all()
                if is_empty or is_nan:
                    last_col_name = df_clean.columns[last_col_index]
                    print(f"删除末尾空列: {last_col_name}")
                    df_clean = df_clean.iloc[:, :-1]
                else:
                    break
            else:
                break
        except Exception as e:
            print(f"删除空列时出错: {e}")
            break
    
    # 4. 清理每行末尾的空值（将末尾的空字符串或'nan'替换为空字符串，但不删除列）
    # 这一步已经在前面处理了，因为pandas会自动处理空值
    
    # 5. 重置索引
    df_clean = df_clean.reset_index(drop=True)
    
    return df_clean

def _apply_chrome_proxy_options(chrome_options):
    """配置 Chrome 代理：默认直连，避免继承失效的系统代理（如 127.0.0.1:7078）。"""
    use_system_proxy = os.environ.get('SELENIUM_USE_SYSTEM_PROXY', '').strip().lower()
    if use_system_proxy in ('1', 'true', 'yes'):
        print("使用系统代理（SELENIUM_USE_SYSTEM_PROXY=1）")
        return
    chrome_options.add_argument('--no-proxy-server')
    chrome_options.add_argument('--proxy-bypass-list=*')
    print("已禁用 Chrome 系统代理（直连）；如需走代理请设置 SELENIUM_USE_SYSTEM_PROXY=1")

def get_limit_up_stocks_selenium():
    """
    使用Selenium控制测试版Chrome获取涨停板数据
    """
    chrome_options = Options()
    
    # --- 1. 指定你的测试版Chrome浏览器路径 ---
    # 如果 D 盘找不到就找 C 盘
    chrome_binary_path = find_file_path(r"D:\download\chrome-win64\chrome-win64\chrome.exe")
    chrome_options.binary_location = chrome_binary_path
    _apply_chrome_proxy_options(chrome_options)
    
    # --- 2. 指定你的ChromeDriver路径 ---
    # 如果 D 盘找不到就找 C 盘
    driver_path = find_file_path(r"D:\download\chromedriver-win64\chromedriver.exe")
    service = Service(executable_path=driver_path)
    
    # 启动浏览器
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        # 打开东方财富网涨停板页面
        url = "https://quote.eastmoney.com/ztb/detail#type=ztb"
        driver.get(url)
        print(f"成功打开页面: {url}")

        wait = WebDriverWait(driver, 30)

        # 打开网页后直接等待3秒
        print("等待3秒...")
        time.sleep(3)
        try:
            # 使用JavaScript点击中间卡片以外的位置（左上角、右上角、左下角、右下角）
            driver.execute_script("""
                // 获取视口尺寸
                var width = window.innerWidth;
                var height = window.innerHeight;
                
                // 定义多个边缘位置（避开中间区域）
                var positions = [
                    {x: width * 0.1, y: height * 0.1},      // 左上角
                    {x: width * 0.9, y: height * 0.1},      // 右上角
                    {x: width * 0.1, y: height * 0.9},      // 左下角
                    {x: width * 0.9, y: height * 0.9},      // 右下角
                    {x: width * 0.05, y: height * 0.5},     // 左边缘中间
                    {x: width * 0.95, y: height * 0.5},     // 右边缘中间
                    {x: width * 0.5, y: height * 0.05},     // 上边缘中间
                    {x: width * 0.5, y: height * 0.95}      // 下边缘中间
                ];
                
                // 尝试点击每个位置，直到成功点击一个
                for (var i = 0; i < positions.length; i++) {
                    var pos = positions[i];
                    var element = document.elementFromPoint(pos.x, pos.y);
                    
                    // 检查是否点击到了中间卡片（通过检查元素是否包含特定类名或文本）
                    if (element) {
                        var className = element.className || '';
                        var text = element.innerText || element.textContent || '';
                        var tagName = element.tagName || '';
                        
                        // 跳过可能是卡片的元素（包含card、popup、modal等关键词）
                        if (className.includes('card') || className.includes('popup') || 
                            className.includes('modal') || className.includes('dialog') ||
                            tagName === 'BUTTON' || tagName === 'A') {
                            continue;
                        }
                        
                        // 创建并触发点击事件
                        var event = new MouseEvent('click', {
                            view: window,
                            bubbles: true,
                            cancelable: true,
                            clientX: pos.x,
                            clientY: pos.y
                        });
                        
                        element.dispatchEvent(event);
                        return true; // 成功点击，退出
                    }
                }
                
                // 如果所有位置都失败，尝试点击body的左上角
                var body = document.body;
                if (body) {
                    var event = new MouseEvent('click', {
                        view: window,
                        bubbles: true,
                        cancelable: true,
                        clientX: 10,
                        clientY: 10
                    });
                    body.dispatchEvent(event);
                }
                return false;
            """)
            print("已点击中间卡片以外的位置关闭弹窗")
            time.sleep(0.5)
        except Exception as e:
            print(f"点击页面边缘位置关闭弹窗时出错: {e}")
        

        # 点击"点击加载更多"按钮，一次性加载所有涨停股票数据
        print("查找并点击'点击加载更多'按钮...")
        
        # 滚动到页面底部，确保按钮可见
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)
        
        # 查找按钮
        load_more_btn = None
        for selector in ["//*[contains(text(), '点击加载更多')]", "//*[contains(text(), '加载更多')]"]:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed() and ('点击加载更多' in element.text or '加载更多' in element.text):
                        load_more_btn = element
                        break
                if load_more_btn:
                    break
            except:
                continue
        
        # 如果找到了按钮，点击它
        if load_more_btn:
            try:
                # 滚动按钮到视口中心
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more_btn)
                time.sleep(0.5)
                
                # 使用JavaScript点击
                driver.execute_script("arguments[0].click();", load_more_btn)
                print("已点击'点击加载更多'按钮")
                
                # 记录点击前的行数
                try:
                    before_count = len(driver.find_elements(By.XPATH, '//table[@id="dt_1"]//tr'))
                except:
                    before_count = 0
                
                # 等待数据加载完成
                time.sleep(2)
                max_wait_time = 60
                waited_time = 0
                previous_row_count = before_count
                stable_count = 0
                
                while waited_time < max_wait_time:
                    time.sleep(1)
                    waited_time += 1
                    
                    try:
                        current_row_count = len(driver.find_elements(By.XPATH, '//table[@id="dt_1"]//tr'))
                        if current_row_count > previous_row_count:
                            previous_row_count = current_row_count
                            stable_count = 0
                        else:
                            stable_count += 1
                            if stable_count >= 3:
                                print(f"数据加载完成，共 {current_row_count} 行数据")
                                break
                    except:
                        if waited_time % 5 == 0:
                            print(f"等待数据加载中... (已等待 {waited_time} 秒)")
                
                # 最终滚动到底部
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
            except Exception as e:
                print(f"点击按钮时出错: {e}")
        else:
            print("未找到'点击加载更多'按钮，可能页面已经显示全部数据")

        # 提取数据，使用多种方式尝试（不一定是表格）
        data_container = None
        container_type = None
        
        # 尝试多种方式查找数据容器
        try:
            data_container = driver.find_element(By.ID, 'dt_1')
            container_type = 'table_id'
            print("找到数据容器: dt_1 (ID)")
        except NoSuchElementException:
            try:
                data_container = driver.find_element(By.XPATH, '//table[@id="dt_1"]')
                container_type = 'table_xpath'
                print("找到数据容器: dt_1 (XPath table)")
            except NoSuchElementException:
                # 尝试查找其他可能的数据容器
                try:
                    data_container = driver.find_element(By.XPATH, '//div[@id="dt_1"]')
                    container_type = 'div_id'
                    print("找到数据容器: dt_1 (DIV ID)")
                except NoSuchElementException:
                    # 尝试查找包含数据的div
                    try:
                        data_container = driver.find_element(By.XPATH, '//div[contains(@class, "table") or contains(@class, "list")]')
                        container_type = 'div_class'
                        print("找到数据容器: div (class包含table或list)")
                    except NoSuchElementException:
                        print("错误：无法找到数据容器")
                        # 尝试获取页面源码用于调试
                        print("当前页面URL:", driver.current_url)
                        print("页面标题:", driver.title)
                        # 打印页面HTML的一部分用于调试
                        try:
                            page_source = driver.page_source[:2000]  # 前2000个字符
                            print("页面源码前2000字符:")
                            print(page_source)
                        except:
                            pass
                        return None

        if data_container is None:
            print("错误：数据容器为None")
            return None

        # 根据容器类型提取数据
        headers = []
        rows = []
        
        # 如果是表格元素
        if container_type in ['table_id', 'table_xpath']:
            print("按表格方式提取数据...")
            try:
                # 提取表头
                th_elements = data_container.find_elements(By.TAG_NAME, 'th')
                if th_elements:
                    headers = [th.text.strip() for th in th_elements]
                else:
                    # 尝试其他方式获取表头
                    try:
                        thead = data_container.find_element(By.TAG_NAME, 'thead')
                        headers = [th.text.strip() for th in thead.find_elements(By.TAG_NAME, 'th')]
                    except:
                        pass
            except Exception as e:
                print(f"提取表头时出错: {e}")
            
            if not headers:
                # 如果无法获取表头，使用默认列名
                headers = ['代码', '名称', '最新价', '涨跌幅', '涨跌额', '成交量', '成交额', '换手率', '振幅', '最高', '最低', '今开', '昨收']
            
            print(f"表头: {headers}")

            # 提取数据行
            try:
                tr_elements = data_container.find_elements(By.TAG_NAME, 'tr')
                print(f"找到 {len(tr_elements)} 行")
                
                for i, tr in enumerate(tr_elements[1:], 1):  # 跳过表头行
                    try:
                        row_text = tr.text.strip()
                        # 跳过包含"点击加载更多"的行
                        if '点击加载更多' in row_text:
                            print(f"跳过包含'点击加载更多'的行")
                            continue
                        # 跳过包含"无更多数据"的行
                        if '无更多数据' in row_text:
                            print(f"跳过包含'无更多数据'的行")
                            continue
                        
                        # 使用 get_attribute('textContent') 获取完整文本，避免CSS截断
                        cells = []
                        for td in tr.find_elements(By.TAG_NAME, 'td'):
                            try:
                                # 优先使用 textContent，如果失败则使用 text
                                cell_text = td.get_attribute('textContent')
                                if cell_text is None:
                                    cell_text = td.text
                                cells.append(cell_text.strip() if cell_text else '')
                            except:
                                cells.append('')
                        # 跳过完全空白的行
                        if not cells or not any(cell for cell in cells):
                            continue
                        # 跳过所有单元格都为空或只包含空白字符的行
                        if all(not cell or cell.strip() == '' for cell in cells):
                            continue
                        rows.append(cells)
                        if i <= 3:  # 打印前3行用于调试
                            print(f"第{i}行数据: {cells}")
                    except Exception as e:
                        print(f"提取第{i}行时出错: {e}")
                        continue
            except Exception as e:
                print(f"提取数据行时出错: {e}")
        
        # 如果是div或其他元素，尝试提取其中的数据
        else:
            print("按非表格方式提取数据...")
            try:
                # 尝试在div中查找表格
                inner_table = data_container.find_element(By.TAG_NAME, 'table')
                if inner_table:
                    print("在div中找到内部表格，按表格方式提取...")
                    th_elements = inner_table.find_elements(By.TAG_NAME, 'th')
                    if th_elements:
                        headers = [th.text.strip() for th in th_elements]
                    
                    if not headers:
                        headers = ['代码', '名称', '最新价', '涨跌幅', '涨跌额', '成交量', '成交额', '换手率', '振幅', '最高', '最低', '今开', '昨收']
                    
                    tr_elements = inner_table.find_elements(By.TAG_NAME, 'tr')
                    print(f"找到 {len(tr_elements)} 行")
                    
                    for i, tr in enumerate(tr_elements[1:], 1):
                        try:
                            row_text = tr.text.strip()
                            # 跳过包含"点击加载更多"的行
                            if '点击加载更多' in row_text:
                                print(f"跳过包含'点击加载更多'的行")
                                continue
                            # 跳过包含"无更多数据"的行
                            if '无更多数据' in row_text:
                                print(f"跳过包含'无更多数据'的行")
                                continue
                            
                            # 使用 get_attribute('textContent') 获取完整文本，避免CSS截断
                            cells = []
                            for td in tr.find_elements(By.TAG_NAME, 'td'):
                                try:
                                    # 优先使用 textContent，如果失败则使用 text
                                    cell_text = td.get_attribute('textContent')
                                    if cell_text is None:
                                        cell_text = td.text
                                    cells.append(cell_text.strip() if cell_text else '')
                                except:
                                    cells.append('')
                            # 跳过完全空白的行
                            if not cells or not any(cell for cell in cells):
                                continue
                            # 跳过所有单元格都为空或只包含空白字符的行
                            if all(not cell or cell.strip() == '' for cell in cells):
                                continue
                            rows.append(cells)
                            if i <= 3:
                                print(f"第{i}行数据: {cells}")
                        except Exception as e:
                            print(f"提取第{i}行时出错: {e}")
                            continue
                else:
                    print("警告：在div中未找到表格元素，无法提取数据")
            except Exception as e:
                print(f"按非表格方式提取数据时出错: {e}")
                print("尝试打印容器内容用于调试...")
                try:
                    container_text = data_container.text[:500]  # 前500字符
                    print(f"容器内容前500字符: {container_text}")
                except:
                    pass

        if rows:
            # 确保列数匹配
            max_cols = max(len(row) for row in rows) if rows else 0
            if len(headers) != max_cols:
                print(f"警告：表头列数({len(headers)})与数据列数({max_cols})不匹配，调整表头...")
                if len(headers) < max_cols:
                    headers.extend([f'列{i+1}' for i in range(len(headers), max_cols)])
                else:
                    headers = headers[:max_cols]
            
            df = pd.DataFrame(rows, columns=headers[:max_cols])
            print(f"成功提取 {len(df)} 行数据")
            # 清理数据
            df = clean_dataframe(df)
            print(f"清理后剩余 {len(df)} 行数据")
            return df
        else:
            print("未能从页面中提取到数据。")
            # 保存页面截图用于调试
            try:
                driver.save_screenshot("debug_screenshot.png")
                print("已保存页面截图: debug_screenshot.png")
            except:
                pass
            return None

    except TimeoutException as e:
        print(f"页面加载超时: {e}")
        return None
    except Exception as e:
        print(f"发生错误: {e}")
        import traceback
        traceback.print_exc()
        return None

    finally:
        driver.quit()
        print("浏览器已关闭。")

# --- 主程序 ---
if __name__ == "__main__":
    print("正在使用Selenium获取涨停板数据...")
    df = get_limit_up_stocks_selenium()

    if df is not None and not df.empty:
        print(f"\n获取成功！共 {len(df)} 只涨停股票：")
        print(df.to_string(index=False))

        # 保存为CSV
        try:
            from datetime import datetime
            today_str = datetime.now().strftime("%Y%m%d")
            filename = f"涨停板数据_Selenium_{today_str}.csv"
            # 在保存前再次清理数据，确保文件干净
            df_clean = clean_dataframe(df)
            df_clean.to_csv(filename, index=False, encoding='utf_8_sig')
            print(f"\n数据已保存至 {filename} (共 {len(df_clean)} 行数据)")
        except Exception as e:
            print(f"\n保存CSV文件失败: {e}")
    else:
        print("\n未能获取到有效的涨停板数据。")