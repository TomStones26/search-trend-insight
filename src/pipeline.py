"""
流水线编排
=========
把「采集 → 清洗 → 打标 → 指标 → 需求分析 → 报告」串起来，并把进度实时写进
data/status.json，供分析过程页轮询。

设计要点：
  - 阶段之间只通过文件通信，任一阶段可单独重跑
  - 采集失败自动降级到最近可用快照（受限网络/代理环境下的必要能力），
    并在 state.json 明确标注 source="cached"，页面挂黄色告警而不是假装成功
  - status.json 采用「写临时文件 + 原子替换」，避免页面读到半截文件
  - 每一步都记录耗时，便于定位瓶颈
"""

import json
import os
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 数据目录默认是项目下的 data/。测试等场景可用 DATAPULSE_DATA_DIR 指到别处，
# 避免跑一次测试就把用户的真实分析结果、快照与历史覆盖掉。
DATA_DIR = os.environ.get("DATAPULSE_DATA_DIR") or os.path.join(BASE_DIR, "data")
SRC_DIR = os.path.join(BASE_DIR, "src")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
STATUS_PATH = os.path.join(DATA_DIR, "status.json")
# 定时采集写自己的状态文件，绝不覆盖用户手动分析留下的 status.json
SCHEDULE_STATE_PATH = os.path.join(DATA_DIR, "schedule_state.json")

sys.path.insert(0, SRC_DIR)
import analyze as ana       # noqa: E402
import gt_collect as gtc    # noqa: E402
import report as rpt        # noqa: E402
from classify import Taxonomy   # noqa: E402
from llm import LLM             # noqa: E402

STEPS = [
    ("collect", "采集数据"),
    ("clean", "清洗与质检"),
    ("label", "分类与品牌识别"),
    ("metrics", "指标计算"),
    ("analyze", "需求分析与多维计算"),
    ("report", "生成报告"),
]

_WRITE_LOCK = threading.Lock()


def _write_json_atomic(path, obj):
    """原子写 JSON。

    踩过的坑：如果临时文件名固定（path + '.tmp'），而 HTTP 服务线程正在高频读
    status.json，Windows 上 os.replace 会偶发 PermissionError，临时文件残留、
    状态永久卡在 running。所以这里做到三点：
      1) 临时文件名带上 pid + 线程 id，互不覆盖
      2) os.replace 失败时退避重试
      3) 整个写入用锁串行化
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with _WRITE_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        last = None
        for i in range(8):
            try:
                os.replace(tmp, path)
                return
            except OSError as e:                      # 目标被读取方短暂占用
                last = e
                time.sleep(0.04 * (i + 1))
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise last


def _cleanup_stale_tmp():
    """清掉历史遗留的 .tmp 文件（旧版本固定文件名留下的）。"""
    d = os.path.dirname(STATUS_PATH)
    if not os.path.isdir(d):
        return 0
    n = 0
    for f in os.listdir(d):
        if f.endswith(".tmp") and f.startswith("status.json"):
            try:
                os.remove(os.path.join(d, f))
                n += 1
            except OSError:
                pass
    return n


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_latest():
    return load_json(os.path.join(DATA_DIR, "latest.json"))


def load_history():
    rows = []
    p = os.path.join(DATA_DIR, "dim_trend.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    return rows


def snapshot_count():
    d = os.path.join(DATA_DIR, "snapshots")
    if not os.path.isdir(d):
        return 0
    return len([f for f in os.listdir(d) if f.endswith(".jsonl")])


# ------------------------------------------------------------------ 状态机
class RunState:
    def __init__(self, requirement="", run_id=None, path=None, kind="analysis"):
        # path 可覆盖写入位置：定时采集用独立状态文件，避免覆盖用户的分析进度
        self.path = path or STATUS_PATH
        self.data = {
            "kind": kind,
            "run_id": run_id or uuid.uuid4().hex[:12],
            "state": "running",
            "requirement": requirement,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "progress": 0,
            "current_step": None,
            "steps": [{"id": s[0], "label": s[1], "state": "pending",
                       "detail": "", "duration_ms": None} for s in STEPS],
            "logs": [],
            "source": None,
            "plan": None,
            "engine": None,
            "quality": None,
            "error": None,
            "artifacts": {},
            "save_error": None,
        }
        self._t0 = {}
        self.last_save_error = None

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.data["logs"].append({"t": ts, "msg": str(msg)})
        if len(self.data["logs"]) > 400:
            self.data["logs"] = self.data["logs"][-400:]
        self.save()

    def start(self, sid, detail=""):
        self._t0[sid] = time.time()
        self._step(sid).update({"state": "running", "detail": detail})
        self.data["current_step"] = sid
        self._progress()
        self.save()

    def done(self, sid, detail=""):
        st = self._step(sid)
        st.update({"state": "done", "detail": detail,
                   "duration_ms": int((time.time() - self._t0.get(sid, time.time())) * 1000)})
        self._progress()
        self.save()

    def fail(self, sid, detail=""):
        st = self._step(sid)
        st.update({"state": "failed", "detail": detail,
                   "duration_ms": int((time.time() - self._t0.get(sid, time.time())) * 1000)})
        self.save()

    def _step(self, sid):
        for s in self.data["steps"]:
            if s["id"] == sid:
                return s
        raise KeyError(sid)

    def _progress(self):
        done = sum(1 for s in self.data["steps"] if s["state"] in ("done", "skipped", "failed"))
        self.data["progress"] = int(done / len(self.data["steps"]) * 100)

    def finish(self, state="done", error=None):
        self.data["state"] = state
        self.data["error"] = error
        self.data["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.data["progress"] = 100 if state == "done" else self.data["progress"]
        self.data["current_step"] = None
        self.save()

    def save(self):
        """写状态文件。**永不抛异常** —— 进度遥测失败不应该搞死正在跑的任务。"""
        try:
            _write_json_atomic(self.path, self.data)
            self.last_save_error = None
            self.data["save_error"] = None
        except Exception as e:                          # noqa: BLE001
            self.last_save_error = "%s: %s" % (type(e).__name__, str(e)[:120])
            self.data["save_error"] = self.last_save_error


# ------------------------------------------------------------------ 默认计划
DEFAULT_REQUIREMENT = "今天美国互联网整体搜索热点概览，重点看增长最快的和值得跟进的信号"


def quality_report(payload, taxonomy):
    """清洗与质检：输出结构化质检结果与 0-1 质量分。"""
    trends = payload.get("trends") or []
    n = max(len(trends), 1)
    checks = []

    null_vol = sum(1 for t in trends if not t.get("search_volume"))
    null_growth = sum(1 for t in trends if not t.get("growth_rate"))
    checks.append({"name": "搜索量完整率", "state": "pass" if null_vol / n < 0.05 else "warn",
                   "detail": f"{n - null_vol}/{n} 条有搜索量"})
    checks.append({"name": "增长率完整率", "state": "pass" if null_growth / n < 0.05 else "warn",
                   "detail": f"{n - null_growth}/{n} 条有增长率"})

    ids = [t.get("trend_id") for t in trends]
    dups = len(ids) - len(set(ids))
    checks.append({"name": "主键唯一性", "state": "pass" if dups == 0 else "fail",
                   "detail": f"重复 {dups} 条"})

    other = sum(1 for t in trends if t.get("category") == "Other")
    other_rate = other / n
    checks.append({"name": "类别覆盖率", "state": "warn" if other_rate > 0.25 else "pass",
                   "detail": f"未归类 {other} 条（{other_rate*100:.1f}%）"})

    branded = sum(1 for t in trends if t.get("brand"))
    checks.append({"name": "品牌识别", "state": "pass" if branded else "warn",
                   "detail": f"识别出 {branded} 条带品牌的热点"})

    snaps = snapshot_count()
    checks.append({"name": "历史期数", "state": "pass" if snaps >= 2 else "warn",
                   "detail": f"已有 {snaps} 期快照" +
                             ("（足以计算生命周期）" if snaps >= 2 else "（生命周期指标不可用）")})

    weights = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
    score = round(sum(weights[c["state"]] for c in checks) / len(checks), 2)
    return {"score": score, "checks": checks,
            "row_count": len(trends), "snapshot_count": snaps}


# ------------------------------------------------------------------ 主流程
def collect_and_prepare(st, tax, geo="US", hours=24, llm=None,
                        write_dashboard=True, default_plan_req=None):
    """阶段 1-4：采集 → 清洗 → 打标 → 指标。

    **全程不调用 LLM** —— 类别与品牌由本地词典（config/taxonomy.json）判定，
    需求解析也传 llm=None 走规则。所以这条路径的 token 消耗恒为 0，
    可以按小时高频运行而不会产生任何 API 费用。

    返回 (payload, dash, quality, source, history)
    """
    llm_status = llm.status() if llm is not None else {"available": False, "mode": "rule",
                                                       "model": None, "calls": 0,
                                                       "last_error": None}

    # ---------------- 1 采集
    st.start("collect")
    st.log(f"采集阶段开始：数据源 Google Trends Trending Now ({geo})")
    source = "live"
    try:
        payload = gtc.collect(geo=geo, hours=hours, log=st.log)
    except Exception as e:                                  # noqa: BLE001
        st.log(f"线上采集失败：{type(e).__name__}: {str(e)[:150]}")
        st.log("降级：改用本地最近一次快照，页面会标注为缓存数据")
        payload = load_latest()
        if not payload:
            raise RuntimeError("线上采集失败且本地无可用快照") from e
        source = "cached"
    st.data["source"] = source
    st.done("collect", f"{payload['count']} 条热点（{'实时' if source=='live' else '缓存'}）")
    st.log(f"采集完成：{payload['count']} 条")

    # ---------------- 2 清洗与质检
    st.start("clean")
    q = quality_report(payload, tax)
    st.data["quality"] = q
    st.done("clean", f"质量分 {q['score']}，{len(q['checks'])} 项检查")
    st.log(f"质检完成：质量分 {q['score']}（{q['row_count']} 行，{q['snapshot_count']} 期快照）")
    _write_json_atomic(os.path.join(DATA_DIR, "quality.json"), q)

    # ---------------- 3 打标
    st.start("label")
    labeled = tax.label_records(payload["trends"], inplace=True)
    cats = {}
    for t in labeled:
        cats[t["category"]] = cats.get(t["category"], 0) + 1
    st.done("label", f"词典 v{tax.version}，覆盖 {len(cats)} 类")
    st.log("类别分布：" + json.dumps(dict(sorted(cats.items(), key=lambda x: -x[1])),
                                    ensure_ascii=False))

    # ---------------- 4 指标计算（常态看板）
    st.start("metrics")
    history = load_history()
    dash_plan = ana.parse_intent(default_plan_req or DEFAULT_REQUIREMENT, tax, llm=None)
    dash = ana.run_analysis(dash_plan, payload, tax, history=history, log=st.log)
    dash["meta"] = {"source": source,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "quality": q, "engine": llm_status, "scope": "all"}
    if write_dashboard:
        _write_json_atomic(os.path.join(DATA_DIR, "dashboard.json"), dash)
        st.data["artifacts"]["dashboard"] = "data/dashboard.json"
    st.done("metrics", f"KPI {len(dash['kpi'])} 项，图表数据 {len(dash['cat_rows'])} 组")
    return payload, dash, q, source, history


def run_pipeline(requirement="", progress_path=STATUS_PATH, quiet=False, state=None):
    requirement = (requirement or "").strip() or DEFAULT_REQUIREMENT
    # state 可由调用方预先创建并落盘，避免「已启动但状态文件仍是上一轮」的竞态
    st = state if state is not None else RunState(requirement)
    st.data["requirement"] = requirement
    st.data["state"] = "running"
    st.save()
    tax = Taxonomy()
    llm = LLM()
    st.data["engine"] = {"llm": llm.status(), "intent": None}
    st.save()

    try:
        # ---------------- 1-4 采集 / 清洗 / 打标 / 指标（本地完成，零 token）
        payload, dash, q, source, history = collect_and_prepare(st, tax, llm=llm)

        # ---------------- 5 需求分析
        st.start("analyze", "解析需求…")
        plan = ana.parse_intent(requirement, tax, llm=llm)
        st.data["engine"]["intent"] = plan.get("engine")
        st.data["plan"] = plan
        st.save()
        st.log(f"需求解析引擎：{plan.get('engine')}｜理解结果：{plan.get('intent_summary')}")
        if plan.get("engine") == "rule" and not llm.available:
            st.log("未配置 LLM 密钥，需求理解走本地规则解析器（结果同样可用，"
                   "但复杂需求建议配置密钥）")
        res = ana.run_analysis(plan, payload, tax, history=history, log=st.log)
        res["meta"] = {"source": source, "generated_at": datetime.now().isoformat(timespec="seconds"),
                       "quality": q, "engine": llm.status(), "scope": "requirement"}
        _write_json_atomic(os.path.join(DATA_DIR, "analysis.json"), res)
        st.data["artifacts"]["analysis"] = "data/analysis.json"
        st.done("analyze", f"筛选后 {res['scope']['selected']} 条，"
                           f"{len(res['findings'])} 条结论")

        # ---------------- 6 报告
        st.start("report")
        rep = rpt.build_report(res, llm=llm, log=st.log)
        _write_json_atomic(os.path.join(DATA_DIR, "report.json"), rep)
        os.makedirs(REPORT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        _write_json_atomic(os.path.join(REPORT_DIR, f"report_{stamp}.json"), rep)
        st.data["artifacts"]["report"] = "data/report.json"
        st.data["artifacts"]["report_archive"] = f"data/reports/report_{stamp}.json"
        st.data["engine"]["narrative"] = rep["meta"]["engine"]["narrative"]
        st.done("report", f"{len(rep['sections'])} 个章节，"
                          f"叙述引擎 {rep['meta']['engine']['narrative']}")
        st.log("报告已生成")

        # 刷新 LLM 的真实状态：配了密钥不代表调用成功。
        # 只在开跑前快照一次的话，运行中调用失败时 UI 会一直显示「LLM 正常」。
        st.data["engine"]["llm"] = llm.status()
        if llm.available and llm.last_error:
            st.log(f"LLM 调用异常，已降级为本地规则：{llm.last_error[:160]}")
        elif llm.available:
            st.log(f"LLM 调用成功 {llm.calls} 次（模型 {llm.model}）")
        st.save()

        st.finish("done")
        return {"ok": True, "run_id": st.data["run_id"], "status": st.data}

    except Exception as e:                                  # noqa: BLE001
        tb = traceback.format_exc()
        st.log("流水线失败：" + str(e)[:300])
        st.log(tb.splitlines()[-1] if tb else "")
        for s in st.data["steps"]:
            if s["state"] in ("running", "pending"):
                st.fail(s["id"], "上游失败，未执行")
        st.finish("failed", f"{type(e).__name__}: {e}")
        return {"ok": False, "run_id": st.data["run_id"], "error": str(e), "status": st.data}


def run_collect_only(geo="US", hours=24, state_path=SCHEDULE_STATE_PATH,
                     requirement=None, kind="collect", state=None):
    """只跑到阶段 4（采集/清洗/打标/指标），**不碰 LLM、不产出分析报告**。

    这是定时采集的专用入口：它的唯一目的是**攒历史快照**，
    供生命周期、每小时新增分布、类别趋势曲线这三类分析用。
    因为完全不经过模型，token 消耗恒为 0，可以按小时长期运行。

    对比 run_pipeline()：后者会额外跑需求解析与报告叙述，那两步才调 LLM。

    state 让 server.py 能复用「已同步落盘」的状态对象，
    沿用与完整分析相同的防竞态写法。
    """
    st = state or RunState(requirement or "（数据刷新，未跑需求分析）",
                           path=state_path, kind=kind)
    st.data["mode"] = "collect_only"
    # 后两步在本次运行中不参与，标记为跳过，进度条才能正常走完
    for s in st.data["steps"]:
        if s["id"] in ("analyze", "report"):
            s["state"] = "skipped"
            s["detail"] = "定时采集不产出分析报告"
    st.save()
    tax = Taxonomy()
    try:
        payload, dash, q, source, history = collect_and_prepare(
            st, tax, geo=geo, hours=hours, llm=None)
        st.data["artifacts"]["latest"] = "data/latest.json"
        st.data["dataset"] = {
            "count": payload["count"],
            "snapshot_count": q["snapshot_count"],
            "tracked": len(history),
            "consumer": sum(1 for t in payload["trends"] if t.get("is_consumer")),
        }
        st.finish("done")
        return {"ok": True, "count": payload["count"], "source": source,
                "snapshot_count": q["snapshot_count"], "quality": q["score"],
                "status": st.data}
    except Exception as e:                                  # noqa: BLE001
        st.log("定时采集失败：" + str(e)[:300])
        for s in st.data["steps"]:
            if s["state"] in ("running", "pending"):
                st.fail(s["id"], "上游失败，未执行")
        st.finish("failed", f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e), "status": st.data}


if __name__ == "__main__":
    req = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    out = run_pipeline(req)
    print(json.dumps({"ok": out["ok"],
                      "state": out["status"]["state"],
                      "source": out["status"]["source"],
                      "steps": [(s["id"], s["state"], s["detail"])
                                for s in out["status"]["steps"]]},
                     ensure_ascii=False, indent=2))
