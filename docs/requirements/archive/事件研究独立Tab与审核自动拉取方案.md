# 事件研究独立 Tab 与审核自动拉取方案

> **状态**：已完成（2026-09-08）
> **进度**：6/6 任务 + Code Review 2 轮 PASS（全部 findings 修复：锁 token+Lua 比对删除并补直接回归测试、前端测试确定性）
> **下一步**：人工检查项（T5/T6 真实环境观察点）待用户执行；方案归档 docs/done/

---

## 一、背景与动机

### 1.1、现状

**前端导航**：

- 侧栏一级导航只有 3 项：大盘（/market）、自选（/watchlist）、AI（/ai），定义于 [frontend/src/app/components/SidebarNav.tsx](frontend/src/app/components/SidebarNav.tsx) 的 `NAV_ITEMS`。
- 事件研究的两个页面挂在 AI 路由下：`/ai/event-study`（影响预测，[AiEventStudyPage.tsx](frontend/src/modules/event-study/pages/AiEventStudyPage.tsx)）、`/ai/event-study/review`（审核，[review/EventStudyReviewPage.tsx](frontend/src/modules/event-study/pages/review/EventStudyReviewPage.tsx)），入口是 AI 看板头部两个按钮（[AiDashboardPage.tsx:52-63](frontend/src/modules/analysis/pages/dashboard/AiDashboardPage.tsx#L52-L63)）。宏观信息卡片跳转 `?event_id=` 也指向 `/ai/event-study`（[MacroInformationPanel.tsx:87](frontend/src/modules/market/pages/MacroInformationPanel.tsx#L87)）。

**审核数据流**（现状不满足诉求 2）：

- 事件草稿的唯一生产者是每日批处理的步骤 1：`event_crawler.fetch_events_from_crawler()`（配置 4 个快讯源：财联社/金十 2 主源默认启用，新浪/东财备源 env 可选开启；每启用源取最新 ~30 条）→ `save_pending_events()`（PG 已处理事件 + Redis 已有草稿标题去重后写 `events:pending:{draft_id}`，TTL 30 天）→ `ai_prelabel.prelabel_events()` AI 预填（[daily_job.py:38-47](AI/eventStudy/scheduler/daily_job.py#L38-L47)）。
- 平台审核端点（[backend/api/routers/event_study_review.py](backend/api/routers/event_study_review.py) 6 个）只**读**草稿（list-pending-events / prelabel / batch / compute / impact-drafts / confirm），**没有任何"现在去采集"的能力**。
- **可观测现象**：`logs/event_study_daily.log` 显示 crawl 步骤最后成功运行于 2026-08-18 15:25；8-30 那次手动运行带 `--skip crawl`；此后 daily_job 无任何日志。→ Redis 待审草稿停留在 8.18 事件，审核页只展示 8.18。

### 1.2、目标

1. **事件研究成为与 AI 同级的一级 tab**：侧栏新增「事件研究」，内部为 hub 页（影响预测 / 事件审核 两个子 Tab）；AI 看板不再承载事件研究入口。
2. **审核时自动拉最新事件 + 自动 AI 分析**：进入审核页自动触发"采集最新事件 → AI 预填"，另有手动刷新按钮；不再依赖每日调度器是否存活。
3. 已确认边界：**不回补 8.19–9.7 历史缺口**（爬虫只取各启用源最新 ~30 条）；**调度器排查不在本次范围**。

---

## 二、架构设计

### 前端导航（改造后）

```
AppShell
└── SidebarNav（一级导航）
    ├── 大盘    /market
    ├── 自选    /watchlist
    ├── AI      /ai（看板 / 任务中心；移除事件研究两个入口按钮）
    └── 事件研究 /event-study  ← 新增，hub 页
        ├── ?tab=predict（默认） → PredictionTab（原 AiEventStudyPage 内容）
        └── ?tab=review          → ReviewTab（原 EventStudyReviewPage 内容）
```

- hub 的 tab 状态用 URL query 参数 `?tab=` 承载（先例：AI 看板 `?create=1`），默认 `predict`。
- 旧路径兼容：`/ai/event-study` 重定向到 `/event-study`（透传原 query，如 `?event_id=`）、`/ai/event-study/review` 重定向到 `/event-study?tab=review`（均 replace），防现网书签/缓存链接 404。
- 宏微观信息卡片跳转改为 `/event-study?event_id=101`（hub 默认 tab 即预测，`event_id` 透传给 PredictionTab 预填，行为不变）。
- react-router 的 `NavLink` 匹配只看 pathname、忽略 query，故侧栏「事件研究」在 `?tab=review` 下同样高亮（无需额外处理）。

### 审核拉取数据流（改造后）

```
PendingEventsTab 挂载（30 分钟节流）
  → POST /api/v1/event-studies/review/refresh（新端点，同步阻塞，经 analysis_services 线程池）
      → EventStudyReviewService.refresh_events()
          → Redis 锁 events:refresh_lock（SET NX PX 120s，防并发重复采集）
          → adapter.fetch_latest_events()  = event_crawler.fetch_events_from_crawler()（启用源各 ~30 条）
          → adapter.save_pending_events(events, conn)（PG + Redis 标题去重 → 新草稿入 Redis）
          → 返回 {fetched, new_drafts}
  → invalidate pending-events 查询
  → 自动跑现有 AI 预填循环（POST /prelabel，前端已有护栏循环，逐条 LLM 60s 超时）
  → invalidate pending-events 查询 → 列表展示最新事件 + AI 建议
```

**refresh 端点不内联 AI 预填**：预填逐条调用 LLM（单条 60s 超时），30 条新草稿最坏 30 分钟，合并进单请求会撑爆前端 300s 超时预算；复用已存在的 `/prelabel` 端点与前端"循环 + prelabeled===0 护栏"，语义与现有人工按钮完全一致。

### 不改变的部分

- 后端分层不变：refresh 走与其余 6 端点相同的防腐层模式（review_adapter 函数级 import AI 侧模块），无新表、无迁移。
- AI 侧爬虫/预填/review_dao 代码零改动（只被复用）。

---

## 三、详细设计

### 3.1 前端独立 Tab（hub 页）

#### 3.1.1 模块设计

**新建 [frontend/src/modules/event-study/pages/EventStudyPage.tsx](frontend/src/modules/event-study/pages/EventStudyPage.tsx)（hub 页）**：

```tsx
export default function EventStudyPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get('tab') === 'review' ? 'review' : 'predict';
  // header：h1「事件研究」+ 描述「事件影响预测与事件审核」+ 两个切换按钮
  //   [影响预测] [事件审核] —— 点击 setSearchParams({...保留现有参数, tab: 'predict'|'review'}, {replace:true})
  // 内容：{tab === 'review' ? <ReviewTab /> : <PredictionTab />}
}
```

- 切换按钮显式传 `tab` 值：到 `predict` 时删掉 `tab` 参数（URL 干净）；到 `review` 时 `tab=review`。
- 其余参数（如 `event_id`）在切换时保留（合并进新 params，不丢弃）。

**`AiEventStudyPage.tsx` → 重命名 `PredictionTab.tsx`**（同目录，组件名 `PredictionTab`）：

- 删除 `<main>` 外层与整段页面 header（h1/描述/「返回大盘」「返回 AI 投研看板」按钮）——导航职责移交 hub 与侧栏。
- `?event_id=` 存在时，在 Card 上方渲染一行提示：`关联事件 #{eventId}`（原 header 描述里的信息不丢失）。
- `useSearchParams` / `useLocation` 的 `event_id`、`state.prefill` 预填逻辑保持不变。
- 删除不再使用的 `Link`、`ArrowLeft` 等 import。

**`review/EventStudyReviewPage.tsx` → 重命名 `review/ReviewTab.tsx`**（组件名 `ReviewTab`）：

- 删除 `<main>` 外层与 h1/描述；保留「待审核事件 / 影响结果确认」子 Tab 按钮行（第二级切换，语义不变）。
- 组件内既有 `type ReviewTab = 'pending' | 'impacts'` 重命名为 `ReviewSubTab`（组件改名 ReviewTab 后与子 Tab 类型撞名，避免 TS type/value 混淆）。
- 其余内容不变。

**路由 [frontend/src/routes/index.tsx](frontend/src/routes/index.tsx)**：

- 删除 `ai/event-study`、`ai/event-study/review` 两条路由及 `AiEventStudyPage`、`EventStudyReviewPage` 两个 lazy import。
- 新增：
  - `{ path: 'event-study', element: page(<EventStudyPage />), errorElement: <RouteErrorBoundary /> }`
  - `{ path: 'ai/event-study', element: <LegacyEventStudyRedirect /> }`（旧入口兼容重定向）
  - `{ path: 'ai/event-study/review', element: <Navigate to="/event-study?tab=review" replace /> }`（旧入口兼容重定向）
- 兼容重定向辅助组件（routes/ 内薄装配，无业务逻辑）：

  ```tsx
  // /ai/event-study 旧入口 → /event-study，透传 query（如 ?event_id=101）
  function LegacyEventStudyRedirect() {
    const location = useLocation();
    return <Navigate to={`/event-study${location.search}`} replace />;
  }
  ```

  `Navigate` 不会自动保留原 query，必须手动拼接 `location.search`；项目无 hash 路由使用，不需处理 hash。

**侧栏 [SidebarNav.tsx](frontend/src/app/components/SidebarNav.tsx)**：

- `NAV_ITEMS` 在 AI 之后追加 `{ to: '/event-study', label: '事件研究', icon: FlaskConical }`（import 自 lucide-react）。
- `end` 属性沿用现有逻辑 `end={to === '/ai'}`——事件研究不加 `end`，保证 `?tab=review`（及未来子路径）下高亮。

**AI 看板 [AiDashboardPage.tsx](frontend/src/modules/analysis/pages/dashboard/AiDashboardPage.tsx)**：

- 删除「事件研究」「事件审核」两个 Button 及 `FlaskConical`、`ClipboardCheck` import；保留「任务中心」。

**宏观信息面板 [MacroInformationPanel.tsx](frontend/src/modules/market/pages/MacroInformationPanel.tsx)**：

- 链接 `/ai/event-study` → `/event-study`（`?event_id=` 拼接逻辑不变）。

#### 3.1.2 三方依赖能力评估

- 无新增依赖。react-router `NavLink` 按 pathname 匹配（query 不影响 isActive）为既有版本能力；`Navigate replace` 重定向为标准能力。

#### 3.1.3 风险与验证方式

- **风险**：页面改名/重命名牵动 3 个测试文件的 import 与路径断言；hub 切换时丢参数（如 `event_id`）。
- **验证**：
  - 前端单测：`pnpm test`（新 EventStudyPage.test.tsx + 改名的两个测试文件 + MacroInformationPanel.test.tsx 路径断言更新）。
  - 人工检查：侧栏高亮、tab 切换 URL、旧路径重定向（`/ai/event-study?event_id=101` → `/event-study?event_id=101`、`/ai/event-study/review` → `/event-study?tab=review`）、从大盘宏观信息卡片跳转预填。

#### 3.1.4 文件变更清单

**新建文件**：

| 路径 | 说明 |
|------|------|
| `frontend/src/modules/event-study/pages/EventStudyPage.tsx` | hub 页：h1 + [影响预测/事件审核] 切换按钮 + 按 `?tab=` 渲染子 Tab |
| `frontend/src/modules/event-study/pages/PredictionTab.tsx` | 自 AiEventStudyPage.tsx 重构：去掉页面壳，保留表单/结果 + `event_id` 提示行 |
| `frontend/src/modules/event-study/pages/EventStudyPage.test.tsx` | hub 默认 tab、`?tab=review` 初始态、按钮切换、切换保留 event_id |
| `frontend/src/modules/event-study/pages/PredictionTab.test.tsx` | 自 AiEventStudyPage.test.tsx 改名（pathname 断言 `/ai/event-study`→`/event-study`） |
| `frontend/src/modules/event-study/pages/review/ReviewTab.tsx` | 自 EventStudyReviewPage.tsx 改名：去页面壳（h1/描述/main），子 Tab 类型改 ReviewSubTab |
| `frontend/src/modules/event-study/pages/review/ReviewTab.test.tsx` | 自 EventStudyReviewPage.test.tsx 改名（断言不变，仅 import 路径更新） |

**修改文件**：

| 路径 | 改动说明 |
|------|---------|
| `frontend/src/routes/index.tsx` | 删 2 条旧路由，加 hub 路由 + 兼容重定向 |
| `frontend/src/app/components/SidebarNav.tsx` | NAV_ITEMS 加「事件研究」 |
| `frontend/src/modules/analysis/pages/dashboard/AiDashboardPage.tsx` | 移除事件研究/事件审核两个入口按钮 |
| `frontend/src/modules/market/pages/MacroInformationPanel.tsx` | 链接改 `/event-study` |
| `frontend/src/modules/market/pages/MacroInformationPanel.test.tsx` | href 断言 `/ai/event-study`→`/event-study`（2 处） |

**删除文件**：

| 路径 | 原因 |
|------|------|
| `frontend/src/modules/event-study/pages/AiEventStudyPage.tsx` | 被 PredictionTab.tsx 替代 |
| `frontend/src/modules/event-study/pages/AiEventStudyPage.test.tsx` | 改名 PredictionTab.test.tsx |
| `frontend/src/modules/event-study/pages/review/EventStudyReviewPage.tsx` | 被 ReviewTab.tsx 替代（去页面壳） |
| `frontend/src/modules/event-study/pages/review/EventStudyReviewPage.test.tsx` | 改名 ReviewTab.test.tsx |

### 3.2 后端 refresh 端点

#### 3.2.1 模块设计

**契约 [backend/modules/event_study/application/review_contracts.py](backend/modules/event_study/application/review_contracts.py)**：

```python
@dataclass(frozen=True)
class RefreshResult:
    fetched: int               # 爬虫实际抓回条数（各源合并去重后）
    new_drafts: int            # 新写入 Redis 待审草稿数（PG + Redis 标题去重后）
    skipped_reason: str | None = None  # None=正常完成；"locked"=锁占用跳过；"failed"=采集/保存异常降级
```

**防腐层 [review_adapter.py](backend/modules/event_study/infrastructure/review_adapter.py)** 新增 4 个方法（函数级 import，与现有风格一致）：

```python
def fetch_latest_events(self) -> list[dict]:
    from AI.eventStudy.collectors import event_crawler
    return event_crawler.fetch_events_from_crawler()

def save_pending_events(self, events: list[dict], conn) -> list[int]:
    from AI.eventStudy.collectors import event_crawler
    return event_crawler.save_pending_events(events, conn=conn)

def try_acquire_refresh_lock(self, key: str, token: str, ttl_ms: int) -> bool:
    from AI.eventStudy.collectors.config import get_redis_client
    return bool(get_redis_client().set(key, token, nx=True, px=ttl_ms))

def release_refresh_lock(self, key: str, token: str) -> None:
    from AI.eventStudy.collectors.config import get_redis_client
    try:
        # Lua 比对删除：仅锁值仍为本方 token 时释放，防 TTL 过期后被后继请求占用时误删
        get_redis_client().eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1, key, token,
        )
    except Exception as e:
        logger.warning(f"拉取锁释放失败: {e}")
```

- 锁常量：`_REFRESH_LOCK_KEY = "events:refresh_lock"`、`_REFRESH_LOCK_TTL_MS = 120_000`（覆盖启用源串行抓取最坏约 5×15s=75s——财联社 v1 失败会回退 nodeapi 多一次 15s 请求——+ Redis 草稿去重扫描，余量充足）。
- 锁值携带随机 token（服务层每次调用 `uuid.uuid4().hex`），释放时 Lua 比对删除：即便极端慢网络下 TTL 过期、后继请求已占锁，前一次运行的 finally 也不会误删后继的锁。
- 锁只防**端点间**并发（自动触发 + 手动按钮连点）；daily_job 子进程不经端点，与其并发窗口极小（08:30 左右），标题去重兜底，风险接受。

**服务 [review_service.py](backend/modules/event_study/application/review_service.py)**：

```python
def refresh_events(self) -> RefreshResult:
    """采集各源最新事件写入待审草稿（审核页自动拉取）。

    失败语义：Redis 不可用 → 抛 503（与其余端点一致）；锁占用 →
    skipped_reason="locked"（另一拉取进行中，幂等跳过）；抓取/保存异常 →
    记 warning skipped_reason="failed"（列表仍可用）；正常完成 → None。
    """
    self._require_redis()
    token = uuid.uuid4().hex  # 每次调用独立 token：释放时 Lua 比对，防误删后继持锁方
    if not self._adapter.try_acquire_refresh_lock(_REFRESH_LOCK_KEY, token, _REFRESH_LOCK_TTL_MS):
        logger.info("最新事件拉取进行中（锁占用），本次跳过")
        return RefreshResult(fetched=0, new_drafts=0, skipped_reason="locked")
    try:
        events = self._adapter.fetch_latest_events()
        conn = self._adapter.open_connection()
        try:
            new_ids = self._adapter.save_pending_events(events, conn=conn)
        finally:
            conn.close()
        return RefreshResult(fetched=len(events), new_drafts=len(new_ids))
    except Exception as e:
        logger.warning("最新事件拉取失败: %s", e)
        return RefreshResult(fetched=0, new_drafts=0, skipped_reason="failed")
    finally:
        self._adapter.release_refresh_lock(_REFRESH_LOCK_KEY, token)
```

- 抓回 0 条时按正常完成处理（`skipped_reason=None`）：各源全挂时爬虫内部已逐源 warning 并返回空列表，服务层无法区分"无新事件"与"全源失败"，前端按"没有新事件"提示（爬虫日志可查实因）。
- **不含 AI 预填**（理由见"二、架构设计·数据流"段落）。

**Schema [backend/api/schemas/event_study_review.py](backend/api/schemas/event_study_review.py)**：

```python
class RefreshData(BaseModel):
    fetched: int
    new_drafts: int
    skipped_reason: Literal["locked", "failed"] | None = None
```

**路由 [backend/api/routers/event_study_review.py](backend/api/routers/event_study_review.py)**：

```python
@router.post("/refresh", response_model=Envelope[RefreshData])
async def refresh(request: Request, trace_id: str = Depends(ensure_trace_context)):
    service = _service(request)
    result = await request.app.state.analysis_services.run(service.refresh_events)
    data = RefreshData(fetched=result.fetched, new_drafts=result.new_drafts, skipped_reason=result.skipped_reason)
    return Envelope(data=data, meta=_meta(request)).model_dump()
```

- 无请求体；`tags=["event-studies"]` 不变（生成仍归 EventStudiesService）。
- 端点完成后必须 `python -m backend.scripts.export_openapi` + `pnpm run generate:api`（CLAUDE.md 踩坑：不重导出他人前端代码 typecheck 会失败）。

#### 3.2.2 三方依赖能力评估

- 全部复用 AI 侧既有函数（event_crawler / save_pending_events），无新增外部依赖；redis-py `SET NX PX` 为标准能力。
- **源开关依赖 env**：`CRAWLER_CONFIG` 中财联社/金十 2 主源默认启用（`CRAWLER_CLS_ENABLED` / `CRAWLER_JIN10_ENABLED` 默认 True），新浪/东财备源默认关闭（`CRAWLER_SINA_ENABLED` / `CRAWLER_EM_ENABLED` 默认 False，见 [collectors/config.py:185-208](AI/eventStudy/collectors/config.py#L185-L208)）。refresh 在 backend 进程内执行，实际采源取决于 **backend 进程 env**，需与 AI 侧配置一致（预期默认 2 主源）。

#### 3.2.3 风险与验证方式

- **风险**：与 daily_job 步骤 1 的并发重复写入（标题去重兜底、窗口极小）；锁 TTL 内进程被杀导致 2 分钟不可拉（TTL 自动过期恢复）；爬虫全源失败返回空列表被当成"没有新事件"；锁超时释放误删后继持锁方（已由 token + Lua 比对删除消除，Code Review 补强）。
- **验证**：
  - 单元：`pytest backend/tests/unit/event_study/test_review_service.py`（新增 refresh 用例，FakeAdapter 扩展 4 个方法）。
  - 契约：`pytest backend/tests/contract/api/test_event_study_review.py`（monkeypatch 爬虫 fetch，断言写入/去重/锁/降级，见下）。
  - 人工：真实环境 POST /refresh 后审核页出现最新事件（前置：确认 backend 进程 `CRAWLER_*` env 与 AI 侧一致，预期默认 2 主源）。

**契约测试新增用例（复用 `_review_test_env`，Redis 已指 db 11、events 表 DDL 已建）**：

1. `POST /refresh` 200：monkeypatch `AI.eventStudy.collectors.event_crawler.fetch_events_from_crawler` 返回 2 条 fixture 事件 → `{fetched:2, new_drafts:2, skipped_reason:null}`；`GET /pending-events` 含这 2 条草稿。
2. 再 `POST /refresh`（同 fixture）→ `{fetched:2, new_drafts:0, skipped_reason:null}`（Redis 草稿标题去重生效）。
3. 锁占用：先 `es_config.get_redis_client().set("events:refresh_lock", "1")` → POST 返回 `{fetched:0, new_drafts:0, skipped_reason:"locked"}` 且 fetch 未被调用；删除锁后 POST 恢复正常。
4. 采集异常降级：monkeypatch fetch 抛 RuntimeError → POST 返回 `{fetched:0, new_drafts:0, skipped_reason:"failed"}`（200，不 500）；锁已被释放（再 POST 可正常）。

**单元测试新增用例**：

- refresh 成功：FakeAdapter.fetch_latest_events 返回 2 条、save_pending_events 返回 [1,2] → `RefreshResult(2,2,None)`；conn 被 close。
- 锁占用：try_acquire_refresh_lock 返回 False → `RefreshResult(0,0,"locked")`，fetch/save 未调用。
- Redis 不可用 → `ReviewUpstreamUnavailableError`（对齐现有 503 语义）。
- fetch 抛异常 → `RefreshResult(0,0,"failed")`，锁已释放。
- save 抛异常 → `RefreshResult(0,0,"failed")`，conn 已 close、锁已释放。

#### 3.2.4 文件变更清单

**新建文件**：无（仅改既有文件）。

**修改文件**：

| 路径 | 改动说明 |
|------|---------|
| `backend/modules/event_study/application/review_contracts.py` | 加 `RefreshResult` frozen dataclass |
| `backend/modules/event_study/infrastructure/review_adapter.py` | 加 fetch_latest_events / save_pending_events / try_acquire_refresh_lock / release_refresh_lock |
| `backend/modules/event_study/application/review_service.py` | 加 `refresh_events()`（锁 + 降级语义） |
| `backend/api/schemas/event_study_review.py` | 加 `RefreshData` |
| `backend/api/routers/event_study_review.py` | 加 `POST /refresh` 端点 |
| `backend/tests/unit/event_study/test_review_service.py` | FakeAdapter 扩展 + 5 个新用例 |
| `backend/tests/contract/api/test_event_study_review.py` | 4 个新用例（采集/去重/锁/降级） |
| `backend/openapi/openapi.v1.json` | 重导出生成（脚本产出，不手改） |
| `docs/API契约.md` | ① 端点总表（28-33 行区）与端点明细表（593-598 行区）各加 refresh 一行；② 全局替换旧路径 `/ai/event-study` → `/event-study`（含 757 行 Q-03 卡片跳转描述） |

### 3.3 审核页自动拉取 + 自动 AI 预填

#### 3.3.1 模块设计

**查询层 [review/queries.ts](frontend/src/modules/event-study/pages/review/queries.ts)**：

```ts
export function useRefreshMutation() {
  return useMutation({
    mutationFn: async (): Promise<RefreshData> =>
      (
        await requestEnvelope<RefreshData>(
          EventStudiesService.refreshApiV1EventStudiesReviewRefreshPost(),  // 方法名以重生成结果为准
          { timeoutMs: REVIEW_TIMEOUT_MS },  // 300s，与其余审核 mutation 一致
        )
      ).data,
  });
}
```

**待审 Tab [PendingEventsTab.tsx](frontend/src/modules/event-study/pages/review/PendingEventsTab.tsx)**：

- 新增状态：`refreshMsg: string | null`、`refreshing: boolean`。
- **挂载自动拉取**（`useEffect(() => {...}, [])`，节流 30 分钟）：

```ts
const AUTO_REFRESH_THROTTLE_MS = 30 * 60 * 1000;
const AUTO_REFRESH_KEY = 'eventStudyReview.lastAutoRefresh';

useEffect(() => {
  const last = Number(sessionStorage.getItem(AUTO_REFRESH_KEY) || 0);
  if (Date.now() - last < AUTO_REFRESH_THROTTLE_MS) return;
  sessionStorage.setItem(AUTO_REFRESH_KEY, String(Date.now()));
  void runRefresh();
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);
```

  - 节流写在 `runRefresh` **之外**：手动按钮「🔄 重新拉取最新事件」直接调 `runRefresh()`，不受节流限制。
  - sessionStorage 保证同会话内审核页两个子 Tab 反复切换（重挂载）不重复采集；换页面/刷新会话后按时间戳判断。
- **runRefresh 流程**：

```ts
async function runRefresh() {
  setRefreshing(true);
  setRefreshMsg('正在拉取最新事件…');
  try {
    const r = await refreshMutation.mutateAsync();
    // skipped_reason 区分锁占用/降级失败，避免与"没有新事件"正常语义混用
    // 实测生成形态：pydantic Literal|None → anyOf，codegen 落为 union 类型
    // （'locked' | 'failed' | null，见生成 RefreshData.ts），非 enum namespace——
    // 字符串比较类型安全（与 ComputeData.status 非可选 Literal 生成 enum 不同）
    if (r.skipped_reason === 'locked') {
      setRefreshMsg('已有拉取正在进行中，本次跳过');
      return;
    }
    if (r.skipped_reason === 'failed') {
      setRefreshMsg('拉取失败，可稍后重试');
      return;
    }
    setRefreshMsg(r.new_drafts > 0 ? `新增 ${r.new_drafts} 条事件，开始 AI 预填…` : '没有新事件（各源最新快讯均已存在）');
    await queryClient.invalidateQueries({ queryKey: queryKeys.eventStudyReview.all });
    await runPrelabel();   // 复用现有 AI 预填循环（含 prelabeled===0 护栏与消息）
  } catch (err) {
    setRefreshMsg(`拉取失败：${toApiError(err).message}`);
  } finally {
    setRefreshing(false);
    void queryClient.invalidateQueries({ queryKey: queryKeys.eventStudyReview.all });
  }
}
```

  - `runPrelabel` 为现有方法原样复用（其 finally 已 invalidate）；自动流程与手动「🤖 AI 预填全部待审事件」按钮共享同一实现，行为一致。
- 按钮区：在「🤖 AI 预填全部待审事件」旁加「🔄 重新拉取最新事件」；`refreshing || prelabeling` 时两按钮均 disabled（与现有互斥语义一致）。

#### 3.3.2 三方依赖能力评估

- 无新增依赖；`sessionStorage` 为浏览器标准能力；生成 client 方法名由 openapi-typescript-codegen 按 operationId 派生（现有先例 `listPendingEventsApiV1EventStudiesReviewPendingEventsGet`），重生成后以实际签名消费。

#### 3.3.3 风险与验证方式

- **风险**：自动预填 LLM 慢（复用 300s 超时 + 护栏循环，已验证）；自动拉取与手动按钮连点并发（后端锁兜底）；测试间 sessionStorage 泄漏（beforeEach 清理）。
- **验证**：
  - 单测：`PendingEventsTab.test.tsx` 新增——挂载自动 refresh + 自动 prelabel（断言两个 mock 调用与消息文案）；节流命中跳过（预置 sessionStorage 时间戳）；手动按钮触发（不受节流）；refresh 请求 reject 失败提示；refresh 200 降级分支文案（`skipped_reason:"locked"` →「已有拉取正在进行中」、`"failed"` →「拉取失败，可稍后重试」，均不触发 prelabel）。mock 的 skipped_reason 用字符串字面量 `'locked'/'failed'`（实测 codegen 对 `Literal|None` 生成 union 类型而非 enum namespace）。
  - EventStudiesService 的 vi.mock 列表加 `refreshApiV1EventStudiesReviewRefreshPost`（3 个测试文件：PendingEventsTab / ReviewTab / EventStudyPage 相关 mock）。

#### 3.3.4 文件变更清单

**新建文件**：无。

**修改文件**：

| 路径 | 改动说明 |
|------|---------|
| `frontend/src/modules/event-study/pages/review/queries.ts` | 加 `useRefreshMutation` |
| `frontend/src/modules/event-study/pages/review/PendingEventsTab.tsx` | 挂载自动拉取（30 分钟节流）+ 手动刷新按钮 + 消息区 |
| `frontend/src/modules/event-study/pages/review/PendingEventsTab.test.tsx` | mock 补 refresh 方法 + 5 个新用例（自动拉取+预填 / 节流跳过 / 手动刷新 / 请求失败 / 200 降级文案） |
| `frontend/src/api/generated/` | `pnpm run generate:api` 重生成（产出，不手改） |

---

## 四、已确认决策 / 待确认问题

**已确认决策**（2026-09-08 与用户逐项确认）：

1. 新 tab 组织方式：**新建 hub 页内嵌两个 Tab**（影响预测 / 事件审核），tab 状态走 URL `?tab=`。
2. 拉取时机：**进入审核页自动触发 + 手动刷新按钮**（30 分钟节流）。
3. 历史缺口：**只拉最新，不回补** 8.19–9.7 事件（爬虫各源仅取最新 ~30 条）。
4. 每日调度器：**不在本次范围**，只做审核页按需拉取兜底。

**待确认问题**：无（方案已闭环）。
