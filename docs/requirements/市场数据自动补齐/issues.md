# 问题与待验证项

| 编号 | 状态 | 事项 | 验证/处置 |
|------|------|------|-----------|
| V1 | 正式依赖与策略已验证，打包另验 | exchange_calendars 4.13.2 的 XSHG 2026 与缓存一致、2027 不支持；XNYS/XKRX 两年可构造 | 正式 .venv 的31项策略单测通过，包括242日缓存比对、DST/提前收盘、EXPIRING/UNKNOWN和市场独立降级；原证据见 attachments/calendar-poc.md，Linux打包由T9验证 |
| V2 | 已授权完成3次采样；首次发布时间仍未知 | 代理端点发布缓冲与 US/KR trade_date 日期口径 | 初次沙箱WinError10013及自动审批拒绝后，用户明确授权三次请求；UTC2026-09-22 17:16样本SPX最新9/21、KS11最新9/22，字段齐全，日期与市场本地session一致。单点观察不证明首次发布时间或SLA，缓冲仍为策略；见 attachments/source-poc.md |
| V3 | 已闭环 | suspend_d 旧请求按 suspend_date 被代理忽略，返回 5000 条截断历史且日期列全空 | 官方文档与代理实测确认应传 trade_date、取 trade_date/suspend_type；代理忽略 suspend_type 筛选，故本地仅将 S 视为停牌、R 不豁免。9 月 22/23 日写入可信状态后，目标日 12 个无行情标的均有 S 证据，个股覆盖 5568/5568 |
| V4 | 本地路径已验证，容器待验 | market PG_* 和平台 LIVEPROFIT_DATABASE_URL 是两条连接配置路径 | 统一解析 DSN 已接入，隔离真实子进程显式连接测试库；Docker Hub 超时阻止整栈容器验证 |
| V5 | 已验证 | 旧 CLI/回填/批处理可能绕过新互斥与版本 | 公开采集入口逐项接入公共 guard；隔离 PG 锁冲突零源调用与 Redis 通知测试通过 |
| V6 | 已闭环 | R1 6.6，12 项修复；R2 PASS，十维均为10，无遗留 finding | 证据见 attachments/review.md；是方案核验通过，不是功能实现通过 |
| V7 | 主要路径已验证，调度故障矩阵待补齐 | Redis 状态/消息丢失及 PG commit 后通知失败 | 真实 Redis 验证并发 CAS、补发上限、旧 token、预算、PG 提交后通知失败与子进程无结果退出核验；T6 继续故障注入 |
| V8 | 部分解除：需管理员压缩 VHDX | Docker 构建曾因 D 盘满报只读文件系统；清理 Docker 内部缓存后宿主机空间未自动回收 | 用户授权后删除 `D:\BaiduNetdiskDownload` 内 148 个同目录 Windows 编号副本（约16.816 GB）；Docker Desktop 恢复，完整约8.96 GB Worker 镜像构建/导入、Compose config、CLI 与隔离 Redis DB15 启动心跳 smoke test 通过。两份本任务 8.78 GB BuildKit 安装缓存已清理，PG/Redis 数据卷未删；`docker_data.vhdx` 仍37.47 GB，D盘余1.54 GB。Windows 动态 VHDX 删除内部块后不会自动缩小，需管理员权限且 Docker/WSL 停止后 compact；当前执行环境未获管理员提升，尚未操作 VHDX。整栈联调仍未完成 |
| V9 | 已闭环 | DC 成分含 001246.SZ、301716.SZ、920201.BJ，完整股票目录不含这些代码 | 逐状态×交易所拉取 5907 条 stock_basic，三者均无记录；板块分类可先于 IPO 上市发布。日线目标改由 market.instrument 的股票目录定义，目录内生命周期未知仍阻断，旧冻结任务的目录外成分核验退出。同步后目标 5568，已补齐 5568/5568；重复代码响应整体拒绝 |
| V10 | 目标日已闭环；出站环境需持续观察 | dc_daily 2026-09-23 仅返回 1030/1031，缺 BK0165.DC；Windows 沙箱此前报 WinError 10013 | dc_index 只有涨跌快照无 OHLC；东财原始 K 线提供完整目标日记录，9 月 22 日与已有库值一致。定向兜底写入后板块 1031/1031 FRESH；API/Dispatcher/Worker 已重启。Worker 的独立后续出站可用性仍应在下一交易日观察 |
| V11 | 历史缺口保留 | HISTORY_GAP 是 3 日自动窗口以前的已知缺口，不代表目标日失效 | 2025-01 至 2026-09-18 股票已知 5481 个代码日期缺口，板块 BK1675.DC 已知 6 个；东财原始源仍缺 2026-08-03 至 08-06 的该板块 OHLC，不能编造填入。前端改为中文提示并在 FRESH 时隐藏旧失败任务。历史修复不属于近 3 日自动补齐范围 |

以上属于工程验证，不要求用户提供截图或凭经验决定技术事实。
