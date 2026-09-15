"""db.instrument 集成测试环境：临时 PG 库（liveprofit_instrument_test）。

建库 → 注入 PG_CONNECTION_STRING（模块常量直赋值）→ init_schema →
逐用例 TRUNCATE 11 张 market 表（test_db.py 的连接生命周期测试不受 env 影响，
独立跑真实库安全操作）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

TEST_DB_NAME = "liveprofit_instrument_test"


def _base_db_url() -> str | None:
    from backend.bootstrap.settings import CoreSettings

    return CoreSettings().resolved_database_url()


def _test_db_url(base_url: str) -> str:
    main, _, query = base_url.partition("?")
    prefix, _, _db = main.rpartition("/")
    url = f"{prefix}/{TEST_DB_NAME}"
    return f"{url}?{query}" if query else url


def _psycopg_dsn(sqlalchemy_url: str) -> str:
    from sqlalchemy.engine.url import make_url

    u = make_url(sqlalchemy_url)
    parts = [f"host={u.host}", f"port={u.port or 5432}",
             f"dbname={u.database}", f"user={u.username}",
             f"password={u.password or ''}"]
    sslmode = u.query.get("sslmode")
    if sslmode:
        parts.append(f"sslmode={sslmode}")
    return " ".join(parts)


@pytest.fixture(scope="module")
def pg_env():
    base_url = _base_db_url()
    if not base_url:
        pytest.skip("缺少 DATABASE_URL")
    engine = create_engine(base_url)
    try:
        with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        engine.dispose()
        pytest.skip("PostgreSQL 不可达，跳过 db.instrument 集成测试")
    engine.dispose()

    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin.dispose()

    # 注入模块常量 → init_schema（次序定稿：注入晚于 init_schema 则落到真实库）
    import db.instrument.db as market_db
    _prev_market_dsn = market_db.PG_CONNECTION_STRING
    market_db.PG_CONNECTION_STRING = _psycopg_dsn(_test_db_url(base_url))
    try:
        from db.instrument.db import init_schema
        assert init_schema(), "market schema 初始化失败"
    except Exception:
        market_db.PG_CONNECTION_STRING = _prev_market_dsn
        raise

    test_engine = create_engine(_test_db_url(base_url))
    yield {"psycopg_dsn": _psycopg_dsn(_test_db_url(base_url)),
           "engine": test_engine}

    test_engine.dispose()
    market_db.PG_CONNECTION_STRING = _prev_market_dsn
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def clean_market_state(pg_env):
    """逐用例清空 11 张 market 表（仅集成测试显式依赖，单测不触发建库）。"""
    with pg_env["engine"].begin() as conn:
        conn.execute(text(
            "TRUNCATE market.instrument, market.instrument_daily, market.factor_daily, "
            "market.adj_factor, market.sector, market.sector_member, market.sector_daily, "
            "market.industry, market.industry_member, market.fund_info, market.stock_info CASCADE"
        ))
    yield
