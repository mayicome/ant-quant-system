#coding:gbk
"""Shadow ��ͻ�� tick ״̬����"""

from typing import Any, Dict, List, Optional, Tuple

try:
    from ant_true_breakthrough_lite import (
        TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS,
        evaluate_true_breakthrough_tick_with_detail,
        infer_tick_vol_to_shares_multiplier,
        is_breakthrough_break_above_trigger_tick,
        is_breakthrough_break_below_trigger_tick,
        is_breakthrough_buy_price_cross_tick,
        is_breakthrough_sell_price_cross_tick,
        normalize_true_breakthrough_cond1_mode,
        per_tick_trade_volumes_list,
        round_price_like_display,
        window_prior_ticks_from_seconds,
    )
except ImportError:
    from qmt_builtin.ant_true_breakthrough_lite import (
        TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS,
        evaluate_true_breakthrough_tick_with_detail,
        infer_tick_vol_to_shares_multiplier,
        is_breakthrough_break_above_trigger_tick,
        is_breakthrough_break_below_trigger_tick,
        is_breakthrough_buy_price_cross_tick,
        is_breakthrough_sell_price_cross_tick,
        normalize_true_breakthrough_cond1_mode,
        per_tick_trade_volumes_list,
        round_price_like_display,
        window_prior_ticks_from_seconds,
    )

try:
    from ant_elastic_sell_lite import (
        compute_best_sell_fallback_from_rule,
        load_elastic_confirm_triple,
        resolve_limit_up_pre_close,
    )
except ImportError:
    try:
        from qmt_builtin.ant_elastic_sell_lite import (
            compute_best_sell_fallback_from_rule,
            load_elastic_confirm_triple,
            resolve_limit_up_pre_close,
        )
    except ImportError:
        compute_best_sell_fallback_from_rule = None  # type: ignore
        load_elastic_confirm_triple = None  # type: ignore
        resolve_limit_up_pre_close = None  # type: ignore

try:
    from ant_elastic_buy_lite import compute_best_buy_rebound_from_rule
except ImportError:
    try:
        from qmt_builtin.ant_elastic_buy_lite import compute_best_buy_rebound_from_rule
    except ImportError:
        compute_best_buy_rebound_from_rule = None  # type: ignore


def _code6(stock_code: str) -> str:
    return (stock_code or "").strip().split(".")[0][:6]


def _light_row(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    out: Dict[str, Any] = {}
    cols = (
        "lastPrice", "last_price", "tradePrice", "matchPrice", "price", "last",
        "open", "openPrice", "open_price", "todayOpen",
        "high", "highPrice", "todayHigh",
        "low", "lowPrice", "todayLow",
        "amount", "volume", "lastVol", "tradeVol", "tradeVolume", "tickVol",
        "singleVol", "matchQty", "qty", "volume_delta", "cumVol", "totalVol", "dealVol",
        "askPrice", "askVol", "bidPrice", "bidVol", "time",
        "highLimit", "upperLimit", "limitUp", "lastClose", "preClose", "pre_close",
    )
    for c in cols:
        v = None
        if hasattr(raw, "get"):
            try:
                v = raw.get(c)
            except Exception:
                v = None
        if v is None:
            try:
                v = getattr(raw, c, None)
            except Exception:
                v = None
        if v is not None:
            out[c] = v
    return out


class _PrefixState:
    __slots__ = (
        "prev_row",
        "prev_last_price",
        "recent_rows",
        "recent_vols",
        "prefix_sum",
        "prefix_cnt",
        "vol_mul",
        "vol_mul_ready",
        "tick_buf",
        "break_below_done",
        "break_above_done",
        "done_task_ids",
    )

    def __init__(self, break_below_preset: bool = False, break_above_preset: bool = False) -> None:
        self.prev_row: Optional[Dict[str, Any]] = None
        self.prev_last_price: Optional[float] = None
        self.recent_rows: List[Dict[str, Any]] = []
        self.recent_vols: List[Optional[float]] = []
        self.prefix_sum = 0.0
        self.prefix_cnt = 0
        self.vol_mul = 100.0
        self.vol_mul_ready = False
        self.tick_buf: List[Dict[str, Any]] = []
        self.break_below_done = bool(break_below_preset)
        self.break_above_done = bool(break_above_preset)
        self.done_task_ids: set = set()


class ShadowTickRunner:
#  armed tasks +  dict shadow 

    def __init__(self, rules: Dict[str, Any], mode: str = "shadow") -> None:
        self.mode = str(mode or "shadow")
        self.trade_date = str(rules.get("trade_date") or "")
        self.tasks: List[Dict[str, Any]] = list(rules.get("tasks") or [])
        self._states: Dict[str, _PrefixState] = {}
        self._tasks_by_code: Dict[str, List[Dict[str, Any]]] = {}
        self._best_sell: Dict[str, Dict[str, Any]] = {}
        self._best_buy: Dict[str, Dict[str, Any]] = {}
        self._cage: Dict[str, Dict[str, Any]] = {}
        self._early: Dict[str, Dict[str, Any]] = {}
        # code -> (limit_up, pre_close)����������С���ԣ�tick ȱ�ֶ�ʱ����
        self._limit_cache: Dict[str, Tuple[float, float]] = {}
        self.early_order_enabled = bool(rules.get("early_order_enabled"))
        for t in self.tasks:
            code = str(t.get("stock_code") or "").strip().upper()
            if not code:
                continue
            self._tasks_by_code.setdefault(code, []).append(t)
            if code not in self._states:
                preset = bool(t.get("break_below_trigger_done"))
                preset_above = bool(t.get("break_above_trigger_done"))
                self._states[code] = _PrefixState(
                    break_below_preset=preset, break_above_preset=preset_above
                )

    def stock_codes(self) -> List[str]:
        return sorted(self._tasks_by_code.keys())

    def reload_rules(self, rules: Dict[str, Any]) -> Tuple[bool, bool]:
# docstring removed for QMT gbk loader
# docstring removed for QMT gbk loader
        old_codes = set(self.stock_codes())
        old_sig = self._tasks_signature()

        self.trade_date = str(rules.get("trade_date") or self.trade_date)
        self.tasks = list(rules.get("tasks") or [])
        self.early_order_enabled = bool(rules.get("early_order_enabled"))
        new_by_code: Dict[str, List[Dict[str, Any]]] = {}
        for t in self.tasks:
            code = str(t.get("stock_code") or "").strip().upper()
            if not code:
                continue
            new_by_code.setdefault(code, []).append(t)

        for code, task_list in new_by_code.items():
            preset = any(bool(x.get("break_below_trigger_done")) for x in task_list)
            preset_above = any(bool(x.get("break_above_trigger_done")) for x in task_list)
            st = self._states.get(code)
            if st is None:
                self._states[code] = _PrefixState(
                    break_below_preset=preset, break_above_preset=preset_above
                )
            else:
                if preset:
                    st.break_below_done = True
                if preset_above:
                    st.break_above_done = True

        for code in list(self._states.keys()):
            if code not in new_by_code:
                del self._states[code]

        self._tasks_by_code = new_by_code
        new_codes = set(self.stock_codes())
        new_sig = self._tasks_signature()
        tasks_changed = new_sig != old_sig
        codes_changed = old_codes != new_codes
        # �����Ѳ����ڵĵ�������״̬
        live_ids = {str(t.get("task_id") or "") for t in self.tasks}
        for tid in list(self._best_sell.keys()):
            if tid and tid not in live_ids:
                self._best_sell.pop(tid, None)
        for tid in list(self._best_buy.keys()):
            if tid and tid not in live_ids:
                self._best_buy.pop(tid, None)
        for tid in list(self._cage.keys()):
            if tid and tid not in live_ids:
                self._cage.pop(tid, None)
        for ekey in list(self._early.keys()):
            base = str(ekey).split("@g")[0]
            if base and base not in live_ids:
                self._early.pop(ekey, None)
        # ����װ������� cage_entered
        for t in self.tasks:
            tid = str(t.get("task_id") or "")
            if not tid or str(t.get("rule_type") or "") not in ("cage_buy", "cage_sell"):
                continue
            if bool(t.get("cage_entered")):
                st = self._cage_state(tid)
                st["entered"] = True
                st["kind"] = str(t.get("rule_type") or "")
        return tasks_changed, codes_changed

    def hydrate_elastic_states(self, states: Optional[Dict[str, Any]]) -> None:
        if not isinstance(states, dict):
            return
        for tid, st in states.items():
            if not tid or not isinstance(st, dict):
                continue
            kind = str(st.get("kind") or "").strip()
            if not kind:
                if st.get("lowest_price") is not None and st.get("highest_price") is None:
                    kind = "best_buy"
                else:
                    kind = "best_sell"
            if kind == "best_buy":
                self._best_buy[str(tid)] = {
                    "kind": "best_buy",
                    "triggered": bool(st.get("triggered")),
                    "lowest_price": st.get("lowest_price"),
                    "tick_idx": int(st.get("tick_idx") or 0),
                    "lowest_tick_idx": int(st.get("lowest_tick_idx") or 0),
                    "rebound_hit_count": int(st.get("rebound_hit_count") or 0),
                }
            else:
                self._best_sell[str(tid)] = {
                    "kind": "best_sell",
                    "triggered": bool(st.get("triggered")),
                    "highest_price": st.get("highest_price"),
                    "tick_idx": int(st.get("tick_idx") or 0),
                    "highest_tick_idx": int(st.get("highest_tick_idx") or 0),
                    "pullback_hit_count": int(st.get("pullback_hit_count") or 0),
                }

    def dump_elastic_states(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for tid, st in (self._best_sell or {}).items():
            if not tid or not isinstance(st, dict):
                continue
            row = dict(st)
            row["kind"] = "best_sell"
            out[str(tid)] = row
        for tid, st in (self._best_buy or {}).items():
            if not tid or not isinstance(st, dict):
                continue
            row = dict(st)
            row["kind"] = "best_buy"
            out[str(tid)] = row
        return out

    def dump_done_task_ids(self) -> List[str]:
        ids = set()
        for st in (self._states or {}).values():
            try:
                ids.update(str(x) for x in (st.done_task_ids or set()))
            except Exception:
                pass
        return sorted(ids)

    def hydrate_done_task_ids(self, ids: Optional[List[Any]]) -> None:
        if not ids:
            return
        for tid in ids:
            t = str(tid or "").strip()
            if not t:
                continue
            for st in (self._states or {}).values():
                try:
                    st.done_task_ids.add(t)
                except Exception:
                    pass

    def _best_buy_state(self, tid: str) -> Dict[str, Any]:
        st = self._best_buy.get(tid)
        if st is None:
            st = {
                "kind": "best_buy",
                "triggered": False,
                "lowest_price": None,
                "tick_idx": 0,
                "lowest_tick_idx": 0,
                "rebound_hit_count": 0,
            }
            self._best_buy[tid] = st
        return st

    def _best_sell_state(self, tid: str) -> Dict[str, Any]:
        st = self._best_sell.get(tid)
        if st is None:
            st = {
                "kind": "best_sell",
                "triggered": False,
                "highest_price": None,
                "tick_idx": 0,
                "highest_tick_idx": 0,
                "pullback_hit_count": 0,
            }
            self._best_sell[tid] = st
        return st

    def _cage_state(self, tid: str) -> Dict[str, Any]:
        st = self._cage.get(tid)
        if st is None:
            st = {"kind": "", "entered": False}
            self._cage[tid] = st
        return st

    @staticmethod
    def _cage_inner_bounds(task: Dict[str, Any]):
        price_low = float(task.get("price_low") or 0)
        price_high = float(task.get("price_high") or 0)
        wt = float(task.get("wall_thickness") or 0)
        if wt <= 0:
            return price_low, price_high
        inner_low = price_low + wt
        inner_high = price_high - wt
        if inner_low > inner_high:
            mid = (price_low + price_high) / 2.0
            return mid, mid
        return inner_low, inner_high

    def dump_cage_states(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for tid, st in (self._cage or {}).items():
            if not tid or not isinstance(st, dict):
                continue
            if not bool(st.get("entered")):
                continue
            out[str(tid)] = {
                "kind": str(st.get("kind") or ""),
                "entered": True,
            }
        return out

    def hydrate_cage_states(self, states: Optional[Dict[str, Any]]) -> None:
        if not isinstance(states, dict):
            return
        for tid, st in states.items():
            if not tid or not isinstance(st, dict):
                continue
            if not bool(st.get("entered")):
                continue
            cst = self._cage_state(str(tid))
            cst["entered"] = True
            if st.get("kind"):
                cst["kind"] = str(st.get("kind") or "")

    def dump_early_states(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for ekey, st in (self._early or {}).items():
            if not ekey or not isinstance(st, dict):
                continue
            if not bool(st.get("active")):
                continue
            out[str(ekey)] = {
                "active": True,
                "task_id": str(st.get("task_id") or ""),
                "kind": str(st.get("kind") or ""),
                "price": float(st.get("price") or 0),
                "volume": int(st.get("volume") or 0),
                "grid_index": st.get("grid_index"),
                "user_order_id": str(st.get("user_order_id") or ""),
            }
        return out

    def hydrate_early_states(self, states: Optional[Dict[str, Any]]) -> None:
        if not isinstance(states, dict):
            return
        for ekey, st in states.items():
            if not ekey or not isinstance(st, dict):
                continue
            if not bool(st.get("active")):
                continue
            self._early[str(ekey)] = {
                "active": True,
                "task_id": str(st.get("task_id") or ""),
                "kind": str(st.get("kind") or ""),
                "price": float(st.get("price") or 0),
                "volume": int(st.get("volume") or 0),
                "grid_index": st.get("grid_index"),
                "user_order_id": str(st.get("user_order_id") or ""),
            }

    def clear_early_state(self, ekey: str) -> None:
        self._early.pop(str(ekey or ""), None)

    @staticmethod
    def _early_key(tid: str, grid_index: Optional[int] = None) -> str:
        if grid_index is not None:
            try:
                return "%s@g%d" % (tid, int(grid_index))
            except (TypeError, ValueError):
                pass
        return str(tid)

    def _early_eval(
        self,
        *,
        code: str,
        tid: str,
        rule_type: str,
        lp: float,
        target: float,
        vol: int,
        tick_time: str,
        grid_index: Optional[int] = None,
        early_enabled: Optional[bool] = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """��ǰ�ҵ� FSM������ (events, skip_normal_hit)��"""
        events: List[Dict[str, Any]] = []
        use_early = (
            self.early_order_enabled if early_enabled is None else bool(early_enabled)
        )
        if not tid or target <= 0 or vol <= 0 or lp <= 0:
            return events, False
        ekey = self._early_key(tid, grid_index)
        est = self._early.get(ekey) or {}
        active = bool(est.get("active"))
        # δ����ǰ���޹ҵ������������ѹң�active���Դ�����/ȷ��
        if not use_early and not active:
            return events, False
        is_buy = rule_type in ("single_buy", "grid_buy")
        diff_pct = abs((lp - target) / target) * 100.0
        direction_ok = (target < lp) if is_buy else (target > lp)

        if active:
            # �۲���󣺳���
            if diff_pct > 1.0:
                events.append(
                    self._event(
                        code,
                        "early_cancel",
                        tick_time,
                        target,
                        msg="\u63d0\u524d\u4e0b\u5355\u64a4\u5355",
                        task_id=tid,
                        last_price=lp,
                        max_volume=vol,
                        grid_index=grid_index,
                        detail="early_key=%s diff=%.3f%%" % (ekey, diff_pct),
                    )
                )
                self.clear_early_state(ekey)
                return events, True
            # �۸񵽴ȷ�ϣ����ٷ�׷�۵���
            reached = (lp <= target) if is_buy else (lp >= target)
            if reached:
                events.append(
                    self._event(
                        code,
                        "early_confirm",
                        tick_time,
                        target,
                        msg="\u63d0\u524d\u4e0b\u5355\u786e\u8ba4",
                        task_id=tid,
                        last_price=lp,
                        max_volume=vol,
                        grid_index=grid_index,
                        detail="early_key=%s" % ekey,
                    )
                )
                self.clear_early_state(ekey)
                return events, True
            # �ҵ��У��������津��
            return events, True

        # δ�ҵ��������ҷ�����ȷ����ǰ�ң�����ǰ����������ǰʱ��
        if use_early and diff_pct < 0.5 and direction_ok:
            uid = tid.replace(":", "_")
            if grid_index is not None:
                try:
                    uid = "%s_early_g%d" % (uid, int(grid_index))
                except (TypeError, ValueError):
                    uid = "%s_early" % uid
            else:
                uid = "%s_early" % uid
            self._early[ekey] = {
                "active": True,
                "task_id": tid,
                "kind": rule_type,
                "price": float(target),
                "volume": int(vol),
                "grid_index": grid_index,
                "user_order_id": uid,
            }
            events.append(
                self._event(
                    code,
                    "early_place",
                    tick_time,
                    target,
                    msg="\u63d0\u524d\u4e0b\u5355",
                    task_id=tid,
                    last_price=lp,
                    max_volume=vol,
                    grid_index=grid_index,
                    detail="early_key=%s uid=%s diff=%.3f%%" % (ekey, uid, diff_pct),
                )
            )
            return events, True
        return events, False

    def _tasks_signature(self) -> str:
        parts: List[str] = []
        for t in sorted(self.tasks, key=lambda x: str(x.get("task_id") or "")):
            parts.append(
                "|".join(
                    [
                        str(t.get("task_id") or ""),
                        str(t.get("stock_code") or ""),
                        str(t.get("rule_type") or ""),
                        str(t.get("trigger_price") or ""),
                        str(t.get("price_low") or ""),
                        str(t.get("price_high") or ""),
                        str(t.get("wall_thickness") or ""),
                        "1" if t.get("cage_entered") else "0",
                        str(t.get("start_price") or ""),
                        str(t.get("end_price") or ""),
                        str(t.get("num_grids") or ""),
                        str(t.get("volume_per_grid") or ""),
                        ",".join(str(x) for x in (t.get("executed_grids") or [])),
                        "1" if t.get("enabled") else "0",
                        "1" if t.get("require_break_below") else "0",
                        "1" if t.get("break_below_trigger_done") else "0",
                        "1" if t.get("require_break_above") else "0",
                        "1" if t.get("break_above_trigger_done") else "0",
                        str(t.get("true_breakthrough_cond1_mode") or ""),
                        str(t.get("max_volume") or ""),
                        "1" if t.get("early_order_enabled") else "0",
                        "1" if t.get("require_true_breakthrough") else "0",
                        "1" if t.get("require_break_below") else "0",
                    ]
                )
            )
        return ";".join(parts)

    def on_quote_dict(self, quote: Any) -> List[Dict[str, Any]]:
        if not isinstance(quote, dict):
            return []
        events: List[Dict[str, Any]] = []
        for stock_code, stock_data in quote.items():
            code = str(stock_code or "").strip().upper()
            if code not in self._tasks_by_code:
                continue
            row = _light_row(stock_data)
            if not row:
                continue
            events.extend(self._process_row(code, row))
        return events

    def on_row(self, stock_code: str, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        code = str(stock_code or "").strip().upper()
        if code not in self._tasks_by_code:
            return []
        return self._process_row(code, _light_row(row))

    def snapshot_results(self) -> Dict[str, Any]:
        try:
            from ant_rules_io import empty_results
        except ImportError:
            from qmt_builtin.ant_rules_io import empty_results

        out = empty_results(mode=self.mode, trade_date=self.trade_date)
        for code, st in self._states.items():
            out["stocks"][code] = {
                "last_price": float(st.prev_last_price or 0),
                "break_below_done": st.break_below_done,
                "done": bool(st.done_task_ids),
                "done_task_ids": sorted(st.done_task_ids),
                "events": [],
            }
        return out

    def _ensure_vol_mul(self, code: str, st: _PrefixState, row: Dict[str, Any]) -> None:
        if st.vol_mul_ready:
            return
        st.tick_buf.append(dict(row))
        if len(st.tick_buf) < 8:
            return
        try:
            import pandas as pd

            df = pd.DataFrame(st.tick_buf)
            st.vol_mul = float(infer_tick_vol_to_shares_multiplier(df))
        except Exception:
            vols = per_tick_trade_volumes_list(st.tick_buf, 100.0)
            st.vol_mul = 100.0 if any(v and v > 0 for v in vols) else 100.0
        st.vol_mul_ready = True

    def _advance_prefix(
        self,
        code: str,
        st: _PrefixState,
        row: Dict[str, Any],
        v_break: Optional[float],
    ) -> None:
        st.prev_row = dict(row)
        st.recent_rows = (st.recent_rows + [dict(row)])[-5:]
        if v_break is not None:
            keep = max(5, TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS)
            st.recent_vols = (st.recent_vols + [float(v_break)])[-keep:]
        if v_break is not None and float(v_break) > 0:
            st.prefix_sum += float(v_break)
            st.prefix_cnt += 1

    def _single_tick_volume(
        self, st: _PrefixState, row: Dict[str, Any]
    ) -> Optional[float]:
        vols = per_tick_trade_volumes_list([row], st.vol_mul)
        if not vols:
            return None
        v = vols[0]
        return float(v) if v is not None else None

    @staticmethod
    def _grid_point_price(rule_type: str, start_price: float, end_price: float, num_grids: int, index: int) -> float:
        """��ͼ��һ�µ������۸�"""
        n = int(num_grids or 0)
        i = int(index or 0)
        if n < 1:
            return float(start_price or 0)
        if i <= 0:
            return float(start_price or 0)
        if i >= n:
            return float(end_price or 0)
        # �м�����Բ�ֵ�������ɵ��÷��� round
        try:
            px = float(start_price) + (float(end_price) - float(start_price)) * float(i) / float(n)
        except Exception:
            px = float(start_price or 0)
        return px

    def _process_row(self, code: str, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        st = self._states[code]
        tasks = self._tasks_by_code.get(code, [])
        if not tasks:
            return events
        active = [
            t
            for t in tasks
            if t.get("enabled", True)
            and str(t.get("task_id") or "") not in st.done_task_ids
        ]
        if not active:
            return events

        self._ensure_vol_mul(code, st, row)
        lp = float(row.get("lastPrice") or row.get("last_price") or 0)
        if lp <= 0:
            return events

        v_break = self._single_tick_volume(st, row)
        tick_time = self._format_tick_time(row.get("time"))

        for task in active:
            tid = str(task.get("task_id") or "")
            rule_type = str(task.get("rule_type") or "breakthrough_buy").strip()

            # ��ʱ��֣�������ױ���Ч tick һ�����ж����ּ� < ����������������������
            if rule_type == "scheduled_clear":
                if not tid:
                    continue
                trigger = float(task.get("trigger_price") or 0)
                vol = int(task.get("max_volume") or 0)
                if trigger <= 0 or vol <= 0:
                    continue
                from datetime import datetime as _dt
                from datetime import time as _dt_time

                now = _dt.now()
                eff = str(task.get("scheduled_clear_effective_date") or "").strip()
                if eff and eff != now.strftime("%Y-%m-%d"):
                    continue
                time_str = str(task.get("scheduled_clear_time") or "14:56:00").strip()
                try:
                    parts = [int(x) for x in time_str.split(":")]
                    while len(parts) < 3:
                        parts.append(0)
                    rule_time = _dt_time(parts[0], parts[1], parts[2])
                except Exception:
                    rule_time = _dt_time(14, 56, 0)
                cur = now.time()
                if cur < rule_time:
                    continue
                elapsed = (
                    cur.hour * 3600
                    + cur.minute * 60
                    + cur.second
                    - (rule_time.hour * 3600 + rule_time.minute * 60 + rule_time.second)
                )
                if elapsed > 300:
                    events.append(
                        self._event(
                            code,
                            "scheduled_clear_skip",
                            tick_time,
                            trigger,
                            msg="\u5b9a\u65f6\u6e05\u4ed3\u8d85\u65f6",
                            task_id=tid,
                            last_price=lp,
                            max_volume=vol,
                            detail="overdue elapsed=%ds" % int(elapsed),
                        )
                    )
                    st.done_task_ids.add(tid)
                    continue
                if lp < trigger:
                    events.append(
                        self._event(
                            code,
                            "scheduled_clear_hit",
                            tick_time,
                            trigger,
                            msg="\u5b9a\u65f6\u6e05\u4ed3\u89e6\u53d1",
                            task_id=tid,
                            last_price=lp,
                            max_volume=vol,
                            detail="lp=%.4f < trig=%.4f" % (lp, trigger),
                        )
                    )
                else:
                    events.append(
                        self._event(
                            code,
                            "scheduled_clear_skip",
                            tick_time,
                            trigger,
                            msg="\u5b9a\u65f6\u6e05\u4ed3\u4ef7\u4e0d\u6ee1",
                            task_id=tid,
                            last_price=lp,
                            max_volume=vol,
                            detail="price_not_met lp=%.4f >= trig=%.4f" % (lp, trigger),
                        )
                    )
                st.done_task_ids.add(tid)
                continue

            # ����ÿ tick ��ഥ��һ��δִ�е�λ����ͼ��һ�£�
            if rule_type in ("grid_buy", "grid_sell"):
                if not tid:
                    continue
                start_price = float(task.get("start_price") or 0)
                end_price = float(task.get("end_price") or 0)
                num_grids = int(task.get("num_grids") or 2)
                vol = int(task.get("volume_per_grid") or task.get("max_volume") or 0)
                if start_price <= 0 or end_price <= 0 or num_grids < 1 or vol <= 0:
                    continue
                executed = set()
                for x in task.get("executed_grids") or []:
                    try:
                        executed.add(int(x))
                    except (TypeError, ValueError):
                        pass
                # �ڴ����Ѵ����ĵ�λҲ�ų�
                for i in range(num_grids + 1):
                    if ("%s@g%d" % (tid, i)) in st.done_task_ids:
                        executed.add(i)
                for i in range(num_grids + 1):
                    if i in executed:
                        continue
                    gp = self._grid_point_price(rule_type, start_price, end_price, num_grids, i)
                    try:
                        gp = round_price_like_display(_code6(code), gp)
                    except Exception:
                        gp = float(gp)
                    early_ev, skip_hit = self._early_eval(
                        code=code,
                        tid=tid,
                        rule_type=rule_type,
                        lp=lp,
                        target=float(gp),
                        vol=vol,
                        tick_time=tick_time,
                        grid_index=i,
                        early_enabled=task.get(
                            "early_order_enabled", self.early_order_enabled
                        ),
                    )
                    events.extend(early_ev)
                    if skip_hit:
                        if any(e.get("type") == "early_confirm" for e in early_ev):
                            st.done_task_ids.add("%s@g%d" % (tid, i))
                            if len(executed) + 1 >= num_grids + 1:
                                st.done_task_ids.add(tid)
                        break
                    hit = (lp <= gp) if rule_type == "grid_buy" else (lp >= gp)
                    if not hit:
                        continue
                    ev_type = "grid_buy_hit" if rule_type == "grid_buy" else "grid_sell_hit"
                    msg = (
                        "\u7f51\u683c\u4e70\u5165\u89e6\u53d1"
                        if rule_type == "grid_buy"
                        else "\u7f51\u683c\u5356\u51fa\u89e6\u53d1"
                    )
                    events.append(
                        self._event(
                            code,
                            ev_type,
                            tick_time,
                            gp,
                            msg=msg,
                            task_id=tid,
                            last_price=lp,
                            max_volume=vol,
                            grid_index=i,
                            detail="grid_index=%d px=%.4f" % (i, gp),
                        )
                    )
                    st.done_task_ids.add("%s@g%d" % (tid, i))
                    if len(executed) + 1 >= num_grids + 1:
                        st.done_task_ids.add(tid)
                    break
                continue

            # ���ӣ��Ƚ������䣬���ƶ˵㴥������ͼ��һ�£�
            if rule_type in ("cage_buy", "cage_sell"):
                if not tid:
                    continue
                price_low = float(task.get("price_low") or 0)
                price_high = float(task.get("price_high") or 0)
                if price_low <= 0 or price_high <= price_low:
                    continue
                vol = int(task.get("max_volume") or 0)
                if vol <= 0:
                    continue
                inner_low, inner_high = self._cage_inner_bounds(task)
                cst = self._cage_state(tid)
                cst["kind"] = rule_type
                entered = bool(cst.get("entered")) or bool(task.get("cage_entered"))

                # ���̼����������ڣ���Ϊ�ѽ��루��ز�һ�£�
                if not entered:
                    open_px = float(
                        row.get("open")
                        or row.get("openPrice")
                        or row.get("open_price")
                        or row.get("todayOpen")
                        or 0
                    )
                    if open_px > 0 and inner_low < open_px < inner_high:
                        entered = True
                        cst["entered"] = True

                if inner_low < lp < inner_high:
                    if not entered:
                        cst["entered"] = True
                        events.append(
                            self._event(
                                code,
                                "cage_entered",
                                tick_time,
                                inner_low,
                                msg="\u7b3c\u5b50\uff1a\u8fdb\u5165\u5185\u533a\u95f4",
                                task_id=tid,
                                last_price=lp,
                                detail="low=%.4f high=%.4f" % (inner_low, inner_high),
                            )
                        )
                    continue

                if not (entered or bool(cst.get("entered"))):
                    continue
                cst["entered"] = True

                if rule_type == "cage_buy":
                    if lp <= inner_low or lp >= price_high:
                        endpoint = "low" if lp <= inner_low else "high"
                        endpoint_px = inner_low if endpoint == "low" else price_high
                        events.append(
                            self._event(
                                code,
                                "cage_buy_hit",
                                tick_time,
                                endpoint_px,
                                msg="\u7b3c\u5b50\u4e70\u5165\u89e6\u53d1",
                                task_id=tid,
                                last_price=lp,
                                max_volume=vol,
                                executed_endpoint=endpoint,
                                detail="endpoint=%s px=%.4f" % (endpoint, endpoint_px),
                            )
                        )
                        st.done_task_ids.add(tid)
                        self._cage.pop(tid, None)
                else:
                    if lp <= price_low or lp >= inner_high:
                        endpoint = "low" if lp <= price_low else "high"
                        endpoint_px = price_low if endpoint == "low" else inner_high
                        events.append(
                            self._event(
                                code,
                                "cage_sell_hit",
                                tick_time,
                                endpoint_px,
                                msg="\u7b3c\u5b50\u5356\u51fa\u89e6\u53d1",
                                task_id=tid,
                                last_price=lp,
                                max_volume=vol,
                                executed_endpoint=endpoint,
                                detail="endpoint=%s px=%.4f" % (endpoint, endpoint_px),
                            )
                        )
                        st.done_task_ids.add(tid)
                        self._cage.pop(tid, None)
                continue

            trig = float(task.get("trigger_price") or 0)
            if trig <= 0:
                continue

            # �������룺�ּ� <= �����ۣ���������ͼ��һ�£�
            # ����������չ��wait_unseal=��ͣ���̵ȿ���󴥷�
            if rule_type == "single_buy":
                vol = int(task.get("max_volume") or 0)
                wait_unseal = bool(task.get("wait_unseal"))
                if wait_unseal:
                    try:
                        from core.rule_activation import (
                            first_ask_volume,
                            is_limit_up_sealed,
                        )
                    except Exception:
                        first_ask_volume = None  # type: ignore
                        is_limit_up_sealed = None  # type: ignore
                    lu = float(task.get("limit_up") or trig or 0)
                    if first_ask_volume is not None:
                        ask_vol = int(first_ask_volume(row) or 0)
                    else:
                        raw_av = row.get("askVol") or row.get("askVolume")
                        if isinstance(raw_av, (list, tuple)) and raw_av:
                            try:
                                ask_vol = int(raw_av[0] or 0)
                            except (TypeError, ValueError):
                                ask_vol = 0
                        else:
                            try:
                                ask_vol = int(raw_av or 0)
                            except (TypeError, ValueError):
                                ask_vol = 0
                    if is_limit_up_sealed is not None:
                        sealed = bool(is_limit_up_sealed(lp, lu, ask_vol))
                    else:
                        sealed = lu > 0 and lp + 1e-9 >= lu - 0.011 and ask_vol <= 0
                    if sealed:
                        continue
                    order_px = lu if lu > 0 else trig
                    if order_px <= 0 or lp > order_px + 1e-9:
                        continue
                    events.append(
                        self._event(
                            code,
                            "single_buy_hit",
                            tick_time,
                            order_px,
                            msg="��������-��ͣ�ȿ���",
                            task_id=tid,
                            last_price=lp,
                            max_volume=vol,
                        )
                    )
                    if tid:
                        st.done_task_ids.add(tid)
                    continue

                early_ev, skip_hit = self._early_eval(
                    code=code,
                    tid=tid,
                    rule_type=rule_type,
                    lp=lp,
                    target=trig,
                    vol=vol,
                    tick_time=tick_time,
                    early_enabled=task.get(
                        "early_order_enabled", self.early_order_enabled
                    ),
                )
                events.extend(early_ev)
                if skip_hit:
                    if any(e.get("type") == "early_confirm" for e in early_ev) and tid:
                        st.done_task_ids.add(tid)
                    continue
                if lp <= trig:
                    msg = (
                        "��������-��һ"
                        if task.get("open_buy_ask")
                        else "\u5355\u70b9\u4e70\u5165\u89e6\u53d1"
                    )
                    events.append(
                        self._event(
                            code,
                            "single_buy_hit",
                            tick_time,
                            trig,
                            msg=msg,
                            task_id=tid,
                            last_price=lp,
                            max_volume=vol,
                        )
                    )
                    if tid:
                        st.done_task_ids.add(tid)
                continue

            # �����������ּ� >= �����ۣ���������ͼ��һ�£�
            if rule_type == "single_sell":
                vol = int(task.get("max_volume") or 0)
                early_ev, skip_hit = self._early_eval(
                    code=code,
                    tid=tid,
                    rule_type=rule_type,
                    lp=lp,
                    target=trig,
                    vol=vol,
                    tick_time=tick_time,
                    early_enabled=task.get(
                        "early_order_enabled", self.early_order_enabled
                    ),
                )
                events.extend(early_ev)
                if skip_hit:
                    if any(e.get("type") == "early_confirm" for e in early_ev) and tid:
                        st.done_task_ids.add(tid)
                    continue
                if lp >= trig:
                    events.append(
                        self._event(
                            code,
                            "single_sell_hit",
                            tick_time,
                            trig,
                            msg="\u5355\u70b9\u5356\u51fa\u89e6\u53d1",
                            task_id=tid,
                            last_price=lp,
                            max_volume=vol,
                        )
                    )
                    if tid:
                        st.done_task_ids.add(tid)
                continue

            # ͻ����������ͼ��һ�� ���� �ּ� < �����ۼ�������Ҫ�������ƣ�
            if rule_type == "breakthrough_sell":
                r_lp = round_price_like_display(_code6(code), lp)
                r_trig = round_price_like_display(_code6(code), trig)
                if r_lp < r_trig:
                    events.append(
                        self._event(
                            code,
                            "breakthrough_sell_hit",
                            tick_time,
                            trig,
                            msg="\u7a81\u7834\u5356\u51fa\u89e6\u53d1",
                            task_id=tid,
                            last_price=lp,
                            max_volume=int(task.get("max_volume") or 0),
                        )
                    )
                    if tid:
                        st.done_task_ids.add(tid)
                continue

            # �������룺���ƴ����� �� ������ͼ� �� ����ȷ������
            if rule_type == "best_buy":
                if compute_best_buy_rebound_from_rule is None:
                    continue
                if not tid:
                    continue
                if load_elastic_confirm_triple is None:
                    continue
                bbt = self._best_buy_state(tid)
                bbt["tick_idx"] = int(bbt.get("tick_idx") or 0) + 1
                try:
                    cfg_confirm, cfg_cooldown, cfg_dyn = load_elastic_confirm_triple()
                except Exception:
                    cfg_confirm, cfg_cooldown, cfg_dyn = 4, 2, 1
                _r_confirm = task.get("confirm_ticks")
                _r_cool = task.get("cooldown_after_extreme_ticks")
                confirm_ticks = int(cfg_confirm) if _r_confirm is None else int(_r_confirm)
                cooldown_ticks = int(cfg_cooldown) if _r_cool is None else int(_r_cool)
                if confirm_ticks < 0:
                    confirm_ticks = 2
                if confirm_ticks == 0:
                    confirm_ticks = 1
                if cooldown_ticks < 0:
                    cooldown_ticks = 0
                _r_dyn = task.get("dynamic_thresholds")
                if _r_dyn is not None:
                    try:
                        cfg_dyn = int(_r_dyn)
                    except (TypeError, ValueError):
                        pass

                triggered = bool(bbt.get("triggered"))
                lowest_price = bbt.get("lowest_price")
                try:
                    lowest_price = float(lowest_price) if lowest_price is not None else None
                except (TypeError, ValueError):
                    lowest_price = None

                # ͼ�����ϸ� < �����ۿ�ʼ׷��
                if lp < trig:
                    if not triggered:
                        bbt["triggered"] = True
                        bbt["lowest_price"] = lp
                        bbt["lowest_tick_idx"] = int(bbt["tick_idx"])
                        bbt["rebound_hit_count"] = 0
                        events.append(
                            self._event(
                                code,
                                "best_buy_arm",
                                tick_time,
                                trig,
                                msg="\u5f39\u6027\u4e70\u5165\uff1a\u5f00\u59cb\u8ffd\u8e2a\u6700\u4f4e\u4ef7",
                                task_id=tid,
                                last_price=lp,
                            )
                        )
                        continue
                    if lowest_price is None or lp < lowest_price:
                        bbt["lowest_price"] = lp
                        bbt["lowest_tick_idx"] = int(bbt["tick_idx"])
                        bbt["rebound_hit_count"] = 0
                        continue

                if triggered and lowest_price is not None and lowest_price > 0:
                    rule_like = {
                        "trigger_price": trig,
                        "rise_percent": float(task.get("rise_percent") or 0.3),
                        "rise_scale": task.get("rise_scale"),
                        "max_rise_percent": task.get("max_rise_percent"),
                        "dynamic_thresholds": cfg_dyn,
                    }
                    _eff, target_price = compute_best_buy_rebound_from_rule(
                        float(lowest_price),
                        rule_like,
                        cfg_dyn=int(cfg_dyn),
                    )
                    lowest_idx = int(bbt.get("lowest_tick_idx") or 0)
                    if lowest_idx > 0 and (int(bbt["tick_idx"]) - lowest_idx) <= cooldown_ticks:
                        continue
                    hit = lp >= float(target_price) and lp > float(lowest_price)
                    if hit:
                        cnt = int(bbt.get("rebound_hit_count") or 0) + 1
                        bbt["rebound_hit_count"] = cnt
                        if cnt < confirm_ticks:
                            continue
                        events.append(
                            self._event(
                                code,
                                "best_buy_hit",
                                tick_time,
                                trig,
                                msg="\u5f39\u6027\u4e70\u5165\u89e6\u53d1",
                                task_id=tid,
                                last_price=lp,
                                max_volume=int(task.get("max_volume") or 0),
                                detail="low=%.4f target=%.4f" % (lowest_price, target_price),
                            )
                        )
                        if tid:
                            st.done_task_ids.add(tid)
                        self._best_buy.pop(tid, None)
                    else:
                        if bbt.get("rebound_hit_count"):
                            bbt["rebound_hit_count"] = 0
                continue

            # �������������ƴ����� �� ������߼� �� ����ȷ������
            if rule_type == "best_sell":
                if compute_best_sell_fallback_from_rule is None:
                    continue
                if not tid:
                    continue
                bst = self._best_sell_state(tid)
                bst["tick_idx"] = int(bst.get("tick_idx") or 0) + 1
                try:
                    cfg_confirm, cfg_cooldown, _cfg_dyn = load_elastic_confirm_triple()
                except Exception:
                    cfg_confirm, cfg_cooldown = 4, 2
                _r_confirm = task.get("confirm_ticks")
                _r_cool = task.get("cooldown_after_extreme_ticks")
                confirm_ticks = int(cfg_confirm) if _r_confirm is None else int(_r_confirm)
                cooldown_ticks = int(cfg_cooldown) if _r_cool is None else int(_r_cool)
                if confirm_ticks < 0:
                    confirm_ticks = 2
                if confirm_ticks == 0:
                    confirm_ticks = 1
                if cooldown_ticks < 0:
                    cooldown_ticks = 0

                triggered = bool(bst.get("triggered"))
                highest_price = bst.get("highest_price")
                try:
                    highest_price = float(highest_price) if highest_price is not None else None
                except (TypeError, ValueError):
                    highest_price = None

                if lp > trig:
                    if not triggered:
                        bst["triggered"] = True
                        bst["highest_price"] = lp
                        bst["highest_tick_idx"] = int(bst["tick_idx"])
                        bst["pullback_hit_count"] = 0
                        events.append(
                            self._event(
                                code,
                                "best_sell_arm",
                                tick_time,
                                trig,
                                msg="\u5f39\u6027\u5356\u51fa\uff1a\u5f00\u59cb\u8ffd\u8e2a\u6700\u9ad8\u4ef7",
                                task_id=tid,
                                last_price=lp,
                            )
                        )
                        continue
                    if highest_price is None or lp > highest_price:
                        bst["highest_price"] = lp
                        bst["highest_tick_idx"] = int(bst["tick_idx"])
                        bst["pullback_hit_count"] = 0
                        continue

                if triggered and highest_price is not None and highest_price > 0:
                    meta = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                    stock_name = str(
                        task.get("stock_name")
                        or meta.get("stock_name")
                        or meta.get("rule_name")
                        or ""
                    )
                    if resolve_limit_up_pre_close is not None:
                        limit_up, pre_close = resolve_limit_up_pre_close(
                            code,
                            row,
                            stock_name=stock_name,
                            cache=self._limit_cache,
                        )
                    else:
                        limit_up = float(
                            row.get("highLimit")
                            or row.get("upperLimit")
                            or row.get("limitUp")
                            or 0
                        )
                        pre_close = float(
                            row.get("lastClose")
                            or row.get("preClose")
                            or row.get("pre_close")
                            or 0
                        )
                    rule_like = {
                        "trigger_price": trig,
                        "drop_percent": float(task.get("drop_percent") or 2.5),
                        "room_blend_start": task.get("room_blend_start"),
                        "pullback_price": task.get("pullback_price"),
                        "dynamic_thresholds": task.get("dynamic_thresholds"),
                    }
                    eff_drop, target_price = compute_best_sell_fallback_from_rule(
                        float(highest_price),
                        rule_like,
                        limit_up=limit_up,
                        pre_close=pre_close,
                    )
                    highest_idx = int(bst.get("highest_tick_idx") or 0)
                    if highest_idx > 0 and (int(bst["tick_idx"]) - highest_idx) <= cooldown_ticks:
                        continue
                    hit = lp <= float(target_price) and lp < float(highest_price)
                    if hit:
                        cnt = int(bst.get("pullback_hit_count") or 0) + 1
                        bst["pullback_hit_count"] = cnt
                        if cnt < confirm_ticks:
                            continue
                        events.append(
                            self._event(
                                code,
                                "best_sell_hit",
                                tick_time,
                                trig,
                                msg="\u5f39\u6027\u5356\u51fa\u89e6\u53d1",
                                task_id=tid,
                                last_price=lp,
                                max_volume=int(task.get("max_volume") or 0),
                                detail=(
                                    "high=%.4f target=%.4f eff_drop=%.3f%% lu=%.2f pc=%.2f"
                                    % (
                                        highest_price,
                                        target_price,
                                        float(eff_drop or 0),
                                        float(limit_up or 0),
                                        float(pre_close or 0),
                                    )
                                ),
                            )
                        )
                        if tid:
                            st.done_task_ids.add(tid)
                        # ���к���״̬�������ظ�
                        self._best_sell.pop(tid, None)
                    else:
                        if bst.get("pullback_hit_count"):
                            bst["pullback_hit_count"] = 0
                continue

            # ͻ�����루Ĭ�ϣ������� �� �ϴ� �� ��ͻ��
            if rule_type not in ("breakthrough_buy", ""):
                continue

            # �۸��Ӳpass����ش�����ͻ�ƣ���λ������ο���>MA5���ϣ�������ͨͻ��·����
            try:
                band_lo = float(task.get("band_low") or 0)
                band_hi = float(task.get("band_high") or 0)
            except (TypeError, ValueError):
                band_lo, band_hi = 0.0, 0.0
            if band_lo > 0 and band_hi >= band_lo:
                if not (band_lo <= float(lp) <= band_hi):
                    continue
                cond1_mode = normalize_true_breakthrough_cond1_mode(
                    task.get("true_breakthrough_cond1_mode") or "window"
                )
                lookback_prior = None
                try:
                    if task.get("true_breakthrough_window_sec") is not None:
                        lookback_prior = window_prior_ticks_from_seconds(
                            task.get("true_breakthrough_window_sec")
                        )
                except Exception:
                    lookback_prior = None
                avg_before = (
                    (st.prefix_sum / st.prefix_cnt) if st.prefix_cnt > 0 else None
                )
                ok, msg, detail, metrics = evaluate_true_breakthrough_tick_with_detail(
                    code,
                    row,
                    st.prev_row,
                    st.vol_mul,
                    avg_before,
                    v_break,
                    (st.recent_rows + [row])[-5:],
                    recent_vols=st.recent_vols,
                    cond1_mode=cond1_mode,
                    lookback_prior=lookback_prior,
                )
                if not ok:
                    # ����δ����ͻ�ƣ�������
                    continue
                try:
                    accept_lo = task.get("band_accept_low")
                    if accept_lo is None or str(accept_lo).strip() == "":
                        accept_lo = task.get("accept_band_low")
                    accept_lo = float(accept_lo) if accept_lo is not None and str(accept_lo).strip() != "" else None
                except (TypeError, ValueError):
                    accept_lo = None
                # ����ο��ۣ���һ(+1��)����ز�ɽ�Ԥ��һ�£�����Ӳ����=band_high(MA5)
                buy_ref = float(lp)
                try:
                    ask_raw = row.get("askPrice") if isinstance(row, dict) else None
                    if isinstance(ask_raw, (list, tuple)) and ask_raw:
                        ask0 = float(ask_raw[0] or 0)
                    else:
                        ask0 = float(ask_raw or 0) if ask_raw is not None else 0.0
                    if ask0 > 0:
                        code6 = _code6(code)
                        slip = 0.001 if str(code6).startswith("688") else 0.01
                        prec = 3 if str(code6).startswith("688") else 2
                        buy_ref = round(ask0 + slip, prec)
                except (TypeError, ValueError):
                    buy_ref = float(lp)
                hp_detail = None
                if accept_lo is not None and float(lp) + 1e-12 < float(accept_lo):
                    hp_detail = (
                        f"�״���ͻ�Ʒ���: �ּ�={float(lp):.2f}<��Ч����={float(accept_lo):.2f}"
                        f"����ش�[{band_lo:.2f},{band_hi:.2f}]��; {detail}"
                    )
                elif band_hi > 0 and float(buy_ref) > float(band_hi) + 1e-12:
                    hp_detail = (
                        f"�״���ͻ�Ʒ���: ����ο���={float(buy_ref):.2f}>Ӳ����MA5={float(band_hi):.2f}"
                        f"���ּ�={float(lp):.2f}����ش�[{band_lo:.2f},{band_hi:.2f}]��; {detail}"
                    )
                if hp_detail:
                    events.append(
                        self._event(
                            code,
                            "tb_fail",
                            tick_time,
                            trig,
                            msg="band_hard_pass",
                            detail=hp_detail,
                            metrics=metrics,
                            task_id=tid,
                            last_price=lp,
                            max_volume=int(task.get("max_volume") or task.get("volume") or 0),
                        )
                    )
                    if tid:
                        st.done_task_ids.add(tid)
                    continue
                events.append(
                    self._event(
                        code,
                        "tb_pass",
                        tick_time,
                        trig,
                        msg=msg,
                        detail=(
                            f"�۸����������: �ּ�={float(lp):.2f} "
                            f"��=[{band_lo:.2f},{band_hi:.2f}]; {detail}"
                        ),
                        metrics=metrics,
                        task_id=tid,
                        last_price=lp,
                        max_volume=int(task.get("max_volume") or task.get("volume") or 0),
                    )
                )
                if tid:
                    st.done_task_ids.add(tid)
                continue

            if bool(task.get("require_break_below")) and not st.break_below_done:
                if bool(task.get("break_below_trigger_done")):
                    st.break_below_done = True
                elif is_breakthrough_break_below_trigger_tick(
                    code, lp, trig, st.prev_last_price
                ):
                    st.break_below_done = True
                    events.append(
                        self._event(
                            code,
                            "break_below",
                            tick_time,
                            trig,
                            msg="\u8dcc\u7834\u89e6\u53d1\u4ef7",
                            task_id=tid,
                        )
                    )

            crossed = is_breakthrough_buy_price_cross_tick(
                code, lp, trig, st.prev_last_price
            )
            if crossed:
                if bool(task.get("require_break_below")) and not st.break_below_done:
                    events.append(
                        self._event(
                            code,
                            "cross_skip",
                            tick_time,
                            trig,
                            msg="\u4e0a\u7a7f\u4f46\u672a\u5148\u8dcc\u7834",
                            task_id=tid,
                        )
                    )
                else:
                    # δҪ����ͻ�ƣ��ϴ�������ͼ�� require_tb=False һ�£�
                    require_tb = True
                    if "require_true_breakthrough" in task:
                        require_tb = bool(task.get("require_true_breakthrough"))
                    if not require_tb:
                        events.append(
                            self._event(
                                code,
                                "tb_pass",
                                tick_time,
                                trig,
                                msg="\u7a81\u7834\u4e70\u5165",
                                detail="no_true_breakthrough_required",
                                task_id=tid,
                                last_price=lp,
                                max_volume=int(task.get("max_volume") or 0),
                            )
                        )
                        if tid:
                            st.done_task_ids.add(tid)
                    else:
                        cond1_mode = normalize_true_breakthrough_cond1_mode(
                            task.get("true_breakthrough_cond1_mode")
                        )
                        avg_before = (
                            (st.prefix_sum / st.prefix_cnt) if st.prefix_cnt > 0 else None
                        )
                        ok, msg, detail, metrics = evaluate_true_breakthrough_tick_with_detail(
                            code,
                            row,
                            st.prev_row,
                            st.vol_mul,
                            avg_before,
                            v_break,
                            (st.recent_rows + [row])[-5:],
                            recent_vols=st.recent_vols,
                            cond1_mode=cond1_mode,
                        )
                        events.append(
                            self._event(
                                code,
                                "tb_pass" if ok else "tb_fail",
                                tick_time,
                                trig,
                                msg=msg,
                                detail=detail,
                                metrics=metrics,
                                task_id=tid,
                                last_price=lp,
                                max_volume=int(task.get("max_volume") or 0),
                            )
                        )
                        if tid:
                            st.done_task_ids.add(tid)

        self._advance_prefix(code, st, row, v_break)
        st.prev_last_price = lp
        return events

    @staticmethod
    def _format_tick_time(raw: Any) -> str:
        if raw is None:
            return ""
        try:
            v = float(raw)
            if v > 1e12:
                v /= 1000.0
            if v > 1e9:
                from datetime import datetime, timezone, timedelta

                dt = datetime.fromtimestamp(v, timezone(timedelta(hours=8)))
                return dt.strftime("%H:%M:%S")
        except (TypeError, ValueError):
            pass
        return str(raw)

    @staticmethod
    def _event(
        code: str,
        kind: str,
        tick_time: str,
        trigger_price: float,
        *,
        msg: str = "",
        detail: str = "",
        metrics: Optional[Dict[str, Any]] = None,
        task_id: str = "",
        last_price: float = 0.0,
        max_volume: int = 0,
        executed_endpoint: str = "",
        grid_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        ev: Dict[str, Any] = {
            "stock_code": code,
            "type": kind,
            "tick_time": tick_time,
            "trigger_price": float(trigger_price),
            "msg": msg,
        }
        if task_id:
            ev["task_id"] = task_id
        if last_price > 0:
            ev["last_price"] = float(last_price)
        if max_volume > 0:
            ev["max_volume"] = int(max_volume)
        if executed_endpoint:
            ev["executed_endpoint"] = str(executed_endpoint)
        if grid_index is not None:
            try:
                ev["grid_index"] = int(grid_index)
            except (TypeError, ValueError):
                pass
        if detail:
            ev["detail"] = detail
        if metrics:
            ev["metrics"] = {
                k: metrics.get(k)
                for k in (
                    "cond1",
                    "cond2",
                    "cond3",
                    "passed",
                    "ratio_cond1",
                    "ask_bid_ratio_cond2",
                    "ratio_cond3",
                )
            }
        return ev
