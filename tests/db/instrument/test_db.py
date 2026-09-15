"""db.instrument.db 连接生命周期与 init_schema 幂等测试。

本文件连真实库执行**无副作用操作**：仅连接生命周期断言与"写后必
rollback"的异常路径断言（rollback 测试以行数不变兜底）；不依赖 pg_env
测试库（注入还原后同进程后续用例不受影响）。
"""

import pytest

from db.instrument import db as db_module
from db.instrument.db import get_connection, init_schema


def test_get_connection_closes_on_exit():
    with get_connection() as conn:
        raw = conn
        assert raw.closed is False
        raw.execute("SELECT 1")
    assert raw.closed is True


def test_get_connection_rollback_on_exception():
    with pytest.raises(RuntimeError):
        with get_connection() as conn:
            # 写入一行后抛异常——contextmanager 必须 rollback，行不落库
            conn.execute(
                "INSERT INTO market.industry (source, industry_code, name) "
                "VALUES ('TEST', '999999', '测试行业')"
            )
            raise RuntimeError("boom")
    with get_connection() as conn:
        count = conn.execute(
            "SELECT count(*) FROM market.industry "
            "WHERE source='TEST' AND industry_code='999999'"
        ).fetchone()[0]
    assert count == 0


def test_init_schema_idempotent():
    # 连真实库重复执行（幂等：全部 IF NOT EXISTS / ON CONFLICT DO NOTHING）
    assert init_schema() is True
    assert init_schema() is True


def test_cli_init_schema_smoke(pg_env, clean_market_state):
    """CLI 冒烟（R2 收尾 2 / CR F4）：run.sh 真实调用路径
    `python -m db.instrument.db --init-schema` → 退出码 0 且 market 表存在。"""
    import subprocess
    import sys

    import db.instrument.db as market_db

    prev = market_db.PG_CONNECTION_STRING
    market_db.PG_CONNECTION_STRING = pg_env["psycopg_dsn"]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "db.instrument.db", "--init-schema"],
            capture_output=True, text=True, timeout=60,
        )
    finally:
        market_db.PG_CONNECTION_STRING = prev
    assert result.returncode == 0, result.stderr
    import psycopg as _psycopg
    conn = _psycopg.connect(pg_env["psycopg_dsn"], connect_timeout=5)
    try:
        row = conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='market' AND table_name='instrument'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 1


def test_dsn_connection_string_priority(monkeypatch):
    # 注入载体 = 模块全局直赋值（setenv 静默失效，方案 3.1.1 定稿）
    monkeypatch.setattr(db_module, "PG_CONNECTION_STRING", "host=injected")
    assert db_module.dsn() == "host=injected"
    monkeypatch.setattr(db_module, "PG_CONNECTION_STRING", None)
    monkeypatch.setattr(db_module, "PG_HOST", "localhost")
    assert "127.0.0.1" in db_module.dsn()  # loopback 归一化
    monkeypatch.setattr(db_module, "PG_HOST", "remote.host")
    assert "remote.host" in db_module.dsn()  # 非 loopback 不归一
