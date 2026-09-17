"""
报告生成
=======
把分析结果组装成一份可读的分析报告。

防"编数字"的三层设计：
  1) 报告里所有数值都来自 analyze.py 已算好的结果，正文用脚本模板生成
  2) LLM 只被允许写「执行摘要」和「行动建议」两段，且 prompt 明确禁止引入新数字
  3) 每段结论都带 evidence，指向具体图表或 trend_id，可点回看板核对
"""

import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_CAVEATS = [
    "搜索量为 Google 提供的估算区间桶值（100 ~ 500,000+，共 12 档），非精确值，"
    "「搜索量合计」只能读作量级参考。",
    "增长率为相对预测基线的离散档位（50/75/100/…/1000），"
    "「增长率中位数」是在桶值上取中位。",
    "类别与品牌由本地词典规则标注（config/taxonomy.json），"
    "源站不提供这两个字段。词典可迭代，误标会随样本积累暴露。",
    "生命周期类指标（duration / persistence / 每小时新增）需要多期快照累积，"
    "单期快照无法计算。",
    "品牌热度衡量的是搜索关注度，**不等同于销量或营收**。",
]


def build_report(analysis, llm=None, log=None):
    log = log or (lambda *a: None)
    res = analysis
    plan = res["plan"]
    win = res["window"]
    sel = res["scope"]["selected"]
    kpi = {k["key"]: k for k in res["kpi"]}
    cats = res["cat_rows"]
    quad = res["quadrant"]

    sections = []

    # ---------------- 一、市场概览
    overview_bits = [
        f"本次分析覆盖 US 地区最近 {win.get('window_hours')} 小时的在榜热点 {sel} 条"
        f"（全量数据集 {res['scope']['total_in_dataset']} 条，筛选后保留 {sel} 条）。",
        f"估算搜索量合计 {kpi['volume']['value']}，搜索量中位数 {kpi['median_volume']['value']}，"
        f"增长率中位数 {kpi['median_growth']['value']}。",
    ]
    if cats:
        overview_bits.append(
            "搜索量最大的板块是" + cats[0]["label"]
            + f"（{cats[0]['count']} 条热点，占搜索量 {cats[0]['volume_share']}%）"
            + (f"，其次是{cats[1]['label']}（{cats[1]['volume_share']}%）。"
               if len(cats) > 1 else "。"))
    overview_bits.append(
        f"消费相关热点 {kpi['consumer']['value']} 条（{kpi['consumer']['note']}），"
        f"满足新兴信号阈值的有 {kpi['emerging']['value']} 条。")
    sections.append({
        "id": "overview", "title": "一、市场概览",
        "narrative": "".join(overview_bits),
        "charts": ["kpi", "category_share", "category_count"],
        "evidence": [{"chart": "kpi"}, {"chart": "category"}],
    })

    # ---------------- 二、增长动能
    growth_bits = []
    if res["top_growth"]:
        g0 = res["top_growth"][0]
        growth_bits.append(
            f"增长最快的是「{g0['name']}」，增长率 {g0['growth']}%，"
            f"当前搜索量 {_fmt(g0['volume'])}，类别 {g0['cat_label']}。")
        head = "、".join(f"「{x['name']}」" for x in res["top_growth"][:5])
        growth_bits.append(f"增长前五依次是：{head}。")
    counts = quad.get("counts") or {}
    growth_bits.append(
        f"四象限分层结果：爆发型 {counts.get('爆发型', 0)} 条、"
        f"成熟型 {counts.get('成熟型', 0)} 条、潜在型 {counts.get('潜在型', 0)} 条、"
        f"长尾型 {counts.get('长尾型', 0)} 条"
        f"（切分线：搜索量中位数 {_fmt(quad.get('y_ref'))}，"
        f"增长率中位数 {quad.get('x_ref')}%）。")
    growth_bits.append(
        "读法：爆发型 = 量级与增速同时在线，当期最值得跟进；"
        "潜在型 = 目前量小但增速极高，是下一阶段的候选热点；"
        "成熟型 = 大盘热点但增速已平缓；长尾型可暂不投入。")
    if res["momentum"]:
        m = res["momentum"][0]
        growth_bits.append(
            f"按自建 Momentum Score（log(搜索量+1)×增长率）排序，"
            f"势能最高的是「{m['name']}」，得分 {m['score']}/100。"
            f"该指标为本项目自定义，非 Google 官方指标。")
    sections.append({
        "id": "growth", "title": "二、增长动能与热点分层",
        "narrative": "".join(growth_bits),
        "charts": ["top_growth", "quadrant", "momentum"],
        "evidence": [{"chart": "quadrant"},
                     {"chart": "momentum",
                      "trend_id": (res["momentum"][0]["id"] if res["momentum"] else None)}],
    })

    # ---------------- 三、内容结构
    struct_bits = [f"共覆盖 {len(cats)} 个内容板块。"]
    if cats:
        struct_bits.append("按搜索量排序："
                           + "、".join(f"{c['label']} {c['volume_share']}%"
                                       for c in cats[:6]) + "。")
        struct_bits.append("按热点条数排序："
                           + "、".join(f"{c['label']} {c['count']} 条"
                                       for c in sorted(cats, key=lambda x: -x["count"])[:6]) + "。")
        struct_bits.append(
            "注意两者并不一致——条数多不等于关注量高。"
            "例如某板块可能有大量长尾热点，但合计搜索量并不突出。")
    conc = res["concentration"]
    struct_bits.append(
        f"集中度方面，搜索量 Top 10 的热点占全部估算搜索量的 "
        f"{conc['top10_volume_share']}%，覆盖 {conc['cat_count']} 个类别。")
    sections.append({
        "id": "structure", "title": "三、内容结构",
        "narrative": "".join(struct_bits),
        "charts": ["category_share", "category_count"],
        "evidence": [{"chart": "category"}],
    })

    # ---------------- 四、消费与品牌信号
    cons_bits = []
    if res["brand_heat"]:
        cons_bits.append(
            f"识别出 {len(res['brand_heat'])} 个品牌出现在热点中，"
            "按 Brand Heat（log(搜索量+1)×平均增长率）排序前五："
            + "、".join(f"{b['brand']}（{b['heat_norm']:.0f}）"
                        for b in res["brand_heat"][:5]) + "。")
        top_b = res["brand_heat"][0]
        cons_bits.append(
            f"其中 {top_b['brand']} 关联 {top_b['count']} 条热点，"
            f"搜索量合计 {_fmt(top_b['volume'])}，平均增长率 {top_b['avg_growth']}%。")
    else:
        cons_bits.append("本次筛选范围内没有识别到品牌相关热点。")
    if res["commerce_types"]:
        cons_bits.append("消费子类分布："
                         + "、".join(f"{c['label']} {c['count']} 条"
                                     for c in res["commerce_types"][:6]) + "。")
    if res["emerging"]:
        cons_bits.append(
            f"新兴消费信号 {len(res['emerging'])} 条，"
            "最靠前的是「" + res["emerging"][0]["name"] + "」。")
    cons_bits.append("提醒：品牌热度代表的是搜索关注度，不能直接读作销量或营收。"
                     "要与真实销售数据关联，还需要外部数据源。")
    sections.append({
        "id": "commerce", "title": "四、消费与品牌信号",
        "narrative": "".join(cons_bits),
        "charts": ["brand_heat", "emerging"],
        "evidence": [{"chart": "brand"},
                     {"chart": "emerging",
                      "trend_id": (res["emerging"][0]["id"] if res["emerging"] else None)}],
    })

    # ---------------- 五、成因线索
    cause_bits = []
    if res.get("network"):
        nodes = [n for n in res["network"]["nodes"] if n["kind"] == "trend"]
        cause_bits.append(
            f"对搜索量 Top {len(nodes)} 的热点展开关联搜索词，"
            f"共连出 {len(res['network']['nodes']) - len(nodes)} 个细分查询，形成需求网络图。"
            "关联词是判断「用户为什么突然搜这个」最直接的线索。")
        for n in nodes[:3]:
            qs = [l["target"][2:] for l in res["network"]["links"] if l["source"] == n["id"]]
            if qs:
                cause_bits.append(f"「{n['name']}」的细分查询集中在："
                                  + "、".join(qs[:5]) + "。")
    else:
        cause_bits.append("本次范围内的热点未携带关联搜索词，无法展开成因分析。")
    cause_bits.append("把关联词与外部新闻源交叉，可以进一步定位是哪个事件驱动的。"
                      "这一步需要额外接入新闻数据源。")
    sections.append({
        "id": "cause", "title": "五、成因线索",
        "narrative": "".join(cause_bits),
        "charts": ["network"] if res.get("network") else [],
        "evidence": [{"chart": "network"}] if res.get("network") else [],
    })

    # ---------------- 六、数据缺口与风险
    risk_bits = []
    lc = res.get("lifecycle") or {}
    if not lc.get("available"):
        risk_bits.append("生命周期维度本次不可用：只有单期快照，"
                         "duration / persistence 无法计算，需要定时采集至少 2 期。")
    else:
        risk_bits.append("生命周期维度已有历史快照支撑，但样本期数仍偏少，结论仅供参考。")
    other_cat = next((c for c in cats if c["cat"] == "Other"), None)
    if other_cat:
        risk_bits.append(
            f"有 {other_cat['count']} 条热点（{other_cat['count_share']}%）未能归类，"
            "多为纯人名或无语义片段。这类样本需要实体识别能力才能进一步利用，"
            "靠关键词词典到此为止。")
    risk_bits.append("类别与品牌为词典规则标注，存在误标可能；"
                     "词典版本已记录，可随样本迭代。")
    sections.append({
        "id": "risk", "title": "六、数据缺口与风险",
        "narrative": "".join(risk_bits),
        "charts": [],
        "evidence": [{"chart": None}],
    })

    # ---------------- 七、结论与建议
    llm_out = _llm_summary(res, llm) if llm is not None else None
    if llm_out:
        summary = llm_out.get("executive_summary") or []
        recs = llm_out.get("recommendations") or []
        summary_engine = "llm"
    else:
        summary = _template_summary(res)
        recs = _template_recommendations(res)
        summary_engine = "rule"

    sections.append({
        "id": "conclusion", "title": "七、结论与建议",
        "narrative": "",
        "bullets": summary,
        "recommendations": recs,
        "charts": [],
        "evidence": [{"chart": "quadrant"}],
    })

    report = {
        "meta": {
            "title": "Google Trends US 实时热点分析报告",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "geo": win.get("geo"),
            "window_hours": win.get("window_hours"),
            "snapshot_time": win.get("snapshot_time"),
            "intent_summary": plan.get("intent_summary"),
            "raw_input": plan.get("raw_input"),
            "engine": {"intent": plan.get("engine"), "narrative": summary_engine},
            "selected": sel,
        },
        "executive_summary": summary,
        "sections": sections,
        "caveats": DATA_CAVEATS,
        "glossary": [
            {"term": "估算搜索量", "def": "Google 给出的区间桶值，非精确次数"},
            {"term": "增长率", "def": "相对预测基线的增长百分比，同样是离散档位"},
            {"term": "爆发型热点", "def": "搜索量与增长率双双高于中位数的热点"},
            {"term": "Momentum Score", "def": "log(搜索量+1)×增长率，本项目自建指标"},
            {"term": "Brand Heat", "def": "log(搜索量+1)×平均增长率，衡量品牌搜索关注度"},
            {"term": "Persistence", "def": "同一热点连续出现在快照中的次数"},
        ],
    }
    return report


def _template_summary(res):
    kpi = {k["key"]: k for k in res["kpi"]}
    cats = res["cat_rows"]
    counts = (res["quadrant"].get("counts") or {})
    out = [
        f"本次共分析 US 在榜热点 {res['scope']['selected']} 条，"
        f"估算搜索量合计 {kpi['volume']['value']}，增长率中位数 {kpi['median_growth']['value']}。",
    ]
    if cats:
        out.append(f"搜索量最大的板块是 {cats[0]['label']}，占 {cats[0]['volume_share']}%。")
    out.append(
        f"分层结果：爆发型 {counts.get('爆发型', 0)} 条、潜在型 {counts.get('潜在型', 0)} 条，"
        f"这两类是当期最值得跟的信号。")
    if res["brand_heat"]:
        out.append(f"品牌侧最强信号来自 {res['brand_heat'][0]['brand']}，"
                   f"但需与销售数据交叉后才能作为经营依据。")
    out.append("生命周期维度需要多期快照累积后才能给出结论，当前不作为判断依据。")
    return out


def _template_recommendations(res):
    counts = (res["quadrant"].get("counts") or {})
    recs = []
    if counts.get("爆发型"):
        recs.append("优先跟进爆发型热点：量级与增速同时在线，"
                    "适合做即时内容承接或广告投放测试。")
    if counts.get("潜在型"):
        recs.append("对潜在型热点做小额监测：量小但增速极高，"
                    "是下一阶段的候选，提前布局成本低。")
    if res["emerging"]:
        recs.append(f"把 {len(res['emerging'])} 条新兴信号加入观察名单，"
                    "重点看下一期快照中是否继续放量。")
    if res["brand_heat"]:
        recs.append("把品牌热搜与站内搜索/销量数据打通，验证关注度是否转化。"
                    "当前只有关注度侧证据。")
    recs.append("开启定时采集（建议每小时 1 次），累积 3–7 天后解锁生命周期与每小时新增分析。")
    return recs


def _llm_summary(res, llm):
    if not llm.available:
        return None
    payload = {
        "scope": res["scope"],
        "window": res["window"],
        "kpi": res["kpi"],
        "category_top": res["cat_rows"][:6],
        "quadrant_counts": res["quadrant"].get("counts"),
        "top_growth": [{"name": t["name"], "growth": t["growth"], "volume": t["volume"],
                        "cat": t["cat_label"]} for t in res["top_growth"][:5]],
        "top_volume": [{"name": t["name"], "volume": t["volume"], "cat": t["cat_label"]}
                       for t in res["top_volume"][:5]],
        "brand_heat": [{"brand": b["brand"], "count": b["count"], "heat": b["heat_norm"]}
                       for b in res["brand_heat"][:5]],
        "emerging_count": len(res["emerging"]),
        "concentration": res["concentration"],
        "lifecycle_available": res["lifecycle"]["available"],
    }
    system = (
        "你是数据分析师。基于下面已经算好的指标，写执行摘要与行动建议。\n"
        "硬性要求：\n"
        "1) 只能引用给定数字，**严禁引入任何新数字、新排名或外部事实**；\n"
        "2) 不夸大因果，不把搜索关注度说成销量；\n"
        "3) 每条建议要具体可执行，不要空话；\n"
        "4) 只输出 JSON：{\"executive_summary\":[\"句1\",\"句2\",\"句3\"],"
        "\"recommendations\":[\"条1\",\"条2\",\"条3\"]}\n"
        "executive_summary 3-5 句，recommendations 3-5 条。"
    )
    # 预算要一次给够「思维链 + 正文」。实测 deepseek-flash 写这份叙述时
    # 思维链本身就超过 2000 token，给 2000 会正文为空、白白浪费一次调用
    # （推理 token 同样计费）。max_tokens 只是上限，给宽不影响实际花费。
    out = llm.json_chat(system, json.dumps(payload, ensure_ascii=False),
                        max_tokens=4000)
    if not isinstance(out, dict):
        return None
    if not out.get("executive_summary"):
        return None
    return out


def _fmt(v):
    v = v or 0
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K" if v >= 100_000 else f"{v/1_000:.1f}K"
    return str(v)
