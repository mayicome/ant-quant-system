# -*- coding: utf-8 -*-
"""两台电脑对比大 QMT 反应速度。

用法（每台电脑各开一个）：
  python tools/qmt_speed_bench_gui.py

行情/处理：两台填同一个开始时刻，点「预约采样」，到点自动采，界面有倒计时。
下单：一台选买或卖的股数，另一台选另一个股数，填同一个触发时刻。
模型交易里的「蚂蚁量化规则」须已加载含 ant_speed_probe 的版本。
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timedelta

from PyQt5.QtCore import QDateTime, QTimer, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA = os.path.join(ROOT, "data")
REQ = os.path.join(DATA, "qmt_speed_probe_request.json")
LOG_DIR = os.path.join(DATA, "qmt_speed_logs")
NTP_HOST = "ntp.aliyun.com"
_NTP_UNIX_DELTA = 2208988800


def _ntp_offset_ms(host=NTP_HOST, timeout=2.0):
    """本机时钟减 NTP 的偏差（毫秒）。正数表示本机偏快。"""
    packet = b"\x1b" + 47 * b"\0"
    t0 = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (host, 123))
        data, _ = sock.recvfrom(256)
    finally:
        sock.close()
    t3 = time.time()
    if len(data) < 48:
        raise RuntimeError("NTP 应答过短")

    def _ts(offset):
        sec, frac = struct.unpack("!II", data[offset : offset + 8])
        return sec + frac / 4294967296.0 - _NTP_UNIX_DELTA

    t1 = _ts(32)
    t2 = _ts(40)
    offset_s = ((t1 - t0) + (t2 - t3)) / 2.0
    rtt_s = (t3 - t0) - (t2 - t1)
    return offset_s * 1000.0, rtt_s * 1000.0


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _pct(values, p):
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    w = k - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def _fmt(v, nd=1):
    if v is None:
        return "—"
    return ("%." + str(nd) + "f") % v


def _ensure_513130():
    from utils.strategy_pool_watch import get_strategy_pool_watch, set_strategy_pool_watch

    cur = list(get_strategy_pool_watch() or [])
    codes6 = []
    for c in cur:
        d = "".join(ch for ch in str(c) if ch.isdigit())[:6]
        if d:
            codes6.append(d)
    if "513130" not in codes6:
        codes6.append("513130")
        set_strategy_pool_watch(codes6)
        return True
    return False


class BenchWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QMT 反应速度对比")
        self.resize(760, 720)
        self._sid = ""
        self._log_pos = 0
        self._events = []
        self._quote_begin = None
        self._quote_end = None
        self._quote_dur = 20
        self._order_fire = None
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(400)

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)

        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("本机角色"))
        self.role = QComboBox()
        self.role.addItem("买 100 股", ("buy", 100))
        self.role.addItem("买 200 股", ("buy", 200))
        self.role.addItem("卖 100 股", ("sell", 100))
        self.role.addItem("卖 200 股", ("sell", 200))
        role_row.addWidget(self.role)
        role_row.addStretch()
        lay.addLayout(role_row)

        hint = QLabel(
            "采样和下单都填同一个时刻，不必同时点击。两台点「检查对时」，"
            "偏差都在大约 50 毫秒以内即可，不必追求 0。"
            "须已启动模型交易里的「蚂蚁量化规则」，并先重启过以加载测速模块。"
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

        clock_row = QHBoxLayout()
        self.lab_ntp = QLabel("对时：尚未检查")
        self.lab_ntp.setWordWrap(True)
        self.btn_ntp = QPushButton("检查对时")
        self.btn_ntp.clicked.connect(self._check_ntp)
        self.btn_resync = QPushButton("立即对时")
        self.btn_resync.clicked.connect(self._resync_windows)
        clock_row.addWidget(self.lab_ntp, 1)
        clock_row.addWidget(self.btn_ntp)
        clock_row.addWidget(self.btn_resync)
        lay.addLayout(clock_row)

        quote = QGroupBox("1. 行情到达    2. 本机处理")
        qf = QVBoxLayout(quote)
        row = QHBoxLayout()
        row.addWidget(QLabel("开始时刻"))
        self.sample_at = QDateTimeEdit()
        self.sample_at.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.sample_at.setDateTime(QDateTime.currentDateTime().addSecs(20))
        self.sample_at.setCalendarPopup(True)
        row.addWidget(self.sample_at)
        row.addWidget(QLabel("采样秒数"))
        self.duration = QSpinBox()
        self.duration.setRange(5, 180)
        self.duration.setValue(20)
        row.addWidget(self.duration)
        self.btn_quote = QPushButton("预约采样")
        self.btn_quote.clicked.connect(self._start_quote)
        self.btn_quote_stop = QPushButton("停止")
        self.btn_quote_stop.clicked.connect(self._stop)
        row.addWidget(self.btn_quote)
        row.addWidget(self.btn_quote_stop)
        row.addStretch()
        qf.addLayout(row)
        self.lab_clock = QLabel("未预约采样")
        self.lab_clock.setWordWrap(True)
        qf.addWidget(self.lab_clock)
        self.lab_quote = QLabel("尚未采样")
        self.lab_quote.setWordWrap(True)
        self.lab_proc = QLabel("处理耗时：—")
        self.lab_proc.setWordWrap(True)
        qf.addWidget(self.lab_quote)
        qf.addWidget(self.lab_proc)
        lay.addWidget(quote)

        order = QGroupBox("3. 同时真买 513130（谁先成交）")
        of = QFormLayout(order)
        self.fire_at = QDateTimeEdit()
        self.fire_at.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.fire_at.setDateTime(QDateTime.currentDateTime().addSecs(30))
        self.fire_at.setCalendarPopup(True)
        of.addRow("触发时刻", self.fire_at)
        self.btn_order = QPushButton("预约下单")
        self.btn_order.clicked.connect(self._arm_order)
        self.btn_order_stop = QPushButton("取消预约")
        self.btn_order_stop.clicked.connect(self._stop)
        brow = QHBoxLayout()
        brow.addWidget(self.btn_order)
        brow.addWidget(self.btn_order_stop)
        brow.addStretch()
        of.addRow("", brow)
        self.lab_order = QLabel("未预约。到点由大 QMT 下 513130 市价单。")
        self.lab_order.setWordWrap(True)
        of.addRow(self.lab_order)
        lay.addWidget(order)

        cmp_row = QHBoxLayout()
        self.btn_import = QPushButton("导入另一台日志并对比")
        self.btn_import.clicked.connect(self._import_other)
        cmp_row.addWidget(self.btn_import)
        cmp_row.addStretch()
        lay.addLayout(cmp_row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)

    def _machine(self):
        data = self.role.currentData()
        if isinstance(data, tuple) and len(data) == 2:
            side, vol = str(data[0]), int(data[1])
        else:
            side, vol = "buy", 100
        verb = "卖" if side == "sell" else "买"
        return "%s%d股" % (verb, vol), vol, side

    def _check_ntp(self):
        self.lab_ntp.setText("正在询问 %s …" % NTP_HOST)
        QApplication.processEvents()
        samples = []
        last_err = None
        for _ in range(3):
            try:
                samples.append(_ntp_offset_ms())
            except Exception as e:
                last_err = e
        if not samples:
            self.lab_ntp.setText("对时检查失败：%s" % last_err)
            return
        samples.sort(key=lambda item: item[0])
        offset_ms, rtt_ms = samples[len(samples) // 2]
        mag = abs(offset_ms)
        if mag <= 50:
            verdict = "已对齐"
        elif mag <= 200:
            verdict = "可以测"
        else:
            verdict = "偏差过大，请立即对时"
        self.lab_ntp.setText(
            "相对 %s：本机 %s %.0f ms（往返 %.0f ms）。%s"
            % (
                NTP_HOST,
                "快" if offset_ms > 0 else "慢",
                mag,
                rtt_ms,
                verdict,
            )
        )

    def _resync_windows(self):
        self.lab_ntp.setText("正在让 Windows 时间服务重新对时…")
        QApplication.processEvents()
        try:
            proc = subprocess.run(
                ["w32tm", "/resync", "/force"],
                capture_output=True,
                text=True,
                timeout=20,
                encoding="gbk" if sys.platform == "win32" else "utf-8",
                errors="replace",
            )
        except Exception as e:
            QMessageBox.warning(self, "对时", "无法执行 w32tm：%s" % e)
            return
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if proc.returncode == 0:
            self._check_ntp()
            return
        QMessageBox.warning(
            self,
            "对时未成功",
            "Windows 拒绝重新对时（通常是因为当前程序不是管理员）。\n"
            "请在两台电脑上分别用管理员打开 PowerShell，执行：\n\n"
            "w32tm /config /manualpeerlist:ntp.aliyun.com /syncfromflags:manual /update\n"
            "w32tm /resync /force\n\n"
            "%s" % (out or ("exit %s" % proc.returncode)),
        )

    def _new_request(self, cmd, extra):
        machine, vol, side = self._machine()
        sid = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        data = {
            "id": sid,
            "cmd": cmd,
            "code": "513130.SH",
            "machine": machine,
            "side": side,
            "volume": vol,
            "confirm_real_order": False,
            "started_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_sec": int(self.duration.value()),
            "fire_at": "",
        }
        data.update(extra or {})
        _write_json(REQ, data)
        self._sid = sid
        self._log_pos = 0
        self._events = []
        self.log.clear()
        return data

    def _start_quote(self):
        when = self.sample_at.dateTime().toPyDateTime().replace(microsecond=0)
        if when <= datetime.now() + timedelta(seconds=2):
            QMessageBox.warning(
                self, "开始时刻", "请把开始时刻设到至少 3 秒之后，两台填同一个时间。"
            )
            return
        added = False
        try:
            added = _ensure_513130()
        except Exception as e:
            QMessageBox.warning(self, "订阅", "写入股票池订阅失败：%s" % e)
            return
        dur = int(self.duration.value())
        self._quote_begin = when
        self._quote_end = when + timedelta(seconds=dur)
        self._quote_dur = dur
        self._order_fire = None
        self._new_request(
            "quote",
            {
                "started_at": when.strftime("%Y-%m-%dT%H:%M:%S"),
                "duration_sec": dur,
            },
        )
        msg = "已预约 %s 开始，采样 %d 秒，%s 结束。" % (
            when.strftime("%H:%M:%S"),
            dur,
            self._quote_end.strftime("%H:%M:%S"),
        )
        if added:
            msg += " 已把 513130 并入临时订阅，大 QMT 会重载一次规则。"
        self.lab_clock.setText(msg)
        self.lab_quote.setText("尚未开始")
        self.lab_proc.setText("处理耗时：—")
        self._tick_clocks()

    def _arm_order(self):
        machine, vol, side = self._machine()
        when = self.fire_at.dateTime().toPyDateTime()
        when = when.replace(microsecond=0)
        if when <= datetime.now() + timedelta(seconds=2):
            QMessageBox.warning(self, "触发时刻", "请把触发时刻设到至少 3 秒之后，两台填同一个时间。")
            return
        verb = "卖出" if side == "sell" else "买入"
        ans = QMessageBox.question(
            self,
            "确认真单",
            "将在 %s 由大 QMT %s 513130 %d 股（真单，角色 %s）。\n"
            "另一台应选另一个股数，并填同一个触发时刻。\n\n确定预约？"
            % (when.strftime("%H:%M:%S"), verb, vol, machine),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        try:
            _ensure_513130()
        except Exception as e:
            QMessageBox.warning(self, "订阅", "写入股票池订阅失败：%s" % e)
            return
        self._quote_begin = None
        self._quote_end = None
        self._order_fire = when
        self._new_request(
            "order",
            {
                "confirm_real_order": True,
                "side": side,
                "volume": vol,
                "fire_at": when.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
        self.lab_clock.setText("采样未在进行（下单预约会替换采样请求）。")
        self.lab_order.setText(
            "已预约 %s %s %d 股。到点后看下方「已报 / 成交」。"
            % (when.strftime("%H:%M:%S"), verb, vol)
        )

    def _stop(self):
        self._quote_begin = None
        self._quote_end = None
        self._order_fire = None
        self.lab_clock.setText("已停止采样。")
        if os.path.isfile(REQ):
            try:
                with open(REQ, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
            if isinstance(data, dict):
                data["cmd"] = "stop"
                data["confirm_real_order"] = False
                _write_json(REQ, data)
        self.lab_order.setText(self.lab_order.text() + "\n已停止。")

    def _poll(self):
        self._tick_clocks()
        if not self._sid:
            return
        path = os.path.join(LOG_DIR, self._sid + ".jsonl")
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                f.seek(self._log_pos)
                chunk = f.read()
                self._log_pos = f.tell()
        except OSError:
            return
        if not chunk:
            return
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._events.append(ev)
            self._append_line(ev)
        self._refresh_stats()

    def _append_line(self, ev):
        kind = ev.get("kind")
        if kind == "quote":
            return
        self.log.append(
            "%s  %s  %s"
            % (ev.get("wall") or "", kind, json.dumps({k: v for k, v in ev.items() if k not in ("kind", "wall", "machine")}, ensure_ascii=False))
        )

    def _tick_clocks(self):
        now = datetime.now()
        if self._quote_begin is not None and self._quote_end is not None:
            if now < self._quote_begin:
                sec = int((self._quote_begin - now).total_seconds()) + 1
                self.lab_clock.setText(
                    "等待开始：还有 %d 秒（%s 开始，采 %d 秒，%s 结束）"
                    % (
                        sec,
                        self._quote_begin.strftime("%H:%M:%S"),
                        self._quote_dur,
                        self._quote_end.strftime("%H:%M:%S"),
                    )
                )
            elif now <= self._quote_end:
                sec = int((self._quote_end - now).total_seconds()) + 1
                self.lab_clock.setText(
                    "采样中：还剩 %d 秒（%s 结束）"
                    % (sec, self._quote_end.strftime("%H:%M:%S"))
                )
            else:
                self.lab_clock.setText(
                    "采样已结束（%s – %s）"
                    % (
                        self._quote_begin.strftime("%H:%M:%S"),
                        self._quote_end.strftime("%H:%M:%S"),
                    )
                )
        if self._order_fire is not None and not any(
            e.get("kind") == "order_sent" for e in self._events
        ):
            if now < self._order_fire:
                sec = max(0, int((self._order_fire - now).total_seconds()) + 1)
                self.lab_order.setText(
                    "等待下单：还有 %d 秒（%s）"
                    % (sec, self._order_fire.strftime("%H:%M:%S"))
                )
            else:
                late = (now - self._order_fire).total_seconds()
                seen = any(e.get("kind") == "probe_seen" for e in self._events)
                if seen and late < 8:
                    self.lab_order.setText("已到点，正在下单…")
                elif late < 8:
                    self.lab_order.setText("已到点，等待大 QMT …")
                else:
                    self.lab_order.setText(
                        "已到点，但还没有下单回报。模型交易输出里若没有「测速」，请停止并重新启动「蚂蚁量化规则」后再预约。"
                    )

    def _refresh_stats(self):
        quotes = [e for e in self._events if e.get("kind") == "quote"]
        lags = [e.get("lag_ms") for e in quotes if e.get("lag_ms") is not None]
        us = [e.get("strategy_us") for e in quotes if e.get("strategy_us") is not None]
        prec = ""
        if quotes:
            prec = str(quotes[-1].get("lag_prec") or "")
        prec_note = "（行情时间只有秒，滞后会偏粗）" if prec == "sec" else ""
        self.lab_quote.setText(
            "样本 %d    行情滞后 中位 %s ms    95分位 %s ms %s"
            % (len(quotes), _fmt(_pct(lags, 50)), _fmt(_pct(lags, 95)), prec_note)
        )
        self.lab_proc.setText(
            "回调处理 中位 %s μs    95分位 %s μs"
            % (_fmt(_pct(us, 50), 0), _fmt(_pct(us, 95), 0))
        )
        sent = next((e for e in self._events if e.get("kind") == "order_sent"), None)
        fills = [e for e in self._events if e.get("kind") == "fill"]
        if sent or fills:
            parts = []
            if sent:
                verb = "卖出" if str(sent.get("side") or "") == "sell" else "买入"
                parts.append(
                    "%s已报 %s  价 %s  数量 %s  passorder %s μs  %s"
                    % (
                        verb,
                        sent.get("wall"),
                        sent.get("price"),
                        sent.get("volume"),
                        sent.get("call_us"),
                        "成功" if sent.get("ok") else ("失败 " + str(sent.get("reason") or "")),
                    )
                )
            if fills:
                last = fills[-1]
                parts.append(
                    "成交 %s  量 %s  价 %s  柜台时间 %s"
                    % (last.get("wall"), last.get("volume"), last.get("price"), last.get("trade_time") or "—")
                )
                if sent and sent.get("wall") and last.get("wall"):
                    try:
                        t0 = datetime.strptime(str(sent["wall"])[:23], "%Y-%m-%dT%H:%M:%S.%f")
                        t1 = datetime.strptime(str(last["wall"])[:23], "%Y-%m-%dT%H:%M:%S.%f")
                        parts.append("本机从发单到成交 %.0f ms" % ((t1 - t0).total_seconds() * 1000.0))
                    except ValueError:
                        pass
            self.lab_order.setText("\n".join(parts))

    def _import_other(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择另一台的 jsonl 日志", LOG_DIR, "JSONL (*.jsonl)"
        )
        if not path:
            return
        other = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        other.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            QMessageBox.warning(self, "读取失败", str(e))
            return
        mine_lag = _pct([e.get("lag_ms") for e in self._events if e.get("kind") == "quote"], 50)
        oth_lag = _pct([e.get("lag_ms") for e in other if e.get("kind") == "quote"], 50)
        mine_us = _pct([e.get("strategy_us") for e in self._events if e.get("kind") == "quote"], 50)
        oth_us = _pct([e.get("strategy_us") for e in other if e.get("kind") == "quote"], 50)
        mine_fill = next((e for e in self._events if e.get("kind") == "fill"), None)
        oth_fill = next((e for e in other if e.get("kind") == "fill"), None)
        lines = [
            "行情滞后中位：本机 %s ms，另一台 %s ms（小者更快，两台需已对时）" % (_fmt(mine_lag), _fmt(oth_lag)),
            "处理耗时中位：本机 %s μs，另一台 %s μs（小者更快，不依赖对时）" % (_fmt(mine_us, 0), _fmt(oth_us, 0)),
        ]
        if mine_fill or oth_fill:
            lines.append(
                "成交本机时钟：本机 %s（%s股）  另一台 %s（%s股）"
                % (
                    (mine_fill or {}).get("wall") or "无",
                    (mine_fill or {}).get("volume") or "—",
                    (oth_fill or {}).get("wall") or "无",
                    (oth_fill or {}).get("volume") or "—",
                )
            )
            if mine_fill and oth_fill and mine_fill.get("wall") and oth_fill.get("wall"):
                try:
                    a = datetime.strptime(str(mine_fill["wall"])[:23], "%Y-%m-%dT%H:%M:%S.%f")
                    b = datetime.strptime(str(oth_fill["wall"])[:23], "%Y-%m-%dT%H:%M:%S.%f")
                    if a < b:
                        lines.append("本机先成交，早 %.0f ms" % ((b - a).total_seconds() * 1000.0))
                    elif b < a:
                        lines.append("另一台先成交，早 %.0f ms" % ((a - b).total_seconds() * 1000.0))
                    else:
                        lines.append("两台成交时钟相同")
                except ValueError:
                    pass
        self.log.append("\n".join(lines))


def main():
    app = QApplication(sys.argv)
    w = BenchWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
