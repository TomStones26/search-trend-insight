"""
LLM 适配层（OpenAI 兼容协议，零第三方依赖）
============================================
设计原则：**LLM 是增强项，不是依赖项。**
  - 配了 key  -> 调真实模型做「需求理解」与「结论解释」
  - 没配 key  -> 自动降级为本地规则解析器，结果里标记 engine="rule"
  - 调用失败  -> 静默降级，不阻断流水线（网络受限环境同样可用）

它只被两处调用，都在「用户提问」路径上：
  src/analyze.py  _llm_intent()      需求理解
  src/report.py   _llm_summary()     报告执行摘要与建议
定时采集路径（scheduler）不经过这里，所以自动采集的 token 消耗恒为 0。

配置读取顺序（先匹配到就用）
--------------------------
  1. 环境变量
  2. config/product.config.json 的 "llm" 段
  3. config/llm.env 文件（KEY=VALUE 每行一条，# 开头为注释）
  4. 内置 provider 预设

只需要写两个变量即可跑起来（用 config/llm.env 最省事）：
  OPENAI_PROVIDER=deepseek
  OPENAI_API_KEY=sk-xxxx

可选：
  OPENAI_BASE_URL / OPENAI_MODEL / OPENAI_TIMEOUT_SECONDS

自检：
  python src/llm.py            # 打印配置并做一次真实连通性测试
  python src/llm.py --raw      # 顺带打印模型原始返回，便于排查
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "product.config.json")
ENV_PATH = os.path.join(BASE_DIR, "config", "llm.env")

# 内置预设。模型名会随厂商迭代变化（DeepSeek 就在 2026-07-24 下线了
# deepseek-chat / deepseek-reasoner 两个旧别名），这里只保证「开箱可用」，
# 长期使用请以厂商官方定价文档为准。
PROVIDER_PRESETS = {
    "openai":    {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "deepseek":  {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash"},
    "dashscope": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "model": "qwen-plus"},
    "moonshot":  {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "zhipu":     {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1",
                    "model": "Qwen/Qwen2.5-7B-Instruct"},
}


def load_env_file(path=ENV_PATH):
    """把 config/llm.env 读进 os.environ（已存在的变量不覆盖）。

    这么做是为了让 Windows 用户不必每开一个终端就 set 一次环境变量，
    直接把密钥写进文件，双击 server.py 就能用。
    """
    if not os.path.exists(path):
        return {}
    loaded = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if not k:
                    continue
                loaded[k] = v
                os.environ.setdefault(k, v)
    except Exception:
        return {}
    return loaded


class LLM:
    def __init__(self, config_path=CONFIG_PATH, read_env_file=True):
        if read_env_file:
            load_env_file()

        cfg = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, encoding="utf-8") as f:
                    cfg = json.load(f).get("llm", {}) or {}
            except Exception:
                cfg = {}

        provider = (os.environ.get("OPENAI_PROVIDER")
                    or cfg.get("provider") or "").strip().lower()
        preset = PROVIDER_PRESETS.get(provider, {})
        preset_default = PROVIDER_PRESETS["openai"]

        self.provider = provider or "custom"
        self.base_url = (os.environ.get("OPENAI_BASE_URL")
                         or cfg.get("base_url")
                         or preset.get("base_url")
                         or preset_default["base_url"]).rstrip("/")
        self.model = (os.environ.get("OPENAI_MODEL")
                      or cfg.get("model")
                      or preset.get("model")
                      or preset_default["model"])
        key_env = cfg.get("api_key_env") or "OPENAI_API_KEY"
        self.api_key = os.environ.get(key_env) or cfg.get("api_key") or ""
        self.timeout = int(os.environ.get("OPENAI_TIMEOUT_SECONDS")
                           or cfg.get("timeout_seconds") or 60)
        self.last_error = None
        self.last_reasoning = None      # 推理型模型的思维链，仅用于排查
        self.last_finish = None
        self.last_retry_reason = None   # 最近一次自动重试的触发原因
        self.calls = 0
        self.retries = 0

    @property
    def available(self):
        return bool(self.api_key)

    def status(self):
        return {
            "available": self.available,
            "mode": "llm" if self.available else "rule",
            "provider": self.provider if self.available else None,
            "model": self.model if self.available else None,
            "base_url": self.base_url if self.available else None,
            "calls": self.calls,
            "retries": self.retries,
            "last_retry_reason": self.last_retry_reason,
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------ 调用
    def _post(self, body):
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key},
            method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _once(self, body):
        """发一次请求，返回 (正文, finish_reason, 错误三元组)。

        错误三元组为 (类别, HTTP码, 说明)；类别 ∈ {"http", "exc", "shape"}。
        绝不抛异常 —— 调用方只需要判断第二个返回值是否为 None。
        """
        try:
            payload = self._post(body)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            return None, None, ("http", e.code, detail or str(e)[:180])
        except Exception as e:                          # noqa: BLE001
            return None, None, ("exc", None, f"{type(e).__name__}: {str(e)[:200]}")

        try:
            choice = payload["choices"][0]
            msg = choice.get("message") or {}
            self.last_reasoning = msg.get("reasoning_content") or None
            return (msg.get("content") or ""), choice.get("finish_reason"), None
        except Exception as e:                          # noqa: BLE001
            return None, None, ("shape", None, f"响应结构异常：{str(e)[:180]}")

    def json_chat(self, system, user, max_tokens=1600):
        """要求模型返回 JSON。任何失败都返回 None，由调用方降级。"""
        if not self.available:
            return None
        budget = int(max_tokens)
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": budget,
            "response_format": {"type": "json_object"},
        }

        text, finish, err = self._once(body)

        # 部分 OpenAI 兼容服务商不支持 response_format，会返回 400。
        # 这时去掉该参数重试一次，而不是直接放弃退回规则模式。
        if err and err[0] == "http" and err[1] == 400:
            self.retries += 1
            self.last_retry_reason = ("response_format 被上游拒绝（400），已去掉该参数重试")
            body.pop("response_format", None)
            text, finish, err = self._once(body)

        # 推理型模型（deepseek-flash / deepseek-v4-pro 这类）的思维链同样
        # 计入 max_tokens。预算给小了会出现「全是推理、正文为空、
        # finish_reason=length」，表面看像密钥失效，实际是预算被思考吃光了。
        # 这里自动放大预算重试一次，避免无谓地降级到规则模式。
        if not err and not (text or "").strip() and finish == "length":
            self.retries += 1
            self.last_retry_reason = (
                f"思维链吃光 max_tokens={budget}（正文为空、finish_reason=length），"
                f"已放大到 {max(budget * 2, 1024)} 重试")
            body["max_tokens"] = max(budget * 2, 1024)
            text, finish, err = self._once(body)

        self.last_finish = finish

        if err:
            kind, code, detail = err
            self.last_error = (f"HTTPError {code}: {detail}" if kind == "http"
                               else detail)
            return None

        self.calls += 1
        if not (text or "").strip():
            self.last_error = (f"模型返回空正文（finish_reason={finish}）。"
                               "该模型若带思维链，需调大 max_tokens。")
            return None

        parsed = _loose_json(text)
        if parsed is None:
            self.last_error = f"返回内容不是合法 JSON：{text[:160]!r}"
        return parsed


def _loose_json(text):
    """模型偶尔会包 ```json 围栏，做一次宽松解析。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1] if "```" in t[3:] else t[3:]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    try:
        return json.loads(t)
    except Exception:
        i, j = t.find("{"), t.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------- 自检
def _selfcheck(raw=False):
    llm = LLM()
    env_from_file = load_env_file()
    print("=" * 62)
    print("  LLM 配置自检")
    print("=" * 62)
    print(f"  provider      : {llm.provider}")
    print(f"  base_url      : {llm.base_url}")
    print(f"  model         : {llm.model}")
    print(f"  密钥          : {'已配置（%d 字符）' % len(llm.api_key) if llm.available else '未配置'}")
    print(f"  超时          : {llm.timeout}s")
    if env_from_file:
        print(f"  已从 config/llm.env 读取：{', '.join(sorted(env_from_file))}")
    if not llm.available:
        print()
        print("  未检测到密钥。两种配置方式，任选其一：")
        print("    A) 新建 config/llm.env，写入：")
        print("         OPENAI_PROVIDER=deepseek")
        print("         OPENAI_API_KEY=sk-你的密钥")
        print("    B) 设置环境变量 OPENAI_API_KEY（当前终端有效）")
        print()
        print("  不配置也能完整运行 —— 需求理解会走本地规则解析器。")
        return 1

    print()
    print("  正在发起一次真实调用 …")
    # 注意：这里不能把预算调得很小。deepseek-flash 之类的推理模型会先产出
    # 思维链，思维链也占 max_tokens；给小了正文就是空的，会被误判成密钥失效。
    out = llm.json_chat("你是测试助手。必须只输出 JSON。",
                        '请原样返回 {"ok": true}', max_tokens=800)
    st = llm.status()
    print(f"  调用次数      : {st['calls']}（自动重试 {st['retries']} 次）")
    print(f"  返回          : {out}")
    print(f"  finish_reason : {llm.last_finish}")
    if st["retries"]:
        print(f"  重试原因      : {st['last_retry_reason']}")
    if raw and llm.last_reasoning:
        print(f"  思维链预览    : {llm.last_reasoning[:200]}")
    print(f"  最后错误      : {st['last_error']}")
    print()
    if out:
        print("  ✅ 密钥可用，需求理解与报告叙述将走真实模型。")
        return 0
    print("  ❌ 调用失败。请核对密钥、余额、模型名与网络。")
    print(f"     错误详情：{st['last_error']}")
    return 2


if __name__ == "__main__":
    sys.exit(_selfcheck(raw="--raw" in sys.argv))
