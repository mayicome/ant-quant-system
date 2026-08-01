import akshare as ak
import time
from datetime import datetime
import pandas as pd

def get_plate_real_time():
    """获取实时板块数据"""
    try:
        # 获取概念板块实时行情
        plate_df = ak.stock_board_concept_name_em()
        
        # 获取行业板块实时行情  
        industry_df = ak.stock_board_industry_name_em()
        
        # 合并数据
        all_plates = pd.concat([plate_df, industry_df])
        
        return all_plates[['板块名称', '涨跌幅', '最新价', '换手率']]
    except Exception as e:
        print(f"获取板块数据失败: {e}")
        return pd.DataFrame()

def detect_plate_anomaly(threshold=2.0):
    """检测板块异动"""
    previous_data = pd.DataFrame()
    
    while True:
        current_time = datetime.now().strftime("%H:%M:%S")
        current_df = get_plate_real_time()

        #print(current_df)
        
        if not current_df.empty and not previous_data.empty:
            for _, row in current_df.iterrows():
                plate_name = row['板块名称']
                current_change = float(row['涨跌幅'])  # 确保是标量值
                
                if plate_name in previous_data.index:
                    prev_change = previous_data.loc[plate_name, '涨跌幅']
                    if isinstance(prev_change, pd.Series):
                        prev_change = prev_change.iloc[0]  # 如果是Series，取第一个值
                    prev_change = float(prev_change)  # 确保是标量值
                    
                    # 检测大幅变动
                    change_diff = abs(current_change - prev_change)
                    if change_diff > threshold:
                        direction = "异动拉升" if current_change > prev_change else "走弱下跌"
                        print(f"🚨 {current_time} {plate_name}{direction} "
                              f"({prev_change:.2f}% → {current_change:.2f}%)")
        
        previous_data = current_df.set_index('板块名称') if not current_df.empty else pd.DataFrame()
        time.sleep(30)  # 每30秒检查一次

# 使用示例
detect_plate_anomaly(threshold=1)