import pandas as pd
import json
import requests
from urllib import request
import time

def get_hot_stock_rank(data_type='大家都在看', date='hour'):
    """获取同花顺热榜数据"""
    # 修复headers格式
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': 'https://q.10jqka.com.cn/'
    }
    
    data_dict = {'大家都在看': 'normal', '快速飙升中':'skyrocket', "技术交易派": "tech", '价值投资派': 'value', '趋势投资派': 'trend'}
    list_type = data_dict[data_type]
    
    if list_type == 'normal' and date == 'hour':
        Type = 'hour'
    elif list_type =='skyrocket' and date == 'hour':
        Type = 'hour'
    if list_type == 'normal' and date == 'day':
        Type = 'day'
    elif list_type =='skyrocket' and date == 'day':
        Type = 'hour'
    elif list_type == 'tech':
        Type = 'day'
    elif list_type == 'value':
        Type = 'day'
    elif list_type == 'trend':
        Type = 'day'
    
    url = 'https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?'
    params = {
       'stock_type': 'a',
        'type': Type,
        'list_type': list_type
    }
    
    try:
        print(f"正在获取 {data_type} 的 {date} 数据...")
        print(f"请求URL: {url}")
        print(f"请求参数: {params}")
        
        res = requests.get(url=url, params=params, headers=headers, timeout=10)
        
        print(f"响应状态码: {res.status_code}")
        print(f"响应头: {dict(res.headers)}")
        
        # 检查响应状态码
        if res.status_code != 200:
            print(f"HTTP请求失败，状态码: {res.status_code}")
            return None
        
        # 检查响应内容
        print(f"响应内容长度: {len(res.text)}")
        print(f"响应内容前200字符: {res.text[:200]}")
        
        # 尝试解析JSON
        try:
            text = res.json()
        except json.JSONDecodeError as e:
            print(f"JSON解析失败: {e}")
            print(f"响应内容: {res.text}")
            return None
        
        status_code = text.get('status_code')
        if status_code == 0:
            try:
                stock_list = text.get('data', {}).get('stock_list', [])
                if not stock_list:
                    print("股票列表为空")
                    return None
                
                df = pd.DataFrame(stock_list)
                
                # 根据实际列数设置列名
                if len(df.columns) == 10:
                    columns = ('市场', '证券代码', '热度', '涨跌幅', '股票名称', '分析', '热度变化', '目标', '排序', '分析主题')
                else:
                    columns = ('市场', '证券代码', '热度', '涨跌幅', '股票名称', '分析', '热度变化', '目标', '排序', '分析主题', '更新时间')
                
                # 确保列数匹配
                if len(df.columns) == len(columns):
                    df.columns = columns
                else:
                    print(f"列数不匹配：实际{len(df.columns)}列，期望{len(columns)}列")
                    print(f"实际列: {list(df.columns)}")
                
                return df
            except Exception as e:
                print(f"数据处理失败: {e}")
                return None
        else:
            print(f'API返回错误，状态码: {status_code}')
            print(f'错误信息: {text.get("message", "未知错误")}')
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"网络请求异常: {e}")
        return None
    except Exception as e:
        print(f"未知错误: {e}")
        return None

def main():
    """主函数"""
    print("=== 同花顺热榜数据获取 ===")
    
    # 尝试不同的数据类型
    data_types = ['大家都在看', '快速飙升中', '技术交易派', '价值投资派', '趋势投资派']
    
    for data_type in data_types:
        print(f"\n尝试获取: {data_type}")
        
        # 先尝试小时数据
        df = get_hot_stock_rank(data_type, 'hour')
        if df is not None and not df.empty:
            print(f"✓ 成功获取 {data_type} 小时数据，共 {len(df)} 条记录")
            break
        
        # 如果小时数据失败，尝试日数据
        df = get_hot_stock_rank(data_type, 'day')
        if df is not None and not df.empty:
            print(f"✓ 成功获取 {data_type} 日数据，共 {len(df)} 条记录")
            break
    
    if df is not None and not df.empty:
        print(f"\n=== 数据获取成功 ===")
        print(f"数据形状: {df.shape}")
        print(f"列名: {list(df.columns)}")
        print("\n前5条数据:")
        print(df.head())
        
        # 保存到Excel
        try:
            filename = f"同花顺热榜_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
            df.to_excel(filename, index=False)
            print(f"\n✓ 数据已保存到: {filename}")
        except Exception as e:
            print(f"保存Excel失败: {e}")
    else:
        print("\n❌ 所有数据类型都无法获取，可能的原因：")
        print("1. 同花顺API已更新或失效")
        print("2. 网络连接问题")
        print("3. 需要更新请求参数或headers")
        print("4. 可能需要登录或验证")

if __name__ == "__main__":
    main()