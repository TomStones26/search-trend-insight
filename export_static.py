# -*- coding: utf-8 -*-
"""
DataPulse 静态快照导出器
==========================================================================
把一个「活的服务端」导出成一个「纯静态、只读的网站目录」，
用来发布到任意静态托管（个人主页 / GitHub Pages / Vercel / 云沙箱）。

为什么需要它
--------------------------------------------------------------------
页面是通过 /api/* 拿数据的，没有服务进程就没有数据（直接双击 html 会空白）。
静态化 = 把这些接口的返回值预先落成 JSON 文件，再让前端改从文件读取。
于是产物变成一堆 html + css + js + json，不需要 Python 也能跑。

安全边界（重要）
--------------------------------------------------------------------
导出产物里 **没有任何密钥**：config/llm.env 不会被复制，
所有 POST 接口在静态版里被替换成「只读提示」，不存在可被利用的写入口。

用法
--------------------------------------------------------------------
    python export_static.py                    # 从 127.0.0.1:8848 拉数据 -> dist/
    python export_static.py --port 9000 --out mysite
    python export_static.py --no-echarts       # 不把 echarts 下载到本地，继续用 CDN

跑之前请确认本地服务已经起来（python server.py）。
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PAGES = ["index.html", "analysis.html", "dashboard.html", "report.html"]

# 只读接口 -> 落盘文件名。前端会把 /api/xxx 映射成 api/xxx.json。
API_ENDPOINTS = [
    ("/api/meta", "meta.json"),
    ("/api/status", "status.json"),
    ("/api/dataset", "dataset.json"),
    ("/api/analysis", "analysis.json"),
    ("/api/report", "report.json"),
    ("/api/dashboard", "dashboard.json"),
    ("/api/quality", "quality.json"),
    ("/api/schedule", "schedule.json"),
    ("/api/schedule_state", "schedule_state.json"),
]

ECHARTS_URL = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"
ECHARTS_TAG_RE = re.compile(
    r'<script\s+src="https://cdn\.jsdelivr\.net/npm/echarts[^"]*"[^>]*>\s*</script>',
    re.S)

# 1) 根路径 -> 相对路径。这样无论部署在域名根还是子目录都能跑。
#    分两档：必需（缺了页面就断）与实际存在才替换（各页用到的链接不同）。
HTML_RULES_REQUIRED = [
    ('href="/assets/', 'href="assets/'),
    ('src="/assets/', 'src="assets/'),
]
HTML_RULES_OPTIONAL = [
    ('href="/dashboard"', 'href="dashboard.html"'),
    ('href="/report"', 'href="report.html"'),
    ('href="/analysis"', 'href="analysis.html"'),
    ('href="/"', 'href="index.html"'),
    ("location.href = '/analysis'", "location.href = 'analysis.html'"),
    ("location.href = '/'", "location.href = 'index.html'"),
]

# 2) common.js：导航改 .html、api() 改读静态 json、post() 改成只读拒绝。
JS_RULES = [
    (
        "  const items = [\n"
        "    ['/', '主页', 'home'],\n"
        "    ['/analysis', '分析过程', 'analysis'],\n"
        "    ['/dashboard', '可视化看板', 'dashboard'],\n"
        "    ['/report', '分析报告', 'report']\n"
        "  ];",
        "  const items = [\n"
        "    ['index.html', '主页', 'home'],\n"
        "    ['analysis.html', '分析过程', 'analysis'],\n"
        "    ['dashboard.html', '可视化看板', 'dashboard'],\n"
        "    ['report.html', '分析报告', 'report']\n"
        "  ];",
    ),
    (
        "async function api(path, opts) {\n"
        "  const r = await fetch(path, Object.assign({ cache: 'no-store' }, opts || {}));",
        "const STATIC_MODE = true;\n"
        "async function api(path, opts) {\n"
        "  const url = String(path).replace(/^\\/api\\/([A-Za-z0-9_]+).*$/, 'api/$1.json');\n"
        "  const r = await fetch(url, Object.assign({ cache: 'no-store' }, opts || {}));",
    ),
    (
        "async function post(path, body) {\n"
        "  return api(path, {\n"
        "    method: 'POST',\n"
        "    headers: { 'Content-Type': 'application/json' },\n"
        "    body: JSON.stringify(body || {})\n"
        "  });\n"
        "}",
        "async function post(path, body) {\n"
        "  toast('这是只读快照版：数据已冻结，不能在这里执行写操作。', 'warn');\n"
        "  const e = new Error('只读快照版');\n"
        "  e.status = 403;\n"
        "  e.silent = true;\n"
        "  throw e;\n"
        "}",
    ),
    ('<a href="/">主页</a>', '<a href="index.html">主页</a>'),
]

# 3) 首页：把三个「会写数据」的操作改成不可点，避免访客点了没反应。
INDEX_RULES = [
    (
        '<button class="btn primary" id="btnCollect">立即更新数据</button>',
        '<button class="btn" id="btnCollect" disabled title="只读快照版，数据已冻结">立即更新数据</button>',
    ),
    (
        '<button class="btn primary" id="btnAnalyze">开始分析 →</button>',
        '<button class="btn" id="btnAnalyze" disabled title="只读快照版，数据已冻结">开始分析 →</button>',
    ),
    (
        '<button class="btn" id="btnSchOnce">立即采一次</button>',
        '<button class="btn" id="btnSchOnce" disabled title="只读快照版">立即采一次</button>',
    ),
    (
        '<button class="btn primary" id="btnSchToggle">开启</button>',
        '<button class="btn" id="btnSchToggle" disabled title="只读快照版">开启</button>',
    ),
    ('<textarea id="req" placeholder=', '<textarea id="req" disabled placeholder='),
    ('<select id="schInterval" style=', '<select id="schInterval" disabled style='),
    (
        "setInterval(() => { if (!document.hidden) loadMeta(); }, 30000);",
        "/* 静态快照版：不需要轮询 */",
    ),
]

BANNER_ANCHOR = '<div class="grid g2 mt-l">'


# --------------------------------------------------------------------- 工具
def fetch_raw(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "DataPulse-Export/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def apply_rules(text, rules, label, missing, required=True):
    for old, new in rules:
        if old not in text:
            if required:
                missing.append(f"{label}: 未找到待替换片段 -> {old[:60]!r}")
            continue
        text = text.replace(old, new)
    return text


# 产物扫描：任何残留的根路径都会在子目录部署时变成死链，必须扫出来。
# 注意 api('/api/xxx') 这种调用点是**故意保留**的 —— 转换发生在 api() 内部，
# 所以这里只查「绕过 api() 直接请求根路径」的写法。
RESIDUE_PATTERNS = [
    (re.compile(r'(?:href|src)="/'), "根路径资源引用"),
    (re.compile(r"""fetch\(\s*['"]/api/"""), "绕过 api() 直接请求根路径"),
    (re.compile(r"""location\.href\s*=\s*['"]/"""), "根路径跳转"),
    (re.compile(r"cdn\.jsdelivr\.net/npm/echarts"), "仍在走 CDN 的 echarts"),
]


def check_static_shim(out):
    """正检查：确认取数改写真的装进了产物，否则页面会一片空白。"""
    js_path = os.path.join(out, "assets", "common.js")
    if not os.path.exists(js_path):
        return ["assets/common.js 缺失"]
    js = read_text(js_path)
    problems = []
    if "api/$1.json" not in js:
        problems.append("assets/common.js 缺少 /api -> .json 的取数转换")
    if "const STATIC_MODE = true" not in js:
        problems.append("assets/common.js 缺少 STATIC_MODE 标记")
    if "只读快照版" not in js:
        problems.append("assets/common.js 未禁用写操作")
    return problems


def scan_residue(out):
    hits = []
    for root, _dirs, files in os.walk(out):
        for f in files:
            if not f.endswith((".html", ".js", ".css")):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, out).replace(os.sep, "/")
            for i, line in enumerate(read_text(p).splitlines(), 1):
                for pat, why in RESIDUE_PATTERNS:
                    if pat.search(line):
                        hits.append(f"{rel}:{i} [{why}] {line.strip()[:90]}")
    return hits


def guard_out_dir(out_dir):
    """绝不允许把项目自己的目录当成输出目录清掉。"""
    real = os.path.abspath(out_dir)
    if real == BASE_DIR:
        raise SystemExit("拒绝：输出目录不能是项目根目录")
    forbidden = {"data", "src", "config", "tests", "assets", "demo", "templates"}
    if os.path.basename(real) in forbidden:
        raise SystemExit(f"拒绝：不建议用 {os.path.basename(real)} 作为输出目录")
    if os.path.commonpath([real, BASE_DIR]) != BASE_DIR:
        raise SystemExit("拒绝：输出目录必须在项目目录之内")
    return real


def fmt_snapshot(meta):
    t = ((meta or {}).get("dataset") or {}).get("snapshot_time")
    if not t:
        return "未知时间"
    try:
        d = dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        d = d.astimezone(dt.timezone(dt.timedelta(hours=8)))
        return d.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(t)[:16].replace("T", " ")


# --------------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out", default=os.path.join(BASE_DIR, "dist"))
    ap.add_argument("--no-echarts", action="store_true",
                    help="不下载 echarts 到本地，继续走 CDN")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    out = guard_out_dir(args.out)

    print("=" * 62)
    print("  DataPulse 静态快照导出")
    print("=" * 62)

    # --- 0. 确认服务可达 ---
    try:
        meta_raw = fetch_raw(base + "/api/meta", timeout=15)
        meta = json.loads(meta_raw.decode("utf-8"))
    except Exception as e:
        print(f"  ✗ 连不上本地服务 {base}（{type(e).__name__}: {e}）")
        print("    请先在另一个窗口运行：python server.py")
        return 2

    # --- 1. 清空输出目录 ---
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)

    missing = []

    # --- 2. 拉取只读接口 ---
    api_dir = os.path.join(out, "api")
    ok_count, fail = 0, []
    for path, fname in API_ENDPOINTS:
        try:
            raw = meta_raw if path == "/api/meta" else fetch_raw(base + path)
            json.loads(raw.decode("utf-8"))          # 必须是合法 JSON 才落盘
            write_bytes(os.path.join(api_dir, fname), raw)
            ok_count += 1
            print(f"  ✓ {path:<24} -> api/{fname:<20} {len(raw)/1024:>8.1f} KB")
        except Exception as e:
            fail.append(f"{path}: {type(e).__name__}: {e}")
            print(f"  ✗ {path:<24} 拉取失败：{type(e).__name__}")

    if fail:
        print("\n  以下接口没导出成功（对应页面会缺数据）：")
        for f in fail:
            print("    -", f)

    # --- 3. 资产 ---
    with open(os.path.join(BASE_DIR, "assets", "theme.css"), "rb") as f:
        write_bytes(os.path.join(out, "assets", "theme.css"), f.read())
    write_text(os.path.join(out, "assets", "common.js"),
               apply_rules(read_text(os.path.join(BASE_DIR, "assets", "common.js")),
                           JS_RULES, "common.js", missing))
    print("  ✓ assets/theme.css, assets/common.js（已改写为静态取数）")

    # --- 4. echarts 本地化（可选但推荐：不依赖 CDN，打开更快更稳） ---
    local_echarts = False
    if not args.no_echarts:
        try:
            js = fetch_raw(ECHARTS_URL, timeout=120)
            if len(js) > 100_000:
                write_bytes(os.path.join(out, "assets", "echarts.min.js"), js)
                local_echarts = True
                print(f"  ✓ assets/echarts.min.js（本地化，{len(js)/1024:.0f} KB）")
            else:
                print("  ! echarts 下载内容异常，改回 CDN 引用")
        except Exception as e:
            print(f"  ! echarts 本地化失败（{type(e).__name__}），继续用 CDN")

    # --- 5. 四个页面 ---
    snap_txt = fmt_snapshot(meta)
    banner = (
        '<div class="card tight mt" style="border-left:4px solid #F59E0B">'
        '<div class="row" style="gap:9px;align-items:center;flex-wrap:wrap">'
        '<span class="chip amber">只读快照</span>'
        f'<span class="sub" style="margin:0">数据截至 <b>{snap_txt}</b>。'
        '本页是静态快照，<b>不能在这里更新数据或提交新需求</b>；'
        '数据由作者在本机采集后重新导出。</span>'
        '</div></div>'
    )

    for page in PAGES:
        text = read_text(os.path.join(BASE_DIR, page))
        text = apply_rules(text, HTML_RULES_REQUIRED, page, missing, required=True)
        text = apply_rules(text, HTML_RULES_OPTIONAL, page, missing, required=False)
        if page == "index.html":
            text = apply_rules(text, INDEX_RULES, page, missing, required=True)
            if BANNER_ANCHOR in text:
                text = text.replace(BANNER_ANCHOR, banner + "\n\n  " + BANNER_ANCHOR, 1)
            else:
                missing.append("index.html: 未找到插入只读横幅的位置")
        if local_echarts and "cdn.jsdelivr.net/npm/echarts" in text:
            text = ECHARTS_TAG_RE.sub('<script src="assets/echarts.min.js"></script>', text)
        write_text(os.path.join(out, page), text)
        print(f"  ✓ {page}")

    # --- 6. 体检 ---
    print()
    total = 0
    for root, _dirs, files in os.walk(out):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))

    leaks = []
    for root, _dirs, files in os.walk(out):
        for f in files:
            if f.endswith((".env", ".py", ".jsonl", ".bat")):
                leaks.append(os.path.relpath(os.path.join(root, f), out))
    if leaks:
        print("  ✗ 产物里出现了不该有的文件：", leaks)
    else:
        print("  ✓ 安全体检：无 .env / .py / 原始数据文件泄漏")

    shim_bad = check_static_shim(out)
    if shim_bad:
        print("  ✗ 取数体检：静态改写没装好，页面会空白")
        for s in shim_bad:
            print("    -", s)
    else:
        print("  ✓ 取数体检：/api -> api/*.json 转换已生效，写操作已禁用")

    residue = scan_residue(out)
    if residue:
        print(f"  ✗ 路径体检：发现 {len(residue)} 处根路径残留（子目录部署会断链）")
        for r in residue[:12]:
            print("    -", r)
    else:
        print("  ✓ 路径体检：无根路径残留，放域名根或子目录都能跑")

    if missing:
        print("\n  ⚠ 有必需片段没匹配上（源文件可能改过结构，请核对）：")
        for m in missing:
            print("    -", m)

    ok = not leaks and not residue and not missing and not shim_bad
    print()
    print(f"  导出目录：{out}")
    print(f"  接口 {ok_count}/{len(API_ENDPOINTS)} · 总体积 {total/1024/1024:.2f} MB")
    print(f"  数据时点：{snap_txt}")
    print()
    print("  本地预览：python -m http.server 8899 --directory dist")
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
