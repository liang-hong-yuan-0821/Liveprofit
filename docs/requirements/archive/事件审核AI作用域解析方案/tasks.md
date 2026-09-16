# 事件审核 AI 作用域解析与表格标题修复方案 任务清单

> **状态**：`已完成`（2026-09-16）
> **进度**：8/8 任务
> **下一步**：随任务文件夹归档
> **关联方案**：[plan.md](plan.md)（方案正文）

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | NameResolver 名称→代码解析器 + 单测 | — | 已完成 |
| T2 | ai_prelabel 改造（提示词/名称解析接入/回填谓词/force/TTL）+ 测试更新 | T1 | 已完成 |
| T3 | backend 契约与服务（draft_ids/谓词/覆写）+ 单测更新 | T2 | 已完成 |
| T4 | openapi 重导出 + codegen + 契约门禁 | T3 | 已完成 |
| T5 | 前端标题列修复（两处 GRID minmax） | — | 已完成 |
| T6 | 前端强制重填按钮 + 详情弹窗警示 + 测试 | T4、T5 | 已完成 |
| T7 | ~~Streamlit review_app（force 分片 + unresolved 警示）~~ | T2 | 已撤销（用户拍板：Streamlit 审核页已由平台替代，不再改动） |
| T8 | 收尾：全量验证 + knowledge 文档整合 | T1–T7 | 已完成 |

## 任务

### T1 NameResolver 名称→代码解析器 + 单测

- **目标**：新建 `AI/eventStudy/review/name_resolver.py`（方案 4.1）：按作用域查 market.instrument / market.industry / market.sector 字典表，把中文实体名称解析为规范引用（精确匹配优先 → 唯一包含兜底 → 同表多行按代码升序 → 行业优先 → 查询异常 rollback + fail-open）；配套 fake conn 单测
- **涉及文件**：
  - 新建：`AI/eventStudy/review/name_resolver.py`（NameResolver 类）
  - 新建：`tests/event_study/test_name_resolver.py`（FakeConn 单测，方案 4.7.1 用例①-⑧）
- **依赖**：无
- **验收标准**（全部勾选才算完成）：
  - [x] `.venv/Scripts/python.exe -m pytest tests/event_study/test_name_resolver.py -q` 全绿（覆盖：精确/唯一包含/多行确定性/行业优先/作用域约束/异常 rollback+fail-open/空列表/LIKE 元字符转义）
- **状态**：`已完成`（2026-09-16）

### T2 ai_prelabel 改造 + 测试更新

- **目标**：`AI/eventStudy/review/ai_prelabel.py`（方案 4.2/4.3/4.4）：提示词规则 7/8 重写（代码仅原文出现时输出、不要凭记忆补代码、美股→A 股概念）；`_sanitize` 名称分流 + resolver 接入 + unresolved_entities + 条数/长度上限；`prelabel_one/_open_existence` 签名扩展（2→3 元组）；`needs_prelabel` 回填谓词；`prelabel_events(force)` 覆写模式 + 写回补 `ex=PENDING_DRAFT_TTL`；docstring/import 随迁。测试按方案 4.7.1 更新（①②③④⑤⑥⑦⑧）
- **涉及文件**：
  - 修改：`AI/eventStudy/review/ai_prelabel.py`
  - 修改：`tests/event_study/test_ai_prelabel.py`
- **依赖**：T1
- **验收标准**（全部勾选才算完成）：
  - [x] `.venv/Scripts/python.exe -m pytest tests/event_study/test_ai_prelabel.py tests/event_study/test_name_resolver.py -q` 全绿（44 passed）
  - [x] 既有用例随迁 3 处 stub（`_open_existence`）+ prelabel_one monkeypatch 按方案 N1-a/M2 全量枚举改完
  - [x] `.venv/Scripts/python.exe -m pytest tests/event_study/ -q` 全目录回归（196 passed）
- **状态**：`已完成`（2026-09-16）

### T3 backend 契约与服务 + 单测更新

- **目标**：`PrelabelRequest.draft_ids`（不加 force 字段，方案 N2）；router 透传；`EventStudyReviewService.prelabel(limit, draft_ids=None)` 谓词+分流（draft_ids 模式以 force=True 调 adapter）；adapter `needs_prelabel`/`prelabel(drafts, force)`；review_contracts 注释与 service docstring 随迁。测试按方案 4.7.1 更新（N1-b fixture、draft_ids 用例）
- **涉及文件**：
  - 修改：`backend/api/schemas/event_study_review.py`
  - 修改：`backend/api/routers/event_study_review.py`
  - 修改：`backend/modules/event_study/application/review_service.py`
  - 修改：`backend/modules/event_study/application/review_contracts.py`
  - 修改：`backend/modules/event_study/infrastructure/review_adapter.py`
  - 修改：`backend/tests/unit/event_study/test_review_service.py`
- **依赖**：T2
- **验收标准**（全部勾选才算完成）：
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/unit/event_study/test_review_service.py -q` 全绿（35 passed）
- **状态**：`已完成`（2026-09-16）

### T4 openapi 重导出 + codegen + 契约门禁

- **目标**：`python -m backend.scripts.export_openapi` + `pnpm run generate:api`（先于前端消费，方案 4.4.1 openapi 链条；工作区 openapi.v1.json 已有用户并发修改属正常，不做 git 操作）
- **涉及文件**：
  - 生成：`backend/openapi/openapi.v1.json`、`frontend/src/api/generated/**`
- **依赖**：T3
- **验收标准**（全部勾选才算完成）：
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/contract/test_openapi_gate.py -q` 通过（2 passed）
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/contract/api/test_event_study_review.py -k prelabel -q` 通过（1 passed；全文件 39 passed）
- **状态**：`已完成`（2026-09-16）

### T5 前端标题列修复

- **目标**：`PENDING_ROW_GRID` 标题列 `minmax(0,1.2fr)` → `minmax(14rem,1.2fr)`；`IMPACT_ROW_GRID` 事件标题 `minmax(0,1.5fr)` → `minmax(14rem,1.5fr)`、备注 `minmax(0,1fr)` → `minmax(6rem,1fr)`（方案 4.5）
- **涉及文件**：
  - 修改：`frontend/src/modules/event-study/pages/review/PendingEventRow.tsx`
  - 修改：`frontend/src/modules/event-study/pages/review/ImpactConfirmTab.tsx`
- **依赖**：无（可与 T1-T3 并行）
- **验收标准**（全部勾选才算完成）：
  - [x] `pnpm exec vitest run src/modules/event-study/pages/review`（frontend/ 下）不因本次改动失败（46 passed）
  - [ ] 人工检查：<1920px 窗口打开审核页两个 Tab，标题列可见、可横向滚动到作用域/目标列（用户验收）
- **状态**：`已完成`（2026-09-16，人工检查项留待用户）

### T6 前端强制重填按钮 + 详情弹窗警示 + 测试

- **目标**：`PendingEventsTab` 加「🤖 强制重填全部」按钮（draft_id 列表 50 条/片分片循环、片内 prelabeled===0 提前终止）+ 底部文案补充；`EventDetailDialog` unresolved_entities 警示行（Array.isArray 守卫）；测试（方案 4.4/4.6/4.7）
- **涉及文件**：
  - 修改：`frontend/src/modules/event-study/pages/review/PendingEventsTab.tsx`
  - 修改：`frontend/src/modules/event-study/pages/review/EventDetailDialog.tsx`
  - 修改：`frontend/src/modules/event-study/pages/review/PendingEventsTab.test.tsx`
  - 新建：`frontend/src/modules/event-study/pages/review/EventDetailDialog.test.tsx`
- **依赖**：T4（需 codegen 后的 draft_ids 类型）、T5
- **验收标准**（全部勾选才算完成）：
  - [x] `pnpm exec vitest run src/modules/event-study/pages/review`（frontend/ 下）全绿（46 passed；全 event-study 模块 59 passed）
  - [x] `pnpm run typecheck`（frontend/ 下）通过
- **状态**：`已完成`（2026-09-16）

### T7 Streamlit review_app 改造

- **目标**：`review_app.py` 预填按钮旁加「重新预填全部」checkbox（force 下 50 条分片循环 + st.progress）；「查看事件原文」expander 内 unresolved_entities `st.warning` 警示（方案 4.4.1/4.6.1）
- **涉及文件**：
  - 修改：`AI/eventStudy/review/review_app.py`
- **依赖**：T2
- **验收标准**：~~人工检查 Streamlit~~（2026-09-16 用户拍板撤销：Streamlit 审核页已被平台集成版替代；AI 侧回填谓词对 Streamlit 自动生效，无需 UI 改动）
- **状态**：`已撤销`（2026-09-16）

### T8 收尾：全量验证 + knowledge 整合

- **目标**：跑齐方案 4.7.3 全部验证命令；`docs/knowledge/backend/API契约.md` 预填口径随迁（`:597` 附近）；README/result.md/retrospective.md 收尾
- **涉及文件**：
  - 修改：`docs/knowledge/backend/API契约.md`
  - 修改：任务文件夹 README.md / result.md / retrospective.md / log.md
- **依赖**：T1–T7
- **验收标准**（全部勾选才算完成）：
  - [x] 方案 4.7.3 六条验证命令全部通过（后端侧 281 passed + 前端 59 passed + typecheck event-study 零报错）
  - [x] API契约.md 预填幂等口径与新谓词一致（含 draft_ids 200 硬上限/50 分片约定）
- **状态**：`已完成`（2026-09-16）

---

## 拆分与维护规则

- **拆分粒度**：每个任务 = 一个可独立验收的实现单元；按依赖排序，无依赖任务可并行（T5、T7 可与主线并行）
- **验收标准必须可执行**：单测命令 / 可运行检查 / 写明观察点的人工检查
- **状态取值**：`待开始` → `进行中` → `已完成`；被阻塞时标 `阻塞：<原因>`
- **生命周期**：任务清单随任务文件夹归档保留；**无需额外评审**（已由评审通过的方案拆出）
