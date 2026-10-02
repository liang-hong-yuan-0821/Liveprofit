"""契约测试环境：临时 PG 库（liveprofit_contract_test）+ Redis db 11。

契约测试验证 HTTP/SSE 协议层（envelope、DTO、错误体、SSE 帧），
服务层使用真实 SQL Repository（fake 图由后续用例注入时再扩展）。
"""

from __future__ import annotations

from tests.support.python.paths import PROJECT_ROOT

from pathlib import Path

import pytest
import redis as redis_lib
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, text

TEST_DB_NAME = "liveprofit_contract_test"
REDIS_TEST_DB = 11


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


def _redis_test_url() -> str | None:
    from backend.bootstrap.settings import CoreSettings

    url = CoreSettings().resolved_redis_url()
    if not url:
        return None
    from tests.support.python.market_refresh_support import redis_database_url
    return redis_database_url(url, REDIS_TEST_DB)


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
def client(_exclusive_test_database):
    from fastapi.testclient import TestClient

    from backend.bootstrap.settings import ApiSettings, CoreSettings, Settings
    from backend.main import create_app

    base_url = _base_db_url()
    redis_url = _redis_test_url()
    if not base_url or not redis_url:
        pytest.skip("缺少 DATABASE_URL / REDIS_URL")
    from tests.support.python.market_refresh_support import assert_test_connections
    assert_test_connections(_psycopg_dsn(_test_db_url(base_url)), redis_url, contract=True)
    engine = create_engine(base_url)
    try:
        with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        engine.dispose()
        pytest.skip("PostgreSQL 不可达，跳过契约测试")
    engine.dispose()

    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    try:
        redis_client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis 不可达，跳过契约测试")

    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin.dispose()
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    cfg.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={_test_db_url(base_url)}"], "name": None})()
    command.upgrade(cfg, "head")
    # market schema 建表（次序定稿 R1 minor 12：先赋值 db.instrument.db 模块常量
    # → 再 init_schema——注入晚于它则落到真实库建表）
    import db.instrument.db as market_db
    _prev_market_dsn = market_db.PG_CONNECTION_STRING
    market_db.PG_CONNECTION_STRING = _psycopg_dsn(_test_db_url(base_url))
    from db.instrument.db import init_schema
    assert init_schema(), "market schema 初始化失败"
    redis_client.flushdb()

    settings = Settings(
        core=CoreSettings(
            env="local",
            database_url=SecretStr(_test_db_url(base_url)),
            redis_url=SecretStr(redis_url),
        ),
        api=ApiSettings(),
    )
    with TestClient(create_app(settings)) as test_client:
        publisher = FakeRefreshPublisher()
        test_client.app.state.market_refresh_publisher = publisher
        yield ContractEnv(http=test_client, redis=redis_client, publisher=publisher)

    redis_client.flushdb()
    redis_client.close()
    # 注入还原（CR M6）：必须在 TestClient 退出后——请求期 market_conn 读
    # 模块全局，提前还原会让请求连回真实库
    market_db.PG_CONNECTION_STRING = _prev_market_dsn
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    admin.dispose()


class ContractEnv:
    def __init__(self, http, redis, publisher=None) -> None:
        self.http = http
        self.redis = redis
        self.publisher = publisher


class FakeRefreshPublisher:
    """契约 fixture 禁止接入真实 Broker。"""
    def __init__(self):
        self.job_ids = []

    def send(self, job_id: str):
        self.job_ids.append(job_id)

    def __call__(self, job_id: str):
        self.send(job_id)


@pytest.fixture(autouse=True)
def _clean_platform_state(client):
    import time

    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import OperationalError

    from backend.bootstrap.settings import CoreSettings

    base_url = CoreSettings().resolved_database_url()
    test_engine = create_engine(_test_db_url(base_url))
    # 与应用内后台任务（指标刷新等）竞态时 TRUNCATE 可能死锁/冲突：有限重试
    for attempt in range(3):
        try:
            with test_engine.begin() as conn:
                # 仅在新建的隔离库临时关闭业务防写触发器（market 防清表、量化不可变
                # 历史等，含 TRUNCATE CASCADE 可能级联命中的表）；事务失败会回滚此 DDL。
                for toggle in ("DISABLE", "ENABLE"):
                    conn.execute(
                        sql_text(
                            f"""
                            DO $$ DECLARE r record; BEGIN
                              FOR r IN SELECT schemaname, tablename FROM pg_tables
                                       WHERE schemaname IN ('public', 'market') LOOP
                                EXECUTE format('ALTER TABLE %I.%I {toggle} TRIGGER ALL',
                                               r.schemaname, r.tablename);
                              END LOOP;
                            END $$
                            """
                        )
                    )
                    if toggle == "DISABLE":
                        conn.execute(
                            sql_text(
                                "TRUNCATE order_fill_events, position_intents, position_daily_facts, "
                                "position_expectations, position_trailing_stops, suggested_orders, "
                                "position_lifecycle_states, lifecycle_policy_versions, "
                                "quant_execution_signals, quant_strategy_versions, quant_strategies, "
                                "portfolio_positions, portfolios, watchlist_items, watchlists, "
                                "macro_information, analysis_reports, task_outbox, analysis_tasks, "
                                "market.instrument, market.instrument_daily, market.factor_daily, "
                                "market.adj_factor, market.trade_status_daily, market.sector, market.sector_member, "
                                "market.sector_daily, market.industry, market.industry_member, market.ingest_state, "
                                "market.fund_info, market.stock_info, market.suspension_evidence, "
                                "market.stock_st_source_batch, market.stock_st_source_conflict, "
                                "market.stock_st_adjudication CASCADE"
                            )
                        )
            break
        except OperationalError:
            time.sleep(0.2 * (attempt + 1))
    else:
        # 清理是契约用例的前提：重试耗尽必须显式失败，不能带着脏表继续执行。
        test_engine.dispose()
        raise RuntimeError("contract 隔离库清理 3 次重试后仍失败（TRUNCATE 死锁/冲突）")
    test_engine.dispose()
    client.redis.flushdb()
    client.publisher.job_ids.clear()
    yield
