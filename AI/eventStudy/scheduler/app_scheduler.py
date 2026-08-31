"""
每日批处理常驻调度器（方案 B：挂 FastAPI lifespan，替代 schtasks）

每天 EVENT_STUDY_DAILY_TIME（默认 08:30，本地时区）以子进程方式触发
daily_job；电脑睡眠/服务停机错过触发点后自动补跑。

补跑机制（三层触发 + 两个标记文件）：
- 完成标记 logs/daily_job_done.YYYYMMDD：子进程退出码 0 时原子写入
  （temp+rename）；存在即"今日已完成"，所有触发层共用此判定
- 运行锁 logs/daily_job.running（内容=子进程 pid）：防并发重复拉起；
  服务启动时按 pid 存活清理陈旧标记
- 触发层：
  1. cron 08:30 正常触发（misfire_grace_time 30 分钟兜底短时停机）
  2. 服务启动时立即自检：已过触发点且今日未完成 → 立即补跑
     （覆盖"服务 08:30 前停机、之后才启动"）
  3. 每 EVENT_STUDY_DAILY_CHECK_INTERVAL（默认 15 分钟）周期自检：
     覆盖睡眠唤醒场景——睡眠期间调度器冻结且无启动事件，唤醒后由
     周期自检发现"已过点未完成"补跑
- 失败次数上限 EVENT_STUDY_DAILY_MAX_ATTEMPTS（默认 3）：当日自动尝试
  超过上限后停止自动补跑（等次日或手动运行），防止持续故障每 15 分钟空跑

子进程隔离：daily_job 崩溃/卡死（周一 store 概念周刷实测 35min~2h）
不影响 API 服务；日志沿用 logs/event_study_daily.log。
uvicorn 运行约束：单 worker、禁用 --reload（避免调度器重复启动）。
"""

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

DAILY_TIME = os.getenv("EVENT_STUDY_DAILY_TIME", "08:30")   # HH:MM，本地时区
MISFIRE_GRACE = 30 * 60                                      # cron 错过补跑窗口（秒）
LOCK_PATH = Path("logs/daily_job.running")                   # 运行中标记（内容=子进程 pid）
ATTEMPTS_PATH = Path("logs/daily_job_attempts.json")         # 当日自动尝试计数

try:
    CHECK_INTERVAL = max(1, int(os.getenv("EVENT_STUDY_DAILY_CHECK_INTERVAL", "15")))
except ValueError:
    CHECK_INTERVAL = 15
try:
    MAX_ATTEMPTS = max(0, int(os.getenv("EVENT_STUDY_DAILY_MAX_ATTEMPTS", "3")))
except ValueError:
    MAX_ATTEMPTS = 3

_scheduler = None
_run_lock = threading.Lock()   # 进程内防并发（与文件锁双保险）


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _marker_path() -> Path:
    return Path("logs") / f"daily_job_done.{_today_str()}"


def _today_done() -> bool:
    return _marker_path().exists()


def _write_done_marker() -> None:
    """完成标记原子写入（temp+rename，沿用项目惯例）。"""
    marker = _marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
    tmp.write_text(datetime.now().isoformat(), encoding="utf-8")
    tmp.replace(marker)


def _read_attempts() -> int:
    """当日自动尝试次数（跨天自动归零）。"""
    try:
        data = json.loads(ATTEMPTS_PATH.read_text(encoding="utf-8"))
        return data["count"] if data.get("date") == _today_str() else 0
    except (OSError, ValueError, TypeError):
        return 0


def _write_attempts(count: int) -> None:
    ATTEMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ATTEMPTS_PATH.with_name(f"{ATTEMPTS_PATH.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"date": _today_str(), "count": count},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(ATTEMPTS_PATH)


def _parse_daily_time(value: str) -> tuple:
    try:
        hour, minute = value.strip().split(":")
        hour, minute = int(hour), int(minute)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except (ValueError, AttributeError):
        logger.error("EVENT_STUDY_DAILY_TIME 非法: %r，回退 08:30", value)
        return 8, 30


def _is_pid_alive(pid: int) -> bool:
    """Windows tasklist 判断进程存活（bytes 包含匹配，规避 GBK 解码问题）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=10,
        ).stdout
        return str(pid).encode() in out
    except Exception:
        return False


def _cleanup_stale_lock() -> None:
    """服务启动时清理陈旧标记：pid 已不存在 → 上一轮被强杀残留，可安全删除。"""
    if not LOCK_PATH.exists():
        return
    pid = None
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None and _is_pid_alive(pid):
        logger.info("检测到 daily_job 仍在运行（pid %d），保留运行标记", pid)
        return
    logger.warning("清理陈旧运行标记 %s（pid=%s 已不存在）", LOCK_PATH, pid)
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _run_daily_job() -> None:
    """执行 daily_job（子进程）。今日已完成 / 正在运行 / 超自动尝试上限时跳过。"""
    if not _run_lock.acquire(blocking=False):
        return   # 另一触发层正在执行，进程内防并发
    try:
        _run_daily_job_locked()
    finally:
        _run_lock.release()


def _run_daily_job_locked() -> None:
    if _today_done():
        logger.info("今日 daily_job 已完成（%s 存在），跳过", _marker_path())
        return
    if LOCK_PATH.exists():
        logger.warning("daily_job 上一轮仍在运行（%s 存在），跳过本次触发", LOCK_PATH)
        return
    attempts = _read_attempts()
    if attempts >= MAX_ATTEMPTS:
        logger.warning("今日自动尝试已达上限 %d 次，停止自动补跑"
                       "（可手动运行 daily_job，或明日自动恢复）", MAX_ATTEMPTS)
        return
    _write_attempts(attempts + 1)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "AI.eventStudy.scheduler.daily_job"]
    try:
        proc = subprocess.Popen(cmd)
        # 标记写入子进程 pid：服务被杀后 pid 失效 → 下次启动按陈旧清理
        LOCK_PATH.write_text(str(proc.pid), encoding="utf-8")
    except OSError as e:
        logger.error("daily_job 子进程拉起失败: %s", e)
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return
    logger.info("daily_job 子进程已启动（pid %d，今日第 %d 次自动尝试）",
                proc.pid, attempts + 1)
    try:
        proc.wait()
        if proc.returncode != 0:
            logger.error("daily_job 子进程退出码 %d（见 logs/event_study_daily.log），"
                         "今日完成标记不写入", proc.returncode)
        else:
            _write_attempts(0)
            _write_done_marker()
            logger.info("daily_job 子进程完成，今日完成标记已写入 %s", _marker_path())
    finally:
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass


def _maybe_catch_up() -> None:
    """自检补跑：已过今日触发点且未完成 → 触发 _run_daily_job
    （其内部含完成/运行中/次数上限判定）。"""
    now = datetime.now()
    hour, minute = _parse_daily_time(
        os.getenv("EVENT_STUDY_DAILY_TIME", DAILY_TIME))
    if (now.hour, now.minute) < (hour, minute):
        return
    _run_daily_job()


def start_daily_scheduler() -> None:
    """启动常驻调度器（幂等）：cron 触发 + 周期自检 + 启动立即自检。"""
    global _scheduler
    if _scheduler is not None:
        return
    _cleanup_stale_lock()
    hour, minute = _parse_daily_time(
        os.getenv("EVENT_STUDY_DAILY_TIME", DAILY_TIME))
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _run_daily_job,
        CronTrigger(hour=hour, minute=minute),
        id="daily_job",
        coalesce=True,          # 积压多次触发合并为一次
        max_instances=1,        # 同一时刻最多一个实例（防重复）
        misfire_grace_time=MISFIRE_GRACE,   # 短时停机后 30 分钟内补跑
    )
    _scheduler.add_job(
        _maybe_catch_up,
        IntervalTrigger(minutes=CHECK_INTERVAL),
        id="daily_job_catchup",    # 周期自检：覆盖睡眠唤醒（调度器冻结无启动事件）
        coalesce=True,
        max_instances=1,
        misfire_grace_time=None,
    )
    # 启动立即自检（一次性，用 daemon 线程——DateTrigger 对 naive datetime
    # 会按 UTC 换算产生 8 小时 misfire 告警，实测噪音）
    threading.Thread(target=_maybe_catch_up, daemon=True,
                     name="daily_job_startup_check").start()
    _scheduler.start()
    logger.info("daily_job 调度器已启动：每天 %02d:%02d（本地时区），"
                "周期自检每 %d 分钟，自动尝试上限 %d 次/日，misfire 补跑窗口 %d 分钟",
                hour, minute, CHECK_INTERVAL, MAX_ATTEMPTS, MISFIRE_GRACE // 60)


def stop_daily_scheduler() -> None:
    """停止调度器（FastAPI lifespan shutdown 调用）。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("daily_job 调度器已停止")
