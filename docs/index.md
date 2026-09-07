# Liveprofit（LiveProfit）多智能体架构重构技术方案

## 市场 — 板块 — 个股三维度分析

> **AI 速览**：市场层（独立子图，7 个 Agent）✅，板块层（独立子图，3 个 Analyst）✅，选股层/仓位管理层（纯代码层，无 LLM）✅，事件研究系统（金融事件影响分析，独立子系统）✅，全市场日线本地库（数据层子系统）✅，个股层持续更新，**后端平台（Web API / Worker / Dispatcher）✅**。
>
> **状态**：市场层 ✅ 板块层 ✅ 选股层 ✅ 仓位管理层 ✅ 事件研究系统 ✅ 全市场日线本地库 ✅ 后端平台 ✅ 个股层持续更新
>
> **关联目录**：`AI/agents/`、`AI/marketAgents/`、`AI/sectorAgents/`、`AI/screening/`、`AI/position/`、`AI/dataflows/`（含 `store/`）、`AI/eventStudy/`、`AI/graph/`、`AI/templates/`、`backend/`（平台）、`docker/`（编排）

### 后端平台（2026-09-06 起）

- **契约与启动**：[API 契约 v1](API契约.md)（冻结端点/字段/错误码，前端实现参照）；启动方式见 `README.md`「平台模式启动」或 `./run.sh platform`
- **架构**：模块化单体（backend/ 可导入包）；API（REST/SSE）＋ Dramatiq Worker（可靠任务执行：Outbox + 租约 fencing + Redis Stream 事件流）＋ Dispatcher（发布/恢复唯一调度者）；PostgreSQL 业务真相，Redis Broker/事件流
- **设计文档**：[后端平台技术方案（已归档）](done/后端方案.md)；产品基线：[产品需求分析](plans/产品需求分析.md)

---

## 目标

在现有以"个股"为中心的分析师体系之上，新增"市场（宏观 + 国际 + 各国技术面）"与"板块（全市场行业/概念强度对比）"两个维度的分析师，形成**自顶向下的三层分析金字塔**，为后续研究员辩论、风险评估、交易决策提供更完整的多维度依据。

---

## 文档导航

| 文档 | 类型 | 内容 |
|------|------|------|
| [市场层](市场层.md) | 主干架构 | 市场层架构定义（已实现 ✅） |
| [板块层](板块层.md) | 主干架构 | 板块层架构定义（已实现 ✅） |
| [个股层](个股层.md) | 主干架构 | 个股层架构定义（持续更新） |
| [plans/](plans/) | 施工方案 | 单次重构/新功能的临时方案（实施完归档） |

> **约定**：主干文档随项目持续演进；`plans/` 下的方案文档实施完成后整合进主干并归档 `done/`。

---

## 一、现状诊断

Liveprofit（LiveProfit）现有 12 个 Agent，全部锚定在"个股"维度：

| 层级 | 现有 Agent | 问题 |
|------|------------|------|
| **分析师** | 个股技术面 / 基本面 / 新闻 / 情绪，均以 ticker 为中心 | 没有独立的"大盘环境"判断，`get_china_market_overview` 只是个股技术分析师可选小工具，未形成独立报告 |
| **板块** | `tech_market_analyst` 已删除 | 全市场行业/概念板块的横向强弱对比、轮动排名，完全空白 |
| **研究/辩论/风控/交易** | 读取 4 份个股报告 + 7 份市场层宏观报告 | 已注入市场层上下文 |
| **历史类比** | 国际新闻分析中内嵌历史案例检索 | 通过静态 JSON 关键词匹配实现，不依赖外部 API |

### 改造方向

要形成"**市场 → 板块 → 个股**"自顶向下分析框架，需要：

- **分析师层**：市场层已实现（7 个 Agent + 独立子图），板块层待实现
- **下游消费**：辩论、经理、风控已注入市场层上下文（`build_market_layer_context()`）
- **数据层**：已补齐 14 个新数据函数 + 5 个占位函数

---

## 二、总体设计：三层金字塔架构

### 设计原则

**新增不改旧**：

- 市场层作为独立编译的 LangGraph 子图，插入到个股层之前
- 各层输出各自独立的 report 字段，不影响现有个股分析师的内部逻辑
- 下游节点（研究员/经理/风控）做增量式 prompt 扩展，把新报告作为附加上下文纳入即可
- **向后兼容**：找不到字段时可为空字符串

### 三层 Subgraph 架构概览

```
顶层 Graph（个股维度）
├── Subgraph: 市场层（★ 已实现）    ← 独立编译，共享 state
│   ├── Layer 0: 国际新闻分析 (Global)
│   │   └── 宏观事件 + 历史案例类比
│   └── Layer 1: 各国市场分析（可扩展）
│       ├── US: 新闻分析 + 技术分析
│       ├── KR: 新闻分析 + 技术分析
│       └── CN: 新闻分析 + 技术分析 ★ 主战场
├── Subgraph: 板块层（★ 已实现）    ← 独立编译，共享 state
│   ├── 板块新闻分析 — 行业排名 + 资金流向 + 板块轮动
│   ├── 板块技术分析 — 全行业技术扫描 + AI专题深挖 + 风格验证
│   └── 板块轮动预测 — 打板题材逐日轮动 + 明日预测
├── 选股层（★ 已实现）              ← 普通函数节点，纯代码，无 LLM（仅全市场模式）
│   └── 东财概念成分股 → 近 N 日涨幅 → 超板块均值筛选 + 流动性过滤 → candidate_stock_pool
├── 仓位管理层（★ 已实现）          ← 纯代码，无 LLM（仅全市场模式）
│   └── 多票决策 + 总资金/持仓/仓位规则 → final_position_plan
└── 个股层 + 辩论 + 风控
    ├── Social → News → Fundamentals → Stock Tech（个股分析师序列）
    ├── Bull ↔ Bear Debate → Research Manager
    └── Trader → Risk → END
```

> 选股层/仓位管理层仅在**全市场模式**（`selectedLayer` 含 `"screening"`）启用；单票模式（默认）拓扑与行为不变。

### 市场层子图内部

市场层是独立编译的子图（`AI/marketAgents/market_layer_graph.py`），父图通过 `add_node("Market Layer", subgraph)` 将其作为一个节点使用。子图内部按地域维度组织，串行执行 7 个 Agent：

| 层级 | Agent | 输出字段 | 工具循环 |
|------|-------|----------|:---:|
| Layer 0 | 国际新闻分析 | `international_news_report` | ✅ |
| US | 美国新闻分析 | `us_news_report` | ✅ |
| US | 美国技术分析 | `us_tech_report` | — |
| KR | 韩国新闻分析 | `kr_news_report` | ✅ |
| KR | 韩国技术分析 | `kr_tech_report` | — |
| CN ★ | 中国新闻分析 | `cn_news_report` | ✅ |
| CN ★ | 中国技术分析 | `cn_tech_report` | — |

> 所有市场层 Agent 不依赖 `company_of_interest`，仅使用 `trade_date`。
> 新增国家：在 `market_layer_graph.py` 的 `COUNTRIES` 中加一行 + 写两个 Analyst 文件即可。

### 数据流

```
START
  → Market Layer (subgraph)     ← 一个节点，内部封装 7 个 Analyst
  → Sector Layer (subgraph)     ← 一个节点，内部封装 2 个 Analyst
  → [个股层: Stock Tech → Social → News → Fundamentals]
  → Bull ↔ Bear Debate → Research Manager → Trader → Risk → END
```

> 子图触发条件：`selected_analysts` 中包含 `"market"` 时运行（默认包含），不含时跳过。
> 子图与父图共享同一个 `AgentState`，子图写入市场层 7 个 report 字段，个股层直接读取。

---

## 三、State 字段汇总

### 市场层产出（7 个 report + 7 个 tool_call_count）

| 字段 | 写入者 |
|------|--------|
| `international_news_report` | 国际新闻分析师 |
| `us_news_report` | 美国新闻分析师 |
| `us_tech_report` | 美国技术分析师 |
| `kr_news_report` | 韩国新闻分析师 |
| `kr_tech_report` | 韩国技术分析师 |
| `cn_news_report` | 中国新闻分析师 |
| `cn_tech_report` | 中国技术分析师 |
| `*_tool_call_count`（7 个） | 对应 Analyst |

### 板块层产出（2 个 report + 2 个 tool_call_count）

| 字段 | 写入者 |
|------|--------|
| `sector_news_report` | 板块新闻分析师 |
| `sector_tech_report` | 板块技术分析师 |
| `sector_news_tool_call_count` | 板块新闻分析师 |
| `sector_tech_tool_call_count` | 板块技术分析师 |

> 板块层产出通过**结构化字段**（`sector_shortlist`、`sector_tech_confirm`）注入个股层决策节点（2026-08 更新）。

### 选股层/仓位管理层新增字段（2026-08 全市场模式）

| 字段 | 写入者 |
|------|--------|
| `risk_gate` | CN Tech 分析师（市场层，规则派生 `normal`/`caution`/`block`，fail-open） |
| `sector_shortlist_structured` | 板块层 News/Tech 分析师（经东财概念全名单过滤，仅全市场模式启用） |
| `candidate_stock_pool` | 选股层 Screening 节点（纯代码） |
| `stock_results` | propagate 层逐票循环（`code → {final_trade_decision, decision_json, ...}`） |
| `final_position_plan` | 仓位管理层 Position Manager（纯代码） |

### 个股层产出（4 个）

| 字段 | 写入者 | 说明 |
|------|--------|------|
| `stock_tech_report` | 个股技术分析师 | 原名 `market_report`，2026-08 重命名 |
| `news_report` | 新闻分析师 | |
| `sentiment_report` | 情绪分析师 | |
| `fundamentals_report` | 基本面分析师 | |

---

## 四、下游消费

### 市场层上下文注入

市场层 7 份报告通过 `build_market_layer_context(state)` 组装为统一上下文文本，注入到个股层的 9 个下游节点：

- 研究员：`bull_researcher`、`bear_researcher`
- 辩论：`aggresive_debator`、`conservative_debator`、`neutral_debator`
- 决策：`research_manager`
- 风控：`risk_manager`
- 交易：`trader`
- 反思：`reflection`

所有下游节点统一使用 `state.get("field_name", "")` 兜底写法，确保未跑市场层时不会 KeyError。

### 数据源覆盖

| 层级 | 实现 | 占位 | 说明 |
|------|------|------|------|
| 市场层 | 14 函数 | 5 函数 | US/KR 部分数据暂无免费源 |
| 板块层 | 5 函数 | 1 函数 | 行业数据/资金流向/技术筛选/alpha排名/概念热度；政策新闻占位 |

---

## 五、板块层（已实现 ✅）

板块层作为三层金字塔的第二层，位于市场层之后、个股层之前。详见 [板块层](板块层.md) 和 [施工方案](plans/板块层技术方案.md)。

已实现内容：
- **SectorLayerGraph** — 独立编译的 LangGraph 子图（`AI/sectorAgents/sector_layer_graph.py`）
- **板块新闻分析** — 全行业涨跌排名 + 资金流向 + 概念热度 + 轮动判断
- **板块技术分析** — 全行业技术状态矩阵（行业板块指数K线）+ AI/科技产业链专题深挖 + 风格因子验证
- **数据层** — 5 个新增实现函数 + 1 个占位 + 2 个已有工具复用（AI产业链 + 科技相关性）
- **图谱集成** — 父图通过 `add_node("Sector Layer", subgraph)` 集成，默认开启，顺序为 `Market → Sector → Stock Tech`
- **独立性** — 板块层产出不传入个股层决策节点，写入 State 后保持独立

---

## 六、选股层与仓位管理层（已实现 ✅，纯代码层）

四层金字塔的收口环节，均为**普通函数节点（纯 Python，无 LLM）**——"从数字里筛数字"和"按规则分配数字"是确定性运算，可回溯、可复现。仅在**全市场模式**（`selectedLayer` 含 `"screening"`）启用；单票模式行为与历史完全一致。

- **选股层**（`AI/screening/`）：消费 `sector_shortlist_structured`（统一**东财概念**体系：Tushare `dc_index`+`dc_member`、AKShare `stock_board_concept_*_em`）→ 成分股 → 近 N 日涨幅 → 跑赢板块均值 + 流动性过滤 → `candidate_stock_pool`（默认上限 10 只）
- **个股层循环**（`AI/graph/stock_loop.py`）：对候选池逐票 invoke 个股层子图（逐票态重置防污染），结果收进 `stock_results`
- **仓位管理层**（`AI/position/`）：置信度加权/等权/凯利三种分配策略 + 单票/板块/总仓位上限裁剪 → `final_position_plan`（JSON 落盘 `logs/{ts}/reports/`）
- **风险熔断**：市场层 `risk_gate` → `block` 只出风险提示计划、`caution` 目标仓位打 5 折
- 详见归档方案：[done/选股层与仓位管理层技术方案.md](done/选股层与仓位管理层技术方案.md)

---

## 七、事件研究系统（已实现 ✅，独立子系统）

金融事件影响分析系统（`AI/eventStudy/`）——采集宏观事件、人工审核、事件研究法标注影响、相似事件检索预测，为市场层国际新闻分析提供历史案例数据支撑。

**核心能力**：

- **存储**：PostgreSQL 16 + pgvector（docker-compose `postgres` 服务；6 表：assets / events / market_data / event_impacts / market_context / predictions）。草稿区约定：待审事件与影响结果先写 Redis 草稿，人工确认后落 PG（PG 只存正式数据）
- **数据采集**：爬虫抓取财经快讯（金十/财联社/新浪 7x24/东财，写 Redis 待审队列）+ Provider 层结构化接口（`get_index_data_df` / `get_trade_cal` / `get_macro_context`，AKShare/Tushare 同步覆写）采集指数日线与宏观指标
- **人工审核**：Streamlit 审核界面（事件类型/子类型/条件/重要性/预期实际值确认；影响结果按资产勾选落表）。**AI 预填**（2026-08-19 增强）：批处理采集后用 LLM（quick 模型）对草稿自动预分类（类型/子类型/条件/重要性/数值提取），审核界面作为表单默认值展示（标注"AI 预填，请确认"），人工确认或修改；LLM 不可用时人工照旧填写
- **影响标注（核心）**：事件研究法——市场模型 OLS 回归（120 日估计窗口 + 10 日间隔），4 个目标指数（上证指数/科创50/科创100/沪深300）× 3 窗口（pre_event_5d / event_day / post_event_5d）CAR + t 统计量 + 方向判定（|CAR|>0.5% 且 |t|>1.96）+ 污染检查；t0 对齐规则：盘前（09:30 前）→ 当日，否则 → 下一交易日
- **市场环境快照**：每日 `market_context`（20 日收益/年化波动/20 日均成交额/10 年国债收益率），事件环境按 T-1 规则匹配（禁止当日快照）
- **AI 预测**：模板匹配（event_type+subtype+condition 的历史平均 CAR/胜率/样本数，权重 1.0）+ 向量检索（bge-m3 1024 维，pgvector 余弦，相似度<0.5 不纳入，权重=相似度×0.5）加权融合；LangGraph 工具 / REST API（`POST /predict`）/ 离线回测共用同一套确定性规则；预测仅显式保存（save=True）或回测时落库
- **调度**：Windows 任务计划程序每日早间批处理（采集 → 行情 → 全市场日线增量 → 市场上下文 → 向量化 → 事件研究）
- 详见归档方案：[done/事件研究方案.md](done/事件研究方案.md)

---

## 八、全市场日线本地库（已实现 ✅，数据层子系统）

全市场 A 股 + 场内基金（ETF/LOF）近 10 年日线的本地 PostgreSQL 落地（`AI/dataflows/store/`），供全市场横截面（涨跌分布/选股）与本地回测消费；与事件研究同库（liveprofit public schema）不同表，现有 `market_data` 表及消费方**零改动**。

**核心能力**：

- **六张表**：`stock_basic` / `fund_basic`（基本信息，含退市股与基金费率/业绩基准）、`stock_daily` / `adj_factor`（日线与复权因子，股票基金**共用**，分类靠 `is_fund_ts_code()` 前缀函数判定，不冗余 asset_type）、`concept` / `concept_member`（概念体系多来源 ths=同花顺 / dc=东方财富；不存成分股名，展示一律 ts_code JOIN stock_basic 取权威名称）
- **数据采集**：`TushareProvider` 新增 6 个结构化方法（`get_full_market_daily_df` / `get_full_market_factor_df` / `get_stock_basic_df` / `get_fund_basic_df` / `get_concept_list_df` / `get_concept_members_df`，基类默认返回 None，规则 8）；全市场拉取**只允许 trade_date 单日查询**（区间查询 6000 行静默截断，实测），单日行数 ≥6000 自动降级分批补拉（每批 100 代码逗号分隔）
- **回填与增量**：`backfill.py` 历史回填（断点续跑以 PG 内 max(trade_date) 为天然断点；单日失败重试 3 次后跳过记 `logs/stock_backfill_failures.json`，`--retry-missing` 补拉；单日提交，库内无"半截日"）；`incremental.py` 每日增量（daily_job 步骤 3，最近 3 交易日 DO UPDATE 覆盖 tushare 日终修正；概念体系周一自动周刷，`refresh_concepts` 可手动触发）
- **查询 DAO**：单标的区间序列（`get_daily`）、全市场横截面（`get_cross_section`）、前复权序列（`get_qfq_daily`，qfq_x = x × factor_t / factor_latest）、概念双向查询（`get_stock_concepts` / `get_concept_members`）
- **调度**：并入事件研究每日批处理（采集 → 行情 → 全市场日线增量 → 市场上下文 → 向量化 → 事件研究），`--skip` 步骤名 `store`
- 详见归档方案：[done/全市场日线本地库方案.md](done/全市场日线本地库方案.md)
