#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
涨停板监控Web应用
实时显示涨停板股票和行业统计
"""

import os
import sys
import json
import re
from datetime import datetime, time as dt_time, date
from flask import Flask, render_template_string, jsonify, request
import pandas as pd
import threading
import time

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils.limit_up_day_path import (  # noqa: E402
    ensure_limit_up_day_data_dir,
    limit_up_day_json_path,
    list_limit_up_day_dates,
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

# 全局数据缓存
_data_cache = {
    'limit_up_stocks': [],
    'plate_stats': [],
    'concept_stats': [],
    'sector_plate_stats': [],
    'last_update_time': None,
    'is_trading_time': False,
    'lock': threading.Lock()
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
    <title>涨停板监控</title>
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
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            transition: all 0.3s ease;
        }
        
        .plate-card:hover {
            border-color: #667eea;
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.2);
        }
        
        .plate-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            padding-bottom: 15px;
            border-bottom: 2px solid #f0f0f0;
            flex-wrap: wrap;
            gap: 10px;
        }
        
        .plate-title {
            font-size: 22px;
            font-weight: bold;
            color: #333;
            flex: 1;
            min-width: 0;
            word-wrap: break-word;
            word-break: keep-all;
            overflow-wrap: break-word;
            white-space: normal;
        }
        
        .plate-count-badge {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 16px;
            font-weight: bold;
            flex-shrink: 0;
            white-space: nowrap;
        }
        
        .plate-stocks {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        
        .stock-item {
            background: #f5f7fa;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 14px;
            border-left: 3px solid #667eea;
        }
        
        .stock-item-code {
            font-family: 'Courier New', monospace;
            font-weight: bold;
            color: #667eea;
            margin-right: 6px;
        }
        
        .stock-item-name {
            color: #333;
        }
        
        /* 单只涨停行业合并显示区域 */
        .single-stock-section {
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        
        .single-stock-section-title {
            font-size: 18px;
            font-weight: bold;
            color: #333;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #f0f0f0;
        }
        
        .single-stock-items {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        
        .single-stock-item {
            background: #f5f7fa;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 14px;
            border-left: 3px solid #90caf9;
        }
        
        .single-stock-item .industry-name {
            color: #666;
            font-size: 12px;
            margin-right: 8px;
        }
        
        .single-stock-item .stock-code {
            font-family: 'Courier New', monospace;
            font-weight: bold;
            color: #667eea;
            margin-right: 6px;
        }
        
        .single-stock-item .stock-name {
            color: #333;
        }
        
        .comparison-table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
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
        }
        
        .comparison-table th:last-child {
            border-right: none;
        }
        
        .comparison-table td {
            padding: 12px 15px;
            text-align: left;
            border-right: 1px solid #f0f0f0;
            vertical-align: top;
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
            line-height: 1.6;
        }
        
        .comparison-table .industry-item:last-child {
            border-bottom: none;
        }
        
        .comparison-table .industry-item-name {
            color: #333;
            font-weight: 500;
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
        
        /* 已下线的书签页：不展示（保留 DOM 以免后端/脚本引用报错） */
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
            
            .history-controls label {
                font-size: 14px;
            }
            
            .history-controls input[type="date"] {
                width: 100%;
                padding: 10px;
                font-size: 16px; /* 防止iOS缩放 */
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
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 涨停板监控</h1>
            <div class="status">
                <span id="update-time">加载中...</span>
                <span id="trading-status" class="badge badge-closed">非交易时间</span>
            </div>
        </div>
        
        <div class="content">
            <div class="tabs">
                <button class="tab active" onclick="switchTab('realtime')">实时数据</button>
                <button class="tab" onclick="switchTab('history')">历史数据</button>
                <button class="tab" onclick="switchTab('recent-comparison')">近期行业对比</button>
                <button class="tab" onclick="switchTab('recent-concept-comparison')">近期概念对比</button>
                <button class="tab" onclick="switchTab('recent-sector-comparison')">近期板块对比</button>
            </div>
            
            <!-- 实时数据区域 -->
            <div id="realtime-content">
                <div class="stats-summary">
                    <div class="stat-card">
                        <div class="label">涨停股票数</div>
                        <div class="value" id="total-stocks">-</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">涉及行业数</div>
                        <div class="value" id="total-plates">-</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">最多涨停行业</div>
                        <div class="value" id="top-plate">-</div>
                    </div>
                </div>
                
                <div class="section">
                    <div class="ranking-tabs">
                        <button class="ranking-tab active" onclick="switchRankingTab('industry')">🏆 涨停板行业排名</button>
                        <button class="ranking-tab" onclick="switchRankingTab('concept')">🏆 涨停板概念排名</button>
                    </div>
                    <div id="industry-stats" class="ranking-content">
                        <div id="plate-stats">
                            <div class="loading">加载中...</div>
                        </div>
                    </div>
                    <div id="concept-stats" class="ranking-content hidden">
                        <div id="concept-stats-content">
                            <div class="loading">加载中...</div>
                        </div>
                    </div>
                </div>
                
                <div class="refresh-info">
                    数据每1分钟自动刷新 | 交易时间（9:25-15:00）实时更新，其他时间显示最后数据
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
                        <div class="label">涉及行业数</div>
                        <div class="value" id="history-total-plates">-</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">最多涨停行业</div>
                        <div class="value" id="history-top-plate">-</div>
                    </div>
                </div>
                
                <div class="section">
                    <div class="ranking-tabs">
                        <button class="ranking-tab active" onclick="switchHistoryRankingTab('industry')">🏆 涨停板行业排名</button>
                        <button class="ranking-tab" onclick="switchHistoryRankingTab('concept')">🏆 涨停板概念排名</button>
                        <button class="ranking-tab" onclick="switchHistoryRankingTab('sector')">🏆 涨停板板块排名</button>
                    </div>
                    
                    <div id="history-industry-content" class="ranking-content">
                        <div id="history-plate-stats">
                            <div class="loading">请选择日期并点击查询</div>
                        </div>
                    </div>
                    
                    <div id="history-concept-content" class="ranking-content hidden">
                        <div id="history-concept-stats">
                            <div class="loading">请选择日期并点击查询</div>
                        </div>
                    </div>
                    
                    <div id="history-sector-content" class="ranking-content hidden">
                        <div id="history-sector-stats">
                            <div class="loading">请选择日期并点击查询</div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- 近期行业对比区域 -->
            <div id="recent-comparison-content" class="hidden">
                <div class="section">
                    <div class="section-title">📊 最近七个交易日行业涨停对比（仅显示每日数量大于等于2的行业）</div>
                    <div id="recent-comparison-stats">
                        <div class="loading">加载中...</div>
                    </div>
                </div>
            </div>
            
            <!-- 近期概念对比区域 -->
            <div id="recent-concept-comparison-content" class="hidden">
                <div class="section">
                    <div class="section-title">📊 最近七个交易日概念涨停对比（仅显示每日数量大于等于2的概念）</div>
                    <div id="recent-concept-comparison-stats">
                        <div class="loading">加载中...</div>
                    </div>
                </div>
            </div>
            
            <!-- 近期板块对比区域 -->
            <div id="recent-sector-comparison-content" class="hidden">
                <div class="section">
                    <div class="section-title">📊 最近七个交易日板块涨停对比（仅显示每日数量大于等于2的板块）</div>
                    <div id="recent-sector-comparison-stats">
                        <div class="loading">加载中...</div>
                    </div>
                </div>
            </div>
            
            <!-- 15日内涨停CSV数据区域（书签已隐藏，面板保留） -->
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
                    <div class="section-title">📊 15日内涨停新高不高于涨停比例50%</div>
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
        
        function renderPlateStats(data, targetElementId = 'plate-stats') {
            const targetElement = document.getElementById(targetElementId);
            if (!targetElement) return;
            
            if (!data || data.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据</div>';
                return;
            }
            
            // 分离数量>=2的行业和数量=1的行业
            const multiStockPlates = [];  // 数量>=2的行业
            const singleStockPlates = []; // 数量=1的行业
            
            data.forEach((plate) => {
                if (plate.count >= 2) {
                    multiStockPlates.push(plate);
                } else {
                    singleStockPlates.push(plate);
                }
            });
            
            let html = '';
            let cardIndex = 0;
            
            // 先显示数量>=2的行业（卡片样式）
            multiStockPlates.forEach((plate) => {
                cardIndex++;
                // 解析股票列表（格式：代码 名称）
                const stockItems = plate.stocks.map(stockStr => {
                    const parts = stockStr.split(' ');
                    const code = parts[0] || '';
                    const name = parts.slice(1).join(' ') || '';
                    return { code, name };
                });
                
                html += `
                    <div class="plate-card">
                        <div class="plate-header">
                            <div class="plate-title">${cardIndex}. ${plate.name}</div>
                            <div class="plate-count-badge">${plate.count} 只涨停</div>
                        </div>
                        <div class="plate-stocks">
                            ${stockItems.map(stock => {
                                // 格式化股票代码为6位，不足6位前面补0
                                const formattedCode = String(stock.code || '').padStart(6, '0');
                                return `
                                <div class="stock-item">
                                    <span class="stock-item-code">${formattedCode}</span>
                                    <span class="stock-item-name">${stock.name}</span>
                                </div>
                            `;
                            }).join('')}
                        </div>
                    </div>
                `;
            });
            
            // 再显示数量=1的行业（合并显示）
            if (singleStockPlates.length > 0) {
                html += `
                    <div class="single-stock-section">
                        <div class="single-stock-section-title">单只涨停行业（${singleStockPlates.length}个行业）</div>
                        <div class="single-stock-items">
                `;
                
                singleStockPlates.forEach((plate) => {
                    // 解析股票列表（格式：代码 名称）
                    const stockItems = plate.stocks.map(stockStr => {
                        const parts = stockStr.split(' ');
                        const code = parts[0] || '';
                        const name = parts.slice(1).join(' ') || '';
                        return { code, name };
                    });
                    
                    stockItems.forEach(stock => {
                        // 格式化股票代码为6位，不足6位前面补0
                        const formattedCode = String(stock.code || '').padStart(6, '0');
                        html += `
                            <div class="single-stock-item">
                                <span class="industry-name">${plate.name}</span>
                                <span class="stock-code">${formattedCode}</span>
                                <span class="stock-name">${stock.name}</span>
                            </div>
                        `;
                    });
                });
                
                html += `
                        </div>
                    </div>
                `;
            }
            
            targetElement.innerHTML = html;
        }
        
        function renderConceptStats(data, targetElementId = 'concept-stats-content') {
            const targetElement = document.getElementById(targetElementId);
            if (!targetElement) return;
            
            if (!data || data.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据（只显示拥有2只或以上股票的概念）</div>';
                return;
            }
            
            let html = '';
            let cardIndex = 0;
            
            // 显示所有概念（已经过滤了数量>=2的）
            data.forEach((concept) => {
                cardIndex++;
                // 解析股票列表（格式：代码 名称）
                const stockItems = concept.stocks.map(stockStr => {
                    const parts = stockStr.split(' ');
                    const code = parts[0] || '';
                    const name = parts.slice(1).join(' ') || '';
                    return { code, name };
                });
                
                html += `
                    <div class="plate-card">
                        <div class="plate-header">
                            <div class="plate-title">${cardIndex}. ${concept.name}</div>
                            <div class="plate-count-badge">${concept.count} 只涨停</div>
                        </div>
                        <div class="plate-stocks">
                            ${stockItems.map(stock => {
                                // 格式化股票代码为6位，不足6位前面补0
                                const formattedCode = String(stock.code || '').padStart(6, '0');
                                return `
                                <div class="stock-item">
                                    <span class="stock-item-code">${formattedCode}</span>
                                    <span class="stock-item-name">${stock.name}</span>
                                </div>
                            `;
                            }).join('')}
                        </div>
                    </div>
                `;
            });
            
            targetElement.innerHTML = html;
        }
        
        function renderSectorPlateStats(data, targetElementId = 'sector-stats-content') {
            const targetElement = document.getElementById(targetElementId);
            if (!targetElement) return;
            
            if (!data || data.length === 0) {
                targetElement.innerHTML = '<div class="loading">暂无数据（只显示拥有2只或以上股票的板块）</div>';
                return;
            }
            
            let html = '';
            let cardIndex = 0;
            
            // 显示所有板块（已经过滤了数量>=2的）
            data.forEach((plate) => {
                cardIndex++;
                // 解析股票列表（格式：代码 名称）
                const stockItems = plate.stocks.map(stockStr => {
                    const parts = stockStr.split(' ');
                    const code = parts[0] || '';
                    const name = parts.slice(1).join(' ') || '';
                    return { code, name };
                });
                
                html += `
                    <div class="plate-card">
                        <div class="plate-header">
                            <div class="plate-title">${cardIndex}. ${plate.name}</div>
                            <div class="plate-count-badge">${plate.count} 只涨停</div>
                        </div>
                        <div class="plate-stocks">
                            ${stockItems.map(stock => {
                                // 格式化股票代码为6位，不足6位前面补0
                                const formattedCode = String(stock.code || '').padStart(6, '0');
                                return `
                                <div class="stock-item">
                                    <span class="stock-item-code">${formattedCode}</span>
                                    <span class="stock-item-name">${stock.name}</span>
                                </div>
                            `;
                            }).join('')}
                        </div>
                    </div>
                `;
            });
            
            targetElement.innerHTML = html;
        }
        
        function switchRankingTab(tab) {
            // 更新书签样式
            document.querySelectorAll('.ranking-tab').forEach(t => {
                t.classList.remove('active');
            });
            
            // 隐藏所有内容
            document.getElementById('industry-stats').classList.add('hidden');
            document.getElementById('concept-stats').classList.add('hidden');
            
            // 显示选中的内容
            if (tab === 'industry') {
                document.querySelectorAll('.ranking-tab')[0].classList.add('active');
                document.getElementById('industry-stats').classList.remove('hidden');
            } else if (tab === 'concept') {
                document.querySelectorAll('.ranking-tab')[1].classList.add('active');
                document.getElementById('concept-stats').classList.remove('hidden');
            }
        }
        
        function switchTab(tab) {
            // 更新标签页样式
            document.querySelectorAll('.tab').forEach(t => {
                t.classList.remove('active');
                if ((tab === 'realtime' && t.textContent === '实时数据') || 
                    (tab === 'history' && t.textContent === '历史数据') ||
                    (tab === 'recent-comparison' && t.textContent === '近期行业对比') ||
                    (tab === 'recent-concept-comparison' && t.textContent === '近期概念对比') ||
                    (tab === 'recent-sector-comparison' && t.textContent === '近期板块对比')) {
                    t.classList.add('active');
                }
            });
            
            // 显示/隐藏内容区域
            document.getElementById('realtime-content').classList.add('hidden');
            document.getElementById('history-content').classList.add('hidden');
            document.getElementById('recent-comparison-content').classList.add('hidden');
            document.getElementById('recent-concept-comparison-content').classList.add('hidden');
            document.getElementById('recent-sector-comparison-content').classList.add('hidden');
            document.getElementById('limit-up-csv-content').classList.add('hidden');
            document.getElementById('near-limit-csv-content').classList.add('hidden');
            
            if (tab === 'realtime') {
                document.getElementById('realtime-content').classList.remove('hidden');
            } else if (tab === 'history') {
                document.getElementById('history-content').classList.remove('hidden');
                // 切换到历史数据时，设置默认日期为今天
                const today = new Date().toISOString().split('T')[0];
                document.getElementById('history-date').value = today;
            } else if (tab === 'recent-comparison') {
                document.getElementById('recent-comparison-content').classList.remove('hidden');
                // 切换到近期行业对比时，自动加载数据
                loadRecentComparison();
            } else if (tab === 'recent-concept-comparison') {
                document.getElementById('recent-concept-comparison-content').classList.remove('hidden');
                // 切换到近期概念对比时，自动加载数据
                loadRecentConceptComparison();
            } else if (tab === 'recent-sector-comparison') {
                document.getElementById('recent-sector-comparison-content').classList.remove('hidden');
                // 切换到近期板块对比时，自动加载数据
                loadRecentSectorComparison();
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
            document.getElementById('history-plate-stats').innerHTML = '<div class="loading">加载中...</div>';
            document.getElementById('history-concept-stats').innerHTML = '<div class="loading">加载中...</div>';
            document.getElementById('history-sector-stats').innerHTML = '<div class="loading">加载中...</div>';
            document.getElementById('history-info').classList.add('hidden');
            
            // 查询历史数据
            fetch(`/api/history?date=${dateStr}`)
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // 更新统计信息
                        document.getElementById('history-total-stocks').textContent = data.total_stocks || 0;
                        document.getElementById('history-total-plates').textContent = data.total_industries || 0;
                        document.getElementById('history-top-plate').textContent = 
                            data.plate_stats && data.plate_stats.length > 0 ? data.plate_stats[0].name : '-';
                        
                        // 显示行业排名
                        renderPlateStats(data.plate_stats || [], 'history-plate-stats');
                        
                        // 显示概念排名
                        renderConceptStats(data.concept_stats || [], 'history-concept-stats');
                        
                        // 显示板块排名
                        renderSectorPlateStats(data.sector_plate_stats || [], 'history-sector-stats');
                        
                        // 显示日期信息
                        const infoEl = document.getElementById('history-info');
                        const dateObj = new Date(data.timestamp * 1000);
                        infoEl.textContent = `日期：${data.date} | 数据时间：${dateObj.toLocaleString('zh-CN')}`;
                        infoEl.classList.remove('hidden');
                    } else {
                        document.getElementById('history-plate-stats').innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                        document.getElementById('history-concept-stats').innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                        document.getElementById('history-sector-stats').innerHTML = 
                            `<div class="loading">${data.message || '加载失败'}</div>`;
                        document.getElementById('history-info').classList.add('hidden');
                    }
                })
                .catch(error => {
                    console.error('加载历史数据失败:', error);
                    document.getElementById('history-plate-stats').innerHTML = 
                        '<div class="loading">加载失败，请稍后重试</div>';
                    document.getElementById('history-concept-stats').innerHTML = 
                        '<div class="loading">加载失败，请稍后重试</div>';
                    document.getElementById('history-sector-stats').innerHTML = 
                        '<div class="loading">加载失败，请稍后重试</div>';
                    document.getElementById('history-info').classList.add('hidden');
                });
        }
        
        function switchHistoryRankingTab(tab) {
            // 隐藏所有内容
            document.querySelectorAll('#history-industry-content, #history-concept-content, #history-sector-content').forEach(el => {
                el.classList.add('hidden');
            });
            
            // 移除历史数据区域的所有活动状态
            document.querySelectorAll('#history-content .ranking-tab').forEach(t => {
                t.classList.remove('active');
            });
            
            // 显示选中的内容
            if (tab === 'industry') {
                document.getElementById('history-industry-content').classList.remove('hidden');
                // 找到历史数据区域的第一个书签按钮
                const tabs = document.querySelectorAll('#history-content .ranking-tab');
                if (tabs.length > 0) tabs[0].classList.add('active');
            } else if (tab === 'concept') {
                document.getElementById('history-concept-content').classList.remove('hidden');
                const tabs = document.querySelectorAll('#history-content .ranking-tab');
                if (tabs.length > 1) tabs[1].classList.add('active');
            } else if (tab === 'sector') {
                document.getElementById('history-sector-content').classList.remove('hidden');
                const tabs = document.querySelectorAll('#history-content .ranking-tab');
                if (tabs.length > 2) tabs[2].classList.add('active');
            }
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
            fetch(`/api/csv/limit-up?date=${dateStr}`)
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
            fetch(`/api/csv/near-limit?date=${dateStr}`)
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
            fetch('/api/recent-comparison')
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
            fetch('/api/recent-concept-comparison')
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
            fetch('/api/recent-sector-comparison')
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
        
        function updateData() {
            // 防止并发请求：如果正在更新，则跳过
            if (isUpdating) {
                console.log('数据更新中，跳过本次请求');
                return;
            }
            
            isUpdating = true;
            fetch('/api/data')
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('update-time').textContent = 
                            '最后更新: ' + formatTime(data.last_update_time);
                        
                        updateTradingStatus(data.is_trading_time);
                        
                        document.getElementById('total-stocks').textContent = data.total_stocks || 0;
                        document.getElementById('total-plates').textContent = data.total_plates || 0;
                        document.getElementById('top-plate').textContent = 
                            data.top_plate || '-';
                        
                        renderPlateStats(data.plate_stats);
                        if (data.concept_stats) {
                            renderConceptStats(data.concept_stats);
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
        
        // 注意：自动刷新由updateTradingStatus函数根据交易时间动态控制
        // 交易时间：每1分钟自动刷新
        // 非交易时间：停止自动刷新，节省资源
    </script>
</body>
</html>
"""


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
    """获取涨停板股票列表（从get_limit_up_dongcai模块获取）"""
    try:
        print("正在使用Selenium从东方财富网获取涨停板数据...")
        limit_up_df = get_limit_up_stocks_selenium()
        
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
    # 需要排除的常见概念
    excluded_concepts = {
        '央国企改革', '融资融券'
    }
    
    concept_count = {}
    
    for stock in stocks:
        # 统计概念题材
        concepts = stock.get('concepts', [])
        if concepts and isinstance(concepts, list):
            for concept in concepts:
                if concept and str(concept).strip():
                    concept_name = str(concept).strip()
                    # 排除常见概念
                    if concept_name in excluded_concepts:
                        continue
                    if concept_name not in concept_count:
                        concept_count[concept_name] = {
                            'name': concept_name,
                            'count': 0,
                            'stocks': []
                        }
                    concept_count[concept_name]['count'] += 1
                    # 确保股票代码格式化为6位
                    code = str(stock.get('code', '')).zfill(6)
                    stock_str = f"{code} {stock['name']}"
                    if stock_str not in concept_count[concept_name]['stocks']:
                        concept_count[concept_name]['stocks'].append(stock_str)
    
    # 转换为列表并按涨停数量排序，只保留数量>=2的概念
    concept_stats = [item for item in concept_count.values() if item['count'] >= 2]
    concept_stats.sort(key=lambda x: x['count'], reverse=True)
    
    return concept_stats


def calculate_sector_plate_stats(stocks):
    """计算板块统计"""
    # 需要排除的常见板块
    excluded_sectors = {
        '央国企改革', '融资融券', '深股通', '沪股通', 
        '机构重仓', 'QFII重仓', '专精特新', '标准普尔', '富时罗素'
    }
    
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


def save_daily_data(stocks, plate_stats, concept_stats=None, sector_plate_stats=None):
    """保存每日数据到文件（每次刷新都保存，非交易日不保存）"""
    try:
        # 非交易日不保存
        if not is_trading_day():
            print("非交易日，跳过数据保存")
            return
        
        today = date.today()
        today_str = today.strftime('%Y-%m-%d')
        csv_filename = os.path.join(HISTORY_DATA_DIR, f'涨停板数据_{today_str}.csv')

        # 本次抓取为空时：不覆盖已有非空 CSV/JSON（避免写出空占位导致下游解析失败）
        if not stocks:
            if os.path.isfile(csv_filename) and os.path.getsize(csv_filename) > 32:
                print(
                    f"跳过保存：本次未获取到涨停数据，保留已有非空 CSV: {csv_filename} "
                    f"(size={os.path.getsize(csv_filename)})"
                )
                return
            print("警告：当前没有涨停股票数据，且无已有非空 CSV，将写入空表头占位")
        
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
            
            # 使用pandas保存为CSV
            df = pd.DataFrame(csv_data)
            if df.empty:
                df = pd.DataFrame(columns=['代码', '名称', '最新价', '涨跌幅(%)', '所属行业'])
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
            
            # 需要排除的常见概念
            excluded_concepts = {
                '央国企改革', '融资融券'
            }
            
            # 只保留每日数量大于等于2的概念，并排除常见概念
            filtered_concepts = []
            for concept in concept_stats:
                concept_name = concept.get('name', '')
                # 排除常见概念
                if concept_name in excluded_concepts:
                    continue
                if concept.get('count', 0) >= 2:
                    filtered_concepts.append({
                        'name': concept_name,
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
            
            # 需要排除的常见板块
            excluded_sectors = {
                '央国企改革', '融资融券', '深股通', '沪股通', 
                '机构重仓', 'QFII重仓', '专精特新', '标准普尔', '富时罗素'
            }
            
            # 只保留每日数量大于等于2的板块，并排除常见板块
            filtered_sectors = []
            for sector in sector_plate_stats:
                sector_name = sector.get('name', '')
                # 排除常见板块
                if sector_name in excluded_sectors:
                    continue
                # 只保留数量大于等于2的板块
                if sector.get('count', 0) >= 2:
                    filtered_sectors.append({
                        'name': sector_name,
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


def update_data():
    """更新数据"""
    global _data_cache
    
    with _data_cache['lock']:
        try:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始更新数据...")
            
            # 获取涨停板股票
            stocks = get_limit_up_stocks()
            print(f"获取到 {len(stocks)} 只涨停股票")
            
            if not stocks:
                print("未获取到涨停股票，使用空数据")
                _data_cache['limit_up_stocks'] = []
                _data_cache['plate_stats'] = []
                _data_cache['concept_stats'] = []
                _data_cache['sector_plate_stats'] = []
                _data_cache['last_update_time'] = int(time.time())
                _data_cache['is_trading_time'] = is_trading_time()
                # 即使没有数据，也尝试保存（每次刷新都保存，非交易日不保存）
                save_daily_data([], [], [], [])
                return
            
            # 计算行业统计
            plate_stats = calculate_plate_stats(stocks)
            
            # 计算概念题材统计
            concept_stats = calculate_concept_stats(stocks)
            
            # 计算板块统计
            sector_plate_stats = calculate_sector_plate_stats(stocks)
            
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
            _data_cache['last_update_time'] = int(time.time())
            _data_cache['is_trading_time'] = is_trading_time()
            
            # 保存每日数据（每次刷新都保存，非交易日不保存）
            save_daily_data(stocks, plate_stats, concept_stats, sector_plate_stats)
            
            print(f"数据更新完成，涨停股票数: {len(stocks)}, 行业数: {len(plate_stats)}, 概念数: {len(concept_stats)}, 板块数: {len(sector_plate_stats)}")
            if plate_stats:
                print(f"行业统计示例（前3个）: {[p['name'] + ':' + str(p['count']) for p in plate_stats[:3]]}")
            if concept_stats:
                print(f"概念统计示例（前3个）: {[c['name'] + ':' + str(c['count']) for c in concept_stats[:3]]}")
            if sector_plate_stats:
                print(f"板块统计示例（前3个）: {[s['name'] + ':' + str(s['count']) for s in sector_plate_stats[:3]]}")
            
        except Exception as e:
            print(f"更新数据失败: {e}")
            import traceback
            traceback.print_exc()


def background_update_thread():
    """后台更新线程"""
    while True:
        try:
            # 如果是交易时间，每30秒更新一次
            # 如果不是交易时间，每小时更新一次，但需要频繁检查是否进入交易时间
            if is_trading_time():
                update_data()
                time.sleep(30)  # 交易时间每30秒更新
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
                    update_data()
                
                # 非交易时间：根据时间智能调整sleep时间
                # 如果即将进入交易时间（9:20-9:25），使用更短的sleep时间以便及时检测
                # 其他时间每1分钟检查一次
                sleep_time = get_sleep_time()
                time.sleep(sleep_time)
        except Exception as e:
            print(f"后台更新线程错误: {e}")
            time.sleep(60)


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
        total_plates = len(_data_cache['plate_stats'])
        top_plate = _data_cache['plate_stats'][0]['name'] if _data_cache['plate_stats'] else None
        
        return jsonify({
            'success': True,
            'limit_up_stocks': _data_cache['limit_up_stocks'],
            'plate_stats': _data_cache.get('plate_stats', []),
            'concept_stats': _data_cache.get('concept_stats', []),
            'sector_plate_stats': _data_cache.get('sector_plate_stats', []),
            'total_stocks': total_stocks,
            'total_plates': total_plates,
            'top_plate': top_plate,
            'last_update_time': _data_cache['last_update_time'],
            'is_trading_time': is_trading_time()
        })


@app.route('/api/history')
def api_history():
    """API：获取历史数据"""
    date_str = request.args.get('date')
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
    
    # 需要排除的常见板块
    excluded_sectors = {
        '央国企改革', '融资融券', '深股通', '沪股通', 
        '机构重仓', 'QFII重仓', '专精特新', '标准普尔', '富时罗素'
    }
    
    # 需要排除的常见概念
    excluded_concepts = {
        '央国企改革', '融资融券'
    }
    
    # 过滤板块统计数据，排除常见板块
    sector_plate_stats = history_data.get('sector_plate_stats', [])
    filtered_sector_plate_stats = [
        item for item in sector_plate_stats 
        if item.get('name', '') not in excluded_sectors
    ]
    
    # 过滤概念统计数据，排除常见概念
    concept_stats = history_data.get('concept_stats', [])
    filtered_concept_stats = [
        item for item in concept_stats 
        if item.get('name', '') not in excluded_concepts
    ]
    
    return jsonify({
        'success': True,
        'date': history_data.get('date'),
        'limit_up_stocks': history_data.get('limit_up_stocks', []),
        'plate_stats': history_data.get('plate_stats', []),
        'concept_stats': filtered_concept_stats,
        'sector_plate_stats': filtered_sector_plate_stats,
        'total_stocks': history_data.get('total_stocks', 0),
        'total_industries': history_data.get('total_industries', 0),
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
        prefix: 文件前缀，如 '15日内涨停' 或 '10日内接近涨停'
    
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
                # 文件名格式：15日内涨停_YYYYMMDD_HHMMSS.csv 或 10日内接近涨停_YYYYMMDD_HHMMSS.csv
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
    """API：获取15日内涨停CSV数据"""
    date_str = request.args.get('date')
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
    file_path, filename = find_latest_csv_file(date_str, '15日内涨停')
    if not file_path:
        return jsonify({
            'success': False,
            'message': f'未找到 {date_str} 的15日内涨停CSV文件'
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
    """API：下载15日内涨停CSV文件"""
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
    file_path, filename = find_latest_csv_file(date_str, '15日内涨停')
    if not file_path:
        abort(404, f'未找到 {date_str} 的15日内涨停CSV文件')
    
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


if __name__ == '__main__':
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
    print("涨停板监控Web应用启动中...")
    print(f"访问地址: http://localhost:{port}")
    if port != 5000:
        print(f"注意：端口5000被占用，已自动切换到端口{port}")
    
    try:
        app.run(host='127.0.0.1', port=port, debug=False)
    except Exception as e:
        print(f"启动失败: {e}")
        import traceback
        traceback.print_exc()

