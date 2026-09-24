"""Real spawned children with isolated connections, never real provider calls."""
import os
import subprocess
import sys
import time

import dramatiq

from pydantic import SecretStr
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL

from backend.bootstrap.settings import Settings, CoreSettings, MarketRefreshSettings
from backend.workers.market_refresh import MarketWorkerRuntime, publish_refresh
from db.instrument.db import get_connection
from db.instrument.ingest.guard import IngestGuard


def _blocked_child(settings, job, output):
    time.sleep(60)


def _commit_then_die(settings, job, output):
    with get_connection(settings.core.resolved_market_dsn()) as conn:
        conn.execute(
            "INSERT INTO market.instrument_daily(ts_code,trade_date,open,high,low,close,source,updated_at) "
            "VALUES ('000001.SH','2026-09-22',10,11,9,10,'test-fixture',now())"
        )
        conn.commit()
    os._exit(44)


def _settings(env, **overrides):
    dsn = conninfo_to_dict(env.pg_dsn)
    url = URL.create("postgresql+psycopg", username=dsn.get("user"), password=dsn.get("password"), host=dsn.get("host"), port=int(dsn.get("port", 5432)), database=dsn["dbname"]).render_as_string(hide_password=False)
    return Settings(core=CoreSettings(database_url=SecretStr(url), redis_url=SecretStr(env.redis_url)),
                    market_refresh=MarketRefreshSettings(key_prefix=env.key_prefix, broker_namespace=env.broker_namespace, **overrides))


def _job(runtime, units):
    spec = dict(resource="CN_INDEX_BARS", target_trade_date="2026-09-22", codes=["000001.SH"], dates=["2026-09-22"], units=units)
    return runtime.store.ensure("CN_INDEX_BARS", "2026-09-22", spec, "auto", time.time())["job_id"]


def test_spawn_rechecks_pg_and_completes_without_provider(refresh_env):
    runtime = MarketWorkerRuntime(_settings(refresh_env))
    try:
        # Empty frozen missing set represents a task already covered before execution.
        job_id = _job(runtime, [])
        runtime.execute(job_id)
        job = runtime.store.job(job_id)
        assert job["status"] == "SUCCEEDED", job["error_code"]
        assert job["result"]["total"] == 0
        runtime.execute(job_id)  # duplicate delivery must be ignored
        assert runtime.store.job(job_id)["attempt"] == 1
        assert runtime.child is None
    finally:
        runtime.close()


def test_live_pg_owner_prevents_spawned_child_from_fetching(refresh_env):
    runtime = MarketWorkerRuntime(_settings(refresh_env))
    try:
        job_id = _job(runtime, [dict(code="000001.SH", trade_date="2026-09-22")])
        with get_connection(refresh_env.pg_dsn) as conn:
            guard = IngestGuard(conn)
            assert guard.try_acquire()
            try:
                runtime.execute(job_id)
                job = runtime.store.job(job_id)
                assert job["status"] == "QUEUED" and not job.get("run_token"), job["error_code"]
                assert runtime.store.context("CN_INDEX_BARS", "2026-09-22", time.time())["budget"] == []
            finally:
                guard.release()
        assert runtime.child is None
    finally:
        runtime.close()


def test_supervisor_reaps_blocked_process_after_bounded_timeout(refresh_env):
    config = _settings(refresh_env, child_timeout_seconds=1, actor_timeout_seconds=5,
                       terminate_grace_seconds=1, kill_grace_seconds=1)
    runtime = MarketWorkerRuntime(config, child_target=_blocked_child)
    try:
        job_id = _job(runtime, [dict(code="000001.SH", trade_date="2026-09-22")])
        runtime.execute(job_id)
        job = runtime.store.job(job_id)
        assert job["status"] == "RETRY_WAIT"
        assert job["error_code"] == "COLLECTION_TIMEOUT"
        assert runtime.child is None
    finally:
        runtime.close()


def test_supervisor_rechecks_pg_when_child_dies_without_payload(refresh_env):
    runtime = MarketWorkerRuntime(_settings(refresh_env), child_target=_commit_then_die)
    try:
        # Durable PG facts win even if the child dies before sending its result.
        job_id = _job(runtime, [dict(code="000001.SH", trade_date="2026-09-22")])
        runtime.execute(job_id)
        job = runtime.store.job(job_id)
        assert job["status"] == "SUCCEEDED", job["error_code"]
        assert job["result"]["completed"] == 1
    finally:
        runtime.close()


def test_dedicated_broker_consumes_only_market_queue(refresh_env):
    settings = _settings(refresh_env)
    child_env = os.environ.copy()
    child_env.update({
        "LIVEPROFIT_DATABASE_URL": settings.core.resolved_database_url(),
        "LIVEPROFIT_REDIS_URL": refresh_env.redis_url,
        "MARKET_REFRESH_KEY_PREFIX": refresh_env.key_prefix,
        "MARKET_REFRESH_BROKER_NAMESPACE": refresh_env.broker_namespace,
        "MARKET_REFRESH_AUTO_ENABLED": "true",
    })
    process = subprocess.Popen(
        [sys.executable, "-c", "from backend.cli import market_worker_main; raise SystemExit(market_worker_main())"],
        env=child_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        runtime = MarketWorkerRuntime(settings)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not runtime.store.workers():
                assert process.poll() is None, "market worker exited before announcing"
                time.sleep(0.2)
            assert runtime.store.workers(), "market worker did not announce"
            job_id = _job(runtime, [])
            analysis_broker = dramatiq.get_broker()
            publish_refresh(settings, job_id)
            assert dramatiq.get_broker() is analysis_broker
            while time.monotonic() < deadline and runtime.store.job(job_id)["status"] != "SUCCEEDED":
                time.sleep(0.2)
            assert runtime.store.job(job_id)["status"] == "SUCCEEDED"
        finally:
            runtime.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
