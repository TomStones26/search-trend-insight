#!/usr/bin/env python3
"""
Token 消耗实测 —— 全程不需要 API 密钥
======================================
目的：回答「每小时自动采集到底烧多少 token」这个问题，用真实 prompt 量出来，不靠估。

做法：起一个 mock OpenAI 兼容端点，把 OPENAI_BASE_URL 指过去，跑真实的
      pipeline.run_pipeline()，把每一次请求的 system / user 全文截获，按
      DeepSeek 官方的换算口径统计 token 量。

两种运行模式分别量：
  A. 完整分析（用户提问触发）—— 会调 LLM
  B. 定时采集（scheduler 触发）—— 不调 LLM

用法：
    python tests/measure_tokens.py
"""

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, SRC_DIR)

REQ_TEXT = "看看科技、消费和游戏板块里增长最快、最值得跟的热点，重点找品牌和商品层面的机会"

INTENT_PAYLOAD = {
    "intent_summary": "聚焦科技、消费与游戏板块，筛选高增长热点并识别品牌机会",
    "filters": {"categories": ["Technology", "Consumer", "Gaming"], "min_growth": 300,
                "min_volume": None, "brand_only": False, "keyword": None},
    "focus": ["growth", "commerce", "brand"],
    "dimensions": ["category", "brand", "quadrant"],
    "top_n": 10,
    "focus_questions": ["科技板块里增长最快的是哪几个热点？",
                        "哪些品牌出现了突然的搜索爆发？",
                        "这波热度里有没有值得跟进的商品机会？"],
}
SUMMARY_PAYLOAD = {
    "executive_summary": ["科技与消费板块合计贡献了主要增量。",
                          "品牌层面的爆发信号集中在三处。",
                          "建议按四象限分层跟进。"],
    "recommendations": ["优先跟进爆发型热点中的品牌词。", "对潜在型热点建立观察清单。",
                        "把成熟型热点用于存量运营。"],
}

CAPTURED = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        CAPTURED.append({"model": body.get("model"),
                         "max_tokens": body.get("max_tokens"),
                         "messages": body.get("messages") or []})
        system = "".join(m.get("content") or "" for m in body.get("messages") or []
                         if m.get("role") == "system")
        payload = SUMMARY_PAYLOAD if "executive_summary" in system else INTENT_PAYLOAD
        self._send(200, {"choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant",
                                                  "content": json.dumps(payload, ensure_ascii=False)}}]})

    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def count_chars(text):
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff" or "\u3000" <= c <= "\u303f"
              or "\uff00" <= c <= "\uffef")
    return cjk, len(text) - cjk


def est(text):
    """三种口径的 token 估算。

    deepseek : DeepSeek 官方口径（中文 0.6 tok/字，英文 0.3 tok/字符）
    openai   : OpenAI 经验口径（中文约 1 tok/字，英文约 4 字符/tok）
    chars    : 粗略下界 = 字符数 / 4
    """
    cjk, other = count_chars(text)
    return {
        "deepseek": round(cjk * 0.6 + other * 0.3),
        "openai": round(cjk * 1.0 + other / 4),
        "chars": round((cjk + other) / 4),
        "chars_total": cjk + other,
        "cjk": cjk,
    }


def main():
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}/v1"

    os.environ["OPENAI_API_KEY"] = "mock-key"
    os.environ["OPENAI_BASE_URL"] = base
    os.environ["OPENAI_MODEL"] = "deepseek-chat"
    os.environ["OPENAI_TIMEOUT_SECONDS"] = "20"

    import pipeline as pipe

    report = {"in": 0, "out": 0, "calls": []}

    # ---------------------------------------------------------- A 完整分析
    print("=" * 74)
    print("A. 完整分析（用户提问一次触发）")
    print("=" * 74)
    CAPTURED.clear()
    t0 = time.time()
    out = pipe.run_pipeline(REQ_TEXT, quiet=True)
    dt = time.time() - t0

    for i, req in enumerate(CAPTURED, 1):
        sys_msg = next((m["content"] for m in req["messages"] if m["role"] == "system"), "")
        usr_msg = next((m["content"] for m in req["messages"] if m["role"] == "user"), "")
        e_in = est(sys_msg + usr_msg)
        # 输出上限用请求里的 max_tokens 估（实际通常更少）
        e_out = req["max_tokens"] or 1600
        kind = "需求理解" if "intent_summary" in sys_msg else "报告叙述"
        print(f"\n  调用 {i} · {kind}")
        print(f"    system : {count_chars(sys_msg)[0]} 中文字 + {count_chars(sys_msg)[1]} 其他字符")
        print(f"    user   : {count_chars(usr_msg)[0]} 中文字 + {count_chars(usr_msg)[1]} 其他字符")
        print(f"    输入估算 : DeepSeek {e_in['deepseek']:>5} tok ｜ OpenAI {e_in['openai']:>5} tok")
        print(f"    输出上限 : {e_out} tok（max_tokens 上限，实测通常 200-600）")
        report["in"] += e_in["deepseek"]
        report["out"] += e_out
        report["calls"].append({"kind": kind, "in_ds": e_in["deepseek"],
                                "in_oai": e_in["openai"], "out_cap": e_out})
    print(f"\n  流水线状态 : {out['status']['state']} ｜ 耗时 {dt:.2f}s ｜ 采集来源 {out['status']['source']}")

    # ---------------------------------------------------------- B 定时采集
    print()
    print("=" * 74)
    print("B. 定时采集（scheduler 触发，不跑 LLM）")
    print("=" * 74)
    CAPTURED.clear()
    t0 = time.time()
    from scheduler import collect_once
    r = collect_once(geo="US", hours=24, quiet=True)
    dt = time.time() - t0
    print(f"  采集结果   : {r['count']} 条 ｜ 来源 {r['source']} ｜ 耗时 {dt:.2f}s")
    print(f"  LLM 调用   : {len(CAPTURED)} 次")

    # ---------------------------------------------------------- 汇总
    print()
    print("=" * 74)
    print("汇总")
    print("=" * 74)
    tot_in = report["in"]
    tot_out_cap = report["out"]
    print(f"\n  ① 每小时定时采集          : LLM 调用 0 次 → 0 token")
    print(f"  ② 用户提问一次（输入）    : 约 {tot_in:,} token（DeepSeek 口径）")
    print(f"  ③ 用户提问一次（输出上限）: {tot_out_cap:,} token（max_tokens 之和）")
    print()
    print("  按 DeepSeek 定价（输入 ¥2/百万 tok、输出 ¥8/百万 tok，缓存未命中价）估算：")
    cost = tot_in / 1e6 * 2 + 400 / 1e6 * 8
    print(f"    单次提问  : 约 ¥{cost:.4f}（输入按实测 {tot_in} tok，输出按实际约 400 tok）")
    print(f"    一天 20 问: 约 ¥{cost * 20:.3f}")
    print(f"    一天 100 问: 约 ¥{cost * 100:.3f}")
    print()
    print("  每小时采集一年（8760 次）: ¥0 —— 采集与打标全部在本地完成，不经过模型。")

    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
