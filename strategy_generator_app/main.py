import sys
import os

# 优先把「运行目录的上一级 / utils」所在仓库根加入 sys.path（须早于 config、price_provider 等导入）
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from repo_path import ensure_paths

ensure_paths()

# 抑制 Qt 和 log4cplus 的警告信息
os.environ['QT_LOGGING_RULES'] = '*.debug=false;*.warning=false'
os.environ['QT_MESSAGE_PATTERN'] = ''

# 过滤 stderr 中的特定警告
class StderrFilter:
    def __init__(self, original):
        self.original = original
        self.filters = [
            'Untested Windows version',
            'log4cplus:ERROR',
            'No appenders could be found',
            'Please initialize the log4cplus',
        ]
    
    def write(self, text):
        if not any(f in text for f in self.filters):
            self.original.write(text)
    
    def flush(self):
        self.original.flush()

sys.stderr = StderrFilter(sys.stderr)

import re
import io
import csv
import json
import time
import warnings
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import datetime, date, timedelta, time as dt_time
from typing import Optional, Dict, List, Tuple, Any
from collections import defaultdict

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QSizePolicy,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QLabel,
    QToolBar,
    QAction,
    QDialog,
    QInputDialog,
    QLineEdit,
    QCheckBox,
    QMessageBox,
    QMenu,
    QPushButton,
    QFileDialog,
    QAbstractItemView,
    QFormLayout,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QScrollArea,
    QHeaderView,
    QStackedWidget,
    QFrame,
    QGroupBox,
    QPlainTextEdit,
    QProgressDialog,
    QDateEdit,
    QDateTimeEdit,
    QTimeEdit,
    QToolButton,
)
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtGui import QIcon, QFontMetrics, QTextCursor
from PyQt5.QtCore import Qt, QSize, pyqtSignal, QDate, QTime, QDateTime, QTimer

from config.strategy_config import (
    StrategyConfig,
    load_all_strategies,
    load_strategy_by_id,
    save_strategy,
    delete_strategy,
    strategy_from_import_data,
    parse_codes_text,
    _default_strategy_code,
    PARAM_BUY_AMOUNT_PER_STOCK,
    PARAM_MIN_ORDER_AMOUNT,
    strategy_uses_scheduled_clear,
    strategy_uses_positions,
    strip_unwanted_scheduled_clear_intents,
    strip_scheduled_clear_params,
)
from engine import run_strategy as engine_run_strategy
from task_builder import build_task_dict, build_tasks_from_intents, write_tasks_to_excel
from price_provider import get_prices as fetch_prices, get_prices_with_key_points
from account_provider import (
    get_account_info,
    get_positions,
    get_positions_with_volume,
    get_positions_with_volume_debug,
    get_positions_for_backtest,
)
from strategy_runner import run_user_strategy

# 屏蔽 PyQt 与 sip 的兼容性 DeprecationWarning（如 sipPyTypeDict() 提示），避免干扰用户
warnings.filterwarnings(
    "ignore",
    message="sipPyTypeDict\\(\\) is deprecated, the extension module should use sipPyTypeDictRef\\(\\) instead",
    category=DeprecationWarning,
)

def _ensure_repo_root_on_sys_path() -> None:
    """将仓库根目录（含 utils/）置于 sys.path 最前。"""
    from repo_path import ensure_repo_root_on_sys_path

    ensure_repo_root_on_sys_path()


def _get_stock_name_fn():
    """获取主项目的 get_stock_name，失败返回 None"""
    try:
        from strategy_generator_app.backtest.stock_info_loader import get_stock_name_callable
        return get_stock_name_callable()
    except Exception:
        try:
            _ensure_repo_root_on_sys_path()
            from utils.stock_info_manager import get_stock_name
            return get_stock_name
        except Exception:
            return None


def _tick_coverage_summary_fields(result: dict) -> Dict[str, str]:
    """从回测结果提取 tick 覆盖摘要（批量汇总列 / 备注用）。"""
    cov = result.get("tick_coverage") if isinstance(result, dict) else None
    if not isinstance(cov, dict):
        return {
            "tick_ratio": "",
            "missing_count": "",
            "missing_codes": "",
            "intent_tick_ratio": "",
            "intent_missing_codes": "",
        }
    last_pool = cov.get("last_pool") if isinstance(cov.get("last_pool"), dict) else None
    last_intent = cov.get("last_intent") if isinstance(cov.get("last_intent"), dict) else None
    req = int((last_pool or {}).get("requested_count") or 0)
    wt = int((last_pool or {}).get("with_tick_count") or 0)
    missing = list((last_pool or {}).get("missing_codes") or [])
    tick_ratio = f"{wt}/{req}" if req else ""
    intent_ratio = ""
    intent_missing: List[str] = []
    if isinstance(last_intent, dict):
        ir = int(last_intent.get("requested_count") or 0)
        iw = int(last_intent.get("with_tick_count") or 0)
        intent_missing = list(last_intent.get("missing_codes") or [])
        if ir:
            intent_ratio = f"{iw}/{ir}"
    return {
        "tick_ratio": tick_ratio,
        "missing_count": str(len(missing)) if req else "",
        "missing_codes": ",".join(missing),
        "intent_tick_ratio": intent_ratio,
        "intent_missing_codes": ",".join(intent_missing),
    }


def _format_tick_coverage_remark(result: dict) -> str:
    """批量备注中的 tick 覆盖说明。"""
    f = _tick_coverage_summary_fields(result)
    parts: List[str] = []
    if f.get("tick_ratio"):
        tr = f["tick_ratio"]
        mc = f.get("missing_count") or "0"
        if mc == "0" or not f.get("missing_codes"):
            parts.append(f"股票池 tick {tr} 齐全")
        else:
            codes = f["missing_codes"]
            if len(codes) > 80:
                codes = codes[:80] + "…"
            parts.append(f"股票池 tick {tr} 缺{mc}只:{codes}")
    it = f.get("intent_tick_ratio") or ""
    if it:
        imc = f.get("intent_missing_codes") or ""
        if imc:
            if len(imc) > 60:
                imc = imc[:60] + "…"
            parts.append(f"意图 tick {it} 缺:{imc}")
        else:
            parts.append(f"意图 tick {it} 齐全")
    return "；".join(parts)


def _summarize_backtest_diagnosis(result: dict, trade_count: int = 0) -> str:
    """
    从回测结果提炼简短诊断，供批量汇总「备注」列使用。
    区分：无有效行情日 / 缺 tick / 无买卖信号 / 有意图但未成交。
    """
    tc = int(trade_count or 0)
    reasons = list(result.get("failure_reasons") or [])
    days_data = int(result.get("days_with_data") or 0)
    days_zero = int(result.get("days_with_zero_prices") or 0)
    gen_items = result.get("generated_intents") or []
    intent_total = sum(len(g.get("intents") or []) for g in gen_items)

    no_tick_days = sum(
        1
        for r in reasons
        if ("无 tick" in r)
        or ("无tick" in r)
        or ("无 tick 数据" in r)
        or ("缺 tick 数据" in r)
        or ("意图缺 tick" in r and "无法回测" in r)
    )
    no_intent_days = sum(1 for r in reasons if "未产生任何交易意图" in r)
    window_no_fill = sum(1 for r in reasons if "未产生成交" in r and "窗口" in r)
    morning_fail = sum(
        1 for r in reasons
        if ("获取早盘行情失败" in r) or ("价格为 0" in r) or ("未返回数据" in r)
    )

    parts: List[str] = []
    tick_remark = _format_tick_coverage_remark(result)
    if tick_remark:
        parts.append(tick_remark)
    if tc > 0:
        if no_tick_days:
            parts.append(f"部分交易日缺 tick（{no_tick_days} 日，无法回测）")
        return "；".join(parts) if parts else ""

    if days_data == 0:
        if morning_fail or days_zero > 0:
            parts.append("无有效行情日（缺日线/早盘价或 QMT 未就绪）")
        else:
            parts.append("回测窗口内无有效交易日")
    if no_tick_days:
        parts.append(f"缺 tick（{no_tick_days} 日，无法回测）")
    if intent_total == 0 or (no_intent_days > 0 and no_intent_days >= max(days_data, 1)):
        parts.append("策略无买卖信号")
    elif intent_total > 0 or window_no_fill > 0:
        parts.append(f"有意图 {intent_total} 条但未成交")
    if not parts:
        parts.append("未成交（可对该选股日单独「运行回测」查看 failure_reasons 详情）")
    return "；".join(parts)


def _batch_summary_tick_columns(result: dict) -> Dict[str, str]:
    """批量汇总表 tick 相关列。"""
    f = _tick_coverage_summary_fields(result)
    return {
        "tick覆盖(池)": f.get("tick_ratio") or "",
        "缺tick数": f.get("missing_count") or "",
        "缺tick代码": f.get("missing_codes") or "",
        "tick覆盖(意图)": f.get("intent_tick_ratio") or "",
        "意图缺tick": f.get("intent_missing_codes") or "",
    }


def _format_codes_with_names(codes):
    """将代码列表格式化为「代码 名称」多行文本，便于阅读"""
    if not codes:
        return ""
    _get_name = _get_stock_name_fn()
    lines = []
    for code in codes:
        code = (code or "").strip()
        if len(code) < 6:
            code = code.zfill(6)
        if _get_name:
            name = _get_name(code)
            lines.append(f"{code} {name}" if name and name != "未知名称" else code)
        else:
            lines.append(code)
    return "\n".join(lines)


def _code_display_text(code):
    """单只股票的显示文本：代码 名称"""
    code = (code or "").strip()
    if len(code) < 6:
        code = code.zfill(6)
    fn = _get_stock_name_fn()
    if fn:
        name = fn(code)
        return f"{code} {name}" if name and name != "未知名称" else code
    return code


def _normalize_code(s):
    """提取6位股票代码（不足6位左补零，兼容 Excel 数值型 1.0→000001）。"""
    if s is None:
        return ""
    try:
        import pandas as pd

        if pd.isna(s):
            return ""
    except Exception:
        pass
    if isinstance(s, bool):
        return ""
    if isinstance(s, int):
        num_str = str(s)
    elif isinstance(s, float):
        try:
            if s != s:
                return ""
            num_str = str(int(s)) if s == int(s) else ""
        except (OverflowError, ValueError):
            return ""
        if not num_str:
            return ""
    else:
        raw = str(s or "").strip()
        if not raw:
            return ""
        # pandas astype(str) 对整型浮点常变成 "1.0"、"890.0"
        m = re.match(r"^(\d+)\.0+$", raw)
        if m:
            num_str = m.group(1)
        else:
            num_str = re.sub(r"[^\d]", "", raw)
    if not num_str:
        return ""
    if len(num_str) > 6:
        num_str = num_str[:6]
    return num_str.zfill(6)


def _normalize_table_header(s: str) -> str:
    """表头去 BOM/零宽/首尾空白，便于两台机 Excel 导出列名略有差异时仍能匹配。"""
    t = (s or "").replace("\ufeff", "").replace("\u200b", "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _looks_like_cn_listed_code_6(c: str) -> bool:
    """
    过滤从金额、日期等字段误抠出的 6 位数字（_normalize_code 只取数字前 6 位时易误判）。
    覆盖常见 A 股 / 科创 / 北交 / 部分 ETF 前缀；过严时可再放宽。
    """
    if len(c) != 6 or not c.isdigit():
        return False
    prefixes = (
        "000",
        "001",
        "002",
        "003",
        "300",
        "301",
        "600",
        "601",
        "603",
        "605",
        "688",
        "689",
        "500",
        "501",
        "502",
        "505",
        "510",
        "511",
        "512",
        "513",
        "515",
        "516",
        "518",
        "588",
        "560",
        "159",
        "150",
        "151",
        "152",
        "153",
        "155",
        "156",
        "158",
        "920",
        "830",
        "871",
        "430",
        "82",
        "83",
        "87",
        "88",
    )
    if any(c.startswith(p) for p in prefixes):
        return True
    if c[0] == "4" and c[1].isdigit():
        return True
    if c[0] == "8" and c[1].isdigit():
        return True
    return False


def _df_header_pairs(df) -> List[Tuple[Any, str]]:
    """(原始列名或位置索引, 规范化表头字符串)"""
    out: List[Tuple[Any, str]] = []
    for c in df.columns:
        out.append((c, _normalize_table_header(str(c))))
    return out


def _find_column_by_header_candidates(df, candidates: Tuple[str, ...]) -> Optional[Any]:
    """按规范化表头在 df 中找列（仅精确匹配，避免「代码」命中「基金代码」等）。"""
    pairs = _df_header_pairs(df)
    for want in candidates:
        w = _normalize_table_header(want).lower()
        for orig, norm in pairs:
            if norm.lower() == w:
                return orig
    return None


def _stock_code_column_score(series) -> float:
    """该列取值有多少比例像真实 6 位证券代码（用于无标准列名时的兜底）。"""
    try:
        vals = series.dropna().head(800)
    except Exception:
        return 0.0
    if len(vals) == 0:
        return 0.0
    ok = 0
    for v in vals:
        c = _normalize_code(v)
        if _looks_like_cn_listed_code_6(c):
            ok += 1
    return ok / float(len(vals))


def _find_stock_code_column_for_import(df) -> Optional[Any]:
    """
    解析股票代码列：优先显式列名（与批量选股一致），否则选「最像代码」的一列。
    注意：短列名「代码」放在「股票代码」「证券代码」之后，避免误命中其它「代码」列。
    """
    preferred = (
        "股票代码",
        "证券代码",
        "stock_code",
        "stockcode",
        "ticker",
        "ts_code",
        "scode",
        "Symbol",
        "code",
        "Code",
        "代码",
    )
    hit = _find_column_by_header_candidates(df, preferred)
    if hit is not None:
        return hit
    for orig, norm in _df_header_pairs(df):
        if "股票代码" in norm:
            return orig
        if "证券代码" in norm and "名称" not in norm:
            return orig
    best_col, best_score = None, 0.0
    for c in df.columns:
        sc = _stock_code_column_score(df[c])
        if sc > best_score:
            best_score, best_col = sc, c
    if best_col is not None and best_score >= 0.45:
        return best_col
    if len(df.columns) > 0:
        c0 = df.columns[0]
        if _stock_code_column_score(df[c0]) >= 0.55:
            return c0
    return None


def _read_tabular_file(file_path: str, ext: str):
    """Excel / CSV 读成 DataFrame；CSV 多编码尝试。"""
    import pandas as pd

    if ext in (".xlsx", ".xls"):
        return pd.read_excel(file_path, header=0)
    if ext == ".csv":
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                return pd.read_csv(file_path, encoding=enc, header=0)
            except Exception as e:
                last_err = e
        raise ValueError(f"无法读取 CSV（已尝试 utf-8-sig/utf-8/gbk）：{last_err}")
    raise ValueError("不支持的扩展名")


def _normalize_schedule_dt(dt: datetime) -> datetime:
    """预约时间存盘与比较：微秒归零（日期时间与秒由界面或 JSON 决定）。"""
    return dt.replace(microsecond=0)


def _schedule_at_storage_str(dt: datetime) -> str:
    """预约时间写入 JSON：精确到秒（如 2026-04-23T09:25:30），与界面显示一致。"""
    return _normalize_schedule_dt(dt).isoformat(timespec="seconds")


def _parse_schedule_at_storage(s: str) -> datetime:
    """解析预约时间字符串（兼容旧数据仅到分钟），比较前微秒归零。"""
    dt = datetime.fromisoformat((s or "").strip())
    return _normalize_schedule_dt(dt)


def _default_schedule_gen_datetime(from_dt: datetime | None = None) -> datetime:
    """未预约策略的默认定时：尚未到来的最近一个交易日 09:25:10（本地时间）。"""
    try:
        from utils.trading_day import next_tradeday_datetime_at

        return _normalize_schedule_dt(next_tradeday_datetime_at(from_dt=from_dt or datetime.now()))
    except Exception:
        return _normalize_schedule_dt((from_dt or datetime.now()) + timedelta(hours=1))


def _default_schedule_gen_qdatetime(from_dt: datetime | None = None) -> QDateTime:
    dt = _default_schedule_gen_datetime(from_dt)
    return QDateTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _beijing_now_diag_str() -> str:
    """策略预览诊断用时钟：一律用北京时间，避免两台电脑系统时区不同导致对比困难。"""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            import pytz

            return datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（本机本地时区，建议 Windows 设为(UTC+08:00)北京）"


def _preview_price_fields_one_line(code_6: str, p: dict) -> str:
    """单行输出 prices 中突破类策略常用字段（便于两台机复制对比）。"""

    def gv(*keys):
        for key in keys:
            v = p.get(key)
            if v is not None and v != "":
                return v
        return ""

    return (
        f"  {code_6}  昨收={gv('昨收盘', 'pre_close')}  今开={gv('今开盘', 'open')}  "
        f"今日最高={gv('今日最高', 'high')}  今日最低={gv('今日最低', 'low')}  "
        f"最新={gv('最新价', 'current')}  MA5={gv('5日')}  MA10={gv('10日')}  MA20={gv('20日')}  "
        f"MA30={gv('30日')}  MA60={gv('60日')}  MA120={gv('120日')}"
    )


def _diagnose_breakthrough_5day_10m(code_6: str, p: dict, params: dict) -> str:
    """
    与内置「买：突破5日线-延时10m」策略同一套条件，给出第一条未通过原因（便于定位「另一台永远选不出」）。
    """
    tol = 0.003
    amount_per_stock = float(params.get("buy_amount_per_stock", 50000))
    min_order_amount = float(params.get("min_order_amount", 5000))
    half = amount_per_stock / 2.0

    def vol_for(amt, price):
        if price <= 0:
            return 0
        v = max(100, int(amt / price / 100) * 100)
        if v * price < min_order_amount:
            return 0
        return v

    cur = float(p.get("current") or p.get("最新价") or 0)
    pre_close = float(p.get("昨收盘") or p.get("pre_close") or cur or 1)
    ma5, ma10 = p.get("5日"), p.get("10日")
    ma20, ma30, ma60, ma120 = p.get("20日"), p.get("30日"), p.get("60日"), p.get("120日")

    if ma5 is None or ma10 is None:
        return "未通过：缺少 MA5/MA10（5日/10日）"
    if cur <= 0:
        return "未通过：最新价为 0（快照/行情未就绪）"
    ma5f = float(ma5)
    ma10f = float(ma10)
    if ma5f - ma10f < tol * pre_close:
        return (
            f"未通过：5日-10日 不够拉开（差={ma5f - ma10f:.4f}，需≥昨收×0.3%={tol * pre_close:.4f}）"
        )
    others = []
    for x in (ma20, ma30, ma60, ma120):
        if x is not None:
            try:
                others.append(float(x))
            except (TypeError, ValueError):
                pass
    if any(o > ma5f for o in others):
        return f"未通过：更长周期均线有高于 5 日的（others max={max(others):.4f} > MA5={ma5f:.4f}）"

    open_px = float(p.get("今开盘") or p.get("open") or 0)
    high_px = float(p.get("今日最高") or p.get("high") or 0)
    low_px = float(p.get("今日最低") or p.get("low") or 0)
    if open_px <= 0 or high_px <= 0 or low_px <= 0:
        miss = []
        if open_px <= 0:
            miss.append("今开盘")
        if high_px <= 0:
            miss.append("今日最高")
        if low_px <= 0:
            miss.append("今日最低")
        return (
            "未通过："
            + "、".join(miss)
            + " 缺失或为0（常见：本机时区非中国导致关键价计算器未写入今日高低；或 QMT 日线未更新）"
        )

    if not (open_px >= ma10f and open_px <= ma5f):
        return (
            f"未通过：开盘不在[MA10,MA5]内（今开={open_px}, MA10={ma10f:.4f}, MA5={ma5f:.4f}）"
        )
    if low_px <= ma10f:
        return f"未通过：今日最低未高于 MA10（低={low_px}, MA10={ma10f:.4f}）"
    if high_px >= ma5f:
        return f"未通过：今日最高未低于 MA5（高={high_px}, MA5={ma5f:.4f}）"

    try:
        from strategy_runner import _derive_limits_from_prices_row
        ld, lu = _derive_limits_from_prices_row(code_6, p)
    except Exception:
        ld, lu = 0.0, 0.0
    if ld > 0 and lu > 0 and not (ld <= ma5f <= lu):
        return (
            f"未通过：触发价 MA5={ma5f:.2f} 不在当日涨跌停区间"
            f" [{ld:.2f}, {lu:.2f}]（此类股票不应生成任务）"
        )

    v = vol_for(half, ma5f)
    if v <= 0:
        return (
            f"未通过：半额委托股数为 0（half={half:.0f}, MA5价={ma5f}, min_order={min_order_amount}）"
        )
    return "本诊断下条件均已满足：若仍无任务，请核对策略代码是否与内置版本一致"


class PythonCodeEdit(QPlainTextEdit):
    """策略代码编辑框：Tab 插入 4 个空格，制表位 4 字符宽，符合 Python 惯例"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tab_spaces = "    "  # 4 spaces
        self._update_tab_stop()

    def _update_tab_stop(self):
        fm = QFontMetrics(self.font())
        w = fm.width("    ") if hasattr(fm, "width") else fm.horizontalAdvance("    ")
        self.setTabStopDistance(w)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Tab:
            self.insertPlainText(self._tab_spaces)
            return
        if event.key() == Qt.Key_Backtab:
            cursor = self.textCursor()
            cursor.movePosition(cursor.StartOfLine, cursor.MoveAnchor)
            cursor.movePosition(cursor.Right, cursor.KeepAnchor, len(self._tab_spaces))
            if cursor.selectedText() == self._tab_spaces:
                cursor.removeSelectedText()
            self.setTextCursor(cursor)
            return
        super().keyPressEvent(event)


def _parse_codes_from_file(file_path):
    """从 Excel / CSV / TXT 文件中解析出 6 位股票代码列表（去重）。

    说明：旧版对 CSV 会遍历「每个单元格」再 _normalize_code，易把日期、金额等误当成代码；
    另一台机若默认用 GBK/分隔符不同，更容易踩坑。现统一按表头识别「股票代码」列（与批量选股一致）。
    """
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        return []
    ext = os.path.splitext(file_path)[1].lower()
    codes: List[str] = []
    seen = set()

    def add_code(c):
        c = _normalize_code(c)
        if not c or len(c) != 6:
            return
        if not _looks_like_cn_listed_code_6(c):
            return
        if c not in seen:
            seen.add(c)
            codes.append(c)

    if ext in (".xlsx", ".xls", ".csv"):
        try:
            df = _read_tabular_file(file_path, ext)
        except Exception:
            return []
        if df is None or getattr(df, "empty", True):
            return []
        col = _find_stock_code_column_for_import(df)
        if col is None:
            return []
        try:
            for v in df[col].dropna():
                add_code(v)
        except Exception:
            return []
        return codes

    # .txt 或其它：按行、按空白/逗号分割（每段单独判断，避免整行数字串误伤）
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(file_path, "r", encoding=enc) as f:
                for line in f:
                    for part in re.split(r"[\s,，]+", (line or "").strip()):
                        add_code(part)
            break
        except Exception:
            continue
    return codes


def _normalize_code_6(s) -> str:
    """6 位股票代码，供批量选股解析使用"""
    c = _normalize_code(str(s))
    if not c:
        return ""
    c = c[:6]
    return c.zfill(6) if len(c) < 6 else c


def _load_task_buy_dates_by_code(project_root: str, codes: List[str]) -> Dict[str, date]:
    """从当日 current_tasks 文件读取各股 buy_date（推算卖出第几个交易日）。"""
    from task_builder import get_tasks_file_path, _normalize_stock_code

    want = {_normalize_code(c) for c in (codes or []) if _normalize_code(c)}
    out: Dict[str, date] = {}
    if not want:
        return out
    path = get_tasks_file_path(project_root)
    if not os.path.isfile(path):
        return out
    try:
        import pandas as pd

        df = pd.read_excel(path)
    except Exception:
        return out
    for _, row in df.iterrows():
        code_6 = _normalize_stock_code(str(row.get("stock_code") or ""))
        if not code_6 or code_6 not in want or code_6 in out:
            continue
        bd = _parse_cell_to_date(row.get("buy_date"))
        if bd:
            out[code_6] = bd
    return out


def _inject_code_sell_day_index(params: Dict[str, Any], codes: List[str], project_root: str) -> None:
    """
    写入 params['code_sell_day_index']：各股从 buy_date 起第几个卖出交易日（含今日）。
    供「末交易日 14:56 定时清仓」等策略在实盘运行日判定是否生成清仓规则。
    """
    if not params.get("scheduled_clear_on_sell_day") and not params.get("sell_hold_trading_days"):
        return
    try:
        from trading_calendar import first_trading_day_on_or_after, get_trading_dates_in_range_sorted
    except Exception:
        return
    buy_dates = _load_task_buy_dates_by_code(project_root, codes)
    today = date.today()
    out: Dict[str, int] = {}
    for code in codes or []:
        code_6 = _normalize_code(code)
        if not code_6:
            continue
        bd = buy_dates.get(code_6) or today
        start = first_trading_day_on_or_after(bd)
        if not start or today < start:
            out[code_6] = 0
            continue
        lst = get_trading_dates_in_range_sorted(start, today)
        out[code_6] = len(lst) if lst else 0
    params["code_sell_day_index"] = out


def _merge_hold_trading_days_into_params(params: Dict[str, Any], hold_n: int) -> None:
    """用界面「持有交易日数」覆盖末日出清 N（scheduled_clear_on_sell_day）；实盘与回测各自注入。"""
    try:
        n = int(hold_n)
    except (TypeError, ValueError):
        return
    if n < 1:
        return
    params["scheduled_clear_on_sell_day"] = n
    params["sell_hold_trading_days"] = n


def _merge_scheduled_clear_time_into_params(params: Dict[str, Any], time_str: str) -> None:
    """用界面「定时清仓时间」覆盖 scheduled_clear_time（实盘/回测共用注入逻辑）。"""
    norm = _normalize_scheduled_clear_time(time_str)
    if norm:
        params["scheduled_clear_time"] = norm


def _normalize_scheduled_clear_time(s: str) -> Optional[str]:
    """将 14:56 / 14:56:00 规范为 HH:MM:SS。"""
    s = (s or "").strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 2:
            h, m = int(parts[0]), int(parts[1])
            return f"{h:02d}:{m:02d}:00"
        if len(parts) >= 3:
            h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
            return f"{h:02d}:{m:02d}:{sec:02d}"
    except (TypeError, ValueError):
        return None
    return None


def _parse_clear_time_sweep_text(text: str) -> List[str]:
    """解析清仓时间对比列表，支持逗号/分号/空格分隔。"""
    parts = re.split(r"[,;\s]+", (text or "").strip())
    out: List[str] = []
    seen = set()
    for p in parts:
        if not p:
            continue
        norm = _normalize_scheduled_clear_time(p)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _time_str_to_seconds(s: str) -> int:
    norm = _normalize_scheduled_clear_time(s)
    if not norm:
        return -1
    h, m, sec = map(int, norm.split(":"))
    return h * 3600 + m * 60 + sec


def _summarize_scheduled_clear_trades(trades: List[Dict[str, Any]]) -> str:
    clears = [
        t for t in (trades or [])
        if (t.get("rule_type") or "").strip() == "scheduled_clear"
        or "定时清仓" in str(t.get("trigger_info") or "")
    ]
    if not clears:
        return "未成交"
    parts = []
    for t in clears:
        code = (t.get("code") or t.get("stock_code") or "").strip()
        px = t.get("price") or 0
        vol = t.get("volume") or 0
        tm = t.get("time") or ""
        parts.append(f"{code} {tm} @{px}×{vol}")
    if len(parts) > 2:
        return "; ".join(parts[:2]) + f" 等{len(clears)}笔"
    return "; ".join(parts)


def _parse_cell_to_date(val) -> Optional[date]:
    """将 Excel/CSV 单元格转为 date"""
    if val is None:
        return None
    try:
        import pandas as pd
        if pd.isna(val):
            return None
    except Exception:
        pass
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        import pandas as pd
        ts = pd.to_datetime(val, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        pass
    s = str(val).strip().split()[0][:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def group_codes_by_selection_date_from_file(file_path: str) -> Tuple[Dict[date, List[str]], str]:
    """
    从板块筛选等导出的 Excel/CSV 中按「选股日」分组股票代码。
    返回 (按日分组的代码字典, 列说明文案)。
    """
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise ValueError("文件不存在")
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        raise ValueError("请使用 Excel（.xlsx）或 CSV 文件")
    df = _read_tabular_file(file_path, ext)
    if df.empty:
        raise ValueError("表格为空")
    date_candidates = (
        "选股日",
        "screen_as_of",
        "基准日",
        "选股基准日",
        "选股日期",
        "交易日期",
        "trade_date",
    )
    dc = _find_column_by_header_candidates(df, date_candidates)
    if not dc:
        for orig, norm in _df_header_pairs(df):
            if "选股日" in norm or "screen_as_of" in norm.lower():
                dc = orig
                break
    cc = _find_stock_code_column_for_import(df)
    if not dc:
        raise ValueError("未找到日期列（需要「选股日」或 screen_as_of 等列）")
    if not cc:
        raise ValueError("未找到股票代码列（需要「股票代码」等列）")
    by_date: Dict[date, List[str]] = defaultdict(list)
    seen = set()
    for _, row in df.iterrows():
        d = _parse_cell_to_date(row.get(dc))
        code = _normalize_code_6(row.get(cc))
        if not d or not code:
            continue
        key = (d, code)
        if key in seen:
            continue
        seen.add(key)
        by_date[d].append(code)
    if not by_date:
        raise ValueError("没有有效行（日期或代码为空）")
    ordered = dict(sorted(by_date.items()))
    hint = f"日期列「{dc}」，代码列「{cc}」，共 {len(ordered)} 个交易日、{sum(len(v) for v in ordered.values())} 条记录"
    return ordered, hint


def _unique_traded_rows_for_selection_copy(trades: List[dict]) -> List[dict]:
    """从成交明细提取不重复的 (选股日, 代码)，供按选股文件回填全部列。"""
    seen: set = set()
    rows: List[dict] = []
    for t in trades or []:
        sel = str(t.get("选股日") or "").strip()[:10]
        code = _normalize_code_6(t.get("code"))
        if not sel or not code:
            continue
        key = (sel, code)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"选股日": sel, "代码": code})
    return rows


def _batch_excel_summary_rows(
    summary_rows: List[dict], stock_summary_rows: List[dict]
) -> List[dict]:
    """有成交：导出选股文件全部列；无成交：退回按选股日汇总。"""
    return stock_summary_rows if stock_summary_rows else summary_rows


class StrategyEditDialog(QDialog):
    """简单的策略编辑对话框：仅编辑策略名称（其它属性保持不变）"""

    def __init__(self, parent=None, cfg: StrategyConfig = None):
        super().__init__(parent)
        self.setWindowTitle("编辑策略" if cfg else "新增策略")
        self._orig_cfg = cfg

        layout = QVBoxLayout(self)

        self.name_edit = QLineEdit()

        layout.addWidget(QLabel("策略名称："))
        layout.addWidget(self.name_edit)

        btn_layout = QHBoxLayout()
        from PyQt5.QtWidgets import QPushButton

        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        if cfg:
            self.name_edit.setText(cfg.name)

    def get_config(self) -> StrategyConfig:
        name = self.name_edit.text().strip()

        if self._orig_cfg:
            cfg = StrategyConfig(
                id=self._orig_cfg.id,
                name=name or self._orig_cfg.name,
                enabled=self._orig_cfg.enabled,
                stock_codes=list(self._orig_cfg.stock_codes),
                strategy_params=dict(self._orig_cfg.strategy_params or {}),
                strategy_code=self._orig_cfg.strategy_code or "",
            )
        else:
            cfg = StrategyConfig.new_default(name=name or "未命名策略")
        return cfg


class StrategyRowWidget(QWidget):
    """策略列表一行的容器：承载删除/名称/导出，并处理右键「编辑策略名称」。回调在创建时用闭包捕获该行 id，避免误用错行。"""

    def __init__(self, on_rename_callback=None, parent=None):
        super().__init__(parent)
        self.on_rename_callback = on_rename_callback  # 无参 callable，内部已绑定该行 strategy_id

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        rename_act = menu.addAction("编辑策略名称")
        action = menu.exec_(event.globalPos())
        if action == rename_act and self.on_rename_callback:
            self.on_rename_callback()


class StrategyListWidget(QListWidget):
    """带删除、导出图标和右键菜单的策略列表"""

    strategy_delete_requested = pyqtSignal(str)
    strategy_rename_requested = pyqtSignal(str)

    def contextMenuEvent(self, event):
        # 若点击在空白处，用列表坐标取 item 作为兜底（行内右键由 StrategyRowWidget 处理）
        item = self.itemAt(event.pos())
        if not item:
            return
        sid = item.data(Qt.UserRole) or ""
        if not sid:
            return
        menu = QMenu(self)
        rename_act = menu.addAction("编辑策略名称")
        action = menu.exec_(event.globalPos())
        if action == rename_act:
            self.strategy_rename_requested.emit(sid)


class StrategyGeneratorMainWindow(QMainWindow):
    """量化策略生成系统主窗口（框架版 + 策略增删改）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("蚂蚁量化策略生成系统")

        # 尽量与现有系统风格统一：图标共用 ant.ico（若存在）
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        icon_path = os.path.join(root_dir, "ant.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self._strategies = []  # type: ignore[list[StrategyConfig]]

        self._init_toolbar()
        self._init_central()

        self._load_strategies_into_list()
        self._clear_stale_strategy_pool_watch_on_startup()

    def _project_root(self) -> str:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _uses_builtin_pool_watch(self) -> bool:
        try:
            from price_provider import _use_builtin_live_feed

            return bool(_use_builtin_live_feed())
        except Exception:
            return False

    def _clear_stale_strategy_pool_watch_on_startup(self) -> None:
        """进程异常退出后残留的 pool 订阅，启动时释放。"""
        try:
            from utils.strategy_pool_watch import clear_strategy_pool_watch

            n = clear_strategy_pool_watch(root=self._project_root())
            if n > 0:
                print(
                    "[strategy_pool_watch] startup cleared %d stale subscribe codes"
                    % n
                )
        except Exception as e:
            print("[strategy_pool_watch] startup clear failed: %s" % e)

    def _release_strategy_pool_watch_after_run(self) -> None:
        """与 set_strategy_pool_watch 成对：本次「运行」结束即释放临时订阅。"""
        if not self._uses_builtin_pool_watch():
            return
        try:
            from utils.strategy_pool_watch import clear_strategy_pool_watch

            n = clear_strategy_pool_watch(root=self._project_root())
            if n > 0:
                self._append_run_log(
                    "[运行] 已释放 strategy_pool_watch 订阅（%d 只）；大 QMT 将缩订阅。"
                    % n
                )
        except Exception as e:
            self._append_run_log(f"[运行] 释放 strategy_pool_watch 失败: {e}")
        # 竞价窗内释放后立刻再预热，避免下一次 9:25 运行又从冷订阅开始
        try:
            self._maybe_prewarm_strategy_pool_watch(log=False)
        except Exception:
            pass

    def _maybe_prewarm_strategy_pool_watch(self, *, log: bool = True) -> None:
        """
        09:14–09:30：提前写入 strategy_pool_watch，让大 QMT 在集合竞价阶段就订阅。

        只覆盖「当前选中策略」+「今早有预约的策略」，避免把未用的超大股票池全量订阅。
        根因：运行时才写 pool_watch → 等订阅/seed 常超时；一到 9:30 连续竞价才有价。
        """
        if not self._uses_builtin_pool_watch():
            return
        now = _normalize_schedule_dt(datetime.now())
        try:
            from utils.trading_day import is_tradeday

            if not is_tradeday(now.date()):
                return
        except Exception:
            if now.weekday() >= 5:
                return
        t = now.time()
        if not (dt_time(9, 14) <= t < dt_time(9, 30)):
            return

        codes: List[str] = []
        seen = set()

        def _add_pool(raw_codes) -> None:
            for c in raw_codes or []:
                c6 = "".join(ch for ch in str(c or "") if ch.isdigit())[:6].zfill(6)
                if not c6 or c6 == "000000" or c6 in seen:
                    continue
                seen.add(c6)
                codes.append(c6)

        sid = (self._get_selected_strategy_id() or "").strip()
        if sid:
            cfg = self._find_strategy_by_id(sid)
            if cfg:
                _add_pool(cfg.stock_codes)

        for c in self._strategies or []:
            raw = (c.scheduled_generate_at or "").strip()
            if not raw:
                continue
            try:
                dt = _parse_schedule_at_storage(raw)
            except ValueError:
                continue
            if dt.date() != now.date():
                continue
            # 今早预约生成（约 9:20–9:35）才预热
            if not (dt_time(9, 20) <= dt.time() <= dt_time(9, 35)):
                continue
            _add_pool(c.stock_codes)

        if not codes:
            return
        try:
            from utils.strategy_pool_watch import set_strategy_pool_watch

            changed = set_strategy_pool_watch(codes, root=self._project_root())
            if changed and log:
                self._append_run_log(
                    f"[预热订阅] 已写入 strategy_pool_watch {len(codes)} 只"
                    f"（{now.strftime('%H:%M:%S')}，竞价阶段提前订阅）\n"
                )
        except Exception as e:
            if log:
                self._append_run_log(f"[预热订阅] 写入失败: {e}\n")

    def _init_toolbar(self):
        toolbar = QToolBar("策略操作", self)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        add_act = QAction("➕ 新增策略", self)
        add_act.triggered.connect(self._on_add_strategy)
        toolbar.addAction(add_act)
        import_act = QAction("📥 导入策略", self)
        import_act.triggered.connect(self._on_import_strategy)
        toolbar.addAction(import_act)

    def _init_central(self):
        # 主体布局：左侧策略列表 + 右侧详情 Tab
        central = QWidget(self)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 左侧：策略列表（带x删除与右键菜单）
        self.strategy_list = StrategyListWidget()
        self.strategy_list.setMinimumWidth(240)
        # 统一使用白色背景，避免灰色交替行在失去焦点时与选中项的深灰色混淆
        self.strategy_list.setAlternatingRowColors(False)
        self.strategy_list.itemDoubleClicked.connect(self._on_doubleclick_rename)
        self.strategy_list.strategy_delete_requested.connect(
            self._on_delete_strategy_by_id
        )
        self.strategy_list.strategy_rename_requested.connect(
            self._on_rename_strategy_by_id
        )

        # 右侧：未保存提示条 + 详情 Tab
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.unsaved_bar = QWidget()
        unsaved_layout = QHBoxLayout(self.unsaved_bar)
        unsaved_layout.setContentsMargins(8, 4, 8, 4)
        self.unsaved_label = QLabel("当前策略有未保存的修改")
        self.unsaved_save_all_btn = QPushButton("保存全部")
        self.unsaved_save_all_btn.clicked.connect(lambda: self._save_all_edits_impl(quiet=False))
        self.unsaved_discard_btn = QPushButton("放弃修改")
        self.unsaved_discard_btn.clicked.connect(self._on_discard_edits)
        unsaved_layout.addWidget(self.unsaved_label)
        unsaved_layout.addStretch()
        unsaved_layout.addWidget(self.unsaved_save_all_btn)
        unsaved_layout.addWidget(self.unsaved_discard_btn)
        self.unsaved_bar.setStyleSheet("background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 4px;")
        self.unsaved_bar.hide()
        right_layout.addWidget(self.unsaved_bar)

        self.detail_tabs = QTabWidget()

        # 股票池 Tab：列表结构，支持排序、删除
        self.pool_tab_widget = QWidget()
        pool_tab_layout = QVBoxLayout(self.pool_tab_widget)
        pool_tab_layout.setContentsMargins(12, 12, 12, 12)
        pool_tab_layout.addWidget(QLabel("本策略股票池（勾选后点「删除选中的股票」可删除多项）："))
        add_row = QHBoxLayout()
        self.pool_add_edit = QLineEdit()
        self.pool_add_edit.setPlaceholderText("输入股票代码，可多个用逗号或空格分隔")
        # 回车也能触发「添加」，无需再单独点按钮
        self.pool_add_edit.returnPressed.connect(self._on_pool_add_codes)
        self.pool_add_btn = QPushButton("添加")
        self.pool_add_btn.clicked.connect(self._on_pool_add_codes)
        add_row.addWidget(self.pool_add_edit)
        add_row.addWidget(self.pool_add_btn)
        pool_tab_layout.addLayout(add_row)
        self.pool_select_all_cb = QCheckBox("全选")
        self.pool_select_all_cb.stateChanged.connect(self._on_pool_select_all_cb_changed)
        pool_tab_layout.addWidget(self.pool_select_all_cb)
        self.pool_list = QListWidget()
        self.pool_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.pool_list.setAlternatingRowColors(True)
        pool_tab_layout.addWidget(self.pool_list)
        pool_btn_row = QHBoxLayout()
        self.pool_import_btn = QPushButton("导入…")
        self.pool_import_btn.clicked.connect(self._on_import_pool_codes)
        self.pool_import_positions_btn = QPushButton("一键导入持仓")
        self.pool_import_positions_btn.setToolTip("从 QMT 当前持仓导入到本策略股票池（已存在的不会重复添加）")
        self.pool_import_positions_btn.clicked.connect(self._on_import_positions)
        self.pool_sort_btn = QPushButton("按代码排序")
        self.pool_sort_btn.clicked.connect(self._on_pool_sort_by_code)
        self.pool_restore_btn = QPushButton("恢复原始排序")
        self.pool_restore_btn.clicked.connect(self._on_pool_restore_order)
        self.pool_del_batch_btn = QPushButton("删除选中的股票")
        self.pool_del_batch_btn.clicked.connect(self._on_pool_delete_batch)
        self.pool_clear_all_btn = QPushButton("删除所有股票")
        self.pool_clear_all_btn.clicked.connect(self._on_pool_clear_all)
        self.pool_copy_btn = QPushButton("复制")
        self.pool_copy_btn.setToolTip("复制当前策略股票池")
        self.pool_copy_btn.clicked.connect(self._on_pool_copy_codes)
        self.pool_paste_btn = QPushButton("粘贴")
        self.pool_paste_btn.setToolTip("将已复制股票池并入当前策略股票池")
        self.pool_paste_btn.clicked.connect(self._on_pool_paste_codes)
        pool_btn_row.addWidget(self.pool_copy_btn)
        pool_btn_row.addWidget(self.pool_paste_btn)
        pool_btn_row.addWidget(self.pool_import_btn)
        pool_btn_row.addWidget(self.pool_import_positions_btn)
        pool_btn_row.addWidget(self.pool_sort_btn)
        pool_btn_row.addWidget(self.pool_restore_btn)
        pool_btn_row.addWidget(self.pool_del_batch_btn)
        pool_btn_row.addWidget(self.pool_clear_all_btn)
        pool_btn_row.addStretch()
        pool_tab_layout.addLayout(pool_btn_row)
        self.detail_tabs.addTab(self.pool_tab_widget, "股票池")
        self._pool_original_order = []  # 加载策略时的代码顺序，用于「恢复原始排序」

        # 参数与逻辑 Tab：参数与代码放在同一页，减少来回切换
        self.params_logic_tab_widget = QWidget()
        params_layout = QVBoxLayout(self.params_logic_tab_widget)
        params_layout.setContentsMargins(12, 12, 12, 12)
        params_layout.addWidget(QLabel("以下参数会传入策略，无需修改代码即可调整。策略代码中通过 params['buy_amount_per_stock'] 读取。"))
        params_form = QFormLayout()
        self.param_buy_amount_spin = QDoubleSpinBox()
        self.param_buy_amount_spin.setRange(0, 99999999)
        self.param_buy_amount_spin.setDecimals(0)
        self.param_buy_amount_spin.setSuffix(" 元")
        self.param_buy_amount_spin.setValue(50000)
        params_form.addRow("单股拟买入金额：", self.param_buy_amount_spin)
        self.param_min_order_amount_spin = QDoubleSpinBox()
        self.param_min_order_amount_spin.setRange(0, 99999999)
        self.param_min_order_amount_spin.setDecimals(0)
        self.param_min_order_amount_spin.setSuffix(" 元")
        self.param_min_order_amount_spin.setValue(5000)
        params_form.addRow("每笔最小交易金额：", self.param_min_order_amount_spin)
        params_layout.addLayout(params_form)
        params_save_btn = QPushButton("保存参数")
        params_save_btn.clicked.connect(self._on_save_params)
        params_layout.addWidget(params_save_btn)
        params_layout.addSpacing(8)

        # 策略逻辑区：用户编写 Python 代码，程序执行 run(codes, prices, get_name, account, params) 生成任务
        api_label = QLabel(
            "编写 Python 代码，必须定义 run(codes, prices, get_name, account, params)。\n"
            "codes=股票池 6 位代码列表；prices=dict[code]->dict，每只股票含：current, pre_close, 昨收盘, 最新价, "
            "涨停板, 跌停板, 今开盘, 今日最高, 今日最低, 5日/10日/20日/30日/60日/120日(均线重合点), 布林带上轨, 布林带下轨, 前高, 前低 等。\n"
            "get_name(code) 取名称；account={\"total_asset\": 总资金, \"cash\": 可用资金}；params 为「策略参数」Tab 中配置（如 buy_amount_per_stock、min_order_amount），无需改代码即可调整。\n"
            "返回 list[dict]，每个 dict 为一条意图：含 stock_code, stock_name, rule_type，及规则参数（如 price/volume、trigger_price/rise_percent 等）。"
        )
        api_label.setWordWrap(True)
        params_layout.addWidget(api_label)
        self.logic_code_edit = PythonCodeEdit()
        self.logic_code_edit.setPlaceholderText("定义 run(codes, prices, get_name, account) 返回意图列表…")
        self.logic_code_edit.setMinimumHeight(280)
        params_layout.addWidget(self.logic_code_edit)
        logic_btn = QPushButton("保存逻辑")
        logic_btn.clicked.connect(self._on_save_logic_params)
        params_layout.addWidget(logic_btn)
        params_layout.addStretch()
        self.detail_tabs.addTab(self.params_logic_tab_widget, "参数与逻辑")

        # 可编辑区块变更时标记未保存（用于 Tab 星号与提示条）
        self.param_buy_amount_spin.valueChanged.connect(lambda: self._mark_dirty("params"))
        self.param_min_order_amount_spin.valueChanged.connect(lambda: self._mark_dirty("params"))
        self.logic_code_edit.textChanged.connect(lambda: self._mark_dirty("logic"))

        # 生成策略 Tab：运行 + 输出 + 结果表格
        self.preview_tab_widget = QWidget()
        preview_layout = QVBoxLayout(self.preview_tab_widget)
        preview_layout.setContentsMargins(12, 12, 12, 12)

        # 运行输出文本框：显示策略运行过程中的 print 输出
        self.run_output_edit = QPlainTextEdit()
        self.run_output_edit.setReadOnly(True)
        self.run_output_edit.setPlaceholderText("运行日志将在这里显示（来自策略代码中的 print 输出）…")
        self.run_output_edit.setMinimumHeight(120)
        preview_layout.addWidget(self.run_output_edit)

        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(5)
        self.preview_table.setHorizontalHeaderLabels(["股票代码", "股票名称", "规则", "价格", "数量"])
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        preview_layout.addWidget(self.preview_table)
        preview_btn_row = QHBoxLayout()
        self.live_hold_days_label = QLabel("持有交易日数：")
        self.live_hold_trading_days_spin = QSpinBox()
        self.live_hold_trading_days_spin.setRange(1, 120)
        self.live_hold_trading_days_spin.setValue(1)
        self.live_hold_trading_days_spin.setToolTip(
            "仅对会生成 scheduled_clear 的卖出策略生效（策略代码含 rule_type: scheduled_clear）。"
            "买入策略（名称以「买」开头）不会注入、也不会生成定时清仓。"
        )
        preview_btn_row.addWidget(self.live_hold_days_label)
        preview_btn_row.addWidget(self.live_hold_trading_days_spin)
        self.live_clear_label = QLabel("定时清仓：")
        self.live_scheduled_clear_time = QTimeEdit()
        self.live_scheduled_clear_time.setDisplayFormat("HH:mm:ss")
        self.live_scheduled_clear_time.setTime(QTime(14, 56, 0))
        self.live_scheduled_clear_time.setToolTip(
            "仅卖出策略（代码中生成 scheduled_clear 规则）使用；买入策略不显示、不注入。"
        )
        preview_btn_row.addWidget(self.live_clear_label)
        preview_btn_row.addWidget(self.live_scheduled_clear_time)
        self.preview_btn = QPushButton("运行")
        self.preview_btn.clicked.connect(lambda: self._on_preview_tasks(quiet=False))
        self.export_tasks_btn = QPushButton("生成任务")
        self.export_tasks_btn.clicked.connect(lambda: self._on_export_tasks(quiet=False))
        preview_btn_row.addWidget(self.preview_btn)
        preview_btn_row.addWidget(self.export_tasks_btn)
        preview_btn_row.addStretch()
        preview_layout.addLayout(preview_btn_row)
        self._preview_task_list = []  # 当前预览生成的任务 dict 列表，供「生成任务」写入
        # 按策略缓存 UI 运行结果（预览/回测等），切换策略时恢复显示
        self._strategy_ui_state = {}
        # 各策略最近一次成功回测的可导出快照（strategy_id -> dict，version=1 单段格式）
        self._backtest_export_by_strategy = {}
        # 最近一次「批量回测(选股文件)」的完整多段快照（version=2），供导出 JSON；与策略 id 绑定
        self._last_batch_export_bundle = None  # type: Optional[dict]
        self._last_batch_selection_file = None  # type: Optional[str]
        self._last_batch_bundle_strategy_id = None  # type: Optional[str]
        # 载入「上一轮批量回测」后的全部档位（供策略 B 下一轮接续，无需再选手动选股日）
        self._chained_batch_segments: Optional[List[dict]] = None
        # 回测成交明细原始数据（避免切换策略时逐格读写表格）
        self._backtest_trades_data: List[dict] = []
        # 股票池跨策略复制/粘贴缓存（仅在当前程序运行期有效）
        self._pool_clipboard_codes = []
        self._pool_clipboard_source_name = ""
        # 未保存修改追踪：四个可编辑区块（备注、股票池、策略参数、策略逻辑）
        self._loading_strategy = False
        self._dirty_sections = set()  # "pool", "params", "logic"
        self._last_saved_snapshot = None  # 上次加载或保存时的表单快照
        self._tab_index_basic = 0
        self._tab_index_pool = 1
        self._tab_index_params_logic = 2
        self.detail_tabs.addTab(self.preview_tab_widget, "生成策略")

        # 定时生成：到点串行执行「保存全部 + 运行 + 生成任务」（多策略同时到期按预约时间先后执行）
        self._schedule_runner_busy = False
        self.schedule_group = QGroupBox(
            "定时生成（到点自动「保存全部 → 运行 → 生成任务」；多策略同时到期时按顺序逐个执行；需保持本程序运行）"
        )
        _sched_layout = QVBoxLayout(self.schedule_group)
        _sched_hint = QLabel(
            "预约时间保存到该策略配置文件。到点时会先写入当前表单中的股票池、参数与逻辑，再拉行情执行策略并写入任务表。"
            "若与其他策略约在同一时刻，将按时间先后排队，同一时间则按策略 id 排序，不会并行执行。"
        )
        _sched_hint.setWordWrap(True)
        _sched_layout.addWidget(_sched_hint)
        _sched_row1 = QHBoxLayout()
        _sched_row1.addWidget(QLabel("下次运行时刻（本地时间，精确到秒）："))
        self.schedule_gen_datetime = QDateTimeEdit()
        self.schedule_gen_datetime.setCalendarPopup(True)
        self.schedule_gen_datetime.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.schedule_gen_datetime.setDateTime(_default_schedule_gen_qdatetime())
        _sched_row1.addWidget(self.schedule_gen_datetime, 1)
        _sched_layout.addLayout(_sched_row1)
        _sched_row2 = QHBoxLayout()
        self.schedule_save_btn = QPushButton("保存预约")
        self.schedule_save_btn.clicked.connect(self._on_schedule_save_clicked)
        self.schedule_clear_btn = QPushButton("清除预约")
        self.schedule_clear_btn.clicked.connect(self._on_schedule_clear_clicked)
        _sched_row2.addWidget(self.schedule_save_btn)
        _sched_row2.addWidget(self.schedule_clear_btn)
        _sched_row2.addStretch()
        _sched_layout.addLayout(_sched_row2)
        self.schedule_status_label = QLabel("请先选择策略")
        _sched_layout.addWidget(self.schedule_status_label)
        preview_layout.insertWidget(0, self.schedule_group)

        self._schedule_timer = QTimer(self)
        self._schedule_timer.timeout.connect(self._on_schedule_tick)
        self._schedule_timer.start(1000)

        # 回测 Tab：选择当前策略，设置回测区间与初始资金，基于 tick 数据运行回测并展示结果
        # 内容较长：用滚动容器避免窗口较小时控件被压扁看不清
        self.backtest_tab_widget = QScrollArea()
        self.backtest_tab_widget.setWidgetResizable(True)
        self.backtest_tab_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        _backtest_inner = QWidget()
        self.backtest_tab_widget.setWidget(_backtest_inner)
        backtest_layout = QVBoxLayout(_backtest_inner)
        backtest_layout.setContentsMargins(12, 12, 12, 12)
        _backtest_desc = QLabel(
            "对当前选中的策略进行历史回测（基于 tick 数据模拟成交）。股票池以左侧当前策略为准；"
            "可选「分时段组合回测」在同一账户内串联第二套策略（见下方分组）。请先保存策略与股票池。\n"
            "结果：单次回测见下方摘要与成交表；批量回测另有汇总表，可保存 Excel。"
            "「导出上次回测结果」与「导出批量回测(JSON)」保存 JSON；"
            "策略 A 批量→策略 B 接续时优先用「载入上一轮批量回测→本策略」，亦可「从回测导出文件导入」（批量会先选档）。"
        )
        _backtest_desc.setWordWrap(True)
        backtest_layout.addWidget(_backtest_desc)
        self.backtest_mode_hint_label = QLabel("")
        self.backtest_mode_hint_label.setWordWrap(True)
        self.backtest_mode_hint_label.setStyleSheet("color: #555;")
        backtest_layout.addWidget(self.backtest_mode_hint_label)
        self._refresh_backtest_mode_hint()
        backtest_form = QFormLayout()
        self.backtest_start_date = QDateEdit()
        self.backtest_start_date.setCalendarPopup(True)
        # 默认起止日期均为上一交易日
        _today = date.today()
        _d = _today
        for _ in range(7):
            _d -= timedelta(days=1)
            if _d.weekday() < 5:  # 0=周一..4=周五
                break
        _prev_trade = _d
        self.backtest_start_date.setDate(QDate(_prev_trade.year, _prev_trade.month, _prev_trade.day))
        self.backtest_end_date = QDateEdit()
        self.backtest_end_date.setCalendarPopup(True)
        self.backtest_end_date.setDate(QDate(_prev_trade.year, _prev_trade.month, _prev_trade.day))
        backtest_form.addRow("回测开始日期：", self.backtest_start_date)
        backtest_form.addRow("回测结束日期：", self.backtest_end_date)
        self.backtest_initial_cash = QDoubleSpinBox()
        self.backtest_initial_cash.setRange(1000, 999999999)
        self.backtest_initial_cash.setDecimals(0)
        self.backtest_initial_cash.setSuffix(" 元")
        self.backtest_initial_cash.setValue(1000000)
        backtest_form.addRow("初始资金：", self.backtest_initial_cash)
        # 初始持仓：支持「资金+股票」一起回测，仅限当前策略股票池内的标的
        backtest_layout.addWidget(QLabel("初始持仓（可选）：回测开始时除初始资金外已有的股票，仅限当前策略股票池内的标的；用于「资金+持仓」回测。"))
        self.backtest_init_positions_table = QTableWidget()
        self.backtest_init_positions_table.setColumnCount(3)
        self.backtest_init_positions_table.setHorizontalHeaderLabels(["股票代码", "数量", "成本(元)"])
        # 允许窗口变窄时表格随之收缩，避免撑出横向滚动
        self.backtest_init_positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.backtest_init_positions_table.setMaximumHeight(140)
        self.backtest_init_positions_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        backtest_pos_btn_row = QHBoxLayout()
        self.backtest_pos_add_btn = QPushButton("添加一行")
        self.backtest_pos_add_btn.clicked.connect(self._on_backtest_add_init_position_row)
        self.backtest_pos_remove_btn = QPushButton("删除选中")
        self.backtest_pos_remove_btn.clicked.connect(self._on_backtest_remove_init_position_rows)
        self.backtest_pos_clear_all_btn = QPushButton("全部删除")
        self.backtest_pos_clear_all_btn.setToolTip("清空初始持仓表中的所有记录")
        self.backtest_pos_clear_all_btn.clicked.connect(self._on_backtest_clear_init_positions)
        self.backtest_pos_import_btn = QPushButton("从当前持仓导入")
        self.backtest_pos_import_btn.setToolTip("从 QMT 当前持仓导入代码、数量与成本；仅导入当前策略股票池内的标的。")
        self.backtest_pos_import_btn.clicked.connect(self._on_backtest_import_positions)
        self.backtest_pos_import_pool_btn = QPushButton("从股票池导入")
        self.backtest_pos_import_pool_btn.setToolTip("将当前策略股票池中所有股票加入初始持仓，数量预设 1000、成本预设 10 元。")
        self.backtest_pos_import_pool_btn.clicked.connect(self._on_backtest_import_from_pool)
        backtest_pos_btn_row.addWidget(self.backtest_pos_add_btn)
        backtest_pos_btn_row.addWidget(self.backtest_pos_remove_btn)
        backtest_pos_btn_row.addWidget(self.backtest_pos_clear_all_btn)
        backtest_pos_btn_row.addWidget(self.backtest_pos_import_btn)
        backtest_pos_btn_row.addWidget(self.backtest_pos_import_pool_btn)
        backtest_pos_btn_row.addStretch()
        backtest_layout.addWidget(self.backtest_init_positions_table)
        backtest_layout.addLayout(backtest_pos_btn_row)
        # 回测导出/导入：策略 A 回测结束后导出，切换到策略 B 后导入期末现金+持仓再继续回测
        backtest_export_row = QHBoxLayout()
        self.backtest_import_export_btn = QPushButton("从回测导出文件导入并同步到股票池…")
        self.backtest_import_export_btn.setMinimumWidth(0)
        self.backtest_import_export_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.backtest_import_export_btn.setToolTip(
            "选择「导出上次回测结果」或「导出批量回测(JSON)」保存的 JSON："
            "将期末现金与持仓填入初始资金与初始持仓表，并同步持仓代码到股票池。"
            "若为批量 JSON（version=2），会先让您选择要导入哪一档选股日。"
            "若要从策略 A 的批量结果接续到策略 B，更推荐用下一行「载入上一轮批量回测→本策略」。"
        )
        self.backtest_import_export_btn.clicked.connect(self._on_import_from_backtest_export_file)
        self.backtest_init_to_pool_btn = QPushButton("同步到股票池")
        self.backtest_init_to_pool_btn.setMinimumWidth(0)
        self.backtest_init_to_pool_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.backtest_init_to_pool_btn.setToolTip(
            "将上方「初始持仓」表中的股票代码合并到当前策略股票池（已存在的不会重复添加）"
        )
        self.backtest_init_to_pool_btn.clicked.connect(self._on_add_init_positions_to_pool)
        backtest_export_row.addWidget(self.backtest_import_export_btn)
        backtest_export_row.addWidget(self.backtest_init_to_pool_btn)
        backtest_export_row.addStretch()
        backtest_layout.addLayout(backtest_export_row)
        backtest_chain_row = QHBoxLayout()
        self.backtest_load_buy_batch_btn = QPushButton("载入上一轮批量回测→本策略…")
        self.backtest_load_buy_batch_btn.setMinimumWidth(0)
        self.backtest_load_buy_batch_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.backtest_load_buy_batch_btn.setToolTip(
            "通用流程：用选股程序生成多日选股结果 → 在策略 A 下跑「批量回测(选股文件)」得到多档结果 →\n"
            "左侧切换到策略 B，点本按钮：一次性载入**全部**档位（每档含选股日、期末资金与持仓）；\n"
            "与首轮批量一样不改写本策略股票池。再在下方「下一轮接续」设置区间规则，点「运行下一轮批量回测」。"
        )
        self.backtest_load_buy_batch_btn.clicked.connect(self._on_load_buy_batch_into_current_strategy)
        backtest_chain_row.addWidget(self.backtest_load_buy_batch_btn)
        backtest_chain_row.addStretch()
        backtest_layout.addLayout(backtest_chain_row)
        sell_chain_row = QHBoxLayout()
        sell_chain_row.addWidget(QLabel("下一轮接续（需先载入上一轮批量）："))
        self.sell_chain_from_t1_cb = QCheckBox("起算：从上一轮结束后的下一交易日起（避免重叠）")
        self.sell_chain_from_t1_cb.setChecked(True)
        self.sell_chain_from_t1_cb.setToolTip(
            "说明：本轮区间会先按选股日规则计算（T 或 T+1 起算），\n"
            "但若与上一轮回测区间重叠，会自动顺延到「上一轮结束后的下一交易日」。\n"
            "此项勾选时：选股日起算按 T+1；不勾选：按 T 当日。"
        )
        self.sell_chain_hold_spin = QSpinBox()
        self.sell_chain_hold_spin.setRange(1, 120)
        self.sell_chain_hold_spin.setValue(2)
        self.sell_chain_hold_spin.setToolTip(
            "下一轮回测区间的长度：从本轮回测「起始交易日」起，连续模拟的交易日个数（含首尾），"
            "与首轮「批量回测」里「持有交易日数」含义相同。"
            "若按选股日算出的起点仍落在上轮区间内，会自动顺延到上轮结束日的下一交易日，再数满这么多天。"
        )
        self.sell_chain_run_btn = QPushButton("运行下一轮批量回测")
        self.sell_chain_run_btn.setEnabled(False)
        self.sell_chain_run_btn.setToolTip(
            "在「载入上一轮批量回测→本策略」之后可用：按每一档选股日，"
            "以该档期末资金+持仓为本轮初始状态逐档回测。"
            "未勾选「分时段组合」时仅用左侧当前策略；勾选时与单次回测相同：时段1=当前策略，时段2=下方所选策略。"
        )
        self.sell_chain_run_btn.clicked.connect(self._on_run_chained_batch_backtest)
        sell_chain_row.addWidget(self.sell_chain_from_t1_cb)
        sell_chain_row.addWidget(QLabel("持有交易日数："))
        sell_chain_row.addWidget(self.sell_chain_hold_spin)
        self.sell_chain_clear_label = QLabel("定时清仓：")
        self.sell_chain_scheduled_clear_time = QTimeEdit()
        self.sell_chain_scheduled_clear_time.setDisplayFormat("HH:mm:ss")
        self.sell_chain_scheduled_clear_time.setTime(QTime(14, 56, 0))
        self.sell_chain_scheduled_clear_time.setToolTip(
            "下一轮接续（卖出策略）末交易日定时清仓时刻，如 14:56:00。"
            "仅「运行下一轮批量回测」时写入 scheduled_clear_time，首轮批量回测不受影响。"
            "须不晚于上方「策略运行结束时间」。"
        )
        sell_chain_row.addWidget(self.sell_chain_clear_label)
        sell_chain_row.addWidget(self.sell_chain_scheduled_clear_time)
        sell_chain_row.addWidget(self.sell_chain_run_btn)
        sell_chain_row.addStretch()
        backtest_layout.addLayout(sell_chain_row)
        sell_chain_clear_sweep_row = QHBoxLayout()
        self.backtest_clear_time_sweep_edit = QLineEdit()
        self.backtest_clear_time_sweep_edit.setPlaceholderText("14:50,14:55,14:56,15:00")
        self.backtest_clear_time_sweep_edit.setText("14:50,14:55,14:56,15:00")
        self.backtest_clear_time_sweep_edit.setToolTip(
            "接续卖出策略：一次对比多个末日出清时间（逗号/空格分隔）。"
            "使用上方单次回测起止日期与「定时清仓」旁的设置；清仓时间须不晚于策略运行结束时间。"
        )
        self.backtest_clear_time_sweep_btn = QPushButton("清仓时间对比")
        self.backtest_clear_time_sweep_btn.setToolTip(
            "对比多个清仓时间点（接续/卖出策略调参用）。"
            "仍以「运行回测」的日期区间为准；日常接续请用「定时清仓」+「运行下一轮批量回测」。"
        )
        self.backtest_clear_time_sweep_btn.clicked.connect(self._on_backtest_clear_time_sweep)
        self.sell_chain_sweep_label = QLabel("接续·清仓时间对比：")
        sell_chain_clear_sweep_row.addWidget(self.sell_chain_sweep_label)
        sell_chain_clear_sweep_row.addWidget(self.backtest_clear_time_sweep_edit, 1)
        sell_chain_clear_sweep_row.addWidget(self.backtest_clear_time_sweep_btn)
        sell_chain_clear_sweep_row.addStretch()
        backtest_layout.addLayout(sell_chain_clear_sweep_row)
        # 可选：同日第二段策略（与时段1 共用当前 Tab 股票池与资金/初始持仓）
        self.backtest_dual_group = QGroupBox("分时段组合回测（可选）")
        dual_inner = QVBoxLayout(self.backtest_dual_group)
        self.backtest_dual_cb = QCheckBox(
            "启用第二时段策略：同一股票池、同一账户内按顺序先跑「时段1」再跑「时段2」（时段2从时段1结束时刻开始）"
        )
        self.backtest_dual_cb.stateChanged.connect(self._on_backtest_dual_toggled)
        dual_inner.addWidget(self.backtest_dual_cb)
        self.backtest_dual_carry_cb = QCheckBox(
            "继续保留第一时段的任务执行状态：未成交继续，已成交不再重复（仅 tick 回测）"
        )
        self.backtest_dual_carry_cb.setEnabled(False)
        dual_inner.addWidget(self.backtest_dual_carry_cb)
        self.backtest_seg2_panel = QWidget()
        seg2_form = QFormLayout(self.backtest_seg2_panel)
        self.backtest_seg2_combo = QComboBox()
        # 允许窗口变窄时下拉框同步收缩
        self.backtest_seg2_combo.setMinimumWidth(0)
        self.backtest_seg2_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        seg2_form.addRow("第二段策略：", self.backtest_seg2_combo)
        self.backtest_seg2_generation_time = QTimeEdit()
        self.backtest_seg2_generation_time.setDisplayFormat("HH:mm")
        self.backtest_seg2_generation_time.setTime(QTime(9, 40, 0))
        seg2_form.addRow("时段2 策略生成时间：", self.backtest_seg2_generation_time)
        self.backtest_seg2_run_start = QTimeEdit()
        self.backtest_seg2_run_start.setDisplayFormat("HH:mm")
        self.backtest_seg2_run_start.setTime(QTime(9, 40, 0))
        seg2_form.addRow("时段2 运行开始：", self.backtest_seg2_run_start)
        self.backtest_seg2_run_end = QTimeEdit()
        self.backtest_seg2_run_end.setDisplayFormat("HH:mm")
        self.backtest_seg2_run_end.setTime(QTime(14, 57, 0))
        seg2_form.addRow("时段2 运行结束：", self.backtest_seg2_run_end)
        dual_inner.addWidget(self.backtest_seg2_panel)
        self.backtest_seg2_panel.setVisible(False)
        backtest_layout.addWidget(self.backtest_dual_group)
        self.backtest_seg1_title = QLabel("时段1（左侧列表当前策略）— 时间与运行窗口")
        self.backtest_seg1_title.setVisible(False)
        backtest_layout.addWidget(self.backtest_seg1_title)
        self.backtest_generation_time = QTimeEdit()
        self.backtest_generation_time.setDisplayFormat("HH:mm")
        self.backtest_generation_time.setTime(QTime(9, 26, 0))
        backtest_form.addRow("策略生成时间：", self.backtest_generation_time)
        self.backtest_run_start_time = QTimeEdit()
        self.backtest_run_start_time.setDisplayFormat("HH:mm")
        self.backtest_run_start_time.setTime(QTime(9, 30, 0))
        backtest_form.addRow("策略运行开始时间：", self.backtest_run_start_time)
        self.backtest_run_end_time = QTimeEdit()
        self.backtest_run_end_time.setDisplayFormat("HH:mm")
        self.backtest_run_end_time.setTime(QTime(14, 57, 0))
        self.backtest_run_end_time.setToolTip(
            "与实盘一致：连续竞价撮合至 14:57（不含 14:57 起尾盘集合竞价）。"
            "界面为 HH:mm，实际按该分钟末（如 14:57 含 14:57:00 之前 tick）截断。"
        )
        backtest_form.addRow("策略运行结束时间：", self.backtest_run_end_time)
        backtest_layout.addLayout(backtest_form)
        batch_opts_row = QHBoxLayout()
        self.backtest_batch_from_t1_cb = QCheckBox("批量：从选股日下一交易日（T+1）开始")
        self.backtest_batch_from_t1_cb.setChecked(True)
        self.backtest_batch_from_t1_cb.setToolTip(
            "勾选：回测从选股日 T 的下一个交易日起算（适合 T 日盘后选股、T+1 实盘）。"
            "不勾选：从选股日 T 当日（若为非交易日则顺延）起算。"
        )
        self.backtest_batch_hold_days_spin = QSpinBox()
        self.backtest_batch_hold_days_spin.setRange(1, 120)
        self.backtest_batch_hold_days_spin.setValue(1)
        self.backtest_batch_hold_days_spin.setToolTip(
            "首轮「批量回测(选股文件)」专用：从起始交易日起连续持有的交易日个数。"
            "下一轮接续请用上方「下一轮接续」里的持有交易日数；末日出清 N 在接续时随区间自动对齐。"
        )
        batch_opts_row.addWidget(self.backtest_batch_from_t1_cb)
        batch_opts_row.addWidget(QLabel("持有交易日数："))
        batch_opts_row.addWidget(self.backtest_batch_hold_days_spin)
        batch_opts_row.addStretch()
        backtest_layout.addLayout(batch_opts_row)
        backtest_btn_row1 = QHBoxLayout()
        self.backtest_run_btn = QPushButton("运行回测")
        self.backtest_run_btn.clicked.connect(self._on_run_backtest)
        self.backtest_batch_file_btn = QPushButton("批量回测(选股文件)…")
        self.backtest_batch_file_btn.setToolTip(
            "选择板块筛选导出的、含「选股日」列的 Excel/CSV：按每个选股日单独用当日股票池跑回测。"
            "回测区间由上方「T+1」「持有交易日数」决定。"
            "若勾选「分时段组合回测」，则与单次回测相同：时段1=左侧当前策略，时段2=下方所选策略，"
            "且要求第二段运行开始=第一段运行结束。使用当前初始资金；批量时忽略初始持仓表。"
        )
        self.backtest_batch_file_btn.clicked.connect(self._on_batch_backtest_from_selection_file)
        self.backtest_export_last_btn = QPushButton("导出上次回测结果…")
        self.backtest_export_last_btn.setEnabled(False)
        self.backtest_export_last_btn.setToolTip(
            "将最近一次成功回测的期末现金、持仓与参考价导出为 JSON（version=1），供「从回测导出文件导入」接续回测。\n"
            "若刚跑过「批量回测」，此处对应最后一档选股日的回测快照。"
        )
        self.backtest_export_last_btn.clicked.connect(self._on_export_last_backtest_result)
        self.backtest_export_batch_json_btn = QPushButton("导出批量回测(JSON)…")
        self.backtest_export_batch_json_btn.setEnabled(False)
        self.backtest_export_batch_json_btn.setToolTip(
            "在成功执行「批量回测(选股文件)」后可用（切换策略后仍可导出内存中的结果）："
            "导出每一档选股日对应的期末现金、持仓等完整 JSON（version=2）。\n"
            "切换到接续用的策略 B 后，可用「载入上一轮批量回测→本策略」或「从回测导出文件导入」择档接续回测。"
        )
        self.backtest_export_batch_json_btn.clicked.connect(self._on_export_batch_backtest_bundle)
        self.backtest_export_trades_btn = QPushButton("导出成交明细(CSV)…")
        self.backtest_export_trades_btn.setToolTip(
            "将下方「成交明细」表导出为 CSV（UTF-8 带 BOM，便于 Excel 打开）。"
            "单次/批量/下一轮批量回测完成后均可导出。"
        )
        self.backtest_export_trades_btn.clicked.connect(self._on_export_backtest_trades_csv)
        # 按钮较多：分两行，避免窗口稍窄就被挤到看不见
        for _b in (
            self.backtest_run_btn,
            self.backtest_batch_file_btn,
            self.backtest_export_last_btn,
            self.backtest_export_batch_json_btn,
            self.backtest_export_trades_btn,
        ):
            _b.setMinimumWidth(0)
            _b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        backtest_btn_row1.addWidget(self.backtest_run_btn)
        backtest_btn_row1.addWidget(self.backtest_batch_file_btn)
        backtest_btn_row1.addWidget(self.backtest_export_last_btn)
        backtest_btn_row1.addStretch()

        backtest_btn_row2 = QHBoxLayout()
        backtest_btn_row2.addWidget(self.backtest_export_batch_json_btn)
        backtest_btn_row2.addWidget(self.backtest_export_trades_btn)
        backtest_btn_row2.addStretch()

        backtest_layout.addLayout(backtest_btn_row1)
        backtest_layout.addLayout(backtest_btn_row2)

        # 工具：合并买卖成交明细 → 各日选股收益汇总.xlsx
        self.merge_trades_group = QGroupBox("选股收益汇总（合并买卖成交明细）")
        self.merge_trades_group.setCheckable(True)
        self.merge_trades_group.setChecked(False)
        merge_layout = QVBoxLayout(self.merge_trades_group)
        merge_layout.setContentsMargins(12, 8, 12, 8)
        _merge_desc = QLabel(
            "选择两份「回测成交明细」CSV（买入侧/卖出侧），按「选股日+代码」汇总；"
            "汇总表中的 end_date 与卖出明细 CSV 中每条记录的结束日一致（同档回测区间）；"
            "未清仓盯市默认按该 end_date 收盘价，亦可改为第 N 个交易日。"
        )
        _merge_desc.setWordWrap(True)
        merge_layout.addWidget(_merge_desc)
        merge_form = QFormLayout()

        buy_row = QHBoxLayout()
        self.merge_buy_csv_edit = QLineEdit()
        self.merge_buy_csv_edit.setPlaceholderText("选择买入侧成交明细 CSV（导出的回测成交明细）")
        self.merge_buy_csv_edit.setMinimumWidth(0)
        buy_btn = QPushButton("选择…")
        buy_btn.clicked.connect(lambda: self._pick_file_to_edit(self.merge_buy_csv_edit, "选择买入侧成交明细 CSV", "CSV (*.csv);;所有文件 (*.*)"))
        buy_row.addWidget(self.merge_buy_csv_edit)
        buy_row.addWidget(buy_btn)
        merge_form.addRow("买入明细：", buy_row)

        sell_row = QHBoxLayout()
        self.merge_sell_csv_edit = QLineEdit()
        self.merge_sell_csv_edit.setPlaceholderText("卖出侧成交明细 CSV（含每条 end_date）")
        self.merge_sell_csv_edit.setMinimumWidth(0)
        sell_btn = QPushButton("选择…")
        sell_btn.clicked.connect(lambda: self._pick_file_to_edit(self.merge_sell_csv_edit, "选择卖出侧成交明细 CSV", "CSV (*.csv);;所有文件 (*.*)"))
        sell_row.addWidget(self.merge_sell_csv_edit)
        sell_row.addWidget(sell_btn)
        merge_form.addRow("卖出明细：", sell_row)

        sel_row = QHBoxLayout()
        self.merge_selection_file_edit = QLineEdit()
        self.merge_selection_file_edit.setPlaceholderText("可选：选择选股文件（Excel/CSV），回填全部列到汇总")
        self.merge_selection_file_edit.setMinimumWidth(0)
        sel_btn = QPushButton("选择…")
        sel_btn.clicked.connect(
            lambda: self._pick_file_to_edit(
                self.merge_selection_file_edit,
                "选择选股文件（Excel/CSV）",
                "Excel/CSV (*.xlsx *.xls *.csv);;所有文件 (*.*)",
            )
        )
        sel_row.addWidget(self.merge_selection_file_edit)
        sel_row.addWidget(sel_btn)
        merge_form.addRow("选股文件：", sel_row)

        out_row = QHBoxLayout()
        self.merge_out_xlsx_edit = QLineEdit()
        self.merge_out_xlsx_edit.setMinimumWidth(0)
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            default_out = os.path.join(root, "history_data", "各日选股收益汇总.xlsx")
        except Exception:
            default_out = "history_data/各日选股收益汇总.xlsx"
        self.merge_out_xlsx_edit.setText(default_out)
        out_btn = QPushButton("保存为…")
        out_btn.clicked.connect(lambda: self._pick_save_to_edit(self.merge_out_xlsx_edit, "保存汇总为 Excel", "Excel (*.xlsx);;所有文件 (*.*)"))
        out_row.addWidget(self.merge_out_xlsx_edit)
        out_row.addWidget(out_btn)
        merge_form.addRow("输出文件：", out_row)

        mark_row = QHBoxLayout()
        self.merge_use_nth_cb = QCheckBox("使用选股日后第 N 个交易日盯市")
        self.merge_use_nth_cb.setChecked(False)
        self.merge_use_nth_cb.setToolTip(
            "不勾选：未清仓按卖出明细 CSV 中的 end_date 列收盘价盯市（与回测区间一致）。"
            "勾选：按选股日后第 N 个交易日（旧版逻辑）。"
        )

        def _sync_merge_mark_spin():
            en = self.merge_use_nth_cb.isChecked()
            self.merge_mark_n_spin.setEnabled(en)

        self.merge_mark_n_spin = QSpinBox()
        self.merge_mark_n_spin.setRange(0, 120)
        self.merge_mark_n_spin.setValue(3)
        self.merge_mark_n_spin.setEnabled(False)
        self.merge_mark_n_spin.setToolTip(
            "仅在勾选「使用选股日后第 N 个交易日盯市」时生效；N=0 表示选股日当日。"
        )
        self.merge_use_nth_cb.stateChanged.connect(lambda _=None: _sync_merge_mark_spin())
        mark_row.addWidget(self.merge_use_nth_cb)
        mark_row.addWidget(QLabel("第 N 个交易日"))
        mark_row.addWidget(self.merge_mark_n_spin)
        mark_row.addStretch()
        merge_form.addRow("未清仓盯市：", mark_row)

        merge_layout.addLayout(merge_form)
        merge_btn_row = QHBoxLayout()
        self.merge_run_btn = QPushButton("生成汇总")
        self.merge_run_btn.clicked.connect(self._on_merge_backtest_trades_to_excel)
        self.merge_open_btn = QPushButton("打开输出文件")
        self.merge_open_btn.setEnabled(False)
        self.merge_open_btn.clicked.connect(self._on_open_merge_output_file)
        merge_btn_row.addWidget(self.merge_run_btn)
        merge_btn_row.addWidget(self.merge_open_btn)
        merge_btn_row.addStretch()
        merge_layout.addLayout(merge_btn_row)
        backtest_layout.addWidget(self.merge_trades_group)

        self.backtest_result_text = QPlainTextEdit("")
        self.backtest_result_text.setReadOnly(True)
        self.backtest_result_text.setMinimumHeight(80)
        self.backtest_result_text.setMaximumHeight(280)
        self.backtest_result_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        backtest_layout.addWidget(self.backtest_result_text)
        self.backtest_trades_table = QTableWidget()
        self.backtest_trades_table.setColumnCount(20)
        self.backtest_trades_table.setHorizontalHeaderLabels(
            [
                "日期",
                "时间",
                "代码",
                "股票名称",
                "选股日",
                "方向",
                "价格",
                "数量",
                "金额",
                "交易后持仓",
                "触发信息",
                "start_date",
                "end_date",
                "真突破①量均量比",
                "真突破①通过",
                "真突破②委卖委买比",
                "真突破②通过",
                "真突破③量被吃卖档比",
                "真突破③通过",
                "真突破③被吃档数",
            ]
        )
        # 避免“触发信息”列把表格撑很宽
        self.backtest_trades_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.backtest_trades_table.setMinimumHeight(120)
        self.backtest_trades_table.setMaximumHeight(400)
        self.backtest_trades_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        backtest_layout.addWidget(self.backtest_trades_table)
        self.detail_tabs.addTab(self.backtest_tab_widget, "回测")

        right_layout.addWidget(self.detail_tabs, 1)
        layout.addWidget(self.strategy_list)
        layout.addWidget(right_container, 1)

        self.setCentralWidget(central)

        # 当左侧选择发生变化时：先把编辑框内容保存到上一个策略，再刷新右侧
        self.strategy_list.currentItemChanged.connect(self._on_strategy_selection_changed)

        self._log_startup_mode_status()

    def _log_startup_mode_status(self) -> None:
        try:
            from qmt_mode_config import startup_status_lines
        except ImportError:
            try:
                from strategy_generator_app.qmt_mode_config import startup_status_lines
            except ImportError:
                return
        for line in startup_status_lines():
            self._append_run_log(line + "\n")

    # --- 策略列表相关 ---

    def _make_strategy_row_widget(self, cfg):
        """为策略列表的一行创建控件：删除按钮、策略名、导出图标按钮；行容器处理右键改名（闭包捕获该行 id）"""
        row = StrategyRowWidget(on_rename_callback=lambda s=cfg.id: self._on_rename_strategy_by_id(s))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        del_btn = QPushButton("✖")
        del_btn.setFixedSize(22, 22)
        del_btn.setToolTip("删除策略")
        del_btn.clicked.connect(lambda checked=False, s=cfg.id: self.strategy_list.strategy_delete_requested.emit(s))
        name_text = cfg.name
        if len(cfg.stock_codes) > 0:
            name_text += f"  ({len(cfg.stock_codes)} 只)"
        name_label = QLabel(name_text)
        name_label.setStyleSheet("border: none;")
        export_btn = QToolButton()
        export_btn.setText("📤")
        export_btn.setToolTip("导出策略")
        export_btn.setFixedSize(24, 24)
        export_btn.clicked.connect(lambda checked=False, s=cfg.id: self._on_export_strategy_by_id(s))
        layout.addWidget(del_btn)
        layout.addWidget(name_label, 1)
        layout.addWidget(export_btn)
        return row

    def _load_strategies_into_list(self, reselect_id: str | None = None):
        """
        从磁盘重载策略列表并刷新左侧 QListWidget。
        reselect_id: 若指定，重载后选中该策略（用于保存后仍停留在当前策略）；
        重载过程中 block 列表信号，避免 clear/setCurrent 触发「未保存的修改」确认框。
        """
        self._strategies = load_all_strategies()
        self.strategy_list.blockSignals(True)
        try:
            self.strategy_list.clear()
            for cfg in self._strategies:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, cfg.id)
                item.setSizeHint(QSize(0, 28))
                self.strategy_list.addItem(item)
                self.strategy_list.setItemWidget(item, self._make_strategy_row_widget(cfg))
            if self.strategy_list.count() > 0:
                chosen = None
                rid = (reselect_id or "").strip()
                if rid:
                    for i in range(self.strategy_list.count()):
                        item = self.strategy_list.item(i)
                        if item and (item.data(Qt.UserRole) or "") == rid:
                            chosen = item
                            break
                if chosen is not None:
                    self.strategy_list.setCurrentItem(chosen)
                else:
                    self.strategy_list.setCurrentItem(self.strategy_list.item(0))
        finally:
            self.strategy_list.blockSignals(False)
        sid = self._get_selected_strategy_id()
        self._update_detail_panel(selected_id=sid)
        self._refresh_backtest_seg2_combo()

    def _on_backtest_dual_toggled(self, state):
        """显示/隐藏第二段策略与时间面板"""
        vis = state == Qt.Checked
        if hasattr(self, "backtest_seg2_panel"):
            self.backtest_seg2_panel.setVisible(vis)
        if hasattr(self, "backtest_seg1_title"):
            self.backtest_seg1_title.setVisible(vis)
        if hasattr(self, "backtest_dual_carry_cb"):
            self.backtest_dual_carry_cb.setEnabled(vis)

    def _refresh_backtest_seg2_combo(self):
        """第二段策略下拉：列出全部策略，默认选与当前左侧策略不同的项"""
        if not hasattr(self, "backtest_seg2_combo"):
            return
        cur = self._get_selected_strategy_id()
        self.backtest_seg2_combo.blockSignals(True)
        self.backtest_seg2_combo.clear()
        for cfg in getattr(self, "_strategies", []) or []:
            self.backtest_seg2_combo.addItem(cfg.name, cfg.id)
        pick = 0
        for i in range(self.backtest_seg2_combo.count()):
            if self.backtest_seg2_combo.itemData(i) != cur:
                pick = i
                break
        if self.backtest_seg2_combo.count() > 0:
            self.backtest_seg2_combo.setCurrentIndex(pick)
        self.backtest_seg2_combo.blockSignals(False)

    def _get_selected_strategy_id(self) -> str:
        item = self.strategy_list.currentItem()
        if not item:
            return ""
        return item.data(Qt.UserRole) or ""

    def _find_strategy_by_id(self, sid: str) -> StrategyConfig | None:
        for cfg in self._strategies:
            if cfg.id == sid:
                return cfg
        return None

    def _refresh_strategy_row_count_text(self, sid: str):
        """刷新左侧列表中某策略行的股票数量显示，不重载整个列表。"""
        if not sid:
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            return
        for i in range(self.strategy_list.count()):
            item = self.strategy_list.item(i)
            if not item or (item.data(Qt.UserRole) or "") != sid:
                continue
            row_widget = self.strategy_list.itemWidget(item)
            if not row_widget:
                return
            layout = row_widget.layout()
            if not layout or layout.count() < 2:
                return
            name_label = layout.itemAt(1).widget()
            if not isinstance(name_label, QLabel):
                return
            name_text = cfg.name
            if len(cfg.stock_codes) > 0:
                name_text += f"  ({len(cfg.stock_codes)} 只)"
            name_label.setText(name_text)
            return

    def _name_exists(self, name: str, exclude_id: str | None = None) -> bool:
        """检查名称是否已被其他策略使用"""
        name = (name or "").strip()
        if not name:
            return False
        for cfg in self._strategies:
            if exclude_id and cfg.id == exclude_id:
                continue
            if cfg.name == name:
                return True
        return False

    def _get_pool_codes_from_list(self):
        """从股票池列表按当前顺序取出代码列表"""
        codes = []
        for i in range(self.pool_list.count()):
            item = self.pool_list.item(i)
            if item:
                c = item.data(Qt.UserRole) or ""
                if c:
                    codes.append(c)
        return codes

    def _fill_pool_list(self, codes, set_original=True):
        """用代码列表填充股票池列表（显示 代码+名称），每项带复选框，可选是否记为原始顺序"""
        self.pool_select_all_cb.blockSignals(True)
        self.pool_select_all_cb.setChecked(False)
        self.pool_select_all_cb.blockSignals(False)
        self.pool_list.clear()
        if set_original:
            self._pool_original_order = list(codes)
        for code in codes:
            code = (code or "").strip()
            if len(code) < 6:
                code = code.zfill(6)
            item = QListWidgetItem(_code_display_text(code))
            item.setData(Qt.UserRole, code)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.pool_list.addItem(item)

    def _clear_time_str_from_qtime(self, time_edit: QTimeEdit) -> str:
        t = time_edit.time()
        return f"{t.hour():02d}:{t.minute():02d}:{t.second():02d}"

    def _apply_live_runtime_strategy_params(
        self, params: Dict[str, Any], cfg: Optional["StrategyConfig"] = None
    ) -> None:
        """实盘运行前：仅对含 scheduled_clear 的卖出策略注入持有天数与清仓时间。"""
        if cfg is None:
            return
        if not strategy_uses_scheduled_clear(
            cfg.strategy_code or "", cfg.strategy_params, getattr(cfg, "name", "") or ""
        ):
            return
        if hasattr(self, "live_hold_trading_days_spin"):
            _merge_hold_trading_days_into_params(params, self.live_hold_trading_days_spin.value())
        if hasattr(self, "live_scheduled_clear_time"):
            _merge_scheduled_clear_time_into_params(
                params, self._clear_time_str_from_qtime(self.live_scheduled_clear_time)
            )

    def _refresh_live_scheduled_clear_controls(self, cfg: Optional["StrategyConfig"] = None) -> None:
        """买入策略隐藏实盘「持有交易日数/定时清仓」控件。"""
        show = False
        if cfg is not None:
            show = strategy_uses_scheduled_clear(
                cfg.strategy_code or "", cfg.strategy_params, getattr(cfg, "name", "") or ""
            )
        for w in (
            getattr(self, "live_hold_days_label", None),
            getattr(self, "live_hold_trading_days_spin", None),
            getattr(self, "live_clear_label", None),
            getattr(self, "live_scheduled_clear_time", None),
        ):
            if w is not None:
                w.setVisible(show)

    def _apply_sell_chain_scheduled_clear_time(self, target: Any) -> None:
        """下一轮接续回测：仅对含 scheduled_clear 的策略段写入清仓时间。"""
        if not hasattr(self, "sell_chain_scheduled_clear_time"):
            return
        ts = self._clear_time_str_from_qtime(self.sell_chain_scheduled_clear_time)
        if isinstance(target, dict):
            _merge_scheduled_clear_time_into_params(target, ts)
            return
        if isinstance(target, list):
            for seg in target:
                code = seg.get("strategy_code") or ""
                seg_name = seg.get("name") or ""
                sp = dict(seg.get("strategy_params") or {})
                if not strategy_uses_scheduled_clear(code, sp, seg_name):
                    strip_scheduled_clear_params(sp)
                    seg["strategy_params"] = sp
                    continue
                _merge_scheduled_clear_time_into_params(sp, ts)
                seg["strategy_params"] = sp

    def _refresh_sell_chain_scheduled_clear_controls(self, cfg: Optional["StrategyConfig"] = None) -> None:
        """非定时清仓策略隐藏「下一轮接续」里的定时清仓与对比控件。"""
        show = False
        if cfg is not None:
            show = strategy_uses_scheduled_clear(
                cfg.strategy_code or "", cfg.strategy_params, getattr(cfg, "name", "") or ""
            )
        for w in (
            getattr(self, "sell_chain_clear_label", None),
            getattr(self, "sell_chain_scheduled_clear_time", None),
            getattr(self, "sell_chain_sweep_label", None),
            getattr(self, "backtest_clear_time_sweep_edit", None),
            getattr(self, "backtest_clear_time_sweep_btn", None),
        ):
            if w is not None:
                w.setVisible(show)

    def _params_from_form(self):
        """从策略参数表单读取当前值（用于保存或运行）"""
        return {
            PARAM_BUY_AMOUNT_PER_STOCK: self.param_buy_amount_spin.value(),
            PARAM_MIN_ORDER_AMOUNT: self.param_min_order_amount_spin.value(),
        }

    def _load_params_to_form(self, params):
        """将 strategy_params 填入策略参数表单"""
        if not params:
            return
        self.param_buy_amount_spin.setValue(float(params.get(PARAM_BUY_AMOUNT_PER_STOCK, 50000)))
        self.param_min_order_amount_spin.setValue(float(params.get(PARAM_MIN_ORDER_AMOUNT, 5000)))

    def _take_snapshot(self):
        """当前表单状态快照，用于判断是否有未保存修改"""
        return {
            "stock_codes": list(self._get_pool_codes_from_list()),
            "strategy_params": dict(self._params_from_form()),
            "strategy_code": (self.logic_code_edit.toPlainText() or "").strip(),
        }

    def _is_dirty(self):
        """当前策略是否有未保存的修改"""
        return bool(self._dirty_sections)

    def _mark_dirty(self, section: str):
        """标记某区块已编辑（股票池/策略参数/策略逻辑）"""
        if self._loading_strategy:
            return
        self._dirty_sections.add(section)
        self._refresh_dirty_ui()

    def _refresh_dirty_ui(self):
        """根据脏状态更新 Tab 标题星号与未保存提示条"""
        pool_suffix = " *" if "pool" in self._dirty_sections else ""
        params_logic_suffix = " *" if ("params" in self._dirty_sections or "logic" in self._dirty_sections) else ""
        self.detail_tabs.setTabText(0, "股票池" + pool_suffix)
        self.detail_tabs.setTabText(1, "参数与逻辑" + params_logic_suffix)
        if self._dirty_sections:
            self.unsaved_bar.show()
        else:
            self.unsaved_bar.hide()

    def _on_strategy_selection_changed(self, cur, prev):
        """左侧策略选择变化：若有未保存修改则提示；保存或放弃后刷新右侧为当前选中策略"""
        prev_sid = (prev.data(Qt.UserRole) or "").strip() if prev else ""
        cur_sid = (cur.data(Qt.UserRole) or "").strip() if cur else ""
        if prev_sid and self._is_dirty():
            reply = QMessageBox.question(
                self,
                "未保存的修改",
                "当前策略有未保存的修改，是否保存？",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply == QMessageBox.Cancel:
                self.strategy_list.blockSignals(True)
                self.strategy_list.setCurrentItem(prev)
                self.strategy_list.blockSignals(False)
                return
            if reply == QMessageBox.Save:
                cfg = self._find_strategy_by_id(prev_sid)
                if cfg:
                    cfg.stock_codes = self._get_pool_codes_from_list()
                    cfg.strategy_params = {**(cfg.strategy_params or {}), **self._params_from_form()}
                    cfg.strategy_code = self.logic_code_edit.toPlainText().strip()
                    save_strategy(cfg)
        if prev_sid:
            try:
                self._save_ui_state_for_strategy(prev_sid)
            except Exception:
                pass
        self._update_detail_panel(selected_id=cur_sid)

    def _capture_table_rows(self, table: QTableWidget):
        rows = []
        if table is None:
            return rows
        n_rows = table.rowCount()
        n_cols = table.columnCount()
        for r in range(n_rows):
            row = []
            for c in range(n_cols):
                it = table.item(r, c)
                row.append(it.text() if it is not None else "")
            rows.append(row)
        return rows

    def _restore_table_rows(self, table: QTableWidget, rows):
        if table is None:
            return
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        try:
            table.setRowCount(0)
            if not rows:
                return
            n_cols = table.columnCount()
            table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c in range(min(n_cols, len(row))):
                    table.setItem(r, c, QTableWidgetItem(str(row[c])))
        finally:
            table.blockSignals(False)
            table.setUpdatesEnabled(True)

    def _save_ui_state_for_strategy(self, sid: str, *, include_trades: bool = True):
        sid = (sid or "").strip()
        if not sid:
            return
        state = {}
        state["_sid"] = sid  # 校验用：恢复时必须是同一策略
        # 生成策略页
        state["preview_log"] = self.run_output_edit.toPlainText() if hasattr(self, "run_output_edit") else ""
        state["preview_rows"] = self._capture_table_rows(self.preview_table) if hasattr(self, "preview_table") else []
        state["preview_task_list"] = list(self._preview_task_list or [])
        state["preview_intents"] = list(getattr(self, "_preview_intents", None) or [])
        # 回测页
        state["backtest_text"] = self.backtest_result_text.toPlainText() if hasattr(self, "backtest_result_text") else ""
        # 成交明细：优先存原始 dict 列表，避免逐格读 QTableWidget（大批量时极慢）
        if include_trades:
            cached = getattr(self, "_backtest_trades_data", None)
            if cached:
                state["backtest_trades_data"] = list(cached)
            else:
                # 兼容：本会话尚未写入缓存时，小表才逐格抓取
                n = 0
                try:
                    n = int(self.backtest_trades_table.rowCount())
                except Exception:
                    n = 0
                if 0 < n <= 800:
                    state["backtest_trades_data"] = []
                    state["backtest_trades"] = self._capture_table_rows(
                        self.backtest_trades_table
                    )
                else:
                    prev = (getattr(self, "_strategy_ui_state", None) or {}).get(sid) or {}
                    state["backtest_trades_data"] = list(
                        prev.get("backtest_trades_data") or []
                    )
                    state["backtest_trades"] = []
                    if n > 800:
                        state["backtest_trades"] = []
        else:
            prev = (getattr(self, "_strategy_ui_state", None) or {}).get(sid) or {}
            if "backtest_trades_data" in prev:
                state["backtest_trades_data"] = list(prev.get("backtest_trades_data") or [])
            else:
                state["backtest_trades_data"] = list(
                    getattr(self, "_backtest_trades_data", None) or []
                )
            state["backtest_trades"] = list(prev.get("backtest_trades") or [])
        if "backtest_trades" not in state:
            state["backtest_trades"] = []  # 兼容旧字段；默认不逐格抓取
        state["init_positions"] = self._capture_table_rows(self.backtest_init_positions_table) if hasattr(self, "backtest_init_positions_table") else []
        if hasattr(self, "backtest_dual_cb"):
            state["backtest_dual"] = self.backtest_dual_cb.isChecked()
            state["backtest_dual_carry"] = bool(
                getattr(self, "backtest_dual_carry_cb", None)
                and self.backtest_dual_carry_cb.isChecked()
            )
            state["backtest_seg2_id"] = self.backtest_seg2_combo.currentData()
            state["backtest_seg2_gen"] = self.backtest_seg2_generation_time.time().toString("HH:mm")
            state["backtest_seg2_rs"] = self.backtest_seg2_run_start.time().toString("HH:mm")
            state["backtest_seg2_re"] = self.backtest_seg2_run_end.time().toString("HH:mm")
        self._strategy_ui_state[sid] = state

    def _restore_ui_state_for_strategy(self, sid: str):
        sid = (sid or "").strip()
        if not sid:
            self._clear_result_panels()
            return
        state = self._strategy_ui_state.get(sid) or {}
        # 只恢复属于该策略的缓存，避免错把 A 策略的结果恢复到 B 策略
        if state.get("_sid") != sid:
            self._clear_result_panels()
            return
        # 生成策略页
        try:
            self.run_output_edit.setPlainText(state.get("preview_log") or "")
        except Exception:
            pass
        try:
            self._restore_table_rows(self.preview_table, state.get("preview_rows") or [])
        except Exception:
            pass
        self._preview_task_list = list(state.get("preview_task_list") or [])
        self._preview_intents = list(state.get("preview_intents") or [])
        # 回测页
        try:
            self.backtest_result_text.setPlainText(state.get("backtest_text") or "")
        except Exception:
            pass
        try:
            trades_data = state.get("backtest_trades_data")
            if isinstance(trades_data, list) and (
                trades_data or "backtest_trades_data" in state
            ):
                self._fill_backtest_trades_table(trades_data)
            else:
                self._restore_table_rows(
                    self.backtest_trades_table, state.get("backtest_trades") or []
                )
                self._backtest_trades_data = []
        except Exception:
            pass
        try:
            self._restore_table_rows(self.backtest_init_positions_table, state.get("init_positions") or [])
        except Exception:
            pass
        try:
            if hasattr(self, "backtest_dual_cb"):
                self.backtest_dual_cb.setChecked(bool(state.get("backtest_dual")))
                if hasattr(self, "backtest_dual_carry_cb"):
                    self.backtest_dual_carry_cb.setChecked(bool(state.get("backtest_dual_carry")))
                bid = state.get("backtest_seg2_id")
                if bid:
                    for i in range(self.backtest_seg2_combo.count()):
                        if self.backtest_seg2_combo.itemData(i) == bid:
                            self.backtest_seg2_combo.setCurrentIndex(i)
                            break
                for w, sk in [
                    (self.backtest_seg2_generation_time, "backtest_seg2_gen"),
                    (self.backtest_seg2_run_start, "backtest_seg2_rs"),
                    (self.backtest_seg2_run_end, "backtest_seg2_re"),
                ]:
                    ts = state.get(sk)
                    if ts:
                        t = QTime.fromString(ts, "HH:mm")
                        if t.isValid():
                            w.setTime(t)
        except Exception:
            pass

    def _clear_result_panels(self):
        """清空生成策略/回测页的运行结果区域"""
        try:
            self.run_output_edit.clear()
            self.preview_table.setRowCount(0)
            self.backtest_result_text.setPlainText("")
            self.backtest_trades_table.setRowCount(0)
            self.backtest_init_positions_table.setRowCount(0)
        except Exception:
            pass
        self._preview_task_list = []
        self._preview_intents = []
        self._backtest_trades_data = []

    def _update_detail_panel(self, selected_id: str = None):
        """根据当前选中策略更新右侧股票池、参数、策略逻辑；并恢复该策略上次的运行结果显示。
        selected_id: 若传入（如从 currentItemChanged 的 cur 取得），则优先使用，避免 list 时序问题。"""
        sid = (selected_id or "").strip() or self._get_selected_strategy_id()
        if not sid:
            self.pool_select_all_cb.setChecked(False)
            self.pool_list.clear()
            self.logic_code_edit.clear()
            self._load_params_to_form({})
            self._pool_original_order = []
            self._last_saved_snapshot = None
            self._dirty_sections.clear()
            self._refresh_dirty_ui()
            self._clear_result_panels()
            if hasattr(self, "backtest_export_last_btn"):
                self.backtest_export_last_btn.setEnabled(False)
            self._refresh_schedule_controls_from_cfg(None)
            self._refresh_live_scheduled_clear_controls(None)
            self._refresh_sell_chain_scheduled_clear_controls(None)
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            self.pool_select_all_cb.setChecked(False)
            self.pool_list.clear()
            self.logic_code_edit.clear()
            self._load_params_to_form({})
            self._pool_original_order = []
            self._last_saved_snapshot = None
            self._dirty_sections.clear()
            self._refresh_dirty_ui()
            self._clear_result_panels()
            if hasattr(self, "backtest_export_last_btn"):
                self.backtest_export_last_btn.setEnabled(False)
            self._refresh_schedule_controls_from_cfg(None)
            self._refresh_live_scheduled_clear_controls(None)
            self._refresh_sell_chain_scheduled_clear_controls(None)
            return
        self._loading_strategy = True
        self._fill_pool_list(cfg.stock_codes, set_original=True)
        self._load_params_to_form(cfg.strategy_params or {})
        code = (cfg.strategy_code or "").strip()
        self.logic_code_edit.setPlainText(code if code else _default_strategy_code())
        self._last_saved_snapshot = self._take_snapshot()
        self._dirty_sections.clear()
        self._loading_strategy = False
        self._refresh_dirty_ui()
        # 恢复该策略上次的预览/回测显示（若没有缓存或 _sid 不一致则清空）
        try:
            self._restore_ui_state_for_strategy(sid)
        except Exception:
            self._clear_result_panels()
        self._refresh_backtest_export_button()
        self._refresh_schedule_controls_from_cfg(cfg)
        self._refresh_live_scheduled_clear_controls(cfg)
        self._refresh_sell_chain_scheduled_clear_controls(cfg)
    def _refresh_schedule_controls_from_cfg(self, cfg: StrategyConfig | None) -> None:
        """根据当前策略刷新「定时生成」控件状态。"""
        if not hasattr(self, "schedule_gen_datetime"):
            return
        if cfg is None:
            self.schedule_save_btn.setEnabled(False)
            self.schedule_clear_btn.setEnabled(False)
            self.schedule_gen_datetime.setEnabled(False)
            self.schedule_status_label.setText("请先选择策略")
            return
        self.schedule_save_btn.setEnabled(True)
        self.schedule_clear_btn.setEnabled(True)
        self.schedule_gen_datetime.setEnabled(True)
        s = (cfg.scheduled_generate_at or "").strip()
        if s:
            try:
                dt = _parse_schedule_at_storage(s)
                self.schedule_gen_datetime.setDateTime(
                    QDateTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
                )
                self.schedule_status_label.setText(f"已预约：{s.replace('T', ' ')}（本地）")
            except Exception:
                self.schedule_status_label.setText(f"预约时间格式无效，请清除后重设：{s}")
        else:
            self.schedule_gen_datetime.setDateTime(_default_schedule_gen_qdatetime())
            self.schedule_status_label.setText("未预约")

    def _on_schedule_save_clicked(self) -> None:
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.information(self, "提示", "请先在左侧选择一个策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            QMessageBox.warning(self, "警告", "未找到所选策略。")
            return
        qdt = self.schedule_gen_datetime.dateTime()
        if not qdt.isValid():
            QMessageBox.warning(self, "提示", "请选择有效的日期与时间。")
            return
        dt_py = datetime(
            qdt.date().year(),
            qdt.date().month(),
            qdt.date().day(),
            qdt.time().hour(),
            qdt.time().minute(),
            qdt.time().second(),
        )
        dt_py = _normalize_schedule_dt(dt_py)
        now_s = _normalize_schedule_dt(datetime.now())
        if dt_py <= now_s:
            QMessageBox.warning(self, "提示", "预约时间必须晚于当前时间。")
            return
        cfg.scheduled_generate_at = _schedule_at_storage_str(dt_py)
        save_strategy(cfg)
        self._load_strategies_into_list(reselect_id=sid)
        cfg2 = self._find_strategy_by_id(sid)
        if cfg2:
            self._refresh_schedule_controls_from_cfg(cfg2)
        QMessageBox.information(self, "完成", "已保存定时预约。")

    def _on_schedule_clear_clicked(self) -> None:
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.information(self, "提示", "请先在左侧选择一个策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            return
        cfg.scheduled_generate_at = None
        save_strategy(cfg)
        self._load_strategies_into_list(reselect_id=sid)
        cfg2 = self._find_strategy_by_id(sid)
        if cfg2:
            self._refresh_schedule_controls_from_cfg(cfg2)
        QMessageBox.information(self, "完成", "已清除定时预约。")

    def _on_schedule_tick(self) -> None:
        # 竞价阶段提前订阅：不占用 busy 锁（仅写 JSON）
        try:
            self._maybe_prewarm_strategy_pool_watch(log=True)
        except Exception:
            pass
        if self._schedule_runner_busy:
            return
        now = _normalize_schedule_dt(datetime.now())
        due: List[Tuple[datetime, str]] = []
        for c in self._strategies or []:
            raw = (c.scheduled_generate_at or "").strip()
            if not raw:
                continue
            try:
                dt = _parse_schedule_at_storage(raw)
            except ValueError:
                continue
            if dt <= now:
                due.append((dt, c.id))
        if not due:
            return
        due.sort(key=lambda x: (x[0], x[1]))
        self._schedule_runner_busy = True
        try:
            for _, sid in due:
                self._execute_one_scheduled_generate(sid)
        finally:
            self._schedule_runner_busy = False
            # 长任务在主线程执行期间会跳过多次 1s 定时器回调；结束后立刻再扫一轮 due，
            # 避免「前一个策略刚跑完、后一个已过点却要等下一次定时器」的体感漏执行。
            try:
                QTimer.singleShot(0, self._on_schedule_tick)
            except Exception:
                pass

    def _execute_one_scheduled_generate(self, sid: str) -> None:
        sid = (sid or "").strip()
        if not sid:
            return
        try:
            # 定时串行时保留界面上的历史消息/预览，仅清空本次执行用的内部缓存，
            # 避免本次运行提前返回时误沿用上一次任务意图导出。
            self._preview_intents = []
            self._preview_task_list = []
            self._select_strategy_programmatically(sid)
            cfg = self._find_strategy_by_id(sid)
            if not cfg or not (cfg.scheduled_generate_at or "").strip():
                self._append_run_log(f"[定时生成] 跳过 {sid}：无有效预约（可能已执行或已清除）\n")
                return
            # 不要在此处清除预约：若运行/导出中途异常，用户仍可从 JSON 看到预约并手动重试；
            # 成功写入任务后再清除，避免「第一个跑很久时第二个策略的预约被误操作或重载丢失」的困惑。
            self._save_all_edits_impl(quiet=True)
            # 下面会触发 _load_strategies_into_list → _restore_ui_state_for_strategy，若把「开始」写在前面会被旧缓存覆盖。
            self._append_run_log(
                f"\n======== [定时生成] 开始 id={sid}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ========\n"
            )
            self._on_preview_tasks(quiet=True)
            self._on_export_tasks(quiet=True)
            cfg2 = self._find_strategy_by_id(sid)
            if cfg2 and (cfg2.scheduled_generate_at or "").strip():
                cfg2.scheduled_generate_at = None
                save_strategy(cfg2)
                # 重载列表时会 _restore_ui_state_for_strategy：必须把本次运行日志/预览写入缓存，否则会被旧状态覆盖成「清空」。
                try:
                    self._save_ui_state_for_strategy(sid)
                except Exception:
                    pass
                self._load_strategies_into_list(reselect_id=sid)
        except Exception as e:
            self._append_run_log(f"[定时生成] 异常 id={sid}: {type(e).__name__}: {e}\n")
        finally:
            self._append_run_log(
                f"======== [定时生成] 结束 id={sid}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ========\n"
            )
            try:
                if (self._get_selected_strategy_id() or "").strip() == sid:
                    self._save_ui_state_for_strategy(sid)
            except Exception:
                pass

    def _on_save_all_edits(self):
        """保存全部：股票池、策略参数、策略逻辑一并写入并清除未保存标记（供按钮连接，避免 clicked(bool) 误传入 quiet）。"""
        self._save_all_edits_impl(quiet=False)

    def _save_all_edits_impl(self, quiet: bool = False) -> None:
        """保存全部实现；quiet=True 时不弹成功/提示框（供定时生成串行调用）。"""
        sid = self._get_selected_strategy_id()
        if not sid:
            if not quiet:
                QMessageBox.information(self, "提示", "请先在左侧选择一个策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            if not quiet:
                QMessageBox.warning(self, "警告", "未找到所选策略。")
            return
        cfg.stock_codes = self._get_pool_codes_from_list()
        cfg.strategy_params = {**(cfg.strategy_params or {}), **self._params_from_form()}
        cfg.strategy_code = self.logic_code_edit.toPlainText().strip()
        save_strategy(cfg)
        self._load_strategies_into_list(reselect_id=sid)
        cfg2 = self._find_strategy_by_id(sid)
        if cfg2:
            self._pool_original_order = list(cfg2.stock_codes)
        if not quiet:
            QMessageBox.information(self, "完成", "已保存全部修改。")

    def _on_discard_edits(self):
        """放弃修改：从上次保存的快照恢复表单，清除未保存标记"""
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.information(self, "提示", "请先在左侧选择一个策略。")
            return
        if not self._dirty_sections:
            QMessageBox.information(self, "提示", "当前没有未保存的修改。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            return
        self._loading_strategy = True
        self._fill_pool_list(cfg.stock_codes, set_original=True)
        self._load_params_to_form(cfg.strategy_params or {})
        code = (cfg.strategy_code or "").strip()
        self.logic_code_edit.setPlainText(code if code else _default_strategy_code())
        self._loading_strategy = False
        self._last_saved_snapshot = self._take_snapshot()
        self._dirty_sections.clear()
        self._refresh_dirty_ui()
        QMessageBox.information(self, "完成", "已放弃本次修改，已恢复为上次保存的状态。")

    def _save_pool_codes_silent(self) -> bool:
        """静默保存当前策略股票池，不弹成功提示；成功返回 True。"""
        sid = self._get_selected_strategy_id()
        if not sid:
            return False
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            return False
        cfg.stock_codes = self._get_pool_codes_from_list()
        save_strategy(cfg)
        self._pool_original_order = list(cfg.stock_codes)
        self._refresh_strategy_row_count_text(sid)
        self._dirty_sections.discard("pool")
        self._refresh_dirty_ui()
        if not self._dirty_sections:
            self._last_saved_snapshot = self._take_snapshot()
        return True

    def _on_pool_copy_codes(self):
        """复制当前策略股票池到内存，供其他策略粘贴并入。"""
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.information(self, "提示", "请先在左侧选择一个策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        codes = list(self._get_pool_codes_from_list())
        self._pool_clipboard_codes = codes
        self._pool_clipboard_source_name = (cfg.name if cfg else sid) or sid
        QMessageBox.information(
            self,
            "复制",
            f"已复制 {len(codes)} 只股票（来源策略：{self._pool_clipboard_source_name}）。",
        )

    def _on_pool_paste_codes(self):
        """把复制缓存中的股票并入当前策略股票池（去重、自动保存）。"""
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.information(self, "提示", "请先在左侧选择一个策略。")
            return
        if not self._pool_clipboard_codes:
            QMessageBox.information(self, "粘贴", "当前没有可粘贴的股票池内容，请先在其他策略点击「复制」。")
            return
        added = self._merge_codes_into_pool(self._pool_clipboard_codes)
        if added > 0:
            src = self._pool_clipboard_source_name or "已复制策略"
            QMessageBox.information(self, "粘贴", f"已从「{src}」并入 {added} 只股票，并自动保存。")
        else:
            QMessageBox.information(self, "粘贴", "粘贴完成：这些股票已在当前策略股票池中，无新增。")

    def _on_import_pool_codes(self):
        """从 Excel/CSV/TXT 文件导入股票代码（替换当前列表）"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择要导入的文件",
            "",
            "Excel (*.xlsx *.xls);;CSV (*.csv);;文本 (*.txt *.text);;所有文件 (*.*)",
        )
        if not path or not os.path.isfile(path):
            return
        codes = _parse_codes_from_file(path)
        if not codes:
            QMessageBox.warning(self, "导入结果", "未能从该文件中解析出有效的 6 位股票代码。")
            return
        self._fill_pool_list(codes, set_original=True)
        self._save_pool_codes_silent()
        QMessageBox.information(self, "导入结果", f"已从文件导入并自动保存 {len(codes)} 只股票。")

    def _on_import_positions(self):
        """从 QMT 当前持仓一键导入到本策略股票池（已存在的不会重复添加）"""
        position_codes = get_positions()
        if not position_codes:
            QMessageBox.warning(
                self,
                "导入持仓",
                "未获取到持仓。请确认：\n1. 大 QMT 模型交易已运行「蚂蚁量化规则」；\n2. data/config.ini 已配置 account_id（builtin 下 path_qmt 可留空）。",
            )
            return
        current = self._get_pool_codes_from_list()
        current_set = set(current)
        added = [c for c in position_codes if c not in current_set]
        merged = list(current) + added
        self._fill_pool_list(merged, set_original=True)
        if added:
            self._save_pool_codes_silent()
            msg = f"已导入 {len(added)} 只持仓股票"
            if len(added) < len(position_codes):
                msg += f"（{len(position_codes) - len(added)} 只已存在已跳过）"
            msg += "，已自动保存。"
            QMessageBox.information(self, "导入持仓", msg)
        else:
            QMessageBox.information(self, "导入持仓", "当前持仓股票均已在本策略股票池中，无需重复添加。")

    def _on_backtest_add_init_position_row(self):
        """回测 Tab：在初始持仓表中添加一行空行"""
        t = self.backtest_init_positions_table
        row = t.rowCount()
        t.insertRow(row)
        t.setItem(row, 0, QTableWidgetItem(""))
        t.setItem(row, 1, QTableWidgetItem(""))
        t.setItem(row, 2, QTableWidgetItem(""))

    def _on_backtest_remove_init_position_rows(self):
        """回测 Tab：删除初始持仓表中选中的行"""
        t = self.backtest_init_positions_table
        rows = sorted(set(i.row() for i in t.selectedIndexes()), reverse=True)
        for r in rows:
            t.removeRow(r)

    def _on_backtest_clear_init_positions(self):
        """回测 Tab：清空初始持仓表中所有行"""
        t = self.backtest_init_positions_table
        t.setRowCount(0)

    def _on_backtest_import_positions(self):
        """回测 Tab：从 QMT 当前持仓导入到初始持仓表，仅导入当前策略股票池内的标的。"""
        pool_codes = set((c or "").strip()[:6].zfill(6) if (c or "").strip() else "" for c in self._get_pool_codes_from_list())
        pool_codes = {c for c in pool_codes if len(c) == 6}
        if not pool_codes:
            QMessageBox.warning(self, "导入持仓", "当前策略股票池为空，无法导入。请先在「股票池」Tab 添加股票并保存。")
            return
        positions = get_positions_for_backtest()
        if not positions:
            QMessageBox.warning(
                self,
                "导入持仓",
                "未获取到持仓。请确认：\n1. 大 QMT 模型交易已运行「蚂蚁量化规则」；\n2. data/config.ini 已配置 account_id（builtin 下 path_qmt 可留空）。",
            )
            return
        # 只保留股票池内的持仓
        in_pool = {c: p for c, p in positions.items() if (c or "").strip()[:6].zfill(6) in pool_codes}
        skipped = len(positions) - len(in_pool)
        t = self.backtest_init_positions_table
        t.setRowCount(0)
        for code_6, pos in in_pool.items():
            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, QTableWidgetItem(code_6))
            t.setItem(row, 1, QTableWidgetItem(str(int(pos.get("volume") or 0))))
            t.setItem(row, 2, QTableWidgetItem(str(float(pos.get("cost") or 0))))
        if skipped > 0:
            QMessageBox.information(self, "导入持仓", f"已导入 {len(in_pool)} 只（均在当前股票池中）。{skipped} 只不在股票池中已跳过。")
        else:
            QMessageBox.information(self, "导入持仓", f"已导入 {len(in_pool)} 只股票的持仓（数量、成本），均可用于回测初始持仓。")

    def _on_backtest_import_from_pool(self):
        """回测 Tab：从当前策略股票池导入到初始持仓表，数量预设 1000、成本预设 10。"""
        codes = self._get_pool_codes_from_list()
        if not codes:
            QMessageBox.warning(self, "从股票池导入", "当前策略股票池为空，请先在「股票池」Tab 添加股票并保存。")
            return
        t = self.backtest_init_positions_table
        t.setRowCount(0)
        for code in codes:
            code_6 = (code or "").strip().replace(".", "")[:6]
            if len(code_6) < 6 and code_6:
                code_6 = code_6.zfill(6)
            if not code_6 or len(code_6) != 6:
                continue
            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, QTableWidgetItem(code_6))
            t.setItem(row, 1, QTableWidgetItem("1000"))
            t.setItem(row, 2, QTableWidgetItem("10"))
        QMessageBox.information(self, "从股票池导入", f"已从股票池导入 {t.rowCount()} 只股票，数量 1000、成本 10 元；可在此修改后用于回测。")

    BACKTEST_EXPORT_VERSION = 1

    def _make_backtest_export_payload(
        self,
        cfg,
        result,
        initial_cash,
        start_date,
        end_date,
        skipped_init: int,
        use_dual: bool,
        cfg_b_dual,
    ):
        """构造可写入 JSON 的回测结束快照（期末现金、持仓、参考价等）"""
        eq = result.get("equity_curve") or []
        last_day = eq[-1].get("date", "") if eq else ""
        fp = result.get("final_positions") or {}
        positions_out = {}
        for k, v in fp.items():
            code_6 = (k or "").strip().replace(".", "")[:6]
            if len(code_6) < 6 and code_6:
                code_6 = code_6.zfill(6)
            if len(code_6) != 6:
                continue
            positions_out[code_6] = {
                "volume": int((v or {}).get("volume") or 0),
                "cost": float((v or {}).get("cost") or 0),
            }
        lp = result.get("last_prices") or {}
        last_prices_out = {}
        for k, v in lp.items():
            try:
                if v is not None and float(v) > 0:
                    last_prices_out[str((k or "").strip()[:6].zfill(6))] = float(v)
            except (TypeError, ValueError):
                pass
        out = {
            "version": self.BACKTEST_EXPORT_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "source_strategy_id": cfg.id,
            "source_strategy_name": cfg.name,
            "backtest_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "last_equity_date": last_day,
            },
            "initial_cash_used": float(initial_cash),
            "skipped_initial_positions_not_in_pool": int(skipped_init),
            "dual_segment": bool(use_dual and cfg_b_dual),
            "seg2_strategy_name": (cfg_b_dual.name if cfg_b_dual else None),
            "final_cash": float(result.get("final_cash") or 0),
            "positions": positions_out,
            "last_mark_prices": last_prices_out,
        }
        return out

    @staticmethod
    def _normalize_batch_backtest_bundle(data: dict) -> Optional[dict]:
        """
        识别并规范化批量回测 JSON（version=2 + kind=batch_backtest + 非空 segments）。
        兼容：version 为 2.0 / \"2\"；kind 大小写与首尾空白；键名带 UTF-8 BOM（\ufeffversion）。
        """
        if not isinstance(data, dict):
            return None
        fixed: Dict[str, Any] = {}
        for k, v in data.items():
            nk = k
            if isinstance(nk, str) and nk and nk[0] == "\ufeff":
                nk = nk.lstrip("\ufeff")
            fixed[nk] = v
        data = fixed
        vr = data.get("version")
        try:
            v_int = int(float(vr))
        except (TypeError, ValueError):
            v_int = -1
        kind_norm = str(data.get("kind") or "").strip().lower()
        if v_int != 2 or kind_norm != "batch_backtest":
            return None
        segs = data.get("segments")
        if not isinstance(segs, list) or not segs:
            return None
        out = dict(data)
        out["version"] = 2
        out["kind"] = "batch_backtest"
        return out

    @staticmethod
    def _load_backtest_export_json(path: str) -> dict:
        # utf-8-sig：兼容带 BOM 的 JSON（避免首键变成 \ufeffversion 导致解析失败或取不到 version）
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("文件格式错误：根节点不是 JSON 对象")
        batch = StrategyGeneratorMainWindow._normalize_batch_backtest_bundle(data)
        if batch is not None:
            return batch
        if data.get("version") != 1:
            raise ValueError(f"不支持的导出版本: {data.get('version')!r}，需要 version=1 或批量 version=2")
        if "final_cash" not in data:
            raise ValueError("缺少字段 final_cash")
        if "positions" not in data or not isinstance(data.get("positions"), dict):
            raise ValueError("缺少或非法字段 positions")
        return data

    def _apply_backtest_export_to_form(self, data: dict):
        """将导出数据填入初始资金与初始持仓表"""
        cash = float(data.get("final_cash") or 0)
        cash = max(1000.0, min(cash, 999999999.0))
        self.backtest_initial_cash.setValue(int(round(cash)))
        t = self.backtest_init_positions_table
        t.setRowCount(0)
        positions = data.get("positions") or {}
        for code_6 in sorted(positions.keys()):
            pos = positions.get(code_6) or {}
            code_6 = (code_6 or "").strip().replace(".", "")[:6]
            if len(code_6) < 6 and code_6:
                code_6 = code_6.zfill(6)
            if len(code_6) != 6:
                continue
            vol = int((pos or {}).get("volume") or 0)
            cost = float((pos or {}).get("cost") or 0)
            if vol <= 0:
                continue
            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, QTableWidgetItem(code_6))
            t.setItem(row, 1, QTableWidgetItem(str(vol)))
            t.setItem(row, 2, QTableWidgetItem(str(round(cost, 4)) if cost else "0"))

    @staticmethod
    def _parse_iso_date_val(val) -> Optional[date]:
        if val is None:
            return None
        if isinstance(val, date):
            return val
        s = str(val).strip()
        if not s:
            return None
        return date.fromisoformat(s[:10])

    def _codes_from_export_positions(self, seg: dict) -> List[str]:
        out: List[str] = []
        for k, p in (seg.get("positions") or {}).items():
            try:
                vol = int((p or {}).get("volume") or 0)
            except (TypeError, ValueError):
                vol = 0
            if vol <= 0:
                continue
            code_6 = (str(k) or "").strip().replace(".", "")[:6]
            if len(code_6) < 6 and code_6:
                code_6 = code_6.zfill(6)
            if len(code_6) == 6:
                out.append(code_6)
        return out

    def _initial_positions_dict_from_segment(self, seg: dict) -> Dict[str, Dict[str, Any]]:
        initial_positions: Dict[str, Dict[str, Any]] = {}
        for k, p in (seg.get("positions") or {}).items():
            code_6 = (str(k) or "").strip().replace(".", "")[:6]
            if len(code_6) < 6 and code_6:
                code_6 = code_6.zfill(6)
            if len(code_6) != 6:
                continue
            try:
                vol = int((p or {}).get("volume") or 0)
            except (TypeError, ValueError):
                vol = 0
            try:
                cost = float((p or {}).get("cost") or 0)
            except (TypeError, ValueError):
                cost = 0.0
            if vol > 0:
                initial_positions[code_6] = {"volume": vol, "cost": cost}
        return initial_positions

    def _compute_chained_round_window(
        self, seg: dict, from_t1: bool, hold_n: int
    ) -> Tuple[Optional[date], Optional[date], str]:
        """
        按选股日起算本轮回测区间。

        若按选股日得到的起点 s_req 与「上一轮」导出区间 [start,end] 在日历上重叠
        （s_req <= end），则顺延起点：使用 max(s_req, 上一轮回测 start 的下一交易日)，
        再由此起算持有窗口。这样与批量导出里「每档 backtest_range.start 逐日推进」一致
        （例如上一轮首日 4/2，则本轮从 4/3 起），而不会误用「end 的下一日」把起点甩到
        整段持有结束之后（例如 end=4/10 周五时误到 4/13）。
        """
        try:
            from strategy_generator_app.trading_calendar import (
                backtest_window_from_selection_day,
                next_trading_day_after,
                trading_day_window_from_start,
            )
        except ImportError:
            from trading_calendar import (
                backtest_window_from_selection_day,
                next_trading_day_after,
                trading_day_window_from_start,
            )
        sel = self._parse_iso_date_val(seg.get("batch_selection_date"))
        br = seg.get("backtest_range") or {}
        prior_start = self._parse_iso_date_val(br.get("start"))
        prior_end = self._parse_iso_date_val(br.get("end")) or self._parse_iso_date_val(
            br.get("last_equity_date")
        )
        if not sel or prior_start is None or prior_end is None:
            return None, None, "缺少选股日或上一轮回测区间(start/end)"
        s_req, e_req, msg = backtest_window_from_selection_day(
            sel, start_next_trading_day=from_t1, hold_trading_days=hold_n
        )
        if s_req is None:
            return None, None, msg
        if s_req <= prior_end:
            n_after_prior_start = next_trading_day_after(prior_start)
            if n_after_prior_start is None:
                return None, None, "无法计算上一轮回测首日之后的交易日"
            ns = s_req if s_req > n_after_prior_start else n_after_prior_start
            s_eff, e_eff, w2 = trading_day_window_from_start(ns, hold_n)
            if s_eff is None:
                return None, None, w2 or "本轮回测区间不足"
            note = (
                f"与导出区间重叠，起点取 max(选股窗口首{s_req}, 上轮start次交易日{n_after_prior_start})→{s_eff}"
            )
            return s_eff, e_eff, note
        return s_req, e_req, ""

    def _pick_batch_segment_payload(
        self,
        segs: List[dict],
        title: str = "选择档位",
        label: str = "请选择其中一档（对应一个选股日）：",
    ) -> Optional[dict]:
        """从批量回测 segments 中选一段；仅一段时直接返回。"""
        if not segs:
            QMessageBox.warning(self, "提示", "没有可选择的档位。")
            return None
        if len(segs) == 1:
            return segs[0]
        labels = []
        for j, s in enumerate(segs):
            bd = s.get("batch_selection_date")
            if bd:
                labels.append(f"{bd} 〔第{j + 1}档〕")
            else:
                br = s.get("backtest_range") or {}
                labels.append(
                    f"{br.get('start', '?')} ~ {br.get('end', '?')} 〔第{j + 1}档〕"
                )
        choice, ok = QInputDialog.getItem(
            self, title, label, labels, len(labels) - 1, False
        )
        if not ok:
            return None
        try:
            idx = labels.index(choice)
        except ValueError:
            QMessageBox.warning(self, "提示", "未匹配到所选档位。")
            return None
        return segs[idx]

    def _on_load_buy_batch_into_current_strategy(self):
        """载入全部档位的上一轮批量回测结果，供「下一轮接续」批量回测（不再选择选股日）。"""
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.warning(self, "提示", "请先在左侧选中策略（一般为接续用的策略 B）。")
            return
        bundle = getattr(self, "_last_batch_export_bundle", None)
        segs = None
        if bundle and bundle.get("kind") == "batch_backtest" and bundle.get("segments"):
            src = bundle.get("source_strategy_name") or ""
            r = QMessageBox.question(
                self,
                "数据来源",
                f"检测到内存中有最近一次批量回测结果。\n来源策略：{src}\n\n"
                "是否直接使用？\n\n选「否」则从已保存的「导出批量回测(JSON)」文件载入。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if r == QMessageBox.Yes:
                segs = list(bundle.get("segments") or [])
        if segs is None:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "选择批量回测 JSON（version=2）",
                "",
                "JSON (*.json);;所有文件 (*.*)",
            )
            if not path:
                return
            try:
                data = self._load_backtest_export_json(path)
            except Exception as e:
                QMessageBox.warning(self, "无法读取", str(e))
                return
            batch_chk = self._normalize_batch_backtest_bundle(data)
            if batch_chk is None:
                hint = "请选择「导出批量回测(JSON)」生成的 version=2 文件（根节点含 kind=batch_backtest 与非空 segments）。"
                if data.get("version") == 1 and isinstance(data.get("positions"), dict):
                    hint += (
                        "\n\n当前文件为 version=1（单次接续快照），不能用于「载入上一轮批量回测」。"
                        "若在「智能突破买入」导出，请改用「导出批量接续 JSON(v2)…」；"
                        "若在策略 A 回测，请改用「导出批量回测(JSON)…」。"
                    )
                QMessageBox.warning(self, "提示", hint)
                return
            segs = list(data.get("segments") or [])
            if data.get("selection_file"):
                self._last_batch_selection_file = str(data.get("selection_file"))
                self._last_batch_export_bundle = data
        if not segs:
            QMessageBox.warning(self, "提示", "没有可用的档位数据。")
            return
        self._chained_batch_segments = segs
        all_codes: List[str] = []
        seen = set()
        for seg in segs:
            for c in self._codes_from_export_positions(seg):
                if c not in seen:
                    seen.add(c)
                    all_codes.append(c)
        # 与首轮「批量回测(选股文件)」一致：不依赖、也不回写当前策略股票池；
        # 下一轮跑数时直接用各档 segment 里的持仓代码。
        self.backtest_init_positions_table.setRowCount(0)
        # 载入档位时不必重抓成交表（可能上万行），只记初始持仓等轻量状态
        self._save_ui_state_for_strategy(sid, include_trades=False)
        if hasattr(self, "sell_chain_run_btn"):
            self.sell_chain_run_btn.setEnabled(True)
        QMessageBox.information(
            self,
            "已载入",
            f"已载入 {len(segs)} 档上一轮结果（每档含选股日、期末资金与持仓）。\n"
            f"涉及持仓代码共 {len(all_codes)} 只（仅用于下一轮回测，未写入股票池）。\n\n"
            "请在下方「下一轮接续」设置：起算规则（T 或 T+1）与持有交易日数，"
            "再点「运行下一轮批量回测」。",
        )

    def _on_run_chained_batch_backtest(self):
        """按已载入的每一档上一轮状态，用当前策略（策略 B）跑下一轮批量回测。"""
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.warning(self, "提示", "请先在左侧选中当前策略（一般为接续用的策略 B）。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            QMessageBox.warning(self, "提示", "未找到该策略配置。")
            return
        refreshed_main = self._refresh_cfg_from_disk(sid)
        if refreshed_main is not None:
            cfg = refreshed_main
        run_code, run_params = self._strategy_inputs_for_run(cfg)
        tp10_hint = self._tp10_effective_params_hint(run_code, run_params)
        segs = getattr(self, "_chained_batch_segments", None) or []
        if not segs:
            QMessageBox.warning(
                self,
                "提示",
                "请先点击「载入上一轮批量回测→本策略」或确保已载入 version=2 批量 JSON 的全部档位。",
            )
            return
        from_t1 = self.sell_chain_from_t1_cb.isChecked()
        hold_n = self.sell_chain_hold_spin.value()
        gen_t = self.backtest_generation_time.time()
        run_start_t = self.backtest_run_start_time.time()
        run_end_t = self.backtest_run_end_time.time()
        strategy_generation_time = f"{gen_t.hour():02d}:{gen_t.minute():02d}"
        strategy_run_start_time = f"{run_start_t.hour():02d}:{run_start_t.minute():02d}"
        strategy_run_end_time = f"{run_end_t.hour():02d}:{run_end_t.minute():02d}"

        use_dual = hasattr(self, "backtest_dual_cb") and self.backtest_dual_cb.isChecked()
        cfg_b_dual = None
        segments_dual = None
        carry_over_dual = False
        if use_dual:
            bid = self.backtest_seg2_combo.currentData()
            cfg_b = self._find_strategy_by_id(bid) if bid else None
            if not cfg_b:
                QMessageBox.warning(
                    self, "提示", "下一轮分时段批量：请在下方下拉框中选择第二段策略。"
                )
                return
            rb = self._refresh_cfg_from_disk(bid) if bid else None
            if rb is not None:
                cfg_b = rb
            cfg_b_dual = cfg_b
            gt2 = self.backtest_seg2_generation_time.time()
            rs2 = self.backtest_seg2_run_start.time()
            re2 = self.backtest_seg2_run_end.time()
            if rs2 != run_end_t:
                QMessageBox.warning(
                    self,
                    "提示",
                    "下一轮分时段批量要求：第二段「运行开始」= 第一段「运行结束」。\n\n"
                    f"第一段结束：{run_end_t.toString('HH:mm')}；第二段开始：{rs2.toString('HH:mm')}",
                )
                return
            if re2 <= rs2:
                QMessageBox.warning(
                    self, "提示", "第二段运行结束时间必须晚于运行开始时间。"
                )
                return
            carry_over_dual = bool(
                getattr(self, "backtest_dual_carry_cb", None)
                and self.backtest_dual_carry_cb.isChecked()
            )
            seg2_gen = f"{gt2.hour():02d}:{gt2.minute():02d}"
            seg2_start = f"{rs2.hour():02d}:{rs2.minute():02d}"
            seg2_end = f"{re2.hour():02d}:{re2.minute():02d}"
            segments_dual = [
                {
                    "strategy_code": run_code,
                    "strategy_params": dict(run_params),
                    "strategy_generation_time": strategy_generation_time,
                    "strategy_run_start_time": strategy_run_start_time,
                    "strategy_run_end_time": strategy_run_end_time,
                    "name": f"时段1·{cfg.name}",
                },
                {
                    "strategy_code": cfg_b.strategy_code or "",
                    "strategy_params": cfg_b.strategy_params or {},
                    "strategy_generation_time": seg2_gen,
                    "strategy_run_start_time": seg2_start,
                    "strategy_run_end_time": seg2_end,
                    "name": f"时段2·{cfg_b.name}",
                },
            ]

        if segments_dual:
            self._apply_sell_chain_scheduled_clear_time(segments_dual)
        elif strategy_uses_scheduled_clear(
            run_code, run_params, getattr(cfg, "name", "") or ""
        ):
            self._apply_sell_chain_scheduled_clear_time(run_params)

        self.sell_chain_run_btn.setEnabled(False)
        self.backtest_run_btn.setEnabled(False)
        self.backtest_batch_file_btn.setEnabled(False)
        self.backtest_result_text.setPlainText("下一轮批量回测运行中…")
        self.backtest_trades_table.setRowCount(0)
        QApplication.processEvents()
        try:
            try:
                from strategy_generator_app.backtest import (
                    run_backtest,
                    run_backtest_segmented,
                    compute_metrics,
                )
            except ImportError:
                from backtest import run_backtest, run_backtest_segmented, compute_metrics
            import pandas as pd
            get_name = _get_stock_name_fn() or (lambda c: "")
            summary_rows: List[dict] = []
            all_chain_trades: List[dict] = []
            hdr = (
                f"下一轮批量：共 {len(segs)} 档 | "
                f"{'起算T+1' if from_t1 else '起算T当日'} 持有 {hold_n} 个交易日（与导出区间重叠时按「上轮 start 次日」与选股窗口对齐顺延）"
            )
            if use_dual and cfg_b_dual:
                hdr += f" | 分时段（时段1「{cfg.name}」+ 时段2「{cfg_b_dual.name}」）"
            lines = [hdr, "=" * 60]
            if tp10_hint:
                lines.append(tp10_hint)
            n_seg = max(1, len(segs))
            bt_progress, bt_dlg = self._backtest_progress_dialog("批量回测（下一轮）")
            try:
                for i, seg in enumerate(segs):
                    sel_s = seg.get("batch_selection_date", "")
                    self.backtest_result_text.setPlainText(
                        f"下一轮批量 {i + 1}/{len(segs)}：选股日 {sel_s}…"
                    )
                    QApplication.processEvents()
                    start_d, end_d, note = self._compute_chained_round_window(seg, from_t1, hold_n)
                    if start_d is None or end_d is None:
                        summary_rows.append(
                            {
                                "选股日": str(sel_s),
                                "本轮回测开始": "",
                                "本轮回测结束": "",
                                "备注": note or "区间计算失败",
                            }
                        )
                        lines.append(f"{sel_s} | 跳过：{note}")
                        continue
                    icash = float(seg.get("final_cash") or 0)
                    init_pos = self._initial_positions_dict_from_segment(seg)
                    codes = self._codes_from_export_positions(seg)
                    if not codes and icash <= 0:
                        summary_rows.append(
                            {
                                "选股日": str(sel_s),
                                "本轮回测开始": start_d.strftime("%Y-%m-%d"),
                                "本轮回测结束": end_d.strftime("%Y-%m-%d"),
                                "备注": "无持仓且无现金",
                            }
                        )
                        lines.append(f"{sel_s} | 跳过：无持仓")
                        continue
                    slot_lo = 100.0 * i / n_seg
                    slot_hi = 100.0 * (i + 1) / n_seg
                    mid = slot_lo + 0.2 * (slot_hi - slot_lo)
                    tag = f"[{i + 1}/{n_seg}] "
                    pf_lines = self._run_backtest_preflight_ui(
                        self._backtest_preflight_union_codes(codes, init_pos),
                        start_d,
                        end_d,
                        status_prefix=f"预热 {sel_s}",
                        progress=self._scoped_backtest_progress(
                            bt_progress, slot_lo, mid, f"{tag}预热 "
                        ),
                    )
                    if pf_lines:
                        lines.append(f"{sel_s} | " + " | ".join(pf_lines[:2]))
                    bt_sub = self._scoped_backtest_progress(
                        bt_progress, mid, slot_hi, f"{tag}回测 "
                    )
                    try:
                        if use_dual and segments_dual:
                            result = run_backtest_segmented(
                                segments_dual,
                                codes,
                                start_d,
                                end_d,
                                initial_cash=icash,
                                get_stock_name=get_name,
                                use_engine_form=False,
                                use_tick_level=True,
                                initial_positions=init_pos if init_pos else None,
                                carry_over_pending_intents=carry_over_dual,
                                progress=bt_sub,
                            )
                        else:
                            bt_params = dict(run_params)
                            if strategy_uses_scheduled_clear(
                                run_code, bt_params, getattr(cfg, "name", "") or ""
                            ):
                                self._apply_sell_chain_scheduled_clear_time(bt_params)
                            else:
                                strip_scheduled_clear_params(bt_params)
                            result = run_backtest(
                                strategy_code=run_code,
                                strategy_params=bt_params,
                                stock_codes_6=codes,
                                start_date=start_d,
                                end_date=end_d,
                                initial_cash=icash,
                                get_stock_name=get_name,
                                use_engine_form=False,
                                use_tick_level=True,
                                strategy_generation_time=strategy_generation_time,
                                strategy_run_start_time=strategy_run_start_time,
                                strategy_run_end_time=strategy_run_end_time,
                                initial_positions=init_pos if init_pos else None,
                                progress=bt_sub,
                            )
                    except Exception as e:
                        lines.append(f"{sel_s} | 回测异常：{e}")
                        summary_rows.append(
                            {
                                "选股日": str(sel_s),
                                "本轮回测开始": start_d.strftime("%Y-%m-%d"),
                                "本轮回测结束": end_d.strftime("%Y-%m-%d"),
                                "备注": f"异常：{e}",
                            }
                        )
                        continue
                    metrics = compute_metrics(
                        result.get("equity_curve") or [],
                        result.get("trades") or [],
                        icash,
                        result.get("final_positions"),
                        result.get("last_prices"),
                        initial_positions=init_pos if init_pos else None,
                        buy_and_hold_total=result.get("buy_and_hold_total"),
                    )
                    tr = metrics.get("total_return", 0) * 100
                    tc = metrics.get("trade_count", 0)
                    ft = metrics.get("final_total", icash)
                    diag_remark = _summarize_backtest_diagnosis(result, tc)
                    if note:
                        diag_remark = f"{note}；{diag_remark}" if diag_remark else note
                    tick_cols = _batch_summary_tick_columns(result)
                    summary_rows.append(
                        {
                            "选股日": str(sel_s),
                            "本轮回测开始": start_d.strftime("%Y-%m-%d"),
                            "本轮回测结束": end_d.strftime("%Y-%m-%d"),
                            **tick_cols,
                            "总收益率%": round(tr, 4),
                            "成交笔数": tc,
                            "期末总资产": round(ft, 2),
                            "备注": diag_remark,
                        }
                    )
                    tick_short = tick_cols.get("tick覆盖(池)") or ""
                    if tick_short:
                        line_extra = f" | tick(池){tick_short}"
                        if tick_cols.get("缺tick代码"):
                            line_extra += f" 缺:{tick_cols['缺tick代码'][:60]}{'…' if len(tick_cols['缺tick代码']) > 60 else ''}"
                    else:
                        line_extra = f" | {diag_remark}" if diag_remark else ""
                    lines.append(
                        f"{sel_s} | [{start_d}~{end_d}] {note or ''} | 收益 {tr:.2f}% | 成交 {tc} | 期末 {ft:,.0f}{line_extra}"
                    )
                    day_label = str(sel_s).strip() or "?"
                    sd_s = start_d.strftime("%Y-%m-%d")
                    ed_s = end_d.strftime("%Y-%m-%d")
                    sold_codes = set()
                    for t in result.get("trades") or []:
                        td = dict(t)
                        c6 = str(td.get("code") or "").strip().replace(".", "")[:6]
                        if c6 and td.get("side") == "sell":
                            sold_codes.add(c6.zfill(6))
                        ti = (td.get("trigger_info") or "").strip()
                        td["trigger_info"] = f"[选股日 {day_label}] {ti}".strip()
                        td["选股日"] = (str(sel_s).strip()[:10] if sel_s else "")
                        td["start_date"] = sd_s
                        td["end_date"] = ed_s
                        all_chain_trades.append(td)
                    # 下一轮接续：若某票本轮没有任何卖出成交，则补一条 0 股卖出占位，
                    # 让卖出明细中也携带该票 end_date，便于后续汇总按卖出 end_date 盯市。
                    fp = result.get("final_positions") or {}
                    for c in codes:
                        c6 = str(c or "").strip().replace(".", "")[:6]
                        if not c6:
                            continue
                        c6 = c6.zfill(6)
                        if c6 in sold_codes:
                            continue
                        p = fp.get(c6) or fp.get(c6 + ".SH") or fp.get(c6 + ".SZ") or {}
                        pos_after = int((p or {}).get("volume") or (init_pos.get(c6, {}) or {}).get("volume") or 0)
                        all_chain_trades.append(
                            {
                                "date": ed_s,
                                "time": "15:00:00",
                                "code": c6,
                                "side": "sell",
                                "price": 0,
                                "volume": 0,
                                "amount": 0,
                                "position_after": pos_after,
                                "trigger_info": f"[选股日 {day_label}] 汇总占位: 本轮无卖出成交，补记0股卖出以记录end_date",
                                "选股日": (str(sel_s).strip()[:10] if sel_s else ""),
                                "start_date": sd_s,
                                "end_date": ed_s,
                            }
                        )
            finally:
                try:
                    bt_progress("批量回测完成", 100)
                    bt_dlg.setValue(100)
                    bt_dlg.close()
                except Exception:
                    pass
            if all_chain_trades:
                lines.append("")
                lines.append(
                    f"成交明细：共 {len(all_chain_trades)} 笔，已填入下方表格；"
                    "「触发信息」列前缀 [选股日 …] 标明该笔所属档。"
                )
            sel_path = ""
            bundle = getattr(self, "_last_batch_export_bundle", None) or {}
            sel_path = str(bundle.get("selection_file") or "").strip()
            if not sel_path:
                sel_path = str(getattr(self, "_last_batch_selection_file", "") or "").strip()
            stock_summary_rows: List[dict] = []
            if sel_path and all_chain_trades:
                from pathlib import Path

                from tools.merge_backtest_trades_by_selection import apply_selection_file_fields

                stock_summary_rows = _unique_traded_rows_for_selection_copy(all_chain_trades)
                apply_selection_file_fields(stock_summary_rows, Path(sel_path))
            if stock_summary_rows:
                lines.append(
                    f"有交易股票：{len(stock_summary_rows)} 只（保存时从选股文件复制全部列）。"
                )
            sdf = pd.DataFrame(summary_rows)
            self.backtest_result_text.setPlainText(
                "\n".join(lines) + "\n\n—— 下一轮批量汇总 ——\n" + sdf.to_string(index=False)
            )
            self._fill_backtest_trades_table(all_chain_trades)
            if summary_rows:
                save_ask = QMessageBox.question(
                    self,
                    "完成",
                    "是否保存下一轮批量汇总为 Excel？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if save_ask == QMessageBox.Yes:
                    default_name = os.path.join(
                        os.path.dirname(__file__),
                        f"下一轮批量回测汇总_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    )
                    save_path, _ = QFileDialog.getSaveFileName(
                        self,
                        "保存下一轮批量汇总",
                        default_name,
                        "Excel (*.xlsx)",
                    )
                    if save_path:
                        try:
                            import pandas as pd

                            export_rows = _batch_excel_summary_rows(
                                summary_rows, stock_summary_rows
                            )
                            with pd.ExcelWriter(save_path, engine="openpyxl") as w:
                                pd.DataFrame(export_rows).to_excel(
                                    w, index=False, sheet_name="汇总"
                                )
                            QMessageBox.information(
                                self,
                                "已保存",
                                f"汇总表：\n{save_path}\n共 {len(export_rows)} 行",
                            )
                        except Exception as ex:
                            QMessageBox.warning(self, "保存失败", str(ex))
        except Exception as e:
            QMessageBox.critical(self, "下一轮批量回测", str(e))
            self.backtest_result_text.setPlainText(f"下一轮批量失败：{e}")
            self._fill_backtest_trades_table([])
        finally:
            self.sell_chain_run_btn.setEnabled(bool(self._chained_batch_segments))
            self.backtest_run_btn.setEnabled(True)
            self.backtest_batch_file_btn.setEnabled(True)

    def _merge_codes_into_pool(self, codes: list) -> int:
        """将代码合并到当前策略股票池，返回新加入数量"""
        existing = set(self._get_pool_codes_from_list())
        added = 0
        for code in codes:
            code = (code or "").strip()
            if len(code) < 6:
                code = code.zfill(6)
            if len(code) != 6 or code in existing:
                continue
            item = QListWidgetItem(_code_display_text(code))
            item.setData(Qt.UserRole, code)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.pool_list.addItem(item)
            existing.add(code)
            added += 1
        if added > 0:
            self._save_pool_codes_silent()
        return added

    def _refresh_backtest_export_button(self):
        sid = self._get_selected_strategy_id()
        if not sid or not hasattr(self, "backtest_export_last_btn"):
            return
        self.backtest_export_last_btn.setEnabled(bool(self._backtest_export_by_strategy.get(sid)))
        if hasattr(self, "backtest_export_batch_json_btn"):
            bundle = getattr(self, "_last_batch_export_bundle", None)
            # 与当前策略 id 无关：策略 A 跑完批量后切换到策略 B 仍可导出内存中的批量 JSON
            self.backtest_export_batch_json_btn.setEnabled(
                bool(bundle) and bool((bundle or {}).get("segments"))
            )

    def _on_export_last_backtest_result(self):
        sid = self._get_selected_strategy_id()
        data = self._backtest_export_by_strategy.get(sid) if sid else None
        if not data:
            QMessageBox.information(
                self,
                "导出",
                "当前策略没有可导出的回测快照。\n请先在本策略下成功运行「运行回测」，"
                "或「批量回测(选股文件)」且至少有一档成功（导出内容对应最后一档成功选股日）。",
            )
            return
        safe_name = re.sub(r'[<>:"/\\\\|?*]', "_", data.get("source_strategy_name") or "strategy")
        last_d = (data.get("backtest_range") or {}).get("last_equity_date") or "export"
        default_fn = f"backtest_export_{safe_name}_{last_d}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出回测结果", default_fn, "JSON (*.json);;所有文件 (*.*)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "导出", f"已导出：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_import_from_backtest_export_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择回测导出 JSON", "", "JSON (*.json);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            data = self._load_backtest_export_json(path)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        if self._normalize_batch_backtest_bundle(data) is not None:
            segs = data.get("segments") or []
            picked = self._pick_batch_segment_payload(
                segs,
                "选择导入哪一档",
                "批量回测 JSON 含多档选股日，请选择要填入「初始资金/初始持仓」的一档：",
            )
            if picked is None:
                return
            data = picked
        self._apply_backtest_export_to_form(data)
        # 自动同步到股票池（去重、自动保存）
        try:
            self._on_add_init_positions_to_pool()
        except Exception:
            pass
        sid = self._get_selected_strategy_id()
        if sid:
            self._save_ui_state_for_strategy(sid)
        npos = len([c for c, p in (data.get("positions") or {}).items() if int((p or {}).get("volume") or 0) > 0])
        QMessageBox.information(
            self,
            "导入完成",
            f"已导入期末现金 {data.get('final_cash')} 元，有效持仓 {npos} 条，并已同步到股票池。\n",
        )

    def _on_add_init_positions_to_pool(self):
        """把回测「初始持仓」表中的代码合并到当前策略股票池（与是否来自导出文件无关）。"""
        t = self.backtest_init_positions_table
        codes = []
        for row in range(t.rowCount()):
            it = t.item(row, 0)
            if not it:
                continue
            code_6 = (it.text() or "").strip().replace(".", "")[:6]
            if len(code_6) < 6 and code_6:
                code_6 = code_6.zfill(6)
            if len(code_6) != 6:
                continue
            codes.append(code_6)
        if not codes:
            QMessageBox.information(self, "提示", "初始持仓表中没有有效的股票代码。")
            return
        added = self._merge_codes_into_pool(codes)
        if added > 0:
            QMessageBox.information(self, "股票池", f"已加入 {added} 只股票到股票池，已自动保存。")
        else:
            QMessageBox.information(self, "股票池", "这些代码已在股票池中，未重复添加。")

    def _on_pool_add_codes(self):
        """从输入框解析代码并追加到列表末尾（已存在的跳过）"""
        text = self.pool_add_edit.text().strip()
        if not text:
            QMessageBox.information(self, "提示", "请输入要添加的股票代码。")
            return
        codes = parse_codes_text(text)
        if not codes:
            QMessageBox.warning(self, "提示", "未解析到有效的 6 位股票代码。")
            return
        existing = set(self._get_pool_codes_from_list())
        added = 0
        for code in codes:
            if code and code not in existing:
                item = QListWidgetItem(_code_display_text(code))
                item.setData(Qt.UserRole, code)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                self.pool_list.addItem(item)
                existing.add(code)
                added += 1
        self.pool_add_edit.clear()
        if added > 0:
            self._save_pool_codes_silent()
            QMessageBox.information(self, "添加", f"已添加并自动保存 {added} 只。")
        else:
            QMessageBox.information(self, "添加", "这些代码已在列表中，未重复添加。")

    def _on_pool_sort_by_code(self):
        """按股票代码升序排序"""
        codes = self._get_pool_codes_from_list()
        if not codes:
            return
        codes.sort()
        self._fill_pool_list(codes, set_original=False)
        self._save_pool_codes_silent()

    def _on_pool_restore_order(self):
        """恢复为加载时的原始顺序"""
        if not self._pool_original_order:
            return
        current = self._get_pool_codes_from_list()
        if not current:
            self._fill_pool_list(self._pool_original_order, set_original=True)
            return
        # 按原始顺序排列；当前有但原始没有的排在最后
        orig_set = set(self._pool_original_order)
        ordered = [c for c in self._pool_original_order if c in current]
        ordered += [c for c in current if c not in orig_set]
        self._fill_pool_list(ordered, set_original=False)
        self._save_pool_codes_silent()

    def _on_save_params(self):
        """保存当前策略的运行参数（策略参数 Tab）"""
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.information(self, "提示", "请先在左侧选择一个策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            return
        cfg.strategy_params = {**(cfg.strategy_params or {}), **self._params_from_form()}
        save_strategy(cfg)
        self._dirty_sections.discard("params")
        self._refresh_dirty_ui()
        if not self._dirty_sections:
            self._last_saved_snapshot = self._take_snapshot()
        QMessageBox.information(self, "完成", "策略参数已保存。")

    def _on_save_logic_params(self):
        """保存当前策略的 Python 策略代码"""
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.information(self, "提示", "请先在左侧选择一个策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            return
        disk = load_strategy_by_id(sid)
        if disk is not None:
            cfg.strategy_params = dict(disk.strategy_params or {})
        cfg.strategy_code = self.logic_code_edit.toPlainText().strip()
        save_strategy(cfg)
        self._dirty_sections.discard("logic")
        self._refresh_dirty_ui()
        if not self._dirty_sections:
            self._last_saved_snapshot = self._take_snapshot()
        QMessageBox.information(self, "完成", "策略代码已保存。")

    def _append_run_log(self, text):
        """向运行日志框追加一行或多行文本，并滚动到底部；内容超出一页时自动出现滚动条。"""
        if not hasattr(self, "run_output_edit") or self.run_output_edit is None:
            return
        edit = self.run_output_edit
        edit.moveCursor(QTextCursor.End)
        if text and not text.endswith("\n"):
            text = text + "\n"
        edit.insertPlainText(text)
        edit.moveCursor(QTextCursor.End)
        sb = edit.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())
        QApplication.processEvents()

    def _on_preview_tasks(self, quiet: bool = False):
        """执行当前策略代码（或表单逻辑），拉行情后生成任务并填充预览表格，同时收集 print 输出到运行日志框"""
        # 手动点击运行时，才清空上一次日志与预览；定时触发保留历史记录，便于连续查看。
        if not quiet and hasattr(self, "run_output_edit") and self.run_output_edit is not None:
            self.run_output_edit.clear()
        if (not quiet) and hasattr(self, "preview_table") and self.preview_table is not None:
            self.preview_table.setRowCount(0)
        if not quiet:
            self._preview_intents = []
            self._preview_task_list = []

        sid = self._get_selected_strategy_id()
        if not sid:
            if quiet:
                self._append_run_log("[定时运行] 未选中策略，跳过。")
            else:
                QMessageBox.information(self, "提示", "请先在左侧选择一个策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            if quiet:
                self._append_run_log(f"[定时运行] 未找到策略配置 id={sid}，跳过。")
            return
        codes = self._get_pool_codes_from_list()
        if not codes:
            if quiet:
                self._append_run_log("[定时运行] 股票池为空，跳过运行。")
            else:
                QMessageBox.information(self, "提示", "当前策略股票池为空，请先在「股票池」中添加代码。")
            return
        get_name = _get_stock_name_fn()

        run_start_time = time.time()
        run_start_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append_run_log(f"开始运行  {run_start_str}")
        self._append_run_log(f"诊断用时钟（北京时间）：{_beijing_now_diag_str()}")
        self._append_run_log("正在拉取行情与关键价格…")
        self._append_run_log("（builtin：等待 results.json + daily_cache，请勿重复点击）")

        # 拉行情会写入 strategy_pool_watch；整次运行无论成功/失败都必须释放。
        price_map: Dict[str, Any] = {}
        try:
            # 拉行情并计算关键价格点（当前价、昨收、涨跌停、均线重合点、前高前低、布林带、今开最高最低等）
            try:
                price_map, key_errors = get_prices_with_key_points(codes)
                for err_line in key_errors:
                    self._append_run_log(err_line)
                if not price_map and any("行情未就绪" in str(e) for e in key_errors):
                    if quiet:
                        self._append_run_log("[定时运行] 行情未就绪，已跳过本次运行。")
                    else:
                        QMessageBox.warning(
                            self,
                            "行情未就绪",
                            "大 QMT 尚未推送股票池现价，请确认模型交易已启动后重试。",
                        )
                    return
            except Exception as e:
                self._append_run_log(f"拉取行情/关键价格异常: {e}")
                try:
                    price_map = fetch_prices(codes) if codes else {}
                    key_errors = []
                except Exception as e2:
                    price_map = {}
                    self._append_run_log(f"备用行情获取失败: {e2}")
                if not price_map:
                    msg = f"拉取行情异常：{e}\n策略代码将收到空 prices。"
                    if quiet:
                        self._append_run_log(f"[定时运行] 行情获取失败：{msg}")
                    else:
                        QMessageBox.warning(self, "行情获取失败", msg)
                else:
                    msg = f"关键价格计算异常：{e}\n已仅使用当前价、昨收。"
                    if quiet:
                        self._append_run_log(f"[定时运行] 部分数据异常：{msg}")
                    else:
                        QMessageBox.warning(self, "部分数据异常", msg)

            # 追加进度：行情已就绪，正在执行策略
            self._append_run_log("行情与关键价格已就绪，正在执行策略代码…")
            # 打印每只股票的关键价格（含今开/今日高低/均线），便于两台机复制对比
            self._append_run_log("【行情快照】各股关键字段（策略判断依赖这些值）：")
            for code_6 in sorted(price_map.keys()):
                p = price_map.get(code_6) or {}
                self._append_run_log(_preview_price_fields_one_line(code_6, p))

            buf = io.StringIO()
            task_list = []
            code_str, params = self._strategy_inputs_for_run(cfg)
            tp10_hint = self._tp10_effective_params_hint(code_str, params)
            if tp10_hint:
                self._append_run_log(tp10_hint)
            account = get_account_info()  # {"total_asset": 总资金, "cash": 可用资金}
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self._apply_live_runtime_strategy_params(params, cfg)
            if strategy_uses_scheduled_clear(
                cfg.strategy_code or "", cfg.strategy_params, getattr(cfg, "name", "") or ""
            ):
                _inject_code_sell_day_index(params, codes, root)
            # 注入各股票可用持仓（如「持仓卖出」等策略通过 params["positions"] 使用）
            positions_debug_info = ""
            try:
                # 用 debug 版本定位“共0只”的原因（配置/连接/查询为空/可用量为0）
                params["positions"], positions_debug_info = get_positions_with_volume_debug()
            except Exception as e:
                params["positions"] = {}
                positions_debug_info = f"注入持仓查询异常: {type(e).__name__}: {e}"

            def _norm_code_6(code: str) -> str:
                s = (code or "").strip().split(".")[0]
                s = s.zfill(6) if len(s) < 6 and s else s[:6]
                return s

            positions = params.get("positions") or {}
            positions_ge_100 = {c: int(v or 0) for c, v in positions.items() if int(v or 0) >= 100}
            pos_count = len(positions_ge_100)

            pool_codes_6 = [_norm_code_6(c) for c in (codes or []) if (c or "").strip()]
            intersect_count = len(set(pool_codes_6) & set(positions_ge_100.keys()))
            top_vol = sorted(((c, int(v or 0)) for c, v in positions.items()), key=lambda kv: kv[1], reverse=True)[:8]
            top_vol_str = ", ".join([f"{k}:{v}" for k, v in top_vol]) if top_vol else ""

            self._append_run_log(
                f"注入持仓: 共 {len(positions)} 只，其中可用>=100股的有 {pos_count} 只；与当前股票池交集可用>=100为 {intersect_count} 只"
            )
            if top_vol_str:
                self._append_run_log(f"注入持仓 TopVol: {top_vol_str}（单位：股，来自 can_use_volume）")

            # 只有在持仓卖出等策略且可用持仓为空时，才输出详细诊断并弹窗（买入策略不依赖持仓）
            needs_positions = strategy_uses_positions(
                cfg.strategy_code or "",
                cfg.strategy_params,
                getattr(cfg, "name", "") or "",
            )
            if needs_positions and ((not positions) or pos_count == 0):
                if positions_debug_info:
                    self._append_run_log("注入持仓调试信息:\n" + positions_debug_info)
                    # 同时弹出对话框，避免用户只看表格不看日志导致“注入失败原因不明”
                    detail = positions_debug_info.strip()
                    if len(detail) > 1200:
                        detail = detail[:1200] + "\n...(已截断，请查看运行日志获取完整内容)..."
                    if quiet:
                        self._append_run_log(f"[定时运行] 注入持仓失败/无可用持仓：\n{detail}")
                    else:
                        QMessageBox.warning(self, "注入持仓失败/无可用持仓", detail)
                else:
                    msg2 = "未拿到持仓数据（positions 为空）。请检查大 QMT 内置策略是否写入 data/results.json，以及 account_id 是否已配置。"
                    if quiet:
                        self._append_run_log(f"[定时运行] 注入持仓失败/无可用持仓：{msg2}")
                    else:
                        QMessageBox.warning(self, "注入持仓失败/无可用持仓", msg2)
            try:
                with redirect_stdout(buf):
                    if code_str:
                        # 执行用户策略代码（传入 account、params，客户可在策略参数 Tab 中配置）
                        intents = run_user_strategy(
                            code_str,
                            codes,
                            price_map,
                            get_name,
                            account,
                            params,
                            strategy_name=getattr(cfg, "name", None),
                        )
                        intents = strip_unwanted_scheduled_clear_intents(
                            intents,
                            code_str,
                            cfg.strategy_params,
                            getattr(cfg, "name", "") or "",
                        )
                    else:
                        # 无代码时退化为简单逻辑：全池单点买入、价格=0（仅作占位）
                        params = {"rule_type": "single_buy", "price": 0, "volume": 1000}
                        intents = engine_run_strategy(
                            codes, get_name, params, price_map=price_map or None
                        )
                    # 预览表格按条显示（每只股票多条规则则多行），便于查看；写入文件时再按股票合并
                    self._preview_intents = intents
                    task_list = [build_task_dict(it) for it in intents]
            except RuntimeError as e:
                # 用户策略执行错误（定时模式下不弹阻塞对话框，避免卡住串行调度）
                if quiet:
                    self._append_run_log(f"[定时运行] 策略执行错误: {e}")
                else:
                    QMessageBox.critical(self, "策略执行错误", str(e))
                # 下面 finally 会追加输出与“运行结束”，无需在此覆盖
                return
            finally:
                # 追加策略的 print 输出与运行结束、总耗时（不覆盖，便于保留完整日志）
                output_text = buf.getvalue()
                if output_text.strip():
                    self._append_run_log(output_text.rstrip())
                elapsed = time.time() - run_start_time
                self._append_run_log(f"运行结束，总耗时 {elapsed:.1f} 秒")

            intents_done = getattr(self, "_preview_intents", None) or []
            if not intents_done:
                strat_name = (getattr(cfg, "name", None) or "").strip()
                self._append_run_log("【诊断】本次未生成任何任务，逐项原因如下：")
                self._append_run_log(f"  策略名称：{strat_name or sid}")
                is_brk = "突破5日线" in strat_name
                for code_6 in sorted(price_map.keys()):
                    p = price_map.get(code_6) or {}
                    if is_brk:
                        self._append_run_log(
                            f"  {code_6} → {_diagnose_breakthrough_5day_10m(code_6, p, params)}"
                        )
                    else:
                        self._append_run_log(
                            f"  {code_6} → 无内置条件诊断；请对照「参数与逻辑」中的策略代码与上方【行情快照】核对"
                        )

            self._preview_task_list = task_list
            self.preview_table.setRowCount(len(task_list))
            _rule_display = {
                "single_buy": "单点买入", "breakthrough_buy": "突破买入", "single_sell": "单点卖出",
                "cage_buy": "笼子买入", "cage_sell": "笼子卖出",
                "best_buy": "弹性买入", "best_sell": "弹性卖出",
            }
            for i, task in enumerate(task_list):
                params = task.get("params") or {}
                rules = params.get("rules") or []
                first_rule = rules[0] if rules else {}
                rule_name = first_rule.get("name", "单点买入") if rules else "单点买入"
                # 规则类型字段在 rule dict 里是 "type"
                rule_type = first_rule.get("type") or ""
                # 预览里的“价格”列，尽量展示规则真正用到的关键价位：
                display_price = ""
                if rule_type in ("single_buy", "single_sell", "breakthrough_buy", "breakthrough_sell", "scheduled_clear"):
                    display_price = first_rule.get("price", "")
                elif rule_type in ("cage_buy", "cage_sell"):
                    # 笼子规则显示上沿价，便于和 5/10 日线重合点核对
                    display_price = first_rule.get("price_high", "")
                elif rule_type in ("best_buy", "best_sell"):
                    display_price = first_rule.get("trigger_price", "")
                else:
                    display_price = task.get("init_cost", 0)
                self.preview_table.setItem(i, 0, QTableWidgetItem(task.get("stock_code", "")))
                self.preview_table.setItem(i, 1, QTableWidgetItem(task.get("stock_name", "")))
                self.preview_table.setItem(i, 2, QTableWidgetItem(rule_name))
                self.preview_table.setItem(i, 3, QTableWidgetItem(str(display_price)))
                self.preview_table.setItem(i, 4, QTableWidgetItem(str(task.get("init_volume", 0))))
            if task_list:
                msg_done = f"已生成 {len(task_list)} 条待执行任务，可点击「生成任务」写入任务文件。"
                if quiet:
                    self._append_run_log(f"[定时运行] {msg_done}")
                else:
                    QMessageBox.information(self, "运行完成", msg_done)
            else:
                msg_empty = "未生成任何任务。"
                if quiet:
                    self._append_run_log(f"[定时运行] {msg_empty}")
                else:
                    QMessageBox.information(self, "运行完成", msg_empty)
        finally:
            self._release_strategy_pool_watch_after_run()

    def _on_export_tasks(self, quiet: bool = False):
        """将当前预览的任务写入主程序任务文件（按股票合并：每只股票一条记录，含该股全部规则）"""
        intents = getattr(self, "_preview_intents", None) or []
        if not intents:
            if quiet:
                self._append_run_log("[定时运行] 无任务意图，跳过「生成任务」写入。")
            else:
                QMessageBox.information(self, "提示", "请先点击「运行」生成任务列表。")
            return
        root = self._project_root()
        try:
            sid = self._get_selected_strategy_id()
            cfg = self._find_strategy_by_id(sid) if sid else None
            sell_hold_n = None
            if cfg:
                sp = cfg.strategy_params or {}
                raw_n = sp.get("scheduled_clear_on_sell_day") or sp.get("sell_hold_trading_days")
                if raw_n is not None:
                    try:
                        sell_hold_n = int(raw_n)
                    except (TypeError, ValueError):
                        sell_hold_n = None
            codes = [(it.get("stock_code") or "").strip() for it in intents]
            buy_dates = _load_task_buy_dates_by_code(root, codes)
            merged_tasks = build_tasks_from_intents(
                intents,
                buy_dates_by_code=buy_dates,
                sell_hold_trading_days=sell_hold_n,
            )
            drop_clear_on_merge = bool(
                cfg
                and not strategy_uses_scheduled_clear(
                    cfg.strategy_code or "",
                    cfg.strategy_params,
                    getattr(cfg, "name", "") or "",
                )
            )
            path = write_tasks_to_excel(
                merged_tasks,
                root,
                append=True,
                drop_scheduled_clear_on_merge=drop_clear_on_merge,
            )
            if quiet:
                self._append_run_log(f"[定时运行] 已写入 {len(merged_tasks)} 条任务到：{path}")
            else:
                QMessageBox.information(self, "完成", f"已写入 {len(merged_tasks)} 条任务（按股票合并）到：\n{path}")
        except Exception as e:
            if quiet:
                self._append_run_log(f"[定时运行] 写入任务失败: {type(e).__name__}: {e}")
            else:
                QMessageBox.critical(self, "错误", f"写入失败：{e}")

    def _fill_backtest_trades_table(self, trades: List[dict]) -> None:
        """将回测成交列表填入下方表格（含选股日/start_date/end_date，供导出 CSV 与合并汇总）。"""
        trades = list(trades or [])
        self._backtest_trades_data = trades
        table = self.backtest_trades_table
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        try:
            table.setRowCount(len(trades))
            get_name = _get_stock_name_fn() or (lambda c: "")
            name_cache: Dict[str, str] = {}
            tb_cols = (
                "真突破①量均量比",
                "真突破①通过",
                "真突破②委卖委买比",
                "真突破②通过",
                "真突破③量被吃卖档比",
                "真突破③通过",
                "真突破③被吃档数",
            )
            for i, t in enumerate(trades):
                code_6 = str(t.get("code", "") or "").strip().replace(".SH", "").replace(".SZ", "")
                if len(code_6) < 6:
                    code_6 = code_6.zfill(6) if code_6 else ""
                name = str(t.get("stock_name", "") or "").strip()
                if not name and code_6:
                    if code_6 not in name_cache:
                        try:
                            name_cache[code_6] = (get_name(code_6) or "").strip() or "未知名称"
                        except Exception:
                            name_cache[code_6] = "未知名称"
                    name = name_cache[code_6]
                table.setItem(i, 0, QTableWidgetItem(str(t.get("date", "") or "")))
                table.setItem(i, 1, QTableWidgetItem(str(t.get("time", "") or "")))
                table.setItem(i, 2, QTableWidgetItem(str(t.get("code", "") or "")))
                table.setItem(i, 3, QTableWidgetItem(name))
                table.setItem(i, 4, QTableWidgetItem(str(t.get("选股日", "") or "")))
                table.setItem(
                    i, 5, QTableWidgetItem("买入" if t.get("side") == "buy" else "卖出")
                )
                table.setItem(i, 6, QTableWidgetItem(str(t.get("price", "") or "")))
                table.setItem(i, 7, QTableWidgetItem(str(t.get("volume", "") or "")))
                table.setItem(i, 8, QTableWidgetItem(str(t.get("amount", "") or "")))
                table.setItem(i, 9, QTableWidgetItem(str(t.get("position_after", "") or "")))
                table.setItem(i, 10, QTableWidgetItem(str(t.get("trigger_info", "") or "")))
                table.setItem(i, 11, QTableWidgetItem(str(t.get("start_date", "") or "")))
                table.setItem(i, 12, QTableWidgetItem(str(t.get("end_date", "") or "")))
                for j, key in enumerate(tb_cols):
                    table.setItem(i, 13 + j, QTableWidgetItem(str(t.get(key, "") or "")))
        finally:
            table.blockSignals(False)
            table.setUpdatesEnabled(True)

    def _refresh_cfg_from_disk(self, sid: str):
        """回测前从磁盘重载该 id 的策略并写回 self._strategies，使手改 JSON 的 strategy_params 立即参与回测。"""
        sid = (sid or "").strip()
        if not sid:
            return None
        try:
            cfg_new = load_strategy_by_id(sid)
        except Exception:
            cfg_new = None
        if cfg_new is None:
            return None
        lst = getattr(self, "_strategies", None) or []
        for i, c in enumerate(lst):
            if c.id == sid:
                lst[i] = cfg_new
                break
        return cfg_new

    def _strategy_inputs_for_run(self, cfg: StrategyConfig) -> Tuple[str, Dict[str, Any]]:
        """回测/运行：参数从磁盘 strategy_params 读取；逻辑优先用当前编辑器内容（与界面一致）。"""
        sid = (getattr(cfg, "id", "") or "").strip()
        disk = self._refresh_cfg_from_disk(sid)
        if disk is not None:
            cfg = disk
        if self._get_selected_strategy_id() == sid and hasattr(self, "logic_code_edit"):
            code = (self.logic_code_edit.toPlainText() or "").strip()
        else:
            code = (cfg.strategy_code or "").strip()
        params = {**(cfg.strategy_params or {}), **self._params_from_form()}
        return code, params

    def _tp10_effective_params_hint(self, code: str, params: Dict[str, Any]) -> str:
        """提示本次运行实际采用的 tp10 / 跌破昨收止损 参数来源，便于核对「改参是否生效」。"""
        hints: List[str] = []
        tp10_keys = (
            "tp10_ratio_low",
            "tp10_up_low",
            "tp10_up_high",
            "tp10_drop_low",
            "tp10_drop_high",
            "tp10_blend_low",
            "tp10_blend_high",
        )
        tp10_parts = [f"{k}={params[k]}" for k in tp10_keys if k in (params or {})]
        if tp10_parts:
            tp10_local = bool(
                re.search(r"^\s*_tp10_ov\s*=\s*dict\s*\(", code or "", re.MULTILINE)
            )
            tp10_src = (
                "代码内 _tp10_ov=dict(...)（覆盖 JSON 参数）"
                if tp10_local
                else "strategy_params / JSON"
            )
            hints.append(f"【tp10】来源: {tp10_src} | " + ", ".join(tp10_parts))
        loss_key = "intraday_loss_stop_pct"
        loss_in_code = "_loss_stop(" in (code or "")
        loss_in_params = loss_key in (params or {})
        if loss_in_code or loss_in_params:
            loss_local = bool(
                re.search(r"^\s*_loss_stop_ov\s*=\s*dict\s*\(", code or "", re.MULTILINE)
            )
            loss_src = (
                "代码内 _loss_stop_ov=dict(...)（覆盖 JSON 参数）"
                if loss_local
                else "strategy_params / JSON"
            )
            loss_val = params.get(loss_key) if loss_in_params else "(见代码默认)"
            hints.append(f"【跌破昨收止损】来源: {loss_src} | {loss_key}={loss_val}")
        return " | ".join(hints)

    def _refresh_backtest_mode_hint(self) -> None:
        try:
            from strategy_generator_app.backtest.preflight import backtest_preflight_hint_lines
        except ImportError:
            from backtest.preflight import backtest_preflight_hint_lines  # type: ignore
        if hasattr(self, "backtest_mode_hint_label"):
            self.backtest_mode_hint_label.setText(
                "\n".join(backtest_preflight_hint_lines())
            )

    def _backtest_preflight_union_codes(
        self,
        pool_codes: List[str],
        initial_positions: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        seen: set = set()
        out: List[str] = []
        for raw in list(pool_codes or []) + list((initial_positions or {}).keys()):
            c6 = (str(raw or "").strip().replace(".", "")[:6] or "").zfill(6)
            if len(c6) < 6:
                c6 = c6.zfill(6)
            if not c6 or c6 == "000000" or c6 in seen:
                continue
            seen.add(c6)
            out.append(c6)
        return sorted(out)

    def _run_backtest_preflight_ui(
        self,
        codes_6: List[str],
        start_date: date,
        end_date: date,
        *,
        status_prefix: str = "回测预热",
        progress=None,
    ) -> List[str]:
        """批量等待 daily_cache / tick。可传入已有 progress，避免每档再弹一条进度条。"""
        try:
            from strategy_generator_app.backtest.preflight import run_backtest_preflight
        except ImportError:
            from backtest.preflight import run_backtest_preflight  # type: ignore

        if not codes_6:
            return []

        own_dlg = None
        if progress is None:
            progress, own_dlg = self._backtest_progress_dialog(status_prefix)

        def _progress(msg: str, pct: Optional[int] = None) -> None:
            # 自建进度条时加前缀；外部共用进度条时由 scoped 前缀负责
            if own_dlg is not None and status_prefix:
                progress(f"{status_prefix}：{msg}", pct)
            else:
                progress(msg, pct)

        try:
            pf = run_backtest_preflight(
                codes_6,
                start_date,
                end_date,
                use_tick_level=True,
                progress=_progress,
            )
        finally:
            if own_dlg is not None:
                try:
                    own_dlg.setValue(100)
                    own_dlg.close()
                except Exception:
                    pass
        return list(pf.lines or [])

    def _backtest_progress_dialog(
        self,
        status_prefix: str,
    ) -> tuple:
        """创建回测进度条，返回 (progress_callback, dialog)。一次回测/批量共用这一条。"""
        dlg = QProgressDialog(
            f"{status_prefix}：准备…",
            None,
            0,
            100,
            self,
        )
        dlg.setWindowTitle(status_prefix)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        dlg.show()
        QApplication.processEvents()

        def _progress(msg: str, pct: Optional[int] = None) -> None:
            text = str(msg or "")
            if status_prefix and not text.startswith(str(status_prefix)):
                text = f"{status_prefix}：{text}"
            dlg.setLabelText(text)
            if pct is not None:
                dlg.setValue(max(0, min(100, int(pct))))
            if hasattr(self, "backtest_result_text"):
                self.backtest_result_text.setPlainText(text)
            QApplication.processEvents()

        return _progress, dlg

    @staticmethod
    def _scoped_backtest_progress(progress, lo: float, hi: float, label_prefix: str = ""):
        """把子阶段 0–100 映射到总进度 [lo, hi]，供预热/回测共用同一条进度条。"""

        def _p(msg: str, pct: Optional[int] = None) -> None:
            text = f"{label_prefix}{msg}" if label_prefix else str(msg or "")
            if pct is None:
                if progress:
                    progress(text, None)
                return
            span = max(0.0, float(hi) - float(lo))
            g = float(lo) + span * max(0, min(100, int(pct))) / 100.0
            if progress:
                progress(text, int(round(g)))

        return _p

    def _on_run_backtest(self):
        """运行回测：使用当前选中策略与股票池，在设定区间内基于 tick 数据模拟成交并展示结果"""
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.warning(self, "提示", "请先在左侧选择要回测的策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            QMessageBox.warning(self, "提示", "未找到该策略配置。")
            return
        refreshed = self._refresh_cfg_from_disk(sid)
        if refreshed is not None:
            cfg = refreshed
        run_code, bt_params = self._strategy_inputs_for_run(cfg)
        tp10_hint = self._tp10_effective_params_hint(run_code, bt_params)
        codes = self._get_pool_codes_from_list()
        if not codes:
            QMessageBox.warning(self, "提示", "当前策略股票池为空，请先在「股票池」Tab 添加股票并保存。")
            return
        start_q = self.backtest_start_date.date()
        end_q = self.backtest_end_date.date()
        start_date = date(start_q.year(), start_q.month(), start_q.day())
        end_date = date(end_q.year(), end_q.month(), end_q.day())
        if start_date > end_date:
            QMessageBox.warning(self, "提示", "回测开始日期不能晚于结束日期。")
            return
        initial_cash = self.backtest_initial_cash.value()
        pool_set = set((c or "").strip()[:6].zfill(6) for c in codes if (c or "").strip())
        # 从「初始持仓」表读取，仅计入当前策略股票池内的标的
        initial_positions = {}
        skipped_init = 0
        t = self.backtest_init_positions_table
        for row in range(t.rowCount()):
            code_item = t.item(row, 0)
            vol_item = t.item(row, 1)
            cost_item = t.item(row, 2)
            code_6 = (code_item.text() or "").strip().replace(".", "")[:6] if code_item else ""
            if len(code_6) < 6:
                code_6 = code_6.zfill(6) if code_6 else ""
            try:
                vol = int(float(vol_item.text() or 0)) if vol_item else 0
            except (ValueError, TypeError):
                vol = 0
            try:
                cost = float(cost_item.text() or 0) if cost_item else 0
            except (ValueError, TypeError):
                cost = 0
            if not code_6 or vol <= 0:
                continue
            if code_6 in pool_set:
                initial_positions[code_6] = {"volume": vol, "cost": cost}
            else:
                skipped_init += 1
        gen_t = self.backtest_generation_time.time()
        run_start_t = self.backtest_run_start_time.time()
        run_end_t = self.backtest_run_end_time.time()
        strategy_generation_time = f"{gen_t.hour():02d}:{gen_t.minute():02d}"
        strategy_run_start_time = f"{run_start_t.hour():02d}:{run_start_t.minute():02d}"
        strategy_run_end_time = f"{run_end_t.hour():02d}:{run_end_t.minute():02d}"
        cfg_b_dual = None  # 分时段组合时第二段策略，用于结果文案
        self.backtest_run_btn.setEnabled(False)
        self.backtest_result_text.setPlainText("回测运行中…")
        self.backtest_trades_table.setRowCount(0)
        QApplication.processEvents()
        bt_progress, bt_dlg = self._backtest_progress_dialog("回测")
        try:
            pf_lines = self._run_backtest_preflight_ui(
                self._backtest_preflight_union_codes(codes, initial_positions),
                start_date,
                end_date,
                progress=self._scoped_backtest_progress(bt_progress, 0, 15, "预热 "),
            )
            preflight_text = "\n".join(pf_lines) if pf_lines else ""
            self.backtest_result_text.setPlainText(
                ((preflight_text + "\n\n") if preflight_text else "") + "回测运行中，请稍候…"
            )
            QApplication.processEvents()
            try:
                from strategy_generator_app.backtest import run_backtest, run_backtest_segmented, compute_metrics
            except ImportError:
                from backtest import run_backtest, run_backtest_segmented, compute_metrics
            get_name = _get_stock_name_fn() or (lambda c: "")
            use_dual = hasattr(self, "backtest_dual_cb") and self.backtest_dual_cb.isChecked()
            bt_sub = self._scoped_backtest_progress(bt_progress, 15, 100, "回测 ")
            if use_dual:
                bid = self.backtest_seg2_combo.currentData()
                cfg_b = self._find_strategy_by_id(bid) if bid else None
                if not cfg_b:
                    QMessageBox.warning(self, "提示", "请在下拉框中选择第二段策略。")
                    self.backtest_run_btn.setEnabled(True)
                    return
                rb = self._refresh_cfg_from_disk(bid) if bid else None
                if rb is not None:
                    cfg_b = rb
                cfg_b_dual = cfg_b
                gt2 = self.backtest_seg2_generation_time.time()
                rs2 = self.backtest_seg2_run_start.time()
                re2 = self.backtest_seg2_run_end.time()
                carry_over = bool(getattr(self, "backtest_dual_carry_cb", None) and self.backtest_dual_carry_cb.isChecked())
                # 为简化问题：要求时段2运行开始时间与时段1运行结束时间“连续对接”
                #（边界 tick 是否重复并不重要，因为我们会根据 carry_over 决定是否把时段1未成交订单带入时段2）
                if rs2 != run_end_t:
                    QMessageBox.warning(
                        self,
                        "提示",
                        "为简化时段组合回测，请将“第二段运行开始”设置为“第一段运行结束”。\n\n"
                        f"时段1 运行结束：{run_end_t.toString('HH:mm')}；时段2 运行开始：{rs2.toString('HH:mm')}\n"
                    )
                    self.backtest_run_btn.setEnabled(True)
                    return
                if re2 <= rs2:
                    QMessageBox.warning(
                        self,
                        "提示",
                        "第二时段运行结束时间必须晚于运行开始时间。"
                    )
                    self.backtest_run_btn.setEnabled(True)
                    return
                seg2_gen = f"{gt2.hour():02d}:{gt2.minute():02d}"
                seg2_start = f"{rs2.hour():02d}:{rs2.minute():02d}"
                seg2_end = f"{re2.hour():02d}:{re2.minute():02d}"
                segments = [
                    {
                        "strategy_code": run_code,
                        "strategy_params": bt_params,
                        "strategy_generation_time": strategy_generation_time,
                        "strategy_run_start_time": strategy_run_start_time,
                        "strategy_run_end_time": strategy_run_end_time,
                        "name": f"时段1·{cfg.name}",
                    },
                    {
                        "strategy_code": cfg_b.strategy_code or "",
                        "strategy_params": cfg_b.strategy_params or {},
                        "strategy_generation_time": seg2_gen,
                        "strategy_run_start_time": seg2_start,
                        "strategy_run_end_time": seg2_end,
                        "name": f"时段2·{cfg_b.name}",
                    },
                ]
                result = run_backtest_segmented(
                    segments,
                    codes,
                    start_date,
                    end_date,
                    initial_cash=initial_cash,
                    get_stock_name=get_name,
                    use_engine_form=False,
                    use_tick_level=True,
                    initial_positions=initial_positions if initial_positions else None,
                    carry_over_pending_intents=carry_over,
                    progress=bt_sub,
                )
            else:
                result = run_backtest(
                    strategy_code=run_code,
                    strategy_params=bt_params,
                    stock_codes_6=codes,
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=initial_cash,
                    get_stock_name=get_name,
                    use_engine_form=False,
                    use_tick_level=True,
                    strategy_generation_time=strategy_generation_time,
                    strategy_run_start_time=strategy_run_start_time,
                    strategy_run_end_time=strategy_run_end_time,
                    initial_positions=initial_positions if initial_positions else None,
                    progress=bt_sub,
                )
            metrics = compute_metrics(
                result.get("equity_curve") or [],
                result.get("trades") or [],
                initial_cash,
                result.get("final_positions"),
                result.get("last_prices"),
                initial_positions=initial_positions if initial_positions else None,
                buy_and_hold_total=result.get("buy_and_hold_total"),
            )
            total_return = metrics.get("total_return", 0) * 100
            annual_return = metrics.get("annual_return", 0) * 100
            max_dd = metrics.get("max_drawdown", 0) * 100
            trade_count = metrics.get("trade_count", 0)
            final_total = metrics.get("final_total", initial_cash)
            pos_count = metrics.get("position_count", 0)
            pos_hit = metrics.get("position_hit_rate", 0) * 100
            avg_unreal = metrics.get("avg_position_unrealized_pct", 0)
            gen_time = result.get("strategy_generation_time", "")
            run_start = result.get("strategy_run_start", "")
            run_end = result.get("strategy_run_end", "")
            msg = ""
            if pf_lines:
                msg += "回测预热：\n" + "\n".join(pf_lines) + "\n\n"
            if gen_time and run_start and run_end:
                msg = f"策略生成时间：{gen_time} | 策略运行时间：{run_start}–{run_end}\n\n"
            if tp10_hint:
                msg += tp10_hint + "\n\n"
            # 回测说明中贴出本次实际生成的任务（intents → 各种 rule_type / price / volume）
            if cfg_b_dual is not None:
                msg += (
                    f"【分时段组合】时段1「{cfg.name}」成交窗口 {strategy_run_start_time}–{strategy_run_end_time}；"
                    f"时段2「{cfg_b_dual.name}」成交窗口 {seg2_start}–{seg2_end}（同一股票池与资金串联）\n\n"
                )
            gen_items = result.get("generated_intents") or []
            if gen_items:
                msg += "回测生成任务（intents，按生成时刻分组）：\n"
                # 为避免 msg 过长，这里只做简要字段展示（仍然能还原每条规则的价格/量）
                def _one_intent_brief(it: dict) -> str:
                    code = (it.get("stock_code") or "").strip()
                    rt = (it.get("rule_type") or "").strip()
                    vol = it.get("volume", "")
                    # 常用价格字段优先展示
                    if rt in ("cage_buy", "cage_sell"):
                        pl = it.get("price_low", "")
                        ph = it.get("price_high", "")
                        return f"{code} {rt} [{pl},{ph}] x{vol}"
                    if rt in ("best_buy", "best_sell"):
                        tp = it.get("trigger_price", "")
                        if rt == "best_buy":
                            rp = it.get("rise_percent", "")
                            return f"{code} {rt} trigger={tp} rise={rp}% x{vol}"
                        dp = it.get("drop_percent", "")
                        fb = it.get("pullback_price", "")
                        fb_txt = f" fb={fb}" if fb not in (None, "", 0) else ""
                        return f"{code} {rt} trigger={tp} drop={dp}%{fb_txt} x{vol}"
                    # 单点/突破
                    px = it.get("price", it.get("trigger_price", ""))  # sell/buy: price 或 best_* 的 trigger_price
                    return f"{code} {rt} price={px} x{vol}"

                # 保持输入顺序：通常是 fill_day 从小到大，段也从前到后
                for item in gen_items:
                    d = item.get("date", "")
                    segn = item.get("segment_name", "")
                    stgt = item.get("strategy_generation_time", "")
                    intents = item.get("intents") or []
                    if not intents:
                        continue
                    msg += f"- {d} {segn} gen@{stgt}: {len(intents)}条\n"
                    # 每个分组最多展示前 25 条，避免 UI 卡死（你如果要全量我再改开关）
                    for it in intents[:25]:
                        try:
                            msg += "  " + _one_intent_brief(it) + "\n"
                        except Exception:
                            pass
                    if len(intents) > 25:
                        msg += f"  ... 已省略 {len(intents) - 25} 条\n"
                msg += "\n"
            if initial_positions:
                initial_equity = initial_cash + sum(
                    p.get("volume", 0) * p.get("cost", 0) for p in initial_positions.values()
                )
                msg += f"初始权益：{initial_equity:,.0f} 元（现金 + 初始持仓市值）\n"
            if skipped_init > 0:
                msg += f"初始持仓中有 {skipped_init} 只不在当前股票池中未计入。\n"
            msg += (
                f"回测完成（tick 级）。总收益：{total_return:.2f}% | 年化：{annual_return:.2f}% | "
                f"最大回撤：{max_dd:.2f}% | 成交笔数：{trade_count} | 期末总资产：{final_total:,.0f} 元"
            )
            bh_ret = metrics.get("buy_and_hold_return")
            excess_ret = metrics.get("excess_return")
            if bh_ret is not None and excess_ret is not None:
                msg += f"\n对比持股不动：策略 {total_return:.2f}% vs 持股不动 {bh_ret * 100:.2f}% | 超额收益：{excess_ret * 100:+.2f}%"
            if pos_count > 0:
                msg += f" | 期末持仓：{pos_count} 只 | 持仓胜率：{pos_hit:.1f}% | 平均持仓浮盈：{avg_unreal:.2f}%"
            # 若没有任何成交且几乎无有效数据，提示可能原因
            days_with_data = result.get("days_with_data", 0)
            days_zero = result.get("days_with_zero_prices", 0)
            if trade_count == 0 and (days_with_data == 0 or days_zero > 0):
                msg += "\n\n提示：未产生任何成交。"
            elif trade_count == 0 and days_with_data > 0:
                msg += "\n\n提示：有有效行情数据但未产生成交。"
            failure_reasons = result.get("failure_reasons") or []
            if failure_reasons:
                msg += "\n\n失败/可能原因：\n" + "\n".join(failure_reasons)
            tick_remark = _format_tick_coverage_remark(result)
            if tick_remark:
                msg += "\n\ntick 数据覆盖：\n" + tick_remark.replace("；", "\n")
                cov = result.get("tick_coverage") or {}
                log = cov.get("log") if isinstance(cov, dict) else None
                if isinstance(log, list) and log:
                    msg += "\n（按交易日/范围明细）"
                    for e in log:
                        d0 = e.get("date", "")
                        scope = e.get("scope", "")
                        wt = e.get("with_tick_count", "")
                        rq = e.get("requested_count", "")
                        miss = e.get("missing_codes") or []
                        miss_s = ",".join(miss) if miss else "无"
                        msg += f"\n  {d0} {scope}: {wt}/{rq} 缺:{miss_s}"
            msg += (
                "\n\n提示：可点击「导出上次回测结果」保存期末现金与持仓（JSON）；"
                "切换到接续用的策略 B 后可用「从回测导出文件导入」，再点击「同步到股票池」。"
            )
            self.backtest_result_text.setPlainText(msg)
            sd_s = start_date.strftime("%Y-%m-%d")
            ed_s = end_date.strftime("%Y-%m-%d")
            trades_out = []
            for t in result.get("trades") or []:
                td = dict(t)
                td["选股日"] = ""
                td["start_date"] = sd_s
                td["end_date"] = ed_s
                trades_out.append(td)
            self._fill_backtest_trades_table(trades_out)
            export_payload = self._make_backtest_export_payload(
                cfg, result, initial_cash, start_date, end_date, skipped_init, use_dual, cfg_b_dual
            )
            self._backtest_export_by_strategy[cfg.id] = export_payload
            # 单次回测覆盖「批量 JSON」缓存，避免误导出旧批量
            self._last_batch_export_bundle = None
            self._last_batch_bundle_strategy_id = None
            self._refresh_backtest_export_button()
        except Exception as e:
            self.backtest_result_text.setPlainText(f"回测失败：{e}")
            QMessageBox.critical(self, "回测错误", str(e))
        finally:
            try:
                bt_progress("回测完成", 100)
                bt_dlg.setValue(100)
                bt_dlg.close()
            except Exception:
                pass
            self.backtest_run_btn.setEnabled(True)

    def _on_backtest_clear_time_sweep(self):
        """一次运行多个 scheduled_clear_time，汇总对比总收益与清仓成交。"""
        if hasattr(self, "backtest_dual_cb") and self.backtest_dual_cb.isChecked():
            QMessageBox.warning(
                self, "提示",
                "清仓时间对比暂不支持「分时段组合回测」。请先取消勾选，或单独运行各时段策略。",
            )
            return
        times = _parse_clear_time_sweep_text(self.backtest_clear_time_sweep_edit.text())
        if not times:
            QMessageBox.warning(self, "提示", "请在「清仓时间对比」中填写至少一个时间，如：14:50,14:55,14:56")
            return
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.warning(self, "提示", "请先在左侧选择要回测的策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            QMessageBox.warning(self, "提示", "未找到该策略配置。")
            return
        refreshed = self._refresh_cfg_from_disk(sid)
        if refreshed is not None:
            cfg = refreshed
        if not strategy_uses_scheduled_clear(
            cfg.strategy_code or "", cfg.strategy_params, getattr(cfg, "name", "") or ""
        ):
            QMessageBox.warning(
                self, "提示",
                "当前策略不含 scheduled_clear 定时清仓规则，无需做清仓时间对比。\n"
                "请切换到如「卖：止盈-28开_14：56清仓」等卖出策略。",
            )
            return
        codes = self._get_pool_codes_from_list()
        if not codes:
            QMessageBox.warning(self, "提示", "当前策略股票池为空，请先在「股票池」Tab 添加股票并保存。")
            return
        start_q = self.backtest_start_date.date()
        end_q = self.backtest_end_date.date()
        start_date = date(start_q.year(), start_q.month(), start_q.day())
        end_date = date(end_q.year(), end_q.month(), end_q.day())
        if start_date > end_date:
            QMessageBox.warning(self, "提示", "回测开始日期不能晚于结束日期。")
            return
        initial_cash = self.backtest_initial_cash.value()
        pool_set = set((c or "").strip()[:6].zfill(6) for c in codes if (c or "").strip())
        initial_positions = {}
        t = self.backtest_init_positions_table
        for row in range(t.rowCount()):
            code_item = t.item(row, 0)
            vol_item = t.item(row, 1)
            cost_item = t.item(row, 2)
            code_6 = (code_item.text() or "").strip().replace(".", "")[:6] if code_item else ""
            if len(code_6) < 6:
                code_6 = code_6.zfill(6) if code_6 else ""
            try:
                vol = int(float(vol_item.text() or 0)) if vol_item else 0
            except (ValueError, TypeError):
                vol = 0
            try:
                cost = float(cost_item.text() or 0) if cost_item else 0
            except (ValueError, TypeError):
                cost = 0
            if not code_6 or vol <= 0:
                continue
            if code_6 in pool_set:
                initial_positions[code_6] = {"volume": vol, "cost": cost}
        gen_t = self.backtest_generation_time.time()
        run_start_t = self.backtest_run_start_time.time()
        run_end_t = self.backtest_run_end_time.time()
        strategy_generation_time = f"{gen_t.hour():02d}:{gen_t.minute():02d}"
        strategy_run_start_time = f"{run_start_t.hour():02d}:{run_start_t.minute():02d}"
        strategy_run_end_time = f"{run_end_t.hour():02d}:{run_end_t.minute():02d}"
        run_end_sec = _time_str_to_seconds(strategy_run_end_time)
        self.backtest_run_btn.setEnabled(False)
        self.backtest_clear_time_sweep_btn.setEnabled(False)
        self.backtest_result_text.setPlainText(f"清仓时间对比运行中（共 {len(times)} 档）…")
        self.backtest_trades_table.setRowCount(0)
        QApplication.processEvents()
        rows: List[Dict[str, Any]] = []
        bt_progress, bt_dlg = self._backtest_progress_dialog("清仓时间对比")
        try:
            try:
                from strategy_generator_app.backtest import run_backtest, compute_metrics
            except ImportError:
                from backtest import run_backtest, compute_metrics
            get_name = _get_stock_name_fn() or (lambda c: "")
            base_params = dict(cfg.strategy_params or {})
            n_times = max(1, len(times))
            self._run_backtest_preflight_ui(
                self._backtest_preflight_union_codes(codes, initial_positions),
                start_date,
                end_date,
                status_prefix="清仓对比预热",
                progress=self._scoped_backtest_progress(bt_progress, 0, 10, "预热 "),
            )
            for i, clear_t in enumerate(times):
                self.backtest_result_text.setPlainText(
                    f"清仓时间对比：第 {i + 1}/{len(times)} 档 {clear_t} …"
                )
                QApplication.processEvents()
                slot_lo = 10.0 + 90.0 * i / n_times
                slot_hi = 10.0 + 90.0 * (i + 1) / n_times
                bt_sub = self._scoped_backtest_progress(
                    bt_progress, slot_lo, slot_hi, f"[{i + 1}/{n_times}] {clear_t} "
                )
                params = dict(base_params)
                params["scheduled_clear_time"] = clear_t
                try:
                    result = run_backtest(
                        strategy_code=cfg.strategy_code or "",
                        strategy_params=params,
                        stock_codes_6=codes,
                        start_date=start_date,
                        end_date=end_date,
                        initial_cash=initial_cash,
                        get_stock_name=get_name,
                        use_engine_form=False,
                        use_tick_level=True,
                        strategy_generation_time=strategy_generation_time,
                        strategy_run_start_time=strategy_run_start_time,
                        strategy_run_end_time=strategy_run_end_time,
                        initial_positions=initial_positions if initial_positions else None,
                        progress=bt_sub,
                    )
                    metrics = compute_metrics(
                        result.get("equity_curve") or [],
                        result.get("trades") or [],
                        initial_cash,
                        result.get("final_positions"),
                        result.get("last_prices"),
                        initial_positions=initial_positions if initial_positions else None,
                        buy_and_hold_total=result.get("buy_and_hold_total"),
                    )
                    note = ""
                    if run_end_sec >= 0 and _time_str_to_seconds(clear_t) > run_end_sec:
                        note = "运行结束早于清仓时间"
                    rows.append({
                        "time": clear_t,
                        "total_return": metrics.get("total_return", 0) * 100,
                        "final_total": metrics.get("final_total", initial_cash),
                        "trade_count": metrics.get("trade_count", 0),
                        "clear_summary": _summarize_scheduled_clear_trades(result.get("trades") or []),
                        "note": note,
                        "error": "",
                    })
                except Exception as e:
                    rows.append({
                        "time": clear_t,
                        "total_return": None,
                        "final_total": None,
                        "trade_count": 0,
                        "clear_summary": "-",
                        "note": "",
                        "error": str(e),
                    })
            sd_s = start_date.strftime("%Y-%m-%d")
            ed_s = end_date.strftime("%Y-%m-%d")
            msg = (
                f"【清仓时间对比】策略「{cfg.name}」| 区间 {sd_s} ~ {ed_s}\n"
                f"运行窗口 {strategy_run_start_time}–{strategy_run_end_time} | "
                f"共 {len(times)} 档（仅末日出清时间不同）\n\n"
            )
            hdr = f"{'清仓时间':<10} {'总收益%':>9} {'期末总资产':>14} {'成交笔数':>6}  定时清仓成交\n"
            msg += hdr
            msg += "-" * len(hdr) + "\n"
            best_ret = None
            best_time = ""
            for r in rows:
                if r.get("error"):
                    msg += f"{r['time']:<10} {'失败':>9} {'-':>14} {'-':>6}  {r['error'][:40]}\n"
                    continue
                ret = r["total_return"]
                if ret is not None and (best_ret is None or ret > best_ret):
                    best_ret = ret
                    best_time = r["time"]
                note = r.get("note") or ""
                clear_txt = r.get("clear_summary") or "-"
                if note:
                    clear_txt = f"{clear_txt}（{note}）"
                msg += (
                    f"{r['time']:<10} {ret:>9.2f} {r['final_total']:>14,.0f} "
                    f"{r['trade_count']:>6}  {clear_txt}\n"
                )
            if best_time:
                msg += f"\n本组最高总收益：{best_ret:.2f}%（清仓时间 {best_time}）"
            msg += (
                "\n\n说明：各档除 scheduled_clear_time 外条件相同；"
                "接续卖出策略请用「下一轮接续」旁的「定时清仓」；"
                "若要查看某一档完整成交明细，可将该时间设到「定时清仓」后再点「运行回测」。"
            )
            self.backtest_result_text.setPlainText(msg)
        except Exception as e:
            self.backtest_result_text.setPlainText(f"清仓时间对比失败：{e}")
            QMessageBox.critical(self, "回测错误", str(e))
        finally:
            try:
                bt_progress("清仓对比完成", 100)
                bt_dlg.setValue(100)
                bt_dlg.close()
            except Exception:
                pass
            self.backtest_run_btn.setEnabled(True)
            self.backtest_clear_time_sweep_btn.setEnabled(True)

    def _on_export_backtest_trades_csv(self):
        """导出当前成交明细表为 CSV。"""
        t = self.backtest_trades_table
        nrows = t.rowCount()
        ncols = t.columnCount()
        if nrows <= 0:
            QMessageBox.information(
                self, "提示", "当前成交明细表为空，请先运行回测或批量/下一轮批量回测。"
            )
            return
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        hd = os.path.join(root, "history_data")
        os.makedirs(hd, exist_ok=True)
        default_fn = os.path.join(
            hd, f"回测成交明细_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出成交明细 CSV",
            default_fn,
            "CSV (*.csv);;所有文件 (*.*)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        headers = []
        for c in range(ncols):
            hi = t.horizontalHeaderItem(c)
            headers.append(hi.text() if hi else f"列{c + 1}")
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(headers)
                for r in range(nrows):
                    row_vals = []
                    for c in range(ncols):
                        it = t.item(r, c)
                        row_vals.append((it.text() if it else "").strip())
                    w.writerow(row_vals)
            QMessageBox.information(self, "已导出", path)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _pick_file_to_edit(self, edit: QLineEdit, title: str, filt: str):
        path, _ = QFileDialog.getOpenFileName(self, title, "", filt)
        if path:
            edit.setText(path)

    def _pick_save_to_edit(self, edit: QLineEdit, title: str, filt: str):
        path, _ = QFileDialog.getSaveFileName(self, title, edit.text() or "", filt)
        if not path:
            return
        # 若用户没写扩展名，按 xlsx 兜底
        if filt.lower().find("xlsx") >= 0 and (not path.lower().endswith(".xlsx")):
            path += ".xlsx"
        edit.setText(path)

    def _on_open_merge_output_file(self):
        p = (getattr(self, "_last_merge_out_path", "") or "").strip()
        if not p:
            return
        try:
            os.startfile(p)  # type: ignore[attr-defined]
        except Exception as e:
            QMessageBox.warning(self, "打开失败", str(e))

    def _on_merge_backtest_trades_to_excel(self):
        """合并买卖成交明细 CSV → 各日选股收益汇总.xlsx（并对未清仓按盯市日收盘价估值）"""
        buy_path = (self.merge_buy_csv_edit.text() or "").strip()
        sell_path = (self.merge_sell_csv_edit.text() or "").strip()
        out_path = (self.merge_out_xlsx_edit.text() or "").strip()
        sel_path = (getattr(self, "merge_selection_file_edit", None).text() or "").strip() if getattr(self, "merge_selection_file_edit", None) else ""
        mark_n = int(getattr(self, "merge_mark_n_spin", None).value()) if getattr(self, "merge_mark_n_spin", None) else 3
        use_nth = bool(getattr(self, "merge_use_nth_cb", None) and self.merge_use_nth_cb.isChecked())
        if not buy_path or not os.path.isfile(buy_path):
            QMessageBox.warning(self, "提示", "请先选择有效的「买入明细」CSV。")
            return
        if not sell_path or not os.path.isfile(sell_path):
            QMessageBox.warning(self, "提示", "请先选择有效的「卖出明细」CSV。")
            return
        if not out_path:
            QMessageBox.warning(self, "提示", "请设置输出文件路径。")
            return

        # 写日志到信息栏（复用 backtest_result_text，避免另开弹窗刷屏）
        self.backtest_result_text.setPlainText("正在生成选股收益汇总，请稍候…")
        QApplication.processEvents()

        try:
            from pathlib import Path
            import pandas as pd

            _ensure_repo_root_on_sys_path()
            from tools.merge_backtest_trades_by_selection import (
                aggregate,
                _build_prices_by_mark_date,
                apply_mark_and_returns,
                apply_selection_file_fields,
                apply_ma_fields_from_daily_cache,
                apply_buy_day_ma5_ref_fields,
            )
        except Exception as e:
            QMessageBox.critical(self, "无法运行", f"导入汇总模块失败：{e}")
            return

        try:
            rows = aggregate(Path(buy_path), Path(sell_path))
            prices_by_mark, price_warn = _build_prices_by_mark_date(
                rows, mark_n=mark_n, use_nth_trading_day=use_nth
            )
            apply_mark_and_returns(
                rows,
                prices_by_mark,
                price_warn,
                mark_n=mark_n,
                use_nth_trading_day=use_nth,
            )
            sel_hint = ""
            if sel_path and os.path.isfile(sel_path):
                sel_hint = apply_selection_file_fields(rows, Path(sel_path))
            ma_hint = ""
            buy_ma_hint = ""
            try:
                ma_hint = apply_ma_fields_from_daily_cache(rows)
            except Exception as e:
                ma_hint = f"选股日均线回填失败：{type(e).__name__}: {e}"
            try:
                buy_ma_hint = apply_buy_day_ma5_ref_fields(rows)
            except Exception as e:
                buy_ma_hint = f"买入日MA5回填失败：{type(e).__name__}: {e}"

            df = pd.DataFrame(rows)
            # 补股票名称（便于阅读）：从本系统的 get_stock_name 获取
            try:
                get_name = _get_stock_name_fn() or (lambda c: "")
                if "代码" in df.columns:
                    codes = df["代码"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip().str.zfill(6)
                    df["股票名称"] = codes.map(lambda c: (get_name(c) or "").strip())
                    # 插到“代码”后面
                    cols = list(df.columns)
                    if "股票名称" in cols and "代码" in cols:
                        cols.remove("股票名称")
                        idx = cols.index("代码") + 1
                        cols.insert(idx, "股票名称")
                        df = df[cols]
            except Exception:
                pass

            out_path_abs = os.path.abspath(out_path)
            os.makedirs(os.path.dirname(out_path_abs), exist_ok=True)
            with pd.ExcelWriter(out_path_abs, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="汇总")

            self._last_merge_out_path = out_path_abs
            self.merge_open_btn.setEnabled(True)
            msg = f"已生成汇总：{out_path_abs}\n共 {len(rows)} 行"
            if not use_nth:
                msg += "\n盯市：未清仓按卖出明细中的 end_date 收盘价"
            else:
                msg += f"\n盯市：选股日后第 {mark_n} 个交易日（与明细 end_date 列可能不同）"
            if price_warn:
                msg += f"\n⚠ 行情提示：{price_warn}"
            if sel_hint:
                msg += f"\n{sel_hint}"
            if ma_hint:
                msg += f"\n{ma_hint}"
            if buy_ma_hint:
                msg += f"\n{buy_ma_hint}"
            self.backtest_result_text.setPlainText(msg)
        except Exception as e:
            self.merge_open_btn.setEnabled(False)
            self.backtest_result_text.setPlainText(f"生成失败：{type(e).__name__}: {e}")
            QMessageBox.critical(self, "生成失败", str(e))

    def _on_export_batch_backtest_bundle(self):
        """导出最近一次批量回测的完整 JSON（version=2，含多档选股日）。"""
        bundle = getattr(self, "_last_batch_export_bundle", None)
        if not bundle or not (bundle.get("segments") or []):
            QMessageBox.information(
                self,
                "提示",
                "没有可导出的批量回测结果。请先运行「批量回测(选股文件)」且至少有一档成功。",
            )
            return
        src_name = bundle.get("source_strategy_name") or "strategy"
        safe_name = re.sub(r'[<>:"/\\\\|?*]', "_", src_name)
        default_fn = f"batch_backtest_export_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出批量回测 JSON", default_fn, "JSON (*.json);;所有文件 (*.*)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(bundle, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "导出", f"已导出 {len(bundle.get('segments') or [])} 档：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_batch_backtest_from_selection_file(self):
        """按选股文件中的「选股日」分组，对每个交易日单独跑回测并汇总（无需拆文件）。"""
        sid = self._get_selected_strategy_id()
        if not sid:
            QMessageBox.warning(self, "提示", "请先在左侧选择要回测的策略。")
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            QMessageBox.warning(self, "提示", "未找到该策略配置。")
            return
        refreshed_b = self._refresh_cfg_from_disk(sid)
        if refreshed_b is not None:
            cfg = refreshed_b
        use_dual = hasattr(self, "backtest_dual_cb") and self.backtest_dual_cb.isChecked()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择含「选股日」的选股结果文件",
            "",
            "Excel (*.xlsx *.xls);;CSV (*.csv);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            by_day, hint = group_codes_by_selection_date_from_file(path)
        except Exception as e:
            QMessageBox.warning(self, "无法解析文件", str(e))
            return
        total_days = len(by_day)
        if total_days == 0:
            QMessageBox.information(self, "提示", "没有可回测的交易日。")
            return
        from_t1 = self.backtest_batch_from_t1_cb.isChecked()
        hold_n = self.backtest_batch_hold_days_spin.value()
        mode_txt = (
            f"从选股日下一交易日（T+1）起连续 {hold_n} 个交易日"
            if from_t1
            else f"从选股日当日起连续 {hold_n} 个交易日"
        )
        initial_cash = self.backtest_initial_cash.value()
        gen_t = self.backtest_generation_time.time()
        run_start_t = self.backtest_run_start_time.time()
        run_end_t = self.backtest_run_end_time.time()
        strategy_generation_time = f"{gen_t.hour():02d}:{gen_t.minute():02d}"
        strategy_run_start_time = f"{run_start_t.hour():02d}:{run_start_t.minute():02d}"
        strategy_run_end_time = f"{run_end_t.hour():02d}:{run_end_t.minute():02d}"

        cfg_b_dual = None
        segments_dual = None
        carry_over_dual = False
        if use_dual:
            bid = self.backtest_seg2_combo.currentData()
            cfg_b = self._find_strategy_by_id(bid) if bid else None
            if not cfg_b:
                QMessageBox.warning(self, "提示", "分时段批量回测：请在下方下拉框中选择第二段策略。")
                return
            rb2 = self._refresh_cfg_from_disk(bid) if bid else None
            if rb2 is not None:
                cfg_b = rb2
            cfg_b_dual = cfg_b
            gt2 = self.backtest_seg2_generation_time.time()
            rs2 = self.backtest_seg2_run_start.time()
            re2 = self.backtest_seg2_run_end.time()
            if rs2 != run_end_t:
                QMessageBox.warning(
                    self,
                    "提示",
                    "分时段批量回测要求：第二段「运行开始」= 第一段「运行结束」。\n\n"
                    f"第一段结束：{run_end_t.toString('HH:mm')}；第二段开始：{rs2.toString('HH:mm')}",
                )
                return
            if re2 <= rs2:
                QMessageBox.warning(self, "提示", "第二段运行结束时间必须晚于运行开始时间。")
                return
            carry_over_dual = bool(
                getattr(self, "backtest_dual_carry_cb", None) and self.backtest_dual_carry_cb.isChecked()
            )
            seg2_gen = f"{gt2.hour():02d}:{gt2.minute():02d}"
            seg2_start = f"{rs2.hour():02d}:{rs2.minute():02d}"
            seg2_end = f"{re2.hour():02d}:{re2.minute():02d}"
            segments_dual = [
                {
                    "strategy_code": cfg.strategy_code or "",
                    "strategy_params": cfg.strategy_params or {},
                    "strategy_generation_time": strategy_generation_time,
                    "strategy_run_start_time": strategy_run_start_time,
                    "strategy_run_end_time": strategy_run_end_time,
                    "name": f"时段1·{cfg.name}",
                },
                {
                    "strategy_code": cfg_b.strategy_code or "",
                    "strategy_params": cfg_b.strategy_params or {},
                    "strategy_generation_time": seg2_gen,
                    "strategy_run_start_time": seg2_start,
                    "strategy_run_end_time": seg2_end,
                    "name": f"时段2·{cfg_b.name}",
                },
            ]
            mode_txt += f"；分时段组合（时段1「{cfg.name}」+ 时段2「{cfg_b.name}」）"

        reply = QMessageBox.question(
            self,
            "确认批量回测",
            f"{hint}\n\n将对 {total_days} 个选股日分别回测：{mode_txt}。\n"
            f"耗时会随选股日数量增加，是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return
        self._last_batch_export_bundle = None
        self._last_batch_bundle_strategy_id = None
        self._chained_batch_segments = None
        if hasattr(self, "sell_chain_run_btn"):
            self.sell_chain_run_btn.setEnabled(False)
        self._refresh_backtest_export_button()
        self.backtest_batch_file_btn.setEnabled(False)
        self.backtest_run_btn.setEnabled(False)
        self.backtest_result_text.setPlainText("批量回测运行中…")
        self.backtest_trades_table.setRowCount(0)
        QApplication.processEvents()
        try:
            try:
                from strategy_generator_app.backtest import (
                    run_backtest,
                    run_backtest_segmented,
                    compute_metrics,
                )
            except ImportError:
                from backtest import run_backtest, run_backtest_segmented, compute_metrics
            try:
                from strategy_generator_app.trading_calendar import backtest_window_from_selection_day
            except ImportError:
                from trading_calendar import backtest_window_from_selection_day
            import pandas as pd
            get_name = _get_stock_name_fn() or (lambda c: "")
            summary_rows = []
            segment_payloads = []
            all_batch_trades: List[dict] = []
            lines = [
                f"批量回测文件：{path}\n{hint}\n"
                f"规则：{'T+1 起' if from_t1 else 'T 当日起'}，持有 {hold_n} 个交易日"
                f"{'；分时段组合' if use_dual else ''}\n",
                "=" * 60,
            ]
            day_items = [(d, codes) for d, codes in by_day.items() if codes]
            n_day = max(1, len(day_items))
            bt_progress, bt_dlg = self._backtest_progress_dialog("批量回测")
            try:
                for i, (d, codes) in enumerate(day_items):
                    self.backtest_result_text.setPlainText(
                        f"批量回测中 {i + 1}/{n_day}：选股日 {d}，{len(codes)} 只…"
                    )
                    QApplication.processEvents()
                    start_d, end_d, _w = backtest_window_from_selection_day(
                        d,
                        start_next_trading_day=from_t1,
                        hold_trading_days=hold_n,
                    )
                    if start_d is None or end_d is None:
                        summary_rows.append(
                            {
                                "选股日": d.strftime("%Y-%m-%d"),
                                "回测开始": "",
                                "回测结束": "",
                                "股票数": len(codes),
                                "总收益率%": None,
                                "成交笔数": None,
                                "期末总资产": None,
                                "备注": _w,
                            }
                        )
                        lines.append(f"{d} | 跳过：{_w}")
                        continue
                    slot_lo = 100.0 * i / n_day
                    slot_hi = 100.0 * (i + 1) / n_day
                    mid = slot_lo + 0.2 * (slot_hi - slot_lo)
                    tag = f"[{i + 1}/{n_day}] "
                    pf_lines = self._run_backtest_preflight_ui(
                        self._backtest_preflight_union_codes(codes),
                        start_d,
                        end_d,
                        status_prefix=f"预热 {d}",
                        progress=self._scoped_backtest_progress(
                            bt_progress, slot_lo, mid, f"{tag}预热 "
                        ),
                    )
                    if pf_lines and len(lines) < 80:
                        lines.append(f"{d} | " + " | ".join(pf_lines[:1]))
                    bt_sub = self._scoped_backtest_progress(
                        bt_progress, mid, slot_hi, f"{tag}回测 "
                    )
                    try:
                        if use_dual and segments_dual:
                            result = run_backtest_segmented(
                                segments_dual,
                                codes,
                                start_d,
                                end_d,
                                initial_cash=initial_cash,
                                get_stock_name=get_name,
                                use_engine_form=False,
                                use_tick_level=True,
                                initial_positions=None,
                                carry_over_pending_intents=carry_over_dual,
                                progress=bt_sub,
                            )
                        else:
                            result = run_backtest(
                                strategy_code=cfg.strategy_code or "",
                                strategy_params=cfg.strategy_params or {},
                                stock_codes_6=codes,
                                start_date=start_d,
                                end_date=end_d,
                                initial_cash=initial_cash,
                                get_stock_name=get_name,
                                use_engine_form=False,
                                use_tick_level=True,
                                strategy_generation_time=strategy_generation_time,
                                strategy_run_start_time=strategy_run_start_time,
                                strategy_run_end_time=strategy_run_end_time,
                                initial_positions=None,
                                progress=bt_sub,
                            )
                        metrics = compute_metrics(
                            result.get("equity_curve") or [],
                            result.get("trades") or [],
                            initial_cash,
                            result.get("final_positions"),
                            result.get("last_prices"),
                            initial_positions=None,
                            buy_and_hold_total=result.get("buy_and_hold_total"),
                        )
                        tr = metrics.get("total_return", 0) * 100
                        tc = metrics.get("trade_count", 0)
                        ft = metrics.get("final_total", initial_cash)
                        diag_remark = _summarize_backtest_diagnosis(result, tc)
                        tick_cols = _batch_summary_tick_columns(result)
                        summary_rows.append(
                            {
                                "选股日": d.strftime("%Y-%m-%d"),
                                "回测开始": start_d.strftime("%Y-%m-%d"),
                                "回测结束": end_d.strftime("%Y-%m-%d"),
                                "股票数": len(codes),
                                **tick_cols,
                                "总收益率%": round(tr, 4),
                                "成交笔数": tc,
                                "期末总资产": round(ft, 2),
                                "备注": diag_remark,
                            }
                        )
                        tick_short = tick_cols.get("tick覆盖(池)") or ""
                        if tick_short:
                            line_extra = f" | tick(池){tick_short}"
                            if tick_cols.get("缺tick代码"):
                                line_extra += f" 缺:{tick_cols['缺tick代码'][:60]}{'…' if len(tick_cols['缺tick代码']) > 60 else ''}"
                        else:
                            line_extra = f" | {diag_remark}" if diag_remark else ""
                        lines.append(
                            f"{d} | [{start_d}~{end_d}] {len(codes)}只 | 收益 {tr:.2f}% | 成交 {tc} | 期末 {ft:,.0f}{line_extra}"
                        )
                        sel_s = d.strftime("%Y-%m-%d")
                        sd_s = start_d.strftime("%Y-%m-%d")
                        ed_s = end_d.strftime("%Y-%m-%d")
                        for t in result.get("trades") or []:
                            td = dict(t)
                            ti = (td.get("trigger_info") or "").strip()
                            td["trigger_info"] = f"[选股日 {sel_s}] {ti}".strip()
                            td["选股日"] = sel_s
                            td["start_date"] = sd_s
                            td["end_date"] = ed_s
                            all_batch_trades.append(td)
                        payload = self._make_backtest_export_payload(
                            cfg,
                            result,
                            initial_cash,
                            start_d,
                            end_d,
                            0,
                            bool(use_dual),
                            cfg_b_dual,
                        )
                        payload["batch_selection_date"] = d.isoformat()
                        payload["batch_mode"] = True
                        segment_payloads.append(payload)
                    except Exception as ex:
                        summary_rows.append(
                            {
                                "选股日": d.strftime("%Y-%m-%d"),
                                "回测开始": start_d.strftime("%Y-%m-%d"),
                                "回测结束": end_d.strftime("%Y-%m-%d"),
                                "股票数": len(codes),
                                "总收益率%": None,
                                "成交笔数": None,
                                "期末总资产": None,
                                "备注": str(ex),
                            }
                        )
                        lines.append(f"{d} | [{start_d}~{end_d}] 失败：{ex}")
            finally:
                try:
                    bt_progress("批量回测完成", 100)
                    bt_dlg.setValue(100)
                    bt_dlg.close()
                except Exception:
                    pass
            lines.append("")
            if segment_payloads:
                lines.append(
                    "提示：「导出上次回测结果」= 最后一档快照；「导出批量回测(JSON)」= 全部档。\n"
                    "切换到接续用的策略 B：「载入上一轮批量回测→本策略」再设「下一轮接续」并点「运行下一轮批量回测」。"
                )
            if all_batch_trades:
                lines.append("")
                lines.append(
                    f"成交明细：共 {len(all_batch_trades)} 笔，已填入下方表格；"
                    "「触发信息」列前缀 [选股日 yyyy-mm-dd] 标明该笔所属选股日。"
                )
            self._last_batch_selection_file = path
            stock_summary_rows: List[dict] = []
            if path and all_batch_trades:
                from pathlib import Path

                from tools.merge_backtest_trades_by_selection import apply_selection_file_fields

                stock_summary_rows = _unique_traded_rows_for_selection_copy(all_batch_trades)
                apply_selection_file_fields(stock_summary_rows, Path(path))
            if stock_summary_rows:
                lines.append(
                    f"有交易股票：{len(stock_summary_rows)} 只（保存时从选股文件复制全部列）。"
                )
            sdf = pd.DataFrame(summary_rows)
            self.backtest_result_text.setPlainText(
                "\n".join(lines) + "\n\n—— 汇总表 ——\n" + sdf.to_string(index=False)
            )
            self._fill_backtest_trades_table(all_batch_trades)
            if segment_payloads:
                self._last_batch_export_bundle = {
                    "version": 2,
                    "kind": "batch_backtest",
                    "exported_at": datetime.now().isoformat(timespec="seconds"),
                    "source_strategy_id": cfg.id,
                    "source_strategy_name": cfg.name,
                    "selection_file": path,
                    "batch_rule": f"{'T+1' if from_t1 else 'T当日'}，持有{hold_n}个交易日"
                    + (f"；分时段+{cfg_b_dual.name}" if use_dual and cfg_b_dual else ""),
                    "segments": segment_payloads,
                }
                self._last_batch_bundle_strategy_id = cfg.id
                self._backtest_export_by_strategy[cfg.id] = segment_payloads[-1]
            else:
                self._last_batch_export_bundle = None
                self._last_batch_bundle_strategy_id = None
            self._refresh_backtest_export_button()
            if summary_rows:
                save_ask = QMessageBox.question(
                    self,
                    "批量回测完成",
                    f"已完成 {len(summary_rows)} 日回测"
                    + (
                        f"，有交易股票 {len(stock_summary_rows)} 只"
                        if stock_summary_rows
                        else ""
                    )
                    + "，是否将汇总表保存为 Excel？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if save_ask == QMessageBox.Yes:
                    default_name = os.path.join(
                        os.path.dirname(path),
                        f"批量回测汇总_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    )
                    save_path, _ = QFileDialog.getSaveFileName(
                        self,
                        "保存批量回测汇总表",
                        default_name,
                        "Excel (*.xlsx)",
                    )
                    if save_path:
                        try:
                            export_rows = _batch_excel_summary_rows(
                                summary_rows, stock_summary_rows
                            )
                            with pd.ExcelWriter(save_path, engine="openpyxl") as w:
                                pd.DataFrame(export_rows).to_excel(
                                    w, index=False, sheet_name="汇总"
                                )
                            QMessageBox.information(
                                self,
                                "已保存",
                                f"汇总表：\n{save_path}\n共 {len(export_rows)} 行",
                            )
                        except Exception as ex:
                            QMessageBox.warning(self, "保存失败", str(ex))
        except Exception as e:
            QMessageBox.critical(self, "批量回测错误", str(e))
            self.backtest_result_text.setPlainText(f"批量回测失败：{e}")
            self._fill_backtest_trades_table([])
        finally:
            self.backtest_batch_file_btn.setEnabled(True)
            self.backtest_run_btn.setEnabled(True)

    def _on_pool_select_all_cb_changed(self, state):
        """表格上方的「全选」复选框：勾选则全选列表项，取消勾选则清除选中"""
        checked = state == Qt.Checked
        for i in range(self.pool_list.count()):
            item = self.pool_list.item(i)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _on_pool_delete_batch(self):
        """删除所有打勾的项（删除选中的股票）"""
        rows_to_remove = []
        for i in range(self.pool_list.count()):
            item = self.pool_list.item(i)
            if item and item.checkState() == Qt.Checked:
                rows_to_remove.append(i)
        if not rows_to_remove:
            QMessageBox.information(self, "提示", "请先勾选要删除的项，再点击「删除选中的股票」。")
            return
        for row in sorted(rows_to_remove, reverse=True):
            self.pool_list.takeItem(row)
        self._save_pool_codes_silent()

    def _on_pool_clear_all(self):
        """清空当前策略股票池中的所有股票"""
        if self.pool_list.count() == 0:
            QMessageBox.information(self, "提示", "当前股票池已为空。")
            return
        self.pool_list.clear()
        self.pool_select_all_cb.setCheckState(Qt.Unchecked)
        self._save_pool_codes_silent()

    def _select_strategy_in_list(self, strategy_id: str):
        """在左侧列表中选中指定策略"""
        for i in range(self.strategy_list.count()):
            item = self.strategy_list.item(i)
            if item and (item.data(Qt.UserRole) or "") == strategy_id:
                self.strategy_list.setCurrentItem(item)
                return

    def _select_strategy_programmatically(self, strategy_id: str) -> None:
        """
        定时任务等场景：切换左侧选中策略但不触发「未保存修改」弹窗。
        若当前表单有未保存内容且与目标策略不同，先静默保存当前策略，再改选中项并刷新右侧。
        """
        sid = (strategy_id or "").strip()
        if not sid:
            return
        prev_sid = self._get_selected_strategy_id()
        if prev_sid and prev_sid != sid and self._is_dirty():
            self._save_all_edits_impl(quiet=True)
        self.strategy_list.blockSignals(True)
        try:
            for i in range(self.strategy_list.count()):
                item = self.strategy_list.item(i)
                if item and (item.data(Qt.UserRole) or "") == sid:
                    self.strategy_list.setCurrentItem(item)
                    break
        finally:
            self.strategy_list.blockSignals(False)
        self._update_detail_panel(selected_id=sid)

    # --- Toolbar actions ---

    def _on_add_strategy(self):
        dlg = StrategyEditDialog(self, cfg=None)
        if dlg.exec_() == QDialog.Accepted:
            cfg = dlg.get_config()
            # 名称重复检查
            if self._name_exists(cfg.name):
                QMessageBox.warning(self, "名称已存在", f"已存在名称为「{cfg.name}」的策略，请换一个名称。")
                return
            save_strategy(cfg)
            self._load_strategies_into_list()

    def _on_export_strategy_by_id(self, sid: str):
        """按策略 id 导出到 JSON 文件（用于每行导出图标按钮）"""
        if not sid:
            return
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            QMessageBox.warning(self, "警告", "未找到所选策略配置。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出策略",
            f"{cfg.name}.json",
            "JSON 文件 (*.json);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            data = asdict(cfg)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "导出完成", f"已导出到：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出失败：{e}")

    def _on_import_strategy(self):
        """从 JSON 文件导入策略（会分配新 id，不覆盖现有策略）"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入策略",
            "",
            "JSON 文件 (*.json);;所有文件 (*.*)",
        )
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = strategy_from_import_data(data)
            if self._name_exists(cfg.name):
                cfg = StrategyConfig(
                    id=cfg.id,
                    name=f"{cfg.name}_导入",
                    enabled=cfg.enabled,
                    stock_codes=cfg.stock_codes,
                    strategy_params=cfg.strategy_params,
                    strategy_code=cfg.strategy_code,
                )
            save_strategy(cfg)
            self._load_strategies_into_list(reselect_id=cfg.id)
            QMessageBox.information(self, "导入完成", f"已导入策略「{cfg.name}」。")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"导入失败：{e}")

    def _on_doubleclick_rename(self, item):
        """双击某一行：对该行策略改名（用被点击的 item 的 id，避免 setItemWidget 下选中项未更新拿错 id）"""
        sid = (item.data(Qt.UserRole) or "") if item else ""
        if sid:
            self._on_rename_strategy_by_id(sid)

    def _on_delete_strategy_by_id(self, sid: str):
        if not sid:
            QMessageBox.information(self, "提示", "请先在左侧选择一个策略")
            return
        cfg = self._find_strategy_by_id(sid)
        name = cfg.name if cfg else sid
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除策略「{name}」吗？此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        delete_strategy(sid)
        self._load_strategies_into_list()

    def _on_rename_strategy_by_id(self, sid: str):
        cfg = self._find_strategy_by_id(sid)
        if not cfg:
            QMessageBox.warning(self, "警告", "未找到所选策略配置")
            return
        new_name, ok = QInputDialog.getText(
            self, "编辑策略名称", "策略名称：", QLineEdit.Normal, cfg.name
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if self._name_exists(new_name, exclude_id=cfg.id):
            QMessageBox.warning(self, "名称已存在", f"已存在名称为「{new_name}」的策略，请换一个名称。")
            return
        cfg.name = new_name
        save_strategy(cfg)
        self._load_strategies_into_list(reselect_id=sid)


def main():
    """程序入口"""
    # Windows 控制台编码保持与主程序一致，避免中文乱码
    if sys.platform.startswith("win"):
        os.system("chcp 65001 > nul")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    app = QApplication(sys.argv)

    # --- 单实例控制：蚂蚁量化策略生成系统只允许一个副本 ---
    # 与备份目录（原版 AntStrategyGeneratorSingleton）并存，便于 A/B 对比
    server_name = "AntStrategyGeneratorSingletonV2"

    # 先尝试作为“第二实例”：连到已有的本地服务器，若成功则发送激活请求并退出
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if socket.waitForConnected(200):
        try:
            socket.write(b"activate")
            socket.flush()
            socket.waitForBytesWritten(200)
        except Exception:
            pass
        socket.disconnectFromServer()
        return
    socket.abort()

    # 没有已运行实例：创建本地服务器，供后续实例发送“activate”指令
    server = QLocalServer()
    try:
        QLocalServer.removeServer(server_name)
    except Exception:
        pass
    server.listen(server_name)

    window = StrategyGeneratorMainWindow()
    window.resize(1100, 700)

    # 处理来自后续实例的“激活”请求：将主窗口前置显示
    def handle_new_connection():
        client = server.nextPendingConnection()
        if not client:
            return
        try:
            if client.waitForReadyRead(200):
                _ = client.readAll()
                window.showNormal()
                window.raise_()
                window.activateWindow()
        except Exception:
            pass
        finally:
            client.disconnectFromServer()

    server.newConnection.connect(handle_new_connection)

    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

