# -*- coding: utf-8 -*-
"""QMT xtdata 板块/行业/概念数据（替代东财 all_a_stock_info.json）。"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
from typing import Dict, List, Literal, Optional, Set

logger = logging.getLogger(__name__)

UI_SECTOR_PREFIXES = ("SW1", "GN", "SW2", "SW3")

EXCLUDE_SUBSTR = (
    "加权",
    "过期",
    "指数",
    "ETF",
    "债券",
    "基金",
    "期权",
    "转债",
    "期货",
    "科创板CDR",
    "连续合约",
)

MARKET_SECTOR_EXACT = frozenset(
    {
        "沪深A股",
        "沪深京A股",
        "沪深B股",
        "上证A股",
        "上证B股",
        "深证A股",
        "深证B股",
        "京市A股",
        "北交所",
        "创业板",
        "科创板",
        "上期所",
        "中金所",
        "大商所",
        "郑商所",
        "能源中心",
        "香港联交所指数",
        "香港联交所股票",
    }
)

_UNIVERSE_SECTOR = "沪深A股"
_UNIVERSE_SECTORS = (
    "沪深A股",
    "上证A股",
    "深证A股",
    "京市A股",
    "沪深京A股",
)
# 虚拟板块：不在任何 SW/GN 等 UI 板块成分中的 A 股（展示名；旧名「未归属板块」仍识别）
UNCLASSIFIED_SECTOR = "不属于任何板块的股票"
_UNCLASSIFIED_ALIASES = frozenset(
    {
        UNCLASSIFIED_SECTOR,
        "未归属板块",
    }
)
_CACHE_FILENAME = "qmt_sector_index.json"
_UNIVERSE_FILENAME = "a_share_universe.json"
CACHE_MAX_AGE_DAYS = 7


def is_virtual_sector(name: str) -> bool:
    return str(name or "").strip() in _UNCLASSIFIED_ALIASES


def _qmt_sectors_only(sectors: List[str]) -> List[str]:
    return sorted({s for s in (sectors or []) if s and not is_virtual_sector(s)})


def _with_virtual_sector(sectors: List[str]) -> List[str]:
    """虚拟板块放列表首项，便于在选股 UI 中找到。"""
    qmt = _qmt_sectors_only(sectors)
    return [UNCLASSIFIED_SECTOR] + qmt


def _project_data_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "data")
    os.makedirs(path, exist_ok=True)
    return path


def _cache_path() -> str:
    return os.path.join(_project_data_dir(), _CACHE_FILENAME)


def _load_xtdata():
    import xtquant.xtdata as xtdata

    try:
        xtdata.enable_hello = False
    except Exception:
        pass
    return xtdata


def code_to_6(full: str) -> str:
    s = str(full).strip().upper()
    if "." in s:
        s = s.split(".")[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def is_ui_sector(name: str) -> bool:
    if not name or name in MARKET_SECTOR_EXACT:
        return False
    if any(x in name for x in EXCLUDE_SUBSTR):
        return False
    return any(name.startswith(p) for p in UI_SECTOR_PREFIXES)


def split_sector_tags(sectors: List[str]) -> Dict[str, List[str]]:
    """将 QMT 板块名拆为导出列：概念(GN)、行业(SW1)、细分(SW2/SW3)。"""
    concepts: List[str] = []
    industries: List[str] = []
    sub: List[str] = []
    for s in sectors:
        if s.startswith("GN"):
            concepts.append(s)
        elif s.startswith("SW1"):
            industries.append(s)
        elif s.startswith(("SW2", "SW3")):
            sub.append(s)
    return {
        "concepts": concepts,
        "industries": industries,
        "sub_sectors": sub,
    }


class QmtSectorStore:
    """QMT 板块数据单例：板块列表、成分股、股票反查索引。"""

    _instance: Optional["QmtSectorStore"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "QmtSectorStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._initialized = False
                    cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._xtdata = None
        self._ui_sectors: Optional[List[str]] = None
        self._universe: Optional[Set[str]] = None
        self._sector_members: Dict[str, Set[str]] = {}
        self._code_sectors: Dict[str, List[str]] = {}
        self._index_built_for: Optional[tuple] = None
        self._index_lock = threading.Lock()
        # 名称查询时 xtdata 一旦失败则整进程禁用，避免全市场逐只重连卡死 UI
        self._xtdata_name_disabled = False

    def xtdata(self):
        if self._xtdata is None:
            self._xtdata = _load_xtdata()
        return self._xtdata

    def _universe_from_file(self) -> Set[str]:
        path = os.path.join(_project_data_dir(), _UNIVERSE_FILENAME)
        if not os.path.isfile(path):
            return set()
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f) or {}
            raw = list(payload.get("codes") or [])
            codes = {code_to_6(c) for c in raw if code_to_6(c)}
            if codes:
                logger.info("沪深A股 universe(文件 %s): %d", _UNIVERSE_FILENAME, len(codes))
            return codes
        except Exception as e:
            logger.warning("读取 %s 失败: %s", _UNIVERSE_FILENAME, e)
            return set()

    def get_universe(self) -> Set[str]:
        """沪深 A 股全集。

        builtin/standalone：优先 data/a_share_universe.json（大 QMT 同步），
        避免本机连不上 xtquant 时在选股启动路径上反复超时。
        其他模式：优先 xtdata「沪深A股」；其次本地文件；再回退板块反查索引键。
        勿把「仅有板块标签的子集」当成全集，否则「不属于任何板块」虚拟板块成员会变成 0。
        """
        if self._universe:
            return self._universe

        prefer_file = False
        try:
            from utils.qmt_execution_config import get_qmt_mode

            prefer_file = get_qmt_mode() in ("builtin", "standalone")
        except Exception:
            prefer_file = False

        if prefer_file:
            self._xtdata_name_disabled = True
            uni = self._universe_from_file()
            if uni:
                self._universe = uni
                return self._universe

        try:
            raw = []
            seen_raw = set()
            for sec in _UNIVERSE_SECTORS:
                try:
                    part = self.xtdata().get_stock_list_in_sector(sec) or []
                except Exception as e:
                    logger.error("get_stock_list_in_sector(%s) 失败: %s", sec, e)
                    part = []
                for c in part:
                    key = str(c or "").strip()
                    if key and key not in seen_raw:
                        seen_raw.add(key)
                        raw.append(key)
        except Exception as e:
            logger.error("get_stock_list_in_sector(universe) 失败: %s", e)
            raw = []
        uni = {code_to_6(c) for c in raw if code_to_6(c)}
        if uni:
            self._universe = uni
            logger.info("沪深京A股 universe: %d", len(self._universe))
            return self._universe

        uni = self._universe_from_file()
        if uni:
            self._universe = uni
            return self._universe

        # 最后回退：索引键（可能缺少无板块归属的股票）
        if not self._code_sectors:
            try:
                self._load_disk_cache([])
            except Exception:
                pass
        if self._code_sectors:
            self._universe = set(self._code_sectors.keys())
            logger.info(
                "沪深A股 universe(索引缓存回退): %d（无 a_share_universe.json 时可能偏少）",
                len(self._universe),
            )
            return self._universe

        self._universe = set()
        logger.warning("沪深A股 universe 为空（QMT 未连且无 universe/板块索引）")
        return self._universe

    def list_ui_sectors(self, force_refresh: bool = False) -> List[str]:
        if self._ui_sectors is not None and not force_refresh:
            return self._ui_sectors
        if not force_refresh and self._load_disk_cache([]):
            return self._ui_sectors
        try:
            raw = self.xtdata().get_sector_list() or []
        except Exception as e:
            logger.error("get_sector_list 失败: %s", e)
            raw = []
        sectors = sorted({s for s in raw if is_ui_sector(s)})
        self._ui_sectors = _with_virtual_sector(sectors)
        gn = sum(1 for s in sectors if s.startswith("GN"))
        uni_n = len(self.get_universe())
        idx_n = len(self._code_sectors or {})
        uncls = max(0, uni_n - idx_n) if idx_n else 0
        logger.info(
            "QMT 可选板块: %d + 虚拟1 (SW1=%d GN=%d 无板块归属≈%d)",
            len(sectors),
            sum(1 for s in sectors if s.startswith("SW1")),
            gn,
            uncls,
        )
        if gn == 0:
            logger.warning(
                "未检测到 GN 概念板块；内置策略会在 07:30/启动时同步板块，或在 QMT 下载中心勾选「全部板块」"
            )
        return self._ui_sectors

    def unclassified_codes(self) -> Set[str]:
        self.ensure_inverted_index()
        return self.get_universe() - set(self._code_sectors.keys())

    def _all_sectors_for_code(self, code6: str) -> List[str]:
        code6 = code_to_6(code6)
        tags = list(self._code_sectors.get(code6, []))
        if not tags and code6 in self.unclassified_codes():
            return [UNCLASSIFIED_SECTOR]
        return tags

    def members_of(self, sector: str) -> Set[str]:
        if is_virtual_sector(sector):
            codes = self.unclassified_codes()
            self._sector_members[sector] = codes
            return codes
        if sector in self._sector_members:
            return self._sector_members[sector]
        try:
            raw = self.xtdata().get_stock_list_in_sector(sector) or []
        except Exception as e:
            logger.warning("get_stock_list_in_sector(%s) 失败: %s", sector, e)
            raw = []
        universe = self.get_universe()
        members = {code_to_6(c) for c in raw if code_to_6(c)}
        if universe:
            members &= universe
        # QMT 未连时 get_stock_list_in_sector 恒为空：从反查索引按标签回退
        if not members:
            try:
                self.ensure_inverted_index()
            except Exception:
                pass
            code_sectors = self._code_sectors or {}
            members = {c for c, tags in code_sectors.items() if sector in (tags or [])}
            if universe:
                members &= universe
        self._sector_members[sector] = members
        return members

    def _load_disk_cache(self, ui_sectors: List[str]) -> bool:
        path = _cache_path()
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            logger.warning("读取板块索引缓存失败: %s", e)
            return False
        if not isinstance(payload, dict):
            return False
        built_at = str(payload.get("built_at") or "").strip()
        if not built_at:
            return False
        try:
            age_days = (date.today() - date.fromisoformat(built_at)).days
        except Exception:
            return False
        if age_days < 0 or age_days > CACHE_MAX_AGE_DAYS:
            return False
        cached_sectors = payload.get("ui_sectors") or []
        if not cached_sectors:
            return False
        if payload.get("sector_count") not in (None, len(cached_sectors)):
            return False
        code_sectors = payload.get("code_sectors") or {}
        if not isinstance(code_sectors, dict) or not code_sectors:
            return False
        self._ui_sectors = _with_virtual_sector(_qmt_sectors_only(cached_sectors))
        self._code_sectors = {
            code_to_6(k): sorted(v for v in (val or []) if v)
            for k, val in code_sectors.items()
            if code_to_6(k)
        }
        self._index_built_for = tuple(self._ui_sectors)
        logger.info(
            "已加载板块索引缓存: %d 只股票, %d 板块, built_at=%s",
            len(self._code_sectors),
            len(self._ui_sectors),
            built_at,
        )
        return True

    def _save_disk_cache(self, ui_sectors: List[str]) -> None:
        path = _cache_path()
        payload = {
            "built_at": date.today().isoformat(),
            "sector_count": len(ui_sectors),
            "ui_sectors": ui_sectors,
            "code_sectors": self._code_sectors,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            logger.info("已写入板块索引缓存: %s", path)
        except Exception as e:
            logger.warning("写入板块索引缓存失败: %s", e)

    def ensure_inverted_index(self, force_rebuild: bool = False) -> None:
        ui_sectors = self.list_ui_sectors()
        qmt_sectors = _qmt_sectors_only(ui_sectors)
        key = tuple(ui_sectors)
        with self._index_lock:
            if not force_rebuild and self._index_built_for == key and self._code_sectors is not None:
                return
            if not force_rebuild and self._load_disk_cache([]):
                if self._index_built_for == key:
                    return

            logger.info("构建 QMT 板块反查索引（%d 个板块）…", len(qmt_sectors))
            code_to_set: Dict[str, Set[str]] = {}
            for i, sector in enumerate(qmt_sectors, 1):
                for code in self.members_of(sector):
                    code_to_set.setdefault(code, set()).add(sector)
                if i % 50 == 0:
                    logger.info("  索引进度 %d/%d", i, len(qmt_sectors))

            self._code_sectors = {c: sorted(ss) for c, ss in code_to_set.items()}
            self._index_built_for = key
            self._save_disk_cache(qmt_sectors)
            logger.info(
                "板块反查索引完成: %d 只有标签, %d 未归属",
                len(self._code_sectors),
                len(self.unclassified_codes()),
            )

    def indexed_stock_count(self) -> int:
        self.ensure_inverted_index()
        return len(self._code_sectors)

    def sectors_for_stock(self, code6: str) -> List[str]:
        self.ensure_inverted_index()
        return self._all_sectors_for_code(code6)

    def stock_name(self, code6: str) -> str:
        code6 = code_to_6(code6)
        try:
            from utils.stock_info_manager import get_stock_name

            name = get_stock_name(code6)
            if name and name != "未知名称":
                return name
        except Exception:
            pass
        if self._xtdata_name_disabled:
            return "未知"
        try:
            suffix = ".SH" if code6.startswith("6") else ".SZ"
            detail = self.xtdata().get_instrument_detail(f"{code6}{suffix}")
            if isinstance(detail, dict):
                n = detail.get("InstrumentName") or detail.get("instrumentName")
                if n:
                    return str(n)
        except Exception:
            self._xtdata_name_disabled = True
        return "未知"

    def stocks_for_sectors(
        self,
        selected: List[str],
        mode: Literal["union", "intersection"] = "union",
    ) -> Dict[str, Dict[str, object]]:
        selected = [s.strip() for s in (selected or []) if s and str(s).strip()]
        if not selected:
            return {}

        self.ensure_inverted_index()
        ui_all = self.list_ui_sectors()
        selected_set = set(selected)
        all_selected = selected_set == set(ui_all)

        if mode == "intersection":
            member_sets = [self.members_of(s) for s in selected]
            if not member_sets:
                return {}
            matched_codes = set.intersection(*member_sets)
            out: Dict[str, Dict[str, object]] = {}
            for code in sorted(matched_codes):
                all_s = self._all_sectors_for_code(code)
                matched_s = sorted(selected_set.intersection(all_s))
                out[code] = {
                    "name": self.stock_name(code),
                    "matched_sectors": matched_s,
                    "all_sectors": all_s,
                }
            return out

        if all_selected:
            # 全选 = 全市场；universe 会在 QMT 不可用时回退到索引缓存
            matched_codes = sorted(self.get_universe())
            if not matched_codes and self._code_sectors:
                matched_codes = sorted(self._code_sectors.keys())
        else:
            matched: Set[str] = set()
            for s in selected:
                matched |= self.members_of(s)
            matched_codes = sorted(matched)

        out = {}
        for code in matched_codes:
            all_s = self._all_sectors_for_code(code)
            if all_selected:
                matched_s = list(all_s)
            else:
                matched_s = sorted(selected_set.intersection(all_s))
            out[code] = {
                "name": self.stock_name(code),
                "matched_sectors": matched_s,
                "all_sectors": all_s,
            }
        return out


_store: Optional[QmtSectorStore] = None


def get_qmt_sector_store() -> QmtSectorStore:
    global _store
    if _store is None:
        _store = QmtSectorStore()
    return _store


def load_all_sectors() -> List[str]:
    """选股 UI 用：返回 QMT 申万/概念等板块列表。"""
    return get_qmt_sector_store().list_ui_sectors()
