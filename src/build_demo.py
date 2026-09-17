"""
从 data/latest.json 生成一个自包含的 HTML 看板 demo（数据内联，双击即可打开）。
用于验证「采集 -> 指标 -> 可视化」整条链路。
"""

import json
import os
import statistics
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATEST = os.path.join(BASE, "data", "latest.json")
OUT_DIR = os.path.join(BASE, "demo")
OUT_HTML = os.path.join(OUT_DIR, "dashboard-demo.html")

CAT_COLORS = {
    "Sports": "#185FA5", "Entertainment": "#534AB7", "Technology": "#0F6E56",
    "Politics": "#A32D2D", "Gaming": "#854F0B", "Consumer": "#D4537E",
    "Finance": "#3B6D11", "Weather": "#378ADD", "Health": "#993C1D",
    "Other": "#888780",
}


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else 0


def main():
    with open(LATEST, encoding="utf-8") as f:
        payload = json.load(f)
    trends = payload["trends"]

    vol = [t["search_volume"] for t in trends if t["search_volume"]]
    growth = [t["growth_rate"] for t in trends if t["growth_rate"]]
    total_vol = sum(vol)
    median_growth = med(growth)
    median_vol = med(vol)

    consumer = [t for t in trends if t["is_consumer"]]
    emerging = sorted(
        [t for t in trends if (t["growth_rate"] or 0) >= 500 and (t["search_volume"] or 0) >= 5000],
        key=lambda x: -(x["search_volume"] or 0))[:12]

    top_vol = sorted(trends, key=lambda x: -(x["search_volume"] or 0))[:15]
    top_growth = sorted(trends, key=lambda x: (-(x["growth_rate"] or 0), -(x["search_volume"] or 0)))[:15]

    cats = {}
    for t in trends:
        c = cats.setdefault(t["category"], {"count": 0, "volume": 0})
        c["count"] += 1
        c["volume"] += t["search_volume"] or 0
    cat_rows = sorted(cats.items(), key=lambda x: -x[1]["volume"])

    # 散点：x=增长率 y=搜索量 size=关联词数 color=类别
    scatter = {}
    for t in trends:
        if not t["search_volume"] or not t["growth_rate"]:
            continue
        scatter.setdefault(t["category"], []).append([
            t["growth_rate"], t["search_volume"], t["related_query_count"], t["trend"]])

    data = {
        "snapshot_time": payload.get("snapshot_time"),
        "geo": payload.get("geo"),
        "window_hours": payload.get("window_hours"),
        "kpi": {
            "total": len(trends),
            "total_vol": total_vol,
            "median_growth": median_growth,
            "median_vol": median_vol,
            "consumer": len(consumer),
            "commerce_share": round(len(consumer) / max(len(trends), 1) * 100, 1),
            "avg_related": round(sum(t["related_query_count"] for t in trends) / max(len(trends), 1), 1),
            "median_growth_ref": median_growth,
            "median_vol_ref": median_vol,
        },
        "top_vol": [[t["trend"], t["search_volume"], t["growth_rate"], t["category"]] for t in top_vol],
        "top_growth": [[t["trend"], t["growth_rate"], t["search_volume"], t["category"]] for t in top_growth],
        "cats": [[k, v["count"], v["volume"]] for k, v in cat_rows],
        "scatter": scatter,
        "cat_colors": CAT_COLORS,
        "emerging": [[t["trend"], t["category"], t["search_volume"], t["growth_rate"],
                      t["brand"] or "-", t["related_query_count"]] for t in emerging],
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    html = TEMPLATE.replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False))
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成:", OUT_HTML)
    print(f"热点 {len(trends)} 条 | 搜索量合计 {total_vol:,} | 增长率中位数 {median_growth}% "
          f"| 消费相关 {len(consumer)} | 新兴热点 {len(emerging)}")


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Google Trends US — 实时搜索热点看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  :root{
    --bg:#F7F7F5; --card:#FFFFFF; --line:rgba(0,0,0,.10);
    --t1:#1A1A19; --t2:#5F5E5A; --t3:#888780;
    --up:#C0392B; --down:#1D9E75; --accent:#185FA5;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--t1);
    font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:13px}
  .wrap{max-width:1400px;margin:0 auto;padding:20px}
  header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:4px}
  h1{font-size:18px;font-weight:500;margin:0}
  .sub{color:var(--t2);font-size:12px}
  .chip{background:#E6F1FB;color:#0C447C;border-radius:20px;padding:3px 10px;font-size:12px}
  .grid{display:grid;gap:12px;margin-top:12px}
  .kpis{grid-template-columns:repeat(6,1fr)}
  .two{grid-template-columns:1fr 1fr}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
  .kpi .label{color:var(--t2);font-size:12px;margin-bottom:6px}
  .kpi .val{font-size:22px;font-weight:500;line-height:1.2}
  .kpi .note{color:var(--t3);font-size:11px;margin-top:4px}
  .t{font-size:14px;font-weight:500;margin:0 0 10px}
  .chart{width:100%}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
  th{color:var(--t2);font-weight:500}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .up{color:var(--up)} .down{color:var(--down)}
  .empty{color:var(--t3);padding:24px;text-align:center}
  .foot{color:var(--t3);font-size:11px;margin-top:14px;line-height:1.7}
  @media (max-width:980px){.kpis{grid-template-columns:repeat(2,1fr)}.two{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Google Trends US · 实时搜索热点看板</h1>
    <span id="snap" class="sub"></span>
    <span class="chip">geo=US</span>
    <span class="chip" id="win"></span>
  </header>
  <div class="sub">数据源：trends.google.com/trending &nbsp;·&nbsp; 单次快照全量 299 条热点，含搜索量、增长率、关联搜索词</div>

  <div class="grid kpis" id="kpis"></div>

  <div class="grid two">
    <div class="card"><div class="t">搜索量 Top 15</div><div id="c_vol" class="chart" style="height:420px"></div></div>
    <div class="card"><div class="t">增长最快 Top 15</div><div id="c_grow" class="chart" style="height:420px"></div></div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="t">搜索量 × 增长率 四象限</div>
      <div class="sub" style="margin-bottom:8px">右上 = 爆发型热点（量大且高速增长）；左上 = 成熟大热点；右下 = 潜在热点；左下 = 长尾。气泡大小 = 关联搜索词数量</div>
      <div id="c_scatter" class="chart" style="height:460px"></div>
    </div>
  </div>

  <div class="grid two">
    <div class="card"><div class="t">类别结构（按搜索量）</div><div id="c_cat" class="chart" style="height:340px"></div></div>
    <div class="card"><div class="t">类别热点数量分布</div><div id="c_catcount" class="chart" style="height:340px"></div></div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="t">新兴热点（增长率 ≥ 500% 且搜索量 ≥ 5,000）</div>
      <div id="tbl_emerging"></div>
    </div>
  </div>

  <div class="foot">
    说明：搜索量为 Google 提供的估算区间桶值（100 ~ 500,000+），非精确值；增长率为相对预测基线的增长百分比。
    <br>Google 原站不提供热点类别与状态字段，类别由关键词规则标注、状态需依赖历史快照自行判定 —— 这两项是数据缺口。
  </div>
</div>

<script>
const D = /*__DATA__*/;

const fmt = n => n == null ? '-' : n.toLocaleString('en-US');
const fmtK = n => n >= 1000000 ? (n/1000000).toFixed(2)+'M'
              : n >= 1000 ? (n/1000).toFixed(n>=100000?0:1)+'K' : String(n);

document.getElementById('snap').textContent = '快照时间 ' + (D.snapshot_time||'').slice(0,16).replace('T',' ');
document.getElementById('win').textContent = '最近 ' + D.window_hours + ' 小时';

const k = D.kpi;
const cards = [
  ['热点总数', fmt(k.total), '当前在榜趋势条数'],
  ['搜索量合计', fmtK(k.total_vol), '估算桶值加总'],
  ['增长率中位数', k.median_growth + '%', '相对预测基线'],
  ['搜索量中位数', fmtK(k.median_vol), '桶值中位'],
  ['消费相关热点', k.consumer, '占比 ' + k.commerce_share + '%'],
  ['平均关联词数', k.avg_related, '每个热点的细分搜索']
];
document.getElementById('kpis').innerHTML = cards.map(c =>
  `<div class="card kpi"><div class="label">${c[0]}</div><div class="val">${c[1]}</div><div class="note">${c[2]}</div></div>`
).join('');

const baseOpt = {grid:{left:8,right:24,top:8,bottom:8,containLabel:true},
  tooltip:{trigger:'axis',axisPointer:{type:'shadow'}}};
const catColor = c => D.cat_colors[c] || '#888780';

// 搜索量 Top15
const v = D.top_vol.slice().reverse();
echarts.init(document.getElementById('c_vol')).setOption(Object.assign({}, baseOpt, {
  tooltip:{trigger:'item', formatter:p=>{
    const d=v[p.dataIndex];
    return `<b>${d[0]}</b><br>搜索量 ${fmt(d[1])}<br>增长率 ${d[2]}%<br>类别 ${d[3]}`;}},
  xAxis:{type:'value',axisLabel:{formatter:fmtK,color:'#5F5E5A',fontSize:11},splitLine:{lineStyle:{color:'rgba(0,0,0,.06)'}}},
  yAxis:{type:'category',data:v.map(d=>d[0].length>26?d[0].slice(0,25)+'…':d[0]),
    axisLabel:{color:'#1A1A19',fontSize:11},axisLine:{lineStyle:{color:'rgba(0,0,0,.15)'}}},
  series:[{type:'bar',data:v.map(d=>({value:d[1],itemStyle:{color:catColor(d[3])}})),
    barWidth:'62%',label:{show:true,position:'right',formatter:p=>fmtK(p.value),fontSize:11,color:'#5F5E5A'}}]
}));

// 增长最快 Top15
const g = D.top_growth.slice().reverse();
echarts.init(document.getElementById('c_grow')).setOption(Object.assign({}, baseOpt, {
  tooltip:{trigger:'item', formatter:p=>{
    const d=g[p.dataIndex];
    return `<b>${d[0]}</b><br>增长率 ${d[1]}%<br>搜索量 ${fmt(d[2])}<br>类别 ${d[3]}`;}},
  xAxis:{type:'value',axisLabel:{formatter:'{value}%',color:'#5F5E5A',fontSize:11},splitLine:{lineStyle:{color:'rgba(0,0,0,.06)'}}},
  yAxis:{type:'category',data:g.map(d=>d[0].length>26?d[0].slice(0,25)+'…':d[0]),
    axisLabel:{color:'#1A1A19',fontSize:11},axisLine:{lineStyle:{color:'rgba(0,0,0,.15)'}}},
  series:[{type:'bar',data:g.map(d=>({value:d[1],itemStyle:{color:catColor(d[3])}})),
    barWidth:'62%',label:{show:true,position:'right',formatter:'{c}%',fontSize:11,color:'#5F5E5A'}}]
}));

// 四象限散点
const series = Object.keys(D.scatter).map(cat => ({
  name: cat, type: 'scatter',
  data: D.scatter[cat].map(d => ({value:[d[0], d[1]], name:d[3], symbolSize: Math.max(7, Math.min(34, 6+d[2]*0.7))})),
  itemStyle:{color:catColor(cat), opacity:.78},
  emphasis:{focus:'series'}
}));
echarts.init(document.getElementById('c_scatter')).setOption({
  grid:{left:20,right:40,top:44,bottom:40,containLabel:true},
  legend:{top:6,textStyle:{fontSize:11,color:'#5F5E5A'},itemWidth:10,itemHeight:10},
  tooltip:{trigger:'item',formatter:p=>`<b>${p.data.name}</b><br>增长率 ${p.value[0]}%<br>搜索量 ${fmt(p.value[1])}<br>类别 ${p.seriesName}`},
  xAxis:{type:'log',name:'增长率 %',nameLocation:'middle',nameGap:26,nameTextStyle:{color:'#5F5E5A',fontSize:11},
    axisLabel:{formatter:'{value}%',color:'#5F5E5A',fontSize:11},splitLine:{lineStyle:{color:'rgba(0,0,0,.06)'}}},
  yAxis:{type:'log',name:'搜索量',nameTextStyle:{color:'#5F5E5A',fontSize:11},
    axisLabel:{formatter:fmtK,color:'#5F5E5A',fontSize:11},splitLine:{lineStyle:{color:'rgba(0,0,0,.06)'}}},
  series: series,
  markLine: undefined
});
// 四象限参考线
const sc = echarts.getInstanceByDom(document.getElementById('c_scatter'));
sc.setOption({series: series.map(s => Object.assign({}, s, {
  markLine: s === series[0] ? {silent:true, symbol:'none', lineStyle:{color:'rgba(0,0,0,.22)',type:'dashed',width:1},
    data:[{xAxis: D.kpi.median_growth_ref}, {yAxis: D.kpi.median_vol_ref}]} : undefined
}))});

// 类别结构（搜索量）
echarts.init(document.getElementById('c_cat')).setOption({
  tooltip:{trigger:'item',formatter:p=>`${p.name}<br>搜索量 ${fmt(p.value) }<br>占比 ${p.percent}%`},
  series:[{type:'pie',radius:['42%','72%'],center:['48%','52%'],
    data:D.cats.map(c=>({name:c[0],value:c[2],itemStyle:{color:catColor(c[0])}})),
    label:{fontSize:11,color:'#1A1A19',formatter:'{b}\n{d}%'},
    labelLine:{length:8,length2:8},
    itemStyle:{borderColor:'#fff',borderWidth:2}}]
});

// 类别数量
const cc = D.cats.slice().sort((a,b)=>b[1]-a[1]).reverse();
echarts.init(document.getElementById('c_catcount')).setOption(Object.assign({}, baseOpt, {
  tooltip:{trigger:'item',formatter:p=>{const d=cc[p.dataIndex];return `<b>${d[0]}</b><br>热点数 ${d[1]}<br>搜索量 ${fmt(d[2])}`;}},
  xAxis:{type:'value',axisLabel:{color:'#5F5E5A',fontSize:11},splitLine:{lineStyle:{color:'rgba(0,0,0,.06)'}}},
  yAxis:{type:'category',data:cc.map(d=>d[0]),axisLabel:{color:'#1A1A19',fontSize:11},axisLine:{lineStyle:{color:'rgba(0,0,0,.15)'}}},
  series:[{type:'bar',data:cc.map(d=>({value:d[1],itemStyle:{color:catColor(d[0])}})),barWidth:'62%',
    label:{show:true,position:'right',fontSize:11,color:'#5F5E5A'}}]
}));

// 新兴热点表
document.getElementById('tbl_emerging').innerHTML = D.emerging.length ? `
<table><thead><tr>
  <th>热点</th><th>类别</th><th class="num">搜索量</th><th class="num">增长率</th><th>品牌</th><th class="num">关联词</th>
</tr></thead><tbody>
${D.emerging.map(r=>`<tr>
  <td>${r[0]}</td><td>${r[1]}</td>
  <td class="num">${fmt(r[2])}</td>
  <td class="num up">+${r[3]}%</td>
  <td>${r[4]}</td><td class="num">${r[5]}</td>
</tr>`).join('')}
</tbody></table>` : '<div class="empty">本次快照没有满足条件的新兴热点</div>';

window.addEventListener('resize', () => {
  ['c_vol','c_grow','c_scatter','c_cat','c_catcount'].forEach(id=>{
    const inst = echarts.getInstanceByDom(document.getElementById(id));
    if (inst) inst.resize();
  });
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
