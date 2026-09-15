"""market_data 集成测试环境：临时 PG 库（liveprofit_market_test）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[4]
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
def env():
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
    # 逐表列出 market 表名（11 张）——TRUNCATE 后用例自 seed（ts_code 直插）
    with env["session_factory"]() as session:
        session.execute(text(
            "TRUNCATE market.instrument, market.instrument_daily, market.factor_daily, "
            "market.adj_factor, market.sector, market.sector_member, market.sector_daily, "
            "market.industry, market.industry_member, market.fund_info, market.stock_info CASCADE"
        ))
        session.commit()
    yield
