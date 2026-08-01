#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多端口Web应用启动器
支持同时启动多个端口的Web服务
"""

import os
import sys
import threading
import time
import json
import urllib.request
import urllib.error
from flask import Flask, request, jsonify, render_template_string
from copyright_manager import CopyrightManager

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

def create_app(port):
    """创建Flask应用"""
    app = Flask(__name__)
    
    # 简易IP归属地缓存: { ip: (expire_epoch_seconds, location_str) }
    app._ip_loc_cache = {}

    def _is_private_ip(ip_str):
        if not ip_str:
            return True
        if ip_str.startswith('127.') or ip_str == '::1':
            return True
        if ip_str.startswith('10.'):
            return True
        if ip_str.startswith('192.168.'):
            return True
        if ip_str.startswith('172.'):
            try:
                second = int(ip_str.split('.')[1])
                return 16 <= second <= 31
            except Exception:
                return False
        return False

    def _parse_ip_api_response(data):
        """解析ip-api.com的返回数据"""
        try:
            country = data.get('country', '')
            region = data.get('regionName', '')
            city = data.get('city', '')
            isp = data.get('isp', '')
            return _format_chinese_location(country, region, city, isp)
        except Exception:
            return None

    def _parse_ipinfo_response(data):
        """解析ipinfo.io的返回数据"""
        try:
            country = data.get('country', '')
            region = data.get('region', '')
            city = data.get('city', '')
            isp = data.get('org', '')
            return _format_chinese_location(country, region, city, isp)
        except Exception:
            return None

    def _parse_ipapi_response(data):
        """解析ipapi.co的返回数据"""
        try:
            country = data.get('country_name', '')
            region = data.get('region', '')
            city = data.get('city', '')
            isp = data.get('org', '') or data.get('asn', '')
            return _format_chinese_location(country, region, city, isp)
        except Exception:
            return None

    def _format_chinese_location(country, region, city, isp):
        """格式化中文归属地信息"""
        try:
            # 构建中文归属地字符串
            parts = []
            
            # 国家
            if country:
                # 将英文国家名转换为中文
                country_map = {
                    'China': '中国', 'United States': '美国', 'Japan': '日本', 'South Korea': '韩国',
                    'Singapore': '新加坡', 'Hong Kong': '香港', 'Taiwan': '台湾', 'Germany': '德国',
                    'United Kingdom': '英国', 'France': '法国', 'Canada': '加拿大', 'Australia': '澳大利亚'
                }
                chinese_country = country_map.get(country, country)
                parts.append(chinese_country)
            
            # 省份/州
            if region:
                # 将英文省份名转换为中文
                region_map = {
                    # 直辖市
                    'Beijing': '北京', 'Shanghai': '上海', 'Tianjin': '天津', 'Chongqing': '重庆',
                    
                    # 省份
                    'Guangdong': '广东', 'Jiangsu': '江苏', 'Zhejiang': '浙江', 'Shandong': '山东',
                    'Fujian': '福建', 'Hunan': '湖南', 'Hubei': '湖北', 'Sichuan': '四川',
                    'Hebei': '河北', 'Henan': '河南', 'Anhui': '安徽', 'Jiangxi': '江西',
                    'Liaoning': '辽宁', 'Jilin': '吉林', 'Heilongjiang': '黑龙江', 'Shanxi': '山西',
                    'Shaanxi': '陕西', 'Gansu': '甘肃', 'Qinghai': '青海', 'Yunnan': '云南',
                    'Guizhou': '贵州', 'Guangxi': '广西', 'Hainan': '海南', 'Xinjiang': '新疆',
                    'Tibet': '西藏', 'Ningxia': '宁夏', 'Inner Mongolia': '内蒙古',
                    
                    # 其他可能的英文名称
                    'Guangdong Province': '广东', 'Jiangsu Province': '江苏', 'Zhejiang Province': '浙江',
                    'Shandong Province': '山东', 'Fujian Province': '福建', 'Hunan Province': '湖南',
                    'Hubei Province': '湖北', 'Sichuan Province': '四川', 'Hebei Province': '河北',
                    'Henan Province': '河南', 'Anhui Province': '安徽', 'Jiangxi Province': '江西',
                    'Liaoning Province': '辽宁', 'Jilin Province': '吉林', 'Heilongjiang Province': '黑龙江',
                    'Shanxi Province': '山西', 'Shaanxi Province': '陕西', 'Gansu Province': '甘肃',
                    'Qinghai Province': '青海', 'Yunnan Province': '云南', 'Guizhou Province': '贵州',
                    'Guangxi Province': '广西', 'Hainan Province': '海南', 'Xinjiang Province': '新疆',
                    'Tibet Province': '西藏', 'Ningxia Province': '宁夏', 'Inner Mongolia Province': '内蒙古'
                }
                chinese_region = region_map.get(region, region)
                parts.append(chinese_region)
            
            # 城市
            if city:
                # 将英文城市名转换为中文
                city_map = {
                    # 一线城市
                    'Beijing': '北京', 'Shanghai': '上海', 'Guangzhou': '广州', 'Shenzhen': '深圳',
                    'Tianjin': '天津', 'Chongqing': '重庆', 'Chengdu': '成都', 'Wuhan': '武汉',
                    'Hangzhou': '杭州', 'Nanjing': '南京', 'Suzhou': '苏州', 'Changsha': '长沙',
                    'Zhengzhou': '郑州', 'Xi\'an': '西安', 'Jinan': '济南', 'Qingdao': '青岛',
                    'Dalian': '大连', 'Shenyang': '沈阳', 'Harbin': '哈尔滨', 'Changchun': '长春',
                    
                    # 山东省城市
                    'Qingdao': '青岛', 'Jinan': '济南', 'Yantai': '烟台', 'Weifang': '潍坊',
                    'Jining': '济宁', 'Linyi': '临沂', 'Zibo': '淄博', 'Dezhou': '德州',
                    'Liaocheng': '聊城', 'Dongying': '东营', 'Weihai': '威海', 'Tai\'an': '泰安',
                    'Binzhou': '滨州', 'Zaozhuang': '枣庄', 'Rizhao': '日照', 'Laiwu': '莱芜',
                    
                    # 广东省城市
                    'Guangzhou': '广州', 'Shenzhen': '深圳', 'Dongguan': '东莞', 'Foshan': '佛山',
                    'Zhuhai': '珠海', 'Zhongshan': '中山', 'Jiangmen': '江门', 'Huizhou': '惠州',
                    'Shantou': '汕头', 'Zhanjiang': '湛江', 'Maoming': '茂名', 'Shaoguan': '韶关',
                    'Heyuan': '河源', 'Meizhou': '梅州', 'Chaozhou': '潮州', 'Jieyang': '揭阳',
                    'Yunfu': '云浮', 'Yangjiang': '阳江', 'Qingyuan': '清远', 'Shanwei': '汕尾',
                    
                    # 江苏省城市
                    'Nanjing': '南京', 'Suzhou': '苏州', 'Wuxi': '无锡', 'Changzhou': '常州',
                    'Zhenjiang': '镇江', 'Nantong': '南通', 'Yangzhou': '扬州', 'Taizhou': '泰州',
                    'Yancheng': '盐城', 'Huai\'an': '淮安', 'Suqian': '宿迁', 'Xuzhou': '徐州',
                    'Lianyungang': '连云港',
                    
                    # 浙江省城市
                    'Hangzhou': '杭州', 'Ningbo': '宁波', 'Wenzhou': '温州', 'Jiaxing': '嘉兴',
                    'Huzhou': '湖州', 'Shaoxing': '绍兴', 'Jinhua': '金华', 'Quzhou': '衢州',
                    'Zhoushan': '舟山', 'Taizhou': '台州', 'Lishui': '丽水',
                    
                    # 其他重要城市
                    'Kunming': '昆明', 'Guiyang': '贵阳', 'Nanning': '南宁', 'Haikou': '海口',
                    'Sanya': '三亚', 'Lhasa': '拉萨', 'Urumqi': '乌鲁木齐', 'Yinchuan': '银川',
                    'Xining': '西宁', 'Lanzhou': '兰州', 'Hefei': '合肥', 'Fuzhou': '福州',
                    'Xiamen': '厦门', 'Nanchang': '南昌', 'Shijiazhuang': '石家庄', 'Taiyuan': '太原',
                    'Hohhot': '呼和浩特', 'Yinchuan': '银川'
                }
                chinese_city = city_map.get(city, city)
                parts.append(chinese_city)
            
            # ISP运营商
            if isp:
                # 将英文运营商名转换为中文
                isp_map = {
                    # 中国电信
                    'China Telecom': '中国电信', 'CHINA TELECOM': '中国电信', 'ChinaTelecom': '中国电信',
                    'ChinaNet': '中国电信', 'CHINANET': '中国电信', 'China Net': '中国电信',
                    
                    # 中国联通
                    'China Unicom': '中国联通', 'CHINA UNICOM': '中国联通', 'ChinaUnicom': '中国联通',
                    'China Unicom Limited': '中国联通', 'CHINA UNICOM LIMITED': '中国联通',
                    'Unicom': '中国联通', 'UNICOM': '中国联通',
                    
                    # 中国移动
                    'China Mobile': '中国移动', 'CHINA MOBILE': '中国移动', 'ChinaMobile': '中国移动',
                    'China Mobile Communications': '中国移动', 'CHINA MOBILE COMMUNICATIONS': '中国移动',
                    'Mobile': '中国移动', 'MOBILE': '中国移动',
                    
                    # 中国网通
                    'China Netcom': '中国网通', 'CHINA NETCOM': '中国网通', 'ChinaNetcom': '中国网通',
                    'Netcom': '中国网通', 'NETCOM': '中国网通',
                    
                    # 教育网
                    'China Education and Research Network': '中国教育网', 'CERNET': '中国教育网',
                    'China Education Network': '中国教育网', 'Education Network': '中国教育网',
                    
                    # 云服务商
                    'Alibaba': '阿里云', 'Alibaba Cloud': '阿里云', 'ALIBABA CLOUD': '阿里云',
                    'Alibaba Group': '阿里云', 'Alibaba.com': '阿里云',
                    'Tencent': '腾讯云', 'Tencent Cloud': '腾讯云', 'TENCENT CLOUD': '腾讯云',
                    'Tencent Technology': '腾讯云', 'Tencent.com': '腾讯云',
                    'Baidu': '百度云', 'Baidu Cloud': '百度云', 'BAIDU CLOUD': '百度云',
                    'Baidu.com': '百度云', 'Baidu Technology': '百度云',
                    'Huawei': '华为云', 'Huawei Cloud': '华为云', 'HUAWEI CLOUD': '华为云',
                    'Huawei Technologies': '华为云', 'Huawei.com': '华为云',
                    
                    # 其他运营商
                    'China Railcom': '中国铁通', 'CHINA RAILCOM': '中国铁通',
                    'China Tietong': '中国铁通', 'Tietong': '中国铁通',
                    'China Satcom': '中国卫通', 'CHINA SATCOM': '中国卫通',
                    'China Broadcasting Network': '中国广电', 'CBN': '中国广电',
                    'China Tower': '中国铁塔', 'CHINA TOWER': '中国铁塔',
                    
                    # 国际运营商
                    'Verizon': '威瑞森', 'AT&T': '美国电话电报', 'T-Mobile': 'T-Mobile',
                    'Sprint': 'Sprint', 'Comcast': '康卡斯特', 'Charter': 'Charter',
                    'Cox': 'Cox', 'Time Warner': '时代华纳', 'CenturyLink': 'CenturyLink',
                    
                    # 其他常见ISP
                    'Internet Service Provider': '互联网服务提供商', 'ISP': '互联网服务提供商',
                    'Broadband': '宽带服务', 'Cable Internet': '有线互联网',
                    'DSL': '数字用户线路', 'Fiber': '光纤', 'Wireless': '无线网络'
                }
                chinese_isp = isp_map.get(isp, isp)
                parts.append(chinese_isp)
            
            # 组合结果
            if parts:
                return '-'.join(parts)
            else:
                return '未知归属地'
                
        except Exception:
            return '未知归属地'

    def get_ip_location(ip_str, ttl_seconds=86400):
        """查询公网IP归属地（带缓存），返回短字符串，如：中国-广东-深圳 电信"""
        try:
            if _is_private_ip(ip_str):
                return '本地/内网'
            now = time.time()
            cached = app._ip_loc_cache.get(ip_str)
            if cached and cached[0] > now:
                return cached[1]
            # 提供商列表（按可用性优先，优化中文显示）
            providers = [
                {
                    'url': f'https://ip-api.com/json/{ip_str}?lang=zh-CN&fields=status,country,regionName,city,isp',
                    'parse': lambda data: _parse_ip_api_response(data) if data.get('status') == 'success' else None
                },
                {
                    'url': f'https://ipinfo.io/{ip_str}/json',
                    'parse': lambda data: _parse_ipinfo_response(data) if data else None
                },
                {
                    'url': f'https://ipapi.co/{ip_str}/json/',
                    'parse': lambda data: _parse_ipapi_response(data) if data else None
                }
            ]
            location = None
            for p in providers:
                try:
                    req = urllib.request.Request(p['url'], headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=1.2) as resp:
                        text = resp.read().decode('utf-8', errors='ignore')
                        data = json.loads(text)
                        loc = p['parse'](data)
                        if loc:
                            location = loc
                            break
                except Exception:
                    continue
            if not location:
                location = '未知归属地'
            app._ip_loc_cache[ip_str] = (now + ttl_seconds, location)
            return location
        except Exception:
            return '未知归属地'

    # 暴露到app，供路由中调用
    app.get_ip_location = get_ip_location
    
    # 创建版权信息管理器
    copyright_manager = CopyrightManager()
    
    # 设置Web查询日志记录器
    try:
        import logging
        from datetime import datetime
        
        # 创建Web查询日志记录器
        web_logger = logging.getLogger(f'web_query_port_{port}')
        web_logger.setLevel(logging.INFO)
        
        # 创建Web查询日志文件处理器
        web_log_file = os.path.join(current_dir, 'logs', 'web_query.log')
        os.makedirs(os.path.dirname(web_log_file), exist_ok=True)
        
        web_handler = logging.FileHandler(web_log_file, encoding='utf-8')
        web_handler.setLevel(logging.INFO)
        
        # 设置日志格式
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        web_handler.setFormatter(formatter)
        
        # 添加处理器到Web日志记录器
        web_logger.addHandler(web_handler)
        web_logger.propagate = False  # 不传播到父记录器
        
        # 将Web日志记录器保存到应用上下文中
        app.web_query_logger = web_logger
        
    except Exception as e:
        print(f"设置Web查询日志失败: {e}")
        app.web_query_logger = None
    
    # 导入web_app中的路由和功能
    try:
        from web_app import init_qmt_adapter, KeyPriceCalculator
        from core.stock_analyzer import StockAnalyzer
        
        # 初始化QMT适配器
        if not init_qmt_adapter():
            print("错误: QMT适配器初始化失败")
            return None
        
        # 创建计算器实例
        calculator = KeyPriceCalculator()
        # 预热：首次加载全局股票信息管理器，避免第一次请求时的冷启动开销
        # 注意：这里不再预热，改为在全局范围内预热一次
        # try:
        #     _ = calculator.get_stock_name('000001')
        # except Exception:
        #     pass
        # 创建股票分析器（在QMT适配器初始化之后）
        stock_analyzer = StockAnalyzer()
        
        # 优先注册favicon路由，确保它能正确匹配
        @app.route('/favicon.ico')
        def favicon():
            """提供网站图标，直接使用ant.png文件"""
            from flask import send_file, make_response
            icon_path = os.path.join(current_dir, 'ant.png')
            print(f"[DEBUG] Favicon requested, path: {icon_path}, exists: {os.path.exists(icon_path)}")
            if os.path.exists(icon_path):
                response = make_response(send_file(icon_path, mimetype='image/png'))
                # 添加缓存控制头
                response.headers['Cache-Control'] = 'public, max-age=3600'
                response.headers['Content-Type'] = 'image/png'
                return response
            else:
                print(f"[ERROR] Favicon file not found at: {icon_path}")
                from flask import abort
                abort(404)
        
        @app.route('/test')
        def test_page():
            """测试页面"""
            with open('test_api.html', 'r', encoding='utf-8') as f:
                return f.read()
        
        @app.route('/debug')
        def debug_page():
            """调试页面"""
            with open('debug.html', 'r', encoding='utf-8') as f:
                return f.read()
        
        @app.route('/debug_api')
        def debug_api_page():
            """API调试页面"""
            with open('debug_api.html', 'r', encoding='utf-8') as f:
                return f.read()
        
        @app.route('/')
        def index():
            """主页"""
            # 获取当前端口的版权信息
            copyright_html = copyright_manager.get_copyright_html(port)
            
            return render_template_string('''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Permissions-Policy" content="microphone=(), camera=(), geolocation=()">
    <title>散户量化看盘工具</title>
    <link rel="shortcut icon" href="/favicon.ico" type="image/png">
    <link rel="icon" href="/favicon.ico" type="image/png" sizes="48x48">
    <link rel="icon" href="/favicon.ico" type="image/png" sizes="32x32">
    <link rel="icon" href="/favicon.ico" type="image/png" sizes="16x16">
    <link rel="apple-touch-icon" href="/favicon.ico" sizes="180x180">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        
        .header {
            background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            font-weight: 300;
        }
        
        .header p {
            font-size: 1.1em;
            opacity: 0.9;
        }
        
        .content {
            padding: 40px;
        }
        
        .input-group {
            margin-bottom: 30px;
            display: flex;
            align-items: center;
            gap: 15px;
        }
        
        .input-group label {
            font-weight: 600;
            color: #333;
            font-size: 1.1em;
            white-space: nowrap;
            flex-shrink: 0;
        }
        
        .input-group input {
            flex: 1;
            padding: 15px 20px;
            border: 2px solid #e1e5e9;
            border-radius: 10px;
            font-size: 1.1em;
            transition: all 0.3s ease;
        }
        
        .input-group input:focus {
            outline: none;
            border-color: #4facfe;
            box-shadow: 0 0 0 3px rgba(79, 172, 254, 0.1);
        }
        
        .btn {
            background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 10px;
            font-size: 1.1em;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            width: 100%;
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(79, 172, 254, 0.3);
        }
        
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        
        .loading {
            display: none;
            text-align: center;
            margin: 20px 0;
        }
        
        .spinner {
            border: 3px solid #f3f3f3;
            border-top: 3px solid #4facfe;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 10px;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .result {
            display: none;
            margin-top: 30px;
        }
        
        .stock-info {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            text-align: center;
        }
        
        .stock-info h3 {
            color: #333;
            margin-bottom: 5px;
        }
        
        .stock-info p {
            color: #666;
            font-size: 1.1em;
        }
        
        /* 分析结果样式 */
        .analysis-section {
            margin-top: 20px;
            background: white;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .analysis-tabs {
            display: flex;
            background: #f8f9fa;
            border-bottom: 1px solid #e1e5e9;
        }
        
        .tab-btn {
            flex: 1;
            padding: 15px 10px;
            border: none;
            background: transparent;
            cursor: pointer;
            font-size: 0.9em;
            font-weight: 600;
            color: #666;
            transition: all 0.3s ease;
            border-bottom: 3px solid transparent;
        }
        
        .tab-btn:hover {
            background: #e9ecef;
            color: #333;
        }
        
        .tab-btn.active {
            color: #4facfe;
            border-bottom-color: #4facfe;
            background: white;
        }
        
        .tab-content {
            padding: 20px;
        }
        
        .tab-content h3 {
            margin-bottom: 15px;
            color: #333;
            font-size: 1.2em;
        }
        
        .analysis-item {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 10px;
            border-left: 4px solid #4facfe;
        }
        
        .analysis-item.limit-up {
            border-left-color: #dc3545;
            background: #fff5f5;
        }
        
        .analysis-item.limit-down {
            border-left-color: #28a745;
            background: #f0fff4;
        }
        
        .analysis-item.normal {
            border-left-color: #6c757d;
            background: #f8f9fa;
        }
        
        .analysis-item-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        
        .analysis-item-time {
            font-weight: 600;
            color: #333;
        }
        
        .analysis-item-status {
            font-weight: 600;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.9em;
        }
        
        .analysis-item-status.limit-up {
            color: #dc3545;
            background: #f8d7da;
        }
        
        .analysis-item-status.limit-down {
            color: #28a745;
            background: #d4edda;
        }
        
        .analysis-item-status.normal {
            color: #6c757d;
            background: #e2e3e5;
        }
        
        .analysis-item-details {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 10px;
            font-size: 0.9em;
            color: #666;
        }
        
        .analysis-item-detail {
            text-align: center;
        }
        
        .analysis-item-detail-label {
            font-weight: 600;
            color: #333;
            margin-bottom: 2px;
        }
        
        .analysis-item-detail-value {
            color: #666;
        }
        
        .analysis-item-detail-value.increase {
            color: #dc3545;
            font-weight: 600;
        }
        
        .analysis-item-detail-value.decrease {
            color: #28a745;
            font-weight: 600;
        }
        
        /* 时间轴样式 */
        .timeline-container {
            margin: 20px 0;
        }
        
        .timeline-title {
            font-size: 18px;
            font-weight: bold;
            color: #333;
            margin-bottom: 20px;
            text-align: center;
        }
        
        .timeline {
            position: relative;
            padding: 20px 0;
        }
        
        .timeline::before {
            content: '';
            position: absolute;
            left: 50%;
            top: 0;
            bottom: 0;
            width: 2px;
            background: #e0e0e0;
            transform: translateX(-50%);
        }
        
        .timeline-node {
            position: relative;
            margin: 10px 0;
            display: flex;
            align-items: center;
            height: 40px;
        }
        
        .timeline-node::before {
            content: '';
            position: absolute;
            left: 50%;
            top: 50%;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            transform: translate(-50%, -50%);
            z-index: 2;
        }
        
        .timeline-node.limit-up::before {
            background: #ff5722;
            border: 2px solid #fff;
            box-shadow: 0 0 0 1px #ff5722;
        }
        
        .timeline-node.limit-down::before {
            background: #4caf50;
            border: 2px solid #fff;
            box-shadow: 0 0 0 1px #4caf50;
        }
        
        .timeline-node.normal::before {
            background: #2196f3;
            border: 2px solid #fff;
            box-shadow: 0 0 0 1px #2196f3;
        }
        
        .timeline-left {
            position: absolute;
            left: 0;
            width: 45%;
            text-align: right;
            padding-right: 20px;
        }
        
        .timeline-right {
            position: absolute;
            right: 0;
            width: 45%;
            text-align: left;
            padding-left: 20px;
        }
        
        .timeline-time {
            font-size: 12px;
            color: #666;
            font-weight: bold;
        }
        
        .timeline-status {
            font-size: 13px;
            font-weight: bold;
            margin-bottom: 2px;
        }
        
        .timeline-node.limit-up .timeline-status {
            color: #ff5722;
        }
        
        .timeline-node.limit-down .timeline-status {
            color: #4caf50;
        }
        
        .timeline-node.normal .timeline-status {
            color: #2196f3;
        }
        
        .timeline-quantity {
            font-size: 11px;
            color: #666;
        }
        
        .timeline-quantity.increase {
            color: #dc3545;
            font-weight: bold;
        }
        
        .timeline-quantity.decrease {
            color: #28a745;
            font-weight: bold;
        }
        
        /* 进度条样式 */
        .progress-container {
            width: 100%;
            height: 8px;
            background: #f0f0f0;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 4px;
            position: relative;
        }
        
        .progress-bar {
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s ease;
        }
        
        .progress-bar.original {
            background: #2196f3; /* 蓝色：原始封单量 */
        }
        
        .progress-bar.add {
            background: #dc3545; /* 红色：加单量 */
        }
        
        .progress-bar.withdraw {
            background: #28a745; /* 绿色：撤单量 */
        }
        
        .progress-bar.volume {
            background: #ffc107; /* 黄色：成交量吃掉的部分 */
        }
        
        .progress-label {
            font-size: 10px;
            color: #666;
            margin-top: 2px;
        }
        
        .behavior-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        
        .behavior-stat {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            border-left: 4px solid #4facfe;
        }
        
        .behavior-stat-label {
            font-weight: 600;
            color: #333;
            margin-bottom: 5px;
        }
        
        .behavior-stat-value {
            font-size: 1.5em;
            font-weight: 700;
            color: #4facfe;
        }
        
        .behavior-stat-value.limit-up-add {
            color: #dc3545;
        }
        
        .behavior-stat-value.limit-up-withdraw {
            color: #28a745;
        }
        
        .behavior-stat-value.limit-down-add {
            color: #dc3545;
        }
        
        .behavior-stat-value.limit-down-withdraw {
            color: #28a745;
        }
        
        .behavior-stat-value.accumulation {
            color: #007bff;
        }
        
        .behavior-stat-value.distribution {
            color: #6c757d;
        }
        
        .behavior-stat-value.lift {
            color: #fd7e14;
        }
        
        .behavior-stat-value.wash {
            color: #6f42c1;
        }
        
        .behavior-stat-value.sweep {
            color: #20c997;
        }
        
        .key-points {
            background: white;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .key-points h3 {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            margin: 0;
            font-weight: 300;
        }
        
        .point-item {
            padding: 20px;
            border-bottom: 1px solid #e1e5e9;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background-color 0.3s ease;
        }
        
        .point-item:hover {
            background-color: #f8f9fa;
        }
        
        .point-item:last-child {
            border-bottom: none;
        }
        
        .point-name {
            font-weight: 600;
            color: #333;
            font-size: 1.1em;
        }
        
        .point-price {
            font-size: 1.2em;
            font-weight: 700;
            color: #4facfe;
        }
        
        .point-price.current {
            color: #0078d4;
            font-weight: 800;
        }
        
        .point-price.limit-up {
            color: #dc3545;
            font-weight: 800;
        }
        
        .point-price.limit-down {
            color: #28a745;
            font-weight: 800;
        }
        
        .point-price.above-limit,
        .point-price.below-limit {
            text-decoration: line-through;
            opacity: 0.7;
        }
        
        .point-ratio {
            font-size: 0.9em;
            color: #666;
            margin-left: 10px;
        }
        
        .error {
            background: #fee;
            color: #c33;
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
            border-left: 4px solid #c33;
        }
        
        .footer {
            text-align: center;
            padding: 20px;
            color: #666;
            font-size: 0.9em;
        }
        
        @media (max-width: 600px) {
            .container {
                margin: 10px;
                border-radius: 15px;
            }
            
            .header {
                padding: 20px;
            }
            
            .header h1 {
                font-size: 2em;
            }
            
            .content {
                padding: 20px;
            }
            
            .input-group {
                gap: 10px;
            }
            
            .input-group label {
                font-size: 1.0em;
            }
            
            .input-group input {
                font-size: 1.0em;
                padding: 12px 15px;
                width: 120px;
                flex: none;
            }
            
            /* 日期输入框在移动端需要更宽 */
            .input-group input[type="date"] {
                width: 160px;
                min-width: 160px;
            }
            
            .point-item {
                padding: 15px;
                flex-direction: row;
                justify-content: space-between;
                align-items: center;
            }
            
            .point-name {
                font-size: 0.95em;
                flex: 1;
                margin-right: 10px;
            }
            
            .point-price {
                font-size: 1.0em;
                flex-shrink: 0;
            }
        }
        
        /* 响应式布局：手机端主力行为单列显示 */
        @media (max-width: 768px) {
            #mainForceList {
                grid-template-columns: 1fr !important;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>散户量化看盘工具</h1>
        </div>
        
        <div class="content">
            <div class="input-group">
                <label for="stockCode">股票代码</label>
                <input type="text" id="stockCode" placeholder="000001" maxlength="10">
            </div>
            
            <div class="input-group">
                <label for="analysisDate">分析日期</label>
                <input type="date" id="analysisDate">
            </div>
            
            <button class="btn" id="calculateBtn" onclick="calculateKeyPoints()">
                确定
            </button>
            
            <div class="loading" id="loading">
                <div class="spinner"></div>
                <p>正在计算中，请稍候...</p>
            </div>
            
            <div class="result" id="result">
                <div class="stock-info" id="stockInfo">
                    <h3 id="stockName">股票名称</h3>
                    <p id="stockCodeDisplay">股票代码</p>
                </div>
                
                <!-- 单股全面分析结果 -->
                <div class="analysis-section" id="analysisSection" style="display: none;">
                    <div class="analysis-tabs">
                        <button class="tab-btn active" onclick="showTab('mainForce')">主力行为</button>
                        <button class="tab-btn" onclick="showTab('limitDetails')">涨跌停板详情</button>
                        <button class="tab-btn" onclick="showTab('abnormalChanges')">盘口详情</button>
                        <button class="tab-btn" onclick="showTab('keyPoints')">关键价格点<sup style="color: #f44336; font-size: 0.6em; margin-left: 3px;">实时</sup></button>
                    </div>
                    
                    <div class="tab-content" id="mainForceTab" style="display: block;">
                        <h3>🎯 主力行为分析 <span style="color: #f44336; font-size: 0.7em; font-weight: normal;">注：本项目由AI程序生成，仅供娱乐。切勿据此操作，否则后果自负。</span></h3>
                        <div id="mainForceList" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;"></div>
                    </div>
                    
                    <div class="tab-content" id="limitDetailsTab" style="display: none;">
                        <h3>📈 涨跌停板详情</h3>
                        <div id="limitDetailsList"></div>
                    </div>
                    
                    <div class="tab-content" id="abnormalChangesTab" style="display: none;">
                        <h3>📊 盘口详情</h3>
                        <div id="abnormalChangesList"></div>
                    </div>
                    
                    <div class="tab-content" id="keyPointsTab" style="display: none;">
                        <h3>💰 关键价格点</h3>
                        <div id="keyPointsList"></div>
                    </div>
                </div>
            </div>
        
            <div class="error" id="error" style="display: none;"></div>
        </div>
        
        <div class="footer">
            <p>注：数据来源于市场公开信息，仅供参考，不作投资建议，若有误差，请以市场数据为准</p>
            <p>© 2025 蚂蚁量化乐园（公众号）</p>
        </div>
    </div>

    <script>
        // 页面加载完成后初始化
        document.addEventListener('DOMContentLoaded', function() {
            const stockCodeInput = document.getElementById('stockCode');
            const analysisDateInput = document.getElementById('analysisDate');
            
            // 设置默认分析日期
            function setDefaultAnalysisDate() {
                const now = new Date();
                const currentTime = now.getHours() * 60 + now.getMinutes(); // 转换为分钟
            const today = new Date().toISOString().split('T')[0];
                
                // 判断今天是否是交易日（简单判断：周一到周五）
                const dayOfWeek = now.getDay(); // 0=周日, 1=周一, ..., 6=周六
                const isTradingDay = dayOfWeek >= 1 && dayOfWeek <= 5;
                
                let defaultDate = today;
                
                if (!isTradingDay) {
                    // 非交易日：显示上一个交易日
                    // 简单处理：如果是周末，回退到周五
                    if (dayOfWeek === 0) { // 周日
                        const friday = new Date(now);
                        friday.setDate(now.getDate() - 2);
                        defaultDate = friday.toISOString().split('T')[0];
                    } else if (dayOfWeek === 6) { // 周六
                        const friday = new Date(now);
                        friday.setDate(now.getDate() - 1);
                        defaultDate = friday.toISOString().split('T')[0];
                    }
                } else {
                    // 交易日：判断时间
                    const tradingStartTime = 9 * 60 + 30; // 9:30 = 570分钟
                    
                    if (currentTime < tradingStartTime) {
                        // 9:30之前：显示上一个交易日
                        const yesterday = new Date(now);
                        yesterday.setDate(now.getDate() - 1);
                        
                        // 如果昨天是周末，继续往前找
                        let prevDay = yesterday;
                        while (prevDay.getDay() === 0 || prevDay.getDay() === 6) {
                            prevDay.setDate(prevDay.getDate() - 1);
                        }
                        
                        defaultDate = prevDay.toISOString().split('T')[0];
                    } else {
                        // 9:30之后：显示当天
                        defaultDate = today;
                    }
                }
                
                analysisDateInput.value = defaultDate;
            }
            
            // 设置默认日期
            setDefaultAnalysisDate();
            
            // 输入框回车事件
            stockCodeInput.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    calculateKeyPoints();
                }
            });
            
            // 输入框变化时清除结果
            stockCodeInput.addEventListener('input', function() {
                hideResult();
                hideError();
            });
        });
        
        function showLoading() {
            document.getElementById('loading').style.display = 'block';
            document.getElementById('calculateBtn').disabled = true;
            hideResult();
            hideError();
        }
        
        function hideLoading() {
            document.getElementById('loading').style.display = 'none';
            document.getElementById('calculateBtn').disabled = false;
        }
        
        function showResult() {
            document.getElementById('result').style.display = 'block';
            
            // 默认显示主力行为标签页
            showTab('mainForce');
        }
        
        function hideResult() {
            document.getElementById('result').style.display = 'none';
            // 恢复股票代码显示
            document.getElementById('stockCodeDisplay').style.display = 'block';
        }
        
        function showError(message) {
            const errorDiv = document.getElementById('error');
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
        }
        
        function hideError() {
            document.getElementById('error').style.display = 'none';
        }
        
        function mergeConcurrentNodes(details) {
            if (!details || details.length === 0) return [];
            
            const merged = [];
            const timeGroups = {};
            
            // 按时间分组
            details.forEach(detail => {
                const time = detail.time || '';
                if (!timeGroups[time]) {
                    timeGroups[time] = [];
                }
                timeGroups[time].push(detail);
            });
            
            // 处理每个时间组
            Object.keys(timeGroups).sort().forEach(time => {
                const group = timeGroups[time];
                
                // 查找封板/开板节点
                const sealNode = group.find(d => 
                    d.node_type && (d.node_type.includes('封板') || d.node_type.includes('开板'))
                );
                
                // 查找超阈值节点（成交量、加单、撤单）
                const thresholdNode = group.find(d => 
                    d.node_type && (d.node_type.includes('成交量超阈值') || 
                                   d.node_type.includes('加单') || 
                                   d.node_type.includes('撤单'))
                );
                
                if (sealNode && thresholdNode) {
                    // 合并封板/开板节点和超阈值节点
                    const mergedNode = {
                        ...sealNode,
                        // 使用超阈值节点的详细信息
                        volume_amount: thresholdNode.volume_amount || sealNode.volume_amount,
                        withdraw_amount: thresholdNode.withdraw_amount || sealNode.withdraw_amount,
                        add_amount: thresholdNode.add_amount || sealNode.add_amount,
                        final_amount: thresholdNode.final_amount || sealNode.final_amount,
                        // 合并显示文本
                        merged_info: {
                            has_add_amount: (thresholdNode.add_amount || 0) > 0,
                            add_amount: thresholdNode.add_amount || 0,
                            volume_amount: thresholdNode.volume_amount || 0,
                            final_amount: thresholdNode.final_amount || 0
                        }
                    };
                    merged.push(mergedNode);
                } else {
                    // 没有需要合并的节点，直接添加
                    group.forEach(node => merged.push(node));
                }
            });
            
            return merged;
        }
        
        async function getClientIpHint() {
            const providers = [
                { url: 'https://api.ipify.org?format=json', parse: async (r) => (await r.json()).ip },
                { url: 'https://ipinfo.io/json', parse: async (r) => (await r.json()).ip },
                { url: 'https://api.my-ip.io/ip.json', parse: async (r) => (await r.json()).ip },
            ];
            const timeoutMs = 2000;
            const tryOnce = async (url, parse) => {
                try {
                    const controller = new AbortController();
                    const timeout = setTimeout(() => controller.abort(), timeoutMs);
                    const resp = await fetch(url, { signal: controller.signal });
                    clearTimeout(timeout);
                    if (!resp.ok) return null;
                    const ip = await parse(resp);
                    if (typeof ip === 'string' && ip.match(/^\d{1,3}(?:\.\d{1,3}){3}$/)) return ip;
                    return null;
                } catch (_) {
                    return null;
                }
            };
            for (const p of providers) {
                const ip = await tryOnce(p.url, p.parse);
                if (ip) return ip;
            }
            return null;
        }

        async function calculateKeyPoints() {
            console.log('calculateKeyPoints 函数被调用');
            
            const stockCode = document.getElementById('stockCode').value.trim();
            const analysisDate = document.getElementById('analysisDate').value;
            
            console.log('股票代码:', stockCode, '分析日期:', analysisDate);
            
            if (!stockCode) {
                showError('请输入股票代码');
                return;
            }
            
            showLoading();
            const clientIpHint = await getClientIpHint();
            
            // 使用合并接口：单股分析 + 关键价格点
            const mergedPromise = fetch('/api/stock_analyze_and_calculate', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                body: JSON.stringify({
                    stock_code: stockCode,
                    analysis_date: analysisDate,
                    client_ip_hint: clientIpHint
                })
            });

            mergedPromise
            .then(response => {
                    console.log('合并接口响应状态:', response.status);
                return response.json();
            })
            .then(data => {
                    console.log('合并接口响应数据:', data);
                    
                hideLoading();
                
                if (data.success) {
                        console.log('合并查询成功，开始显示结果');
                        // 显示分析结果
                        displayAnalysisResult(data);
                    // 显示关键价格点
                    displayKeyPoints(data);
                } else {
                        console.log('合并查询失败:', data.error);
                        showError(data.error || '查询失败');
                }
            })
            .catch(error => {
                hideLoading();
                    showError(error.message || '网络错误，请检查网络连接');
                console.error('Error:', error);
            });
        }
        
        function displayAnalysisResult(data) {
            try {
                console.log('displayAnalysisResult 接收到的数据:', data);
                
                // 显示股票信息
                document.getElementById('stockName').textContent = `${data.stock_name} (${data.stock_code})`;
                document.getElementById('stockCodeDisplay').style.display = 'none';
                
                // 显示分析结果区域
                document.getElementById('analysisSection').style.display = 'block';
                
                // 检查是否有涨跌停板数据
                const limitDetails = data.analysis_result.limit_up_analysis || {};
                const hasLimitDetails = limitDetails.limit_details && limitDetails.limit_details.length > 0;
                
                console.log('涨跌停板数据:', limitDetails);
                console.log('是否有涨跌停板数据:', hasLimitDetails);
                
                // 控制涨跌停板详情标签页的显示
                const limitDetailsTab = document.querySelector('button[onclick="showTab(\\'limitDetails\\')"]');
                const limitDetailsContent = document.getElementById('limitDetailsTab');
                
                if (hasLimitDetails) {
                    limitDetailsTab.style.display = 'inline-block';
                    displayLimitDetails(limitDetails);
                } else {
                    limitDetailsTab.style.display = 'none';
                    limitDetailsContent.style.display = 'none';
                }
                
                // 显示盘口详情（K线图）
                displayAbnormalChanges(data.analysis_result.abnormal_changes, data.analysis_result.tick_data, data);
                
                // 显示主力行为
                displayMainForce(data.analysis_result.main_force_analysis);
                
                showResult();
            } catch (error) {
                console.error('displayAnalysisResult 错误:', error);
                throw error; // 重新抛出错误，让上层catch处理
            }
        }
        
        function displayKeyPoints(data) {
            // 显示关键价格点
            const keyPointsList = document.getElementById('keyPointsList');
            keyPointsList.innerHTML = '';
            
            if (data.key_points && data.key_points.length > 0) {
                data.key_points.forEach(point => {
                    const pointDiv = document.createElement('div');
                    pointDiv.className = 'point-item';
                    
                    const nameSpan = document.createElement('span');
                    nameSpan.className = 'point-name';
                    
                    // 检查是否是前高或前低，添加小字备注
                    if (point.name.startsWith('前高') || point.name.startsWith('前低')) {
                        nameSpan.innerHTML = `${point.name}<small style="font-size: 10px; color: #666; margin-left: 5px;">（近30个交易日内）</small>`;
                    } else {
                    nameSpan.textContent = point.name;
                    }
                    
                    const priceSpan = document.createElement('span');
                    priceSpan.className = 'point-price';
                    
                    // 根据价格点类型添加相应的CSS类
                    if (point.type === 'current') {
                        priceSpan.classList.add('current');
                    } else if (point.type === 'limit_up') {
                        priceSpan.classList.add('limit-up');
                    } else if (point.type === 'limit_down') {
                        priceSpan.classList.add('limit-down');
                    } else if (point.type === 'above_limit' || point.type === 'below_limit') {
                        priceSpan.classList.add(point.type.replace('_', '-'));
                    }
                    
                    priceSpan.textContent = `¥${point.price.toFixed(2)}`;
                    
                    if (point.cost_ratio) {
                        const ratioSpan = document.createElement('span');
                        ratioSpan.className = 'point-ratio';
                        ratioSpan.textContent = point.cost_ratio;
                        priceSpan.appendChild(ratioSpan);
                    }
                    
                    pointDiv.appendChild(nameSpan);
                    pointDiv.appendChild(priceSpan);
                    keyPointsList.appendChild(pointDiv);
                });
            } else {
                const noDataDiv = document.createElement('div');
                noDataDiv.className = 'point-item';
                noDataDiv.innerHTML = '<span style="color: #666;">暂无关键价格点数据</span>';
                keyPointsList.appendChild(noDataDiv);
            }
        }
        
        function displayLimitDetails(limitAnalysis) {
            try {
                console.log('displayLimitDetails 接收到的数据:', limitAnalysis);
                
                const limitDetailsList = document.getElementById('limitDetailsList');
                limitDetailsList.innerHTML = '';
                
                if (limitAnalysis && limitAnalysis.limit_details && limitAnalysis.limit_details.length > 0) {
                    console.log('涨跌停板详情数量:', limitAnalysis.limit_details.length);
                    
                    // 创建时间轴容器
                    const timelineDiv = document.createElement('div');
                    timelineDiv.className = 'timeline-container';
                    
                    // 添加时间轴标题
                    const timelineTitle = document.createElement('div');
                    timelineTitle.className = 'timeline-title';
                    timelineTitle.textContent = '涨跌停板时间轴';
                    timelineDiv.appendChild(timelineTitle);
                    
                    // 创建时间轴
                    const timeline = document.createElement('div');
                    timeline.className = 'timeline';
                    
                    // 计算全局最大总量作为100%基准
                    let globalMaxTotal = 0;
                    limitAnalysis.limit_details.forEach((detail) => {
                        // 对于新的节点数据结构
                        if (detail.node_type) {
                            const volumeAmount = detail.volume_amount || 0;
                            const withdrawAmount = detail.withdraw_amount || 0;
                            const addAmount = detail.add_amount || 0;
                            const finalAmount = detail.final_amount || 0;
                            const nodeTotal = Math.max(
                                volumeAmount + withdrawAmount + finalAmount,
                                volumeAmount + withdrawAmount + addAmount
                            );
                            globalMaxTotal = Math.max(globalMaxTotal, nodeTotal);
                        } else {
                            // 兼容旧的数据结构
                            const status = detail.status || '';
                            if (status.includes('封板')) {
                                if (status.includes('涨停')) {
                                    globalMaxTotal = Math.max(globalMaxTotal, detail.bid_vol || 0);
                                } else if (status.includes('跌停')) {
                                    globalMaxTotal = Math.max(globalMaxTotal, detail.ask_vol || 0);
                                }
                            }
                        }
                    });
                    
                    // 先对数据进行合并处理
                    const mergedDetails = mergeConcurrentNodes(limitAnalysis.limit_details);
                    
                    mergedDetails.forEach((detail, index) => {
                        console.log(`处理第${index}个详情:`, detail);
                        
                        // 创建时间轴节点
                        const nodeDiv = document.createElement('div');
                        nodeDiv.className = 'timeline-node';
                        
                        let time, price, nodeType, isLimitUp, isLimitDown;
                        
                        // 处理新的节点数据结构
                        if (detail.node_type) {
                            time = detail.time || '';
                            price = (detail.price || 0).toFixed(2);
                            nodeType = detail.node_type || '';
                            isLimitUp = detail.is_limit_up || false;
                            isLimitDown = detail.is_limit_down || false;
                            
                            // 根据涨跌停状态设置节点样式
                            if (isLimitUp) {
                                nodeDiv.classList.add('limit-up');
                            } else if (isLimitDown) {
                                nodeDiv.classList.add('limit-down');
                            } else {
                                nodeDiv.classList.add('normal');
                            }
                        } else {
                            // 兼容旧的数据结构
                            const status = detail.status || '';
                            time = detail.time || '';
                            price = (detail.price || 0).toFixed(2);
                            nodeType = status;
                            
                            if (status.includes('涨停') && !status.includes('开板')) {
                                nodeDiv.classList.add('limit-up');
                            } else if (status.includes('跌停') && !status.includes('开板')) {
                                nodeDiv.classList.add('limit-down');
                            } else {
                                nodeDiv.classList.add('normal');
                            }
                        }
                        
                        // 创建左侧内容（时间）
                        const leftDiv = document.createElement('div');
                        leftDiv.className = 'timeline-left';
                        
                        // 时间标签
                        const timeLabel = document.createElement('div');
                        timeLabel.className = 'timeline-time';
                        timeLabel.textContent = time;
                        
                        leftDiv.appendChild(timeLabel);
                        
                        // 创建右侧内容（状态+价格和数量）
                        const rightDiv = document.createElement('div');
                        rightDiv.className = 'timeline-right';
                        
                        // 状态标签（只对封板和开板显示价格）
                        const statusLabel = document.createElement('div');
                        statusLabel.className = 'timeline-status';
                        
                        // 只对封板和开板显示价格，其他节点不显示价格
                        if (nodeType.includes('封板') || nodeType.includes('开板')) {
                            statusLabel.textContent = `${nodeType}（¥${price}）`;
                        } else {
                            statusLabel.textContent = nodeType;
                        }
                        
                        // 数量标签（使用进度条可视化）
                        let quantityLabel = null;
                        
                        if (detail.node_type) {
                            // 新的节点数据结构
                            quantityLabel = document.createElement('div');
                            quantityLabel.className = 'timeline-quantity';
                            
                            const volumeAmount = detail.volume_amount || 0;
                            const withdrawAmount = detail.withdraw_amount || 0;
                            const addAmount = detail.add_amount || 0;
                            const finalAmount = detail.final_amount || 0;
                            
                            // 计算当前节点的总量
                            const currentNodeTotal = Math.max(
                                volumeAmount + withdrawAmount + finalAmount,
                                volumeAmount + withdrawAmount + addAmount
                            );
                            
                            // 计算当前节点进度条的总宽度（相对于全局最大总量）
                            const containerWidthPercent = globalMaxTotal > 0 ? (currentNodeTotal / globalMaxTotal) * 100 : 0;
                            
                            // 创建进度条容器
                            const progressContainer = document.createElement('div');
                            progressContainer.className = 'progress-container';
                            progressContainer.style.width = `${containerWidthPercent}%`;
                            progressContainer.style.height = '12px';
                            progressContainer.style.backgroundColor = '#f0f0f0';
                            progressContainer.style.borderRadius = '4px';
                            progressContainer.style.overflow = 'hidden';
                            
                            // 计算各部分在当前节点中的占比
                            const volumePercent = currentNodeTotal > 0 ? (volumeAmount / currentNodeTotal) * 100 : 0;
                            const withdrawPercent = currentNodeTotal > 0 ? (withdrawAmount / currentNodeTotal) * 100 : 0;
                            const finalPercent = currentNodeTotal > 0 ? (finalAmount / currentNodeTotal) * 100 : 0;
                            
                            // 添加最终量条（放在最前面）
                            if (finalAmount > 0) {
                                const finalBar = document.createElement('div');
                                finalBar.className = 'progress-bar final';
                                finalBar.style.width = `${finalPercent}%`;
                                finalBar.style.height = '100%';
                                finalBar.style.backgroundColor = '#2196f3';  // 统一使用蓝色
                                finalBar.style.float = 'left';
                                finalBar.style.display = 'inline-block';
                                finalBar.style.position = 'relative';
                                
                                // 如果有加单，在最终量条内部嵌套加单条（居中显示）
                                if (addAmount > 0) {
                                    const addBar = document.createElement('div');
                                    addBar.className = 'progress-bar add';
                                    // 计算加单量在最终量中的占比
                                    const addPercentInFinal = (addAmount / finalAmount) * 100;
                                    addBar.style.width = `${addPercentInFinal}%`;
                                    addBar.style.height = '100%';
                                    addBar.style.backgroundColor = '#f44336';  // 统一使用红色
                                    addBar.style.position = 'absolute';
                                    addBar.style.top = '0';
                                    addBar.style.left = '50%';  // 居中
                                    addBar.style.transform = 'translateX(-50%)';  // 精确居中
                                    finalBar.appendChild(addBar);
                                }
                                
                                progressContainer.appendChild(finalBar);
                            }
                            
                            // 如果有撤单，添加撤单条（放在最终量右侧）
                            if (withdrawAmount > 0) {
                                const withdrawBar = document.createElement('div');
                                withdrawBar.className = 'progress-bar withdraw';
                                withdrawBar.style.width = `${withdrawPercent}%`;
                                withdrawBar.style.height = '100%';
                                withdrawBar.style.backgroundColor = '#000000';  // 黑色
                                withdrawBar.style.float = 'left';
                                withdrawBar.style.display = 'inline-block';
                                progressContainer.appendChild(withdrawBar);
                            }
                            
                            // 如果有成交量，添加成交量条（放在最后）
                            if (volumeAmount > 0) {
                                const volumeBar = document.createElement('div');
                                volumeBar.className = 'progress-bar volume';
                                volumeBar.style.width = `${volumePercent}%`;
                                volumeBar.style.height = '100%';
                                volumeBar.style.backgroundColor = '#ffc107';  // 黄色
                                volumeBar.style.float = 'left';
                                volumeBar.style.display = 'inline-block';
                                progressContainer.appendChild(volumeBar);
                            }
                            
                            quantityLabel.appendChild(progressContainer);
                            
                            // 创建标签
                            const progressLabel = document.createElement('div');
                            progressLabel.className = 'progress-label';
                            
                            let labelText = '';
                            
                            // 检查是否是合并的节点
                            if (detail.merged_info) {
                                const mergedInfo = detail.merged_info;
                                if (isLimitUp) {
                                    labelText = `买一量: ${mergedInfo.final_amount}`;
                                    if (mergedInfo.has_add_amount) labelText += ` (含加单: ${mergedInfo.add_amount})`;
                                    labelText += ` 成交量: ${mergedInfo.volume_amount}`;
                                } else {
                                    labelText = `卖一量: ${mergedInfo.final_amount}`;
                                    if (mergedInfo.has_add_amount) labelText += ` (含加单: ${mergedInfo.add_amount})`;
                                    labelText += ` 成交量: ${mergedInfo.volume_amount}`;
                                }
                            } else {
                                // 原有逻辑
                                if (isLimitUp) {
                                    labelText = `买一量: ${finalAmount}`;
                                    if (addAmount > 0) labelText += ` (含加单: ${addAmount})`;
                                    if (withdrawAmount > 0) labelText += ` 撤单: ${withdrawAmount}`;
                                    labelText += ` 成交量: ${volumeAmount}`;
                                } else {
                                    labelText = `卖一量: ${finalAmount}`;
                                    if (addAmount > 0) labelText += ` (含加单: ${addAmount})`;
                                    if (withdrawAmount > 0) labelText += ` 撤单: ${withdrawAmount}`;
                                    labelText += ` 成交量: ${volumeAmount}`;
                                }
                            }
                            
                            progressLabel.textContent = labelText;
                            quantityLabel.appendChild(progressLabel);
                            
                        } else if (status.includes('封板') || status.includes('开板')) {
                            quantityLabel = document.createElement('div');
                            quantityLabel.className = 'timeline-quantity';
                            
                            if (status.includes('涨停')) {
                                const bidAskVol = status.includes('开板') ? (detail.bid_vol || 0) : (detail.bid_vol || 0);
                                const label = status.includes('开板') ? '买一量' : '买一量';
                                
                                // 创建进度条
                                const progressContainer = document.createElement('div');
                                progressContainer.className = 'progress-container';
                                
                                const progressBar = document.createElement('div');
                                progressBar.className = 'progress-bar original';
                                const widthPercent = maxAmount > 0 ? (bidAskVol / maxAmount) * 100 : 0;
                                progressBar.style.width = `${widthPercent}%`;
                                
                                progressContainer.appendChild(progressBar);
                                quantityLabel.appendChild(progressContainer);
                                
                                const progressLabel = document.createElement('div');
                                progressLabel.className = 'progress-label';
                                
                                // 调试信息
                                console.log(`涨停进度条调试: status=${status}, bidAskVol=${bidAskVol}, detail.bid_vol=${detail.bid_vol}, detail.ask_vol=${detail.ask_vol}`);
                                
                                // 如果是开板，在买一量前加上成交量
                                let labelText = `${label}: ${bidAskVol}`;
                                if (status.includes('开板')) {
                                    const volume = detail.volume_change || 0;
                                    labelText = `成交量: ${volume} ${label}: ${bidAskVol}`;
                                }
                                
                                progressLabel.textContent = labelText;
                                console.log(`涨停封板标签设置: ${labelText}`);
                                quantityLabel.appendChild(progressLabel);
                                
                            } else if (status.includes('跌停')) {
                                const bidAskVol = status.includes('开板') ? (detail.ask_vol || 0) : (detail.ask_vol || 0);
                                const label = status.includes('开板') ? '卖一量' : '卖一量';
                                
                                // 创建进度条
                                const progressContainer = document.createElement('div');
                                progressContainer.className = 'progress-container';
                                
                                const progressBar = document.createElement('div');
                                progressBar.className = 'progress-bar original';
                                const widthPercent = maxAmount > 0 ? (bidAskVol / maxAmount) * 100 : 0;
                                progressBar.style.width = `${widthPercent}%`;
                                
                                progressContainer.appendChild(progressBar);
                                quantityLabel.appendChild(progressContainer);
                                
                                const progressLabel = document.createElement('div');
                                progressLabel.className = 'progress-label';
                                
                                // 调试信息
                                console.log(`跌停进度条调试: status=${status}, bidAskVol=${bidAskVol}, detail.bid_vol=${detail.bid_vol}, detail.ask_vol=${detail.ask_vol}`);
                                
                                // 如果是开板，在卖一量前加上成交量
                                let labelText = `${label}: ${bidAskVol}`;
                                if (status.includes('开板')) {
                                    const volume = detail.volume_change || 0;
                                    labelText = `成交量: ${volume} ${label}: ${bidAskVol}`;
                                }
                                
                                progressLabel.textContent = labelText;
                                console.log(`成交活跃标签设置: ${labelText}`);
                                quantityLabel.appendChild(progressLabel);
                            }
                        } else if (status.includes('成交活跃')) {
                            if (detail.volume_change !== undefined) {
                                quantityLabel = document.createElement('div');
                                quantityLabel.className = 'timeline-quantity';
                                
                                // 创建进度条显示成交量吃掉封单量
                                const progressContainer = document.createElement('div');
                                progressContainer.className = 'progress-container';
                                
                                const volumeChange = Math.abs(detail.volume_change || 0);
                                const originalVol = volumeChange; // 成交活跃时的原始封单量就是成交量
                                const totalVol = originalVol;
                                
                                const totalWidthPercent = maxVol > 0 ? (totalVol / maxVol) * 100 : 0;
                                const volumePercent = 100; // 成交量占100%
                                
                                // 成交量吃掉的部分用黄色表示
                                const volumeBar = document.createElement('div');
                                volumeBar.className = 'progress-bar volume';
                                volumeBar.style.width = `${totalWidthPercent}%`;
                                
                                progressContainer.appendChild(volumeBar);
                                quantityLabel.appendChild(progressContainer);
                                
                                const progressLabel = document.createElement('div');
                                progressLabel.className = 'progress-label';
                                
                                // 根据涨停/跌停状态添加相应的量
                                let labelText = `成交量: ${detail.volume_change}`;
                                if (status.includes('涨停成交活跃')) {
                                    const bidVol = detail.bid_vol || 0;
                                    labelText += ` 买一量: ${bidVol}`;
                                } else if (status.includes('跌停成交活跃')) {
                                    const askVol = detail.ask_vol || 0;
                                    labelText += ` 卖一量: ${askVol}`;
                                }
                                
                                progressLabel.textContent = labelText;
                                quantityLabel.appendChild(progressLabel);
                            }
                        } else if (status.includes('加单') || status.includes('撤单')) {
                            let addWithdrawVol = 0;
                            let originalVol = 0;
                            
                            if (status.includes('涨停加单')) {
                                addWithdrawVol = detail.limit_up_add || 0;
                                // 加单后的封单量 = 加单前的封单量 + 加单量
                                // 所以加单前的封单量 = 加单后的封单量 - 加单量
                                // 加单后的封单量可以通过 bid_vol_change 推算
                                const currentVol = Math.abs(detail.bid_vol_change || 0);
                                originalVol = Math.max(0, currentVol - addWithdrawVol);
                            } else if (status.includes('涨停撤单')) {
                                addWithdrawVol = detail.limit_up_withdraw || 0;
                                // 撤单后的封单量 = 撤单前的封单量 - 撤单量
                                // 所以撤单前的封单量 = 撤单后的封单量 + 撤单量
                                const currentVol = Math.abs(detail.bid_vol_change || 0);
                                originalVol = currentVol + addWithdrawVol;
                            } else if (status.includes('跌停加单')) {
                                addWithdrawVol = detail.limit_down_add || 0;
                                const currentVol = Math.abs(detail.ask_vol_change || 0);
                                originalVol = Math.max(0, currentVol - addWithdrawVol);
                            } else if (status.includes('跌停撤单')) {
                                addWithdrawVol = detail.limit_down_withdraw || 0;
                                const currentVol = Math.abs(detail.ask_vol_change || 0);
                                originalVol = currentVol + addWithdrawVol;
                            }
                            
                            if (addWithdrawVol > 0) {
                                quantityLabel = document.createElement('div');
                                quantityLabel.className = 'timeline-quantity';
                                
                                // 创建进度条
                                const progressContainer = document.createElement('div');
                                progressContainer.className = 'progress-container';
                                
                                const totalVol = originalVol + addWithdrawVol;
                                const totalWidthPercent = maxVol > 0 ? (totalVol / maxVol) * 100 : 0;
                                const originalPercent = totalVol > 0 ? (originalVol / totalVol) * 100 : 0;
                                const addWithdrawPercent = totalVol > 0 ? (addWithdrawVol / totalVol) * 100 : 0;
                                
                                // 调试信息
                                console.log(`${status}: detail.bid_vol=${detail.bid_vol}, detail.ask_vol=${detail.ask_vol}`);
                                console.log(`originalVol=${originalVol}, addWithdrawVol=${addWithdrawVol}, totalVol=${totalVol}, maxVol=${maxVol}`);
                                console.log(`totalWidthPercent=${totalWidthPercent}, originalPercent=${originalPercent}, addWithdrawPercent=${addWithdrawPercent}`);
                                
                                if (status.includes('撤单')) {
                                    // 撤单：左侧绿色（撤单量）+ 右侧蓝色（剩余量），右侧留空
                                    const withdrawBar = document.createElement('div');
                                    withdrawBar.className = 'progress-bar withdraw';
                                    withdrawBar.style.width = `${(addWithdrawPercent * totalWidthPercent / 100)}%`;
                                    withdrawBar.style.float = 'left';
                                    
                                    const originalBar = document.createElement('div');
                                    originalBar.className = 'progress-bar original';
                                    originalBar.style.width = `${(originalPercent * totalWidthPercent / 100)}%`;
                                    originalBar.style.float = 'left';
                                    
                                    progressContainer.appendChild(withdrawBar);
                                    progressContainer.appendChild(originalBar);
                                } else {
                                    // 加单：左侧蓝色（原始量）+ 右侧红色（加单量）
                                    const originalBar = document.createElement('div');
                                    originalBar.className = 'progress-bar original';
                                    originalBar.style.width = `${(originalPercent * totalWidthPercent / 100)}%`;
                                    originalBar.style.float = 'left';
                                    
                                    const addBar = document.createElement('div');
                                    addBar.className = 'progress-bar add';
                                    addBar.style.width = `${(addWithdrawPercent * totalWidthPercent / 100)}%`;
                                    addBar.style.float = 'left';
                                    
                                    progressContainer.appendChild(originalBar);
                                    progressContainer.appendChild(addBar);
                                }
                                
                                quantityLabel.appendChild(progressContainer);
                                
                                const progressLabel = document.createElement('div');
                                progressLabel.className = 'progress-label';
                                const addWithdrawText = status.includes('撤单') ? `-${addWithdrawVol}` : `+${addWithdrawVol}`;
                                
                                // 根据涨停/跌停状态添加相应的量
                                let labelText = `加撤单: ${addWithdrawText}`;
                                if (status.includes('涨停加单') || status.includes('涨停撤单')) {
                                    // 使用计算出的当前封单量
                                    const currentVol = originalVol + (status.includes('加单') ? addWithdrawVol : -addWithdrawVol);
                                    labelText += ` 买一量: ${currentVol}`;
                                } else if (status.includes('跌停加单') || status.includes('跌停撤单')) {
                                    // 使用计算出的当前封单量
                                    const currentVol = originalVol + (status.includes('加单') ? addWithdrawVol : -addWithdrawVol);
                                    labelText += ` 卖一量: ${currentVol}`;
                                }
                                
                                progressLabel.textContent = labelText;
                                quantityLabel.appendChild(progressLabel);
                            }
                        }
                        
                        rightDiv.appendChild(statusLabel);
                        if (quantityLabel) {
                            rightDiv.appendChild(quantityLabel);
                        }
                        
                        // 组装节点内容
                        nodeDiv.appendChild(leftDiv);
                        nodeDiv.appendChild(rightDiv);
                        timeline.appendChild(nodeDiv);
                    });
                    
                    timelineDiv.appendChild(timeline);
                    limitDetailsList.appendChild(timelineDiv);
                } else {
                    const noDataDiv = document.createElement('div');
                    noDataDiv.className = 'analysis-item';
                    noDataDiv.innerHTML = '<span style="color: #666;">暂无涨跌停板数据</span>';
                    limitDetailsList.appendChild(noDataDiv);
                }
            } catch (error) {
                console.error('displayLimitDetails 错误:', error);
                throw error;
            }
        }
        
        function displayAbnormalChanges(abnormalChanges, tickData, data) {
            const abnormalChangesList = document.getElementById('abnormalChangesList');
            abnormalChangesList.innerHTML = '';
            
            if (tickData && tickData.length > 0) {
                // 创建K线图容器
                const klineContainer = document.createElement('div');
                klineContainer.style.width = '100%';
                klineContainer.style.marginBottom = '20px';
                
                // 创建K线图
                const klineChartContainer = document.createElement('div');
                klineChartContainer.style.width = '100%';
                klineChartContainer.style.height = '300px';
                klineChartContainer.style.marginBottom = '10px';
                klineChartContainer.style.border = '1px solid #e0e0e0';
                klineChartContainer.style.borderRadius = '8px';
                klineChartContainer.style.padding = '10px';
                
                const klineCanvas = document.createElement('canvas');
                klineCanvas.id = 'klineChart';
                klineCanvas.style.width = '100%';
                klineCanvas.style.height = '100%';
                klineChartContainer.appendChild(klineCanvas);
                
                // 创建成交量图
                const volumeChartContainer = document.createElement('div');
                volumeChartContainer.style.width = '100%';
                volumeChartContainer.style.height = '180px';
                volumeChartContainer.style.border = '1px solid #e0e0e0';
                volumeChartContainer.style.borderRadius = '8px';
                volumeChartContainer.style.padding = '10px';
                
                const volumeCanvas = document.createElement('canvas');
                volumeCanvas.id = 'volumeChart';
                volumeCanvas.style.width = '100%';
                volumeCanvas.style.height = '100%';
                volumeChartContainer.appendChild(volumeCanvas);
                
                // 创建买一卖一量图
                const bidAskChartContainer = document.createElement('div');
                bidAskChartContainer.style.width = '100%';
                bidAskChartContainer.style.height = '220px';
                bidAskChartContainer.style.border = '1px solid #e0e0e0';
                bidAskChartContainer.style.borderRadius = '8px';
                bidAskChartContainer.style.padding = '10px';
                bidAskChartContainer.style.marginTop = '10px';
                
                // 添加买一卖一量图标题
                const bidAskTitle = document.createElement('div');
                bidAskTitle.style.fontSize = '14px';
                bidAskTitle.style.fontWeight = 'bold';
                bidAskTitle.style.color = '#333';
                bidAskTitle.style.marginBottom = '5px';
                bidAskTitle.style.textAlign = 'center';
                bidAskTitle.textContent = '买一卖一量图';
                bidAskChartContainer.appendChild(bidAskTitle);
                
                // 添加图例说明
                const bidAskLegend = document.createElement('div');
                bidAskLegend.style.fontSize = '10px';
                bidAskLegend.style.color = '#666';
                bidAskLegend.style.marginBottom = '10px';
                bidAskLegend.style.textAlign = 'center';
                bidAskLegend.style.display = 'flex';
                bidAskLegend.style.justifyContent = 'center';
                bidAskLegend.style.gap = '15px';
                bidAskLegend.style.flexWrap = 'wrap';
                bidAskLegend.innerHTML = `
                    <span style="color: red;">● 红色：买一量 > 卖一量</span>
                    <span style="color: green;">● 绿色：买一量 < 卖一量</span>
                `;
                bidAskChartContainer.appendChild(bidAskLegend);
                
                console.log('[DEBUG] 创建买一量图表容器');
                
                const bidAskCanvas = document.createElement('canvas');
                bidAskCanvas.id = 'bidAskChart';
                bidAskCanvas.style.width = '100%';
                bidAskCanvas.style.height = '100%';
                bidAskChartContainer.appendChild(bidAskCanvas);
                
                console.log('[DEBUG] Canvas创建完成，ID:', bidAskCanvas.id);
                console.log('[DEBUG] Canvas尺寸:', bidAskCanvas.width, 'x', bidAskCanvas.height);
                
                // 准备K线图数据（传入完整响应，便于识别ST/市场类型）
                const chartData = prepareTickKlineData(
                    tickData,
                    abnormalChanges,
                    { analysis_result: data.analysis_result, stock_code: data.stock_code, stock_name: data.stock_name }
                );
                
                // 添加调试信息
                console.log('[DEBUG] chartData:', chartData);
                console.log('[DEBUG] klineData datasets:', chartData.klineData.datasets);
                console.log('[DEBUG] 第一个数据集数据点数量:', chartData.klineData.datasets[0].data.length);
                if (chartData.klineData.datasets[0].data.length > 0) {
                    console.log('[DEBUG] 第一个数据点:', chartData.klineData.datasets[0].data[0]);
                }
                
                // 调试买一卖一量数据
                console.log('[DEBUG] bidAskData:', chartData.bidAskData);
                console.log('[DEBUG] 买一量数据集:', chartData.bidAskData.datasets[0]);
                console.log('[DEBUG] 卖一量数据集:', chartData.bidAskData.datasets[1]);
                if (chartData.bidAskData.datasets[0].data.length > 0) {
                    console.log('[DEBUG] 第一个买一量数据点:', chartData.bidAskData.datasets[0].data[0]);
                    console.log('[DEBUG] 第一个卖一量数据点:', chartData.bidAskData.datasets[1].data[0]);
                    
                    // 打印前5个原始数据点
                    console.log('[DEBUG] 前5个原始买一量数据:');
                    for (let i = 0; i < Math.min(5, chartData.bidAskData.datasets[0].data.length); i++) {
                        const point = chartData.bidAskData.datasets[0].data[i];
                        console.log(`  [${i}] bid_vol: ${point.bid_vol}, time: ${point.time}`);
                    }
                    
                    console.log('[DEBUG] 前5个原始卖一量数据:');
                    for (let i = 0; i < Math.min(5, chartData.bidAskData.datasets[1].data.length); i++) {
                        const point = chartData.bidAskData.datasets[1].data[i];
                        console.log(`  [${i}] ask_vol: ${point.ask_vol}, time: ${point.time}`);
                    }
                }
                
                // 获取价格区间信息
                const lastClose = chartData.lastClose || 0;
                const limitUpPrice = chartData.limitUpPrice || lastClose * 1.1;
                const limitDownPrice = chartData.limitDownPrice || lastClose * 0.9;
                
                // 创建K线图数据（使用labels数组，跟买一量图一样）
                const klineLabels = [];
                const klinePrices = [];
                
                if (chartData.klineData.datasets[0].data.length > 0) {
                    chartData.klineData.datasets[0].data.forEach(point => {
                        klineLabels.push(point.time);
                        klinePrices.push(point.close);
                    });
                }
                
                console.log('[DEBUG] K线图labels数量:', klineLabels.length);
                if (klineLabels.length > 0) {
                    console.log('[DEBUG] K线图时间范围:', klineLabels[0], '到', klineLabels[klineLabels.length - 1]);
                } else {
                    console.log('[DEBUG] K线图时间范围: 无数据');
                }
                
                // 创建成交量图数据（使用labels数组，跟买一量图一样）
                const volumeLabels = [];
                const volumeData = [];
                
                if (chartData.volumeData.datasets[0].data.length > 0) {
                    chartData.volumeData.datasets[0].data.forEach(point => {
                        volumeLabels.push(point.time);
                        volumeData.push(point.volume);
                    });
                }
                
                console.log('[DEBUG] 成交量图labels数量:', volumeLabels.length);
                if (volumeLabels.length > 0) {
                    console.log('[DEBUG] 成交量图时间范围:', volumeLabels[0], '到', volumeLabels[volumeLabels.length - 1]);
                } else {
                    console.log('[DEBUG] 成交量图时间范围: 无数据');
                }
                
                // 检查Chart.js是否已加载
                if (typeof Chart === 'undefined') {
                    console.error('[错误] Chart.js未加载，无法创建图表');
                    klineChartContainer.innerHTML = '<div style="padding: 20px; text-align: center; color: #f44336;">⚠️ Chart.js库加载失败，请检查网络连接或刷新页面</div>';
                    return;
                }
                
                // 创建K线图
                const klineCtx = klineCanvas.getContext('2d');
                klineChartInstance = new Chart(klineCtx, {
                    type: 'line',
                    data: {
                        labels: klineLabels,
                        datasets: [
                            {
                                label: '最新价',
                                data: klinePrices,
                                borderColor: '#26a69a',
                                backgroundColor: 'rgba(38, 166, 154, 0.1)',
                                borderWidth: 1,
                                fill: false,
                                tension: 0.1,
                                pointRadius: 1,
                                pointHoverRadius: 3
                            },
                            {
                                label: '涨停板',
                                data: klineLabels.map(() => limitUpPrice),
                                borderColor: '#f44336',
                                backgroundColor: 'transparent',
                                borderWidth: 1,
                                borderDash: [5, 5],
                                fill: false,
                                pointRadius: 0,
                                pointHoverRadius: 0
                            },
                            {
                                label: '昨收盘',
                                data: klineLabels.map(() => lastClose),
                                borderColor: '#2196F3',
                                backgroundColor: 'transparent',
                                borderWidth: 1,
                                borderDash: [5, 5],
                                fill: false,
                                pointRadius: 0,
                                pointHoverRadius: 0
                            },
                            {
                                label: '跌停板',
                                data: klineLabels.map(() => limitDownPrice),
                                borderColor: '#4caf50',
                                backgroundColor: 'transparent',
                                borderWidth: 1,
                                borderDash: [5, 5],
                                fill: false,
                                pointRadius: 0,
                                pointHoverRadius: 0
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            title: {
                                display: true,
                                text: 'K线图',
                                font: {
                                    size: 16,
                                    weight: 'bold'
                                }
                            },
                            legend: {
                                display: false
                            },
                            tooltip: {
                                callbacks: {
                                    title: function(context) {
                                        return context[0].label;
                                    },
                                    label: function(context) {
                                        return `最新价: ¥${context.parsed.y.toFixed(2)}`;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                title: {
                                    display: false
                                },
                                ticks: {
                                    maxTicksLimit: 24
                                }
                            },
                            y: {
                                title: {
                                    display: true,
                                    text: '价格 (¥)'
                                },
                                min: limitDownPrice * 0.98,  // 跌停板下方留2%空间
                                max: limitUpPrice * 1.02,   // 涨停板上方留2%空间
                                beginAtZero: false
                            }
                        },
                        onClick: function(event, elements) {
                            if (elements.length > 0) {
                                const point = elements[0].element.$context.raw;
                                highlightVolumeBar(point.time);
                            }
                        }
                    }
                });
                
                // 创建成交量图（Chart.js已在创建K线图时检查过）
                const volumeCtx = volumeCanvas.getContext('2d');
                volumeChartInstance = new Chart(volumeCtx, {
                    type: 'bar',
                    data: {
                        labels: volumeLabels,
                        datasets: [{
                            label: '成交量',
                            data: volumeData,
                            backgroundColor: 'rgba(66, 165, 245, 0.7)',
                            borderColor: '#42a5f5',
                            borderWidth: 1
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            title: {
                                display: true,
                                text: '成交量',
                                font: {
                                    size: 14,
                                    weight: 'bold'
                                }
                            },
                            legend: {
                                display: false
                            },
                            tooltip: {
                                callbacks: {
                                    title: function(context) {
                                        return context[0].label;
                                    },
                                    label: function(context) {
                                        return `成交量: ${context.parsed.y}`;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                title: {
                                    display: false
                                },
                                ticks: {
                                    maxTicksLimit: 24
                                }
                            },
                            y: {
                                title: {
                                    display: false
                                },
                                beginAtZero: true
                            }
                        },
                        onClick: function(event, elements) {
                            if (elements.length > 0) {
                                const element = elements[0];
                                const time = element.element.$context.raw || element.label;
                                const price = element.parsed.y;
                                // 显示最新价信息
                                alert(`时间: ${time}\n最新价: ¥${price.toFixed(2)}\n涨停板: ¥${limitUpPrice.toFixed(2)}\n跌停板: ¥${limitDownPrice.toFixed(2)}`);
                                highlightKlineCandle(time);
                            }
                        }
                    }
                });
                
                // 调试买一卖一量数据
                console.log('[DEBUG] 买一卖一量数据检查:');
                console.log('  bidAskData datasets数量:', chartData.bidAskData.datasets.length);
                if (chartData.bidAskData.datasets.length >= 2) {
                    console.log('  买一量数据点数量:', chartData.bidAskData.datasets[0].data.length);
                    console.log('  卖一量数据点数量:', chartData.bidAskData.datasets[1].data.length);
                    
                    if (chartData.bidAskData.datasets[0].data.length > 0) {
                        const bidValues = chartData.bidAskData.datasets[0].data.map(point => point.y);
                        const askValues = chartData.bidAskData.datasets[1].data.map(point => point.y);
                        
                        console.log('  买一量Y值范围:', Math.min(...bidValues), '到', Math.max(...bidValues));
                        console.log('  卖一量Y值范围:', Math.min(...askValues), '到', Math.max(...askValues));
                        console.log('  前5个买一量Y值:', bidValues.slice(0, 5));
                        console.log('  前5个卖一量Y值:', askValues.slice(0, 5));
                        
                        // 检查是否有非零值
                        const hasNonZeroBid = bidValues.some(val => val !== 0);
                        const hasNonZeroAsk = askValues.some(val => val !== 0);
                        console.log('  有非零买一量:', hasNonZeroBid);
                        console.log('  有非零卖一量:', hasNonZeroAsk);
                    }
                }
                
                // 创建买一量图
                const bidAskCtx = bidAskCanvas.getContext('2d');
                
                // 等待DOM更新后设置Canvas尺寸并创建图表
                setTimeout(() => {
                    const containerWidth = bidAskChartContainer.clientWidth - 20; // 减去padding
                    const containerHeight = bidAskChartContainer.clientHeight - 20; // 减去padding
                    
                    bidAskCanvas.width = containerWidth;
                    bidAskCanvas.height = containerHeight;
                    
                    console.log('[DEBUG] Canvas尺寸设置:');
                    console.log('  容器宽度:', bidAskChartContainer.clientWidth);
                    console.log('  容器高度:', bidAskChartContainer.clientHeight);
                    console.log('  Canvas宽度:', bidAskCanvas.width);
                    console.log('  Canvas高度:', bidAskCanvas.height);
                    console.log('  clientWidth:', bidAskCanvas.clientWidth);
                    console.log('  clientHeight:', bidAskCanvas.clientHeight);
                    
                    // 检查Chart.js是否已加载
                    if (typeof Chart === 'undefined') {
                        console.error('[错误] Chart.js未加载，无法创建买一卖一量图');
                        bidAskChartContainer.innerHTML = '<div style="padding: 20px; text-align: center; color: #f44336;">⚠️ Chart.js库加载失败</div>';
                        return;
                    }
                    
                    // 现在创建图表
                    createBidAskChart();
                }, 100);
                
                // 创建买一卖一量图表的函数
                function createBidAskChart() {
                    console.log('[DEBUG] 开始创建买一卖一量图（合并版）');
                    
                    // 再次检查Chart.js
                    if (typeof Chart === 'undefined') {
                        console.error('[错误] Chart.js未加载');
                        return;
                    }
                    
                    // 创建买一卖一量数据数组
                    const bidAskLabels = [];
                    const combinedVolData = [];
                    const colors = [];
                    
                    if (chartData.bidAskData && chartData.bidAskData.datasets && chartData.bidAskData.datasets.length >= 2) {
                        const bidData = chartData.bidAskData.datasets[0].data || [];
                        const askData = chartData.bidAskData.datasets[1].data || [];
                        
                        console.log('[DEBUG] 买一量原始数据长度:', bidData.length);
                        console.log('[DEBUG] 卖一量原始数据长度:', askData.length);
                        
                        // 确保两个数据集长度一致
                        const minLength = Math.min(bidData.length, askData.length);
                        
                        for (let i = 0; i < minLength; i++) {
                            const bidPoint = bidData[i];
                            const askPoint = askData[i];
                            
                            if (bidPoint && bidPoint.time && askPoint && askPoint.time) {
                                let bidVol = 0;
                                let askVol = 0;
                                
                                // 处理买一量
                                if (Array.isArray(bidPoint.bid_vol)) {
                                    bidVol = bidPoint.bid_vol[0] || 0;
                                } else if (bidPoint.bid_vol && !isNaN(bidPoint.bid_vol)) {
                                    bidVol = bidPoint.bid_vol;
                                } else if (bidPoint.y && !isNaN(bidPoint.y)) {
                                    bidVol = bidPoint.y; // 备用值
                                }
                                
                                // 处理卖一量
                                if (Array.isArray(askPoint.ask_vol)) {
                                    askVol = askPoint.ask_vol[0] || 0;
                                } else if (askPoint.ask_vol && !isNaN(askPoint.ask_vol)) {
                                    askVol = askPoint.ask_vol;
                                } else if (askPoint.y && !isNaN(askPoint.y)) {
                                    askVol = Math.abs(askPoint.y); // 备用值，取绝对值
                                }
                                
                                if (!isNaN(bidVol) && !isNaN(askVol) && bidVol >= 0 && askVol >= 0) {
                                    bidAskLabels.push(bidPoint.time);
                                    
                                    // 创建叠加量柱数据：买一量为负值，卖一量为正值
                                    // 量柱范围从 -买一量 到 +卖一量
                                    const barData = {
                                        bidVol: -bidVol,  // 买一量显示为负值
                                        askVol: askVol    // 卖一量显示为正值
                                    };
                                    combinedVolData.push(barData);
                                    
                                    // 根据买卖量关系确定颜色
                                    if (bidVol > askVol) {
                                        colors.push('red'); // 买一量大于卖一量，显示红色
                                    } else if (bidVol < askVol) {
                                        colors.push('green'); // 买一量小于卖一量，显示绿色
                    } else {
                                        colors.push('blue'); // 两者相同，显示蓝色
                                    }
                                }
                            }
                        }
                        
                        console.log('[DEBUG] ===== 买一卖一量图时间范围:', bidAskLabels[0], '到', bidAskLabels[bidAskLabels.length - 1], '=====');
                        console.log('[DEBUG] 时间标签数量:', bidAskLabels.length);
                        console.log('[DEBUG] 合并量数据点数量:', combinedVolData.length);
                        console.log('[DEBUG] 合并量值范围:', Math.min(...combinedVolData), '到', Math.max(...combinedVolData));
                        
                        // 检查前几个数据点
                        if (bidAskLabels.length > 0) {
                            console.log('[DEBUG] 前3个时间点:', bidAskLabels.slice(0, 3));
                            console.log('[DEBUG] 前3个合并量值:', combinedVolData.slice(0, 3));
                            console.log('[DEBUG] 前3个颜色:', colors.slice(0, 3));
                        }
                    }
                    
                    // 检查数据长度一致性
                    if (bidAskLabels.length !== combinedVolData.length || combinedVolData.length !== colors.length) {
                        console.log('[ERROR] 数据长度不一致，无法创建图表');
                        console.log('[ERROR] 时间标签数量:', bidAskLabels.length);
                        console.log('[ERROR] 合并量数据数量:', combinedVolData.length);
                        console.log('[ERROR] 颜色数据数量:', colors.length);
                        return;
                    }
                    
                    console.log('[DEBUG] 数据长度检查通过，开始创建图表');
                    
                    // 再次检查Chart.js
                    if (typeof Chart === 'undefined') {
                        console.error('[错误] Chart.js未加载，无法创建买一卖一量图');
                        return;
                    }
                
                    // 创建叠加的买一卖一量图表
                    bidAskChartInstance = new Chart(bidAskCtx, {
                        type: 'bar',
                        data: {
                            labels: bidAskLabels,
                            datasets: [
                                {
                                    label: '买一量',
                                    data: combinedVolData.map(item => item.bidVol), // 负值，向左显示
                                    backgroundColor: colors.map(color => color === 'red' ? 'rgba(255, 0, 0, 0.7)' : 
                                                                  color === 'green' ? 'rgba(0, 255, 0, 0.7)' : 
                                                                  'rgba(0, 0, 255, 0.7)'), // 根据买卖量关系确定颜色
                                    borderColor: colors,
                                    borderWidth: 1,
                                    stack: 'bidask'
                                },
                                {
                                    label: '卖一量',
                                    data: combinedVolData.map(item => item.askVol), // 正值，向右显示
                                    backgroundColor: colors.map(color => color === 'red' ? 'rgba(255, 0, 0, 0.7)' : 
                                                                  color === 'green' ? 'rgba(0, 255, 0, 0.7)' : 
                                                                  'rgba(0, 0, 255, 0.7)'), // 根据买卖量关系确定颜色
                                    borderColor: colors,
                                    borderWidth: 1,
                                    stack: 'bidask'
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            layout: {
                                padding: {
                                    top: 20,
                                    bottom: 40,
                                    left: 10,
                                    right: 10
                                }
                            },
                            plugins: {
                                legend: {
                                    display: false
                                }
                            },
                            scales: {
                                x: {
                                    ticks: {
                                        maxTicksLimit: 24,
                                        font: {
                                            size: 12
                                        }
                                    },
                                    title: {
                                        display: false
                                    }
                                },
                                y: {
                                    beginAtZero: false,
                                    title: {
                                        display: false
                                    },
                                    suggestedMax: function(context) {
                                        // 确保y轴最大值略大于数据的最大值
                                        const chart = context.chart;
                                        const max = Math.max(...chart.data.datasets.flatMap(d => d.data.map(v => v >= 0 ? v : 0)));
                                        return max * 1.1; // 增加10%的空间
                                    },
                                    suggestedMin: function(context) {
                                        // 确保y轴最小值略小于数据的最小值
                                        const chart = context.chart;
                                        const min = Math.min(...chart.data.datasets.flatMap(d => d.data.map(v => v <= 0 ? v : 0)));
                                        return min * 1.1; // 增加10%的空间
                                    },
                                    ticks: {
                                        font: {
                                            size: 12
                                        },
                                        // 添加0轴刻度
                                        callback: function(value) {
                                            if (value === 0) {
                                                return '0';
                                            }
                                            return value;
                                        }
                                    },
                                    // 突出显示0轴
                                    grid: {
                                        drawOnChartArea: true,
                                        color: function(context) {
                                            if (context.tick.value === 0) {
                                                return '#000000'; // 0轴用黑色线
                                            }
                                            return '#e0e0e0'; // 其他网格线用浅灰色
                                        },
                                        lineWidth: function(context) {
                                            if (context.tick.value === 0) {
                                                return 2; // 0轴线宽2px
                                            }
                                            return 1; // 其他网格线1px
                                        },
                                        drawBorder: true,
                                        borderColor: '#cccccc',
                                        borderWidth: 1
                                    }
                                }
                            }
                        }
                    });
                    
                    console.log('[DEBUG] 买一卖一量图（合并版）创建完成');
                    console.log('[DEBUG] 图表数据:', {
                        labels: bidAskLabels,
                        combinedVolData: combinedVolData,
                        colors: colors
                    });
                    console.log('[DEBUG] 合并量数据范围:', Math.min(...combinedVolData), '到', Math.max(...combinedVolData));
                    
                    // 检查图表是否创建成功
                    if (bidAskChartInstance) {
                        console.log('[DEBUG] 图表实例创建成功');
                        console.log('[DEBUG] 图表数据点数量:', bidAskChartInstance.data.labels.length);
                        console.log('[DEBUG] 合并量数据集:', bidAskChartInstance.data.datasets[0]);
                    } else {
                        console.log('[DEBUG] 图表实例创建失败');
                    }
                } // 结束createBidAskChart函数
                
                // 创建买二-买五总量和卖二-卖五总量图
                function createBidAsk25Chart() {
                    console.log('[DEBUG] 开始创建买二-买五总量和卖二-卖五总量图（叠加版）');
                    
                    // 检查Chart.js是否已加载
                    if (typeof Chart === 'undefined') {
                        console.error('[错误] Chart.js未加载，无法创建买二-买五总量图');
                        return;
                    }
                    
                    // 创建图表容器
                    const bidAsk25ChartContainer = document.createElement('div');
                    bidAsk25ChartContainer.style.width = '100%';
                    bidAsk25ChartContainer.style.height = '220px';
                    bidAsk25ChartContainer.style.border = '1px solid #e0e0e0';
                    bidAsk25ChartContainer.style.borderRadius = '8px';
                    bidAsk25ChartContainer.style.padding = '10px';
                    bidAsk25ChartContainer.style.marginTop = '10px';
                    
                    // 添加买二-买五总量图标题
                    const bidAsk25Title = document.createElement('div');
                    bidAsk25Title.style.fontSize = '14px';
                    bidAsk25Title.style.fontWeight = 'bold';
                    bidAsk25Title.style.color = '#333';
                    bidAsk25Title.style.marginBottom = '5px';
                    bidAsk25Title.style.textAlign = 'center';
                    bidAsk25Title.textContent = '买二-买五与卖二-卖五总量图';
                    bidAsk25ChartContainer.appendChild(bidAsk25Title);
                    
                    // 添加图例说明
                    const bidAsk25Legend = document.createElement('div');
                    bidAsk25Legend.style.fontSize = '10px';
                    bidAsk25Legend.style.color = '#666';
                    bidAsk25Legend.style.marginBottom = '10px';
                    bidAsk25Legend.style.textAlign = 'center';
                    bidAsk25Legend.style.display = 'flex';
                    bidAsk25Legend.style.justifyContent = 'center';
                    bidAsk25Legend.style.gap = '15px';
                    bidAsk25Legend.style.flexWrap = 'wrap';
                    bidAsk25Legend.innerHTML = `
                        <span style="color: red;">● 红色：买二-买五总量 > 卖二-卖五总量</span>
                        <span style="color: green;">● 绿色：买二-买五总量 < 卖二-卖五总量</span>
                    `;
                    bidAsk25ChartContainer.appendChild(bidAsk25Legend);
                    
                    const bidAsk25Canvas = document.createElement('canvas');
                    bidAsk25Canvas.id = 'bidAsk25Chart';
                    bidAsk25Canvas.style.width = '100%';
                    bidAsk25Canvas.style.height = '100%';
                    bidAsk25ChartContainer.appendChild(bidAsk25Canvas);
                    
                    // 创建买二-买五总量和卖二-卖五总量数据数组
                    const bidAsk25Labels = [];
                    const combined25VolData = [];
                    const colors25 = [];
                    
                    // 直接从K线数据中获取买二-买五数据
                    if (chartData.klineData && chartData.klineData.datasets && chartData.klineData.datasets.length > 0) {
                        const klineData = chartData.klineData.datasets[0].data || [];
                        
                        console.log('[DEBUG] K线数据长度:', klineData.length);
                        
                        for (let i = 0; i < klineData.length; i++) {
                            const klinePoint = klineData[i];
                            
                            if (klinePoint && klinePoint.time) {
                                let bid25Vol = 0;
                                let ask25Vol = 0;
                                
                                // 调试：检查数据结构
                                if (i === 0) { // 只打印第一个数据点的详细信息
                                    console.log('[DEBUG] 第一个K线数据点:', klinePoint);
                                    console.log('[DEBUG] K线数据点所有属性:', Object.keys(klinePoint));
                                    
                                    // 检查是否有买二-买五相关的字段
                                    for (let key in klinePoint) {
                                        if (key.includes('bid') || key.includes('ask')) {
                                            console.log('[DEBUG] klinePoint.' + key + ':', klinePoint[key]);
                                        }
                                    }
                                }
                                
                                // 直接使用后端计算好的买二-买五总量
                                bid25Vol = klinePoint.bid25_vol || 0;
                                ask25Vol = klinePoint.ask25_vol || 0;
                                
                                // 调试：打印计算结果
                                if (i === 0) {
                                    console.log('[DEBUG] 前端读取K线数据:');
                                    console.log('[DEBUG] klinePoint.bid25_vol:', klinePoint.bid25_vol);
                                    console.log('[DEBUG] klinePoint.ask25_vol:', klinePoint.ask25_vol);
                                    console.log('[DEBUG] 买二-买五总量计算结果:', bid25Vol);
                                    console.log('[DEBUG] 卖二-卖五总量计算结果:', ask25Vol);
                                    console.log('[DEBUG] 数据有效性检查:', {
                                        bid25VolValid: !isNaN(bid25Vol) && bid25Vol >= 0,
                                        ask25VolValid: !isNaN(ask25Vol) && ask25Vol >= 0,
                                        bid25Vol: bid25Vol,
                                        ask25Vol: ask25Vol
                                    });
                                }
                                
                                if (!isNaN(bid25Vol) && !isNaN(ask25Vol) && bid25Vol >= 0 && ask25Vol >= 0) {
                                    bidAsk25Labels.push(klinePoint.time);
                                    
                                    // 创建叠加量柱数据：买二-买五总量为负值，卖二-卖五总量为正值
                                    // 量柱范围从 -买二-买五总量 到 +卖二-卖五总量
                                    const barData = {
                                        bid25Vol: -bid25Vol,  // 买二-买五总量显示为负值
                                        ask25Vol: ask25Vol    // 卖二-卖五总量显示为正值
                                    };
                                    combined25VolData.push(barData);
                                    
                                    // 根据买卖量关系确定颜色
                                    if (bid25Vol > ask25Vol) {
                                        colors25.push('red'); // 买二-买五总量大于卖二-卖五总量，显示红色
                                    } else if (bid25Vol < ask25Vol) {
                                        colors25.push('green'); // 买二-买五总量小于卖二-卖五总量，显示绿色
                                    } else {
                                        colors25.push('blue'); // 两者相同，显示蓝色
                                    }
                                }
                            }
                        }
                        
                        console.log('[DEBUG] 买二-买五总量数据长度:', combined25VolData.length);
                        console.log('[DEBUG] 标签数据长度:', bidAsk25Labels.length);
                        console.log('[DEBUG] 颜色数据长度:', colors25.length);
                        console.log('[DEBUG] 数据长度是否一致:', bidAsk25Labels.length === combined25VolData.length && combined25VolData.length === colors25.length);
                        
                        // 检查前几个数据点
                        if (bidAsk25Labels.length > 0) {
                            console.log('[DEBUG] 前3个时间点:', bidAsk25Labels.slice(0, 3));
                            console.log('[DEBUG] 前3个合并量值:', combined25VolData.slice(0, 3));
                            console.log('[DEBUG] 前3个颜色:', colors25.slice(0, 3));
                        }
                    }
                    
                    if (bidAsk25Labels.length === 0) {
                        console.log('[DEBUG] 没有买二-买五总量和卖二-卖五总量数据');
                        bidAsk25ChartContainer.innerHTML = '<div style="text-align: center; padding: 20px; color: #666;">原始tick数据中没有买二-买五信息，只有买一量和卖一量</div>';
                        klineContainer.appendChild(bidAsk25ChartContainer);
                        return;
                    }
                    
                    if (bidAsk25Labels.length !== combined25VolData.length || combined25VolData.length !== colors25.length) {
                        console.log('[DEBUG] 买二-买五总量和卖二-卖五总量数据长度不一致，跳过创建图表');
                        return;
                    }
                    
                    console.log('[DEBUG] 数据长度检查通过，开始创建买二-买五总量和卖二-卖五总量图表');
                    
                    // 创建叠加的买二-买五总量和卖二-卖五总量图表
                    const bidAsk25Ctx = bidAsk25Canvas.getContext('2d');
                    const bidAsk25ChartInstance = new Chart(bidAsk25Ctx, {
                        type: 'bar',
                        data: {
                            labels: bidAsk25Labels,
                            datasets: [
                                {
                                    label: '买二-买五总量',
                                    data: combined25VolData.map(item => item.bid25Vol), // 负值，向左显示
                                    backgroundColor: colors25.map(color => color === 'red' ? 'rgba(255, 0, 0, 0.7)' : 
                                                                  color === 'green' ? 'rgba(0, 255, 0, 0.7)' : 
                                                                  'rgba(0, 0, 255, 0.7)'), // 根据买卖量关系确定颜色
                                    borderColor: colors25,
                                    borderWidth: 1,
                                    stack: 'bidask25'
                                },
                                {
                                    label: '卖二-卖五总量',
                                    data: combined25VolData.map(item => item.ask25Vol), // 正值，向右显示
                                    backgroundColor: colors25.map(color => color === 'red' ? 'rgba(255, 0, 0, 0.7)' : 
                                                                  color === 'green' ? 'rgba(0, 255, 0, 0.7)' : 
                                                                  'rgba(0, 0, 255, 0.7)'), // 根据买卖量关系确定颜色
                                    borderColor: colors25,
                                    borderWidth: 1,
                                    stack: 'bidask25'
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            layout: {
                                padding: {
                                    top: 20,
                                    bottom: 40,
                                    left: 10,
                                    right: 10
                                }
                            },
                            plugins: {
                                legend: {
                                    display: false
                                }
                            },
                            scales: {
                                x: {
                                    ticks: {
                                        maxTicksLimit: 24,
                                        font: {
                                            size: 12
                                        }
                                    },
                                    title: {
                                        display: false
                                    }
                                },
                                y: {
                                    beginAtZero: false,
                                    title: {
                                        display: false
                                    },
                                    ticks: {
                                        font: {
                                            size: 12
                                        },
                                        // 添加0轴刻度
                                        callback: function(value) {
                                            if (value === 0) {
                                                return '0';
                                            }
                                            return value;
                                        }
                                    },
                                    // 突出显示0轴
                                    grid: {
                                        drawOnChartArea: true,
                                        color: function(context) {
                                            if (context.tick.value === 0) {
                                                return '#000000'; // 0轴用黑色线
                                            }
                                            return '#e0e0e0'; // 其他网格线用浅灰色
                                        },
                                        lineWidth: function(context) {
                                            if (context.tick.value === 0) {
                                                return 2; // 0轴线宽2px
                                            }
                                            return 1; // 其他网格线1px
                                        },
                                        drawBorder: true,
                                        borderColor: '#cccccc',
                                        borderWidth: 1
                                    }
                                }
                            }
                        }
                    });
                    
                    console.log('[DEBUG] 买二-买五总量和卖二-卖五总量图（叠加版）创建完成');
                    console.log('[DEBUG] 图表数据:', {
                        labels: bidAsk25Labels,
                        combined25VolData: combined25VolData,
                        colors25: colors25
                    });
                    
                    // 将图表容器添加到K线容器中
                    klineContainer.appendChild(bidAsk25ChartContainer);
                } // 结束createBidAsk25Chart函数
                
                // 组装K线图
                klineContainer.appendChild(klineChartContainer);
                klineContainer.appendChild(volumeChartContainer);
                klineContainer.appendChild(bidAskChartContainer);
                
                // 创建买二-买五总量和卖二-卖五总量图
                createBidAsk25Chart();
                abnormalChangesList.appendChild(klineContainer);
                
                
                } else {
                const noDataDiv = document.createElement('div');
                noDataDiv.className = 'analysis-item';
                noDataDiv.innerHTML = '<span style="color: #666;">暂无tick数据</span>';
                abnormalChangesList.appendChild(noDataDiv);
            }
        }
        
        function prepareTickKlineData(tickData, abnormalChanges, backendData) {
            // 将tick数据转换为1分钟K线数据
            const klineData = [];
            const volumeData = [];
            
            console.log('[DEBUG] 开始准备K线数据');
            console.log('[DEBUG] tickData数量:', tickData ? tickData.length : 0);
            if (tickData && tickData.length > 0) {
                console.log('[DEBUG] 第一条tick数据:', tickData[0]);
                console.log('[DEBUG] 最后一条tick数据:', tickData[tickData.length - 1]);
                
                // 检查时间范围
                const firstTime = tickData[0].time;
                const lastTime = tickData[tickData.length - 1].time;
                console.log('[DEBUG] 数据时间范围:', firstTime, '到', lastTime);
                
                // 检查前3个tick的买一卖一量数据
                // console.log('[DEBUG] 前3个tick的买一卖一量数据:');
                // for (let i = 0; i < Math.min(3, tickData.length); i++) {
                //     const tick = tickData[i];
                //     console.log(`  Tick ${i}: 买一量=${tick.bid_vol}, 卖一量=${tick.ask_vol}, 时间=${tick.time}`);
                // }
            }
            
            if (!tickData || tickData.length === 0) {
                console.log('[DEBUG] 没有tick数据');
                return {
                    klineData: { datasets: [{ label: 'K线', data: [] }] },
                    volumeData: { datasets: [{ label: '成交量', data: [] }] }
                };
            }
            
            // 按时间分组，每1分钟一组
            const timeGroups = {};
            
            tickData.forEach(tick => {
                const time = tick.time;
                if (!time) return;
                
                const minute = time.substring(0, 5); // 取HH:MM部分
                const hour = parseInt(minute.substring(0, 2));
                const min = parseInt(minute.substring(3, 5));
                
                // 过滤闭市时间：11:30-13:00
                if ((hour === 11 && min >= 30) || (hour === 12) || (hour === 13 && min === 0)) {
                    return; // 跳过闭市时间
                }
                
                // 确保时间在交易时间内：9:30-15:00
                if (hour < 9 || (hour === 9 && min < 30) || hour >= 15) {
                    return; // 跳过非交易时间
                }
                
                if (!timeGroups[minute]) {
                    timeGroups[minute] = [];
                }
                timeGroups[minute].push(tick);
            });
            
            console.log('[DEBUG] 时间分组数量:', Object.keys(timeGroups).length);
            
            // 为每个1分钟组创建K线数据
            Object.keys(timeGroups).sort().forEach(minute => {
                const ticks = timeGroups[minute];
                if (ticks.length === 0) return;
                
                // 计算OHLC - 不过滤价格，让所有价格都参与计算
                const prices = ticks.map(t => t.last_price || 0);
                const volumes = ticks.map(t => t.volume || 0);
                
                if (prices.length === 0) return;
                
                // 过滤掉无效价格（0或负数）
                const validPrices = prices.filter(p => p > 0);
                if (validPrices.length === 0) {
                    console.log(`[DEBUG] 分钟 ${minute} 没有有效价格`);
                    return;
                }
                
                const open = validPrices[0];
                const close = validPrices[validPrices.length - 1];
                const high = Math.max(...validPrices);
                const low = Math.min(...validPrices);
                
                // 计算成交量变化
                const volumeChange = volumes.length > 1 ? volumes[volumes.length - 1] - volumes[0] : volumes[0] || 0;
                
                // 获取最后一个tick的信息
                const lastTick = ticks[ticks.length - 1];
                
                // 调试：检查原始tick数据结构
                if (klineData.length === 0) {
                    console.log('[DEBUG] 原始tick数据数量:', ticks.length);
                    if (ticks.length > 0) {
                        console.log('[DEBUG] 第一个原始tick数据:', ticks[0]);
                        console.log('[DEBUG] 第一个tick所有属性:', Object.keys(ticks[0]));
                        
                        // 检查第一个tick是否有买二-买五相关的字段
                        for (let key in ticks[0]) {
                            if (key.includes('bid') || key.includes('ask') || key.includes('vol')) {
                                console.log('[DEBUG] 第一个tick.' + key + ':', ticks[0][key], '类型:', typeof ticks[0][key]);
                                if (Array.isArray(ticks[0][key])) {
                                    console.log('[DEBUG] ' + key + '是数组，长度:', ticks[0][key].length, '内容:', ticks[0][key]);
                                }
                            }
                        }
                    }
                }
                
                // 检查是否有盘口变化
                const abnormalChange = abnormalChanges ? abnormalChanges.find(ac => ac.time === minute) : null;
                
                // 调整时间轴，跳过午休时间
                let adjustedTime;
                const hour = parseInt(minute.substring(0, 2));
                const min = parseInt(minute.substring(3, 5));
                
                if (hour >= 13) {
                    // 下午时间：13:00开始，减去1.5小时（90分钟）跳过午休
                    adjustedTime = new Date('2025-10-17 ' + minute + ':00');
                    adjustedTime.setMinutes(adjustedTime.getMinutes() - 90);
                } else {
                    // 上午时间：正常显示
                    adjustedTime = new Date('2025-10-17 ' + minute + ':00');
                }
                
                // 调试：检查第一个tick数据的bid_vol和ask_vol结构
                if (klineData.length === 0) {
                    console.log('[DEBUG] 第一个lastTick数据:', lastTick);
                    console.log('[DEBUG] lastTick所有属性:', Object.keys(lastTick));
                    console.log('[DEBUG] lastTick.bid_vol类型:', typeof lastTick.bid_vol, '值:', lastTick.bid_vol);
                    console.log('[DEBUG] lastTick.ask_vol类型:', typeof lastTick.ask_vol, '值:', lastTick.ask_vol);
                    
                    // 检查是否有其他买二-买五相关的字段
                    for (let key in lastTick) {
                        if (key.includes('bid') || key.includes('ask') || key.includes('vol')) {
                            console.log('[DEBUG] lastTick.' + key + ':', lastTick[key], '类型:', typeof lastTick[key]);
                            if (Array.isArray(lastTick[key])) {
                                console.log('[DEBUG] ' + key + '是数组，长度:', lastTick[key].length, '内容:', lastTick[key]);
                            }
                        }
                    }
                    
                    if (Array.isArray(lastTick.bid_vol)) {
                        console.log('[DEBUG] bid_vol是数组，长度:', lastTick.bid_vol.length, '内容:', lastTick.bid_vol);
                    }
                    if (Array.isArray(lastTick.ask_vol)) {
                        console.log('[DEBUG] ask_vol是数组，长度:', lastTick.ask_vol.length, '内容:', lastTick.ask_vol);
                    }
                }
                
                // 计算买二-买五总量和卖二-卖五总量
                let bid25Vol = 0;
                let ask25Vol = 0;
                
                // 调试：显示买二-买五计算过程
                if (klineData.length === 0) {
                    console.log('[DEBUG] 后端计算买二-买五总量:');
                    console.log('[DEBUG] lastTick.bid_vol类型:', typeof lastTick.bid_vol, '值:', lastTick.bid_vol);
                    console.log('[DEBUG] lastTick.ask_vol类型:', typeof lastTick.ask_vol, '值:', lastTick.ask_vol);
                    console.log('[DEBUG] lastTick所有键:', Object.keys(lastTick));
                    
                    // 检查是否有其他买二-买五相关的字段
                    for (let key in lastTick) {
                        if (key.includes('bid') || key.includes('ask') || key.includes('vol')) {
                            console.log('[DEBUG] lastTick.' + key + ':', lastTick[key], '类型:', typeof lastTick[key]);
                        }
                    }
                    
                    // 检查bid_vol_array和ask_vol_array是否为数组
                    if (Array.isArray(lastTick.bid_vol_array)) {
                        console.log('[DEBUG] bid_vol_array是数组，长度:', lastTick.bid_vol_array.length, '内容:', lastTick.bid_vol_array);
                        if (lastTick.bid_vol_array.length >= 5) {
                            console.log('[DEBUG] 买二-买五各值:');
                            for (let j = 1; j <= 4; j++) {
                                console.log(`[DEBUG] 买${j+1}量:`, lastTick.bid_vol_array[j]);
                            }
                        }
                    }
                    
                    if (Array.isArray(lastTick.ask_vol_array)) {
                        console.log('[DEBUG] ask_vol_array是数组，长度:', lastTick.ask_vol_array.length, '内容:', lastTick.ask_vol_array);
                        if (lastTick.ask_vol_array.length >= 5) {
                            console.log('[DEBUG] 卖二-卖五各值:');
                            for (let j = 1; j <= 4; j++) {
                                console.log(`[DEBUG] 卖${j+1}量:`, lastTick.ask_vol_array[j]);
                            }
                        }
                    }
                }
                
                if (Array.isArray(lastTick.bid_vol_array) && lastTick.bid_vol_array.length >= 5) {
                    // 买二-买五总量（数组索引1-4）
                    for (let j = 1; j <= 4; j++) {
                        bid25Vol += lastTick.bid_vol_array[j] || 0;
                    }
                }
                
                if (Array.isArray(lastTick.ask_vol_array) && lastTick.ask_vol_array.length >= 5) {
                    // 卖二-卖五总量（数组索引1-4）
                    for (let j = 1; j <= 4; j++) {
                        ask25Vol += lastTick.ask_vol_array[j] || 0;
                    }
                }
                
                // 调试：显示计算后的买二-买五总量
                if (klineData.length === 0) {
                    console.log('[DEBUG] 计算后的买二-买五总量:', bid25Vol);
                    console.log('[DEBUG] 计算后的卖二-卖五总量:', ask25Vol);
                    console.log('[DEBUG] 为什么计算结果都是0？');
                    console.log('[DEBUG] 检查条件: Array.isArray(lastTick.bid_vol_array):', Array.isArray(lastTick.bid_vol_array));
                    console.log('[DEBUG] 检查条件: lastTick.bid_vol_array.length >= 5:', lastTick.bid_vol_array ? lastTick.bid_vol_array.length >= 5 : 'bid_vol_array不存在');
                    console.log('[DEBUG] 检查条件: Array.isArray(lastTick.ask_vol_array):', Array.isArray(lastTick.ask_vol_array));
                    console.log('[DEBUG] 检查条件: lastTick.ask_vol_array.length >= 5:', lastTick.ask_vol_array ? lastTick.ask_vol_array.length >= 5 : 'ask_vol_array不存在');
                }
                
                const klinePoint = {
                    x: adjustedTime, // 使用调整后的时间
                    time: minute + ':00',
                    open: open,
                    high: high,
                    low: low,
                    close: close,
                    volume: Math.abs(volumeChange),
                    bid_vol: lastTick.bid_vol || 0,
                    ask_vol: lastTick.ask_vol || 0,
                    bid25_vol: bid25Vol, // 买二-买五总量
                    ask25_vol: ask25Vol, // 卖二-卖五总量
                    is_limit_up: lastTick.is_limit_up || false,
                    is_limit_down: lastTick.is_limit_down || false,
                    is_abnormal: abnormalChange ? true : false,
                    abnormal_reason: abnormalChange ? abnormalChange.reason : null,
                    last_close: lastTick.last_close || 0  // 添加last_close字段
                };
                
                klineData.push(klinePoint);
                
                volumeData.push({
                    x: adjustedTime, // 使用调整后的时间
                    time: minute + ':00',
                    volume: Math.abs(volumeChange),
                    bid_vol: lastTick.bid_vol || 0,
                    ask_vol: lastTick.ask_vol || 0,
                    is_limit_up: lastTick.is_limit_up || false,
                    is_limit_down: lastTick.is_limit_down || false,
                    is_abnormal: abnormalChange ? true : false,
                    abnormal_reason: abnormalChange ? abnormalChange.reason : null
                });
                
                // 调试信息（只显示前3个）
                // if (klineData.length <= 3) {
                //     console.log(`[DEBUG] K线点 ${klineData.length}:`, klinePoint);
                //     console.log(`[DEBUG] 原始tick数据 - 买一量: ${lastTick.bid_vol}, 卖一量: ${lastTick.ask_vol}`);
                // }
            });
            
            console.log('[DEBUG] 最终K线数据数量:', klineData.length);
            if (klineData.length > 0) {
                // 检查K线数据的时间范围
                const firstKlineTime = klineData[0].time;
                const lastKlineTime = klineData[klineData.length - 1].time;
                console.log('[DEBUG] ===== K线数据时间范围:', firstKlineTime, '到', lastKlineTime, '=====');
                
                // 检查成交量数据的时间范围
                if (volumeData && volumeData.length > 0) {
                    const firstVolTime = volumeData[0].time;
                    const lastVolTime = volumeData[volumeData.length - 1].time;
                    console.log('[DEBUG] ===== 成交量数据时间范围:', firstVolTime, '到', lastVolTime, '=====');
                }
            }
            
            // 获取昨收盘价（从第一个数据点获取）
            const lastClose = klineData.length > 0 ? klineData[0].last_close || 0 : 0;
            
            // 调试：检查昨收盘价
            console.log('[DEBUG] 昨收盘价检查:');
            console.log('  klineData长度:', klineData.length);
            if (klineData.length > 0) {
                console.log('  第一个数据点:', klineData[0]);
                console.log('  last_close字段:', klineData[0].last_close);
            }
            console.log('  计算出的lastClose:', lastClose);
            
            // 计算涨停板和跌停板价格（根据股票类型计算正确比例）
            let limitUpPrice = lastClose * 1.1;  // 默认10%
            let limitDownPrice = lastClose * 0.9;  // 默认10%
            
            // 根据股票代码判断涨跌停板比例
            const stockCode = backendData.stock_code || '';
            const stockName = backendData.stock_name || '';
            let limitRatio = 0.10;  // 默认10%
            
            // 优先判断ST股票（5%）
            if (stockName.includes('ST') || stockName.includes('*ST') || 
                stockCode.includes('ST') || stockCode.includes('*ST')) {
                limitRatio = 0.05;
            } else if (stockCode.startsWith('30') || stockCode.startsWith('68')) {
                // 创业板和科创板：20%
                limitRatio = 0.20;
            } else if (stockCode.startsWith('8') || stockCode.startsWith('4')) {
                // 北交所：30%
                limitRatio = 0.30;
            }
            
            // 调试：输出股票类型判断
            console.log('[DEBUG] 股票类型判断:');
            console.log('  股票代码:', stockCode);
            console.log('  股票名称:', stockName);
            console.log('  涨跌停比例:', (limitRatio * 100) + '%');
            
            // 使用正确的比例计算涨跌停板价格
            limitUpPrice = lastClose * (1 + limitRatio);
            limitDownPrice = lastClose * (1 - limitRatio);
            
            // 四舍五入到2位小数
            limitUpPrice = Math.round(limitUpPrice * 100) / 100;
            limitDownPrice = Math.round(limitDownPrice * 100) / 100;
            
            // 调试：输出涨跌停板价格
            console.log('[DEBUG] 涨跌停板价格计算:');
            console.log('  昨收盘价:', lastClose);
            console.log('  涨停板价格:', limitUpPrice);
            console.log('  跌停板价格:', limitDownPrice);
            
            return {
                lastClose: lastClose,
                limitUpPrice: limitUpPrice,
                limitDownPrice: limitDownPrice,
                klineData: {
                    datasets: [
                        {
                            label: '最新价',
                            data: klineData.map(point => ({
                                x: point.x,  // 使用Date对象
                                y: point.close,  // 使用收盘价作为最新价
                                time: point.time,
                                open: point.open,
                                high: point.high,
                                low: point.low,
                                close: point.close,
                                volume: point.volume,
                                bid_vol: point.bid_vol,
                                ask_vol: point.ask_vol,
                                bid25_vol: point.bid25_vol,
                                ask25_vol: point.ask25_vol,
                                is_limit_up: point.is_limit_up,
                                is_limit_down: point.is_limit_down,
                                is_abnormal: point.is_abnormal,
                                abnormal_reason: point.abnormal_reason
                            })),
                            borderColor: '#26a69a',
                            backgroundColor: 'rgba(38, 166, 154, 0.1)',
                            borderWidth: 1,
                            fill: false,
                            tension: 0.1,
                            pointRadius: 1,
                            pointHoverRadius: 3
                        },
                            {
                                label: '昨收盘',
                                data: klineData.map(point => ({
                                    x: point.x,  // 使用Date对象
                                    y: lastClose
                                })),
                                borderColor: '#2196F3',
                                backgroundColor: 'transparent',
                                borderWidth: 1,
                                borderDash: [5, 5],
                                fill: false,
                                pointRadius: 0,
                                pointHoverRadius: 0
                            },
                        {
                            label: '涨停板',
                            data: klineData.map(point => ({
                                x: point.x,  // 使用Date对象
                                y: limitUpPrice
                            })),
                            borderColor: '#ff5722',
                            backgroundColor: 'rgba(255, 87, 34, 0.3)',
                            borderWidth: 0.5,
                            borderDash: [3, 3],
                            fill: false,
                            pointRadius: 0,
                            pointHoverRadius: 0
                        },
                        {
                            label: '跌停板',
                            data: klineData.map(point => ({
                                x: point.x,  // 使用Date对象
                                y: limitDownPrice
                            })),
                            borderColor: '#4caf50',
                            backgroundColor: 'rgba(76, 175, 80, 0.3)',
                            borderWidth: 0.5,
                            borderDash: [3, 3],
                            fill: false,
                            pointRadius: 0,
                            pointHoverRadius: 0
                        }
                    ]
                },
                volumeData: {
                    datasets: [{
                        label: '成交量',
                        data: volumeData.map(point => ({
                            x: point.x,  // 使用Date对象
                            y: point.volume,
                            time: point.time,
                            volume: point.volume,
                            bid_vol: point.bid_vol,
                            ask_vol: point.ask_vol,
                            is_limit_up: point.is_limit_up,
                            is_limit_down: point.is_limit_down,
                            is_abnormal: point.is_abnormal,
                            abnormal_reason: point.abnormal_reason
                        })),
                        backgroundColor: 'rgba(66, 165, 245, 0.7)',
                        borderColor: '#42a5f5',
                        borderWidth: 1
                    }]
                },
                bidAskData: {
                    datasets: [
                        {
                            label: '买一量',
                            data: volumeData.map(point => ({
                                x: point.x,  // 使用Date对象
                                y: point.bid_vol,  // 正值
                                time: point.time,
                                volume: point.volume,
                                bid_vol: point.bid_vol,
                                ask_vol: point.ask_vol,
                                is_limit_up: point.is_limit_up,
                                is_limit_down: point.is_limit_down,
                                is_abnormal: point.is_abnormal,
                                abnormal_reason: point.abnormal_reason
                            })),
                            backgroundColor: 'rgba(76, 175, 80, 0.7)',
                            borderColor: '#4caf50',
                            borderWidth: 1,
                            barThickness: 8
                        },
                        {
                            label: '卖一量',
                            data: volumeData.map(point => ({
                                x: point.x,  // 使用Date对象
                                y: -point.ask_vol,  // 负值
                                time: point.time,
                                volume: point.volume,
                                bid_vol: point.bid_vol,
                                ask_vol: point.ask_vol,
                                is_limit_up: point.is_limit_up,
                                is_limit_down: point.is_limit_down,
                                is_abnormal: point.is_abnormal,
                                abnormal_reason: point.abnormal_reason
                            })),
                            backgroundColor: 'rgba(244, 67, 54, 0.7)',
                            borderColor: '#f44336',
                            borderWidth: 1,
                            barThickness: 8
                        }
                    ]
                }
            };
        }
        
        // 全局变量存储图表实例
        let klineChartInstance = null;
        let volumeChartInstance = null;
        let bidAskChartInstance = null;
        
        function highlightVolumeBar(time) {
            // 高亮成交量图中对应时间的柱子
            if (volumeChartInstance) {
                console.log(`高亮成交量柱时间: ${time}`);
            }
        }
        
        function highlightKlineCandle(time) {
            // 高亮K线图中对应时间的蜡烛
            if (klineChartInstance) {
                console.log(`高亮K线蜡烛时间: ${time}`);
            }
        }
        
        function displayMainForce(mainForceAnalysis) {
            const mainForceList = document.getElementById('mainForceList');
            mainForceList.innerHTML = '';
            
            console.log('[前端] 主力分析数据:', mainForceAnalysis);
            
            // 辅助函数：规范化每个行为分析的最终得分到0-100范围
            function normalizeBehaviorScore(rawScore, maxPossibleScore) {
                // 限制分数范围，然后归一化到0-100
                const clampedScore = Math.max(0, Math.min(rawScore, maxPossibleScore));
                return Math.round((clampedScore / maxPossibleScore) * 100);
            }
            
            // 记录是否显示了极端行情，以便后续不显示涨停/跌停板分析
            let hasExtremeSwing = false;
            
            // 检查是否为极端行情，但不立即显示
            if (mainForceAnalysis && mainForceAnalysis.extreme_swing_behavior) {
                console.log('[前端] 检测到极端行情:', mainForceAnalysis.extreme_swing_behavior);
                hasExtremeSwing = true;
            }
            
            // 显示高位出货或洗盘分析（合并了原高位出货和主力洗盘）
            if (mainForceAnalysis && mainForceAnalysis.high_level_distribution_or_wash) {
                const distOrWash = mainForceAnalysis.high_level_distribution_or_wash;
                
                // 创建高位出货或洗盘分析容器
                const distOrWashDiv = document.createElement('div');
                distOrWashDiv.className = 'high-level-distribution-or-wash';
                distOrWashDiv.style.padding = '8px';
                distOrWashDiv.style.border = '1px solid #e0e0e0';
                distOrWashDiv.style.borderRadius = '6px';
                distOrWashDiv.style.backgroundColor = '#f9f9f9';
                
                // 标题
                const titleDiv = document.createElement('div');
                titleDiv.style.fontSize = '13px';
                titleDiv.style.fontWeight = 'bold';
                titleDiv.style.color = '#333';
                titleDiv.style.marginBottom = '4px';
                titleDiv.textContent = '高位出货或洗盘分析';
                distOrWashDiv.appendChild(titleDiv);
                
                // 总分和风险等级
                const scoreDiv = document.createElement('div');
                scoreDiv.style.marginBottom = '6px';
                
                const rawTotalScore = distOrWash.total_score || 0;
                const totalScore = normalizeBehaviorScore(rawTotalScore, 100); // 归一化到0-100
                const riskLevel = distOrWash.risk_level || '未知';
                const riskDescription = distOrWash.risk_description || '';
                const distributionScore = distOrWash.distribution_score || 0;
                const washScore = distOrWash.wash_score || 0;
                
                // 根据风险等级设置颜色
                let riskColor = '#666';
                if (riskLevel.includes('高风险') || riskLevel.includes('高概率洗盘')) {
                    riskColor = '#f44336';
                } else if (riskLevel.includes('需关注') || riskLevel.includes('中等概率')) {
                    riskColor = '#ff9800';
                } else if (riskLevel === '风险较低') {
                    riskColor = '#4caf50';
                }
                
                scoreDiv.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;">
                        <span style="font-weight: bold; font-size: 13px;">总分: <span style="color: ${riskColor};">${totalScore}</span></span>
                        <span style="font-weight: bold; color: ${riskColor}; font-size: 13px;">${riskLevel}</span>
                    </div>
                    <div style="color: #666; font-size: 12px; margin-bottom: 2px;">${riskDescription}</div>
                    <div style="color: #888; font-size: 10px;">
                        高位出货得分: ${distributionScore.toFixed(1)}分 | 洗盘得分: ${washScore.toFixed(1)}分
                    </div>
                `;
                distOrWashDiv.appendChild(scoreDiv);
                
                // 各公式详情
                if (distOrWash.formulas) {
                    const formulasDiv = document.createElement('div');
                    formulasDiv.style.marginTop = '4px';
                    
                    const formulas = distOrWash.formulas;
                    const formulaNames = {
                        'formula1': '公式1：日K线级位置判断',
                        'formula2': '公式2：Tick级买卖盘分析',
                        'formula3': '公式3：Tick级量能变化'
                    };
                    
                    Object.keys(formulaNames).forEach(formulaKey => {
                        if (formulas[formulaKey]) {
                            const formula = formulas[formulaKey];
                            const formulaDiv = document.createElement('div');
                            formulaDiv.style.marginBottom = '4px';
                            formulaDiv.style.padding = '4px 5px';
                            formulaDiv.style.backgroundColor = '#fff';
                            formulaDiv.style.borderRadius = '3px';
                            formulaDiv.style.border = '1px solid #ddd';
                            
                            const score = formula.score || 0;
                            const description = formula.description || '';
                            
                            // 根据得分设置颜色
                            let scoreColor = '#666';
                            if (score >= 70) {
                                scoreColor = '#f44336';
                            } else if (score >= 35) {
                                scoreColor = '#ff9800';
                            } else if (score > 0) {
                                scoreColor = '#4caf50';
                            }
                            
                            formulaDiv.innerHTML = `
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <span style="font-weight: bold; font-size: 11px;">${formulaNames[formulaKey]}</span>
                                    <span style="color: ${scoreColor}; font-weight: bold; font-size: 11px;">${score}分</span>
                                </div>
                                <div style="color: #666; font-size: 10px; margin-top: 1px; line-height: 1.2;">${description}</div>
                            `;
                            
                            formulasDiv.appendChild(formulaDiv);
                        }
                    });
                    
                    distOrWashDiv.appendChild(formulasDiv);
                }
                
                mainForceList.appendChild(distOrWashDiv);
            }
            
            // 显示新的低位吸筹分析
            if (mainForceAnalysis && mainForceAnalysis.low_level_accumulation) {
                const lowLevelAccum = mainForceAnalysis.low_level_accumulation;
                
                // 创建低位吸筹分析容器
                const lowLevelDiv = document.createElement('div');
                lowLevelDiv.className = 'low-level-accumulation';
                lowLevelDiv.style.padding = '8px';
                lowLevelDiv.style.border = '1px solid #e0e0e0';
                lowLevelDiv.style.borderRadius = '6px';
                lowLevelDiv.style.backgroundColor = '#f0f8ff';
                
                // 标题
                const titleDiv = document.createElement('div');
                titleDiv.style.fontSize = '13px';
                titleDiv.style.fontWeight = 'bold';
                titleDiv.style.color = '#333';
                titleDiv.style.marginBottom = '4px';
                titleDiv.textContent = '低位吸筹分析';
                lowLevelDiv.appendChild(titleDiv);
                
                // 总分和风险等级
                const scoreDiv = document.createElement('div');
                scoreDiv.style.marginBottom = '4px';
                
                const rawTotalScore = lowLevelAccum.total_score || 0;
                const totalScore = normalizeBehaviorScore(rawTotalScore, 100); // 低位吸筹最大100分，归一化到0-100
                const riskLevel = lowLevelAccum.risk_level || '未知';
                const riskDescription = lowLevelAccum.risk_description || '';
                const discountApplied = lowLevelAccum.discount_applied || false;
                
                // 根据风险等级设置颜色
                let riskColor = '#666';
                if (riskLevel === '高概率吸筹') {
                    riskColor = '#4caf50';
                } else if (riskLevel === '中等嫌疑') {
                    riskColor = '#ff9800';
                } else if (riskLevel === '低概率') {
                    riskColor = '#f44336';
                }
                
                // 不再显示折扣提示，因为最终得分已经包含了折扣和参数调整的影响
                scoreDiv.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                        <span style="font-weight: bold; font-size: 13px;">总分: <span style="color: ${riskColor};">${totalScore}</span></span>
                        <span style="font-weight: bold; color: ${riskColor}; font-size: 12px;">${riskLevel}</span>
                    </div>
                    <div style="color: #666; font-size: 11px;">${riskDescription}</div>
                `;
                lowLevelDiv.appendChild(scoreDiv);
                
                // 各公式详情
                if (lowLevelAccum.formulas) {
                    const formulasDiv = document.createElement('div');
                    formulasDiv.style.marginTop = '4px';
                    
                    const formulas = lowLevelAccum.formulas;
                    const formulaNames = {
                        'formula1': '公式1：日K线级低位量价匹配',
                        'formula2': '公式2：Tick级卖档压单被啃食',
                        'formula3': '公式3：Tick级分时抗跌+尾盘抢筹'
                    };
                    
                    Object.keys(formulaNames).forEach(formulaKey => {
                        if (formulas[formulaKey]) {
                            const formula = formulas[formulaKey];
                            const formulaDiv = document.createElement('div');
                            formulaDiv.style.marginBottom = '4px';
                            formulaDiv.style.padding = '4px 5px';
                            formulaDiv.style.backgroundColor = '#fff';
                            formulaDiv.style.borderRadius = '3px';
                            formulaDiv.style.border = '1px solid #ddd';
                            
                            const score = formula.score || 0;
                            const condition = formula.condition || 'normal';
                            const description = formula.description || '';
                            
                            // 根据得分设置颜色
                            let scoreColor = '#666';
                            if (score > 0) {
                                scoreColor = '#4caf50';
                            }
                            
                            formulaDiv.innerHTML = `
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <span style="font-weight: bold; font-size: 11px;">${formulaNames[formulaKey]}</span>
                                    <span style="color: ${scoreColor}; font-weight: bold; font-size: 11px;">${score}分</span>
                                </div>
                                <div style="color: #666; font-size: 10px; margin-top: 1px; line-height: 1.2;">${description}</div>
                            `;
                            
                            formulasDiv.appendChild(formulaDiv);
                        }
                    });
                    
                    lowLevelDiv.appendChild(formulasDiv);
                }
                
                mainForceList.appendChild(lowLevelDiv);
            }

            // 显示新的主力拉升分析（合并了原主力拉升和主力扫货，公式已合并）
            if (mainForceAnalysis && mainForceAnalysis.main_force_lift) {
                const mainForceLift = mainForceAnalysis.main_force_lift;

                // 创建主力拉升分析容器
                const liftDiv = document.createElement('div');
                liftDiv.className = 'main-force-lift';
                liftDiv.style.padding = '8px';
                liftDiv.style.border = '1px solid #e0e0e0';
                liftDiv.style.borderRadius = '6px';
                liftDiv.style.backgroundColor = '#fff8e1'; // Light yellow background

                // Title
                const titleDiv = document.createElement('div');
                titleDiv.style.fontSize = '13px';
                titleDiv.style.fontWeight = 'bold';
                titleDiv.style.color = '#333';
                titleDiv.style.marginBottom = '4px';
                titleDiv.textContent = '主力拉升分析';
                liftDiv.appendChild(titleDiv);

                // Total score and risk level
                const scoreDiv = document.createElement('div');
                scoreDiv.style.marginBottom = '4px';

                const rawTotalScore = mainForceLift.total_score || 0;
                const totalScore = normalizeBehaviorScore(rawTotalScore, 100); // 主力拉升最大100分，归一化到0-100
                const riskLevel = mainForceLift.risk_level || '未知';
                const riskDescription = mainForceLift.risk_description || '';

                // Set color based on risk level
                let riskColor = '#666';
                if (riskLevel === '高概率拉升') {
                    riskColor = '#4caf50'; // Green
                } else if (riskLevel === '中等概率拉升') {
                    riskColor = '#ff9800'; // Orange
                } else if (riskLevel === '低概率拉升') {
                    riskColor = '#ff9800'; // Orange
                } else if (riskLevel === '无拉升信号') {
                    riskColor = '#f44336'; // Red
                }

                scoreDiv.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                        <span style="font-weight: bold; font-size: 13px;">总分: <span style="color: ${riskColor};">${totalScore}</span></span>
                        <span style="font-weight: bold; color: ${riskColor}; font-size: 12px;">${riskLevel}</span>
                    </div>
                    <div style="color: #666; font-size: 11px;">${riskDescription}</div>
                `;
                liftDiv.appendChild(scoreDiv);

                // Formula details - 显示合并后的3个公式
                if (mainForceLift.formulas) {
                    const formulasDiv = document.createElement('div');
                    formulasDiv.style.marginTop = '4px';

                    const formulas = mainForceLift.formulas;
                    const formulaNames = {
                        'formula1': '公式1：日K线级位置判断',
                        'formula2': '公式2：Tick级主动买单',
                        'formula3': '公式3：Tick级承接/量价'
                    };

                    Object.keys(formulaNames).forEach(formulaKey => {
                        if (formulas[formulaKey]) {
                            const formula = formulas[formulaKey];
                            const formulaDiv = document.createElement('div');
                            formulaDiv.style.marginBottom = '4px';
                            formulaDiv.style.padding = '4px 5px';
                            formulaDiv.style.backgroundColor = '#fff';
                            formulaDiv.style.borderRadius = '3px';
                            formulaDiv.style.border = '1px solid #ddd';
                            
                            const score = formula.score || 0;
                            const description = formula.description || '';
                            
                            // Set color based on score
                            let scoreColor = '#666';
                            if (score >= 70) {
                                scoreColor = '#4caf50'; // Green
                            } else if (score >= 35) {
                                scoreColor = '#ff9800'; // Orange
                            } else if (score > 0) {
                                scoreColor = '#f44336'; // Red
                            }
                            
                            formulaDiv.innerHTML = `
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <span style="font-weight: bold; font-size: 11px;">${formulaNames[formulaKey]}</span>
                                    <span style="color: ${scoreColor}; font-weight: bold; font-size: 11px;">${score}分</span>
                                </div>
                                <div style="color: #666; font-size: 10px; margin-top: 1px; line-height: 1.2;">${description}</div>
                            `;
                            
                            formulasDiv.appendChild(formulaDiv);
                        }
                    });
                    
                    liftDiv.appendChild(formulasDiv);
                }

                mainForceList.appendChild(liftDiv);
            }

            // 主力洗盘分析已合并到高位出货或洗盘分析中，不再单独显示

            // 主力扫货分析已合并到主力拉升分析中，不再单独显示
            
            // 显示量化参与分析（放在涨跌停板前面，无论是否有量化参与都显示）
            if (mainForceAnalysis && mainForceAnalysis.quantitative_participation) {
                const quantitativeParticipation = mainForceAnalysis.quantitative_participation;
                
                // 创建量化参与分析容器（无论是否有量化参与都显示）
                const quantitativeDiv = document.createElement('div');
                quantitativeDiv.className = 'quantitative-participation-behavior';
                quantitativeDiv.style.padding = '10px';
                quantitativeDiv.style.border = '2px solid #9c27b0';
                quantitativeDiv.style.borderRadius = '8px';
                quantitativeDiv.style.backgroundColor = '#f3e5f5'; // 紫色背景
                quantitativeDiv.style.marginBottom = '12px';
                
                // 标题
                const titleDiv = document.createElement('div');
                titleDiv.style.fontSize = '15px';
                titleDiv.style.fontWeight = 'bold';
                titleDiv.style.color = '#7b1fa2';
                titleDiv.style.marginBottom = '6px';
                
                // 判断是否有量化参与
                if (quantitativeParticipation.has_quantitative_participation) {
                    // 判断是深度量化参与还是普通量化参与
                    const satisfiedDimensions = quantitativeParticipation.satisfied_dimensions || 0;
                    const participationType = satisfiedDimensions === 3 ? '深度量化参与' : '量化参与';
                    titleDiv.textContent = `🤖 ${participationType}（满足${satisfiedDimensions}个维度）`;
                } else {
                    titleDiv.textContent = '🤖 量化参与分析（未检测到量化参与）';
                }
                quantitativeDiv.appendChild(titleDiv);
                
                // 主导行为显示（如果有）
                if (quantitativeParticipation.dominant_behavior) {
                    const dominantDiv = document.createElement('div');
                    dominantDiv.style.marginBottom = '6px';
                    const dominantName = quantitativeParticipation.behavior_names[quantitativeParticipation.dominant_behavior] || quantitativeParticipation.dominant_behavior;
                    const rawDominantScore = quantitativeParticipation.behaviors[quantitativeParticipation.dominant_behavior] || 0;
                    const dominantScore = normalizeBehaviorScore(rawDominantScore, 100);
                    
                    dominantDiv.innerHTML = `
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <span style="font-weight: bold; font-size: 14px;">主导行为: <span style="color: #7b1fa2;">${dominantName}</span></span>
                            <span style="font-weight: bold; color: #7b1fa2; font-size: 13px;">${dominantScore}分</span>
                                </div>
                    `;
                    quantitativeDiv.appendChild(dominantDiv);
                }
                
                // 三个维度得分列表（总是显示）
                if (quantitativeParticipation.dimension_scores) {
                    const dimensionsDiv = document.createElement('div');
                    dimensionsDiv.style.marginTop = '6px';
                    
                    const dimensionLabels = {
                        'volume_fluctuation': '3秒总手数波动',
                        'order_book_changes': '盘口挂单变动',
                        'volume_price_linkage': '量价联动逻辑'
                    };
                    
                    const allDimensionKeys = ['volume_fluctuation', 'order_book_changes', 'volume_price_linkage'];
                    allDimensionKeys.forEach(dimensionKey => {
                        const rawScore = quantitativeParticipation.dimension_scores[dimensionKey] || 0;
                        const normalizedScore = normalizeBehaviorScore(rawScore, 100);
                        
                        const dimensionDiv = document.createElement('div');
                        dimensionDiv.style.marginBottom = '6px';
                        dimensionDiv.style.padding = '6px 8px';
                        dimensionDiv.style.backgroundColor = '#fff';
                        dimensionDiv.style.borderRadius = '4px';
                        dimensionDiv.style.border = '1px solid #ba68c8';
                        
                        const label = dimensionLabels[dimensionKey] || dimensionKey;
                        const scoreColor = normalizedScore >= 50 ? '#7b1fa2' : (normalizedScore > 0 ? '#ab47bc' : '#999');
                        const isSatisfied = normalizedScore >= 50 ? '✓' : '';
                        dimensionDiv.innerHTML = `
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                <span style="font-weight: bold; font-size: 12px;">${isSatisfied} ${label}</span>
                                <span style="color: ${scoreColor}; font-weight: bold; font-size: 12px;">${normalizedScore}分</span>
                                </div>
                            `;

                        dimensionsDiv.appendChild(dimensionDiv);
                    });

                    quantitativeDiv.appendChild(dimensionsDiv);
                }

                mainForceList.appendChild(quantitativeDiv);
            }
            
            // 显示涨停板行为分析（极端行情时不显示）
            if (!hasExtremeSwing && mainForceAnalysis && mainForceAnalysis.limit_up_behavior) {
                const limitUpBehavior = mainForceAnalysis.limit_up_behavior;
                
                // 检查是否有有效的行为数据
                if (limitUpBehavior.behaviors && Object.keys(limitUpBehavior.behaviors).length > 0) {
                    // 创建涨停板行为分析容器
                    const limitUpDiv = document.createElement('div');
                    limitUpDiv.className = 'limit-up-behavior';
                    limitUpDiv.style.padding = '8px';
                    limitUpDiv.style.border = '1px solid #e0e0e0';
                    limitUpDiv.style.borderRadius = '6px';
                    limitUpDiv.style.backgroundColor = '#fff3e0'; // 橙色背景
                    
                    // 标题
                    const titleDiv = document.createElement('div');
                    titleDiv.style.fontSize = '13px';
                    titleDiv.style.fontWeight = 'bold';
                    titleDiv.style.color = '#333';
                    titleDiv.style.marginBottom = '4px';
                    titleDiv.textContent = '涨停板行为分析';
                    limitUpDiv.appendChild(titleDiv);
                    
                    // 主导行为显示
                    if (limitUpBehavior.dominant_behavior) {
                        const dominantDiv = document.createElement('div');
                        dominantDiv.style.marginBottom = '4px';
                        const dominantName = limitUpBehavior.behavior_names[limitUpBehavior.dominant_behavior] || limitUpBehavior.dominant_behavior;
                        const rawDominantScore = limitUpBehavior.behaviors[limitUpBehavior.dominant_behavior];
                        const dominantScore = normalizeBehaviorScore(rawDominantScore, 100); // 涨停行为分析最大100分
                        dominantDiv.innerHTML = `
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                                <span style="font-weight: bold; font-size: 13px;">主导行为: <span style="color: #ff6600;">${dominantName}</span></span>
                                <span style="font-weight: bold; color: #ff6600; font-size: 12px;">${dominantScore}分</span>
                            </div>
                        `;
                        limitUpDiv.appendChild(dominantDiv);
                    }
                    
                    // 三种行为得分列表
                    const behaviorsDiv = document.createElement('div');
                    behaviorsDiv.style.marginTop = '4px';
                    
                    const behaviorLabels = {
                        'distribution': '诱多出货',
                        'strong_seal': '强势封板',
                        'wash': '洗盘',
                        'test': '试盘'
                    };
                    
                    // 显示所有四种行为（即使得分为0也要显示）
                    const allBehaviorKeys = ['distribution', 'strong_seal', 'wash', 'test'];
                    allBehaviorKeys.forEach(behaviorKey => {
                        const rawScore = limitUpBehavior.behaviors[behaviorKey] || 0;
                        const normalizedScore = normalizeBehaviorScore(rawScore, 100);
                        
                        const behaviorDiv = document.createElement('div');
                        behaviorDiv.style.marginBottom = '4px';
                        behaviorDiv.style.padding = '4px 5px';
                        behaviorDiv.style.backgroundColor = '#fff';
                        behaviorDiv.style.borderRadius = '3px';
                        behaviorDiv.style.border = '1px solid #ddd';
                        
                        const label = behaviorLabels[behaviorKey] || behaviorKey;
                        const scoreColor = normalizedScore > 0 ? '#ff6600' : '#999';
                        behaviorDiv.innerHTML = `
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <span style="font-weight: bold; font-size: 11px;">${label}</span>
                                <span style="color: ${scoreColor}; font-weight: bold; font-size: 11px;">${normalizedScore}分</span>
                            </div>
                        `;
                        
                        behaviorsDiv.appendChild(behaviorDiv);
                    });
                    
                    limitUpDiv.appendChild(behaviorsDiv);
                    mainForceList.appendChild(limitUpDiv);
                }
            }
            
            // 显示跌停板行为分析（极端行情时不显示）
            if (!hasExtremeSwing && mainForceAnalysis && mainForceAnalysis.limit_down_behavior) {
                const limitDownBehavior = mainForceAnalysis.limit_down_behavior;
                
                // 检查是否有有效的行为数据
                if (limitDownBehavior.behaviors && Object.keys(limitDownBehavior.behaviors).length > 0) {
                    // 创建跌停板行为分析容器
                    const limitDownDiv = document.createElement('div');
                    limitDownDiv.className = 'limit-down-behavior';
                    limitDownDiv.style.padding = '8px';
                    limitDownDiv.style.border = '1px solid #e0e0e0';
                    limitDownDiv.style.borderRadius = '6px';
                    limitDownDiv.style.backgroundColor = '#e3f2fd'; // 蓝色背景
                    
                    // 标题
                    const titleDiv = document.createElement('div');
                    titleDiv.style.fontSize = '13px';
                    titleDiv.style.fontWeight = 'bold';
                    titleDiv.style.color = '#333';
                    titleDiv.style.marginBottom = '4px';
                    titleDiv.textContent = '跌停板行为分析';
                    limitDownDiv.appendChild(titleDiv);
                    
                    // 主导行为显示
                    if (limitDownBehavior.dominant_behavior) {
                        const dominantDiv = document.createElement('div');
                        dominantDiv.style.marginBottom = '4px';
                        const dominantName = limitDownBehavior.behavior_names[limitDownBehavior.dominant_behavior] || limitDownBehavior.dominant_behavior;
                        const rawDominantScore = limitDownBehavior.behaviors[limitDownBehavior.dominant_behavior];
                        const dominantScore = normalizeBehaviorScore(rawDominantScore, 100); // 跌停行为分析最大100分
                        dominantDiv.innerHTML = `
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                                <span style="font-weight: bold; font-size: 13px;">主导行为: <span style="color: #1976d2;">${dominantName}</span></span>
                                <span style="font-weight: bold; color: #1976d2; font-size: 12px;">${dominantScore}分</span>
                            </div>
                        `;
                        limitDownDiv.appendChild(dominantDiv);
                    }
                    
                    // 三种行为得分列表
                    const behaviorsDiv = document.createElement('div');
                    behaviorsDiv.style.marginTop = '4px';
                    
                    const behaviorLabels = {
                        'wash_panic': '恐慌洗盘',
                        'distribution': '出货砸盘',
                        'passive': '被动承压'
                    };
                    
                    // 显示所有三种行为（即使得分为0也要显示）
                    const allBehaviorKeys = ['wash_panic', 'distribution', 'passive'];
                    allBehaviorKeys.forEach(behaviorKey => {
                        const rawScore = limitDownBehavior.behaviors[behaviorKey] || 0;
                        const normalizedScore = normalizeBehaviorScore(rawScore, 100);
                        
                        const behaviorDiv = document.createElement('div');
                        behaviorDiv.style.marginBottom = '4px';
                        behaviorDiv.style.padding = '4px 5px';
                        behaviorDiv.style.backgroundColor = '#fff';
                        behaviorDiv.style.borderRadius = '3px';
                        behaviorDiv.style.border = '1px solid #ddd';
                        
                        const label = behaviorLabels[behaviorKey] || behaviorKey;
                        const scoreColor = normalizedScore > 0 ? '#1976d2' : '#999';
                        behaviorDiv.innerHTML = `
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <span style="font-weight: bold; font-size: 11px;">${label}</span>
                                <span style="color: ${scoreColor}; font-weight: bold; font-size: 11px;">${normalizedScore}分</span>
                            </div>
                        `;
                        
                        behaviorsDiv.appendChild(behaviorDiv);
                    });
                    
                limitDownDiv.appendChild(behaviorsDiv);
                mainForceList.appendChild(limitDownDiv);
                }
            }
            
            // 显示极端行情分析（放在最后）
            if (hasExtremeSwing && mainForceAnalysis && mainForceAnalysis.extreme_swing_behavior) {
                const extremeSwing = mainForceAnalysis.extreme_swing_behavior;
                
                // 创建极端行情分析容器
                const extremeDiv = document.createElement('div');
                extremeDiv.className = 'extreme-swing-behavior';
                extremeDiv.style.padding = '10px';
                extremeDiv.style.border = '2px solid #ff0000';
                extremeDiv.style.borderRadius = '8px';
                extremeDiv.style.backgroundColor = '#ffebee'; // 红色背景
                extremeDiv.style.marginBottom = '12px';
                
                // 标题
                const titleDiv = document.createElement('div');
                titleDiv.style.fontSize = '15px';
                titleDiv.style.fontWeight = 'bold';
                titleDiv.style.color = '#c62828';
                titleDiv.style.marginBottom = '6px';
                titleDiv.textContent = `极端行情主力分析（切换${extremeSwing.switch_count || 0}次）`;
                extremeDiv.appendChild(titleDiv);
                
                // 主导行为显示
                if (extremeSwing.dominant_behaviors && extremeSwing.dominant_behaviors.length > 0) {
                    const dominantDiv = document.createElement('div');
                    dominantDiv.style.marginBottom = '6px';
                    
                    // 获取并列主导行为
                    const dominantBehaviors = extremeSwing.dominant_behaviors;
                    
                    // 显示主导行为名称（支持多个）
                    const dominantNames = dominantBehaviors.map(key => 
                        extremeSwing.behavior_names[key] || key
                    ).join('、');
                    
                    // 获取最高分数
                    const dominantScore = normalizeBehaviorScore(extremeSwing.max_score || 0, 100);
                    
                    dominantDiv.innerHTML = `
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <span style="font-weight: bold; font-size: 14px;">主导行为: <span style="color: #c62828;">${dominantNames}</span></span>
                            <span style="font-weight: bold; color: #c62828; font-size: 13px;">${dominantScore}分</span>
                        </div>
                    `;
                    extremeDiv.appendChild(dominantDiv);
                }
                
                // 三种行为得分列表
                const behaviorsDiv = document.createElement('div');
                behaviorsDiv.style.marginTop = '6px';
                
                const behaviorLabels = {
                    'high_distribution': '高位诱多出货',
                    'low_wash': '低位恐慌洗盘',
                    'capital_speculation': '游资短期博弈'
                };
                
                const allBehaviorKeys = ['high_distribution', 'low_wash', 'capital_speculation'];
                allBehaviorKeys.forEach(behaviorKey => {
                    const rawScore = extremeSwing.behaviors[behaviorKey] || 0;
                    const normalizedScore = normalizeBehaviorScore(rawScore, 100);
                    
                    const behaviorDiv = document.createElement('div');
                    behaviorDiv.style.marginBottom = '6px';
                    behaviorDiv.style.padding = '6px 8px';
                    behaviorDiv.style.backgroundColor = '#fff';
                    behaviorDiv.style.borderRadius = '4px';
                    behaviorDiv.style.border = '1px solid #ef9a9a';
                    
                    const label = behaviorLabels[behaviorKey] || behaviorKey;
                    const scoreColor = normalizedScore > 0 ? '#c62828' : '#999';
                    behaviorDiv.innerHTML = `
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-weight: bold; font-size: 12px;">${label}</span>
                            <span style="color: ${scoreColor}; font-weight: bold; font-size: 12px;">${normalizedScore}分</span>
                        </div>
                    `;
                    
                    behaviorsDiv.appendChild(behaviorDiv);
                });
                
                extremeDiv.appendChild(behaviorsDiv);
                mainForceList.appendChild(extremeDiv);
            }
        }
        
        function showTab(tabName) {
            console.log('[前端] 切换标签页到:', tabName);
            
            // 隐藏所有标签内容
            const tabs = ['mainForce', 'limitDetails', 'abnormalChanges', 'keyPoints'];
            tabs.forEach(tab => {
                document.getElementById(tab + 'Tab').style.display = 'none';
                document.querySelector(`[onclick="showTab('${tab}')"]`).classList.remove('active');
            });
            
            // 显示选中的标签内容
            document.getElementById(tabName + 'Tab').style.display = 'block';
            document.querySelector(`[onclick="showTab('${tabName}')"]`).classList.add('active');
        }
    </script>
</body>
</html>
            ''', port=request.environ.get('SERVER_PORT', 'Unknown'), copyright_html=copyright_html)
        
        @app.route('/api/calculate', methods=['POST'])
        def calculate():
            """计算关键价格点"""
            try:
                t0 = time.time()
                data = request.get_json()
                stock_code = data.get('stock_code', '').strip()
                client_ip_hint = (data.get('client_ip_hint') or '').strip()
                
                # 记录查询日志
                # 优先从常见代理头中解析真实客户端 IP，其次回退到 REMOTE_ADDR
                forwarded_for = request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP') or request.environ.get('HTTP_X_FORWARDED_FOR')
                if forwarded_for:
                    # 可能是逗号分隔的多级代理链，取第一个非空
                    client_ip = forwarded_for.split(',')[0].strip()
                else:
                    client_ip = request.environ.get('REMOTE_ADDR', 'unknown')
                # 如果没有通过代理头获取到真实公网IP，使用前端提供的提示
                if (not forwarded_for) and client_ip_hint:
                    client_ip = client_ip_hint
                # 去除调试日志，仅在分析接口统一记录
                query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # 仅保留分析查询的日志，避免重复记录
                # （此处不再记录“Web查询记录”，统一在分析接口记录）
                
                if not stock_code:
                    return jsonify({
                        'success': False,
                        'error': '股票代码不能为空'
                    })
                
                # 获取股票名称
                stock_name = calculator.get_stock_name(stock_code)
                
                # 计算关键价格点
                result = calculator.calculate_key_points(stock_code)
                
                if result and len(result) > 0:
                    elapsed_ms = int((time.time() - t0) * 1000)
                    return jsonify({
                        'success': True,
                        'stock_code': stock_code,
                        'stock_name': stock_name,
                        'key_points': result,
                        'elapsed_ms': elapsed_ms
                    })
                else:
                    elapsed_ms = int((time.time() - t0) * 1000)
                    return jsonify({
                        'success': False,
                        'error': '未获取到关键价格数据',
                        'elapsed_ms': elapsed_ms
                    })
                    
            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': f'计算失败: {str(e)}'
                })
        
        @app.route('/api/stock_analysis', methods=['POST'])
        def get_stock_analysis():
            """获取单股全面分析API"""
            try:
                t0 = time.time()
                data = request.get_json()
                stock_code = data.get('stock_code', '').strip()
                analysis_date_str = data.get('analysis_date', '').strip()
                client_ip_hint = (data.get('client_ip_hint') or '').strip()
                
                # 记录查询日志
                forwarded_for = request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP') or request.environ.get('HTTP_X_FORWARDED_FOR')
                if forwarded_for:
                    client_ip = forwarded_for.split(',')[0].strip()
                else:
                    client_ip = request.environ.get('REMOTE_ADDR', 'unknown')
                if (not forwarded_for) and client_ip_hint:
                    client_ip = client_ip_hint
                # 去除调试日志与多余的头部检查日志，仅保留统一的业务日志
                query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # 使用Web查询日志记录器
                if hasattr(app, 'web_query_logger') and app.web_query_logger:
                    ip_location = app.get_ip_location(client_ip)
                    app.web_query_logger.info(
                        f"Web单股分析查询 - 时间: {query_time}, IP: {client_ip}（{ip_location}）, 端口: {port}, 股票代码: {stock_code}, 分析日期: {analysis_date_str}"
                    )
                
                if not stock_code:
                    return jsonify({
                        'success': False,
                        'error': '请输入股票代码'
                    })
                
                # 清理股票代码
                clean_stock_code = stock_code.split('.')[0] if '.' in stock_code else stock_code
                
                # 处理分析日期
                from datetime import date
                if analysis_date_str:
                    try:
                        analysis_date = datetime.strptime(analysis_date_str, '%Y-%m-%d').date()
                    except ValueError:
                        return jsonify({
                            'success': False,
                            'error': '分析日期格式错误，请使用YYYY-MM-DD格式'
                        })
                else:
                    # 默认使用今天
                    analysis_date = date.today()
                
                # 使用股票分析器进行分析
                try:
                    t_analyze_start = time.time()
                    analysis_result = stock_analyzer.analyze_stock(clean_stock_code, analysis_date)
                    t_analyze_end = time.time()
                    
                    # 检查是否有错误
                    if 'error' in analysis_result:
                        return jsonify({
                            'success': False,
                            'error': analysis_result['error']
                        })
                    
                    # 获取股票名称
                    stock_name = calculator.get_stock_name(clean_stock_code)
                    
                    # 转换numpy/pandas数据类型为Python原生类型
                    def convert_types(obj):
                        if hasattr(obj, 'item'):  # numpy scalar
                            return obj.item()
                        elif hasattr(obj, 'tolist'):  # numpy array
                            return obj.tolist()
                        elif isinstance(obj, dict):
                            return {k: convert_types(v) for k, v in obj.items()}
                        elif isinstance(obj, list):
                            return [convert_types(item) for item in obj]
                        else:
                            return obj
                    
                    t_convert_start = time.time()
                    converted_result = convert_types(analysis_result)
                    t_convert_end = time.time()
                    elapsed_ms = int((time.time() - t0) * 1000)
                    
                    result_json = {
                        'success': True,
                        'stock_code': clean_stock_code,
                        'stock_name': stock_name,
                        'analysis_date': analysis_date_str,
                        'analysis_result': converted_result,
                        'elapsed_ms': elapsed_ms,
                        'timings_ms': {
                            'analyze': int((t_analyze_end - t_analyze_start) * 1000),
                            'convert': int((t_convert_end - t_convert_start) * 1000)
                        }
                    }
                    # 记录耗时
                    if hasattr(app, 'web_query_logger') and app.web_query_logger:
                        app.web_query_logger.info(
                            f"分析耗时 - 总计: {elapsed_ms}ms, analyze: {result_json['timings_ms']['analyze']}ms, convert: {result_json['timings_ms']['convert']}ms, 股票: {clean_stock_code}"
                        )
                    return jsonify(result_json)
                    
                except Exception as calc_error:
                    return jsonify({
                        'success': False,
                        'error': f'分析失败: {str(calc_error)}'
                    })
            
            except Exception as e:
                error_msg = str(e)
                # 打印完整错误信息用于调试
                print(f"[错误详情] {type(e).__name__}: {error_msg}")
                import traceback
                print(f"[错误堆栈] {traceback.format_exc()}")
                
                # 根据错误类型提供更具体的提示
                if 'tick_data' in error_msg.lower() or 'no data' in error_msg.lower():
                    error_msg = '没有找到tick数据，请检查：\n1. 股票代码是否正确\n2. 日期是否为交易日\n3. 日期是否太早（无tick数据）'
                elif 'stock_code' in error_msg.lower() or 'invalid' in error_msg.lower():
                    error_msg = '股票代码无效，请检查股票代码是否正确'
                elif 'date' in error_msg.lower() or 'time' in error_msg.lower():
                    error_msg = '日期格式错误或日期无效，请检查日期格式'
                
                return jsonify({
                    'success': False,
                    'error': error_msg
                })
        
        @app.route('/api/stock_info', methods=['POST'])
        def get_stock_info():
            """获取股票信息"""
            try:
                data = request.get_json()
                stock_code = data.get('stock_code', '').strip()
                
                if not stock_code:
                    return jsonify({
                        'success': False,
                        'error': '股票代码不能为空'
                    })
                
                # 获取股票名称
                stock_name = calculator.get_stock_name(stock_code)
                
                return jsonify({
                    'success': True,
                    'stock_code': stock_code,
                    'stock_name': stock_name
                })
                
            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': f'获取股票信息失败: {str(e)}'
                })
        
        @app.route('/api/stock_analyze_and_calculate', methods=['POST'])
        def get_stock_analyze_and_calculate():
            """合并接口：单股分析 + 关键价格点计算（共用数据）"""
            try:
                t0 = time.time()
                data = request.get_json()
                stock_code = data.get('stock_code', '').strip()
                analysis_date_str = data.get('analysis_date', '').strip()
                client_ip_hint = (data.get('client_ip_hint') or '').strip()
                
                # 记录查询日志
                forwarded_for = request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP') or request.environ.get('HTTP_X_FORWARDED_FOR')
                if forwarded_for:
                    client_ip = forwarded_for.split(',')[0].strip()
                else:
                    client_ip = request.environ.get('REMOTE_ADDR', 'unknown')
                if (not forwarded_for) and client_ip_hint:
                    client_ip = client_ip_hint
                
                query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                if not stock_code:
                    return jsonify({
                        'success': False,
                        'error': '请输入股票代码'
                    })
                
                # 清理股票代码
                clean_stock_code = stock_code.split('.')[0] if '.' in stock_code else stock_code
                
                # 处理分析日期
                from datetime import date
                if analysis_date_str:
                    try:
                        analysis_date = datetime.strptime(analysis_date_str, '%Y-%m-%d').date()
                    except ValueError:
                        return jsonify({
                            'success': False,
                            'error': '分析日期格式错误，请使用YYYY-MM-DD格式'
                        })
                else:
                    # 默认使用今天
                    analysis_date = date.today()
                
                # 获取股票名称
                stock_name = calculator.get_stock_name(clean_stock_code)
                
                # 步骤1：加载当日tick数据（用于分析）
                t_tick_start = time.time()
                from core.backtest_engine import BacktestEngine
                from utils.logger import Logger
                engine = BacktestEngine(stock_code=clean_stock_code)
                engine.set_logger(Logger())
                tick_success = engine.load_data(analysis_date, analysis_date)
                t_tick_end = time.time()
                
                if not tick_success or engine.data is None or engine.data.empty:
                    return jsonify({
                        'success': False,
                        'error': '没有找到tick数据'
                    })
                
                tick_data = engine.data
                
                # 步骤2：获取历史日线数据（阈值计算需要历史数据，无法复用tick数据）
                t_day_start = time.time()
                try:
                    # 构造完整的股票代码（带后缀）
                    full_stock_code = clean_stock_code
                    if not '.' in clean_stock_code:
                        # 根据股票代码特征判断后缀
                        if clean_stock_code.startswith(('0', '1', '3')):
                            full_stock_code = f"{clean_stock_code}.SZ"
                        elif clean_stock_code.startswith('6'):
                            full_stock_code = f"{clean_stock_code}.SH"
                        elif clean_stock_code.startswith('8') or clean_stock_code.startswith('4') or clean_stock_code.startswith('920'):
                            full_stock_code = f"{clean_stock_code}.BJ"
                    
                    daily_df = calculator._get_qmt_daily_data(full_stock_code)
                    
                    if daily_df is None or daily_df.empty:
                        return jsonify({
                            'success': False,
                            'error': '没有找到历史日线数据'
                        })
                except Exception as e:
                    return jsonify({
                        'success': False,
                        'error': f'获取历史日线数据失败: {str(e)}'
                    })
                t_day_end = time.time()
                
                # 步骤3：计算关键价格点（使用完整250天日线）
                t_keypoints_start = time.time()
                try:
                        # 临时替换calculator的数据源
                        original_get_data = calculator.get_stock_data
                        calculator.get_stock_data = lambda code: daily_df
                        
                        key_points_result = calculator.calculate_key_points(clean_stock_code)
                        
                        # 恢复原始方法
                        calculator.get_stock_data = original_get_data
                except Exception as e:
                    calculator.get_stock_data = original_get_data
                    return jsonify({
                        'success': False,
                        'error': f'关键价格点计算失败: {str(e)}'
                    })
                t_keypoints_end = time.time()
                
                # 步骤4：计算分析结果（使用analyze_stock方法）
                t_analyze_start = time.time()
                try:
                    # 使用analyze_stock方法进行分析，传入已加载的tick数据和日线数据避免重复加载
                    analysis_result = stock_analyzer.analyze_stock(clean_stock_code, analysis_date, tick_data, daily_df)
                    
                    # 开始计时：主力行为分析
                    main_force_start = time.time()
                    
                    # 执行新的高位出货或洗盘判断系统（合并了原高位出货和主力洗盘）
                    high_level_distribution_or_wash_analysis = stock_analyzer.analyze_high_level_distribution_or_wash_comprehensive(
                        daily_df, tick_data, clean_stock_code, str(analysis_date)
                    )
                    
                    # 执行新的低位吸筹判断系统
                    low_level_accumulation_analysis = stock_analyzer.analyze_low_level_accumulation_comprehensive(
                        daily_df, tick_data, clean_stock_code, str(analysis_date)
                    )
                    
                    # 执行新的主力拉升判断系统
                    main_force_lift_analysis = stock_analyzer.analyze_main_force_lift_comprehensive(
                        daily_df, tick_data, clean_stock_code, str(analysis_date)
                    )
                    
                    # 主力洗盘分析已合并到高位出货或洗盘分析中，不再单独调用
                    
                    # 主力扫货分析已合并到主力拉升分析中，不再单独调用
                    
                    # 应用优化后的参数计算最终得分（用于用户展示和判断主要行为）
                    # 硬编码的优化参数（来自优化器结果）
                    DISTRIBUTION_WEIGHT = 0.5
                    DISTRIBUTION_BIAS = 30.0
                    DISTRIBUTION_THRESHOLD = 30.0
                    LIFT_WEIGHT = 1.3
                    LIFT_BIAS = 20.0
                    LIFT_THRESHOLD = 25.0
                    ACCUMULATION_WEIGHT = 1.0
                    ACCUMULATION_BIAS = 15.0
                    ACCUMULATION_THRESHOLD = 25.0
                    
                    # 获取原始得分
                    distribution_raw_score = high_level_distribution_or_wash_analysis.get('total_score', 0)
                    lift_raw_score = main_force_lift_analysis.get('total_score', 0)
                    accumulation_raw_score = low_level_accumulation_analysis.get('total_score', 0)
                    
                    # 计算调整后的得分
                    distribution_adjusted_score = distribution_raw_score * DISTRIBUTION_WEIGHT + DISTRIBUTION_BIAS
                    lift_adjusted_score = lift_raw_score * LIFT_WEIGHT + LIFT_BIAS
                    accumulation_adjusted_score = accumulation_raw_score * ACCUMULATION_WEIGHT + ACCUMULATION_BIAS
                    
                    # 应用阈值：低于阈值的得分设为0
                    distribution_final_score = distribution_adjusted_score if distribution_adjusted_score >= DISTRIBUTION_THRESHOLD else 0
                    lift_final_score = lift_adjusted_score if lift_adjusted_score >= LIFT_THRESHOLD else 0
                    accumulation_final_score = accumulation_adjusted_score if accumulation_adjusted_score >= ACCUMULATION_THRESHOLD else 0
                    
                    # 归一化最终得分到0-100分（使用统一的最大值，保持相对关系）
                    # 理论最大值：原始得分最大100，最终得分 = 100 × 权重 + 偏移
                    DISTRIBUTION_MAX = 100 * DISTRIBUTION_WEIGHT + DISTRIBUTION_BIAS  # 100 * 0.5 + 30 = 80
                    LIFT_MAX = 100 * LIFT_WEIGHT + LIFT_BIAS  # 100 * 1.3 + 20 = 150
                    ACCUMULATION_MAX = 100 * ACCUMULATION_WEIGHT + ACCUMULATION_BIAS  # 100 * 1.0 + 15 = 115
                    
                    # 使用所有行为中的最大理论值作为统一的最大值（150），保持相对关系
                    UNIFIED_MAX = max(DISTRIBUTION_MAX, LIFT_MAX, ACCUMULATION_MAX)  # 150
                    
                    # 归一化到0-100（使用统一的最大值）
                    if UNIFIED_MAX > 0:
                        distribution_final_score = min(100, max(0, round((distribution_final_score / UNIFIED_MAX) * 100, 1)))
                        lift_final_score = min(100, max(0, round((lift_final_score / UNIFIED_MAX) * 100, 1)))
                        accumulation_final_score = min(100, max(0, round((accumulation_final_score / UNIFIED_MAX) * 100, 1)))
                    
                    # 将调整后的得分添加到分析结果中
                    high_level_distribution_or_wash_analysis['raw_score'] = distribution_raw_score
                    high_level_distribution_or_wash_analysis['adjusted_score'] = distribution_adjusted_score
                    high_level_distribution_or_wash_analysis['final_score'] = distribution_final_score
                    
                    main_force_lift_analysis['raw_score'] = lift_raw_score
                    main_force_lift_analysis['adjusted_score'] = lift_adjusted_score
                    main_force_lift_analysis['final_score'] = lift_final_score
                    
                    low_level_accumulation_analysis['raw_score'] = accumulation_raw_score
                    low_level_accumulation_analysis['adjusted_score'] = accumulation_adjusted_score
                    low_level_accumulation_analysis['final_score'] = accumulation_final_score
                    
                    # 同时更新 total_score 为最终得分（用于展示）
                    high_level_distribution_or_wash_analysis['total_score'] = distribution_final_score
                    main_force_lift_analysis['total_score'] = lift_final_score
                    low_level_accumulation_analysis['total_score'] = accumulation_final_score
                    
                    # 根据最终得分重新计算 risk_level 和 risk_description（用于拉升分析）
                    # 最终得分已归一化到0-100，阈值也需要归一化
                    # 原始得分阈值映射到归一化后的阈值：
                    # 原始得分 >= 70 → 归一化得分 >= (70 * 1.3 + 20) / 150 * 100 = 73.3
                    # 原始得分 >= 50 → 归一化得分 >= (50 * 1.3 + 20) / 150 * 100 = 56.7
                    # 原始得分 >= 35 → 归一化得分 >= (35 * 1.3 + 20) / 150 * 100 = 43.7
                    # 阈值 >= 25 → 归一化得分 >= (25 * 1.3 + 20) / 150 * 100 = 30.0
                    if lift_final_score >= 73.3:
                        main_force_lift_analysis['risk_level'] = '高概率拉升'
                        main_force_lift_analysis['risk_description'] = '趋势、主动买单、承接均强，主力真拉升（确定性/大规模拉升）'
                    elif lift_final_score >= 56.7:
                        main_force_lift_analysis['risk_level'] = '中等概率拉升'
                        main_force_lift_analysis['risk_description'] = '存在拉升信号特征，但强度中等（可能为试探性/小规模拉升）'
                    elif lift_final_score >= 43.7:
                        main_force_lift_analysis['risk_level'] = '低概率拉升'
                        main_force_lift_analysis['risk_description'] = '存在信号特征，但承接稍弱或趋势铺垫不足（试探性拉升）'
                    elif lift_final_score >= 30.0:  # 归一化后的阈值
                        main_force_lift_analysis['risk_level'] = '低概率拉升'
                        main_force_lift_analysis['risk_description'] = '存在轻微拉升信号，但强度较弱'
                    else:  # < 30.0，最终得分已被设为0
                        main_force_lift_analysis['risk_level'] = '无拉升信号'
                        main_force_lift_analysis['risk_description'] = '可能为散户跟风，非主力拉升'
                    
                    # 添加涨停板行为分析
                    limit_up_behavior_analysis = None
                    try:
                        limit_up_behavior_analysis = stock_analyzer.analyze_limit_up_behavior_comprehensive(
                            tick_data, daily_df, clean_stock_code, str(analysis_date)
                        )
                    except Exception as e:
                        print(f"涨停板行为分析失败: {e}")
                    
                    # 添加跌停板行为分析
                    limit_down_behavior_analysis = None
                    try:
                        limit_down_behavior_analysis = stock_analyzer.analyze_limit_down_behavior_comprehensive(
                            tick_data, daily_df, clean_stock_code, str(analysis_date)
                        )
                    except Exception as e:
                        print(f"跌停板行为分析失败: {e}")
                    
                    # 添加极端行情分析
                    extreme_swing_analysis = None
                    try:
                        extreme_swing_analysis = stock_analyzer.analyze_extreme_swing_behavior(
                            tick_data, daily_df, clean_stock_code, str(analysis_date)
                        )
                    except Exception as e:
                        print(f"极端行情分析失败: {e}")
                    
                    # 添加量化参与分析
                    quantitative_participation_analysis = None
                    try:
                        quantitative_participation_analysis = stock_analyzer.analyze_quantitative_participation_behavior(
                            tick_data, daily_df, clean_stock_code, str(analysis_date)
                        )
                    except Exception as e:
                        print(f"量化参与分析失败: {e}")
                    
                    # 确保 main_force_analysis 存在
                    if 'main_force_analysis' not in analysis_result:
                        analysis_result['main_force_analysis'] = {}
                    
                    # 添加新的分析到结果中
                    if extreme_swing_analysis and extreme_swing_analysis.get('is_extreme_swing'):
                        print(f"[后端] 检测到极端行情，添加极端行情分析和前5种主力分析到结果中")
                        # 如果是极端行情，添加极端行情分析和前5种主力分析，但不添加涨停/跌停板分析
                        if 'main_force_analysis' in analysis_result:
                            # 先添加前5种主力分析
                            analysis_result['main_force_analysis']['high_level_distribution_or_wash'] = high_level_distribution_or_wash_analysis
                            analysis_result['main_force_analysis']['low_level_accumulation'] = low_level_accumulation_analysis
                            analysis_result['main_force_analysis']['main_force_lift'] = main_force_lift_analysis
                            # 主力洗盘分析已合并到高位出货或洗盘分析中，不再单独存储
                            # 主力扫货分析已合并到主力拉升分析中，不再单独存储
                            # 再添加极端行情分析
                            analysis_result['main_force_analysis']['extreme_swing_behavior'] = extreme_swing_analysis
                            # 量化参与分析总是添加（无论是否检测到）
                            if quantitative_participation_analysis:
                                analysis_result['main_force_analysis']['quantitative_participation'] = quantitative_participation_analysis
                            else:
                                # 如果没有分析结果，创建一个默认的空结果
                                analysis_result['main_force_analysis']['quantitative_participation'] = {
                                    'has_quantitative_participation': False,
                                    'behaviors': {},
                                    'dominant_behavior': None,
                                    'behavior_names': {
                                        'quantitative_participation': '量化参与'
                                    },
                                    'dimension_scores': {
                                        'volume_fluctuation': 0,
                                        'order_book_changes': 0,
                                        'volume_price_linkage': 0
                                    },
                                    'satisfied_dimensions': 0
                                }
                        else:
                            analysis_result['main_force_analysis'] = {
                                'high_level_distribution_or_wash': high_level_distribution_or_wash_analysis,
                                'low_level_accumulation': low_level_accumulation_analysis,
                                'main_force_lift': main_force_lift_analysis,
                                # 主力洗盘分析已合并到高位出货或洗盘分析中，不再单独存储
                                # 主力扫货分析已合并到主力拉升分析中，不再单独存储
                                'extreme_swing_behavior': extreme_swing_analysis
                            }
                            # 量化参与分析总是添加（无论是否检测到）
                            if quantitative_participation_analysis:
                                analysis_result['main_force_analysis']['quantitative_participation'] = quantitative_participation_analysis
                            else:
                                # 如果没有分析结果，创建一个默认的空结果
                                analysis_result['main_force_analysis']['quantitative_participation'] = {
                                    'has_quantitative_participation': False,
                                    'behaviors': {},
                                    'dominant_behavior': None,
                                    'behavior_names': {
                                        'quantitative_participation': '量化参与'
                                    },
                                    'dimension_scores': {
                                        'volume_fluctuation': 0,
                                        'order_book_changes': 0,
                                        'volume_price_linkage': 0
                                    },
                                    'satisfied_dimensions': 0
                            }
                    else:
                        # 如果不是极端行情，正常添加涨停/跌停板分析
                        # 确保 main_force_analysis 存在
                        if 'main_force_analysis' not in analysis_result:
                            analysis_result['main_force_analysis'] = {}
                        
                        if 'main_force_analysis' in analysis_result:
                            analysis_result['main_force_analysis']['high_level_distribution_or_wash'] = high_level_distribution_or_wash_analysis
                            analysis_result['main_force_analysis']['low_level_accumulation'] = low_level_accumulation_analysis
                            analysis_result['main_force_analysis']['main_force_lift'] = main_force_lift_analysis
                            # 主力洗盘分析已合并到高位出货或洗盘分析中，不再单独存储
                            # 主力扫货分析已合并到主力拉升分析中，不再单独存储
                            if limit_up_behavior_analysis:
                                analysis_result['main_force_analysis']['limit_up_behavior'] = limit_up_behavior_analysis
                            if limit_down_behavior_analysis:
                                analysis_result['main_force_analysis']['limit_down_behavior'] = limit_down_behavior_analysis
                            # 量化参与分析总是添加（无论是否检测到）
                            if quantitative_participation_analysis:
                                analysis_result['main_force_analysis']['quantitative_participation'] = quantitative_participation_analysis
                            else:
                                # 如果没有分析结果，创建一个默认的空结果
                                analysis_result['main_force_analysis']['quantitative_participation'] = {
                                    'has_quantitative_participation': False,
                                    'behaviors': {},
                                    'dominant_behavior': None,
                                    'behavior_names': {
                                        'quantitative_participation': '量化参与'
                                    },
                                    'dimension_scores': {
                                        'volume_fluctuation': 0,
                                        'order_book_changes': 0,
                                        'volume_price_linkage': 0
                                    },
                                    'satisfied_dimensions': 0
                                }
                        else:
                            analysis_result['main_force_analysis'] = {
                                'high_level_distribution_or_wash': high_level_distribution_or_wash_analysis,
                                'low_level_accumulation': low_level_accumulation_analysis,
                                'main_force_lift': main_force_lift_analysis,
                                # 主力洗盘分析已合并到高位出货或洗盘分析中，不再单独存储
                                # 主力扫货分析已合并到主力拉升分析中，不再单独存储
                            }
                            if limit_up_behavior_analysis:
                                analysis_result['main_force_analysis']['limit_up_behavior'] = limit_up_behavior_analysis
                            if limit_down_behavior_analysis:
                                analysis_result['main_force_analysis']['limit_down_behavior'] = limit_down_behavior_analysis
                            # 量化参与分析总是添加（无论是否检测到）
                            if quantitative_participation_analysis:
                                analysis_result['main_force_analysis']['quantitative_participation'] = quantitative_participation_analysis
                            else:
                                # 如果没有分析结果，创建一个默认的空结果
                                analysis_result['main_force_analysis']['quantitative_participation'] = {
                                    'has_quantitative_participation': False,
                                    'behaviors': {},
                                    'dominant_behavior': None,
                                    'behavior_names': {
                                        'quantitative_participation': '量化参与'
                                    },
                                    'dimension_scores': {
                                        'volume_fluctuation': 0,
                                        'order_book_changes': 0,
                                        'volume_price_linkage': 0
                                    },
                                    'satisfied_dimensions': 0
                                }
                    
                except Exception as e:
                    return jsonify({
                        'success': False,
                        'error': f'分析失败: {str(e)}'
                    })
                t_analyze_end = time.time()
                
                # 步骤5：转换数据类型
                t_convert_start = time.time()
                def convert_types(obj):
                    if hasattr(obj, 'item'):  # numpy scalar
                        return obj.item()
                    elif hasattr(obj, 'tolist'):  # numpy array
                        return obj.tolist()
                    elif isinstance(obj, dict):
                        return {k: convert_types(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_types(item) for item in obj]
                    else:
                        return obj
                
                converted_result = convert_types(analysis_result)
                t_convert_end = time.time()
                
                # 记录合并日志
                elapsed_ms = int((time.time() - t0) * 1000)
                
                if hasattr(app, 'web_query_logger') and app.web_query_logger:
                    ip_location = app.get_ip_location(client_ip)
                    timings = {
                        'tick_load': int((t_tick_end - t_tick_start) * 1000),
                        'day_load': int((t_day_end - t_day_start) * 1000),
                        'keypoints': int((t_keypoints_end - t_keypoints_start) * 1000),
                        'analyze': int((t_analyze_end - t_analyze_start) * 1000),
                        'convert': int((t_convert_end - t_convert_start) * 1000)
                    }
                    
                    app.web_query_logger.info(
                        f"Web合并查询 - 时间: {query_time}, IP: {client_ip}（{ip_location}）, 端口: {port}, 股票代码: {clean_stock_code}, 分析日期: {analysis_date_str}, 耗时: {elapsed_ms}ms (tick_load: {timings['tick_load']}ms, day_load: {timings['day_load']}ms, keypoints: {timings['keypoints']}ms, analyze: {timings['analyze']}ms, convert: {timings['convert']}ms)"
                    )
                
                return jsonify({
                    'success': True,
                    'stock_code': clean_stock_code,
                    'stock_name': stock_name,
                    'analysis_date': analysis_date_str,
                    'analysis_result': converted_result,
                    'key_points': key_points_result,
                    'elapsed_ms': elapsed_ms,
                    'timings_ms': {
                        'tick_load': int((t_tick_end - t_tick_start) * 1000),
                        'day_load': int((t_day_end - t_day_start) * 1000),
                        'keypoints': int((t_keypoints_end - t_keypoints_start) * 1000),
                        'analyze': int((t_analyze_end - t_analyze_start) * 1000),
                        'convert': int((t_convert_end - t_convert_start) * 1000)
                    }
                })
                
            except Exception as e:
                error_msg = str(e)
                # 打印完整错误信息用于调试
                print(f"[错误详情] {type(e).__name__}: {error_msg}")
                import traceback
                print(f"[错误堆栈] {traceback.format_exc()}")
                
                if 'tick_data' in error_msg.lower() or 'no data' in error_msg.lower():
                    error_msg = '没有找到tick数据，请检查：\n1. 股票代码是否正确\n2. 日期是否为交易日\n3. 日期是否太早（无tick数据）'
                elif 'stock_code' in error_msg.lower() or 'invalid' in error_msg.lower():
                    error_msg = '股票代码无效，请检查股票代码是否正确'
                elif 'date' in error_msg.lower() or 'time' in error_msg.lower():
                    error_msg = '日期格式错误或日期无效，请检查日期格式'
                
                return jsonify({
                    'success': False,
                    'error': error_msg
                })
        
        return app
        
    except ImportError as e:
        print(f"导入模块失败: {e}")
        return None

def run_app_on_port(port):
    """在指定端口运行应用"""
    try:
        print(f"🚀 正在创建端口 {port} 的应用...")
        app = create_app(port)
        if app is None:
            print(f"❌ 端口 {port} 应用创建失败")
            return
        
        print(f"🚀 启动端口 {port}...")
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"❌ 端口 {port} 已被占用")
        else:
            print(f"❌ 端口 {port} 启动失败 (OSError): {e}")
    except Exception as e:
        print(f"❌ 端口 {port} 启动失败: {e}")
        import traceback
        traceback.print_exc()

def main():
    """主函数"""
    print("=" * 60)
    print("🌐 多端口Web应用启动器")
    print("=" * 60)
    
    # 初始化版权信息
    print("📝 初始化版权信息...")
    from copyright_manager import init_default_copyrights
    init_default_copyrights()
    
    # 默认端口列表
    default_ports = [8080, 8081, 8082, 8083]
    
    # 从命令行参数获取端口
    if len(sys.argv) > 1:
        try:
            ports = [int(p) for p in sys.argv[1:]]
        except ValueError:
            print("❌ 端口号必须是数字")
            print("用法: python multi_port_web.py [端口1] [端口2] ...")
            print("示例: python multi_port_web.py 8080 8081 8082")
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
    
    # 全局预热：只加载一次股票信息管理器
    print("🔥 全局预热股票信息管理器...")
    try:
        from key_price_calculator import KeyPriceCalculator
        calculator = KeyPriceCalculator()
        _ = calculator.get_stock_name('000001')
        print("✅ 股票信息管理器预热完成")
    except Exception as e:
        print(f"⚠ 股票信息管理器预热失败: {e}")
    
    # 创建线程启动多个端口
    threads = []
    for port in ports:
        print(f"🔄 准备启动端口 {port}...")
        thread = threading.Thread(target=run_app_on_port, args=(port,))
        thread.daemon = True
        thread.start()
        threads.append(thread)
        time.sleep(2)  # 增加等待时间，避免端口冲突
    
    # 等待所有线程启动
    time.sleep(3)
    
    print(f"\n✅ 所有服务启动完成!")
    print(f"按 Ctrl+C 停止所有服务")
    
    try:
        # 保持主线程运行
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n\n🛑 正在停止所有服务...")
        print("服务已停止")

if __name__ == '__main__':
    main()
