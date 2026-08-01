#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键价格计算Web应用
提供H5页面接口，让用户通过浏览器访问关键价格计算功能
"""

from flask import Flask, render_template, request, jsonify
import json
import os
import sys
from datetime import datetime
import traceback

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# 导入项目模块
try:
    from brokers.qmt_adapter import QMTManager
    from key_price_calculator import KeyPriceCalculator
    from core.stock_analyzer import StockAnalyzer
    from utils.logger import Logger
except ImportError as e:
    print(f"导入模块失败: {e}")
    print("请确保在项目根目录下运行此脚本")

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 支持中文

# 全局变量
qmt_adapter = None
key_calculator = None
stock_analyzer = None
logger = None

def init_qmt_adapter():
    """初始化QMT适配器和关键价格计算器"""
    global qmt_adapter, key_calculator, stock_analyzer, logger
    try:
        # 创建Web应用专用的日志记录器
        logger = Logger()
        # 为Web查询创建专门的日志文件
        import logging
        web_logger = logging.getLogger('web_query')
        web_logger.setLevel(logging.INFO)
        
        # 创建Web查询日志文件处理器
        web_log_file = os.path.join(current_dir, 'logs', 'web_query.log')
        os.makedirs(os.path.dirname(web_log_file), exist_ok=True)
        
        web_handler = logging.FileHandler(web_log_file, encoding='utf-8')
        web_handler.setLevel(logging.INFO)
        
        # 设置日志格式
        web_formatter = logging.Formatter('%(asctime)s - %(message)s')
        web_handler.setFormatter(web_formatter)
        
        # 添加处理器到Web日志记录器
        web_logger.addHandler(web_handler)
        web_logger.propagate = False  # 不传播到父记录器
        
        # 将Web日志记录器保存到全局变量
        globals()['web_query_logger'] = web_logger
        
        # QMTManager需要path和account参数，这里使用默认值
        qmt_adapter = QMTManager(path="", account="")
        # 创建关键价格计算器
        key_calculator = KeyPriceCalculator(qmt_adapter=qmt_adapter)
        # 创建股票分析器
        stock_analyzer = StockAnalyzer()
        logger.info("QMT适配器、关键价格计算器和股票分析器初始化成功")
        return True
    except Exception as e:
        print(f"QMT适配器初始化失败: {e}")
        return False

@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    """获取服务状态API"""
    return jsonify({
        'success': True,
        'qmt_adapter_available': qmt_adapter is not None,
        'key_calculator_available': key_calculator is not None,
        'message': '关键价格计算器已初始化' if key_calculator is not None else '关键价格计算器未初始化'
    })

@app.route('/api/calculate', methods=['POST'])
def calculate_key_points():
    """计算关键价格点API"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code', '').strip()
        
        # 记录查询日志
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
        query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 使用专门的Web查询日志记录器
        web_query_logger = globals().get('web_query_logger')
        if web_query_logger:
            web_query_logger.info(f"Web查询记录 - 时间: {query_time}, IP: {client_ip}, 股票代码: {stock_code}")
        
        if not stock_code:
            return jsonify({
                'success': False,
                'error': '请输入股票代码'
            })
        
        # 清理股票代码（去掉后缀）
        clean_stock_code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        
        # 检查关键价格计算器是否可用
        if key_calculator is None:
            return jsonify({
                'success': False,
                'error': 'QMT适配器初始化失败，请检查QMT软件是否正常运行'
            })
        
        # 使用关键价格计算器计算
        try:
            stock_name = key_calculator.get_stock_name(clean_stock_code)
            key_points = key_calculator.calculate_key_points(clean_stock_code)
            
            result = {
                'success': True,
                'stock_code': clean_stock_code,
                'stock_name': stock_name,
                'key_points': key_points
            }
            
            return jsonify(result)
        except Exception as calc_error:
            return jsonify({
                'success': False,
                'error': f'计算失败: {str(calc_error)}'
            })
        
    except Exception as e:
        logger.error(f"计算关键价格点失败: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': f'计算失败: {str(e)}'
        })

@app.route('/api/stock_analysis', methods=['POST'])
def get_stock_analysis():
    """获取单股全面分析API"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code', '').strip()
        analysis_date_str = data.get('analysis_date', '').strip()
        
        # 记录查询日志
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
        query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 使用专门的Web查询日志记录器
        web_query_logger = globals().get('web_query_logger')
        if web_query_logger:
            web_query_logger.info(f"Web单股分析查询 - 时间: {query_time}, IP: {client_ip}, 股票代码: {stock_code}, 分析日期: {analysis_date_str}")
        
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
        
        # 检查股票分析器是否可用
        if stock_analyzer is None:
            return jsonify({
                'success': False,
                'error': '股票分析器未初始化，请检查QMT软件是否正常运行'
            })
        
        # 使用股票分析器进行分析
        try:
            analysis_result = stock_analyzer.analyze_stock(clean_stock_code, analysis_date)
            
            # 检查是否有错误
            if 'error' in analysis_result:
                return jsonify({
                    'success': False,
                    'error': analysis_result['error']
                })
            
            # 获取股票名称
            stock_name = key_calculator.get_stock_name(clean_stock_code) if key_calculator else f"股票{clean_stock_code}"
            
            return jsonify({
                'success': True,
                'stock_code': clean_stock_code,
                'stock_name': stock_name,
                'analysis_date': analysis_date_str,
                'analysis_result': analysis_result
            })
            
        except Exception as calc_error:
            return jsonify({
                'success': False,
                'error': f'分析失败: {str(calc_error)}'
            })
        
    except Exception as e:
        logger.error(f"单股全面分析失败: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': f'分析失败: {str(e)}'
        })

@app.route('/api/stock_info', methods=['POST'])
def get_stock_info():
    """获取股票基本信息API"""
    try:
        data = request.get_json()
        stock_code = data.get('stock_code', '').strip()
        
        if not stock_code:
            return jsonify({
                'success': False,
                'error': '请输入股票代码'
            })
        
        # 清理股票代码
        clean_stock_code = stock_code.split('.')[0] if '.' in stock_code else stock_code
        
        # 获取股票名称
        stock_name = "未知股票"
        try:
            # 这里可以添加获取股票名称的逻辑
            # 暂时使用简单的方式
            stock_name = f"股票{clean_stock_code}"
        except:
            pass
        
        return jsonify({
            'success': True,
            'stock_code': clean_stock_code,
            'stock_name': stock_name
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'获取股票信息失败: {str(e)}'
        })

if __name__ == '__main__':
    # 初始化QMT适配器
    if not init_qmt_adapter():
        print("错误: QMT适配器初始化失败，Web应用无法正常运行")
        print("请检查:")
        print("1. QMT软件是否正常运行")
        print("2. QMT适配器配置是否正确")
        print("3. 网络连接是否正常")
        exit(1)
    
    # 创建templates目录
    templates_dir = os.path.join(current_dir, 'templates')
    if not os.path.exists(templates_dir):
        os.makedirs(templates_dir)
    
    # 创建static目录
    static_dir = os.path.join(current_dir, 'static')
    if not os.path.exists(static_dir):
        os.makedirs(static_dir)
    
    # 从命令行参数或环境变量获取端口
    port = 8080  # 默认端口
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print("❌ 端口号必须是数字，使用默认端口8080")
    elif 'FLASK_PORT' in os.environ:
        try:
            port = int(os.environ['FLASK_PORT'])
        except ValueError:
            print("❌ 环境变量FLASK_PORT不是有效数字，使用默认端口8080")
    
    print("关键价格计算Web应用启动中...")
    print(f"访问地址: http://localhost:{port}")
    print("按 Ctrl+C 停止服务")
    
    app.run(host='0.0.0.0', port=port, debug=True)
