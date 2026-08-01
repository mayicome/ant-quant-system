import os
import pandas as pd

def symbol2stock(symbol):
    """
    将股票代码转换为QMT识别的格式
    Args:
        symbol (str): 原始股票代码（例如：000001、600001等）
    Returns:
        str: QMT格式的股票代码（例如：000001.SZ、600001.SH等）
    """
    symbol = symbol.strip()
    
    if '.SZ' in symbol or '.SH' in symbol or '.BJ' in symbol:
        return symbol
        
    symbol = symbol.zfill(6)
    
    if symbol.startswith(('0', '1', '3')):
        return f"{symbol}.SZ"  # 深交所
    elif symbol.startswith(('5', '6')):
        return f"{symbol}.SH"  # 上交所
    elif symbol.startswith(('4', '8', '920')):
        return f"{symbol}.BJ"  # 北交所
    else:
        raise ValueError(f"无效的股票代码: {symbol}")
    
def get_stock_name(all_a_stocks, stock_code):
    """获取股票名称（兼容旧版本，建议使用utils.stock_info_manager.get_stock_name）"""
    try:
        from utils.stock_info_manager import get_stock_name as get_stock_name_new
        return get_stock_name_new(stock_code)
    except ImportError:
        # 如果全局管理器不可用，使用旧方法
        result = all_a_stocks[all_a_stocks['证券代码'] == stock_code[:6]]
        if not result.empty:
            return result['证券简称'].values[0]
        return "未知名称"

def load_all_stocks_info():
    """加载股票基本信息（现在调用全局股票信息管理器，消除代码冗余）"""
    try:
        # 使用全局股票信息管理器
        from utils.stock_info_manager import get_stock_info_manager
        
        # 获取管理器实例
        manager = get_stock_info_manager()
        
        # 确保缓存已加载
        manager._load_stock_info()
        
        # 如果缓存为空，尝试创建文件
        if not manager._stock_info_cache:
            csv_file = os.path.join('data', 'all_a_stocks.csv')
            if manager._create_stock_info_file(csv_file):
                # 重新加载
                manager._load_stock_info()
        
        # 返回DataFrame格式的数据（兼容旧代码）
        if manager._stock_info_cache:
            # 将缓存转换为DataFrame格式
            data_list = []
            for code, info in manager._stock_info_cache.items():
                data_list.append({
                    '证券代码': code,
                    '证券简称': info['证券简称'],
                    '上市日期': info['上市日期']
                })
            
            all_a_stocks = pd.DataFrame(data_list)
            return all_a_stocks
        else:
            return pd.DataFrame(columns=['证券代码', '证券简称', '上市日期'])
            
    except ImportError:
        print("警告：全局股票信息管理器不可用，返回空DataFrame")
        return pd.DataFrame(columns=['证券代码', '证券简称', '上市日期'])
    except Exception as e:
        print(f"加载股票信息失败: {e}")
        return pd.DataFrame(columns=['证券代码', '证券简称', '上市日期'])