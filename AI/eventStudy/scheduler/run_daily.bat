@echo off
rem 事件研究系统每日批处理（由 Windows 任务计划程序每天 08:30 触发）
rem 日志输出: logs/event_study_daily.log
cd /d D:\code\workspace\python\Liveprofit
D:\code\workspace\python\Liveprofit\.venv\Scripts\python.exe -m AI.eventStudy.scheduler.daily_job
