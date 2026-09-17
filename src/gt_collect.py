"""
Google Trends US - Trending Now 采集器
=====================================
数据源：https://trends.google.com/trending?geo=US
原理：该页面把完整数据以 AF_initDataCallback({key:'ds:0', data:[...]}) 的形式
      内嵌在 HTML 里，无需渲染 JS、无需浏览器、无需 API Key。
      本次实测单次返回 299 条热点。

依赖：仅标准库。
用法：python gt_collect.py            # 抓取 US / 最近24小时
      python gt_collect.py --hours 4
      python gt_collect.py --geo US --hours 48
"""

import argparse
import ast
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 与 pipeline.py 保持一致：可用 DATAPULSE_DATA_DIR 覆盖数据目录
DATA_DIR = os.environ.get("DATAPULSE_DATA_DIR") or os.path.join(BASE_DIR, "data")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")


# ---------------------------------------------------------------- 抓取
def fetch_page(geo: str, hours: int, hl: str = "en-US") -> str:
    url = (f"https://trends.google.com/trending"
           f"?geo={geo}&hl={hl}&hours={hours}")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return r.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 解析
def _balanced_slice(src: str, start: int) -> str:
    """从 src[start]（必须是 '['）开始做括号配对，跳过字符串内部。"""
    depth = 0
    i = start
    n = len(src)
    in_str = False
    quote = ""
    esc = False
    while i < n:
        c = src[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
            i += 1
            continue
        if c in "\"'":
            in_str, quote = True, c
            i += 1
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise ValueError("括号未闭合，页面结构可能已变化")


def _js_to_json(src: str) -> str:
    """
    把 Google 的 JS 字面量转成合法 JSON：
      - 单引号字符串 -> 双引号
      - undefined -> null
      - 删除尾逗号（在字符串感知状态下做，避免误伤字符串内容）
    """
    out = []
    i, n = 0, len(src)
    in_str = False
    while i < n:
        c = src[i]
        if in_str:
            if c == "\\":
                out.append(src[i:i + 2])
                i += 2
                continue
            if c == '"':
                in_str = False
            out.append(c)
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "'":
            i += 1
            buf = []
            while i < n and src[i] != "'":
                if src[i] == "\\":
                    buf.append(src[i:i + 2])
                    i += 2
                    continue
                buf.append(src[i])
                i += 1
            i += 1
            out.append('"' + "".join(buf).replace('"', '\\"') + '"')
            continue
        if src.startswith("undefined", i):
            out.append("null")
            i += 9
            continue
        if c in "]}":
            # 去掉紧邻的尾逗号
            j = len(out) - 1
            while j >= 0 and out[j].strip() == "":
                j -= 1
            if j >= 0 and out[j] == ",":
                out.pop(j)
        out.append(c)
        i += 1
    return "".join(out)


def parse_ds0(html: str):
    key_idx = html.find("key: 'ds:0'")
    if key_idx < 0:
        raise RuntimeError("页面里找不到 ds:0 数据块（Google 可能改版或返回了同意页）")
    data_idx = html.index("data:", key_idx)
    start = html.index("[", data_idx)
    literal = _balanced_slice(html, start)
    return json.loads(_js_to_json(literal))


# ---------------------------------------------------------------- 归一化
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import Taxonomy  # noqa: E402

_TX = None


def taxonomy():
    global _TX
    if _TX is None:
        _TX = Taxonomy()
    return _TX


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def normalize(raw_list, geo: str, hours: int, snapshot_dt: datetime):
    tx = taxonomy()
    recs = []
    for r in raw_list:
        if not isinstance(r, list) or not r or not isinstance(r[0], str):
            continue
        name = r[0]
        started = _first_ts(r, 3)
        ts4 = _first_ts(r, 4)
        rec = {
            "trend_id": slug(name),
            "trend": name,
            "geo": r[2] if len(r) > 2 else geo,
            "search_volume": r[6] if len(r) > 6 and isinstance(r[6], int) else None,
            "growth_rate": r[8] if len(r) > 8 and isinstance(r[8], int) else None,
            "started_at": started,
            "started_at_iso": _iso(started),
            "raw_ts4": ts4,
            "raw_ts4_iso": _iso(ts4),
            "related_queries": r[9] if len(r) > 9 and isinstance(r[9], list) else [],
            "news_article_refs": r[11] if len(r) > 11 and isinstance(r[11], list) else [],
            "snapshot_time": snapshot_dt.isoformat(),
            "snapshot_date": snapshot_dt.strftime("%Y-%m-%d"),
            "snapshot_hour": snapshot_dt.strftime("%Y-%m-%dT%H:00"),
            "window_hours": hours,
        }
        rec.update(tx.classify(name, rec["related_queries"]))
        rec["related_query_count"] = len(rec["related_queries"])
        rec["duration_hours"] = None      # 需多快照累积，见 merge_history()
        rec["first_seen_at"] = rec["snapshot_time"]
        rec["last_seen_at"] = rec["snapshot_time"]
        rec["status"] = "Active"          # 源站只暴露当前活跃热点，Ended 需自行判定
        recs.append(rec)
    return recs


def _first_ts(r, idx):
    v = r[idx] if len(r) > idx else None
    if isinstance(v, list) and v and isinstance(v[0], (int, float)):
        return int(v[0])
    return None


def _iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------- 历史合并
def merge_history(recs, hist_path, geo, hours):
    """
    与历史 trend 维度表合并，算出 first_seen_at / last_seen_at / duration_hours / persistence。

    注意：生命周期只在「同 geo + 同时间窗口」的口径内比较。
    切换窗口（如 24h -> 4h）会导致大量热点不在榜，但这不代表它们 Ended，
    所以必须按 geo + window_hours 分组，否则会把全部热点误判为 Ended。
    """
    hist = {}
    if os.path.exists(hist_path):
        with open(hist_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    hist[d["trend_id"]] = d

    current_ids = set()
    for rec in recs:
        tid = rec["trend_id"]
        current_ids.add(tid)
        h = hist.get(tid)
        same_scope = h and h.get("geo") == geo and h.get("window_hours") == hours
        if same_scope:
            first = h.get("first_seen_at")
            if not first or first < rec["first_seen_at"]:
                first = h.get("first_seen_at")
            rec["first_seen_at"] = first or rec["snapshot_time"]
            # 连续出现次数：同 scope 且上一轮也算过才累加
            rec["persistence"] = (h.get("persistence") or 1) + 1
        else:
            rec["persistence"] = 1
            rec["first_seen_at"] = rec["snapshot_time"]
        rec["last_seen_at"] = rec["snapshot_time"]
        try:
            a = datetime.fromisoformat(rec["first_seen_at"])
            b = datetime.fromisoformat(rec["snapshot_time"])
            rec["duration_hours"] = round((b - a).total_seconds() / 3600, 2)
        except Exception:
            rec["duration_hours"] = None
        rec["status"] = "Active"
        hist[tid] = rec

    # 同范围内上一轮在榜、本轮消失的 -> 判定为 Ended
    for tid, h in hist.items():
        if (tid not in current_ids
                and h.get("geo") == geo
                and h.get("window_hours") == hours
                and h.get("status") != "Ended"):
            h["status"] = "Ended"

    return recs, list(hist.values())


# ---------------------------------------------------------------- 主流程
def collect(geo="US", hours=24, log=print):
    """执行一次采集，返回 payload。供 CLI 与流水线共用。"""
    os.makedirs(SNAP_DIR, exist_ok=True)
    t0 = time.time()

    log(f"抓取 trends.google.com/trending?geo={geo}&hours={hours} ...")
    html = fetch_page(geo, hours)
    log(f"  HTML {len(html):,} 字符，耗时 {time.time()-t0:.1f}s")

    log("解析内嵌 ds:0 数据块 ...")
    data = parse_ds0(html)
    raw_list = data[1] if len(data) > 1 and isinstance(data[1], list) else []
    if not raw_list:
        raise RuntimeError("解析到 0 条热点，页面结构可能已变化")
    log(f"  原始热点 {len(raw_list)} 条")

    log("归一化 + 词典打标 ...")
    snap_dt = datetime.now(timezone(timedelta(hours=8)))
    recs = normalize(raw_list, geo, hours, snap_dt)

    hist_path = os.path.join(DATA_DIR, "dim_trend.jsonl")
    recs, hist_rows = merge_history(recs, hist_path, geo, hours)

    log("落盘快照 / 维度表 / latest ...")
    snap_file = os.path.join(
        SNAP_DIR,
        f"snapshot_{geo}_{hours}h_{snap_dt.strftime('%Y%m%dT%H%M%S')}.jsonl")
    with open(snap_file, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(hist_path, "w", encoding="utf-8") as f:
        for r in hist_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    payload = {"snapshot_time": snap_dt.isoformat(),
               "geo": geo, "window_hours": hours,
               "count": len(recs), "trends": recs,
               "snapshot_file": os.path.basename(snap_file)}
    with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    cats = {}
    for r in recs:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    log(f"完成：{len(recs)} 条热点，快照 {os.path.basename(snap_file)}")
    log("  类别分布 " + json.dumps(dict(sorted(cats.items(), key=lambda x: -x[1])),
                                  ensure_ascii=False))
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--geo", default="US")
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()
    collect(args.geo, args.hours)


if __name__ == "__main__":
    main()
