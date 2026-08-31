# 定时任务调度配置说明（Windows 任务计划程序）

每天早间触发 `daily_job.py` 有**两种方案**，二选一（同时开启也安全：运行标记文件防重复拉起）：

| 方案 | 机制 | 适用 |
|------|------|------|
| **A. schtasks 每日触发**（下文） | Windows 任务计划程序 08:30 拉起一次性脚本 | 无常驻服务；进程拉起即退 |
| **B. 后端常驻自调度**（已实现 2026-08-31，推荐） | 挂 eventStudy FastAPI lifespan 的 APScheduler | API 服务（uvicorn :8100）常驻时 |

日志统一输出到 `logs/event_study_daily.log`。

---

## 方案 B（推荐）：后端常驻自调度（app_scheduler.py）

原理：`AI/eventStudy/api/main.py` 的 FastAPI lifespan 在服务启动时调
`start_daily_scheduler()`、关闭时调 `stop_daily_scheduler()`。
`AI/eventStudy/scheduler/app_scheduler.py` 内置 APScheduler `BackgroundScheduler`，
每天 `EVENT_STUDY_DAILY_TIME`（默认 08:30，本地时区）以**子进程**方式触发
`python -m AI.eventStudy.scheduler.daily_job`。

运行（单 worker、**禁用 --reload**——reload 会导致调度器重复启动）：

```bash
.venv/Scripts/python.exe -m uvicorn AI.eventStudy.api.main:app --host 0.0.0.0 --port 8100
```

可靠性设计：

- **防重复**：`logs/daily_job.running` 标记文件写入子进程 pid；上一轮未结束时到点跳过。
  服务启动时检测陈旧标记（pid 已不存在 → 清理；pid 存活 → 保留）；进程内 threading.Lock 双保险
- **补跑机制（三层触发）**：完成标记 `logs/daily_job_done.YYYYMMDD`（子进程退出码 0 时
  原子写入，存在即"今日已完成"）：
  1. cron 08:30 正常触发（misfire_grace_time 30 分钟兜底短时停机）
  2. 服务启动时立即自检：已过触发点且今日未完成 → 立即补跑
  3. 每 15 分钟周期自检：**覆盖电脑睡眠唤醒**——睡眠期间调度器冻结且无启动事件，
     唤醒后由周期自检发现"已过点未完成"补跑
- **失败次数上限**：当日自动尝试 ≥3 次仍失败则停止自动补跑（防止持续故障每 15 分钟
  空跑），次日自动恢复或手动运行；成功后计数归零
- **子进程隔离**：daily_job 崩溃/卡死（周一 store 概念周刷实测 35min~2h）不影响 API 服务
- 配置环境变量：
  - `EVENT_STUDY_DAILY_TIME=HH:MM`（默认 08:30，非法值回退 08:30）
  - `EVENT_STUDY_DAILY_CHECK_INTERVAL`（分钟，默认 15）
  - `EVENT_STUDY_DAILY_MAX_ATTEMPTS`（默认 3）

### Windows 守护（让 uvicorn 开机常驻）

**NSSM 注册为 Windows 服务**（推荐，服务崩溃自动拉起）：

```powershell
nssm install LiveProfitEventAPI "D:\code\workspace\python\Liveprofit\.venv\Scripts\python.exe" ^
  "-m uvicorn AI.eventStudy.api.main:app --host 0.0.0.0 --port 8100"
nssm set LiveProfitEventAPI AppDirectory "D:\code\workspace\python\Liveprofit"
nssm set LiveProfitEventAPI Start SERVICE_AUTO_START
nssm start LiveProfitEventAPI
```

**备选**：任务计划程序开机触发（一次性，进程崩溃不自动拉起）：

```powershell
schtasks /Create /TN "LiveProfit事件研究API服务" ^
  /TR "D:\code\workspace\python\Liveprofit\.venv\Scripts\python.exe -m uvicorn AI.eventStudy.api.main:app --host 0.0.0.0 --port 8100" ^
  /SC ONSTART /RL LIMITED /F
```

---

## 方案 A：schtasks 每日触发（无常驻服务时用）

部署环境为 Windows，使用任务计划程序（schtasks）每天早间触发 `daily_job.py`。
日后迁移 Linux 可等价换成 cron。

## 任务内容（方案 A）

```bat
@echo off
cd /d D:\code\workspace\python\Liveprofit
D:\code\workspace\python\Liveprofit\.venv\Scripts\python.exe -m AI.eventStudy.scheduler.daily_job
```

> 若已 `pip install -e .`，等价命令：`python -m AI.eventStudy.scheduler.daily_job`。
> 路径按实际部署目录调整。

## 注册计划任务（管理员 PowerShell）

```powershell
schtasks /Create /TN "LiveProfit事件研究每日批处理" ^
  /TR "cmd /c D:\code\workspace\python\Liveprofit\AI\eventStudy\scheduler\run_daily.bat" ^
  /SC DAILY /ST 08:30 ^
  /RL LIMITED /F
```

建议用批处理文件包装（避免引号转义问题），`run_daily.bat` 内容即上方任务内容。

## 常用管理命令

```powershell
schtasks /Query /TN "LiveProfit事件研究每日批处理"      # 查看任务
schtasks /Run  /TN "LiveProfit事件研究每日批处理"       # 手动立即运行
schtasks /End  /TN "LiveProfit事件研究每日批处理"       # 终止运行中的任务
schtasks /Delete /TN "LiveProfit事件研究每日批处理" /F  # 删除任务
```

## 手动运行 / 调试

```bash
# 全量（默认行情/上下文区间为过去 2 年）
python -m AI.eventStudy.scheduler.daily_job

# 指定区间 + 跳过爬虫
python -m AI.eventStudy.scheduler.daily_job --start-date 2024-01-01 --end-date 2026-08-17 --skip crawl
```

## 失败告警（3.9.3 风险）

任务计划程序默认静默失败。当前以日志文件 + 退出码记录：
- 各步骤独立 try/except，单步失败不影响后续，错误写入 `logs/event_study_daily.log`
- 严重失败（schema 初始化失败）以退出码 1 结束；任务计划程序可勾选
  "如果任务失败，重新启动"（间隔 1 小时，最多 3 次）
- 邮件通知：可在任务属性的"操作"中添加 PowerShell 脚本，
  检查退出码非 0 时 `Send-MailMessage`（需 SMTP 配置），暂不内置
