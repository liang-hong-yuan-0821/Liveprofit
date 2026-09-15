# DB 测试隔离 / 契约测试注入 / Redis 数据安全 / PG 参数上限

> 一句话结论：共享 DB 集成测试必须逐用例 TRUNCATE+flushdb；契约测试靠 monkeypatch AI 侧 config 模块全局切库；对真实 Redis/PG 写数据前必须先检查现存 key/行；大窗口批量 upsert 必须分批（PG 单语句参数上限 65535）。

## module 级共享 DB 的集成测试（2026-09-05）

- 必须 autouse fixture 逐用例 TRUNCATE + flushdb 隔离（否则前用例遗留 Outbox/任务被后用例 claim，如 dispatch 数量断言翻倍）。
- pytest 输出经管道时用 `-o faulthandler_timeout` 或写文件排查挂起。

## 契约测试复用 AI 侧全局 config 的注入点（2026-09-08，事件研究审核平台集成引入）

- AI 侧 `AI.eventStudy.collectors.config` 的 `pg_dsn()`/`redis_uri()` 在**调用时读模块全局**（env 仅导入时捕获进模块常量），故契约测试可：

  ```python
  monkeypatch.setattr(es_config, "PG_CONNECTION_STRING", 测试串)  # REDIS_CONNECTION_STRING 同理
  es_config._redis_client = None  # 懒加载客户端重建指向 db 11
  ```

  把 AI 侧连接切到契约测试库；配合 AI 侧函数级 import，补丁在调用时生效。
- 先例：`backend/tests/contract/api/test_event_study_review.py` 的 `_review_test_env` fixture（含 events/assets/event_impacts 最小列集 DDL + 逐用例 TRUNCATE）。

## E2E/脚本向真实 Redis 写草稿前必须先检查现存 key（2026-09-08，曾覆盖 3 条真实草稿）

- **根因**：爬虫 `events:draft_seq` 已分配大量号段，`SET events:pending:<id>` 无条件覆盖会毁掉真实待审草稿（当日靠 dump.rdb 快照 + 临时容器恢复）。
- **正确姿势**：先 `SCAN events:pending:*` + 读 `events:draft_seq`，用**远高于 seq 的 draft_id**（如 9001+）并事后 `DELETE` 清理；向真实 PG 写行同理先确认无同标题行、事后按明确条件 DELETE。

## Redis 数据误覆盖恢复手法（2026-09-08 验证有效）

```bash
docker cp liveprofit-redis:/data/dump.rdb <本地目录>
docker run --rm -v <本地目录>:/data -p 6390:6379 redis:7-alpine redis-server --appendonly no --save ""
docker exec redis-cli -p 6390 --raw GET <key>
```

- 两个坑：Git Bash 的 `/tmp` 路径 Docker Desktop 挂载无效（必须 Windows 形式 `C:/Users/...`）；Windows GBK locale 下 Python subprocess 读中文输出必须显式 `encoding="utf-8"`。

## PG 单语句参数上限 65535（2026-09-12）

- **表象**：全历史因子回填（8715 行 × 14 列 ≈ 12 万参数）单批 `pg_insert().values([...])` 报 `number of parameters must be between 0 and 65535`。
- **正确姿势**：`MarketFactorRepository.upsert` 已按 `_UPSERT_BATCH=2000` 分批（2000×14=28000 参数留余量）；**任何新增"全历史/大窗口批量 upsert"的 Repository 必须同样分批**（单批行数 ≤ 65535/列数 打七折），集成测试需含超单批上限的回归用例（test_factor_ingestion_large_batch_beyond_param_limit）。
