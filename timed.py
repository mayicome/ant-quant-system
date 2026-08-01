import os
import sys
from datetime import datetime, timedelta
import pandas as pd
import akshare as ak

# 缓存交易日历，避免重复获取
_trade_calendar_cache = None

def _get_trade_calendar():
    """获取交易日历（带缓存）"""
    global _trade_calendar_cache
    if _trade_calendar_cache is None:
        try:
            trade_cal = ak.tool_trade_date_hist_sina()
            # 将日期列转换为日期类型
            trade_cal['trade_date'] = pd.to_datetime(trade_cal['trade_date']).dt.date
            _trade_calendar_cache = set(trade_cal['trade_date'].values)
        except Exception as e:
            print(f"获取交易日历失败: {e}")
            _trade_calendar_cache = set()  # 空集合，将使用备用逻辑
    return _trade_calendar_cache

def is_tradeday(date=None):
    """
    判断指定日期是否为交易日
    如果 date 为 None，则判断今天
    使用 akshare 获取交易日历，支持2026年及以后
    """
    if date is None:
        date = datetime.now().date()
    
    # 将 date 转换为 datetime
    if isinstance(date, datetime):
        date = date.date()
    
    # 获取交易日历
    trade_dates = _get_trade_calendar()
    
    # 如果交易日历为空（获取失败），使用基本规则
    if not trade_dates:
        weekday = date.weekday()  # 0=Monday, 6=Sunday
        return weekday < 5  # 周一到周五
    
    # 检查该日期是否在交易日历中
    return date in trade_dates

#如果今天不是交易日，则跳过
if not is_tradeday():
    print("今天不是交易日，跳过")
    sys.exit(0)

os.system("python fillt0.py")

os.system("python summary.py")

tomorrow = datetime.now().date() + timedelta(days=1)
if not is_tradeday(tomorrow):
    default_folder_path = os.path.join(os.getcwd(), "共享文件夹", "T+0week")
    os.system(f"python fillt0.py '{default_folder_path}'")
    os.system(f"python summary.py '{default_folder_path}'")
sys.exit(0)