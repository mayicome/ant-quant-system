# -*- coding: utf-8 -*-
"""盘后经 QMT 探针拉全 A（+ETF）日线并写入 data/daily_cache。
盘后批跑生产路径仍是 tools/append_daily_cache_external.py；本脚本仅可选/手工。

可选/手工备用；盘后生产默认仍是 tools/append_daily_cache_external.py（东财/腾讯/同花顺）。
外源工具 tools/append_daily_cache_external.py 仍保留作兜底。

前置：
  1) 国金 QMT 模型交易里「探针」策略已启动（同一实例，勿双开）
  2) 本工具会覆盖 userdata/qmt_daily_watch/current.py（内嵌 JOB）+ job.json 旁路副本
  3) 探针需为 dual-watch 版（tools/mz1_qmt_fine_probe.py ≥ 1.6，已部署到 python/探针.py）
  4) QMT 策略内禁止 FileIO：K 线走 FormulaOutput ROW/BATCH，不写 jsonl

用法：
  python tools/run_qmt_daily_cache.py --date 2026-10-27
  python tools/run_qmt_daily_cache.py --date 2026-10-27 --skip-etf
  python tools/run_qmt_daily_cache.py --date 2026-10-27 --codes 000001.SZ,600000.SH
  python tools/run_qmt_daily_cache.py --date 2026-10-27 --dry-run   # 只写 job，不轮询
  python tools/run_qmt_daily_cache.py --ingest-only --run-tag 20261027_190501

启动器盘后批跑日后可把 daily_cache 步骤改调本脚本；当前请手动/命令行跑。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.qmt_daily_probe import (  # noqa: E402
    PROJECT_WATCH_BACKUP,
    atomic_write_json,
    atomic_write_text,
    build_job,
    find_summary_for_run_tag,
    job_path,
    load_universe_codes,
    make_run_tag,
    out_dir,
    read_log_tail_text,
    render_watch_script,
    resolve_today_formula_output,
    watch_dir,
    watch_path,
)


TEMPLATE_PATH = Path(__file__).resolve().parent / "qmt_daily_job_template.py"


def _resolve_trade_date(raw: str) -> str:
    if raw:
        return datetime.strptime(raw[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    try:
        from utils.post_market_pipeline import resolve_asof_day

        return resolve_asof_day().isoformat()
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _parse_codes(s: str):
    out = []
    for part in (s or "").replace(";", ",").replace(" ", ",").split(","):
        p = part.strip().upper()
        if p:
            out.append(p)
    return out


def write_watch_job(
    *,
    trade_date: str,
    codes,
    run_tag: str,
    batch_size: int,
    include_etf: bool,
    download_first: bool,
    emit_batch: int = 40,
) -> Path:
    if not TEMPLATE_PATH.is_file():
        raise SystemExit("缺少模板: %s" % TEMPLATE_PATH)
    source = TEMPLATE_PATH.read_text(encoding="utf-8")

    wd = watch_dir()
    wd.mkdir(parents=True, exist_ok=True)
    try:
        out_dir().mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    job = build_job(
        trade_date=trade_date,
        codes=list(codes),
        run_tag=run_tag,
        batch_size=batch_size,
        include_etf=include_etf,
        download_first=download_first,
        emit_batch=emit_batch,
    )
    # 旁路副本：给人看 / CLI；探针脚本禁止 open 它
    atomic_write_json(job_path(), job)

    rendered = render_watch_script(source, job)
    if "json.loads(" not in rendered or "__ANT_DAILY_JOB_JSON__" in rendered:
        raise SystemExit("内嵌 JOB 失败：占位符未替换")
    # 探针内不得再 open job.json / 写 jsonl
    lowered = rendered.replace(" ", "")
    if "open(JOB_PATH" in lowered or 'open(path,"r"' in lowered or "open(out_path" in lowered:
        raise SystemExit("模板仍含 FileIO open，拒绝投递")

    # 项目侧备份
    try:
        PROJECT_WATCH_BACKUP.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        (PROJECT_WATCH_BACKUP / ("job_%s.json" % stamp)).write_text(
            job_path().read_text(encoding="utf-8"), encoding="utf-8"
        )
        (PROJECT_WATCH_BACKUP / ("current_%s.py" % stamp)).write_text(rendered, encoding="utf-8")
    except Exception as e:
        print("[qmt-daily] 项目备份失败（忽略）: %s" % e, flush=True)

    atomic_write_text(watch_path(), rendered, encoding="utf-8")
    print(
        "[qmt-daily] 已投递 watch=%s codes=%d run_tag=%s day=%s (embedded JOB, FormulaOutput rows)"
        % (watch_path(), len(codes), run_tag, trade_date),
        flush=True,
    )
    return watch_path()


def wait_summary(
    run_tag: str,
    *,
    timeout_sec: int = 0,
    poll_sec: float = 2.0,
    start_offset: int = 0,
) -> dict:
    """轮询当天 FormulaOutput，直到出现该 run_tag 的 SUMMARY/ERROR。

    timeout_sec<=0 表示一直等（与 MZ1 GUI 一致）。
    start_offset: 投递 job 前的日志大小，避免漏读秒级 SUMMARY。
    """
    path, err = resolve_today_formula_output()
    if path is None:
        print("[qmt-daily] %s ；继续等待日志出现…" % err, flush=True)
    offset = max(0, int(start_offset or 0))
    if path is not None and path.is_file():
        try:
            size = path.stat().st_size
            if offset > size:
                offset = 0
        except OSError:
            pass
    started = time.time()
    last_report = 0.0
    buf = ""
    while True:
        path2, _err2 = resolve_today_formula_output()
        if path2 is not None:
            path = path2
        if path is not None and path.is_file():
            try:
                chunk, offset = read_log_tail_text(path, from_offset=offset)
                if chunk:
                    buf += chunk
                    hit = find_summary_for_run_tag(buf, run_tag)
                    if hit is None:
                        try:
                            raw = path.read_bytes()
                            tail = raw[-2_000_000:] if len(raw) > 2_000_000 else raw
                            text = None
                            for enc in ("utf-8", "gbk", "latin-1"):
                                try:
                                    text = tail.decode(enc)
                                    break
                                except Exception:
                                    continue
                            hit = find_summary_for_run_tag(text or "", run_tag)
                        except Exception:
                            hit = None
                    if hit is not None:
                        return hit
                    if ("ANT_DAILY|ERROR|" in chunk) and (run_tag in chunk):
                        pass
            except Exception as e:
                print("[qmt-daily] 读日志异常: %s" % e, flush=True)

        now = time.time()
        if timeout_sec > 0 and (now - started) >= timeout_sec:
            raise TimeoutError("等待 SUMMARY 超时 run_tag=%s" % run_tag)
        if (now - last_report) >= 15.0:
            last_report = now
            waited = int(now - started)
            print(
                "[qmt-daily] 等待探针 SUMMARY run_tag=%s 已等待 %ds（请确认模型交易里探针在跑）"
                % (run_tag, waited),
                flush=True,
            )
        time.sleep(max(0.5, float(poll_sec)))


def main() -> int:
    ap = argparse.ArgumentParser(description="QMT 探针拉日线 → daily_cache")
    ap.add_argument("--date", default="", help="目标交易日 YYYY-MM-DD")
    ap.add_argument("--codes", default="", help="逗号分隔代码；默认全 A（+ETF）")
    ap.add_argument("--skip-etf", action="store_true", help="股票池不含 ETF")
    ap.add_argument("--no-bj", action="store_true", help="排除北交所")
    ap.add_argument("--batch-size", type=int, default=80)
    ap.add_argument("--emit-batch", type=int, default=40, help="每条 BATCH 最多几根 K")
    ap.add_argument("--no-download", action="store_true", help="不先 download_history_data")
    ap.add_argument("--timeout", type=int, default=0, help="等 SUMMARY 秒数；0=一直等")
    ap.add_argument("--dry-run", action="store_true", help="只写 job/current.py，不轮询不 ingest")
    ap.add_argument("--ingest-only", action="store_true", help="不投递，只按 run_tag/jsonl/FormulaOutput ingest")
    ap.add_argument("--run-tag", default="", help="ingest-only 或自定义 run_tag")
    ap.add_argument("--jsonl", default="", help="直接指定 jsonl（跳过等日志）")
    ap.add_argument("--limit", type=int, default=0, help="调试：最多 N 只")
    ap.add_argument("--offset", type=int, default=0, help="skip first N codes")
    ap.add_argument("--shard-size", type=int, default=0, help="sequential shards")
    ap.add_argument(
        "--shard-interval",
        type=float,
        default=3.0,
        help="seconds to sleep between shards after SUMMARY (default 3)",
    )
    args = ap.parse_args()

    if args.ingest_only or args.jsonl:
        from tools.ingest_qmt_daily_cache import ingest_jsonl, main as ingest_main

        if args.jsonl:
            return ingest_jsonl(Path(args.jsonl), trade_date=_resolve_trade_date(args.date))
        sys.argv = [
            "ingest_qmt_daily_cache.py",
            "--from-formula-log",
        ]
        if args.run_tag:
            sys.argv += ["--run-tag", args.run_tag]
        if args.date:
            sys.argv += ["--date", args.date]
        return ingest_main()

    trade_date = _resolve_trade_date(args.date)
    if args.codes.strip():
        codes = _parse_codes(args.codes)
        include_etf = not args.skip_etf
    else:
        include_etf = not args.skip_etf
        codes = load_universe_codes(include_etf=include_etf, include_bj=not args.no_bj)
    offset0 = max(0, int(getattr(args, "offset", 0) or 0))
    if offset0:
        codes = codes[offset0:]
    if args.limit and args.limit > 0:
        codes = codes[: int(args.limit)]

    if not codes:
        print("[qmt-daily] 股票池为空", flush=True)
        return 2

    shard_size = max(0, int(getattr(args, "shard_size", 0) or 0))
    # QMT 全量一次易在 ~400 只附近把客户端打崩；默认每片 100
    if shard_size <= 0 and len(codes) > 100 and not args.dry_run:
        shard_size = 100
        print(
            "[qmt-daily] 代码超过 100，自动按 shard_size=%d 分片（可用 --shard-size 改；--shard-size 0 强制整包）"
            % shard_size,
            flush=True,
        )

    from tools.ingest_qmt_daily_cache import ingest_from_summary

    def _one_shot(codes_part, tag_suffix=""):
        run_tag = (args.run_tag.strip() or make_run_tag()) + (tag_suffix or "")
        pre_offset = 0
        pre_path, _ = resolve_today_formula_output()
        if pre_path is not None and pre_path.is_file():
            try:
                pre_offset = pre_path.stat().st_size
            except OSError:
                pre_offset = 0

        write_watch_job(
            trade_date=trade_date,
            codes=codes_part,
            run_tag=run_tag,
            batch_size=max(1, int(args.batch_size)),
            include_etf=include_etf,
            download_first=not args.no_download,
            emit_batch=max(1, int(args.emit_batch or 40)),
        )

        if args.dry_run:
            print("[qmt-daily] dry-run：已写 embedded JOB + current.py，未等待探针", flush=True)
            return 0, run_tag, None

        print(
            "[qmt-daily] 已投递 run_tag=%s codes=%d；请确认模型交易里「探针」在跑（1.6 init 主线程日线）"
            % (run_tag, len(codes_part)),
            flush=True,
        )
        try:
            summary = wait_summary(
                run_tag, timeout_sec=int(args.timeout or 0), start_offset=pre_offset
            )
        except TimeoutError as e:
            print("[qmt-daily] %s" % e, flush=True)
            return 3, run_tag, None
        except KeyboardInterrupt:
            print(
                "[qmt-daily] 用户中断；稍后可用 --ingest-only --run-tag %s" % run_tag,
                flush=True,
            )
            return 130, run_tag, None

        print("[qmt-daily] SUMMARY %s" % summary, flush=True)
        if summary.get("error"):
            print("[qmt-daily] 探针报错: %s" % summary.get("error"), flush=True)
        rc = ingest_from_summary(
            summary,
            trade_date=str(summary.get("trade_date") or trade_date),
            run_tag=run_tag,
        )
        return rc, run_tag, summary

    if shard_size <= 0 or len(codes) <= shard_size:
        rc, _, _ = _one_shot(codes)
        return rc

    # sequential shards
    total = len(codes)
    n_shards = (total + shard_size - 1) // shard_size
    print(
        "[qmt-daily] 分片模式 total=%d shard_size=%d shards=%d offset0=%d"
        % (total, shard_size, n_shards, offset0),
        flush=True,
    )
    last_rc = 0
    for si in range(n_shards):
        a = si * shard_size
        b = min(total, a + shard_size)
        part = codes[a:b]
        abs_off = offset0 + a
        tag_suffix = "_s%02d" % (si + 1)
        print(
            "[qmt-daily] === shard %d/%d codes[%d:%d] abs_offset=%d n=%d ==="
            % (si + 1, n_shards, a, b, abs_off, len(part)),
            flush=True,
        )
        rc, tag, summary = _one_shot(part, tag_suffix=tag_suffix)
        last_rc = rc
        if args.dry_run:
            # dry-run only writes last shard file; still ok to stop after first write demo
            print("[qmt-daily] dry-run 只演示第 1 片，退出", flush=True)
            return 0
        if rc not in (0, None) and rc != 0:
            print(
                "[qmt-daily] 分片失败 rc=%d run_tag=%s；QMT 若已崩请重启后继续："
                " python tools/run_qmt_daily_cache.py --offset %d --shard-size %d"
                % (rc, tag, abs_off + (len(part) if summary and not summary.get("error") else 0), shard_size),
                flush=True,
            )
            # if no summary (crash), resume at this shard start
            if summary is None:
                print(
                    "[qmt-daily] 无 SUMMARY（疑似客户端崩溃），从 abs_offset=%d 重跑本片"
                    % abs_off,
                    flush=True,
                )
            return int(rc or 3)
        if summary and summary.get("error"):
            print(
                "[qmt-daily] 本片 SUMMARY 带 error，停止。续跑 --offset %d --shard-size %d"
                % (abs_off, shard_size),
                flush=True,
            )
            return 4
        # 片间冷却，减轻客户端内存/日志压力
        if si + 1 < n_shards:
            gap = float(getattr(args, "shard_interval", 3.0) or 0.0)
            if gap > 0:
                print(
                    "[qmt-daily] 片间等待 %.1fs 后继续下一片…" % gap,
                    flush=True,
                )
                time.sleep(gap)
    print("[qmt-daily] 全部分片完成", flush=True)
    return int(last_rc or 0)


if __name__ == "__main__":
    raise SystemExit(main())
