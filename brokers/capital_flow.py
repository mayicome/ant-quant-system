import pandas as pd
import akshare as ak
import requests
from datetime import datetime
import os

def get_capital_flow(stock_code):
    # 检查主力流入
    found = False
    result = ""
    flow_df = get_realtime_capital_flow()
    symbol = stock_code.split('.')[0]
    # 从flow_df的最后一行取主力净流入值
    min_flow = flow_df.iloc[-1]['主力净流入']
    # 找出flow_df的代码列中是否存在symbol
    flow_df = flow_df[flow_df['代码'] == symbol]
    if flow_df.empty:
        result = f"主力净流入低于排名第400名的{min_flow};"
        return found,min_flow,0
    else:
        found = True
        # 从flow_df的最后一行取主力净流入值
        main_flow = flow_df.iloc[-1]['主力净流入']
        supper_rate = flow_df.iloc[-1]['超大单净占比']
        big_rate = flow_df.iloc[-1]['大单净占比']
        main_rate = supper_rate + big_rate
        result = f"主力净流入{main_flow}，主力净占比{main_rate}"
        return found,main_flow,main_rate

def get_realtime_capital_flow():
    """获取实时资金流向数据"""
    df = pd.DataFrame()
    for i in range(25):
        url = "http://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": str(i+1),     # 页码
            "pz": "100",   # 每页数量
            "po": "1",     # 排序方式
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f62",  # 资金流字段
            "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深A股
            "fields": "f12,f14,f2,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124"
        }

        # 参考资料：所有url的fs参数值
        '''cmd = {
            '沪深A股': "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
            '上证A股': "m:1+t:2,m:1+t:23",
            '深证A股': "m:0+t:6,m:0+t:13,m:0+t:80",
            '新股': "m:0+f:8,m:1+f:8",
            '中小板': "m:0+t:13",
            '创业板': "m:0+t:80",
            '科创板': "m:1+t:23",
            '沪股通': "b:BK0707",
            '深股通': "b:BK0804",
            'B股': "m:0+t:7,m:1+t:3",
            '上证AB股比价': "m:1+b:BK0498",
            '深证AB股比价': "m:0+b:BK0498",
            '风险警示板': "m:0+f:4,m:1+f:4",
            '两网及退市': "m:0+s:3",
            '港股': "",	
            '美股': "",
            '英股': ""
        }

        def get_url(_type, page, _cmd):
            if _type == "港股":
                url = "http://66.push2.eastmoney.com/api/qt/clist/get?cb=jQuery112406357577502075646_1616143565610&pn=" + str(
                    page) + "&pz=20&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:128+t:3," \
                            "m:128+t:4,m:128+t:1,m:128+t:2"
            elif _type == "英股":
                url = "http://push2.eastmoney.com/api/qt/clist/get?cb=jQuery1124011375626195911481_1616142975767&pn=" + str(
                    page) + "&pz=20&po=1&fid=f3&np=1&ut=fa5fd1943c7b386f172d6893dbfba10b&fs=m:155+t:1,m:155+t:2,m:155+t:3," \
                            "m:156+t:1,m:156+t:2,m:156+t:5,m:156+t:6,m:156+t:7,m:156+t:8"
            elif _type == "美股":
                url = "http://8.push2.eastmoney.com/api/qt/clist/get?cb=jQuery112406676382329604522_1616140562794&pn=" + str(
                    page) + "&pz=20&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:105,m:106,m:107"
            else:
                url = "http://30.push2.eastmoney.com/api/qt/clist/get?cb=jQuery1124022574761343490946_1616140246053&pn=" + str(
                    page) + "&pz=20&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=" + _cmd
            return url'''
        response = requests.get(url, params=params)
        #print(response.json())
        #{'rc': 0, 'rt': 6, 'svr': 181669434, 'lt': 1, 'full': 1, 'dlmkts': '', 
        # 'data': {'total': 2861, 'diff': 
        #  "最新价",      "代码",          "名称",            "主力净流入",         "超大单净额",         "超大单净占比", "大单净额",         "大单净占比","中单净额",           "中单净占比",  "小单净额",           "小单净占比", 'f124','f184','f204','f205','f206' 
        # [{'f2': 38.78, 'f12': '000063', 'f14': '中兴通讯', 'f62': 1170335776.0, 'f66': 1248103568.0, 'f69': 12.79, 'f72': -77767792.0, 'f75': -0.8, 'f78': -607408160.0, 'f81': -6.23, 'f84': -562927632.0, 'f87': -5.77, 'f124': 1741764894, 'f184': 12.0, 'f204': '-', 'f205': '-', 'f206': '-'}, 
        # {'f2': 23.61, 'f12': '300059', 'f14': '东方财富', 'f62': 753929856.0, 'f66': 654611728.0, 'f69': 6.63, 'f72': 99318128.0, 'f75': 1.01, 'f78': -452277568.0, 'f81': -4.58, 'f84': -301652272.0, 'f87': -3.05, 'f124': 1741764864, 'f184': 7.63, 'f204': '-', 'f205': '-', 'f206': '-'}, 
        # {'f2': 26.46, 'f12': '000032', 'f14': '深桑达Ａ', '
        data = response.json()["data"]["diff"]
        dfnew = pd.DataFrame(data)
        # 转换为DataFrame
        df = pd.concat([df, dfnew])

    df.columns = [
        "最新价","代码","名称","主力净流入",
        "超大单净额","超大单净占比","大单净额","大单净占比",
        "中单净额","中单净占比","小单净额","小单净占比",
        'f124','f184','f204','f205','f206' ]
    df = df.drop_duplicates(subset=["代码"])
    
    # 确保主力净流入列为数值类型
    df['主力净流入'] = pd.to_numeric(df['主力净流入'], errors='coerce').fillna(0)
    
    # 按主力净流入降序排序
    df = df.sort_values("主力净流入", ascending=False)
    df = df.reset_index(drop=True)
    return df

# 使用示例
if __name__ == "__main__":
    start_time = datetime.now()
    flow_df = get_realtime_capital_flow()
    print(flow_df)
    end_time = datetime.now()
    print(f"运行时间: {end_time - start_time}")
