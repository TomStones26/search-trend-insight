"""
多维分析引擎
============
职责：把「一句自然语言需求」变成「结构化分析计划」，再按计划算出多维结果。

关键设计（防止报告"看起来很专业但数字是编的"）：
  - **所有数值一律由本模块用脚本算出**，LLM 只参与两步：
      ① 需求理解（把中文口语变成 filters/dimensions/metrics 结构）
      ② 结论解释（在已算好的数字上写人话）
  - 每条结论都带 evidence（引用某个图表 ID + 具体 trend_id），可追溯、可核对
  - LLM 不可用时，需求理解走本地规则解析器，结论走模板生成，功能不降级

对外主函数：
    plan = parse_intent(text, taxonomy, llm)          -> dict
    result = run_analysis(plan, payload, taxonomy)    -> dict
"""

import json
import math
import os
import re
from collections import Counter, defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------ 词典别名
# 数据集本身的地域词。这些词出现在 keyword 筛选里没有意义 —— 它们会命中
# 全部数据（数据源就是该地区），既浪费了筛选也让用户误以为做过筛选。
GEO_WORDS = {"美国", "全美", "美國", "美", "us", "usa", "u.s.", "u.s.a.",
             "united states", "america", "american", "北美", "美区"}

CAT_ALIASES = {
    "Sports": ["体育", "运动", "赛事", "比赛", "球", "sport", "sports"],
    "Entertainment": ["娱乐", "影视", "综艺", "明星", "音乐", "八卦", "entertainment"],
    "Politics": ["政治", "政务", "选举", "政府", "国会", "politics"],
    "Society": ["时事", "社会", "民生", "节日", "媒体", "新闻", "society"],
    "Technology": ["科技", "数码", "技术", "ai", "人工智能", "手机", "电脑", "technology"],
    "Gaming": ["游戏", "电竞", "主机", "gaming", "game"],
    "Business": ["企业", "商业", "就业", "公司", "裁员", "business"],
    "Finance": ["金融", "股市", "股票", "加密", "货币", "利率", "finance", "stock"],
    "Consumer": ["消费", "电商", "零售", "品牌", "商品", "促销", "联名", "commerce", "retail"],
    "Travel": ["旅行", "旅游", "出行", "航空", "酒店", "travel"],
    "Health": ["健康", "医疗", "疾病", "药品", "health"],
    "Weather": ["天气", "自然", "灾害", "气象", "weather"],
}

FOCUS_RULES = [
    ("growth", ["增长最快", "增长", "爆发", "飙升", "升温", "上升", "涨得快", "增速", "growth",
                "growth rate", "fastest"]),
    ("volume", ["搜索量", "最热", "热度", "最高", "排名", "排行", "top", "榜", "volume",
                "最受关注", "多少人在搜"]),
    ("commerce", ["品牌", "消费", "电商", "商机", "商品", "零售", "卖", "销量", "购买",
                  "brand", "commerce", "consumer", "购买意愿"]),
    ("lifecycle", ["生命周期", "持续", "时长", "多久", "消退", "退潮", "结束", "生命力",
                   "lifecycle", "duration", "persistence"]),
    ("structure", ["结构", "分布", "占比", "构成", "板块", "类别", "composition", "share"]),
    ("cause", ["为什么", "原因", "背后", "关联词", "相关搜索", "why", "reason", "驱动"]),
    ("risk", ["异常", "风险", "警惕", "突变", "异动", "anomaly", "risk"]),
    ("compare", ["对比", "比较", "相比", "差异", "versus", "compare"]),
]


# ------------------------------------------------------------------ 需求解析
def parse_intent(text, taxonomy, llm=None):
    """把自然语言需求解析成分析计划。优先 LLM，失败降级规则。"""
    text = (text or "").strip()
    if not text:
        text = "今天美国互联网整体搜索热点概览"

    plan = None
    engine = "rule"
    if llm is not None and llm.available:
        plan = _llm_intent(text, taxonomy, llm)
        if plan:
            engine = "llm"

    if not plan:
        plan = _rule_intent(text, taxonomy)

    plan["raw_input"] = text
    plan["engine"] = engine
    plan.setdefault("top_n", 10)
    plan.setdefault("filters", {})
    plan.setdefault("focus", ["volume", "growth"])
    plan.setdefault("dimensions", [])
    plan.setdefault("focus_questions", [])
    plan["intent_summary"] = plan.get("intent_summary") or _summary_from_plan(plan, taxonomy)
    return plan


def _llm_intent(text, taxonomy, llm):
    cats = {cid: {"label": c["label"], "desc": c["desc"]}
            for cid, c in taxonomy.cats.items()}
    system = (
        "你是数据产品的需求解析器。把用户的一句话需求转成 JSON 分析计划。"
        "只输出 JSON，不要解释。字段：\n"
        '{"intent_summary": "一句话复述你理解的需求(中文)",'
        '"filters": {"categories": ["类别ID"], "min_growth": null, "min_volume": null,'
        ' "brand_only": false, "keyword": null},'
        '"focus": ["volume","growth","commerce","lifecycle","structure","cause","risk"],'
        '"dimensions": ["category","brand","quadrant","momentum"],'
        '"top_n": 10,'
        '"focus_questions": ["准备回答的问题1","问题2","问题3"]}\n'
        "categories 只能从给定类别ID里选，无筛选就留空数组。"
        "数值阈值拿不准就填 null，不要编造。\n"
        "重要：数据本身就是美国地区的 Google Trends，所以「美国/US/全美」这类"
        "地区词不要填进 keyword，也不要用它做筛选 —— 填了会把全部数据都算作命中，"
        "等于没筛。keyword 只放用户明确想看的主题词（如某产品、某事件）。"
        "用户没说具体品类时，categories 留空数组，让分析覆盖全量。"
    )
    user = ("可用类别ID与含义：" + json.dumps(cats, ensure_ascii=False)
            + "\n\n用户需求：" + text)
    # 预算要覆盖「思维链 + 正文」——推理型模型的思维链也计入 max_tokens。
    out = llm.json_chat(system, user, max_tokens=1200)
    if not isinstance(out, dict):
        return None
    out.setdefault("filters", {})
    if not isinstance(out.get("filters"), dict):
        out["filters"] = {}
    cats_valid = [c for c in (out["filters"].get("categories") or [])
                  if c in taxonomy.cats]
    out["filters"]["categories"] = cats_valid

    # 兜底：模型偶尔会把地区词（「美国」「US」）填进 keyword。数据本身就是
    # 该地区的，这样的筛选会命中全部数据、等于没筛，还让用户以为筛过了。
    kw = out["filters"].get("keyword")
    if kw and str(kw).strip().lower() in GEO_WORDS:
        out["filters"]["keyword"] = None
    return out


def _rule_intent(text, taxonomy):
    low = text.lower()

    cats = []
    for cid, aliases in CAT_ALIASES.items():
        if cid not in taxonomy.cats:
            continue
        for a in aliases:
            if a in low:
                cats.append(cid)
                break
    cats = list(dict.fromkeys(cats))

    focus = []
    for name, kws in FOCUS_RULES:
        if any(k in low for k in kws):
            focus.append(name)
    if not focus:
        focus = ["volume", "growth"]

    top_n = 10
    m = re.search(r"(?:top|前|头)\s*(\d{1,2})", low) or re.search(r"(\d{1,2})\s*(?:个|条|名|款)", low)
    if m:
        top_n = max(3, min(30, int(m.group(1))))

    min_growth = min_volume = None
    m = re.search(r"(?:增长|增速|growth)\D{0,6}?(\d{2,5})\s*%?", low)
    if m:
        min_growth = int(m.group(1))
    m = re.search(r"(?:搜索量|volume)\D{0,6}?(\d{2,7})", low)
    if m:
        min_volume = int(m.group(1))

    brand_only = bool(re.search(r"只看品牌|仅品牌|品牌相关|brand only", low))

    filters = {"categories": cats, "min_growth": min_growth, "min_volume": min_volume,
               "brand_only": brand_only, "keyword": None}
    m = re.search(r"[「\"']([^」\"']{2,20})[」\"']", text)
    if m:
        filters["keyword"] = m.group(1)

    return {"filters": filters, "focus": focus, "top_n": top_n,
            "dimensions": _dims_from_focus(focus),
            "focus_questions": _questions(plan_hint={"focus": focus, "cats": cats},
                                          taxonomy=taxonomy, text=text)}


def _dims_from_focus(focus):
    dims = []
    if "volume" in focus or "growth" in focus:
        dims += ["category", "quadrant"]
    if "commerce" in focus:
        dims += ["brand", "commerce_type"]
    if "lifecycle" in focus:
        dims += ["lifecycle", "persistence"]
    if "structure" in focus:
        dims += ["category", "concentration"]
    if "cause" in focus:
        dims += ["related_queries"]
    if "compare" in focus:
        dims += ["category_compare"]
    return list(dict.fromkeys(dims))


def _questions(plan_hint, taxonomy, text):
    qs = []
    cats = plan_hint.get("cats") or []
    scope = "、".join(taxonomy.cats[c]["label"] for c in cats) if cats else "全类别"
    if "growth" in plan_hint["focus"]:
        qs.append(f"{scope}里哪些热点正在快速升温？")
    if "volume" in plan_hint["focus"]:
        qs.append(f"{scope}里当前搜索量最大的是哪些？")
    if "commerce" in plan_hint["focus"]:
        qs.append("哪些品牌或商品出现了可跟踪的消费需求信号？")
    if "lifecycle" in plan_hint["focus"]:
        qs.append("这些热点能持续多久？哪些只是昙花一现？")
    if "structure" in plan_hint["focus"]:
        qs.append("热点在内容结构上如何分布？集中在少数板块吗？")
    if "cause" in plan_hint["focus"]:
        qs.append("这些热点为什么会火？用户到底在搜什么？")
    if not qs:
        qs = ["整体热度规模如何？", "增长最快的是哪些？", "有哪些值得跟进的信号？"]
    return qs[:5]


def _summary_from_plan(plan, taxonomy):
    cats = plan.get("filters", {}).get("categories") or []
    scope = "、".join(taxonomy.cats[c]["label"] for c in cats) if cats else "全类别"
    f = plan.get("focus", [])
    labels = {"volume": "热度排名", "growth": "增长动能", "commerce": "消费与品牌信号",
              "lifecycle": "生命周期", "structure": "内容结构", "cause": "成因分析",
              "risk": "异常识别", "compare": "类别对比"}
    want = "、".join(labels.get(x, x) for x in f)
    return f"围绕「{scope}」，重点做{want}方面的分析"


# ------------------------------------------------------------------ 工具
def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return 0
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def apply_filters(trends, f):
    out = []
    cats = set(f.get("categories") or [])
    mg = f.get("min_growth")
    mv = f.get("min_volume")
    kw = f.get("keyword")
    for t in trends:
        if cats and t.get("category") not in cats and not (set(t.get("categories") or []) & cats):
            continue
        if mg is not None and (t.get("growth_rate") or 0) < mg:
            continue
        if mv is not None and (t.get("search_volume") or 0) < mv:
            continue
        if f.get("brand_only") and not t.get("brand"):
            continue
        if kw:
            blob = (t.get("trend", "") + " " + " ".join(t.get("related_queries") or [])).lower()
            if kw.lower() not in blob:
                continue
        out.append(t)
    return out


def momentum_score(t):
    """Momentum = log10(搜索量+1) × 增长率。属于自建指标，非官方。"""
    v = t.get("search_volume") or 0
    g = t.get("growth_rate") or 0
    return math.log10(v + 1) * g


def quadrants(trends):
    """按搜索量中位数 / 增长率中位数切四象限。"""
    vols = [t.get("search_volume") or 0 for t in trends]
    grs = [t.get("growth_rate") or 0 for t in trends]
    vref, gref = median(vols), median(grs)
    labels = {("hi", "hi"): "爆发型", ("hi", "lo"): "成熟型",
              ("lo", "hi"): "潜在型", ("lo", "lo"): "长尾型"}
    out = defaultdict(list)
    for t in trends:
        v = t.get("search_volume") or 0
        g = t.get("growth_rate") or 0
        key = ("hi" if v >= vref else "lo", "hi" if g >= gref else "lo")
        out[labels[key]].append(t)
    return out, vref, gref


# ------------------------------------------------------------------ 主分析
def run_analysis(plan, payload, taxonomy, history=None, log=None):
    log = log or (lambda *a: None)
    all_trends = payload.get("trends") or []
    sel = apply_filters(all_trends, plan.get("filters", {}))
    if not sel:
        log("筛选后为空，自动回退到全量数据集")
        plan["filters"]["categories"] = []
        plan["filters"]["min_growth"] = None
        plan["filters"]["min_volume"] = None
        plan["filters"]["brand_only"] = False
        sel = list(all_trends)

    top_n = int(plan.get("top_n") or 10)
    cat_meta = {cid: {"label": c["label"], "color": c["color"]}
                for cid, c in taxonomy.cats.items()}
    cat_meta["Other"] = {"label": "其他", "color": "#94A3B8"}

    # ---- KPI
    vols = [t["search_volume"] for t in sel if t.get("search_volume")]
    grs = [t["growth_rate"] for t in sel if t.get("growth_rate")]
    consumer = [t for t in sel if t.get("is_consumer")]
    emerging = [t for t in sel
                if (t.get("growth_rate") or 0) >= 500 and (t.get("search_volume") or 0) >= 5000]
    n = max(len(sel), 1)
    kpi = [
        {"key": "count", "label": "热点条数", "value": len(sel),
         "note": f"占全量 {len(all_trends)} 条的 {len(sel)/max(len(all_trends),1)*100:.0f}%"},
        {"key": "volume", "label": "搜索量合计", "value": _fmt_k(sum(vols)),
         "note": "估算桶值加总，非精确值"},
        {"key": "median_growth", "label": "增长率中位数", "value": f"{median(grs):g}%",
         "note": "相对预测基线"},
        {"key": "median_volume", "label": "搜索量中位数", "value": _fmt_k(median(vols)),
         "note": "桶值中位"},
        {"key": "consumer", "label": "消费相关", "value": len(consumer),
         "note": f"占比 {len(consumer)/n*100:.1f}%"},
        {"key": "emerging", "label": "新兴信号", "value": len(emerging),
         "note": "增长≥500% 且搜索量≥5K"},
    ]

    # ---- 类别结构
    cat_stat = defaultdict(lambda: {"count": 0, "volume": 0, "growth_sum": 0})
    for t in sel:
        c = cat_stat[t.get("category") or "Other"]
        c["count"] += 1
        c["volume"] += t.get("search_volume") or 0
        c["growth_sum"] += t.get("growth_rate") or 0
    cat_rows = []
    for cid, s in sorted(cat_stat.items(), key=lambda x: -x[1]["volume"]):
        cat_rows.append({
            "cat": cid, "label": cat_meta.get(cid, {}).get("label", cid),
            "color": cat_meta.get(cid, {}).get("color", "#94A3B8"),
            "count": s["count"], "volume": s["volume"],
            "count_share": round(s["count"] / n * 100, 1),
            "volume_share": round(s["volume"] / max(sum(vols), 1) * 100, 1),
            "avg_growth": round(s["growth_sum"] / max(s["count"], 1), 1),
        })

    # ---- 榜单
    by_vol = sorted(sel, key=lambda x: -(x.get("search_volume") or 0))
    by_growth = sorted(sel, key=lambda x: (-(x.get("growth_rate") or 0),
                                           -(x.get("search_volume") or 0)))
    top_volume = [_row(t) for t in by_vol[:max(top_n, 15)]]
    top_growth = [_row(t) for t in by_growth[:max(top_n, 15)]]

    # ---- 四象限
    q, vref, gref = quadrants(sel)
    q_series = defaultdict(list)
    for name, ts in q.items():
        for t in ts:
            q_series[name].append({
                "name": t["trend"], "cat": t.get("category"),
                "value": [t.get("growth_rate") or 0, t.get("search_volume") or 0],
                "related": t.get("related_query_count") or 0,
                "brand": t.get("brand"), "id": t.get("trend_id"),
            })
    quadrant = {
        "series": [{"name": k, "data": v} for k, v in
                   sorted(q_series.items(), key=lambda x: -len(x[1]))],
        "x_ref": gref, "y_ref": vref,
        "counts": {k: len(v) for k, v in q.items()},
    }

    # ---- Momentum
    mom = sorted(sel, key=lambda t: -momentum_score(t))
    mmax = max((momentum_score(t) for t in sel), default=1) or 1
    momentum = [{"name": t["trend"], "score": round(momentum_score(t) / mmax * 100, 1),
                 "cat": t.get("category"), "volume": t.get("search_volume"),
                 "growth": t.get("growth_rate"), "id": t.get("trend_id")}
                for t in mom[:top_n]]

    # ---- 品牌热度
    brand_stat = defaultdict(lambda: {"volume": 0, "growth": 0, "count": 0,
                                      "type": None, "trends": []})
    for t in sel:
        b = t.get("brand")
        if not b:
            continue
        s = brand_stat[b]
        s["volume"] += t.get("search_volume") or 0
        s["growth"] += t.get("growth_rate") or 0
        s["count"] += 1
        s["type"] = s["type"] or t.get("brand_type")
        s["trends"].append({"name": t["trend"], "volume": t.get("search_volume"),
                            "growth": t.get("growth_rate"), "id": t.get("trend_id")})
    brand_heat = []
    for b, s in brand_stat.items():
        heat = math.log10(s["volume"] + 1) * (s["growth"] / max(s["count"], 1))
        brand_heat.append({"brand": b, "type": s["type"], "volume": s["volume"],
                           "avg_growth": round(s["growth"] / max(s["count"], 1), 1),
                           "count": s["count"], "heat": round(heat, 1),
                           "trends": s["trends"]})
    brand_heat.sort(key=lambda x: -x["heat"])
    for b in brand_heat:
        b["heat_norm"] = round(b["heat"] / max(brand_heat[0]["heat"], 1) * 100, 1)

    # ---- 消费子类
    ct = Counter(t.get("commerce_type") for t in sel if t.get("commerce_type"))
    commerce_types = [{"type": k, "count": v,
                       "label": taxonomy.commerce_labels.get(k, k)}
                      for k, v in ct.most_common()]

    # ---- 关联词网络（取搜索量 Top N 的热点）
    net_nodes, net_links, seen = [], [], set()
    for t in by_vol[:min(top_n, 8)]:
        rqs = (t.get("related_queries") or [])[:8]
        if not rqs:
            continue
        tid = "T:" + (t.get("trend_id") or t["trend"])
        if tid not in seen:
            seen.add(tid)
            net_nodes.append({"id": tid, "name": t["trend"], "kind": "trend",
                              "cat": t.get("category"),
                              "size": t.get("related_query_count") or 1})
        for rq in rqs:
            qid = "Q:" + rq.lower()
            if qid not in seen:
                seen.add(qid)
                net_nodes.append({"id": qid, "name": rq, "kind": "query", "size": 1})
            net_links.append({"source": tid, "target": qid})

    # ---- 生命周期 / 时间线（依赖历史快照）
    hist_rows = history or []
    dur_buckets = [{"bucket": "<2h", "count": 0}, {"bucket": "2-6h", "count": 0},
                   {"bucket": "6-12h", "count": 0}, {"bucket": "12-24h", "count": 0},
                   {"bucket": ">24h", "count": 0}]
    persistence = []
    if hist_rows:
        for r in hist_rows:
            d = r.get("duration_hours")
            if isinstance(d, (int, float)):
                if d < 2:
                    dur_buckets[0]["count"] += 1
                elif d < 6:
                    dur_buckets[1]["count"] += 1
                elif d < 12:
                    dur_buckets[2]["count"] += 1
                elif d < 24:
                    dur_buckets[3]["count"] += 1
                else:
                    dur_buckets[4]["count"] += 1
        persistence = [{"name": r["trend"], "persistence": r.get("persistence") or 1,
                        "duration": r.get("duration_hours"), "status": r.get("status"),
                        "id": r.get("trend_id")}
                       for r in sorted(hist_rows,
                                       key=lambda x: -(x.get("persistence") or 0))[:top_n]]

    # ---- 新兴信号表
    emerging_rows = [{"name": t["trend"], "cat": t.get("category"),
                      "cat_label": cat_meta.get(t.get("category"), {}).get("label"),
                      "volume": t.get("search_volume"), "growth": t.get("growth_rate"),
                      "brand": t.get("brand"), "related": t.get("related_query_count"),
                      "related_queries": (t.get("related_queries") or [])[:6],
                      "id": t.get("trend_id")}
                     for t in sorted(emerging,
                                     key=lambda x: (-(x.get("growth_rate") or 0),
                                                    -(x.get("search_volume") or 0)))]

    # ---- 集中度
    total_v = sum(vols) or 1
    top10_v = sum(sorted(vols, reverse=True)[:10])
    concentration = {
        "top10_volume_share": round(top10_v / total_v * 100, 1),
        "hhi": round(sum((v / total_v * 100) ** 2 for v in vols), 1),
        "cat_count": len(cat_rows),
    }

    result = {
        "plan": plan,
        "scope": {"total_in_dataset": len(all_trends), "selected": len(sel)},
        "kpi": kpi,
        "cat_rows": cat_rows,
        "top_volume": top_volume,
        "top_growth": top_growth,
        "quadrant": quadrant,
        "momentum": momentum,
        "brand_heat": brand_heat[:top_n],
        "commerce_types": commerce_types,
        "emerging": emerging_rows,
        "network": {"nodes": net_nodes, "links": net_links} if net_nodes else None,
        "lifecycle": {"duration_buckets": dur_buckets,
                      "persistence_top": persistence,
                      "available": bool(hist_rows)},
        "concentration": concentration,
        "cat_meta": cat_meta,
        "window": {"geo": payload.get("geo"), "window_hours": payload.get("window_hours"),
                   "snapshot_time": payload.get("snapshot_time")},
    }
    result["findings"] = build_findings(result, taxonomy)
    return result


def _row(t):
    return {"name": t["trend"], "id": t.get("trend_id"),
            "volume": t.get("search_volume"), "growth": t.get("growth_rate"),
            "cat": t.get("category"),
            "cat_label": t.get("category_label"),
            "color": t.get("category_color"),
            "brand": t.get("brand"), "related": t.get("related_query_count"),
            "confidence": t.get("classify_confidence")}


def _fmt_k(v):
    v = v or 0
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K" if v >= 100_000 else f"{v/1_000:.1f}K"
    return str(v)


# ------------------------------------------------------------------ 结论生成
def build_findings(res, taxonomy):
    """基于已算好的数字生成结论，每条带 evidence 便于核对。LLM 不参与。"""
    f = []
    sel = res["scope"]["selected"]
    kpi = {k["key"]: k for k in res["kpi"]}
    cats = res["cat_rows"]
    q = res["quadrant"]
    win = res["window"]

    f.append({
        "title": "热度规模",
        "text": (f"最近 {win.get('window_hours')} 小时，US 在榜热点 {sel} 条，"
                 f"估算搜索量合计 {kpi['volume']['value']}，"
                 f"搜索量中位数 {kpi['median_volume']['value']}，"
                 f"增长率中位数 {kpi['median_growth']['value']}。"),
        "evidence": {"chart": "kpi"},
        "confidence": "high",
    })

    if cats:
        c0 = cats[0]
        f.append({
            "title": "结构集中在哪",
            "text": (f"按搜索量看，{c0['label']}是最大板块：{c0['count']} 条热点、"
                     f"贡献 {c0['volume_share']}% 的搜索量。"
                     + (f"第二名{cats[1]['label']}占 {cats[1]['volume_share']}%。"
                        if len(cats) > 1 else "")),
            "evidence": {"chart": "category", "cat": c0["cat"]},
            "confidence": "high",
        })

    if q["counts"]:
        burst = q["counts"].get("爆发型", 0)
        emit = q["counts"].get("成熟型", 0)
        pot = q["counts"].get("潜在型", 0)
        tail = q["counts"].get("长尾型", 0)
        f.append({
            "title": "四象限分层",
            "text": (f"以搜索量中位数 {_fmt_k(q['y_ref'])}、增长率中位数 {q['x_ref']:g}% 为切分线："
                     f"爆发型 {burst} 条、成熟型 {emit} 条、潜在型 {pot} 条、长尾型 {tail} 条。"
                     f"爆发型量级与增速同时在线，是当期最值得跟的信号。"),
            "evidence": {"chart": "quadrant"},
            "confidence": "high",
        })

    if res["momentum"]:
        m = res["momentum"][0]
        f.append({
            "title": "势能最高",
            "text": (f"按自建 Momentum Score（log(搜索量+1)×增长率）排序，"
                     f"「{m['name']}」势能最高（{m['score']}/100，搜索量 "
                     f"{_fmt_k(m['volume'])}、增长 {m['growth']}%）。"),
            "evidence": {"chart": "momentum", "trend_id": m.get("id")},
            "confidence": "medium",
        })

    if res["brand_heat"]:
        b = res["brand_heat"][0]
        f.append({
            "title": "品牌信号",
            "text": (f"识别出 {len(res['brand_heat'])} 个品牌出现在热点中，"
                     f"热度最高的是 {b['brand']}（{b['type'] or '未分类'}，"
                     f"关联 {b['count']} 条热点、搜索量合计 {_fmt_k(b['volume'])}）。"
                     f"注意：品牌热度反映的是「搜索关注度」，不等于销量。"),
            "evidence": {"chart": "brand", "brand": b["brand"]},
            "confidence": "medium",
        })

    if res["emerging"]:
        e = res["emerging"][0]
        f.append({
            "title": "新兴信号",
            "text": (f"有 {len(res['emerging'])} 条热点满足「增长≥500% 且搜索量≥5K」，"
                     f"读作正在快速起量而不是已经到顶。最靠前的是「{e['name']}」"
                     f"（增长 {e['growth']}%，搜索量 {_fmt_k(e['volume'])}）。"),
            "evidence": {"chart": "emerging", "trend_id": e.get("id")},
            "confidence": "medium",
        })

    lc = res.get("lifecycle") or {}
    if lc.get("available"):
        top = (lc.get("persistence_top") or [])
        if top:
            f.append({
                "title": "生命周期",
                "text": (f"历史快照显示，持续在榜最久的是「{top[0]['name']}」"
                         f"（连续 {top[0]['persistence']} 次快照在榜）。"
                         f"生命周期指标依赖长期累积，当前样本还偏少，结论只做参考。"),
                "evidence": {"chart": "lifecycle", "trend_id": top[0].get("id")},
                "confidence": "low",
            })
    else:
        f.append({
            "title": "生命周期暂不可用",
            "text": ("本次只有单期快照，duration / persistence 这类变化类指标拿不到，"
                     "需要定时采集累积 2 次以上。这部分不是功能缺失，是数据前提不满足。"),
            "evidence": {"chart": None},
            "confidence": "high",
        })

    conc = res["concentration"]
    f.append({
        "title": "集中度",
        "text": (f"搜索量 Top 10 的热点占了全部估算搜索量的 {conc['top10_volume_share']}%，"
                 f"覆盖 {conc['cat_count']} 个类别。"
                 + ("头部集中度较高，少量热点主导了当期热度。"
                    if conc["top10_volume_share"] >= 50
                    else "热度分布相对分散，没有单一热点吞掉大盘。")),
        "evidence": {"chart": "category"},
        "confidence": "high",
    })
    return f
