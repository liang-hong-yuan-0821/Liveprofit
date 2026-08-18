# 定时任务调度配置说明（Windows 任务计划程序）

部署环境为 Windows，使用任务计划程序（schtasks）每天早间触发 `daily_job.py`。
日后迁移 Linux 可等价换成 cron。日志输出到 `logs/event_study_daily.log`。

## 任务内容

```bat
@echo off
cd /d D:\code\workspace\python\Liveprofit
D:\code\workspace\python\Liveprofit\.venv\Scripts\python.exe -m AI.eventStudy.scheduler.daily_job
```

> 若已 `pip install -e .`，等价命令：`python -m AI.eventStudy.scheduler.daily_job`。
> 路径按实际部署目录调整。

## 注册计划任务（管理员 PowerShell）

```powershell
schtasks /Create /TN "YoHo事件研究每日批处理" ^
  /TR "cmd /c D:\code\workspace\python\Liveprofit\AI\eventStudy\scheduler\run_daily.bat" ^
  /SC DAILY /ST 08:30 ^
  /RL LIMITED /F
```

建议用批处理文件包装（避免引号转义问题），`run_daily.bat` 内容即上方任务内容。

## 常用管理命令

```powershell
schtasks /Query /TN "YoHo事件研究每日批处理"      # 查看任务
schtasks /Run  /TN "YoHo事件研究每日批处理"       # 手动立即运行
schtasks /End  /TN "YoHo事件研究每日批处理"       # 终止运行中的任务
schtasks /Delete /TN "YoHo事件研究每日批处理" /F  # 删除任务
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
