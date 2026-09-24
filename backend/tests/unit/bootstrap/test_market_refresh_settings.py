import pytest
from pydantic import SecretStr, ValidationError
from psycopg.conninfo import conninfo_to_dict

from backend.bootstrap.settings import CoreSettings, MarketRefreshSettings, Settings, SettingsValidationError


def test_refresh_defaults_and_switches(monkeypatch):
    monkeypatch.setenv("MARKET_REFRESH_ENABLED", "false")
    monkeypatch.setenv("MARKET_REFRESH_AUTO_ENABLED", "false")
    config = Settings().market_refresh
    assert not config.enabled and not config.auto_enabled
    assert config.auto_max_attempts == 3
    assert config.rolling_max_attempts == 6
    assert config.auto_retry_delays_seconds == (900, 3600)
    assert config.dispatch_retry_delays_seconds == (300, 900)
    assert config.child_timeout_seconds == 5400
    assert config.actor_timeout_seconds == 5700
    assert config.publish_lag_seconds["KR_INDEX_BARS"] == 14400
    assert config.publish_lag_seconds["CN_STOCK_DAILY"] == 18000


def test_market_dsn_uses_explicit_platform_connection(monkeypatch):
    monkeypatch.setenv("PG_DATABASE", "legacy_database")
    config = CoreSettings(database_url=SecretStr("postgresql+psycopg://test:p%40ss%27word@localhost:5432/isolated?sslmode=disable"))
    params = conninfo_to_dict(config.resolved_market_dsn())
    assert params["dbname"] == "isolated"
    assert params["password"] == "p@ss'word"
    assert params["host"] == "127.0.0.1"
    assert "p@ss" not in repr(config)


def test_market_dsn_uses_legacy_platform_fallback(monkeypatch):
    monkeypatch.setenv("PG_HOST", "localhost")
    monkeypatch.setenv("PG_DATABASE", "legacy_test")
    config = CoreSettings(database_url=None)
    assert conninfo_to_dict(config.resolved_market_dsn())["dbname"] == "legacy_test"


@pytest.mark.parametrize("values", [
    {"actor_timeout_seconds": 5401}, {"worker_ttl_seconds": 20},
    {"key_prefix": "test:*:"}, {"publish_lag_seconds": {}},
    {"budget_ttl_seconds": 1}, {"max_dispatches": 5},
])
def test_invalid_refresh_bounds(values):
    with pytest.raises(ValidationError):
        MarketRefreshSettings(**values)


def test_broker_reconfiguration_fails_without_credentials(monkeypatch):
    from backend.workers import broker
    monkeypatch.setattr(broker, "_configured", True)
    monkeypatch.setattr(broker, "_configuration", ("redis://secret@localhost/12", "first"))
    with pytest.raises(RuntimeError) as error:
        broker.configure_broker("redis://other@localhost/11", namespace="second")
    assert "secret" not in str(error.value) and "other" not in str(error.value)


def test_market_worker_requires_platform_connections(monkeypatch):
    monkeypatch.delenv("PG_HOST", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_CONNECTION_STRING", raising=False)
    settings = Settings(core=CoreSettings(database_url=None, redis_url=None))
    with pytest.raises(SettingsValidationError, match="DATABASE_URL"):
        settings.validate_for_process("market_worker")
    with pytest.raises(SettingsValidationError, match="未知"):
        settings.validate_for_process("typo")


def test_explicit_market_connection_overrides_cli_fallback(monkeypatch):
    from types import SimpleNamespace
    from db.instrument import db
    called = []
    connection = SimpleNamespace(execute=lambda sql: None, close=lambda: called.append("closed"))
    monkeypatch.setattr(db, "PG_CONNECTION_STRING", "dbname=legacy")
    monkeypatch.setattr(db.psycopg, "connect", lambda dsn, **kwargs: (called.append(dsn), connection)[1])
    with db.get_connection("dbname=explicit_test"):
        pass
    with db.get_connection():
        pass
    assert called == ["dbname=explicit_test", "closed", "dbname=legacy", "closed"]
