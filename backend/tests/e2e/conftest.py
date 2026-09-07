"""E2E 环境：临时 PG 库（liveprofit_e2e_test）+ Redis db 12 + 真实 API 进程内应用。

浏览器级 E2E（Playwright + Compose）依赖前端工程与容器镜像，另行标记阻塞；
本层覆盖 API→Dispatcher→Worker(fake graph)→SSE/报告/看板的完整链路。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import redis as redis_lib
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEST_DB_NAME = "liveprofit_e2e_test"
REDIS_TEST_DB = 12


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


class E2EEnv:
    def __init__(self, http, redis, session_factory) -> None:
        self.http = http
        self.redis = redis
        self.session_factory = session_factory


@pytest.fixture(scope="module")
def env():
    from fastapi.testclient import TestClient

    from backend.bootstrap.settings import ApiSettings, CoreSettings, Settings
    from backend.main import create_app

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
        pytest.skip("PostgreSQL 不可达，跳过 E2E")
    engine.dispose()

    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    try:
        redis_client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis 不可达，跳过 E2E")

    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin.dispose()
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    cfg.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={_test_db_url(base_url)}"], "name": None})()
    command.upgrade(cfg, "head")
    redis_client.flushdb()

    settings = Settings(
        core=CoreSettings(
            env="local",
            database_url=SecretStr(_test_db_url(base_url)),
            redis_url=SecretStr(redis_url),
        ),
        api=ApiSettings(),
    )
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(_test_db_url(base_url))
    session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    with TestClient(create_app(settings)) as test_client:
        yield E2EEnv(http=test_client, redis=redis_client, session_factory=session_factory)

    test_engine.dispose()
    redis_client.flushdb()
    redis_client.close()
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(autouse=True)
def _clean_e2e_state(env):
    with env.session_factory() as session:
        session.execute(
            text(
                "TRUNCATE portfolio_positions, portfolios, watchlist_items, watchlists, "
                "macro_information, analysis_reports, task_outbox, analysis_tasks CASCADE"
            )
        )
        session.commit()
    env.redis.flushdb()
    yield
