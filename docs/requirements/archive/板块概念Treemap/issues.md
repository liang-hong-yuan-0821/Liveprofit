# 问题与解决

评审 findings 与实施中遇到的问题。按时间**倒序**追加，每条三要素：表象 → 根因 → 解决。

## 2026-09-14 CR 二轮前发现的预置测试隔离问题（非本任务引入，未修）

- **表象**：混跑批次（contract 测试在前、tests/db/instrument 在后）时 `test_instrument_daily.py::test_bulk_upsert_daily_drops_close_nan_and_nan_to_none` 的 caplog 断言失败（caplog.text 为空，`"close 为 NaN 已 drop"` 捕获不到）。
- **根因**：contract 测试套件（app 启动链）污染了后续文件的 logging 捕获（用未改动的 test_event_study_review.py + 该用例同样复现；各套件单独跑全绿）。
- **解决**：未修（超出本任务范围）；分套件跑法为有效验证口径。跨任务主题候选，可沉淀 pitfalls/backend。

## 2026-09-14 CR 第一轮 findings（11 条 minor/polish，全部修复）

- **表象**：实现完成后的 code review verdict PASS（0 blocker / 0 major），11 条 minor/polish：interval 必填未校验、成分截断在 Python 侧拉全量（~2.9 万行）、弹窗 enabled 门控断言恒真、tooltip 未用 treePathInfo、visibleMin 无 sort 不生效、文档三处缺口、真库写路径无自动化覆盖等。
- **根因**：多为"方案细节在实现时被简化/省略"与"测试断言未覆盖真实路径"。
- **解决**：11 条全部修复（SQL ROW_NUMBER 截断、interval 422、treePathInfo、sort:'desc'、pg_env 真库用例、文档补齐、关闭卸载等）；二轮核验见 result.md。

- **表象**：方案 v2 评审 5 组 findings（无 blocker/major）。
- **根因**：① 写入列漏 source/updated_at——`bulk_upsert_sector_daily` 不自动补 updated_at（sector_daily.updated_at 可空无默认），漏填则 DO UPDATE 把 NULL 覆盖回已有行；② 窗口 45 自然日跨春节/国庆只剩 26–28 交易日，不足端点 33 日上限；③ "个股 K 线全历史"与固定窗口 180 天自相矛盾；④ 1031 次串行请求无熔断（最坏 8.6 小时/日）；⑤ polish 八项（行号锚/章节引用/limit 必填/BarsData 回显口径/测试落点/dc 成分实测数字等）。
- **解决**：① 采集层显式填 `source='dc'` + `updated_at=采集时刻`；② window_days 放宽 70 自然日（返回行数仍由端点封顶 33，成本为零）；③ 三处统一"近 180 自然日固定窗口（首版；全历史另案）"；④ 连续 5 板块失败熔断终止本步骤；⑤ 逐项修正。R2' 核验 PASS，4 条 polish 措辞残留顺手修完。

## 2026-09-14 v1 R1 评审关键 findings（v1 已废弃，结论在 v2 沿用）

- **表象**：v1 评审 19 条 findings（1 blocker + 7 major）。
- **根因**：① blocker：echarts 6.1 treemap `itemStyle.color` 回调返回值被丢弃（实测 SSR：回调被调用但 SVG 无红绿色）——颜色必须生成时静态写入 per-node；② major 代表项：service.py 实为 4 处 source='dc'（漏第 4 处名称映射会 500）；成员 SQL INNER JOIN 丢 88 行无 instrument 记录的成分（改 LEFT JOIN）；payload 低估 11 倍（ths top 30 板块 27,292 成分，dc 实测 29,062——截断 top 100 定稿）；openapi 重导出链漏列；生产 wrapper 未随迁。
- **解决**：全部修复后 v1 3 轮收敛（R1 6.0 → R2 8.1 → R3 PASS）；v2 重写时保留有效结论（静态色/LEFT JOIN/截断 top 100/openapi 链/熔断），删除随 ths 路线废弃的部分。

## 2026-09-14 采集链列名不匹配 bug（板块成分从未写库成功）

- **表象**：用户反馈概念下个股数量异常少；重采 dc 成分时 COPY 报 `NotNullViolation: null value in column "sector_code"`（failing row `(dc, null, 920117.BJ)`）；部分概念成分数明显偏少（历史新高库内 2 行 vs 端点 12 只）。
- **根因**：provider `get_concept_members_df` 归一列名仍为 `concept_code`（决策 12 改名落点遗漏），DAO `upsert_sector_members`/`_clean_frame` 只认 `sector_code` → 每板块帧的 sector_code 全被填 null → **全部板块的 COPY 都失败**；存量 93,397 行是统一方案迁移来的，采集链自迁移后从未写成功过（单测 fixture 用对列名所以未暴露）。期间"光刻胶 0 成分"是排查时的查询误判（sector_code 少 `.DC` 后缀），已纠正。
- **解决**：provider 输出列改名 `sector_code`（docstring 同步）+ 单测断言更新；双源成分重采启动（bug 修复后首次真正写入）。衍生教训：结构化帧的列名契约要靠"清洗链回归测试"（provider 帧 → _clean_frame → 断言关键列非空）锁住，纯 fixture 单测会漏。
