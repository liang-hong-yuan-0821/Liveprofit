"""契约测试环境：临时 PG 库（liveprofit_contract_test）+ Redis db 11。

契约测试验证 HTTP/SSE 协议层（envelope、DTO、错误体、SSE 帧），
服务层使用真实 SQL Repository（fake 图由后续用例注入时再扩展）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import redis as redis_lib
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[4]
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


def _redis_test_url() -> str | None:
    from backend.bootstrap.settings import CoreSettings

    url = CoreSettings().resolved_redis_url()
    if not url:
        return None
    main, _, query = url.partition("?")
    head, _, _db = main.rpartition("/")
    test_url = f"{head}/{REDIS_TEST_DB}"
    return f"{test_url}?{query}" if query else test_url


@pytest.fixture(scope="module")
def client():
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
        yield ContractEnv(http=test_client, redis=redis_client)

    redis_client.flushdb()
    redis_client.close()
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    admin.dispose()


class ContractEnv:
    def __init__(self, http, redis) -> None:
        self.http = http
        self.redis = redis


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
                conn.execute(
                    sql_text(
                        "TRUNCATE portfolio_positions, portfolios, watchlist_items, watchlists, "
                        "macro_information, analysis_reports, task_outbox, analysis_tasks, "
                        "concept_hotness_snapshots, market_bars_daily CASCADE"
                    )
                )
            break
        except OperationalError:
            time.sleep(0.2 * (attempt + 1))
    test_engine.dispose()
    client.redis.flushdb()
    yield
