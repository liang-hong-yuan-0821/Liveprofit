# Windows 上 PG/psycopg/管道坑

> 一句话结论：backend 在 Windows 上跑 PG/redis 必须 SelectorEventLoop + uvicorn `loop="none"` + host 归一化；排查"卡死"先抓 faulthandler 栈，别信管道输出。

## psycopg async 需要 SelectorEventLoop + uvicorn loop="none"（2026-09-05）

- **表象**：ProactorEventLoop 下 psycopg async 直接 InterfaceError。
- **根因**：create_app 内设 event loop policy 对 uvicorn 场景太晚（uvicorn 在加载工厂前建循环）；且 uvicorn 的 `loop="auto"` 在 Windows 会强制装回 Proactor 覆盖你的策略。
- **正确姿势**：cli 里先 `set_event_loop_policy(WindowsSelectorEventLoopPolicy)` 再 `uvicorn.run(..., loop="none")`。
- **连带坑**：redis.asyncio 两种循环都可用，但连接串 host 也须归一化——`_normalize_loopback_host` 注意 `:pass@host` 形式 username 为空串，重建 netloc 时不得丢密码（曾因此把 redis 密码丢了）。

## PG_HOST=localhost 必须归一化为 127.0.0.1（2026-09-05）

- **表象**：无限挂起（faulthandler 定位在 psycopg wait_conn）。
- **根因**：Docker 端口代理仅监听 IPv4 loopback，psycopg 优先尝试 `::1` 被黑洞且默认无 connect_timeout。
- **正确姿势**：`backend.bootstrap.settings` 的 resolved_database_url/resolved_redis_url 已做归一化，bootstrap 引擎统一带 connect_timeout=5。

## Windows 管道下 Python stdout 全缓冲（2026-09-05）

- **表象**：pytest 经 `| tail/head` 观察输出会误判"卡死"（实际在跑）。
- **正确姿势**：排查挂起用 `-o faulthandler_timeout=30` 抓真实栈。
