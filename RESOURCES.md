# DataPulse 资源选型参考

以下 ID 均为当前环境可检索到的真实候选。**按需选择，不必全装。**

---

## 一、连接器（Connector）

> 连接器是外部的数据源与执行能力。装了才能「真的抓到数据」，否则只能用手写爬虫。

### 1.1 采集 / 爬取类（对应 S1 数据采集）

| ID | 名称 | 用途 | 适配场景 |
|---|---|---|---|
| `bazhuayu` | 八爪鱼 | 自然语言驱动云采集：搜索模板、启动任务、查询进度、导出结构化数据 | **首选**。不想自己维护爬虫时用，直接拿结构化数据 |
| `agent-earth` | AgentEarth 金融电商社媒工具集 | 一次注册覆盖金融/电商/社媒数据 + **网页爬取代理** + 搜索采集 | 一个连接器覆盖多领域数据源，适合做「可换领域」的产品 |
| `qveris` | QVeris | 一个连接发现并调用 10,000+ 实时数据与工具服务 | 需求不确定、需要边做边找数据源时 |

### 1.2 数据源类（按领域选 1–2 个）

**财经 / 投研**
| ID | 名称 | 覆盖 |
|---|---|---|
| `tushare` | Tushare | A股、指数、ETF、财务、估值、资金流、公告、宏观 |
| `wind-finance` | Wind Alice | 全球 60+ 国家股票、基金、债券、商品、指数、经济数据 |
| `pandadata` | PandaData | A股/期货/期权/港美股/基金/宏观/量化因子 |
| `ifind-mcp` | 同花顺 iFinD | 投研级金融数据，支持日内实时行情、智能选股选基 |
| `dzh-mcp` | 大智慧 | K线、实时行情、财务三表、资金流向、龙虎榜、融资融券 |
| `mx-ds-mcp` | 东方财富妙想 | A股/港股/美股/基金/债券/指数板块/宏观，研报公告检索 |
| `datayes-data` | 通联数据 | 金融数据 + 因子 + 宏观 + 政策法规 |
| `xhcj-mcp-announcements-news-policy` | 新华财经资讯 | 公告、新闻、政策、快讯（适合做舆情/事件分析维度） |

**电商 / 跨境**
| ID | 名称 | 覆盖 |
|---|---|---|
| `fastmoss` | FastMoss | TikTok Shop 商品/达人/店铺/MCN/直播/广告/类目榜单 |
| `sif-mcp` | Sif 亚马逊运营分析 | 亚马逊市场、流量、广告架构、竞品监控 |
| `linkfox-product-selection` | LinkFox | 竞品查询、市场调研、店铺运营 |
| `proboost` | OpenBoost 跨境数据 | TikTok 达人/商品 + Amazon 选品 + 全球专利 |

**社媒 / 内容**
| ID | 名称 | 覆盖 |
|---|---|---|
| `noxinfluencer-cli` | NoxInfluencer | YouTube/TikTok/Instagram 红人数据、受众画像、内容表现 |
| `agent-earth` | AgentEarth | X/Twitter、YouTube、TikTok 社媒数据 |

**企业工商 / 风险**
| ID | 名称 |
|---|---|
| `tyc-mcp` | 天眼查（160+ 项企业数据） |
| `qcc-company` | 企查查 |
| `shuidi-credit` | 水滴信用（全维度尽调） |

### 1.3 BI / 看板 / 报告类（对应 S4、S5）

| ID | 名称 | 用途 | 备注 |
|---|---|---|---|
| `databuddy` | 腾讯云 DataBuddy | 问数、报告、**异动归因**、预测、生成实时更新仪表盘 | **与本产品定位重叠度最高**，可作为增强或对照方案 |
| `jiushuyun` | 九数云 BI | 上传 Excel/CSV 一键生成可视化报告、仪表板 | 快速验证看板效果时用 |
| `tdengine` | TDengine | 建分析、监控事件、创建可视化面板、问数与根因分析 | 数据量大、时序场景 |
| `tencent-dlc` | 腾讯云数据湖计算 | 执行 SQL/Spark SQL，浏览表与分区 | 需要 SQL 级分析时 |
| `cloudbase` | 腾讯云 CloudBase | 全栈开发、部署、静态托管、数据库 | 想把产品部署上线时 |

---

## 二、专家（Expert / Expert Team）

> 同一时间只能启用一个专家或专家团队。按阶段挑。

| ID | 名称 | 类型 | 用在哪个阶段 |
|---|---|---|---|
| `ProductStrategyTeam` | 产品战略团队 | 团队（含需求分析师/PRD、用户研究、竞品、数据分析、路线图） | **阶段 1 立项**：写 PRD、拆功能规格、定指标 |
| `ProductManagementExpert` | 产品通 | 单专家 | 只想快速出一份功能规格 |
| `DataAnalyticsReporter` | 舒明析 | 单专家 | **阶段 2 方法论**：KPI 框架设计、指标诊断、数据质量评估 |
| `DataAnalysisExpert` | 析数数 | 单专家 | 表格级数据分析与可视化 |
| `ModernWebappExpert` | 速构构 | 单专家 | **阶段 3 前端**：React+TS+Vite+Tailwind+shadcn 实现页面 |
| `MvpDevExpertTeam` | MVP 开发专家团 | 团队（8 位：调研→设计→编码→测试→部署） | 从想法到 MVP 全流程 |
| `SoftwareCompany` | 软件开发团队 | 团队（PM/架构/工程/QA） | 需求会持续迭代的工程化项目 |
| `DeepResearchExpert` | 深研研 | 单专家 | 需要外部资料、竞品、事实验证时 |

**推荐组合路径**
1. 立项 → 启用 `ProductStrategyTeam`，产出 PRD 与功能规格（本包已给初稿，可让它继续深化）
2. 方法论 → 切到 `DataAnalyticsReporter`，把 KPI 框架和指标口径定死
3. 开发 → 切到 `ModernWebappExpert` 或 `MvpDevExpertTeam` 实现前端与后端

---

## 三、内置技能（Skill）

| 技能 | 用途 | 用在哪 |
|---|---|---|
| `发布为应用` | 把本地项目发布成在线链接 | M2：把看板共享出去 |
| `tencent-docx` | Word 文档生成与排版 | 报告导出 .docx |
| `tencent-pptx` | PPT 生成 | 把报告转成汇报 PPT |
| `tencent-docs-sheetagent` | 表格读写、查询、数据分析、公式、图表、透视表 | 清洗与分析的表格操作底层能力 |
| `agent-browser` | 浏览器自动化：打开页面、截图、提取内容、点击 | 抓取动态渲染页面（S1） |
| `find-skills` | 搜索并安装新技能 | 需要新能力前先查一遍 |
| `skill-creator` | 创建新技能 | 本包的 7 个技能需要调整时 |

---

## 四、推荐起步组合（M0 最小可用）

如果只想最快跑通，装这三样就够：

| 类型 | 选择 | 理由 |
|---|---|---|
| 连接器 | `bazhuayu` 或 `agent-earth` | 解决「数据从哪来」 |
| 专家 | `ProductStrategyTeam` | 解决「做什么、怎么做」 |
| 技能 | 本包的 `datapulse-collector` + `datapulse-analyst` + `datapulse-dashboard` | 解决「跑起来」 |

跑通之后再补 `datapulse-cleaner`、`datapulse-reporter`、`datapulse-app-shell`。
