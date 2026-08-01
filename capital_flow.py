import pandas as pd
import akshare as ak
import requests
from datetime import datetime
import os
import sys
import time
from utils.trading_day import is_tradeday

def get_realtime_capital_flow():
    """获取实时资金流向数据"""
    df = pd.DataFrame()
    
    # 使用Session保持连接
    session = requests.Session()
    
    # 更完整的请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'http://quote.eastmoney.com/',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache'
    }
    session.headers.update(headers)
    
    # 测试网络连接
    print("正在测试网络连接...")
    test_urls = [
        "https://push2.eastmoney.com/api/qt/clist/get",
        "http://push2.eastmoney.com/api/qt/clist/get"
    ]
    base_url = None
    for test_url in test_urls:
        try:
            test_params = {"pn": "1", "pz": "1", "po": "1", "np": "1", "fltt": "2", "invt": "2", 
                          "fid": "f62", "fs": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23",
                          "fields": "f12,f14,f2"}
            response = session.get(test_url, params=test_params, timeout=5)
            if response.status_code == 200:
                base_url = test_url
                print(f"✓ 网络连接正常，使用: {test_url}")
                break
        except:
            continue
    
    if base_url is None:
        print("✗ 网络连接失败，请检查:")
        print("  1. 网络连接是否正常")
        print("  2. 防火墙是否阻止了连接")
        print("  3. 是否需要配置代理")
        session.close()
        return df
    
    # 使用可用的URL
    url_base = base_url
    
    for i in range(25):
        # 使用测试后确定的URL
        url = url_base
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
        
        # 重试机制
        max_retries = 3
        retry_count = 0
        success = False
        
        while retry_count < max_retries and not success:
            try:
                response = session.get(url, params=params, timeout=15, verify=True)
                response.raise_for_status()  # 检查HTTP状态码
                
                # 检查响应内容
                json_data = response.json()
                
                # 检查响应结构
                if "data" not in json_data:
                    print(f"第 {i+1} 页响应格式异常: {json_data}")
                    retry_count += 1
                    if retry_count < max_retries:
                        time.sleep(2)
                    continue
                
                if "diff" not in json_data["data"]:
                    print(f"第 {i+1} 页数据为空，可能已到最后一页")
                    success = True  # 标记为成功，但不再继续
                    break
                
                data = json_data["data"]["diff"]
                if not data:
                    print(f"第 {i+1} 页数据为空，可能已到最后一页")
                    success = True
                    break
                
                dfnew = pd.DataFrame(data)
                df = pd.concat([df, dfnew], ignore_index=True)
                success = True
                print(f"第 {i+1} 页获取成功，共 {len(dfnew)} 条数据")
                
                # 请求成功后延迟，避免请求过快
                if i < 24:  # 最后一页不需要延迟
                    time.sleep(0.3)  # 延迟300毫秒
                    
            except requests.exceptions.ConnectionError as e:
                retry_count += 1
                error_msg = str(e)
                if retry_count == 1:  # 第一次失败时提供诊断信息
                    print(f"第 {i+1} 页连接失败: {error_msg}")
                    print(f"  提示: 请检查网络连接、防火墙设置或代理配置")
                    print(f"  尝试访问: {url}")
                if retry_count < max_retries:
                    print(f"第 {i+1} 页连接失败，正在重试 ({retry_count}/{max_retries})...")
                    time.sleep(2)  # 重试前等待2秒
                else:
                    print(f"第 {i+1} 页连接失败，已重试 {max_retries} 次，跳过该页")
                    break
            except requests.exceptions.Timeout as e:
                retry_count += 1
                if retry_count < max_retries:
                    print(f"第 {i+1} 页请求超时，正在重试 ({retry_count}/{max_retries})...")
                    time.sleep(2)
                else:
                    print(f"第 {i+1} 页请求超时，已重试 {max_retries} 次，跳过该页")
                    break
            except requests.exceptions.RequestException as e:
                retry_count += 1
                if retry_count < max_retries:
                    print(f"第 {i+1} 页请求异常，正在重试 ({retry_count}/{max_retries}): {str(e)}")
                    time.sleep(2)
                else:
                    print(f"第 {i+1} 页请求异常，已重试 {max_retries} 次，跳过该页: {str(e)}")
                    break
            except KeyError as e:
                print(f"第 {i+1} 页数据解析失败: {e}")
                print(f"响应内容: {response.text[:500] if 'response' in locals() else 'N/A'}")
                break
            except Exception as e:
                print(f"第 {i+1} 页发生未知错误: {type(e).__name__}: {str(e)}")
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(2)
                else:
                    break
    
    session.close()

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
    #如果今天不是交易日，则跳过
    if not is_tradeday():
        print("今天不是交易日，跳过")
        sys.exit(0)

    start_time = datetime.now()
    flow_df = get_realtime_capital_flow()
    
    if flow_df.empty:
        print("警告：未获取到任何数据")
    else:
        print(f"成功获取 {len(flow_df)} 条数据")
        print(flow_df.head(10))  # 只显示前10条
        
        # 确保保存目录存在
        save_dir = "capital_flow"
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        
        # 保存到以当前日期为名称的excel文件中
        excel_path = os.path.join(save_dir, f"{datetime.now().strftime('%Y%m%d')}.xlsx")
        flow_df.to_excel(excel_path, index=False)
        print(f"数据已保存到: {excel_path}")
    
    end_time = datetime.now()
    print(f"运行时间: {end_time - start_time}")
