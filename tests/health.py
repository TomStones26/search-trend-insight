"""服务连通性检查：逐个打关键路由。"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8848"
paths = ["/", "/api/meta", "/api/status", "/api/dataset", "/api/schedule_state",
         "/assets/theme.css", "/analysis.html", "/dashboard.html", "/report.html"]
lines = []
for p in paths:
    try:
        with urllib.request.urlopen(BASE + p, timeout=8) as r:
            body = r.read()
        lines.append(f"OK   {r.status:3d} {len(body):>7d}B  {p}")
    except urllib.error.HTTPError as e:
        lines.append(f"HTTP {e.code}          {p}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"FAIL {type(e).__name__:>10}  {p}  {str(e)[:80]}")

# meta 内容
try:
    with urllib.request.urlopen(BASE + "/api/meta", timeout=8) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    lines.append("META=" + json.dumps(d, ensure_ascii=False)[:600])
except Exception as e:  # noqa: BLE001
    lines.append(f"META_FAIL {type(e).__name__}: {e}")

with open(r"C:\Users\ynwas\WorkBuddy\2026-09-16-17-05-18\data-product-kit\_health.txt",
          "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("done")
