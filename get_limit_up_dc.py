import requests
import json

def get_limit_up_stocks_eastmoney():
    """
    从东方财富网获取实时涨停板数据
    :return: 一个包含涨停板股票信息的列表(dict)，如果失败则返回None
    """
    url = "http://push2.eastmoney.com/api/qt/clist/get"
    
    # 构造请求参数
    params = {
        'pn': 1,              # 页码
        'pz': 50,             # 每页数量
        'po': 1,              # 排序方向
        'np': 1,              # 不知道啥意思，照着填
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281', # 用户token，固定
        'fltt': 2,            # 过滤条件，2表示未过滤
        'invt': 2,            # 投资类型，2表示全部
        'fid': 'f3',          # 排序字段，f3是涨跌幅
        # 'fs' 参数是关键，定义了筛选条件：A股、B股、创业板等
        'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048',
        # 'fields' 参数定义了需要返回的字段
        'fields': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152'
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'http://quote.eastmoney.com/ztb/detail.html', # 伪装来源页
        'Accept-Encoding': 'gzip, deflate'
    }

    try:
        # 发送GET请求
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        # 检查响应状态
        if response.status_code == 200:
            # 解析JSON数据
            data = response.json()
            
            # 检查返回的数据是否包含 'data' 和 'diff'
            if data.get('data') and data['data'].get('diff'):
                # 'diff' 列表中的每个元素都是一只股票的信息
                stocks_list = data['data']['diff']
                
                # 为了让输出更友好，我们可以只提取几个关键信息
                simplified_list = []
                for stock in stocks_list:
                    # 注意：字段 f12, f14, f2, f3 等需要你根据实际返回的JSON来对应
                    simplified_stock = {
                        '代码': stock.get('f12', ''),
                        '名称': stock.get('f14', ''),
                        '最新价': stock.get('f2', 0),
                        '涨跌幅': stock.get('f3', 0),
                        '封单金额': stock.get('f62', 0) # 封单金额可能对应不同的字段
                    }
                    simplified_list.append(simplified_stock)
                
                return simplified_list
            else:
                print("返回的数据结构不正确或没有找到涨停板数据。")
                print(f"完整返回: {data}")
                return None
        else:
            print(f"请求失败，状态码: {response.status_code}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"网络请求异常: {e}")
        return None
    except json.JSONDecodeError:
        print("解析JSON数据失败，返回的可能不是有效的JSON格式。")
        # 有时网站会返回带有回调函数的JSONP格式，需要处理一下
        # 例如：jQuery112403460595299921387_1672507942315({"rc":0, ...});
        try:
            text = response.text
            start_idx = text.find('(') + 1
            end_idx = text.rfind(')')
            json_data = json.loads(text[start_idx:end_idx])
            print("成功解析JSONP格式数据。")
            # 后续处理逻辑类似...
        except:
            print("尝试解析JSONP也失败了。")
        return None

# --- 主程序 ---
if __name__ == "__main__":
    print("正在从东方财富网获取实时涨停板信息...")
    limit_up_stocks = get_limit_up_stocks_eastmoney()

    if limit_up_stocks:
        print(f"\n共找到 {len(limit_up_stocks)} 只涨停股票：")
        for stock in limit_up_stocks:
            print(f"{stock['代码']} - {stock['名称']}: {stock['最新价']}元 ({stock['涨跌幅']}%)")
    else:
        print("未能获取到涨停板信息。")