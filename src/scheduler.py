#!/usr/bin/env python3
"""
定时采集调度器
==============
只做一件事：**按固定间隔采集快照并落盘**，为生命周期类分析攒历史。

为什么它不花 token
------------------
调度器调用的是 pipeline.run_collect_only()，只跑「采集 → 清洗 → 打标 → 指标」四步：
  - 采集   ：HTTP 请求 trends.google.com，纯标准库
  - 清洗   ：本地规则
  - 打标   ：本地词典 config/taxonomy.json（不是让模型逐条分类）
  - 指标   ：pandas-free 的纯 Python 计算
需求解析与报告叙述这两步（唯一调 LLM 的地方）被跳过。
所以每小时采一次，一年 8760 次，**API 费用是 0**。

安全阀
------
  - 最小间隔 15 分钟（防止把对方站点打疼）
  - 每轮请求间隔 >= 1s（采集器内建）
  - 每日运行次数上限（默认 30，正常 24 次/天，留 6 次给失败重试）
  - 采集失败自动降级到本地快照，不算作崩溃

用法
----
    python src/scheduler.py                  # 前台跑，每小时一次
    python src/scheduler.py --once           # 只采一次，可用于系统计划任务
    python src/scheduler.py --interval 30    # 每 30 分钟
    python src/scheduler.py --status         # 查看调度状态，不采集

Windows 计划任务 / cron 里推荐用 --once 模式，让操作系统负责调度。
"""

import argparse
import json
import os
import random
import sys
import threading
import time
import traceback
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
# 与 pipeline.py 保持一致：可用 DATAPULSE_DATA_DIR 覆盖数据目录
DATA_DIR = os.environ.get("DATAPULSE_DATA_DIR") or os.path.join(BASE_DIR, "data")
SCHEDULER_PATH = os.path.join(DATA_DIR, "scheduler.json")
sys.path.insert(0, SRC_DIR)

MIN_INTERVAL_MINUTES = 15

# 进程内调度线程控制（供 server.py 一键开关）
_STOP = threading.Event()
_THREAD = {"t": None, "interval": None, "started_at": None}


def request_stop():
    """请求停止运行中的调度循环。当前这一轮采集会跑完，不会半途丢数据。"""
    _STOP.set()


def is_running():
    t = _THREAD.get("t")
    return bool(t and t.is_alive())


def thread_info():
    return {
        "running": is_running(),
        "interval_minutes": _THREAD.get("interval"),
        "started_at": _THREAD.get("started_at"),
    }


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    """原子写，避免读方拿到半截文件。"""
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_state():
    return _load(SCHEDULER_PATH, {
        "created_at": None,
        "interval_minutes": None,
        "total_runs": 0,
        "total_failures": 0,
        "llm_calls": 0,             # 恒为 0：采集路径不经过模型
        "today": None,
        "today_runs": 0,
        "last_run": None,
        "last_result": None,
        "next_run_at": None,
        "history": [],              # 最近若干次运行摘要
        "hour_coverage": {},        # "YYYY-MM-DDTHH" -> 采到的条数，用于看有没有漏采
    })


def prune(state, keep=200):
    state["history"] = state["history"][-keep:]
    # hour_coverage 只保留最近 30 天
    keys = sorted(state["hour_coverage"].keys())
    if len(keys) > 24 * 30:
        for k in keys[:len(keys) - 24 * 30]:
            state["hour_coverage"].pop(k, None)
    return state


def daily_cap_reached(state, max_per_day):
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("today") != today:
        state["today"] = today
        state["today_runs"] = 0
    return state["today_runs"] >= max_per_day


def collect_once(geo="US", hours=24, quiet=False, max_per_day=30, force=False):
    """执行一次采集。返回 pipeline.run_collect_only() 的结果字典。

    这是本模块对外的主要接口 —— 也可以被 server.py 或计划任务直接调用。
    """
    import pipeline as pipe

    state = load_state()
    now = datetime.now()
    state.setdefault("created_at", now.isoformat(timespec="seconds"))

    if not force and daily_cap_reached(state, max_per_day):
        if not quiet:
            print(f"[{now:%H:%M:%S}] 已达今日上限 {max_per_day} 次，跳过本次采集")
        state["last_skipped_at"] = now.isoformat(timespec="seconds")
        state["last_skipped_reason"] = f"daily cap {max_per_day}"
        _save(SCHEDULER_PATH, prune(state))
        return {"ok": False, "skipped": True, "reason": f"daily cap {max_per_day}"}

    if not quiet:
        print(f"[{now:%H:%M:%S}] 开始采集（geo={geo} hours={hours}）…")

    t0 = time.time()
    try:
        out = pipe.run_collect_only(geo=geo, hours=hours)
    except Exception as e:                                  # noqa: BLE001
        out = {"ok": False, "error": f"{type(e).__name__}: {e}",
               "traceback": traceback.format_exc()[-400:]}
    dt = round(time.time() - t0, 2)

    state["total_runs"] += 1
    state["today_runs"] += 1
    state["last_run"] = now.isoformat(timespec="seconds")
    state["interval_minutes"] = state.get("interval_minutes")

    if out.get("ok"):
        state["last_result"] = {
            "ok": True, "count": out["count"], "source": out["source"],
            "snapshot_count": out["snapshot_count"], "quality": out["quality"],
            "duration_s": dt,
        }
        state["hour_coverage"][now.strftime("%Y-%m-%dT%H")] = out["count"]
        state["history"].append({
            "at": now.strftime("%Y-%m-%dT%H:%M:%S"), "ok": True,
            "count": out["count"], "source": out["source"], "duration_s": dt,
        })
        if not quiet:
            print(f"[{now:%H:%M:%S}] 完成：{out['count']} 条，来源 {out['source']}，"
                  f"{dt}s，累计快照 {out['snapshot_count']} 期")
    else:
        state["total_failures"] += 1
        state["last_result"] = {"ok": False, "error": str(out.get("error"))[:200],
                                "duration_s": dt}
        state["history"].append({"at": now.strftime("%Y-%m-%dT%H:%M:%S"), "ok": False,
                                 "error": str(out.get("error"))[:160], "duration_s": dt})
        if not quiet:
            print(f"[{now:%H:%M:%S}] 失败：{str(out.get('error'))[:160]}")

    state["llm_calls"] = 0      # 采集路径不调 LLM，恒为 0
    _save(SCHEDULER_PATH, prune(state))
    return out


def run_scheduler(interval_minutes=60, geo="US", hours=24, max_per_day=30,
                  once=False, quiet=False, run_immediately=True, thread_name="scheduler"):
    interval_minutes = max(int(interval_minutes), MIN_INTERVAL_MINUTES)

    if once:
        out = collect_once(geo=geo, hours=hours, quiet=quiet, max_per_day=max_per_day)
        return 0 if out.get("ok") or out.get("skipped") else 1

    state = load_state()
    state["interval_minutes"] = interval_minutes
    _save(SCHEDULER_PATH, state)

    _STOP.clear()
    _THREAD.update({"interval": interval_minutes, "started_at": datetime.now().isoformat(timespec="seconds")})

    if not quiet:
        print("=" * 68)
        print(f"  定时采集已启动    间隔 {interval_minutes} 分钟   geo={geo} hours={hours}")
        print(f"  每日上限 {max_per_day} 次   LLM 调用 0 次/轮   token 消耗 0")
        print(f"  状态文件 {os.path.relpath(SCHEDULER_PATH, BASE_DIR)}")
        print("  Ctrl+C 停止")
        print("=" * 68)

    # 对齐到间隔边界（便于按小时做时间序列分析），并加抖动避免整点扎堆
    def next_delay():
        now = time.time()
        nxt = (int(now // (interval_minutes * 60)) + 1) * interval_minutes * 60
        base = max(nxt - now, 30)
        return base + random.uniform(0, 90)

    try:
        if run_immediately:
            collect_once(geo=geo, hours=hours, quiet=quiet, max_per_day=max_per_day)

        while not _STOP.is_set():
            delay = next_delay()
            nxt = datetime.fromtimestamp(time.time() + delay)
            st = load_state()
            st["next_run_at"] = nxt.isoformat(timespec="seconds")
            _save(SCHEDULER_PATH, st)
            if not quiet:
                print(f"\n下次采集：{nxt:%Y-%m-%d %H:%M:%S}（{delay/60:.1f} 分钟后）")

            # 可被 request_stop() 立刻打断的等待，不用干等到下一轮
            if _STOP.wait(delay):
                break
            collect_once(geo=geo, hours=hours, quiet=quiet, max_per_day=max_per_day)
        if not quiet:
            print("\n定时采集已停止。")
        return 0
    except KeyboardInterrupt:
        if not quiet:
            print("\n已停止。")
        return 0
    finally:
        _THREAD.update({"t": None, "interval": None})
        st = load_state()
        st["next_run_at"] = None
        _save(SCHEDULER_PATH, st)


def start_in_thread(interval_minutes=60, geo="US", hours=24, max_per_day=30):
    """在后台线程里启动调度循环（供 server.py 一键开启）。

    返回 (ok, message)。已在运行时不会重复启动。
    """
    if is_running():
        return False, "定时采集已在运行中"
    t = threading.Thread(target=run_scheduler,
                         kwargs={"interval_minutes": interval_minutes, "geo": geo,
                                 "hours": hours, "max_per_day": max_per_day,
                                 "quiet": True, "run_immediately": True},
                         daemon=True, name="scheduler")
    _THREAD["t"] = t
    t.start()
    return True, "定时采集已启动"


def print_status():
    s = load_state()
    print(json.dumps({
        "间隔(分钟)": s.get("interval_minutes"),
        "总运行次数": s.get("total_runs"),
        "总失败次数": s.get("total_failures"),
        "今日运行": s.get("today_runs"),
        "LLM 调用次数": s.get("llm_calls"),
        "最近运行": s.get("last_run"),
        "最近结果": s.get("last_result"),
        "下次运行": s.get("next_run_at"),
        "已覆盖小时数": len(s.get("hour_coverage") or {}),
    }, ensure_ascii=False, indent=2))


def reset_history(geo="US", hours=24, quiet=False):
    """把历史快照归档并重建基线。

    为什么需要它：开发/测试期间往往几分钟就连采多次，这些快照时间间隔不等且极短，
    会让 duration_hours / persistence 这类指标失去业务意义。
    正式启用定时采集前应该重建一次基线。

    安全做法：**归档而不是删除** —— 快照被移动到 data/snapshots/_archive_<时间戳>/，
    dim_trend.jsonl 同样改名保留。任何东西都没丢。
    """
    import shutil

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    snap_dir = os.path.join(DATA_DIR, "snapshots")
    archive = os.path.join(snap_dir, "_archive_" + stamp)
    moved = 0

    if os.path.isdir(snap_dir):
        files = [f for f in os.listdir(snap_dir) if f.endswith(".jsonl")]
        if files:
            os.makedirs(archive, exist_ok=True)
            for f in files:
                shutil.move(os.path.join(snap_dir, f), os.path.join(archive, f))
                moved += 1

    hist = os.path.join(DATA_DIR, "dim_trend.jsonl")
    if os.path.exists(hist):
        shutil.move(hist, hist + ".bak_" + stamp)

    # 重置调度器的覆盖统计
    st = load_state()
    st["hour_coverage"] = {}
    st["history"] = []
    st["total_runs"] = 0
    st["total_failures"] = 0
    st["today_runs"] = 0
    st["today"] = datetime.now().strftime("%Y-%m-%d")
    st["last_run"] = None
    st["last_result"] = None
    _save(SCHEDULER_PATH, st)

    if not quiet:
        print(f"已归档 {moved} 期历史快照 → {os.path.relpath(archive, BASE_DIR)}")
        print("dim_trend.jsonl 已保留为 .bak，计数已归零")

    # 立刻采一次，建立新基线
    out = collect_once(geo=geo, hours=hours, quiet=quiet, force=True)
    if not quiet:
        print("新基线已建立，之后按固定间隔累积即可得到有意义的生命周期指标。")
    return out


def main():
    ap = argparse.ArgumentParser(description="Google Trends 定时采集")
    ap.add_argument("--interval", type=int, default=60, help="间隔分钟数，最小 15")
    ap.add_argument("--geo", default="US")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--max-per-day", type=int, default=30)
    ap.add_argument("--once", action="store_true", help="只采一次（供系统计划任务调用）")
    ap.add_argument("--status", action="store_true", help="只查看状态")
    ap.add_argument("--reset-history", action="store_true",
                    help="归档历史快照并重建基线（测试数据攒歪了时用）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.status:
        print_status()
        return 0
    if args.reset_history:
        out = reset_history(geo=args.geo, hours=args.hours, quiet=args.quiet)
        return 0 if out.get("ok") else 1
    return run_scheduler(interval_minutes=args.interval, geo=args.geo, hours=args.hours,
                         max_per_day=args.max_per_day, once=args.once, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
