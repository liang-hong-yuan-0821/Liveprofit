"""迁移集成测试：临时库升级/降级/约束断言（本地 PostgreSQL，无 LLM/Provider 依赖）。

- 使用独立测试库 liveprofit_platform_test，不触碰业务库 liveprofit。
- 事件研究既有表不属于平台 metadata：断言迁移只创建平台表集合。
- PostgreSQL 不可达时 skip（CI 无 Docker 环境不阻塞其他测试）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEST_DB_NAME = "liveprofit_platform_test"

PLATFORM_TABLES = {
    "analysis_tasks",
    "task_outbox",
    "analysis_reports",
    "agent_prompt_overrides",
    # concept_hotness_snapshots 随 0006 删除；market_assets/market_bars_daily/
    # market_index_factors 随 0007 删除（0007 已落地）
    "macro_information",
    "watchlists",
    "watchlist_items",
    "portfolios",
    "portfolio_positions",
}

EVENT_STUDY_TABLES = {"assets", "events", "market_data", "event_impacts", "market_context", "predictions"}

ANALYSIS_TASK_COLUMNS = {
    "id", "task_type", "status", "request_params", "selected_layers", "ticker",
    "requested_trade_date", "effective_trade_date", "date_correction", "input_hash",
    "idempotency_key", "attempt_no", "lease_token", "lease_expires_at", "heartbeat_at",
    "worker_id", "next_retry_at", "cancel_requested_at", "error_code", "error_summary",
    "config_snapshot", "core_version", "started_at", "finished_at", "created_at", "updated_at",
}
TASK_OUTBOX_COLUMNS = {
    "id", "task_id", "attempt_no", "message_type", "payload", "status",
    "dispatch_lease_token", "dispatch_lease_expires_at", "retry_count",
    "next_attempt_at", "published_at", "trace_context", "created_at", "updated_at",
}
ANALYSIS_REPORT_COLUMNS = {
    "id", "task_id", "attempt_no", "report_version", "schema_version", "report_json",
    "conclusion_summary", "risk_flag", "risk_hint", "has_report", "decision",
    "artifact_uri", "checksum", "core_version", "generated_at", "created_at", "updated_at",
}
# MARKET_INDEX_FACTOR_COLUMNS 列断言已删（旧表随 0007 DROP；factor_daily 列
# 定义在 db/instrument/schema.sql，列检查由 init_schema 建表与 DAO 测试覆盖）


def _base_url() -> str | None:
    """从 Settings 解析业务库 URL（LIVEPROFIT_DATABASE_URL / PG_* 回退）。"""
    from backend.bootstrap.settings import CoreSettings

    return CoreSettings().resolved_database_url()


def _test_url(base_url: str) -> str:
    # postgresql+psycopg://user:pass@host:port/db[?sslmode=...] → 替换 db 名
    main, _, query = base_url.partition("?")
    prefix, _, _db = main.rpartition("/")
    url = f"{prefix}/{TEST_DB_NAME}"
    return f"{url}?{query}" if query else url


def _create_test_database(base_url: str) -> None:
    engine = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        engine.dispose()


def _drop_test_database(base_url: str) -> None:
    engine = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    finally:
        engine.dispose()


def _alembic_config(test_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    # 传给 env.py 的 -x db_url=... 覆盖（env.py 优先读取 x 参数）
    cfg.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={test_url}"], "name": None})()
    return cfg


@pytest.fixture(scope="module")
def test_db() -> tuple[str, str]:
    base_url = _base_url()
    if not base_url:
        pytest.skip("缺少数据库 URL（LIVEPROFIT_DATABASE_URL / PG_* 未配置）")
    engine = create_engine(base_url)
    try:
        with engine.connect():
            pass
    except Exception:  # noqa: BLE001 - PostgreSQL 不可达（如 CI 无 Docker）
        engine.dispose()
        pytest.skip("PostgreSQL 不可达，跳过迁移集成测试")
    engine.dispose()

    _create_test_database(base_url)
    test_url = _test_url(base_url)
    yield base_url, test_url
    _drop_test_database(base_url)


def _upgrade(test_url: str) -> None:
    command.upgrade(_alembic_config(test_url), "head")


def test_upgrade_creates_exactly_platform_tables(test_db):
    _, test_url = test_db
    _upgrade(test_url)

    engine = create_engine(test_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        created = tables - {"alembic_version"}
        assert created == PLATFORM_TABLES, f"迁移创建表集合不符：{created}"
        # 事件研究既有表语义不被触碰：迁移不得创建它们
        assert EVENT_STUDY_TABLES.isdisjoint(created)
    finally:
        engine.dispose()


def test_required_columns_present(test_db):
    _, test_url = test_db
    _upgrade(test_url)

    engine = create_engine(test_url)
    try:
        inspector = inspect(engine)
        assert ANALYSIS_TASK_COLUMNS <= {c["name"] for c in inspector.get_columns("analysis_tasks")}
        assert TASK_OUTBOX_COLUMNS <= {c["name"] for c in inspector.get_columns("task_outbox")}
        assert ANALYSIS_REPORT_COLUMNS <= {c["name"] for c in inspector.get_columns("analysis_reports")}
    finally:
        engine.dispose()


def test_unique_constraints_enforced(test_db):
    _, test_url = test_db
    _upgrade(test_url)

    engine = create_engine(test_url)
    task_id = uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO analysis_tasks "
                    "(id, task_type, status, request_params, selected_layers, input_hash, idempotency_key) "
                    "VALUES (:id, 'SINGLE_STOCK', 'PENDING', '{}', '[\"market\"]', 'h1', :key)"
                ),
                {"id": task_id, "key": "key-dup"},
            )
        # 同 idempotency_key 重复
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO analysis_tasks "
                        "(id, task_type, status, request_params, selected_layers, input_hash, idempotency_key) "
                        "VALUES (:id, 'SINGLE_STOCK', 'PENDING', '{}', '[\"market\"]', 'h2', :key)"
                    ),
                    {"id": uuid.uuid4(), "key": "key-dup"},
                )
        # task_outbox (task_id, attempt_no) 唯一
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO task_outbox (id, task_id, attempt_no, message_type, payload, status) "
                    "VALUES (:id, :task_id, 1, 'analysis_task', '{}', 'PENDING')"
                ),
                {"id": uuid.uuid4(), "task_id": task_id},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO task_outbox (id, task_id, attempt_no, message_type, payload, status) "
                        "VALUES (:id, :task_id, 1, 'analysis_task', '{}', 'PENDING')"
                    ),
                    {"id": uuid.uuid4(), "task_id": task_id},
                )
        # watchlist_items 同分组同标的唯一
        watchlist_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO watchlists (id, name) VALUES (:id, :name)"),
                {"id": watchlist_id, "name": f"w-{uuid.uuid4().hex[:8]}"},
            )
            conn.execute(
                text(
                    "INSERT INTO watchlist_items (id, watchlist_id, market, symbol, display_order) "
                    "VALUES (:id, :wid, 'CN', '000001.SH', 0)"
                ),
                {"id": uuid.uuid4(), "wid": watchlist_id},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO watchlist_items (id, watchlist_id, market, symbol, display_order) "
                        "VALUES (:id, :wid, 'CN', '000001.SH', 1)"
                    ),
                    {"id": uuid.uuid4(), "wid": watchlist_id},
                )
        # portfolios 名称唯一
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO portfolios (id, name) VALUES (:id, :name)"), {"id": uuid.uuid4(), "name": "p-dup"})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO portfolios (id, name) VALUES (:id, :name)"), {"id": uuid.uuid4(), "name": "p-dup"})
    finally:
        engine.dispose()


def test_downgrade_then_upgrade_is_idempotent(test_db):
    _, test_url = test_db
    cfg = _alembic_config(test_url)
    command.downgrade(cfg, "base")

    engine = create_engine(test_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert tables <= {"alembic_version"}, f"downgrade 后残留表：{tables}"
    finally:
        engine.dispose()

    _upgrade(test_url)
    engine = create_engine(test_url)
    try:
        inspector = inspect(engine)
        assert PLATFORM_TABLES <= set(inspector.get_table_names())
    finally:
        engine.dispose()
