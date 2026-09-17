"""真实端到端验证：用真密钥跑一次完整分析，检查模型输出质量。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8848"
REQ = "最近美国有什么科技和消费领域的高增长热点？哪些品牌在爆发？"
out = []


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


out.append("REQ=" + REQ)
try:
    out.append("POST /api/analyze -> " + json.dumps(post("/api/analyze",
                                                         {"requirement": REQ}),
                                                    ensure_ascii=False))
except Exception as e:  # noqa: BLE001
    out.append(f"POST_FAIL {type(e).__name__}: {e}")

t0 = time.time()
last = None
while time.time() - t0 < 180:
    st = get("/api/status")
    cur = (st.get("state"), st.get("percent"))
    if cur != last:
        out.append(f"  [{time.time()-t0:5.1f}s] state={cur[0]} pct={cur[1]} "
                   f"stage={st.get('stage')} msg={st.get('message')}")
        last = cur
    if st.get("state") in ("done", "error"):
        break
    time.sleep(1)

st = get("/api/status")
out.append("FINAL_STATE=" + str(st.get("state")))
out.append("LLM=" + json.dumps((st.get("engine") or {}).get("llm"), ensure_ascii=False))
out.append("STAGES=" + json.dumps(st.get("stages"), ensure_ascii=False)[:900])

an = get("/api/analysis")
plan = an.get("plan") or {}
out.append("PLAN_ENGINE=" + str(plan.get("engine")))
out.append("INTENT_SUMMARY=" + str(plan.get("intent_summary")))
out.append("FILTERS=" + json.dumps(plan.get("filters"), ensure_ascii=False))
out.append("FOCUS_Q=" + json.dumps(plan.get("focus_questions"), ensure_ascii=False))
out.append("SELECTED=" + str((an.get("scope") or {}).get("selected")))
out.append("FINDINGS_N=" + str(len(an.get("findings") or [])))

rp = get("/api/report")
out.append("NARRATIVE_ENGINE=" + str(((rp.get("meta") or {}).get("engine") or {}).get("narrative")))
out.append("EXEC=" + json.dumps(rp.get("executive_summary"), ensure_ascii=False))
out.append("RECS=" + json.dumps(rp.get("recommendations"), ensure_ascii=False))
out.append("SECTIONS=" + json.dumps([s.get("title") for s in (rp.get("sections") or [])],
                                    ensure_ascii=False))

with open(r"C:\Users\ynwas\WorkBuddy\2026-09-16-17-05-18\data-product-kit\_e2e.txt",
          "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("done")
