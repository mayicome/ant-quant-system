#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键价格计算模块（Web版本）
不依赖QWidget，专门为Web应用设计
按照主程序的计算逻辑实现
"""

import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta, time as datetime_time, date
from typing import Optional
import json
import time as time_module
import re

from utils.trading_day import REFERENCE_SWITCH_TIME

# 版本指纹：用于确认运行时加载的是哪份 key_price_calculator.py
KEY_PRICE_CALC_BUILD = "kpcalc-build-2026-04-08-v2"
_KP_VERSION_PRINTED = False
_OPEN_FALLBACK_LOGGED = False

# 模块加载即打印一次（用于排查“导入的不是这份文件”）
try:
    _fp = os.path.abspath(__file__)
    _mt = datetime.fromtimestamp(os.path.getmtime(_fp)).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[KEY_PRICE_CALC_IMPORTED] {KEY_PRICE_CALC_BUILD} | file={_fp} | mtime={_mt}")
except Exception:
    print(f"[KEY_PRICE_CALC_IMPORTED] {KEY_PRICE_CALC_BUILD}")

# 模块级缓存：存储当天的股票数据
_stock_data_cache = {}
_cache_date = None
_cache_phase = None


def _china_now() -> datetime:
    """
    A 股交易时段、是否交易日、是否输出「今开/今日最高/今日最低」等，一律按北京时间判断。
    若仅用本机 datetime.now()，在时区设为 UTC 或非中国时会出现 9:00–15:00 判定失败，
    导致关键字段缺失，策略（如突破5日线）在另一台电脑上永远选不出股票。
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        try:
            import pytz

            return datetime.now(pytz.timezone("Asia/Shanghai"))
        except Exception:
            return datetime.now()


def _builtin_price_feed() -> bool:
    try:
        from utils.qmt_execution_config import use_builtin_price_feed
        return use_builtin_price_feed()
    except Exception:
        return False


def _load_daily_df_from_cache_for_kpc(stock_code: str, through_date: date, *, allow_on_demand: bool = True):
    """builtin：从 daily_cache 构造 KeyPriceCalculator 用的日线 DataFrame。"""
    try:
        from utils.daily_cache_reader import load_daily_dataframe, to_full_stock_code
    except Exception:
        return None
    full = to_full_stock_code(stock_code)
    if not full:
        return None
    raw = load_daily_dataframe(
        full,
        through_date=through_date,
        allow_xtdata_fallback=False,
        allow_on_demand=allow_on_demand,
    )
    if raw is None or getattr(raw, "empty", True):
        return None
    data = raw.copy()
    if getattr(data.index, "name", None) == "time" or (
        len(getattr(data.index, "names", []) or []) == 1
        and (data.index.names or [None])[0] == "time"
    ):
        data = data.reset_index(drop=True)
    if "date" in data.columns:
        data["time"] = pd.to_datetime(data["date"])
    elif "time" in data.columns:
        try:
            sample = data["time"].iloc[0]
            if isinstance(sample, (int, float)) and float(sample) > 1e10:
                data["time"] = pd.to_datetime(data["time"], unit="ms", errors="coerce")
            else:
                data["time"] = pd.to_datetime(data["time"], errors="coerce")
        except Exception:
            data["time"] = pd.to_datetime(data["time"], errors="coerce")
    try:
        data = data.sort_values("time")
    except ValueError:
        data = data.sort_index()
    return data


def _get_cache_key(stock_code):
    """生成缓存键：股票代码 + 当前日期"""
    today = _china_now().date()
    return f"{stock_code}_{today}"

def _clear_cache_if_needed():
    """当日期或交易阶段变化时清空缓存，避免盘中缓存跨到收盘后仍被复用。"""
    global _cache_date, _cache_phase
    now = _china_now()
    today = now.date()
    phase = "other"
    try:
        from utils.trading_day import is_tradeday
        if is_tradeday(today):
            t = now.time()
            if t < datetime_time(9, 30):
                phase = "pre_open"
            elif t < REFERENCE_SWITCH_TIME:
                phase = "intraday"
            else:
                phase = "after_close"
        else:
            phase = "non_trading"
    except Exception:
        phase = "other"

    if _cache_date != today or _cache_phase != phase:
        _stock_data_cache.clear()
        _cache_date = today
        _cache_phase = phase


def _row_time_to_date(ts) -> Optional[date]:
    """日线 time 列转 date。"""
    if ts is None:
        return None
    try:
        if hasattr(ts, "date"):
            return ts.date()
        return pd.to_datetime(ts).date()
    except Exception:
        return None


def _prev_trading_day_close_from_daily_df(df, today: date) -> Optional[float]:
    """
    上一完整交易日的日线收盘价（用于集合竞价/9:30 前）。
    仅当末日（或今日前最近一根）正好是「上一交易日」才返回；偏旧则 None，交给 live lastClose。
    """
    if df is None or len(df) < 1:
        return None
    prev_td = _previous_tradeday_date(today)
    try:
        for i in range(len(df) - 1, -1, -1):
            td = _row_time_to_date(df.iloc[i].get("time"))
            if td is None:
                continue
            if td == today:
                continue
            if td == prev_td:
                return float(df.iloc[i]["close"])
            # 比上一交易日更早 → daily_cache 落后
            if td < prev_td:
                return None
    except Exception:
        return None
    return None


def _previous_tradeday_date(today: date) -> date:
    try:
        from utils.trading_day import previous_tradeday

        return previous_tradeday(today)
    except Exception:
        d = today - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d


def _fetch_live_last_close(stock_code: str) -> float:
    """盘中从 results.json 取 QMT lastClose，纠正偏旧 daily_cache 昨收。"""
    code = str(stock_code or "").strip().upper()
    if not code:
        return 0.0
    c6 = "".join(ch for ch in code if ch.isdigit())[:6]
    variants = {code}
    if c6:
        variants.add(c6)
        variants.add(f"{c6}.SH" if c6.startswith(("5", "6", "9")) else f"{c6}.SZ")

    try:
        from utils.ant_rules_io_ext import default_paths, load_json

        root = os.path.dirname(os.path.abspath(__file__))
        _, results_path = default_paths(root)
        if not os.path.isfile(results_path):
            return 0.0
        data = load_json(results_path)
        stocks = (data or {}).get("stocks") if isinstance(data, dict) else {}
        if not isinstance(stocks, dict):
            return 0.0
        for key in variants:
            bucket = stocks.get(key) or stocks.get(str(key).upper())
            if not isinstance(bucket, dict):
                continue
            for fk in ("last_close", "lastClose", "pre_close", "preClose"):
                try:
                    v = float(bucket.get(fk) or 0)
                except (TypeError, ValueError):
                    v = 0.0
                if v > 0:
                    return v
    except Exception:
        return 0.0
    return 0.0


def _resolve_intraday_prev_close(
    df,
    today: date,
    latest_close: float,
    latest_date: Optional[date],
    stock_code: str,
) -> float:
    """盘中昨收：日线末日须等于上一交易日；否则用 live lastClose，再退回日线末日。"""
    prev_td = _previous_tradeday_date(today)
    if latest_date is not None and latest_date == today and len(df) >= 2:
        try:
            return float(df.iloc[-2]["close"])
        except Exception:
            pass
    if latest_date is not None and latest_date == prev_td:
        return float(latest_close)
    live = _fetch_live_last_close(stock_code)
    if live > 0:
        return float(live)
    return float(latest_close)


class KeyPriceCalculator:
    """关键价格计算器（Web版本）"""
    
    def __init__(self, qmt_adapter=None):
        self.qmt_adapter = qmt_adapter
        self.logger = None
        
        # 初始化logger
        try:
            from utils.logger import Logger
            self.logger = Logger()
        except ImportError:
            import logging
            self.logger = logging.getLogger(__name__)
    
    def get_stock_name(self, stock_code):
        """获取股票名称"""
        try:
            # 1. 优先使用项目中的股票信息管理器
            try:
                from utils.stock_info_manager import get_stock_name
                name = get_stock_name(stock_code)
                if name and name != "未知名称":
                    return name
            except ImportError:
                pass
            
            # 2. 如果项目中的方法不可用，尝试使用QMT获取股票信息
            try:
                import xtquant.xtdata as xtdata
                # 尝试不同的股票代码格式
                stock_codes_to_try = [stock_code]
                if not '.' in stock_code:
                    stock_codes_to_try.extend([f"{stock_code}.SZ", f"{stock_code}.SH", f"{stock_code}.BJ"])
                
                for code in stock_codes_to_try:
                    try:
                        # 使用QMT获取股票基本信息
                        stock_info = xtdata.get_instrument_detail(code)
                        if stock_info and 'InstrumentName' in stock_info:
                            return stock_info['InstrumentName']
                    except:
                        continue
            except:
                pass
            
            # 3. 如果都失败了，返回股票代码
            return f"未知({stock_code})"
            
        except Exception as e:
            self.logger.error(f"获取股票名称失败: {e}")
            return f"获取失败({stock_code})"
    
    def get_stock_data(self, stock_code):
        """获取股票历史数据"""
        try:
            if self.qmt_adapter and hasattr(self.qmt_adapter, 'get_daily_data'):
                return self.qmt_adapter.get_daily_data(stock_code)
            else:
                raise Exception("QMT适配器不可用，无法获取股票数据")
        except Exception as e:
            self.logger.error(f"获取股票数据失败: {e}")
            raise e
    
    def _get_qmt_daily_data(self, stock_code):
        """通过QMT获取日线数据（带缓存）"""
        # 检查并清理过期缓存
        _clear_cache_if_needed()
        
        # 生成缓存键
        cache_key = _get_cache_key(stock_code)
        
        # 检查缓存
        if cache_key in _stock_data_cache:
            return _stock_data_cache[cache_key].copy()  # 返回副本，避免修改缓存
        
        current_date = _china_now().date()
        if _builtin_price_feed():
            allow_od = getattr(self, "_allow_on_demand_sync", True)
            data = _load_daily_df_from_cache_for_kpc(
                stock_code, current_date, allow_on_demand=allow_od
            )
            if data is not None and len(data) > 0:
                _stock_data_cache[cache_key] = data
                return data.copy()
            raise Exception(
                f"builtin 模式缺少 {stock_code} 的 daily_cache 日线，请先由大 QMT 同步"
            )

        try:
            import xtquant.xtdata as xtdata
            end_date = current_date  # 包含今天的数据
            
            # 往前推800天，确保有足够的数据计算120日均线
            start_date = end_date - timedelta(days=800)
            
            # 转换为QMT需要的格式
            startdate = start_date.strftime("%Y%m%d")# + "000000"
            enddate = end_date.strftime("%Y%m%d")# + "235959"
            
            # 下载历史数据
            try:
                xtdata.download_history_data(stock_code, '1d', startdate, enddate)
                time_module.sleep(1)  # 增加等待时间，确保下载完成
            except Exception as e:
                raise Exception(f"下载历史数据失败: {str(e)}")
            
            # 获取历史行情数据
            # 注意：count=-1 表示获取所有数据，但QMT可能有默认限制
            # 如果数据不足，可能需要多次获取或使用更大的count值
            try:
                # 先尝试获取所有数据
                df = xtdata.get_market_data_ex([], [stock_code], period='1d', 
                                             start_time=startdate, 
                                             end_time=enddate, 
                                             count=-1)
            except Exception as e:
                raise Exception(f"获取历史行情数据失败: {str(e)}")
            
            if stock_code not in df or len(df[stock_code]) == 0:
                raise Exception(f"未获取到股票{stock_code}的数据或数据为空")
            
            # 检查实际获取的数据量
            actual_data_count = len(df[stock_code])
            
            # 如果数据不足120条，立即触发二次获取
            df2 = None
            if actual_data_count < 120:
                
                # 快速处理获取起始日期（最小化处理）
                temp_data = pd.DataFrame(df[stock_code])
                if 'time' not in temp_data.columns:
                    temp_data = temp_data.reset_index()
                    if 'index' in temp_data.columns:
                        temp_data = temp_data.rename(columns={'index': 'time'})
                
                # 快速解析时间获取起始日期
                if 'time' in temp_data.columns and len(temp_data) > 0:
                    try:
                        temp_data['time'] = temp_data['time'].apply(lambda x: datetime.fromtimestamp(x/1000))
                        if temp_data['time'].iloc[0].year == 1970:
                            raw_values = temp_data['time'].values
                            temp_data['time'] = pd.Series([datetime.fromtimestamp(ts/1000000) for ts in raw_values])
                            if temp_data['time'].iloc[0].year == 1970:
                                temp_data['time'] = pd.Series([datetime.fromtimestamp(ts/1000000000) for ts in raw_values])
                        
                        temp_data = temp_data.sort_values('time')
                        first_date_obj = temp_data['time'].iloc[0]
                        if isinstance(first_date_obj, pd.Timestamp):
                            first_date_obj = first_date_obj.to_pydatetime()
                        elif not isinstance(first_date_obj, datetime):
                            first_date_obj = pd.to_datetime(first_date_obj).to_pydatetime()
                        
                        # 使用第一次数据的起始日期减1天作为第二次获取的结束日期
                        second_end_date = first_date_obj - timedelta(days=1)
                        # 往前推100天作为开始日期
                        second_start_date = second_end_date - timedelta(days=100)
                        
                        # 转换为QMT需要的格式
                        second_startdate = second_start_date.strftime("%Y%m%d") + "000000"
                        second_enddate = second_end_date.strftime("%Y%m%d") + "235959"
                        
                        try:
                            # 下载历史数据
                            xtdata.download_history_data(stock_code, '1d', second_startdate, second_enddate)
                            time_module.sleep(0.5)
                            
                            # 获取历史行情数据
                            df2 = xtdata.get_market_data_ex([], [stock_code], period='1d', 
                                                          start_time=second_startdate, 
                                                          end_time=second_enddate, 
                                                          count=-1)
                            
                            if not (df2 and stock_code in df2 and len(df2[stock_code]) > 0):
                                df2 = None
                        except Exception as e:
                            df2 = None
                    except Exception as e:
                        df2 = None
                else:
                    df2 = None
            
            # 如果有二次获取的数据，先合并原始数据
            if df2 is not None and stock_code in df2 and len(df2[stock_code]) > 0:
                # 将df2和df的数据合并（先转换为DataFrame再合并）
                try:
                    df2_data = pd.DataFrame(df2[stock_code])
                    df1_data = pd.DataFrame(df[stock_code])
                    # 合并两次获取的数据
                    combined_data = pd.concat([df2_data, df1_data], ignore_index=True)
                    # 更新df
                    df = {stock_code: combined_data}
                except Exception as e:
                    # 如果合并失败，df2会在后面单独处理
                    pass
            
            # 转换为DataFrame
            try:
                data = pd.DataFrame(df[stock_code])
                
                # 调试信息：打印原始DataFrame信息（已精简）
                # self.logger.info(f"原始DataFrame索引类型: {type(data.index)}, 索引值示例: {data.index[:3].tolist()}")
                # self.logger.info(f"原始DataFrame列名: {data.columns.tolist()}")
                
                # 检查是否有重复的列名
                if len(data.columns) != len(set(data.columns)):
                    # 重命名重复的列名
                    new_columns = []
                    column_counts = {}
                    for col in data.columns:
                        if col in column_counts:
                            column_counts[col] += 1
                            new_columns.append(f"{col}_{column_counts[col]}")
                        else:
                            column_counts[col] = 0
                            new_columns.append(col)
                    data.columns = new_columns
                
                # QMT返回的数据使用索引作为时间，需要重置索引
                if 'time' not in data.columns:
                    self.logger.info("没有time列，准备重置索引")
                    data = data.reset_index()
                    if 'index' in data.columns:
                        data = data.rename(columns={'index': 'time'})
                        self.logger.info("已将index列重命名为time")
                
                # 确保time列是datetime类型
                if 'time' in data.columns:
                    # self.logger.info(f"time列原始值示例: {data['time'].head(3).tolist()}")
                    # self.logger.info(f"time列原始类型: {type(data['time'].iloc[0])}")
                    
                    # 尝试不同的时间戳单位
                    try:
                        # 使用datetime.fromtimestamp确保本地时区
                        data['time'] = data['time'].apply(lambda x: datetime.fromtimestamp(x/1000))
                        # self.logger.info(f"本地时区解析后: {data['time'].head(3).tolist()}")
                        
                        # 检查是否解析为1970年
                        if data['time'].iloc[0].year == 1970:
                            self.logger.warning("检测到1970年，尝试不同时间戳单位")
                            # 获取原始值
                            raw_values = data['time'].values
                            # 使用datetime.fromtimestamp确保本地时区
                            data['time'] = pd.Series([datetime.fromtimestamp(ts/1000) for ts in raw_values])
                            # self.logger.info(f"本地时区解析后: {data['time'].head(3).tolist()}")
                            
                            # 如果还是1970年，尝试微秒
                            if data['time'].iloc[0].year == 1970:
                                data['time'] = pd.Series([datetime.fromtimestamp(ts/1000000) for ts in raw_values])
                                self.logger.info(f"微秒本地时区解析后: {data['time'].head(3).tolist()}")
                            
                            # 如果还是1970年，尝试纳秒
                            if data['time'].iloc[0].year == 1970:
                                data['time'] = pd.Series([datetime.fromtimestamp(ts/1000000000) for ts in raw_values])
                                self.logger.info(f"纳秒本地时区解析后: {data['time'].head(3).tolist()}")
                    except Exception as e:
                        self.logger.warning(f"时间戳解析失败: {e}")
                    
                    data = data.sort_values('time')
                
                # 重命名列名以匹配主程序
                column_mapping = {
                    'open': 'open',
                    'high': 'high', 
                    'low': 'low',
                    'close': 'close',
                    'volume': 'volume'
                }
                
                # 只保留需要的列
                available_columns = [col for col in column_mapping.keys() if col in data.columns]
                data = data[['time'] + available_columns]
                
                # 重命名列
                rename_dict = {col: column_mapping[col] for col in available_columns}
                data = data.rename(columns=rename_dict)
                
                # 如果有合并的数据，需要去重和排序
                if df2 is not None and stock_code in df2 and len(df2[stock_code]) > 0:
                    # 按时间排序
                    data = data.sort_values('time')
                    # 去重（保留最后一条）
                    data = data.drop_duplicates(subset=['time'], keep='last')
                    # 重置索引
                    data = data.reset_index(drop=True)
                
                # 存入缓存
                _stock_data_cache[cache_key] = data.copy()
                
                return data
                
            except Exception as e:
                raise Exception(f"处理数据失败: {str(e)}")
                
        except Exception as e:
            raise Exception(f"获取QMT数据失败: {str(e)}")
    
    def _calculate_technical_indicators(self, df):
        """计算技术指标"""
        try:
            # 计算移动平均线
            ma_periods = [5, 10, 20, 30, 60, 120]
            for period in ma_periods:
                df[f'MA{period}'] = df['close'].rolling(window=period).mean()
            
            # 计算布林带
            df['BOLL_MID'] = df['close'].rolling(window=20).mean()
            df['BOLL_STD'] = df['close'].rolling(window=20).std()
            df['BOLL_UPPER'] = df['BOLL_MID'] + 2 * df['BOLL_STD']
            df['BOLL_LOWER'] = df['BOLL_MID'] - 2 * df['BOLL_STD']
            
            # 计算前高前低（30个交易日）
            df['HIGH_30'] = df['high'].rolling(window=30).max()
            df['LOW_30'] = df['low'].rolling(window=30).min()
            df['HIGH_4'] = df['high'].rolling(window=4).max()
            
            return df
            
        except Exception as e:
            self.logger.error(f"计算技术指标失败: {e}")
            return df
    
    def _prior_close_ma(self, df, period, is_trading_day, current_time, today):
        """上一完整交易日收盘口径的真均线（近 period 日收盘均值）。

        - 交易日且尚未到 REFERENCE_SWITCH_TIME：若末根已是今日 K（未收盘），剔除后再算；
        - 收盘切换后或非交易日：末根已是完整交易日，直接用末根及之前 period 日。
        与「N日」重合点不同；供开盘夹档等策略使用。
        """
        try:
            if df is None or len(df) == 0 or period <= 0:
                return None
            closes = df["close"].astype(float)
            latest_date = df.iloc[-1]["time"]
            if hasattr(latest_date, "date"):
                latest_date = latest_date.date()
            else:
                latest_date = pd.to_datetime(latest_date).date()
            series = closes
            if (
                is_trading_day
                and current_time < REFERENCE_SWITCH_TIME
                and latest_date == today
                and len(closes) >= 2
            ):
                series = closes.iloc[:-1]
            if len(series) < period:
                return None
            val = float(series.iloc[-period:].mean())
            if val != val or val <= 0:
                return None
            return val
        except Exception:
            return None

    def _calculate_ma_intersection_price(self, df, period, prev_close, is_trading_day, current_time):
        """
        计算与均线重合的可能的最新价
        
        业务逻辑：
        - 交易日的 REFERENCE_SWITCH_TIME 后～24:00：获取到今天的历史数据，以到今天为止的四天的收盘价的均价作为5日线重合点
        - 交易日的 00:00～REFERENCE_SWITCH_TIME 前：获取到前一交易日的历史数据，以到前一交易日为止的四天的收盘价的均价作为5日线重合点
        - 非交易日：获取到前一交易日的历史数据，以到前一交易日为止的四天的收盘价的均价作为5日线重合点
        
        注意：5日线重合点是前4天的收盘价的平均值（除以4），不是5天除以4。
        
        Args:
            df: 历史数据DataFrame
            period: 均线周期（例如5表示5日线）
            prev_close: 昨收盘价
            is_trading_day: 是否是交易日
            current_time: 当前时间
            
        Returns:
            float: 与均线重合的价格，如果无法计算则返回None
        """
        try:
            # 需要前(period-1)天的数据来计算重合点
            # 例如5日线需要前4天的数据
            days_needed = period - 1
            if len(df) < days_needed:
                return None
            
            today = _china_now().date()
            
            # 判断df的最后一个交易日是否是今天
            latest_date = df.iloc[-1]['time']
            if hasattr(latest_date, 'date'):
                latest_date = latest_date.date()
            else:
                try:
                    latest_date = pd.to_datetime(latest_date).date()
                except:
                    latest_date = None
            is_df_contains_today = (latest_date == today) if latest_date is not None else False
            
            # 判断当前时间段
            is_trading_day_15_24 = is_trading_day and REFERENCE_SWITCH_TIME <= current_time
            is_trading_day_0_15 = is_trading_day and current_time < REFERENCE_SWITCH_TIME
            is_non_trading_day = not is_trading_day
            
            # 根据时间段获取前(period-1)天的收盘价
            if is_trading_day_15_24:
                # 交易日 REFERENCE_SWITCH_TIME 后：获取到今天的历史数据，以到今天为止的四天的收盘价的均价
                # 需要df包含今天的数据
                if not is_df_contains_today:
                    raise ValueError(
                        f"交易日 {REFERENCE_SWITCH_TIME.strftime('%H:%M')} 之后，df必须包含今天({today})的数据，"
                        f"但df的最后一条数据日期是{latest_date}"
                    )
                
                # 获取最近(period-1)天的收盘价，包括今天
                # 例如5日线：取最近4天（包括今天）
                if len(df) >= days_needed:
                    # df包含今天，取最近days_needed天的数据（包括今天）
                    recent_closes = df['close'].iloc[-days_needed:].tolist()
                else:
                    # 数据不够
                    return None
            elif is_trading_day_0_15 or is_non_trading_day:
                # 交易日的 00:00～REFERENCE_SWITCH_TIME 前或非交易日：获取到前一交易日的历史数据
                # 不包含今天的数据
                if is_df_contains_today:
                    # df包含今天的数据，排除今天
                    if len(df) > 1:
                        recent_closes = df['close'].iloc[:-1].tolist()
                    else:
                        return None
                else:
                    # df不包含今天的数据，直接使用
                    recent_closes = df['close'].iloc[:].tolist()
                
                # 取最近days_needed天的数据
                if len(recent_closes) >= days_needed:
                    recent_closes = recent_closes[-days_needed:]
                else:
                    return None
            else:
                return None
            
            # 计算前(period-1)天的收盘价的平均值
            if len(recent_closes) == days_needed:
                intersection_price = sum(recent_closes) / days_needed
                
                # 验证计算结果：检查是否在合理范围内
                if intersection_price > 0 and intersection_price < prev_close * 2:
                    return intersection_price
            
            return None
            
        except Exception as e:
            # 如果计算失败，返回None
            return None
    
    def _calculate_high_low_date(self, df, price_col, target_price, is_trading_day, current_time):
        """
        计算前高或前低的具体日期
        
        Args:
            df: 历史数据DataFrame
            price_col: 价格列名（high或low）
            target_price: 目标价格（前高或前低）
            is_trading_day: 是否是交易日
            current_time: 当前时间
            
        Returns:
            str: 日期字符串，格式为"m月n日"，如果无法计算则返回None
        """
        try:
            from datetime import time as datetime_time
            
            # 确定查找范围：根据交易日和时间确定
            if is_trading_day and current_time >= REFERENCE_SWITCH_TIME:
                # 交易日 REFERENCE_SWITCH_TIME 之后：从最新数据开始查找（包含今天）
                search_start_index = len(df) - 1
            else:
                # 交易日 REFERENCE_SWITCH_TIME 之前或非交易日：从最新数据开始查找（不包含今天）
                search_start_index = len(df) - 1
            
            # 从最新数据往前查找，找到等于目标价格的位置
            for i in range(search_start_index, -1, -1):
                if df.iloc[i][price_col] == target_price:
                    # 找到目标价格，获取对应的日期
                    row_data = df.iloc[i]
                    
                    # 调试信息：打印可用的列名（已精简）
                    # self.logger.info(f"可用列名: {list(row_data.index)}")
                    
                    # 尝试从不同可能的列名获取日期
                    date_value = None
                    possible_date_columns = ['time', 'date', 'Date', 'TIME', 'index']
                    
                    for col_name in possible_date_columns:
                        if col_name in row_data.index:
                            date_value = row_data[col_name]
                            # self.logger.info(f"找到日期列 {col_name}: {date_value}, 类型: {type(date_value)}")
                            break
                    
                    # 如果从行数据中找不到日期，尝试从DataFrame的索引获取
                    if date_value is None:
                        try:
                            date_value = df.index[i]
                            self.logger.info(f"从索引获取日期: {date_value}, 类型: {type(date_value)}")
                        except:
                            pass
                    
                    # 格式化日期
                    if date_value is not None:
                        # 调试信息：打印原始值和类型（已精简）
                        # self.logger.info(f"原始日期值: {date_value}, 类型: {type(date_value)}, dtype: {getattr(date_value, 'dtype', 'N/A')}")
                        
                        # 处理不同类型的日期格式
                        if hasattr(date_value, 'month') and hasattr(date_value, 'day'):
                            # pandas Timestamp 或 datetime 对象
                            # 检查是否是1970年（Unix时间戳起始年），说明时间戳解析有问题
                            if date_value.year == 1970:
                                self.logger.warning(f"检测到1970年日期，可能是时间戳解析错误: {date_value}")
                                # 尝试从原始数据重新解析
                                try:
                                    # 获取原始的时间戳值
                                    raw_timestamp = df.iloc[i]['time']
                                    if hasattr(raw_timestamp, 'value'):  # pandas Timestamp的纳秒值
                                        timestamp_ns = raw_timestamp.value
                                        # 尝试不同的时间戳单位
                                        for unit in ['ns', 'us', 'ms', 's']:
                                            try:
                                                if unit == 'ns':
                                                    timestamp_s = timestamp_ns / 1_000_000_000
                                                elif unit == 'us':
                                                    timestamp_s = timestamp_ns / 1_000_000
                                                elif unit == 'ms':
                                                    timestamp_s = timestamp_ns / 1_000
                                                else:  # 's'
                                                    timestamp_s = timestamp_ns
                                                
                                                # 使用本地时区（QMT时间戳已经是本地时间）
                                                dt = datetime.fromtimestamp(timestamp_s)
                                                # 检查日期是否合理（2000年以后）
                                                if dt.year >= 2000:
                                                    result = f"{dt.month}月{dt.day}日"
                                                    self.logger.info(f"重新解析时间戳结果({unit}): {result}")
                                                    return result
                                            except:
                                                continue
                                except Exception as e:
                                    self.logger.warning(f"重新解析时间戳失败: {e}")
                            
                            result = f"{date_value.month}月{date_value.day}日"
                            # self.logger.info(f"格式化结果: {result}")
                            return result
                        elif hasattr(date_value, 'strftime'):
                            # 可以格式化的日期对象
                            try:
                                formatted_date = date_value.strftime('%m月%d日')
                                # self.logger.info(f"strftime格式化结果: {formatted_date}")
                                return formatted_date
                            except Exception as e:
                                self.logger.warning(f"strftime格式化失败: {e}")
                        else:
                            # 处理各种可能的日期格式
                            try:
                                # 方法1：处理时间戳格式（如1760025600000.0）
                                if hasattr(date_value, 'dtype') and 'float' in str(date_value.dtype):
                                    # 检查是否是时间戳格式（13位数字）
                                    date_str = str(int(date_value))
                                    if len(date_str) == 13:  # 毫秒时间戳格式
                                        timestamp = int(date_value) / 1000  # 转换为秒
                                        # 使用本地时区（QMT时间戳已经是本地时间）
                                        dt = datetime.fromtimestamp(timestamp)
                                        result = f"{dt.month}月{dt.day}日"
                                        self.logger.info(f"时间戳解析结果: {result}")
                                        return result
                                    elif len(date_str) == 8:  # YYYYMMDD格式
                                        year = int(date_str[:4])
                                        month = int(date_str[4:6])
                                        day = int(date_str[6:8])
                                        result = f"{month}月{day}日"
                                        self.logger.info(f"QMT日期解析结果: {result}")
                                        return result
                                
                                # 方法2：处理字符串格式的日期
                                date_str = str(date_value)
                                if len(date_str) == 13 and date_str.isdigit():  # 毫秒时间戳格式
                                    timestamp = int(date_str) / 1000  # 转换为秒
                                    # 使用本地时区（QMT时间戳已经是本地时间）
                                    dt = datetime.fromtimestamp(timestamp)
                                    result = f"{dt.month}月{dt.day}日"
                                    self.logger.info(f"字符串时间戳解析结果: {result}")
                                    return result
                                elif len(date_str) == 8 and date_str.isdigit():  # YYYYMMDD格式
                                    year = int(date_str[:4])
                                    month = int(date_str[4:6])
                                    day = int(date_str[6:8])
                                    result = f"{month}月{day}日"
                                    self.logger.info(f"字符串日期解析结果: {result}")
                                    return result
                                
                                # 方法3：处理带小数点的日期字符串
                                if '.' in date_str:
                                    date_str = date_str.split('.')[0]
                                    if len(date_str) == 13 and date_str.isdigit():  # 毫秒时间戳格式
                                        timestamp = int(date_str) / 1000  # 转换为秒
                                        # 使用本地时区（QMT时间戳已经是本地时间）
                                        dt = datetime.fromtimestamp(timestamp)
                                        result = f"{dt.month}月{dt.day}日"
                                        self.logger.info(f"小数点时间戳解析结果: {result}")
                                        return result
                                    elif len(date_str) == 8 and date_str.isdigit():  # YYYYMMDD格式
                                        year = int(date_str[:4])
                                        month = int(date_str[4:6])
                                        day = int(date_str[6:8])
                                        result = f"{month}月{day}日"
                                        self.logger.info(f"小数点日期解析结果: {result}")
                                        return result
                                
                                # 方法4：尝试pandas解析其他格式
                                import pandas as pd
                                parsed_date = pd.to_datetime(date_value)
                                result = f"{parsed_date.month}月{parsed_date.day}日"
                                self.logger.info(f"pandas解析结果: {result}")
                                return result
                            except Exception as e:
                                self.logger.warning(f"所有日期解析方法都失败: {e}")
                                # 最后的备用方案：尝试直接转换
                                try:
                                    if isinstance(date_value, (int, float)):
                                        date_str = str(int(date_value))
                                        if len(date_str) == 8:
                                            month = int(date_str[4:6])
                                            day = int(date_str[6:8])
                                            result = f"{month}月{day}日"
                                            self.logger.info(f"备用解析结果: {result}")
                                            return result
                                except Exception as e2:
                                    self.logger.warning(f"备用解析也失败: {e2}")
                    
                    self.logger.warning(f"无法格式化日期: {date_value}")
            
            # 如果没有找到精确匹配，返回None
            self.logger.warning(f"未找到价格 {target_price} 的匹配记录")
            return None
            
        except Exception as e:
            # 如果计算失败，返回None
            self.logger.error(f"计算日期失败: {e}")
            return None
    
    def calculate_key_points(self, stock_code, error_out=None, allow_on_demand_sync=True):
        """计算关键价格点。若传入 error_out（list），失败时将错误信息追加到该列表，供调用方写入运行日志。"""
        self._allow_on_demand_sync = bool(allow_on_demand_sync)
        try:
            # 只打印一次版本指纹，便于确认是否加载了最新文件
            global _KP_VERSION_PRINTED
            if not _KP_VERSION_PRINTED:
                _KP_VERSION_PRINTED = True
                try:
                    fp = os.path.abspath(__file__)
                    mt = datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[KEY_PRICE_CALC_VERSION] {KEY_PRICE_CALC_BUILD} | file={fp} | mtime={mt}")
                except Exception:
                    print(f"[KEY_PRICE_CALC_VERSION] {KEY_PRICE_CALC_BUILD}")

            # 智能判断股票代码格式
            df = None
            error_msg = ""
            
            # 如果已经包含后缀，直接使用
            if '.' in stock_code:
                try:
                    df = self._get_qmt_daily_data(stock_code)
                    if df is not None and not df.empty:
                        pass
                except Exception as e:
                    error_msg += f"直接使用失败: {str(e)}\n"
            else:
                # 根据股票代码特征智能判断后缀
                stock_codes_to_try = []
                
                # 根据股票代码开头判断可能的市场
                if stock_code.startswith(('0', '1', '3')):
                    # 深市股票：000001-099999, 100001-199999, 300001-399999
                    stock_codes_to_try.append(f"{stock_code}.SZ")
                elif stock_code.startswith(('5', '6')):
                    # 沪市股票：500001-599999, 600001-699999
                    stock_codes_to_try.append(f"{stock_code}.SH")
                elif stock_code.startswith(('4', '8', '920')):
                    # 北交所股票：400001-499999, 800001-899999, 920001-920999
                    stock_codes_to_try.append(f"{stock_code}.BJ")
                else:
                    # 如果无法判断，按常见顺序尝试
                    stock_codes_to_try = [f"{stock_code}.SZ", f"{stock_code}.SH", f"{stock_code}.BJ"]
                
                # 尝试智能判断的代码
                for symbol_with_suffix in stock_codes_to_try:
                    try:
                        df = self._get_qmt_daily_data(symbol_with_suffix)
                        if df is not None and not df.empty:
                            days_count = len(df)
                            self.logger.info(f"成功获取股票 {symbol_with_suffix} 的{days_count}天的日线数据")
                            break
                    except Exception as e:
                        error_msg += f"尝试 {symbol_with_suffix} 失败: {str(e)}\n"
                        continue
                
                # 如果智能判断失败，尝试所有可能的后缀
                if df is None or df.empty:
                    all_suffixes = [f"{stock_code}.SZ", f"{stock_code}.SH", f"{stock_code}.BJ"]
                    for suffix_code in all_suffixes:
                        if suffix_code not in stock_codes_to_try:  # 避免重复尝试
                            try:
                                df = self._get_qmt_daily_data(suffix_code)
                                if df is not None and not df.empty:
                                    days_count = len(df)
                                    self.logger.info(f"成功获取股票 {suffix_code} 的{days_count}天的日线数据")
                                    break
                            except Exception as e:
                                error_msg += f"尝试 {suffix_code} 失败: {str(e)}\n"
                                continue
            
            if df is None or df.empty:
                raise Exception(f"无法获取股票 {stock_code} 的数据。尝试的错误信息：{error_msg}")
            
            # 计算技术指标
            df = self._calculate_technical_indicators(df)
            
            # 获取最新数据
            latest_data = df.iloc[-1]
            latest_close = latest_data['close']
            latest_high = latest_data['high']
            latest_low = latest_data['low']
            
            # 判断当前时间和交易日，确定昨收盘价格（北京时间，见 _china_now 说明）
            now = _china_now()
            current_time = now.time()
            today = now.date()
            
            # 判断今天是否是交易日
            try:
                from utils.trading_day import is_tradeday
                is_trading_day = is_tradeday(today)
            except ImportError:
                # 如果没有chncal模块，简单判断工作日
                is_trading_day = today.weekday() < 5
            
            # 判断是否在交易日下午4点后到下一个交易日早上9点半前的时间段
            def is_after_trading_close_to_next_morning():
                """判断是否在交易日下午4点后到下一个交易日早上9点半前"""
                if is_trading_day:
                    # 如果是交易日，检查是否在 REFERENCE_SWITCH_TIME 之后
                    if REFERENCE_SWITCH_TIME <= current_time:
                        return True
                    elif current_time < datetime_time(9, 30):
                        # 交易日的0:00-9:30之间，需要判断前一天是否是交易日
                        yesterday = today - timedelta(days=1)
                        try:
                            from utils.trading_day import is_tradeday
                            is_yesterday_trading_day = is_tradeday(yesterday)
                        except ImportError:
                            # 如果没有chncal模块，简单判断工作日
                            is_yesterday_trading_day = yesterday.weekday() < 5
                        
                        # 如果前一天是交易日，说明今天是交易日的第二天早上，返回True
                        return is_yesterday_trading_day
                    else:
                        # 交易日的9:30～REFERENCE_SWITCH_TIME 前：复盘基准，返回False
                        return False
                else:
                    # 如果是非交易日，整天都算（昨收盘是上一个交易日的收盘价）
                    return True
            
            is_after_close_period = is_after_trading_close_to_next_morning()
            
            # 最新一根 K 线的日期（用于盘中/盘后分支）
            latest_date = df.iloc[-1]['time']
            if hasattr(latest_date, 'date'):
                latest_date = latest_date.date()
            else:
                try:
                    latest_date = pd.to_datetime(latest_date).date()
                except Exception:
                    latest_date = today
            
            is_latest_data_today = latest_date == today
            
            prev_close = float(latest_close)

            # 1) 交易日 9:30 前（含 9:25～9:30 策略生成）：昨收必须是「上一完整交易日」的日线收盘价。
            #    不能直接用 latest_close：当日 K 线往往已是集合竞价价，会出现昨收=今开、跌停板按错基准（如 17.97）。
            #    也不能依赖 is_after_close_period 的 else 分支误用 latest_close（周二～五早盘曾踩坑）。
            if is_trading_day and current_time < datetime_time(9, 30):
                pc = _prev_trading_day_close_from_daily_df(df, today)
                if pc is not None and pc > 0:
                    prev_close = float(pc)
                else:
                    live_pc = _fetch_live_last_close(stock_code)
                    if live_pc > 0:
                        prev_close = float(live_pc)
                    elif not _builtin_price_feed():
                        try:
                            import xtquant.xtdata as xtdata
                            fc = stock_code if "." in stock_code else None
                            if not fc:
                                c6 = (stock_code or "").strip().replace(".", "")
                                if len(c6) < 6:
                                    c6 = c6.zfill(6)
                                fc = f"{c6}.SH" if c6.startswith("6") else f"{c6}.SZ"
                            tmap = xtdata.get_full_tick([fc])
                            if isinstance(tmap, dict) and fc in tmap:
                                tk = tmap[fc]
                                if isinstance(tk, dict):
                                    lp = float(tk.get("lastClose") or tk.get("pre_close") or 0)
                                else:
                                    lp = float(
                                        getattr(tk, "lastClose", None)
                                        or getattr(tk, "pre_close", None)
                                        or 0
                                    )
                                if lp > 0:
                                    prev_close = float(lp)
                        except Exception:
                            pass
            elif is_trading_day and datetime_time(9, 30) <= current_time < REFERENCE_SWITCH_TIME:
                # 旧逻辑在「末日≠今天」时直接用末日收盘当昨收，daily_cache 落后多日会算错
                # （603137：缓存停在 8/13 的 28.98，真实昨收 8/14 的 26.08）
                prev_close = _resolve_intraday_prev_close(
                    df, today, float(latest_close), latest_date, stock_code
                )
            elif is_after_close_period:
                # 收盘后～次日早盘前（不含已在 1 中处理的交易日 9:30 前）
                if is_trading_day and REFERENCE_SWITCH_TIME <= current_time:
                    # 当日 REFERENCE_SWITCH_TIME 后：昨收即当日日线收盘价
                    prev_close = float(latest_close)
                elif is_latest_data_today and len(df) >= 2:
                    prev_close = float(df.iloc[-2]['close'])
                else:
                    prev_close = float(latest_close)
            else:
                prev_close = float(latest_close)

            # 最新价：始终是当前最新的收盘价
            current_price = latest_close
            
            # 根据股票代码判断涨停幅度（沪深主板 ST 自 2026-07-06 起 ±10%）
            try:
                stock_name = self.get_stock_name(stock_code) or ""
            except Exception:
                stock_name = ""

            from utils.limit_ratio import get_limit_multipliers, normalize_stock_code

            limit_up_ratio, limit_down_ratio = get_limit_multipliers(stock_code, stock_name)
            code_norm = normalize_stock_code(stock_code)

            # 诊断开关：打印涨跌停幅度选择过程（便于排查“创业板被当成10%”）
            try:
                dbg_on = os.environ.get("KEY_PRICE_DEBUG", "").strip().lower() in ("1", "true", "yes")
                # 保险：即使没开开关，也对 300303 打一行（只打一只，方便远程截图）
                dbg_hit = code_norm == "300303"
                if dbg_on or dbg_hit:
                    msg = (
                        f"[KEY_PRICE_DEBUG] stock_code={stock_code!r} code_norm={code_norm!r} "
                        f"stock_name={stock_name!r} limit_up_ratio={limit_up_ratio} limit_down_ratio={limit_down_ratio} "
                        f"prev_close={prev_close} env_KEY_PRICE_DEBUG={os.environ.get('KEY_PRICE_DEBUG')!r}"
                    )
                    # 优先打印到控制台（最直观），再尝试 logger
                    print(msg)
                    try:
                        self.logger.info(msg)
                    except Exception:
                        pass
            except Exception:
                pass
            
            # 计算涨停板和跌停板价格（基于昨收盘）
            # A股涨停板价格计算规则：
            # 1. 计算理论价格：昨收盘 × (1 + 涨跌幅比例)
            # 2. 根据股票类型获取最小价格变动单位（通常是0.01元，基金可能是0.001元）
            # 3. 使用标准四舍五入到最小价格单位
            # 注意：不是简单的向上取整，而是标准四舍五入
            
            # 根据股票代码获取正确的价格精度
            try:
                from core.utils.security_type import SecurityTypeUtil
                precision = SecurityTypeUtil.get_price_precision(stock_code)
            except Exception:
                # 如果获取失败，默认使用2位小数
                precision = 2
            
            # 计算原始价格（未四舍五入）
            limit_up_raw = prev_close * limit_up_ratio
            limit_down_raw = prev_close * limit_down_ratio
            
            # A股涨停板价格使用标准四舍五入（不是银行家舍入）
            # Python的round()使用银行家舍入（round half to even），对于10.285会舍入为10.28
            # 股票价格应该使用标准四舍五入，10.285应该舍入为10.29
            import math
            def stock_price_round(value, precision):
                """
                A股价格标准四舍五入函数
                规则：小数部分 >= 0.5 时向上取整，< 0.5 时向下取整
                例如：10.285 -> 10.29, 10.284 -> 10.28
                """
                multiplier = 10 ** precision
                # 使用 math.floor(value * multiplier + 0.5) 实现标准四舍五入
                # 这样可以避免银行家舍入的问题
                return math.floor(value * multiplier + 0.5) / multiplier
            
            limit_up = stock_price_round(limit_up_raw, precision)  # 涨停板
            limit_down = stock_price_round(limit_down_raw, precision)  # 跌停板
            
            # 查找最近的涨停板价格
            recent_limit_up_price = None
            days_since_limit_up = None
            
            # 确定查找范围：根据交易日和时间确定
            if is_trading_day and current_time < REFERENCE_SWITCH_TIME:
                # 切换时刻前：不包含今天，从昨天开始查找
                search_start_index = len(df) - 2
            else:
                # 交易日 REFERENCE_SWITCH_TIME 后或非交易日：从最新数据开始查找
                search_start_index = len(df) - 1
            
            for i in range(search_start_index, -1, -1):  # 从指定位置往前查找
                if i <= 0:  # 确保有前一天的数据
                    break
                    
                row = df.iloc[i]
                high_price = row['high']
                close_price = row['close']
                
                # 判断是否涨停（当天收盘价相比前一天收盘价涨幅达到涨停幅度）
                prev_close_for_limit = df.iloc[i-1]['close']
                if prev_close_for_limit > 0:
                    # 计算涨幅
                    price_change_pct = (close_price - prev_close_for_limit) / prev_close_for_limit
                    # 判断是否涨停（涨幅达到涨停幅度）
                    expected_ratio = limit_up_ratio - 1  # 转换为涨幅比例
                    if price_change_pct >= expected_ratio:
                        recent_limit_up_price = stock_price_round(close_price, precision)
                        # 计算距离天数
                        days_since_limit_up = len(df) - 1 - i
                        break
            
            # 查找最近的跌停板价格
            recent_limit_down_price = None
            days_since_limit_down = None
            
            # 使用相同的查找范围
            for i in range(search_start_index, -1, -1):  # 从指定位置往前查找
                if i <= 0:  # 确保有前一天的数据
                    break
                    
                row = df.iloc[i]
                high_price = row['high']
                close_price = row['close']
                
                # 判断是否跌停（当天收盘价相比前一天收盘价跌幅达到跌停幅度）
                prev_close_for_limit = df.iloc[i-1]['close']
                if prev_close_for_limit > 0:
                    # 计算跌幅
                    price_change_pct = (close_price - prev_close_for_limit) / prev_close_for_limit
                    # 判断是否跌停（跌幅达到跌停幅度）
                    expected_ratio = limit_down_ratio - 1  # 转换为跌幅比例
                    if price_change_pct <= expected_ratio:
                        recent_limit_down_price = stock_price_round(close_price, precision)
                        # 计算距离天数
                        days_since_limit_down = len(df) - 1 - i
                        break
            
            # 获取关键价格点
            key_prices = []
            
            # 添加基础价格点
            key_prices.append(("涨停板", limit_up))
            
            # 判断是否在交易时段（交易日9:00-15:00）
            is_trading_hours = is_trading_day and datetime_time(9, 0) <= current_time <= datetime_time(15, 0)
            
            if is_trading_hours:
                # builtin 且 daily_cache 尚无当日 K（15:35 前）：今开/当日高低由 results.json 实时 tick 提供
                if _builtin_price_feed() and not is_latest_data_today:
                    pass
                else:
                    key_prices.append(("今日最高", stock_price_round(latest_high, precision)))
                    # 今开盘优先用日线 open；若 QMT 当日行 open 为空/0，则回退到 full_tick
                    open_px = None
                    try:
                        o = latest_data.get("open") if hasattr(latest_data, "get") else latest_data["open"]
                        if o is not None and not pd.isna(o) and float(o) > 0:
                            open_px = float(o)
                    except Exception:
                        open_px = None
                    used_open_fallback = False
                    if open_px is None and not _builtin_price_feed():
                        try:
                            import xtquant.xtdata as xtdata
                            fc = stock_code if "." in stock_code else None
                            if not fc:
                                c6 = (stock_code or "").strip().replace(".", "")
                                if len(c6) < 6:
                                    c6 = c6.zfill(6)
                                fc = f"{c6}.SH" if c6.startswith(("5", "6")) else f"{c6}.SZ"
                            tmap = xtdata.get_full_tick([fc])
                            tk = tmap.get(fc) if isinstance(tmap, dict) else None
                            if isinstance(tk, dict):
                                cand = (
                                    tk.get("open")
                                    or tk.get("openPrice")
                                    or tk.get("open_price")
                                    or tk.get("todayOpen")
                                )
                            else:
                                cand = (
                                    getattr(tk, "open", None)
                                    or getattr(tk, "openPrice", None)
                                    or getattr(tk, "open_price", None)
                                    or getattr(tk, "todayOpen", None)
                                )
                            if cand is not None and float(cand) > 0:
                                open_px = float(cand)
                                used_open_fallback = True
                        except Exception:
                            open_px = None
                    global _OPEN_FALLBACK_LOGGED
                    if used_open_fallback and not _OPEN_FALLBACK_LOGGED:
                        _OPEN_FALLBACK_LOGGED = True
                        try:
                            print(
                                f"[OPEN_FALLBACK] code={stock_code} "
                                f"daily_open_missing=True use_full_tick_open={open_px}"
                            )
                        except Exception:
                            pass
                    if open_px is not None and open_px > 0:
                        key_prices.append(("今开盘", stock_price_round(open_px, precision)))
                    key_prices.append(("今日最低", stock_price_round(latest_low, precision)))
            # 非交易时段（含周末）：不返回今开盘，策略侧用 今开盘 or 昨收盘 时自然以昨收盘为基准

            key_prices.append(("最新价", stock_price_round(current_price, precision)))
            key_prices.append(("昨收盘", stock_price_round(prev_close, precision)))
            key_prices.append(("跌停板", limit_down))
            
            # 添加最近的涨停板价格（如果存在）
            if recent_limit_up_price is not None:
                # 根据时间状态调整显示
                if is_trading_day and current_time >= REFERENCE_SWITCH_TIME:
                    # 交易日 REFERENCE_SWITCH_TIME 后：可以显示今天的涨停
                    if days_since_limit_up == 0:
                        limit_up_name = "最近涨停（今天）"
                    elif days_since_limit_up == 1:
                        limit_up_name = "最近涨停（1交易日前）"
                    else:
                        limit_up_name = f"最近涨停（{days_since_limit_up}交易日前）"
                else:
                    # 交易日15点前或非交易日：使用交易日前的表述
                    if days_since_limit_up == 0:
                        limit_up_name = "最近涨停（0交易日前）"
                    elif days_since_limit_up == 1:
                        limit_up_name = "最近涨停（1交易日前）"
                    else:
                        limit_up_name = f"最近涨停（{days_since_limit_up}交易日前）"
                key_prices.append((limit_up_name, recent_limit_up_price))
            
            # 添加最近的跌停板价格（如果存在）
            if recent_limit_down_price is not None:
                # 根据时间状态调整显示
                if is_trading_day and current_time >= REFERENCE_SWITCH_TIME:
                    # 交易日 REFERENCE_SWITCH_TIME 后：可以显示今天的跌停
                    if days_since_limit_down == 0:
                        limit_down_name = "最近跌停（今天）"
                    elif days_since_limit_down == 1:
                        limit_down_name = "最近跌停（1交易日前）"
                    else:
                        limit_down_name = f"最近跌停（{days_since_limit_down}交易日前）"
                else:
                    # 交易日15点前或非交易日：使用交易日前的表述
                    if days_since_limit_down == 0:
                        limit_down_name = "最近跌停（0交易日前）"
                    elif days_since_limit_down == 1:
                        limit_down_name = "最近跌停（1交易日前）"
                    else:
                        limit_down_name = f"最近跌停（{days_since_limit_down}交易日前）"
                key_prices.append((limit_down_name, recent_limit_down_price))
            
            # 计算均线重合价格点（与均线重合的可能的最新价）
            ma_periods = [5, 10, 20, 30, 60, 120]
            for period in ma_periods:
                # 获取当前均线值
                current_ma = latest_data[f'MA{period}']
                if not pd.isna(current_ma):
                    # 计算与均线重合的价格
                    ma_intersection_price = self._calculate_ma_intersection_price(df, period, prev_close, is_trading_day, current_time)
                    if ma_intersection_price is not None:
                        final_price = stock_price_round(ma_intersection_price, precision)
                        key_prices.append((f"{period}日", final_price))
                    else:
                        # 如果无法计算重合价格，显示当前均线值
                        final_price = stock_price_round(current_ma, precision)
                        key_prices.append((f"{period}日", final_price))

            # 上一完整交易日收盘口径真均线（与「N日」重合点并存）
            for period in ma_periods:
                prior_ma = self._prior_close_ma(
                    df, period, is_trading_day, current_time, today
                )
                if prior_ma is not None:
                    key_prices.append(
                        (f"昨MA{period}", stock_price_round(prior_ma, precision))
                    )
            
            # 布林带价格点
            if not pd.isna(latest_data['BOLL_UPPER']):
                key_prices.append(("布林带上轨", stock_price_round(latest_data['BOLL_UPPER'], precision)))
            if not pd.isna(latest_data['BOLL_LOWER']):
                key_prices.append(("布林带下轨", stock_price_round(latest_data['BOLL_LOWER'], precision)))
            
            # 前高（4日）：早盘不含当日 K，取此前 4 个完整交易日最高价
            high_4_px = None
            try:
                if (
                    is_trading_day
                    and current_time < REFERENCE_SWITCH_TIME
                    and len(df) >= 5
                ):
                    high_4_px = float(df["high"].iloc[-5:-1].max())
                elif len(df) >= 4:
                    high_4_px = float(df["high"].iloc[-4:].max())
            except Exception:
                high_4_px = None
            if high_4_px is None and not pd.isna(latest_data.get("HIGH_4")):
                high_4_px = float(latest_data["HIGH_4"])
            if high_4_px is not None and high_4_px > 0:
                key_prices.append(("前高（4日）", stock_price_round(high_4_px, precision)))

            # 前高和前低价格点
            if not pd.isna(latest_data['HIGH_30']):
                # 调试信息：打印使用的列名（已精简）
                # self.logger.info(f"Web版本 - 计算前高日期，使用列名: high")
                # 计算前高的具体日期
                high_30_date = self._calculate_high_low_date(df, 'high', latest_data['HIGH_30'], is_trading_day, current_time)
                if high_30_date is not None:
                    key_prices.append((f"前高（{high_30_date}）", stock_price_round(latest_data['HIGH_30'], precision)))
                else:
                    key_prices.append(("前高", stock_price_round(latest_data['HIGH_30'], precision)))
            
            if not pd.isna(latest_data['LOW_30']):
                # 调试信息：打印使用的列名（已精简）
                # self.logger.info(f"Web版本 - 计算前低日期，使用列名: low")
                # 计算前低的具体日期
                low_30_date = self._calculate_high_low_date(df, 'low', latest_data['LOW_30'], is_trading_day, current_time)
                if low_30_date is not None:
                    key_prices.append((f"前低（{low_30_date}）", stock_price_round(latest_data['LOW_30'], precision)))
                else:
                    key_prices.append(("前低", stock_price_round(latest_data['LOW_30'], precision)))
            
            # 按价格从高到低排序
            # 处理字符串"-"的情况，将其排在最后
            def sort_key(item):
                price = item[1]
                if price == "-":
                    return float('-inf')
                else:
                    # 确保转换为Python原生类型，避免numpy类型比较问题
                    return float(price)
            
            key_prices.sort(key=sort_key, reverse=True)
            
            # 转换为Web格式
            result = []
            for name, price in key_prices:
                # 判断价格点类型
                point_type = 'normal'
                if name == '最新价':
                    point_type = 'current'  # 最新价用蓝色
                elif name == '涨停板':
                    point_type = 'limit_up'  # 涨停板用红色
                elif name == '跌停板':
                    point_type = 'limit_down'  # 跌停板用绿色
                elif price > limit_up:
                    point_type = 'above_limit'  # 高于涨停板用虚线
                elif price < limit_down:
                    point_type = 'below_limit'  # 低于跌停板用虚线
                
                result.append({
                    'name': name,
                    'price': price,
                    'cost_ratio': None,  # Web版本暂时不计算相对成本
                    'type': point_type
                })
            
            return result
            
        except Exception as e:
            msg = f"计算关键价格点失败: {e}"
            self.logger.error(msg)
            if error_out is not None:
                error_out.append(msg)
            return []
