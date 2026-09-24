# 行情状态与指数卡片布局方案

> **状态**：初稿（2026-09-24）

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 行情进度 | `MarketOverviewPage` 在页首渲染六组 `MarketRefreshStatus`，移动端占一整块且内部滚动；个股日线也被概念树/个股 K 线消费 | 个股 5568/5568、指数 11/11 与板块 1031/1031 混在大盘头部；量化用户需离开量化页面查看个股进度 | 指数与板块在自身区块显示精简拉取状态，个股状态在量化扫描区块显示；既有概念树数据消费保持可用 |
| 指数卡片 | `IndexQuoteCard` 将名称、代码币种、价格、涨跌幅和日期分多行 | `标普500` 与 `.INX` 本是同一标的身份，却分两行；价格与涨跌幅也分两行 | 同维度信息同行展示，保持窄屏可读和 K 线展开行为 |

## 二、架构设计

不改变后端 API、数据库或服务端调度。重组前端已有 `refresh-status` 数据的显示位置，并将前端自动投递及忙态轮询按页面资源范围分配：大盘只投递/加快轮询四组指数和板块，量化页只投递/加快轮询个股。大盘仍监听个股版本，失效本页消费个股日线的概念树缓存和已展开的个股 K 线查询；不因此展示个股任务或加快轮询。接口仍一次返回六组，共享同一 React Query 缓存；后台 Dispatcher 的自动补齐职责不变。

## 三、设计概览

### frontend

#### 共享组件

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| `MarketRefreshStatus`【修改】 | 改为可按资源筛选的精简行内状态，不展示过期任务 | `frontend/src/modules/market/components/MarketRefreshStatus.tsx` | 拉取中显示当前进度；缺口显示覆盖数和可重试入口 |

#### API 接入

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| `useMarketRefresh`【修改】 | 按页面资源范围限定自动投递和忙态轮询；大盘保留个股版本变化对概念树和活动个股 K 线的失效；量化页不执行全市场兜底重拉 | `frontend/src/modules/market/pages/refreshQueries.ts` | 大盘与量化各只启动自身资源，概念树和展开的 K 线及时更新 |

#### 页面区块

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| `MarketIndicesPanel`【修改】 | 指数卡片同类信息同行排布 | `frontend/src/modules/market/pages/MarketIndicesPanel.tsx` | 名称/代码、价格/涨跌幅同排 |

#### 页面集成

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 大盘与量化页【修改】 | 移除页首状态块，在指数、板块及量化区块各自放置状态 | `MarketOverviewPage.tsx`、`DailyResearchPage.tsx` | 业务状态贴近内容 |

## 四、详细设计

### 4.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| 行内刷新状态 | `MarketRefreshStatus` 固定输出六组状态并占 `h-40` | 增加 `resources` 参数，仅输出对应资源，区分活动任务、缺口和已更新 |
| 页面分布 | 大盘页首状态包含 `CN_STOCK_DAILY` | 指数区放四组指数资源，板块区放板块资源，量化策略扫描卡放个股资源 |
| 指数卡片 | `IndexQuoteCard` 名称/代码上下分行 | 名称和代码同行，价格和涨跌幅同行，次要元数据合并在末行 |

### 4.1 行内刷新状态

组件接收 `refresh: ReturnType<typeof useMarketRefresh>` 和 `resources: Resource[]`。活动 job（QUEUED/RUNNING/RETRY_WAIT）显示阶段和“已处理 N/M”，避免混淆覆盖数；FRESH 只显示简短已更新提示；非 FRESH 显示覆盖比例和手动重试。`HISTORY_GAP` 只在非 FRESH 时显示说明，避免旧历史提示占据当前行情模块。无状态或服务不可用给对应区块简短提示。旧 FAILED/PARTIAL job 在 FRESH 时隐藏；既有“已完成 22/22”组件测试改为验证 FRESH 状态不暴露旧任务冗余进度。

### 4.2 页面分布

大盘移除独立状态卡；指数标题下方用可换行的精简提示显示 `CN_INDEX_BARS/CN_INDEX_FACTORS/US_INDEX_BARS/KR_INDEX_BARS`，板块标题下方显示 `CN_SECTOR_DAILY`。`DailyResearchPage` 的量化策略扫描卡中放 `CN_STOCK_DAILY`，新闻卡不显示个股。`useMarketRefresh({scope:'market'|'stock'})` 分别只自动投递所属资源、按所属资源活跃任务加快轮询。大盘的个股版本变化失效 `conceptTree` 和已展开的活动 `stockBars`，因概念树成员覆盖及 KLineDialog 均消费个股日线；不投递个股，也不因个股任务加快状态轮询。量化页只对个股的 `conceptTree/stockBars` 消费链路失效，并不执行每五分钟的全市场兜底查询。服务端 Dispatcher 自行补齐不依赖用户是否访问页面。用两入口独立挂载测试确认大盘不投递个股、量化不投递指数/板块，以及大盘个股版本变化仍使概念树和活动个股 K 线失效。

### 4.3 指数卡片

卡片首行用 flex：名称可截断，代码及展开图标保持可见；价格和涨跌幅同排；日期、币种和状态放末行。无行情、加载和错误分支保留。较长中文名在窄屏不把代码挤出卡片。

## 五、三方依赖能力评估

复用现有 React Query 状态缓存、Tailwind 响应式类与 Codex Computer Use 浏览器检查能力，不增加依赖或第三方接口。`refresh-status` 已提供 `job.status/result/processed/total`、`freshness` 与目标日覆盖数；无后端变更。

## 六、接口与兼容

后端契约、路由、查询键不变。只改变 `MarketRefreshStatus` 前端属性及页面调用点；大盘页前端协调指数/板块投递、量化页前端协调个股投递。两页共享查询缓存，大盘继续响应个股版本以刷新概念树和已展开的个股 K 线。修改组件和入口页面测试。

## 七、验证方法

运行前端 TypeScript 检查及相关 Vitest；通过 Computer Use 在本地浏览器检查大盘、量化页的桌面和窄屏排版，保存并交付截图。核对 FRESH、活动任务与缺口的模拟组件状态。

## 八、文件变更清单

`frontend/src/modules/market/components/MarketRefreshStatus.tsx`、`MarketRefreshStatus.test.tsx`、`frontend/src/modules/market/pages/refreshQueries.ts`、`refreshQueries.test.tsx`、`MarketOverviewPage.tsx`、`MarketOverviewPage.test.tsx`、`MarketIndicesPanel.tsx`、`MarketIndicesPanel.test.tsx`、`frontend/src/modules/daily-research/pages/DailyResearchPage.tsx`、`DailyResearchPage.test.tsx`；任务文档目录及必要的 `docs/knowledge/frontend/前端平台.md`。
