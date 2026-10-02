# 每日批处理触发方式与补跑机制（2026-08-31 起）

> 一句话结论：方案 B 常驻自调度（APScheduler 08:30 子进程 daily_job）+ 三层补跑（cron 触发、启动自检、15 分钟周期自检）；uvicorn 单 worker 禁 --reload。

## 方案 B（推荐）：常驻自调度

- `AI/eventStudy/scheduler/app_scheduler.py` 的 APScheduler 挂在 eventStudy FastAPI lifespan（`AI/eventStudy/api/main.py`），每天 `EVENT_STUDY_DAILY_TIME`（默认 08:30，本地时区）以**子进程**触发 `python -m AI.eventStudy.scheduler.daily_job`。
- 防重复：`var/logs/daily_job.running` 标记文件（写子进程 pid，服务启动时按 pid 存活清理陈旧标记）+ 进程内 threading.Lock；misfire 补跑窗口 30 分钟。

## 补跑机制（2026-08-31，电脑睡眠场景）

- **场景**：电脑睡眠期间调度器冻结且无启动事件，当日任务漏跑。
- **机制**：完成标记 `var/logs/daily_job_done.YYYYMMDD`（子进程退出码 0 时原子写入）＋三层触发——cron 08:30 正常触发、服务启动自检、**每 15 分钟周期自检**（唤醒后靠周期自检补跑）；当日自动尝试上限 `EVENT_STUDY_DAILY_MAX_ATTEMPTS`（默认 3）防持续故障空跑，次日自动恢复。

## 运行约束

- uvicorn **单 worker、禁用 --reload**（否则调度器重复启动）；Windows 守护用 NSSM 注册 Windows 服务（见 AI/eventStudy/scheduler/scheduler_setup.md）。

## 迟到新闻驱动的量化候选刷新（2026-09-23 验证）

- 量化批次在截止时因新闻积压为 partial，后续完整新闻批次清空积压时，可以创建派生刷新任务：幂等身份绑定根量化任务 ID 与新新闻任务 ID，刷新链始终保留根任务，避免同一新闻版本重复派生。
- 刷新复用父批次的行情日、策略扫描结果和量化基础分，只重算事件证据贡献、候选排序与需要更新的重点候选深研；不重复运行全部策略，也不把较晚行情日混入旧候选快照。
- 如果父报告不存在完整扫描，或刷新时无法读取行情水位，跳过刷新或保留父批次的候选/行情日并标记 partial；不能将降级读取结果当作空候选覆盖有效结果。
- 验证方式：每日研究调度与量化快照单测，以及“刷新行情水位失败仍保留父候选、标记 partial、不重复策略执行”的端到端模拟回归。

## 方案 A：schtasks

- schtasks 每日 08:30 触发（无常驻服务时用，注册命令见 scheduler_setup.md）；A/B 同时开启安全（标记文件防重复），但建议只用一个。
