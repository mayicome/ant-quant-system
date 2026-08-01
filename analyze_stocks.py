import sys
import pandas as pd
from datetime import datetime
from core.backtest_engine import BacktestEngine
from utils.logger import Logger
import json

def analyze_stock_tick(stock_code, date):
    """分析单个股票的tick数据"""
    print(f"\n=== 分析 {stock_code} {date} 的tick数据 ===")
    
    # 创建回测引擎
    engine = BacktestEngine(stock_code=stock_code)
    engine.set_logger(Logger())
    
    # 加载历史数据
    success = engine.load_data(date, date)  # 只加载指定日期的数据
    if success and engine.data is not None and not engine.data.empty:
        # 打印数据的时间范围
        print(f"数据开始时间: {engine.data.index[0]}")
        print(f"数据结束时间: {engine.data.index[-1]}")
        
        # 计算涨停板状态 - 对每一行进行判断
        engine.data['is_limit_up'] = (engine.data['askPrice'].apply(lambda x: x[0] == 0)) & (engine.data['askVol'].apply(lambda x: x[0] == 0))
        
        # 打印涨停板状态统计
        print(f"涨停板时间点数量: {engine.data['is_limit_up'].sum()}")
        print(f"总时间点数量: {len(engine.data)}")
        
        # 创建5分钟时间段的列表
        time_slots = []
        for hour in range(9, 15):
            for minute in range(0, 60, 5):
                if hour == 9 and minute < 30:  # 跳过9:00-9:30
                    continue
                if hour == 11 and minute >= 30:  # 跳过11:30-13:00
                    continue
                if hour == 12:  # 跳过12:00-13:00
                    continue
                if hour == 14 and minute >= 55:  # 跳过14:55-15:00
                    continue
                
                time_str = f"{hour:02d}:{minute:02d}"
                time_slots.append(time_str)

        # 添加最后一个时间段 14:57-15:00
        time_slots.append("14:57")
        time_slots.append("15:00")
        
        # 创建结果字典，初始化为0
        result_dict = {'股票代码': stock_code,'日期':date.strftime('%Y%m%d')}
        
        # 计算总成交量
        total_volume = 0
        period_volumes = {}  # 存储每个时间段的成交量
        
        # 遍历每个时间段
        for i in range(len(time_slots)-1):
            start_time = time_slots[i]
            end_time = time_slots[i+1]
            time_slot = f"{start_time}-{end_time}"
            
            # 转换时间为tick数据格式
            start_tick = f"{date.strftime('%Y%m%d')}{start_time.replace(':', '')}00"
            end_tick = f"{date.strftime('%Y%m%d')}{end_time.replace(':', '')}00"
            
            # 获取该时间段的数据
            if time_slot == "14:57-15:00":
                # 对最后一个时间段特殊处理，包含结束时间点
                mask = (engine.data.index >= start_tick) & (engine.data.index <= end_tick)
            else:
                # 其他时间段不包含结束时间点
                mask = (engine.data.index >= start_tick) & (engine.data.index < end_tick)
            period_data = engine.data[mask]
            
            if not period_data.empty:
                # 对最后一个时间段特殊处理
                if time_slot == "14:57-15:00":
                    print(f"\n最后一个时间段 {time_slot}:")
                    print(f"数据点数量: {len(period_data)}")
                    print(f"开始时间: {period_data.index[0]}")
                    print(f"结束时间: {period_data.index[-1]}")
                    print(f"开始成交量: {period_data['volume'].iloc[0]}")
                    print(f"结束成交量: {period_data['volume'].iloc[-1]}")
                    
                    volume = period_data['volume'].iloc[-1] - period_data['volume'].iloc[0]
                    period_volumes[time_slot] = volume
                    total_volume += volume
                    print(f"计算得到的成交量: {volume}")
                else:
                    # 检查是否有非涨停板的时间点
                    is_all_limit_up = period_data['is_limit_up'].all()
                    
                    if is_all_limit_up:
                        # 如果全部是涨停板，计算该时间段的交易量
                        volume = period_data['volume'].iloc[-1] - period_data['volume'].iloc[0]
                        period_volumes[time_slot] = volume
                        total_volume += volume
                    else:
                        # 如果有非涨停板的时间点，成交量记为0
                        period_volumes[time_slot] = 0
            else:
                period_volumes[time_slot] = 0
                print(f"\n时间段 {time_slot}: 无数据")
        
        # 计算每个时间段的成交量占比
        for time_slot, volume in period_volumes.items():
            if total_volume > 0:
                result_dict[time_slot] = volume / total_volume
            else:
                result_dict[time_slot] = 0
        
        print(f"总成交量: {total_volume}")
        return result_dict
    return None

def analyze_multiple_stocks(input_file):
    """从Excel文件读取股票列表并分析"""
    try:
        # 读取Excel文件
        df_input = pd.read_excel(input_file)
        
        # 检查必要的列是否存在
        required_columns = ['股票代码', '日期']
        if not all(col in df_input.columns for col in required_columns):
            print(f"错误：Excel文件必须包含以下列：{required_columns}")
            return
        
        # 存储所有股票的数据
        all_data = []
        
        # 遍历每一行
        for _, row in df_input.iterrows():
            # 处理股票代码格式
            stock_code = str(row['股票代码']).strip()
            # 补足6位
            stock_code = stock_code.zfill(6)
            # 添加市场后缀
            stock_code = f"{stock_code}.SH" if stock_code.startswith('6') else f"{stock_code}.SZ"
            
            # 处理日期格式
            date = pd.to_datetime(row['日期']).date()  # 转换为date对象
            
            try:
                # 分析单个股票
                result = analyze_stock_tick(stock_code, date)
                if result is not None:
                    all_data.append(result)
                    print(f"完成分析: {stock_code} {date.strftime('%Y%m%d')}")
            except Exception as e:
                print(f"分析 {stock_code} {date.strftime('%Y%m%d')} 时出错: {str(e)}")
        
        if all_data:
            # 创建DataFrame
            df = pd.DataFrame(all_data)
            
            # 保存到Excel
            output_file = f"volume_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            df.to_excel(output_file, index=False)
            print(f"\n分析结果已保存到: {output_file}")
            
            # 打印统计信息
            print("\n总体统计信息:")
            print(f"分析股票数量: {len(all_data)}")
            print(f"时间段数量: {len(df.columns) - 1}")  # 减去股票代码列
    
    except Exception as e:
        print(f"处理Excel文件时出错: {str(e)}")

# 使用示例
if __name__ == "__main__":
    input_file = "limit_up_notbroken.xlsx"  # 输入文件路径
    #input_file = "test.xlsx"  # 输入文件路径
    analyze_multiple_stocks(input_file) 