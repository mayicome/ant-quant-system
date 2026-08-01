import sys
import pandas as pd
from datetime import datetime
from core.backtest_engine import BacktestEngine
from utils.logger import Logger
import json
import os

def analyze_limit_up_periods(stock_code, date):
    """分析涨停到开板的情况"""
    print(f"\n=== 分析 {stock_code} {date} 的涨停开板情况 ===")
    
    # 创建回测引擎
    engine = BacktestEngine(stock_code=stock_code)
    engine.set_logger(Logger())
    
    # 加载历史数据
    success = engine.load_data(date, date)
    if success and engine.data is not None and not engine.data.empty:
        # 计算涨停板状态
        engine.data['is_limit_up'] = (engine.data['askPrice'].apply(lambda x: x[0] == 0)) & (engine.data['askVol'].apply(lambda x: x[0] == 0))
        
        # 设置时间范围
        start_time = f"{date.strftime('%Y%m%d')}093000"
        end_time = f"{date.strftime('%Y%m%d')}145700"
        
        # 筛选时间范围内的数据
        mask = (engine.data.index >= start_time) & (engine.data.index <= end_time)
        period_data = engine.data[mask]
        
        # 找出涨停到开板的变化点
        limit_up_periods = []
        in_limit_up = False
        start_time = None
        prev_bid_vol = None
        prev_volume = None
        withdrawals = []
        up_limit_price = None
        
        for idx, row in period_data.iterrows():
            if row['is_limit_up']:
                if not in_limit_up:
                    # 开始涨停
                    in_limit_up = True
                    start_time = idx
                    withdrawals = []  # 重置撤单记录
                    up_limit_price = row['lastPrice']
                    prev_bid_vol = row['bidVol'][0]
                    prev_volume = row['volume']
                    continue
                
                # 检查撤单情况
                if prev_bid_vol is not None:
                    bid_vol_diff = row['bidVol'][0] - prev_bid_vol + row['volume'] - prev_volume
                    bid_vol_ratio = bid_vol_diff / prev_bid_vol if prev_bid_vol > 0 else 0
                    
                    # 判断是否为明显撤单（撤单金额>100万且撤单比例>50%）
                    if bid_vol_diff < 0:
                        cancel_amount = abs(bid_vol_diff) * row['lastPrice'] * 100
                        if cancel_amount > 200000 and bid_vol_ratio < -0.5:
                            withdrawals.append(idx)
                
                prev_bid_vol = row['bidVol'][0]
                prev_volume = row['volume']
            
            elif in_limit_up:
                # 结束涨停
                in_limit_up = False
                end_time = idx
                
                # 计算涨停时长（秒）
                start_seconds = int(start_time[-6:])
                end_seconds = int(end_time[-6:])
                duration = end_seconds - start_seconds
                
                # 计算每次撤单距离开板的时间
                withdrawal_times = []
                for w_time in withdrawals:
                    w_seconds = int(w_time[-6:])
                    seconds_before_break = end_seconds - w_seconds
                    withdrawal_times.append(str(seconds_before_break))
                
                withdrawal_info = ','.join(withdrawal_times) if withdrawal_times else "没有明显撤单"
                
                limit_up_periods.append({
                    'start_time': start_time,
                    'end_time': end_time,
                    'duration': duration,
                    'withdrawals': withdrawal_info
                })
                
                prev_bid_vol = None
                withdrawals = []
        
        # 打印结果
        print(f"\n涨停到开板次数: {len(limit_up_periods)}")
        for i, period in enumerate(limit_up_periods, 1):
            print(f"\n第{i}次涨停:")
            print(f"开始时间: {period['start_time']}")
            print(f"结束时间: {period['end_time']}")
            print(f"涨停时长: {period['duration']}秒")
            print(f"撤单情况: {period['withdrawals']}")
        
        return limit_up_periods
    return None

def analyze_multiple_stocks(stock_codes, date):
    """分析多个股票的涨停开板情况"""
    all_periods = []  # 将这个移到函数开始，用于收集所有股票的数据
    
    for stock_code in stock_codes:
        # 分析涨停开板情况
        limit_up_periods = analyze_limit_up_periods(stock_code, date)
        if limit_up_periods:
            # 直接将每个周期的数据添加到总列表中
            for period in limit_up_periods:
                all_periods.append({
                    'stock_code': stock_code,
                    'date': date.strftime('%Y-%m-%d'),  # 添加日期列
                    'start_time': period['start_time'],
                    'end_time': period['end_time'],
                    'duration': period['duration'],
                    'withdrawals': period['withdrawals']
                })
    
    # 如果有数据，保存到CSV文件
    if all_periods:
        # 转换为DataFrame
        df_limit_up = pd.DataFrame(all_periods)
        
        # 检查文件是否已存在
        filename = 'limit_up_analysis.csv'
        if os.path.exists(filename):
            # 如果文件存在，读取现有数据并追加新数据
            existing_df = pd.read_csv(filename)
            df_limit_up = pd.concat([existing_df, df_limit_up], ignore_index=True)
        
        # 保存所有数据
        df_limit_up.to_csv(filename, index=False)
        print(f"\n涨停开板结果已保存到 {filename}")
    
    return all_periods

def read_stock_data_from_excel(excel_path):
    """
    Read stock codes and dates from Excel file
    Expected Excel format:
    | 股票代码 | 日期 |
    | 002105 | 2024-05-16 |
    """
    try:
        df = pd.read_excel(excel_path)
        if '股票代码' not in df.columns or '日期' not in df.columns:
            print("Error: Excel file must contain '股票代码' and '日期' columns")
            return None
        
        # Convert date column to datetime and then to date
        df['日期'] = pd.to_datetime(df['日期']).dt.date
        
        # Ensure stock codes are strings with proper formatting
        df['股票代码'] = df['股票代码'].astype(str).str.zfill(6)
        
        # Group by date to get list of stock codes for each date
        grouped_data = df.groupby('日期')['股票代码'].apply(list).to_dict()
        return grouped_data
        
    except Exception as e:
        print(f"Error reading Excel file: {str(e)}")
        return None

if __name__ == "__main__":
    # Read configuration from Excel
    #excel_path = "test.xlsx"
    excel_path = "limit_up_notbroken.xlsx"
    stock_data = read_stock_data_from_excel(excel_path)

    
    if stock_data:
        all_results = []  # 用于收集所有日期的结果
        for date, stock_codes in stock_data.items():
            print(f"\nAnalyzing stocks for date: {date.strftime('%Y-%m-%d')}")
            results = analyze_multiple_stocks(stock_codes, date)
            if results:
                all_results.extend(results)
    else:
        print("Failed to read stock data from Excel. Please check the Excel file format.") 