# -*- coding: utf-8 -*-
"""
验证 dist/ 静态快照站：**不启动任何后端**，只用一个纯静态文件服务器，
看四个页面是否真的能渲染出数据。

为什么要单独验这一层：
    导出脚本只能证明「文件写对了」，证明不了「浏览器跑得起来」。
    如果漏了某个 /api 转换，Python 侧一切正常，页面却是空白的。

一个踩过的坑（所以这里要剥离 <script>）：
    --dump-dom 会把 <script> 标签的源码也输出。而页面里恰好有一批
    「无法连接本地服务」之类的错误文案是写死在 JS 字符串里的，
    直接对整份 DOM 做关键字匹配，会把源码里的文案误判成渲染结果，
    于是好页面被报成坏页面。必须先剥掉 script/style 再断言。

用法：
    python tests/verify_static.py
"""

import http.server
import os
import re
import socketserver
import subprocess
import sys
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(BASE_DIR, "dist")
SHOTS = os.path.join(BASE_DIR, "demo", "shots-static")
PROFILE = os.path.join(BASE_DIR, "tests", "_chromeprofile")
PORT = 8899
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# 每个页面「渲染文本里必须出现」和「绝不能出现」的特征串。
# 注意这些都要用页面上真正显示出来的文字，不要用源码里的字符串。
CHECKS = [
    ("index.html",
     ["只读快照", "数据源连接正常", "热点"],
     ["无法连接本地服务", "正在读取数据集"]),
    ("dashboard.html",
     ["美国实时搜索热点看板"],
     ["读不到数据集"]),
    ("report.html",
     ["Google Trends US 实时热点分析报告"],
     ["尚未生成"]),
    ("analysis.html",
     ["你的需求"],
     ["无法连接本地服务", "读不到状态文件"]),
]

SHOT_PAGES = [("index.html", "1500,2200"), ("dashboard.html", "1500,2800"),
              ("report.html", "1500,2600"), ("analysis.html", "1500,2400")]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def render_text(dom):
    """剥掉 script/style，取出浏览器真正渲染出来的文字。"""
    txt = re.sub(r"<script[\s\S]*?</script>", " ", dom, flags=re.I)
    txt = re.sub(r"<style[\s\S]*?</style>", " ", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def chrome(args, timeout=200):
    return subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--user-data-dir=" + PROFILE] + args,
        capture_output=True, timeout=timeout)


def main():
    if not os.path.isdir(DIST):
        print("dist/ 不存在，请先运行：python export_static.py")
        return 2

    handler = lambda *a, **kw: QuietHandler(*a, directory=DIST, **kw)  # noqa: E731
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)

    base = f"http://127.0.0.1:{PORT}"
    print(f"纯静态服务器（无任何后端）：{base}\n")

    fails = []
    for page, must, never in CHECKS:
        try:
            out = chrome(["--virtual-time-budget=15000", "--dump-dom", f"{base}/{page}"])
            dom = out.stdout.decode("utf-8", "replace")
        except Exception as e:
            fails.append(f"{page}: 浏览器执行失败 {type(e).__name__}")
            print(f"  ✗ {page:<16} 浏览器执行失败：{e}")
            continue

        if len(dom) < 500:
            fails.append(f"{page}: DOM 几乎为空，Chrome 可能没正常启动")
            print(f"  ✗ {page:<16} DOM 只有 {len(dom)} 字节")
            continue

        text = render_text(dom)
        problems = [f"缺少「{s}」" for s in must if s not in text]
        problems += [f"出现了「{s}」" for s in never if s in text]

        if problems:
            fails.append(f"{page}: " + "；".join(problems))
            print(f"  ✗ {page:<16} " + "；".join(problems))
        else:
            print(f"  ✓ {page:<16} 渲染正常（可见文字 {len(text)} 字符）")

    print("\n截图：")
    os.makedirs(SHOTS, exist_ok=True)
    for page, size in SHOT_PAGES:
        png = os.path.join(SHOTS, page.replace(".html", ".png"))
        try:
            chrome(["--hide-scrollbars", "--window-size=" + size,
                    "--virtual-time-budget=15000", "--screenshot=" + png, f"{base}/{page}"])
            print(f"  demo/shots-static/{os.path.basename(png)}  {os.path.getsize(png)//1024} KB")
        except Exception as e:
            print(f"  {page} 截图失败：{e}")

    srv.shutdown()
    print()
    if fails:
        print(f"结果：{len(fails)} 个页面对不上")
        return 1
    print("结果：四个页面在纯静态环境下全部渲染正常 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
