import sys
import pandas as pd
from datetime import datetime, timedelta
from core.backtest_engine import BacktestEngine
from utils.logger import Logger
import json
import os

def analyze_volume(stock_code, date):
    """分析股票在不同时间段的成交量"""
    print(f"\n=== 分析 {stock_code} {date} 的成交量情况 ===")
    
    # 创建回测引擎
    engine = BacktestEngine(stock_code=stock_code)
    engine.set_logger(Logger())
    
    # 加载历史数据
    success = engine.load_data(date, date)
    if success and engine.data is not None and not engine.data.empty:
        # 计算涨停板状态
        engine.data['is_limit_up'] = (engine.data['askPrice'].apply(lambda x: x[0] == 0)) & (engine.data['askVol'].apply(lambda x: x[0] == 0))
        
        # 找到首次涨停的时间
        first_limit_up_time = None
        for idx, row in engine.data.iterrows():
            if row['is_limit_up']:
                first_limit_up_time = idx
                break
        
        if first_limit_up_time is None:
            print(f"{stock_code} 当天没有涨停")
            return None
            
        # 检查到14:57:00之前是否有开板
        end_check_time = f"{date.strftime('%Y%m%d')}145700"
        
        # 检查是否持续涨停到14:57
        continuous_limit_up = True
        for idx, row in engine.data[first_limit_up_time:end_check_time].iterrows():
            if not row['is_limit_up']:
                continuous_limit_up = False
                print(f"{stock_code} 在14:57:00之前开板，跳过统计")
                return None
        
        # 如果开板，直接返回None，不统计该股票
        if not continuous_limit_up:
            return None
            
        # 继续计算各个时间段的成交量
        first_limit_up_time_dt = datetime.strptime(first_limit_up_time, '%Y%m%d%H%M%S')
        
        # 使用timedelta来计算正确的时间
        one_min_after = (first_limit_up_time_dt + timedelta(minutes=1)).strftime('%Y%m%d%H%M%S')
        five_min_after = (first_limit_up_time_dt + timedelta(minutes=5)).strftime('%Y%m%d%H%M%S')
        fifteen_min_after = (first_limit_up_time_dt + timedelta(minutes=15)).strftime('%Y%m%d%H%M%S')
        
        # 计算这些时间段内的涨停成交量
        volume_1min = 0
        volume_5min = 0
        volume_15min = 0
        prev_volume = None
        
        for idx, row in engine.data[first_limit_up_time:fifteen_min_after].iterrows():
            if prev_volume is None:
                prev_volume = row['volume']
                continue
            
            current_volume = row['volume'] - prev_volume
            if current_volume > 0:
                # 累加到相应的时间段
                if idx <= one_min_after:
                    volume_1min += current_volume
                if idx <= five_min_after:
                    volume_5min += current_volume
                if idx <= fifteen_min_after:
                    volume_15min += current_volume
            
            prev_volume = row['volume']
        
        # 4. 收盘前15分钟（14:45-15:00）
        last_15min_start = f"{date.strftime('%Y%m%d')}144500"
        last_15min_end = f"{date.strftime('%Y%m%d')}150000"
        
        # 使用相同的计算方法
        volume_last_15min = 0
        prev_volume = None
        
        for idx, row in engine.data[last_15min_start:last_15min_end].iterrows():
            if prev_volume is None:
                prev_volume = row['volume']
                continue
            
            current_volume = row['volume'] - prev_volume
            if current_volume > 0:
                volume_last_15min += current_volume
            
            prev_volume = row['volume']
        
        # 5. 涨停期间的总成交量
        limit_up_volume = 0
        prev_volume = None
        in_limit_up = False
        start_volume = None  # 记录每段涨停开始时的成交量
        
        print(f"\n调试信息 - {stock_code}:")
        for idx, row in engine.data.iterrows():
            if row['is_limit_up']:
                if not in_limit_up:
                    # 开始涨停
                    in_limit_up = True
                    start_volume = row['volume']  # 记录开始时的成交量
                    prev_volume = row['volume']
                    print(f"开始涨停: {idx}, volume: {prev_volume}")
                else:
                    # 在涨停期间累计成交量
                    current_volume = row['volume'] - prev_volume
                    limit_up_volume += current_volume
                    print(f"涨停中: {idx}, volume: {row['volume']}, prev: {prev_volume}, diff: {current_volume}, total: {limit_up_volume}")
                    prev_volume = row['volume']
            else:
                if in_limit_up:
                    # 结束涨停，计算最后一段的成交量
                    final_volume = row['volume'] - start_volume
                    if final_volume > 0:  # 如果这段涨停有成交
                        limit_up_volume += final_volume
                    print(f"结束涨停: {idx}, volume: {row['volume']}, total: {limit_up_volume}")
                # 不是涨停状态
                in_limit_up = False
                prev_volume = None
                start_volume = None
        
        result = {
            'stock_code': stock_code,
            'date': date.strftime('%Y-%m-%d'),
            'first_limit_up_time': first_limit_up_time,
            'volume_after_1min': volume_1min,
            'volume_after_5min': volume_5min,
            'volume_after_15min': volume_15min,
            'volume_last_15min': volume_last_15min,
            'limit_up_volume': limit_up_volume
        }
        
        # 打印结果
        print(f"\n首次涨停时间: {first_limit_up_time}")
        print(f"涨停后1分钟成交量: {volume_1min:.0f}")
        print(f"涨停后5分钟成交量: {volume_5min:.0f}")
        print(f"涨停后15分钟成交量: {volume_15min:.0f}")
        print(f"收盘前15分钟成交量: {volume_last_15min:.0f}")
        print(f"涨停期间总成交量: {limit_up_volume:.0f}")
        
        return result
    return None

def analyze_multiple_stocks(stock_codes, date):
    """分析多个股票的成交量情况"""
    all_results = []
    
    for stock_code in stock_codes:
        result = analyze_volume(stock_code, date)
        if result:
            all_results.append(result)
    
    # 如果有数据，保存到CSV文件
    if all_results:
        # 转换为DataFrame
        df_results = pd.DataFrame(all_results)
        
        # 检查文件是否已存在
        filename = 'volume_analysis.csv'
        if os.path.exists(filename):
            # 如果文件存在，读取现有数据并追加新数据
            existing_df = pd.read_csv(filename)
            df_results = pd.concat([existing_df, df_results], ignore_index=True)
        
        # 保存所有数据
        df_results.to_csv(filename, index=False)
        print(f"\n分析结果已保存到 {filename}")
    
    return all_results

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
    excel_path = "limit_up_df.xlsx"
    excel_path = "test.xlsx"
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