#!/usr/bin/env python3
"""
LLM 通道集成测试 —— 全程不需要 API 密钥
==========================================
目的：把 src/llm.py 那条「写了但没密钥、验不了」的路径真正跑一遍。

做法：在本地起一个 mock 的 OpenAI 兼容端点，把 OPENAI_BASE_URL 指过去，
      然后调用真实的 pipeline.run_pipeline()，端到端检查表现。

它验证的是「代码路径」，不是「某个模型的回答质量」——后者必须有真密钥。

全程不碰项目的 data/ 目录：所有产物写在 tests/_tmpdata/，跑完可以随意删。

用法：
    python tests/test_llm_path.py

场景：
    01 ok        正常返回             -> engine=llm，plan 生效，报告叙述走 LLM
    02 fenced    被 ```json 围栏包裹   -> 宽松解析生效，仍为 llm
    03 garbage   返回非 JSON 文本       -> 静默降级 rule，流水线不中断
    04 http500   上游 500              -> 静默降级 rule，记录 last_error
    05 timeout   上游不响应            -> 静默降级 rule，记录 last_error
    06 no_rf     服务商不支持 response_format（400）-> 自动去掉该参数重试，仍为 llm
    07 starved   推理型模型思维链吃光预算（正文空 + finish_reason=length）
                                      -> 自动放大 max_tokens 重试，仍为 llm
    08 empty     上游持续返回空正文      -> 降级 rule，且 last_error 必须写明原因
"""

import json
import os
import shutil
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 关键：测试会真实跑完整流水线，如果不隔离，就会把用户辛苦跑出来的
# data/analysis.json、data/report.json，以及快照与历史全部覆盖掉。
# 这里在 import pipeline 之前把数据目录改到 tests/_tmpdata/。
TMP_DATA_DIR = os.path.join(BASE_DIR, "tests", "_tmpdata")
os.environ["DATAPULSE_DATA_DIR"] = TMP_DATA_DIR

SRC_DIR = os.path.join(BASE_DIR, "src")
DATA_DIR = TMP_DATA_DIR
sys.path.insert(0, SRC_DIR)

# ---------------------------------------------------------------- mock 响应体
# 故意选一组「规则解析器不可能得出」的结果，用来证明 LLM 真的生效了：
# 需求文本里不含任何类别关键词，类别完全由 mock 决定。
REQ_TEXT = "帮我盯一下这波热度里哪些方向最值得下注"

INTENT_PAYLOAD = {
    "intent_summary": "【MOCK】聚焦科技与消费两个板块，筛选增长率 500% 以上的高动能热点",
    "filters": {"categories": ["Technology", "Consumer"], "min_growth": 500,
                "min_volume": None, "brand_only": False, "keyword": None},
    "focus": ["growth", "commerce"],
    "dimensions": ["category", "brand", "quadrant"],
    "top_n": 8,
    "focus_questions": [
        "【MOCK】科技板块里增长最快的是哪几个热点？",
        "【MOCK】哪些品牌出现了突然的搜索爆发？",
        "【MOCK】这波热度里有没有值得下注的长周期信号？",
    ],
}

SUMMARY_PAYLOAD = {
    "executive_summary": ["【MOCK】总述第一句。", "【MOCK】总述第二句。", "【MOCK】总述第三句。"],
    "recommendations": ["【MOCK】建议一。", "【MOCK】建议二。", "【MOCK】建议三。"],
}

MODE = {"v": "ok"}
REQUESTS = []
# 「starved」模式专用：每个 prompt 第一次只回思维链，之后才回正文，
# 用来模拟推理型模型（deepseek-flash）预算被思考吃光的情况。
SEEN = {}


# ---------------------------------------------------------------- mock 服务端
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):        # 静音，别把测试输出淹掉
        pass

    def handle_one_request(self):
        # 超时场景里客户端会主动断开，这里不该刷 traceback
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}

        REQUESTS.append({
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "model": body.get("model"),
            "response_format": body.get("response_format"),
            "max_tokens": body.get("max_tokens"),
            "messages": body.get("messages") or [],
        })

        mode = MODE["v"]
        if mode == "http500":
            self._send(500, {"error": {"message": "mock upstream failure"}})
            return
        if mode == "no_response_format":
            # 模拟「不支持 response_format 的 OpenAI 兼容服务商」：
            # 带该参数就 400，去掉就正常返回。验证客户端是否会自动重试。
            if body.get("response_format"):
                self._send(400, {"error": {"message":
                                           "Invalid parameter: response_format is not supported "
                                           "by this model"}})
                return
        if mode == "timeout":
            # 故意拖过客户端超时，模拟上游不响应
            time.sleep(5)
            self._send(200, self._envelope("{}"))
            return

        system = "".join(m.get("content") or "" for m in (body.get("messages") or [])
                         if m.get("role") == "system")
        if "executive_summary" in system:
            payload = SUMMARY_PAYLOAD
        elif "intent_summary" in system:
            payload = INTENT_PAYLOAD
        else:
            payload = {}

        if mode == "garbage":
            content = "抱歉，我无法完成这个请求。"
        else:
            content = json.dumps(payload, ensure_ascii=False)
            if mode == "fenced":
                content = "```json\n" + content + "\n```"

        # 07/08：模拟推理型模型「思维链占满 max_tokens、正文为空」。
        # 这正是 deepseek-flash 在预算过小时的真实表现：content="",
        # reasoning_content 有内容，finish_reason="length"。
        if mode == "starved":
            sig = system[:60]
            SEEN[sig] = SEEN.get(sig, 0) + 1
            if SEEN[sig] == 1:
                self._send(200, self._envelope("", "length", reasoning="让我想一下用户想要什么…"))
                return
        if mode == "always_empty":
            self._send(200, self._envelope("", "length", reasoning="让我想一下…"))
            return

        self._send(200, self._envelope(content))

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass          # 客户端已超时断开，属预期

    @staticmethod
    def _envelope(content, finish="stop", reasoning=None):
        msg = {"role": "assistant", "content": content}
        if reasoning:
            msg["reasoning_content"] = reasoning
        return {
            "id": "chatcmpl-mock", "object": "chat.completion", "model": "mock-gpt",
            "choices": [{"index": 0, "finish_reason": finish, "message": msg}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_mock():
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}/v1"


def read_json(name):
    p = os.path.join(DATA_DIR, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 场景执行
def run_scenario(label, mode, timeout=3):
    MODE["v"] = mode
    REQUESTS.clear()
    SEEN.clear()

    os.environ["OPENAI_API_KEY"] = "mock-key"
    os.environ["OPENAI_BASE_URL"] = BASE_URL
    os.environ["OPENAI_MODEL"] = "mock-gpt"
    os.environ["OPENAI_TIMEOUT_SECONDS"] = str(timeout)

    # 环境变量在 LLM() 构造时读取，而 pipeline 内部才 new，所以无需重载模块
    import pipeline as pipe
    out = pipe.run_pipeline(REQ_TEXT, quiet=True)

    status = out["status"]
    analysis = read_json("analysis.json") or {}
    report = read_json("report.json") or {}

    return {
        "label": label,
        "state": status.get("state"),
        "intent_engine": (analysis.get("plan") or {}).get("engine"),
        "narrative_engine": (report.get("meta") or {}).get("engine", {}).get("narrative"),
        "intent_summary": (analysis.get("plan") or {}).get("intent_summary"),
        "categories": ((analysis.get("plan") or {}).get("filters") or {}).get("categories"),
        "min_growth": ((analysis.get("plan") or {}).get("filters") or {}).get("min_growth"),
        "selected": (analysis.get("scope") or {}).get("selected"),
        "llm": (status.get("engine") or {}).get("llm") or {},
        "exec_summary": report.get("executive_summary") or [],
        "calls": len(REQUESTS),
        # 快照本场景的请求，避免被后续场景的 REQUESTS.clear() 覆盖
        "requests": [dict(q) for q in REQUESTS],
        "source": status.get("source"),
    }


def main():
    global BASE_URL

    # 安全闸：隔离没生效就立刻中止。宁可测试不跑，也不能覆盖用户的真实数据。
    import pipeline as _p
    if os.path.abspath(_p.DATA_DIR) != os.path.abspath(TMP_DATA_DIR):
        print("⚠ 数据目录隔离失败，已中止测试以免覆盖真实数据。")
        print(f"  期望：{TMP_DATA_DIR}")
        print(f"  实际：{_p.DATA_DIR}")
        return 3

    # 每轮从干净的临时目录开始
    if os.path.isdir(TMP_DATA_DIR):
        shutil.rmtree(TMP_DATA_DIR, ignore_errors=True)
    os.makedirs(TMP_DATA_DIR, exist_ok=True)
    print(f"测试数据隔离目录：{TMP_DATA_DIR}")

    srv, BASE_URL = start_mock()
    print(f"mock OpenAI 兼容端点已启动：{BASE_URL}\n")

    scenarios = [
        ("01 正常返回", "ok"),
        ("02 ```json 围栏", "fenced"),
        ("03 非 JSON 垃圾", "garbage"),
        ("04 上游 500", "http500"),
        ("05 响应超时", "timeout"),
        ("06 response_format 被拒", "no_response_format"),
        ("07 思维链吃光预算", "starved"),
        ("08 持续空正文", "always_empty"),
    ]
    results = []
    for label, mode in scenarios:
        t0 = time.time()
        r = run_scenario(label, mode)
        r["elapsed"] = round(time.time() - t0, 1)
        results.append(r)
        print(f"  {label:<18} state={r['state']:<8} intent={str(r['intent_engine']):<5} "
              f"narrative={str(r['narrative_engine']):<5} "
              f"llm调用={r['calls']:<2} err={(r['llm'].get('last_error') or '-')[:34]:<36} "
              f"{r['elapsed']}s")

    print("\n" + "=" * 96)
    checks = []

    def chk(name, cond, got=""):
        checks.append((name, bool(cond), got))

    r1 = results[0]
    chk("01 engine=llm（需求理解走模型）", r1["intent_engine"] == "llm", r1["intent_engine"])
    chk("01 类别来自模型而非规则", r1["categories"] == ["Technology", "Consumer"], r1["categories"])
    chk("01 min_growth 过滤生效", r1["min_growth"] == 500, r1["min_growth"])
    chk("01 报告叙述走 LLM", r1["narrative_engine"] == "llm", r1["narrative_engine"])
    chk("01 报告正文用模型原文", r1["exec_summary"] and r1["exec_summary"][0].startswith("【MOCK】"),
        (r1["exec_summary"] or [""])[0][:24])
    chk("01 mock 收到 2 次调用（意图+叙述）", r1["calls"] == 2, r1["calls"])
    q1 = r1["requests"]
    chk("01 请求头带 Bearer 密钥", bool(q1) and all(q["auth"] == "Bearer mock-key" for q in q1),
        q1[0]["auth"] if q1 else None)
    chk("01 请求体为 json_object 模式", bool(q1) and all(q["response_format"] == {"type": "json_object"} for q in q1),
        q1[0]["response_format"] if q1 else None)
    chk("01 model 参数透传", bool(q1) and all(q["model"] == "mock-gpt" for q in q1),
        q1[0]["model"] if q1 else None)
    chk("01 端点路径正确 (/v1/chat/completions)",
        bool(q1) and all(q["path"] == "/v1/chat/completions" for q in q1),
        q1[0]["path"] if q1 else None)
    cats_in_prompt = "Technology" in json.dumps(
        [q["messages"] for q in q1 if any("intent_summary" in (m.get("content") or "")
                                          for m in q["messages"])], ensure_ascii=False)
    chk("01 可用类别清单已注入 prompt", cats_in_prompt)

    chk("02 围栏 JSON 被宽松解析", results[1]["intent_engine"] == "llm", results[1]["intent_engine"])
    chk("02 叙述仍为 llm", results[1]["narrative_engine"] == "llm", results[1]["narrative_engine"])

    chk("03 垃圾返回降级 rule", results[2]["intent_engine"] == "rule", results[2]["intent_engine"])
    chk("03 流水线未中断 (done)", results[2]["state"] == "done", results[2]["state"])
    chk("03 报告降级为模板", results[2]["narrative_engine"] == "rule", results[2]["narrative_engine"])

    chk("04 HTTP 500 降级 rule", results[3]["intent_engine"] == "rule", results[3]["intent_engine"])
    chk("04 流水线未中断 (done)", results[3]["state"] == "done", results[3]["state"])
    chk("04 last_error 已记录 500", "500" in str(results[3]["llm"].get("last_error")),
        str(results[3]["llm"].get("last_error"))[:40])

    chk("05 超时降级 rule", results[4]["intent_engine"] == "rule", results[4]["intent_engine"])
    chk("05 流水线未中断 (done)", results[4]["state"] == "done", results[4]["state"])
    chk("05 last_error 已记录超时",
        "timeout" in str(results[4]["llm"].get("last_error")).lower(),
        str(results[4]["llm"].get("last_error"))[:40])

    r6 = results[5]
    chk("06 去掉 response_format 重试后仍走 llm",
        r6["intent_engine"] == "llm", r6["intent_engine"])
    chk("06 叙述也走 llm", r6["narrative_engine"] == "llm", r6["narrative_engine"])
    chk("06 确实发生了重试（>=2 次）",
        (r6["llm"].get("retries") or 0) >= 2, r6["llm"].get("retries"))
    chk("06 重试请求体里已无 response_format",
        any(q["response_format"] is None for q in r6["requests"]),
        [q["response_format"] for q in r6["requests"]])
    chk("06 流水线未中断 (done)", r6["state"] == "done", r6["state"])

    # 07：推理型模型先吐思维链把预算吃光，客户端应放大 max_tokens 重试成功，
    #     而不是白白降级到规则模式。这是 deepseek-flash 上真实踩过的坑。
    r7 = results[6]
    chk("07 空正文+length 后放大预算重试成功",
        r7["intent_engine"] == "llm", r7["intent_engine"])
    chk("07 叙述也重试成功", r7["narrative_engine"] == "llm", r7["narrative_engine"])
    chk("07 确实发生了重试（>=2 次）",
        (r7["llm"].get("retries") or 0) >= 2, r7["llm"].get("retries"))
    budgets = [q["max_tokens"] for q in r7["requests"] if q["max_tokens"]]
    chk("07 重试时 max_tokens 被放大到 >=2 倍",
        bool(budgets) and max(budgets) >= 2 * min(budgets),
        f"budgets={budgets}")
    chk("07 流水线未中断 (done)", r7["state"] == "done", r7["state"])

    # 08：上游一直只给思维链不给正文。此时必须降级，且错误原因要写清楚
    #     （修复前这里 last_error 是 None，自检会打印出让人误判密钥失效的
    #      「错误详情：None」）。
    r8 = results[7]
    chk("08 持续空正文降级 rule", r8["intent_engine"] == "rule", r8["intent_engine"])
    chk("08 流水线未中断 (done)", r8["state"] == "done", r8["state"])
    chk("08 last_error 说明了「空正文」而非 None",
        "空正文" in str(r8["llm"].get("last_error")),
        str(r8["llm"].get("last_error"))[:60])

    chk("全部场景采集未降级缓存 (source=live)",
        all(r["source"] == "live" for r in results),
        [r["source"] for r in results])

    ok = 0
    for name, cond, got in checks:
        print(("  PASS  " if cond else "  FAIL  ") + name + (("   -> " + str(got)) if not cond else ""))
        ok += 1 if cond else 0

    srv.shutdown()
    print(f"\n结果：{ok}/{len(checks)} 通过")
    return 0 if ok == len(checks) else 1


BASE_URL = ""

if __name__ == "__main__":
    sys.exit(main())
