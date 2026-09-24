"""真实 Redis/Broker/PG 仅连接专用测试基础设施。"""
import pytest
from sqlalchemy import text

from backend.tests.market_refresh_support import assert_test_connections, redis_database_url


def _wait(process):
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr
    assert "isolated connection verified" in stdout


def test_broker_namespaces_do_not_cross(refresh_env_factory):
    first, second = refresh_env_factory(), refresh_env_factory()
    _wait(first.start("produce", "first"))
    _wait(second.start("produce", "second"))
    _wait(second.start("consume"))
    assert first.redis.lrange(first.key_prefix + "received", 0, -1) == []
    assert second.redis.lrange(second.key_prefix + "received", 0, -1) == ["second"]
    _wait(first.start("consume"))
    assert first.redis.lrange(first.key_prefix + "received", 0, -1) == ["first"]
    first.close()
    assert not second.redis.exists(first.key_prefix + "received")
    assert second.redis.lrange(second.key_prefix + "received", 0, -1) == ["second"]


@pytest.mark.parametrize("case", ["write_then_clear", "reverse_order"])
def test_trade_status_has_no_previous_case(env, case):
    with env["session_factory"]() as session:
        assert session.execute(text("SELECT count(*) FROM market.trade_status_daily")).scalar_one() == 0
        session.execute(text("INSERT INTO market.trade_status_daily "
                             "(ts_code,trade_date,is_suspended,source) "
                             "VALUES ('600000.SH','2026-09-22',true,'test')"))
        session.commit()


@pytest.mark.parametrize("db", [0, 10, 11, 13])
def test_rejects_other_redis_database(env, db):
    with pytest.raises(ValueError, match="Redis DB"):
        assert_test_connections(env["psycopg_dsn"], f"redis://localhost/{db}")


def test_rejects_production_pg():
    with pytest.raises(ValueError, match="PostgreSQL"):
        assert_test_connections("dbname=liveprofit", "redis://localhost/12")


def test_url_query_cannot_override_test_database(env):
    url = redis_database_url("redis://localhost/0?db=0&socket_timeout=2", 12)
    assert_test_connections(env["psycopg_dsn"], url)
