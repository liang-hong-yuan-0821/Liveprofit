"""T4 集成测试环境：临时 PG 库（liveprofit_analysis_test）+ Redis db 10（flushdb 隔离）。

无 LLM/Provider 依赖；PG/Redis 不可达时整组 skip。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import redis as redis_lib
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[4]
TEST_DB_NAME = "liveprofit_analysis_test"
REDIS_TEST_DB = 10


def _base_db_url() -> str | None:
    from backend.bootstrap.settings import CoreSettings

    return CoreSettings().resolved_database_url()


def _test_db_url(base_url: str) -> str:
    main, _, query = base_url.partition("?")
    prefix, _, _db = main.rpartition("/")
    url = f"{prefix}/{TEST_DB_NAME}"
    return f"{url}?{query}" if query else url


def _redis_test_url() -> str | None:
    from backend.bootstrap.settings import CoreSettings

    url = CoreSettings().resolved_redis_url()
    if not url:
        return None
    main, _, query = url.partition("?")
    head, _, _db = main.rpartition("/")
    test_url = f"{head}/{REDIS_TEST_DB}"
    return f"{test_url}?{query}" if query else test_url


def _prepare_test_db(base_url: str, test_url: str) -> None:
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        admin.dispose()
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    cfg.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={test_url}"], "name": None})()
    command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def env():
    base_url = _base_db_url()
    redis_url = _redis_test_url()
    if not base_url or not redis_url:
        pytest.skip("缺少 DATABASE_URL / REDIS_URL")
    engine = create_engine(base_url)
    try:
        with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        engine.dispose()
        pytest.skip("PostgreSQL 不可达，跳过 integration/analysis")
    engine.dispose()

    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    try:
        redis_client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis 不可达，跳过 integration/analysis")

    _prepare_test_db(base_url, _test_db_url(base_url))
    redis_client.flushdb()

    test_engine = create_engine(_test_db_url(base_url))
    yield {
        "session_factory": sessionmaker(bind=test_engine, expire_on_commit=False),
        "redis": redis_client,
        "base_url": base_url,
        "test_url": _test_db_url(base_url),
    }

    test_engine.dispose()
    redis_client.flushdb()
    redis_client.close()
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    finally:
        admin.dispose()


@pytest.fixture(autouse=True)
def _clean_platform_state(env):
    """每个测试前清空平台表与 Redis，保证用例隔离（env 为 module 级共享）。"""
    from sqlalchemy import text as sql_text

    with env["session_factory"]() as session:
        session.execute(sql_text("TRUNCATE analysis_reports, task_outbox, analysis_tasks CASCADE"))
        session.commit()
    env["redis"].flushdb()
    yield
