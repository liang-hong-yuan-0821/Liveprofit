# 前端体验优化技术方案

> **状态**：待确认（2026-09-22，R2核验PASS）；权威进度见 [README.md](README.md)。
> **关联文档**：[项目约定](../../../../agent.md)｜[方案模板](../../templates/plan.md.模板.md)｜[前端平台](../../../knowledge/frontend/前端平台.md)｜[API 契约](../../../knowledge/backend/API契约.md)｜[量化策略与实操层](../量化策略与实操层/plan.md)

## 一、背景与动机

2026-09-22 使用 Computer Use 检查了大盘、自选、组合设置、AI 看板、新建分析、任务中心、成功任务报告、Agent 拓扑、事件预测和审核页。观察环境为约 1280×720 桌面深色界面；没有修改代码或提交分析、审核、持仓表单。进入审核页时应用自行拉取并显示新增 47 条事件及 AI 预填提示，这属于已核实的现有自动行为。

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 页面布局 | `AppShell.tsx` 的 aside 使用普通文档流；`SidebarNav.tsx` 对 `/ai` 设置 `end` | 大盘向下滚动后左边只剩空侧栏；进入 `/ai/tasks` 后 AI 导航失去选中态 | 长页面始终能导航，二级页面归属明确 |
| 行情 | `MarketIndicesPanel.tsx` 顺序 US→KR→CN，11 张详细 K 线，每张传 `height={460}` | 首屏两张图挤满；韩国单张图右侧留空；没有突出的最新点位和涨跌摘要 | 首屏快速了解市场，并可继续使用完整 K 线工具 |
| 板块 | `HotConceptsPanel.tsx` 默认当天；`conceptTreeOption.ts` 将 null 绘成灰色，`splitUpDown` 将 null 放入 down 组 | 9 月 22 日图中大量灰块、涨跌幅“—”；用户无法分辨无行情与下跌，标题大量截断 | 明确无数据含义，保留已确认的图表口径与点击行为 |
| AI 结果 | 看板 `RecentConclusionsPanel.tsx` 固定绿色“成功”；`PendingActionsPanel.tsx` 显示不可用区块 | 同一任务既“成功”又“报告区块不可用”；示例为 9 月 21 日 16:28 的仓位任务 | 分开解释执行状态、报告区块状态和量化结果 |
| 阅读顺序 | `AiTaskDetailPage.tsx` 将状态、拓扑、日志放在报告前 | 已完成任务首屏出现两块空日志，“尚未开始写入”与成功状态矛盾 | 成功任务先读结果，运行中任务先看进度 |
| 拓扑 | `buildTopologyChartOption` 固定间距、11px 不换行标签，图宽 100% | `International Event…` 等英文标签交叠，点击目标不清 | 节点与文字可辨，图和列表均可进入节点操作 |
| 自选／账户 | `PositionList.tsx` 的统一 upsert 按钮为“新增/修改”；设置弹窗直接展示比例小数 | 不知道是否覆盖已有持仓；`0.01` 需要换算为 1%；选择提示写“左侧”但对象在上方 | 清晰区分新增与修改，降低数值填写错误 |
| 事件审核 | `PENDING_ROW_GRID` 为 14 列；`PendingEventsTab` 挂载自动 refresh 并串联 prelabel | 桌面宽度也看不全操作列；原文含 `<b>`；AI 建议默认以 JSON 展示 | 核对原文和动作无需往返横向滚动，后台工作可见 |
| 预测输入 | `PredictionForm.tsx` 用原生 datalist，类型／子类型等自由输入 | 输入“平安”未看到候选或无匹配提示；可选字段无填写例子 | 名称／代码检索有反馈，自由代码输入仍可用 |

### 范围与边界

- 本方案调整信息组织、样式、可访问操作和必要的只读投影。保留 React、TanStack Query、Zustand、Tailwind、Radix Dialog、ECharts 技术栈。
- 不调整股票筛选、下单、风控、行情采集和 AI 输出算法；不将“没有建议买入”改成错误，不把数据缺失伪装为持平。
- 不回填历史报告、不改变任务终态、不移除量化订单／成交生命周期能力。当前另有量化任务并发修改同名文件，实施前逐文件重新核对，按最终工作区合并改动。
- 浅色、窄屏、键盘完整流程目前未实测；本方案将它们列为实施验收目标，不声称已经发现手机适配缺陷。量化策略编辑页面不是本次浏览器审查范围。
- 第一阶段保留已确认的 US→KR→CN 顺序、双层涨跌分图、面积与排序口径、点击节点开 K 线、默认分析层级全不选。改变自动采集触发方式列入第五章，不能作为已获用户批准的变更。

## 二、架构设计

本方案不改变现有架构；新增少量展示模型与共用 UI，后端只补充既有任务／报告的只读字段，不新增表、迁移、队列或外部依赖。

```text
既有 task.selected_layers / report.report_json / 行情 DTO / 事件 DTO
  ├─ 后端应用层投影 → API DTO → OpenAPI → 生成客户端
  └─ 前端 Query（原 key 和失效关系）
       → 模块内 mapper（标签、状态、比例、文本）
       → 共享展示组件（提示、页签、表单、可访问列表）
       → 页面区块 → AppShell 与路由

用户显式编辑 → 本地草稿 → 原 mutation → 原 revision/幂等校验
                            → 成功后精准 invalidate；失败保留草稿
```

### 2.1 数据模型设计

下列新增字段为设计目标，示例中已有字段值来源于现有界面；组合示例仅用于验收构造，不代表已上线 API。

| 字段／对象 | 类型 | 写入者 | 业务含义、取值与消费方 |
|------------|------|--------|----------------------|
| `TaskListItemDTO.selected_layers`【新增】 | `list[str]` / `string[]`，schema 默认空列表 | `_to_list_item_dto(task)` 读取 `task.selected_layers`，路由显式传递 | 列表识别任务内容；如 `["position"]` 显示“仓位分析”；合法已知层 market/sector/stock/screening/position，未知值仅展示，不参与推断 |
| `RecentConclusionDTO.unavailable_blocks`【新增】 | `list[UnavailableBlockDTO]`，schema 默认空列表 | `get_dashboard()` 对当前 recent pair 的 report 调用既有 `_unavailable_blocks` | 与 pending 区块使用同一提取器；例 `[{block:"decision",reason:"该区块无可展示内容",retryable:true}]`，描述明确不可用的区块，不等同全部结果不可用 |
| `ResultPresentation`【前端新增】 | `{executionLabel:string, unavailableBlocks:..., reportKnown:boolean}` | `analysis/shared/resultPresentation.ts` | `SUCCEEDED` 文案统一“执行完成”；明确有 unavailable blocks 才显示“部分报告区块不可用”；字段缺失不能推断“完整” |
| `IndexQuoteVM`【前端新增】 | `{lastClose:number|null, change:number|null, changePct:number|null, asOf:string|null}` | 行情 mapper 读取 `BarsData.bars` | 如前收 100、末收 102，则变化 +2、+2%；单根或前收≤0时变化为空。此为“最近两条日线变化”，非实时涨跌 |
| `PercentField`【前端新增】 | 草稿 `string`，提交值 `number` | 输入组件＋表单适配 | 接口 0.01→界面 1（尾缀 %）；用户填 2.5→接口 0.025。空串是缺失，不变成 0 |
| 审核草稿【沿用】 | `Record<draft_id, PendingEventRowVM>` + edited ID set | `PendingEventsTab.patchRow` | 筛选／展开不新建第二份草稿；空数值提交 null，0 提交 0；沿用作用域约束。审核请求没有 revision/version，不宣称服务端乐观锁 |
| 账户／持仓编辑基线【前端新增】 | `{original:DTO, baseVersion:number}`／`{original:DTO|null, baseRevision:number}` | 打开账户设置、进入持仓编辑、开始新增草稿时捕获 | 原值、未编辑字段和提交版本绑定同一次快照；后台刷新不自动更新该基线 |

### 2.2 接口与兼容

- 仅 `GET /api/v1/analysis-tasks` 列表项新增 `selected_layers`、`GET /api/v1/analysis-dashboard` 最近结论新增 `unavailable_blocks`；请求参数、排序、游标与错误码均不变。
- 全链路同步：application dataclass → `task_lifecycle.py` 构造 → API schema → route mapper → OpenAPI → generated → fixtures → 页面。数据已在当前 ORM 记录中，不额外发每行详情请求。
- 新字段的 API schema 默认 `[]`，生产构造器必须显式填充；前端 `?? []` 只支持升级过渡，空值不标记“报告完整”。旧前端忽略增量字段；不创建 `_v2` 或运行时回退开关。
- 不从量化 JSON 推断传统 `decision` 区块可用；两者并列解释。根因修复若涉及报告生产／历史记录迁移，转入量化方案，不在此以 UI 隐藏真实缺失。
- 最近结论保留 `conclusion_summary` 唯一来源；为空时显示“暂无摘要，可查看报告”，不得截取全文伪造服务端结论。策略／组合名称尚未进入任务列表 DTO，本期不承诺在列表补齐这两项。
- 比例仅改变 UI 单位，后端继续接收 0–1 的比例；金额、成本、行情原始单位不变，跨市场价格标注原币种，不将 USD／KRW 当元。

## 三、设计概览

### backend

#### 服务层

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 任务列表／看板投影【修改】 | 复制已有层级；recent复用提取器，并补齐提取器结构／类型守卫 | `backend/modules/analysis/application/contracts.py`、`task_lifecycle.py` | 列表可识别任务，最近结果能说明局部缺失（4.3） |

#### DTO 与契约

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 任务与看板 DTO【修改】 | 两个增量字段，导出 schema | `backend/api/schemas/tasks.py`、`dashboard.py`、`backend/openapi/openapi.v1.json` | 前端不需要拼接多次详情查询（4.3） |

#### API 路由

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 列表／看板 mapper【修改】 | 显式传递新增字段 | `backend/api/routers/analysis_tasks.py`、`analysis_dashboard.py` | 两个 GET 响应携带展示信息（4.3） |

### frontend

#### 共享组件

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 视觉基础、页签、错误提示【修改／新增】 | 深浅token、Markdown随主题反色、文字层级、技术详情折叠 | `frontend/src/styles.css`、`frontend/src/shared/ui/`、`frontend/src/shared/feedback/` | 正文可读，动作与页签区分（4.1） |
| 图表主题【新增／修改】 | 图表中性文字与网格随themeMode适配 | `frontend/src/shared/charts/useChartTheme.ts`、`CandlestickChart.tsx`、趋势与拓扑消费点 | 浅色中性线可辨，切换不重置交互（4.1、4.4） |
| 图表展示【修改】 | 拓扑尺寸、标签、图外等价操作列表 | `modules/analysis/components/topologyChartOption.ts` | 解决节点重叠（4.4） |
| 百分比输入与资产选择【新增】 | 字符串草稿、单位转换、候选和空反馈 | `shared/ui/PercentField.tsx`、`AssetCombobox.tsx` | 减少输入猜测（4.5、4.7） |

#### API 接入

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 生成客户端【重新生成】 | 跟随两项 DTO 变更 | `frontend/src/api/generated/` | 类型与服务端一致（4.3） |
| 查询启用条件【修改】 | 行情详细图／终态日志按展开挂载；审核 mutation 入口职责显式化 | market `pages/queries.ts`、analysis `pages/task-detail/queries.ts`、event-study `pages/review/queries.ts` | 浏览不重复请求，表单草稿不被刷新覆盖（4.2、4.3、4.6） |

#### 页面区块

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 行情与板块【修改】 | 摘要、折叠详细图、缺数据说明 | `modules/market/pages/`、`components/ConceptTreemap.tsx` | 减少长滚动和灰图误读（4.2） |
| AI 看板与报告【修改】 | 两种状态分别呈现、成功任务结果优先 | `modules/analysis/pages/dashboard/`、`task-detail/`、`tasks/` | 结果更易找到（4.3） |
| 自选与分析表单【修改】 | 新增／编辑、账户参数分组、层级解释 | `modules/watchlist/`、`AnalysisTaskForm.tsx`、`QuantParamsPicker.tsx` | 明确提交对象和含义（4.5） |
| 审核与预测【修改】 | 精简主列表、详情编辑、可见进度、文本清理 | `modules/event-study/pages/` | 审核上下文完整、预测有输入反馈（4.6、4.7） |

#### 页面集成

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| AppShell／导航【修改】 | sticky 导航、路由归属、窄屏折叠入口 | `frontend/src/app/AppShell.tsx`、`app/components/SidebarNav.tsx` | 长页和二级页面可定位（4.1） |
| 页面头部与区块锚点【修改】 | 统一标题与操作，保留路由及 `#report` | `MarketOverviewPage.tsx`、`AiDashboardPage.tsx`、`AiTaskDetailPage.tsx`、`EventStudyPage.tsx` | 切换和返回行为一致（4.1、4.2、4.3） |

## 四、详细设计

### 4.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|----------|
| 全局布局与视觉层级 | `AppShell` 侧栏随长页面消失；任务详情主操作为红色删除 | 固定可见导航、明确页面层级、统一主次动作与错误提示 |
| 行情摘要与板块状态 | 11 张详细图拉长页面；null 涨跌幅放在下跌图内 | 紧凑行情摘要、按需详细图、明确缺失数据，不改变原口径 |
| AI 结果状态与阅读顺序 | 同任务显示成功／不可用；两块空日志挡在报告前 | 增量 DTO、统一展示映射、完成后报告优先 |
| 拓扑可读性与操作入口 | `fontSize:11` 长标签不换行，宽图缩进当前容器 | 明确画布尺寸、限制标签宽度、节点列表等价操作 |
| 自选与分析表单 | “新增/修改”与 0.01 比例输入使操作含义不清 | 分离编辑状态、百分比适配、基础／高级字段分组 |
| 事件审核工作台 | 14 列超宽、HTML 标签与 JSON 原样展示、自动任务反馈隐蔽 | 主列表＋同源详情草稿、纯文本清理、后台进度可见 |
| 事件预测输入 | 原生 datalist 输入中文无明确匹配反馈，可选字段无例子 | 可访问资产检索、可选项渐进展开、保留原预测契约 |

### 4.1 全局布局与视觉层级

#### 4.1.1 模块设计

1. 桌面侧栏外层保留占位，内部 `sticky top-0 h-dvh`，导航独立溢出；主页面继续使用文档滚动，保留浏览器锚点。去掉 `/ai` 的精确匹配限制，确保 `/ai/tasks`、`/ai/strategies` 同属 AI。
2. ≥1024px 保留可折叠侧栏；768–1023px 默认紧凑导航但不改写用户保存偏好；<768px 顶部菜单用现有 Dialog 展开，焦点返回菜单按钮。响应式状态与持久偏好分开。
3. 样式目标：页面标题 22–24px，区块标题 16px，常规正文 14px，次级时间／来源 12px；价格／摘要数值 20–24px。控件桌面高度≥36px，窄屏触摸主要控件≥44px；比例和金额列表对齐，用等宽数字样式。
4. 当前 `styles.css` 只有深色 `:root` token，`AppProviders` 只切换 `.dark`，所以本方案显式补齐浅色：保留 `:root` 深色默认，在 `.light` 设置浅色背景／surface／文字／边框与 `color-scheme:light`；`AppProviders` 随既有 themeMode 互斥设置 `.dark`／`.light`，不新增偏好存储。浅色建议基线 bg=#f8fafc、surface=#ffffff、fg=#172033、muted=#475569、border=#cbd5e1、accent=#2563eb，最终以对比度检查为准。新增语义 token 只表示 selected／warning／danger，不把技术异常都涂成业务风险。正文文字对比度验收目标≥4.5:1，边框／焦点可见。`MarkdownView` 的固定 `prose-invert` 改为 `dark:prose-invert`，不另建渲染引擎。
5. 共用 `useChartTheme` 读取当前 themeMode 对应的图表文字／网格／中性线颜色；K线、趋势和拓扑只将这些视觉颜色放入option依赖，不改变数据、系列名、日期锚或事件处理。红涨绿跌和指标系列的业务识别配色保持；浅色下浅灰／白色中性线使用可辨深色值。主题切换不能重置用户缩放或绘图。
6. 页签使用带选中标识的 tablist 或导航链接，不与“提交分析”共用实心主按钮样式。首屏保留一个主要提交／查看动作；删除转入次级操作，原确认流程不变，禁止按钮嵌套在整行链接内。
7. 用户错误提示统一由 `toUserMessage(error)` 返回 `{title,description,details}`：稳定错误码映射中文；未知码显示“操作未完成，请查看详情”，技术原文可展开，不输出到主要卡片。retryable=false 不提供假重试；已缓存数据刷新失败保留数据并标记“更新失败”。

#### 4.1.2 三方依赖能力评估

现有 Tailwind／CSS 支持 sticky、媒体查询和变量，Radix Dialog 已承载弹窗及焦点管理；使用现有依赖。窄屏菜单与页签的键盘行为必须真实浏览器验收，不以 jsdom 样式快照代替。

#### 4.1.3 风险与验证方式

- 1280×720 页面滚动到底仍可点击导航；AI 详情路由选中态正确；`#report` 不被 sticky 头遮挡（区块加 scroll margin）。
- 1440×900、1024×768、390×844，深／浅两主题检查正文、焦点、弹窗；除图表／数据表自身容器外不允许整页横向滚动。
- 错误覆盖未知码、非重试错误、后台刷新失败；不把业务不可用改为网络失败。

#### 4.1.4 文件变更清单

- **新建（根目录 `frontend/src/`）**：`shared/ui/SectionTabs.tsx`（页签）；`shared/feedback/userMessage.ts` 及其测试（错误文案映射）；`shared/charts/useChartTheme.ts`（图表主题适配）。
- **修改（根目录 `frontend/src/`）**：`styles.css`、`app/AppShell.tsx`、`app/AppProviders.tsx`、`app/components/SidebarNav.tsx`、`shared/feedback/ErrorState.tsx`、`shared/ui/markdown.tsx` 及现有Markdown测试、`shared/charts/CandlestickChart.tsx` 及现有测试、`modules/market/pages/TrendComparisonPanel.tsx` 及现有测试、`shared/ui/dialog.tsx`（提供可选固定底部布局，保持默认兼容）；页面头部由所属模块调整。
- **删除**：无。

### 4.2 行情摘要与板块状态

#### 4.2.1 模块设计

**行情摘要**：保留 11 指数与 US→KR→CN 分组，在各组展示紧凑报价卡。卡片含名称、代码、最新日线收盘、变化、数据日期和状态；“查看 K 线”展开组内独立详细区（每组最多一张），不把无行情显示为 0。原图例、MACD、BOLL、画线、橡皮擦、清空仍在详细区。

数据流：每个报价卡请求原 `useMarketBarsQuery`，初始日期范围沿用现有最近 180 天；mapper 对 timestamp 升序整理副本，取最后两条有效日线，变化 `(last.close / previous.close - 1) * 100`。不称“实时”或“今日涨跌”，文案为“较前一条日线”，附最近两个日期；单根／非有限值／前收≤0只显示收盘及“变化不可计算”。不新增交易日历查询，不自算技术指标。

同一个指数的初始 summary/detail 使用相同 symbol、market、interval、from、to 的 query key。详细区只挂载选中指数，摘要已有结果传给详细区或共享 observer 并禁用打开时重复 refetch；详细图平移扩展范围仍沿用原 loadedFrom、日期锚逻辑。摘要固定取自己的完整初始缓存，不随用户图上缩放变成历史报价。绘图持久化严格保留 `loadDrawings(asset.symbol)`／`saveDrawings(asset.symbol,...)` 的 symbol 键；组件按 market＋symbol 重挂载以隔离本地状态，不迁移已有存储键。

**页面区块**：顶部增加“市场／趋势／板块／信息”锚点入口，保留现有区块和趋势区间按钮。第一阶段不重排市场顺序、不改归一化共同首日=100。

**板块数据**：items 非空但概念 pct_chg 全 null 时，先显示“该日期无可用概念涨跌幅，灰色不代表下跌”，展示选中日期与响应 as_of。部分 null 时显示“有行情 X／全部 Y”，灰色图例“暂无行情”；down 图标题改“下跌／持平／暂无行情”，仍保留原 split 规则。null 不认定停牌，后端未提供原因时不能自行推断。FRESH 也不证明每个节点有行情。

**保留图表约定**：双层概念／个股、红涨绿跌、涨跌分图、排序、面积保底、top30／每概念最多100成分、点击开 K 线均保留。增加图外紧凑概念列表（代码、全名、涨跌、查看 K 线），窄屏仍能用列表定位。降低白色边框强度，过小文字不强塞；鼠标／键盘通过列表访问完整值。暂不做默认单层下钻，不自动跳“最近有数据日期”——现有接口没有可用日期清单。

**数据时效**：直接消费 freshness_status 与 market_session_status，分别显示“已收盘”和“数据可能延迟”；展开来源时才展示 source、source_updated_at。任何过期数据都保留其 as_of，不能用本地当前日期覆盖。信息区无结果时按当前筛选说明，提供重置筛选；不承诺存在可审核事件就一定有宏观信息。

#### 4.2.2 三方依赖能力评估

复用 ECharts 和既有 `CandlestickChart`、`ConceptTreemap`，不换图库。遵守 [日期锚互斥](../../../experience/pitfalls/frontend/echarts-datazoom-anchors.md) 与 [事件解绑约定](../../../experience/pitfalls/frontend/zrender-shared-eventful.md)。概念节点继续静态着色，避免恢复已证实不生效的颜色回调。

#### 4.2.3 风险与验证方式

- 两条有效 bars（100→102）、单条、空列表、乱序、非有限值、前收0测试；确认用户缩放不改变报价日期。
- 测试概念全 null、部分 null、0%、正负混合；接口 STALE 但可展示时保留图与日期。零值颜色保留现有规则，图例准确解释。
- 浏览器切换指数、拖动／滚轮放大缩小、画线后关闭重开；报价查询正常时首屏不实例化 11 张详细图。图表监听清理必须按 handler 引用。

#### 4.2.4 文件变更清单

- **新建**：`frontend/src/modules/market/pages/mappers/toIndexQuote.ts` 及测试；`modules/market/components/IndexQuoteCard.tsx`、`ConceptSummaryList.tsx`。
- **修改**：`modules/market/pages/MarketOverviewPage.tsx`、`MarketIndicesPanel.tsx`、`HotConceptsPanel.tsx`、`MacroInformationPanel.tsx`、`queries.ts`；`components/ConceptTreemap.tsx`、`conceptTreeOption.ts` 和对应现有测试。以上均以 `frontend/src/` 为根。
- **删除**：无；`CandlestickChart` 仅按4.1调整主题颜色，不改变公共缩放与绘图契约。

### 4.3 AI 结果状态与阅读顺序

#### 4.3.1 模块设计

**状态严格分开**：task.status 保持后端唯一真相，SUCCEEDED 文案改“执行完成”。不可用区块保留原服务端三态；`NOT_REQUESTED` 不记为失败。最近结论使用新增 unavailable_blocks 显示“交易决策区块不可用”，待处理区使用相同中文 block 映射。若量化结果存在而传统 decision 不可用，报告显示“量化扫描结果已生成；交易决策区块暂无内容”，保留量化警告和原不可用区块，不能把 quant_execution 直接当成 decision 的替代状态。

**字段闭环**：`get_dashboard()` 的 recent 查询已拿到 `(task, report)`，直接复用 `_unavailable_blocks(report)`，不从最多10条 pending_actions 反查（会漏数据）。`list_tasks()` 使用 ORM 的 selected_layers，经 `_to_list_item_dto` 和路由 `_to_list_item` 传出。无需改 repository、报告生产逻辑或持久化字段。

**提取器边界（本次补齐，并非既有防御）**：`_unavailable_blocks` 只接受字典 report_json 与 list 类型 sections；根对象或sections容器非法返回空列表并记录结构告警（只记任务/报告标识和异常类别，不记录正文）。逐项仅接收字典、`status==UNAVAILABLE`、block属于 market/sector/stock/decision；未知或缺失block过滤并记录告警。reason仅保留str，否则为null；retryable仅当值严格为True时为true，字符串 `"false"` 不当真。过滤不是“报告完整”的证据，最近结论始终不显示完整性保证；既有pending记录若提取为空，显示“报告状态需要核查，请查看详情”，保留入口。详情报告端点的历史损坏数据解析不在本次修复范围，出现现有错误响应时按错误态展示，不能显示空白成功。

**列表识别**：标题“仓位分析 · 全市场”，副行有效交易日／更新时间与短 ID；同日同层级多任务依靠短 ID 区分，不杜撰策略名。主区是详情链接，删除按钮与链接同级。看板“暂无摘要”仅提供查看报告，不制造具体投资结论；进行中为空时缩成一行状态，不让中列占一大块空白。

**详情状态布局表**：

| REST 状态 | 主区 | 次级区 |
|-----------|------|--------|
| PENDING/QUEUED/RUNNING/RETRYING/CANCEL_REQUESTED | 状态、真实进度、原取消操作 | 拓扑／日志；原 SSE 与 REST 回退机制保留 |
| SUCCEEDED | 紧凑完成信息→报告加载／错误／正文→量化摘要及限制 | 任务元信息、执行拓扑、调用日志折叠 |
| FAILED | 失败原因中文说明→重新发起分析入口 | 诊断拓扑、日志及已有节点重跑入口；保留重跑确认 |
| CANCELLED | 已取消、返回列表／新建分析 | 元信息及可用历史日志 |

成功任务仍只在 SUCCEEDED 后请求报告；折叠日志按需挂载，非终态原轮询照常。日志为空时终态显示“本次任务未提供执行日志”，非终态才显示“等待执行日志”。`#report` 等查询成功和 section 挂载后定位，刷新／返回不重复跳动。保持 cancel、rerun、删除、404 与查询失效的既有逻辑。

**量化阅读**：优先展示扫描数、命中数、建议买卖数、失败数，以及请求日／市场数据日不同的提示；0条建议是有效结果。持仓风险限制保持可见；英文 code 用映射显示中文解释，原码在技术详情。策略价标“策略参考价（前复权）”，订单候选标“订单候选价（原始价格）”，两者分组，不更改精度、口径或订单状态。不新增“全部可以买入”之类结论。

#### 4.3.2 三方依赖能力评估

现有 FastAPI/Pydantic、生成客户端与 Query 足够。按 [codegen 约定](../../../experience/pitfalls/frontend/pnpm-openapi-codegen.md) 先导出后消费；Literal 的 enum／union 形状以重新生成结果为准。无新 API、无新行情或 LLM 请求。

#### 4.3.3 风险与验证方式

- 构造同一成功任务 recent＋pending；两边均解释“执行完成但部分区块不可用”。另构造 unavailable 任务落在 pending 限额之外，recent 仍能正确提示。
- 旧报告无 quant_execution、空摘要、NOT_REQUESTED、报告404分别构造；缺失新增字段只能降级为未知，不能标完整。新增提取器测试覆盖非字典report_json、非list sections、缺失／未知block、数值reason、字符串retryable；非法字段不进入Pydantic Literal验证。使用 `test_dashboard.py` 已有 `_service()`、`_run_to_success()` 和 FakeUnitOfWork；根对象损坏不能通过正常写入helper构造时，先保存合法报告再在fake repository对象上直接替换 `report_json`。只在测试中这样构造，不修改真实历史记录。
- `AiTaskDetailPage.test.tsx` 现有“日志在报告前”的断言需要随迁；新增终态日志关闭不查询、展开才查询、#report定位与重跑后恢复轮询测试，不宣称已存在懒加载覆盖。新列表字段在 `backend/tests/contract/api/test_analysis_tasks.py` 核对；看板脏数据用同文件隔离client fixture写入测试记录，断言GET响应不因未知block崩溃。
- 当前量化页面已有并发变更，实施只改展示容器和文案，不删除其建议订单、成交登记与生命周期入口。

#### 4.3.4 文件变更清单

- **新建**：`frontend/src/modules/analysis/shared/resultPresentation.ts` 及测试。
- **修改 backend（分组根明确）**：应用层根 `backend/modules/analysis/application/` 下 `contracts.py`、`task_lifecycle.py`；schema根 `backend/api/schemas/` 下 `tasks.py`、`dashboard.py`；路由根 `backend/api/routers/` 下 `analysis_tasks.py`、`analysis_dashboard.py`；`backend/openapi/openapi.v1.json`；测试根 `backend/tests/unit/analysis/` 下 `test_dashboard.py`、`test_task_service.py`，以及 `backend/tests/contract/api/test_analysis_tasks.py`。
- **修改 frontend生成客户端**：`frontend/src/api/generated/` 由codegen重新生成。
- **修改分析共享（根目录 `frontend/src/modules/analysis/shared/`）**：`taskStatus.ts`、`analysisLayers.ts`。
- **修改看板（根目录 `frontend/src/modules/analysis/pages/dashboard/`）**：`PendingActionsPanel.tsx`、`RecentConclusionsPanel.tsx`、`AiDashboardPage.tsx` 及对应测试。
- **修改任务列表**：`frontend/src/modules/analysis/pages/tasks/AnalysisTaskList.tsx` 及对应测试。
- **修改详情（根目录 `frontend/src/modules/analysis/pages/task-detail/`）**：`AiTaskDetailPage.tsx`、`TaskStatusCard.tsx`、`GraphTopologyPanel.tsx`、`ExecutionLogsPanel.tsx`、`ReportContent.tsx`、`QuantExecutionPanel.tsx`、`BuySignalCard.tsx`、`queries.ts` 及对应测试。
- **删除**：无。

### 4.4 拓扑可读性与操作入口

#### 4.4.1 模块设计

复用现有节点 id、row、order、layer、edge kind；不重新提取 AI 图，不改变节点顺序或纯代码节点守卫。

- 坐标采用 `x=90+order*180`、`y=60+row*120`。取所有实际节点坐标的 minX/maxX/minY/maxY，跨度 dx=maxX-minX、dy=maxY-minY。画布宽 `max(360,dx+180)`，高 `max(180,dy+180)`；外层独立横向滚动，不将画布按父宽fit缩小。节点文字最大宽140px、最多两行、字号12–14px，长名称保留 tooltip 全文。
- 正常轴使用 `series.left=90,width=dx`、`top=60,height=dy`；对应轴跨度为0时，当前库把min/max各扩1，显式使用width/height=2，水平单列left=画布宽/2-1使其水平居中，垂直单行top=59使节点中心保持y=60，避免除0。空节点直接空态，不调用跨度计算。
- 行标签在最终实例 `onChartReady` 后，读取 `chart.getModel().getSeriesByIndex(0).coordinateSystem.dataToPoint([该行节点x,该行节点y])` 的真实y，再设置独立graphic标签top；图数据/尺寸重建后重新定位，不能套未变换的row像素。更新graphic使用独立稳定id且不重建series，避免反馈循环。实际像素可受当前库视图变换影响，以此读取结果作为对齐真值。缩放控件不是本期交付依赖。
- 新增图外节点列表：按层分组，完整标签、默认／已自定义／纯代码状态。可编辑节点用 button 打开原 PromptEditDialog；纯代码节点只读。任务详情列表复用原状态、节点详情与重跑权限，纯代码／不可重跑节点不提供操作。
- 深浅主题文字／连线从 token 传入 option，状态不只依靠颜色，附中文文字。Tooltip 中动态名称按文本转义，不直接拼入可执行 HTML。

#### 4.4.2 三方依赖能力评估

当前已使用 ECharts graph `layout:none`、固定坐标与 graphic。2026-09-22只读诊断在当前安装的ECharts上运行SSR真实实例：画布2520×720、12节点x=90+i×180、y=(i mod 2)×120，series left=90/top=90/width=1980/height=120；dataToPoint结果 `[90,0]→[90,90]`、`[270,120]→[270,210]`、`[2070,120]→[2070,210]`，间距180px保留。`echarts/lib/chart/graph/createView.js` 已核实零跨度轴min/max各扩1。该诊断只验证坐标变换，文字边界、真实容器resize和主题切换仍须浏览器验收；不通过时本模块不得标完成，节点列表可用于诊断期间操作，但不能替代“不重叠”验收要求。

零跨度补充诊断：按本方案规则，单行2160×180画布的节点落点为 `[90,60]`、`[270,60]`、`[2070,60]`；单列360×300为 `[180,60]`、`[180,180]`；单节点360×180为 `[180,60]`。均为当前库SSR实测，不是前端页面实现结果。

#### 4.4.3 风险与验证方式

使用当前21节点、“同层12节点＋长英文标签”、单行、单列、单节点和空节点构造数据，检查真实dataToPoint坐标与标签边界，确认每个可见标签不与相邻标签相交、节点能准确点击、层标签对齐。通过图和列表打开同一 id；图横向滚动不带动整个页面。复核任务拓扑的条件边、平行边、循环边和提示词编辑守卫。

#### 4.4.4 文件变更清单

- **新建**：`frontend/src/modules/analysis/components/TopologyNodeList.tsx` 及测试。
- **修改（根目录 `frontend/src/modules/analysis/`）**：`components/topologyChartOption.ts`、`pages/agents/AgentTopologyPage.tsx`、`pages/task-detail/GraphTopologyPanel.tsx` 及对应测试。
- **删除**：无。

### 4.5 自选与分析表单

#### 4.5.1 模块设计

**自选**：保留自选和手工组合两个独立资源区。选择文案不依赖方向，统一“先选择一个组合／分组”。空分组区只显示创建入口；选择后标题写明当前对象，组合卡片带选中态。大额金额加千分位，风险显示百分比。

**持仓**：本地模式 `add | edit`。默认“添加持仓”；每行新增“编辑”进入 edit，回填完整原持仓，市场／代码锁定，按钮为“保存修改”。添加已存在 market＋symbol 时阻止直接 upsert，提示“已存在，进入编辑”，用户显式点击才切换并丢弃未提交的新建字段；按钮说明该操作会载入现有持仓。删除沿用确认。

字段顺序为市场→代码→数量→平均成本→止损，数量不自动乘100（界面写“按持仓数量录入，非手数”），价格按市场显示币种。空成本与合法0明确区分。进入edit时捕获原持仓与 `baseRevision`；add在首次编辑时捕获当时的revision与现有标的集合，查询未成功前禁用输入。提交 `expectedPortfolioRevision=baseRevision`，不得使用背景refetch后的最新revision。发现当前revision与基线不同或收到409，保留草稿供查看但阻止再次提交，提示“数据已变化，重新载入后再编辑”；显式重新载入将放弃当前草稿、取最新数据并创建新基线，不能自动合并后换新版本重试。这样其他客户端新增同标的也不会被add悄悄upsert覆盖。切换组合若有草稿，以放弃／留在当前的提示处理，不跨组合携带。

**账户设置**：基础信息／仓位限制／风险与熔断／净值事实四组；默认展开基础，其余按需展开。弹窗标题和保存／取消固定，内容单独滚动。错误发生在折叠组时展开并定位到字段。

弹窗每次打开捕获同一份 `originalPortfolio` 与 `baseVersion=portfolio.version`。字段初值、未编辑值和 `expectedVersion` 全从该快照取，不能因props更新而抬高版本。背景版本变化／409同样进入需重新载入状态；用户明确重新载入才替换快照和草稿。未编辑直接关闭不提示丢失。每次关闭后重新打开以最新DTO建新会话，避免 `useState` 初次挂载值长期陈旧。

PercentField 只处理展示与字符串编辑，保留空串、小数编辑中状态；每个 pct 字段在提交适配层仅除100一次，非pct字段（最低盈亏比、金额）不转换。加载只乘100一次；不要用四舍五入后的显示字符串覆盖未编辑的原始值。未编辑字段提交原 DTO 值，编辑字段解析为有限数且 `0 < percentage <= 100`；关系校验仍在转换后的接口值上做。例输入100合法、0非法、空串必填错误、2.5→0.025。

**新建分析**：保留全市场模式、默认不选、自由非空组合、position 可独立。每个层级附一句产出说明；提交附近实时说明缺少层级／策略／组合的原因，禁用不合法提交并仍保留字段校验。仓位关闭时清除量化三字段；幂等 key、版本冲突、提交中防重和失败保留输入沿用。新建包含某层级的入口只添加 `layers` 查询参数（白名单解析），不自动选择其他层、不自动提交；普通 `/ai?create=1` 仍全不选。

#### 4.5.2 三方依赖能力评估

复用现有 react-hook-form／zod、Radix Dialog，不引入金融计算库；PercentField 是边界适配，不替代后端金融计算。遵守 [弹窗回填保护](../../../experience/pitfalls/frontend/react-query-dialog.md)：显式 dirty 标识，包括用户清空操作。

#### 4.5.3 风险与验证方式

重点测试百分比往返、未编辑精度保持、空值／0、409、切换组合、重复代码、新建转编辑确认、隐藏高级字段不丢值；覆盖“草稿基线v1→refetch v2→不得携带v2提交”、add期间另一客户端新增同标的、账户原DTO与baseVersion同源、用户显式重载才解除阻塞。测试返回表单预填只影响白名单层级。仅在 mock 后端验证 mutation，禁止用真实持仓作试写。

#### 4.5.4 文件变更清单

- **新建**：`frontend/src/shared/ui/PercentField.tsx` 及测试。
- **修改自选（根目录 `frontend/src/modules/watchlist/`）**：`pages/WatchlistPage.tsx`、`watchlists/WatchlistGroupList.tsx`、`watchlists/WatchlistItemList.tsx`、`portfolios/PortfolioList.tsx`、`portfolios/PortfolioSettingsDialog.tsx`、`portfolios/PositionList.tsx`、`queries.ts` 及相关测试。
- **修改分析（根目录 `frontend/src/modules/analysis/`）**：`pages/dashboard/AnalysisTaskForm.tsx`、`pages/dashboard/QuantParamsPicker.tsx`、`pages/dashboard/AiDashboardPage.tsx`、`pages/task-detail/ReportContent.tsx` 及相关测试。
- **删除**：无；不调整写入接口。

### 4.6 事件审核工作台

#### 4.6.1 模块设计

**主表／详情**：默认主列表仅保留时间与来源、完整标题摘要（最多两行）、事件类型、重要性、处理动作、详情入口；其他编辑字段放在详情 Dialog 内。标题列正下界≥14rem；主表自己的横向滚动容器与 sticky 标题／操作列，在1024px仍可操作。低于768px转卡片，详情字段单列。保留可选“完整表格”供批量编辑，复用原14列模型和提交逻辑。

详情与表格共用父层 `rows[draftId]`、`patchRow`，不在弹窗复制可独立覆盖的完整草稿。详情修改即时写本地草稿，按钮“返回列表”，说明“尚未提交”；真正提交仅通过原批量确认。搜索、筛选只过滤显示，不能重置 rows／editedDraftIds。新增搜索和类型筛选只针对当前已加载列表，明确标“当前列表”，不伪称全库搜索。

**提交范围**：粘性底栏展示“通过X／忽略Y／未提交变更Z”；确认弹窗明确“包括被筛选隐藏的N条”，以原所有非skip行作为提交集合，用户可返回检查。仍按10条分块，保留行级错误、成功反馈、影响计算失败重试和 sector/stock 缺目标拦截；不能因精简字段而取消这些约束。手动清空数值保持 null，合法0保留。

**原文**：只对事件来源文本新增 `normalizeEventText`，不全局改变 MarkdownView。采用已知 HTML 标记到安全文本的保守转换：只移除 b/strong/em/i/p/div/br/span 的标签标记，块标签转换为换行，常见实体按明确表解码（amp/lt/gt/quot/nbsp），不解码后再进行标签剥除循环，不执行／挂载任何 HTML、不请求远程资源。普通 Markdown 原样交给 MarkdownView；未知标签保持转义文本，原始内容折叠保留。`<script>`、img、iframe 不执行；事件标题同一清理规则，不出现前后不一致。

来源 URL 只允许 http/https 生成链接，其余协议显示文本。AI 建议主视图显示中文字段、实际值／前值、作用域与目标；未知 key 和原始 JSON 放“技术详情”。引用名无法解析时保留代码并提示“名称未解析”，不把 US 事件映射到 CN 的正确性当作本次样式结论。

审核原文渲染 `MarkdownView` 时传新增可选 `imagePolicy="text"`：自定义img组件仅返回alt纯文本或“图片已省略”，不创建img节点，也不发src请求。默认 `imagePolicy="allow"` 保持其他报告／日志原有行为。Markdown的 `![图](https://...)` 与原始HTML img都纳入请求拦截验收；仅清理HTML标签不能保证这一点。链接仍需用户点击才跳转。

**自动操作**：基线保留现有30分钟 sessionStorage 节流的自动 refresh→新增时 prelabel，修改成顶部始终可见的阶段条：“自动拉取事件”→“新增N条，AI预填中”→“完成／部分失败”。显示手动／自动来源和本页已处理数量；从首次动作开始就展示，不到完成才说明发生过什么。现有 `runPrelabel(false)` 按 remaining 批次执行，界面必须写“全部待预填事件”，不能声称只处理本次新增N条；强制重填仍明确当前草稿ID集合。保留0进展护栏、互斥按钮、locked／failed区分。前端不提供假“取消服务端计算”。

若用户接受第五章 D1，则改为进入页面仅 GET，显式“拉取事件”和“AI预填”两个动作，移除挂载自动 effect 及 runRefresh 后的自动预填；两条现有 POST 契约不变。未确认前此分支不进入实施范围。

#### 4.6.2 三方依赖能力评估

无新增服务端搜索、分页、任务取消端点。复用现有 query／mutation／Radix；HTML 使用白名单文本转换，不引入 rehype-raw 或第二 Markdown 引擎。完整表格遵守 [Grid 正下界约定](../../../experience/pitfalls/frontend/grid-minmax-collapse.md)。

#### 4.6.3 风险与验证方式

- 同一行在简表／完整表／详情往返数值一致；输入0、清空、刷新、筛选隐藏后提交范围正确；已编辑行不被后台预填刷新覆盖。
- 伪造带 `<b>`、Markdown、`<script>`、`<img src=...>`、`![图](https://example.invalid/test.png)`、实体、非法来源协议的数据，审核原文无脚本执行或图片资源请求，主要正文无已知粗体标签残留；另测默认imagePolicy不改变其他页面既有Markdown行为。
- 基线测试首次挂载、节流命中、locked、failed、0新增、prelabel零进展、分块失败；若选择D1则改为断言挂载与切Tab无POST、点击一次才发对应请求。
- Computer Use 验收审核页必须使用拦截写请求的测试环境，防止再次因浏览触发真实采集／LLM。

#### 4.6.4 文件变更清单

- **新建**：`frontend/src/modules/event-study/pages/review/ReviewSummaryRow.tsx`、`normalizeEventText.ts` 及测试。
- **修改**：同目录 `PendingEventsTab.tsx`、`PendingEventRow.tsx`、`EventDetailDialog.tsx`、`ImpactConfirmTab.tsx`（仅共用布局与状态文案）、`queries.ts`、`mappers/toPendingEventRowVM.ts` 及对应测试；主页面 `EventStudyPage.tsx` 页签统一。
- **修改共享组件**：`frontend/src/shared/ui/markdown.tsx` 及其测试，新增默认兼容的imagePolicy；主页面完整路径为 `frontend/src/modules/event-study/pages/EventStudyPage.tsx`。
- **删除**：无；D1获批时仅删除自动 effect 与节流常量，不删采集功能。

### 4.7 事件预测输入

#### 4.7.1 模块设计

资产选择使用既有 assets DTO（ticker/name/market），本地同时按名称与代码匹配，列表显示“名称 · 代码 · 市场”。输入文字与已选 ticker 分开；选中项回填ticker，修改文字后清除旧选中值。键盘上下选择、Enter确认、Escape收起，空列表明确“无匹配资产，可输入代码”，不硬编码股票目录。

继续允许自由代码：当未选择候选时，只把用户显式输入文本按现有必填规则提交服务端，不把“平安”猜成任意一只股票；显示“将按输入内容作为资产代码查询”，服务端错误关联资产字段并保留输入。assets 加载失败仍允许自由输入，提供重试资产清单的入口。

事件文本增加字符计数与例子“某政策发布及主要内容”，保留1–20000字符限制；类型／子类型／条件放在“补充信息（可选）”，给出“货币政策／利率调整／超预期”等填写例子，但不把示例当新增枚举约束。预测窗口保留原 pre_event_5d/event_day/post_event_5d 枚举，不擅自将“5日”改成“5个交易日”；save文案改为“保存预测记录，便于追踪”。提交中防重，503/504保留输入、仅手动重试。

#### 4.7.2 三方依赖能力评估

现有资产清单支持 name/ticker，无远程模糊搜索依赖。AssetCombobox 用语义化 combobox/listbox、受控状态实现，focus／keyboard 用现有测试工具和浏览器检查。

#### 4.7.3 风险与验证方式

构造名称含“平安”的多条资产、代码精确命中、无匹配、空目录、目录加载失败；不得沿用前一条已选 ticker 提交。折叠补充项不丢输入；503／504 不自动重试、不清表单；不调用真实预测服务验收。

#### 4.7.4 文件变更清单

- **新建**：`frontend/src/shared/ui/AssetCombobox.tsx` 及测试。
- **修改**：`frontend/src/modules/event-study/pages/PredictionForm.tsx`、`PredictionTab.tsx`、`PredictionTab.test.tsx`；资产查询仍沿用 `queries.ts`，如需传加载错误状态只调整消费 props。
- **删除**：无。

### 4.8 实施顺序与验收矩阵

本节是方案阶段的交付顺序与验证约束，正式开发任务在用户确认后填入 tasks.md。

1. 基础布局／文案和两个只读DTO字段；先后端契约导出，再前端消费。
2. AI状态、报告阅读顺序和拓扑，先解决结果解读与遮挡。
3. 事件审核列表／详情／状态条；D1选择需在该模块实施前明确。
4. 行情摘要、账户与预测表单，最后进行跨页面真实浏览器验收。

| 验收层 | 执行方式（实施阶段执行） | 必须验证的行为 |
|--------|------------------------|----------------|
| 后端投影 | `.\.venv\Scripts\python.exe -m pytest backend/tests/unit/analysis/test_dashboard.py backend/tests/unit/analysis/test_task_service.py` | recent独立于pending限额；layers正确传出；无持久化写入 |
| API契约 | `.\.venv\Scripts\python.exe -m pytest backend/tests/contract/api/test_analysis_tasks.py`；其conftest的module级client会建立／清理 `liveprofit_contract_test` 和Redis db11，只在隔离测试基础设施运行 | 字段、默认值、脏区块过滤、排序／游标保持一致；禁止指向用户业务库或共用Redis db11 |
| 生成契约 | `.\.venv\Scripts\python.exe -m backend.scripts.export_openapi`；`pnpm --dir frontend generate:api` | 生成结果保留并发量化模块字段，不手改generated |
| 静态质量 | `pnpm --dir frontend typecheck`；`pnpm --dir frontend test`；`pnpm --dir frontend build` | 新旧类型一致，相关行为测试通过，构建成功 |
| 交互回归 | 新建 `frontend/e2e/ux-review.spec.ts`；运行 `pnpm --dir frontend e2e ux-review.spec.ts`；本地Vite须已启动，Playwright在首次导航前拦截全部 `/api/**`，未配置的请求直接失败，不穿透真实后端 | 状态／空态／失败、只读浏览、草稿版本、表单保留与布局矩阵；不运行既有真实LLM闭环用例 |
| 图表验证 | 当前ECharts真实实例＋Computer Use | zoom／draw不回归，21节点长标签不重叠，null行情不误读 |
| 视觉验收 | 1280×720、1440×900、1024×768、390×844；深浅主题；关键页面截图 | 非表格区无横向溢出、操作不遮挡、Tab焦点可见、错误可定位 |

基线和改后截图在同尺寸同fixture比较，验收记录写入 result.md；验证未执行时标“未执行”，不能以方案评审代替实现验证。图表参数不能只断言 option 中存在某项，必须观察实际交互与布局。

### 4.9 向后兼容、风险与回退

- 路由、`create=1`、`tab`、`#report` 保持；新增 `layers` 只识别白名单，不让URL直接触发写操作。
- 既有绘图存储key、日期锚、报价币种、趋势归一口径、报告三态、数量／比例API含义、幂等键、revision冲突检查全部回归。
- 无DB迁移；按模块提交时只显式暂存本任务文件。后端增量字段先部署，前端后部署；回退前端后可保留兼容字段，完整回退按版本进行，不做v2双轨。
- 当前工作区大量量化代码未提交。本文不移动、回滚或提交这些文件；实施前确认其状态并在最终schema上重新生成客户端。原文文本规范化只影响展示，不改事件记录。
- 完成开发与Code Review后才更新 knowledge；本次方案不把目标设计写成系统现状，不归档、不自动commit。

## 五、已确认决策 / 待确认问题

### 已确认范围

- 用户要求先从用户视角找问题，再按 agent.md／CLAUDE.md 模板生成技术方案；当前阶段只写方案。
- 遵循 agent.md 八文件骨架及初稿独立评审；README 为状态权威。现有 CLAUDE.md 在工作区处于删除状态，不恢复它或修改根目录约定。
- 历史已确认约定作为基线：US→KR→CN、双层涨跌图及点击开K线、分析层级默认全不选。此次评审意见不等于推翻历史决定。

### D1：是否把审核页自动拉取与 AI 预填改为两个显式动作？

- **背景**：`PendingEventsTab.tsx` 挂载effect每30分钟允许触发 `runRefresh()`，新增事件后调用 `runPrelabel()`；浏览器实测进入页面即显示新增47条并开始预填。该行为来自已有自动拉取方案，移除会改变使用习惯。
- **目标**：A 保留自动流程，补齐持续可见的阶段条、范围和结果；B 页面只读加载，点击“拉取事件”才采集，点击“AI预填”才处理全部待预填事件。
- **推荐**：B，浏览与后台写操作边界更清楚；本方案基线按A保持兼容，B需用户明确选择后才替换4.6的自动触发行为与测试。

### D2：大盘是否采用“报价摘要＋按需展开详细K线”？

- **背景**：`MarketIndicesPanel.tsx` 同时渲染11张详细图，首屏只能看到两张的部分内容；改成摘要会增加打开详细图的一次操作。
- **目标**：A 按4.2，每组报价卡配一个按需详细区；B 保留全部详细图，仅增加摘要和区块定位，页面仍较长。
- **推荐**：A，优先提高跨市场扫描效率且保留完整工具；4.2按A编写，方案整体确认即采用A，若用户选B则只删详细图折叠设计。

D1不影响其余模块的方案完整性；在用户决定并授权实现前不启动开发。具体测试布局、原始技术信息折叠和字段说明为本方案推荐设计，并非已经实施或验证通过。


实施补充（2026-09-22）：用户已授权开发及视觉升级。增加统一深海蓝/青碧主题、浅色主题、柔和背景光晕、面板层次和轻量交互动画；遵守 prefers-reduced-motion，不遮挡数据、不引入新的运行依赖。D1采用保留自动流程，D2采用报价摘要与展开K线。
