"""
分类打标引擎（词典驱动，本地规则，零模型调用）
==============================================
设计：config/taxonomy.json 由 LLM 依据真实样本构建，本模块只负责高效执行。

相比"首个命中即返回"的关键改动：
  1) 加权打分：strong(3) / weak(1) / patterns(可配权重)，取最高分而非第一个匹配
  2) 双文本信号：热点名权重 1.0，关联搜索词权重 0.5（名称模糊时关联词能救回来）
  3) 多标签：主类别之外，输出所有超阈值类别，便于交叉分析
  4) 可解释：保留命中的关键词，供分析页展示与后续词典迭代

用法：
  from classify import Taxonomy
  tx = Taxonomy()
  r = tx.classify("pokemon owala", ["owala pokemon", "pokemon owala target"])
  # {'category':'Consumer', 'brand':'Owala', 'commerce_type':'Product', ...}

命令行：
  python classify.py                 # 对 data/latest.json 做标注质量体检
  python classify.py --relabel       # 用新词典重新标注 latest.json 与 dim_trend.jsonl
"""

import argparse
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAXONOMY_PATH = os.path.join(BASE_DIR, "config", "taxonomy.json")


def _alt(terms):
    """把关键词列表编译成一条 alternation；长词优先，避免短词抢先命中。"""
    return "|".join(sorted({re.escape(t) for t in terms}, key=len, reverse=True))


def _word_re(terms):
    if not terms:
        return None
    # (?<![a-z0-9]) ... (?![a-z0-9]) 等价于词边界，但对多词关键词与连字符更友好
    return re.compile(r"(?<![a-z0-9])(?:" + _alt(terms) + r")(?![a-z0-9])", re.I)


class Taxonomy:
    def __init__(self, path=TAXONOMY_PATH):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        self.raw = raw
        self.version = raw.get("version", "unknown")
        sc = raw.get("scoring", {})
        self.w_strong = sc.get("strong", 3)
        self.w_weak = sc.get("weak", 1)
        self.w_name = sc.get("query_text_weight", 1.0)
        self.w_rq = sc.get("related_queries_weight", 0.5)
        self.ml_ratio = sc.get("multilabel_ratio", 0.6)
        self.ml_min = sc.get("min_score_for_multilabel", 3)

        self.cats = {}
        for cid, c in raw["categories"].items():
            self.cats[cid] = {
                "label": c.get("label", cid),
                "color": c.get("color", "#94A3B8"),
                "desc": c.get("desc", ""),
                "strong_re": _word_re(c.get("strong", [])),
                "weak_re": _word_re(c.get("weak", [])),
                "patterns": [(re.compile(p, re.I), w) for p, w in c.get("patterns", [])],
            }

        # 品牌：别名 -> 品牌条目。长别名优先，避免 "target" 抢在 "target 30th" 之前
        self.brand_aliases = []
        for b in raw.get("brands", []):
            for a in [b["name"]] + list(b.get("aliases", [])):
                self.brand_aliases.append((a.lower(), b))
        self.brand_aliases.sort(key=lambda x: -len(x[0]))
        self.brand_re = None
        if self.brand_aliases:
            self.brand_re = re.compile(
                r"(?<![a-z0-9])(?:" + _alt([a for a, _ in self.brand_aliases]) + r")(?![a-z0-9])",
                re.I)
        self._alias_index = {a: b for a, b in self.brand_aliases}

        # 消费子类规则
        self.commerce_rules = []
        for tid, t in raw.get("commerce_types", {}).items():
            kws = t.get("keywords", [])
            if kws:
                self.commerce_rules.append((tid, _word_re(kws)))
        self.commerce_labels = {k: v.get("label", k)
                                for k, v in raw.get("commerce_types", {}).items()}

    # ------------------------------------------------------------------ 打分
    def _score_text(self, text, packed):
        """返回 (加权得分, 命中词集合)。"""
        if not text:
            return 0.0, set()
        hits = set()
        score = 0.0
        r = packed["strong_re"]
        if r:
            for m in r.finditer(text):
                hits.add(m.group(0).lower())
            if hits:
                score += self.w_strong * len(hits)
        wr = packed["weak_re"]
        if wr:
            wh = {m.group(0).lower() for m in wr.finditer(text)}
            wh -= hits
            if wh:
                hits |= wh
                score += self.w_weak * len(wh)
        for rx, w in packed["patterns"]:
            if w and rx.search(text):
                score += w
                hits.add("/" + rx.pattern + "/")
        return score, hits

    def classify(self, name, related_queries=None, top_k=3):
        """对单条热点打标。"""
        rq = related_queries or []
        name_text = (name or "").lower()
        rq_text = " | ".join(str(q) for q in rq).lower()

        scores, matched = {}, {}
        for cid, packed in self.cats.items():
            s1, h1 = self._score_text(name_text, packed)
            s2, h2 = self._score_text(rq_text, packed)
            s = s1 * self.w_name + s2 * self.w_rq
            if s > 0:
                scores[cid] = round(s, 2)
                matched[cid] = sorted(h1 | h2)

        if not scores:
            return {
                "category": "Other", "category_label": "其他", "category_color": "#94A3B8",
                "categories": [], "category_scores": {}, "category_matched": {},
                "classify_confidence": 0.0, "brand": None, "brand_type": None,
                "commerce_type": None, "is_consumer": False,
            }

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        top_cat, top_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        conf = round(top_score / (top_score + second), 3) if second > 0 else 1.0

        multi = [c for c, s in ranked[:top_k]
                 if s >= self.ml_min and s >= top_score * self.ml_ratio]

        # 品牌识别：名称优先，其次关联词
        brand, btype = self._detect_brand(name_text)
        if not brand:
            brand, btype = self._detect_brand(rq_text)

        commerce = btype if btype else self._detect_commerce_type(name_text + " " + rq_text)
        is_consumer = bool(brand) or top_cat == "Consumer" or bool(commerce)

        return {
            "category": top_cat,
            "category_label": self.cats[top_cat]["label"],
            "category_color": self.cats[top_cat]["color"],
            "categories": multi,
            "category_scores": dict(ranked[:top_k]),
            "category_matched": {c: matched[c][:6] for c, _ in ranked[:top_k]},
            "classify_confidence": conf,
            "brand": brand,
            "brand_type": btype,
            "commerce_type": commerce,
            "is_consumer": is_consumer,
        }

    def _detect_brand(self, text):
        if not text or not self.brand_re:
            return None, None
        for m in self.brand_re.finditer(text):
            b = self._alias_index.get(m.group(0).lower())
            if b:
                return b["name"], b.get("commerce_type")
        return None, None

    def _detect_commerce_type(self, text):
        for tid, rx in self.commerce_rules:
            if rx and rx.search(text):
                return tid
        return None

    # ------------------------------------------------------------------ 批量
    def label_records(self, recs, inplace=True):
        out = []
        for r in recs:
            res = self.classify(r.get("trend"), r.get("related_queries"))
            t = r if inplace else dict(r)
            t.update(res)
            out.append(t)
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--relabel", action="store_true", help="用当前词典重新标注数据文件")
    ap.add_argument("--samples", action="store_true", help="输出各类抽样，核对标注正确性")
    ap.add_argument("--taxonomy", default=TAXONOMY_PATH)
    args = ap.parse_args()

    tx = Taxonomy(args.taxonomy)
    latest = os.path.join(BASE_DIR, "data", "latest.json")
    if not os.path.exists(latest):
        print("找不到 data/latest.json，请先运行 python src/gt_collect.py")
        return 1
    with open(latest, encoding="utf-8") as f:
        payload = json.load(f)
    trends = payload["trends"]

    if args.relabel:
        tx.label_records(trends)
        with open(latest, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        hist = os.path.join(BASE_DIR, "data", "dim_trend.jsonl")
        if os.path.exists(hist):
            rows = [json.loads(l) for l in open(hist, encoding="utf-8") if l.strip()]
            tx.label_records(rows)
            with open(hist, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("已重新标注:", latest)
        return 0

    res = tx.label_records([dict(t) for t in trends], inplace=True)

    print("词典版本:", tx.version)
    print("样本量  :", len(res))
    print()
    dist = {}
    for r in res:
        d = dist.setdefault(r["category"], {"n": 0, "v": 0})
        d["n"] += 1
        d["v"] += r["search_volume"] or 0
    print("%-16s %6s %8s %14s" % ("类别", "条数", "占比", "搜索量合计"))
    for c, d in sorted(dist.items(), key=lambda x: -x[1]["n"]):
        print("%-16s %6d %7.1f%% %14s" % (c, d["n"], d["n"] / len(res) * 100, format(d["v"], ",")))
    other = dist.get("Other", {}).get("n", 0)
    print()
    print("Other 占比: %.1f%%  (改动前为 77.9%%)" % (other / len(res) * 100))
    print("识别品牌  : %d 个 -> %s" % (
        len({r["brand"] for r in res if r["brand"]}),
        sorted({r["brand"] for r in res if r["brand"]})))
    print("消费相关  : %d 条 (%.1f%%)" % (
        sum(1 for r in res if r["is_consumer"]),
        sum(1 for r in res if r["is_consumer"]) / len(res) * 100))
    conflicted = [r for r in res if len(r["categories"]) > 1]
    print("多标签    : %d 条" % len(conflicted))
    print()
    print("--- 落到 Other 的样本（用于继续补词典）---")
    for r in [x for x in res if x["category"] == "Other"][:30]:
        print("   ", r["trend"])

    if args.samples:
        print()
        print("=== 各类抽样核对（名称 <= 命中词）===")
        for c in sorted(dist, key=lambda x: -dist[x]["n"]):
            print("\n[" + c + "]")
            for r in [x for x in res if x["category"] == c][:12]:
                hit = ""
                sc = r["category_scores"].get(c)
                mt = r["category_matched"].get(c) or []
                hit = " <= " + ", ".join(mt[:4]) if mt else ""
                print("    %-40s%s" % (r["trend"][:40], hit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
