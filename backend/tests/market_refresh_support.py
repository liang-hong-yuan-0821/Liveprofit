"""Market refresh 测试专用连接护栏与子进程 Broker 装配。"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from psycopg.conninfo import conninfo_to_dict
from redis import Redis


def redis_database_url(url: str, db: int) -> str:
    parts = urlsplit(url)
    # 去除 query 中可能覆盖 /DB 的 db 参数。
    from urllib.parse import parse_qsl, urlencode
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != "db"])
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", query, ""))


def assert_test_connections(pg_dsn: str, redis_url: str, *, contract: bool = False) -> None:
    expected_db = "liveprofit_contract_test" if contract else "liveprofit_market_test"
    if conninfo_to_dict(pg_dsn).get("dbname") != expected_db:
        raise ValueError("拒绝非专用测试 PostgreSQL 数据库")
    client = Redis.from_url(redis_url)
    try:
        redis_db = int(client.connection_pool.connection_kwargs.get("db", 0))
        if redis_db != (11 if contract else 12):
            raise ValueError("拒绝非专用测试 Redis DB")
    finally:
        client.close()


@dataclass
class RefreshTestEnvironment:
    pg_dsn: str = field(repr=False)
    redis_url: str = field(repr=False)
    key_prefix: str
    broker_namespace: str
    redis: Redis = field(repr=False)
    processes: list = field(default_factory=list, repr=False)

    def start(self, action: str, payload: str = ""):
        assert_test_connections(self.pg_dsn, self.redis_url)
        child_env = dict(os.environ)
        child_env.update({
            "MARKET_TEST_PG_DSN": self.pg_dsn,
            "MARKET_TEST_REDIS_URL": self.redis_url,
            "MARKET_TEST_PREFIX": self.key_prefix,
            "MARKET_TEST_NAMESPACE": self.broker_namespace,
            "PYTHONUTF8": "1",
        })
        process = subprocess.Popen(
            [sys.executable, "-m", "backend.tests.market_refresh_probe", action, payload],
            env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.processes.append(process)
        return process

    def keys(self) -> list:
        return list(self.redis.scan_iter(match=self.key_prefix + "*")) + list(
            self.redis.scan_iter(match=self.broker_namespace + ":*"))

    def close(self):
        # 先关闭写入者，随后仅清本次随机 namespace。
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=20)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
        assert all(p.poll() is not None for p in self.processes)
        assert_test_connections(self.pg_dsn, self.redis_url)
        keys = self.keys()
        if keys:
            self.redis.delete(*keys)
        assert not self.keys()
        self.redis.close()


from contextlib import contextmanager


@contextmanager
def exclusive_test_database(base_url, database_name):
    """Serialize users of a fixed disposable test DB; no production rows are changed."""
    import psycopg
    import pytest
    from backend.bootstrap.settings import database_url_to_dsn
    if database_name not in {"liveprofit_market_test", "liveprofit_contract_test"}:
        raise ValueError("refuse non-test database lease")
    try:
        conn = psycopg.connect(database_url_to_dsn(base_url), connect_timeout=5, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL 不可达，跳过隔离测试")
    try:
        conn.execute("SELECT pg_advisory_lock(hashtext(%s))", ("pytest:"+database_name,))
        yield
    finally:
        conn.close()
