# DataPulse Skill 包

7 个 Skill，覆盖从采集到报告的完整链路。每个都是可直接复制粘贴的 `SKILL.md` 全文。

## 技能总览

| # | 技能名 | 触发场景 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| 1 | `datapulse-orchestrator` | 用户提出任何数据需求 / 点击更新 | 自然语言 + 配置 | 全套产物调度 | 2–6 |
| 2 | `datapulse-collector` | 需要抓取 / 更新数据 | `sources/*.json` | `raw/*.jsonl`、`sources.manifest.json` | 无 |
| 3 | `datapulse-cleaner` | 有原始数据待整理 | `raw/*.jsonl` | `clean/data.json`、`quality.json` | 无 |
| 4 | `datapulse-analyst` | 有干净数据待分析 | `plan.json`、`clean/data.json` | `insights.json` | 无 |
| 5 | `datapulse-dashboard` | 需要可视化看板 | `insights.json` | `dashboard.html` | 无 |
| 6 | `datapulse-reporter` | 需要分析报告 | `insights.json`、`quality.json` | `report.html` | 无 |
| 7 | `datapulse-app-shell` | 首次搭建 / 改版前端 | 无 | 4 个 HTML 页面 | 无 |

## 目录结构建议

```
~/.workbuddy/skills/          （用户级，跨项目可用）
 或 <项目>/.workbuddy/skills/ （项目级，团队共享）
├── datapulse-orchestrator/SKILL.md
├── datapulse-collector/SKILL.md
├── datapulse-cleaner/SKILL.md
├── datapulse-analyst/SKILL.md
├── datapulse-dashboard/SKILL.md
├── datapulse-reporter/SKILL.md
└── datapulse-app-shell/SKILL.md
```

复制方式：在对应目录下建 `<技能名>/SKILL.md`，粘贴下面的全文即可。若当前环境支持技能市场安装，也可以让我把这份包直接落成真实技能目录。

## 调用契约（技能之间只通过文件通信）

```
用户输入
   │
   ▼
[orchestrator] ──写──▶ plan.json
   │
   ├──▶ [collector] ──▶ raw/*.jsonl + sources.manifest.json
   ├──▶ [cleaner]   ──▶ clean/data.json + quality.json
   ├──▶ [analyst]   ──▶ insights.json
   ├──▶ [dashboard] ──▶ dashboard.html
   └──▶ [reporter]  ──▶ report.html
   │
   └──全程写──▶ status.json  ←── 前端轮询
```

**关键约定**：技能之间不传递内存变量，只传递文件路径。这样任何一步都能单独重跑、单独调试。

---

# 技能 1 · datapulse-orchestrator

```markdown
---
name: datapulse-orchestrator
description: DataPulse 数据产品的总编排技能。当用户提出数据分析需求、点击"更新数据"、或需要跑完整流水线（采集→清洗→分析→看板→报告）时使用。负责解析需求生成 plan.json、按序调度五个子技能、维护 status.json 状态机、守产物契约。
agent_created: true
---

# DataPulse 编排器

## 何时使用
- 用户说「更新数据」「看看最近怎么样」「分析一下 XX」等任何数据需求
- 前端点击「更新数据」按钮触发
- 需要重跑某个 run 的某一阶段

## 前置
- 存在 `config/product.config.json`（领域、指标口径、维度、defaultPlan）
- 存在 `config/sources/*.json`（数据源 Adapter 配置）
- 运行目录 `runs/`，软链 `cache/latest`

## 执行步骤

1. **生成 run_id**：`YYYYMMDD-HHmm-<4位随机>`，创建 `runs/<run_id>/`。
2. **初始化 status.json**：六个 step 全部 pending，state=running。
3. **S0 执行需求解析**：套用 P1 Prompt，产出 `plan.json`。
   - 输入为空 → 用 `config.product.config.json` 的 defaultPlan，output_type=dashboard。
   - `clarifications_needed` 含关键项 → 暂停，state=waiting_input，等 `control.json` 的 confirm。
4. **S1 调用 datapulse-collector**，传参 `{run_id, plan, mode}`。
5. **S2 调用 datapulse-cleaner**，传参 `{run_id, sources}`。
6. **S3 调用 datapulse-analyst**，传参 `{run_id, plan, quality}`。
7. **S4 调用 datapulse-dashboard**，传参 `{run_id, insights}`。
8. **S5 调用 datapulse-reporter**（仅当 output_type 含 report）。
9. **更新 cache/latest** 指向本 run，追加 `cache/index.json`。

## 每步都要做的事
- 步骤开始：step.state=running，记录 started_at
- 步骤中：更新 step.message 与顶层 progress，供前端实时展示
- 步骤结束：step.state=done，记录 ended_at 与 artifacts
- 失败：step.state=failed，error 写清阶段 + 异常摘要 + 是否可重试

## 状态文件写入方式
先写 `status.json.tmp`，再原子重命名覆盖 `status.json`。保证前端永远读到完整 JSON。

## 用户控制
- `control.json: {"abort": true}` → 在阶段边界终止，state=aborted，保留已完成产物
- `control.json: {"confirm": {...}}` → 用确认内容覆盖 plan，继续执行

## 保护规则
- 绝不编造数据。数字必须来自实际文件。
- 绝不在分析阶段自创指标口径，口径只从配置读。
- 绝不删除已完成阶段的产物。
- 单源采集失败不阻断整体；全部失败才终止。
```

---

# 技能 2 · datapulse-collector

```markdown
---
name: datapulse-collector
description: 通用数据采集技能。按 Adapter 配置抓取公开数据并标准化落盘，支持增量 upsert。当需要更新数据、新增数据源、或抓取网页/API 数据时使用。内置合规闸门（robots.txt、限速、反隐私字段）。
agent_created: true
---

# DataPulse 采集器

## 何时使用
- 用户点击「更新数据」
- 新增数据源后首次抓取
- 需要在定时任务里自动更新

## 输入
`config/sources/*.json`，每个源：
```json
{
  "id": "src_alpha",
  "name": "数据源名称",
  "type": "api | html | csv | connector",
  "entry": "https://example.com/api/list",
  "method": "GET",
  "headers": {},
  "params": {},
  "pagination": { "type": "page", "size_param": "page_size", "index_param": "page", "size": 100 },
  "primary_key": ["id"],
  "updated_at_field": "update_time",
  "field_map": { "原始字段": "标准字段" },
  "limit": 5000,
  "rate_limit_ms": 1000
}
```

## 执行步骤

1. **合规闸门（必做，不可跳过）**
   - 检查目标 `robots.txt` 是否允许该路径 → 不允许则 skip，标注 `skipped_robots`
   - 是否公开可访问、无需登录 → 需要登录则 skip，标注 `skipped_auth`
   - 确认请求间隔 ≥ `rate_limit_ms`（默认 1000ms）
   - 单源总请求数 > 500 时停止并报告
2. **抓取**：按 pagination 翻页直到达到 limit 或数据耗尽
   - `type=html` 且目标为动态渲染页面时，改用浏览器自动化能力抓取
   - `type=connector` 时走已连接的官方连接器（见 RESOURCES.md）
3. **标准化**：按 `field_map` 重命名字段；时间戳统一转 ISO 8601（Asia/Shanghai）
4. **隐私过滤**：发现手机号/身份证/邮箱/住址等字段，直接丢弃并记 `dropped_pii`
5. **落盘**：`runs/<run_id>/raw/<source_id>.jsonl`，一行一条 JSON
6. **增量合并**：若 `cache/store/<source_id>.jsonl` 存在，按 primary_key upsert
   - 新 primary_key → append
   - 已存在且 updated_at 更新 → 覆盖，旧记录移入 `cache/archive/`
   - 已存在且 updated_at 未变 → 跳过
7. **写 manifest**：`runs/<run_id>/sources.manifest.json`

## 输出契约
```json
{
  "run_id": "...",
  "mode": "incremental|full",
  "sources": [
    { "id": "src_alpha", "status": "ok", "rows_fetched": 1240, "rows_new": 1180,
      "rows_updated": 60, "duration_ms": 43200, "error": null }
  ]
}
```
status 取值：`ok | failed | skipped_robots | skipped_auth | skipped_limit`

## 红线
- 绝不绕过登录、验证码、付费墙、反爬机制
- 绝不采集个人隐私字段
- 绝不高频请求单一站点
- 抓取失败绝不伪造数据，宁可返回空并报错
```

---

# 技能 3 · datapulse-cleaner

```markdown
---
name: datapulse-cleaner
description: 数据清洗与质量评估技能。把原始 JSONL 整理成可用于分析的标准表，输出 quality.json 质量报告。当有新的原始数据需要标准化、去重、类型转换、异常标记时使用。
agent_created: true
---

# DataPulse 清洗器

## 何时使用
- 采集完成后自动触发
- 用户反馈数据有问题需要重新清洗
- 接入新数据源后首次标准化

## 输入
`runs/<run_id>/raw/*.jsonl` + `sources.manifest.json`

## 处理流程（顺序不可调换）

1. **合并多源**：按 `primary_key` 合并；冲突时取 `updated_at` 较新的记录；冲突数记 `conflicts`
2. **字段映射校验**：所有未命中 `field_map` 的原始字段记入 `unmapped_fields`
3. **类型转换**
   - 数字：去掉千分位 `,`、货币符号 `¥$€`、百分号 `%`（并除以 100）
   - 日期：统一转 ISO 8601，按 Asia/Shanghai 解释
   - 布尔：`是/否`、`Y/N`、`1/0` → `true/false`
4. **去重**：按 `primary_key` 保留 `updated_at` 最新一条，丢弃数记 `dropped.duplicate`
5. **缺失处理**
   - 关键字段（primary_key、时间、核心指标）缺失 → 丢弃，记 `dropped.missing_key`
   - 非关键字段缺失 → 填 `null` 并加标记字段
6. **异常标记**：对每个数值字段用 IQR 法（Q1-1.5IQR / Q3+1.5IQR）标出异常点，新增 `is_outlier_<field>` 布尔字段。**只标记不删除。**
7. **质量打分**：`quality_score = 1 - (丢弃率 × 0.6 + 关键字段缺失率 × 0.4)`，保留 2 位小数
8. **落盘**：`clean/data.json` + `quality.json`

## 输出契约
`quality.json` 结构见 PRD 5.3。**必须自洽**：
```
rows_in - (duplicate + missing_key + out_of_range) == rows_out
```

## 自检（必做）
1. 用代码验算上面的等式，不相等就查原因并修正
2. `quality_score < 0.8` 时，warnings 里必须写清「哪一步丢得最多」+ 改进建议
3. 每个数值字段都要有 min/max/null_rate，不能缺

## 红线
- 绝不静默丢数据。任何丢弃都要计数并出现在 quality.json
- 绝不更改原始值。清洗结果写到新文件，原始 raw 保持不动
- 绝不对缺失值做「猜测式填充」（如用均值填关键指标）
```

---

# 技能 4 · datapulse-analyst

```markdown
---
name: datapulse-analyst
description: 多维数据分析技能。按 plan.json 对干净数据执行趋势/对比/构成/异常/交叉五类分析，产出 insights.json（图表数据 + 带证据的结论 + 建议）。当需要从数据中提炼洞察时使用。
agent_created: true
---

# DataPulse 分析师

## 何时使用
- 清洗完成后自动触发
- 用户要求换角度重新分析
- 需要为看板或报告产出结构化分析结果

## 输入
`plan.json` + `clean/data.json` + `quality.json`

## 计算原则（最高优先级）
- **所有数值计算用脚本完成**（Python/Node），结果写 JSON。LLM 不用来"估算"数字。
- 大表先聚合再输出。页面只渲染聚合结果，不塞明细。
- 数据不足时不要输出空数组，改为不输出该图表并在 `warnings` 说明。

## 五类必做分析

| 类型 | 做法 | 图表 |
|---|---|---|
| 趋势 | 按 `granularity` 聚合主指标，算环比 wow / 同比 yoy | line |
| 对比 | 按第一维度拆解，Top-N + 其余归并「其他」 | bar |
| 构成 | 按维度算占比，含本期 vs 上期占比变化 | pie |
| 异常 | 找出偏离滚动均值 2σ 以上的点，输出日期/指标/偏离幅度/影响量级 | scatter 或标注 |
| 交叉 | dimensions ≥ 2 时做二维交叉定位问题集中区 | heatmap |

## 结论生成规则
- 每条 conclusion 必须能追溯，格式 `{"text": "...", "evidence": "c1"}`
- 每条必须带具体数字：绝对值 + 变化幅度 + 时间范围
- 禁止「如图所示」「表现较好」「有所下滑」等无量化表述
- 异常不能只报「有异常」，必须给出偏离量级 + 可能集中的维度

## 输出契约
`insights.json`，schema 见 PRD 5.4：
`kpis` / `charts` / `cross_analysis` / `anomalies` / `conclusions` / `recommendations` / `warnings`

## 自检（输出前必做）
1. 每个 chart 的 series 长度是否与 x 一致？
2. kpis 的 value 是否与 charts 数据对得上？
3. 是否存在没有 evidence 的 conclusion？有就删
4. 结论方向与数据方向是否一致（下滑不能写成增长）？
任一条不通过就修正后重出。

## 红线
- 绝不使用 config 之外的指标口径
- 绝不产出无证据的结论
- 绝不把明细数据全量写进 insights.json
```

---

# 技能 5 · datapulse-dashboard

```markdown
---
name: datapulse-dashboard
description: 可视化看板生成技能。把 insights.json 渲染成自包含的 dashboard.html（ECharts，离线可用），含 KPI 卡、趋势图、维度拆解、交叉热力图、异动预警。当需要产出或更新可视化看板时使用。
agent_created: true
---

# DataPulse 看板生成器

## 何时使用
- 分析完成后自动触发
- 用户要求调整看板模块或配色
- 新增指标后刷新看板版式

## 输入
`runs/<run_id>/insights.json`（+ `quality.json` 取质量分）

## 技术要求
- 输出单文件 `dashboard.html`，双击可开；数据通过 `fetch('insights.json')` 读取，不硬编码
- 图表库 ECharts，用本地 `assets/echarts.min.js`，不依赖 CDN
- 配色遵循中式金融习惯：**涨红跌绿**。金额用 ¥
- 响应式：< 900px 单列堆叠
- 生成后更新 `cache/latest` 指向本 run

## 页面结构（自上而下）
1. 顶部条：产品名 · 数据新鲜度 · 质量分徽标 · run_id · 更新按钮
2. KPI 卡片区：数值 + 环比箭头 + 迷你趋势线
3. 趋势区：type=line 全宽
4. 维度拆解区：type=bar 与 type=pie 并排
5. 交叉热力图区：type=heatmap（存在才渲染）
6. 异动预警列表：日期 / 指标 / 偏离幅度 / 备注
7. 结论与建议：分栏
8. 筛选器：时间范围、维度多选、对比开关（纯前端过滤）
9. 导出：PNG / CSV / 查看报告链接

## 边界情况（必须处理）
| 情况 | 处理 |
|---|---|
| 数据为空 | 空状态插画 + 「先去更新数据」按钮 |
| 只有 1 个数据点 | 折线退化为点/单柱 |
| 全零数据 | 正常渲染坐标系，不出现 NaN |
| 图表类型缺失 | 跳过该区块，不留空白 |

## 质量要求
- `quality_score < 0.8` 时顶部显示黄色告警条
- 所有 tooltip 显示原始值 + 变化幅度
- 页面首屏 ≤ 1.5s

## 红线
- 绝不硬编码数据进 HTML（否则数据更新后看板不刷新）
- 绝不在看板里做需要重新分析的计算
```

---

# 技能 6 · datapulse-reporter

```markdown
---
name: datapulse-reporter
description: 专项分析报告生成技能。基于 insights.json 与 quality.json 产出结构化的 report.html（7 节固定结构：摘要/需求理解/数据说明/分析正文/归因/建议/附录），支持导出 Word 与 PDF。当用户要求分析报告、结论总结、归因说明时使用。
agent_created: true
---

# DataPulse 报告撰写器

## 何时使用
- plan 的 output_type 含 report
- 用户说「出一份报告」「总结一下」「为什么」
- 需要把看板结论整理成交付物

## 输入
`insights.json` + `quality.json` + `plan.json`

## 输出
`runs/<run_id>/report.html`（自包含单文件，内联样式 + ECharts）

## 报告结构（固定 7 节）
1. **摘要**：3–5 条核心结论，每条一个关键数字，讲清「发生了什么、在哪、多严重」
2. **需求理解与假设**：回显 plan 的 intent 与 assumptions，供用户核对
3. **数据说明**：数据源清单、行数、时间范围、质量分、已知缺陷
4. **分析正文**：按主题分节，每节 = 1 图 + 1 段解读 + 证据引用。解读要解释「为什么这图重要」，不是复述图
5. **归因分析**：可能原因 + 置信度（高/中/低）+ 支撑证据。置信度低要说明还缺什么数据
6. **行动建议**：3–5 条，每条含「做什么 + 预期影响 + 优先级 P0/P1/P2」，必须可执行有主语
7. **附录**：指标口径定义、数据源、run_id、生成时间

## 写作规范
- 中文，专业但不堆术语
- 金额用 ¥，时间写具体日期
- 禁止无数字的形容词式结论
- 数据不支持的话题直接写「本次数据无法验证」，不编造
- 每段 3–5 句，避免文字墙

## 自检（输出前逐条核对）
1. 每条结论都能在 insights.json 找到对应数据？
2. 有没有只有标题没有内容的节？
3. 建议是否可落地到具体动作？
4. 有没有与数据方向矛盾的表述？
5. 有没有编造行业背景或外部信息？有就删

## 导出
- HTML 直接用浏览器的「打印 → 存为 PDF」
- 需要 .docx 时，把报告内容交给 Word 文档技能做格式转换

## 红线
- 绝不编造未在数据中出现的信息
- 绝不写无量化依据的结论
- 绝不让结论与数据方向矛盾
```

---

# 技能 7 · datapulse-app-shell

```markdown
---
name: datapulse-app-shell
description: DataPulse 前端四页面生成技能。生成 index.html（主页：更新按钮 + 需求输入）、analysis.html（六阶段过程流）、dashboard.html（看板）、report.html（报告）及其样式脚本。当首次搭建产品前端、或需要改版页面时使用。
agent_created: true
---

# DataPulse 前端壳生成器

## 何时使用
- 项目初始化，还没有前端
- 需要调整页面结构或样式
- 需要新增页面

## 技术约束
- 纯静态 HTML + 原生 JS + ECharts（本地文件，无 CDN）
- 无框架、无构建步骤
- 数据一律 `fetch` 同目录 JSON
- 中文界面，中式金融配色（涨红跌绿），金额 ¥
- 响应式：< 900px 单列

## 四个页面

### index.html
- 数据新鲜度卡片（读 `cache/latest/quality.json`）
- 「更新数据」主按钮 → 触发采集 → 按钮转进度态 → 轮询 `status.json`
- 需求输入框 + 3 个快捷模板芯片
- 提交 → 生成 run_id → 跳转 `analysis.html?run=<run_id>`
- 底部最近 10 次 run 列表（读 `cache/index.json`）

### analysis.html
- 顶部：run_id + 状态徽标
- 六阶段步骤条，四态：pending / running（脉冲动画）/ done / failed
- Plan 卡片：展示解析出的指标、维度、时间范围、假设
- 待确认区：`clarifications_needed` 非空时以选项按钮呈现，选择后写 `control.json`
- 实时日志区：滚动展示 message，自动滚底
- 底部：中断按钮 / 完成后「查看看板」「查看报告」
- 每 1s 轮询 `status.json`，终态时停止并跳转

### dashboard.html
- 读 `cache/latest/insights.json` 渲染，结构见看板技能
- 刷新即生效，不重新生成

### report.html
- 读 URL 参数 run 指定的报告
- 左侧锚点目录 + 右侧正文
- 「导出 Word」「打印 PDF」按钮

## 交付物
`index.html` / `analysis.html` / `dashboard.html` / `report.html` / `assets/app.css` / `assets/app.js`

## 要求
- 每个页面独立打开都不报错（数据缺失时展示空状态而非白屏）
- 代码写注释
- 本地预览：`python -m http.server 8000`
- 需要在线分享时，走「发布为应用」能力

## 红线
- 绝不引入外部 CDN 依赖（保证离线可用）
- 绝不把数据硬编码进页面
```
