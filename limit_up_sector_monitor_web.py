#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
涨停板板块监控Web应用
实时显示涨停板股票和板块统计
"""

import os
import sys
import json
import re
import argparse
import urllib.request
import urllib.error
from urllib.parse import quote
from datetime import datetime, time as dt_time, date
from flask import Flask, render_template_string, jsonify, request
import pandas as pd
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import subprocess
import platform
import glob
from typing import List, Dict, Set, Tuple

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils.limit_up_day_path import (  # noqa: E402
    ensure_limit_up_day_data_dir,
    limit_up_day_json_path,
    list_limit_up_day_dates,
    list_limit_up_day_json_files,
    resolve_limit_up_day_json_path,
)

# 检查文件是否存在
module_file = os.path.join(current_dir, 'get_limit_up_dongcai.py')
if not os.path.exists(module_file):
    print(f"错误：找不到模块文件 get_limit_up_dongcai.py")
    print(f"当前工作目录: {os.getcwd()}")
    print(f"脚本所在目录: {current_dir}")
    print(f"查找的文件路径: {module_file}")
    print(f"Python路径: {sys.path[:3]}")  # 只显示前3个路径
    sys.exit(1)

try:
    from get_limit_up_dongcai import get_limit_up_stocks_selenium
except ImportError as e:
    print(f"错误：无法导入 get_limit_up_dongcai 模块")
    print(f"详细错误信息: {e}")
    print(f"当前工作目录: {os.getcwd()}")
    print(f"脚本所在目录: {current_dir}")
    print(f"模块文件路径: {module_file}")
    print(f"文件是否存在: {os.path.exists(module_file)}")
    print(f"Python路径: {sys.path[:5]}")  # 显示前5个路径
    import traceback
    traceback.print_exc()
    sys.exit(1)

app = Flask(__name__)

# 简易IP归属地缓存: { ip: (expire_epoch_seconds, location_str) }
app._ip_loc_cache = {}

# 全局排除名单（必须在所有统计/接口路径一致生效）
# 注意：不要把“光学光电子”加入排除
EXCLUDED_CONCEPTS: Set[str] = {
    '央国企改革', '融资融券'
}

EXCLUDED_SECTORS: Set[str] = {
    '央国企改革', '融资融券',
    '深股通', '沪股通',
    '机构重仓', 'QFII重仓', '专精特新',
    '标准普尔', '富时罗素',
    '创业板综', '中证', '上证', 'MSCI中国',
    '转债标的',
    '低价股', 'AH股',
    '光学光电',
    '广东板块', '深圳特区', '北京板块', '上海板块',
    '山东板块', '四川板块', '福建板块', '湖北板块', '安徽板块', '河南板块', '湖南板块',
    '小盘股', '江苏板块', '最近多板', '长江三角', '深成',
    '一带一路', '西部大开发', '浙江板块', '中字头', '创投',
}


def _aggregate_tag_name(raw_name: str) -> str:
    """将「xxx概念」归一化为「xxx」，返回去空白后的名称。"""
    name = (raw_name or '').strip()
    if not name or name == 'nan':
        return ''
    if name.endswith('概念'):
        base = name[:-2].strip()
        return base if base else ''
    return name


def _is_excluded_tag(name: str) -> bool:
    """统一排除判断：用于行业/概念/板块/综合的所有路径。"""
    return name in EXCLUDED_SECTORS or name in EXCLUDED_CONCEPTS

# 全局数据缓存
_data_cache = {
    'limit_up_stocks': [],
    'plate_stats': [],
    'concept_stats': [],
    'sector_plate_stats': [],
    'combined_stats': [],
    'last_update_time': None,
    'is_trading_time': False,
    'lock': threading.Lock(),
    'last_browser_update_time': None  # 记录上次浏览器更新的时间
}

# 历史数据存储目录
HISTORY_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'history_data')
if not os.path.exists(HISTORY_DATA_DIR):
    os.makedirs(HISTORY_DATA_DIR)

# HTML模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>涨停板板块监控</title>
    <script>
        // 动态加载 Chart.js，支持多个备用 CDN
        (function() {
            var cdns = [
                'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js',
                'https://unpkg.com/chart.js@4.4.0/dist/chart.umd.min.js',
                'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js'
            ];
            
            function loadScript(src, onSuccess, onError) {
                var script = document.createElement('script');
                script.src = src;
                script.onload = onSuccess;
                script.onerror = onError;
                document.head.appendChild(script);
            }
            
            function tryNextCdn(index) {
                if (index >= cdns.length) {
                    console.error('所有 Chart.js CDN 都加载失败，图表功能可能无法使用');
                    return;
                }
                
                loadScript(
                    cdns[index],
                    function() {
                        console.log('Chart.js 加载成功: ' + cdns[index]);
                    },
                    function() {
                        console.warn('Chart.js CDN 加载失败: ' + cdns[index] + '，尝试下一个...');
                        tryNextCdn(index + 1);
                    }
                );
            }
            
            // 开始尝试加载第一个 CDN
            tryNextCdn(0);
        })();
    </script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Microsoft YaHei', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            overflow: hidden;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px;
            text-align: center;
        }
        
        .header h1 {
            font-size: 32px;
            margin-bottom: 10px;
        }
        
        .header .status {
            font-size: 14px;
            opacity: 0.9;
        }
        
        .content {
            padding: 25px;
        }
        
        .stats-summary {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        
        .stat-card {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            padding: 20px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        
        .stat-card .label {
            font-size: 14px;
            color: #666;
            margin-bottom: 8px;
        }
        
        .stat-card .value {
            font-size: 28px;
            font-weight: bold;
            color: #333;
            word-wrap: break-word;
            word-break: keep-all;
            overflow-wrap: break-word;
            min-height: 1.2em;
            white-space: normal;
        }
        
        .section {
            margin-bottom: 30px;
        }
        
        .section-title {
            font-size: 20px;
            font-weight: bold;
            color: #333;
            margin-bottom: 15px;
        }
        
        .ranking-tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            border-bottom: 2px solid #e0e0e0;
            padding-bottom: 0;
        }
        
        .ranking-tab {
            background: transparent;
            border: none;
            padding: 12px 20px;
            font-size: 16px;
            font-weight: 500;
            color: #666;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            transition: all 0.3s ease;
            position: relative;
            top: 2px;
        }
        
        .ranking-tab:hover {
            color: #667eea;
            background: #f5f7fa;
        }
        
        .ranking-tab.active {
            color: #667eea;
            border-bottom-color: #667eea;
            font-weight: bold;
        }
        
        .ranking-content {
            margin-top: 20px;
        }
        
        .ranking-content.hidden {
            display: none;
        }
            padding-bottom: 10px;
            border-bottom: 2px solid #667eea;
        }
        
        .table-wrapper {
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            /* 确保最后一列内容在截图中可见 */
            padding-right: 30px;
            margin-right: 10px;
            box-sizing: border-box;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            min-width: 600px; /* 确保表格有最小宽度 */
        }
        
        thead {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        th {
            padding: 15px;
            text-align: left;
            font-weight: 600;
        }
        
        td {
            padding: 12px 15px;
            border-bottom: 1px solid #eee;
        }
        
        tbody tr:hover {
            background: #f5f7fa;
        }
        
        tbody tr:last-child td {
            border-bottom: none;
        }
        
        .stock-code {
            font-family: 'Courier New', monospace;
            font-weight: bold;
            color: #667eea;
        }
        
        .stock-name {
            font-weight: 500;
        }
        
        .plate-name {
            background: #e3f2fd;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            margin-right: 5px;
            display: inline-block;
            margin-bottom: 3px;
        }
        
        .plate-count {
            font-weight: bold;
            color: #667eea;
        }
        
        .plate-card {
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 12px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.08);
            transition: all 0.3s ease;
        }
        
        .plate-card:hover {
            border-color: #667eea;
            box-shadow: 0 3px 10px rgba(102, 126, 234, 0.2);
        }
        
        .plate-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid #f0f0f0;
            flex-wrap: wrap;
            gap: 8px;
        }
        
        .plate-title {
            font-size: 16px;
            font-weight: bold;
            color: #333;
            flex: 1;
            min-width: 0;
            word-wrap: break-word;
            word-break: keep-all;
            overflow-wrap: break-word;
            white-space: normal;
            line-height: 1.3;
        }
        
        .plate-count-badge {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            flex-shrink: 0;
            white-space: nowrap;
        }
        
        .plate-stocks {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }
        
        .stock-item {
            background: #f5f7fa;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            border-left: 2px solid #667eea;
            line-height: 1.4;
        }
        
        .stock-link {
            text-decoration: none;
            color: inherit;
            display: flex;
            align-items: center;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        
        .stock-link:hover {
            opacity: 0.8;
        }
        
        .stock-item-code {
            font-family: 'Courier New', monospace;
            font-weight: bold;
            color: #667eea;
            margin-right: 4px;
            font-size: 11px;
        }
        
        .stock-item-name {
            color: #333;
            font-size: 12px;
        }
        
        /* 单只涨停行业合并显示区域 */
        .single-stock-section {
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 12px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.08);
        }
        
        .single-stock-section-title {
            font-size: 14px;
            font-weight: bold;
            color: #333;
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid #f0f0f0;
        }
        
        .single-stock-items {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }
        
        .single-stock-item {
            background: #f5f7fa;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            border-left: 2px solid #90caf9;
            line-height: 1.4;
        }
        
        .single-stock-item .industry-name {
            color: #666;
            font-size: 11px;
            margin-right: 6px;
        }
        
        .single-stock-item .stock-link {
            text-decoration: none;
            color: inherit;
            display: inline-flex;
            align-items: center;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        
        .single-stock-item .stock-link:hover {
            opacity: 0.8;
        }
        
        .single-stock-item .stock-code {
            font-family: 'Courier New', monospace;
            font-weight: bold;
            color: #667eea;
            margin-right: 4px;
            font-size: 11px;
        }
        
        .single-stock-item .stock-name {
            color: #333;
            font-size: 12px;
        }
        
        /* 自由组合样式 */
        .free-combination-container {
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        
        .free-combination-checkboxes {
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            margin-bottom: 20px;
            padding: 15px;
            background: #f5f7fa;
            border-radius: 8px;
        }
        
        .free-combination-checkbox-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            background: white;
            border-radius: 6px;
            border: 1px solid #ddd;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .free-combination-checkbox-item:hover {
            border-color: #667eea;
            background: #f0f4ff;
        }
        
        .free-combination-checkbox-item input[type="checkbox"] {
            width: 18px;
            height: 18px;
            cursor: pointer;
        }
        
        .free-combination-checkbox-item label {
            cursor: pointer;
            font-size: 14px;
            color: #333;
            user-select: none;
        }
        
        .free-combination-results {
            margin-top: 20px;
        }
        
        .free-combination-results-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #f0f0f0;
        }
        
        .free-combination-results-title {
            font-size: 16px;
            font-weight: bold;
            color: #333;
        }
        
        .free-combination-export-btn {
            background: #28a745;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            transition: transform 0.2s;
        }
        
        .free-combination-export-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        }
        
        .free-combination-export-btn:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }
        
        .free-combination-stocks-list {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        
        .free-combination-stock-item {
            background: #f5f7fa;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 14px;
            border-left: 3px solid #667eea;
        }
        
        .free-combination-stock-item .stock-link {
            text-decoration: none;
            color: inherit;
            display: flex;
            align-items: center;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        
        .free-combination-stock-item .stock-link:hover {
            opacity: 0.8;
        }
        
        .free-combination-stock-code {
            font-family: 'Courier New', monospace;
            font-weight: bold;
            color: #667eea;
            margin-right: 6px;
        }
        
        .free-combination-stock-name {
            color: #333;
        }
        
        .free-combination-empty {
            padding: 40px;
            text-align: center;
            color: #999;
            font-size: 14px;
        }
        
        .comparison-table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 10px;
            overflow: visible; /* 改为visible，确保最后一列内容不被裁剪 */
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        
        /* 板块历史分布表格的列宽设置 */
        #sector-history-table {
            table-layout: fixed; /* 固定表格布局，让列宽设置生效 */
        }
        
        #sector-history-table th:first-child,
        #sector-history-table td:first-child {
            width: 120px; /* 日期列 */
        }
        
        #sector-history-table th:nth-child(2),
        #sector-history-table td:nth-child(2) {
            width: 80px; /* 数量列 */
        }
        
        #sector-history-table th:last-child,
        #sector-history-table td:last-child {
            width: auto; /* 涨停股票列，占据剩余空间 */
        }
        
        .comparison-table thead {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .comparison-table th {
            padding: 15px;
            text-align: center;
            font-weight: bold;
            font-size: 16px;
            border-right: 1px solid rgba(255,255,255,0.2);
            width: calc(100% / 11); /* 11列等宽 */
            box-sizing: border-box;
        }
        
        .comparison-table th:last-child {
            border-right: none;
        }
        
        .comparison-table td {
            padding: 12px 15px;
            text-align: left;
            border-right: 1px solid #f0f0f0;
            vertical-align: top;
            width: calc(100% / 11); /* 11列等宽 */
            box-sizing: border-box;
            /* 确保所有列的高度一致 */
            height: 1px; /* 最小高度，让内容撑开 */
        }
        
        .comparison-table td:last-child {
            border-right: none;
        }
        
        .comparison-table tbody tr:hover {
            background: #f5f7fa;
        }
        
        .comparison-table .industry-item {
            padding: 6px 0;
            border-bottom: 1px solid #f0f0f0;
            font-size: 14px;
            line-height: 1.5;
            /* 使用最小高度，允许内容自然撑开，但保持基本对齐 */
            min-height: 1.5em; /* 最小高度，允许内容换行时自然增加高度 */
            display: flex;
            align-items: flex-start;
            gap: 8px;
        }
        
        .comparison-table .industry-item:last-child {
            border-bottom: none;
        }
        
        .comparison-table .industry-item-name {
            color: #333;
            font-weight: 500;
            /* 允许换行，但保持行高一致 */
            word-break: break-word;
            flex: 1;
            min-width: 0; /* 允许flex item收缩 */
        }
        
        .comparison-table .industry-item-count {
            color: #667eea;
            font-weight: bold;
            margin-left: 8px;
        }
        
        /* 行业最后一天数量颜色标识 */
        .comparison-table .industry-item.count-0 {
            background-color: #ffebee; /* 红色 - 最后一天没有股票 */
            padding: 6px 8px;
            border-radius: 4px;
            margin: 2px 0;
        }
        
        .comparison-table .industry-item.count-1-2 {
            background-color: #fff9c4; /* 黄色 - 最后一天有1-2只股票 */
            padding: 6px 8px;
            border-radius: 4px;
            margin: 2px 0;
        }
        
        .comparison-table .industry-item.count-2-4 {
            background-color: #e8f5e9; /* 绿色 - 最后一天有2只以上（2-4只）股票 */
            padding: 6px 8px;
            border-radius: 4px;
            margin: 2px 0;
        }
        /* 最后一天有5只以上股票的板块，不显示背景色（默认） */
        
        .loading {
            text-align: center;
            padding: 40px;
            color: #666;
        }
        
        .refresh-info {
            text-align: center;
            padding: 15px;
            background: #f5f7fa;
            color: #666;
            font-size: 14px;
        }
        
        .badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }
        
        .badge-trading {
            background: #4caf50;
            color: white;
        }
        
        .badge-closed {
            background: #9e9e9e;
            color: white;
        }
        
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            border-bottom: 2px solid #e0e0e0;
        }
        
        .tab {
            padding: 12px 24px;
            background: #f5f7fa;
            border: none;
            border-radius: 8px 8px 0 0;
            cursor: pointer;
            font-size: 16px;
            font-weight: 500;
            color: #666;
            transition: all 0.3s;
        }
        
        .tab:hover {
            background: #e0e0e0;
        }
        
        .tab.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .history-controls {
            display: flex;
            gap: 15px;
            align-items: center;
            margin-bottom: 20px;
            padding: 15px;
            background: #f5f7fa;
            border-radius: 10px;
        }
        
        .history-controls label {
            font-weight: 500;
            color: #333;
        }
        
        /* 搜索输入组：标签和输入框在同一行 */
        .search-input-group {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .search-input-group label {
            white-space: nowrap;
            flex-shrink: 0;
        }
        
        .search-input-group input[type="text"],
        .search-input-group input[type="date"] {
            flex: 1;
            min-width: 0;
        }
        
        .history-controls input[type="date"] {
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 14px;
        }
        
        .history-controls button {
            padding: 8px 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: transform 0.2s;
        }
        
        .history-controls button:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        }
        
        .history-controls button:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }
        
        .history-info {
            padding: 10px;
            background: #e3f2fd;
            border-left: 4px solid #2196f3;
            border-radius: 5px;
            margin-bottom: 20px;
            color: #1976d2;
        }
        
        .hidden {
            display: none;
        }
        
        /* 已下线的书签页：不展示（保留 DOM 以免 API/脚本引用报错） */
        .bookmark-panel-disabled {
            display: none !important;
        }
        
        /* 移动端响应式设计 */
        @media screen and (max-width: 768px) {
            body {
                padding: 10px;
            }
            
            .container {
                border-radius: 10px;
            }
            
            .header {
                padding: 15px;
            }
            
            .header h1 {
                font-size: 24px;
                margin-bottom: 8px;
            }
            
            .header .status {
                font-size: 12px;
                display: block;
                margin-top: 8px;
            }
            
            .content {
                padding: 15px;
            }
            
            .stats-summary {
                grid-template-columns: 1fr;
                gap: 10px;
                margin-bottom: 20px;
            }
            
            .stat-card {
                padding: 15px;
            }
            
            .stat-card .label {
                font-size: 13px;
                margin-bottom: 6px;
            }
            
            .stat-card .value {
                font-size: 24px;
            }
            
            .section-title {
                font-size: 18px;
                margin-bottom: 12px;
                padding-bottom: 8px;
            }
            
            /* 实时数据的两个section在移动端改为垂直堆叠 */
            #realtime-content > div[style*="display: flex"] {
                flex-direction: column !important;
                gap: 20px !important;
            }
            
            #realtime-content > div[style*="display: flex"] > .section {
                flex: none !important;
                width: 100% !important;
            }
            /* 标签页改为可横向滚动 */
            .tabs {
                display: flex;
                overflow-x: auto;
                overflow-y: hidden;
                -webkit-overflow-scrolling: touch;
                scrollbar-width: thin;
                scrollbar-color: rgba(0,0,0,0.2) transparent;
                gap: 8px;
                padding-bottom: 10px;
                margin-bottom: 15px;
                position: relative;
            }
            
            .tabs::-webkit-scrollbar {
                height: 4px;
            }
            
            .tabs::-webkit-scrollbar-track {
                background: transparent;
            }
            
            .tabs::-webkit-scrollbar-thumb {
                background: rgba(0,0,0,0.2);
                border-radius: 2px;
            }
            
            .tab {
                padding: 10px 16px;
                font-size: 14px;
                white-space: nowrap;
                flex-shrink: 0;
                min-width: fit-content;
                min-height: 44px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            /* 历史控制区域改为垂直布局 */
            .history-controls {
                flex-direction: column;
                align-items: stretch;
                gap: 10px;
                padding: 12px;
            }
            
            /* 搜索输入组：标签和输入框在同一行 */
            .search-input-group {
                display: flex;
                align-items: center;
                gap: 10px;
            }
            
            .search-input-group label {
                white-space: nowrap;
                flex-shrink: 0;
            }
            
            .search-input-group input[type="text"],
            .search-input-group input[type="date"] {
                flex: 1;
                min-width: 0;
            }
            
            .history-controls label {
                font-size: 14px;
            }
            
            .history-controls input[type="date"] {
                width: 100%;
                padding: 10px;
                font-size: 16px; /* 防止iOS缩放 */
            }
            
            /* 在搜索输入组中的日期输入框使用flex布局 */
            .search-input-group input[type="date"] {
                width: auto;
                flex: 1;
                min-width: 0;
            }
            
            .history-controls button {
                width: 100%;
                padding: 12px;
                font-size: 16px;
                min-height: 44px; /* 触摸友好 */
            }
            
            /* 表格横向滚动 */
            .table-wrapper {
                width: 100%;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                margin: 0 -15px;
                padding: 0 15px;
            }
            
            table {
                min-width: 600px;
            }
            
            .comparison-table {
                min-width: 800px; /* 确保表格有足够宽度，可以横向滚动 */
                width: 100%;
            }
            
            /* 近期综合对比表格在移动端自适应宽度 */
            #recent-combined-comparison-table {
                min-width: auto !important;
                width: 100% !important;
                table-layout: fixed !important;
            }
            
            #recent-combined-comparison-table th,
            #recent-combined-comparison-table td {
                white-space: normal !important; /* 允许内容换行 */
                word-break: break-word !important;
            }
            
            /* 板块历史分布表格在移动端自适应宽度 */
            #sector-history-table {
                min-width: auto !important;
                width: 100% !important;
                table-layout: fixed !important;
            }
            
            /* 移动端：日期列和数量列更窄 */
            #sector-history-table th:first-child,
            #sector-history-table td:first-child {
                width: 80px !important; /* 日期列：从120px缩小到80px */
                min-width: 80px !important;
            }
            
            #sector-history-table th:nth-child(2),
            #sector-history-table td:nth-child(2) {
                width: 50px !important; /* 数量列：从80px缩小到50px */
                min-width: 50px !important;
            }
            
            #sector-history-table th:last-child,
            #sector-history-table td:last-child {
                white-space: normal !important; /* 允许内容换行 */
                word-wrap: break-word !important;
                word-break: break-all !important;
            }
            
            /* 移动端隐藏导出按钮 */
            .export-button {
                display: none !important;
            }
            
            /* 移动端隐藏板块历史分布信息 */
            #sector-distribution-info {
                display: none !important;
            }
            
            .comparison-table thead,
            .comparison-table tbody {
                display: table;
                width: 100%;
                table-layout: auto;
            }
            
            .comparison-table tr {
                display: table-row;
            }
            
            .comparison-table th,
            .comparison-table td {
                display: table-cell;
                white-space: nowrap;
            }
            
            th, td {
                padding: 10px 8px;
                font-size: 13px;
            }
            
            .comparison-table th {
                font-size: 12px;
                padding: 10px 6px;
            }
            
            .comparison-table td {
                font-size: 12px;
                padding: 10px 6px;
            }
            
            .comparison-table .industry-item {
                font-size: 12px;
                padding: 5px 0;
            }
            
            /* 行业卡片优化 */
            .plate-card {
                padding: 15px;
                margin-bottom: 15px;
            }
            
            .plate-header {
                flex-direction: column;
                align-items: flex-start;
                gap: 8px;
                margin-bottom: 12px;
                padding-bottom: 12px;
            }
            
            .plate-title {
                font-size: 18px;
                width: 100%;
            }
            
            .plate-count-badge {
                font-size: 14px;
                padding: 6px 12px;
            }
            
            .plate-stocks {
                gap: 8px;
            }
            
            .stock-item {
                padding: 6px 10px;
                font-size: 13px;
            }
            
            .stock-item-code {
                font-size: 12px;
                margin-right: 4px;
            }
            
            .stock-item-name {
                font-size: 13px;
            }
            
            /* 单只涨停行业合并显示区域 - 移动端 */
            .single-stock-section {
                padding: 15px;
                margin-bottom: 15px;
            }
            
            .single-stock-section-title {
                font-size: 16px;
                margin-bottom: 12px;
                padding-bottom: 8px;
            }
            
            .single-stock-items {
                gap: 8px;
            }
            
            .single-stock-item {
                padding: 6px 10px;
                font-size: 13px;
            }
            
            .single-stock-item .industry-name {
                font-size: 11px;
                margin-right: 6px;
            }
            
            .single-stock-item .stock-code {
                font-size: 12px;
                margin-right: 4px;
            }
            
            .single-stock-item .stock-name {
                font-size: 13px;
            }
            
            .refresh-info {
                padding: 12px;
                font-size: 12px;
                line-height: 1.5;
            }
            
            .history-info {
                padding: 10px;
                font-size: 13px;
                line-height: 1.5;
            }
            
            .loading {
                padding: 30px 20px;
                font-size: 14px;
            }
            
            .badge {
                padding: 4px 8px;
                font-size: 11px;
            }
        }
        
        /* 超小屏幕（小于480px） */
        @media screen and (max-width: 480px) {
            body {
                padding: 5px;
            }
            
            .header {
                padding: 12px;
            }
            
            .header h1 {
                font-size: 20px;
            }
            
            .content {
                padding: 12px;
            }
            
            .tab {
                padding: 8px 12px;
                font-size: 12px;
            }
            
            .stat-card .value {
                font-size: 20px;
            }
            
            .section-title {
                font-size: 16px;
            }
            
            .plate-title {
                font-size: 16px;
            }
            
            th, td {
                padding: 8px 6px;
                font-size: 12px;
            }
            
            .comparison-table th,
            .comparison-table td {
                padding: 8px 4px;
                font-size: 11px;
            }
        }
        
        /* 触摸设备优化 */
        @media (hover: none) and (pointer: coarse) {
            .tab {
                min-height: 44px;
            }
            
            button {
                min-height: 44px;
                touch-action: manipulation;
            }
            
            .stock-item {
                min-height: 36px;
                display: flex;
                align-items: center;
            }
            
            /* 自由组合移动端优化 */
            .free-combination-checkboxes {
                gap: 10px;
                padding: 12px;
            }
            
            .free-combination-checkbox-item {
                padding: 6px 10px;
                font-size: 13px;
            }
            
            .free-combination-checkbox-item input[type="checkbox"] {
                width: 16px;
                height: 16px;
            }
            
            .free-combination-results-header {
                flex-direction: column;
                align-items: flex-start;
                gap: 10px;
            }
            
            .free-combination-export-btn {
                width: 100%;
                padding: 10px;
                font-size: 14px;
            }
            
            .free-combination-stocks-list {
                gap: 8px;
            }
            
            .free-combination-stock-item {
                padding: 6px 10px;
                font-size: 13px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 涨停板板块监控</h1>
            <div class="status">
                <span id="update-time">加载中...</span>
                <span id="trading-status" class="badge badge-closed">非交易时间</span>
            </div>
        </div>
        
        <div class="content">
            <div class="tabs">
                <button class="tab active" onclick="switchTab('realtime')">实时数据</button>
                <button class="tab" onclick="switchTab('history')">历史数据</button>
                <button class="tab" onclick="switchTab('recent-combined-comparison')">近期综合对比</button>
                <button class="tab" onclick="switchTab('sector-history-distribution')">板块历史分布</button>
            </div>
            
            <!-- 实时数据区域 -->
            <div id="realtime-content">
                <div class="stats-summary">
                    <div class="stat-card">
                        <div class="label">涨停股票数</div>
                        <div class="value" id="total-stocks">-</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">涉及板块数</div>
                        <div class="value" id="total-plates">-</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">最多涨停行业</div>
                        <div class="value" id="top-plate">-</div>
                    </div>
                </div>
                
                <div class="section">
                    <div class="section-title">🔧 板块自由组合</div>
                    <div id="free-combination-stats">
                            <div class="loading">加载中...</div>
                        </div>
                    </div>
                
                <div class="section">
                    <div class="section-title">🏆 涨停板板块排名</div>
                    <div id="combined-stats">
                        <div class="loading">加载中...</div>
                    </div>
                </div>
                
                <div class="refresh-info">
                    数据每1分钟自动刷新 | 交易时间（9:25-15:00）实时更新，其他时间显示最后数据
                </div>
                
                <div style="text-align: center; padding: 15px; color: #666; font-size: 12px; line-height: 1.8;">
                    <div style="margin-bottom: 8px;">
                        注：数据来源于市场公开信息，仅供参考，不作投资建议，若有误差，请以市场数据为准
                    </div>
                    <div>
                        © 2025 蚂蚁量化乐园（公众号）
                    </div>
                </div>
            </div>
            
            <!-- 历史数据区域 -->
            <div id="history-content" class="hidden">
                <div class="history-controls">
                    <label for="history-date">选择日期：</label>
                    <input type="date" id="history-date" onchange="onHistoryDateChange()">
                    <button onclick="loadHistoryData()">查询</button>
                    <button onclick="loadTodayData()">今天</button>
                </div>
                
                <div id="history-info" class="history-info hidden"></div>
                
                <div class="stats-summary">
                    <div class="stat-card">
                        <div class="label">涨停股票数</div>
                        <div class="value" id="history-total-stocks">-</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">涉及板块数</div>
                        <div class="value" id="history-total-plates">-</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">最多涨停行业</div>
                        <div class="value" id="history-top-plate">-</div>
                    </div>
                </div>
                
                <div class="section">
                    <div class="section-title">🏆 涨停板板块排名</div>
                    <div id="history-combined-stats">
                            <div class="loading">请选择日期并点击查询</div>
                        </div>
                        </div>
                    </div>
                    
            <!-- 近期综合对比区域 -->
            <div id="recent-combined-comparison-content" class="hidden">
                <div class="section">
                    <div class="section-title">📊 每日涨停板最多的板块排名</div>
                    <div id="recent-combined-comparison-stats">
                        <div class="loading">加载中...</div>
                    </div>
                </div>
            </div>
            
            <!-- 板块历史分布区域 -->
            <div id="sector-history-distribution-content" class="hidden">
                <div class="history-controls">
                    <div class="search-input-group">
                        <label for="sector-search-text">行业、概念、板块：</label>
                        <input type="text" id="sector-search-text" placeholder="输入关键词，如：人工智能、新能源等..." onkeypress="if(event.key==='Enter') loadSectorHistoryDistribution();" style="padding: 8px 12px; border: 1px solid #ddd; border-radius: 5px; font-size: 14px;">
                    </div>
                    <div class="search-input-group">
                        <label for="sector-start-date">起始日期：</label>
                        <input type="date" id="sector-start-date" style="padding: 8px 12px; border: 1px solid #ddd; border-radius: 5px; font-size: 14px;">
                    </div>
                    <button onclick="loadSectorHistoryDistribution()" style="margin-left: 15px;">查询</button>
                    <button onclick="exportSectorDistributionCsv()" class="export-button" style="margin-left: 10px; background-color: #28a745; color: white;">📥 导出分布表</button>
                    <button onclick="exportSectorDistributionStocks()" class="export-button" style="margin-left: 10px; background-color: #17a2b8; color: white;">📥 导出所有股票（按涨停次数）</button>
                </div>
                
                <div id="sector-distribution-info" class="history-info hidden"></div>
                
                <div class="section">
                    <div class="section-title">📊 板块历史分布</div>
                    <div id="sector-distribution-chart" style="margin-bottom: 20px; background: white; border-radius: 10px; padding: 20px 20px 60px 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); display: none; min-height: 450px;">
                        <canvas id="sector-chart-canvas" style="width: 100%; height: 400px; max-width: 100%;"></canvas>
                    </div>
                    <div id="sector-distribution-table">
                        <div class="loading">请输入搜索文本和起始日期，然后点击查询</div>
                    </div>
                </div>
            </div>
            
            <!-- 10日内涨停CSV数据区域（书签已隐藏，面板保留） -->
            <div id="limit-up-csv-content" class="hidden bookmark-panel-disabled">
                <div class="history-controls">
                    <label for="limit-up-csv-date">选择日期：</label>
                    <input type="date" id="limit-up-csv-date" onchange="onLimitUpCsvDateChange()">
                    <button onclick="loadLimitUpCsvData()">查询</button>
                    <button onclick="loadLimitUpCsvTodayData()">今天</button>
                    <button onclick="downloadLimitUpCsv()" style="margin-left: 10px; background-color: #28a745; color: white;">📥 下载CSV</button>
                </div>
                
                <div id="limit-up-csv-info" class="history-info hidden"></div>
                
                <div class="section">
                    <div class="section-title">📊 10日内涨停新高不高于涨停比例50%</div>
                    <div id="limit-up-csv-table">
                        <div class="loading">请选择日期并点击查询</div>
                    </div>
                </div>
            </div>
            
            <!-- 10日内接近涨停CSV数据区域（书签已隐藏，面板保留） -->
            <div id="near-limit-csv-content" class="hidden bookmark-panel-disabled">
                <div class="history-controls">
                    <label for="near-limit-csv-date">选择日期：</label>
                    <input type="date" id="near-limit-csv-date" onchange="onNearLimitCsvDateChange()">
                    <button onclick="loadNearLimitCsvData()">查询</button>
                    <button onclick="loadNearLimitCsvTodayData()">今天</button>
                    <button onclick="downloadNearLimitCsv()" style="margin-left: 10px; background-color: #28a745; color: white;">📥 下载CSV</button>
                </div>
                
                <div id="near-limit-csv-info" class="history-info hidden"></div>
                
                <div class="section">
                    <div class="section-title">📊 10日内接近涨停新高不高于涨停比例40%</div>
                    <div id="near-limit-csv-table">
                        <div class="loading">请选择日期并点击查询</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        function formatTime(timestamp) {
            if (!timestamp) return '未知';
            const date = new Date(timestamp * 1000);
            return date.toLocaleString('zh-CN');
        }
        
        function getEastMoneyLink(stockCode) {
            /**
             * 生成东方财富网页版的链接
             * @param {string} stockCode - 股票代码（6位数字）
             * @returns {string} - 东方财富网页链接
             */
            if (!stockCode) return '';
            
            const code = String(stockCode).padStart(6, '0');
            
            // 判断市场并生成对应的网页链接
            let marketPrefix;
            if (code.startsWith('60') || code.startsWith('68')) {
                // 沪市A股（60开头）或科创板（68开头）
                marketPrefix = 'sh';
                return `https://quote.eastmoney.com/${marketPrefix}${code}.html`;
            } else if (code.startsWith('00') || code.startsWith('30')) {
                // 深市A股（00/30开头）
                marketPrefix = 'sz';
                return `https://quote.eastmoney.com/${marketPrefix}${code}.html`;
            } else if (code.startsWith('92')) {
                // 北交所（92开头，新代码）
                marketPrefix = 'bj';
                return `https://quote.eastmoney.com/${marketPrefix}/${code}.html`;
            } else if (code.startsWith('83') || code.startsWith('87')) {
                // 北交所（83/87开头，旧代码）
                marketPrefix = 'bj';
                return `https://quote.eastmoney.com/${marketPrefix}${code}.html`;
            } else {
                // 默认使用沪市
                marketPrefix = 'sh';
                return `https://quote.eastmoney.com/${marketPrefix}${code}.html`;
            }
        }
        
        function createStockLink(code, name) {
            /**
             * 创建带链接的股票显示HTML（用于板块排名）
             * @param {string} code - 股票代码
             * @param {string} name - 股票名称
             * @returns {string} - HTML字符串
             */
            const formattedCode = String(code || '').padStart(6, '0');
            const link = getEastMoneyLink(formattedCode);
            
            return `
                <div class="stock-item">
                    <a href="${link}" class="stock-link" target="_blank" title="在东方财富中打开 ${name}">
                        <span class="stock-item-code">${formattedCode}</span>
                        <span class="stock-item-name">${name}</span>
                    </a>
                </div>
            `;
        }
        
        function createSingleStockLink(code, name, industryName) {
            /**
             * 创建带链接的单只涨停股票显示HTML
             * @param {string} code - 股票代码
             * @param {string} name - 股票名称
             * @param {string} industryName - 行业名称
             * @returns {string} - HTML字符串
             */
            const formattedCode = String(code || '').padStart(6, '0');
            const link = getEastMoneyLink(formattedCode);
            
            return `
                <div class="single-stock-item">
                    <span class="industry-name">${industryName}</span>
                    <a href="${link}" class="stock-link" target="_blank" title="在东方财富中打开 ${name}">
                        <span class="stock-code">${formattedCode}</span>
                        <span class="stock-name">${name}</span>
                    </a>
                </div>
            `;
        }
        
        function createStockLinkInline(code, name) {
            /**
             * 创建内联股票链接（用于表格中）
             * @param {string} code - 股票代码
             * @param {string} name - 股票名称
             * @returns {string} - HTML字符串
             */
            const formattedCode = String(code || '').padStart(6, '0');
            const link = getEastMoneyLink(formattedCode);
            
            return `<a href="${link}" target="_blank" title="在东方财富中打开 ${name}" style="color: #667eea; text-decoration: none; font-weight: bold; font-size: 10px; cursor: pointer;" onmouseover="this.style.opacity='0.8'" onmouseout="this.style.opacity='1'">${formattedCode}</a> <span style="font-size: 10px;">${name}</span>`;
        }
        
        function createFreeCombinationStockLink(code, name) {
            /**
             * 创建带链接的板块自由组合股票显示HTML
             * @param {string} code - 股票代码
             * @param {string} name - 股票名称
             * @returns {string} - HTML字符串
             */
            const formattedCode = String(code || '').padStart(6, '0');
            const link = getEastMoneyLink(formattedCode);
            
            return `
                <div class="free-combination-stock-item">
                    <a href="${link}" class="stock-link" target="_blank" title="在东方财富中打开 ${name}">
                        <span class="free-combination-stock-code">${formattedCode}</span>
                        <span class="free-combination-stock-name">${name}</span>
                    </a>
                </div>
            `;
        }
        
        let refreshInterval = null;
        let lastTradingStatus = null;
        let isUpdating = false; // 防止并发请求
        
        function updateTradingStatus(isTrading) {
            const statusEl = document.getElementById('trading-status');
            
            // 只在交易状态发生变化时才更新定时器
            if (lastTradingStatus === isTrading) {
                // 状态未变化，只更新显示
                if (isTrading) {
                    statusEl.textContent = '交易时间';
                    statusEl.className = 'badge badge-trading';
                } else {
                    statusEl.textContent = '非交易时间';
                    statusEl.className = 'badge badge-closed';
                }
                return;
            }
            
            // 状态发生变化，更新显示和定时器
            lastTradingStatus = isTrading;
            
            if (isTrading) {
                statusEl.textContent = '交易时间';
                statusEl.className = 'badge badge-trading';
                // 交易时间：启动自动刷新（确保只创建一次）
                if (refreshInterval) {
                    clearInterval(refreshInterval);
                }
                refreshInterval = setInterval(updateData, 60000);
                console.log('交易时间：已启动自动刷新（每1分钟）');
            } else {
                statusEl.textContent = '非交易时间';
                statusEl.className = 'badge badge-closed';
                // 非交易时间：停止自动刷新
                if (refreshInterval) {
                    clearInterval(refreshInterval);
                    refreshInterval = null;
                    console.log('非交易时间：已停止自动刷新');
                }
            }
        }
        
        function renderCombinedStats(data, targetElementId = 'combined-stats') {
            const targetElement = document.getElementById(targetElementId);
            if (!targetElement) return;
            
            if (!data || data.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 检测是否是实时数据区域
            const isRealtimeData = targetElementId === 'combined-stats';
            
            // 分离数量>=2的标签和数量=1的标签
            const multiStockTags = [];  // 数量>=2的标签
            const singleStockTags = []; // 数量=1的标签
            
            data.forEach((tag) => {
                if (tag.count >= 2) {
                    multiStockTags.push(tag);
                } else {
                    singleStockTags.push(tag);
                }
            });
            
            // 在实时数据区域时，只显示前10个板块（电脑端和移动端统一规则）
            let displayMultiStockTags = multiStockTags;
            if (isRealtimeData) {
                displayMultiStockTags = multiStockTags.slice(0, 10);
            }
            
            let html = '';
            
            // 在实时数据区域时，如果有多于10个板块，显示提示
            if (isRealtimeData && multiStockTags.length > 10) {
                html += `<div style="padding: 10px; background: #e3f2fd; border-left: 4px solid #2196f3; border-radius: 5px; margin-bottom: 15px; font-size: 12px; color: #1976d2;">
                    仅显示前10个板块（共${multiStockTags.length}个）
                </div>`;
            }
            
            let cardIndex = 0;
            
            // 先显示数量>=2的标签（卡片样式）
            displayMultiStockTags.forEach((tag) => {
                cardIndex++;
                // 解析股票列表（格式：代码 名称）
                const stockItems = tag.stocks.map(stockStr => {
                    const parts = stockStr.split(' ');
                    const code = parts[0] || '';
                    const name = parts.slice(1).join(' ') || '';
                    return { code, name };
                });
                
                html += `
                    <div class="plate-card">
                        <div class="plate-header">
                            <div class="plate-title">${cardIndex}. ${tag.name}</div>
                            <div class="plate-count-badge">${tag.count} 只涨停</div>
                        </div>
                        <div class="plate-stocks">
                            ${stockItems.map(stock => {
                                return createStockLink(stock.code, stock.name);
                            }).join('')}
                        </div>
                    </div>
                `;
            });
            
            // 数量=1的标签不再显示（电脑版和手机版都不显示）
            // if (singleStockTags.length > 0) {
            //     html += `
            //         <div class="single-stock-section">
            //             <div class="single-stock-section-title">单只涨停标签（${singleStockTags.length}个标签）</div>
            //             <div class="single-stock-items">
            //     `;
            //     
            //     singleStockTags.forEach((tag) => {
            //         // 解析股票列表（格式：代码 名称）
            //         const stockItems = tag.stocks.map(stockStr => {
            //             const parts = stockStr.split(' ');
            //             const code = parts[0] || '';
            //             const name = parts.slice(1).join(' ') || '';
            //             return { code, name };
            //         });
            //         
            //         stockItems.forEach(stock => {
            //             html += createSingleStockLink(stock.code, stock.name, tag.name);
            //         });
            //     });
            //     
            //     html += `
            //             </div>
            //         </div>
            //     `;
            // }
            
            targetElement.innerHTML = html;
        }
        
        function switchTab(tab) {
            // 更新标签页样式
            document.querySelectorAll('.tab').forEach(t => {
                t.classList.remove('active');
                if ((tab === 'realtime' && t.textContent === '实时数据') || 
                    (tab === 'history' && t.textContent === '历史数据') ||
                    (tab === 'recent-combined-comparison' && t.textContent === '近期综合对比') ||
                    (tab === 'sector-history-distribution' && t.textContent === '板块历史分布')) {
                    t.classList.add('active');
                }
            });
            
            // 显示/隐藏内容区域
            document.getElementById('realtime-content').classList.add('hidden');
            document.getElementById('history-content').classList.add('hidden');
            document.getElementById('recent-combined-comparison-content').classList.add('hidden');
            document.getElementById('sector-history-distribution-content').classList.add('hidden');
            document.getElementById('limit-up-csv-content').classList.add('hidden');
            document.getElementById('near-limit-csv-content').classList.add('hidden');
            
            if (tab === 'realtime') {
                document.getElementById('realtime-content').classList.remove('hidden');
            } else if (tab === 'history') {
                document.getElementById('history-content').classList.remove('hidden');
                // 切换到历史数据时，设置默认日期为今天
                const today = new Date().toISOString().split('T')[0];
                document.getElementById('history-date').value = today;
            } else if (tab === 'recent-combined-comparison') {
                document.getElementById('recent-combined-comparison-content').classList.remove('hidden');
                // 切换到近期综合对比时，自动加载数据
                loadRecentCombinedComparison();
            } else if (tab === 'sector-history-distribution') {
                document.getElementById('sector-history-distribution-content').classList.remove('hidden');
                // 切换到板块历史分布时，设置默认起始日期为2025-11-24
                document.getElementById('sector-start-date').value = '2025-11-24';
            }
        }
        
        function onHistoryDateChange() {
            // 日期改变时的处理（可以在这里添加一些逻辑）
        }
        
        function loadTodayData() {
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('history-date').value = today;
            loadHistoryData();
        }
        
        function loadHistoryData() {
            const dateInput = document.getElementById('history-date');
            const dateStr = dateInput.value;
            
            if (!dateStr) {
                alert('请选择日期');
                return;
            }
            
            // 显示加载状态
            document.getElementById('history-combined-stats').innerHTML = '<div class="loading">加载中...</div>';
            document.getElementById('history-info').classList.add('hidden');
            
            // 查询历史数据
            fetch(addClientIpHint(`/api/history?date=${dateStr}`))
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 更新统计信息
                        document.getElementById('history-total-stocks').textContent = data.total_stocks || 0;
                        document.getElementById('history-total-plates').textContent = data.total_combined_tags || 0;
                        document.getElementById('history-top-plate').textContent = 
                            data.combined_stats && data.combined_stats.length > 0 ? data.combined_stats[0].name : '-';
                        
                        // 显示综合排名
                        renderCombinedStats(data.combined_stats || [], 'history-combined-stats');
                        
                        // 显示日期信息
                        const infoEl = document.getElementById('history-info');
                        const dateObj = new Date(data.timestamp * 1000);
                        infoEl.textContent = `日期：${data.date} | 数据时间：${dateObj.toLocaleString('zh-CN')}`;
                        infoEl.classList.remove('hidden');
                    } else {
                        document.getElementById('history-combined-stats').innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                        document.getElementById('history-info').classList.add('hidden');
                    }
                })
                .catch(error => {
                    console.error('加载历史数据失败:', error);
                    document.getElementById('history-combined-stats').innerHTML = 
                        '<div class="loading">加载失败，请稍后重试</div>';
                    document.getElementById('history-info').classList.add('hidden');
                });
        }
        
        function onLimitUpCsvDateChange() {
            // 日期改变时的处理
        }
        
        function loadLimitUpCsvTodayData() {
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('limit-up-csv-date').value = today;
            loadLimitUpCsvData();
        }
        
        function loadLimitUpCsvData() {
            const dateInput = document.getElementById('limit-up-csv-date');
            const dateStr = dateInput.value;
            
            if (!dateStr) {
                alert('请选择日期');
                return;
            }
            
            // 显示加载状态
            document.getElementById('limit-up-csv-table').innerHTML = '<div class="loading">加载中...</div>';
            document.getElementById('limit-up-csv-info').classList.add('hidden');
            
            // 查询CSV数据
            fetch(addClientIpHint(`/api/csv/limit-up?date=${dateStr}`))
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 显示表格
                        renderCsvTable(data.rows, data.columns, 'limit-up-csv-table');
                        
                        // 显示文件信息
                        const infoEl = document.getElementById('limit-up-csv-info');
                        infoEl.textContent = `日期：${data.date} | 文件：${data.filename} | 记录数：${data.row_count}`;
                        infoEl.classList.remove('hidden');
                    } else {
                        document.getElementById('limit-up-csv-table').innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                        document.getElementById('limit-up-csv-info').classList.add('hidden');
                    }
                })
                .catch(error => {
                    console.error('加载CSV数据失败:', error);
                    document.getElementById('limit-up-csv-table').innerHTML = 
                        '<div class="loading">加载失败，请稍后重试</div>';
                    document.getElementById('limit-up-csv-info').classList.add('hidden');
                });
        }
        
        function onNearLimitCsvDateChange() {
            // 日期改变时的处理
        }
        
        function loadNearLimitCsvTodayData() {
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('near-limit-csv-date').value = today;
            loadNearLimitCsvData();
        }
        
        function loadNearLimitCsvData() {
            const dateInput = document.getElementById('near-limit-csv-date');
            const dateStr = dateInput.value;
            
            if (!dateStr) {
                alert('请选择日期');
                return;
            }
            
            // 显示加载状态
            document.getElementById('near-limit-csv-table').innerHTML = '<div class="loading">加载中...</div>';
            document.getElementById('near-limit-csv-info').classList.add('hidden');
            
            // 查询CSV数据
            fetch(addClientIpHint(`/api/csv/near-limit?date=${dateStr}`))
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 显示表格
                        renderCsvTable(data.rows, data.columns, 'near-limit-csv-table');
                        
                        // 显示文件信息
                        const infoEl = document.getElementById('near-limit-csv-info');
                        infoEl.textContent = `日期：${data.date} | 文件：${data.filename} | 记录数：${data.row_count}`;
                        infoEl.classList.remove('hidden');
                    } else {
                        document.getElementById('near-limit-csv-table').innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                        document.getElementById('near-limit-csv-info').classList.add('hidden');
                    }
                })
                .catch(error => {
                    console.error('加载CSV数据失败:', error);
                    document.getElementById('near-limit-csv-table').innerHTML = 
                        '<div class="loading">加载失败，请稍后重试</div>';
                    document.getElementById('near-limit-csv-info').classList.add('hidden');
                });
        }
        
        function renderCsvTable(rows, columns, targetElementId) {
            const targetElement = document.getElementById(targetElementId);
            if (!targetElement) return;
            
            if (!rows || rows.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            let html = '<table><thead><tr>';
            
            // 表头
            if (columns && columns.length > 0) {
                columns.forEach(col => {
                    html += `<th>${col}</th>`;
                });
            } else if (rows.length > 0) {
                // 如果没有列名，使用第一行的键作为列名
                Object.keys(rows[0]).forEach(key => {
                    html += `<th>${key}</th>`;
                });
            }
            
            html += '</tr></thead><tbody>';
            
            // 数据行
            rows.forEach(row => {
                html += '<tr>';
                if (columns && columns.length > 0) {
                    columns.forEach(col => {
                        let value = row[col] !== undefined ? row[col] : '';
                        // 如果是代码列，格式化为6位，不足6位前面补0
                        if ((col === '代码' || col === '股票代码' || col === 'code' || col === '证券代码') && value) {
                            value = String(value).padStart(6, '0');
                        }
                        html += `<td>${value}</td>`;
                    });
                } else {
                    Object.entries(row).forEach(([key, value]) => {
                        // 如果是代码列，格式化为6位，不足6位前面补0
                        if ((key === '代码' || key === '股票代码' || key === 'code' || key === '证券代码') && value) {
                            value = String(value).padStart(6, '0');
                        }
                        html += `<td>${value !== undefined ? value : ''}</td>`;
                    });
                }
                html += '</tr>';
            });
            
            html += '</tbody></table>';
            // 添加表格包装器以支持移动端横向滚动
            targetElement.innerHTML = '<div class="table-wrapper">' + html + '</div>';
        }
        
        function downloadLimitUpCsv() {
            const dateInput = document.getElementById('limit-up-csv-date');
            const dateStr = dateInput.value;
            
            if (!dateStr) {
                alert('请先选择日期');
                return;
            }
            
            // 构建下载URL
            const downloadUrl = `/api/csv/limit-up/download?date=${dateStr}`;
            
            // 创建临时链接并触发下载
            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = '';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
        
        function downloadNearLimitCsv() {
            const dateInput = document.getElementById('near-limit-csv-date');
            const dateStr = dateInput.value;
            
            if (!dateStr) {
                alert('请先选择日期');
                return;
            }
            
            // 构建下载URL
            const downloadUrl = `/api/csv/near-limit/download?date=${dateStr}`;
            
            // 创建临时链接并触发下载
            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = '';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
        
        function loadRecentComparison() {
            // 显示加载状态
            document.getElementById('recent-comparison-stats').innerHTML = '<div class="loading">加载中...</div>';
            
            // 查询近期行业对比数据
            fetch(addClientIpHint('/api/recent-comparison'))
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 显示对比数据
                        renderRecentComparison(data.comparison_data);
                    } else {
                        document.getElementById('recent-comparison-stats').innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                    }
                })
                .catch(error => {
                    console.error('加载近期行业对比数据失败:', error);
                    document.getElementById('recent-comparison-stats').innerHTML = 
                        '<div class="loading">加载失败，请稍后重试</div>';
                });
        }
        
        function renderRecentComparison(comparisonData) {
            const targetElement = document.getElementById('recent-comparison-stats');
            if (!targetElement) return;
            
            if (!comparisonData || comparisonData.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 按日期排序（从远到近，即最早的在前）
            const sortedData = [...comparisonData].sort((a, b) => {
                return new Date(a.date) - new Date(b.date);
            });
            
            // 只取最近7个交易日
            const recent7Days = sortedData.slice(-7);
            
            if (recent7Days.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 获取最后一天（最新一天）的数据
            const lastDayData = recent7Days[recent7Days.length - 1];
            
            // 构建最后一天每个行业的数量映射
            const lastDayIndustryCount = new Map();
            if (lastDayData && lastDayData.industries && lastDayData.industries.length > 0) {
                lastDayData.industries.forEach(industry => {
                    lastDayIndustryCount.set(industry.name, industry.count);
                });
            }
            
            // 根据最后一天的数量获取CSS类名
            function getOccurrenceClass(industryName) {
                const count = lastDayIndustryCount.get(industryName) || 0;
                if (count === 0) {
                    return 'count-0'; // 红色 - 最后一天没有股票
                } else if (count >= 1 && count <= 2) {
                    return 'count-1-2'; // 黄色 - 最后一天有1-2只股票
                } else if (count >= 3 && count <= 5) {
                    return 'count-2-4'; // 绿色 - 最后一天有3-5只股票
                }
                // count >= 6，不添加颜色类，使用默认背景
                return '';
            }
            
            // 生成表格HTML
            let html = '<table class="comparison-table" id="recent-comparison-table">';
            
            // 表头：日期（从远到近）
            html += '<thead><tr>';
            recent7Days.forEach(dayData => {
                html += `<th>${dayData.date}</th>`;
            });
            html += '</tr></thead>';
            
            // 表体：只有一行，每列显示该日期的行业列表
            html += '<tbody><tr>';
            recent7Days.forEach(dayData => {
                html += '<td>';
                // 行业已经按数量从大到小排序（后端已处理）
                if (dayData.industries && dayData.industries.length > 0) {
                    dayData.industries.forEach(industry => {
                        const occurrenceClass = getOccurrenceClass(industry.name);
                        const classAttr = occurrenceClass ? ` class="industry-item ${occurrenceClass}"` : ' class="industry-item"';
                        // 添加 data-industry-name 属性用于标识行业
                        html += `<div${classAttr} data-industry-name="${industry.name.replace(/"/g, '&quot;')}">`;
                        html += `<span class="industry-item-name">${industry.name}</span>`;
                        html += `<span class="industry-item-count">${industry.count}</span>`;
                        html += `</div>`;
                    });
                } else {
                    html += '<div class="loading" style="padding: 20px; color: #999;">暂无数据</div>';
                }
                html += '</td>';
            });
            html += '</tr></tbody>';
            
            html += '</table>';
            
            // 添加表格包装器以支持移动端横向滚动
            targetElement.innerHTML = '<div class="table-wrapper">' + html + '</div>';
            
            // 添加鼠标悬停高亮功能
            setupIndustryHoverHighlight('recent-comparison-table');
        }
        
        // 全局状态存储，用于保存每个表格的状态
        const tableHoverStates = new Map();
        
        // 设置行业悬停高亮功能
        function setupIndustryHoverHighlight(tableId) {
            const table = document.getElementById(tableId);
            if (!table) return;
            
            // 如果表格已经存在状态，先清除之前的定时器
            if (tableHoverStates.has(tableId)) {
                const oldState = tableHoverStates.get(tableId);
                if (oldState.hoverTimer) {
                    clearTimeout(oldState.hoverTimer);
                }
                if (oldState.mouseLeaveTimer) {
                    clearTimeout(oldState.mouseLeaveTimer);
                }
            }
            
            // 获取或创建状态对象
            let state = tableHoverStates.get(tableId);
            if (!state) {
                state = {
                    hoverTimer: null,
                    highlightedIndustry: null,
                    targetIndustryName: null,
                    isMouseInTable: false,
                    mouseLeaveTimer: null
                };
                tableHoverStates.set(tableId, state);
            } else {
                // 清除之前的定时器
                if (state.hoverTimer) {
                    clearTimeout(state.hoverTimer);
                    state.hoverTimer = null;
                }
                if (state.mouseLeaveTimer) {
                    clearTimeout(state.mouseLeaveTimer);
                    state.mouseLeaveTimer = null;
                }
                // 重置状态（但保留引用）
                state.highlightedIndustry = null;
                state.targetIndustryName = null;
                state.isMouseInTable = false;
            }
            
            // 过滤显示特定行业
            function filterIndustry(industryName) {
                if (!industryName) {
                    // 恢复显示所有行业
                    const allItems = table.querySelectorAll('.industry-item');
                    allItems.forEach(item => {
                        item.style.visibility = '';
                        item.style.opacity = '';
                    });
                    state.highlightedIndustry = null;
                    state.targetIndustryName = null;
                    return;
                }
                
                // 隐藏其他行业，但保持它们占据空间（保持表格高度不变）
                const allItems = table.querySelectorAll('.industry-item');
                allItems.forEach(item => {
                    const itemIndustryName = item.getAttribute('data-industry-name');
                    if (itemIndustryName === industryName) {
                        // 显示目标行业
                        item.style.visibility = '';
                        item.style.opacity = '';
                    } else {
                        // 隐藏其他行业，但保持占据空间
                        item.style.visibility = 'hidden';
                        item.style.opacity = '0';
                    }
                });
                state.highlightedIndustry = industryName;
            }
            
            // 获取表格容器
            const tableWrapper = table.closest('.table-wrapper') || table.parentElement;
            
            // 跟踪全局鼠标位置（只添加一次监听器）
            if (!window._globalMouseTrackerAdded) {
                window._globalMouseX = 0;
                window._globalMouseY = 0;
                document.addEventListener('mousemove', function(e) {
                    window._globalMouseX = e.clientX;
                    window._globalMouseY = e.clientY;
                });
                window._globalMouseTrackerAdded = true;
            }
            
            // 在表格级别监听 mouseover 事件
            table.addEventListener('mouseover', function(e) {
                // 清除离开定时器
                if (state.mouseLeaveTimer) {
                    clearTimeout(state.mouseLeaveTimer);
                    state.mouseLeaveTimer = null;
                }
                
                // 检查鼠标是否悬停在行业项上
                const industryItem = e.target.closest('.industry-item');
                if (industryItem) {
                    const industryName = industryItem.getAttribute('data-industry-name');
                    if (industryName) {
                        // 如果已经高亮显示这个行业，不需要重新设置
                        if (industryName === state.highlightedIndustry) {
                            return;
                        }
                        
                        // 设置目标行业
                        state.targetIndustryName = industryName;
                        
                        // 清除之前的定时器
                        if (state.hoverTimer) {
                            clearTimeout(state.hoverTimer);
                        }
                        
                        // 设置2秒延迟，给用户足够的时间查看
                        state.hoverTimer = setTimeout(() => {
                            // 再次检查：鼠标是否还在表格内，且目标行业没有改变
                            if (state.isMouseInTable && state.targetIndustryName === industryName) {
                                filterIndustry(industryName);
                            }
                        }, 2000);
                    }
                }
                // 如果鼠标不在行业项上，但还在表格内，不清除定时器
            });
            
            // 监听表格区域的鼠标进入和离开
            if (tableWrapper) {
                tableWrapper.addEventListener('mouseenter', function(e) {
                    state.isMouseInTable = true;
                    // 清除离开定时器
                    if (state.mouseLeaveTimer) {
                        clearTimeout(state.mouseLeaveTimer);
                        state.mouseLeaveTimer = null;
                    }
                });
                
                tableWrapper.addEventListener('mouseleave', function(e) {
                    // 延迟清除，避免快速移动时误触发
                    if (state.mouseLeaveTimer) {
                        clearTimeout(state.mouseLeaveTimer);
                    }
                    state.mouseLeaveTimer = setTimeout(() => {
                        // 检查 relatedTarget 是否在表格内
                        const relatedTarget = e.relatedTarget;
                        if (relatedTarget && (tableWrapper.contains(relatedTarget) || table.contains(relatedTarget))) {
                            // 鼠标还在表格内，不清除
                            return;
                        }
                        
                        // 使用全局鼠标位置检查是否在可见的表格行内
                        // 这样可以处理表格缩小到一行时的情况
                        const elementAtPoint = document.elementFromPoint(window._globalMouseX || 0, window._globalMouseY || 0);
                        if (elementAtPoint) {
                            // 检查鼠标当前位置是否在可见的表格行内
                            const visibleItem = elementAtPoint.closest('.industry-item');
                            if (visibleItem) {
                                // 检查这个行是否可见（没有被隐藏）
                                const style = window.getComputedStyle(visibleItem);
                                if (style.visibility !== 'hidden' && style.opacity !== '0') {
                                    // 鼠标还在可见行内，不清除过滤
                                    state.isMouseInTable = true;
                                    return;
                                }
                            }
                            
                            // 检查是否在表格容器内
                            if (tableWrapper.contains(elementAtPoint) || table.contains(elementAtPoint)) {
                                state.isMouseInTable = true;
                                return;
                            }
                        }
                        
                        // 鼠标真的离开了表格，清除状态
                        state.isMouseInTable = false;
                        if (state.hoverTimer) {
                            clearTimeout(state.hoverTimer);
                            state.hoverTimer = null;
                        }
                        state.targetIndustryName = null;
                        filterIndustry(null);
                    }, 600); // 延迟600ms检查，给足够的时间让用户操作
                });
            }
            
            // 在表格上监听 mouseover，确保鼠标在表格内移动时不会清除状态
            // 这个事件会在鼠标在表格内移动时持续触发
            table.addEventListener('mouseover', function(e) {
                // 只要鼠标在表格内移动，就清除离开定时器
                if (state.mouseLeaveTimer) {
                    clearTimeout(state.mouseLeaveTimer);
                    state.mouseLeaveTimer = null;
                }
                // 确保 isMouseInTable 为 true
                state.isMouseInTable = true;
            });
        }
        
        function loadRecentConceptComparison() {
            // 显示加载状态
            document.getElementById('recent-concept-comparison-stats').innerHTML = '<div class="loading">加载中...</div>';
            
            // 查询近期概念对比数据
            fetch(addClientIpHint('/api/recent-concept-comparison'))
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 显示对比数据
                        renderRecentConceptComparison(data.comparison_data);
                    } else {
                        document.getElementById('recent-concept-comparison-stats').innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                    }
                })
                .catch(error => {
                    console.error('加载近期概念对比数据失败:', error);
                    document.getElementById('recent-concept-comparison-stats').innerHTML = 
                        '<div class="loading">加载失败，请稍后重试</div>';
                });
        }
        
        function renderRecentConceptComparison(comparisonData) {
            const targetElement = document.getElementById('recent-concept-comparison-stats');
            if (!targetElement) return;
            
            if (!comparisonData || comparisonData.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 按日期排序（从远到近，即最早的在前）
            const sortedData = [...comparisonData].sort((a, b) => {
                return new Date(a.date) - new Date(b.date);
            });
            
            // 只取最近7个交易日
            const recent7Days = sortedData.slice(-7);
            
            if (recent7Days.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 获取最后一天（最新一天）的数据
            const lastDayData = recent7Days[recent7Days.length - 1];
            
            // 构建最后一天每个概念的数量映射
            const lastDayConceptCount = new Map();
            if (lastDayData && lastDayData.concepts && lastDayData.concepts.length > 0) {
                lastDayData.concepts.forEach(concept => {
                    lastDayConceptCount.set(concept.name, concept.count);
                });
            }
            
            // 根据最后一天的数量获取CSS类名
            function getOccurrenceClass(conceptName) {
                const count = lastDayConceptCount.get(conceptName) || 0;
                if (count === 0) {
                    return 'count-0'; // 红色 - 最后一天没有股票
                } else if (count >= 1 && count <= 2) {
                    return 'count-1-2'; // 黄色 - 最后一天有1-2只股票
                } else if (count >= 3 && count <= 5) {
                    return 'count-2-4'; // 绿色 - 最后一天有3-5只股票
                }
                // count >= 6，不添加颜色类，使用默认背景
                return '';
            }
            
            // 生成表格HTML
            let html = '<table class="comparison-table" id="recent-concept-comparison-table">';
            
            // 表头：日期（从远到近）
            html += '<thead><tr>';
            recent7Days.forEach(dayData => {
                html += `<th>${dayData.date}</th>`;
            });
            html += '</tr></thead>';
            
            // 表体：只有一行，每列显示该日期的概念列表
            html += '<tbody><tr>';
            recent7Days.forEach(dayData => {
                html += '<td>';
                // 概念已经按数量从大到小排序（后端已处理）
                if (dayData.concepts && dayData.concepts.length > 0) {
                    dayData.concepts.forEach(concept => {
                        const occurrenceClass = getOccurrenceClass(concept.name);
                        const classAttr = occurrenceClass ? ` class="industry-item ${occurrenceClass}"` : ' class="industry-item"';
                        // 添加 data-industry-name 属性用于标识概念
                        html += `<div${classAttr} data-industry-name="${concept.name.replace(/"/g, '&quot;')}">`;
                        html += `<span class="industry-item-name">${concept.name}</span>`;
                        html += `<span class="industry-item-count">${concept.count}</span>`;
                        html += `</div>`;
                    });
                } else {
                    html += '<div class="loading" style="padding: 20px; color: #999;">暂无数据</div>';
                }
                html += '</td>';
            });
            html += '</tr></tbody>';
            
            html += '</table>';
            
            // 添加表格包装器以支持移动端横向滚动
            targetElement.innerHTML = '<div class="table-wrapper">' + html + '</div>';
            
            // 添加鼠标悬停高亮功能
            setupIndustryHoverHighlight('recent-concept-comparison-table');
        }
        
        function loadRecentSectorComparison() {
            // 显示加载状态
            document.getElementById('recent-sector-comparison-stats').innerHTML = '<div class="loading">加载中...</div>';
            
            // 查询近期板块对比数据
            fetch(addClientIpHint('/api/recent-sector-comparison'))
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 显示对比数据
                        renderRecentSectorComparison(data.comparison_data);
                    } else {
                        document.getElementById('recent-sector-comparison-stats').innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                    }
                })
                .catch(error => {
                    console.error('加载近期板块对比数据失败:', error);
                    document.getElementById('recent-sector-comparison-stats').innerHTML = 
                        '<div class="loading">加载失败，请稍后重试</div>';
                });
        }
        
        function renderRecentSectorComparison(comparisonData) {
            const targetElement = document.getElementById('recent-sector-comparison-stats');
            if (!targetElement) return;
            
            if (!comparisonData || comparisonData.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 按日期排序（从远到近，即最早的在前）
            const sortedData = [...comparisonData].sort((a, b) => {
                return new Date(a.date) - new Date(b.date);
            });
            
            // 只取最近7个交易日
            const recent7Days = sortedData.slice(-7);
            
            if (recent7Days.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 获取最后一天（最新一天）的数据
            const lastDayData = recent7Days[recent7Days.length - 1];
            
            // 构建最后一天每个板块的数量映射
            const lastDaySectorCount = new Map();
            if (lastDayData && lastDayData.sectors && lastDayData.sectors.length > 0) {
                lastDayData.sectors.forEach(sector => {
                    lastDaySectorCount.set(sector.name, sector.count);
                });
            }
            
            // 根据最后一天的数量获取CSS类名
            function getOccurrenceClass(sectorName) {
                const count = lastDaySectorCount.get(sectorName) || 0;
                if (count === 0) {
                    return 'count-0'; // 红色 - 最后一天没有股票
                } else if (count >= 1 && count <= 2) {
                    return 'count-1-2'; // 黄色 - 最后一天有1-2只股票
                } else if (count >= 3 && count <= 5) {
                    return 'count-2-4'; // 绿色 - 最后一天有3-5只股票
                }
                // count >= 6，不添加颜色类，使用默认背景
                return '';
            }
            
            // 生成表格HTML
            let html = '<table class="comparison-table" id="recent-sector-comparison-table">';
            
            // 表头：日期（从远到近）
            html += '<thead><tr>';
            recent7Days.forEach(dayData => {
                html += `<th>${dayData.date}</th>`;
            });
            html += '</tr></thead>';
            
            // 表体：只有一行，每列显示该日期的板块列表
            html += '<tbody><tr>';
            recent7Days.forEach(dayData => {
                html += '<td>';
                // 板块已经按数量从大到小排序（后端已处理）
                if (dayData.sectors && dayData.sectors.length > 0) {
                    dayData.sectors.forEach(sector => {
                        const occurrenceClass = getOccurrenceClass(sector.name);
                        const classAttr = occurrenceClass ? ` class="industry-item ${occurrenceClass}"` : ' class="industry-item"';
                        // 添加 data-industry-name 属性用于标识板块
                        html += `<div${classAttr} data-industry-name="${sector.name.replace(/"/g, '&quot;')}">`;
                        html += `<span class="industry-item-name">${sector.name}</span>`;
                        html += `<span class="industry-item-count">${sector.count}</span>`;
                        html += `</div>`;
                    });
                } else {
                    html += '<div class="loading" style="padding: 20px; color: #999;">暂无数据</div>';
                }
                html += '</td>';
            });
            html += '</tr></tbody>';
            
            html += '</table>';
            
            // 添加表格包装器以支持移动端横向滚动
            targetElement.innerHTML = '<div class="table-wrapper">' + html + '</div>';
            
            // 添加鼠标悬停高亮功能
            setupIndustryHoverHighlight('recent-sector-comparison-table');
        }
        
        function loadRecentCombinedComparison() {
            // 显示加载状态
            const targetElement = document.getElementById('recent-combined-comparison-stats');
            if (!targetElement) return;
            
            targetElement.innerHTML = '<div class="loading">加载中...</div>';
            
            // 查询近期综合对比数据
            fetch(addClientIpHint('/api/recent-combined-comparison'))
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP错误: ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.success) {
                        // 显示对比数据（即使数据为空也正常显示）
                        if (data.comparison_data && data.comparison_data.length > 0) {
                            renderRecentCombinedComparison(data.comparison_data);
                        } else {
                            targetElement.innerHTML = '<div class="loading">暂无数据（可能历史数据文件不完整）</div>';
                        }
                    } else {
                        console.error('API返回失败:', data);
                        targetElement.innerHTML = 
                            `<div class="loading">${data.message || '加载失败，请检查服务器日志'}</div>`;
                    }
                })
                .catch(error => {
                    console.error('加载近期综合对比数据失败:', error);
                    targetElement.innerHTML = 
                        '<div class="loading">加载失败，请稍后重试（错误: ' + error.message + '）</div>';
                });
        }
        
        // 存储当前显示的日期数量
        let currentDisplayDays = 11;
        
        function renderRecentCombinedComparison(comparisonData) {
            const targetElement = document.getElementById('recent-combined-comparison-stats');
            if (!targetElement) return;
            
            if (!comparisonData || comparisonData.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 按日期排序（从远到近，即最早的在前）
            const sortedData = [...comparisonData].sort((a, b) => {
                return new Date(a.date) - new Date(b.date);
            });
            
            // 从localStorage读取用户选择的日期数量，如果没有则使用默认值
            const savedDays = localStorage.getItem('recentComparisonDisplayDays');
            if (savedDays) {
                currentDisplayDays = parseInt(savedDays, 10);
            }
            
            // 检测是否为移动端（屏幕宽度小于768px）
            const isMobile = window.innerWidth < 768;
            // 如果是移动端且没有保存的选择，默认显示5天
            if (isMobile && !savedDays) {
                currentDisplayDays = 5;
            }
            
            // 只取最近N个交易日（N由currentDisplayDays决定）
            const recentDays = sortedData.slice(-currentDisplayDays);
            
            if (recentDays.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 使用recentDays替代recent11Days
            const recent11Days = recentDays;
            
            // 根据选择的天数决定显示多少行（板块数量）
            // 电脑版：3天 -> 30行, 5天 -> 20行, 7天 -> 15行, 9天 -> 10行, 11天 -> 10行
            // 手机版：3天 -> 10行, 5天 -> 10行, 7天 -> 10行, 9天 -> 10行, 11天 -> 10行
            const desktopMaxRowsMap = {
                3: 30,
                5: 20,
                7: 15,
                9: 10,
                11: 10
            };
            const mobileMaxRowsMap = {
                3: 10,
                5: 7,
                7: 5,
                9: 5,
                11: 5
            };
            const maxRowsMap = isMobile ? mobileMaxRowsMap : desktopMaxRowsMap;
            const maxRows = maxRowsMap[currentDisplayDays] || (isMobile ? 10 : 30); // 手机版默认10行，电脑版默认30行
            
            // 计算某个概念在指定列之后所有日期中的涨停数量之和（后续）
            function getFutureCountSum(itemName, currentColumnIndex) {
                let sum = 0;
                for (let i = currentColumnIndex + 1; i < recent11Days.length; i++) {
                    const dayData = recent11Days[i];
                    if (dayData && dayData.items && dayData.items.length > 0) {
                        const item = dayData.items.find(it => it.name === itemName);
                        if (item) {
                            sum += item.count || 0;
                        }
                    }
                }
                return sum;
            }
            
            // 计算某个概念在指定列之前所有日期中的涨停数量之和（之前）
            function getPastCountSum(itemName, currentColumnIndex) {
                let sum = 0;
                for (let i = 0; i < currentColumnIndex; i++) {
                    const dayData = recent11Days[i];
                    if (dayData && dayData.items && dayData.items.length > 0) {
                        const item = dayData.items.find(it => it.name === itemName);
                        if (item) {
                            sum += item.count || 0;
                        }
                    }
                }
                return sum;
            }
            
            // 根据数量之和返回背景色：0红 #ffebee，1-4黄 #fff9c4，5-8绿 #e8f5e9，>=9白 #ffffff
            function getColorByCount(sum) {
                if (sum === 0) return '#ffebee';
                if (sum >= 1 && sum <= 4) return '#fff9c4';
                if (sum >= 5 && sum <= 8) return '#e8f5e9';
                return '#ffffff';
            }
            
            // 左半背景：之前天数涨停数量；右半背景：后续天数涨停数量。第一天左侧、最后一天右侧无数据则白色
            function getCellBackgroundStyle(itemName, currentColumnIndex) {
                const pastSum = getPastCountSum(itemName, currentColumnIndex);
                const futureSum = getFutureCountSum(itemName, currentColumnIndex);
                const leftColor = currentColumnIndex === 0 ? '#ffffff' : getColorByCount(pastSum);
                const rightColor = currentColumnIndex === recent11Days.length - 1 ? '#ffffff' : getColorByCount(futureSum);
                if (leftColor === rightColor) {
                    return `background: ${leftColor};`;
                }
                return `background: linear-gradient(to right, ${leftColor} 50%, ${rightColor} 50%);`;
            }
            
            // 添加日期范围选择器（在移动端更明显）
            let controlHtml = '<div id="recent-combined-controls" style="margin-bottom: 15px; padding: ' + (isMobile ? '12px' : '10px') + '; background-color: #e3f2fd; border-radius: 5px; font-size: ' + (isMobile ? '13px' : '14px') + ';">';
            controlHtml += '<div style="display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: ' + (isMobile ? '10px' : '0') + ';">';
            controlHtml += '<span style="font-weight: bold; color: #1976d2; font-size: ' + (isMobile ? '13px' : 'inherit') + ';">天数：</span>';
            const dayOptions = [3, 5, 7, 9, 11];
            dayOptions.forEach(days => {
                const isSelected = currentDisplayDays === days;
                controlHtml += '<button onclick="changeRecentComparisonDays(' + days + ')" style="padding: ' + (isMobile ? '8px 12px' : '8px 14px') + '; border: 2px solid ' + (isSelected ? '#1976d2' : '#90caf9') + '; background-color: ' + (isSelected ? '#1976d2' : '#ffffff') + '; color: ' + (isSelected ? '#ffffff' : '#1976d2') + '; border-radius: 5px; cursor: pointer; font-size: ' + (isMobile ? '12px' : '14px') + '; font-weight: ' + (isSelected ? 'bold' : 'normal') + '; transition: all 0.2s;">' + days + '</button>';
            });
            controlHtml += '</div>';
            controlHtml += '</div>';
            
            // 先添加背景颜色说明（左半=之前天数，右半=后续天数）
            let legendHtml = '<div id="recent-combined-legend" style="margin-bottom: 20px; padding: 15px; background-color: #f5f5f5; border-radius: 5px; font-size: 14px; line-height: 1.8; width: 100%; box-sizing: border-box;">';
            legendHtml += '<div style="font-weight: bold; margin-bottom: 12px; color: #333; font-size: 15px;">背景颜色说明（每格左半=之前天数该概念涨停数，右半=后续天数该概念涨停数）：</div>';
            legendHtml += '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px 30px;">';
            legendHtml += '<div style="display: flex; align-items: center; gap: 10px;"><span style="display: inline-block; width: 24px; height: 24px; background-color: #ffebee; border-radius: 4px; flex-shrink: 0;"></span><span>红色：0只</span></div>';
            legendHtml += '<div style="display: flex; align-items: center; gap: 10px;"><span style="display: inline-block; width: 24px; height: 24px; background-color: #fff9c4; border-radius: 4px; flex-shrink: 0;"></span><span>黄色：1-4只</span></div>';
            legendHtml += '<div style="display: flex; align-items: center; gap: 10px;"><span style="display: inline-block; width: 24px; height: 24px; background-color: #e8f5e9; border-radius: 4px; flex-shrink: 0;"></span><span>绿色：5-8只</span></div>';
            legendHtml += '<div style="display: flex; align-items: center; gap: 10px;"><span style="display: inline-block; width: 24px; height: 24px; background-color: #ffffff; border: 2px solid #ddd; border-radius: 4px; flex-shrink: 0;"></span><span>白色：大于8只</span></div>';
            legendHtml += '</div>';
            legendHtml += '</div>';
            
            // 生成表格HTML：maxRows行 × N列（N为选择的天数）
            // 每行对应同一排名位置（第1行是各天第1名，第2行是各天第2名，...）
            let html = '<table class="comparison-table" id="recent-combined-comparison-table">';
            
            // 表头：日期（从远到近，只显示月日）
            html += '<thead><tr>';
            recent11Days.forEach(dayData => {
                // 将日期格式从 YYYY-MM-DD 转换为 MM-DD（只显示月日）
                const dateStr = dayData.date;
                if (dateStr && dateStr.includes('-')) {
                    const parts = dateStr.split('-');
                    if (parts.length >= 3) {
                        const monthDay = `${parts[1]}-${parts[2]}`;
                        html += `<th>${monthDay}</th>`;
                    } else {
                        html += `<th>${dateStr}</th>`;
                    }
                } else {
                    html += `<th>${dateStr}</th>`;
                }
            });
            html += '</tr></thead>';
            
            // 表体：maxRows行，每行对应同一排名位置
            html += '<tbody>';
            for (let rowIndex = 0; rowIndex < maxRows; rowIndex++) {
                html += '<tr>';
                recent11Days.forEach((dayData, columnIndex) => {
                    html += '<td>';
                    // 获取该日期第rowIndex+1名的板块（如果存在）
                    if (dayData.items && dayData.items.length > rowIndex) {
                        const item = dayData.items[rowIndex];
                        const bgStyle = getCellBackgroundStyle(item.name, columnIndex);
                        html += `<div class="industry-item" style="${bgStyle} padding: 6px 8px; border-radius: 4px; margin: 2px 0;" data-industry-name="${item.name.replace(/"/g, '&quot;')}">`;
                        html += `<span class="industry-item-name">${item.name}</span>`;
                        html += `<span class="industry-item-count">${item.count}</span>`;
                        html += `</div>`;
                    } else {
                        // 该日期没有第rowIndex+1名，显示空单元格（用于保持对齐）
                        html += '<div class="industry-item" style="min-height: 1.5em; border: none; padding: 0;"></div>';
                    }
                    html += '</td>';
                });
                html += '</tr>';
            }
            html += '</tbody>';
            
            html += '</table>';
            
            // 添加表格包装器以支持移动端横向滚动（只包装表格）
            let scrollHint = '';
            if (isMobile && recent11Days.length > 7) {
                scrollHint = '<div style="margin-top: 10px; padding: 10px; background-color: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px; font-size: 14px; color: #856404;"><span style="font-weight: bold;">💡 提示：</span>表格较宽，可以左右滑动查看所有日期</div>';
            }
            // 移除table-wrapper的padding和margin，确保表格宽度与背景说明一致
            const tableWrapperStyle = 'style="width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; padding-right: 0; margin-right: 0; box-sizing: border-box;"';
            let tableHtml = '<div ' + tableWrapperStyle + '>' + html + '</div>';
            
            // 将控制按钮、注释、表格和滚动提示组合在一起
            targetElement.innerHTML = controlHtml + legendHtml + tableHtml + scrollHint;
            
            // 添加鼠标悬停高亮功能
            setupIndustryHoverHighlight('recent-combined-comparison-table');
            
            // 移动端优化：根据实际列数动态设置表格样式
            setTimeout(() => {
                const table = document.getElementById('recent-combined-comparison-table');
                if (!table) return;
                
                // 获取实际列数
                const columnCount = recent11Days.length;
                
                // 在移动端，移除固定宽度，让表格自适应
                if (isMobile) {
                    // 移除min-width限制
                    table.style.minWidth = 'auto';
                    table.style.width = '100%';
                    
                    // 设置表格布局为固定，根据列数动态计算列宽
                    table.style.tableLayout = 'fixed';
                    
                    // 为每个th和td设置动态宽度
                    const ths = table.querySelectorAll('th');
                    const tds = table.querySelectorAll('td');
                    const columnWidth = (100 / columnCount) + '%';
                    
                    ths.forEach(th => {
                        th.style.width = columnWidth;
                        th.style.padding = '8px 4px';
                        th.style.fontSize = '11px';
                    });
                    
                    tds.forEach(td => {
                        td.style.width = columnWidth;
                        td.style.padding = '6px 4px';
                    });
                    
                    // 优化industry-item在移动端的显示
                    const industryItems = table.querySelectorAll('.industry-item');
                    industryItems.forEach(item => {
                        item.style.fontSize = '11px';
                        item.style.padding = '4px 0';
                        item.style.lineHeight = '1.4';
                    });
                    
                    const itemNames = table.querySelectorAll('.industry-item-name');
                    itemNames.forEach(name => {
                        name.style.fontSize = '11px';
                    });
                    
                    const itemCounts = table.querySelectorAll('.industry-item-count');
                    itemCounts.forEach(count => {
                        count.style.fontSize = '10px';
                        count.style.marginLeft = '4px';
                    });
                }
                
                const rows = Array.from(table.querySelectorAll('tbody tr'));
                if (rows.length === 0) return;
                
                // 对每一行，找到该行所有单元格的最大高度
                rows.forEach(row => {
                    const cells = Array.from(row.querySelectorAll('td'));
                    let maxHeight = 0;
                    
                    // 先重置所有单元格的高度
                    cells.forEach(cell => {
                        const item = cell.querySelector('.industry-item');
                        if (item) {
                            item.style.height = 'auto';
                            item.style.minHeight = 'auto';
                        }
                    });
                    
                    // 强制浏览器重新计算布局
                    void row.offsetHeight;
                    
                    // 计算该行的最大高度
                    cells.forEach(cell => {
                        const item = cell.querySelector('.industry-item');
                        if (item) {
                            maxHeight = Math.max(maxHeight, item.scrollHeight);
                        }
                    });
                    
                    // 将该行所有单元格设置为相同高度
                    if (maxHeight > 0) {
                        cells.forEach(cell => {
                            const item = cell.querySelector('.industry-item');
                            if (item) {
                                item.style.minHeight = maxHeight + 'px';
                                item.style.height = maxHeight + 'px';
                            }
                        });
                    }
                });
            }, 200);
        }
        
        // 切换显示的日期数量
        function changeRecentComparisonDays(days) {
            currentDisplayDays = days;
            localStorage.setItem('recentComparisonDisplayDays', days.toString());
            // 重新加载数据并渲染
            loadRecentCombinedComparison();
        }
        
        function toggleStocks(hiddenId, toggleId, totalCount) {
            const hiddenDiv = document.getElementById(hiddenId);
            const toggleLink = document.getElementById(toggleId);
            
            if (!hiddenDiv || !toggleLink) return;
            
            if (hiddenDiv.style.display === 'none') {
                hiddenDiv.style.display = 'block';
                toggleLink.textContent = '收起';
            } else {
                hiddenDiv.style.display = 'none';
                toggleLink.textContent = `展开显示全部${totalCount}只`;
            }
        }
        
        function exportStocksToTxt(exportBtnId, sector1, sector2) {
            try {
                // 获取股票数据
                const dataDiv = document.getElementById(exportBtnId);
                if (!dataDiv) {
                    alert('无法获取股票数据');
                    return;
                }
                
                // 从data属性获取股票数据
                const stocksJson = dataDiv.getAttribute('data-stocks');
                if (!stocksJson) {
                    alert('没有股票数据可导出');
                    return;
                }
                
                // 解析JSON数据（需要将&quot;转换回"）
                const stocks = JSON.parse(stocksJson.replace(/&quot;/g, '"'));
                if (!stocks || stocks.length === 0) {
                    alert('没有股票数据可导出');
                    return;
                }
                
                // 格式化股票数据为txt内容：一行一个代码加空格再加股票名称
                let txtContent = '';
                stocks.forEach((stock) => {
                    const parts = stock.split(' ');
                    const code = parts[0] || '';
                    const name = parts.slice(1).join(' ') || '';
                    txtContent += `${code} ${name}\n`;
                });
                
                // 创建Blob对象
                const blob = new Blob([txtContent], { type: 'text/plain;charset=utf-8' });
                
                // 创建下载链接
                const link = document.createElement('a');
                const fileName = `${sector1}_${sector2}_${new Date().toISOString().slice(0, 10)}.txt`;
                link.href = URL.createObjectURL(blob);
                link.download = fileName;
                
                // 触发下载
                document.body.appendChild(link);
                link.click();
                
                // 清理
                document.body.removeChild(link);
                URL.revokeObjectURL(link.href);
            } catch (error) {
                console.error('导出失败:', error);
                alert('导出失败：' + error.message);
            }
        }
        
        function loadSectorHistoryDistribution() {
            const searchText = document.getElementById('sector-search-text').value.trim();
            const startDate = document.getElementById('sector-start-date').value;
            
            if (!searchText) {
                alert('请输入搜索文本');
                return;
            }
            
            if (!startDate) {
                alert('请选择起始日期');
                return;
            }
            
            // 显示加载状态
            const targetElement = document.getElementById('sector-distribution-table');
            const infoElement = document.getElementById('sector-distribution-info');
            if (!targetElement) return;
            
            targetElement.innerHTML = '<div class="loading">加载中...</div>';
            infoElement.classList.add('hidden');
            
            // 查询板块历史分布数据
            fetch(addClientIpHint(`/api/sector-history-distribution?search_text=${encodeURIComponent(searchText)}&start_date=${encodeURIComponent(startDate)}`))
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP错误: ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.success) {
                        // 显示分布数据
                        renderSectorHistoryDistribution(data.distribution_data, data.matched_stocks_count);
                        infoElement.textContent = `搜索文本: ${searchText} | 起始日期: ${startDate} | 匹配股票: ${data.matched_stocks_count} 只 | 记录数: ${data.distribution_data.length}`;
                        infoElement.classList.remove('hidden');
                    } else {
                        targetElement.innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                        infoElement.classList.add('hidden');
                    }
                })
                .catch(error => {
                    console.error('加载板块历史分布数据失败:', error);
                    targetElement.innerHTML = 
                        '<div class="loading">加载失败，请稍后重试（错误: ' + error.message + '）</div>';
                    infoElement.classList.add('hidden');
                });
        }
        
        function renderSectorHistoryDistribution(distributionData, matchedStocksCount) {
            const targetElement = document.getElementById('sector-distribution-table');
            const chartElement = document.getElementById('sector-distribution-chart');
            if (!targetElement) return;
            
            if (!distributionData || distributionData.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                if (chartElement) {
                    chartElement.style.display = 'none';
                }
                return;
            }
            
            // 显示图表容器
            if (chartElement) {
                chartElement.style.display = 'block';
            }
            
            // 准备图表数据：按日期排序，过滤掉"未涨停股票"行
            const chartData = distributionData.filter(row => row['日期'] !== '未涨停股票');
            const sortedData = [...chartData].sort((a, b) => {
                // 如果日期是有效的日期格式，按日期排序
                const dateA = a['日期'];
                const dateB = b['日期'];
                // 检查是否是有效的日期格式（YYYY-MM-DD）
                if (/^\d{4}-\d{2}-\d{2}$/.test(dateA) && /^\d{4}-\d{2}-\d{2}$/.test(dateB)) {
                    return new Date(dateA) - new Date(dateB);
                }
                return 0;
            });
            
            const dates = sortedData.map(row => row['日期'] || '');
            const counts = sortedData.map(row => parseInt(row['数量']) || 0);
            
            // 绘制折线图
            const canvas = document.getElementById('sector-chart-canvas');
            if (canvas) {
                // 销毁旧图表（如果存在）
                if (window.sectorChart) {
                    window.sectorChart.destroy();
                }
                
                // 简单的图表配置，不处理截长图问题
                const ctx = canvas.getContext('2d');
                window.sectorChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: dates,
                        datasets: [{
                            label: '涨停板数量',
                            data: counts,
                            borderColor: 'rgb(220, 53, 69)',
                            backgroundColor: 'rgba(220, 53, 69, 0.1)',
                            borderWidth: 2,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 4,
                            pointHoverRadius: 6,
                            pointBackgroundColor: 'rgb(220, 53, 69)',
                            pointBorderColor: '#fff',
                            pointBorderWidth: 2
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            title: {
                                display: true,
                                text: '板块涨停板数量趋势',
                                font: {
                                    size: 16,
                                    weight: 'bold'
                                },
                                padding: {
                                    top: 10,
                                    bottom: 20
                                }
                            },
                            legend: {
                                display: true,
                                position: 'top'
                            },
                            tooltip: {
                                mode: 'index',
                                intersect: false,
                                callbacks: {
                                    label: function(context) {
                                        return '数量: ' + context.parsed.y;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                title: {
                                    display: true,
                                    text: '日期'
                                },
                                ticks: {
                                    maxRotation: 45,
                                    minRotation: 45,
                                    callback: function(value, index) {
                                        const label = this.getLabelForValue(value);
                                        // 如果是日期格式 YYYY-MM-DD，只显示月日 MM-DD
                                        if (label && /^\d{4}-\d{2}-\d{2}$/.test(label)) {
                                            const parts = label.split('-');
                                            if (parts.length >= 3) {
                                                return `${parts[1]}-${parts[2]}`;
                                            }
                                        }
                                        return label;
                                    }
                                }
                            },
                            y: {
                                title: {
                                    display: false
                                },
                                beginAtZero: true,
                                ticks: {
                                    stepSize: 1
                                }
                            }
                        },
                        interaction: {
                            mode: 'nearest',
                            axis: 'x',
                            intersect: false
                        }
                    }
                });
            }
            
            // 检测是否为移动端
            const isMobile = window.innerWidth < 768;
            
            // 生成表格HTML
            // 在移动端使用更小的列宽
            const dateColWidth = isMobile ? '80px' : '120px';
            const countColWidth = isMobile ? '50px' : '80px';
            
            let html = '<table class="comparison-table" id="sector-history-table"><thead><tr>';
            html += '<th style="width: ' + dateColWidth + ';">日期</th>';
            html += '<th style="width: ' + countColWidth + ';">数量</th>';
            html += '<th style="width: auto;">涨停股票</th>';
            html += '</tr></thead><tbody>';
            
            // 收集所有股票的涨停次数，找出前10名
            const allStockCounts = {};
            distributionData.forEach(row => {
                if (row['股票详情'] && Array.isArray(row['股票详情'])) {
                    row['股票详情'].forEach(stock => {
                        const key = `${stock.code} ${stock.name}`;
                        if (!allStockCounts[key] || allStockCounts[key] < stock.count) {
                            allStockCounts[key] = stock.count;
                        }
                    });
                }
            });
            
            // 按涨停次数排序，取前10名
            const top10Stocks = Object.entries(allStockCounts)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 10)
                .map(([key, count]) => ({ key, count }));
            
            // 创建前10名股票的颜色映射（红色系渐变）
            // 第1-4名：深红背景+白色文字，第1名颜色最深
            // 第5-10名：浅红背景（从中等深度到最浅）+深红文字
            const colorMap = {};
            const colors = [
                { bg: '#b71c1c', text: '#ffffff' },   // 第1名：最深红背景，白色文字
                { bg: '#c62828', text: '#ffffff' },   // 第2名：深红背景，白色文字
                { bg: '#d32f2f', text: '#ffffff' },   // 第3名：深红背景，白色文字
                { bg: '#e53935', text: '#ffffff' },   // 第4名：深红背景，白色文字
                { bg: '#f44336', text: '#000000' },   // 第5名：中等深度红背景，黑色文字
                { bg: '#ef5350', text: '#000000' },   // 第6名：浅红背景，黑色文字
                { bg: '#e57373', text: '#000000' },   // 第7名：更浅红背景，黑色文字
                { bg: '#ef9a9a', text: '#b71c1c' },   // 第8名：更浅红背景，深红文字
                { bg: '#ffcdd2', text: '#c62828' },   // 第9名：很浅红背景，深红文字
                { bg: '#ffebee', text: '#c62828' }    // 第10名：最浅红背景，深红文字
            ];
            
            top10Stocks.forEach((stock, index) => {
                colorMap[stock.key] = colors[index];
            });
            
            // 渲染表格
            distributionData.forEach((row, rowIndex) => {
                html += '<tr>';
                html += `<td style="width: ${dateColWidth}; font-size: ${isMobile ? '12px' : 'inherit'};">${row['日期'] || ''}</td>`;
                html += `<td style="width: ${countColWidth}; text-align: center; font-size: ${isMobile ? '12px' : 'inherit'};">${row['数量'] || 0}</td>`;
                
                // 渲染涨停股票列，为前10名添加背景颜色
                let stocksHtml = '';
                const isNeverLimitUpRow = row['日期'] === '未涨停股票';
                const toggleId = `toggle-stocks-${rowIndex}`;
                const stocksContainerId = `stocks-container-${rowIndex}`;
                
                if (row['股票详情'] && Array.isArray(row['股票详情'])) {
                    const stockItems = row['股票详情'].map(stock => {
                        const key = `${stock.code} ${stock.name}`;
                        const colorInfo = colorMap[key];
                        const displayText = stock.count > 1 ? `${stock.code} ${stock.name}(${stock.count})` : `${stock.code} ${stock.name}`;
                        
                        if (colorInfo) {
                            return `<span style="background-color: ${colorInfo.bg}; color: ${colorInfo.text}; padding: ${isMobile ? '2px 4px' : '2px 6px'}; border-radius: 3px; margin-right: 4px; margin-bottom: 2px; display: inline-block; font-size: ${isMobile ? '11px' : 'inherit'}; white-space: normal; word-break: break-word;">${displayText}</span>`;
                        } else {
                            return `<span style="margin-right: 4px; margin-bottom: 2px; display: inline-block; font-size: ${isMobile ? '11px' : 'inherit'}; white-space: normal; word-break: break-word;">${displayText}</span>`;
                        }
                    });
                    stocksHtml = stockItems.join('');
                } else {
                    // 兼容旧格式（没有股票详情的情况）
                    const stocks = (row['涨停股票'] || '').split(';');
                    stocks.forEach(stock => {
                        if (stock.trim()) {
                            const colorInfo = colorMap[stock.trim()];
                            if (colorInfo) {
                                stocksHtml += `<span style="background-color: ${colorInfo.bg}; color: ${colorInfo.text}; padding: ${isMobile ? '2px 4px' : '2px 6px'}; border-radius: 3px; margin-right: 4px; margin-bottom: 2px; display: inline-block; font-size: ${isMobile ? '11px' : 'inherit'}; white-space: normal; word-break: break-word;">${stock.trim()}</span>`;
                            } else {
                                stocksHtml += `<span style="margin-right: 4px; margin-bottom: 2px; display: inline-block; font-size: ${isMobile ? '11px' : 'inherit'}; white-space: normal; word-break: break-word;">${stock.trim()}</span>`;
                            }
                        }
                    });
                }
                
                // 如果是"未涨停股票"行，添加显示/隐藏按钮
                if (isNeverLimitUpRow && stocksHtml) {
                    html += `<td style="text-align: left; word-wrap: break-word; word-break: break-all; white-space: normal; font-size: ${isMobile ? '11px' : 'inherit'};">`;
                    html += `<span id="${stocksContainerId}">${stocksHtml}</span>`;
                    html += ` <button id="${toggleId}" onclick="toggleNeverLimitUpStocks('${stocksContainerId}', '${toggleId}')" style="margin-left: 8px; padding: 4px 8px; background-color: #667eea; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: ${isMobile ? '11px' : '12px'};">隐藏</button>`;
                    html += `</td>`;
                } else {
                    html += `<td style="text-align: left; word-wrap: break-word; word-break: break-all; white-space: normal; font-size: ${isMobile ? '11px' : 'inherit'};">${stocksHtml || (row['涨停股票'] || '')}</td>`;
                }
                html += '</tr>';
            });
            
            html += '</tbody></table>';
            
            // 添加表格包装器以支持移动端横向滚动
            // 在移动端移除padding和margin，确保表格宽度与容器一致
            const tableWrapperStyle = isMobile ? 'style="width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; padding-right: 0; margin-right: 0; box-sizing: border-box;"' : 'class="table-wrapper"';
            targetElement.innerHTML = '<div ' + tableWrapperStyle + '>' + html + '</div>';
            
            // 在移动端进一步优化表格样式
            if (isMobile) {
                setTimeout(() => {
                    const table = document.getElementById('sector-history-table');
                    if (table) {
                        table.style.width = '100%';
                        table.style.tableLayout = 'fixed';
                        
                        // 确保涨停股票列可以换行
                        const lastCols = table.querySelectorAll('td:last-child, th:last-child');
                        lastCols.forEach(col => {
                            col.style.wordWrap = 'break-word';
                            col.style.wordBreak = 'break-all';
                            col.style.whiteSpace = 'normal';
                        });
                    }
                }, 100);
            }
        }
        
        function exportSectorDistributionCsv() {
            const searchText = document.getElementById('sector-search-text').value.trim();
            const startDate = document.getElementById('sector-start-date').value;
            
            if (!searchText || !startDate) {
                alert('请先输入搜索文本和起始日期并查询');
                return;
            }
            
            // 构建下载URL
            const downloadUrl = addClientIpHint(`/api/sector-history-distribution/export?search_text=${encodeURIComponent(searchText)}&start_date=${encodeURIComponent(startDate)}`);
            
            // 创建临时链接并触发下载
            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = '';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
        
        function exportSectorDistributionStocks() {
            const searchText = document.getElementById('sector-search-text').value.trim();
            const startDate = document.getElementById('sector-start-date').value;
            
            if (!searchText || !startDate) {
                alert('请先输入搜索文本和起始日期并查询');
                return;
            }
            
            // 构建下载URL
            const downloadUrl = addClientIpHint(`/api/sector-history-distribution/export-stocks?search_text=${encodeURIComponent(searchText)}&start_date=${encodeURIComponent(startDate)}`);
            
            // 创建临时链接并触发下载
            const link = document.createElement('a');
            link.href = downloadUrl;
            link.download = '';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
        
        function toggleNeverLimitUpStocks(containerId, buttonId) {
            const container = document.getElementById(containerId);
            const button = document.getElementById(buttonId);
            
            if (!container || !button) return;
            
            if (container.style.display === 'none') {
                container.style.display = 'inline';
                button.textContent = '隐藏';
            } else {
                container.style.display = 'none';
                button.textContent = '显示';
            }
        }
        
        // 全局变量存储客户端IP（用于日志记录）
        let clientIpHint = null;
        
        // 获取客户端IP（用于日志记录）
        async function getClientIpHint() {
            if (clientIpHint) {
                return clientIpHint; // 已获取过，直接返回
            }
            const providers = [
                { url: 'https://api.ipify.org?format=json', parse: async (r) => (await r.json()).ip },
                { url: 'https://ipinfo.io/json', parse: async (r) => (await r.json()).ip },
                { url: 'https://api.my-ip.io/ip.json', parse: async (r) => (await r.json()).ip },
                { url: 'https://api.ip.sb/ip', parse: async (r) => (await r.text()).trim() },
                { url: 'https://ifconfig.me/ip', parse: async (r) => (await r.text()).trim() },
                { url: 'https://icanhazip.com', parse: async (r) => (await r.text()).trim() },
            ];
            const timeoutMs = 1500; // 缩短超时时间，快速失败并尝试下一个
            const tryOnce = async (url, parse) => {
                try {
                    const controller = new AbortController();
                    const timeout = setTimeout(() => controller.abort(), timeoutMs);
                    const resp = await fetch(url, { 
                        signal: controller.signal,
                        mode: 'cors',
                        cache: 'no-cache'
                    }).catch(() => null); // 捕获网络错误，避免控制台显示
                    clearTimeout(timeout);
                    if (!resp || !resp.ok) return null;
                    const ip = await parse(resp);
                    if (typeof ip === 'string' && ip.match(/^\d{1,3}(?:\.\d{1,3}){3}$/)) return ip;
                    return null;
                } catch (_) {
                    return null; // 静默失败
                }
            };
            for (const p of providers) {
                const ip = await tryOnce(p.url, p.parse);
                if (ip) {
                    clientIpHint = ip;
                    return ip;
                }
            }
            return null;
        }
        
        // 不在页面加载时自动获取IP，只在调用API时按需获取（通过 addClientIpHint 函数）
        // 这样可以避免在页面加载时立即触发网络请求，减少控制台错误信息
        
        // 辅助函数：为URL添加client_ip_hint参数
        function addClientIpHint(url) {
            if (!clientIpHint) {
                // 如果还没获取到IP，尝试立即获取（不阻塞，异步进行）
                getClientIpHint().catch(() => {});
                return url; // 如果还没获取到，先返回原URL，下次请求时再尝试
            }
            const separator = url.includes('?') ? '&' : '?';
            return `${url}${separator}client_ip_hint=${encodeURIComponent(clientIpHint)}`;
        }
        
        // 全局变量存储所有股票信息
        let allStocksData = null;
        let topSectorsList = [];
        let limitUpCodesSet = new Set();
        let selectedSectorsSet = new Set(); // 保存选中的板块
        
        // localStorage 缓存配置
        const STOCKS_CACHE_KEY = 'all_stocks_data_cache';
        const STOCKS_CACHE_VERSION = 'v1'; // 数据版本，如果后端数据结构变化，更新此版本号以清除旧缓存
        const STOCKS_CACHE_EXPIRY = 24 * 60 * 60 * 1000; // 缓存过期时间：24小时（毫秒）
        
        // 从 localStorage 加载缓存的股票数据
        function loadStocksDataFromCache() {
            try {
                const cacheStr = localStorage.getItem(STOCKS_CACHE_KEY);
                if (!cacheStr) {
                    return null;
                }
                
                const cache = JSON.parse(cacheStr);
                
                // 检查版本号
                if (cache.version !== STOCKS_CACHE_VERSION) {
                    console.log('缓存版本不匹配，清除旧缓存');
                    localStorage.removeItem(STOCKS_CACHE_KEY);
                    return null;
                }
                
                // 检查是否过期
                const now = Date.now();
                if (now - cache.timestamp > STOCKS_CACHE_EXPIRY) {
                    console.log('缓存已过期，清除旧缓存');
                    localStorage.removeItem(STOCKS_CACHE_KEY);
                    return null;
                }
                
                console.log('从缓存加载股票数据，数量:', Object.keys(cache.data).length);
                return cache.data;
            } catch (e) {
                console.error('从缓存加载股票数据失败:', e);
                // 如果缓存损坏，清除它
                try {
                    localStorage.removeItem(STOCKS_CACHE_KEY);
                } catch (e2) {
                    // 忽略清除失败
                }
                return null;
            }
        }
        
        // 保存股票数据到 localStorage
        function saveStocksDataToCache(data) {
            try {
                const cache = {
                    version: STOCKS_CACHE_VERSION,
                    timestamp: Date.now(),
                    data: data
                };
                const cacheStr = JSON.stringify(cache);
                
                // 检查大小（localStorage 通常限制为 5-10MB）
                if (cacheStr.length > 5 * 1024 * 1024) {
                    console.warn('股票数据太大，无法缓存（超过5MB）');
                    return false;
                }
                
                localStorage.setItem(STOCKS_CACHE_KEY, cacheStr);
                console.log('股票数据已保存到缓存，大小:', (cacheStr.length / 1024).toFixed(2), 'KB');
                return true;
            } catch (e) {
                console.error('保存股票数据到缓存失败:', e);
                // 可能是存储空间不足，尝试清除旧缓存
                if (e.name === 'QuotaExceededError') {
                    console.warn('localStorage 空间不足，尝试清除旧缓存...');
                    try {
                        // 清除所有相关的缓存
                        localStorage.removeItem(STOCKS_CACHE_KEY);
                        console.log('已清除旧缓存，请重试');
                    } catch (e2) {
                        // 忽略清除失败
                    }
                }
                return false;
            }
        }
        
        function loadFreeCombination(combinedStats) {
            // 在刷新前保存当前选中的板块
            saveSelectedSectors();
            
            // 获取前20个板块
            const newTopSectorsList = (combinedStats || []).slice(0, 20).map(s => s.name);
            
            // 如果板块列表没有变化，只更新数据，不重新渲染
            const sectorsChanged = JSON.stringify(topSectorsList) !== JSON.stringify(newTopSectorsList);
            topSectorsList = newTopSectorsList;
            
            if (sectorsChanged) {
                // 板块列表变化了，需要重新渲染
                renderFreeCombination();
            } else {
                // 板块列表没变化，只更新涨停股票数据并重新计算
                updateLimitUpCodes();
            }
            
            // 加载所有股票信息和涨停股票代码（如果还没有加载）
            if (!allStocksData) {
                loadAllStocksData();
            } else {
                // 如果数据已加载，只更新涨停股票代码并重新计算
                updateLimitUpCodes();
            }
        }
        
        function saveSelectedSectors() {
            // 保存当前选中的板块
            selectedSectorsSet.clear();
            const checkboxes = document.querySelectorAll('.free-combination-checkbox-item input[type="checkbox"]:checked');
            checkboxes.forEach(cb => {
                const sector = cb.value.replace(/&quot;/g, '"');
                selectedSectorsSet.add(sector);
            });
            console.log('保存选中的板块:', Array.from(selectedSectorsSet));
        }
        
        function restoreSelectedSectors(shouldUpdate = true) {
            // 恢复选中的板块
            if (selectedSectorsSet.size === 0) return;
            
            // 只保留仍然在前二十的板块
            const validSelectedSectors = new Set();
            const checkboxes = document.querySelectorAll('.free-combination-checkbox-item input[type="checkbox"]');
            checkboxes.forEach(cb => {
                const sector = cb.value.replace(/&quot;/g, '"');
                if (selectedSectorsSet.has(sector)) {
                    cb.checked = true;
                    validSelectedSectors.add(sector);
                }
            });
            
            // 清理不在前二十的板块
            const removedSectors = Array.from(selectedSectorsSet).filter(s => !validSelectedSectors.has(s));
            if (removedSectors.length > 0) {
                console.log('以下板块已不在前二十，已自动取消选中:', removedSectors);
                // 从selectedSectorsSet中移除不在前二十的板块
                removedSectors.forEach(s => selectedSectorsSet.delete(s));
            }
            
            // 更新selectedSectorsSet为只包含仍然有效的板块
            selectedSectorsSet.clear();
            validSelectedSectors.forEach(s => selectedSectorsSet.add(s));
            
            console.log('恢复选中的板块（仅保留在前二十的）:', Array.from(selectedSectorsSet));
            
            // 如果所有选中的板块都不在前二十了，清空显示
            if (selectedSectorsSet.size === 0) {
                const stocksListElement = document.getElementById('free-combination-stocks-list');
                const exportBtn = document.getElementById('free-combination-export-btn');
                if (stocksListElement) {
                    stocksListElement.innerHTML = '<div class="free-combination-empty">之前选中的板块已不在前二十，请重新选择</div>';
                }
                if (exportBtn) {
                    exportBtn.disabled = true;
                }
                return;
            }
            
            // 恢复选中后，重新计算交集（如果需要）
            if (shouldUpdate && selectedSectorsSet.size > 0) {
                updateFreeCombinationResults();
            }
        }
        
        function updateLimitUpCodes() {
            // 只更新涨停股票代码，不重新加载所有股票数据
            fetch(addClientIpHint('/api/data'))
                .then(response => response.json())
                .then(limitUpData => {
                    if (limitUpData.success && limitUpData.limit_up_stocks) {
                        limitUpCodesSet.clear();
                        limitUpData.limit_up_stocks.forEach(stock => {
                            const code = String(stock.code || '').padStart(6, '0');
                            limitUpCodesSet.add(code);
                        });
                        console.log('更新涨停股票数量:', limitUpCodesSet.size);
                        
                        // 重新计算交集（如果有选中的板块）
                        if (selectedSectorsSet.size > 0) {
                            updateFreeCombinationResults();
                        }
                    }
                })
                .catch(error => {
                    console.error('更新涨停股票数据失败:', error);
                });
        }
        
        let isLoadingStocksData = false; // 防止重复加载
        
        function loadAllStocksData(showLoading = false) {
            // 如果正在加载，直接返回
            if (isLoadingStocksData) {
                console.log('股票数据正在加载中，跳过重复请求');
                return;
            }
            
            // 如果已经加载完成，直接返回
            if (allStocksData) {
                console.log('股票数据已加载，无需重复加载');
                return;
            }
            
            // 首先尝试从缓存加载
            const cachedData = loadStocksDataFromCache();
            if (cachedData) {
                console.log('从缓存加载股票数据成功');
                allStocksData = cachedData;
                
                // 更新UI：如果显示的是加载提示，更新为正常状态
                const stocksListElement = document.getElementById('free-combination-stocks-list');
                if (stocksListElement) {
                    const currentContent = stocksListElement.innerHTML;
                    // 如果当前显示的是加载提示，更新UI
                    if (currentContent.includes('正在加载') || currentContent.includes('数据准备中') || currentContent.includes('正在后台加载')) {
                        // 恢复选中的板块（如果之前有选中）
                        restoreSelectedSectors(false); // 先不触发更新，手动处理
                        
                        // 重新计算交集（如果已有选中的板块）
                        if (selectedSectorsSet.size > 0) {
                            updateFreeCombinationResults();
                        } else {
                            stocksListElement.innerHTML = '<div class="free-combination-empty">请选择至少一个板块</div>';
                        }
                    } else {
                        // 如果UI不是加载提示，正常恢复选中板块
                        restoreSelectedSectors();
                    }
                } else {
                    // 如果元素不存在，正常恢复选中板块
                    restoreSelectedSectors();
                }
                
                // 在后台异步更新缓存（不阻塞UI）
                setTimeout(() => {
                    if (!isLoadingStocksData) {
                        loadAllStocksDataFromServer(false);
                    }
                }, 1000);
                
                return;
            }
            
            // 缓存不存在或已过期，从服务器加载
            loadAllStocksDataFromServer(showLoading);
        }
        
        function loadAllStocksDataFromServer(showLoading = false) {
            isLoadingStocksData = true;
            console.log('开始从服务器加载所有股票数据...');
            
            // 更新加载状态（仅在需要显示时）
            const stocksListElement = document.getElementById('free-combination-stocks-list');
            if (showLoading && stocksListElement) {
                stocksListElement.innerHTML = '<div class="loading">正在加载股票数据，请稍候...</div>';
            }
            
            // 添加超时处理（30秒超时）
            const timeoutPromise = new Promise((_, reject) => {
                setTimeout(() => {
                    isLoadingStocksData = false;
                    reject(new Error('加载超时，请刷新页面重试'));
                }, 30000);
            });
            
            // 同时获取所有股票信息和当前涨停股票
            Promise.race([
                Promise.all([
                    fetch(addClientIpHint('/api/all-stocks')).then(r => {
                        if (!r.ok) throw new Error(`HTTP ${r.status}: ${r.statusText}`);
                        return r.json();
                    }),
                    fetch(addClientIpHint('/api/data')).then(r => {
                        if (!r.ok) throw new Error(`HTTP ${r.status}: ${r.statusText}`);
                        return r.json();
                    })
                ]),
                timeoutPromise
            ]).then(([stocksData, limitUpData]) => {
                isLoadingStocksData = false;
                console.log('股票数据加载结果:', stocksData.success, '涨停数据加载结果:', limitUpData.success);
                
                if (stocksData.success) {
                    allStocksData = stocksData.stocks_data;
                    console.log('成功加载股票数据，数量:', Object.keys(allStocksData).length);
                    
                    // 保存到缓存
                    saveStocksDataToCache(allStocksData);
                } else {
                    console.error('加载所有股票信息失败:', stocksData.message);
                    if (showLoading && stocksListElement) {
                        stocksListElement.innerHTML = '<div class="free-combination-empty" style="color: red;">加载股票数据失败: ' + (stocksData.message || '未知错误') + '</div>';
                    }
                    return;
                }
                
                if (limitUpData.success && limitUpData.limit_up_stocks) {
                    limitUpCodesSet.clear();
                    limitUpData.limit_up_stocks.forEach(stock => {
                        const code = String(stock.code || '').padStart(6, '0');
                        limitUpCodesSet.add(code);
                    });
                    console.log('涨停股票数量:', limitUpCodesSet.size);
                }
                
                // 恢复选中的板块（如果之前有选中）
                restoreSelectedSectors(false); // 先不触发更新，手动处理
                
                // 重新获取元素引用（因为DOM可能在加载过程中被重新渲染）
                const currentStocksListElement = document.getElementById('free-combination-stocks-list');
                
                // 重新计算交集（如果已有选中的板块）
                if (selectedSectorsSet.size > 0) {
                    updateFreeCombinationResults();
                } else {
                    // 如果没有选中的板块，强制更新UI显示提示
                    // 无论showLoading是true还是false，只要数据加载完成，都应该更新界面
                    if (currentStocksListElement) {
                        // 直接更新为正常提示，不检查当前内容
                        currentStocksListElement.innerHTML = '<div class="free-combination-empty">请选择至少一个板块</div>';
                    }
                }
            }).catch(error => {
                isLoadingStocksData = false;
                console.error('加载股票数据失败:', error);
                // 重新获取元素引用
                const currentStocksListElement = document.getElementById('free-combination-stocks-list');
                if (showLoading && currentStocksListElement) {
                    currentStocksListElement.innerHTML = '<div class="free-combination-empty" style="color: red;">加载股票数据失败: ' + error.message + '<br>请刷新页面重试</div>';
                }
            });
        }
        
        function renderFreeCombination() {
            const targetElement = document.getElementById('free-combination-stats');
            if (!targetElement) return;
            
            if (!topSectorsList || topSectorsList.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            let html = '<div class="free-combination-container">';
            
            // 复选框区域
            html += '<div class="free-combination-checkboxes">';
            topSectorsList.forEach((sector, index) => {
                html += `
                    <div class="free-combination-checkbox-item">
                        <input type="checkbox" id="free-comb-checkbox-${index}" value="${sector.replace(/"/g, '&quot;')}" onchange="updateFreeCombinationResults()">
                        <label for="free-comb-checkbox-${index}">${sector}</label>
                    </div>
                `;
            });
            html += '</div>';
            
            // 结果显示区域
            html += '<div class="free-combination-results">';
            html += '<div class="free-combination-results-header">';
            html += '<div class="free-combination-results-title">选中板块的交集股票：</div>';
            html += '<button id="free-combination-export-btn" class="free-combination-export-btn" onclick="exportFreeCombinationStocks()" disabled>📥 导出股票列表</button>';
            html += '</div>';
            html += '<div id="free-combination-stocks-list" class="free-combination-stocks-list">';
            // 检查数据是否正在加载，如果正在加载，显示加载提示
            if (isLoadingStocksData) {
                html += '<div class="loading" style="padding: 20px; text-align: center; color: #666;">正在后台加载股票数据，请稍候...<br><small style="color: #999;">首次加载可能需要几秒钟</small></div>';
            } else if (!allStocksData) {
                html += '<div class="free-combination-empty" style="color: #999;">数据准备中，请稍候...<br><small>首次使用需要加载股票数据</small></div>';
            } else {
                html += '<div class="free-combination-empty">请选择至少一个板块</div>';
            }
            html += '</div>';
            html += '</div>';
            
            html += '</div>';
            
            targetElement.innerHTML = html;
            
            // 恢复选中的板块（渲染后恢复，会自动触发更新）
            restoreSelectedSectors(true);
            
            // 如果数据还没加载且不在加载中，立即开始预加载
            if (!allStocksData && !isLoadingStocksData) {
                console.log('板块自由组合渲染完成，开始预加载股票数据...');
                loadAllStocksData(false);
            }
        }
        
        function updateFreeCombinationResults() {
            // 获取所有选中的板块
            const checkboxes = document.querySelectorAll('.free-combination-checkbox-item input[type="checkbox"]:checked');
            const selectedSectors = Array.from(checkboxes).map(cb => {
                // 将HTML转义的 &quot; 转换回 "
                return cb.value.replace(/&quot;/g, '"');
            });
            
            const stocksListElement = document.getElementById('free-combination-stocks-list');
            const exportBtn = document.getElementById('free-combination-export-btn');
            
            if (!stocksListElement || !exportBtn) {
                console.error('找不到必要的DOM元素');
                return;
            }
            
            // 更新selectedSectorsSet，保存当前选中的板块
            selectedSectorsSet.clear();
            selectedSectors.forEach(s => selectedSectorsSet.add(s));
            console.log('当前选中的板块:', Array.from(selectedSectorsSet));
            
            if (selectedSectors.length === 0) {
                stocksListElement.innerHTML = '<div class="free-combination-empty">请选择至少一个板块</div>';
                exportBtn.disabled = true;
                return;
            }
            
            // 如果还没有加载所有股票数据，先加载（显示加载提示）
            if (!allStocksData) {
                console.log('股票数据未加载，开始加载...');
                // 如果正在加载中，显示更友好的提示
                if (isLoadingStocksData) {
                    stocksListElement.innerHTML = '<div class="loading" style="padding: 20px; text-align: center; color: #666;">正在加载股票数据，请稍候...<br><small style="color: #999;">首次加载可能需要几秒钟，请耐心等待</small></div>';
                    // 设置定时器，每2秒检查一次加载状态
                    const checkInterval = setInterval(function() {
                        if (allStocksData) {
                            clearInterval(checkInterval);
                            // 数据加载完成，重新计算
                            updateFreeCombinationResults();
                        } else if (!isLoadingStocksData) {
                            clearInterval(checkInterval);
                            // 加载失败，显示错误
                            stocksListElement.innerHTML = '<div class="free-combination-empty" style="color: red;">加载失败，请刷新页面重试</div>';
                        }
                    }, 2000);
                } else {
                    stocksListElement.innerHTML = '<div class="loading" style="padding: 20px; text-align: center; color: #666;">正在加载股票数据，请稍候...<br><small style="color: #999;">首次加载可能需要几秒钟</small></div>';
                    loadAllStocksData(true); // 传入true表示需要显示加载提示
                    // 注意：数据加载完成后会自动调用updateFreeCombinationResults()，因为selectedSectorsSet已经更新
                }
                return;
            }
            
            console.log('选中的板块:', selectedSectors);
            console.log('股票数据已加载，数量:', Object.keys(allStocksData).length);
            
            // 计算交集：找出同时属于所有选中板块的股票
            stocksListElement.innerHTML = '<div class="loading">正在计算...</div>';
            const commonStocks = findCommonStocks(selectedSectors);
            
            console.log('找到的交集股票数量:', commonStocks.length);
            
            // 显示结果
            if (commonStocks.length === 0) {
                stocksListElement.innerHTML = '<div class="free-combination-empty">没有找到同时属于所有选中板块的股票</div>';
                exportBtn.disabled = true;
            } else {
                let html = '';
                commonStocks.forEach(stock => {
                    const parts = stock.split(' ');
                    const code = parts[0] || '';
                    const name = parts.slice(1).join(' ') || '';
                    html += createFreeCombinationStockLink(code, name);
                });
                stocksListElement.innerHTML = html;
                exportBtn.disabled = false;
                
                // 保存当前选中的股票数据到按钮的data属性
                exportBtn.setAttribute('data-stocks', JSON.stringify(commonStocks));
                exportBtn.setAttribute('data-sectors', JSON.stringify(selectedSectors));
            }
        }
        
        function findCommonStocks(selectedSectors) {
            if (!allStocksData || selectedSectors.length === 0) {
                return [];
            }
            
            // 需要排除的常见概念和板块
            const excludedConcepts = {
                '央国企改革': true,
                '融资融券': true
            };
            const excludedSectors = {
                '央国企改革': true,
                '融资融券': true,
                '深股通': true,
                '沪股通': true,
                '机构重仓': true,
                'QFII重仓': true,
                '专精特新': true,
                '标准普尔': true,
                '富时罗素': true
            };
            
            // 为每个股票构建板块集合
            const stockSectorsMap = {}; // {code: {sectors: Set, name: string}}
            
            for (const [code, stockInfo] of Object.entries(allStocksData)) {
                const formattedCode = String(code).padStart(6, '0');
                // 跳过涨停股票
                if (limitUpCodesSet.has(formattedCode)) continue;
                
                const sectors = new Set();
                
                // 1. 添加行业
                const industry = stockInfo.industry;
                if (industry && industry.trim() && industry !== 'nan') {
                    sectors.add(industry.trim());
                }
                
                // 2. 添加概念
                const concepts = stockInfo.concepts || [];
                if (Array.isArray(concepts)) {
                    concepts.forEach(concept => {
                        if (concept && String(concept).trim() && !excludedConcepts[String(concept).trim()]) {
                            sectors.add(String(concept).trim());
                        }
                    });
                }
                
                // 3. 添加板块
                const plates = stockInfo.plates || [];
                if (Array.isArray(plates)) {
                    plates.forEach(plate => {
                        if (plate && String(plate).trim() && !excludedSectors[String(plate).trim()]) {
                            sectors.add(String(plate).trim());
                        }
                    });
                }
                
                if (sectors.size > 0) {
                    stockSectorsMap[formattedCode] = {
                        sectors: sectors,
                        name: stockInfo.name || '未知'
                    };
                }
            }
            
            // 找出同时属于所有选中板块的股票
            const commonStocks = [];
            console.log('开始查找交集股票，选中板块:', selectedSectors);
            console.log('股票板块映射数量:', Object.keys(stockSectorsMap).length);
            
            // 调试：查看前几个股票的板块信息
            let debugCount = 0;
            for (const [code, stockData] of Object.entries(stockSectorsMap)) {
                if (debugCount < 3) {
                    console.log(`股票 ${code} 的板块:`, Array.from(stockData.sectors));
                    debugCount++;
                }
                
                // 检查该股票是否同时属于所有选中的板块
                let belongsToAll = true;
                for (const sector of selectedSectors) {
                    // 确保sector是字符串并去除空格
                    const sectorTrimmed = String(sector).trim();
                    if (!stockData.sectors.has(sectorTrimmed)) {
                        belongsToAll = false;
                        break;
                    }
                }
                
                if (belongsToAll) {
                    commonStocks.push(`${code} ${stockData.name}`);
                }
            }
            
            // 排序
            commonStocks.sort();
            
            console.log('计算完成，找到', commonStocks.length, '只股票');
            if (commonStocks.length > 0 && commonStocks.length <= 10) {
                console.log('股票列表:', commonStocks);
            }
            
            return commonStocks;
        }
        
        function exportFreeCombinationStocks() {
            try {
                const exportBtn = document.getElementById('free-combination-export-btn');
                if (!exportBtn || exportBtn.disabled) {
                    alert('没有可导出的股票');
                    return;
                }
                
                const stocksJson = exportBtn.getAttribute('data-stocks');
                const sectorsJson = exportBtn.getAttribute('data-sectors');
                
                if (!stocksJson) {
                    alert('没有股票数据可导出');
                    return;
                }
                
                const stocks = JSON.parse(stocksJson);
                const sectors = JSON.parse(sectorsJson || '[]');
                
                if (!stocks || stocks.length === 0) {
                    alert('没有股票数据可导出');
                    return;
                }
                
                // 格式化股票数据为txt内容：一行一个代码加空格再加股票名称
                let txtContent = '';
                stocks.forEach((stock) => {
                    const parts = stock.split(' ');
                    const code = parts[0] || '';
                    const name = parts.slice(1).join(' ') || '';
                    txtContent += `${code} ${name}\n`;
                });
                
                // 创建Blob对象
                const blob = new Blob([txtContent], { type: 'text/plain;charset=utf-8' });
                
                // 创建下载链接
                const link = document.createElement('a');
                const sectorNames = sectors.join('_');
                const fileName = `板块自由组合_${sectorNames}_${new Date().toISOString().slice(0, 10)}.txt`;
                link.href = URL.createObjectURL(blob);
                link.download = fileName;
                
                // 触发下载
                document.body.appendChild(link);
                link.click();
                
                // 清理
                document.body.removeChild(link);
                URL.revokeObjectURL(link.href);
            } catch (error) {
                console.error('导出失败:', error);
                alert('导出失败：' + error.message);
            }
        }
        
        function updateData() {
            // 防止并发请求：如果正在更新，则跳过
            if (isUpdating) {
                console.log('数据更新中，跳过本次请求');
                return;
            }
            
            isUpdating = true;
            fetch(addClientIpHint('/api/data'))
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('update-time').textContent = 
                            '最后更新: ' + formatTime(data.last_update_time);
                        
                        updateTradingStatus(data.is_trading_time);
                        
                        document.getElementById('total-stocks').textContent = data.total_stocks || 0;
                        document.getElementById('total-plates').textContent = data.total_combined_tags || 0;
                        document.getElementById('top-plate').textContent = 
                            data.top_combined_tag || '-';
                        
                        renderCombinedStats(data.combined_stats || []);
                        
                        // 加载自由组合数据
                        loadFreeCombination(data.combined_stats || []);
                        
                        // 预加载所有股票数据（在后台静默加载，不阻塞UI，不显示加载提示）
                        if (!allStocksData && !isLoadingStocksData) {
                            console.log('预加载所有股票数据...');
                            loadAllStocksData(false); // 传入false表示不显示加载提示
                        }
                    } else {
                        console.error('获取数据失败:', data.error);
                    }
                })
                .catch(error => {
                    console.error('请求失败:', error);
                })
                .finally(() => {
                    isUpdating = false;
                });
        }
        
        // 初始加载
        updateData();
        
        // 页面加载完成后，立即预加载股票数据（如果还没加载）
        // 优化：减少延迟时间，在手机上更快开始预加载
        window.addEventListener('load', function() {
            // 立即开始预加载（不延迟），让数据在用户点击前就准备好
            if (!allStocksData && !isLoadingStocksData) {
                console.log('页面加载完成，立即预加载所有股票数据...');
                loadAllStocksData(false); // 传入false表示不显示加载提示
            }
        });
        
        // 额外：在DOMContentLoaded时也尝试预加载（更早开始）
        document.addEventListener('DOMContentLoaded', function() {
            // 延迟200ms后开始预加载，避免与页面渲染冲突
            setTimeout(function() {
                if (!allStocksData && !isLoadingStocksData) {
                    console.log('DOM加载完成，开始预加载所有股票数据...');
                    loadAllStocksData(false);
                }
            }, 200);
        });
        
        // 注意：自动刷新由updateTradingStatus函数根据交易时间动态控制
        // 交易时间：每1分钟自动刷新
        // 非交易时间：停止自动刷新，节省资源
    </script>
</body>
</html>
"""


def _is_private_ip(ip_str):
    """判断是否为内网IP"""
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
            }
            chinese_region = region_map.get(region, region)
            parts.append(chinese_region)
        
        # 城市
        if city:
            # 将英文城市名转换为中文
            city_map = {
                'Beijing': '北京', 'Shanghai': '上海', 'Guangzhou': '广州', 'Shenzhen': '深圳',
                'Tianjin': '天津', 'Chongqing': '重庆', 'Chengdu': '成都', 'Wuhan': '武汉',
                'Hangzhou': '杭州', 'Nanjing': '南京', 'Suzhou': '苏州', 'Changsha': '长沙',
            }
            chinese_city = city_map.get(city, city)
            parts.append(chinese_city)
        
        # ISP运营商
        if isp:
            # 将英文运营商名转换为中文
            isp_map = {
                'China Telecom': '中国电信', 'CHINA TELECOM': '中国电信',
                'China Unicom': '中国联通', 'CHINA UNICOM': '中国联通',
                'China Mobile': '中国移动', 'CHINA MOBILE': '中国移动',
                'Alibaba Cloud': '阿里云', 'Tencent Cloud': '腾讯云',
                'Baidu Cloud': '百度云', 'Huawei Cloud': '华为云',
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


def get_client_ip(request):
    """获取客户端真实IP地址（优先从代理头获取，其次从前端传递的client_ip_hint获取）"""
    try:
        # 按优先级检查各种代理头
        # 1. X-Forwarded-For（最常用，可能包含多个IP，取第一个）
        forwarded_for = request.headers.get('X-Forwarded-For')
        if forwarded_for:
            # 可能是逗号分隔的多级代理链，取第一个非空IP
            ips = [ip.strip() for ip in forwarded_for.split(',')]
            for ip in ips:
                if ip and not _is_private_ip(ip):
                    return ip
            # 如果都是内网IP，返回第一个
            if ips:
                return ips[0]
        
        # 2. X-Real-IP（Nginx等常用）
        x_real_ip = request.headers.get('X-Real-IP')
        if x_real_ip:
            ip = x_real_ip.strip()
            if ip:
                return ip
        
        # 3. HTTP_X_FORWARDED_FOR（环境变量形式）
        http_x_forwarded_for = request.environ.get('HTTP_X_FORWARDED_FOR')
        if http_x_forwarded_for:
            ips = [ip.strip() for ip in http_x_forwarded_for.split(',')]
            for ip in ips:
                if ip and not _is_private_ip(ip):
                    return ip
            if ips:
                return ips[0]
        
        # 4. CF-Connecting-IP（Cloudflare）
        cf_connecting_ip = request.headers.get('CF-Connecting-IP')
        if cf_connecting_ip:
            ip = cf_connecting_ip.strip()
            if ip:
                return ip
        
        # 5. True-Client-IP（某些CDN使用）
        true_client_ip = request.headers.get('True-Client-IP')
        if true_client_ip:
            ip = true_client_ip.strip()
            if ip:
                return ip
        
        # 6. X-Client-IP（某些代理使用）
        x_client_ip = request.headers.get('X-Client-IP')
        if x_client_ip:
            ip = x_client_ip.strip()
            if ip:
                return ip
        
        # 7. 从前端传递的client_ip_hint获取（用于内网穿透场景）
        client_ip_hint = request.args.get('client_ip_hint', '').strip()
        if client_ip_hint:
            return client_ip_hint
        
        # 8. 最后回退到 REMOTE_ADDR
        remote_addr = request.environ.get('REMOTE_ADDR', 'unknown')
        if remote_addr and remote_addr != 'unknown':
            # 如果 REMOTE_ADDR 是 127.0.0.1，可能是通过 localhost 访问
            # 尝试从其他环境变量获取
            if remote_addr == '127.0.0.1' or remote_addr == '::1':
                # 检查是否有其他方式获取IP
                # 某些情况下，可以通过 request.remote_addr 获取
                if hasattr(request, 'remote_addr') and request.remote_addr:
                    if request.remote_addr != '127.0.0.1' and request.remote_addr != '::1':
                        return request.remote_addr
            return remote_addr
        
        return 'unknown'
    except Exception as e:
        print(f"获取客户端IP失败: {e}")
        return 'unknown'


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


# 设置Web查询日志记录器
try:
    # 创建Web查询日志记录器
    web_logger = logging.getLogger('web_query_limit_up_monitor')
    web_logger.setLevel(logging.INFO)
    
    # 创建Web查询日志文件处理器
    web_log_file = os.path.join(current_dir, 'logs', 'web_query_limit_up_monitor.log')
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


def is_trading_day():
    """判断今天是否为交易日"""
    today = date.today()
    try:
        from utils.trading_day import is_tradeday
        return is_tradeday(today)
    except ImportError:
        # 如果没有chncal模块，简单判断工作日（周一到周五）
        return today.weekday() < 5


def is_trading_time():
    """判断当前是否为交易时间（9:25-15:00）"""
    now = datetime.now()
    current_time = now.time()
    
    # 交易时间：9:25 - 15:00
    start_time = dt_time(9, 25)
    end_time = dt_time(15, 0)
    
    # 判断是否为工作日（简单判断，不考虑节假日）
    weekday = now.weekday()  # 0=Monday, 6=Sunday
    
    # 周一到周五，且在交易时间内
    if weekday < 5 and start_time <= current_time <= end_time:
        return True
    
    return False


def kill_chrome_processes():
    """强制清理Selenium启动的Chrome和ChromeDriver进程（Windows）
    只关闭Selenium启动的Chrome进程，不影响用户正在使用的其他Chrome浏览器
    """
    try:
        if platform.system() == 'Windows':
            # 只关闭ChromeDriver进程（这个肯定是Selenium启动的）
            try:
                subprocess.run(['taskkill', '/F', '/IM', 'chromedriver.exe'], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except Exception:
                pass
            
            # 对于Chrome进程，只关闭Selenium启动的（通过检查命令行参数识别）
            # Selenium启动的Chrome通常包含以下特征参数之一：
            # --remote-debugging-port, --test-type=webdriver, --disable-blink-features=AutomationControlled
            try:
                # 使用wmic获取所有Chrome进程的命令行参数和进程ID
                result = subprocess.run(
                    ['wmic', 'process', 'where', "name='chrome.exe'", 'get', 'commandline,processid'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode == 0:
                    selenium_chrome_pids = []
                    
                    # 解析wmic输出，查找Selenium启动的Chrome进程
                    # wmic输出格式：CommandLine ProcessId（可能跨多行）
                    # 使用正则表达式匹配进程ID（数字）和命令行
                    output = result.stdout
                    
                    # 匹配模式：查找包含Selenium特征参数的行，并提取进程ID
                    # 进程ID通常是单独一行或多行输出的最后一部分数字
                    selenium_patterns = [
                        r'--remote-debugging-port',
                        r'--test-type=webdriver',
                        r'--disable-blink-features=AutomationControlled',
                        r'chromedriver',
                        r'selenium'
                    ]
                    
                    # 将输出按空行分割成块（每个进程一个块）
                    blocks = re.split(r'\n\s*\n', output)
                    for block in blocks:
                        block = block.strip()
                        if not block or 'CommandLine' in block or 'ProcessId' in block:
                            continue
                        
                        # 检查是否包含Selenium特征参数
                        block_lower = block.lower()
                        is_selenium_chrome = any(
                            re.search(pattern, block_lower, re.IGNORECASE) 
                            for pattern in selenium_patterns
                        )
                        
                        if is_selenium_chrome:
                            # 提取进程ID（块中的最后一个数字，通常是进程ID）
                            pid_matches = re.findall(r'\b(\d+)\b', block)
                            if pid_matches:
                                try:
                                    # 取最后一个数字作为进程ID（wmic输出中PID通常在最后）
                                    pid = int(pid_matches[-1])
                                    # 验证PID是否合理（Windows进程ID通常是4-5位数）
                                    if 100 <= pid <= 999999:
                                        selenium_chrome_pids.append(pid)
                                except (ValueError, IndexError):
                                    pass
                    
                    # 只关闭Selenium启动的Chrome进程
                    if selenium_chrome_pids:
                        for pid in selenium_chrome_pids:
                            try:
                                subprocess.run(['taskkill', '/F', '/PID', str(pid)], 
                                             stdout=subprocess.DEVNULL, 
                                             stderr=subprocess.DEVNULL, 
                                             timeout=3)
                            except Exception:
                                pass
                        print(f"已清理 {len(selenium_chrome_pids)} 个Selenium启动的Chrome进程和ChromeDriver进程")
                    else:
                        print("未找到Selenium启动的Chrome进程（可能已关闭）")
            except Exception as e:
                # 如果wmic命令失败，只关闭ChromeDriver，不关闭Chrome
                # 这样可以避免误关闭用户正在使用的浏览器
                print(f"无法识别Selenium Chrome进程（已关闭ChromeDriver）: {e}")
    except Exception as e:
        print(f"清理Chrome进程时出错（可忽略）: {e}")


def get_sleep_time():
    """根据当前时间计算合适的sleep时间（秒）
    如果即将进入交易时间，缩短sleep时间以便及时检测
    """
    now = datetime.now()
    current_time = now.time()
    weekday = now.weekday()
    
    # 非工作日，使用默认的60秒
    if weekday >= 5:
        return 60
    
    # 工作日
    start_time = dt_time(9, 25)  # 交易开始时间
    
    # 如果当前时间在 9:20-9:25 之间（即将进入交易时间），使用更短的sleep时间
    if dt_time(9, 20) <= current_time < start_time:
        # 计算距离交易开始还有多少秒，但最少10秒，最多30秒
        seconds_until_start = (start_time.hour * 3600 + start_time.minute * 60) - \
                              (current_time.hour * 3600 + current_time.minute * 60 + current_time.second)
        sleep_time = max(10, min(30, seconds_until_start + 5))  # 加5秒缓冲
        return sleep_time
    
    # 其他非交易时间，使用60秒
    return 60


def get_limit_up_stocks():
    """获取涨停板股票列表（从get_limit_up_dongcai模块获取）
    带超时保护，防止Selenium操作卡住
    带浏览器进程清理，防止内存溢出
    """
    try:
        # 在启动浏览器前，先清理可能残留的Chrome进程
        kill_chrome_processes()
        time.sleep(1)  # 等待进程清理完成
        
        print("正在使用Selenium从东方财富网获取涨停板数据...")
        
        # 使用线程池执行器添加超时保护（最多等待120秒）
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(get_limit_up_stocks_selenium)
        
        try:
            limit_up_df = future.result(timeout=120)  # 120秒超时
        except FutureTimeoutError:
            print("警告：获取涨停板数据超时（120秒），返回空数据")
            executor.shutdown(wait=False)  # 不等待线程完成，直接关闭
            # 超时后强制清理Chrome进程
            kill_chrome_processes()
            return []
        finally:
            executor.shutdown(wait=False)
            # 执行完成后，再次清理可能残留的Chrome进程
            time.sleep(0.5)  # 等待driver.quit()完成
            kill_chrome_processes()
        
        if limit_up_df is None or limit_up_df.empty:
            print("DataFrame为空或None")
            return []
        
        print(f"成功获取到 {len(limit_up_df)} 条涨停板数据")
        
        # 如果最后一个列名是空字符串，将其改为'所属行业'
        if len(limit_up_df.columns) > 0 and limit_up_df.columns[-1] == '':
            limit_up_df.columns = list(limit_up_df.columns[:-1]) + ['所属行业']
        
        print(f"列名: {list(limit_up_df.columns)}")
        # 打印前几行数据用于调试
        if len(limit_up_df) > 0:
            print(f"第一行数据示例: {dict(limit_up_df.iloc[0])}")
        
        stocks = []
        for idx, row in limit_up_df.iterrows():
            # 尝试不同的列名
            stock_code = None
            stock_name = None
            latest_price = 0
            change_pct = 0
            
            # 尝试获取代码
            for code_col in ['代码', '股票代码', 'code', '证券代码']:
                if code_col in row.index:
                    try:
                        code_val = str(row[code_col]).strip()
                        if code_val and code_val != 'nan' and code_val != '':
                            stock_code = code_val.zfill(6)
                            break
                    except:
                        pass
            
            # 如果通过列名没找到代码，尝试按位置索引获取（当列名都是空的时候）
            if not stock_code:
                try:
                    # 通常代码在第2列（索引1），名称在第3列（索引2）
                    # 根据日志：['1', '000890', '法尔胜', ...]
                    if len(row) > 1:
                        code_val = str(row.iloc[1] if hasattr(row, 'iloc') else row[1]).strip()
                        if code_val and code_val != 'nan' and code_val != '' and code_val.isdigit():
                            stock_code = code_val.zfill(6)
                            print(f"通过位置索引获取代码: {stock_code}")
                except Exception as e:
                    print(f"通过位置索引获取代码失败: {e}")
            
            # 尝试获取名称
            for name_col in ['名称', '股票名称', 'name', '证券简称']:
                if name_col in row.index:
                    try:
                        name_val = str(row[name_col]).strip()
                        if name_val and name_val != 'nan' and name_val != '':
                            stock_name = name_val
                            break
                    except:
                        pass
            
            # 如果通过列名没找到名称，尝试按位置索引获取
            if not stock_name:
                try:
                    # 通常名称在第3列（索引2）
                    if len(row) > 2:
                        name_val = str(row.iloc[2] if hasattr(row, 'iloc') else row[2]).strip()
                        if name_val and name_val != 'nan' and name_val != '':
                            stock_name = name_val
                            print(f"通过位置索引获取名称: {stock_name}")
                except Exception as e:
                    print(f"通过位置索引获取名称失败: {e}")
            
            # 尝试获取价格
            for price_col in ['最新价', '最新价格', 'price', '现价']:
                if price_col in row.index:
                    try:
                        price_val = str(row[price_col]).strip()
                        if price_val and price_val != 'nan' and price_val != '':
                            latest_price = float(price_val)
                            break
                    except:
                        pass
            
            # 尝试获取涨跌幅
            for pct_col in ['涨跌幅', '涨幅', 'change_pct', '涨跌幅度']:
                if pct_col in row.index:
                    try:
                        pct_val = str(row[pct_col]).strip()
                        if pct_val and pct_val != 'nan' and pct_val != '':
                            # 移除百分号
                            pct_val = pct_val.replace('%', '')
                            change_pct = float(pct_val)
                            break
                    except:
                        pass
            
            if not stock_code or not stock_name:
                continue
            
            # 尝试从涨停板数据中获取所属行业
            industry = None
            
            def safe_get_value(row, col_name):
                """安全地获取DataFrame行的单个值，确保返回标量而不是Series"""
                try:
                    val = row[col_name]
                    # 如果是Series，取第一个值
                    if hasattr(val, 'iloc'):
                        val = val.iloc[0] if len(val) > 0 else None
                    # 如果是numpy数组，取第一个元素
                    elif hasattr(val, '__len__') and not isinstance(val, str):
                        val = val[0] if len(val) > 0 else None
                    return val
                except:
                    return None
            
            def clean_industry_name(val):
                """清理行业名称，移除不需要的信息"""
                if not val or val == 'nan':
                    return None
                val_str = str(val).strip()
                # 移除pandas Series的字符串表示（包含Name和dtype）
                if 'Name:' in val_str and 'dtype:' in val_str:
                    # 尝试提取实际值
                    parts = val_str.split('Name:')
                    if len(parts) > 0:
                        val_str = parts[0].strip()
                # 移除包含"连板"、"首板"等字样的部分
                if '连板' in val_str or '首板' in val_str:
                    # 尝试提取行业名称（通常在"连板"或"首板"之后）
                    parts = val_str.split('连板')
                    if len(parts) > 1:
                        val_str = parts[-1].strip()
                    parts = val_str.split('首板')
                    if len(parts) > 1:
                        val_str = parts[-1].strip()
                # 移除日期格式（如"1/1"）
                val_str = re.sub(r'\d+/\d+\s*', '', val_str).strip()
                # 验证：行业名称不应该包含"连板"、"首板"、"Name"、"dtype"等字样
                invalid_keywords = ['连板', '首板', 'Name:', 'dtype:', 'object']
                if any(keyword in val_str for keyword in invalid_keywords):
                    return None
                return val_str if val_str else None
            
            # 首先尝试常见的列名
            for industry_col in ['所属行业', '行业', 'industry']:
                if industry_col in row.index:
                    try:
                        val = safe_get_value(row, industry_col)
                        industry_val = clean_industry_name(val)
                        if industry_val:
                            industry = industry_val
                            break
                    except:
                        pass
            
            # 如果还没找到，尝试查找未命名的列（CSV表头末尾逗号导致最后一列没有列名）
            if not industry:
                # 方法1：先找到"连板数"列的位置，然后检查它后面的列（应该是空列名的所属行业列）
                try:
                    # 查找"连板数"列
                    lianban_col_idx = None
                    for idx, col_name in enumerate(row.index):
                        if col_name == '连板数':
                            lianban_col_idx = idx
                            break
                    
                    # 如果找到"连板数"列，检查它后面的列（应该是空列名）
                    if lianban_col_idx is not None and lianban_col_idx + 1 < len(row.index):
                        next_col_name = row.index[lianban_col_idx + 1]
                        # 检查是否是空列名或Unnamed列
                        if next_col_name == '' or (isinstance(next_col_name, str) and next_col_name.startswith('Unnamed')):
                            try:
                                val = safe_get_value(row, next_col_name)
                                industry_val = clean_industry_name(val)
                                if industry_val:
                                    industry = industry_val
                            except:
                                pass
                except:
                    pass
                
                # 方法2：如果方法1没找到，遍历所有列查找空列名或Unnamed列
                if not industry:
                    for col_name in row.index:
                        # 检查是否是未命名列（空字符串或以Unnamed开头）
                        # 但要排除"连板数"列本身
                        if col_name != '连板数' and (col_name == '' or (isinstance(col_name, str) and col_name.startswith('Unnamed'))):
                            try:
                                val = safe_get_value(row, col_name)
                                industry_val = clean_industry_name(val)
                                if industry_val:
                                    industry = industry_val
                                    break
                            except:
                                pass
            
            stocks.append({
                'code': stock_code,
                'name': stock_name,
                'price': latest_price,
                'change_pct': change_pct,
                'industry': industry  # 所属行业
            })
        
        # 加载股票信息JSON文件，获取概念和板块信息
        json_path = os.path.join(current_dir, 'data', 'all_a_stock_info.json')
        stock_info_dict = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    stock_info_dict = json.load(f)
                print(f"成功加载股票信息文件，包含 {len(stock_info_dict)} 只股票的信息")
            except Exception as e:
                print(f"加载股票信息文件失败: {e}")
        else:
            print(f"股票信息文件不存在: {json_path}")
        
        # 为每只股票添加概念和板块信息
        for stock in stocks:
            code = str(stock.get('code', '')).zfill(6)
            stock_info = stock_info_dict.get(code, {})
            
            # 概念：列表转字符串（用分号分隔）
            concepts = stock_info.get('concepts', [])
            if concepts and isinstance(concepts, list):
                stock['concepts'] = concepts
            else:
                stock['concepts'] = []
            
            # 板块：列表
            plates = stock_info.get('plates', [])
            if plates and isinstance(plates, list):
                stock['plates'] = plates
            else:
                stock['plates'] = []
        
        print(f"成功解析 {len(stocks)} 只涨停股票")
        return stocks
    except Exception as e:
        print(f"获取涨停板股票失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def calculate_plate_stats(stocks):
    """计算行业统计"""
    industry_count = {}
    
    for stock in stocks:
        # 统计所属行业
        industry = stock.get('industry')
        if industry and industry.strip() and industry != 'nan':
            industry_name = industry.strip()
            if _is_excluded_tag(industry_name):
                continue
            if industry_name not in industry_count:
                industry_count[industry_name] = {
                    'name': industry_name,
                    'count': 0,
                    'stocks': []
                }
            industry_count[industry_name]['count'] += 1
            # 确保股票代码格式化为6位
            code = str(stock.get('code', '')).zfill(6)
            industry_count[industry_name]['stocks'].append(f"{code} {stock['name']}")
    
    # 转换为列表并按涨停数量排序
    plate_stats = list(industry_count.values())
    plate_stats.sort(key=lambda x: x['count'], reverse=True)
    
    return plate_stats


def calculate_concept_stats(stocks):
    """计算概念题材统计"""
    excluded_concepts = EXCLUDED_CONCEPTS
    
    concept_count = {}
    
    for stock in stocks:
        # 统计概念题材
        concepts = stock.get('concepts', [])
        if concepts and isinstance(concepts, list):
            for concept in concepts:
                if concept and str(concept).strip():
                    concept_name = str(concept).strip()
                    aggregate_name = _aggregate_tag_name(concept_name) or concept_name.strip()
                    # 排除常见概念/板块（概念被归一化后也要排除）
                    if aggregate_name in excluded_concepts or aggregate_name in EXCLUDED_SECTORS:
                        continue
                    if aggregate_name not in concept_count:
                        concept_count[aggregate_name] = {
                            'name': aggregate_name,
                            'count': 0,
                            'stocks': []
                        }
                    concept_count[aggregate_name]['count'] += 1
                    # 确保股票代码格式化为6位
                    code = str(stock.get('code', '')).zfill(6)
                    stock_str = f"{code} {stock['name']}"
                    if stock_str not in concept_count[aggregate_name]['stocks']:
                        concept_count[aggregate_name]['stocks'].append(stock_str)
    
    # 转换为列表并按涨停数量排序，只保留数量>=2的概念
    concept_stats = [item for item in concept_count.values() if item['count'] >= 2]
    concept_stats.sort(key=lambda x: x['count'], reverse=True)
    
    return concept_stats


def calculate_sector_plate_stats(stocks):
    """计算板块统计"""
    excluded_sectors = EXCLUDED_SECTORS
    
    sector_plate_count = {}
    
    for stock in stocks:
        # 统计板块
        plates = stock.get('plates', [])
        if plates and isinstance(plates, list):
            for plate in plates:
                if plate and str(plate).strip():
                    plate_name = str(plate).strip()
                    # 排除常见板块
                    if plate_name in excluded_sectors:
                        continue
                    if plate_name not in sector_plate_count:
                        sector_plate_count[plate_name] = {
                            'name': plate_name,
                            'count': 0,
                            'stocks': []
                        }
                    sector_plate_count[plate_name]['count'] += 1
                    # 确保股票代码格式化为6位
                    code = str(stock.get('code', '')).zfill(6)
                    stock_str = f"{code} {stock['name']}"
                    if stock_str not in sector_plate_count[plate_name]['stocks']:
                        sector_plate_count[plate_name]['stocks'].append(stock_str)
    
    # 转换为列表并按涨停数量排序，只保留数量>=2的板块
    sector_plate_stats = [item for item in sector_plate_count.values() if item['count'] >= 2]
    sector_plate_stats.sort(key=lambda x: x['count'], reverse=True)
    
    return sector_plate_stats


def calculate_combined_stats(stocks):
    """计算综合排名（合并行业、概念、板块）"""
    excluded_concepts = EXCLUDED_CONCEPTS
    excluded_sectors = EXCLUDED_SECTORS
    
    combined_count = {}
    
    for stock in stocks:
        # 确保股票代码格式化为6位
        code = str(stock.get('code', '')).zfill(6)
        stock_str = f"{code} {stock['name']}"
        
        # 1. 添加行业
        industry = stock.get('industry')
        if industry and industry.strip() and industry != 'nan':
            industry_name = industry.strip()
            if industry_name in excluded_sectors or industry_name in excluded_concepts:
                continue
            if industry_name not in combined_count:
                combined_count[industry_name] = {
                    'name': industry_name,
                    'stocks': set()  # 使用 set 自动去重
                }
            combined_count[industry_name]['stocks'].add(stock_str)
        
        # 2. 添加概念；「xxx概念」归入「xxx」（如光伏概念→光伏），与行业合并
        concepts = stock.get('concepts', [])
        if concepts and isinstance(concepts, list):
            for concept in concepts:
                if concept and str(concept).strip():
                    concept_name = str(concept).strip()
                    if concept_name.endswith('概念'):
                        aggregate_name = concept_name[:-2].strip()  # 光伏概念 -> 光伏
                        if not aggregate_name:
                            continue
                    else:
                        aggregate_name = concept_name
                    # 排除常见概念和板块
                    if aggregate_name in excluded_concepts or aggregate_name in excluded_sectors:
                        continue
                    if aggregate_name not in combined_count:
                        combined_count[aggregate_name] = {
                            'name': aggregate_name,
                            'stocks': set()  # 使用 set 自动去重
                        }
                    combined_count[aggregate_name]['stocks'].add(stock_str)
        
        # 3. 添加板块
        plates = stock.get('plates', [])
        if plates and isinstance(plates, list):
            for plate in plates:
                if plate and str(plate).strip():
                    plate_name = str(plate).strip()
                    # 排除常见板块
                    if plate_name in excluded_sectors:
                        continue
                    if plate_name not in combined_count:
                        combined_count[plate_name] = {
                            'name': plate_name,
                            'stocks': set()  # 使用 set 自动去重
                        }
                    combined_count[plate_name]['stocks'].add(stock_str)
    
    # 转换为列表，计算 count（使用 stocks 集合的长度），并按涨停数量排序
    combined_stats = []
    for name, data in combined_count.items():
        stocks_list = sorted(list(data['stocks']))  # 转换为排序后的列表
        combined_stats.append({
            'name': data['name'],
            'count': len(data['stocks']),  # 使用集合长度作为计数（自动去重）
            'stocks': stocks_list
        })
    
    combined_stats.sort(key=lambda x: x['count'], reverse=True)
    
    return combined_stats


def _existing_daily_limit_up_file_has_rows(today_str: str) -> bool:
    """当日 CSV/JSON 是否已有有效涨停数据（用于避免空抓取覆盖）。"""
    csv_filename = os.path.join(HISTORY_DATA_DIR, f'涨停板数据_{today_str}.csv')
    json_filename = resolve_limit_up_day_json_path(today_str, HISTORY_DATA_DIR)

    if json_filename:
        try:
            with open(json_filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('total_stocks', 0) > 0 or len(data.get('limit_up_stocks') or []) > 0:
                return True
        except Exception:
            pass

    if os.path.exists(csv_filename):
        try:
            df = pd.read_csv(csv_filename, encoding='utf-8-sig')
            if len(df) > 0:
                return True
        except Exception:
            try:
                with open(csv_filename, 'r', encoding='utf-8-sig') as f:
                    return sum(1 for _ in f) > 1
            except Exception:
                pass
    return False


def _restore_today_cache_from_history(today_str: str) -> bool:
    """抓取失败时从今日已保存 JSON 恢复内存缓存。"""
    history = load_history_data(today_str)
    if not history or not history.get('limit_up_stocks'):
        return False

    stocks = history['limit_up_stocks']
    _data_cache['limit_up_stocks'] = stocks
    _data_cache['plate_stats'] = history.get('plate_stats') or calculate_plate_stats(stocks)
    _data_cache['concept_stats'] = history.get('concept_stats') or calculate_concept_stats(stocks)
    _data_cache['sector_plate_stats'] = history.get('sector_plate_stats') or calculate_sector_plate_stats(stocks)
    _data_cache['combined_stats'] = calculate_combined_stats(stocks)
    _data_cache['last_update_time'] = history.get('timestamp') or int(time.time())
    _data_cache['is_trading_time'] = is_trading_time()
    print(f"从磁盘恢复今日涨停数据: {len(stocks)} 只")
    return True


def save_daily_data(stocks, plate_stats, concept_stats=None, sector_plate_stats=None):
    """保存每日数据到文件（每次刷新都保存，非交易日不保存）"""
    try:
        # 非交易日不保存
        if not is_trading_day():
            print("非交易日，跳过数据保存")
            return
        
        today = date.today()
        today_str = today.strftime('%Y-%m-%d')

        if not stocks and _existing_daily_limit_up_file_has_rows(today_str):
            print(f"跳过保存：本次未获取到涨停数据，保留已有文件（{today_str}）")
            return
        
        # 准备保存的数据
        data_to_save = {
            'date': today_str,
            'timestamp': int(time.time()),
            'limit_up_stocks': stocks,
            'plate_stats': plate_stats,
            'concept_stats': concept_stats or [],
            'sector_plate_stats': sector_plate_stats or [],
            'total_stocks': len(stocks),
            'total_industries': len(plate_stats)
        }
        
        # 保存JSON文件（每次刷新都保存，覆盖之前的数据）
        ensure_limit_up_day_data_dir(HISTORY_DATA_DIR)
        json_filename = limit_up_day_json_path(today_str, HISTORY_DATA_DIR)
        with open(json_filename, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        
        print(f"数据已保存到: {json_filename} (时间: {datetime.now().strftime('%H:%M:%S')})")
        
        # 同时保存CSV文件（每次刷新都保存，覆盖之前的数据）
        csv_filename = os.path.join(HISTORY_DATA_DIR, f'涨停板数据_{today_str}.csv')
        print(f"开始保存CSV文件: {csv_filename}, 股票数量: {len(stocks) if stocks else 0}")
        
        try:
            # 检查pandas是否可用
            try:
                pd.DataFrame()
            except Exception as pd_err:
                print(f"错误：pandas不可用: {pd_err}")
                print("请安装pandas: pip install pandas")
                return
            
            # 准备CSV数据
            csv_data = []
            if stocks:
                for stock in stocks:
                    # 确保股票代码格式化为6位
                    code = str(stock.get('code', '')).zfill(6)
                    csv_data.append({
                        '代码': code,
                        '名称': stock.get('name', ''),
                        '最新价': stock.get('price', 0),
                        '涨跌幅(%)': stock.get('change_pct', 0),
                        '所属行业': stock.get('industry', '')
                    })
            else:
                print("警告：当前没有涨停股票数据，将创建空的CSV文件（当日尚无有效历史文件）")
            
            # 使用pandas保存为CSV
            df = pd.DataFrame(csv_data)
            df.to_csv(csv_filename, index=False, encoding='utf-8-sig')  # 使用utf-8-sig以便Excel正确打开
            
            # 验证文件是否创建成功
            if os.path.exists(csv_filename):
                file_size = os.path.getsize(csv_filename)
                print(f"✓ CSV文件已成功保存到: {csv_filename}")
                print(f"  文件大小: {file_size} 字节, 股票数量: {len(csv_data)}, 时间: {datetime.now().strftime('%H:%M:%S')}")
            else:
                print(f"错误：CSV文件保存后不存在: {csv_filename}")
                
        except ImportError as e:
            print(f"错误：无法导入pandas模块: {e}")
            print("请安装pandas: pip install pandas")
            import traceback
            traceback.print_exc()
        except Exception as e:
            print(f"保存CSV文件失败: {e}")
            print(f"CSV文件路径: {csv_filename}")
            print(f"HISTORY_DATA_DIR: {HISTORY_DATA_DIR}")
            print(f"目录是否存在: {os.path.exists(HISTORY_DATA_DIR)}")
            import traceback
            traceback.print_exc()
        
    except Exception as e:
        print(f"保存每日数据失败: {e}")
        import traceback
        traceback.print_exc()


def load_history_data(date_str):
    """加载指定日期的历史数据"""
    try:
        filename = resolve_limit_up_day_json_path(date_str, HISTORY_DATA_DIR)
        if not filename:
            return None
        
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"加载历史数据失败: {e}")
        return None


def get_available_dates():
    """获取所有可用的历史日期"""
    try:
        return list_limit_up_day_dates(HISTORY_DATA_DIR, reverse=True)
    except Exception as e:
        print(f"获取可用日期失败: {e}")
        return []


def get_recent_trading_days(count=5):
    """获取最近N个交易日（从历史数据文件中查找）
    
    Args:
        count: 需要获取的交易日数量，默认5个
    
    Returns:
        日期字符串列表，格式为 YYYY-MM-DD，按日期倒序排列（最新的在前）
    """
    try:
        # 获取所有可用的历史日期
        all_dates = get_available_dates()
        
        if not all_dates:
            return []
        
        # 返回最近N个交易日
        return all_dates[:count]
    except Exception as e:
        print(f"获取最近交易日失败: {e}")
        return []


def get_recent_comparison_data():
    """获取最近七个交易日的行业涨停对比数据
    只返回每日数量大于等于2的行业
    注意：在9:30之前，如果日期是今天，则返回空数据（保留日期列，但industries为空）
    
    Returns:
        列表，每个元素包含：
        {
            'date': 'YYYY-MM-DD',
            'total_industries': 行业数量,
            'industries': [
                {'name': '行业名称', 'count': 涨停数量},
                ...
            ]
        }
    """
    try:
        # 获取最近7个交易日
        recent_dates = get_recent_trading_days(7)
        
        if not recent_dates:
            return []
        
        # 检查当前时间，如果在9:30之前，则今天的数据返回空
        # 但只有在今天是交易日的情况下才需要检查
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        current_time = now.time()
        is_before_930 = current_time < dt_time(9, 30)  # 9:30之前
        is_today_trading = is_trading_day()  # 检查今天是否是交易日
        
        comparison_data = []
        
        for date_str in recent_dates:
            # 如果是今天、是交易日、且时间在9:30之前，返回空数据
            if date_str == today_str and is_today_trading and is_before_930:
                comparison_data.append({
                    'date': date_str,
                    'total_industries': 0,
                    'industries': []
                })
                continue
            
            # 加载该日期的历史数据
            history_data = load_history_data(date_str)
            if not history_data:
                continue
            
            # 获取行业统计数据
            plate_stats = history_data.get('plate_stats', [])
            
            # 只保留每日数量大于等于2的行业
            filtered_industries = []
            for plate in plate_stats:
                if plate.get('count', 0) >= 2:
                    filtered_industries.append({
                        'name': plate.get('name', ''),
                        'count': plate.get('count', 0)
                    })
            
            # 按涨停数量排序（降序）
            filtered_industries.sort(key=lambda x: x['count'], reverse=True)
            
            # 添加数据（即使为空也添加，以保持日期列的一致性）
            comparison_data.append({
                'date': date_str,
                'total_industries': len(filtered_industries),
                'industries': filtered_industries
            })
        
        return comparison_data
    except Exception as e:
        print(f"获取近期行业对比数据失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_recent_concept_comparison_data():
    """获取最近七个交易日的概念涨停对比数据
    只返回每日数量大于等于2的概念
    注意：在9:30之前，如果日期是今天，则返回空数据（保留日期列，但concepts为空）
    
    Returns:
        列表，每个元素包含：
        {
            'date': 'YYYY-MM-DD',
            'total_concepts': 概念数量,
            'concepts': [
                {'name': '概念名称', 'count': 涨停数量},
                ...
            ]
        }
    """
    try:
        # 获取最近7个交易日
        recent_dates = get_recent_trading_days(7)
        
        if not recent_dates:
            return []
        
        # 检查当前时间，如果在9:30之前，则今天的数据返回空
        # 但只有在今天是交易日的情况下才需要检查
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        current_time = now.time()
        is_before_930 = current_time < dt_time(9, 30)  # 9:30之前
        is_today_trading = is_trading_day()  # 检查今天是否是交易日
        
        comparison_data = []
        
        for date_str in recent_dates:
            # 如果是今天、是交易日、且时间在9:30之前，返回空数据
            if date_str == today_str and is_today_trading and is_before_930:
                comparison_data.append({
                    'date': date_str,
                    'total_concepts': 0,
                    'concepts': []
                })
                continue
            
            # 加载该日期的历史数据
            history_data = load_history_data(date_str)
            if not history_data:
                continue
            
            # 获取概念统计数据
            concept_stats = history_data.get('concept_stats', [])
            
            excluded_concepts = EXCLUDED_CONCEPTS
            
            # 只保留每日数量大于等于2的概念，并排除常见概念
            filtered_concepts = []
            for concept in concept_stats:
                concept_name = concept.get('name', '')
                aggregate_name = _aggregate_tag_name(concept_name) or (concept_name or '').strip()
                if not aggregate_name or _is_excluded_tag(aggregate_name):
                    continue
                if concept.get('count', 0) >= 2:
                    filtered_concepts.append({
                        'name': aggregate_name,
                        'count': concept.get('count', 0)
                    })
            
            # 按涨停数量排序（降序）
            filtered_concepts.sort(key=lambda x: x['count'], reverse=True)
            
            # 添加数据（即使为空也添加，以保持日期列的一致性）
            comparison_data.append({
                'date': date_str,
                'total_concepts': len(filtered_concepts),
                'concepts': filtered_concepts
            })
        
        return comparison_data
    except Exception as e:
        print(f"获取近期概念对比数据失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_recent_sector_comparison_data():
    """获取最近七个交易日的板块涨停对比数据
    只返回每日数量大于等于2的板块
    注意：在9:30之前，如果日期是今天，则返回空数据（保留日期列，但sectors为空）
    
    Returns:
        列表，每个元素包含：
        {
            'date': 'YYYY-MM-DD',
            'total_sectors': 板块数量,
            'sectors': [
                {'name': '板块名称', 'count': 涨停数量},
                ...
            ]
        }
    """
    try:
        # 获取最近7个交易日
        recent_dates = get_recent_trading_days(7)
        
        if not recent_dates:
            return []
        
        # 检查当前时间，如果在9:30之前，则今天的数据返回空
        # 但只有在今天是交易日的情况下才需要检查
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        current_time = now.time()
        is_before_930 = current_time < dt_time(9, 30)  # 9:30之前
        is_today_trading = is_trading_day()  # 检查今天是否是交易日
        
        comparison_data = []
        
        for date_str in recent_dates:
            # 如果是今天、是交易日、且时间在9:30之前，返回空数据
            if date_str == today_str and is_today_trading and is_before_930:
                comparison_data.append({
                    'date': date_str,
                    'total_sectors': 0,
                    'sectors': []
                })
                continue
            
            # 加载该日期的历史数据
            history_data = load_history_data(date_str)
            if not history_data:
                continue
            
            # 获取板块统计数据
            sector_plate_stats = history_data.get('sector_plate_stats', [])
            
            excluded_sectors = EXCLUDED_SECTORS
            
            # 只保留每日数量大于等于2的板块，并排除常见板块
            filtered_sectors = []
            for sector in sector_plate_stats:
                sector_name = sector.get('name', '')
                aggregate_name = _aggregate_tag_name(sector_name) or (sector_name or '').strip()
                if not aggregate_name or _is_excluded_tag(aggregate_name):
                    continue
                # 只保留数量大于等于2的板块
                if sector.get('count', 0) >= 2:
                    filtered_sectors.append({
                        'name': aggregate_name,
                        'count': sector.get('count', 0)
                    })
            
            # 按涨停数量排序（降序）
            filtered_sectors.sort(key=lambda x: x['count'], reverse=True)
            
            # 添加数据（即使为空也添加，以保持日期列的一致性）
            comparison_data.append({
                'date': date_str,
                'total_sectors': len(filtered_sectors),
                'sectors': filtered_sectors
            })
        
        return comparison_data
    except Exception as e:
        print(f"获取近期板块对比数据失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_recent_combined_comparison_data():
    """获取最近十一个交易日的综合对比数据（合并行业、概念、板块）
    只返回每日板块数量的前30名
    注意：在交易日的9:30之前，会跳过今天的数据，只显示到前一个交易日为止
    
    Returns:
        列表，每个元素包含：
        {
            'date': 'YYYY-MM-DD',
            'total_items': 标签数量,
            'items': [
                {'name': '标签名称', 'count': 涨停数量},
                ...
            ]
        }
    """
    try:
        excluded_concepts = EXCLUDED_CONCEPTS
        excluded_sectors = EXCLUDED_SECTORS
        
        # 获取最近11个交易日
        recent_dates = get_recent_trading_days(11)
        
        if not recent_dates:
            return []
        
        # 检查当前时间，如果在9:30之前，则今天的数据不显示（跳过）
        # 但只有在今天是交易日的情况下才需要检查
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        current_time = now.time()
        is_before_930 = current_time < dt_time(9, 30)  # 9:30之前
        is_today_trading = is_trading_day()  # 检查今天是否是交易日
        
        comparison_data = []
        
        for date_str in recent_dates:
            try:
                # 如果是今天、是交易日、且时间在9:30之前，跳过今天的数据（不显示）
                if date_str == today_str and is_today_trading and is_before_930:
                    continue  # 直接跳过，不添加任何数据
                
                # 加载该日期的历史数据
                history_data = load_history_data(date_str)
                if not history_data:
                    print(f"[近期综合对比] 日期 {date_str} 的历史数据不存在，跳过")
                    # 即使数据不存在，也添加一个空条目以保持日期列的一致性
                    comparison_data.append({
                        'date': date_str,
                        'total_items': 0,
                        'items': []
                    })
                    continue
            
                # 使用字典合并行业、概念、板块，自动去重（如果名称相同，取较大的count）
                combined_items = {}
                
                # 1. 添加行业；若名称以「概念」结尾则归入去掉「概念」的名称
                plate_stats = history_data.get('plate_stats', [])
                if plate_stats:
                    for plate in plate_stats:
                        try:
                            plate_name = plate.get('name', '').strip()
                            if not plate_name or plate_name == 'nan':
                                continue
                            if plate_name.endswith('概念'):
                                aggregate_name = plate_name[:-2].strip()
                                if not aggregate_name:
                                    continue
                            else:
                                aggregate_name = plate_name
                            if aggregate_name in excluded_sectors or aggregate_name in excluded_concepts:
                                continue
                            count = plate.get('count', 0)
                            if count > 0:
                                if aggregate_name not in combined_items or combined_items[aggregate_name]['count'] < count:
                                    combined_items[aggregate_name] = {
                                        'name': aggregate_name,
                                        'count': count
                                    }
                        except Exception as e:
                            print(f"[近期综合对比] 处理日期 {date_str} 的行业数据时出错: {e}")
                            continue
                
                # 2. 添加概念；「xxx概念」归入「xxx」，与行业合并（取较大 count）
                concept_stats = history_data.get('concept_stats', [])
                if concept_stats:
                    for concept in concept_stats:
                        try:
                            concept_name = concept.get('name', '').strip()
                            if not concept_name or concept_name in excluded_concepts:
                                continue
                            if concept_name.endswith('概念'):
                                aggregate_name = concept_name[:-2].strip()
                                if not aggregate_name:
                                    continue
                            else:
                                aggregate_name = concept_name
                            if aggregate_name in excluded_sectors or aggregate_name in excluded_concepts:
                                continue
                            count = concept.get('count', 0)
                            if count > 0:
                                if aggregate_name not in combined_items or combined_items[aggregate_name]['count'] < count:
                                    combined_items[aggregate_name] = {
                                        'name': aggregate_name,
                                        'count': count
                                    }
                        except Exception as e:
                            print(f"[近期综合对比] 处理日期 {date_str} 的概念数据时出错: {e}")
                            continue
                
                # 3. 添加板块；「xxx概念」同样归入「xxx」
                sector_plate_stats = history_data.get('sector_plate_stats', [])
                if sector_plate_stats:
                    for sector in sector_plate_stats:
                        try:
                            sector_name = sector.get('name', '').strip()
                            if not sector_name or sector_name in excluded_sectors:
                                continue
                            if sector_name.endswith('概念'):
                                aggregate_name = sector_name[:-2].strip()
                                if not aggregate_name:
                                    continue
                            else:
                                aggregate_name = sector_name
                            if aggregate_name in excluded_sectors:
                                continue
                            count = sector.get('count', 0)
                            if count > 0:
                                if aggregate_name not in combined_items or combined_items[aggregate_name]['count'] < count:
                                    combined_items[aggregate_name] = {
                                        'name': aggregate_name,
                                        'count': count
                                    }
                        except Exception as e:
                            print(f"[近期综合对比] 处理日期 {date_str} 的板块数据时出错: {e}")
                            continue
                
                # 转换为列表并按涨停数量排序（降序）
                filtered_items = list(combined_items.values())
                filtered_items.sort(key=lambda x: x['count'], reverse=True)
                
                # 只取前30名
                filtered_items = filtered_items[:30]
                
                # 添加数据（即使为空也添加，以保持日期列的一致性）
                comparison_data.append({
                    'date': date_str,
                    'total_items': len(filtered_items),
                    'items': filtered_items
                })
            except Exception as e:
                print(f"[近期综合对比] 处理日期 {date_str} 时出错: {e}")
                import traceback
                traceback.print_exc()
                # 即使出错，也添加一个空条目以保持日期列的一致性
                comparison_data.append({
                    'date': date_str,
                    'total_items': 0,
                    'items': []
                })
                continue
        
        print(f"[近期综合对比] 成功处理 {len(comparison_data)} 个交易日的数据")
        return comparison_data
    except Exception as e:
        print(f"[近期综合对比] 获取数据失败: {e}")
        import traceback
        traceback.print_exc()
        # 即使出错，也返回空列表而不是None，避免前端报错
        return []


def get_all_stocks_info():
    """获取所有股票信息（从data目录下的all_a_stock_info.json加载）"""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, 'data', 'all_a_stock_info.json')
        
        if not os.path.exists(json_path):
            print(f"股票信息文件不存在: {json_path}")
            return {}
        
        with open(json_path, 'r', encoding='utf-8') as f:
            stock_info_dict = json.load(f)
        
        print(f"成功加载所有股票信息，包含 {len(stock_info_dict)} 只股票")
        return stock_info_dict
    except Exception as e:
        print(f"加载所有股票信息失败: {e}")
        import traceback
        traceback.print_exc()
        return {}


def parse_date_from_filename(filename: str) -> str:
    """从文件名中提取日期
    
    Args:
        filename: 文件名，如 Table_20251224.xls
    
    Returns:
        str: 日期字符串，如 2025-12-24
    """
    # 提取 Table_YYYYMMDD.xls 中的日期部分
    match = re.search(r'Table_(\d{8})\.xls', filename)
    if match:
        date_str = match.group(1)
        # 转换为 YYYY-MM-DD 格式
        year = date_str[:4]
        month = date_str[4:6]
        day = date_str[6:8]
        return f"{year}-{month}-{day}"
    return ""


def filter_stocks_by_text(stock_info_dict: Dict, search_text: str) -> List[Tuple[str, str]]:
    """根据输入的文本筛选股票
    
    Args:
        stock_info_dict: 股票信息字典
        search_text: 行业、概念、板块
    
    Returns:
        List[Tuple[str, str]]: [(股票代码, 股票名称), ...]
    """
    if not search_text or not search_text.strip():
        return []
    
    search_text = search_text.strip()
    matched_stocks = []
    
    for code, stock_info in stock_info_dict.items():
        code = str(code).zfill(6)
        name = stock_info.get('name', '未知')
        
        # 检查行业
        industry = stock_info.get('industry', '')
        if industry and search_text in str(industry):
            matched_stocks.append((code, name))
            continue
        
        # 检查概念（支持 list 或 逗号/空格分隔的字符串）
        concepts = stock_info.get('concepts', [])
        if concepts is not None:
            if isinstance(concepts, str):
                concepts = [c.strip() for c in concepts.replace('，', ',').split(',') if c.strip()]
            if isinstance(concepts, list):
                matched_in_concepts = False
                for concept in concepts:
                    if concept and search_text in str(concept).strip():
                        matched_stocks.append((code, name))
                        matched_in_concepts = True
                        break
                if matched_in_concepts:
                    continue
        
        # 检查板块（支持 list 或 逗号/空格分隔的字符串）
        plates = stock_info.get('plates', [])
        if plates is not None:
            if isinstance(plates, str):
                plates = [p.strip() for p in plates.replace('，', ',').split(',') if p.strip()]
            if isinstance(plates, list):
                for plate in plates:
                    if plate and search_text in str(plate).strip():
                        matched_stocks.append((code, name))
                        break
    
    # 去重（按代码）
    seen_codes = set()
    unique_stocks = []
    for code, name in matched_stocks:
        if code not in seen_codes:
            seen_codes.add(code)
            unique_stocks.append((code, name))
    
    return unique_stocks


def _parse_date_from_limit_csv_filename(filename: str) -> str:
    """从 涨停板数据_YYYY-MM-DD.csv 文件名中提取日期，返回 YYYY-MM-DD。"""
    match = re.search(r'涨停板数据_(\d{4})-(\d{2})-(\d{2})\.csv', filename)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return ""


def load_limit_up_data_from_files(history_data_dir: str, start_date: str = None) -> Dict[str, Set[str]]:
    """加载 history_data 下的涨停板数据。

    新逻辑：
    - 优先使用每天自动生成的 JSON 文件（YYYY-MM-DD.json）；
    - 再加载 涨停板数据_YYYY-MM-DD.csv（如果存在，补充当日的其它来源）；
    - 仅在某个日期既没有 JSON 也没有 CSV 时，才考虑 Table_YYYYMMDD.xls（旧文件）。
    """
    limit_up_data: Dict[str, Set[str]] = {}
    
    # 如果提供了起始日期，转换为datetime对象用于比较
    start_date_obj = None
    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
        except ValueError:
            print(f"起始日期格式错误: {start_date}，将忽略日期过滤")
            start_date_obj = None
    
    def _apply_start_date(date_str: str) -> bool:
        if not start_date_obj:
            return True
        try:
            file_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            return file_date >= start_date_obj
        except ValueError:
            return False
    
    # 0) 先从 JSON 文件加载（YYYY-MM-DD.json）
    for _date_key, json_file in list_limit_up_day_json_files(history_data_dir):
        try:
            filename = os.path.basename(json_file)
            base_date = filename.split('.')[0]
            # 先用文件名推个日期，后面如 JSON 内有 date 字段再覆盖
            date_str = base_date
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get('date'):
                date_str = data['date']
            if not date_str or not _apply_start_date(date_str):
                continue
            stocks = data.get('limit_up_stocks') or []
            codes: Set[str] = set()
            for item in stocks:
                code_raw = str(item.get('code', '')).strip()
                clean = ''.join(c for c in code_raw if c.isdigit())
                if len(clean) == 6:
                    codes.add(clean.zfill(6))
            if codes:
                if date_str not in limit_up_data:
                    limit_up_data[date_str] = set()
                limit_up_data[date_str] |= codes
        except Exception as e:
            print(f"处理涨停板 JSON 文件 {json_file} 时出错: {e}")
            continue
    
    # 1) 从 涨停板数据_YYYY-MM-DD.csv 加载（与 JSON 互补）
    csv_pattern = os.path.join(history_data_dir, '涨停板数据_*.csv')
    for csv_file in glob.glob(csv_pattern):
        try:
            filename = os.path.basename(csv_file)
            date_str = _parse_date_from_limit_csv_filename(filename)
            if not date_str or not _apply_start_date(date_str):
                continue
            df = None
            for enc in ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'gb18030']:
                try:
                    df = pd.read_csv(csv_file, encoding=enc, dtype=str)
                    break
                except Exception:
                    continue
            if df is None or df.empty:
                continue
            code_col = None
            for col in df.columns:
                if '代码' in str(col).strip() or str(col).strip().lower() == 'code':
                    code_col = col
                    break
            if code_col is None and len(df.columns) >= 1:
                code_col = df.columns[0]
            if code_col is None:
                continue
            stock_codes = set()
            for _, row in df.iterrows():
                code = str(row[code_col]).strip() if pd.notna(row.get(code_col)) else ''
                clean = ''.join(c for c in code if c.isdigit())
                if len(clean) == 6:
                    stock_codes.add(clean.zfill(6))
            if stock_codes:
                if date_str not in limit_up_data:
                    limit_up_data[date_str] = set()
                limit_up_data[date_str] |= stock_codes
        except Exception as e:
            print(f"处理涨停板CSV {csv_file} 时出错: {e}")
            continue
    
    # 2) 查找所有Table_*.xls文件（仅在该日期尚无 JSON/CSV 数据时才作为补充）
    pattern = os.path.join(history_data_dir, 'Table_*.xls')
    xls_files = glob.glob(pattern)
    
    for xls_file in xls_files:
        try:
            # 从文件名提取日期
            filename = os.path.basename(xls_file)
            date_str = parse_date_from_filename(filename)
            if not date_str or not _apply_start_date(date_str):
                continue

            # 如果该日期已经有 JSON 或 CSV 数据，就不再用 Table 文件
            if date_str in limit_up_data:
                continue
            
            # 读取文件（这些.xls文件实际上是制表符分隔的文本文件）
            df = None
            last_error = None
            
            # 方法1: 尝试作为TSV（制表符分隔）文件读取
            encodings = ['gbk', 'gb2312', 'gb18030', 'utf-8', 'utf-8-sig']
            for encoding in encodings:
                try:
                    df = pd.read_csv(xls_file, sep='\t', encoding=encoding, dtype=str)
                    if df is not None and not df.empty:
                        break
                except Exception as e:
                    last_error = e
                    continue
            
            # 方法2: 如果TSV读取失败，尝试作为HTML读取
            if df is None or df.empty:
                for encoding in encodings:
                    try:
                        html_tables = pd.read_html(xls_file, encoding=encoding)
                        if html_tables and len(html_tables) > 0:
                            df = html_tables[0]
                            break
                    except Exception as e:
                        last_error = e
                        continue
            
            # 方法3: 如果HTML读取失败，尝试Excel引擎
            if df is None or df.empty:
                try:
                    df = pd.read_excel(xls_file, engine='xlrd')
                except Exception as e1:
                    last_error = e1
                    try:
                        df = pd.read_excel(xls_file, engine='openpyxl')
                    except Exception as e2:
                        last_error = e2
                        print(f"读取文件失败 {xls_file}: {last_error}")
                        continue
            
            if df is None or df.empty:
                print(f"读取文件失败或文件为空 {xls_file}")
                continue
            
            # 查找代码列
            code_col = None
            for col in df.columns:
                col_str = str(col).strip()
                if '代码' in col_str or 'code' in col_str.lower():
                    code_col = col
                    break
            
            if code_col is None:
                # 没有“代码”列时，尝试第0列或第1列：选更像代码的一列（6位数字多）
                def _count_6digit_in_col(series):
                    n = 0
                    for v in series:
                        s = str(v).strip() if pd.notna(v) else ''
                        m = re.search(r'["\']?(\d{6})["\']?', s)
                        clean = m.group(1) if m else ''.join(c for c in s if c.isdigit())
                        if clean and len(clean) == 6:
                            n += 1
                    return n
                c0 = _count_6digit_in_col(df.iloc[:, 0]) if len(df.columns) >= 1 else 0
                c1 = _count_6digit_in_col(df.iloc[:, 1]) if len(df.columns) >= 2 else 0
                if c0 >= c1 and len(df.columns) >= 1:
                    code_col = df.columns[0]
                elif len(df.columns) >= 2:
                    code_col = df.columns[1]
                else:
                    code_col = df.columns[0]
            
            # 提取股票代码
            stock_codes = set()
            for idx, row in df.iterrows():
                code = str(row[code_col]).strip() if pd.notna(row[code_col]) else ''
                # 处理格式如 = "600693" 的情况
                match = re.search(r'["\'](\d{6})["\']', code)
                if match:
                    code_clean = match.group(1)
                else:
                    code_clean = ''.join(c for c in code if c.isdigit())
                
                if code_clean and len(code_clean) == 6:
                    stock_codes.add(code_clean.zfill(6))
            
            if stock_codes:
                # 同一天可能有多份文件（如不同来源），合并而非覆盖
                if date_str not in limit_up_data:
                    limit_up_data[date_str] = set()
                limit_up_data[date_str] |= stock_codes
                
        except Exception as e:
            print(f"处理文件 {xls_file} 时出错: {e}")
            continue
    
    return limit_up_data


def generate_sector_distribution_table(matched_stocks: List[Tuple[str, str]], 
                                      limit_up_data: Dict[str, Set[str]]) -> List[Dict]:
    """生成板块历史分布表格数据
    
    Args:
        matched_stocks: 匹配的股票列表 [(code, name), ...]
        limit_up_data: 涨停板数据 {日期: {股票代码集合}}
    
    Returns:
        List[Dict]: [{'日期': date, '数量': count, '涨停股票': 'code1 name1;code2 name2;...'}, ...]
    """
    # 创建股票代码到名称的映射
    stock_dict = {code: name for code, name in matched_stocks}
    matched_codes = set(stock_dict.keys())
    
    # 统计每只股票在整个时间段内的涨停次数
    stock_limit_up_count = {}
    for date_str, limit_up_codes in limit_up_data.items():
        for code in limit_up_codes:
            if code in matched_codes:
                stock_limit_up_count[code] = stock_limit_up_count.get(code, 0) + 1
    
    # 收集所有在涨停板数据中出现过的匹配股票代码
    all_limit_up_codes = set()
    for date_str, limit_up_codes in limit_up_data.items():
        for code in limit_up_codes:
            if code in matched_codes:
                all_limit_up_codes.add(code)
    
    # 生成结果列表
    results = []
    
    # 按日期排序
    sorted_dates = sorted(limit_up_data.keys())
    
    for date_str in sorted_dates:
        limit_up_codes = limit_up_data[date_str]
        
        # 找出匹配的股票中哪些在当天涨停了
        matched_limit_up = []
        for code in limit_up_codes:
            if code in matched_codes:
                name = stock_dict.get(code, '未知')
                # 如果涨停次数>1，在名称后加上次数
                count = stock_limit_up_count.get(code, 0)
                if count > 1:
                    matched_limit_up.append(f"{code} {name}({count})")
                else:
                    matched_limit_up.append(f"{code} {name}")
        
        # 如果当天有匹配的股票涨停，添加到结果中
        if matched_limit_up:
            # 为每只股票添加涨停次数信息，用于前端颜色标识
            stock_items = []
            for code in limit_up_codes:
                if code in matched_codes:
                    name = stock_dict.get(code, '未知')
                    count = stock_limit_up_count.get(code, 0)
                    stock_items.append({
                        'code': code,
                        'name': name,
                        'count': count
                    })
            
            results.append({
                '日期': date_str,
                '数量': len(matched_limit_up),
                '涨停股票': ';'.join(matched_limit_up),
                '股票详情': stock_items  # 添加详细信息用于前端渲染
            })
    
    # 在最后添加一行，显示从未涨停的股票
    never_limit_up_codes = matched_codes - all_limit_up_codes
    if never_limit_up_codes:
        never_limit_up_stocks = []
        for code in sorted(never_limit_up_codes):
            name = stock_dict.get(code, '未知')
            never_limit_up_stocks.append(f"{code} {name}")
        
        results.append({
            '日期': '未涨停股票',
            '数量': len(never_limit_up_codes),
            '涨停股票': ';'.join(never_limit_up_stocks)
        })
    
    return results


def get_sector_combination_data():
    """获取热门板块组合数据
    返回一个二维表格，显示前十名板块之间的组合股票（排除涨停股票）
    
    Returns:
        {
            'top_sectors': ['板块1', '板块2', ...],  # 前十名板块
            'combinations': {
                '板块1,板块2': ['000001 股票1', '000002 股票2', ...],  # 同时属于这两个板块的股票列表
                ...
            }
        }
    """
    try:
        global _data_cache
        
        # 获取板块排名的前十名
        combined_stats = _data_cache.get('combined_stats', [])
        if len(combined_stats) < 10:
            return {
                'top_sectors': [s['name'] for s in combined_stats],
                'combinations': {}
            }
        
        top_sectors = [s['name'] for s in combined_stats[:10]]
        
        # 获取所有股票信息
        all_stocks_info = get_all_stocks_info()
        if not all_stocks_info:
            return {
                'top_sectors': top_sectors,
                'combinations': {}
            }
        
        # 获取涨停股票代码集合（用于排除）
        limit_up_stocks = _data_cache.get('limit_up_stocks', [])
        limit_up_codes = set()
        for stock in limit_up_stocks:
            code = str(stock.get('code', '')).zfill(6)
            limit_up_codes.add(code)
        
        excluded_concepts = EXCLUDED_CONCEPTS
        excluded_sectors = EXCLUDED_SECTORS
        
        # 为每个股票构建板块集合（行业+概念+板块）
        stock_sectors_map = {}  # {code: set(板块名称)}
        
        for code, stock_info in all_stocks_info.items():
            code = str(code).zfill(6)
            # 跳过涨停股票
            if code in limit_up_codes:
                continue
            
            sectors = set()
            
            # 1. 添加行业
            industry = stock_info.get('industry')
            if industry and industry.strip() and industry != 'nan':
                industry_name = industry.strip()
                if not _is_excluded_tag(industry_name):
                    sectors.add(industry_name)
            
            # 2. 添加概念
            concepts = stock_info.get('concepts', [])
            if concepts and isinstance(concepts, list):
                for concept in concepts:
                    if concept and str(concept).strip():
                        concept_name = str(concept).strip()
                        aggregate_name = _aggregate_tag_name(concept_name) or concept_name
                        if aggregate_name and aggregate_name not in excluded_concepts and aggregate_name not in excluded_sectors:
                            sectors.add(aggregate_name)
            
            # 3. 添加板块
            plates = stock_info.get('plates', [])
            if plates and isinstance(plates, list):
                for plate in plates:
                    if plate and str(plate).strip():
                        plate_name = str(plate).strip()
                        aggregate_name = _aggregate_tag_name(plate_name) or plate_name
                        if aggregate_name and aggregate_name not in excluded_sectors and aggregate_name not in excluded_concepts:
                            sectors.add(aggregate_name)
            
            if sectors:
                stock_sectors_map[code] = {
                    'sectors': sectors,
                    'name': stock_info.get('name', '未知')
                }
        
        # 计算板块组合
        combinations = {}
        
        for i, sector1 in enumerate(top_sectors):
            for j, sector2 in enumerate(top_sectors):
                if i == j:
                    # 对角线显示为"-"
                    key = f"{sector1},{sector2}"
                    combinations[key] = None  # None表示对角线
                elif i < j:
                    # 只计算上三角（i < j），因为i>j时与j<i相同
                    # 找出同时属于这两个板块的股票
                    common_stocks = []
                    for code, stock_data in stock_sectors_map.items():
                        if sector1 in stock_data['sectors'] and sector2 in stock_data['sectors']:
                            stock_str = f"{code} {stock_data['name']}"
                            common_stocks.append(stock_str)
                    
                    # 排序
                    common_stocks.sort()
                    key = f"{sector1},{sector2}"
                    combinations[key] = common_stocks
        
        return {
            'top_sectors': top_sectors,
            'combinations': combinations
        }
    except Exception as e:
        print(f"获取板块组合数据失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            'top_sectors': [],
            'combinations': {}
        }


def update_data():
    """更新数据（带超时保护和更新频率限制）"""
    global _data_cache
    
    with _data_cache['lock']:
        try:
            # 检查最小更新间隔（防止过于频繁地打开浏览器）
            MIN_UPDATE_INTERVAL = 120  # 最小更新间隔：120秒（2分钟）
            now = time.time()
            last_browser_update = _data_cache.get('last_browser_update_time')
            
            if last_browser_update is not None:
                elapsed = now - last_browser_update
                if elapsed < MIN_UPDATE_INTERVAL:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 距离上次浏览器更新仅{elapsed:.1f}秒，跳过本次更新（最小间隔{MIN_UPDATE_INTERVAL}秒）")
                    return  # 跳过本次更新，使用缓存数据
            
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始更新数据...")
            
            # 获取涨停板股票（带超时保护）
            try:
                stocks = get_limit_up_stocks()
                print(f"获取到 {len(stocks)} 只涨停股票")
                # 更新浏览器更新时间
                _data_cache['last_browser_update_time'] = time.time()
            except Exception as e:
                print(f"获取涨停板股票时出错: {e}")
                import traceback
                traceback.print_exc()
                stocks = []  # 出错时使用空列表
            
            if not stocks:
                print("未获取到涨停股票")
                if _data_cache.get('limit_up_stocks'):
                    print(f"抓取失败，保留内存缓存（{len(_data_cache['limit_up_stocks'])} 只）")
                    return

                today_str = date.today().strftime('%Y-%m-%d')
                if _restore_today_cache_from_history(today_str):
                    return

                print("尚无有效历史数据，使用空数据")
                _data_cache['limit_up_stocks'] = []
                _data_cache['plate_stats'] = []
                _data_cache['concept_stats'] = []
                _data_cache['sector_plate_stats'] = []
                _data_cache['combined_stats'] = []
                _data_cache['last_update_time'] = int(time.time())
                _data_cache['is_trading_time'] = is_trading_time()
                save_daily_data([], [], [], [])
                return
            
            # 计算行业统计
            plate_stats = calculate_plate_stats(stocks)
            
            # 计算概念题材统计
            concept_stats = calculate_concept_stats(stocks)
            
            # 计算板块统计
            sector_plate_stats = calculate_sector_plate_stats(stocks)
            
            # 计算综合排名（合并行业、概念、板块）
            combined_stats = calculate_combined_stats(stocks)
            
            # 调试信息：检查股票数据中的行业信息
            stocks_with_industry = sum(1 for s in stocks if s.get('industry'))
            stocks_with_concepts = sum(1 for s in stocks if s.get('concepts'))
            stocks_with_plates = sum(1 for s in stocks if s.get('plates'))
            print(f"股票数据统计: 有行业信息={stocks_with_industry}, 有概念信息={stocks_with_concepts}, 有板块信息={stocks_with_plates}")
            
            # 更新缓存
            _data_cache['limit_up_stocks'] = stocks
            _data_cache['plate_stats'] = plate_stats
            _data_cache['concept_stats'] = concept_stats
            _data_cache['sector_plate_stats'] = sector_plate_stats
            _data_cache['combined_stats'] = combined_stats
            _data_cache['last_update_time'] = int(time.time())
            _data_cache['is_trading_time'] = is_trading_time()
            
            # 保存每日数据（每次刷新都保存，非交易日不保存）
            save_daily_data(stocks, plate_stats, concept_stats, sector_plate_stats)
            
            print(f"数据更新完成，涨停股票数: {len(stocks)}, 行业数: {len(plate_stats)}, 概念数: {len(concept_stats)}, 板块数: {len(sector_plate_stats)}, 综合标签数: {len(combined_stats)}")
            if combined_stats:
                print(f"综合排名示例（前3个）: {[c['name'] + ':' + str(c['count']) for c in combined_stats[:3]]}")
            
        except Exception as e:
            print(f"更新数据失败: {e}")
            import traceback
            traceback.print_exc()


def background_update_thread():
    """后台更新线程（带异常保护，防止线程崩溃）"""
    while True:
        try:
            # 如果是交易时间，每30秒更新一次
            # 如果不是交易时间，每小时更新一次，但需要频繁检查是否进入交易时间
            if is_trading_time():
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [后台线程] 交易时间，开始更新数据...")
                try:
                    update_data()
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [后台线程] 数据更新完成，等待100秒后再次更新...")
                except Exception as e:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [后台线程] 更新数据时出错: {e}")
                    import traceback
                    traceback.print_exc()
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [后台线程] 继续运行，等待100秒后重试...")
                time.sleep(100)  # 交易时间每100秒更新一次，减少浏览器打开频率
            else:
                # 非交易时间
                now = datetime.now()
                current_time = now.time()
                current_hour = now.hour
                
                # 特殊处理：交易日15:00-15:30之间，至少再更新一次，确保保存当天的数据
                is_today_trading_day = is_trading_day()
                is_after_market_close = dt_time(15, 0) <= current_time <= dt_time(15, 30)
                
                # 检查今天的数据是否已保存
                today_str = date.today().strftime('%Y-%m-%d')
                today_data_saved = resolve_limit_up_day_json_path(today_str, HISTORY_DATA_DIR) is not None
                
                # 如果是交易日，且在15:00-15:30之间，且今天的数据还没保存，则更新
                should_update_after_close = (is_today_trading_day and 
                                            is_after_market_close and 
                                            not today_data_saved)
                
                # 非交易时间，只在首次或每小时更新一次，或者在交易日收盘后需要保存数据时更新
                if _data_cache['last_update_time'] is None or \
                   (current_hour != datetime.fromtimestamp(_data_cache['last_update_time']).hour) or \
                   should_update_after_close:
                    try:
                        update_data()
                    except Exception as e:
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [后台线程] 非交易时间更新数据时出错: {e}")
                        import traceback
                        traceback.print_exc()
                
                # 非交易时间：根据时间智能调整sleep时间
                # 如果即将进入交易时间（9:20-9:25），使用更短的sleep时间以便及时检测
                # 其他时间每1分钟检查一次
                sleep_time = get_sleep_time()
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [后台线程] 非交易时间，等待{sleep_time}秒后检查...")
                time.sleep(sleep_time)
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [后台线程] 后台更新线程错误: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)


def run_fetch_once() -> int:
    """抓取并保存一次涨停板数据后退出（不启动 Web 服务）。"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [--once] 开始单次抓取...")
    try:
        update_data()
        stocks = _data_cache.get('limit_up_stocks') or []
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [--once] 完成，"
            f"涨停 {len(stocks)} 只，程序退出"
        )
        return 0
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [--once] 失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main():
    parser = argparse.ArgumentParser(description="涨停板板块监控 Web 应用")
    parser.add_argument(
        "--once",
        action="store_true",
        help="仅抓取并保存一次数据后退出（不启动 Web 服务与后台循环）",
    )
    args = parser.parse_args()

    if args.once:
        sys.exit(run_fetch_once())

    import socket

    # 启动后台更新线程（后台线程会负责首次更新，避免重复打开浏览器）
    update_thread = threading.Thread(target=background_update_thread, daemon=True)
    update_thread.start()

    # 注意：不再立即调用update_data()，避免与后台线程重复打开浏览器
    # 后台线程会在启动后立即执行首次更新

    # 检测可用端口
    def find_free_port(start_port=5000, max_attempts=10):
        """查找可用端口"""
        for port in range(start_port, start_port + max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('127.0.0.1', port))
                    return port
            except OSError:
                continue
        return None

    # 查找可用端口
    port = find_free_port(5000)
    if port is None:
        print("错误：无法找到可用端口")
        sys.exit(1)

    # 启动Flask应用
    print("涨停板板块监控Web应用启动中...")
    print(f"访问地址: http://localhost:{port}")
    if port != 5000:
        print(f"注意：端口5000被占用，已自动切换到端口{port}")

    try:
        # 启用多线程支持，提高并发处理能力
        # threaded=True: 允许处理多个并发请求
        # 注意：Flask开发服务器不适合生产环境，如需更高并发，建议使用Gunicorn或uWSGI
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
    except Exception as e:
        print(f"启动失败: {e}")
        import traceback
        traceback.print_exc()


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


@app.route('/')
def index():
    """首页"""
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/data')
def api_data():
    """API：获取数据"""
    global _data_cache
    
    # 不记录此接口的访问日志（自动刷新调用太频繁）
    
    with _data_cache['lock']:
        # 如果是交易时间且数据为空，立即更新
        if is_trading_time() and not _data_cache['limit_up_stocks']:
            update_data()
        
        # 特殊处理：交易日15:00-15:30之间，如果今天的数据还没保存，也触发更新
        if not is_trading_time():
            now = datetime.now()
            current_time = now.time()
            is_today_trading_day = is_trading_day()
            is_after_market_close = dt_time(15, 0) <= current_time <= dt_time(15, 30)
            
            # 检查今天的数据是否已保存
            today_str = date.today().strftime('%Y-%m-%d')
            today_data_saved = resolve_limit_up_day_json_path(today_str, HISTORY_DATA_DIR) is not None
            
            # 如果是交易日，且在15:00-15:30之间，且今天的数据还没保存，则更新
            if is_today_trading_day and is_after_market_close and not today_data_saved:
                print(f"[API] 交易日收盘后，检测到今天数据未保存，触发更新...")
                update_data()
        
        # 计算统计数据
        total_stocks = len(_data_cache['limit_up_stocks'])
        combined_stats = _data_cache.get('combined_stats', [])
        total_combined_tags = len(combined_stats)
        top_combined_tag = combined_stats[0]['name'] if combined_stats else None
        
        return jsonify({
            'success': True,
            'limit_up_stocks': _data_cache['limit_up_stocks'],
            'plate_stats': _data_cache.get('plate_stats', []),
            'concept_stats': _data_cache.get('concept_stats', []),
            'sector_plate_stats': _data_cache.get('sector_plate_stats', []),
            'combined_stats': combined_stats,
            'total_stocks': total_stocks,
            'total_plates': total_combined_tags,  # 保持兼容性，但使用综合标签数
            'total_combined_tags': total_combined_tags,
            'top_plate': top_combined_tag,  # 保持兼容性，但使用综合排名第一
            'top_combined_tag': top_combined_tag,
            'last_update_time': _data_cache['last_update_time'],
            'is_trading_time': is_trading_time()
        })


@app.route('/api/history')
def api_history():
    """API：获取历史数据"""
    date_str = request.args.get('date')
    
    # 记录访问者信息
    try:
        client_ip = get_client_ip(request)
        query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if hasattr(app, 'web_query_logger') and app.web_query_logger:
            ip_location = get_ip_location(client_ip)
            app.web_query_logger.info(
                f"Web查询 - 时间: {query_time}, IP: {client_ip}（{ip_location}）, 接口: /api/history, 日期: {date_str or '未指定'}"
            )
    except Exception as e:
        print(f"记录访问者信息失败: {e}")
    
    if not date_str:
        return jsonify({
            'success': False,
            'message': '请提供日期参数（格式：YYYY-MM-DD）'
        }), 400
    
    try:
        # 验证日期格式
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({
            'success': False,
            'message': '日期格式错误，请使用 YYYY-MM-DD 格式'
        }), 400
    
    # 加载历史数据
    history_data = load_history_data(date_str)
    
    # 如果查询的是当天的数据，且数据不存在，且是交易日收盘后（15:00-15:30），则触发更新
    if history_data is None:
        today_str = date.today().strftime('%Y-%m-%d')
        if date_str == today_str:
            now = datetime.now()
            current_time = now.time()
            is_today_trading_day = is_trading_day()
            is_after_market_close = dt_time(15, 0) <= current_time <= dt_time(15, 30)
            
            # 如果是交易日，且在15:00-15:30之间，则触发更新
            if is_today_trading_day and is_after_market_close:
                print(f"[API] 查询当天历史数据，但数据不存在，触发更新...")
                global _data_cache
                with _data_cache['lock']:
                    update_data()
                # 更新后再次尝试加载
                history_data = load_history_data(date_str)
    
    if history_data is None:
        return jsonify({
            'success': False,
            'message': f'未找到 {date_str} 的历史数据'
        }), 404
    
    excluded_sectors = EXCLUDED_SECTORS
    excluded_concepts = EXCLUDED_CONCEPTS
    
    # 过滤板块统计数据，排除常见板块
    sector_plate_stats = history_data.get('sector_plate_stats', [])
    filtered_sector_plate_stats = [
        item for item in sector_plate_stats 
        if _aggregate_tag_name(item.get('name', '')) and not _is_excluded_tag(_aggregate_tag_name(item.get('name', '')))
    ]
    
    # 过滤概念统计数据，排除常见概念
    concept_stats = history_data.get('concept_stats', [])
    filtered_concept_stats = [
        item for item in concept_stats 
        if _aggregate_tag_name(item.get('name', '')) and not _is_excluded_tag(_aggregate_tag_name(item.get('name', '')))
    ]
    
    # 计算综合排名：如果历史数据中有 stocks，从 stocks 计算；否则从已有的统计数据合并
    stocks = history_data.get('limit_up_stocks', [])
    if stocks:
        combined_stats = calculate_combined_stats(stocks)
    else:
        # 如果没有 stocks，尝试从已有的统计数据合并（兼容旧数据）
        combined_stats = history_data.get('combined_stats', [])
        if not combined_stats:
            # 如果也没有 combined_stats，从 plate_stats、concept_stats、sector_plate_stats 合并
            # 这里简化处理：只使用 plate_stats（因为旧数据可能没有完整的 stocks）
            combined_stats = history_data.get('plate_stats', [])
    
    # 把「xxx概念」合并到「xxx」：同名的股票归到一处
    merged_combined = {}
    for x in combined_stats:
        name = (x.get('name') or '').strip()
        if not name:
            continue
        if name.endswith('概念'):
            base = name[:-2].strip()
            if not base:
                continue
            key = base
        else:
            key = name
        if key not in merged_combined:
            merged_combined[key] = {'name': key, 'stocks': set()}
        for s in (x.get('stocks') or []):
            merged_combined[key]['stocks'].add(s)
    combined_stats = []
    for key, data in merged_combined.items():
        if key in excluded_sectors or key in excluded_concepts:
            continue
        stocks_list = sorted(list(data['stocks']))
        combined_stats.append({'name': key, 'count': len(stocks_list), 'stocks': stocks_list})
    combined_stats.sort(key=lambda x: x['count'], reverse=True)
    
    total_combined_tags = len(combined_stats)
    top_combined_tag = combined_stats[0]['name'] if combined_stats else None
    
    return jsonify({
        'success': True,
        'date': history_data.get('date'),
        'limit_up_stocks': stocks,
        'plate_stats': history_data.get('plate_stats', []),
        'concept_stats': filtered_concept_stats,
        'sector_plate_stats': filtered_sector_plate_stats,
        'combined_stats': combined_stats,
        'total_stocks': history_data.get('total_stocks', 0),
        'total_industries': history_data.get('total_industries', 0),
        'total_combined_tags': total_combined_tags,
        'top_combined_tag': top_combined_tag,
        'timestamp': history_data.get('timestamp')
    })


@app.route('/api/history/dates')
def api_history_dates():
    """API：获取所有可用的历史日期"""
    dates = get_available_dates()
    return jsonify({
        'success': True,
        'dates': dates,
        'count': len(dates)
    })


def find_latest_csv_file(date_str, prefix):
    """查找指定日期的最新CSV文件
    
    Args:
        date_str: 日期字符串，格式 YYYY-MM-DD
        prefix: 文件前缀，如 '10日内涨停' 或 '10日内接近涨停'
    
    Returns:
        最新的文件路径，如果不存在则返回None
    """
    try:
        # 将日期格式从 YYYY-MM-DD 转换为 YYYYMMDD
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        date_ymd = date_obj.strftime('%Y%m%d')
        
        # 查找匹配的文件
        matching_files = []
        if os.path.exists(HISTORY_DATA_DIR):
            for filename in os.listdir(HISTORY_DATA_DIR):
                # 文件名格式：10日内涨停_YYYYMMDD_HHMMSS.csv 或 10日内接近涨停_YYYYMMDD_HHMMSS.csv
                expected_prefix = prefix + '_' + date_ymd + '_'
                if filename.startswith(expected_prefix) and filename.endswith('.csv'):
                    file_path = os.path.join(HISTORY_DATA_DIR, filename)
                    # 从文件名中提取时间戳（HHMMSS）
                    try:
                        # 文件名格式：prefix_YYYYMMDD_HHMMSS.csv
                        # 使用更健壮的方式提取时间戳：去掉前缀和.csv，然后提取最后6位
                        # 例如：10日内接近涨停_20251121_153857.csv
                        # expected_prefix = "10日内接近涨停_20251121_"
                        # 去掉前缀后：153857.csv
                        # 去掉.csv后：153857
                        time_part = filename[len(expected_prefix):-4]  # 去掉前缀和.csv
                        if len(time_part) == 6 and time_part.isdigit():
                            # 转换为整数用于排序
                            time_stamp = int(time_part)
                            matching_files.append((file_path, filename, time_stamp))
                        else:
                            # 如果时间戳格式不对，使用文件修改时间作为备选
                            mtime = os.path.getmtime(file_path)
                            matching_files.append((file_path, filename, mtime))
                    except Exception as e:
                        # 如果解析失败，使用文件修改时间作为备选
                        print(f"解析文件名时间戳失败 {filename}: {e}")
                        mtime = os.path.getmtime(file_path)
                        matching_files.append((file_path, filename, mtime))
        
        if not matching_files:
            return None, None
        
        # 按时间戳排序，返回最新的文件（时间戳最大的）
        matching_files.sort(key=lambda x: x[2], reverse=True)
        return matching_files[0][0], matching_files[0][1]
    except Exception as e:
        print(f"查找CSV文件失败: {e}")
        return None, None


def read_csv_file(file_path):
    """读取CSV文件并返回数据
    
    Returns:
        (columns, rows) 或 (None, None) 如果失败
    """
    try:
        # 尝试不同的编码
        encodings = ['utf-8-sig', 'utf-8', 'gbk', 'gb2312']
        df = None
        
        for encoding in encodings:
            try:
                df = pd.read_csv(file_path, encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        
        if df is None:
            return None, None
        
        # 转换为字典列表
        columns = list(df.columns)
        rows = df.to_dict('records')
        
        # 将NaN值转换为空字符串
        for row in rows:
            for key, value in row.items():
                if pd.isna(value):
                    row[key] = ''
                else:
                    row[key] = str(value)
        
        return columns, rows
    except Exception as e:
        print(f"读取CSV文件失败: {e}")
        import traceback
        traceback.print_exc()
        return None, None


@app.route('/api/csv/limit-up')
def api_csv_limit_up():
    """API：获取10日内涨停CSV数据"""
    date_str = request.args.get('date')
    
    # 记录访问者信息
    try:
        client_ip = get_client_ip(request)
        query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if hasattr(app, 'web_query_logger') and app.web_query_logger:
            ip_location = get_ip_location(client_ip)
            app.web_query_logger.info(
                f"Web查询 - 时间: {query_time}, IP: {client_ip}（{ip_location}）, 接口: /api/csv/limit-up, 日期: {date_str or '未指定'}"
            )
    except Exception as e:
        print(f"记录访问者信息失败: {e}")
    
    if not date_str:
        return jsonify({
            'success': False,
            'message': '请提供日期参数（格式：YYYY-MM-DD）'
        }), 400
    
    try:
        # 验证日期格式
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({
            'success': False,
            'message': '日期格式错误，请使用 YYYY-MM-DD 格式'
        }), 400
    
    # 查找文件
    file_path, filename = find_latest_csv_file(date_str, '10日内涨停')
    if not file_path:
        return jsonify({
            'success': False,
            'message': f'未找到 {date_str} 的10日内涨停CSV文件'
        }), 404
    
    # 读取CSV文件
    columns, rows = read_csv_file(file_path)
    if columns is None:
        return jsonify({
            'success': False,
            'message': f'读取CSV文件失败: {filename}'
        }), 500
    
    return jsonify({
        'success': True,
        'date': date_str,
        'filename': filename,
        'columns': columns,
        'rows': rows,
        'row_count': len(rows)
    })


@app.route('/api/csv/near-limit')
def api_csv_near_limit():
    """API：获取10日内接近涨停CSV数据"""
    date_str = request.args.get('date')
    
    # 记录访问者信息
    try:
        client_ip = get_client_ip(request)
        query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if hasattr(app, 'web_query_logger') and app.web_query_logger:
            ip_location = get_ip_location(client_ip)
            app.web_query_logger.info(
                f"Web查询 - 时间: {query_time}, IP: {client_ip}（{ip_location}）, 接口: /api/csv/near-limit, 日期: {date_str or '未指定'}"
            )
    except Exception as e:
        print(f"记录访问者信息失败: {e}")
    
    if not date_str:
        return jsonify({
            'success': False,
            'message': '请提供日期参数（格式：YYYY-MM-DD）'
        }), 400
    
    try:
        # 验证日期格式
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({
            'success': False,
            'message': '日期格式错误，请使用 YYYY-MM-DD 格式'
        }), 400
    
    # 查找文件
    file_path, filename = find_latest_csv_file(date_str, '10日内接近涨停')
    if not file_path:
        return jsonify({
            'success': False,
            'message': f'未找到 {date_str} 的10日内接近涨停CSV文件'
        }), 404
    
    # 读取CSV文件
    columns, rows = read_csv_file(file_path)
    if columns is None:
        return jsonify({
            'success': False,
            'message': f'读取CSV文件失败: {filename}'
        }), 500
    
    return jsonify({
        'success': True,
        'date': date_str,
        'filename': filename,
        'columns': columns,
        'rows': rows,
        'row_count': len(rows)
    })


@app.route('/api/csv/limit-up/download')
def api_csv_limit_up_download():
    """API：下载10日内涨停CSV文件"""
    from flask import send_file, abort
    date_str = request.args.get('date')
    if not date_str:
        abort(400, '请提供日期参数（格式：YYYY-MM-DD）')
    
    try:
        # 验证日期格式
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        abort(400, '日期格式错误，请使用 YYYY-MM-DD 格式')
    
    # 查找文件
    file_path, filename = find_latest_csv_file(date_str, '10日内涨停')
    if not file_path:
        abort(404, f'未找到 {date_str} 的10日内涨停CSV文件')
    
    # 发送文件
    return send_file(
        file_path,
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )


@app.route('/api/csv/near-limit/download')
def api_csv_near_limit_download():
    """API：下载10日内接近涨停CSV文件"""
    from flask import send_file, abort
    date_str = request.args.get('date')
    if not date_str:
        abort(400, '请提供日期参数（格式：YYYY-MM-DD）')
    
    try:
        # 验证日期格式
        datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        abort(400, '日期格式错误，请使用 YYYY-MM-DD 格式')
    
    # 查找文件
    file_path, filename = find_latest_csv_file(date_str, '10日内接近涨停')
    if not file_path:
        abort(404, f'未找到 {date_str} 的10日内接近涨停CSV文件')
    
    # 发送文件
    return send_file(
        file_path,
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )


@app.route('/api/recent-comparison')
def api_recent_comparison():
    """API：获取最近七个交易日的行业涨停对比数据"""
    try:
        comparison_data = get_recent_comparison_data()
        
        return jsonify({
            'success': True,
            'comparison_data': comparison_data,
            'count': len(comparison_data)
        })
    except Exception as e:
        print(f"获取近期行业对比数据失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取数据失败: {str(e)}'
        }), 500


@app.route('/api/recent-concept-comparison')
def api_recent_concept_comparison():
    """API：获取最近七个交易日的概念涨停对比数据"""
    try:
        comparison_data = get_recent_concept_comparison_data()
        
        return jsonify({
            'success': True,
            'comparison_data': comparison_data,
            'count': len(comparison_data)
        })
    except Exception as e:
        print(f"获取近期概念对比数据失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取数据失败: {str(e)}'
        }), 500


@app.route('/api/recent-sector-comparison')
def api_recent_sector_comparison():
    """API：获取最近七个交易日的板块涨停对比数据"""
    try:
        comparison_data = get_recent_sector_comparison_data()
        
        return jsonify({
            'success': True,
            'comparison_data': comparison_data,
            'count': len(comparison_data)
        })
    except Exception as e:
        print(f"获取近期板块对比数据失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取数据失败: {str(e)}'
        }), 500


@app.route('/api/recent-combined-comparison')
def api_recent_combined_comparison():
    """API：获取最近十一个交易日的综合对比数据（合并行业、概念、板块）"""
    # 记录访问者信息
    try:
        client_ip = get_client_ip(request)
        query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if hasattr(app, 'web_query_logger') and app.web_query_logger:
            ip_location = get_ip_location(client_ip)
            app.web_query_logger.info(
                f"Web查询 - 时间: {query_time}, IP: {client_ip}（{ip_location}）, 接口: /api/recent-combined-comparison"
            )
    except Exception as e:
        print(f"记录访问者信息失败: {e}")
    
    try:
        comparison_data = get_recent_combined_comparison_data()
        
        # 确保返回的数据格式正确
        if comparison_data is None:
            comparison_data = []
        
        return jsonify({
            'success': True,
            'comparison_data': comparison_data,
            'count': len(comparison_data)
        })
    except Exception as e:
        print(f"[API] 获取近期综合对比数据失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取数据失败: {str(e)}'
        }), 500


@app.route('/api/sector-combination')
def api_sector_combination():
    """API：获取热门板块组合数据"""
    # 不记录此接口的访问日志（自动刷新调用太频繁）
    
    try:
        combination_data = get_sector_combination_data()
        
        return jsonify({
            'success': True,
            'top_sectors': combination_data['top_sectors'],
            'combinations': combination_data['combinations']
        })
    except Exception as e:
        print(f"获取板块组合数据失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取数据失败: {str(e)}'
        }), 500


@app.route('/api/all-stocks')
def api_all_stocks():
    """API：获取所有股票信息（用于板块自由组合功能）"""
    # 记录访问者信息
    try:
        client_ip = get_client_ip(request)
        query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if hasattr(app, 'web_query_logger') and app.web_query_logger:
            ip_location = get_ip_location(client_ip)
            app.web_query_logger.info(
                f"Web查询 - 时间: {query_time}, IP: {client_ip}（{ip_location}）, 接口: /api/all-stocks"
            )
    except Exception as e:
        print(f"记录访问者信息失败: {e}")
    
    try:
        all_stocks_info = get_all_stocks_info()
        
        return jsonify({
            'success': True,
            'stocks_data': all_stocks_info,
            'count': len(all_stocks_info)
        })
    except Exception as e:
        print(f"获取所有股票信息失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取数据失败: {str(e)}'
        }), 500


@app.route('/api/sector-history-distribution')
def api_sector_history_distribution():
    """API：获取板块历史分布数据"""
    # 记录访问者信息
    try:
        client_ip = get_client_ip(request)
        query_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if hasattr(app, 'web_query_logger') and app.web_query_logger:
            ip_location = get_ip_location(client_ip)
            app.web_query_logger.info(
                f"Web查询 - 时间: {query_time}, IP: {client_ip}（{ip_location}）, 接口: /api/sector-history-distribution"
            )
    except Exception as e:
        print(f"记录访问者信息失败: {e}")
    
    try:
        search_text = request.args.get('search_text', '').strip()
        start_date = request.args.get('start_date', '').strip()
        
        if not search_text:
            return jsonify({
                'success': False,
                'message': '请提供搜索文本参数'
            }), 400
        
        if not start_date:
            return jsonify({
                'success': False,
                'message': '请提供起始日期参数（格式：YYYY-MM-DD）'
            }), 400
        
        # 验证日期格式
        try:
            datetime.strptime(start_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({
                'success': False,
                'message': '日期格式错误，请使用 YYYY-MM-DD 格式'
            }), 400
        
        # 获取所有股票信息
        stock_info_dict = get_all_stocks_info()
        if not stock_info_dict:
            return jsonify({
                'success': False,
                'message': '无法加载股票信息文件'
            }), 500
        
        # 筛选股票
        matched_stocks = filter_stocks_by_text(stock_info_dict, search_text)
        matched_stocks_count = len(matched_stocks)
        
        if not matched_stocks:
            return jsonify({
                'success': True,
                'matched_stocks_count': 0,
                'distribution_data': []
            })
        
        # 加载涨停板数据
        current_dir = os.path.dirname(os.path.abspath(__file__))
        history_data_dir = os.path.join(current_dir, 'history_data')
        
        if not os.path.exists(history_data_dir):
            return jsonify({
                'success': False,
                'message': f'history_data目录不存在: {history_data_dir}'
            }), 500
        
        limit_up_data = load_limit_up_data_from_files(history_data_dir, start_date)
        
        if not limit_up_data:
            return jsonify({
                'success': True,
                'matched_stocks_count': matched_stocks_count,
                'distribution_data': []
            })
        
        # 生成分布表
        distribution_data = generate_sector_distribution_table(matched_stocks, limit_up_data)
        
        return jsonify({
            'success': True,
            'matched_stocks_count': matched_stocks_count,
            'distribution_data': distribution_data
        })
        
    except Exception as e:
        print(f"[API] 获取板块历史分布数据失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'获取数据失败: {str(e)}'
        }), 500


@app.route('/api/sector-history-distribution/export')
def api_sector_history_distribution_export():
    """API：导出板块历史分布CSV文件"""
    from flask import send_file, make_response
    import io
    
    try:
        search_text = request.args.get('search_text', '').strip()
        start_date = request.args.get('start_date', '').strip()
        
        if not search_text or not start_date:
            return jsonify({
                'success': False,
                'message': '请提供搜索文本和起始日期参数'
            }), 400
        
        # 验证日期格式
        try:
            datetime.strptime(start_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({
                'success': False,
                'message': '日期格式错误，请使用 YYYY-MM-DD 格式'
            }), 400
        
        # 获取所有股票信息
        stock_info_dict = get_all_stocks_info()
        if not stock_info_dict:
            return jsonify({
                'success': False,
                'message': '无法加载股票信息文件'
            }), 500
        
        # 筛选股票
        matched_stocks = filter_stocks_by_text(stock_info_dict, search_text)
        
        if not matched_stocks:
            return jsonify({
                'success': False,
                'message': '未找到匹配的股票'
            }), 404
        
        # 加载涨停板数据
        current_dir = os.path.dirname(os.path.abspath(__file__))
        history_data_dir = os.path.join(current_dir, 'history_data')
        
        if not os.path.exists(history_data_dir):
            return jsonify({
                'success': False,
                'message': f'history_data目录不存在: {history_data_dir}'
            }), 500
        
        limit_up_data = load_limit_up_data_from_files(history_data_dir, start_date)
        
        # 生成分布表
        distribution_data = generate_sector_distribution_table(matched_stocks, limit_up_data)
        
        # 转换为DataFrame并生成CSV
        df = pd.DataFrame(distribution_data)
        
        # 创建CSV内容
        output = io.StringIO()
        df.to_csv(output, index=False, encoding='utf-8-sig')
        output.seek(0)
        
        # 创建响应
        response = make_response(output.getvalue().encode('utf-8-sig'))
        response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
        
        # 使用RFC 2231编码处理中文文件名
        filename = f"板块历史分布_{search_text}_{start_date}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        encoded_filename = quote(filename.encode('utf-8'))
        response.headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{encoded_filename}"
        
        return response
        
    except Exception as e:
        print(f"[API] 导出板块历史分布CSV失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'导出失败: {str(e)}'
        }), 500


@app.route('/api/sector-history-distribution/export-stocks')
def api_sector_history_distribution_export_stocks():
    """API：导出板块历史分布的所有股票（按涨停次数排序）"""
    from flask import send_file, make_response
    import io
    
    try:
        search_text = request.args.get('search_text', '').strip()
        start_date = request.args.get('start_date', '').strip()
        
        if not search_text or not start_date:
            return jsonify({
                'success': False,
                'message': '请提供搜索文本和起始日期参数'
            }), 400
        
        # 验证日期格式
        try:
            datetime.strptime(start_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({
                'success': False,
                'message': '日期格式错误，请使用 YYYY-MM-DD 格式'
            }), 400
        
        # 获取所有股票信息
        stock_info_dict = get_all_stocks_info()
        if not stock_info_dict:
            return jsonify({
                'success': False,
                'message': '无法加载股票信息文件'
            }), 500
        
        # 筛选股票
        matched_stocks = filter_stocks_by_text(stock_info_dict, search_text)
        
        if not matched_stocks:
            return jsonify({
                'success': False,
                'message': '未找到匹配的股票'
            }), 404
        
        # 加载涨停板数据
        current_dir = os.path.dirname(os.path.abspath(__file__))
        history_data_dir = os.path.join(current_dir, 'history_data')
        
        if not os.path.exists(history_data_dir):
            return jsonify({
                'success': False,
                'message': f'history_data目录不存在: {history_data_dir}'
            }), 500
        
        limit_up_data = load_limit_up_data_from_files(history_data_dir, start_date)
        
        # 统计每只股票的涨停次数
        stock_limit_up_count = {}
        for date_str, limit_up_codes in limit_up_data.items():
            for code in limit_up_codes:
                stock_limit_up_count[code] = stock_limit_up_count.get(code, 0) + 1
        
        # 构建股票列表，包含涨停次数
        stock_list = []
        for code, name in matched_stocks:
            count = stock_limit_up_count.get(code, 0)
            stock_list.append({
                '股票代码': code,
                '股票名称': name,
                '涨停次数': count
            })
        
        # 按涨停次数降序排列
        stock_list.sort(key=lambda x: x['涨停次数'], reverse=True)
        
        # 转换为DataFrame并生成CSV
        df = pd.DataFrame(stock_list)
        
        # 创建CSV内容
        output = io.StringIO()
        df.to_csv(output, index=False, encoding='utf-8-sig')
        output.seek(0)
        
        # 创建响应
        response = make_response(output.getvalue().encode('utf-8-sig'))
        response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
        
        # 使用RFC 2231编码处理中文文件名
        filename = f"所有股票_按涨停次数排序_{search_text}_{start_date}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        encoded_filename = quote(filename.encode('utf-8'))
        response.headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{encoded_filename}"
        
        return response
        
    except Exception as e:
        print(f"[API] 导出板块历史分布股票CSV失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'导出失败: {str(e)}'
        }), 500


if __name__ == '__main__':
    main()

