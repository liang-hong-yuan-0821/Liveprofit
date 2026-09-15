# pnpm 布局 / OpenAPI codegen 坑

> 一句话结论：frontend 一律 pnpm（npm 会炸）；backend 改路由后必须先重导出 openapi + `generate:api` 再动前端消费代码；codegen 的 Literal 形态分必选/可选两套，mock 写法不同。

## frontend 是 pnpm 布局（2026-09-06）

- **表象**：`npm install` 报 `Cannot read properties of null (reading 'matches')`。
- **根因**：npm arborist 无法处理 pnpm 布局（pnpm-lock.yaml + node_modules/.pnpm 符号链接）。
- **正确姿势**：依赖操作一律 `pnpm add` / `pnpm install` / `pnpm run`。

## openapi 重导出链条（2026-09-08 实测）

- backend 新增/修改路由后必须 `python -m backend.scripts.export_openapi` + `pnpm run generate:api` 再动前端消费代码；并发编辑下他人前端代码依赖新枚举（如 action approve/ignore）时，未重导出会导致其 typecheck 失败。

## codegen 按 tags 分组生成 Service

- openapi-typescript-codegen 按 **tags 分组**生成 Service——同 tag 的多个端点会归进同一个 Service 类（如 execution-logs 端点按 tags=["analysis-tasks"] 归入 AnalysisTasksService），不存在独立 Service 文件属正常，消费时按 tag 找方法。

## 必选 Literal → enum namespace（值导入）

- openapi-typescript-codegen 把 Literal 生成 enum namespace（如 `TopologyNodeDTO.status.EXECUTED`），测试/组件需**值导入**（不能 import type），mock 数据用枚举成员不用字符串字面量。

## 可选 Literal（`Literal[...] | None`）→ union 类型（2026-09-08 实测）

- pydantic `Literal[...] | None` 进 OpenAPI 为 anyOf → codegen 落为 `'x' | 'y' | null`（如 `RefreshData.skipped_reason`），**无枚举成员可导入**，字符串比较即类型安全；与必选 Literal 的 enum namespace 形态不同，写 mock/断言时勿套用枚举成员写法。
