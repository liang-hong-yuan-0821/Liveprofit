# 复盘

## 做对了什么（可复用）

- **plan 写代码草图 + 契约逐条枚举 → 实现期几乎零设计回头**：R3 PASS 的方案把 DTO/服务方法/路由/组件接口都写到了代码草图级，T1–T5 实现全程只有实现级 bug（漏负号、缺 Provider、死字段），没有设计级返工。方案越具体，实现越便宜。
- **定向回填技巧第二次成功复用**：`inc.INDEX_TARGETS.clear()+update(子集)` 后跑 `backfill_index_history`（incremental/backfill 共享同一 dict 对象），12 分钟完成 2 指数全历史回填（8369 bars + 8368 factors），避开全量 15 码回填。已沉淀进 memory（best-practices/backend/ingest-targeted-backfill.md）。
- **面板测试把「区间 from 口径」拆成纯函数 + 组件两层断言**：`addMonthsClamped` 作为导出纯函数单独断言（含 2026-03-31→2026-02-28 日溢出夹取），组件层只断言 mock 收到的请求参数——日溢出这类边界不靠渲染测试硬碰。
- **codegen 链一次到位**：backend 路由改完立即 export_openapi + generate:api 再动前端（CLAUDE.md 规则），前后端消费代码与生成客户端零脱节。

## 踩了什么坑（教训）

- **`trendRangeFrom` 漏对 months 取负**：RANGES 表里写的是正数（months: 3/12/36），映射到 `addMonthsClamped` 时漏了 `-months`——面板首跑全部请求 from=2027-09-19（未来日期），单测的纯函数断言第一遍就抓住。教训：**「近 N 月」的语义是减法，常量表与调用点的符号方向要在同一处声明**（要么表里存负值，要么映射处显式 `-months` 并注释）。
- **面板渲染测试缺 QueryClientProvider**：面板 hooks 依赖 React Query，直接用 `render()` 而非项目既有 `renderWithRouter`（内带 Provider）——"No QueryClient set" 6 连败。教训：**凡组件内含 useQuery，一律 renderWithRouter / withQueryClient**（项目 test/utils 已提供，别再裸 render）。
- **方案实测数值被真实回填推翻**：plan 4.1.2 单请求实测 000905 = 4791 行/2007-01-04 起，但分块回填实际拿到 5275 行/2005-01-04 起（单请求路径低估了历史深度）；连带「全部」区间共同首日从预测的 2014-01-02 修正为 2013-12-31。教训：**点测（单请求）与回填路径（5 年分块）的数据深度口径可能不同，验收断言以回填后查库为准，plan 的实测值标注"预测、以回填为准"**。
- **CR 抓出的两处轻量不一致**：LineChart 注释宣称"引用稳定"但面板传内联对象字面量（memo 实际不生效）；`LineChartViewModel.height` 是方案草图的死字段。教训：**组件注释里的性能断言要对着唯一消费方的实际用法核**。

## 下次改进

- 环境侧：AI 交易日历 2026 缓存未覆盖近期（30 天回溯落空返回原值），导致真实环境 freshness 恒 STALE——刷新日历缓存是独立课题，本次只是记录；后续任务若依赖真实环境 freshness 目检，先核日历覆盖。
- plan 的「实测数值」统一加口径标注（点测 vs 回填 vs 库内 SQL），并注明"以回填后查库为准"。
