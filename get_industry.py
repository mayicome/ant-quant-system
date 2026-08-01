"""
通过东方财富网页抓取股票的概念题材、所属行业、所属板块信息
使用Selenium访问网页并提取数据
"""
import time
import json
import csv
import os
from typing import List, Dict, Optional, Tuple
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup
import re

# --- 配置 ---
# 本地CSV文件路径，用于获取所有A股股票列表
STOCK_LIST_CSV = os.path.join("data", "all_a_stocks.csv")

# 网页URL模板
WEB_URL_TEMPLATE = "https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code={}&color=b#/hxtc"

# 延时设置
PAGE_LOAD_WAIT = 5  # 页面加载等待时间（秒）
REQUEST_DELAY = 2  # 每次请求后延时（秒）
MAX_RETRIES = 3  # 最大重试次数
RETRY_DELAY = 3  # 重试时等待时间（秒）
CONSECUTIVE_ERROR_LIMIT = 10  # 连续错误次数限制

# 保存设置
SAVE_INTERVAL = 50  # 每处理多少只股票就保存一次（建议50-100）
# 数据存储目录
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
OUTPUT_FILENAME = os.path.join(DATA_DIR, "all_a_stock_info.json")  # 输出文件名


def _noise_concept_names():
    """与 auto_limit_up_filter.EXCLUDED_TAGS 一致，避免概念列表里残留「一级」等过宽标签。"""
    try:
        from auto_limit_up_filter import EXCLUDED_TAGS

        return frozenset(EXCLUDED_TAGS)
    except Exception:
        return frozenset({"一级", "二级", "三级"})


# 东财行业树占位（出现在 industry 或 plates 里），与概念噪声分开处理
_TIER_MARKERS = frozenset({"一级", "二级", "三级"})


def _strip_noise_concepts_from_record(info: Dict, noise: frozenset) -> Dict:
    """写入 JSON 前：concepts 按 noise（EXCLUDED_TAGS）剔除；plates/industry 剔除层级占位 一级/二级/三级。"""
    if not isinstance(info, dict):
        return info
    out = dict(info)
    raw = out.get("concepts")
    if isinstance(raw, list):
        out["concepts"] = [
            str(c).strip()
            for c in raw
            if c is not None and str(c).strip() and str(c).strip() not in noise
        ]
    raw_p = out.get("plates")
    if isinstance(raw_p, list):
        out["plates"] = [
            str(p).strip()
            for p in raw_p
            if p is not None and str(p).strip() and str(p).strip() not in _TIER_MARKERS
        ]
    ind = out.get("industry")
    if ind is not None and str(ind).strip() in _TIER_MARKERS:
        out["industry"] = ""
    return out


# Chrome配置（如果需要指定路径，取消注释并修改）
CHROME_BINARY_PATH = r"D:\download\chrome-win64\chrome-win64\chrome.exe"  # Chrome浏览器路径
CHROMEDRIVER_PATH = r"D:\download\chromedriver-win64\chromedriver.exe"  # ChromeDriver路径

def get_stock_code_prefix(stock_code: str) -> str:
    """
    根据股票代码获取市场前缀
    :param stock_code: 股票代码，如 '600519' 或 '000001'
    :return: 市场前缀，如 'SH' 或 'SZ'
    """
    if stock_code.startswith('6'):
        return 'SH'
    elif stock_code.startswith(('0', '3')):
        return 'SZ'
    else:
        return 'SZ'  # 默认深市

def get_web_url(stock_code: str) -> str:
    """
    构造股票信息网页URL
    :param stock_code: 股票代码，如 '600519'
    :return: 完整的网页URL
    """
    prefix = get_stock_code_prefix(stock_code)
    code_with_prefix = f"{prefix}{stock_code}"
    return WEB_URL_TEMPLATE.format(code_with_prefix)

def init_driver() -> webdriver.Chrome:
    """
    初始化Chrome浏览器驱动
    :return: Chrome WebDriver实例
    """
    chrome_options = Options()
    
    # 可选：使用无头模式（不显示浏览器窗口）
    # chrome_options.add_argument('--headless')
    
    # 指定Chrome浏览器路径
    if CHROME_BINARY_PATH and os.path.exists(CHROME_BINARY_PATH):
        chrome_options.binary_location = CHROME_BINARY_PATH
        print(f"使用指定的Chrome浏览器: {CHROME_BINARY_PATH}")
    else:
        print("警告：未指定Chrome浏览器路径或路径不存在，将使用系统默认Chrome")
        if CHROME_BINARY_PATH:
            print(f"  指定的路径: {CHROME_BINARY_PATH}")
    
    # 指定ChromeDriver路径
    if CHROMEDRIVER_PATH and os.path.exists(CHROMEDRIVER_PATH):
        service = Service(executable_path=CHROMEDRIVER_PATH)
        print(f"使用指定的ChromeDriver: {CHROMEDRIVER_PATH}")
        driver = webdriver.Chrome(service=service, options=chrome_options)
    else:
        if CHROMEDRIVER_PATH:
            print(f"警告：ChromeDriver路径不存在: {CHROMEDRIVER_PATH}")
            print("将尝试使用系统默认的ChromeDriver")
        else:
            print("警告：未指定ChromeDriver路径，将使用系统默认的ChromeDriver")
        # 使用系统默认的Chrome和ChromeDriver
        driver = webdriver.Chrome(options=chrome_options)
    
    return driver

def extract_stock_info_from_page(driver: webdriver.Chrome, stock_code: str) -> Dict[str, any]:
    """
    从网页中提取股票的概念题材、所属行业、所属板块信息
    :param driver: Selenium WebDriver实例
    :param stock_code: 股票代码
    :return: 包含概念题材、所属行业、所属板块的字典
    """
    result = {
        'stock_code': stock_code,
        'name': '',  # 股票名称
        'concepts': [],  # 概念题材列表
        'industry': '',  # 所属行业（格式：一级-二级-三级）
        'plates': []  # 所属板块列表
    }
    
    try:
        # 等待页面加载完成
        try:
            # 等待页面基本元素加载
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except TimeoutException:
            pass
        
        # 额外等待，确保JavaScript执行完成
        time.sleep(2)
        
        # 获取页面源码
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        
        # 方法1: 尝试通过JavaScript执行获取数据（单页应用通常数据在JS中）
        try:
            # 查找包含行业、概念、板块信息的script标签或数据
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    script_text = script.string
                    
                    # 注意：忽略"题材亮点"中的"所属行业"，只从"所属板块"中提取行业
                    # 所属板块的第一个是行业，后面的都是板块
                    
                    # 优先查找板块信息（所属板块的第一个是行业）
                    plate_patterns = [
                        r'所属板块[：:]\s*([^\n<]+)',
                        r'板块[：:]\s*([^\n<]+)',
                        r'plate["\']?\s*[:：]\s*["\']?([^"\'\n<]+)',
                    ]
                    for pattern in plate_patterns:
                        matches = re.findall(pattern, script_text, re.IGNORECASE)
                        if matches:
                            plates_str = matches[0].strip()
                            if plates_str:
                                plates = [p.strip() for p in re.split(r'[,，;；\s]+', plates_str) if p.strip() and len(p.strip()) > 1]
                                if plates:
                                    # 第一个是行业，后面的都是板块
                                    if not result.get('industry') and len(plates) > 0:
                                        result['industry'] = plates[0]
                                    if len(plates) > 1:
                                        result['plates'] = plates[1:]
                                    break
                    
                    # 查找概念题材
                    concept_patterns = [
                        r'概念题材[：:]\s*([^\n<]+)',
                        r'概念[：:]\s*([^\n<]+)',
                        r'concept["\']?\s*[:：]\s*["\']?([^"\'\n<]+)',
                    ]
                    for pattern in concept_patterns:
                        matches = re.findall(pattern, script_text, re.IGNORECASE)
                        if matches:
                            concepts_str = matches[0].strip()
                            if concepts_str:
                                # 尝试分割概念（可能是逗号、分号分隔）
                                concepts = [c.strip() for c in re.split(r'[,，;；]', concepts_str) if c.strip()]
                                if concepts:
                                    result['concepts'] = concepts
                                    break
        except Exception as e:
            print(f"  解析script标签时出错: {e}")
        
        # 方法2: 通过页面元素查找（改进版）
        try:
            # 获取整个页面的文本内容用于调试
            page_text = driver.find_element(By.TAG_NAME, "body").text
            
            # 注意：忽略"题材亮点"中的"所属行业"，只从"所属板块"中提取行业
            # 所属板块的第一个是行业，后面的都是板块
            
            # 查找包含"概念"的元素（改进搜索策略）
            # 方法2.0: 优先查找"智能点评"，然后提取其后的概念题材
            try:
                # 查找包含"智能点评"的元素
                smart_comment_elements = driver.find_elements(By.XPATH, "//*[contains(text(), '智能点评')]")
                for smart_elem in smart_comment_elements:
                    try:
                        text = smart_elem.text.strip()
                        # 确保是"智能点评"标题本身，而不是包含"智能点评"的其他文本
                        if text == '智能点评' or re.match(r'^智能点评[：:：]?$', text):
                            concepts_found = []
                            
                            # 方法2.0.1: 查找"智能点评"后面的兄弟元素
                            try:
                                next_sibling = smart_elem.find_element(By.XPATH, "./following-sibling::*[1]")
                                sibling_count = 0
                                while next_sibling and sibling_count < 10:
                                    sibling_text = next_sibling.text.strip()
                                    # 如果遇到下一个标题，停止查找
                                    if any(keyword in sibling_text for keyword in ['所属行业', '所属板块', '入选理由', '人气龙头']):
                                        break
                                    # 检查是否包含"-"和"%"
                                    if '-' in sibling_text and '%' in sibling_text:
                                        concept_match = re.match(r'^([^-\d\s%％]+)', sibling_text)
                                        if concept_match:
                                            concept_name = concept_match.group(1).strip()
                                            if concept_name and len(concept_name) > 1 and concept_name not in ['概念', '题材', '概念题材', '智能', '点评']:
                                                concept_name = re.sub(r'[：:：]$', '', concept_name)
                                                if concept_name and concept_name not in concepts_found:
                                                    concepts_found.append(concept_name)
                                    try:
                                        next_sibling = next_sibling.find_element(By.XPATH, "./following-sibling::*[1]")
                                        sibling_count += 1
                                    except:
                                        break
                            except:
                                pass
                            
                            # 方法2.0.2: 如果兄弟元素没找到，查找父容器中"智能点评"后面的内容
                            if not concepts_found:
                                try:
                                    container = smart_elem.find_element(By.XPATH, "./ancestor::div[1] | ./ancestor::section[1] | ./ancestor::li[1]")
                                    container_text = container.text
                                    lines = container_text.split('\n')
                                    found_smart_comment = False
                                    
                                    for line in lines:
                                        line = line.strip()
                                        if '智能点评' in line:
                                            found_smart_comment = True
                                            continue
                                        
                                        if found_smart_comment:
                                            # 如果遇到下一个标题，停止查找
                                            if any(keyword in line for keyword in ['所属行业', '所属板块', '入选理由', '人气龙头']):
                                                break
                                            # 检查是否包含"-"和"%"
                                            if '-' in line and '%' in line:
                                                concept_match = re.match(r'^([^-\d\s%％]+)', line)
                                                if concept_match:
                                                    concept_name = concept_match.group(1).strip()
                                                    if concept_name and len(concept_name) > 1 and concept_name not in ['概念', '题材', '概念题材', '智能', '点评']:
                                                        concept_name = re.sub(r'[：:：]$', '', concept_name)
                                                        if concept_name and concept_name not in concepts_found:
                                                            concepts_found.append(concept_name)
                                except:
                                    pass
                            
                            if concepts_found:
                                result['concepts'] = concepts_found
                                break
                    except:
                        continue
            except:
                pass
            
            # 方法2.1: 如果方法2.0没找到，尝试查找"概念题材"标题，然后提取其后的所有概念项（列表格式）
            if not result.get('concepts'):
                try:
                    # 查找包含"概念题材"的元素
                    concept_title_elements = driver.find_elements(By.XPATH, "//*[contains(text(), '概念题材')]")
                    for title_elem in concept_title_elements:
                        try:
                            # 方法2.1.1: 尝试找到标题所在的容器，然后查找所有包含百分比的概念项
                            container = title_elem.find_element(By.XPATH, "./ancestor::div[1] | ./ancestor::section[1] | ./ancestor::li[1]")
                            
                            # 查找容器内所有包含"-"和"%"的元素（概念格式通常是"概念名称 -2.60%"）
                            concept_elements = container.find_elements(By.XPATH, ".//*[contains(text(), '-') and contains(text(), '%')]")
                            concepts_found = []
                            
                            for elem in concept_elements:
                                try:
                                    text = elem.text.strip()
                                    # 提取概念名称（去除百分比、数字等）
                                    # 格式：概念名称 -2.60% 或 概念名称 -2.60
                                    concept_match = re.match(r'^([^-\d\s%％]+)', text)
                                    if concept_match:
                                        concept_name = concept_match.group(1).strip()
                                        # 过滤掉一些明显不是概念名称的内容
                                        if concept_name and len(concept_name) > 1 and concept_name not in ['概念', '题材', '概念题材', '入选理由', '人气龙头']:
                                            concept_name = re.sub(r'[：:：]$', '', concept_name)
                                            if concept_name and concept_name not in concepts_found:
                                                concepts_found.append(concept_name)
                                except:
                                    continue
                            
                            if concepts_found:
                                result['concepts'] = concepts_found
                                break
                            
                            # 方法2.1.2: 如果方法2.1.1没找到，尝试从容器文本中提取
                            if not concepts_found:
                                container_text = container.text
                                # 在容器文本中查找所有概念项
                                # 概念题材通常格式为：概念题材\n  概念1 -2.60%\n  概念2 -1.20%\n ...
                                lines = container_text.split('\n')
                                concepts_found = []
                                found_title = False
                                
                                for line in lines:
                                    line = line.strip()
                                    if '概念题材' in line:
                                        found_title = True
                                        continue
                                    
                                    if found_title and line:
                                        # 跳过"入选理由"、"人气龙头"等标题
                                        if any(keyword in line for keyword in ['入选理由', '人气龙头', '题材亮点', '题材详情']):
                                            break
                                        
                                        # 提取概念名称（去除涨跌幅等额外信息）
                                        # 格式可能是：概念名称 -2.60% 或 概念名称 或其他格式
                                        # 使用正则表达式提取概念名称（去除百分比、数字等）
                                        concept_match = re.match(r'^([^-\d\s%％]+)', line)
                                        if concept_match:
                                            concept_name = concept_match.group(1).strip()
                                            # 过滤掉一些明显不是概念名称的内容
                                            if concept_name and len(concept_name) > 1 and concept_name not in ['概念', '题材', '概念题材']:
                                                # 进一步清理：去除可能的标点符号
                                                concept_name = re.sub(r'[：:：]$', '', concept_name)
                                                if concept_name and concept_name not in concepts_found:
                                                    concepts_found.append(concept_name)
                                
                                if concepts_found:
                                    result['concepts'] = concepts_found
                                    break
                        except:
                            continue
                except:
                    pass
            
            # 方法2.2: 如果方法2.1没找到，尝试原来的搜索方式
            if not result.get('concepts'):
                concept_elements = driver.find_elements(By.XPATH, "//*[contains(text(), '概念题材') or contains(text(), '概念')]")
                for elem in concept_elements:
                    try:
                        text = elem.text.strip()
                        # 尝试获取父元素和兄弟元素
                        try:
                            parent = elem.find_element(By.XPATH, "./..")
                            parent_text = parent.text.strip()
                        except:
                            parent_text = text
                        
                        # 尝试获取整个容器元素
                        try:
                            container = elem.find_element(By.XPATH, "./ancestor::div[1]")
                            container_text = container.text.strip()
                        except:
                            container_text = parent_text
                        
                        # 在多个文本中搜索
                        for search_text in [text, parent_text, container_text]:
                            if '概念题材' in search_text or '概念' in search_text:
                                # 尝试多种正则表达式模式
                                patterns = [
                                    r'概念题材[：:]\s*([^\n\r]+)',
                                    r'概念[：:]\s*([^\n\r]+)',
                                    r'概念题材\s*[：:]\s*([^\n\r]+)',
                                ]
                                for pattern in patterns:
                                    match = re.search(pattern, search_text)
                                    if match:
                                        concepts_str = match.group(1).strip()
                                        # 尝试分割概念（可能是逗号、分号、空格分隔）
                                        concepts = [c.strip() for c in re.split(r'[,，;；\s]+', concepts_str) 
                                                  if c.strip() and len(c.strip()) > 1]
                                        if concepts:
                                            result['concepts'] = concepts
                                            break
                                if result.get('concepts'):
                                    break
                        if result.get('concepts'):
                            break
                    except:
                        continue
            
            # 如果还没找到概念，尝试在整个页面文本中搜索（支持列表格式）
            if not result.get('concepts'):
                # 方法2.3: 在页面文本中搜索概念题材（列表格式）
                lines = page_text.split('\n')
                concepts_found = []
                found_title = False
                
                # 方法2.3.1: 先尝试通过"入选理由"定位概念题材
                # "入选理由"前面的都是概念题材
                for i, line in enumerate(lines):
                    line = line.strip()
                    if '入选理由' in line:
                        # 向前查找概念题材（从当前位置向前查找，最多50行）
                        for j in range(max(0, i - 50), i):
                            concept_line = lines[j].strip()
                            if concept_line:
                                # 跳过标题行
                                if any(keyword in concept_line for keyword in ['概念题材', '所属行业', '所属板块', '行业', '板块', '入选理由']):
                                    continue
                                # 提取概念名称（去除涨跌幅等额外信息）
                                concept_match = re.match(r'^([^-\d\s%％]+)', concept_line)
                                if concept_match:
                                    concept_name = concept_match.group(1).strip()
                                    # 过滤掉一些明显不是概念名称的内容
                                    if concept_name and len(concept_name) > 1 and concept_name not in ['概念', '题材', '概念题材', '所属', '行业', '板块']:
                                        concept_name = re.sub(r'[：:：]$', '', concept_name)
                                        if concept_name and concept_name not in concepts_found:
                                            concepts_found.append(concept_name)
                        if concepts_found:
                            result['concepts'] = concepts_found
                            break
                
                # 方法2.3.2: 如果通过"入选理由"没找到，尝试通过"概念题材"标题查找
                if not result.get('concepts'):
                    for i, line in enumerate(lines):
                        line = line.strip()
                        if '概念题材' in line:
                            found_title = True
                            # 继续查找后续行中的概念
                            # 通常概念会在标题后的几行内
                            for j in range(i + 1, min(i + 20, len(lines))):  # 查找标题后20行
                                concept_line = lines[j].strip()
                                if concept_line:
                                    # 提取概念名称（去除涨跌幅等额外信息）
                                    concept_match = re.match(r'^([^-\d\s%％]+)', concept_line)
                                    if concept_match:
                                        concept_name = concept_match.group(1).strip()
                                        # 过滤掉一些明显不是概念名称的内容
                                        if concept_name and len(concept_name) > 1 and concept_name not in ['概念', '题材', '概念题材']:
                                            concept_name = re.sub(r'[：:：]$', '', concept_name)
                                            if concept_name and concept_name not in concepts_found:
                                                concepts_found.append(concept_name)
                                    # 如果遇到下一个标题（如"所属行业"），停止收集
                                    elif any(keyword in concept_line for keyword in ['所属行业', '所属板块', '行业', '板块', '入选理由']):
                                        break
                            if concepts_found:
                                result['concepts'] = concepts_found
                                break
                
                # 方法2.4: 如果列表格式没找到，尝试原来的单行格式
                if not result.get('concepts'):
                    concept_patterns = [
                        r'概念题材[：:]\s*([^\n\r]+)',
                        r'概念[：:]\s*([^\n\r]+)',
                    ]
                    for pattern in concept_patterns:
                        matches = re.findall(pattern, page_text)
                        if matches:
                            concepts_str = matches[0].strip()
                            concepts = [c.strip() for c in re.split(r'[,，;；\s]+', concepts_str) 
                                      if c.strip() and len(c.strip()) > 1]
                            if concepts:
                                result['concepts'] = concepts
                                break
            
            # 查找包含"板块"的元素
            plate_elements = driver.find_elements(By.XPATH, "//*[contains(text(), '板块')]")
            for elem in plate_elements:
                try:
                    text = elem.text.strip()
                    try:
                        parent = elem.find_element(By.XPATH, "./..")
                        parent_text = parent.text.strip()
                    except:
                        parent_text = text
                    
                    if '所属板块' in text or '所属板块' in parent_text:
                        match = re.search(r'所属板块[：:]\s*([^\n\r]+)', parent_text)
                        if match:
                            plates_str = match.group(1).strip()
                            plates = [p.strip() for p in re.split(r'[,，;；\s]+', plates_str) 
                                    if p.strip() and len(p.strip()) > 1]
                            if plates:
                                result['plates'] = plates
                                break
                except:
                    continue
            
            # 如果还没找到板块，尝试在整个页面文本中搜索
            # 注意：所属板块的第一个是行业，后面的都是板块
            # 重要：忽略"题材亮点"中的"所属行业"，只从"所属板块"中提取
            if not result.get('plates') or not result.get('industry'):
                # 方法：只查找"所属板块"（忽略"题材亮点"中的"所属行业"），第一个是行业，后面的都是板块
                lines = page_text.split('\n')
                found_industry_plate_section = False
                
                for i, line in enumerate(lines):
                    line = line.strip()
                    # 只查找"所属板块"标题（忽略"题材亮点"中的"所属行业"）
                    # 检查是否在"题材亮点"部分，如果是则跳过
                    if i > 0:
                        # 检查前面几行是否有"题材亮点"
                        context_lines = lines[max(0, i-5):i]
                        context_text = ' '.join(context_lines)
                        if '题材亮点' in context_text and '所属行业' in line:
                            # 这是"题材亮点"中的"所属行业"，跳过
                            continue
                    
                    # 只查找"所属板块"标题
                    if '所属板块' in line:
                        found_industry_plate_section = True
                        # 查找标题后的内容（可能是列表格式或单行格式）
                        industry_plates = []
                        
                        # 先尝试列表格式（每行一个）
                        for j in range(i + 1, min(i + 30, len(lines))):  # 查找标题后30行
                            item_line = lines[j].strip()
                            if not item_line:
                                continue
                            
                            # 如果遇到下一个标题，停止收集
                            if any(keyword in item_line for keyword in ['概念题材', '入选理由', '其他']):
                                break
                            
                            # 提取板块/行业名称（去除百分比、数字等）
                            item_match = re.match(r'^([^-\d\s%％]+)', item_line)
                            if item_match:
                                item_name = item_match.group(1).strip()
                                # 过滤掉明显不是名称的内容
                                if item_name and len(item_name) > 1 and item_name not in ['所属', '行业', '板块', '所属行业', '所属板块']:
                                    item_name = re.sub(r'[：:：]$', '', item_name)
                                    if item_name and item_name not in industry_plates:
                                        industry_plates.append(item_name)
                        
                        # 如果列表格式找到了，第一个是行业，后面的都是板块
                        if industry_plates:
                            if not result.get('industry') and len(industry_plates) > 0:
                                result['industry'] = industry_plates[0]
                            if len(industry_plates) > 1:
                                result['plates'] = industry_plates[1:]
                            break
                        
                        # 如果列表格式没找到，尝试单行格式
                        if not industry_plates:
                            # 在当前行或下一行查找
                            search_text = line
                            if i + 1 < len(lines):
                                search_text += ' ' + lines[i + 1].strip()
                            
                            # 只查找"所属板块"（忽略"所属行业"）
                            match = re.search(r'所属板块[：:]\s*([^\n\r]+)', search_text)
                            if match:
                                items_str = match.group(1).strip()
                                items = [item.strip() for item in re.split(r'[,，;；\s]+', items_str) 
                                        if item.strip() and len(item.strip()) > 1]
                                if items:
                                    if not result.get('industry') and len(items) > 0:
                                        result['industry'] = items[0]
                                    if len(items) > 1:
                                        result['plates'] = items[1:]
                                    break
                
                # 如果还没找到，尝试原来的正则表达式方法
                if not result.get('plates') and not result.get('industry'):
                    plate_patterns = [
                        r'所属板块[：:]\s*([^\n\r]+)',
                        r'板块[：:]\s*([^\n\r]+)',
                    ]
                    for pattern in plate_patterns:
                        matches = re.findall(pattern, page_text)
                        if matches:
                            plates_str = matches[0].strip()
                            plates = [p.strip() for p in re.split(r'[,，;；\s]+', plates_str) 
                                    if p.strip() and len(p.strip()) > 1]
                            if plates:
                                # 第一个是行业，后面的都是板块
                                if not result.get('industry') and len(plates) > 0:
                                    result['industry'] = plates[0]
                                if len(plates) > 1:
                                    result['plates'] = plates[1:]
                                break
        except Exception as e:
            print(f"  查找页面元素时出错: {e}")
        
        # 方法3: 尝试通过JavaScript直接获取数据（改进版）
        try:
            # 尝试执行JavaScript获取数据
            js_code = """
            var result = {
                industry: '',
                concepts: [],
                plates: []
            };
            
            // 方法3.1: 尝试从页面全局变量或数据中获取
            if (window.stockData) {
                result.industry = window.stockData.industry || '';
                result.concepts = window.stockData.concepts || [];
                result.plates = window.stockData.plates || [];
            }
            
            // 方法3.2: 尝试从DOM元素中提取文本（改进搜索策略）
            var allElements = document.querySelectorAll('*');
            var pageText = document.body.textContent || document.body.innerText || '';
            
            // 注意：忽略"题材亮点"中的"所属行业"，只从"所属板块"中提取行业
            // 行业信息应该从"所属板块"中提取（第一个是行业），这里不再单独提取"所属行业"
            
            // 查找概念题材（多种模式，支持列表格式）
            if (result.concepts.length === 0) {
                var lines = pageText.split('\\n');
                var conceptsFound = [];
                
                // 方法1: 通过"智能点评"定位概念题材（"智能点评"后面的都是概念题材）
                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i].trim();
                    if (line.indexOf('智能点评') !== -1) {
                        // 先尝试单行格式：从"智能点评"所在行或下一行提取（可能用逗号分隔）
                        var singleLineText = '';
                        // 尝试从当前行提取（智能点评后面的内容）
                        var match = line.match(/智能点评[：:：]?\s*(.+)/);
                        if (match && match[1]) {
                            singleLineText = match[1].trim();
                        } else if (i + 1 < lines.length) {
                            // 如果当前行没有，尝试下一行
                            singleLineText = lines[i + 1].trim();
                        }
                        
                        // 如果单行文本存在，尝试提取概念
                        if (singleLineText) {
                            // 如果包含逗号或分号，按分隔符分割
                            if (singleLineText.indexOf(',') !== -1 || singleLineText.indexOf('，') !== -1 || 
                                singleLineText.indexOf(';') !== -1 || singleLineText.indexOf('；') !== -1) {
                                var concepts = singleLineText.split(/[,，;；]+/).map(function(c) {
                                    return c.trim();
                                }).filter(function(c) {
                                    return c && c.length > 1 && 
                                           c !== '概念' && c !== '题材' && 
                                           c !== '概念题材' && c !== '所属' && 
                                           c !== '行业' && c !== '板块' &&
                                           c !== '智能' && c !== '点评';
                                });
                                if (concepts.length > 0) {
                                    result.concepts = concepts;
                                    break;
                                }
                            } else {
                                // 如果没有分隔符，将整个文本作为一个概念（过滤无效内容）
                                var trimmed = singleLineText.trim();
                                if (trimmed && trimmed.length > 1 && 
                                    trimmed !== '概念' && trimmed !== '题材' && 
                                    trimmed !== '概念题材' && trimmed !== '所属' && 
                                    trimmed !== '行业' && trimmed !== '板块' &&
                                    trimmed !== '智能' && trimmed !== '点评') {
                                    result.concepts = [trimmed];
                                    break;
                                }
                            }
                        }
                        
                        // 如果单行格式没找到，尝试列表格式（每行一个概念）
                        // 向后查找概念题材（从"智能点评"后面开始查找，最多50行）
                        for (var j = i + 1; j < Math.min(i + 50, lines.length); j++) {
                            var conceptLine = lines[j].trim();
                            if (conceptLine) {
                                // 如果遇到下一个标题，停止收集
                                if (conceptLine.indexOf('所属行业') !== -1 || 
                                    conceptLine.indexOf('所属板块') !== -1 || 
                                    conceptLine.indexOf('行业') !== -1 || 
                                    conceptLine.indexOf('板块') !== -1 || 
                                    conceptLine.indexOf('概念题材') !== -1 ||
                                    conceptLine.indexOf('智能点评') !== -1 ||
                                    conceptLine.indexOf('入选理由') !== -1 ||
                                    conceptLine.indexOf('经营范围') !== -1 ||
                                    conceptLine.indexOf('主营业务') !== -1) {
                                    break;
                                }
                                // 提取概念名称（去除涨跌幅等）
                                var conceptMatch = conceptLine.match(/^([^-\\d\\s%％]+)/);
                                if (conceptMatch) {
                                    var conceptName = conceptMatch[1].trim();
                                    if (conceptName && conceptName.length > 1 && 
                                        conceptName !== '概念' && conceptName !== '题材' && 
                                        conceptName !== '概念题材' && conceptName !== '所属' && 
                                        conceptName !== '行业' && conceptName !== '板块' &&
                                        conceptName !== '智能' && conceptName !== '点评') {
                                        conceptName = conceptName.replace(/[：:：]$/, '');
                                        if (conceptName && conceptsFound.indexOf(conceptName) === -1) {
                                            conceptsFound.push(conceptName);
                                        }
                                    }
                                }
                            }
                        }
                        if (conceptsFound.length > 0) {
                            result.concepts = conceptsFound;
                            break;
                        }
                    }
                }
                
                // 方法2: 如果通过"智能点评"没找到，尝试通过"概念题材"标题查找
                if (result.concepts.length === 0) {
                    for (var i = 0; i < lines.length; i++) {
                        var line = lines[i].trim();
                        if (line.indexOf('概念题材') !== -1) {
                            // 查找标题后的概念项（最多20行）
                            for (var j = i + 1; j < Math.min(i + 20, lines.length); j++) {
                                var conceptLine = lines[j].trim();
                                if (conceptLine) {
                                    // 提取概念名称（去除涨跌幅等）
                                    var conceptMatch = conceptLine.match(/^([^-\\d\\s%％]+)/);
                                    if (conceptMatch) {
                                        var conceptName = conceptMatch[1].trim();
                                        if (conceptName && conceptName.length > 1 && 
                                            conceptName !== '概念' && conceptName !== '题材' && 
                                            conceptName !== '概念题材') {
                                            conceptName = conceptName.replace(/[：:：]$/, '');
                                            if (conceptName && conceptsFound.indexOf(conceptName) === -1) {
                                                conceptsFound.push(conceptName);
                                            }
                                        }
                                    } else if (conceptLine.indexOf('所属行业') !== -1 || 
                                              conceptLine.indexOf('所属板块') !== -1 ||
                                              conceptLine.indexOf('行业') !== -1 ||
                                              conceptLine.indexOf('板块') !== -1 ||
                                              conceptLine.indexOf('智能点评') !== -1 ||
                                              conceptLine.indexOf('入选理由') !== -1) {
                                        break; // 遇到下一个标题，停止收集
                                    }
                                }
                            }
                            if (conceptsFound.length > 0) {
                                result.concepts = conceptsFound;
                                break;
                            }
                        }
                    }
                }
                
                // 方法2: 如果列表格式没找到，尝试单行格式
                if (result.concepts.length === 0) {
                    var conceptPatterns = [
                        /概念题材[：:]\s*([^\\n\\r]+)/,
                        /概念[：:]\s*([^\\n\\r]+)/,
                    ];
                    
                    for (var i = 0; i < conceptPatterns.length; i++) {
                        var match = pageText.match(conceptPatterns[i]);
                        if (match && match[1]) {
                            var conceptsStr = match[1].trim();
                            var concepts = conceptsStr.split(/[,，;；\\s]+/).filter(function(c) {
                                return c.trim().length > 1;
                            });
                            if (concepts.length > 0) {
                                result.concepts = concepts;
                                break;
                            }
                        }
                    }
                }
            }
            
            // 查找板块信息（注意：所属板块的第一个是行业，后面的都是板块）
            if (result.plates.length === 0 || !result.industry) {
                var lines = pageText.split('\\n');
                var industryPlates = [];
                var foundIndustryPlateSection = false;
                
                // 只查找"所属板块"标题（忽略"题材亮点"中的"所属行业"）
                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i].trim();
                    // 检查是否在"题材亮点"部分，如果是则跳过
                    if (i > 0) {
                        // 检查前面几行是否有"题材亮点"
                        var contextLines = lines.slice(Math.max(0, i - 5), i);
                        var contextText = contextLines.join(' ');
                        if (contextText.indexOf('题材亮点') !== -1 && line.indexOf('所属行业') !== -1) {
                            // 这是"题材亮点"中的"所属行业"，跳过
                            continue;
                        }
                    }
                    // 只查找"所属板块"标题
                    if (line.indexOf('所属板块') !== -1) {
                        foundIndustryPlateSection = true;
                        // 查找标题后的内容（可能是列表格式或单行格式）
                        industryPlates = [];
                        
                        // 先尝试列表格式（每行一个）
                        for (var j = i + 1; j < Math.min(i + 30, lines.length); j++) {
                            var itemLine = lines[j].trim();
                            if (!itemLine) {
                                continue;
                            }
                            
                            // 如果遇到下一个标题，停止收集
                            if (itemLine.indexOf('概念题材') !== -1 || 
                                itemLine.indexOf('入选理由') !== -1 || 
                                itemLine.indexOf('其他') !== -1) {
                                break;
                            }
                            
                            // 提取板块/行业名称（去除百分比、数字等）
                            var itemMatch = itemLine.match(/^([^-\\d\\s%％]+)/);
                            if (itemMatch) {
                                var itemName = itemMatch[1].trim();
                                // 过滤掉明显不是名称的内容
                                if (itemName && itemName.length > 1 && 
                                    itemName !== '所属' && itemName !== '行业' && 
                                    itemName !== '板块' && itemName !== '所属行业' && 
                                    itemName !== '所属板块') {
                                    itemName = itemName.replace(/[：:：]$/, '');
                                    if (itemName && industryPlates.indexOf(itemName) === -1) {
                                        industryPlates.push(itemName);
                                    }
                                }
                            }
                        }
                        
                        // 如果列表格式找到了，第一个是行业，后面的都是板块
                        if (industryPlates.length > 0) {
                            if (!result.industry && industryPlates.length > 0) {
                                result.industry = industryPlates[0];
                            }
                            if (industryPlates.length > 1) {
                                result.plates = industryPlates.slice(1);
                            }
                            break;
                        }
                        
                        // 如果列表格式没找到，尝试单行格式
                        if (industryPlates.length === 0) {
                            var searchText = line;
                            if (i + 1 < lines.length) {
                                searchText += ' ' + lines[i + 1].trim();
                            }
                            
                            // 只查找"所属板块"（忽略"所属行业"）
                            var match = searchText.match(/所属板块[：:]\s*([^\\n\\r]+)/);
                            if (match && match[1]) {
                                var itemsStr = match[1].trim();
                                var items = itemsStr.split(/[,，;；\\s]+/).filter(function(item) {
                                    return item.trim().length > 1;
                                });
                                if (items.length > 0) {
                                    if (!result.industry && items.length > 0) {
                                        result.industry = items[0];
                                    }
                                    if (items.length > 1) {
                                        result.plates = items.slice(1);
                                    }
                                    break;
                                }
                            }
                        }
                    }
                }
                
                // 如果还没找到，尝试原来的正则表达式方法
                if ((result.plates.length === 0 || !result.industry) && !foundIndustryPlateSection) {
                    var platePatterns = [
                        /所属板块[：:]\s*([^\\n\\r]+)/,
                        /板块[：:]\s*([^\\n\\r]+)/,
                    ];
                    
                    for (var i = 0; i < platePatterns.length; i++) {
                        var match = pageText.match(platePatterns[i]);
                        if (match && match[1]) {
                            var platesStr = match[1].trim();
                            var plates = platesStr.split(/[,，;；\\s]+/).filter(function(p) {
                                return p.trim().length > 1;
                            });
                            if (plates.length > 0) {
                                // 第一个是行业，后面的都是板块
                                if (!result.industry && plates.length > 0) {
                                    result.industry = plates[0];
                                }
                                if (plates.length > 1) {
                                    result.plates = plates.slice(1);
                                }
                                break;
                            }
                        }
                    }
                }
            }
            
            // 方法3.3: 通过DOM选择器直接查找"智能点评"后面的概念题材元素
            if (result.concepts.length === 0) {
                // 优先查找包含"智能点评"的元素
                var allElementsForSearch = document.querySelectorAll('*');
                for (var i = 0; i < allElementsForSearch.length; i++) {
                    var elem = allElementsForSearch[i];
                    var text = elem.textContent || elem.innerText || '';
                    
                    // 查找"智能点评"元素
                    if (text.trim() === '智能点评' || text.match(/^智能点评[：:：]?$/)) {
                        try {
                            var conceptsFound = [];
                            
                            // 方法3.3.1: 查找"智能点评"后面的兄弟元素
                            var nextSibling = elem.nextElementSibling;
                            while (nextSibling && conceptsFound.length < 10) {
                                var siblingText = nextSibling.textContent || nextSibling.innerText || '';
                                // 如果遇到下一个标题，停止查找
                                if (siblingText.includes('所属行业') || siblingText.includes('所属板块') || 
                                    siblingText.includes('入选理由') || siblingText.includes('人气龙头')) {
                                    break;
                                }
                                // 检查是否包含"-"和"%"
                                if (siblingText.includes('-') && siblingText.includes('%')) {
                                    var conceptMatch = siblingText.match(/^([^-\d\s%％]+)/);
                                    if (conceptMatch) {
                                        var conceptName = conceptMatch[1].trim();
                                        if (conceptName && conceptName.length > 1 && 
                                            conceptName !== '概念' && conceptName !== '题材' && 
                                            conceptName !== '概念题材' && conceptName !== '智能' && 
                                            conceptName !== '点评') {
                                            conceptName = conceptName.replace(/[：:：]$/, '');
                                            if (conceptName && conceptsFound.indexOf(conceptName) === -1) {
                                                conceptsFound.push(conceptName);
                                            }
                                        }
                                    }
                                }
                                nextSibling = nextSibling.nextElementSibling;
                            }
                            
                            // 方法3.3.2: 如果兄弟元素没找到，查找父容器中"智能点评"后面的元素
                            if (conceptsFound.length === 0) {
                                var container = elem.parentElement;
                                if (container) {
                                    var containerText = container.textContent || container.innerText || '';
                                    var lines = containerText.split('\\n');
                                    var foundSmartComment = false;
                                    
                                    for (var k = 0; k < lines.length; k++) {
                                        var line = lines[k].trim();
                                        if (line.includes('智能点评')) {
                                            foundSmartComment = true;
                                            continue;
                                        }
                                        
                                        if (foundSmartComment) {
                                            // 如果遇到下一个标题，停止查找
                                            if (line.includes('所属行业') || line.includes('所属板块') || 
                                                line.includes('入选理由') || line.includes('人气龙头')) {
                                                break;
                                            }
                                            // 检查是否包含"-"和"%"
                                            if (line.includes('-') && line.includes('%')) {
                                                var conceptMatch = line.match(/^([^-\d\s%％]+)/);
                                                if (conceptMatch) {
                                                    var conceptName = conceptMatch[1].trim();
                                                    if (conceptName && conceptName.length > 1 && 
                                                        conceptName !== '概念' && conceptName !== '题材' && 
                                                        conceptName !== '概念题材' && conceptName !== '智能' && 
                                                        conceptName !== '点评') {
                                                        conceptName = conceptName.replace(/[：:：]$/, '');
                                                        if (conceptName && conceptsFound.indexOf(conceptName) === -1) {
                                                            conceptsFound.push(conceptName);
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            
                            if (conceptsFound.length > 0) {
                                result.concepts = conceptsFound;
                                break;
                            }
                        } catch (e) {
                            // 忽略错误，继续查找
                        }
                    }
                }
            }
            
            // 方法3.4: 遍历所有元素，查找包含关键词的元素
            for (var i = 0; i < allElements.length; i++) {
                var elem = allElements[i];
                var text = elem.textContent || elem.innerText || '';
                
                // 注意：忽略"题材亮点"中的"所属行业"，只从"所属板块"中提取行业
                // 行业信息应该从"所属板块"中提取（第一个是行业），这里不再单独提取"所属行业"
                
                // 查找概念题材（通过"智能点评"定位）
                if (result.concepts.length === 0 && text.includes('智能点评')) {
                    var lines = text.split('\\n');
                    var conceptsFound = [];
                    
                    for (var k = 0; k < lines.length; k++) {
                        var line = lines[k].trim();
                        if (line.indexOf('智能点评') !== -1) {
                            // 先尝试单行格式：从"智能点评"所在行或下一行提取（可能用逗号分隔）
                            var singleLineText = '';
                            // 尝试从当前行提取（智能点评后面的内容）
                            var match = line.match(/智能点评[：:：]?\s*(.+)/);
                            if (match && match[1]) {
                                singleLineText = match[1].trim();
                            } else if (k + 1 < lines.length) {
                                // 如果当前行没有，尝试下一行
                                singleLineText = lines[k + 1].trim();
                            }
                            
                            // 如果单行文本存在，尝试提取概念
                            if (singleLineText) {
                                // 如果包含逗号或分号，按分隔符分割
                                if (singleLineText.indexOf(',') !== -1 || singleLineText.indexOf('，') !== -1 || 
                                    singleLineText.indexOf(';') !== -1 || singleLineText.indexOf('；') !== -1) {
                                    var concepts = singleLineText.split(/[,，;；]+/).map(function(c) {
                                        return c.trim();
                                    }).filter(function(c) {
                                        return c && c.length > 1 && 
                                               c !== '概念' && c !== '题材' && 
                                               c !== '概念题材' && c !== '所属' && 
                                               c !== '行业' && c !== '板块' &&
                                               c !== '智能' && c !== '点评';
                                    });
                                    if (concepts.length > 0) {
                                        result.concepts = concepts;
                                        break;
                                    }
                                } else {
                                    // 如果没有分隔符，将整个文本作为一个概念（过滤无效内容）
                                    var trimmed = singleLineText.trim();
                                    if (trimmed && trimmed.length > 1 && 
                                        trimmed !== '概念' && trimmed !== '题材' && 
                                        trimmed !== '概念题材' && trimmed !== '所属' && 
                                        trimmed !== '行业' && trimmed !== '板块' &&
                                        trimmed !== '智能' && trimmed !== '点评') {
                                        result.concepts = [trimmed];
                                        break;
                                    }
                                }
                            }
                            
                            // 如果单行格式没找到，尝试列表格式（每行一个概念）
                            // 向后查找概念题材（从"智能点评"后面开始查找，最多50行）
                            for (var m = k + 1; m < Math.min(k + 50, lines.length); m++) {
                                var conceptLine = lines[m].trim();
                                if (conceptLine) {
                                    // 如果遇到下一个标题，停止收集
                                    if (conceptLine.indexOf('所属行业') !== -1 || 
                                        conceptLine.indexOf('所属板块') !== -1 || 
                                        conceptLine.indexOf('行业') !== -1 || 
                                        conceptLine.indexOf('板块') !== -1 || 
                                        conceptLine.indexOf('概念题材') !== -1 ||
                                        conceptLine.indexOf('智能点评') !== -1 ||
                                        conceptLine.indexOf('入选理由') !== -1 ||
                                        conceptLine.indexOf('经营范围') !== -1 ||
                                        conceptLine.indexOf('主营业务') !== -1) {
                                        break;
                                    }
                                    // 提取概念名称（去除涨跌幅等）
                                    var conceptMatch = conceptLine.match(/^([^-\\d\\s%％]+)/);
                                    if (conceptMatch) {
                                        var conceptName = conceptMatch[1].trim();
                                        if (conceptName && conceptName.length > 1 && 
                                            conceptName !== '概念' && conceptName !== '题材' && 
                                            conceptName !== '概念题材' && conceptName !== '所属' && 
                                            conceptName !== '行业' && conceptName !== '板块' &&
                                            conceptName !== '智能' && conceptName !== '点评') {
                                            conceptName = conceptName.replace(/[：:：]$/, '');
                                            if (conceptName && conceptsFound.indexOf(conceptName) === -1) {
                                                conceptsFound.push(conceptName);
                                            }
                                        }
                                    }
                                }
                            }
                            if (conceptsFound.length > 0) {
                                result.concepts = conceptsFound;
                                break;
                            }
                        }
                    }
                }
                
                // 如果通过"智能点评"没找到，尝试通过"概念题材"标题查找
                if (result.concepts.length === 0 && (text.includes('概念题材') || text.includes('概念：'))) {
                    // 先尝试列表格式
                    var lines = text.split('\\n');
                    var conceptsFound = [];
                    
                    for (var k = 0; k < lines.length; k++) {
                        var line = lines[k].trim();
                        if (line.indexOf('概念题材') !== -1) {
                            // 查找标题后的概念项
                            for (var m = k + 1; m < Math.min(k + 20, lines.length); m++) {
                                var conceptLine = lines[m].trim();
                                if (conceptLine) {
                                    var conceptMatch = conceptLine.match(/^([^-\\d\\s%％]+)/);
                                    if (conceptMatch) {
                                        var conceptName = conceptMatch[1].trim();
                                        if (conceptName && conceptName.length > 1 && 
                                            conceptName !== '概念' && conceptName !== '题材' && 
                                            conceptName !== '概念题材') {
                                            conceptName = conceptName.replace(/[：:：]$/, '');
                                            if (conceptName && conceptsFound.indexOf(conceptName) === -1) {
                                                conceptsFound.push(conceptName);
                                            }
                                        }
                                    } else if (conceptLine.indexOf('所属行业') !== -1 || 
                                              conceptLine.indexOf('所属板块') !== -1 ||
                                              conceptLine.indexOf('智能点评') !== -1) {
                                        break;
                                    }
                                }
                            }
                            if (conceptsFound.length > 0) {
                                result.concepts = conceptsFound;
                                break;
                            }
                        }
                    }
                    
                    // 如果列表格式没找到，尝试单行格式
                    if (result.concepts.length === 0) {
                        var match = text.match(/概念题材[：:]\s*([^\\n\\r]+)/);
                        if (!match) {
                            match = text.match(/概念[：:]\s*([^\\n\\r]+)/);
                        }
                        if (match && match[1]) {
                            var conceptsStr = match[1].trim();
                            var concepts = conceptsStr.split(/[,，;；\\s]+/).filter(function(c) {
                                return c.trim().length > 1;
                            });
                            if (concepts.length > 0) {
                                result.concepts = concepts;
                            }
                        }
                    }
                }
                
                // 查找板块信息
                if (result.plates.length === 0 && (text.includes('所属板块') || text.includes('板块：'))) {
                    var match = text.match(/所属板块[：:]\s*([^\\n\\r]+)/);
                    if (match && match[1]) {
                        var platesStr = match[1].trim();
                        var plates = platesStr.split(/[,，;；\\s]+/).filter(function(p) {
                            return p.trim().length > 1;
                        });
                        if (plates.length > 0) {
                            result.plates = plates;
                        }
                    }
                }
            }
            
            return JSON.stringify(result);
            """
            js_result = driver.execute_script(js_code)
            if js_result and js_result != '{}':
                js_data = json.loads(js_result)
                if js_data.get('industry') and not result.get('industry'):
                    result['industry'] = js_data['industry']
                if js_data.get('concepts') and not result.get('concepts'):
                    result['concepts'] = js_data['concepts']
                if js_data.get('plates') and not result.get('plates'):
                    result['plates'] = js_data['plates']
        except Exception as e:
            pass  # JavaScript执行失败，忽略
        
        # 调试输出：如果找到了数据，打印出来
        if result.get('concepts') or result.get('industry') or result.get('plates'):
            if result.get('concepts'):
                print(f"  找到概念题材: {result['concepts']}")
            if result.get('industry'):
                print(f"  找到行业: {result['industry']}")
            if result.get('plates'):
                print(f"  找到板块: {result['plates']}")
        else:
            # 如果没找到数据，尝试打印页面部分内容用于调试
            try:
                page_text = driver.find_element(By.TAG_NAME, "body").text
                # 查找包含"概念"、"行业"、"板块"关键词的文本片段
                lines = page_text.split('\n')
                relevant_lines = []
                for line in lines:
                    if any(keyword in line for keyword in ['概念', '行业', '板块', '题材']):
                        relevant_lines.append(line.strip())
                        if len(relevant_lines) >= 10:  # 只显示前10行相关文本
                            break
                if relevant_lines:
                    print(f"  调试：页面中包含相关关键词的行（前10行）:")
                    for line in relevant_lines[:10]:
                        if line:
                            print(f"    {line[:100]}")  # 每行最多显示100字符
            except:
                pass
        
    except Exception as e:
        print(f"  提取数据时出错: {e}")
    
    # 处理概念列表：删除"智能点评"及其之前的所有元素（在返回前统一处理）
    if result.get('concepts') and isinstance(result['concepts'], list):
        try:
            if '智能点评' in result['concepts']:
                index = result['concepts'].index('智能点评')
                # 只保留"智能点评"后面的元素
                result['concepts'] = result['concepts'][index + 1:]
        except (ValueError, IndexError):
            pass  # 如果找不到或索引错误，保持原样
        
        # 如果concept里有'暂无概念'或'该公司暂无概念题材数据'，清空concept
        try:
            has_no_concept = False
            for concept in result['concepts']:
                if isinstance(concept, str):
                    if concept == '暂无概念' or concept.startswith('该公司暂无概念题材数据'):
                        has_no_concept = True
                        break
            if has_no_concept:
                result['concepts'] = []
        except (ValueError, IndexError, TypeError):
            pass  # 如果出错，保持原样
        
        # 过滤掉以"昨日"开头的概念
        try:
            result['concepts'] = [
                concept for concept in result['concepts']
                if not (isinstance(concept, str) and concept.startswith('昨日'))
            ]
        except (ValueError, IndexError, TypeError):
            pass  # 如果出错，保持原样
    
    # 处理板块列表：删除"地区"元素，以及从"经营范围"开始的所有内容（在返回前统一处理）
    if result.get('plates') and isinstance(result['plates'], list):
        try:
            if '地区' in result['plates']:
                result['plates'].remove('地区')
            # 删除从"经营范围"开始的所有内容
            if '经营范围' in result['plates']:
                index = result['plates'].index('经营范围')
                result['plates'] = result['plates'][:index]
            
            # 过滤掉以"昨日"开头的板块
            result['plates'] = [
                plate for plate in result['plates']
                if not (isinstance(plate, str) and plate.startswith('昨日'))
            ]
        except (ValueError, IndexError):
            pass  # 如果找不到或索引错误，保持原样
    
    return result

def get_stock_info_web(stock_code: str, driver: webdriver.Chrome) -> Dict[str, any]:
    """
    通过网页获取股票信息
    :param stock_code: 股票代码
    :param driver: Selenium WebDriver实例
    :return: 包含概念题材、所属行业、所属板块的字典
    """
    url = get_web_url(stock_code)
    
    for attempt in range(MAX_RETRIES):
        try:
            # 访问网页
            driver.get(url)
            
            # 等待页面加载
            time.sleep(PAGE_LOAD_WAIT)
            
            # 尝试等待特定元素加载（如果页面有特定标识）
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except TimeoutException:
                pass  # 如果超时，继续尝试提取数据
            
            # 提取数据
            result = extract_stock_info_from_page(driver, stock_code)
            
            # 如果获取到了任何数据，返回结果
            if result.get('industry') or result.get('concepts') or result.get('plates'):
                return result
            
            # 如果第一次尝试失败，再等待一下
            if attempt < MAX_RETRIES - 1:
                time.sleep(2)
                continue
            
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  获取 {stock_code} 时出错，{RETRY_DELAY}秒后重试... ({attempt + 1}/{MAX_RETRIES})")
                time.sleep(RETRY_DELAY)
                continue
            else:
                # 最后一次尝试失败，打印错误信息
                print(f"  获取 {stock_code} 失败: {type(e).__name__}: {str(e)[:100]}")
                break
    
    # 返回空结果
    return {
        'stock_code': stock_code,
        'name': '',
        'concepts': [],
        'industry': '',
        'plates': []
    }

def get_stock_name_dict() -> Dict[str, str]:
    """
    从本地CSV文件获取股票代码到名称的映射字典
    :return: 股票代码到名称的字典
    """
    stock_name_dict = {}
    
    if not os.path.exists(STOCK_LIST_CSV):
        print(f"警告：文件 '{STOCK_LIST_CSV}' 不存在，无法获取股票名称。")
        return stock_name_dict

    # 尝试多种编码方式
    encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig', 'gb18030']
    
    for encoding in encodings:
        try:
            with open(STOCK_LIST_CSV, 'r', encoding=encoding) as f:
                reader = csv.DictReader(f)
                # 读取每一行，提取证券代码和证券简称
                for row in reader:
                    code = row.get('证券代码', '').strip()
                    name = row.get('证券简称', '').strip()
                    if code and name:
                        # 标准化代码为6位
                        code = code.zfill(6)
                        stock_name_dict[code] = name

            if stock_name_dict:
                print(f"成功使用 {encoding} 编码读取文件，获取 {len(stock_name_dict)} 只股票的名称映射。")
            return stock_name_dict

        except UnicodeDecodeError:
            # 编码不匹配，尝试下一个
            continue
        except IOError as e:
            print(f"读取文件 '{STOCK_LIST_CSV}' 时出错: {e}")
            return {}
        except Exception as e:
            # 其他错误，也尝试下一个编码
            if encoding == encodings[-1]:
                # 如果是最后一个编码，输出错误信息
                print(f"解析文件 '{STOCK_LIST_CSV}' 时出错: {e}")
                return {}
            continue
    
    # 所有编码都失败了
    print(f"警告：无法使用任何编码方式读取文件 '{STOCK_LIST_CSV}'，无法获取股票名称。")
    return {}

def get_all_a_stock_codes() -> List[str]:
    """
    从本地CSV文件获取所有A股股票代码列表
    :return: 股票代码列表
    """
    print(f"正在从本地文件 '{STOCK_LIST_CSV}' 获取所有A股股票代码...")
    
    if not os.path.exists(STOCK_LIST_CSV):
        print(f"错误：文件 '{STOCK_LIST_CSV}' 不存在。")
        return []

    # 尝试多种编码方式
    encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig', 'gb18030']
    
    for encoding in encodings:
        try:
            stock_codes = []
            with open(STOCK_LIST_CSV, 'r', encoding=encoding) as f:
                reader = csv.reader(f)
                # 跳过表头
                next(reader, None)
                # 读取每一行，提取第一列（证券代码）
                for row in reader:
                    if row and row[0].strip():
                        code = row[0].strip()
                        stock_codes.append(code)

            print(f"成功使用 {encoding} 编码读取文件，获取 {len(stock_codes)} 只A股股票代码。")
            return stock_codes

        except UnicodeDecodeError:
            # 编码不匹配，尝试下一个
            continue
        except IOError as e:
            print(f"读取文件 '{STOCK_LIST_CSV}' 时出错: {e}")
            return []
        except Exception as e:
            # 其他错误，也尝试下一个编码
            if encoding == encodings[-1]:
                # 如果是最后一个编码，输出错误信息
                print(f"解析文件 '{STOCK_LIST_CSV}' 时出错: {e}")
                return []
            continue
    
    # 所有编码都失败了
    print(f"错误：无法使用任何编码方式读取文件 '{STOCK_LIST_CSV}'。")
    return []

def save_stock_info(all_stock_info: Dict, filename: str = OUTPUT_FILENAME):
    """保存股票信息到JSON文件（保存前从 concepts 剔除与涨停概念统计对齐的噪声标签）。"""
    try:
        noise = _noise_concept_names()
        sanitized: Dict = {}
        for k, v in all_stock_info.items():
            if isinstance(v, dict):
                sanitized[k] = _strip_noise_concepts_from_record(v, noise)
            else:
                sanitized[k] = v
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(sanitized, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"\n保存文件时出错: {e}")
        return False

def load_stock_info(filename: str = OUTPUT_FILENAME) -> Dict:
    """加载已有的股票信息JSON文件"""
    if not os.path.exists(filename):
        return {}
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"加载已有文件 '{filename}' 时出错: {e}，将从头开始")
        return {}

def main():
    """
    主函数，执行获取所有A股股票信息（概念题材、所属行业、所属板块）的任务
    """
    # 1. 获取股票名称映射字典
    stock_name_dict = get_stock_name_dict()
    
    # 2. 尝试加载已有的数据（断点续传）
    all_stock_info = load_stock_info()
    if all_stock_info:
        print(f"✓ 已加载 {len(all_stock_info)} 只股票的历史数据，将继续处理剩余股票")
        # 为已有数据填充股票名称（如果缺失）
        for code, info in all_stock_info.items():
            if 'name' not in info or not info.get('name'):
                code_normalized = str(code).zfill(6)
                if code_normalized in stock_name_dict:
                    info['name'] = stock_name_dict[code_normalized]
                elif code in stock_name_dict:
                    info['name'] = stock_name_dict[code]
        print()
    
    # 3. 获取所有股票代码
    stock_codes = get_all_a_stock_codes()
    if not stock_codes:
        print("未能获取到股票代码，程序终止。")
        return

    # 3.1. 过滤掉以5和1开头的代码（这些代码获取不到数据）
    original_count = len(stock_codes)
    stock_codes = [code for code in stock_codes if not (code.startswith('5') or code.startswith('1'))]
    filtered_count = original_count - len(stock_codes)
    if filtered_count > 0:
        print(f"已过滤掉 {filtered_count} 只以5或1开头的股票代码（无法获取数据）\n")

    # 4. 过滤掉已处理的股票
    remaining_codes = [code for code in stock_codes if code not in all_stock_info]
    total_count = len(stock_codes)
    remaining_count = len(remaining_codes)
    
    if remaining_count == 0:
        print(f"所有 {total_count} 只股票已处理完成！")
        return
    
    print(f"\n开始处理股票：")
    print(f"  总共: {total_count} 只")
    print(f"  已完成: {total_count - remaining_count} 只")
    print(f"  待处理: {remaining_count} 只")
    print(f"  每 {SAVE_INTERVAL} 只保存一次，避免数据丢失\n")
    
    consecutive_errors = 0
    
    # 5. 初始化浏览器（在循环外，复用浏览器）
    driver = None
    try:
        driver = init_driver()
    except Exception as e:
        print(f"初始化浏览器失败: {e}")
        return
    
    # 6. 遍历剩余股票代码
    try:
        for i, code in enumerate(remaining_codes, 1):
            # 每10个显示一次详细进度
            show_progress = (i % 10 == 0) or (i == remaining_count)
            
            current_total = total_count - remaining_count + i
            if show_progress:
                print(f"[{i}/{remaining_count}] ({current_total}/{total_count}) {code}...", end=' ', flush=True)
            else:
                print(f"[{i}/{remaining_count}] ({current_total}/{total_count}) {code}...", end=' ', flush=True)
            
            # 如果浏览器已关闭，重新初始化
            if driver is None:
                try:
                    driver = init_driver()
                except Exception as e:
                    consecutive_errors += 1
                    error_msg = str(e)[:50]
                    if show_progress:
                        print(f"✗ 启动浏览器失败: {error_msg}")
                    else:
                        print("✗")
                    continue
            
            # 获取股票信息
            try:
                info = get_stock_info_web(code, driver)
                # 从CSV文件中获取股票名称
                code_normalized = code.zfill(6)
                if code_normalized in stock_name_dict:
                    info['name'] = stock_name_dict[code_normalized]
                elif code in stock_name_dict:
                    info['name'] = stock_name_dict[code]
                all_stock_info[code] = info
                
                # 显示结果
                has_data = bool(info.get('industry') or info.get('concepts') or info.get('plates'))
                if has_data:
                    consecutive_errors = 0
                    if show_progress:
                        industry_str = info.get('industry', '无')
                        concepts_str = ', '.join(info.get('concepts', []))[:50] if info.get('concepts') else '无'
                        plates_str = ', '.join(info.get('plates', []))[:50] if info.get('plates') else '无'
                        print(f"✓ 行业:{industry_str} | 概念:{concepts_str} | 板块:{plates_str}")
                    else:
                        print("✓")
                else:
                    consecutive_errors += 1
                    if show_progress:
                        print(f"✗ 未获取到数据")
                    else:
                        print("✗")
                    
                    # 如果连续错误过多，增加等待时间
                    if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
                        wait_time = REQUEST_DELAY * 3
                        print(f"\n连续错误过多，等待 {wait_time} 秒后继续...")
                        time.sleep(wait_time)
                        consecutive_errors = 0
                
            except Exception as e:
                consecutive_errors += 1
                error_msg = str(e)[:50]
                if show_progress:
                    print(f"✗ 错误: {error_msg}")
                else:
                    print("✗")
                
                if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
                    wait_time = REQUEST_DELAY * 3
                    print(f"\n连续错误过多，等待 {wait_time} 秒后继续...")
                    time.sleep(wait_time)
                    consecutive_errors = 0
            
            # 定期保存，避免数据丢失（保存时关闭浏览器）
            if i % SAVE_INTERVAL == 0:
                # 关闭浏览器
                if driver is not None:
                    try:
                        driver.quit()
                        driver = None
                    except Exception:
                        pass
                
                # 保存数据
                if save_stock_info(all_stock_info):
                    print(f"\n[自动保存] 已保存 {len(all_stock_info)} 只股票的数据到 '{OUTPUT_FILENAME}'")
                    print(f"[自动保存] 浏览器已关闭，将在处理下一只股票时重新初始化\n")
            
            # 延时，避免请求过快
            if i < remaining_count:  # 最后一个不需要延时
                time.sleep(REQUEST_DELAY)

    finally:
        # 循环结束后关闭浏览器
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    # 6. 最终保存结果到JSON文件
    if save_stock_info(all_stock_info):
        print(f"\n任务完成！结果已成功保存到 '{OUTPUT_FILENAME}' 文件中。")
        
        # 统计信息
        success_count = sum(1 for info in all_stock_info.values() 
                          if info.get('industry') or info.get('concepts') or info.get('plates'))
        industry_count = sum(1 for info in all_stock_info.values() if info.get('industry'))
        concepts_count = sum(1 for info in all_stock_info.values() if info.get('concepts'))
        plates_count = sum(1 for info in all_stock_info.values() if info.get('plates'))
        
        print(f"\n统计信息:")
        print(f"  总共 {total_count} 只股票")
        print(f"  成功获取信息: {success_count} 只")
        print(f"  获取到行业信息: {industry_count} 只")
        print(f"  获取到概念题材: {concepts_count} 只")
        print(f"  获取到板块信息: {plates_count} 只")
        
        # 显示部分结果预览
        print(f"\n部分结果预览（前5个）:")
        preview_count = 0
        for code, info in all_stock_info.items():
            if info.get('industry') or info.get('concepts') or info.get('plates'):
                print(f"  {code}:")
                if info.get('industry'):
                    print(f"    行业: {info['industry']}")
                if info.get('concepts'):
                    print(f"    概念: {', '.join(info['concepts'][:5])}")
                if info.get('plates'):
                    print(f"    板块: {', '.join(info['plates'][:5])}")
                preview_count += 1
                if preview_count >= 5:
                    break
    else:
        print(f"\n警告：保存文件失败，但数据仍在内存中")

if __name__ == "__main__":
    main()
