import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
import pandas as pd
from typing import Optional
from datetime import date, datetime, timedelta
import os
import argparse
from utils.trading_day import is_tradeday
import sys

# Windows 控制台常为 GBK：避免 print 含 ✓/✗ 等字符时把整页提取打崩
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def last_tradeday_on_or_before(check_date: Optional[date] = None) -> Optional[date]:
    """不晚于 check_date 的最近一个交易日（含当天）。不依赖 trading_day 新版本。"""
    if check_date is None:
        check_date = date.today()
    if is_tradeday(check_date):
        return check_date
    d = check_date
    for _ in range(15):
        d -= timedelta(days=1)
        if is_tradeday(d):
            return d
    return None


def previous_tradeday(before: Optional[date] = None) -> Optional[date]:
    """严格早于 before 的最近一个交易日。"""
    if before is None:
        before = date.today()
    d = before - timedelta(days=1)
    for _ in range(15):
        if is_tradeday(d):
            return d
        d -= timedelta(days=1)
    return None


def _flow_csv_path(save_date_str: str, history_dir: str = "history_data") -> str:
    from utils.main_force_inflow_path import ensure_flow_data_dir, flow_csv_path

    ensure_flow_data_dir(history_dir)
    return flow_csv_path(save_date_str, history_dir)

def parse_capital_flow_amount(value_str):
    """
    解析主力净流入金额字符串，转换为万元（数值）
    
    支持的格式：
    - "1234.56万" -> 1234.56
    - "1.23亿" -> 12300.0
    - "1234万" -> 1234.0
    - "-1234.56万" -> -1234.56
    - "-1.23亿" -> -12300.0
    
    返回：金额（万元，浮点数），如果解析失败返回None
    """
    try:
        # 处理 pandas Series 的情况
        if isinstance(value_str, pd.Series):
            if value_str.empty:
                return None
            value_str = value_str.iloc[0] if len(value_str) > 0 else None
        
        # 检查是否为 None 或 NaN
        if value_str is None or pd.isna(value_str):
            return None
        
        # 转换为字符串并去除空格
        value_str = str(value_str).strip()
        
        # 如果为空，返回None
        if not value_str or value_str == '' or value_str.lower() == 'nan':
            return None
        
        # 判断正负号
        is_negative = False
        if value_str.startswith('-'):
            is_negative = True
            value_str = value_str[1:].strip()
        elif value_str.startswith('+'):
            value_str = value_str[1:].strip()
        
        # 提取数字部分
        if '亿' in value_str:
            # 处理"亿"单位
            number_part = value_str.replace('亿', '').strip()
            try:
                amount = float(number_part)
                amount = amount * 10000  # 转换为万元
            except ValueError:
                return None
        elif '万' in value_str:
            # 处理"万"单位
            number_part = value_str.replace('万', '').strip()
            try:
                amount = float(number_part)
            except ValueError:
                return None
        else:
            # 尝试直接解析为数字（假设已经是万元）
            try:
                amount = float(value_str)
            except ValueError:
                return None
        
        # 应用正负号
        if is_negative:
            amount = -amount
        
        return amount
    except Exception as e:
        print(f"解析金额时出错: {value_str}, 错误: {e}")
        return None

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
    
    # 4. 重置索引
    df_clean = df_clean.reset_index(drop=True)
    
    return df_clean

def extract_page_data(driver):
    """
    从当前页面提取数据，返回DataFrame
    """
    try:
        # 滚动到表格位置，确保表格在视口中（便于Selenium访问）
        # 注意：此页面数据已全部显示，滚动不会触发数据加载
        try:
            scroll_result = driver.execute_script("""
                var table = document.querySelector('table#dt_1') || document.querySelector('table');
                if (table) {
                    // 只滚动到表格位置，确保在视口中
                    table.scrollIntoView({block: 'center', behavior: 'auto'});
                    return {found: true};
                }
                return {found: false};
            """)
            print(f"滚动到表格位置: {scroll_result}")
            time.sleep(1)  # 短暂等待，确保滚动完成
        except Exception as e:
            print(f"滚动时出错: {e}")
        
        # 等待DOM稳定（数据已全部显示，只需等待渲染完成）
        print("等待DOM稳定...")
        time.sleep(2)  # 减少等待时间，因为数据已全部显示
        
        # 直接提取数据（如果数据已存在，上面的检查已经确认了）
        # 如果数据不存在，这里也会尝试提取（可能数据在检查后加载了）
        js_data = driver.execute_script("""
            // 尝试多种方式找到表格（优先找有数据的表格）
            var allTables = document.querySelectorAll('table');
            var table = null;
            var maxDataRows = 0;
            
            // 先找id为dt_1的表格
            table = document.querySelector('table#dt_1');
            if (table) {
                console.log('找到表格: table#dt_1');
            } else {
                // 遍历所有表格，找数据最多的
                for (var i = 0; i < allTables.length; i++) {
                    var t = allTables[i];
                    var tbody = t.querySelector('tbody') || t;
                    var trs = tbody.querySelectorAll('tr');
                    var dataCount = 0;
                    for (var j = 0; j < trs.length; j++) {
                        var tds = trs[j].querySelectorAll('td');
                        if (tds.length >= 2) {
                            var code = tds[1].innerText.trim();
                            if (code && /^\\d{6}$/.test(code)) {
                                dataCount++;
                            }
                        }
                    }
                    if (dataCount > maxDataRows) {
                        maxDataRows = dataCount;
                        table = t;
                    }
                }
                if (table) {
                    console.log('找到数据最多的表格，数据行数:', maxDataRows);
                }
            }
            
            // 如果还是没找到，就用第一个表格
            if (!table && allTables.length > 0) {
                table = allTables[0];
                console.log('使用第一个表格');
            }
            
            if (!table) {
                return {error: '未找到表格', tableCount: allTables.length};
            }
            
            var rows = [];
            var headers = [];
            var debugInfo = {
                tableFound: true,
                hasThead: false,
                hasTbody: false,
                trCount: 0,
                tbodyTrCount: 0,
                theadTrCount: 0
            };
            
            // 获取表头
            var thead = table.querySelector('thead');
            if (thead) {
                debugInfo.hasThead = true;
                var headerRows = thead.querySelectorAll('tr');
                debugInfo.theadTrCount = headerRows.length;
                for (var i = 0; i < headerRows.length; i++) {
                    var ths = headerRows[i].querySelectorAll('th');
                    var headerRow = [];
                    for (var j = 0; j < ths.length; j++) {
                        headerRow.push(ths[j].innerText.trim());
                    }
                    if (headerRow.length > 0) {
                        headers = headers.concat(headerRow);
                    }
                }
            }
            
            // 获取数据行 - 尝试多种方式
            var tbody = table.querySelector('tbody');
            var trContainer = tbody || table;
            
            if (tbody) {
                debugInfo.hasTbody = true;
            }
            
            // 获取所有tr元素
            var trs = trContainer.querySelectorAll('tr');
            debugInfo.trCount = trs.length;
            if (tbody) {
                debugInfo.tbodyTrCount = tbody.querySelectorAll('tr').length;
            }
            
            console.log('调试信息:', debugInfo);
            console.log('表头数量:', headers.length);
            console.log('TR总数:', trs.length);
            
            // 遍历所有tr，包括表头行（用于调试）
            for (var i = 0; i < trs.length; i++) {
                var tr = trs[i];
                var rowText = tr.innerText.trim();
                
                // 检查是否是表头行（在tbody中也可能有表头）
                var isHeaderRow = false;
                if (tr.querySelectorAll('th').length > 0) {
                    isHeaderRow = true;
                }
                if (rowText.includes('序号') && rowText.includes('代码') && rowText.includes('名称')) {
                    isHeaderRow = true;
                }
                
                // 跳过表头行
                if (isHeaderRow) {
                    continue;
                }
                
                // 跳过空行
                if (!rowText || rowText.length < 10) {
                    continue;
                }
                
                var tds = tr.querySelectorAll('td');
                if (tds.length > 0) {
                    var row = [];
                    for (var j = 0; j < tds.length; j++) {
                        var cellText = tds[j].innerText.trim();
                        row.push(cellText);
                    }
                    
                    // 检查是否是数据行（至少包含股票代码）
                    if (row.length >= 2) {
                        // 尝试在第二列找代码，如果第二列不是代码，尝试其他列
                        var foundCode = false;
                        for (var k = 0; k < row.length; k++) {
                            if (row[k] && /^\\d{6}$/.test(row[k])) {
                                foundCode = true;
                                break;
                            }
                        }
                        
                        if (foundCode) {
                            rows.push(row);
                        } else {
                            // 即使没有找到代码，如果行有足够的数据，也尝试添加（可能是数据格式不同）
                            if (row.length >= 5 && row.some(function(cell) { return cell && cell.length > 0; })) {
                                console.log('可疑数据行（无代码）:', row.slice(0, 5));
                            }
                        }
                    }
                }
            }
            
            console.log('提取到的数据行数:', rows.length);
            if (rows.length > 0) {
                console.log('第一行数据示例:', rows[0]);
            }
            
            return {
                headers: headers,
                rows: rows,
                rowCount: rows.length,
                debugInfo: debugInfo
            };
        """)
        
        # 打印调试信息
        print(f"\n=== 数据提取结果 ===")
        if js_data:
            print(f"返回数据: {type(js_data)}, 包含键: {list(js_data.keys()) if isinstance(js_data, dict) else 'N/A'}")
            
        if js_data and 'debugInfo' in js_data:
            debug = js_data['debugInfo']
            print(f"调试信息: 表格找到={debug.get('tableFound')}, "
                  f"有thead={debug.get('hasThead')}, "
                  f"有tbody={debug.get('hasTbody')}, "
                  f"TR总数={debug.get('trCount')}, "
                  f"tbody中TR数={debug.get('tbodyTrCount')}")
        
        if js_data and 'error' in js_data:
            print(f"JavaScript错误: {js_data['error']}, 页面中表格数量: {js_data.get('tableCount', 0)}")
        
        if js_data and js_data.get('rowCount', 0) > 0:
            print(f"✓ 提取到 {js_data.get('rowCount', 0)} 行数据")
            if js_data.get('rows') and len(js_data['rows']) > 0:
                print(f"第一行数据示例: {js_data['rows'][0][:5]}")  # 只显示前5列
        elif js_data and js_data.get('rowCount', 0) == 0:
            print(f"⚠ 警告: 提取到0行数据")
        
        if js_data and js_data.get('rows') and len(js_data['rows']) > 0:
            print(f"✓ 使用JavaScript成功获取 {len(js_data['rows'])} 行数据")
            headers = js_data.get('headers', [])
            rows = js_data.get('rows', [])
            
            # 如果表头为空，使用默认表头
            if not headers or len(headers) < 5:
                headers = ['序号', '代码', '名称', '相关', '最新价', '今日涨跌幅', 
                          '今日主力净流入-净额', '今日主力净流入-净占比',
                          '今日超大单净流入-净额', '今日超大单净流入-净占比',
                          '今日大单净流入-净额', '今日大单净流入-净占比',
                          '今日中单净流入-净额', '今日中单净流入-净占比',
                          '今日小单净流入-净额', '今日小单净流入-净占比']
            
            # 确保列数匹配
            max_cols = max(len(row) for row in rows) if rows else 0
            if len(headers) != max_cols:
                if len(headers) < max_cols:
                    headers.extend([f'列{i+1}' for i in range(len(headers), max_cols)])
                else:
                    headers = headers[:max_cols]
            
            df = pd.DataFrame(rows, columns=headers[:max_cols])
            print(f"成功提取 {len(df)} 行数据")
            df = clean_dataframe(df)
            print(f"清理后剩余 {len(df)} 行数据")
            
            if not df.empty:
                return df
            else:
                print("JavaScript获取的数据为空")
                return None
        else:
            print("JavaScript未获取到数据")
            return None
    except Exception as e:
        print(f"提取页面数据时出错: {e}")
        import traceback
        traceback.print_exc()
        return None


def click_next_page(driver):
    """
    查找并点击下一页按钮，返回是否成功
    """
    try:
        print("\n正在查找下一页按钮...")
        
        # 尝试多种方式查找下一页按钮
        next_page_selectors = [
            # 通过文本内容查找
            (By.XPATH, "//a[contains(text(), '下一页')]"),
            (By.XPATH, "//button[contains(text(), '下一页')]"),
            (By.XPATH, "//span[contains(text(), '下一页')]"),
            (By.XPATH, "//a[contains(text(), '>')]"),
            (By.XPATH, "//button[contains(text(), '>')]"),
            # 通过class查找
            (By.CSS_SELECTOR, "a.pagination-next"),
            (By.CSS_SELECTOR, "button.pagination-next"),
            (By.CSS_SELECTOR, ".pagination-next"),
            # 通过id查找
            (By.ID, "nextPage"),
            (By.ID, "next"),
            # 通过属性查找
            (By.XPATH, "//a[@title='下一页']"),
            (By.XPATH, "//button[@title='下一页']"),
        ]
        
        next_page_btn = None
        for by, selector in next_page_selectors:
            try:
                elements = driver.find_elements(by, selector)
                for element in elements:
                    # 检查元素是否可见且可点击
                    if element.is_displayed() and element.is_enabled():
                        # 检查是否被禁用（可能是最后一页）
                        if 'disabled' not in element.get_attribute('class') or 'disabled' not in element.get_attribute('class').lower():
                            next_page_btn = element
                            print(f"✓ 找到下一页按钮: {selector}")
                            break
                if next_page_btn:
                    break
            except Exception as e:
                continue
        
        # 如果没找到，尝试使用JavaScript查找并点击
        if not next_page_btn:
            print("使用JavaScript查找下一页按钮...")
            clicked = driver.execute_script("""
                // 查找包含"下一页"文本的元素
                var allElements = document.querySelectorAll('a, button, span, div');
                
                for (var i = 0; i < allElements.length; i++) {
                    var el = allElements[i];
                    var text = el.innerText || el.textContent || '';
                    var title = el.getAttribute('title') || '';
                    var className = el.className || '';
                    
                    // 检查是否包含"下一页"或">"
                    if ((text.includes('下一页') || text.includes('>') || title.includes('下一页')) 
                        && el.offsetParent !== null  // 元素可见
                        && !className.includes('disabled')) {
                        // 滚动到元素位置
                        el.scrollIntoView({block: 'center', behavior: 'auto'});
                        // 点击元素
                        el.click();
                        return {
                            success: true,
                            text: text.trim(),
                            tagName: el.tagName
                        };
                    }
                }
                
                return {success: false};
            """)
            
            if clicked and clicked.get('success'):
                print(f"✓ 已通过JavaScript点击下一页按钮: {clicked.get('tagName')} - {clicked.get('text', '')[:30]}")
                # 等待页面加载
                print("等待新页面数据加载...")
                time.sleep(3)
                
                # 等待表格数据更新
                for i in range(10):  # 最多等待10秒
                    time.sleep(1)
                    # 检查表格是否有数据
                    data_check = driver.execute_script("""
                        var table = document.querySelector('table#dt_1') || document.querySelector('table');
                        if (!table) return 0;
                        var tbody = table.querySelector('tbody') || table;
                        var trs = tbody.querySelectorAll('tr');
                        var dataRowCount = 0;
                        for (var i = 0; i < trs.length; i++) {
                            var tds = trs[i].querySelectorAll('td');
                            if (tds.length >= 2) {
                                var code = tds[1].innerText.trim();
                                if (code && /^\\d{6}$/.test(code)) {
                                    dataRowCount++;
                                }
                            }
                        }
                        return dataRowCount;
                    """)
                    if data_check > 0:
                        print(f"✓ 新页面数据已加载，共 {data_check} 行")
                        return True
                    if i % 2 == 0:
                        print(f"  等待中... ({i+1}秒)")
                
                print("警告：等待新页面数据超时，但继续执行...")
                return True
            else:
                print("✗ 未找到下一页按钮，可能已是最后一页")
                return False
        
        if next_page_btn:
            try:
                # 滚动到按钮位置
                driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'auto'});", next_page_btn)
                time.sleep(0.5)
                
                # 使用JavaScript点击，更可靠
                driver.execute_script("arguments[0].click();", next_page_btn)
                print("✓ 已点击下一页按钮")
                
                # 等待页面加载
                print("等待新页面数据加载...")
                time.sleep(3)
                
                # 等待表格数据更新
                for i in range(10):  # 最多等待10秒
                    time.sleep(1)
                    # 检查表格是否有数据
                    data_check = driver.execute_script("""
                        var table = document.querySelector('table#dt_1') || document.querySelector('table');
                        if (!table) return 0;
                        var tbody = table.querySelector('tbody') || table;
                        var trs = tbody.querySelectorAll('tr');
                        var dataRowCount = 0;
                        for (var i = 0; i < trs.length; i++) {
                            var tds = trs[i].querySelectorAll('td');
                            if (tds.length >= 2) {
                                var code = tds[1].innerText.trim();
                                if (code && /^\\d{6}$/.test(code)) {
                                    dataRowCount++;
                                }
                            }
                        }
                        return dataRowCount;
                    """)
                    if data_check > 0:
                        print(f"✓ 新页面数据已加载，共 {data_check} 行")
                        return True
                    if i % 2 == 0:
                        print(f"  等待中... ({i+1}秒)")
                
                print("警告：等待新页面数据超时，但继续执行...")
                return True
            except Exception as e:
                print(f"点击下一页按钮时出错: {e}")
                return False
        else:
            print("✗ 未找到下一页按钮，可能已是最后一页")
            return False
    except Exception as e:
        print(f"查找下一页按钮时出错: {e}")
        import traceback
        traceback.print_exc()
        return False


def get_capital_flow_selenium(min_amount_threshold=3000):
    """
    使用Selenium控制测试版Chrome获取个股主力净流入数据
    
    参数:
        min_amount_threshold: 最小净流入金额阈值（万元），默认3000万元
                             当某页最后一条数据的净额小于此值时，停止提取
    """
    chrome_options = Options()
    
    # --- 1. 指定你的测试版Chrome浏览器路径 ---
    chrome_options.binary_location = r"D:\download\chrome-win64\chrome-win64\chrome.exe"
    
    # --- 2. 指定你的ChromeDriver路径 ---
    driver_path = r"D:\download\chromedriver-win64\chromedriver.exe"
    service = Service(executable_path=driver_path)
    
    # 启动浏览器
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        # 打开东方财富网个股主力净流入页面
        url = "https://data.eastmoney.com/zjlx/detail.html"
        driver.get(url)
        print(f"成功打开页面: {url}")

        wait = WebDriverWait(driver, 30)

        # 打开网页后直接等待3秒
        print("等待页面加载...")
        time.sleep(3)
        
        # 尝试关闭可能的弹窗
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
            print("已尝试关闭可能的弹窗")
            time.sleep(0.5)
            
            # 使用driver.refresh()刷新页面，更可靠
            print("刷新页面...")
            try:
                driver.refresh()
                print("页面刷新完成，等待页面加载...")
                
                # 等待页面加载完成（检查document.readyState）
                wait_for_page_load = WebDriverWait(driver, 15)
                wait_for_page_load.until(lambda d: d.execute_script('return document.readyState') == 'complete')
                print("页面DOM加载完成")
                
                # 额外等待一下，确保动态内容加载
                time.sleep(2)
                
                # 检查是否有loading遮罩层，如果有则等待其消失
                try:
                    loading_selectors = [
                        'div[class*="loading"]',
                        'div[class*="Loading"]',
                        'div[id*="loading"]',
                        'div[id*="Loading"]',
                        '.ant-spin-spinning',  # Ant Design的loading
                        '.el-loading-mask',    # Element UI的loading
                    ]
                    
                    loading_found = False
                    for selector in loading_selectors:
                        try:
                            loading_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                            # 检查是否可见
                            visible_loadings = [el for el in loading_elements if el.is_displayed()]
                            if visible_loadings:
                                loading_found = True
                                print(f"检测到loading元素: {selector}，等待其消失...")
                                break
                        except:
                            continue
                    
                    if loading_found:
                        # 等待loading消失（最多等待10秒）
                        loading_timeout = False
                        for i in range(20):  # 每0.5秒检查一次，共10秒
                            still_loading = False
                            for selector in loading_selectors:
                                try:
                                    loading_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                                    visible_loadings = [el for el in loading_elements if el.is_displayed()]
                                    if visible_loadings:
                                        still_loading = True
                                        break
                                except:
                                    continue
                            
                            if not still_loading:
                                print("Loading已消失")
                                break
                            time.sleep(0.5)
                        else:
                            loading_timeout = True
                            print("警告：等待loading超时（10秒），尝试重新导航...")
                            
                            # 如果loading超时，尝试重新导航到URL
                            try:
                                current_url = driver.current_url
                                print(f"重新导航到: {current_url}")
                                driver.get(current_url)
                                print("重新导航完成，等待页面加载...")
                                
                                # 再次等待页面加载
                                wait_for_page_load2 = WebDriverWait(driver, 15)
                                wait_for_page_load2.until(lambda d: d.execute_script('return document.readyState') == 'complete')
                                print("重新导航后页面DOM加载完成")
                                time.sleep(2)
                                
                                # 再次检查loading
                                still_loading_after_reload = False
                                for selector in loading_selectors:
                                    try:
                                        loading_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                                        visible_loadings = [el for el in loading_elements if el.is_displayed()]
                                        if visible_loadings:
                                            still_loading_after_reload = True
                                            break
                                    except:
                                        continue
                                
                                if still_loading_after_reload:
                                    print("警告：重新导航后仍有loading，继续执行...")
                                else:
                                    print("重新导航后loading已消失")
                            except Exception as reload_error:
                                print(f"重新导航时出错: {reload_error}，继续执行...")
                except Exception as e:
                    print(f"检查loading状态时出错（可忽略）: {e}")
                    
            except Exception as e:
                print(f"刷新页面时出错: {e}")
                # 如果刷新失败，至少等待一下
                time.sleep(3)
        except Exception as e:
            print(f"关闭弹窗时出错: {e}")

        # 快速检查表格是否已经存在（不等待）
        print("检查表格是否已加载...")
        table_found = False
        table_element = None
        
        # 先快速尝试查找表格（不等待）
        for selector in [
            (By.ID, 'dt_1'),
            (By.XPATH, '//table[@id="dt_1"]'),
            (By.XPATH, '//table[contains(@class, "dataview")]'),
            (By.XPATH, '//table')
        ]:
            try:
                table_element = driver.find_element(*selector)
                print(f"✓ 表格已存在: {selector}")
                table_found = True
                break
            except:
                continue
        
        # 如果表格不存在，才使用显式等待（但缩短超时时间）
        if not table_found:
            print("表格未找到，等待表格加载（最多3秒）...")
            try:
                for selector in [
                    (By.ID, 'dt_1'),
                    (By.XPATH, '//table[@id="dt_1"]'),
                    (By.XPATH, '//table[contains(@class, "dataview")]'),
                    (By.XPATH, '//table')
                ]:
                    try:
                        short_wait = WebDriverWait(driver, 3)  # 缩短到3秒
                        table_element = short_wait.until(EC.presence_of_element_located(selector))
                        print(f"✓ 表格已加载: {selector}")
                        table_found = True
                        break
                    except TimeoutException:
                        continue
                
                if not table_found:
                    print("警告：未检测到表格元素，继续尝试...")
            except Exception as e:
                print(f"等待表格时出错: {e}")
        
        # 如果表格存在，快速检查是否有数据行
        if table_found:
            print("检查表格数据是否已加载...")
            try:
                # 使用JavaScript快速检查数据行
                data_check = driver.execute_script("""
                    var table = arguments[0];
                    var tbody = table.querySelector('tbody');
                    var trContainer = tbody || table;
                    var trs = trContainer.querySelectorAll('tr');
                    var dataRowCount = 0;
                    
                    for (var i = 0; i < trs.length; i++) {
                        var tr = trs[i];
                        var tds = tr.querySelectorAll('td');
                        if (tds.length >= 2) {
                            var code = tds[1].innerText.trim();
                            if (code && /^\\d{6}$/.test(code)) {
                                dataRowCount++;
                            }
                        }
                    }
                    
                    return dataRowCount;
                """, table_element)
                
                if data_check and data_check > 0:
                    print(f"✓ 表格数据已存在，共 {data_check} 行数据，直接提取...")
                    # 数据已存在，跳过所有等待，直接进入提取阶段
                else:
                    print(f"表格存在但数据未加载，等待数据出现（最多5秒）...")
                    # 数据未加载，短时间等待
                    for i in range(5):
                        time.sleep(1)
                        data_check = driver.execute_script("""
                            var table = arguments[0];
                            var tbody = table.querySelector('tbody');
                            var trContainer = tbody || table;
                            var trs = trContainer.querySelectorAll('tr');
                            var dataRowCount = 0;
                            
                            for (var i = 0; i < trs.length; i++) {
                                var tr = trs[i];
                                var tds = tr.querySelectorAll('td');
                                if (tds.length >= 2) {
                                    var code = tds[1].innerText.trim();
                                    if (code && /^\\d{6}$/.test(code)) {
                                        dataRowCount++;
                                    }
                                }
                            }
                            
                            return dataRowCount;
                        """, table_element)
                        
                        if data_check and data_check > 0:
                            print(f"✓ 数据已加载，共 {data_check} 行")
                            break
            except Exception as e:
                print(f"检查数据时出错: {e}，继续执行...")
        
        # 滚动到表格位置（如果需要）
        if table_found:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", table_element)
                time.sleep(0.5)  # 减少等待时间
            except:
                pass

        # 循环提取多页数据（根据最后一条数据的净额决定是否继续）
        all_dataframes = []
        page_num = 0
        
        while True:
            page_num += 1
            print(f"\n{'='*60}")
            print(f"开始提取第 {page_num} 页数据...")
            print(f"{'='*60}")
            
            # 提取当前页数据
            df_page = extract_page_data(driver)
            
            if df_page is not None and not df_page.empty:
                print(f"✓ 第 {page_num} 页提取成功，共 {len(df_page)} 行数据")
                all_dataframes.append(df_page)
                
                # 检查最后一条数据的"今日主力净流入-净额"
                last_row = df_page.iloc[-1]
                
                # 查找"今日主力净流入-净额"列（按优先级查找）
                amount_col = None
                
                # 优先级1：查找包含"今日主力净流入"和"净额"的列
                for col in df_page.columns:
                    if '今日主力净流入' in str(col) and '净额' in str(col):
                        amount_col = col
                        print(f"  找到列（优先级1）: {col}")
                        break
                
                # 优先级2：查找包含"今日主力净"的列（可能是"今日主力净"）
                if amount_col is None:
                    for col in df_page.columns:
                        if '今日主力净' in str(col):
                            amount_col = col
                            print(f"  找到列（优先级2）: {col}")
                            break
                
                # 优先级3：查找包含"净额"的列（作为备用）
                if amount_col is None:
                    print("⚠ 警告：未找到'今日主力净流入-净额'或'今日主力净'列，尝试查找包含'净额'的列...")
                    for col in df_page.columns:
                        if '净额' in str(col):
                            amount_col = col
                            print(f"  找到列（优先级3）: {col}")
                            break
                
                if amount_col:
                    # 确保获取标量值而不是 Series
                    try:
                        # 使用 .at 方法获取标量值（最快且最安全）
                        last_row_idx = df_page.index[-1]
                        last_amount_str = df_page.at[last_row_idx, amount_col]
                    except (KeyError, IndexError, AttributeError):
                        try:
                            # 备用方法：直接通过列索引获取
                            last_amount_str = df_page[amount_col].iloc[-1]
                        except (KeyError, IndexError):
                            print(f"⚠ 警告：无法获取列 '{amount_col}' 的值")
                            last_amount_str = None
                    
                    last_amount = parse_capital_flow_amount(last_amount_str)
                    
                    if last_amount is not None:
                        print(f"\n第 {page_num} 页最后一条数据:")
                        # 尝试查找代码和名称列
                        code_col = None
                        name_col = None
                        for col in df_page.columns:
                            if '代码' in str(col) and code_col is None:
                                code_col = col
                            if '名称' in str(col) and name_col is None:
                                name_col = col
                        
                        if code_col:
                            print(f"  股票代码: {last_row.get(code_col, 'N/A')}")
                        if name_col:
                            print(f"  股票名称: {last_row.get(name_col, 'N/A')}")
                        print(f"  今日主力净流入-净额: {last_amount_str} (解析为: {last_amount:.2f} 万元)")
                        
                        if last_amount >= min_amount_threshold:
                            print(f"  ✓ 净额 ({last_amount:.2f} 万元) >= 阈值 ({min_amount_threshold} 万元)，继续获取下一页...")
                            # 尝试点击下一页
                            if not click_next_page(driver):
                                print(f"无法继续到下一页，停止提取（已提取 {page_num} 页）")
                                break
                        else:
                            print(f"  ✗ 净额 ({last_amount:.2f} 万元) < 阈值 ({min_amount_threshold} 万元)，停止提取")
                            break
                    else:
                        print(f"⚠ 警告：无法解析最后一条数据的净额值: {last_amount_str}")
                        print("  继续尝试获取下一页...")
                        # 如果无法解析，继续尝试下一页（可能是数据格式问题）
                        if not click_next_page(driver):
                            print(f"无法继续到下一页，停止提取（已提取 {page_num} 页）")
                            break
                else:
                    print("⚠ 警告：未找到'今日主力净流入-净额'列，显示最后一条数据的所有列:")
                    print(f"  列名: {list(df_page.columns)}")
                    print(f"  最后一行数据: {dict(last_row)}")
                    print("  继续尝试获取下一页...")
                    # 如果找不到列，继续尝试下一页
                    if not click_next_page(driver):
                        print(f"无法继续到下一页，停止提取（已提取 {page_num} 页）")
                        break
            else:
                print(f"✗ 第 {page_num} 页提取失败或数据为空")
                if page_num == 1:
                    # 第一页就失败，直接返回
                    print("第一页提取失败，无法继续")
                    break
                else:
                    # 后续页失败，停止提取
                    print(f"第 {page_num} 页提取失败，停止提取（已提取 {page_num - 1} 页）")
                    break
        
        # 合并所有页的数据
        if all_dataframes:
            print(f"\n{'='*60}")
            print(f"合并 {len(all_dataframes)} 页数据...")
            df_combined = pd.concat(all_dataframes, ignore_index=True)
            df_combined = clean_dataframe(df_combined)
            print(f"合并完成，共 {len(df_combined)} 行数据")
            return df_combined
        else:
            print("\n未能提取到任何数据")
            return None

        # 以下代码已不再使用，保留作为备用
        # 首先尝试使用JavaScript直接获取表格数据
        print("提取表格数据...")
        try:
            # 先调试：检查页面中所有表格的信息
            print("调试：检查页面中的表格...")
            table_debug = driver.execute_script("""
                var tables = document.querySelectorAll('table');
                var tableInfo = [];
                for (var i = 0; i < tables.length; i++) {
                    var table = tables[i];
                    var tbody = table.querySelector('tbody');
                    var trs = (tbody || table).querySelectorAll('tr');
                    var dataRows = 0;
                    for (var j = 0; j < trs.length; j++) {
                        var tds = trs[j].querySelectorAll('td');
                        if (tds.length >= 2) {
                            var code = tds[1].innerText.trim();
                            if (code && /^\\d{6}$/.test(code)) {
                                dataRows++;
                            }
                        }
                    }
                    tableInfo.push({
                        id: table.id || '',
                        className: table.className || '',
                        trCount: trs.length,
                        dataRows: dataRows,
                        hasTbody: !!tbody,
                        firstRowText: trs.length > 0 ? trs[0].innerText.substring(0, 50) : ''
                    });
                }
                return {
                    tableCount: tables.length,
                    tables: tableInfo
                };
            """)
            print(f"页面中共有 {table_debug.get('tableCount', 0)} 个表格")
            for i, table_info in enumerate(table_debug.get('tables', [])):
                print(f"  表格{i+1}: id={table_info.get('id', '无')}, "
                      f"class={table_info.get('className', '无')[:50]}, "
                      f"TR数={table_info.get('trCount', 0)}, "
                      f"数据行数={table_info.get('dataRows', 0)}")
            
            # 滚动到表格位置，确保表格在视口中（便于Selenium访问）
            # 注意：此页面数据已全部显示，滚动不会触发数据加载
            try:
                scroll_result = driver.execute_script("""
                    var table = document.querySelector('table#dt_1') || document.querySelector('table');
                    if (table) {
                        // 只滚动到表格位置，确保在视口中
                        table.scrollIntoView({block: 'center', behavior: 'auto'});
                        return {found: true};
                    }
                    return {found: false};
                """)
                print(f"滚动到表格位置: {scroll_result}")
                time.sleep(1)  # 短暂等待，确保滚动完成
            except Exception as e:
                print(f"滚动时出错: {e}")
            
            # 等待DOM稳定（数据已全部显示，只需等待渲染完成）
            print("等待DOM稳定...")
            time.sleep(2)  # 减少等待时间，因为数据已全部显示
            
            # 直接提取数据（如果数据已存在，上面的检查已经确认了）
            # 如果数据不存在，这里也会尝试提取（可能数据在检查后加载了）
            js_data = driver.execute_script("""
                // 尝试多种方式找到表格（优先找有数据的表格）
                var allTables = document.querySelectorAll('table');
                var table = null;
                var maxDataRows = 0;
                
                // 先找id为dt_1的表格
                table = document.querySelector('table#dt_1');
                if (table) {
                    console.log('找到表格: table#dt_1');
                } else {
                    // 遍历所有表格，找数据最多的
                    for (var i = 0; i < allTables.length; i++) {
                        var t = allTables[i];
                        var tbody = t.querySelector('tbody') || t;
                        var trs = tbody.querySelectorAll('tr');
                        var dataCount = 0;
                        for (var j = 0; j < trs.length; j++) {
                            var tds = trs[j].querySelectorAll('td');
                            if (tds.length >= 2) {
                                var code = tds[1].innerText.trim();
                                if (code && /^\\d{6}$/.test(code)) {
                                    dataCount++;
                                }
                            }
                        }
                        if (dataCount > maxDataRows) {
                            maxDataRows = dataCount;
                            table = t;
                        }
                    }
                    if (table) {
                        console.log('找到数据最多的表格，数据行数:', maxDataRows);
                    }
                }
                
                // 如果还是没找到，就用第一个表格
                if (!table && allTables.length > 0) {
                    table = allTables[0];
                    console.log('使用第一个表格');
                }
                
                if (!table) {
                    return {error: '未找到表格', tableCount: allTables.length};
                }
                
                var rows = [];
                var headers = [];
                var debugInfo = {
                    tableFound: true,
                    hasThead: false,
                    hasTbody: false,
                    trCount: 0,
                    tbodyTrCount: 0,
                    theadTrCount: 0
                };
                
                // 获取表头
                var thead = table.querySelector('thead');
                if (thead) {
                    debugInfo.hasThead = true;
                    var headerRows = thead.querySelectorAll('tr');
                    debugInfo.theadTrCount = headerRows.length;
                    for (var i = 0; i < headerRows.length; i++) {
                        var ths = headerRows[i].querySelectorAll('th');
                        var headerRow = [];
                        for (var j = 0; j < ths.length; j++) {
                            headerRow.push(ths[j].innerText.trim());
                        }
                        if (headerRow.length > 0) {
                            headers = headers.concat(headerRow);
                        }
                    }
                }
                
                // 获取数据行 - 尝试多种方式
                var tbody = table.querySelector('tbody');
                var trContainer = tbody || table;
                
                if (tbody) {
                    debugInfo.hasTbody = true;
                }
                
                // 获取所有tr元素
                var trs = trContainer.querySelectorAll('tr');
                debugInfo.trCount = trs.length;
                if (tbody) {
                    debugInfo.tbodyTrCount = tbody.querySelectorAll('tr').length;
                }
                
                console.log('调试信息:', debugInfo);
                console.log('表头数量:', headers.length);
                console.log('TR总数:', trs.length);
                
                // 遍历所有tr，包括表头行（用于调试）
                for (var i = 0; i < trs.length; i++) {
                    var tr = trs[i];
                    var rowText = tr.innerText.trim();
                    
                    // 检查是否是表头行（在tbody中也可能有表头）
                    var isHeaderRow = false;
                    if (tr.querySelectorAll('th').length > 0) {
                        isHeaderRow = true;
                    }
                    if (rowText.includes('序号') && rowText.includes('代码') && rowText.includes('名称')) {
                        isHeaderRow = true;
                    }
                    
                    // 跳过表头行
                    if (isHeaderRow) {
                        continue;
                    }
                    
                    // 跳过空行
                    if (!rowText || rowText.length < 10) {
                        continue;
                    }
                    
                    var tds = tr.querySelectorAll('td');
                    if (tds.length > 0) {
                        var row = [];
                        for (var j = 0; j < tds.length; j++) {
                            var cellText = tds[j].innerText.trim();
                            row.push(cellText);
                        }
                        
                        // 检查是否是数据行（至少包含股票代码）
                        if (row.length >= 2) {
                            // 尝试在第二列找代码，如果第二列不是代码，尝试其他列
                            var foundCode = false;
                            for (var k = 0; k < row.length; k++) {
                                if (row[k] && /^\\d{6}$/.test(row[k])) {
                                    foundCode = true;
                                    break;
                                }
                            }
                            
                            if (foundCode) {
                                rows.push(row);
                            } else {
                                // 即使没有找到代码，如果行有足够的数据，也尝试添加（可能是数据格式不同）
                                if (row.length >= 5 && row.some(function(cell) { return cell && cell.length > 0; })) {
                                    console.log('可疑数据行（无代码）:', row.slice(0, 5));
                                }
                            }
                        }
                    }
                }
                
                console.log('提取到的数据行数:', rows.length);
                if (rows.length > 0) {
                    console.log('第一行数据示例:', rows[0]);
                }
                
                return {
                    headers: headers,
                    rows: rows,
                    rowCount: rows.length,
                    debugInfo: debugInfo
                };
            """)
            
            # 打印调试信息
            print(f"\n=== 数据提取结果 ===")
            if js_data:
                print(f"返回数据: {type(js_data)}, 包含键: {list(js_data.keys()) if isinstance(js_data, dict) else 'N/A'}")
                
            if js_data and 'debugInfo' in js_data:
                debug = js_data['debugInfo']
                print(f"调试信息: 表格找到={debug.get('tableFound')}, "
                      f"有thead={debug.get('hasThead')}, "
                      f"有tbody={debug.get('hasTbody')}, "
                      f"TR总数={debug.get('trCount')}, "
                      f"tbody中TR数={debug.get('tbodyTrCount')}")
            
            if js_data and 'error' in js_data:
                print(f"JavaScript错误: {js_data['error']}, 页面中表格数量: {js_data.get('tableCount', 0)}")
            
            if js_data and js_data.get('rowCount', 0) > 0:
                print(f"✓ 提取到 {js_data.get('rowCount', 0)} 行数据")
                if js_data.get('rows') and len(js_data['rows']) > 0:
                    print(f"第一行数据示例: {js_data['rows'][0][:5]}")  # 只显示前5列
            elif js_data and js_data.get('rowCount', 0) == 0:
                print(f"⚠ 警告: 提取到0行数据")
                # 再次检查页面中的表格
                recheck = driver.execute_script("""
                    var table = document.querySelector('table#dt_1') || document.querySelector('table');
                    if (!table) return {found: false};
                    var tbody = table.querySelector('tbody') || table;
                    var trs = tbody.querySelectorAll('tr');
                    var sampleRows = [];
                    for (var i = 0; i < Math.min(3, trs.length); i++) {
                        var tds = trs[i].querySelectorAll('td, th');
                        var row = [];
                        for (var j = 0; j < Math.min(5, tds.length); j++) {
                            row.push(tds[j].innerText.trim());
                        }
                        sampleRows.push(row);
                    }
                    return {
                        found: true,
                        trCount: trs.length,
                        sampleRows: sampleRows
                    };
                """)
                print(f"重新检查: {recheck}")
            
            if js_data and js_data.get('rows') and len(js_data['rows']) > 0:
                print(f"✓ 使用JavaScript成功获取 {len(js_data['rows'])} 行数据")
                headers = js_data.get('headers', [])
                rows = js_data.get('rows', [])
                
                # 如果表头为空，使用默认表头
                if not headers or len(headers) < 5:
                    headers = ['序号', '代码', '名称', '相关', '最新价', '今日涨跌幅', 
                              '今日主力净流入-净额', '今日主力净流入-净占比',
                              '今日超大单净流入-净额', '今日超大单净流入-净占比',
                              '今日大单净流入-净额', '今日大单净流入-净占比',
                              '今日中单净流入-净额', '今日中单净流入-净占比',
                              '今日小单净流入-净额', '今日小单净流入-净占比']
                
                # 确保列数匹配
                max_cols = max(len(row) for row in rows) if rows else 0
                if len(headers) != max_cols:
                    if len(headers) < max_cols:
                        headers.extend([f'列{i+1}' for i in range(len(headers), max_cols)])
                    else:
                        headers = headers[:max_cols]
                
                df = pd.DataFrame(rows, columns=headers[:max_cols])
                print(f"成功提取 {len(df)} 行数据")
                df = clean_dataframe(df)
                print(f"清理后剩余 {len(df)} 行数据")
                
                if not df.empty:
                    return df
                else:
                    print("JavaScript获取的数据为空，继续使用Selenium方式...")
            elif js_data and js_data.get('rowCount', 0) == 0:
                # 如果获取到表格但数据行为0，尝试等待并重试
                # 注意：数据已全部显示，滚动不会触发加载，只需等待DOM稳定后重试
                print("表格存在但数据行为0，尝试等待DOM稳定后重试...")
                for retry in range(3):
                    time.sleep(1)
                    print(f"  重试 {retry + 1}/3...")
                    
                    # 重新尝试获取数据
                    js_data_retry = driver.execute_script("""
                        var table = document.querySelector('table#dt_1') || document.querySelector('table');
                        if (!table) return null;
                        
                        var tbody = table.querySelector('tbody') || table;
                        var trs = tbody.querySelectorAll('tr');
                        var rows = [];
                        
                        for (var i = 0; i < trs.length; i++) {
                            var tr = trs[i];
                            var tds = tr.querySelectorAll('td');
                            if (tds.length >= 2) {
                                var row = [];
                                for (var j = 0; j < tds.length; j++) {
                                    row.push(tds[j].innerText.trim());
                                }
                                // 检查是否有股票代码
                                var hasCode = false;
                                for (var k = 0; k < row.length; k++) {
                                    if (row[k] && /^\\d{6}$/.test(row[k])) {
                                        hasCode = true;
                                        break;
                                    }
                                }
                                if (hasCode) {
                                    rows.push(row);
                                }
                            }
                        }
                        return {rows: rows, rowCount: rows.length};
                    """)
                    
                    if js_data_retry and js_data_retry.get('rowCount', 0) > 0:
                        print(f"✓ 重试成功，获取到 {js_data_retry['rowCount']} 行数据")
                        rows = js_data_retry.get('rows', [])
                        headers = js_data.get('headers', [])
                        if not headers or len(headers) < 5:
                            headers = ['序号', '代码', '名称', '相关', '最新价', '今日涨跌幅', 
                                      '今日主力净流入-净额', '今日主力净流入-净占比',
                                      '今日超大单净流入-净额', '今日超大单净流入-净占比',
                                      '今日大单净流入-净额', '今日大单净流入-净占比',
                                      '今日中单净流入-净额', '今日中单净流入-净占比',
                                      '今日小单净流入-净额', '今日小单净流入-净占比']
                        max_cols = max(len(row) for row in rows) if rows else 0
                        if len(headers) != max_cols:
                            if len(headers) < max_cols:
                                headers.extend([f'列{i+1}' for i in range(len(headers), max_cols)])
                            else:
                                headers = headers[:max_cols]
                        df = pd.DataFrame(rows, columns=headers[:max_cols])
                        df = clean_dataframe(df)
                        if not df.empty:
                            return df
                print("重试后仍未获取到数据，继续使用Selenium方式...")
            else:
                print("JavaScript未获取到数据，继续使用Selenium方式...")
        except Exception as e:
            print(f"使用JavaScript获取数据时出错: {e}")
            import traceback
            traceback.print_exc()
            print("继续使用Selenium方式...")
        
        # 提取数据，使用多种方式尝试
        data_container = None
        container_type = None
        
        # 尝试多种方式查找数据容器
        selectors = [
            (By.ID, 'dt_1', 'table_id'),
            (By.XPATH, '//table[@id="dt_1"]', 'table_xpath'),
            (By.XPATH, '//table[contains(@class, "dataview")]', 'table_class'),
            (By.XPATH, '//table[contains(@class, "table")]', 'table_class2'),
            (By.XPATH, '//table', 'table_first'),
            (By.XPATH, '//div[@id="dt_1"]//table', 'div_table'),
            (By.XPATH, '//div[contains(@class, "dataview")]//table', 'div_dataview_table')
        ]
        
        print("正在查找数据表格...")
        container_by = None
        container_selector_str = None
        for by, selector, ctype in selectors:
            try:
                elements = driver.find_elements(by, selector)
                if elements:
                    data_container = elements[0]
                    container_type = ctype
                    container_by = by
                    container_selector_str = selector
                    print(f"✓ 找到数据容器: {selector} ({ctype})")
                    # 打印表格的一些信息用于调试
                    try:
                        print(f"  表格可见性: {data_container.is_displayed()}")
                        print(f"  表格文本长度: {len(data_container.text)}")
                        print(f"  表格文本前200字符: {data_container.text[:200]}")
                    except:
                        pass
                    break
            except NoSuchElementException:
                print(f"  ✗ 未找到: {selector}")
                continue
            except Exception as e:
                print(f"  ✗ 查找 {selector} 时出错: {e}")
                continue
        
        if data_container is None:
            print("\n错误：无法找到数据容器")
            print("当前页面URL:", driver.current_url)
            print("页面标题:", driver.title)
            
            # 尝试查找所有表格用于调试
            try:
                all_tables = driver.find_elements(By.XPATH, '//table')
                print(f"\n页面中共找到 {len(all_tables)} 个table元素")
                for i, table in enumerate(all_tables[:5]):  # 只显示前5个
                    try:
                        table_id = table.get_attribute('id')
                        table_class = table.get_attribute('class')
                        print(f"  Table {i+1}: id={table_id}, class={table_class}, visible={table.is_displayed()}")
                    except:
                        pass
            except:
                pass
            
            # 保存页面截图用于调试
            try:
                driver.save_screenshot("debug_screenshot.png")
                print("已保存页面截图: debug_screenshot.png")
            except:
                pass
            
            # 打印页面HTML的一部分用于调试
            try:
                page_source = driver.page_source
                # 查找包含"dt_1"的部分
                if 'dt_1' in page_source:
                    idx = page_source.find('dt_1')
                    start = max(0, idx - 500)
                    end = min(len(page_source), idx + 1000)
                    print(f"\n页面源码中包含'dt_1'的部分 (位置 {idx}):")
                    print(page_source[start:end])
                else:
                    print("\n页面源码中未找到'dt_1'")
                    # 查找table标签
                    if '<table' in page_source:
                        idx = page_source.find('<table')
                        start = max(0, idx - 200)
                        end = min(len(page_source), idx + 1000)
                        print(f"\n页面源码中第一个table标签附近的内容:")
                        print(page_source[start:end])
            except Exception as e:
                print(f"打印页面源码时出错: {e}")
            
            return None

        # 根据容器类型提取数据
        headers = []
        rows = []
        
        # 如果是表格元素
        if container_type in ['table_id', 'table_xpath', 'table_class', 'table_first']:
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
                # 如果无法获取表头，使用默认列名（根据东方财富主力净流入页面的列）
                headers = ['序号', '代码', '名称', '最新价', '今日涨跌幅', '今日主力净流入', '今日超大单净流入', 
                          '今日大单净流入', '今日中单净流入', '今日小单净流入']
            
            print(f"表头: {headers}")

            # 提取数据行 - 使用显式等待确保数据行加载完成
            try:
                # 等待数据行出现（至少有一个包含股票代码的tr）
                print("等待数据行加载...")
                print("提示：如果数据加载较慢，请耐心等待（最多等待15秒）...")
                
                # 使用较短的超时时间，避免长时间卡住
                short_wait = WebDriverWait(driver, 15)
                
                try:
                    # 使用自定义等待条件：检查是否有包含6位数字代码的tr
                    # 添加进度提示
                    start_time = time.time()
                    last_print_time = start_time
                    
                    def has_data_rows(driver):
                        nonlocal last_print_time
                        current_time = time.time()
                        # 每3秒打印一次进度
                        if current_time - last_print_time >= 3:
                            elapsed = int(current_time - start_time)
                            print(f"  等待中... ({elapsed}秒)")
                            last_print_time = current_time
                        
                        # 每次重新查找容器，避免 stale element 问题
                        try:
                            if container_by and container_selector_str:
                                elements = driver.find_elements(container_by, container_selector_str)
                                if not elements:
                                    return False
                                current_container = elements[0]
                            else:
                                # 回退到使用原始容器（如果选择器不可用）
                                current_container = data_container
                            
                            # 尝试查找 tbody，如果没有则直接查找 tr
                            try:
                                tbody = current_container.find_element(By.TAG_NAME, 'tbody')
                                trs = tbody.find_elements(By.TAG_NAME, 'tr')
                            except:
                                trs = current_container.find_elements(By.TAG_NAME, 'tr')
                            
                            # 检查是否有足够的tr元素（至少2个，包括表头）
                            if len(trs) < 2:
                                return False
                            
                            # 检查是否有包含数据的tr
                            for tr in trs:
                                try:
                                    tds = tr.find_elements(By.TAG_NAME, 'td')
                                    if len(tds) >= 2:
                                        code = tds[1].text.strip()
                                        if code and len(code) == 6 and code.isdigit():
                                            elapsed = int(time.time() - start_time)
                                            print(f"数据行已加载 (耗时{elapsed}秒)")
                                            return True
                                except:
                                    continue
                            return False
                        except StaleElementReferenceException:
                            # 如果元素过时，返回 False 让等待重试
                            return False
                        except Exception as e:
                            # 其他异常也返回 False
                            return False
                    
                    short_wait.until(lambda d: has_data_rows(d))
                except TimeoutException:
                    elapsed = int(time.time() - start_time)
                    print(f"警告：等待数据行超时（{elapsed}秒），继续尝试提取现有数据...")
                
                # 重新查找容器，避免 stale element 问题
                try:
                    if container_by and container_selector_str:
                        elements = driver.find_elements(container_by, container_selector_str)
                        if elements:
                            data_container = elements[0]
                except:
                    pass
                
                # 先尝试查找tbody，如果没有则直接查找tr
                tbody = None
                try:
                    tbody = data_container.find_element(By.TAG_NAME, 'tbody')
                    print("找到tbody元素")
                except:
                    print("未找到tbody元素，直接查找tr")
                
                tr_container = tbody if tbody else data_container
                tr_elements = tr_container.find_elements(By.TAG_NAME, 'tr')
                print(f"找到 {len(tr_elements)} 个tr元素")
                
                # 使用JavaScript获取更详细的表格信息
                try:
                    table_info = driver.execute_script("""
                        var table = arguments[0];
                        var info = {
                            trCount: 0,
                            tbodyTrCount: 0,
                            theadTrCount: 0,
                            dataRows: []
                        };
                        
                        var tbody = table.querySelector('tbody');
                        var thead = table.querySelector('thead');
                        
                        if (thead) {
                            info.theadTrCount = thead.querySelectorAll('tr').length;
                        }
                        if (tbody) {
                            var tbodyTrs = tbody.querySelectorAll('tr');
                            info.tbodyTrCount = tbodyTrs.length;
                            // 检查前5行的内容
                            for (var i = 0; i < Math.min(5, tbodyTrs.length); i++) {
                                var tr = tbodyTrs[i];
                                var tds = tr.querySelectorAll('td');
                                var rowData = [];
                                for (var j = 0; j < Math.min(5, tds.length); j++) {
                                    rowData.push(tds[j].innerText.trim());
                                }
                                info.dataRows.push(rowData);
                            }
                        }
                        
                        info.trCount = table.querySelectorAll('tr').length;
                        return info;
                    """, data_container)
                    
                    if table_info:
                        print(f"表格详细信息: tbody中TR数={table_info.get('tbodyTrCount')}, "
                              f"thead中TR数={table_info.get('theadTrCount')}, "
                              f"总TR数={table_info.get('trCount')}")
                        if table_info.get('dataRows'):
                            print("tbody前5行数据示例:")
                            for idx, row in enumerate(table_info['dataRows']):
                                print(f"  行{idx}: {row}")
                except Exception as e:
                    print(f"获取表格详细信息时出错: {e}")
                
                # 打印前几个tr的文本用于调试
                for i, tr in enumerate(tr_elements[:15]):  # 显示前15个
                    try:
                        tr_text = tr.text[:150] if tr.text else '(空)'
                        # 检查是否包含股票代码
                        has_code = False
                        code_text = ''
                        try:
                            tds = tr.find_elements(By.TAG_NAME, 'td')
                            if len(tds) >= 2:
                                code_text = tds[1].text.strip()
                                if code_text and len(code_text) == 6 and code_text.isdigit():
                                    has_code = True
                        except:
                            pass
                        print(f"  tr[{i}] 文本: {tr_text[:100]} {'[有代码:'+code_text+']' if has_code else ''}")
                    except Exception as e:
                        print(f"  tr[{i}] 读取错误: {e}")
                
                data_row_count = 0
                header_found = False
                for i, tr in enumerate(tr_elements):
                    try:
                        row_text = tr.text.strip()
                        
                        # 先检查是否是th元素（表头）
                        th_elements = tr.find_elements(By.TAG_NAME, 'th')
                        if th_elements:
                            th_cells = [th.text.strip() for th in th_elements]
                            if th_cells and not header_found:
                                print(f"第{i}行是表头 (th元素): {th_cells[:5]}")
                                if not headers or len(headers) < len(th_cells):
                                    headers = th_cells
                                    header_found = True
                            continue
                        
                        # 跳过包含"点击加载更多"的行
                        if '点击加载更多' in row_text:
                            continue
                        # 跳过包含"无更多数据"的行
                        if '无更多数据' in row_text:
                            continue
                        
                        cells = [td.text.strip() for td in tr.find_elements(By.TAG_NAME, 'td')]
                        
                        # 跳过完全空白的行
                        if not cells or len(cells) < 2:
                            continue
                        
                        # 跳过所有单元格都为空或只包含空白字符的行
                        if all(not cell or cell.strip() == '' for cell in cells):
                            continue
                        
                        # 检查是否是表头行（更宽松的判断）
                        is_header = False
                        # 如果第一行且包含表头关键词，可能是表头
                        if i == 0 and ('序号' in row_text or ('代码' in row_text and '名称' in row_text)):
                            is_header = True
                        # 如果包含多个表头关键词，可能是表头
                        if ('净额' in row_text and '净占比' in row_text) and not any(cell and cell.replace('.', '').replace('-', '').isdigit() for cell in cells[2:] if cell):
                            is_header = True
                        
                        if is_header:
                            print(f"第{i}行判断为表头: {cells[:5]}")
                            if not headers or len(headers) < len(cells):
                                headers = cells
                            continue
                        
                        # 验证是否是有效的数据行
                        # 方法1: 检查第二列是否是6位股票代码
                        has_valid_code = False
                        code_col = -1
                        if len(cells) >= 2:
                            for col_idx in range(min(3, len(cells))):  # 检查前3列
                                code = cells[col_idx].strip()
                                if code and len(code) == 6 and code.isdigit():
                                    has_valid_code = True
                                    code_col = col_idx
                                    break
                        
                        # 方法2: 如果找不到代码，但行有足够的数据列（至少5列），也可能是数据行
                        has_enough_data = len(cells) >= 5 and any(cell and cell.replace('.', '').replace('-', '').replace(',', '').isdigit() for cell in cells[2:] if cell)
                        
                        if has_valid_code or has_enough_data:
                            rows.append(cells)
                            data_row_count += 1
                            if data_row_count <= 5:  # 打印前5行数据用于调试
                                code_info = f"代码在列{code_col}" if has_valid_code else "无代码但数据完整"
                                print(f"第{data_row_count}行数据 ({code_info}, 共{len(cells)}列): {cells[:5]}...")
                        else:
                            # 可能是无效行，打印调试信息
                            if len(cells) >= 3:
                                print(f"跳过可疑行[{i}]: {cells[:3]} (无有效代码且数据不足)")
                    except Exception as e:
                        print(f"提取第{i}行时出错: {e}")
                        import traceback
                        traceback.print_exc()
                        continue
                
                print(f"成功提取 {len(rows)} 行数据")
            except Exception as e:
                print(f"提取数据行时出错: {e}")
                import traceback
                traceback.print_exc()
        
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
                        headers = ['序号', '代码', '名称', '最新价', '今日涨跌幅', '今日主力净流入', 
                                  '今日超大单净流入', '今日大单净流入', '今日中单净流入', '今日小单净流入']
                    
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
                            
                            cells = [td.text.strip() for td in tr.find_elements(By.TAG_NAME, 'td')]
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

def _resolve_save_date_str(save_date_arg: Optional[str] = None) -> str:
    """确定 CSV 文件名中的交易日：默认取今天或之前最近一个交易日。"""
    if save_date_arg:
        s = str(save_date_arg).strip().replace("-", "")
        if len(s) != 8 or not s.isdigit():
            raise ValueError(f"无效 --save-date: {save_date_arg}，应为 YYYYMMDD")
        save_dt = datetime.strptime(s, "%Y%m%d").date()
        if not is_tradeday(save_dt):
            raise ValueError(f"--save-date {s} 不是交易日，请使用有效交易日")
        return s

    anchor = last_tradeday_on_or_before(date.today())
    if anchor is None:
        raise RuntimeError("无法确定保存日期：交易日历不可用")
    today = date.today()
    if not is_tradeday(today):
        print(
            f"提示：今天({today})非交易日；东方财富页面通常为上一交易日 "
            f"({anchor}) 的日终数据，将按该日期保存文件。"
        )
    return anchor.strftime("%Y%m%d")


# --- 主程序 ---
if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="抓取东方财富个股主力净流入并保存为 CSV")
    parser.add_argument(
        "--save-date",
        metavar="YYYYMMDD",
        help="指定保存文件名中的交易日（默认：今天；若今天非交易日则为最近一个交易日）",
    )
    parser.add_argument(
        "--min-amount",
        type=float,
        default=0,
        help="落盘后可选过滤：净流入低于该值（万元）的行丢弃；默认 0=全量保留",
    )
    parser.add_argument(
        "--selenium",
        action="store_true",
        help="改用旧版 Selenium 翻页（默认走 push2 JSON 全量接口）",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="接口每页条数（默认 100，最大 100）",
    )
    args = parser.parse_args()

    try:
        save_date_str = _resolve_save_date_str(args.save_date)
    except (ValueError, RuntimeError) as e:
        print(e)
        sys.exit(1)

    today = date.today()
    if not is_tradeday(today) and not args.save_date:
        prev = previous_tradeday(today)
        if prev is None:
            print("今天非交易日，且无法确定上一交易日，退出。")
            sys.exit(1)
        prev_str = prev.strftime("%Y%m%d")
        from utils.main_force_inflow_path import resolve_flow_csv_path

        existing = resolve_flow_csv_path(prev_str)
        if existing and not args.selenium:
            # 非交易日：若要用接口刷新上一交易日全量，请显式 --save-date
            print(
                f"今天({today})非交易日，上一交易日({prev_str})文件已存在：{existing}，跳过抓取。"
            )
            print("如需用接口重刷全量，请加：--save-date " + prev_str)
            sys.exit(0)
        print(
            f"今天({today})非交易日，上一交易日({prev_str})文件不存在，开始补抓。"
        )

    print(f"保存交易日: {save_date_str}")
    df = None
    if args.selenium:
        print("正在使用 Selenium 获取个股主力净流入数据...")
        print("提取策略：翻页直到页末净额 < 阈值（--min-amount，默认已改 0 时请自行设门槛）")
        print("-" * 60)
        thr = args.min_amount if args.min_amount > 0 else 3000
        df = get_capital_flow_selenium(min_amount_threshold=thr)
    else:
        print("正在使用东方财富 push2 接口拉取全量个股主力净流入...")
        print("-" * 60)
        try:
            from utils.eastmoney_fund_flow import fetch_individual_fund_flow_df

            df, meta = fetch_individual_fund_flow_df(page_size=args.page_size)
            print(
                f"接口完成: total={meta.get('total')} fetched={meta.get('fetched')} "
                f"pages={meta.get('pages')} df={meta.get('dataframe_rows')} "
                f"耗时字段见上"
            )
        except Exception as e:
            print(f"接口抓取失败: {e}")
            import traceback

            traceback.print_exc()
            print("可加 --selenium 回退旧版翻页。")
            sys.exit(1)

    if df is not None and not df.empty:
        print(f"\n获取成功！共 {len(df)} 条数据：")
        print(df.head(10).to_string(index=False))

        try:
            history_dir = "history_data"
            if not os.path.exists(history_dir):
                os.makedirs(history_dir)

            filename = _flow_csv_path(save_date_str, history_dir)
            from utils.main_force_inflow_rank import enrich_and_rank_by_inflow_ratio

            if args.selenium:
                df_clean = clean_dataframe(df)
            else:
                # 接口结果已是干净中文列；再跑一遍排序/补列以统一格式
                df_clean = df.copy()

            df_clean, rank_stats = enrich_and_rank_by_inflow_ratio(
                df_clean, min_inflow_wan=float(args.min_amount or 0)
            )
            print(
                f"按净流入/流通市值重排: {rank_stats.get('in')} → {rank_stats.get('out')} 条"
                f"（无流通市值 {rank_stats.get('no_cap', 0)}，低于门槛丢弃 {rank_stats.get('dropped', 0)}）"
            )
            df_clean.to_csv(filename, index=False, encoding="utf_8_sig")
            print(f"\n数据已保存至 {filename} (共 {len(df_clean)} 行数据)")
            try:
                from tools.export_main_flow_to_jsonl import write_daily_main_flow_jsonl_shard

                write_daily_main_flow_jsonl_shard(filename, save_date_str)
            except Exception as e:
                print(f"[main_flow jsonl] 写出失败: {e}")
        except Exception as e:
            print(f"\n保存CSV文件失败: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)
    else:
        print("\n未能获取到有效的主力净流入数据。")
        sys.exit(1)

