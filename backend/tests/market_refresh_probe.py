"""仅供隔离测试的新进程生产/消费器，不导入生产采集 actor。"""
from __future__ import annotations

import os
import sys

from backend.tests.market_refresh_support import assert_test_connections


def main():
    import dramatiq
    import psycopg
    import redis
    from dramatiq import Worker
    from backend.workers.broker import configure_broker

    pg_dsn = os.environ["MARKET_TEST_PG_DSN"]
    redis_url = os.environ["MARKET_TEST_REDIS_URL"]
    prefix = os.environ["MARKET_TEST_PREFIX"]
    namespace = os.environ["MARKET_TEST_NAMESPACE"]
    assert_test_connections(pg_dsn, redis_url)
    broker = configure_broker(redis_url, namespace=namespace)
    assert broker.namespace == namespace
    assert broker.client.connection_pool.connection_kwargs["db"] == 12
    with psycopg.connect(pg_dsn, connect_timeout=5) as conn:
        assert conn.execute("SELECT current_database()").fetchone()[0] == "liveprofit_market_test"
    client = redis.Redis.from_url(redis_url, decode_responses=True)

    @dramatiq.actor(broker=broker, actor_name="refresh_isolation_probe", queue_name="market-data", max_retries=0)
    def probe(value):
        client.rpush(prefix + "received", value)

    worker = None
    try:
        if sys.argv[1] == "produce":
            probe.send(sys.argv[2])
        elif sys.argv[1] == "consume":
            worker = Worker(broker, queues=["market-data"], worker_threads=1, worker_timeout=100)
            worker.start()
            broker.join("market-data", timeout=10000)
        else:
            raise ValueError("unknown probe action")
        print("isolated connection verified")
    finally:
        if worker:
            worker.stop(timeout=5000)
        broker.close()
        client.close()


if __name__ == "__main__":
    main()
