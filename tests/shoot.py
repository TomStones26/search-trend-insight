"""用无头 Chrome 给四个页面截图，用于确认渲染正常。"""
import os
import subprocess

BASE = r"C:\Users\ynwas\WorkBuddy\2026-09-16-17-05-18\data-product-kit"
OUT = os.path.join(BASE, "demo", "shots")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
os.makedirs(OUT, exist_ok=True)

pages = [("index", 2600), ("dashboard", 3000), ("report", 3600), ("analysis", 1600)]
lines = []
for name, h in pages:
    shot = os.path.join(OUT, name + ".png")
    if os.path.exists(shot):
        os.remove(shot)
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           f"--window-size=1500,{h}", "--virtual-time-budget=9000",
           f"--screenshot={shot}", f"http://127.0.0.1:8848/{name}.html"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=90)
        size = os.path.getsize(shot) if os.path.exists(shot) else 0
        lines.append(f"{name:10s} rc={r.returncode} bytes={size}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"{name:10s} FAIL {type(e).__name__}: {e}")

with open(os.path.join(BASE, "_shots.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("done")
