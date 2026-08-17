# -*- coding: utf-8 -*-
"""选股引擎用：东财板块/概念日榜 → 合格 TopN 热门 + 组内近10日 RS 排名。

供规则 ctx['em_board_hot'] 使用。
行业：缺 D 或 D-1 快照时返回 error（两臂对照都需要 D/D-1）。
概念：可选；缺任一日 concept CSV 时概念侧为空，不报错。

合格 TopN（方案 B）：按涨跌幅排名顺序遍历全日榜，跳过成分股数 < MIN_MEMBERS
的标签，以及交易状态/风格因子等非产业属性板（utils.em_board_exclude），取满 top_n 个。

同一次加载产出两臂池（便于对照实验）：
- continuous_*：连续热门 = D ∩ D-1 合格 TopN
- new_only_*：仅今日热门 = D 合格 TopN 且不在 D-1 合格 TopN

组内 RS 截断（连续/仅今日臂建池）：每标签取前 ceil(样本数/3)，且不超过 rs_top_k。
今日臂建池保留全部有 RS 的成分；截断字段（合格榜标签RS样本数等）交给规则按 RS_HI 再判。
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import pandas as pd

DateLike = Union[date, datetime, str, None]
_CODE_RE = re.compile(r"(\d{6})")

DEFAULT_TOP_N = 50
DEFAULT_RS_TOP_K = 20
DEFAULT_RS_LOOKBACK = 10  # 近10日：close_D vs close_{D-10} → 需 11 根收盘
MIN_MEMBERS = 30  # 合格热门：成分股数下限（QMT / all_a_stock_info）


def _dashed(d: DateLike) -> Optional[str]:
    if d is None:
        d = date.today()
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    s = str(d).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _to_date(d: DateLike) -> Optional[date]:
    ds = _dashed(d)
    if not ds:
        return None
    try:
        return date(int(ds[0:4]), int(ds[5:7]), int(ds[8:10]))
    except Exception:
        return None


def _norm_code6(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if not s:
        return ""
    m = _CODE_RE.search(s)
    if m:
        return m.group(1)
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)[-6:]
    return ""


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_board_rank_dir() -> str:
    return os.path.join(_project_root(), "data", "eastmoney_board_rank")


def _aggregate_tag_name(raw_name: str) -> str:
    """与 hot_theme 一致：将「xxx概念」归一化为「xxx」。"""
    name = (raw_name or "").strip()
    if not name or name == "nan":
        return ""
    if name.endswith("概念"):
        base = name[:-2].strip()
        return base if base else ""
    return name


def _strip_qmt_prefix(tag: str) -> str:
    s = str(tag or "").strip()
    for pref in ("SW1", "SW2", "SW3", "GN"):
        if s.startswith(pref):
            return s[len(pref) :].strip()
    return s


def _theme_match_keys(name: str) -> Set[str]:
    n = str(name or "").strip()
    if not n:
        return set()
    keys = {n}
    agg = _aggregate_tag_name(n)
    if agg:
        keys.add(agg)
    if not n.endswith("概念") and n:
        keys.add(f"{n}概念")
    return keys


def _tag_matches_hot(tag: str, hot_keys: Set[str]) -> bool:
    if not hot_keys:
        return False
    bare = _strip_qmt_prefix(tag)
    if not bare:
        return False
    if bare in hot_keys:
        return True
    agg = _aggregate_tag_name(bare)
    return bool(agg) and agg in hot_keys


def resolve_prev_trading_day(as_of: DateLike) -> Optional[date]:
    """选股日 D 的前一交易日 D-1。"""
    d = _to_date(as_of)
    if d is None:
        return None
    try:
        from utils.trading_day import last_tradeday_on_or_before
    except Exception:
        last_tradeday_on_or_before = None  # type: ignore[assignment]
    if last_tradeday_on_or_before is not None:
        prev = last_tradeday_on_or_before(d - timedelta(days=1))
        if prev is not None:
            return prev
    # 日历不可用时：向前跳过周末（不静默当作连续2日数据源）
    cur = d - timedelta(days=1)
    for _ in range(10):
        if cur.weekday() < 5:
            return cur
        cur -= timedelta(days=1)
    return None


def board_rank_csv_paths(
    as_of: DateLike,
    *,
    rank_dir: Optional[str] = None,
) -> Dict[str, str]:
    """返回某日 industry / concept CSV 路径（不一定存在）。"""
    ds = _dashed(as_of) or ""
    base = rank_dir or default_board_rank_dir()
    return {
        "industry": os.path.join(base, f"industry_rank_{ds}.csv"),
        "concept": os.path.join(base, f"concept_rank_{ds}.csv"),
    }


def board_fund_flow_csv_paths(
    as_of: DateLike,
    *,
    rank_dir: Optional[str] = None,
) -> Dict[str, str]:
    """返回某日 industry/concept 资金流 CSV 路径（不一定存在）。"""
    ds = _dashed(as_of) or ""
    base = rank_dir or default_board_rank_dir()
    return {
        "industry": os.path.join(base, f"industry_fund_flow_{ds}.csv"),
        "concept": os.path.join(base, f"concept_fund_flow_{ds}.csv"),
    }


def _load_fund_flow_net_ratio_map(path: str) -> Dict[str, float]:
    """名称 → 今日主力净流入-净占比（%）。"""
    out: Dict[str, float] = {}
    if not path or not os.path.isfile(path):
        return out
    try:
        df = _read_csv(path)
    except Exception:
        return out
    if df is None or df.empty:
        return out
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    name_col = None
    for c in ("名称", "板块名称"):
        if c in df.columns:
            name_col = c
            break
    if name_col is None:
        for c in df.columns:
            if "名称" in str(c):
                name_col = c
                break
    ratio_col = "今日主力净流入-净占比" if "今日主力净流入-净占比" in df.columns else None
    if ratio_col is None:
        for c in df.columns:
            if "主力净流入" in str(c) and "占比" in str(c):
                ratio_col = c
                break
    if name_col is None or ratio_col is None:
        return out
    for _, row in df.iterrows():
        name = str(row.get(name_col) or "").strip()
        if not name or name in out:
            continue
        try:
            v = row.get(ratio_col)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            out[name] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _read_csv(path: str) -> pd.DataFrame:
    last_err: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"无法读取 {path}: {last_err}")


def _load_rank_chg_ordered(path: str) -> List[Tuple[str, int, Optional[float]]]:
    """全日榜按涨跌幅顺序：[(板块名称, 排名, 涨跌幅%), ...]，排名 1=最强。

    优先「排名」列升序；否则按「涨跌幅」降序并赋 1..N。
    涨跌幅列优先「涨跌幅」，其次「今日涨跌幅」。
    """
    if not path or not os.path.isfile(path):
        return []
    df = _read_csv(path)
    if df is None or df.empty:
        return []
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    name_col = "板块名称" if "板块名称" in df.columns else None
    if name_col is None:
        for c in df.columns:
            if "名称" in str(c):
                name_col = c
                break
    if name_col is None:
        return []

    chg_col = None
    for c in ("涨跌幅", "今日涨跌幅"):
        if c in df.columns:
            chg_col = c
            break

    if "排名" in df.columns:
        df["_rank"] = pd.to_numeric(df["排名"], errors="coerce")
        work = df.dropna(subset=["_rank"]).sort_values("_rank", ascending=True, kind="mergesort")
    elif chg_col is not None:
        df["_chg"] = pd.to_numeric(df[chg_col], errors="coerce")
        work = df.dropna(subset=["_chg"]).sort_values("_chg", ascending=False, kind="mergesort")
        work = work.reset_index(drop=True)
        work["_rank"] = work.index + 1
    else:
        return []

    if chg_col is not None and "_chg" not in work.columns:
        work = work.copy()
        work["_chg"] = pd.to_numeric(work[chg_col], errors="coerce")

    out: List[Tuple[str, int, Optional[float]]] = []
    seen: Set[str] = set()
    for _, row in work.iterrows():
        name = str(row.get(name_col) or "").strip()
        if not name or name in seen:
            continue
        try:
            rk = int(row["_rank"])
        except (TypeError, ValueError):
            continue
        if rk < 1:
            continue
        chg: Optional[float] = None
        if "_chg" in work.columns:
            try:
                v = row.get("_chg")
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    chg = float(v)
            except (TypeError, ValueError):
                chg = None
        seen.add(name)
        out.append((name, rk, chg))
    return out


def _load_rank_ordered(path: str) -> List[Tuple[str, int]]:
    """全日榜按涨跌幅顺序：[(板块名称, 排名), ...]，排名 1=最强。"""
    return [(n, rk) for n, rk, _chg in _load_rank_chg_ordered(path)]


def _load_rank_chg_maps(path: str) -> Tuple[Dict[str, int], Dict[str, float]]:
    """板块名称 → 原始排名 / 涨跌幅%。"""
    rank: Dict[str, int] = {}
    chg: Dict[str, float] = {}
    for name, rk, pct in _load_rank_chg_ordered(path):
        if name not in rank:
            rank[name] = int(rk)
        if pct is not None and name not in chg:
            chg[name] = float(pct)
    return rank, chg


def _load_rank_map(path: str, *, top_n: int) -> Dict[str, int]:
    """板块名称 → 排名（1=最强），仅保留排名 ≤ top_n（原始 TopN，不含成分过滤）。"""
    n = max(1, int(top_n))
    out: Dict[str, int] = {}
    for name, rk in _load_rank_ordered(path):
        if rk > n:
            continue
        if name not in out:
            out[name] = rk
    return out


def _eligible_top_n(
    ordered: List[Tuple[str, int]],
    *,
    top_n: int,
    min_members: int,
    member_count: Callable[[str], int],
) -> Dict[str, int]:
    """按排名顺序取合格 TopN：跳过成分股数 < min_members 的标签，取满 top_n。

    同时跳过交易状态/风格因子等非产业属性板（见 utils.em_board_exclude）。

    返回 name → 东财原始排名。dict 插入序 = 合格榜内序位（1..top_n）。
    """
    from utils.em_board_exclude import is_excluded_em_board

    n = max(1, int(top_n))
    min_m = max(0, int(min_members))
    out: Dict[str, int] = {}
    for name, rk in ordered:
        if len(out) >= n:
            break
        if not name or name in out:
            continue
        if is_excluded_em_board(name):
            continue
        try:
            cnt = int(member_count(name))
        except Exception:
            cnt = 0
        if cnt < min_m:
            continue
        out[name] = int(rk)
    return out


def _eligible_rank_map(eligible: Dict[str, int]) -> Dict[str, int]:
    """合格 TopN dict（插入序）→ name → 合格榜内序位（1=最热）。"""
    return {name: i for i, name in enumerate(eligible.keys(), start=1)}


def _parse_float_mv_yuan(text: Any) -> Optional[float]:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    if isinstance(text, (int, float)):
        v = float(text)
        return v if v > 1e7 else None
    s = str(text).strip().replace(",", "")
    m = re.match(r"([0-9.]+)\s*亿", s)
    if m:
        return float(m.group(1)) * 1e8
    m = re.match(r"([0-9.]+)\s*万", s)
    if m:
        return float(m.group(1)) * 1e4
    try:
        v = float(s)
        return v if v > 1e7 else None
    except Exception:
        return None


def _float_mv_csv_path(as_of: date) -> str:
    ymd = as_of.strftime("%Y%m%d")
    return os.path.join(
        _project_root(), "history_data", "个股主力净流入", f"个股主力净流入_{ymd}.csv"
    )


def _read_float_mv_yi_from_csv(path: str) -> Dict[str, float]:
    """解析主力净流入 CSV 中的流通市值（亿）。无「流通市值」列则返回空。"""
    out: Dict[str, float] = {}
    if not path or not os.path.isfile(path):
        return out
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return out
    code_cols = [c for c in df.columns if "代码" in str(c)]
    cap_cols = [c for c in df.columns if "流通市值" in str(c)]
    if not code_cols or not cap_cols:
        return out
    code_col, cap_col = code_cols[0], cap_cols[0]
    for _, r in df.iterrows():
        c6 = _norm_code6(r[code_col])
        yuan = _parse_float_mv_yuan(r[cap_col])
        if c6 and yuan and yuan > 0:
            out[c6] = yuan / 1e8
    return out


def _load_float_mv_yi_for_day(as_of: date) -> Dict[str, float]:
    """从 history_data/个股主力净流入 读流通市值（亿）。

    优先当日；若缺文件或旧版 CSV 无「流通市值」列，则向后/向前各最多 12 个
    自然日寻找最近可用快照（流通市值日间变动通常很小，避免月初归档表无该列时全灭）。
    """
    primary = _read_float_mv_yi_from_csv(_float_mv_csv_path(as_of))
    if primary:
        return primary
    for delta in range(1, 13):
        for sign in (1, -1):
            d = as_of + timedelta(days=sign * delta)
            if d.weekday() >= 5:
                continue
            got = _read_float_mv_yi_from_csv(_float_mv_csv_path(d))
            if got:
                return got
    return {}


def _empty_ctx(
    as_of: DateLike,
    *,
    error: str = "",
    prev_date: str = "",
    top_n: int = DEFAULT_TOP_N,
    rs_top_k: int = DEFAULT_RS_TOP_K,
    min_members: int = MIN_MEMBERS,
) -> Dict[str, Any]:
    return {
        "as_of": _dashed(as_of) or "",
        "prev_date": prev_date,
        "top_n": int(top_n),
        "rs_top_k": int(rs_top_k),
        "min_members": int(min_members),
        "error": str(error or ""),
        "continuous_sectors": [],  # [{name, rank_d, rank_d1}]
        "continuous_concepts": [],
        "new_only_sectors": [],
        "new_only_concepts": [],
        "today_sectors": [],
        "today_concepts": [],
        "tag_rs": {},  # "kind|tag" -> {code: {rs, rs_rank, kind}}
        "pool_codes": set(),
        "code_hits": {},  # code6 -> extra fields for select()
        "new_only_pool_codes": set(),
        "new_only_code_hits": {},
        "today_pool_codes": set(),
        "today_code_hits": {},
        "float_mv_yi": {},  # code6 -> 流通市值(亿)
    }


def _load_stock_info_tag_index(
    info_path: Optional[str] = None,
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """从 all_a_stock_info 建 name→codes 与 code→tags。

    Returns:
        (tag_to_codes, code_to_tags)
    """
    path = info_path or os.path.join(_project_root(), "data", "all_a_stock_info.json")
    tag_to_codes: Dict[str, Set[str]] = {}
    code_to_tags: Dict[str, Set[str]] = {}
    if not os.path.isfile(path):
        return tag_to_codes, code_to_tags
    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return tag_to_codes, code_to_tags
    if not isinstance(data, dict):
        return tag_to_codes, code_to_tags

    for code, row in data.items():
        c6 = _norm_code6(code)
        if not c6 or not isinstance(row, dict):
            continue
        tags: Set[str] = set()
        ind = str(row.get("industry") or "").strip()
        if ind:
            tags.add(ind)
        for key in ("concepts", "plates"):
            raw = row.get(key) or []
            if isinstance(raw, list):
                for t in raw:
                    s = str(t or "").strip()
                    if s:
                        tags.add(s)
        if not tags:
            continue
        code_to_tags[c6] = tags
        for t in tags:
            tag_to_codes.setdefault(t, set()).add(c6)
    return tag_to_codes, code_to_tags


def _resolve_members_for_tag(
    tag_name: str,
    *,
    kind: str,
    info_tag_to_codes: Dict[str, Set[str]],
    qmt_tag_to_codes: Dict[str, Set[str]],
) -> Set[str]:
    """优先 all_a_stock_info；再用预建的 QMT 反查索引（避免逐板块打 xtdata）。"""
    keys = _theme_match_keys(tag_name)
    members: Set[str] = set()
    for k in keys:
        members |= set(info_tag_to_codes.get(k) or [])
    members |= set(info_tag_to_codes.get(tag_name) or [])

    for k in keys:
        members |= set(qmt_tag_to_codes.get(k) or [])
    members |= set(qmt_tag_to_codes.get(tag_name) or [])
    return {_norm_code6(c) for c in members if _norm_code6(c)}


def build_code_owned_board_rank_maps(
    as_of: DateLike,
    *,
    rank_dir: Optional[str] = None,
    info_path: Optional[str] = None,
) -> Dict[str, Any]:
    """按东财当日行业/概念涨幅榜 + 成分归属，为每只股票找名次最好的行业与概念。

    与选股规则里「标签名精确撞榜」不同：这里用 all_a_stock_info / QMT 成分反查，
    因此「食品加工」等个股标签也能挂到东财行业板（如畜禽饲料）上。

    Returns:
        {
          \"error\": str,
          \"code_best_industry\": {code6: {\"rank\", \"name\", \"chg\", \"flow_ratio\"}},
          \"code_best_concept\": 同上,
          \"code_industry_hits\": {code6: [(rank, name, chg), ...] 升序},
          \"code_concept_hits\": 同上,
        }
    """
    out: Dict[str, Any] = {
        "error": "",
        "code_best_industry": {},
        "code_best_concept": {},
        "code_industry_hits": {},
        "code_concept_hits": {},
    }
    as_of_d = _to_date(as_of)
    if as_of_d is None:
        out["error"] = "无效日期"
        return out

    try:
        from utils.em_board_exclude import is_excluded_em_board
    except Exception:
        def is_excluded_em_board(_n: str) -> bool:
            return False

    paths = board_rank_csv_paths(as_of_d, rank_dir=rank_dir)
    ind_rank, ind_chg = _load_rank_chg_maps(paths.get("industry") or "")
    con_rank, con_chg = _load_rank_chg_maps(paths.get("concept") or "")
    if not ind_rank and not con_rank:
        out["error"] = "无东财行业/概念涨幅榜"
        return out

    ind_flow: Dict[str, float] = {}
    con_flow: Dict[str, float] = {}
    try:
        fpaths = board_fund_flow_csv_paths(as_of_d, rank_dir=rank_dir)
        ind_flow = _load_fund_flow_net_ratio_map(fpaths.get("industry") or "")
        con_flow = _load_fund_flow_net_ratio_map(fpaths.get("concept") or "")
    except Exception:
        pass

    info_tag_to_codes, _code_to_tags = _load_stock_info_tag_index(info_path)
    store = None
    try:
        from utils.qmt_sector_store import get_qmt_sector_store

        store = get_qmt_sector_store()
    except Exception:
        store = None
    qmt_tag_to_codes = _build_qmt_tag_to_codes(store)

    def _fill(
        rank_map: Dict[str, int],
        chg_map: Dict[str, Any],
        flow_map: Dict[str, float],
        *,
        kind: str,
        best_key: str,
        hits_key: str,
    ) -> None:
        best_map: Dict[str, Dict[str, Any]] = out[best_key]
        hits_map: Dict[str, List[Tuple[int, str, Optional[float]]]] = out[hits_key]
        for name, rk in (rank_map or {}).items():
            nm = str(name or "").strip()
            if not nm or is_excluded_em_board(nm):
                continue
            try:
                rki = int(rk)
            except (TypeError, ValueError):
                continue
            if rki < 1:
                continue
            members = _resolve_members_for_tag(
                nm,
                kind=kind,
                info_tag_to_codes=info_tag_to_codes,
                qmt_tag_to_codes=qmt_tag_to_codes,
            )
            if not members:
                continue
            chg_raw = (chg_map or {}).get(nm)
            try:
                chg_v = None if chg_raw is None else float(chg_raw)
            except (TypeError, ValueError):
                chg_v = None
            flow_raw = (flow_map or {}).get(nm)
            try:
                flow_v = None if flow_raw is None else float(flow_raw)
            except (TypeError, ValueError):
                flow_v = None
            for c6 in members:
                hits_map.setdefault(c6, []).append((rki, nm, chg_v))
                prev = best_map.get(c6)
                if prev is None or rki < int(prev.get("rank") or 10**9):
                    best_map[c6] = {
                        "rank": rki,
                        "name": nm,
                        "chg": chg_v,
                        "flow_ratio": flow_v,
                    }
        for c6, rows in hits_map.items():
            rows.sort(key=lambda x: (int(x[0]), str(x[1])))
            # 同名去重保最优
            seen: Set[str] = set()
            dedup: List[Tuple[int, str, Optional[float]]] = []
            for rki, nm, chg_v in rows:
                if nm in seen:
                    continue
                seen.add(nm)
                dedup.append((rki, nm, chg_v))
            hits_map[c6] = dedup

    _fill(
        ind_rank,
        ind_chg,
        ind_flow,
        kind="sector",
        best_key="code_best_industry",
        hits_key="code_industry_hits",
    )
    _fill(
        con_rank,
        con_chg,
        con_flow,
        kind="concept",
        best_key="code_best_concept",
        hits_key="code_concept_hits",
    )
    return out


def _build_qmt_tag_to_codes(store) -> Dict[str, Set[str]]:
    """从 QMT 反查索引构建 bare-name → codes（SW/GN 前缀已剥离，并含原文键）。"""
    out: Dict[str, Set[str]] = {}
    if store is None:
        return out
    try:
        store.ensure_inverted_index()
    except Exception:
        return out
    code_sectors = getattr(store, "_code_sectors", None) or {}
    if not isinstance(code_sectors, dict):
        return out
    for code, tags in code_sectors.items():
        c6 = _norm_code6(code)
        if not c6 or not isinstance(tags, (list, tuple, set)):
            continue
        for tag in tags:
            t = str(tag or "").strip()
            if not t:
                continue
            bare = _strip_qmt_prefix(t) or t
            agg = _aggregate_tag_name(bare) or bare
            for key in (bare, agg, t):
                if key:
                    out.setdefault(key, set()).add(c6)
            # 概念别名：裸名 ↔ 「裸名概念」
            if bare and not bare.endswith("概念"):
                out.setdefault(f"{bare}概念", set()).add(c6)
    return out


def _rs_for_code(
    code6: str,
    as_of: date,
    *,
    lookback: int = DEFAULT_RS_LOOKBACK,
    close_cache: Dict[str, Optional[Tuple[float, float]]],
) -> Optional[float]:
    """近 lookback 日 RS；(close_D - close_{D-lookback}) / close_{D-lookback}。"""
    if code6 in close_cache:
        pair = close_cache[code6]
        if pair is None:
            return None
        c_d, c_prev = pair
        if c_prev <= 0:
            return None
        return (c_d - c_prev) / c_prev

    try:
        from utils.daily_cache_reader import load_daily_from_cache
    except Exception:
        close_cache[code6] = None
        return None

    df = load_daily_from_cache(code6, through_date=as_of)
    if df is None or getattr(df, "empty", True):
        close_cache[code6] = None
        return None
    if "close" not in df.columns or "date" not in df.columns:
        close_cache[code6] = None
        return None
    work = df[df["date"] <= as_of].copy()
    if work.empty:
        close_cache[code6] = None
        return None
    closes = pd.to_numeric(work["close"], errors="coerce").dropna().tolist()
    need = int(lookback) + 1
    if len(closes) < need:
        close_cache[code6] = None
        return None
    c_d = float(closes[-1])
    c_prev = float(closes[-need])
    if c_d != c_d or c_prev != c_prev or c_prev <= 0 or c_d <= 0:
        close_cache[code6] = None
        return None
    close_cache[code6] = (c_d, c_prev)
    return (c_d - c_prev) / c_prev


def _rank_rs_map(rs_by_code: Dict[str, float]) -> Dict[str, Dict[str, Any]]:
    """rs desc → {code: {rs, rs_rank}}。"""
    items = sorted(rs_by_code.items(), key=lambda kv: (-kv[1], kv[0]))
    out: Dict[str, Dict[str, Any]] = {}
    for i, (code, rs) in enumerate(items, start=1):
        out[code] = {"rs": float(rs), "rs_rank": int(i)}
    return out


def load_em_board_hot_map(
    as_of: DateLike,
    *,
    rank_dir: Optional[str] = None,
    top_n: int = DEFAULT_TOP_N,
    rs_top_k: int = DEFAULT_RS_TOP_K,
    rs_lookback: int = DEFAULT_RS_LOOKBACK,
    min_members: int = MIN_MEMBERS,
    info_path: Optional[str] = None,
    arms: Optional[Any] = None,
    elig_lo: Optional[int] = None,
    elig_hi: Optional[int] = None,
    elig_bands: Optional[Any] = None,
) -> Dict[str, Any]:
    """加载并预计算连续 / 仅今日 / 今日全量热门 + 组内 RS，供选股规则使用。

    结构要点::
        error: 非空则当日不可用（如缺 D/D-1 行业榜）
        continuous_* / pool_codes / code_hits: 连续热门臂
        new_only_* / new_only_pool_codes / new_only_code_hits: 仅今日臂
        today_* / today_pool_codes / today_code_hits: 今日合格 TopN 全量
          （含 eligible_rank=合格榜内序位 1..top_n）
        float_mv_yi: code6 → 流通市值(亿)，来自主力净流入当日表

    连续热门 = D ∩ D-1；仅今日 = D \\ D-1；今日全量 = D 合格 TopN。

    arms:
      None / 省略 → 三臂全建（兼容旧行为）
      可传 str 或可迭代：\"today\" / \"continuous\" / \"new_only\"
      仅建需要的 RS 池，减少无关计算与日志噪音。

    elig_lo / elig_hi 或 elig_bands:
      仅对今日臂生效。建 RS 池前先丢掉序位不在档内的标签，
      避免对 Top50 全员算 RS（头档 Elig1–15 时通常可少算一半以上）。
    """
    arm_set = {"continuous", "new_only", "today"}
    if arms is not None:
        if isinstance(arms, str):
            raw_arms = [arms]
        else:
            try:
                raw_arms = list(arms)
            except TypeError:
                raw_arms = [arms]
        arm_set = {
            str(a).strip().lower()
            for a in raw_arms
            if str(a).strip().lower() in ("continuous", "new_only", "today")
        }
        if not arm_set:
            arm_set = {"continuous", "new_only", "today"}

    bands: List[Tuple[int, int]] = []
    if elig_bands is not None:
        try:
            for item in elig_bands:
                if item is None:
                    continue
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    lo, hi = int(item[0]), int(item[1])
                    if lo >= 1 and hi >= lo:
                        bands.append((lo, hi))
        except (TypeError, ValueError):
            bands = []
    if not bands and elig_lo is not None and elig_hi is not None:
        try:
            lo, hi = int(elig_lo), int(elig_hi)
            if lo >= 1 and hi >= lo:
                bands.append((lo, hi))
        except (TypeError, ValueError):
            pass
    ds = _dashed(as_of)
    as_of_d = _to_date(as_of)
    min_m = max(0, int(min_members))
    if not ds or as_of_d is None:
        return _empty_ctx(
            as_of, error="无效选股日", top_n=top_n, rs_top_k=rs_top_k, min_members=min_m
        )

    prev_d = resolve_prev_trading_day(as_of_d)
    prev_ds = prev_d.strftime("%Y-%m-%d") if prev_d else ""
    if prev_d is None:
        return _empty_ctx(
            as_of,
            error="无法解析前一交易日 D-1",
            top_n=top_n,
            rs_top_k=rs_top_k,
            min_members=min_m,
        )

    paths_d = board_rank_csv_paths(as_of_d, rank_dir=rank_dir)
    paths_p = board_rank_csv_paths(prev_d, rank_dir=rank_dir)
    # 行业必齐：缺 D 或 D-1 → error（绝不把单日当成连续2日）
    missing_ind: List[str] = []
    for label, p in (
        ("D行业", paths_d["industry"]),
        ("D-1行业", paths_p["industry"]),
    ):
        if not os.path.isfile(p):
            missing_ind.append(f"{label}={os.path.basename(p)}")
    if missing_ind:
        return _empty_ctx(
            as_of,
            error=(
                f"东财行业榜快照不齐（需 D={ds} 与 D-1={prev_ds} 的 industry）："
                + "；".join(missing_ind)
                + "。请先运行 tools/snapshot_eastmoney_board_rank.py 补齐，勿用单日冒充连续2日。"
            ),
            prev_date=prev_ds,
            top_n=top_n,
            rs_top_k=rs_top_k,
            min_members=min_m,
        )

    n = max(1, int(top_n))
    # 仅今日臂 + Elig 档时：合格榜只需取到档内最大序位，不必凑满 Top50
    if bands and arm_set <= {"today"}:
        try:
            max_hi = max(hi for _lo, hi in bands)
            if max_hi >= 1:
                n = min(n, int(max_hi))
        except ValueError:
            pass

    # 成分归属先建好，供合格 TopN 过滤（QMT / all_a_stock_info）
    info_tag_to_codes, _code_to_tags = _load_stock_info_tag_index(info_path)
    store = None
    try:
        from utils.qmt_sector_store import get_qmt_sector_store

        store = get_qmt_sector_store()
    except Exception:
        store = None
    qmt_tag_to_codes = _build_qmt_tag_to_codes(store)

    _member_cache: Dict[str, int] = {}

    def _member_count(tag_name: str, *, kind: str = "sector") -> int:
        key = f"{kind}|{tag_name}"
        if key in _member_cache:
            return _member_cache[key]
        members = _resolve_members_for_tag(
            tag_name,
            kind=kind,
            info_tag_to_codes=info_tag_to_codes,
            qmt_tag_to_codes=qmt_tag_to_codes,
        )
        cnt = len(members)
        _member_cache[key] = cnt
        return cnt

    ind_ord_d = _load_rank_ordered(paths_d["industry"])
    ind_ord_p = _load_rank_ordered(paths_p["industry"])
    ind_d = _eligible_top_n(
        ind_ord_d,
        top_n=n,
        min_members=min_m,
        member_count=lambda name: _member_count(name, kind="sector"),
    )
    ind_p = _eligible_top_n(
        ind_ord_p,
        top_n=n,
        min_members=min_m,
        member_count=lambda name: _member_count(name, kind="sector"),
    )

    # 概念可选：D 与 D-1 均存在才加载；缺任一日则 continuous_concepts=[]
    concept_ok = os.path.isfile(paths_d["concept"]) and os.path.isfile(paths_p["concept"])
    con_d: Dict[str, int] = {}
    con_p: Dict[str, int] = {}
    if concept_ok:
        con_ord_d = _load_rank_ordered(paths_d["concept"])
        con_ord_p = _load_rank_ordered(paths_p["concept"])
        con_d = _eligible_top_n(
            con_ord_d,
            top_n=n,
            min_members=min_m,
            member_count=lambda name: _member_count(name, kind="concept"),
        )
        con_p = _eligible_top_n(
            con_ord_p,
            top_n=n,
            min_members=min_m,
            member_count=lambda name: _member_count(name, kind="concept"),
        )

    ind_elig = _eligible_rank_map(ind_d)
    con_elig = _eligible_rank_map(con_d) if concept_ok else {}

    continuous_sectors: List[Dict[str, Any]] = []
    new_only_sectors: List[Dict[str, Any]] = []
    today_sectors: List[Dict[str, Any]] = []
    for name, rk in ind_d.items():
        elig = int(ind_elig.get(name) or 0)
        today_sectors.append(
            {
                "name": name,
                "rank_d": int(rk),
                "eligible_rank": elig,
                "rank_d1": int(ind_p[name]) if name in ind_p else "",
            }
        )
        rk1 = ind_p.get(name)
        if rk1 is None:
            new_only_sectors.append(
                {"name": name, "rank_d": int(rk), "eligible_rank": elig, "rank_d1": ""}
            )
        else:
            continuous_sectors.append(
                {
                    "name": name,
                    "rank_d": int(rk),
                    "eligible_rank": elig,
                    "rank_d1": int(rk1),
                }
            )
    continuous_sectors.sort(key=lambda x: (x["rank_d"], x["name"]))
    new_only_sectors.sort(key=lambda x: (x["rank_d"], x["name"]))
    today_sectors.sort(key=lambda x: (int(x["eligible_rank"]), x["name"]))

    continuous_concepts: List[Dict[str, Any]] = []
    new_only_concepts: List[Dict[str, Any]] = []
    today_concepts: List[Dict[str, Any]] = []
    if concept_ok:
        for name, rk in con_d.items():
            elig = int(con_elig.get(name) or 0)
            today_concepts.append(
                {
                    "name": name,
                    "rank_d": int(rk),
                    "eligible_rank": elig,
                    "rank_d1": int(con_p[name]) if name in con_p else "",
                }
            )
            rk1 = con_p.get(name)
            if rk1 is None:
                new_only_concepts.append(
                    {"name": name, "rank_d": int(rk), "eligible_rank": elig, "rank_d1": ""}
                )
            else:
                continuous_concepts.append(
                    {
                        "name": name,
                        "rank_d": int(rk),
                        "eligible_rank": elig,
                        "rank_d1": int(rk1),
                    }
                )
        continuous_concepts.sort(key=lambda x: (x["rank_d"], x["name"]))
        new_only_concepts.sort(key=lambda x: (x["rank_d"], x["name"]))
        today_concepts.sort(key=lambda x: (int(x["eligible_rank"]), x["name"]))

    # tag_name -> {code: {rs, rs_rank, kind, em_rank}}（多臂共用缓存，避免重复算 RS）
    tag_rs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    close_cache: Dict[str, Optional[Tuple[float, float]]] = {}
    k_top = max(1, int(rs_top_k))

    def _process_tags(
        tags: List[Dict[str, Any]], kind: str, code_tag_hits: Dict[str, List[Dict[str, Any]]]
    ) -> None:
        for rec in tags:
            name = str(rec.get("name") or "").strip()
            if not name:
                continue
            em_rank = int(rec.get("rank_d") or 0)
            elig_rank = int(rec.get("eligible_rank") or 0)
            cache_key = f"{kind}|{name}"
            if cache_key in tag_rs:
                for c6, row in tag_rs[cache_key].items():
                    hit_row = dict(row)
                    # 缓存行可能缺 eligible_rank（旧路径）；以当前 rec 为准
                    hit_row["eligible_rank"] = elig_rank
                    hit_row["em_rank"] = em_rank
                    code_tag_hits.setdefault(c6, []).append(hit_row)
                continue
            members = _resolve_members_for_tag(
                name,
                kind=kind,
                info_tag_to_codes=info_tag_to_codes,
                qmt_tag_to_codes=qmt_tag_to_codes,
            )
            rs_map: Dict[str, float] = {}
            for c6 in members:
                rs = _rs_for_code(c6, as_of_d, lookback=rs_lookback, close_cache=close_cache)
                if rs is None:
                    continue
                rs_map[c6] = float(rs)
            ranked = _rank_rs_map(rs_map)
            tag_rs_n = len(ranked)
            enriched: Dict[str, Dict[str, Any]] = {}
            for c6, info in ranked.items():
                row = {
                    "rs": info["rs"],
                    "rs_rank": info["rs_rank"],
                    "tag_rs_n": tag_rs_n,
                    "kind": kind,
                    "tag": name,
                    "em_rank": em_rank,
                    "eligible_rank": elig_rank,
                }
                enriched[c6] = row
                code_tag_hits.setdefault(c6, []).append(row)
            tag_rs[cache_key] = enriched

    def _build_pool(
        sector_tags: List[Dict[str, Any]],
        concept_tags: List[Dict[str, Any]],
        *,
        hit_count_key: str,
        a_rank_key: str,
        b_rank_key: str,
        include_eligible: bool = False,
        rs_cap: Optional[int] = None,
    ) -> Tuple[Set[str], Dict[str, Dict[str, Any]]]:
        code_tag_hits: Dict[str, List[Dict[str, Any]]] = {}
        _process_tags(sector_tags, "sector", code_tag_hits)
        _process_tags(concept_tags, "concept", code_tag_hits)

        hard_cap = int(rs_cap) if rs_cap is not None else None

        def _rs_cut_for_hit(h: Dict[str, Any]) -> int:
            """每标签入选上限：ceil(样本数/3)，且不超过 hard_cap（若有）。"""
            try:
                n = int(h.get("tag_rs_n") or 0)
            except (TypeError, ValueError):
                n = 0
            if n <= 0:
                return 0
            frac_cut = max(1, (n + 2) // 3)  # ceil(n/3)
            if hard_cap is None:
                return frac_cut
            return min(hard_cap, frac_cut)

        pool_codes: Set[str] = set()
        code_hits: Dict[str, Dict[str, Any]] = {}
        for c6, hits in code_tag_hits.items():
            if not hits:
                continue
            if hard_cap is None:
                # 今日臂：池内保留全部有 RS 的成分，截断由规则按样本数/RS_HI 再判
                in_pool = any(int(h.get("rs_rank") or 0) > 0 for h in hits)
            else:
                # 连续/仅今日臂：组内 RS ≤ min(rs_top_k, ceil(n/3))
                in_pool = any(
                    int(h.get("rs_rank") or 999999) <= _rs_cut_for_hit(h) for h in hits
                )
            if not in_pool:
                continue
            pool_codes.add(c6)

            sector_hits = [h for h in hits if h.get("kind") == "sector"]
            concept_hits = [h for h in hits if h.get("kind") == "concept"]

            best_sector = (
                min(sector_hits, key=lambda h: (int(h["em_rank"]), str(h["tag"])))
                if sector_hits
                else None
            )
            best_concept = (
                min(concept_hits, key=lambda h: (int(h["em_rank"]), str(h["tag"])))
                if concept_hits
                else None
            )
            best_rs_hit = min(
                hits,
                key=lambda h: (int(h["rs_rank"]), int(h["em_rank"]), str(h["tag"])),
            )

            a_rank = int(best_sector["em_rank"]) if best_sector else None
            b_rank = int(best_concept["em_rank"]) if best_concept else None
            rs_in_a = int(best_sector["rs_rank"]) if best_sector else None
            rs_in_b = int(best_concept["rs_rank"]) if best_concept else None

            row_out: Dict[str, Any] = {
                a_rank_key: a_rank if a_rank is not None else "",
                b_rank_key: b_rank if b_rank is not None else "",
                "在A中的RS排名": rs_in_a if rs_in_a is not None else "",
                "在B中的RS排名": rs_in_b if rs_in_b is not None else "",
                "A对应板块": str(best_sector["tag"]) if best_sector else "",
                "B对应概念": str(best_concept["tag"]) if best_concept else "",
                "RS最好的热门板块或概念": str(best_rs_hit.get("tag") or ""),
                "在其中的RS排名": int(best_rs_hit["rs_rank"]),
                "近10日RS": round(float(best_rs_hit["rs"]), 6),
                hit_count_key: len(hits),
            }
            if include_eligible:
                # 全部命中标签明细（供规则按「任一标签 Elig+RS 过关」再筛，并在过关集里取最热）
                tag_rows = []
                for h in hits:
                    try:
                        elig_n = int(h.get("tag_rs_n") or 0)
                    except (TypeError, ValueError):
                        elig_n = 0
                    tag_rows.append(
                        {
                            "tag": str(h.get("tag") or ""),
                            "kind": str(h.get("kind") or ""),
                            "eligible_rank": int(h.get("eligible_rank") or 0),
                            "em_rank": int(h.get("em_rank") or 0),
                            "rs_rank": int(h.get("rs_rank") or 0),
                            "tag_rs_n": elig_n,
                            "rs": h.get("rs"),
                        }
                    )
                row_out["合格榜命中标签"] = tag_rows
                # 兼容旧字段：仍填「全部命中里最热」；规则侧应改读命中列表后重选过关最热
                best_elig = min(
                    hits,
                    key=lambda h: (
                        int(h.get("eligible_rank") or 999999),
                        int(h.get("em_rank") or 999999),
                        str(h.get("tag") or ""),
                    ),
                )
                row_out["合格榜内序位"] = int(best_elig.get("eligible_rank") or 0)
                row_out["合格榜对应标签"] = str(best_elig.get("tag") or "")
                row_out["合格榜标签类型"] = str(best_elig.get("kind") or "")
                row_out["合格榜标签东财排名"] = int(best_elig.get("em_rank") or 0)
                row_out["合格榜标签内RS排名"] = int(best_elig.get("rs_rank") or 0)
                try:
                    elig_n = int(best_elig.get("tag_rs_n") or 0)
                except (TypeError, ValueError):
                    elig_n = 0
                row_out["合格榜标签RS样本数"] = elig_n
                row_out["合格榜标签RS前三分之一"] = (
                    max(1, (elig_n + 2) // 3) if elig_n > 0 else 0
                )
            code_hits[c6] = row_out
        return pool_codes, code_hits

    pool_codes: Set[str] = set()
    code_hits: Dict[str, Dict[str, Any]] = {}
    new_only_pool_codes: Set[str] = set()
    new_only_code_hits: Dict[str, Dict[str, Any]] = {}
    today_pool_codes: Set[str] = set()
    today_code_hits: Dict[str, Dict[str, Any]] = {}

    if "continuous" in arm_set:
        pool_codes, code_hits = _build_pool(
            continuous_sectors,
            continuous_concepts,
            hit_count_key="命中连续热门标签数",
            a_rank_key="连续2天热门板块最高排名A",
            b_rank_key="连续两天热门概念最高排名B",
            rs_cap=k_top,
        )
    if "new_only" in arm_set:
        new_only_pool_codes, new_only_code_hits = _build_pool(
            new_only_sectors,
            new_only_concepts,
            hit_count_key="命中仅今日热门标签数",
            a_rank_key="仅今日热门板块最高排名A",
            b_rank_key="仅今日热门概念最高排名B",
            rs_cap=k_top,
        )
    if "today" in arm_set:
        sec_for_rs = today_sectors
        con_for_rs = today_concepts
        if bands:

            def _elig_ok(rec: Dict[str, Any]) -> bool:
                try:
                    er = int(rec.get("eligible_rank") or 0)
                except (TypeError, ValueError):
                    return False
                return any(lo <= er <= hi for lo, hi in bands)

            sec_for_rs = [t for t in today_sectors if _elig_ok(t)]
            con_for_rs = [t for t in today_concepts if _elig_ok(t)]
        # 今日臂：默认可含全部合格 TopN；若给了 Elig 档则只对档内标签算 RS
        today_pool_codes, today_code_hits = _build_pool(
            sec_for_rs,
            con_for_rs,
            hit_count_key="命中今日热门标签数",
            a_rank_key="今日热门板块最高排名A",
            b_rank_key="今日热门概念最高排名B",
            include_eligible=True,
            rs_cap=None,
        )
    else:
        sec_for_rs = []
        con_for_rs = []

    float_mv_yi = _load_float_mv_yi_for_day(as_of_d)

    return {
        "as_of": ds,
        "prev_date": prev_ds,
        "top_n": n,
        "rs_top_k": k_top,
        "min_members": min_m,
        "error": "",
        "arms": sorted(arm_set),
        "elig_bands": list(bands),
        "continuous_sectors": continuous_sectors if "continuous" in arm_set else [],
        "continuous_concepts": continuous_concepts if "continuous" in arm_set else [],
        "new_only_sectors": new_only_sectors if "new_only" in arm_set else [],
        "new_only_concepts": new_only_concepts if "new_only" in arm_set else [],
        "today_sectors": list(sec_for_rs) if "today" in arm_set else [],
        "today_concepts": list(con_for_rs) if "today" in arm_set else [],
        "tag_rs": tag_rs,
        "pool_codes": pool_codes,
        "code_hits": code_hits,
        "new_only_pool_codes": new_only_pool_codes,
        "new_only_code_hits": new_only_code_hits,
        "today_pool_codes": today_pool_codes,
        "today_code_hits": today_code_hits,
        "float_mv_yi": float_mv_yi,
    }
