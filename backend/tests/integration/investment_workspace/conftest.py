"""investment_workspace 集成测试环境：临时 PG 库（liveprofit_workspace_test）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[4]
TEST_DB_NAME = "liveprofit_workspace_test"


def _base_db_url() -> str | None:
    from backend.bootstrap.settings import CoreSettings

    return CoreSettings().resolved_database_url()


def _test_db_url(base_url: str) -> str:
    main, _, query = base_url.partition("?")
    prefix, _, _db = main.rpartition("/")
    url = f"{prefix}/{TEST_DB_NAME}"
    return f"{url}?{query}" if query else url


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
        pytest.skip("PostgreSQL 不可达，跳过 integration/investment_workspace")
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

    test_engine = create_engine(_test_db_url(base_url))
    yield {"session_factory": sessionmaker(bind=test_engine, expire_on_commit=False)}

    test_engine.dispose()
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(autouse=True)
def _clean_workspace_state(env):
    with env["session_factory"]() as session:
        session.execute(
            text(
                "TRUNCATE portfolio_positions, portfolios, watchlist_items, watchlists, "
                "analysis_reports, task_outbox, analysis_tasks CASCADE"
            )
        )
        session.commit()
    yield
