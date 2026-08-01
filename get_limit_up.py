import akshare as ak

# 获取实时涨停板信息
def get_limit_up_stocks_ak():
    try:
        # 调用 涨停板行情 接口
        df = ak.stock_zt_pool_em()  # 东方财富网涨停板数据
        
        # 打印结果
        if not df.empty:
            print("获取实时涨停板信息成功：")
            print(df[['代码', '名称', '最新价', '涨跌幅', '封单金额']])
        else:
            print("暂无涨停板数据或市场未开盘。")
            
        return df
        
    except Exception as e:
        print(f"获取数据失败：{e}")
        return None

# 调用函数
limit_up_df_ak = get_limit_up_stocks_ak()