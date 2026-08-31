"""
单元测试：daily_job 常驻调度器（app_scheduler，方案 B + 补跑机制）

- 触发时间解析（env EVENT_STUDY_DAILY_TIME / 非法值回退 08:30）
- start：三个 job（cron 触发 / 周期自检 / 启动自检）参数正确、幂等
- _run_daily_job：子进程命令形态、成功后写完成标记、失败不写标记并计尝试次数、
  今日已完成/运行中/超尝试上限跳过、尝试次数跨天归零
- _maybe_catch_up：触发点前不补跑、触发点后补跑
- _cleanup_stale_lock：pid 不存在 → 清理；pid 存活 → 保留
"""

import json
from datetime import datetime as _real_dt
from unittest.mock import MagicMock

import pytest

from AI.eventStudy.scheduler import app_scheduler as sch


class _FakeDateTime(_real_dt):
    _now = _real_dt(2026, 8, 31, 8, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._now


@pytest.fixture
def env(monkeypatch, tmp_path):
    """隔离文件路径 + 重置调度器全局；返回 (lock, attempts, marker) 三个路径。"""
    monkeypatch.setattr(sch, "_scheduler", None)
    lock = tmp_path / "running"
    attempts = tmp_path / "attempts.json"
    marker = tmp_path / "done.marker"
    monkeypatch.setattr(sch, "LOCK_PATH", lock)
    monkeypatch.setattr(sch, "ATTEMPTS_PATH", attempts)
    monkeypatch.setattr(sch, "_marker_path", lambda: marker)
    return lock, attempts, marker


def _fake_proc(returncode=0):
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    return proc


# ==================== 触发时间解析 ====================

def test_parse_daily_time():
    assert sch._parse_daily_time("9:05") == (9, 5)
    assert sch._parse_daily_time("08:30") == (8, 30)


def test_parse_daily_time_invalid_falls_back():
    assert sch._parse_daily_time("bogus") == (8, 30)
    assert sch._parse_daily_time("25:99") == (8, 30)


# ==================== start / stop ====================

def test_start_registers_jobs_and_startup_check_thread(monkeypatch, env):
    fake = MagicMock()
    monkeypatch.setattr(sch, "BackgroundScheduler", lambda: fake)
    monkeypatch.setattr(sch, "_cleanup_stale_lock", MagicMock())
    fake_thread = MagicMock()
    monkeypatch.setattr(sch.threading, "Thread", MagicMock(return_value=fake_thread))
    monkeypatch.setenv("EVENT_STUDY_DAILY_TIME", "09:15")
    sch.start_daily_scheduler()

    calls = fake.add_job.call_args_list
    ids = [c.kwargs["id"] for c in calls]
    assert ids == ["daily_job", "daily_job_catchup"]
    # cron 正常触发
    cron_call = calls[0]
    assert str(cron_call.args[1]) == "cron[hour='9', minute='15']"
    assert cron_call.kwargs["coalesce"] is True
    assert cron_call.kwargs["max_instances"] == 1
    assert cron_call.kwargs["misfire_grace_time"] == 30 * 60
    # 周期自检（睡眠唤醒补跑的关键层）
    interval_call = calls[1]
    assert "interval" in str(interval_call.args[1])
    assert interval_call.kwargs["misfire_grace_time"] is None
    fake.start.assert_called_once()
    # 启动立即自检经 daemon 线程执行（规避 DateTrigger 时区 misfire 告警）
    fake_thread.start.assert_called_once()


def test_start_is_idempotent(monkeypatch, env):
    fake = MagicMock()
    monkeypatch.setattr(sch, "_scheduler", fake)
    sch.start_daily_scheduler()
    fake.add_job.assert_not_called()


def test_stop_shuts_down(monkeypatch, env):
    fake = MagicMock()
    monkeypatch.setattr(sch, "_scheduler", fake)
    sch.stop_daily_scheduler()
    fake.shutdown.assert_called_once_with(wait=False)
    assert sch._scheduler is None


# ==================== _run_daily_job ====================

def test_success_writes_marker_and_resets_attempts(monkeypatch, env):
    lock, attempts, marker = env
    monkeypatch.setattr(sch.subprocess, "Popen",
                        MagicMock(return_value=_fake_proc(0)))
    sch._run_daily_job()

    cmd = sch.subprocess.Popen.call_args[0][0]
    assert cmd == [sch.sys.executable, "-m", "AI.eventStudy.scheduler.daily_job"]
    assert marker.exists()                 # 完成标记已写入
    assert not lock.exists()               # 运行锁已清理
    data = json.loads(attempts.read_text(encoding="utf-8"))
    assert data["count"] == 0              # 成功后尝试计数归零


def test_skips_when_today_done(monkeypatch, env):
    _, _, marker = env
    marker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sch.subprocess, "Popen", MagicMock())
    sch._run_daily_job()
    sch.subprocess.Popen.assert_not_called()


def test_skips_when_locked(monkeypatch, env):
    lock, _, _ = env
    lock.write_text("99999", encoding="utf-8")
    monkeypatch.setattr(sch.subprocess, "Popen", MagicMock())
    sch._run_daily_job()
    sch.subprocess.Popen.assert_not_called()
    assert lock.exists()                   # 他方标记不被误删


def test_failure_no_marker_and_attempts_counted(monkeypatch, env):
    lock, attempts, marker = env
    monkeypatch.setattr(sch.subprocess, "Popen",
                        MagicMock(return_value=_fake_proc(3)))
    sch._run_daily_job()
    sch._run_daily_job()
    assert not marker.exists()             # 失败不写完成标记
    assert not lock.exists()
    data = json.loads(attempts.read_text(encoding="utf-8"))
    assert data["count"] == 2              # 失败尝试计数（用于上限判定）


def test_attempts_cap_stops_auto_retry(monkeypatch, env):
    _, attempts, _ = env
    attempts.write_text(json.dumps({"date": sch._today_str(), "count": 3}),
                        encoding="utf-8")
    monkeypatch.setattr(sch.subprocess, "Popen", MagicMock())
    sch._run_daily_job()
    sch.subprocess.Popen.assert_not_called()   # 达上限停止自动补跑


def test_attempts_reset_on_new_day(monkeypatch, env):
    _, attempts, marker = env
    attempts.write_text(json.dumps({"date": "19990101", "count": 5}),
                        encoding="utf-8")
    monkeypatch.setattr(sch.subprocess, "Popen",
                        MagicMock(return_value=_fake_proc(0)))
    sch._run_daily_job()
    sch.subprocess.Popen.assert_called_once()   # 跨天计数归零，可再跑


# ==================== _maybe_catch_up ====================

def test_catch_up_not_before_scheduled_time(monkeypatch, env):
    before = type("_B", (_FakeDateTime,), {})
    before._now = _real_dt(2026, 8, 31, 7, 0, 0)
    monkeypatch.setattr(sch, "datetime", before)
    monkeypatch.setattr(sch, "_run_daily_job", MagicMock())
    sch._maybe_catch_up()
    sch._run_daily_job.assert_not_called()      # 08:30 前不补跑


def test_catch_up_runs_after_scheduled_time(monkeypatch, env):
    after = type("_A", (_FakeDateTime,), {})
    after._now = _real_dt(2026, 8, 31, 14, 0, 0)   # 睡眠唤醒场景：已过点
    monkeypatch.setattr(sch, "datetime", after)
    monkeypatch.setattr(sch, "_run_daily_job", MagicMock())
    sch._maybe_catch_up()
    sch._run_daily_job.assert_called_once()


# ==================== 陈旧锁清理 ====================

def test_cleanup_stale_lock_removes_dead_pid(monkeypatch, tmp_path):
    lock = tmp_path / "r"
    lock.write_text("424242", encoding="utf-8")
    monkeypatch.setattr(sch, "LOCK_PATH", lock)
    monkeypatch.setattr(sch, "_is_pid_alive", lambda pid: False)
    sch._cleanup_stale_lock()
    assert not lock.exists()


def test_cleanup_stale_lock_keeps_alive_pid(monkeypatch, tmp_path):
    lock = tmp_path / "r"
    lock.write_text("424242", encoding="utf-8")
    monkeypatch.setattr(sch, "LOCK_PATH", lock)
    monkeypatch.setattr(sch, "_is_pid_alive", lambda pid: True)
    sch._cleanup_stale_lock()
    assert lock.exists()
