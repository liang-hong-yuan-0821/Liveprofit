# 最终产出与结论

## 验证结果

| 层面 | 命令/方式 | 结果 |
|------|----------|------|
| AI 侧单测 | `.venv/Scripts/python.exe -m pytest tests/event_study/ -q` | 197 passed |
| 后端服务单测 | `.venv/Scripts/python.exe -m pytest backend/tests/unit/event_study/ -q` | 35 passed |
| 契约门禁 | `.venv/Scripts/python.exe -m pytest backend/tests/contract/test_openapi_gate.py -q` | 2 passed（openapi.v1.json 与代码导出全等） |
| 契约 API | `.venv/Scripts/python.exe -m pytest backend/tests/contract/api/test_event_study_review.py -q` | 39 passed |
| 前端测试 | `pnpm exec vitest run src/modules/event-study`（frontend/ 下） | 59 passed |
| 前端类型 | `pnpm run typecheck`（frontend/ 下） | event-study 相关文件零报错（analysis 模块报错为用户并发开发中的改动，与本任务无关） |
| 语法检查 | `python -m py_compile` 三个 AI 侧改动文件 | 通过 |

## Code Review

- **结论**：第 1 轮 PASS（无 blocker/major，6 条 minor/polish）→ 修复 F1-F6 → 第 2 轮 PASS（F1-F6 全部落地，新发现 N1/N2 已随收尾修复）
- **遗留**：无。T5 的人工视觉检查项（窄窗口标题列可见）留待用户验收；T7 Streamlit 改动按用户拍板已撤销

## 交付物

- 标题列修复：`frontend/src/modules/event-study/pages/review/PendingEventRow.tsx`、`ImpactConfirmTab.tsx`（minmax 正下界）
- 名称→代码解析：`AI/eventStudy/review/name_resolver.py`（新建，字典表确定性解析）
- AI 预填增强：`AI/eventStudy/review/ai_prelabel.py`（提示词规则 7/8、名称解析接入、unresolved_entities、needs_prelabel 回填谓词、force 覆写、TTL 修复）
- 回填/强制重填通道：backend schema/router/service/adapter + openapi/codegen 产物 + 前端「强制重填全部」按钮（50 条/片分片）
- 详情弹窗 unresolved 警示：`EventDetailDialog.tsx`（Streamlit 对称展示按用户拍板撤销，见 decisions）
- 测试：`tests/event_study/test_name_resolver.py`（新建）、`EventDetailDialog.test.tsx`（新建）、既有 3 个测试文件扩展
- 文档：`docs/knowledge/backend/API契约.md` 预填口径随迁
