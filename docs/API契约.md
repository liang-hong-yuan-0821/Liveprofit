# Liveprofit API 契约 v1（前端实现参照）

> **状态**：已冻结（2026-09-05；产品决策 P-1~P-9 与跨文档契约裁决 D-01~D-12 已确认；2026-09-06 增补 §3.5/3.6 执行调用日志端点，见 [任务执行调用日志方案](done/任务执行调用日志方案.md)）
> **对应文档**：[后端方案（§2.6 API 契约）](plans/后端方案.md)｜[产品需求分析](plans/产品需求分析.md)
> **说明**：本文档是前端实现的工作参照。后端导出 OpenAPI（`backend/openapi/openapi.v1.json`，T9）后，前端用 `openapi-typescript-codegen@0.29.0` 生成 TypeScript DTO/client，**不得手写后端领域类型**；本文档与 OpenAPI 冲突时以 OpenAPI 为准，发现冲突请反馈后端修正。
> **阶段标注**：「第一阶段」= 任务/看板/SSE/报告/事件研究（后端优先交付）；「第二阶段」= 市场读模型/宏观信息/自选/组合（前端可先按本文档做 Mock 与页面开发）。

---

## 〇、端点总览

| 领域 | 方法与路径 | 阶段 |
|------|-----------|------|
| 系统 | GET /health/live、GET /health/ready、GET /metrics | 第一阶段 |
| 分析任务 | POST /api/v1/analysis-tasks（Idempotency-Key 头，202） | 第一阶段 |
| 分析任务 | GET /api/v1/analysis-tasks?cursor=&limit=&status= | 第一阶段 |
| 分析任务 | GET /api/v1/analysis-tasks/{taskId} | 第一阶段 |
| 分析任务 | POST /api/v1/analysis-tasks/{taskId}/cancel | 第一阶段 |
| 分析任务 | DELETE /api/v1/analysis-tasks/{taskId}（仅终态） | 第一阶段 |
| 分析任务 | GET /api/v1/analysis-tasks/{taskId}/events（SSE） | 第一阶段 |
| 分析任务 | GET /api/v1/analysis-tasks/{taskId}/report | 第一阶段 |
| 分析任务 | GET /api/v1/analysis-tasks/{taskId}/execution-logs | 第一阶段（2026-09-06 增补） |
| 分析任务 | GET /api/v1/analysis-tasks/{taskId}/execution-logs/content?file= | 第一阶段（2026-09-06 增补） |
| 分析任务 | GET /api/v1/analysis-tasks/{taskId}/graph-topology | 第一阶段（2026-09-08 增补） |
| AI 看板 | GET /api/v1/analysis-dashboard | 第一阶段 |
| 事件研究 | POST /api/v1/event-studies/predictions | 第一阶段 |
| 事件研究 | GET /api/v1/event-studies/assets | 第一阶段 |
| 事件研究审核 | GET /api/v1/event-studies/review/pending-events | 第一阶段（2026-09-08 增补） |
| 事件研究审核 | POST /api/v1/event-studies/review/prelabel | 第一阶段（2026-09-08 增补） |
| 事件研究审核 | POST /api/v1/event-studies/review/batch | 第一阶段（2026-09-08 增补） |
| 事件研究审核 | POST /api/v1/event-studies/review/events/{event_id}/compute | 第一阶段（2026-09-08 增补） |
| 事件研究审核 | GET /api/v1/event-studies/review/impact-drafts | 第一阶段（2026-09-08 增补） |
| 事件研究审核 | POST /api/v1/event-studies/review/impact-drafts/{event_id}/confirm | 第一阶段（2026-09-08 增补） |
| 事件研究审核 | POST /api/v1/event-studies/review/refresh | 第一阶段（2026-09-08 增补） |
| 市场 | GET /api/v1/market-assets?enabled=true | 第二阶段 |
| 市场 | GET /api/v1/market-data/indices/{symbol}/bars | 第二阶段 |
| 市场 | GET /api/v1/market-data/concepts/hot | 第二阶段 |
| 信息 | GET /api/v1/macro-information | 第二阶段 |
| 自选 | /api/v1/watchlists 及其 items 子资源 | 第二阶段 |
| 组合 | /api/v1/portfolios 及其 positions 子资源 | 第二阶段 |

Base URL：前端经 Nginx 同源反代访问，业务路径即 `/api/v1/...`（本地 loopback，如 `http://127.0.0.1:3000/api/v1/...`）。

---

## 一、通用协议

### 1.1 响应信封（所有 /api/v1 业务接口）

```json
// 成功（含 POST/PATCH/PUT/DELETE；201/202 只表示 HTTP 语义，body 形状不变）：
{ "data": <对象>, "meta": { "request_id": "...", "schema_version": "v1" } }

// 列表：data 固定为对象 { "items": [...] }，续页游标只在 meta.next_cursor：
{ "data": { "items": [...] }, "meta": { "request_id": "...", "schema_version": "v1", "next_cursor": "opaque-or-null" } }

// 删除成功：200 + data 固定为 { "deleted": true, "resource_id": "<UUID>" }，不使用 204
```

- `/health/*` 与 `/metrics` 不属于业务 API，使用健康检查/Prometheus 标准格式。

### 1.2 错误响应（RFC 7807 Problem Details）

```json
{
  "type": "about:blank",
  "title": "Resource Not Found",
  "status": 404,
  "detail": "任务不存在",
  "code": "TASK_NOT_FOUND",
  "request_id": "...",
  "retryable": false
}
```

- `code` 稳定错误码（见下表）；`retryable=true` 才显示显式重试入口。
- 响应不泄露内部堆栈、Token、租约、artifact 路径或 Provider 原始异常。

**错误码总表（OpenAPI v1 冻结全集）**：

| code | HTTP | retryable | 场景与前端行为 |
|------|------|-----------|----------------|
| VALIDATION_ERROR | 422 | false | 通用参数/字段校验；字段级提示，保留输入 |
| RESOURCE_NOT_FOUND | 404 | false | 自选/组合等通用资源缺失；刷新父列表 |
| TASK_NOT_FOUND | 404 | false | 任务不存在；专用不存在态，返回 /ai |
| TASK_NOT_TERMINAL | 409 | false | 删除非终态任务被拒；提示先取消后再删除 |
| REPORT_NOT_FOUND | 404 | false | 报告不存在/尚无可用版本；不影响任务状态展示 |
| TASK_CREATE_INVALID | 422 | false | 任务创建参数/层级组合非法；字段级提示，不跳转 |
| IDEMPOTENCY_KEY_REUSED | 409 | false | 同幂等键但输入不一致；生成新键重新提交 |
| INVALID_TASK_FILTER | 422 | false | status 筛选值非法 |
| REVISION_CONFLICT | 409 | true | 自选/组合改名 expected_version 不匹配；重新拉取 |
| WATCHLIST_NAME_CONFLICT | 409 | false | 分组重名；保留输入就地提示 |
| WATCHLIST_ITEM_DUPLICATE | 409 | false | 同分组同标的；不插入本地乐观项 |
| WATCHLIST_NOT_EMPTY | 409 | false | 删除非空分组；提示先清空 |
| WATCHLIST_ITEM_ORDER_CONFLICT | 409 | true | 排序 revision/条目集合不一致；重新读取该分组 |
| TASK_STATE_CONFLICT | 409 | false | 任务状态前置不满足（事件/写入与当前状态冲突）；以详情 REST 为准 |
| TASK_LEASE_CONFLICT | 409 | false | attempt/租约已失效（迟到写入被 fencing 拒绝）；以详情 REST 为准 |
| PORTFOLIO_NAME_CONFLICT | 409 | false | 组合重名；保留输入就地提示 |
| PORTFOLIO_NOT_EMPTY | 409 | false | 删除非空组合；提示先删除持仓 |
| PORTFOLIO_POSITION_CONFLICT | 409 | true | 持仓 revision 冲突；重新读取 positions |
| INVALID_POSITION | 422 | false | quantity<=0、average_cost<0 或路径与 body 不一致 |
| INTERVAL_NOT_SUPPORTED | 422 | false | interval 不在资产白名单；提示该资产不支持 |
| RANGE_TOO_LARGE | 422 | false | 日期范围超窗；提示缩小范围，不静默截断 |
| ASSET_DISABLED | 409 | false | 资产未启用；不可用空态，不自动重试 |
| MARKET_DATA_UPSTREAM_UNAVAILABLE | 503 | true | 指数 K 线上游故障；可重试错误态 |
| HOT_CONCEPTS_UPSTREAM_UNAVAILABLE | 503 | true | 热点快照上游故障；不显示过期静态名单 |
| EVENT_STUDY_BUSY | 503 | true | 事件研究繁忙；仅手动重试 |
| EVENT_STUDY_TIMEOUT | 504 | true | 事件研究超时；仅手动重试 |
| EVENT_STUDY_INTERNAL | 500 | false | 事件研究内部失败；脱敏摘要 |
| REVIEW_DRAFT_NOT_FOUND | 404 | false | 待审草稿/影响草稿不存在或已过期；刷新列表后重试 |
| REVIEW_EVENT_NOT_FOUND | 404 | false | 补算目标事件不存在 |
| REVIEW_UPSTREAM_UNAVAILABLE | 503 | true | 审核草稿区（Redis）不可用；可重试错误态 |
| REVIEW_COMPUTE_FAILED | 500 | true | 补算执行异常；可重试 |
| INTERNAL_ERROR | 500 | false | 未分类内部错误；安全摘要 + request_id |

> 任务 DTO 的 `error_code` 是**任务级错误分类**（非 HTTP 错误码），首期取值包括：
> `PROVIDER_UNAVAILABLE` / `RATE_LIMITED` / `LEASE_EXPIRED` / `INPUT_INVALID` / `ANALYSIS_INTERNAL` / `INTERNAL_ERROR` 等，
> 由 Worker 在执行失败时写入；前端仅作展示用。

### 1.3 公共枚举

| 枚举 | 取值 | 说明 |
|------|------|------|
| market | `US` / `KR` / `CN` | 前端固定按 US → KR → CN 组序渲染，不允许扩展 |
| task_type | `SINGLE_STOCK` / `MARKET_WIDE` | 任务类型 |
| task_status | `PENDING` / `QUEUED` / `RUNNING` / `RETRYING` / `SUCCEEDED` / `FAILED` / `CANCELLED` / `CANCEL_REQUESTED` | 任务正式状态（详情接口唯一真相） |
| analysis_layer | `market` / `sector` / `stock` / `screening` / `position` | selected_layers 取值（与后端内核一致） |
| freshness_status | `FRESH` / `STALE` / `UNAVAILABLE` | 数据新鲜度：覆盖最近收盘日 / 落后但有快照 / 无可展示时序 |
| market_session_status | `OPEN` / `CLOSED` | 开闭市状态，与 freshness 正交；CLOSED 时附 market_closed_reason |
| report_block | `market` / `sector` / `stock` / `decision` | 报告区块标识 |
| block_status | `AVAILABLE` / `UNAVAILABLE` / `NOT_REQUESTED` | 报告区块三态 |
| interval | `"1d"`（首期） | K 线周期；取值受资产 supported_intervals 白名单约束 |
| window_type | `pre_event_5d` / `event_day` / `post_event_5d` | 事件研究预测窗口 |
| dashboard_kind | `FAILED_TASK` / `REPORT_SECTION_UNAVAILABLE` | 看板待处理事项类型 |

### 1.4 幂等键（Idempotency-Key）

- 创建任务请求**头**携带 `Idempotency-Key: <UUID>`（前端生成，1–128 个 URL-safe 字符）。
- 同一表单会话（未修改任何输入）的重复提交/网络超时重试**复用同一键** → 服务端返回同一任务（202）。
- 用户修改任一输入后重新提交 → **必须生成新键**。
- 同键不同输入 → 409 IDEMPOTENCY_KEY_REUSED。

### 1.5 Cursor 分页

- 列表请求可带 `cursor`（首次省略/传空）；响应只在 `meta.next_cursor` 返回不透明游标，`null` 表示无下一页（不显示"加载更多"）。
- 任务/自选/组合列表排序固定为 `updated_at DESC, id DESC`；标的列表 `display_order ASC, id ASC`；持仓 `market ASC, symbol ASC`。前端不得本地改序、不得用 offset/页码。

### 1.6 时间与日期格式

- datetime：RFC 3339（ISO 8601，UTC，如 `2026-09-05T09:10:00Z`）；date：`YYYY-MM-DD`。
- K 线 `from`/`to` 使用**该资产 market_timezone 的日历日期**；请求与响应均以服务端为准，前端不得按本地时区换算。

---

## 二、系统端点

### GET /health/live
进程存活。200 `{"status": "alive"}`。

### GET /health/ready
依赖就绪（PG + Redis + 必要配置，不调用 LLM）。
- 200 `{"status": "ready", "checks": {"postgres": "ok", "redis": "ok"}}`
- 503 `{"status": "not_ready", "checks": {...}}`

### GET /metrics
Prometheus 文本格式。前端无需消费。

---

## 三、分析任务 API

### 3.1 创建任务 POST /api/v1/analysis-tasks

请求头：`Idempotency-Key`（必填，见 1.4）。

请求体（Pydantic discriminated union）：

```json
// SINGLE_STOCK：ticker 必填；selected_layers 只允许 market/sector/stock 及其子集
{
  "task_type": "SINGLE_STOCK",
  "ticker": "000001.SZ",
  "requested_trade_date": "2026-09-04",
  "selected_layers": ["market", "sector", "stock"],
  "analysis_options": { "include_memory": true }
}

// MARKET_WIDE：禁止 ticker；selected_layers 为任意非空合法子集（2026-09-06 v3 放开组合约束）；
// position 仅可随 screening 出现
{
  "task_type": "MARKET_WIDE",
  "requested_trade_date": "2026-09-04",
  "selected_layers": ["market", "sector", "screening", "position"],
  "analysis_options": {}
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| task_type | enum | 是 | SINGLE_STOCK / MARKET_WIDE |
| ticker | string? | 单股必填 | 全市场禁止 |
| requested_trade_date | date | 是 | 用户请求交易日 |
| selected_layers | string[] | 是 | 任意非空合法子集；position 仅随 screening；服务层复验 |
| analysis_options | object? | 否 | 接口定义的其余选项 |

成功响应 **202**：

```json
{
  "data": {
    "task_id": "b1f9b8e0-fd26-4bc8-91fd-1b2fbe53780c",
    "status": "PENDING",
    "requested_trade_date": "2026-09-04",
    "effective_trade_date": "2026-09-03",
    "date_correction": "2026-09-04 → 2026-09-03",
    "events_url": "/api/v1/analysis-tasks/b1f9b8e0-fd26-4bc8-91fd-1b2fbe53780c/events",
    "report_url": "/api/v1/analysis-tasks/b1f9b8e0-fd26-4bc8-91fd-1b2fbe53780c/report"
  },
  "meta": { "request_id": "...", "schema_version": "v1" }
}
```

错误：422 TASK_CREATE_INVALID；409 IDEMPOTENCY_KEY_REUSED。创建成功后前端**立即跳转** `/ai/tasks/:taskId`。

### 3.2 任务列表 GET /api/v1/analysis-tasks

Query：`cursor`（可选）、`limit`（可选，默认 20，范围 1..100）、`status`（可选，默认 `all`）。

| status 取值 | 映射 |
|-------------|------|
| all（默认） | 全部状态 |
| active | PENDING / QUEUED / RUNNING / RETRYING / CANCEL_REQUESTED |
| succeeded | SUCCEEDED |
| failed | FAILED |
| cancelled | CANCELLED |

排序固定 `updated_at DESC, id DESC`。成功响应：

```json
{
  "data": {
    "items": [
      {
        "id": "b1f9b8e0-fd26-4bc8-91fd-1b2fbe53780c",
        "task_type": "SINGLE_STOCK",
        "ticker": "000001.SZ",
        "effective_trade_date": "2026-09-03",
        "status": "FAILED",
        "attempt_no": 2,
        "error_code": "PROVIDER_UNAVAILABLE",
        "error_summary": "行情数据暂不可用",
        "created_at": "2026-09-05T09:10:00Z",
        "updated_at": "2026-09-05T09:15:00Z"
      }
    ]
  },
  "meta": { "request_id": "...", "schema_version": "v1", "next_cursor": "opaque-or-null" }
}
```

TaskListItemDTO 字段：`id: UUID、task_type、ticker?: string、effective_trade_date?: date、status、attempt_no: int、error_code?: string、error_summary?: string、created_at、updated_at`。列表不返回请求参数、幂等键、租约、Outbox、事件历史、artifact 路径、报告正文。

错误：422 INVALID_TASK_FILTER（status 非法）。

### 3.3 任务详情 GET /api/v1/analysis-tasks/{taskId}

成功 200，data 为 TaskDTO：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 任务 ID |
| task_type | enum | SINGLE_STOCK / MARKET_WIDE |
| ticker | string? | 目标标的 |
| requested_trade_date | date? | 用户请求日期 |
| effective_trade_date | date? | 服务端有效交易日 |
| date_correction | string? | 校正说明（无校正为 null，不夸大显示） |
| selected_layers | string[] | 分析层级 |
| status | enum | 正式状态（**页面判断的唯一依据**；RETRYING/attempt_no/next_retry_at 是展示重投过程的唯一来源） |
| attempt_no | int | 当前尝试次数 |
| next_retry_at | datetime? | 仅 RETRYING 且可给出时返回 |
| error_code | string? | 终态错误码（脱敏） |
| error_summary | string? | 终态错误摘要（脱敏） |
| created_at / updated_at | datetime | — |
| events_url | string | 服务端派生，恒等于 `/api/v1/analysis-tasks/{id}/events` |
| report_url | string | 服务端派生，恒等于 `/api/v1/analysis-tasks/{id}/report` |
| execution_logs_url | string | 服务端派生，恒等于 `/api/v1/analysis-tasks/{id}/execution-logs` |
| graph_topology_url | string | 服务端派生，恒等于 `/api/v1/analysis-tasks/{id}/graph-topology` |

前端在 `/ai/tasks/:taskId` 先获取该 DTO；**仅在任务非终态且 events_url 严格等于上述 canonical 规则时建立 SSE**，否则视为协议错误只做 REST 轮询（5s 一次直至终态）。

错误：404 TASK_NOT_FOUND。

### 3.4 取消任务 POST /api/v1/analysis-tasks/{taskId}/cancel

无 body。200 + envelope，data 为最新 TaskDTO（取消是协作式：PENDING/QUEUED/RETRYING 直接收口 CANCELLED；RUNNING 置 CANCEL_REQUESTED 等待 Worker 收口；重复取消幂等返回当前状态）。点击后显示"取消请求中"，最终以 TaskDTO 正式状态为准；不能承诺立即停止在途 LLM 调用。

错误：404 TASK_NOT_FOUND。

### 3.5 执行调用日志 GET /api/v1/analysis-tasks/{taskId}/execution-logs

200 + envelope，data 为 ExecutionLogsDTO：任务日志目录（`{execution_logs_root}/tasks/{taskId}/{attempt_no}/`，按任务**当前** attempt_no 定位）的完整树——layer（market/sector/stock/screening 固定执行序）→ LLM 节点 → DP 调用（req/res + tushare 端点子调用）→ 工具调用 → LLM 提示词/输出 → meta，字段结构见 OpenAPI（ExecutionLogsDTO/ExecutionLayerDTO/ExecutionNodeDTO/ExecutionDpCallDTO/ExecutionTushareDTO/ExecutionToolDTO/ExecutionFileDTO）。

要点：

- 目录不存在 → 200 + `available=false`（PENDING 尚未建目录是正常态，不是错误）；任务不存在 → 404 TASK_NOT_FOUND。
- 内容两级上限：单文件 >100KB → `content=null, truncated=true, total_bytes=实际大小`；整树聚合 >5MB → 树序靠后的内容文件统一 truncated。truncated 语义统一为"内容未内嵌，可经 content 端点拉全量"；req.json/meta.json 等裸 dict 字段不参与树级截断。
- 缺失文件对应字段为 null（运行中未写完是正常态）；`parse_error=true` 表示坏 JSON/半写文件（可经 content 端点查看原文）。
- 运行中每 5s 轮询"生长"，终态停止（终态回看静态可用）；详情页在 TaskDTO 状态由非终态翻转为终态的瞬间补拉一次（防尾部 ≤5s 日志丢失）。

### 3.6 执行调用日志内容 GET /api/v1/analysis-tasks/{taskId}/execution-logs/content?file={relpath}

截断文件/解析失败文件的"查看完整内容"端点。200 + envelope，data 为 ExecutionFileDTO（content 全量、truncated=false）。

- `file` 必须为 `/` 分隔的相对任务日志目录路径、扩展名 ∈ {.json,.md,.txt}、resolve 后位于任务日志目录内（双保险防路径逃逸）；任一失败 → 422 VALIDATION_ERROR。
- 单文件上限 10MB（超限 → 422）；文件不存在 → 404 RESOURCE_NOT_FOUND；任务不存在 → 404 TASK_NOT_FOUND。

### 3.7 图拓扑 GET /api/v1/analysis-tasks/{taskId}/graph-topology（2026-09-08 增补）

200 + envelope，data 为 GraphTopologyDTO：静态图拓扑（LangGraph 图定义提取，三层 + 层内主节点，tools_*/Msg Clear 辅助节点折叠）+ 本次运行状态叠加，字段结构见 OpenAPI（TopologyNodeDTO/TopologyEdgeDTO，状态枚举 not_executed/executed/running/error，边 kind direct/conditional/loop）。

要点：

- 静态拓扑来源是图定义本身（`AI/graph/topology.py` dummy 编译三层子图，lru_cache），不手写第二份拓扑表；screening 任务 stock 层恒出现（逐票循环复用 stock 子图，与 selected_layers 是否含 "stock" 无关），以 `kind="loop"` 虚线边挂 Screening 之后。
- 运行状态按日志目录叠加：节点目录分 LLM 目录（含节点级 meta.json）与预测目录（dataprovider_log 为纯代码节点按 llm_seq+1 预测创建，两者 seq 可能并列）——running/error 兜底只作用于 LLM 目录；error = 任一 DP meta.error 或（FAILED 终态 + 全局最大 seq 的 LLM 目录无 res.md）；未匹配拓扑节点的目录忽略。
- 目录不存在 → 200 + `available=false`（nodes 仍为静态结构、全 not_executed）；任务不存在 → 404 TASK_NOT_FOUND。
- 运行中每 5s 轮询，终态停止；详情页终态翻转瞬间与 execution-logs 一起补拉一次（防尾部滞留）。前端点击节点弹面板展示该节点日志（复用 execution-logs 同 query 缓存，零额外内容端点）。

---

## 四、AI 投研看板 GET /api/v1/analysis-dashboard

只读聚合投影：不返回 SSE 事件、任务请求参数、租约/Outbox、artifact 路径或报告全文。三个数组上限固定 pending_actions=10、active_tasks=10、recent_conclusions=5，均按 updated_at DESC（结论按 completed_at DESC）稳定排序，空数组是正常结果。

```json
{
  "data": {
    "pending_actions": [
      {
        "kind": "FAILED_TASK",
        "task_id": "b1f9...",
        "task_type": "SINGLE_STOCK",
        "ticker": "000001.SZ",
        "effective_trade_date": "2026-09-03",
        "updated_at": "2026-09-05T09:15:00Z",
        "error_code": "PROVIDER_UNAVAILABLE",
        "error_summary": "行情数据暂不可用",
        "unavailable_blocks": null,
        "retryable": false
      },
      {
        "kind": "REPORT_SECTION_UNAVAILABLE",
        "task_id": "c2a0...",
        "task_type": "MARKET_WIDE",
        "ticker": null,
        "effective_trade_date": "2026-09-03",
        "updated_at": "2026-09-05T08:00:00Z",
        "error_code": null,
        "error_summary": null,
        "unavailable_blocks": [
          { "block": "sector", "reason": "数据源失败", "retryable": true }
        ],
        "retryable": true
      }
    ],
    "active_tasks": [
      {
        "task_id": "d3b1...",
        "task_type": "SINGLE_STOCK",
        "ticker": "600000.SH",
        "effective_trade_date": "2026-09-04",
        "status": "RETRYING",
        "attempt_no": 2,
        "updated_at": "2026-09-05T09:30:00Z",
        "next_retry_at": "2026-09-05T09:32:00Z"
      }
    ],
    "recent_conclusions": [
      {
        "task_id": "e4c2...",
        "task_type": "SINGLE_STOCK",
        "ticker": "000858.SZ",
        "effective_trade_date": "2026-09-04",
        "completed_at": "2026-09-05T09:05:00Z",
        "conclusion_summary": "估值偏低，情绪面偏多，维持关注",
        "risk_flag": true,
        "risk_hint": "注意流动性风险",
        "has_report": true,
        "updated_at": "2026-09-05T09:05:00Z"
      }
    ],
    "generated_at": "2026-09-05T10:00:00Z"
  },
  "meta": { "request_id": "...", "schema_version": "v1" }
}
```

字段语义：

| 数组 | 元素字段 | 语义 |
|------|----------|------|
| pending_actions | kind、task_id、task_type、ticker?、effective_trade_date?、updated_at、error_code、error_summary、unavailable_blocks?、retryable | kind=FAILED_TASK 时 error_code/error_summary 必填、unavailable_blocks 为 null（retryable 恒 false，终态）；kind=REPORT_SECTION_UNAVAILABLE 时 unavailable_blocks 非空（同任务多区块聚合为一条）、error 字段为 null、retryable 取区块并集。RETRYING/CANCELLED/NOT_REQUESTED/字段缺失不进待处理 |
| active_tasks | task_id、task_type、ticker?、effective_trade_date?、status（仅 PENDING/QUEUED/RUNNING/RETRYING）、attempt_no、updated_at、next_retry_at? | 不带阶段日志；进度仅由详情 SSE 提供；看板不建立 SSE |
| recent_conclusions | task_id、task_type、ticker?、effective_trade_date?、completed_at、conclusion_summary?（≤200 字）、risk_flag: bool、risk_hint?、has_report: bool、updated_at | 只包含具备可阅读报告的 SUCCEEDED 任务；conclusion_summary 为 null 时只展示任务元信息 + "查看完整报告"入口，**不得截取报告正文兜底**；has_report=false 不带 #report 锚点 |

---

## 五、SSE 事件流 GET /api/v1/analysis-tasks/{taskId}/events

**协议**：标准 SSE wire 格式。业务帧 `id/event/data`；控制帧只发 `event/data` 无 id。

**事件白名单（严格仅此 8 种，其他值均为协议错误，前端应立即断开连接并转 REST 轮询）**：

| event | 持久化 | data 必含字段 | 事件专属字段 |
|-------|--------|---------------|--------------|
| queued | 是（Redis Stream，有 id） | task_id、attempt_no、occurred_at、schema_version:"v1" | — |
| started | 是 | 同上 | worker_id |
| progress | 是 | 同上 | sequence（严格递增 int）、phase（market/sector/stock/decision 等稳定阶段）、message |
| completed | 是 | 同上 | report_id、duration_ms |
| failed | 是 | 同上 | error_code、message（**不可含 retryable=true**；仅数据库 FAILED 提交后发送） |
| cancelled | 是 | 同上 | message |
| reset（控制） | 否，无 id | — | 固定仅 {task_url, earliest_event_id, occurred_at, schema_version:"v1"}；发送一次后**关闭连接** |
| heartbeat（控制） | 否，无 id | — | 固定仅 {connection_id, sent_at, schema_version:"v1"}；空闲保活约 30s 一条 |

示例帧：

```
id: 42-0
event: progress
data: {"task_id":"...","attempt_no":1,"occurred_at":"2026-09-05T09:12:01Z","schema_version":"v1","sequence":3,"phase":"sector","message":"板块层分析完成"}

event: heartbeat
data: {"connection_id":"...","sent_at":"2026-09-05T09:12:30Z","schema_version":"v1"}
```

**前端处理规则**：

1. 游标：连接时用 `?after=<lastEventId>`；服务端优先级 Last-Event-ID 头 > after 参数 > 0-0（从头回放）。前端统一映射事件为 `{ id: lastEventId, eventType: eventName, payload: JSON.parse(data) }`。
2. 去重/乱序：仅接受 Stream ID 严格递增的业务事件；重复丢弃、旧事件不改状态。
3. 合法性：task_id 与 URL 不匹配、schema_version 不支持、结构不合法 → **立即断开且不写入时间线/游标**，重新读任务，未终态转 REST 轮询。
4. reset：收到后清空本地进度窗口，重新读任务；未终态转 REST 轮询（不把残缺历史当完整记录）。
5. 重连：指数退避 1s/2s/4s/8s…（上限 30s）；连续 5 次失败转 5s REST 轮询直至终态。心跳超时判定 45s 无任何事件按断连处理。
6. 时间线最多保留 50 条（超出丢弃最早）；任务终态或离开页面/切换任务即停止全部连接。
7. RETRYING **没有** SSE 事件：它只经详情 REST 的 status/attempt_no/next_retry_at 呈现；新 attempt 的 Outbox 确认投递后才以新 attempt_no 的 queued 重新进入流。
8. 终态收口：任务已终态但末事件缺失时以 REST 正式状态为准。

---

## 六、报告 GET /api/v1/analysis-tasks/{taskId}/report

只读**最新成功版本**，无版本 Query 参数（前端不暴露版本选择器）。仅 SUCCEEDED 任务返回报告；运行中/失败/取消任务不显示伪造报告空态。

```json
{
  "data": {
    "schema_version": "v1",
    "report_version": 1,
    "generated_at": "2026-09-05T09:05:00Z",
    "task": {
      "task_id": "...",
      "task_type": "SINGLE_STOCK",
      "ticker": "000858.SZ",
      "effective_trade_date": "2026-09-04",
      "duration_ms": 152000
    },
    "sections": [
      {
        "block": "market",
        "status": "AVAILABLE",
        "title": "市场环境",
        "summary": "全球风险偏好回暖…",
        "content": "…结构化正文（长文本按需展开）…",
        "charts": null,
        "unavailable_reason": null,
        "retryable": null
      },
      {
        "block": "sector",
        "status": "UNAVAILABLE",
        "title": "板块分析",
        "summary": null,
        "content": null,
        "charts": null,
        "unavailable_reason": "数据源失败",
        "retryable": true
      },
      {
        "block": "stock",
        "status": "NOT_REQUESTED",
        "title": "个股研究",
        "summary": null,
        "content": null,
        "charts": null,
        "unavailable_reason": null,
        "retryable": null
      },
      {
        "block": "decision",
        "status": "AVAILABLE",
        "title": "交易决策",
        "summary": "建议关注…",
        "content": "…含 final_position_plan 展示…",
        "charts": null,
        "unavailable_reason": null,
        "retryable": null
      }
    ],
    "data_sources": [
      { "label": "行情", "source": "tushare", "as_of": "2026-09-04" }
    ],
    "risk_note": "以上分析仅供参考，不构成投资建议。"
  },
  "meta": { "request_id": "...", "schema_version": "v1" }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| schema_version / report_version | string / int | 协议与报告版本（report_version 仅用于可追溯） |
| generated_at | datetime | 报告生成时间 |
| task | object | 任务元信息（task_id/task_type/ticker?/effective_trade_date?/duration_ms?） |
| sections[] | array | 固定顺序 market → sector → stock → decision |
| sections[].block | enum | market/sector/stock/decision |
| sections[].status | enum | AVAILABLE / UNAVAILABLE / NOT_REQUESTED（**三态显式区分**，不得由字段缺失推断） |
| sections[].title / summary / content | string? | 区块标题/摘要/结构化正文 |
| sections[].charts | array? | 图表数据（通用图表渲染） |
| sections[].unavailable_reason / retryable | string? / bool? | UNAVAILABLE 时的原因与可重试提示 |
| data_sources | array? | [{label, source, as_of}] 数据来源说明 |
| risk_note | string? | 风险说明（脱敏后） |

前端展示约定（Q-06 已确认）：**decision 区块默认展开**，market/sector/stock 默认折叠（折叠态显示 summary，无 summary 则正文首段约 120 字符预览 + "展开全文"）；图表随区块展开/收起。

错误：404 REPORT_NOT_FOUND（不影响任务状态展示）。

---

## 七、事件研究 API

### 7.1 预测 POST /api/v1/event-studies/predictions

同步、有容量限制的一次性操作（不创建分析任务、不进任务进度时间线）。请求体：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| event_text | string | 是 | — | 1..20000 字符 |
| asset_ticker | string | 是 | — | 目标资产代码，服务端校验 |
| window_type | enum | 否 | post_event_5d | pre_event_5d / event_day / post_event_5d |
| event_type | string? | 否 | — | 事件类型标签 |
| event_subtype | string? | 否 | — | 事件子类型标签 |
| event_condition | string? | 否 | — | 关键条件 |
| save | bool | 否 | false | true 时预测落库供追踪（首期露出"保存本次预测"开关，默认关） |
| event_id | int? | 否 | — | 关联事件 ID（save=true 时可选） |

成功 200：

```json
{
  "data": {
    "prediction": {
      "direction": "up",
      "predicted_return": 0.023,
      "confidence": 0.61
    },
    "template_stats": { "sample_count": 12, "avg_car": 0.018, "win_rate": 0.58 },
    "supplement_events": [
      { "event_id": 101, "title": "…", "similarity": 0.87, "weight": 0.31 }
    ],
    "note": null
  },
  "meta": { "request_id": "...", "schema_version": "v1" }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| prediction.direction | enum | up / down / neutral（服务端判定，前端仅映射文案） |
| prediction.predicted_return | number? | 预测收益（CAR）；无样本为 null |
| prediction.confidence | number | 0~1 |
| template_stats.sample_count / avg_car? / win_rate? | int / number? / number? | 模板匹配统计；无样本时 avg_car/win_rate 为 null |
| supplement_events | array | 相似历史事件 [{event_id, title, similarity, weight}] |
| note | string? | 服务端说明（如"事件库暂无相似事件"），原样展示 |

错误：422 VALIDATION_ERROR（窗口非法/文本超长，字段级提示）；503 EVENT_STUDY_BUSY（retryable=true，仅手动重试）；504 EVENT_STUDY_TIMEOUT（retryable=true，不能暗示后端计算已停止）；500 EVENT_STUDY_INTERNAL。前端请求超时建议 45s（大于服务端 30s 超时，优先收到 504）。

### 7.2 资产清单 GET /api/v1/event-studies/assets

200 + `data.items: [{ticker, name, market}]`（首期为事件研究系统已初始化的 4 个 CN 指数：000001.SH 上证指数 / 000688.SH 科创50 / 000698.SH 科创100 / 000300.SH 沪深300）。供资产下拉选项；接口缺失时前端保留自由输入，**不硬编码名单**。

### 7.3 审核 API（2026-09-08 增补，平台集成版替代 Streamlit review_app）

行为对齐原 Streamlit 审核界面；草稿存 Redis（`events:pending:{draft_id}` 30 天 / `event_impacts:draft:{event_id}` 7 天），审核终态写 PG `events`（approved/ignored）与 `event_impacts`，审核日志 LPUSH Redis `event_review_log`。设计细节见 docs/done/事件研究审核界面平台集成方案.md。

| Method/Path | 请求体 | 响应 data |
|---|---|---|
| GET /api/v1/event-studies/review/pending-events | — | `items: [{draft_id, title, announced_at, source, content, source_url, importance_hint, ai_suggestions}]`（announced_at 降序全量；Redis 不可用 503） |
| POST /api/v1/event-studies/review/prelabel | `{limit: 1..200 = 50}` | `{prelabeled, remaining}`（幂等：仅预填无 ai_suggestions 的草稿；remaining=执行后仍无建议数） |
| POST /api/v1/event-studies/review/batch | `{items: [1..50]}`，每行 `{draft_id, action: approve\|ignore, event_type?, event_subtype?, event_condition?, importance? 1..5, expected_value?, actual_value?, previous_value?, operator? = "admin"}` | `{results: [{draft_id, ok, event_id?, error_code?, error_message?, compute_status?}], summary: {approved, ignored, computed}}`（行级失败不失败整批：`REVIEW_DRAFT_NOT_FOUND` 草稿缺失 / `REVIEW_ROW_FAILED` 其他行级错误；approve 成功即内联计算影响，失败仅 `compute_status="failed"`，daily_job 兜底） |
| POST /api/v1/event-studies/review/events/{event_id}/compute | `{operator? = "admin"}` | `{event_id, status: ok\|failed, message?}`（补算覆盖草稿刷新 TTL；事件不存在 404；全窗口失败折叠为 200 failed） |
| GET /api/v1/event-studies/review/impact-drafts | — | `items: [{event_id, title, t0, computed_at, assets: {ticker: {window_type: {...}}}}]`（含事件标题 join） |
| POST /api/v1/event-studies/review/impact-drafts/{event_id}/confirm | `{tickers: [1..], operator? = "admin"}` | `{event_id, inserted}`（仅正常窗口落表，`ON CONFLICT DO NOTHING`；草稿不存在 404；ticker 缺失静默跳过） |
| POST /api/v1/event-studies/review/refresh | —（无请求体） | `{fetched, new_drafts, skipped_reason: "locked"\|"failed"\|null}`（采集各启用源最新事件写入待审草稿：PG + Redis 标题去重；Redis 锁防并发，锁占用 → skipped_reason="locked"，采集异常 → "failed"，均 200 降级不报错；Redis 不可用 503；不含 AI 预填，预填走 /prelabel） |

- 审核字段省略键 = 委托 review_dao 默认链（importance → importance_hint 或 3；数值键 → 落 NULL，0 是合法值）。
- 无认证；operator 仅写入审核日志（追溯保险），默认 "admin"。

---

## 八、市场数据 API（第二阶段）

### 8.1 资产目录 GET /api/v1/market-assets?enabled=true

成功 200：

```json
{
  "data": {
    "items": [
      {
        "market": "US",
        "symbol": ".INX",
        "name": "标普500",
        "currency": "USD",
        "market_timezone": "America/New_York",
        "display_order": 10,
        "enabled": true,
        "supported_intervals": ["1d"],
        "availability_status": "AVAILABLE"
      }
    ]
  },
  "meta": { "request_id": "...", "schema_version": "v1" }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| market | enum | US / KR / CN |
| symbol | string | 交易代码（如 000001.SH / .INX / KOSPI） |
| name | string | 指数名称 |
| currency | string | USD / KRW / CNY |
| market_timezone | string | 市场时区（如 Asia/Shanghai） |
| display_order | int | 服务端维护的组内顺序 |
| enabled | bool | 是否启用 |
| supported_intervals | string[] | 可请求 interval 唯一白名单（首期 ["1d"]） |
| availability_status | enum | AVAILABLE / DISABLED / UNAVAILABLE |

前端渲染规则：平铺 items 按**固定组序 US → KR → CN** 分组、组内按 display_order ASC（不得本地维护名单/排序）；enabled=false 不在默认清单（管理页读全量时显示"该资产未启用"且不请求 bars）；UNAVAILABLE 表示未通过实测验收，不请求 bars、不伪造数据。某市场无可展示资产显示该组正常空态。

### 8.2 指数 K 线 GET /api/v1/market-data/indices/{symbol}/bars

Query 全部必填：`market`（US/KR/CN）、`interval`（目录白名单）、`from`、`to`（该资产 market_timezone 的日历日期，from ≤ to，单次窗口 ≤ 365 自然日）。`symbol` 为 URL 编码的标的代码。

成功 200：

```json
{
  "data": {
    "asset": {
      "market": "CN", "symbol": "000001.SH", "name": "上证综指",
      "currency": "CNY", "market_timezone": "Asia/Shanghai", "supported_intervals": ["1d"]
    },
    "interval": "1d",
    "from": "2026-08-01",
    "to": "2026-09-04",
    "bars": [
      { "timestamp": "2026-08-03T00:00:00Z", "open": 3352.1, "high": 3368.5, "low": 3341.2, "close": 3360.4, "volume": 421000000 }
    ],
    "indicators": {
      "ma": [
        { "period": 5,  "values": [null, null, null, null, 3348.1, 3350.2] },
        { "period": 10, "values": [null, null, null, null, null, 3348.9] },
        { "period": 20, "values": [null, null, null, null, null, null] },
        { "period": 60, "values": [null, null, null, null, null, null] }
      ],
      "boll": { "period": 20, "k": 2.0, "mid": [null, null, null, null, null, null], "upper": [null, null, null, null, null, null], "lower": [null, null, null, null, null, null] }
    },
    "source": "tushare",
    "as_of": "2026-09-04",
    "source_updated_at": "2026-09-04T12:30:00Z",
    "freshness_status": "STALE",
    "market_session_status": "CLOSED",
    "market_closed_reason": "已收盘"
  },
  "meta": { "request_id": "...", "schema_version": "v1" }
}
```

- bars 按 timestamp 升序；`volume` 可选（无成交量数据整体省略）。
- `indicators`（可选，bars 为空时整个字段为 null）：后端计算的 MA5/10/20/60 均线与 BOLL(20,2) 布林带。各数组**与 bars 等长、按 index 对齐**，窗口不足处为 null；后端补窗口计算（区间前多取 120 自然日，覆盖长假休市）保证区间起点处指标即有值（资产历史不足除外）。前端遇无该字段的旧后端响应应降级渲染纯 K 线。上方 JSON 为片段示意：实际响应中 indicators 各数组与 bars 严格等长（示例省略了其余 bars 与指标值）。
- freshness_status 与 market_session_status **正交**：休市不是错误——返回 200 + 最近闭市 bars 并以 CLOSED + market_closed_reason 提示；休市日的收盘数据可以是 FRESH（覆盖最近收盘日）。FRESH 正常展示；STALE 保留最近成功快照 + "数据可能延迟"标注；UNAVAILABLE 无可展示时序。
- 错误：422 INTERVAL_NOT_SUPPORTED；422 RANGE_TOO_LARGE；409 ASSET_DISABLED（不可用空态，不自动/反复请求）；503 MARKET_DATA_UPSTREAM_UNAVAILABLE（可重试错误态）。前端不得依据字段缺失猜测状态。

### 8.3 热门概念 GET /api/v1/market-data/concepts/hot

Query：`market`（首期仅 CN）、`interval`、`from`、`to`、`limit`（必填，上限 30）；`as_of`（可选，省略取最新已完成快照）。首期 top_n≤30 全量返回，无 cursor 分页（meta.next_cursor 恒 null）。

成功 200：

```json
{
  "data": {
    "as_of": "2026-09-04",
    "algorithm_version": "heat_v1",
    "result_status": "OK",
    "items": [
      {
        "concept_code": "BK1036",
        "concept_name": "半导体",
        "rank": 1,
        "hotness_reason": "区间涨幅居前且成交量显著放大",
        "period_return": 6.32,
        "daily_changes": [ { "date": "2026-08-24", "change_pct": 1.2 } ],
        "updated_at": "2026-09-04T18:00:00Z",
        "bars": [ { "timestamp": "2026-08-03T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": null } ]
      }
    ],
    "source": "akshare",
    "source_updated_at": "2026-09-04T18:00:00Z",
    "freshness_status": "FRESH"
  },
  "meta": { "request_id": "...", "schema_version": "v1", "next_cursor": "opaque-or-null" }
}
```

- items 按 rank ASC；`hotness_reason`、`rank`、`algorithm_version` 是唯一热点解释来源，前端不自行计算。
- `daily_changes`（近 10 个交易日逐日涨跌幅）供卡片展开；bars 与请求 interval/range 对齐，不可用时卡片显示不可用状态。
- **无热点是正常业务态**：200 + `items: []` + `result_status: "NO_HOT_CONCEPTS"`，显示"当前条件下暂无热点概念"，不是错误。
- STALE 可展示旧快照并标注快照日期与延迟；上游失败 503 HOT_CONCEPTS_UPSTREAM_UNAVAILABLE（不显示过期静态概念清单）。

### 8.4 宏观信息 GET /api/v1/macro-information

Query：`limit`（必填，受上限约束）、`cursor`（可选）、`market`（可选）、`topic`（可选）。仅返回 review_status=APPROVED 的投影，按 occurred_at DESC。

成功 200：

```json
{
  "data": {
    "items": [
      {
        "id": "f5d3...",
        "event_id": 101,
        "title": "…",
        "occurred_at": "2026-09-03T02:00:00Z",
        "market_tags": ["CN"],
        "macro_topic": "货币政策",
        "summary": "…",
        "source": "…",
        "related_assets": ["000001.SH"],
        "research_status": "已审核"
      }
    ]
  },
  "meta": { "request_id": "...", "schema_version": "v1", "next_cursor": "opaque-or-null" }
}
```

空列表为正常空态（"暂无可展示的事件研究宏观信息"），不从预测响应或报告文本拼装。主题筛选采用可搜索下拉（选项来自已加载数据去重），自由输入以服务端校验为准。

**卡片跳转事件研究（Q-03 已确认）**：点击标题 → `/event-study?event_id=<id>`；事件研究页用**卡片已渲染字段**（标题/摘要/市场标签）预填 event_text 与类型标签，不预填资产（除非卡片有唯一关联资产），窗口默认 post_event_5d，**不自动提交**（后端不提供 prefill 接口）。

---

## 九、自选 API（第二阶段）

资源 ID 均为 UUID；`market` 为枚举；`symbol` 用 URL 编码的标的代码。分组 version 是资源级乐观并发令牌；标的写操作携带父资源 `expected_watchlist_revision`。

### 9.1 分组

| 方法与路径 | 请求 | 成功响应 | 关键错误 |
|-----------|------|----------|----------|
| GET /api/v1/watchlists?cursor=&limit= | limit 默认 20（1..100） | items: WatchlistDTO[]（updated_at DESC, id DESC） | — |
| POST /api/v1/watchlists | {name} | 201 + WatchlistDTO | WATCHLIST_NAME_CONFLICT（409） |
| PATCH /api/v1/watchlists/{watchlistId} | {name, expected_version} | 200 + WatchlistDTO | WATCHLIST_NAME_CONFLICT / REVISION_CONFLICT（409，重新拉取） |
| DELETE /api/v1/watchlists/{watchlistId}?expected_version= | — | 200 + {deleted: true, resource_id} | WATCHLIST_NOT_EMPTY（409）；RESOURCE_NOT_FOUND（404） |

WatchlistDTO：`{id, name, version: int, item_count: int, created_at, updated_at}`（item_count 为分组内标的数量）。

### 9.2 分组标的

| 方法与路径 | 请求 | 成功响应 | 关键错误 |
|-----------|------|----------|----------|
| GET /api/v1/watchlists/{watchlistId}/items | —（组内列表为本地小列表，全量返回，无 cursor/limit） | {watchlist_id, watchlist_revision, items: WatchlistItemDTO[]}（display_order ASC, id ASC） | RESOURCE_NOT_FOUND |
| POST /api/v1/watchlists/{watchlistId}/items | {market, symbol, expected_watchlist_revision} | 201 + {item: WatchlistItemDTO, watchlist_revision} | WATCHLIST_ITEM_DUPLICATE（409，不插入本地乐观项） |
| PUT /api/v1/watchlists/{watchlistId}/items/order | {expected_watchlist_revision, items: [{market, symbol, display_order}] 完整顺序} | 200 + {watchlist_id, watchlist_revision, items: WatchlistItemDTO[]} | WATCHLIST_ITEM_ORDER_CONFLICT（409，重新读取该分组） |
| DELETE /api/v1/watchlists/{watchlistId}/items/{itemId}?expected_watchlist_revision= | — | 200 + {deleted: true, resource_id} | RESOURCE_NOT_FOUND（404，刷新） |

WatchlistItemDTO：`{id, watchlist_id, market, symbol, display_order, created_at, updated_at}`。

并发规则：任何 409 冲突后**重新拉取服务端最新资源**作为下一轮操作基线，不做客户端静默合并/乐观排序。删除均返回 200 + envelope，**不使用 204**。

---

## 十、组合 API（第二阶段）

### 10.1 组合

| 方法与路径 | 请求 | 成功响应 | 关键错误 |
|-----------|------|----------|----------|
| GET /api/v1/portfolios?cursor=&limit= | limit 默认 20（1..100） | items: PortfolioDTO[]（updated_at DESC, id DESC） | — |
| POST /api/v1/portfolios | {name} | 201 + PortfolioDTO | PORTFOLIO_NAME_CONFLICT（409） |
| PATCH /api/v1/portfolios/{portfolioId} | {name, expected_version} | 200 + PortfolioDTO | PORTFOLIO_NAME_CONFLICT / REVISION_CONFLICT（409） |
| DELETE /api/v1/portfolios/{portfolioId}?expected_version= | — | 200 + {deleted: true, resource_id} | PORTFOLIO_NOT_EMPTY（409）；RESOURCE_NOT_FOUND（404） |

PortfolioDTO：`{id, name, version: int, position_count: int, created_at, updated_at}`。

### 10.2 持仓

| 方法与路径 | 请求 | 成功响应 | 关键错误 |
|-----------|------|----------|----------|
| GET /api/v1/portfolios/{portfolioId}/positions | —（组内列表为本地小列表，全量返回，无 cursor/limit） | {portfolio_id, portfolio_revision, items: PortfolioPositionDTO[]}（market ASC, symbol ASC） | RESOURCE_NOT_FOUND |
| PUT /api/v1/portfolios/{portfolioId}/positions/{market}/{symbol} | {quantity, average_cost, expected_portfolio_revision} | 200 + {position: PortfolioPositionDTO, portfolio_revision}（upsert 为同一条持仓） | INVALID_POSITION（422，字段级）；PORTFOLIO_POSITION_CONFLICT（409，重新读取 positions） |
| DELETE /api/v1/portfolios/{portfolioId}/positions/{market}/{symbol}?expected_portfolio_revision= | — | 200 + {deleted: true, resource_id} | RESOURCE_NOT_FOUND（404） |

PortfolioPositionDTO：`{portfolio_id, market, symbol, quantity: number, average_cost: number, updated_at}`。

校验（前端先行 + 服务端兜底）：`quantity > 0`、`average_cost >= 0`。组合/持仓与 AI 仓位建议（final_position_plan）互不自动合并。

---

## 十一、前端实现注意事项（契约要点清单）

1. **状态唯一真相是 TaskDTO.status**（详情 REST）；SSE 只用于进度体验。RETRYING 只经 REST 呈现。
2. 建立 SSE 前必须校验 events_url 恒等于 `/api/v1/analysis-tasks/{id}/events`；任务终态不建 SSE。
3. 任务中心初始展示全量（status 默认 all）；筛选值 all/active/succeeded/failed/cancelled 由服务端过滤，契约不支持时禁用控件而非本地过滤。
4. 创建任务：每次新的用户提交意图生成新 UUID 幂等键放请求头；提交中禁用重复点击；同一意图重试复用键。
5. 全部删除接口返回 200 + envelope，**不要按 204 无 body 处理**；任务删除仅限终态（409 TASK_NOT_TERMINAL 提示先取消）。
6. 市场区块：US→KR→CN 组序、组内 display_order 是产品固定规则；周期选择只来自 supported_intervals；from>to 前端阻止，超窗错误提示缩小范围。
7. 休市（market_session_status=CLOSED）不是错误：展示最近闭市快照 + market_closed_reason；它与 freshness_status 独立判断。
8. 热点无结果是 200 + 空 items（result_status=NO_HOT_CONCEPTS）正常空态，与 503 可重试错误态严格区分。
9. 看板三区块独立加载/失败；pending_actions 不包含 RETRYING/CANCELLED/NOT_REQUESTED；conclusion_summary 为 null 时不得截取报告正文。
10. 宏观信息空列表为正常空态；不从预测响应或报告文本拼装。
11. 事件研究：503/504 仅手动重试；成功保留表单输入；结果为空/样本不足按 note 正常呈现。
12. 自选/组合写操作必带 expected_version / expected_watchlist_revision / expected_portfolio_revision；409 冲突后重新读取服务端最新资源。
13. 敏感信息：页面、浏览器存储、URL 不保存 API Key、Token、租约、内部堆栈、本地绝对路径。
14. 本地部署边界：首期仅本机 loopback（frontend 发布 127.0.0.1:${FRONTEND_HOST_PORT}:80）；不因前端页面存在而默认支持 LAN/公网。
15. 执行调用日志：`available=false` 与"目录存在但空树"都是正常空态（"暂无执行日志"）；truncated/parse_error 的内容一律经 content 端点拉全量，不得在前端拼装或本地截断语义。
