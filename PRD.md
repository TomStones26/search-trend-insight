# DataPulse 产品需求文档（PRD）

| 项 | 内容 |
|---|---|
| 产品名 | DataPulse · 数据脉冲 |
| 版本 | v0.1（立项稿） |
| 状态 | 待评审 |
| 定位一句话 | 一个「说一句话，就自动更新数据、跑完多维分析、给出看板与报告」的轻量数据产品 |
| 形态 | 本地/云端 Agent 引擎 + 4 个 HTML 页面 |

---

## 1. 背景与目标

### 1.1 要解决的问题

日常做数据洞察有三段重复劳动：

- **找数据**：每次都要重新抓一遍，或者翻上次的表格，口径还对不上。
- **洗数据**：字段名不统一、缺值、重复、单位混乱，手工清洗占掉 60% 时间。
- **出结论**：想看的维度每次都要重新拉透视表，看板做完就过时，专项报告又要重做一遍。

### 1.2 产品目标

把这三段合成一条自动化流水线：**一键更新数据** → **一句话描述需求** → **自动多维分析** → **常态化看板 + 专项报告**。

### 1.3 成功指标（M1 阶段）

| 指标 | 目标 |
|---|---|
| 数据更新成功率 | ≥ 95%（单次全量采集） |
| 端到端耗时 | 中等数据量（10 万行内）从点击到出看板 ≤ 5 分钟 |
| 需求解析准确率 | 第一版 Plan 无需人工修改即被采纳的比例 ≥ 70% |
| 看板复用率 | 常态看板无需重新生成即可直接查看的比例 ≥ 80% |
| 报告可用性 | 生成报告经人工小改后可直接交付的比例 ≥ 60% |

### 1.4 非目标（本期不做）

- 不做多人协同、权限体系、审批流。
- 不做实时流式数据（秒级/毫秒级），最小粒度为「按需触发的一次批处理」。
- 不做复杂机器学习建模（预测/分类模型），只做描述性 + 诊断性分析。
- 不做绕过登录墙、验证码、付费墙的数据获取。

---

## 2. 用户与场景

### 2.1 目标用户

| 角色 | 诉求 |
|---|---|
| 分析师 / 运营 | 快速拿到带结论的看板和报告，不想手工清洗 |
| 产品 / 业务负责人 | 用自然语言问「上周哪个渠道掉得最狠」，要立刻看到答案 |
| 独立研究者 | 有一批自己关心的公开数据，想让它自动更新并长期跟踪 |

### 2.2 典型场景

**场景 A · 日常盯盘**
用户打开主页 → 点「更新数据」→ 等 1 分钟 → 看板自动刷新 → 扫一眼 KPI 卡片和异动列表 → 关闭。全程不输入任何需求。

**场景 B · 专项追问**
用户输入「对比最近 8 周各渠道的转化率，指出下滑最明显的两个渠道并给出可能原因」→ 跳转分析页看步骤流 → 自动生成专属看板 + 一份专项分析报告。

**场景 C · 定期跟踪**
用户配置「每周一 09:00 自动更新 + 生成周报」，每周收到一份新的报告和更新后的看板。

---

## 3. 产品形态与信息架构

### 3.1 页面流

```
┌──────────────┐   输入需求    ┌──────────────┐   完成   ┌──────────────┐
│  index.html  │ ───────────▶ │ analysis.html│ ──────▶ │dashboard.html│
│  主页         │              │  分析过程页   │         │  可视化看板   │
│  · 更新数据   │              │  · 步骤流     │    ──▶  ├──────────────┤
│  · 需求输入框 │              │  · 实时日志   │         │  report.html │
└──────────────┘              └──────────────┘         │  分析报告     │
       ▲                                               └──────────────┘
       └───────────────────── 返回 ──────────────────────────┘
```

### 3.2 页面职责

| 页面 | 职责 | 关键元素 |
|---|---|---|
| `index.html` | 入口与触发 | 数据新鲜度徽标、`更新数据` 主按钮、需求输入框（支持快捷模板）、历史 Run 列表 |
| `analysis.html` | 过程透明化 | 6 个阶段的步骤条（待执行/进行中/完成/失败）、实时日志滚动区、Plan 卡片（展示 LLM 对需求的理解）、可中断 |
| `dashboard.html` | 常态看板 | KPI 卡、趋势图、维度拆解、异动预警、筛选器（时间/维度）、导出 |
| `report.html` | 专项报告 | 结论摘要、证据图表、归因分析、建议清单、导出 Word/PDF |

### 3.3 关键设计原则

- **过程可见**：分析页必须让用户看到「AI 理解成了什么」，避免黑箱。Plan 卡片需可视化展示解析出的指标、维度、时间范围。
- **产物可复用**：看板是常态的，每次更新数据只刷新数据不动版式；报告是专项的，每次需求单独生成。
- **落盘优先**：所有中间产物写成本地 JSON/JSONL 文件，页面只读文件，刷新不丢结果。

---

## 4. 系统架构

### 4.1 架构选型

**Agent 是引擎，HTML 是壳。**

浏览器无法直接跑爬虫和调用 LLM，所以：

- **执行层**：由 Agent（WorkBuddy 会话 / Skill）承担采集、清洗、分析、生成。
- **存储层**：本地目录 `runs/<run_id>/` 存放全部中间产物与最终页面。
- **展示层**：4 个静态 HTML 页面，通过 `fetch` 读取同目录 JSON 渲染。
- **运行方式**：`python -m http.server 8000` 本地起服务，或直接发布到线上。

### 4.2 目录结构

```
datapulse/
├── index.html                     # 主页
├── analysis.html                  # 分析过程页
├── dashboard.html                 # 常看看板（读 latest）
├── report.html                    # 报告页（读指定 run）
├── assets/
│   ├── app.css
│   ├── echarts.min.js
│   └── app.js
├── config/
│   ├── product.config.json        # 产品配置（域、源、指标、维度）
│   └── sources/*.json             # 每个数据源的 Adapter 配置
├── runs/
│   └── 20260916-1730-ab12/        # 一次运行 = 一个 run_id
│       ├── status.json            # 状态机（前端轮询这个）
│       ├── plan.json              # 需求解析结果
│       ├── raw/items.jsonl        # 原始数据
│       ├── clean/data.json        # 清洗后数据
│       ├── quality.json           # 数据质量报告
│       ├── insights.json          # 分析结果（看板与报告的数据源）
│       ├── dashboard.html         # 本次生成的看板快照
│       └── report.html            # 本次生成的报告
└── cache/
    ├── latest -> runs/<最新 run_id>
    └── index.json                 # 历史 run 索引
```

### 4.3 流水线六阶段

| 阶段 | 名称 | 输入 | 输出 | 归属 Skill |
|---|---|---|---|---|
| S0 | 需求解析 | 用户自然语言 + `product.config.json` | `plan.json` | `datapulse-orchestrator` |
| S1 | 数据采集 | `plan.json` + sources 配置 | `raw/*.jsonl` + 采集清单 | `datapulse-collector` |
| S2 | 数据清洗 | `raw/*.jsonl` | `clean/data.json` + `quality.json` | `datapulse-cleaner` |
| S3 | 多维分析 | `clean/data.json` + `plan.json` | `insights.json` | `datapulse-analyst` |
| S4 | 看板生成 | `insights.json` | `dashboard.html` | `datapulse-dashboard` |
| S5 | 报告生成 | `insights.json` + `quality.json` | `report.html` | `datapulse-reporter` |

### 4.4 增量更新策略

- 每个数据源在 Adapter 里声明 `primary_key` 与 `updated_at 字段`。
- 更新时先按 `primary_key` 做 upsert 合并，写入 `cache/store/<source>.jsonl`。
- 全量重抓仅在用户显式点击「全量重建」时执行。
- 同一个 `primary_key` 若 `updated_at` 更新，保留新记录，旧记录归档到 `archive/`。

---

## 5. 数据契约

所有产物文件必须符合以下结构。**这是页面与 Agent 之间的唯一接口**，任何一方都不能随意改字段名。

### 5.1 `plan.json`

```json
{
  "run_id": "20260916-1730-ab12",
  "created_at": "2026-09-16T17:30:12+08:00",
  "input": { "raw_question": "对比最近8周各渠道转化率", "mode": "adhoc" },
  "intent": {
    "output_type": "dashboard+report",
    "goal": "定位转化率下滑最严重的渠道",
    "entities": ["渠道转化率"],
    "time_range": { "start": "2026-07-21", "end": "2026-09-16", "granularity": "week", "compare": "wow" },
    "metrics": [
      { "key": "conversion_rate", "name": "转化率", "formula": "orders / visits", "unit": "%" }
    ],
    "dimensions": [
      { "key": "channel", "name": "渠道" },
      { "key": "week", "name": "周" }
    ],
    "filters": [{ "field": "status", "op": "=", "value": "valid" }],
    "top_n": 5,
    "charts": ["line", "bar", "heatmap"]
  },
  "clarifications_needed": [],
  "assumptions": ["未指定订单口径，默认取 status=valid 的已支付订单"]
}
```

### 5.2 `status.json`

```json
{
  "run_id": "20260916-1730-ab12",
  "state": "running",
  "started_at": "2026-09-16T17:30:12+08:00",
  "updated_at": "2026-09-16T17:31:40+08:00",
  "current_step": "S2",
  "progress": 0.45,
  "steps": [
    { "id": "S0", "name": "需求解析", "state": "done",   "message": "识别 2 指标 / 2 维度", "started_at": "...", "ended_at": "...", "artifacts": ["plan.json"] },
    { "id": "S1", "name": "数据采集", "state": "done",   "message": "抓取 12,480 条",     "started_at": "...", "ended_at": "...", "artifacts": ["raw/items.jsonl"] },
    { "id": "S2", "name": "数据清洗", "state": "running","message": "去重中 3,200/12,480", "started_at": "...", "ended_at": null, "artifacts": [] },
    { "id": "S3", "name": "多维分析", "state": "pending","message": "", "started_at": null, "ended_at": null, "artifacts": [] },
    { "id": "S4", "name": "看板生成", "state": "pending","message": "", "started_at": null, "ended_at": null, "artifacts": [] },
    { "id": "S5", "name": "报告生成", "state": "pending","message": "", "started_at": null, "ended_at": null, "artifacts": [] }
  ],
  "error": null
}
```

### 5.3 `quality.json`

```json
{
  "rows_in": 12480,
  "rows_out": 11702,
  "dropped": { "duplicate": 620, "missing_key": 158, "out_of_range": 0 },
  "fields": [
    { "name": "channel", "type": "string", "null_rate": 0.0, "distinct": 6, "sample": ["A", "B", "C"] },
    { "name": "conversion_rate", "type": "number", "null_rate": 0.004, "min": 0.0, "max": 0.42, "outliers": 3 }
  ],
  "quality_score": 0.94,
  "warnings": ["channel='other' 占比 11.3%，建议补充映射规则"]
}
```

### 5.4 `insights.json`

```json
{
  "run_id": "20260916-1730-ab12",
  "generated_at": "2026-09-16T17:33:02+08:00",
  "kpis": [
    { "key": "conversion_rate", "name": "整体转化率", "value": 0.128, "unit": "%", "delta": -0.021, "delta_type": "wow", "trend": "down" }
  ],
  "charts": [
    {
      "id": "c1",
      "type": "line",
      "title": "各渠道周转化率趋势",
      "x": ["W31", "W32", "W33", "W34", "W35", "W36", "W37", "W38"],
      "series": [
        { "name": "渠道A", "data": [0.152, 0.148, 0.141, 0.139, 0.132, 0.128, 0.121, 0.118] },
        { "name": "渠道B", "data": [0.164, 0.160, 0.155, 0.149, 0.140, 0.133, 0.127, 0.124] }
      ],
      "insight": "渠道A 与渠道B 连续 8 周下滑，累计跌幅分别为 -22.4% 与 -24.4%"
    }
  ],
  "cross_analysis": [
    { "dimension": "channel × device", "finding": "渠道A 的下滑集中在 iOS 端（-31%），Android 端基本持平", "evidence": "c2" }
  ],
  "anomalies": [
    { "date": "2026-08-11", "metric": "conversion_rate", "deviation": -0.28, "note": "当日渠道B 转化率仅 0.118，低于均值 2.1 个标准差" }
  ],
  "conclusions": ["...", "..."],
  "recommendations": ["...", "..."]
}
```

---

## 6. 功能需求

### FR-01 数据源管理与适配器

**描述**：支持配置多个数据源，每个数据源由一份 Adapter 配置 + 一段抓取逻辑组成。

**Adapter 配置字段**：`id` / `name` / `type`（api|html|csv|connector）/ `entry`（URL 或连接器 ID）/ `schedule` / `primary_key` / `updated_at_field` / `field_map`（原始字段 → 标准字段）/ `limit`。

**验收标准**
- 新增一个数据源只需新增一个配置文件，不改动其他任何代码。
- 单源抓取失败不影响其他源，失败信息写入 `status.json` 的 `error`。

### FR-02 一键更新数据

**描述**：主页主按钮，触发全量或增量采集。

**验收标准**
- 点击后 2 秒内主页出现进度反馈（按钮转为进度态）。
- 更新完成后主页显示「数据新鲜度」：最新记录时间 + 本次新增/更新条数。
- 支持 `?full=1` 触发全量重建。
- 抓取遵循：请求间隔 ≥ 1s、遵守 robots.txt、仅抓公开数据、UA 标识来源。

### FR-03 数据清洗与质量报告

**描述**：对原始数据做标准化处理并产出质量报告。

**处理规则（固定顺序）**
1. 字段映射：按 `field_map` 统一字段名。
2. 类型推断与转换：数字/日期/枚举。
3. 去重：按 `primary_key` 保留 `updated_at` 最新的一条。
4. 缺失处理：关键字段缺失的行直接丢弃并计数；非关键字段填默认值或标记。
5. 异常值标记：3σ 或 IQR 法标记但不删除，保留 `is_outlier` 字段。
6. 质量打分：`quality_score = 1 - (丢弃率 × 0.6 + 关键字段缺失率 × 0.4)`。

**验收标准**
- 输出 `quality.json`，`rows_in`、`rows_out`、`dropped` 三者自洽。
- `quality_score < 0.8` 时，在分析页与看板顶部显示黄色告警。

### FR-04 需求解析（INPUT → Plan）

**描述**：把用户输入的自然语言转成结构化分析计划。

**验收标准**
- 输出符合 5.1 的 `plan.json`。
- 必须显式列出 `assumptions`（做了哪些默认假设），并在分析页 Plan 卡片中展示。
- 无法确定的关键信息（如时间范围、指标口径）写入 `clarifications_needed`，前端以可点击选项形式让用户确认，而不是直接失败。
- 输入为空时，走「默认常态分析」：使用 `product.config.json` 里的 `defaultPlan`。

### FR-05 多维分析

**描述**：按 Plan 执行分析，产出 KPI、图表数据、交叉分析、异动、结论、建议。

**必做分析类型**（至少 4 类）
1. **趋势**：时间序列 + 环比/同比。
2. **对比**：维度拆解（Top-N + 长尾归并）。
3. **构成**：占比与结构变化。
4. **异常**：偏离均值 2σ 以上的点，附时间与量级。
5. **交叉**（当 `dimensions` ≥ 2 时必做）：二维交叉定位问题集中区。

**验收标准**
- 每条 `conclusion` 必须能追溯到某个 `chart.id` 或 `cross_analysis.evidence`，禁止无证据结论。
- `charts[].insight` 必须是「带数字的句子」，不能是「如图所示」这类空话。

### FR-06 分析过程可视化

**描述**：分析页实时展示流水线状态。

**验收标准**
- 轮询 `status.json`（间隔 1s），渲染 6 个阶段步骤条，状态四态：pending / running / done / failed。
- 实时日志区展示各阶段 `message`，自动滚到底部。
- 失败时展示错误摘要 + 重试按钮。
- 支持中断（按钮写 `control.json` 的 `abort: true`，编排层在阶段边界检查）。

### FR-07 常态化看板

**描述**：`dashboard.html` 读取 `cache/latest` 的数据渲染。

**模块清单**
1. 顶部条：数据新鲜度、质量分、run_id、更新按钮。
2. KPI 卡片区：4–6 个核心指标，含数值 + 环比 + 迷你趋势图。
3. 趋势区：主指标时间序列（可切粒度：日/周/月）。
4. 维度拆解区：Top-N 条形图 + 占比环形图。
5. 交叉热力图（当维度 ≥ 2）。
6. 异动预警列表：日期、指标、偏离幅度、备注。
7. 筛选器：时间范围、维度多选、对比开关。
8. 导出：PNG / CSV / 打开报告。

**验收标准**
- 数据更新后刷新页面即生效，无需重新生成看板 HTML。
- 空数据、单数据点、全零数据均不报错，展示空状态占位。

### FR-08 专项分析报告

**描述**：`report.html` 输出结构化报告。

**报告结构**
1. 摘要（3–5 条核心结论，每条带一个关键数字）。
2. 需求理解与假设（回显 Plan，让用户核对）。
3. 数据说明（数据量、时间范围、质量分、已知缺陷）。
4. 分析正文（每节：图 + 解读段落 + 证据引用）。
5. 归因分析（下滑/增长可能原因，标注置信度：高/中/低）。
6. 行动建议（可执行、有优先级、有预期影响）。
7. 附录：指标口径定义、数据源清单、run_id。

**验收标准**
- 每节至少 1 张图，至少 1 段解读。
- 结论与建议不得与数据矛盾（生成后做一次自检）。
- 支持导出为 Word / PDF。

### FR-09 历史与回溯

- `cache/index.json` 记录全部 run：`run_id`、时间、问题、状态、耗时。
- 主页展示最近 10 次 run，可点进查看对应看板/报告。
- 相同问题的历史 run 可对比。

---

## 7. 非功能需求

| 类别 | 要求 |
|---|---|
| 性能 | 10 万行内数据，单阶段 ≤ 90s；页面首屏 ≤ 1.5s |
| 可靠性 | 单源失败不阻断整体；每阶段产物原子写入（先写 `.tmp` 再 rename） |
| 可扩展 | 新增数据源/新增分析类型不改动既有 Skill |
| 可观测 | 每次 run 保留完整日志，可复现 |
| 合规 | 遵守 robots.txt 与站点 ToS；限速；不抓个人隐私数据；不绕过登录/验证码；数据仅本地存储，不外传 |
| 可移植 | 换领域只改 `product.config.json` 与 `sources/*.json` |

---

## 8. 里程碑

### M0 · 打通链路（最小可用）
- 1 个数据源（公开可抓的接口或公开数据集）、固定 1 个问题、产出看板。
- 验收：点击更新 → 输入问题 → 看到 1 个 KPI 卡 + 1 张趋势图。
- 交付：`index.html`、`analysis.html`、`dashboard.html` + 3 个 Skill。

### M1 · 完整体验
- 3–5 个数据源、增量更新、清洗质量报告、专项报告、历史回溯。
- 验收：PRD 第 6 章 FR-01 至 FR-09 全部通过。
- 交付：全部 4 个页面 + 7 个 Skill。

### M2 · 自动化与增强
- 定时自动更新（用自动化任务每周跑一次）。
- 报告导出 Word/PDF、看板发布成在线链接。
- 同比/环比自动化、异常归因增强。
- 交付：自动化配置 + 发布链接。

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 目标站点改版 / 反爬 | 采集失败 | Adapter 独立 + 失败告警 + 备用数据源（连接器兜底） |
| LLM 误解需求 | 分析跑偏 | 强制输出 `assumptions` 并在分析页可视化，让用户先确认再执行 |
| 数据口径不一致 | 结论失真 | 所有指标在 `product.config.json` 里定义口径公式，禁止分析阶段临时改口径 |
| 数据量超预期 | 页面卡顿 | 分析阶段做聚合下钻，页面只渲染聚合结果，不渲染原始明细 |
| 合规风险 | 法律问题 | 只抓公开数据、限速、遵守 robots、不绕验证码；采集前过 compliance-guard 检查 |
| Token 成本失控 | 成本高 | 数据聚合后再送 LLM，不送原始明细；大表用代码算，不用 LLM 算 |

---

## 10. 开放问题

1. 数据领域与首批数据源清单？（决定 Adapter 与连接器选型）
2. 「常态看板」的固定 KPI 与维度是哪几个？
3. 是否需要多用户/权限？（当前假设不需要）
4. 报告交付格式：仅 HTML，还是必须导出 Word/PDF？
5. 部署形态：纯本地，还是要发布成在线链接共享？
