# 严格 as_of 检索不能使用事后补生成的事件向量

事件向量是带自身可用时间的派生证据；只过滤新闻发布时间和 assessment.available_at，仍会让回填向量穿越历史时点。

## 表象

- 按历史 `as_of` 查询事件时，标签和事件列表保持不变，但候选排序、相似度或命中集合随之后的向量回填改变。
- `embedding IS NOT NULL` 被直接用作向量可用条件，未来补生成的 embedding 被错误地当成当时可召回。

## 根因

- 同一 assessment 标签版本创建后，向量可能在数小时/数天后才成功生成；向量可用时间不同于业务判断的 `available_at`。
- 召回 SQL 只限制 assessment 时间，却直接使用当前行的 `embedding` 做距离排序。

## 正确姿势

- 每日快照先限制来源 `published_at`/`first_seen_at <= news_cutoff_at`，再限制 `assessment.available_at <= report_as_of`；历史向量召回还必须满足 `embedding_available_at <= report_as_of`。
- `embedding_available_at` 缺失时该行按“此时无可用向量”处理；可保留结构化事实结果，但不得走向量相似度排序。
- 向量一次性补填后记录真实可用时间；不可回写历史 `as_of` 结果或复用 `events.embedding` 代替版本向量。
- 验证时构造标签先可用、向量晚于快照生成的场景，断言旧快照不使用向量，新快照可使用。

验证来源：每日投研流程 PostgreSQL 用例验证 late vector 的历史可用性门控。
