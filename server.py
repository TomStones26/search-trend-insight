"""
本地服务（零第三方依赖）
======================
浏览器跑不了爬虫、也调不了 LLM，所以执行层放在这个服务里，页面只负责展示。

启动：
    python server.py                 # 默认 http://127.0.0.1:8848
    python server.py --port 9000
    python server.py --open          # 启动后自动打开浏览器

接口：
    GET  /api/meta        数据集与引擎元信息
    GET  /api/status      流水线实时进度（分析过程页每秒轮询）
    GET  /api/dashboard   常态看板数据
    GET  /api/analysis    本次需求的分析结果
    GET  /api/report      分析报告
    GET  /api/quality     数据质检报告
    POST /api/collect     {"requirement": ""}   刷新数据（不跑需求分析）
    POST /api/analyze     {"requirement": "..."} 跑完整分析
"""

import argparse
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
DATA_DIR = os.path.join(BASE_DIR, "data")
sys.path.insert(0, SRC_DIR)

import pipeline  # noqa: E402

PAGES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/analysis": "analysis.html",
    "/analysis.html": "analysis.html",
    "/dashboard": "dashboard.html",
    "/dashboard.html": "dashboard.html",
    "/report": "report.html",
    "/report.html": "report.html",
}

API_FILES = {
    "/api/status": "status.json",
    "/api/dashboard": "dashboard.json",
    "/api/analysis": "analysis.json",
    "/api/report": "report.json",
    "/api/quality": "quality.json",
    "/api/dataset": "latest.json",
    "/api/schedule": "scheduler.json",
    "/api/schedule_state": "schedule_state.json",
}

_RUN_LOCK = threading.Lock()
_RUNNING = {"active": False}


def _read(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build_meta():
    st = _read(os.path.join(DATA_DIR, "status.json"), {}) or {}
    latest = pipeline.load_latest() or {}
    taxonomy = _read(os.path.join(BASE_DIR, "config", "taxonomy.json"), {}) or {}
    src = _read(os.path.join(BASE_DIR, "config", "sources", "google-trends-us.json"), {}) or {}
    snaps = pipeline.snapshot_count()
    try:
        from llm import LLM
        llm_status = LLM().status()
    except Exception:
        llm_status = {"available": False, "mode": "rule"}
    try:
        import scheduler as sch
        sched_state = _read(os.path.join(DATA_DIR, "scheduler.json"), {}) or {}
        schedule = {
            "running": sch.is_running(),
            "interval_minutes": (sch.thread_info().get("interval_minutes")
                                 or sched_state.get("interval_minutes")),
            "thread_started_at": sch.thread_info().get("started_at"),
            "total_runs": sched_state.get("total_runs"),
            "total_failures": sched_state.get("total_failures"),
            "today_runs": sched_state.get("today_runs"),
            "llm_calls": sched_state.get("llm_calls"),
            "last_run": sched_state.get("last_run"),
            "last_result": sched_state.get("last_result"),
            "next_run_at": sched_state.get("next_run_at"),
            "hours_covered": len(sched_state.get("hour_coverage") or {}),
        }
    except Exception:
        schedule = {"running": False}
    return {
        "product": "DataPulse · Google Trends US 实时热点分析",
        "data_source": {
            "name": src.get("name"),
            "entry": src.get("entry"),
            "geo": latest.get("geo"),
            "window_hours": latest.get("window_hours"),
        },
        "dataset": {
            "count": latest.get("count"),
            "snapshot_time": latest.get("snapshot_time"),
            "snapshot_count": snaps,
            "last_source": st.get("source"),
            "category_count": len(taxonomy.get("categories", {})),
            "taxonomy_version": taxonomy.get("version"),
            "brand_count": len(taxonomy.get("brands", [])),
        },
        "engine": {"llm": llm_status,
                   "intent": (st.get("engine") or {}).get("intent"),
                   "narrative": (st.get("engine") or {}).get("narrative")},
        "last_run": {"run_id": st.get("run_id"), "state": st.get("state"),
                     "kind": st.get("kind") or "analysis",
                     "started_at": st.get("started_at"),
                     "finished_at": st.get("finished_at"),
                     "requirement": st.get("requirement")},
        "schedule": schedule,
        "caveats": [
            "搜索量与增长率均为 Google 提供的离散桶值，非精确值",
            "类别与品牌由本地词典规则标注，源站不提供这两个字段",
            "生命周期类指标需要多期快照累积",
        ],
    }


def start_run(requirement, mode="full"):
    """后台跑流水线。同一时间只允许一个任务。

    关键：状态文件必须在返回「已启动」之前就同步落盘，
    否则前端轮询会先读到上一轮遗留的完成态，出现「点完就是 100%」的假象。

    mode="full"    跑完整六阶段（含需求解析与报告）—— 会调 LLM
    mode="collect" 只跑到指标阶段（刷新数据 + 重建看板）—— 不调 LLM，零 token
    """
    with _RUN_LOCK:
        if _RUNNING["active"]:
            return False, "已有任务在运行中"
        _RUNNING["active"] = True
        # 刷新数据时保留上一次的真实需求文本，避免主页「最近需求」被覆盖成占位符
        prev_req = (_read(os.path.join(DATA_DIR, "status.json"), {}) or {}).get("requirement") or ""
        state = pipeline.RunState(requirement or "",
                                  kind="analysis" if mode == "full" else "collect")
        if mode != "full":
            for s in state.data["steps"]:
                if s["id"] in ("analyze", "report"):
                    s["state"] = "skipped"
                    s["detail"] = "数据刷新不产出分析报告"
            state.data["requirement"] = prev_req
        state.save()

    def _worker():
        try:
            if mode == "full":
                pipeline.run_pipeline(requirement or "", state=state)
            else:
                pipeline.run_collect_only(state_path=pipeline.STATUS_PATH,
                                          requirement=prev_req, kind="collect",
                                          state=state)
        except BaseException as e:                          # noqa: BLE001
            # 连 BaseException 都兜住：绝不能让线程静默死掉、状态永久卡在 running
            try:
                state.log("后台任务异常终止：" + type(e).__name__ + ": " + str(e)[:200])
                state.finish("failed", "%s: %s" % (type(e).__name__, e))
            except Exception:
                pass
        finally:
            _RUNNING["active"] = False

    threading.Thread(target=_worker, daemon=True, name="pipeline").start()

    # 看门狗：超时仍未结束就明确标记失败，而不是让页面无限转圈
    def _watchdog():
        limit = 600
        while limit > 0:
            time.sleep(5)
            limit -= 5
            if not _RUNNING["active"]:
                return
        if _RUNNING["active"]:
            try:
                state.log("任务超过 %d 秒未结束，已标记为失败" % 600)
                state.finish("failed", "任务超时（超过 %d 秒）" % 600)
            except Exception:
                pass
            _RUNNING["active"] = False

    threading.Thread(target=_watchdog, daemon=True, name="watchdog").start()
    return True, "已启动"


class Handler(BaseHTTPRequestHandler):
    server_version = "DataPulse/1.0"

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/status"):
            return                                          # 轮询日志太吵
        sys.stderr.write("  %s %s\n" % (self.command, self.path))

    # -------------------------------------------------------------- 工具
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _send_file(self, path):
        if not os.path.isfile(path):
            return self._send(404, "404 Not Found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _safe_join(self, base, rel):
        p = os.path.normpath(os.path.join(base, rel.lstrip("/\\")))
        return p if p.startswith(os.path.normpath(base)) else None

    # -------------------------------------------------------------- GET
    def do_GET(self):
        path = urlparse(self.path).path

        if path in API_FILES:
            data = _read(os.path.join(DATA_DIR, API_FILES[path]))
            if data is None:
                return self._send_json({"error": "not_ready", "path": path}, 404)
            return self._send_json(data)

        if path == "/api/meta":
            return self._send_json(build_meta())

        if path in PAGES:
            return self._send_file(os.path.join(BASE_DIR, PAGES[path]))

        if path.startswith("/assets/"):
            p = self._safe_join(BASE_DIR, path)
            return self._send_file(p) if p else self._send(403, "403", "text/plain")

        p = self._safe_join(BASE_DIR, path)
        if p and os.path.isfile(p):
            return self._send_file(p)
        self._send(404, "404 Not Found", "text/plain; charset=utf-8")

    # -------------------------------------------------------------- POST
    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
        try:
            body = json.loads(raw or "{}")
        except Exception:
            body = {}

        if path == "/api/collect":
            ok, msg = start_run("", mode="collect")
            return self._send_json({"ok": ok, "message": msg, "tokens": 0,
                                    "hint": "刷新数据不含需求分析，不消耗 token；进度见 /api/status"},
                                  200 if ok else 409)
        if path == "/api/analyze":
            req = (body.get("requirement") or "").strip()
            if not req:
                return self._send_json({"ok": False, "message": "需求不能为空"}, 400)
            ok, msg = start_run(req, mode="full")
            return self._send_json({"ok": ok, "message": msg}, 200 if ok else 409)

        # ------------------------------------------------------ 定时采集开关
        if path == "/api/schedule/start":
            try:
                import scheduler
                interval = int(body.get("interval_minutes") or 60)
            except Exception:
                return self._send_json({"ok": False, "message": "参数不合法"}, 400)
            ok, msg = scheduler.start_in_thread(interval_minutes=interval)
            return self._send_json({"ok": ok, "message": msg,
                                    "interval_minutes": max(interval, scheduler.MIN_INTERVAL_MINUTES)},
                                   200 if ok else 409)
        if path == "/api/schedule/stop":
            import scheduler
            scheduler.request_stop()
            return self._send_json({"ok": True, "message": "已请求停止，当前这一轮采集会正常跑完"})
        if path == "/api/schedule/run-once":
            import scheduler
            out = scheduler.collect_once(quiet=True)
            return self._send_json({"ok": bool(out.get("ok")), "result": {
                k: out.get(k) for k in ("count", "source", "snapshot_count", "quality", "error")
            }}, 200 if out.get("ok") else 500)

        self._send(404, "404 Not Found", "text/plain; charset=utf-8")


def _port_in_use(host, port):
    """端口上是否已经有服务在监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


class Server(ThreadingHTTPServer):
    """Windows 上 SO_REUSEADDR 允许两个进程绑同一端口，第二个实例能"启动成功"，
    但请求会随机落到先启动的旧进程上 —— 你以为在跑新代码，其实在跑旧的，
    排查起来极其迷惑（本地实测踩到过）。这里显式关掉：
    宁可启动时报错退出，也不要出现两个实例抢同一个端口。
    """
    allow_reuse_address = False
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = ap.parse_args()

    url = f"http://{args.host}:{args.port}/"
    n = pipeline._cleanup_stale_tmp()
    if n:
        print(f"  已清理 {n} 个遗留的临时状态文件")
    if _port_in_use(args.host, args.port):
        print("=" * 62)
        print(f"  端口 {args.port} 上已经有一个 DataPulse 在跑了。")
        print(f"  直接用就行：{url}")
        print("  想另起一个请换端口：python server.py --port 9000")
        print("=" * 62)
        return 1

    srv = Server((args.host, args.port), Handler)
    print("=" * 62)
    print("  DataPulse · Google Trends US 实时热点分析")
    print("=" * 62)
    print(f"  服务已启动：{url}")
    print("  页面：/ 主页  /analysis 分析过程  /dashboard 看板  /report 报告")
    print("  停止：Ctrl+C")
    print("=" * 62)
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
