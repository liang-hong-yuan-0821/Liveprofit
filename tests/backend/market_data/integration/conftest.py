"""market_data 集成测试环境：临时 PG 库（liveprofit_market_test）。"""

from __future__ import annotations

from tests.support.python.paths import PROJECT_ROOT

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DB_NAME = "liveprofit_market_test"


def _base_db_url() -> str | None:
    from backend.bootstrap.settings import CoreSettings

    return CoreSettings().resolved_database_url()


def _test_db_url(base_url: str) -> str:
    main, _, query = base_url.partition("?")
    prefix, _, _db = main.rpartition("/")
    url = f"{prefix}/{TEST_DB_NAME}"
    return f"{url}?{query}" if query else url


def _psycopg_dsn(sqlalchemy_url: str) -> str:
    """SQLAlchemy URL → psycopg conninfo（db.instrument 直连注入用）。"""
    from backend.bootstrap.settings import database_url_to_dsn
    return database_url_to_dsn(sqlalchemy_url)


@pytest.fixture(scope="module")
def _exclusive_test_database():
    # A second pytest process must not DROP the fixed test DB while another uses it.
    from tests.support.python.market_refresh_support import exclusive_test_database
    base_url = _base_db_url()
    if not base_url:
        pytest.skip("缺少 DATABASE_URL")
    with exclusive_test_database(base_url, TEST_DB_NAME):
        yield


@pytest.fixture(scope="module")
def env(_exclusive_test_database):
    base_url = _base_db_url()
    if not base_url:
        pytest.skip("缺少 DATABASE_URL")
    engine = create_engine(base_url)
    try:
        with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        engine.dispose()
        pytest.skip("PostgreSQL 不可达，跳过 integration/market_data")
    engine.dispose()

    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin.dispose()
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    cfg.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={_test_db_url(base_url)}"], "name": None})()
    command.upgrade(cfg, "head")
    # market schema 建表（次序定稿：先注入模块常量再 init_schema）
    import db.instrument.db as market_db
    _prev_market_dsn = market_db.PG_CONNECTION_STRING
    market_db.PG_CONNECTION_STRING = _psycopg_dsn(_test_db_url(base_url))
    from db.instrument.db import init_schema
    assert init_schema(), "market schema 初始化失败"

    test_engine = create_engine(_test_db_url(base_url))
    yield {"session_factory": sessionmaker(bind=test_engine, expire_on_commit=False),
           "psycopg_dsn": _psycopg_dsn(_test_db_url(base_url))}

    test_engine.dispose()
    # 注入还原（CR M6）：yield 期间服务层测试经 market_conn 读模块全局，
    # 还原须在测试全部结束后
    market_db.PG_CONNECTION_STRING = _prev_market_dsn
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(autouse=True)
def _clean_market_state(env):
    # 逐表列出隔离库 market 表——TRUNCATE 后用例自 seed（ts_code 直插）
    with env["session_factory"]() as session:
        # 固定隔离库的清理事务临时关闭防清表触发器，随后恢复。
        protected = ("instrument_daily", "adj_factor", "factor_daily",
                     "trade_status_daily", "fact_revision")
        for table in protected:
            session.execute(text(f"ALTER TABLE market.{table} DISABLE TRIGGER fact_history_no_truncate"))
        session.execute(text(
            "TRUNCATE market.instrument, market.instrument_daily, market.factor_daily, "
            "market.adj_factor, market.trade_status_daily, market.sector, market.sector_member, market.sector_daily, "
            "market.industry, market.industry_member, market.ingest_state, market.fund_info, "
            "market.stock_info, market.fact_revision CASCADE"
        ))
        for table in protected:
            session.execute(text(f"ALTER TABLE market.{table} ENABLE TRIGGER fact_history_no_truncate"))
        session.commit()
    yield


@pytest.fixture
def refresh_env_factory(env):
    """每次调用一套随机双 namespace；PG 与既有 env 共用隔离测试库。"""
    import uuid
    import redis
    from backend.bootstrap.settings import CoreSettings
    from tests.support.python.market_refresh_support import (
        RefreshTestEnvironment, assert_test_connections, redis_database_url,
    )
    url = CoreSettings().resolved_redis_url()
    if not url:
        pytest.skip("缺少 Redis URL")
    url = redis_database_url(url, 12)
    assert_test_connections(env["psycopg_dsn"], url)
    created = []

    def factory():
        client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=3, socket_timeout=5)
        try:
            client.ping()
        except redis.RedisError:
            client.close()
            pytest.skip("测试 Redis 不可达")
        run_id = uuid.uuid4().hex
        result = RefreshTestEnvironment(
            pg_dsn=env["psycopg_dsn"], redis_url=url,
            key_prefix=f"test:{run_id}:market-refresh:",
            broker_namespace=f"test:{run_id}:broker", redis=client,
        )
        assert not result.keys()
        created.append(result)
        return result

    yield factory
    for result in created:
        result.close()


@pytest.fixture
def refresh_env(refresh_env_factory):
    return refresh_env_factory()
