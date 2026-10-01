# 事件审核 AI 作用域解析与表格标题修复方案

> **状态**：待确认（2026-09-16，评审 3 轮收敛：R3 PASS，全维度 ≥8）
> **关联文档**：[README.md](README.md)、../事件研究审核界面平台集成方案.md、../市场层证据驱动分析与三级事件路由改造方案.md

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 标题展示 | 审核表格标题列宽 `minmax(0,1.2fr)`（`frontend/src/modules/event-study/pages/review/PendingEventRow.tsx:11`）；昨日提交 08e9c34f 新增「作用域(8rem)+目标(12rem)」两列后，14 列固定轨道总和 93rem + 13×0.5rem 间隙 ≈ 1592px | 容器宽度 < 1592px 时 fr 轨道按 `minmax` 下界 0 塌缩为 **0px**，标题整列（含表头「标题」二字）不可见——「事件审核没有展示出标题」的直接原因 | 标题列恒有最小宽度（窄屏横向滚动），作用域/目标等编辑列照常可达 |
| AI 作用域判定 | 提示词（`AI/eventStudy/review/ai_prelabel.py:35-63`）规定「只填原文明确指向的代码，禁止编造代码」「无法确定时选 market」；Redis 328 条草稿实测（2026-09-16）：**145 条**旧建议缺 event_scope 字段（2026-09-11 扩产前生成，前端 `toPendingEventRowVM.ts:62-65` 回退显示 market）、**162 条** AI 判 market、仅 21 条 sector/stock | 快讯原文几乎不带代码 → LLM 大面积落 market；且 `prelabel_events` 幂等谓词「有 ai_suggestions 就跳过」（`ai_prelabel.py:209`）使 145 条旧草稿永远得不到回填 | AI 能按新闻内容判断出具体影响的板块/个股：名称经字典表解析成规范引用（SW:801080 / CONCEPT:BK1753.DC / stock:600519.SH），落 sector/stock 作用域；旧草稿自动回填，可强制全量重填 |
| 名称→代码解析 | 字典表已具备（market schema，与存在性校验 `RouteExistence` 同库）：`market.instrument` 5564 只 A 股（name→ts_code）、`market.industry` 31 条 SW2021 行业、`market.sector` dc 概念（实测覆盖「英伟达概念 BK1161.DC」「算力概念」「创新药」「光伏概念」等） | 无任何模块把新闻里的中文实体名称映射为引用代码——这是 AI 只能落 market 的根因之一 | 确定性解析步骤（非 LLM）：LLM 输出名称 → 查字典表 → 规范引用；解析失败的名称记录供人工处理 |

## 二、架构设计

本方案不改变现有架构（三级路由/预填/审核链路不变），只做两处增强：

1. AI 预填链路新增「名称解析」环节：

```
LLM 输出（原文出现的代码 or 中文名称——不凭记忆补代码）
   → _sanitize 分流：normalize_scope_ref 成功 = 代码；失败 = 候选名称
   → NameResolver.resolve_names(names, scope) 查字典表（instrument/industry/sector）＝名称→代码唯一映射源
   → 解析结果并入 refs → filter_existing_refs 存在性初筛 → 写回 ai_suggestions
   → 未解析名称 → ai_suggestions.unresolved_entities（仅展示）
```

2. 预填幂等谓词放宽（回填）：`无 ai_suggestions` → `无 ai_suggestions 或 缺 event_scope 键`，另加 force 强制全量重填通道（前端按草稿 id 分片驱动，循环必有界——评审 B1 修复）。

### 2.1 数据模型设计

仅一处数据形态变化：Redis 草稿的 `ai_suggestions` 新增可选键（**自由 dict，无 Schema/DTO 变更**——backend `PendingEventDTO.ai_suggestions: dict[str, Any]` 原样透传）。

| 字段 | 类型 | 写入者 | 说明 |
|------|------|--------|------|
| `ai_suggestions.unresolved_entities` | `list[str]`（可缺失） | ai_prelabel._sanitize | 未解析实体名称数组：LLM 输出的中文板块/公司名在字典表（instrument/industry/sector）查不到时的原始名称，供人工在详情弹窗核对后手工补目标引用或回退 market。示例值：`["英伟达", "某新概念"]`；为空时不写该键（与现建议结构兼容） |

其余结构（event_scope 三值、affected_scope_refs 规范形态）不变，见 ../市场层证据驱动分析与三级事件路由改造方案.md。

## 三、设计概览

### AI（AI/eventStudy/）

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| NameResolver【新增】 | 名称→规范引用解析器（字典表查询，确定性） | AI/eventStudy/review/name_resolver.py | 板块/公司中文名解析为 SW:/CONCEPT:/stock: 引用 |
| _SYSTEM_PROMPT【修改】 | 规则 7/8 重写：market 收窄为全市场性影响；refs 允许中文实体名称；代码仅原文出现时输出（名称→代码交系统查表）；美股事件→A 股概念映射 | AI/eventStudy/review/ai_prelabel.py | 预填出的 sector/stock 占比显著提升 |
| _sanitize / prelabel_one【修改】 | 名称分流 + NameResolver 接入 + unresolved_entities 落建议 | AI/eventStudy/review/ai_prelabel.py | 名称自动变引用；未解析名称可见 |
| needs_prelabel / prelabel_events【修改】 | 回填谓词（缺 event_scope 也要重填）+ force 覆写模式 + TTL 顺手修 | AI/eventStudy/review/ai_prelabel.py | 145 条旧草稿下次预填自动补齐；可强制全量重填且循环必有界 |
| review_app（Streamlit）【修改】 | 预填按钮旁加「重新预填全部」checkbox（50 条分片 + 进度提示） | AI/eventStudy/review/review_app.py | Streamlit 审核页同样可回填/强刷 |

### backend（backend/）

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| PrelabelRequest【修改】 | 加 `draft_ids: list[int] | None`（强制重填分片驱动；**不加 force 字段**——覆写语义完全由 draft_ids 表达，评审 N2） | backend/api/schemas/event_study_review.py | 契约含强制重填目标草稿集合 |
| prelabel 端点【修改】 | 透传 draft_ids | backend/api/routers/event_study_review.py | POST /prelabel 支持 draft_ids |
| EventStudyReviewService.prelabel【修改】 | 过滤谓词同步（缺 scope 计入）+ draft_ids 分流（draft_ids 模式内部以 force=True 调 AI 侧覆写） | backend/modules/event_study/application/review_service.py | 服务层回填语义与 AI 侧一致 |
| EventStudyReviewAdapter【修改】 | needs_prelabel 透传 + prelabel(drafts, force) | backend/modules/event_study/infrastructure/review_adapter.py | 防腐层签名扩展 |
| openapi + codegen【生成】 | `python -m backend.scripts.export_openapi` + `pnpm run generate:api` | backend/openapi/openapi.v1.json、frontend/src/api/generated/ | 前端拿到 PrelabelRequest.draft_ids 类型 |

### frontend（frontend/）

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| PENDING_ROW_GRID【修改】 | 标题列 `minmax(0,1.2fr)` → `minmax(14rem,1.2fr)` | frontend/src/modules/event-study/pages/review/PendingEventRow.tsx | 窄屏标题列恒可见（整表横向滚动） |
| IMPACT_ROW_GRID【修改】 | 事件标题 `minmax(0,1.5fr)` → `minmax(14rem,1.5fr)`；备注 `minmax(0,1fr)` → `minmax(6rem,1fr)` | frontend/src/modules/event-study/pages/review/ImpactConfirmTab.tsx | Tab2 影响确认标题列同类修复 |
| PendingEventsTab【修改】 | 「强制重填全部」按钮（50 条/片分片循环）+ 底部文案补充 | frontend/src/modules/event-study/pages/review/PendingEventsTab.tsx | 一键重跑 328 条预填 |
| EventDetailDialog【修改】 | unresolved_entities 警示行（AI 建议块上方） | frontend/src/modules/event-study/pages/review/EventDetailDialog.tsx | 未解析名称可见，人工可补目标 |

## 四、详细设计

### 4.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| 名称→代码解析器 | 「半导体」「贵州茅台」等名称无法变成引用：现 `_sanitize` 只调 `normalize_scope_refs` 丢弃非法项（`ai_prelabel.py:142-149`），LLM 不输出代码时 refs 恒空 → 落 market | 新增 NameResolver：按作用域查三张字典表（instrument/industry/sector），精确匹配 + 唯一包含兜底，行业优先 |
| 提示词改造 | 规则 8「只填原文明确指向的代码，禁止编造代码」使无代码原文必落 market（实测 162/328 条 market） | 规则 7/8 重写：market 收窄、允许中文名称、代码仅原文出现时输出（名称→代码交系统查表）、美股→A 股概念映射 |
| sanitize 名称解析接入 | _sanitize 无名称处理路径 | 非法项改为候选名称 → resolver 解析 → 未解析进 unresolved_entities |
| 回填与强制重填 | `prelabel_events` 谓词「有 ai_suggestions 跳过」使 145 条缺 scope 草稿永远 market 显示（`ai_prelabel.py:209`；前端 `toPendingEventRowVM.ts:64` 回退 market） | needs_prelabel 谓词（缺 event_scope 也要重填）+ force 覆写模式；强制重填由前端按草稿 id 分片驱动（`draft_ids`），循环次数 = 分片数必有界；AI/backend/API/Streamlit/前端按钮 |
| 标题列修复 | 14 列固定轨道总和 ≈1592px，标题列 `minmax(0,1.2fr)` 窄容器塌缩为 0px（`PendingEventRow.tsx:10-11`，08e9c34f 引入） | 标题列改 `minmax(14rem,1.2fr)`（Tab2 同类修复），窄屏横向滚动 |
| 详情弹窗展示 | unresolved_entities 无展示渠道 | EventDetailDialog 警示行 + 表格底部文案补充 |
| 测试 | 现测试断言「非法项丢弃」（`tests/event_study/test_ai_prelabel.py:128-129`）与新语义冲突 | 更新旧断言 + 新增 resolver/回填/force/前端按钮用例 |

### 4.1 名称→代码解析器（NameResolver）

#### 4.1.1 模块设计

职责：把 LLM 输出的中文实体名称（板块名/公司名）解析为规范引用代码。确定性查询，不调 LLM。

```python
@dataclass(frozen=True)
class NameResolver:
    conn: object

    def resolve_names(self, names: list[str], scope: str) -> tuple[list[str], list[str]]:
        """名称 → (解析出的规范引用列表, 未解析名称列表)。
        scope='stock' 只查个股表；scope='sector' 只查行业+概念表（先行业后概念）。"""
```

查询（market schema，liveprofit 库，与 `RouteExistence` 同库；全部批量，单批 ≤4 条查询——stock 最多 2 条（精确+包含）、sector 最多 4 条（industry/sector × 精确/包含），不随名称数增长）：

| scope | 查询 | 产出形态 |
|-------|------|---------|
| stock | `market.instrument`：`instrument_type='stock' AND name = ANY(names)` | ts_code 匹配 `^\d{6}\.(SH|SZ|BJ)$` → `stock:<code>` |
| sector | `market.industry`：`source='SW2021' AND name = ANY(names)` | industry_code → `SW:<code>` |
| sector | `market.sector`：`source='dc' AND name = ANY(names)` | sector_code 匹配 `^BK\d{4}\.DC$` → `CONCEPT:<code>` |

匹配规则（v1 确定性强、可解释）：

1. **精确匹配优先**：先跑 `name = ANY(names)`；
2. **唯一包含兜底**：仍未解析的名称（长度 ≥2），按 `name ILIKE %s`（**参数绑定 `'%词%'`**——psycopg3 下 SQL 文本直接写 `%词%` 会被当占位符报错；查询前对名称中的 LIKE 元字符 `%`/`_`/`\` 转义）查，**恰好命中 1 行**才采用（「茅台」→ 贵州茅台；「光伏」命中光伏概念/光伏设备/…多行 → 不采用）；
3. **同表多行命中**（精确匹配时同名字段多行，dc 概念确存在重名：跨境电商 BK1115.DC/BK1547.DC）：按代码升序取首行（确定性）；
4. **行业与概念都命中**：行业优先（申万是互斥完备分类体系，概念是多对多标签）；
5. 失败语义：查询异常 → **每个 except 分支先 `conn.rollback()`（自身再包 try）**——共享 PG conn（`AI/eventStudy/db/connection.py` autocommit=False，与 `RouteExistence` 同连接），不回滚会事务中毒，同一批内后续全部语句（含逐草稿的 `filter_existing_refs` 初筛）静默失效（先例：`AI/utils/event_prefetch_core.py:72`，见 docs/experience/pitfalls/workspace/评审循环踩坑.md）→ 回滚后对应名称全部未解析（fail-open，不抛，不阻塞预填）；resolver 为 None（PG 不可用）→ 调用侧不建 resolver，名称直接进 unresolved_entities。

数据事实（2026-09-16 实测）：instrument stock 目录 5564 只全部 A 股（无美股个股，见第五章决策 2）；industry 31 条 SW2021；sector dc 概念含「英伟达概念 BK1161.DC」「算力概念 BK1134.DC」「创新药 BK1106.DC」等。

#### 4.1.2 三方依赖能力评估

仅依赖 PG 字典表（既有连接，无新三方依赖）。`ILIKE` 包含匹配在 ≤5564 行表上无性能问题（批量一次）。

#### 4.1.3 风险与验证方式

- 概念表存在「2026中报首亏」等财报型板块名（BK1751.DC 实测）——LLM 输出此类名称会被解析成 CONCEPT 引用，属 LLM 判断问题，人工确认兜底（审核流程本就有）；
- 英文名（Apple）→ 表内是中文名（苹果）→ 解析失败进 unresolved；提示词已要求输出中文名；
- 验证：fake conn 单测覆盖全部匹配规则（见 4.7）。

#### 4.1.4 文件变更清单

- **新建文件**：
  - `AI/eventStudy/review/name_resolver.py`（NameResolver 类 + 查询常量）
  - `tests/event_study/test_name_resolver.py`（fake conn 单测）
- **修改文件**：
  - `AI/eventStudy/review/ai_prelabel.py`（_sanitize/prelabel_one 接入 resolver）

### 4.2 提示词改造（_SYSTEM_PROMPT）

#### 4.2.1 模块设计

`AI/eventStudy/review/ai_prelabel.py:35-63` 的 `_SYSTEM_PROMPT` 规则 7/8 重写（输出 JSON 格式不变；模块顶部 docstring 同步一句「美股公司事件映射 A 股概念板块」）：

新规则 7：

```
7. event_scope 从以下选：market / sector / stock——
   仅全市场性影响（宏观数据、央行政策、地缘冲突、交易制度、大盘行情）选 market；
   影响特定行业或概念板块（行业政策、题材催化、行业供需/涨价）选 sector；
   点名特定上市公司（公司公告、并购、业绩、订单）选 stock；
   美股等海外公司事件 → 选 sector 并输出 A 股对应概念板块名称（如「英伟达概念」），
   找不到对应概念时才保持 market
```

新规则 8：

```
8. affected_scope_refs：目标引用数组——market 固定 []；
   sector/stock 允许三种形态：规范代码（SW:801080 / CONCEPT:BK1753.DC /
   stock:600519.SH）、裸代码（801080 / BK1753.DC / 600519.SH）、
   中文实体名称（行业名如「电子」、概念名如「光刻胶」「英伟达概念」、
   公司全名如「贵州茅台」）；代码仅在原文明确出现时输出，原文只出现
   名称时只输出准确的中文名称，不要凭记忆补代码（名称→代码由系统
   查表完成，禁止编造名称与代码）；确实无法确定具体影响对象时
   event_scope=market、affected_scope_refs=[]
```

要点：

- 删除「无法确定时选 market」的兜底倾向（改为「无法确定具体影响对象时」）；
- **「拿代码」的职责从 LLM 移交系统（2026-09-16 用户确认）**：LLM 只做语义判断与名称提取，代码仅在原文明确出现时照抄；名称→代码的唯一映射源是 NameResolver 查字典表——LLM 凭记忆补代码即使碰巧存在，也可能张冠李戴（同名概念/记错代码），存在性初筛只是兜底防线，不作为主路径；
- 保留「禁止编造」（改为「禁止编造名称与代码」——既有测试断言字符串需同步，见 4.7）。

#### 4.2.2 三方依赖能力评估

不依赖外部库。LLM 输出质量依赖 quick 模型（deepseek-v4-flash）指令遵循，无新增三方能力需求。

#### 4.2.3 风险与验证方式

- LLM 可能输出「半导体板块」而非「半导体」——唯一包含兜底（4.1 规则 2）+ unresolved 人工兜底；
- LLM 可能违反规则凭记忆补代码——存在性初筛剔除幻觉引用兜底（主路径仍是名称查表，初筛仅作防线）；
- 验证：单测断言提示词关键短语（更新旧断言）+ 人工点「强制重填全部」观察 sector/stock 占比提升。

#### 4.2.4 文件变更清单

- **修改文件**：`AI/eventStudy/review/ai_prelabel.py`（_SYSTEM_PROMPT 规则 7/8 + 模块 docstring）

### 4.3 sanitize 名称解析接入

#### 4.3.1 模块设计

`_sanitize(suggestion, existence=None)` 扩展为 `_sanitize(suggestion, existence=None, resolver=None)`；`prelabel_one(draft, existence=None, resolver=None)` 同步扩展。

新流程（`ai_prelabel.py:142-149` 改造）：

1. 文本/数值字段清洗、scope 收敛不变；
2. `raw_items = _raw_ref_items(suggestion.get("affected_scope_refs"))` 分流：
   - `normalize_scope_ref(item)` 成功 → 代码引用；
   - 失败且非空白 → 候选名称。**边界（m5）**：单条长度 >64 → 直接丢弃（超长视为垃圾，不进 unresolved）；候选名称条数上限 `review_dao.MAX_SCOPE_REFS`（20），超出部分丢弃；
3. scope ∈ (sector, stock) 且 resolver 可用 → `resolver.resolve_names(names, scope)`，解析结果并入代码引用（代码引用在前、解析引用在后，保序）；
4. scope ∉ (sector, stock)（market/None）→ refs 强制 `[]`，名称不解析不记录（market 语义：无目标，与现 `ai_prelabel.py:146-147` 一致）；
5. 合并去重保序，**总条数截断至 MAX_SCOPE_REFS=20**（建议值；正式 approve 时 `resolve_scope_fields` 仍按 20 上限行级拦截）→ `filter_existing_refs`（LLM 给的代码仍要幻觉初筛；解析出的引用天然存在，初筛幂等无害）→ `clean["affected_scope_refs"]`；
6. 未解析名称 → `clean["unresolved_entities"]`（去重保序；为空不写键）。

**语义变化点**：旧 `_sanitize` 静默丢弃非法项（`JUNK`、`600519` 这类）；新流程它们成为候选名称 → 解析失败 → 进 `unresolved_entities`。这是预填建议的宽容路径（仅建议值、供人工确认），与服务端 approve 的「格式非法 = 行级拦截」严格校验（`resolve_scope_fields`）是两个层次，互不影响。

#### 4.3.2 三方依赖能力评估

NameResolver 依赖既有 PG 连接（`_open_existence` 返回的 conn 复用构造 resolver，`ai_prelabel.py:180-191` 扩展：`return RouteExistence(conn), NameResolver(conn), conn`）；PG 不可用 → resolver=None → 名称全进 unresolved（fail-open）。

#### 4.3.3 风险与验证方式

- 既有测试 `test_sanitize_scope_converges_and_cleans_refs`（断言 JUNK/600519 被丢弃）需按新语义更新（见 4.7，同类表述同步纪律）；
- 验证：fake resolver 单测 + 真库冒烟（人工点预填按钮）。

#### 4.3.4 文件变更清单

- **修改文件**：
  - `AI/eventStudy/review/ai_prelabel.py`（_sanitize/prelabel_one/_open_existence；新增 `from AI.eventStudy.review.review_dao import MAX_SCOPE_REFS, _raw_ref_items`——现未 import 这两个符号，评审 N3）

### 4.4 回填与强制重填

#### 4.4.1 模块设计

AI 侧新增纯函数（单一事实来源，backend 复用）：

```python
def needs_prelabel(draft: dict) -> bool:
    """非 force 预填判定：无 ai_suggestions，或建议缺 event_scope
    （2026-09-11 扩产前旧建议回填）。"""
    suggestions = draft.get("ai_suggestions")
    return not suggestions or "event_scope" not in suggestions
```

- `prelabel_events(drafts, force=False)`：跳过判定从 `draft.get("ai_suggestions")` 改为 `not needs_prelabel(draft)`（`ai_prelabel.py:207-210`）；`force=True` 时**不做谓词过滤**，对传入列表全部重生成建议（覆写）——是否覆写的决策在调用方（service/Streamlit 控制传入哪些草稿）；
- **force 收敛机制（评审 B1 修复，采纳评审选项 b）**：强制重填由**前端按草稿 id 分片驱动**——前端在请求时携带 `draft_ids`（当前待审列表的草稿 id 分片，每片 ≤50，与既有 limit=50 口径一致），服务端对指定草稿逐片覆写；循环次数 = 分片数（328 条 = 7 次请求），**必有界**，不依赖 remaining 收敛（评审 B1 指出的「force 谓词恒真 → remaining 恒 >0 → 死循环」路径不复存在）。每次点击「强制重填全部」都真实重跑全部草稿（语义诚实），LLM 不可用时某片 `prelabeled===0` → 提前终止并提示（复用现有护栏文案）；
- **顺手修 TTL 抹除（评审 P6）**：`prelabel_events` 写回 Redis 的 `r.set`（`ai_prelabel.py:215-216`）现无 `ex` 参数，预填后草稿 TTL 被抹成 -1（实测 328 条全部无过期，与「草稿 30 天过期」文案矛盾）；同变更补 `ex=PENDING_DRAFT_TTL`；
- backend `PrelabelRequest` 加 `draft_ids: list[int] | None = Field(default=None, max_length=200)`（分片驱动；**请求级不设 force 字段**——覆写语义完全由 draft_ids 表达，评审 N2：保留无行为的 force 字段会让实现者「顺手让它生效」重新引入非收敛路径）；router prelabel 透传 draft_ids；
- `EventStudyReviewService.prelabel(self, limit: int, draft_ids: list[int] | None = None)`（**默认值保持既有位置调用 `service.prelabel(50)` 兼容**，`test_review_service.py:198/207`）：
  - `draft_ids` 非 None：按 id 集合过滤当前草稿列表 → `adapter.prelabel(targets, force=True)` 覆写（草稿中途被提交删除的自动跳过）；
  - 否则：`unlabeled = [d for d in 列表 if self._adapter.needs_prelabel(d)][:limit]` → `adapter.prelabel(unlabeled, force=False)`；
  - remaining 维持「执行后仍命中 needs_prelabel 的草稿数」口径（非 draft_ids 路径的收敛依据；draft_ids 分片路径前端不看 remaining）；
- **既有调用方 `daily_job.py:45`（行为变化，评审 m4，确认为期望行为）**：`prelabel_events(get_pending_events())` 无签名变化；新谓词使每日批处理在部署后首次运行时自动回填 145 条缺 scope 草稿（此后仅新草稿），这是「自动回填」的落地主通道——一次性 LLM 成本 145 条，写入方案为预期行为；
- Streamlit `review_app.py:110-114`：预填按钮旁加 checkbox「重新预填全部（覆盖已有建议）」→ force 下**就地按 50 条分片循环**调用 `prelabel_events(chunk, force=True)` + `st.progress` 进度提示（评审 m8：不按片串行 328 次 LLM 会超 Streamlit 交互时限；Streamlit 直接持草稿列表，无需 draft_ids 契约）；回填无需 UI 改动自动生效；
- 前端 `PendingEventsTab`：「🤖 强制重填全部」按钮（outline/sm，置于 AI 预填按钮旁）→ `runPrelabel(force=true)`：取 `query.data.items` 的 draft_id 列表按 50 条/片分片，逐片 `prelabelMutation.mutateAsync({ limit: 50, draft_ids: 片 })`（请求级无 force 字段，覆写语义由 draft_ids 表达），进度文案「已重填 x/y 条…」，片内 `prelabeled===0` 提前终止（`PendingEventsTab.tsx:104-129` 的 `runPrelabel` 改造）；非 force 按钮走既有 remaining 循环不变。

**openapi 链条顺序（强制，防并发 typecheck 失败）**：改 backend schema → `python -m backend.scripts.export_openapi` → `pnpm run generate:api` → 前端消费。注意：工作区 `backend/openapi/openapi.v1.json` 当前已有用户并发修改（M 状态），重导出产物会同时包含其路由变更——属生成物正常行为（R1 已实测该 M 文件与当前代码导出结果一致、门禁 2 passed），本任务不做任何 git 操作。

**覆盖安全性**：`ai_suggestions` 仅存 AI 建议；人工编辑在表单本地（前端 `editedDraftIds` 机制在 refetch 时保留本地编辑，`PendingEventsTab.tsx:48-63`）——强制重填不覆盖人工已填的表单值；已提交行草稿已从 Redis 删除，无影响。

#### 4.4.2 三方依赖能力评估

无新依赖。LLM 成本（全部 quick 模型 deepseek-v4-flash，单条输出上限 500 tokens）：强制重填 328 条由人工按钮触发；daily_job 首次运行自动回填 145 条（一次性）；此后每日仅新草稿。LLM 不可用时预填返回 0 不崩溃（既有 fail-open），旧草稿留待下次预填重试。

#### 4.4.3 风险与验证方式

- 循环必有界（B1 修复口径）：force 路径循环次数 = 前端分片数（draft_id 列表在点击时快照，逐片提交），不依赖服务端 remaining；片内 LLM 不可用时 `prelabeled===0` → 提前终止并提示（复用 `PendingEventsTab.tsx:116-119` 护栏文案）。非 force 路径保持 remaining 收敛（缺 scope 谓词，145→95→45→0，每轮重填后不再命中谓词）；
- 分片期间草稿被提交/删除：服务端按 id 过滤自动跳过，prelabeled 少于片大小属正常，进度文案按累计处理数显示；
- 验证：backend 单测（谓词两态 + draft_ids 分流 + 覆写行为）+ 前端按钮用例（draft_ids 分片入参、prelabeled===0 提前终止）。

#### 4.4.4 文件变更清单

- **修改文件**：
  - `AI/eventStudy/review/ai_prelabel.py`（needs_prelabel、prelabel_events(force)、写回补 `ex=PENDING_DRAFT_TTL`；docstring 随迁：模块头「只预填尚无 ai_suggestions 的草稿（幂等）」→ 新谓词、`_open_existence`「不可用返回 (None, None)」→ 三元组、`prelabel_events` 方法 docstring）
  - `AI/eventStudy/review/review_app.py`（force checkbox + 50 条分片循环 + st.progress）
  - `backend/api/schemas/event_study_review.py`（PrelabelRequest.draft_ids）
  - `backend/api/routers/event_study_review.py`（透传 draft_ids）
  - `backend/modules/event_study/application/review_service.py`（prelabel(limit, draft_ids=None) 谓词+分流；方法 docstring「对尚无 ai_suggestions 的草稿切片做 AI 预填（幂等…）」与 `:92` 行内注释「remaining = 执行后重新列草稿、仍无建议的数量」随迁新谓词口径，评审 NP1）
  - `backend/modules/event_study/application/review_contracts.py`（PrelabelResult.remaining 注释随迁：「仍无建议数」→ 新谓词口径）
  - `backend/modules/event_study/infrastructure/review_adapter.py`（needs_prelabel、prelabel(drafts, force)）
  - `frontend/src/modules/event-study/pages/review/PendingEventsTab.tsx`（强制重填按钮 + 分片循环）
  - `docs/knowledge/backend/API契约.md`（`:597` 附近「幂等：仅预填无 ai_suggestions 的草稿」口径随迁——knowledge 整合步骤）
- **生成文件**：`backend/openapi/openapi.v1.json`、`frontend/src/api/generated/**`（export_openapi + generate:api 产物，随任务提交）
- **行为变化（无代码改动）**：`AI/eventStudy/scheduler/daily_job.py:45` 的既有调用在新谓词下自动回填缺 scope 草稿（确认期望行为，见 4.4.1）

### 4.5 审核表格标题列修复

#### 4.5.1 模块设计

- `PendingEventRow.tsx:10-11`：`minmax(0,1.2fr)` → `minmax(14rem,1.2fr)`（标题列最小 14rem ≈ 224px ≈ 14 个中文字符，`truncate` + title 悬停补全文）；
- `ImpactConfirmTab.tsx:15-16`：事件标题 `minmax(0,1.5fr)` → `minmax(14rem,1.5fr)`；备注 `minmax(0,1fr)` → `minmax(6rem,1fr)`（同类塌缩问题）。

原理：fr 轨道在固定轨道总和超出容器宽度时按 min 收缩，`minmax(0, …)` 的下界 0 允许塌缩为 0px（整列含表头不可见）；固定正下界后容器 `overflow-x-auto` 承担横向滚动，标题列恒可见。

#### 4.5.2 三方依赖能力评估

纯 Tailwind 任意值类名，无依赖。

#### 4.5.3 风险与验证方式

- 14rem 下界后表格总宽 ≈1592px+224px，窄屏需横向滚动——与修复前一致（此前是标题列 0 宽、其余列同样超宽滚动）；
- 验证：人工检查——1920 以下窗口打开审核页（待审核事件 + 影响结果确认两个 Tab），标题列可见、横向滚动可达作用域/目标列。

#### 4.5.4 文件变更清单

- **修改文件**：
  - `frontend/src/modules/event-study/pages/review/PendingEventRow.tsx`
  - `frontend/src/modules/event-study/pages/review/ImpactConfirmTab.tsx`

### 4.6 详情弹窗与文案

#### 4.6.1 模块设计

- `EventDetailDialog.tsx`：「AI 预填建议」pre 块上方，`vm.aiSuggestions?.unresolved_entities` 经 **`Array.isArray` 运行时守卫**（评审 P4：`aiSuggestions: Record<string, unknown>`，元素 string 过滤）为非空数组时渲染一行警示：「⚠ 未解析目标：英伟达、某概念——请人工补目标引用（SW:801080 / CONCEPT:BK1753.DC / stock:600519.SH）或回退 market」（普通文本，非 markdown 内容）；
- `PendingEventsTab.tsx:292-295` 底部提示补一句：「板块/个股名称已自动解析为引用；未解析名称见详情弹窗」；
- Streamlit 侧对称展示（评审 m7）：`review_app.py`「查看事件原文」expander 内，`ai_suggestions.unresolved_entities` 非空时 `st.warning` 一行提示未解析名称（现 `review_app.py:121` 的 `normalize_scope_refs` 会静默丢弃未识别项，「目标」列看不到该信息）。

#### 4.6.2 三方依赖能力评估

不适用。

#### 4.6.3 风险与验证方式

前端单测（详情弹窗警示渲染，unresolved_entities 有/无两态）。

#### 4.6.4 文件变更清单

- **修改文件**：
  - `frontend/src/modules/event-study/pages/review/EventDetailDialog.tsx`
  - `frontend/src/modules/event-study/pages/review/PendingEventsTab.tsx`（文案）
  - `AI/eventStudy/review/review_app.py`（unresolved_entities `st.warning` 展示，与 4.4 的 force checkbox 改动同文件）

### 4.7 测试

#### 4.7.1 模块设计

| 文件 | 改动 |
|------|------|
| `tests/event_study/test_name_resolver.py`【新建】 | FakeConn（execute 按 SQL 前缀/表名返回预置行，含 rollback 记录）。用例：① stock/sector 精确匹配产出规范引用；② 唯一包含匹配（「茅台」→ 贵州茅台）与多行命中不采用（「光伏」）；③ 同表多行命中按代码升序取首行（确定性）；④ 行业与概念都命中 → 行业优先；⑤ 作用域约束（stock 不查行业/概念、sector 不查个股）；⑥ 查询异常 fail-open（全未解析）**且断言 FakeConn.rollback 被调用**（评审 M1：共享 conn 事务防毒）；⑦ 空名称列表/纯代码项不进解析；⑧ LIKE 元字符（`%`/`_`）转义后查询 |
| `tests/event_study/test_ai_prelabel.py`【修改】 | ① `test_system_prompt_declares_route_fields` 更新断言（「中文实体名称」「禁止编造名称与代码」「不要凭记忆补代码」）；② `test_sanitize_scope_converges_and_cleans_refs` 更新断言（非法项进 unresolved_entities 而非静默丢弃）；③ 新增 sanitize 名称解析成功路径（FakeResolver 注入）；④ 新增 resolver=None fail-open（全进 unresolved）；⑤ 新增 `needs_prelabel` 谓词两态用例（无建议/缺 scope → True；完整建议 → False）；⑥ `prelabel_events` 回填用例（缺 scope 草稿被重填）+ force=True 覆写用例（有建议的草稿也被重生成）；⑦ **随迁（评审 M2 全量枚举 + R2 N1-a）**：`_open_existence` 返回形状 2→3 元组的 3 处 stub——`test_open_existence_unavailable`（`:223` 断言 `(None, None)` → `(None, None, None)`）、`test_prelabel_events_writes_and_skips_existing`（`:239-240` monkeypatch 返回值补第三个元素）、`test_prelabel_events_llm_unavailable_returns_zero`（`:262` `(None, None)` → `(None, None, None)`）；`prelabel_one` monkeypatch（`:241-243` 的 `lambda draft, existence=None` 补 `resolver=None` 参数）；**同用例 draft 2 的 fixture 补 `"event_scope": "market"`**（现 `:247` 是 `{"importance": 3}` 缺 event_scope，新谓词下不再跳过 → `:249` 返回 2、`:251` store 两键，必挂；补键保留「已有建议跳过」用例语义）；⑧ 写回 Redis 带 `ex=PENDING_DRAFT_TTL` 断言（FakeRedis.set 扩展 `ex` kwarg 记录，评审 P6） |
| `backend/tests/unit/event_study/test_review_service.py`【修改】 | prelabel 谓词变化（缺 scope 草稿计入 unlabeled、remaining 同口径）；**随迁**：`FakeAdapter.prelabel(self, drafts)`（`:107`）加 `force` 参数 + 新增 `needs_prelabel` 桩；新增用例：① draft_ids 模式（指定 id 覆写、草稿缺失跳过、覆写开关经 adapter 透传）② 非 draft_ids 模式切片机制（`[:limit]`）不变，谓词口径按 `needs_prelabel`（评审 N1-c 措辞）；**fixture 同步（评审 N1-b）**：`:188-200` 用例的 `labeled` 草稿（`_draft()` 默认 `:158` 无 event_scope）在新谓词下会进 unlabeled 切片顶掉 `unlabeled_1`（`:199` 断言 `[[unlabeled_1]]` 必挂、`:200` remaining 1→2 必挂）→ 该用例局部传 `_draft(draft_id=1, ai_suggestions={**原两键, "event_scope": "market"})`，**不改 `_draft()` 默认值**（`:174` 等用例断言默认值原文）；既有位置调用 `service.prelabel(50)`（`:207`）与关键字调用（`:198`）靠默认值保持签名不破 |
| `frontend/src/modules/event-study/pages/review/PendingEventsTab.test.tsx`【修改】 | 「强制重填全部」按钮 → 按 50 条/片分片逐片调 prelabel，每片入参含 `draft_ids`（请求级无 force 字段）；片内 prelabeled=0 → 提前终止并显示提示；非 force 按钮循环回归不破坏 |
| `frontend/src/modules/event-study/pages/review/EventDetailDialog.test.tsx`【新建】 | 现无此文件（评审 P5）；unresolved_entities 警示渲染两态（有/无、非数组守卫） |

自测约束：`tests/event_study/` 目录内无用例使用 `real_llm`/`real_toolkit` fixture（grep 零命中；fixture 定义于 `tests/conftest.py`，对该目录可用），可直接 `pytest`；frontend 用 `pnpm exec vitest run`。

#### 4.7.2 三方依赖能力评估

不适用（全部 fake/monkeypatch，无真实 LLM、无真实 PG 依赖）。

#### 4.7.3 风险与验证方式

验证命令（python 命令在仓库根执行；pnpm 命令在 `frontend/` 目录执行）：
- `.venv/Scripts/python.exe -m pytest tests/event_study/test_name_resolver.py tests/event_study/test_ai_prelabel.py -q`
- `.venv/Scripts/python.exe -m pytest backend/tests/unit/event_study/test_review_service.py -q`
- `.venv/Scripts/python.exe -m pytest backend/tests/contract/test_openapi_gate.py -q`（codegen 前门禁：PrelabelRequest.draft_ids 进 openapi 后必跑，不重导出该门禁必挂）
- `.venv/Scripts/python.exe -m pytest backend/tests/contract/api/test_event_study_review.py -k prelabel -q`（既有用例用 `ai_suggestions=None` 草稿断言 remaining=2，新谓词下仍成立，回归确认；`:276` 注释「无 ai_suggestions 的草稿才进入预填切片」随迁新谓词口径，评审 NP2）
- `pnpm exec vitest run src/modules/event-study/pages/review`（frontend/ 下）
- `pnpm run typecheck`（codegen 后，frontend/ 下）

#### 4.7.4 文件变更清单

- **新建文件**：`tests/event_study/test_name_resolver.py`、`frontend/src/modules/event-study/pages/review/EventDetailDialog.test.tsx`
- **修改文件**：`tests/event_study/test_ai_prelabel.py`、`backend/tests/unit/event_study/test_review_service.py`、`frontend/src/modules/event-study/pages/review/PendingEventsTab.test.tsx`

## 五、已确认决策 / 待确认问题

已确认决策（2026-09-16 用户拍板）：

1. **旧草稿回填范围**：自动回填（缺 event_scope 的 145 条在预填时重生成）+「强制重填全部」按钮（328 条全量重跑）。实现：`needs_prelabel` 谓词 + force 覆写模式（前端按草稿 id 分片驱动，循环必有界）。
2. **美股公司事件归类**：本任务先做 A 股映射——提示词引导美股公司事件 → A 股对应概念板块（英伟达 → 「英伟达概念」CONCEPT:BK1161.DC 落 sector），找不到对应概念时保持 market；**美股个股引用本任务不做**（已核实：instrument 目录美股仅 3 个指数零个股、stock_info 仅沪深北、影响计算底座全 A 股），作为后续独立任务（依赖美股数据接入）。
