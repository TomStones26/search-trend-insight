/* ============================================================
   DataPulse 前端公共层
   ============================================================ */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

/* ---------------- 数字格式 ---------------- */
function fmtInt(n) {
  if (n === null || n === undefined || isNaN(n)) return '-';
  return Number(n).toLocaleString('en-US');
}
function fmtK(n) {
  if (n === null || n === undefined || isNaN(n)) return '-';
  n = Number(n);
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (Math.abs(n) >= 1e5) return (n / 1e3).toFixed(0) + 'K';
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(n);
}
function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function clip(s, n) {
  s = String(s || '');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
function tsShort(iso) {
  if (!iso) return '-';
  return String(iso).slice(0, 16).replace('T', ' ');
}
function ago(iso) {
  if (!iso) return '';
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (isNaN(d)) return '';
  if (d < 60) return Math.max(1, Math.round(d)) + ' 秒前';
  if (d < 3600) return Math.round(d / 60) + ' 分钟前';
  if (d < 86400) return Math.round(d / 3600) + ' 小时前';
  return Math.round(d / 86400) + ' 天前';
}

/* ---------------- 类别色板（与 taxonomy.json 保持一致） ---------------- */
const CAT_COLORS = {
  Sports: '#F43F5E', Entertainment: '#A855F7', Politics: '#F59E0B',
  Society: '#0EA5E9', Technology: '#06B6D4', Gaming: '#10B981',
  Business: '#3B82F6', Finance: '#6366F1', Consumer: '#EC4899',
  Travel: '#F97316', Health: '#8B5CF6', Weather: '#14B8A6',
  Other: '#94A3B8'
};
const CAT_LABELS = {
  Sports: '体育', Entertainment: '娱乐', Politics: '政治与公共事务',
  Society: '时事与社会', Technology: '科技', Gaming: '游戏',
  Business: '企业与就业', Finance: '金融与市场', Consumer: '消费与零售',
  Travel: '旅行', Health: '健康与医疗', Weather: '天气与自然', Other: '其他'
};
const catColor = c => CAT_COLORS[c] || '#94A3B8';
const catLabel = c => CAT_LABELS[c] || c || '其他';

/* ---------------- 导航 ---------------- */
function mountNav(active, extra) {
  const items = [
    ['/', '主页', 'home'],
    ['/analysis', '分析过程', 'analysis'],
    ['/dashboard', '可视化看板', 'dashboard'],
    ['/report', '分析报告', 'report']
  ];
  const el = document.createElement('div');
  el.className = 'nav';
  el.innerHTML = `<div class="nav-in">
    <div class="logo"><span class="dot"></span>
      <span>DataPulse<small> · Google Trends US 实时热点分析</small></span>
    </div>
    <div class="nav-links">
      ${items.map(([href, label, key]) =>
        `<a href="${href}" class="${key === active ? 'on' : ''}">${label}</a>`).join('')}
      ${extra || ''}
    </div>
  </div>`;
  document.body.insertBefore(el, document.body.firstChild);
}

/* ---------------- 请求 ---------------- */
async function api(path, opts) {
  const r = await fetch(path, Object.assign({ cache: 'no-store' }, opts || {}));
  if (!r.ok) {
    let detail = null;
    try { detail = await r.json(); } catch (e) { /* ignore */ }
    const err = new Error('HTTP ' + r.status);
    err.status = r.status; err.detail = detail;
    throw err;
  }
  return r.json();
}
async function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
}

/* ---------------- 提示条 ---------------- */
function toast(msg, kind = 'info') {
  let box = $('#toast');
  if (!box) {
    box = document.createElement('div');
    box.id = 'toast';
    box.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);bottom:34px;' +
      'z-index:999;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none';
    document.body.appendChild(box);
  }
  const colors = { info: '#6366F1', ok: '#10B981', warn: '#F59E0B', err: '#EF4444' };
  const el = document.createElement('div');
  el.style.cssText = 'background:#fff;border:1px solid #E7EAF4;border-left:4px solid ' +
    colors[kind] + ';border-radius:12px;padding:11px 17px;font-size:13.5px;font-weight:600;' +
    'color:#0F172A;box-shadow:0 12px 34px rgba(15,23,42,.14);opacity:0;transition:.25s';
  el.textContent = msg;
  box.appendChild(el);
  requestAnimationFrame(() => { el.style.opacity = '1'; });
  setTimeout(() => {
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 300);
  }, 3200);
}

/* ---------------- ECharts 辅助 ---------------- */
const AXIS_INK = '#475569';
const GRID_LINE = 'rgba(15,23,42,.06)';

function axisBase(extra) {
  return Object.assign({
    axisLine: { lineStyle: { color: 'rgba(15,23,42,.16)' } },
    axisLabel: { color: AXIS_INK, fontSize: 11 },
    splitLine: { lineStyle: { color: GRID_LINE } }
  }, extra || {});
}
function tipBase(extra) {
  return Object.assign({
    backgroundColor: 'rgba(255,255,255,.97)',
    borderColor: '#E7EAF4',
    borderWidth: 1,
    textStyle: { color: '#0F172A', fontSize: 12.5 },
    extraCssText: 'box-shadow:0 10px 30px rgba(15,23,42,.14);border-radius:10px;padding:9px 12px'
  }, extra || {});
}
function makeChart(id) {
  const dom = document.getElementById(id);
  if (!dom) return null;
  if (typeof echarts === 'undefined') {
    dom.innerHTML = '<div class="empty">图表库未能加载（可能是网络受限）。' +
      '数据本身已正常获取，可查看下方表格。</div>';
    return null;
  }
  const inst = echarts.init(dom, null, { renderer: 'canvas' });
  _charts.push(inst);
  return inst;
}
const _charts = [];
window.addEventListener('resize', () => _charts.forEach(c => { try { c.resize(); } catch (e) {} }));

/* ---------------- 状态轮询 ---------------- */
function pollStatus(onUpdate, ms = 1000) {
  let stopped = false;
  let timer = null;
  async function tick() {
    if (stopped) return;
    try {
      const st = await api('/api/status');
      onUpdate(st);
      if (st.state === 'running') timer = setTimeout(tick, ms);
      else stopped = true;
    } catch (e) {
      timer = setTimeout(tick, Math.min(ms * 2, 5000));
    }
  }
  tick();
  return () => { stopped = true; clearTimeout(timer); };
}

/* ---------------- 状态徽章 ---------------- */
function stateChip(state) {
  const map = {
    running: ['chip', 'dot-live', '分析中'],
    done: ['chip mint', 'dot-live', '已完成'],
    failed: ['chip rose', 'dot-err', '失败'],
    idle: ['chip gray', 'dot-warn', '待运行']
  };
  const [cls, dot, text] = map[state] || map.idle;
  return `<span class="${cls}"><span class="${dot}"></span>${text}</span>`;
}

/* ---------------- 渲染步骤条 ---------------- */
function renderSteps(container, steps) {
  container.innerHTML = steps.map((s, i) => {
    const cls = s.state === 'done' ? 'done' : s.state === 'running' ? 'running'
      : s.state === 'failed' ? 'failed' : '';
    const mark = s.state === 'done' ? '✓' : s.state === 'failed' ? '!' : (i + 1);
    const time = s.duration_ms !== null && s.duration_ms !== undefined
      ? `<span class="s-time">${s.duration_ms} ms</span>` : '';
    return `<div class="step ${cls}">
      <div class="bullet">${mark}</div>
      <div class="s-body">
        <div class="spread"><span class="s-title">${esc(s.label)}</span>${time}</div>
        <div class="s-detail">${esc(s.detail || '')}</div>
      </div>
    </div>`;
  }).join('');
}

/* ---------------- 渲染日志 ---------------- */
function renderLogs(container, logs) {
  container.innerHTML = (logs || []).slice(-200).map(l =>
    `<div><span class="t">${esc(l.t)}</span>${esc(l.msg)}</div>`).join('')
    || '<div class="muted">暂无日志</div>';
  container.scrollTop = container.scrollHeight;
}

/* ---------------- Plan 卡片 ---------------- */
function renderPlan(el, plan, taxonomyLabels) {
  if (!plan) { el.innerHTML = '<div class="empty">尚未解析需求</div>'; return; }
  const f = plan.filters || {};
  const cats = (f.categories || []).map(c =>
    `<span class="tag" style="background:${catColor(c)}22;color:${catColor(c)}">${esc(catLabel(c))}</span>`
  ).join(' ') || '<span class="muted">全部类别</span>';

  const focusLabel = {
    volume: '热度排名', growth: '增长动能', commerce: '消费与品牌',
    lifecycle: '生命周期', structure: '内容结构', cause: '成因分析',
    risk: '异常识别', compare: '类别对比'
  };
  const focus = (plan.focus || []).map(x =>
    `<span class="chip">${esc(focusLabel[x] || x)}</span>`).join(' ') || '<span class="muted">—</span>';

  const conds = [];
  if (f.min_growth !== null && f.min_growth !== undefined) conds.push('增长率 ≥ ' + f.min_growth + '%');
  if (f.min_volume !== null && f.min_volume !== undefined) conds.push('搜索量 ≥ ' + fmtInt(f.min_volume));
  if (f.brand_only) conds.push('仅品牌相关');
  if (f.keyword) conds.push('关键词「' + esc(f.keyword) + '」');

  const engineTag = plan.engine === 'llm'
    ? '<span class="chip mint">LLM 理解</span>'
    : '<span class="chip amber">本地规则理解</span>';

  el.innerHTML = `
    <div class="spread" style="margin-bottom:12px">
      <h3 style="margin:0">AI 理解结果</h3>${engineTag}
    </div>
    <p style="margin:0 0 14px;font-size:14px;color:var(--ink)">${esc(plan.intent_summary || '')}</p>
    <div class="grid g3" style="gap:12px">
      <div><label class="f">筛选范围</label><div>${cats}</div></div>
      <div><label class="f">分析重点</label><div>${focus}</div></div>
      <div><label class="f">额外条件</label>
        <div>${conds.length ? conds.map(c => `<span class="chip gray">${c}</span>`).join(' ') : '<span class="muted">无</span>'}</div>
      </div>
    </div>
    ${(plan.focus_questions || []).length ? `
    <div style="margin-top:14px;padding-top:13px;border-top:1px dashed var(--line)">
      <label class="f">本次准备回答</label>
      <ol style="margin:0;padding-left:20px;font-size:13px;color:var(--ink-2)">
        ${plan.focus_questions.map(q => `<li>${esc(q)}</li>`).join('')}
      </ol>
    </div>` : ''}
    <div style="margin-top:13px" class="muted">
      理解有偏差？回
      <a href="/">主页</a> 重新描述需求即可，改写一句话比调参数快。
    </div>`;
}

/* ---------------- Findings ---------------- */
function renderFindings(el, findings) {
  if (!findings || !findings.length) {
    el.innerHTML = '<div class="empty">暂无结论</div>';
    return;
  }
  el.innerHTML = findings.map(f => {
    const ev = f.evidence || {};
    const bits = [];
    if (ev.chart) bits.push('图表：' + ev.chart);
    if (ev.trend_id) bits.push('热点：' + ev.trend_id);
    if (ev.cat) bits.push('类别：' + catLabel(ev.cat));
    if (ev.brand) bits.push('品牌：' + ev.brand);
    return `<div class="finding ${f.confidence || 'high'}">
      <div class="f-title">${esc(f.title)}</div>
      <div class="f-text">${esc(f.text)}</div>
      ${bits.length ? `<div class="f-ev">依据 ▸ ${esc(bits.join(' ｜ '))}</div>` : ''}
    </div>`;
  }).join('');
}
