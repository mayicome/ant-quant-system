#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
执行记录管理器
用于记录和查询每天的任务执行记录
"""

import os
import json
from datetime import datetime, date
from typing import List, Dict, Optional
from utils.logger import Logger

# 本地终结态哨兵，不是柜台唯一合同号；多笔跳过共用同一字符串时不得按 sysid 去重合并
PSEUDO_ORDER_SYSIDS = frozenset(
    {
        "SKIPPED_MIN_BUY",
        "SKIPPED_BUY_WINDOW",
        "BAND_HARD_PASS",
        "ORDER_FAILED",
        "PO_BUILTIN",
        "NO_CASH",
        "MIN_BUY_AMOUNT",
        "NO_POSITION",
        "NOT_TRUE_BREAKTHROUGH",
        "CANCELLED",
        "PROBE_REMAIN_SKIPPED",
    }
)


def is_unique_broker_sysid(sysid) -> bool:
    """真实柜台合同号才可作全局唯一键；哨兵 / PO 占位则否。"""
    s = str(sysid or "").strip()
    if not s or s in PSEUDO_ORDER_SYSIDS:
        return False
    if s.startswith("PO") and len(s) <= 24:
        return False
    return True


class ExecutionRecordManager:
    """执行记录管理器"""
    
    def __init__(self):
        """初始化执行记录管理器"""
        self.logger = Logger()
        
        # 设置数据存储目录
        current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.records_dir = os.path.join(current_dir, 'data', 'execution_records')
        os.makedirs(self.records_dir, exist_ok=True)
    
    def _get_record_file_path(self, record_date: date) -> str:
        """获取指定日期的记录文件路径"""
        date_str = record_date.strftime('%Y-%m-%d')
        filename = f"execution_records_{date_str}.json"
        return os.path.join(self.records_dir, filename)
    
    def add_execution_record(self, record: Dict, dedupe_key: Optional[str] = None):
        """添加执行记录
        
        Args:
            record: 执行记录字典，包含以下字段：
                - execution_time: 执行时间 (datetime或字符串)
                - stock_code: 股票代码
                - stock_name: 股票名称
                - rule_type: 规则类型
                - rule_name: 规则名称
                - rule_detail: 规则详情字典
                - current_price: 当前价
                - trade_price: 实际交易价格
                - trade_volume: 实际交易数量
                - order_id: 订单号
                - require_manual_approval: 是否需要人工审核
                - approval_result: 审核结果 (approved/rejected/cancelled/auto/skipped/...)
                - approval_time: 审核时间
                - skip_reason: 未下单原因说明（可选）
                - execution_outcome: ordered / skipped / order_failed
            dedupe_key: 可选；相同键已存在则跳过，避免大 QMT 回写重复记一笔
        """
        try:
            # 解析执行时间
            exec_time = record.get('execution_time')
            if isinstance(exec_time, datetime):
                record_date = exec_time.date()
                record['execution_time'] = exec_time.strftime('%Y-%m-%d %H:%M:%S')
            elif isinstance(exec_time, str):
                # 尝试解析字符串
                try:
                    dt = datetime.strptime(exec_time, '%Y-%m-%d %H:%M:%S')
                    record_date = dt.date()
                except:
                    record_date = date.today()
            else:
                record_date = date.today()
                record['execution_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            key = str(dedupe_key or record.get("dedupe_key") or "").strip()
            if key:
                record["dedupe_key"] = key

            # 读取当天的记录文件
            file_path = self._get_record_file_path(record_date)
            records = []
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        records = json.load(f)
                except Exception as e:
                    self.logger.warning(f"读取执行记录文件失败: {str(e)}")
                    records = []

            if key:
                for old in records:
                    if isinstance(old, dict) and str(old.get("dedupe_key") or "") == key:
                        return False
                    # 兼容：同订单号+时间+数量已存在
                    oid_cmp = str(record.get("order_id") or "").strip()
                    if (
                        isinstance(old, dict)
                        and str(old.get("order_id") or "") == oid_cmp
                        and str(old.get("execution_time") or "") == str(record.get("execution_time") or "")
                        and int(old.get("trade_volume") or 0) == int(record.get("trade_volume") or 0)
                        and str(old.get("stock_code") or "") == str(record.get("stock_code") or "")
                        and is_unique_broker_sysid(oid_cmp)
                    ):
                        return False
            
            # 添加记录ID
            if 'record_id' not in record:
                record['record_id'] = f"{record_date.strftime('%Y%m%d')}_{len(records) + 1}"
            
            # 添加新记录
            records.append(record)
            
            # 保存到文件
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            
            self.logger.info(f"执行记录已保存: {record.get('stock_code')} - {record.get('rule_name')}")
            return True
            
        except Exception as e:
            self.logger.error(f"添加执行记录失败: {str(e)}", exc_info=True)
            return False

    @staticmethod
    def builtin_order_dedupe_key(order_rec: Dict, order_id: str = "") -> str:
        """大 QMT / results.orders 去重键：同一笔委托只记一次。"""
        rec = order_rec or {}
        sysid = str(rec.get("order_sysid") or "").strip()
        # 真实合同号才按 sys: 去重；SKIPPED_MIN_BUY 等哨兵多笔共用，必须落到 task_id
        if is_unique_broker_sysid(sysid):
            return "sys:%s" % sysid
        tid = str(rec.get("task_id") or "").strip()
        at = str(rec.get("at") or rec.get("order_time") or "").strip()
        gi = rec.get("grid_index")
        gi_s = ""
        if gi is not None:
            try:
                gi_s = "|g%d" % int(gi)
            except (TypeError, ValueError):
                gi_s = ""
        if tid and at:
            return "loc:%s|%s|%s|%s%s" % (tid, at, rec.get("price"), rec.get("volume"), gi_s)
        oid = str(order_id or rec.get("user_order_id") or "").strip()
        if oid and is_unique_broker_sysid(oid):
            return "oid:%s" % oid
        code = str(rec.get("stock_code") or "").strip().upper()
        if tid:
            return "loc:%s|%s|%s|%s|%s%s" % (
                tid,
                at,
                code,
                rec.get("price"),
                rec.get("volume"),
                gi_s,
            )
        if oid:
            return "oid:%s|%s|%s" % (oid, code, at)
        return "po:%s|%s|%s|%s|%s%s" % (
            tid,
            at,
            code,
            rec.get("price"),
            rec.get("volume"),
            gi_s,
        )

    def record_from_builtin_order(
        self,
        order_rec: Dict,
        *,
        order_id: str = "",
        stock_name: str = "",
        rule: Optional[Dict] = None,
    ) -> bool:
        """从大 QMT results.orders 记录写一条执行记录（失败/废单也会记）。"""
        rec = order_rec or {}
        code = str(rec.get("stock_code") or "").strip().upper()
        if not code:
            return False
        status = str(rec.get("status") or "").strip().lower()
        msg = str(rec.get("msg") or "").strip()
        try:
            bst = int(rec.get("broker_status")) if rec.get("broker_status") is not None else -1
        except (TypeError, ValueError):
            bst = -1
        ordered = (
            msg == "passorder_called"
            or status in ("submitted", "filled", "passorder_called", "error", "skipped")
            or bst in (50, 51, 52, 55, 56, 57)
        )
        if not ordered:
            return False

        ev = str(rec.get("event_type") or "").strip()
        ev_map = {
            "single_buy_hit": ("single_buy", "单点买入"),
            "single_sell_hit": ("single_sell", "单点卖出"),
            "tb_pass": ("breakthrough_buy", "突破买入"),
            "tb_fail": ("breakthrough_buy", "突破买入"),
            "breakthrough_sell_hit": ("breakthrough_sell", "突破卖出"),
            "best_buy_hit": ("best_buy", "弹性买入"),
            "best_sell_hit": ("best_sell", "弹性卖出"),
            "cage_buy_hit": ("cage_buy", "笼子买入"),
            "cage_sell_hit": ("cage_sell", "笼子卖出"),
            "grid_buy_hit": ("grid_buy", "网格买入"),
            "grid_sell_hit": ("grid_sell", "网格卖出"),
            "scheduled_clear_hit": ("scheduled_clear", "定时清仓"),
            "scheduled_clear_skip": ("scheduled_clear", "定时清仓"),
            "night_buy_hit": ("night_buy", "夜市买入"),
            "night_sell_hit": ("night_sell", "夜市卖出"),
            "early_place": ("early_order", "提前下单"),
            "early_confirm": ("early_order", "提前下单确认"),
            "early_cancel": ("early_order", "提前撤单"),
        }
        rule_type, rule_type_cn = ev_map.get(ev, ("", ""))
        if rule and isinstance(rule, dict):
            rt = str(rule.get("type") or rule.get("rule_type") or "").strip()
            if rt:
                rule_type = rt
            rn = str(rule.get("name") or "").strip()
        else:
            rn = ""
        if not rule_type:
            side = str(rec.get("side") or "buy").lower()
            rule_type = "single_sell" if side == "sell" else "single_buy"
            rule_type_cn = "单点卖出" if side == "sell" else "单点买入"
        if not rule_type_cn:
            try:
                from core.trading_rules import RULE_TYPE_NAMES, RuleType
                rule_type_cn = RULE_TYPE_NAMES.get(RuleType(rule_type), rule_type)
            except Exception:
                rule_type_cn = rule_type or "未知类型"

        strategy = str(rec.get("strategy_name") or "").strip()
        rule_name = rn or strategy or rule_type_cn or "内置下单"

        at = str(rec.get("at") or "").strip()
        if "T" in at:
            exec_time = at.replace("T", " ")[:19]
        elif at:
            exec_time = at[:19]
        else:
            exec_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        px = float(rec.get("price") or 0)
        vol = int(rec.get("volume") or 0)
        oid = str(order_id or rec.get("order_sysid") or "").strip()
        failed = status == "error" or bst == 57
        if failed and not oid:
            oid = "ORDER_FAILED"
        if not oid:
            oid = self.builtin_order_dedupe_key(rec)

        is_band_hp = (
            bool(rec.get("band_hard_pass"))
            or str(rec.get("msg") or "") == "band_hard_pass"
            or oid == "BAND_HARD_PASS"
            or "硬pass" in str(rec.get("detail") or rec.get("true_breakthrough_detail") or "")
        )
        is_skipped = (
            status == "skipped"
            or ev == "scheduled_clear_skip"
            or bool(rec.get("buy_block_window"))
            or is_band_hp
        )

        if rule and isinstance(rule, dict):
            ti = {
                "type": "sell" if "sell" in rule_type else "buy",
                "volume": vol,
                "price": px,
            }
            if rec.get("grid_index") is not None:
                try:
                    ti["grid_index"] = int(rec.get("grid_index"))
                except (TypeError, ValueError):
                    pass
            detail = self.format_rule_detail(rule, rule_type, ti)
        else:
            detail = "%s: 价格%.4f元, %d股" % (rule_type_cn, px, vol)
            if rec.get("grid_index") is not None:
                detail = "%s | 第%s格" % (detail, rec.get("grid_index"))
        if strategy and strategy not in detail:
            detail = "%s | %s" % (detail, strategy)

        skip_reason = None
        approval_result = "auto"
        execution_outcome = "ordered"
        tb_detail = str(
            rec.get("true_breakthrough_detail")
            or rec.get("detail")
            or ""
        ).strip() or None
        tb_passed = rec.get("true_breakthrough_passed")

        if failed:
            detail = "%s | 废单/下单失败" % detail
            skip_reason = "废单/下单失败"
            approval_result = "order_failed"
            execution_outcome = "order_failed"
        elif is_band_hp:
            skip_reason = str(
                rec.get("detail")
                or rec.get("true_breakthrough_detail")
                or rec.get("msg")
                or "价格带放弃未下单"
            ).strip()
            if skip_reason == "band_hard_pass":
                skip_reason = "价格带放弃未下单"
            # 兼容旧文案
            if "硬pass" in skip_reason:
                skip_reason = skip_reason.replace("硬pass", "放弃")
            if skip_reason and skip_reason not in detail:
                detail = "%s | %s" % (detail, skip_reason)
            approval_result = "band_hard_pass"
            execution_outcome = "skipped"
            oid = oid or "BAND_HARD_PASS"
            if oid in ("", "PO_BUILTIN"):
                oid = "BAND_HARD_PASS"
            tb_passed = True if tb_passed is None else tb_passed
            vol = 0
        elif is_skipped:
            skip_reason = str(rec.get("msg") or "").strip()
            if bool(rec.get("buy_block_window")) or oid == "SKIPPED_BUY_WINDOW":
                approval_result = "buy_block_window"
                if not skip_reason:
                    skip_reason = "命中禁买时间窗"
            elif "overdue" in skip_reason:
                skip_reason = "超时未执行"
                approval_result = "skipped"
            elif "price_not_met" in skip_reason:
                skip_reason = "价格不满足跳过"
                approval_result = "skipped"
            else:
                approval_result = "skipped"
            # 真突破三条数值在前，跳过原因在后（最小买入等也保留明细）
            if tb_detail and tb_detail not in detail:
                detail = "%s | %s" % (detail, tb_detail)
            if skip_reason and skip_reason not in detail:
                detail = "%s | %s" % (detail, skip_reason)
            execution_outcome = "skipped"
            if not skip_reason:
                skip_reason = "已跳过"

        record = {
            "execution_time": exec_time,
            "stock_code": code,
            "stock_name": stock_name or code,
            "rule_type": rule_type,
            "rule_type_cn": rule_type_cn,
            "rule_name": rule_name,
            "rule_detail": detail,
            "true_breakthrough_detail": tb_detail,
            "true_breakthrough_passed": tb_passed,
            "skip_reason": skip_reason,
            "execution_outcome": execution_outcome,
            "current_price": float(rec.get("last_price") or px or 0),
            "trade_price": px,
            "trade_volume": vol,
            "order_id": oid,
            "require_manual_approval": False,
            "approval_result": approval_result,
            "approval_time": None,
        }
        return bool(
            self.add_execution_record(
                record,
                dedupe_key=self.builtin_order_dedupe_key(rec, oid),
            )
        )

    def get_records_by_date(self, record_date: date) -> List[Dict]:
        """获取指定日期的执行记录
        
        Args:
            record_date: 日期
            
        Returns:
            执行记录列表
        """
        try:
            file_path = self._get_record_file_path(record_date)
            if not os.path.exists(file_path):
                return []
            
            with open(file_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
            
            return records
            
        except Exception as e:
            self.logger.error(f"读取执行记录失败: {str(e)}", exc_info=True)
            return []
    
    def get_records_by_date_range(self, start_date: date, end_date: date) -> List[Dict]:
        """获取日期范围内的执行记录
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            执行记录列表（按执行时间排序）
        """
        all_records = []
        
        current_date = start_date
        while current_date <= end_date:
            records = self.get_records_by_date(current_date)
            all_records.extend(records)
            # 移动到下一天
            from datetime import timedelta
            current_date += timedelta(days=1)
        
        # 按执行时间排序
        all_records.sort(key=lambda x: x.get('execution_time', ''))
        
        return all_records
    
    def get_today_records(self) -> List[Dict]:
        """获取今天的执行记录"""
        return self.get_records_by_date(date.today())
    
    def format_rule_detail(self, rule: Dict, rule_type: str, trade_info: Dict) -> str:
        """格式化规则详情字符串
        
        Args:
            rule: 规则字典
            rule_type: 规则类型
            trade_info: 交易信息字典
            
        Returns:
            格式化的规则详情字符串
        """
        try:
            if rule_type == 'single_buy':
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                return f"单点买入: 价格{price:.2f}元, {volume}股"
            
            elif rule_type == 'single_sell':
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                return f"单点卖出: 价格{price:.2f}元, {volume}股"
            
            elif rule_type == 'breakthrough_buy':
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                base = f"突破买入: 突破价{price:.2f}元, 计划{volume}股"
                phase = (trade_info or {}).get("breakthrough_probe_phase")
                if phase == "probe":
                    probe_v = int((trade_info or {}).get("volume") or 0)
                    remain_v = int((trade_info or {}).get("breakthrough_probe_remain") or 0)
                    return f"{base} | 试探建仓-试探{probe_v}股（待确认补买{remain_v}股）"
                if phase == "remain":
                    add_v = int((trade_info or {}).get("volume") or 0)
                    return f"{base} | 试探建仓-确认补买{add_v}股"
                if phase == "remain_skipped":
                    skip_v = int((trade_info or {}).get("volume") or 0)
                    return f"{base} | 试探建仓-放弃补买{skip_v}股"
                return f"{base}（价格>突破价时买入）"
            
            elif rule_type == 'breakthrough_sell':
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                return f"突破卖出: 突破价{price:.2f}元, {volume}股（价格<突破价时卖出）"
            
            elif rule_type == 'cage_buy':
                price_low = rule.get('price_low', 0)
                price_high = rule.get('price_high', 0)
                volume = rule.get('volume', 0)
                return f"笼子买入: [{price_low:.2f} ~ {price_high:.2f}]元, {volume}股"
            
            elif rule_type == 'cage_sell':
                price_low = rule.get('price_low', 0)
                price_high = rule.get('price_high', 0)
                volume = rule.get('volume', 0)
                return f"笼子卖出: [{price_low:.2f} ~ {price_high:.2f}]元, {volume}股"
            
            elif rule_type == 'grid_buy':
                start_price = rule.get('start_price', 0)
                end_price = rule.get('end_price', 0)
                num_grids = rule.get('num_grids', 3)
                volume_per_grid = rule.get('volume_per_grid', 100)
                grid_index = trade_info.get('grid_index')
                grid_step = rule.get('grid_step', 0.5)
                
                # 计算触发的网格价格
                if grid_index is not None:
                    grid_price = start_price - (start_price - end_price) * grid_index / num_grids
                    grid_price = round(grid_price, 2)
                    return f"网格买入: [{start_price:.2f} ~ {end_price:.2f}]元, {num_grids+1}个网格点, 每格{volume_per_grid}股, 间距{grid_step:.2f}元, 触发第{grid_index+1}个点({grid_price:.2f}元)"
                else:
                    return f"网格买入: [{start_price:.2f} ~ {end_price:.2f}]元, {num_grids+1}个网格点, 每格{volume_per_grid}股, 间距{grid_step:.2f}元"
            
            elif rule_type == 'grid_sell':
                start_price = rule.get('start_price', 0)
                end_price = rule.get('end_price', 0)
                num_grids = rule.get('num_grids', 3)
                volume_per_grid = rule.get('volume_per_grid', 100)
                grid_index = trade_info.get('grid_index')
                grid_step = rule.get('grid_step', 0.5)
                
                # 计算触发的网格价格
                if grid_index is not None:
                    grid_price = start_price + (end_price - start_price) * grid_index / num_grids
                    grid_price = round(grid_price, 2)
                    return f"网格卖出: [{start_price:.2f} ~ {end_price:.2f}]元, {num_grids+1}个网格点, 每格{volume_per_grid}股, 间距{grid_step:.2f}元, 触发第{grid_index+1}个点({grid_price:.2f}元)"
                else:
                    return f"网格卖出: [{start_price:.2f} ~ {end_price:.2f}]元, {num_grids+1}个网格点, 每格{volume_per_grid}股, 间距{grid_step:.2f}元"
            
            elif rule_type == 'best_buy':
                trigger_price = rule.get('trigger_price', 0)
                rise_percent = rule.get('rise_percent', 5.0)
                volume = rule.get('volume', 0)
                return f"弹性买入: 触发价{trigger_price:.2f}元, 反弹{rise_percent}%, {volume}股"
            
            elif rule_type == 'best_sell':
                trigger_price = rule.get('trigger_price', 0)
                drop_percent = rule.get('drop_percent', 5.0)
                volume = rule.get('volume', 0)
                return f"弹性卖出: 触发价{trigger_price:.2f}元, 回落{drop_percent}%, {volume}股"
            
            elif rule_type == 'night_buy':
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                return f"夜市买入: {price:.2f}元, {volume}股"
            
            elif rule_type == 'night_sell':
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                return f"夜市卖出: {price:.2f}元, {volume}股"
            
            elif rule_type == 'scheduled_clear':
                scheduled_clear_time = rule.get('scheduled_clear_time', '14:56:00')
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                vol_text = f"{volume}股" if volume > 0 else "全部"
                return f"定时清仓: 时间{scheduled_clear_time}, 触发价{price:.2f}元, {vol_text}"
            
            else:
                return f"未知规则类型: {rule_type}"
                
        except Exception as e:
            self.logger.error(f"格式化规则详情失败: {str(e)}", exc_info=True)
            return "规则详情格式化失败"

