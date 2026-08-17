#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局股票信息管理器
在程序启动时加载一次all_a_stocks.csv，然后全局共享使用
"""

import os
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Any, List


def _code_6_from_qmt_symbol(symbol: str) -> Optional[str]:
    code_6 = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    if len(code_6) < 6:
        return None
    return code_6[:6].zfill(6)


def _normalize_qmt_symbol(symbol: str) -> Optional[str]:
    s = str(symbol or "").strip().upper()
    if not s:
        return None
    if "." in s:
        return s
    code_6 = _code_6_from_qmt_symbol(s)
    if not code_6:
        return None
    if code_6.startswith("6"):
        return f"{code_6}.SH"
    if code_6.startswith(("4", "8")) or code_6.startswith("920"):
        return f"{code_6}.BJ"
    return f"{code_6}.SZ"


def _name_from_instrument_detail(detail: Any) -> str:
    if not isinstance(detail, dict):
        return ""
    if detail.get("InstrumentName"):
        return str(detail["InstrumentName"]).strip()
    for key in (
        "instrumentName",
        "name",
        "Name",
        "SecuAbbr",
        "SecurityName",
        "InstrumentDisplayName",
    ):
        val = detail.get(key)
        if val:
            return str(val).strip()
    return ""


def _list_date_from_instrument_detail(detail: Any) -> str:
    if not isinstance(detail, dict):
        return ""
    for key in ("OpenDate", "openDate", "CreateDate"):
        val = detail.get(key)
        if val is None or val == "":
            continue
        s = str(val).strip()
        if len(s) >= 8 and s[:8].isdigit():
            d = s[:8]
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return s
    return ""


def _fetch_instrument_details_batched(xtdata, symbols: List[str], chunk_size: int = 800) -> Dict[str, Any]:
    """批量获取合约详情；优先 get_instrument_detail_list，失败时分块/逐只回退。"""
    out: Dict[str, Any] = {}
    unique = []
    seen = set()
    for sym in symbols:
        full = _normalize_qmt_symbol(sym)
        if not full or full in seen:
            continue
        seen.add(full)
        unique.append(full)

    fetch_list = getattr(xtdata, "get_instrument_detail_list", None)
    total = len(unique)
    for start in range(0, total, chunk_size):
        chunk = unique[start : start + chunk_size]
        got = False
        if callable(fetch_list):
            try:
                part = fetch_list(chunk) or {}
                if isinstance(part, dict) and part:
                    out.update(part)
                    got = True
            except Exception:
                got = False
        if not got:
            for sym in chunk:
                try:
                    detail = xtdata.get_instrument_detail(sym)
                except Exception:
                    detail = None
                if detail:
                    out[sym] = detail
        done = min(start + chunk_size, total)
        if total > chunk_size:
            print(f"  已获取合约详情 {done}/{total}")
    return out


class StockInfoManager:
    """全局股票信息管理器（单例模式）
    
    名称体系统一以 QMT InstrumentName 为准：
    - 批量缓存来自 QMT 板块（A股 + ETF/基金）写入 all_a_stocks.csv
    - 单次查询缓存未命中时实时调 get_instrument_detail，并写入内存缓存
    """
    
    _instance = None
    _stock_info_cache = None
    _cache_time = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StockInfoManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._runtime_qmt_cache: Dict[str, str] = {}
            self._sector_download_tried = False
            # 自动路径上 ETF 补全只尝试一次，避免每次冷启动都卡在 QMT/akshare
            self._etf_rebuild_attempted = False
            # xtquant 不可用时禁用逐只实时查名（全市场列表会卡死数分钟）
            self._qmt_lookup_disabled = False
            self._csv_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data",
                "all_a_stocks.csv",
            )
            self._load_stock_info()

    @staticmethod
    def _code_6(stock_code: str) -> str:
        """统一成 6 位数字代码（支持 513130.SH / 513130）。"""
        s = str(stock_code or "").strip().upper()
        if not s:
            return ""
        if "." in s:
            s = s.split(".", 1)[0]
        digits = "".join(ch for ch in s if ch.isdigit())
        if not digits:
            return ""
        return digits[:6].zfill(6)

    def _remember_name(self, code_6: str, name: str, list_date: str = "", *, persist: bool = True) -> None:
        if not code_6 or not name or name in ("未知名称", "未知"):
            return
        self._runtime_qmt_cache[code_6] = name
        if self._stock_info_cache is None:
            self._stock_info_cache = {}
        row = self._stock_info_cache.get(code_6) or {}
        prev = str(row.get("证券简称") or "").strip()
        row["证券简称"] = name
        if list_date and not row.get("上市日期"):
            row["上市日期"] = list_date
        elif "上市日期" not in row:
            row["上市日期"] = ""
        self._stock_info_cache[code_6] = row
        # 新股/漏网代码：写回 CSV，避免订单列表再次落成「未知名称」
        if persist and prev != name:
            try:
                self._persist_name_to_csv(code_6, name, str(row.get("上市日期") or ""))
            except Exception:
                pass

    def _persist_name_to_csv(self, code_6: str, name: str, list_date: str = "") -> None:
        path = getattr(self, "_csv_path", "") or ""
        if not path or not code_6 or not name:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = []
        if os.path.isfile(path):
            for encoding in ("utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030"):
                try:
                    rows = pd.read_csv(path, encoding=encoding, dtype=str).fillna("").to_dict("records")
                    break
                except Exception:
                    rows = []
        updated = False
        out = []
        for row in rows:
            code = str(row.get("证券代码") or "").strip().zfill(6)
            if code == code_6:
                row["证券代码"] = code_6
                row["证券简称"] = name
                if list_date and not str(row.get("上市日期") or "").strip():
                    row["上市日期"] = list_date
                updated = True
            out.append(
                {
                    "证券代码": code or str(row.get("证券代码") or "").strip(),
                    "证券简称": str(row.get("证券简称") or "").strip(),
                    "上市日期": str(row.get("上市日期") or "").strip(),
                }
            )
        if not updated:
            out.append({"证券代码": code_6, "证券简称": name, "上市日期": list_date or ""})
        pd.DataFrame(out, columns=["证券代码", "证券简称", "上市日期"]).to_csv(
            path, index=False, encoding="utf-8"
        )

    def _peek_csv_name(self, code_6: str) -> str:
        """内存缓存缺码时，直接读 CSV 补一条（支持运行中热补新股）。"""
        path = getattr(self, "_csv_path", "") or ""
        if not path or not os.path.isfile(path) or not code_6:
            return ""
        try:
            for encoding in ("utf-8", "utf-8-sig", "gbk"):
                try:
                    df = pd.read_csv(path, encoding=encoding, dtype=str)
                    break
                except Exception:
                    df = None
            if df is None or "证券代码" not in df.columns:
                return ""
            codes = df["证券代码"].astype(str).str.zfill(6)
            hit = df.loc[codes == code_6]
            if hit.empty:
                return ""
            row = hit.iloc[0]
            name = str(row.get("证券简称") or "").strip()
            list_date = str(row.get("上市日期") or "").strip()
            if name and name not in ("未知名称", "未知"):
                self._remember_name(code_6, name, list_date, persist=False)
                return name
        except Exception:
            pass
        return ""

    def _try_download_sector_once(self, xtdata) -> None:
        if self._sector_download_tried:
            return
        self._sector_download_tried = True
        if not callable(getattr(xtdata, "download_sector_data", None)):
            return
        try:
            import threading

            done = {"err": None}

            def _worker():
                try:
                    xtdata.download_sector_data()
                except Exception as e:
                    done["err"] = e

            th = threading.Thread(target=_worker, daemon=True)
            th.start()
            th.join(8.0)
        except Exception:
            pass

    def _lookup_qmt_name(self, code_6: str) -> str:
        """实时向 QMT 查 InstrumentName（失败返回空串）。"""
        if not code_6 or self._qmt_lookup_disabled:
            return ""
        cached = self._runtime_qmt_cache.get(code_6)
        if cached:
            return cached
        try:
            import xtquant.xtdata as xtdata

            try:
                xtdata.enable_hello = False
            except Exception:
                pass
            candidates = []
            full = _normalize_qmt_symbol(code_6)
            if full:
                candidates.append(full)
            # ETF/股票常见后缀兜底
            for suf in (".SH", ".SZ", ".BJ"):
                sym = f"{code_6}{suf}"
                if sym not in candidates:
                    candidates.append(sym)

            connect_errors = 0

            def _probe():
                nonlocal connect_errors
                for sym in candidates:
                    try:
                        detail = xtdata.get_instrument_detail(sym)
                    except Exception as e:
                        detail = None
                        msg = str(e or "")
                        if "无法连接" in msg or "xtquant" in msg.lower():
                            connect_errors += 1
                    name = _name_from_instrument_detail(detail)
                    if name:
                        list_date = _list_date_from_instrument_detail(detail)
                        self._remember_name(code_6, name, list_date)
                        return name
                return ""

            name = _probe()
            if name:
                return name
            if connect_errors:
                self._qmt_lookup_disabled = True
                return ""
            # 新股常因本地板块未刷新而查不到：补一次板块下载再试
            self._try_download_sector_once(xtdata)
            return _probe()
        except Exception as e:
            msg = str(e or "")
            if "无法连接" in msg or "xtquant" in msg.lower():
                self._qmt_lookup_disabled = True
        return ""
    
    def _load_stock_info(self):
        """加载股票信息。

        自动路径（选股 GUI 启动等）优先用本地 CSV，禁止同步走 akshare 全量拉网，
        否则 QMT 不可用时界面会卡死数分钟。完整重建请用 refresh_cache()。
        """
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            stocks_file = os.path.join(project_root, "data", "all_a_stocks.csv")
            # 检查缓存是否需要更新（每天更新一次）
            current_time = datetime.now()
            if (self._stock_info_cache is None or 
                self._cache_time is None or
                self._cache_time.date() != current_time.date()):
                need_regen = True
                if os.path.exists(stocks_file):
                    try:
                        mtime_date = datetime.fromtimestamp(os.path.getmtime(stocks_file)).date()
                        need_regen = mtime_date != current_time.date()
                    except Exception:
                        # 只要无法判断 mtime，就走保守：重建一次
                        need_regen = True

                if need_regen:
                    # 已有可用 CSV：自动路径直接沿用，避免启动时同步连 QMT/akshare
                    if os.path.exists(stocks_file):
                        print(
                            f"[!] all_a_stocks.csv 不是今天的数据，启动路径沿用本地文件"
                            f"（避免阻塞 UI）: {stocks_file}"
                        )
                    else:
                        print(
                            f"[!] all_a_stocks.csv 缺失，尝试仅用 QMT 生成"
                            f"（失败不走 akshare）: {stocks_file}"
                        )
                        try:
                            self._create_stock_info_file(
                                stocks_file, allow_akshare=False
                            )
                        except Exception as e:
                            print(
                                f"[!] 生成 all_a_stocks.csv 失败: {e}"
                            )

                if not os.path.exists(stocks_file):
                    self._stock_info_cache = {}
                    self._cache_time = current_time
                    print("[!] all_a_stocks.csv 不存在且重建失败")
                    return

                # 尝试多种编码方式读取CSV文件
                encodings = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'utf-8-sig']
                stocks_df = None
                last_error = None
                for encoding in encodings:
                    try:
                        stocks_df = pd.read_csv(stocks_file, encoding=encoding)
                        break  # 成功读取，跳出循环
                    except (UnicodeDecodeError, UnicodeError) as e:
                        last_error = e
                        continue
                    except Exception as e:
                        last_error = e
                        continue

                if stocks_df is None:
                    print(f"[!] 无法读取股票信息文件，最后错误: {str(last_error)}")
                    self._stock_info_cache = {}
                    self._cache_time = current_time
                    return

                # 标准化股票代码格式
                stocks_df['证券代码_标准化'] = stocks_df['证券代码'].astype(str).str.zfill(6)
                # 创建字典索引，提高查找效率
                self._stock_info_cache = stocks_df.set_index('证券代码_标准化')[['证券简称', '上市日期']].to_dict('index')
                self._cache_time = current_time
                # 旧 CSV 仅含沪深 A 股时缺 ETF：仅用 QMT 补一次；禁止 akshare 阻塞启动
                etf_like = sum(
                    1
                    for c in (self._stock_info_cache or {})
                    if str(c).startswith(("15", "16", "18", "50", "51", "56", "58", "159"))
                )
                if etf_like < 50 and not self._etf_rebuild_attempted:
                    self._etf_rebuild_attempted = True
                    # builtin：选股主路径不依赖 ETF 名称；同步探测 QMT 会拖死启动
                    skip_etf_sync = False
                    try:
                        from utils.qmt_execution_config import get_qmt_mode

                        skip_etf_sync = get_qmt_mode() in ("builtin", "standalone")
                    except Exception:
                        skip_etf_sync = False
                    if skip_etf_sync:
                        # 本机 GUI 不连 MiniQMT：禁用逐只 xtdata 查名，避免首屏再卡十几秒
                        self._qmt_lookup_disabled = True
                        print(
                            f"[!] all_a_stocks.csv 疑似缺少ETF（etf_like={etf_like}）；"
                            f"builtin 模式跳过启动时同步补全（避免卡住）。"
                            f"需要时请手动 refresh_cache()"
                        )
                    else:
                        print(
                            f"[!] all_a_stocks.csv 疑似缺少ETF（etf_like={etf_like}），"
                            f"尝试仅用 QMT 板块补全（失败则跳过，不拉 akshare）..."
                        )
                        try:
                            if self._create_stock_info_file(
                                stocks_file, allow_akshare=False
                            ):
                                stocks_df = pd.read_csv(stocks_file, encoding="utf-8")
                                stocks_df["证券代码_标准化"] = (
                                    stocks_df["证券代码"].astype(str).str.zfill(6)
                                )
                                self._stock_info_cache = stocks_df.set_index(
                                    "证券代码_标准化"
                                )[["证券简称", "上市日期"]].to_dict("index")
                            else:
                                print(
                                    "[!] QMT 不可用，跳过 ETF 补全；"
                                    "选股名称仍可用现有 CSV，需补全时请手动 refresh_cache()"
                                )
                        except Exception as e:
                            print(f"[!] ETF 补全重建失败: {e}")
                print(f"[OK] 全局股票信息管理器已加载，共 {len(self._stock_info_cache)} 只股票")
                    
        except Exception as e:
            print(f"加载股票信息失败: {e}")
            self._stock_info_cache = {}
            self._cache_time = current_time
    
    def get_stock_name(self, stock_code: str) -> str:
        """获取股票名称（优先 CSV/内存缓存，未命中则实时查 QMT InstrumentName）。"""
        try:
            self._load_stock_info()
            code_6 = self._code_6(stock_code)
            if not code_6:
                return "未知名称"

            runtime = self._runtime_qmt_cache.get(code_6)
            if runtime:
                return runtime

            if self._stock_info_cache and code_6 in self._stock_info_cache:
                name = str(self._stock_info_cache[code_6].get("证券简称") or "").strip()
                if name and name not in ("未知名称", "未知"):
                    return name

            # 今日 CSV 漏掉新股时：再读盘一次（外部/热写补录）
            csv_name = self._peek_csv_name(code_6)
            if csv_name:
                return csv_name

            qmt_name = self._lookup_qmt_name(code_6)
            if qmt_name:
                return qmt_name

            return "未知名称"
            
        except Exception as e:
            print(f"获取股票名称失败: {e}")
            return "未知名称"
    
    def get_stock_info(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """获取完整股票信息"""
        try:
            self._load_stock_info()
            code_6 = self._code_6(stock_code)
            if not code_6:
                return None

            name = self.get_stock_name(code_6)
            list_date = ""
            if self._stock_info_cache and code_6 in self._stock_info_cache:
                list_date = str(self._stock_info_cache[code_6].get("上市日期") or "")
            if name and name != "未知名称":
                return {
                    "name": name,
                    "list_date": list_date,
                    "market": self._get_stock_market(code_6),
                }
            return None
            
        except Exception as e:
            print(f"获取股票信息失败: {e}")
            return None
    
    def _get_stock_market(self, stock_code: str) -> str:
        """根据股票代码判断市场"""
        if stock_code.startswith('6') or stock_code.startswith('5'):
            return 'sh'
        elif stock_code.startswith('0'):
            return 'sz'
        elif stock_code.startswith('1'):
            return 'sz'
        elif stock_code.startswith('3'):
            return 'sz_gem'
        elif stock_code.startswith('688'):
            return 'sh_star'
        else:
            return 'unknown'
    
    def get_all_stocks(self) -> Dict[str, Dict[str, Any]]:
        """获取所有股票信息"""
        try:
            # 确保缓存已加载
            self._load_stock_info()
            
            result = {}
            for stock_code, stock_info in self._stock_info_cache.items():
                result[stock_code] = {
                    'name': stock_info['证券简称'],
                    'list_date': stock_info['上市日期'],
                    'market': self._get_stock_market(stock_code)
                }
            
            return result
            
        except Exception as e:
            print(f"获取所有股票信息失败: {e}")
            return {}
    
    def refresh_cache(self):
        """刷新缓存（允许 QMT 失败后走 akshare 全量重建）。"""
        self._stock_info_cache = None
        self._cache_time = None
        self._runtime_qmt_cache = {}
        self._sector_download_tried = False
        self._etf_rebuild_attempted = False
        self._qmt_lookup_disabled = False
        stocks_file = self._csv_path
        try:
            self._create_stock_info_file(stocks_file, allow_akshare=True)
        except Exception as e:
            print(f"[!] refresh_cache 重建失败: {e}")
        self._load_stock_info()
        print("[OK] 股票信息缓存已刷新")
    
    def _create_stock_info_file(self, file_path, allow_akshare: bool = True):
        """自动创建股票信息文件。

        allow_akshare=False：仅尝试 QMT；失败立即返回 False。
        选股 GUI 等自动加载路径必须传 False，避免主线程被 akshare 拉网卡住。
        """
        try:
            # 确保data目录存在
            data_dir = os.path.dirname(file_path)
            os.makedirs(data_dir, exist_ok=True)
            
            print("正在获取股票信息（优先 QMT），请稍候...")

            # 1) 优先：用 QMT 获取“代码+名称”，上市日期留空
            #    获取失败则按 allow_akshare 决定是否回退。
            qmt_ok = False
            try:
                import xtquant.xtdata as _xtdata
                try:
                    _xtdata.enable_hello = False
                except Exception:
                    pass

                # 先刷新本地板块，避免新股（如今日申购）不在 get_stock_list_in_sector 里
                try:
                    self._try_download_sector_once(_xtdata)
                except Exception:
                    pass

                # A股含北交所；名称体系另覆盖常用场内基金
                sector_names = (
                    "沪深A股",
                    "上证A股",
                    "深证A股",
                    "京市A股",
                    "沪深京A股",
                    "沪深ETF",
                    "沪深基金",
                )
                raw = []
                seen_raw = set()
                for sec in sector_names:
                    try:
                        tmp = _xtdata.get_stock_list_in_sector(sec) or []
                    except Exception:
                        tmp = []
                    for item in tmp:
                        key = str(item or "").strip().upper()
                        if key and key not in seen_raw:
                            seen_raw.add(key)
                            raw.append(item)
                    if tmp:
                        print(f"  板块 {sec}: {len(tmp)} 只")

                raw = list(raw or [])
                if raw:
                    symbols = []
                    code_by_symbol = {}
                    for r in raw:
                        full = _normalize_qmt_symbol(r)
                        if not full:
                            continue
                        code_6 = _code_6_from_qmt_symbol(full)
                        if not code_6:
                            continue
                        symbols.append(full)
                        code_by_symbol[full] = code_6

                    print(f"  合计 {len(symbols)} 只，批量获取 QMT InstrumentName...")
                    details = _fetch_instrument_details_batched(_xtdata, symbols)

                    rows = []
                    named_cnt = 0
                    for full, code_6 in code_by_symbol.items():
                        detail = details.get(full)
                        if detail is None and full.upper() != full:
                            detail = details.get(full.upper())
                        name = _name_from_instrument_detail(detail)
                        list_date = _list_date_from_instrument_detail(detail)
                        if name:
                            named_cnt += 1
                        rows.append((code_6, name, list_date))

                    if rows and named_cnt > 0:
                        stocks_df = pd.DataFrame(
                            rows, columns=["证券代码", "证券简称", "上市日期"]
                        )
                        stocks_df = stocks_df.drop_duplicates(subset=["证券代码"], keep="last")
                        stocks_df.to_csv(file_path, index=False, encoding="utf-8")
                        qmt_ok = True
                        print(
                            f"[OK] QMT 批量生成 all_a_stocks.csv 成功："
                            f"{len(stocks_df)} 只（有名称 {named_cnt}）"
                        )
            except Exception as e:
                if allow_akshare:
                    print(f"[!] QMT 生成失败，回退到 akshare: {e}")
                else:
                    print(f"[!] QMT 生成失败（已禁用 akshare 回退）: {e}")

            # 2) 回退：akshare 生成（保留原逻辑：上市日期也会填）
            if not qmt_ok and not allow_akshare:
                return False
            if not qmt_ok:
                print("正在获取股票信息（akshare 备份），请稍候...")
                try:
                    import akshare as ak
                except ImportError:
                    print("错误：未安装akshare库，且 QMT 生成失败，无法自动获取股票信息")
                    return False

                print("正在获取上海证券交易所股票信息...")
                stock_info_sh_name_code_df = ak.stock_info_sh_name_code()
                stock_info_sh = stock_info_sh_name_code_df[['证券代码', '证券简称', '上市日期']].copy()
                stock_info_sh.loc[:, '证券代码'] = stock_info_sh['证券代码'].astype(str)

                print("正在获取科创板股票信息...")
                stock_info_star_name_code_df = ak.stock_info_sh_name_code(symbol="科创板")
                stock_info_star = stock_info_star_name_code_df[['证券代码', '证券简称', '上市日期']].copy()
                stock_info_star.loc[:, '证券代码'] = stock_info_star['证券代码'].astype(str)

                print("正在获取深圳证券交易所股票信息...")
                stock_info_sz_name_code_df = ak.stock_info_sz_name_code()
                stock_info_sz = stock_info_sz_name_code_df[['A股代码', 'A股简称', 'A股上市日期']].copy()
                stock_info_sz.loc[:, 'A股代码'] = stock_info_sz['A股代码'].astype(str)
                stock_info_sz = stock_info_sz.rename(columns={
                    'A股代码': '证券代码',
                    'A股简称': '证券简称',
                    'A股上市日期': '上市日期'
                })

                print("正在获取北京证券交易所股票信息...")
                stock_info_bj_name_code_df = ak.stock_info_bj_name_code()
                stock_info_bj = stock_info_bj_name_code_df[['证券代码', '证券简称', '上市日期']].copy()
                stock_info_bj.loc[:, '证券代码'] = stock_info_bj['证券代码'].astype(str)

                has_920_codes = stock_info_bj['证券代码'].str.startswith('920').any()

                if not has_920_codes:
                    print("[!] 检测到akshare返回的北交所代码仍为旧代码（8开头），尝试转换为920开头...")
                    print("[!] 注意：如果转换后出现代码重复，可能需要使用官方对照表进行准确映射")

                    def convert_bj_code(code):
                        code = str(code).strip()
                        if len(code) == 6 and code.startswith('8') and code.isdigit():
                            new_code = '920' + code[3:]
                            return new_code
                        elif code.startswith('920'):
                            return code
                        else:
                            return code

                    stock_info_bj.loc[:, '证券代码'] = stock_info_bj['证券代码'].apply(convert_bj_code)

                    duplicates = stock_info_bj[stock_info_bj.duplicated(subset=['证券代码'], keep=False)]
                    if not duplicates.empty:
                        print(f"[!] 警告：转换后发现 {len(duplicates)} 个重复代码，可能需要使用官方对照表")
                else:
                    print("[OK] akshare已返回920开头的北交所代码，无需转换")

                print("正在获取ETF基金信息...")
                try:
                    stock_info_jj_name_code_df = ak.fund_etf_spot_em()
                    stock_info_jj = stock_info_jj_name_code_df[['代码', '名称']].copy()
                    stock_info_jj.loc[:, '代码'] = stock_info_jj['代码'].astype(str)
                    stock_info_jj.rename(columns={'代码': '证券代码', '名称': '证券简称'}, inplace=True)
                    stock_info_jj['上市日期'] = ''
                except Exception as e:
                    print(f"获取ETF基金信息失败: {e}，跳过ETF基金")
                    stock_info_jj = pd.DataFrame(columns=['证券代码', '证券简称', '上市日期'])

                print("正在合并股票信息...")
                all_a_stocks = pd.concat([stock_info_sh, stock_info_star, stock_info_sz, stock_info_bj, stock_info_jj])

                print(f"正在写入文件: {file_path}")
                all_a_stocks.to_csv(file_path, index=False, encoding='utf-8')
                print(f"[OK] 股票信息文件创建成功（akshare），共 {len(all_a_stocks)} 只股票")
                return True

            return qmt_ok
            
        except Exception as e:
            print(f"自动创建股票信息文件失败: {e}")
            import traceback
            print(f"错误详情: {traceback.format_exc()}")
            return False

# 全局实例
_stock_info_manager = None

def get_stock_info_manager() -> StockInfoManager:
    """获取全局股票信息管理器实例"""
    global _stock_info_manager
    if _stock_info_manager is None:
        _stock_info_manager = StockInfoManager()
    return _stock_info_manager

def get_stock_name(stock_code: str) -> str:
    """获取股票名称（便捷函数）"""
    return get_stock_info_manager().get_stock_name(stock_code)

def get_stock_info(stock_code: str) -> Optional[Dict[str, Any]]:
    """获取股票信息（便捷函数）"""
    return get_stock_info_manager().get_stock_info(stock_code)

def get_all_stocks() -> Dict[str, Dict[str, Any]]:
    """获取所有股票信息（便捷函数）"""
    return get_stock_info_manager().get_all_stocks()

def refresh_stock_cache():
    """刷新股票信息缓存（便捷函数）"""
    get_stock_info_manager().refresh_cache()
