"""Settings 分组校验与 Secret 掩码单测（无网络）。"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from backend.bootstrap.settings import (
    ApiSettings,
    CoreSettings,
    DispatcherSettings,
    EventStudySettings,
    Settings,
    SettingsValidationError,
    WorkerSettings,
)

DB_URL = "postgresql+psycopg://liveprofit:secret@127.0.0.1:5432/liveprofit"
REDIS_URL = "redis://:secret@127.0.0.1:6379/0"


def make_settings(**overrides) -> Settings:
    defaults = dict(
        core=CoreSettings(env="local", database_url=SecretStr(DB_URL), redis_url=SecretStr(REDIS_URL)),
        api=ApiSettings(),
        worker=WorkerSettings(),
        dispatcher=DispatcherSettings(),
        event_study=EventStudySettings(),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_api_missing_database_url_raises(monkeypatch):
    monkeypatch.delenv("PG_HOST", raising=False)
    monkeypatch.delenv("PG_USER", raising=False)
    monkeypatch.delenv("PG_PASSWORD", raising=False)
    settings = make_settings(core=CoreSettings(env="local", database_url=None, redis_url=SecretStr(REDIS_URL)))
    with pytest.raises(SettingsValidationError, match="DATABASE_URL"):
        settings.validate_for_process("api")


def test_api_missing_redis_url_raises(monkeypatch):
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_CONNECTION_STRING", raising=False)
    settings = make_settings(core=CoreSettings(env="local", database_url=SecretStr(DB_URL), redis_url=None))
    with pytest.raises(SettingsValidationError, match="REDIS_URL"):
        settings.validate_for_process("api")


def test_local_env_rejects_non_loopback_api_host():
    settings = make_settings(api=ApiSettings(host="0.0.0.0"))
    with pytest.raises(SettingsValidationError, match="loopback"):
        settings.validate_for_process("api")


def test_container_env_allows_non_loopback_api_host():
    settings = make_settings(
        core=CoreSettings(env="container", database_url=SecretStr(DB_URL), redis_url=SecretStr(REDIS_URL)),
        api=ApiSettings(host="0.0.0.0"),
    )
    settings.validate_for_process("api")  # 不应抛错


def test_worker_lease_heartbeat_invariants():
    # heartbeat >= lease/2 拒绝
    settings = make_settings(worker=WorkerSettings(lease_ttl_seconds=120, heartbeat_interval_seconds=60))
    with pytest.raises(SettingsValidationError, match="heartbeat"):
        settings.validate_for_process("worker")
    # 默认值通过（30 < 120/2）
    make_settings().validate_for_process("worker")


def test_worker_rejects_non_positive_max_attempt_runtime():
    settings = make_settings(worker=WorkerSettings(max_attempt_runtime_seconds=0))
    with pytest.raises(SettingsValidationError, match="max_attempt_runtime"):
        settings.validate_for_process("worker")


def test_dispatcher_rejects_invalid_batch_size():
    settings = make_settings(dispatcher=DispatcherSettings(batch_size=0))
    with pytest.raises(SettingsValidationError, match="batch_size"):
        settings.validate_for_process("dispatcher")
    make_settings().validate_for_process("dispatcher")


def test_event_study_must_be_positive():
    settings = make_settings(event_study=EventStudySettings(timeout_seconds=0))
    with pytest.raises(SettingsValidationError, match="event_study"):
        settings.validate_for_process("api")


def test_secrets_masked_in_repr():
    core = CoreSettings(
        env="local",
        api_key=SecretStr("sk-test-secret"),
        database_url=SecretStr(DB_URL),
        redis_url=SecretStr(REDIS_URL),
    )
    rendered = repr(core)
    # 独立断言：任一泄漏都必须失败（不得用恒真支兜底）
    assert "sk-test-secret" not in rendered  # API Key 明文
    assert "liveprofit:secret@" not in rendered  # DB URL 内嵌密码
    assert ":secret@127.0.0.1" not in rendered  # Redis URL 内嵌密码
    assert DB_URL not in rendered  # 完整明文 URL 整体不出现
    assert "**********" in rendered


def test_database_url_falls_back_to_pg_vars(monkeypatch):
    monkeypatch.setenv("PG_HOST", "127.0.0.1")
    monkeypatch.setenv("PG_USER", "u")
    monkeypatch.setenv("PG_PASSWORD", "p")
    monkeypatch.setenv("PG_PORT", "5433")
    monkeypatch.setenv("PG_DATABASE", "db")
    monkeypatch.delenv("PG_SSLMODE", raising=False)
    core = CoreSettings(env="local", database_url=None)
    url = core.resolved_database_url()
    assert url == "postgresql+psycopg://u:p@127.0.0.1:5433/db"


def test_redis_url_falls_back_to_redis_vars(monkeypatch):
    monkeypatch.delenv("REDIS_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_DB", "2")
    core = CoreSettings(env="local", redis_url=None)
    assert core.resolved_redis_url() == "redis://:pw@127.0.0.1:6380/2"


def test_api_process_validates_all_required_groups():
    settings = make_settings()
    settings.validate_for_process("api")
    settings.validate_for_process("worker")
    settings.validate_for_process("dispatcher")
