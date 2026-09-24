"""Dedicated market-data consumer with a bounded, supervised collection process."""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import threading
import time
from dataclasses import asdict
from functools import lru_cache, partial
from uuid import uuid4

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from redis import Redis

from backend.modules.market_data.infrastructure.redis_refresh_store import (
    RedisRefreshStore,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _publishing_broker(redis_url: str, namespace: str):
    # Dispatcher already owns the analysis default broker; a market namespace
    # must not reconfigure that process-global broker.
    broker = RedisBroker(url=redis_url, namespace=namespace)
    broker.declare_queue("market-data")
    return broker


def publish_refresh(settings, job_id):
    broker = _publishing_broker(settings.core.resolved_redis_url(), settings.market_refresh.broker_namespace)
    broker.enqueue(dramatiq.Message(queue_name="market-data", actor_name="market_refresh", args=(job_id,), kwargs={}, options={}))


def _collect_child(settings, job, output):
    """Spawn entrypoint: connections and providers are created only in the child."""
    from backend.modules.market_data.infrastructure.refresh_repository import (
        RefreshRepository,
    )
    from db.instrument.db import get_connection
    from db.instrument.ingest.guard import IngestGuard
    from db.instrument.ingest.refresh import RefreshSpec, collect_refresh
    config = settings.market_refresh
    client = Redis.from_url(settings.core.resolved_redis_url(), socket_connect_timeout=3, socket_timeout=3)
    store = RedisRefreshStore(client, config)
    job_id, token = job["id"], job["run_token"]
    stop = threading.Event()
    deadline = time.monotonic()+config.child_timeout_seconds

    def watchdog():
        while not stop.wait(min(config.heartbeat_interval_seconds, 2)):
            try:
                parent = mp.parent_process()
                alive = (parent is None or parent.is_alive()) and store.owns(job_id, token)
            except Exception:
                alive = False
            if not alive or time.monotonic() >= deadline:
                # A blocking provider cannot be interrupted safely by a Python exception.
                # Exiting this process closes the actual PG lock connection as well.
                os._exit(72)
    thread = threading.Thread(target=watchdog, daemon=True)
    thread.start()
    try:
        factory = partial(get_connection, settings.core.resolved_market_dsn())
        repository = RefreshRepository(factory)
        with factory() as conn:
            guard = IngestGuard(conn, fence=lambda: store.owns(job_id, token), changed=store.changed)
            if not guard.try_acquire():
                output.send({"busy": True})
                return
            try:
                verified = repository.verify_spec(job["target_spec"], conn=conn)
                units = verified["missing_units"]
                if units:
                    spec = job["target_spec"]
                    if job["resource"] == "CN_STOCK_QUANT_INPUTS":
                        request = RefreshSpec(
                            resource=job["resource"], target_trade_date=job["target_trade_date"],
                            codes=tuple(spec["codes"]), dates=tuple(spec["dates"]),
                            component_missing_units=tuple(spec.get("component_missing_units", ())),
                            component_thresholds=spec.get("component_thresholds", {}),
                            operation_units=tuple(spec.get("units", ())),
                        )
                    else:
                        request = RefreshSpec(resource=job["resource"], target_trade_date=job["target_trade_date"],
                                              codes=tuple(spec["codes"]), dates=tuple(spec["dates"]),
                                              missing_units=tuple((u["code"], u["trade_date"]) for u in units))
                    last = [0.0]
                    def progress(value):
                        if time.monotonic()-last[0] >= config.progress_interval_seconds or value.processed == value.total:
                            if not store.heartbeat(job_id, token, time.time(), asdict(value)):
                                raise RuntimeError("collection ownership lost")
                            last[0] = time.monotonic()
                    collect_refresh(conn, request, progress, guard)
                verified = repository.verify_spec(job["target_spec"], conn=conn)
                output.send({"verified": verified})
            finally:
                guard.release()
    except Exception as exc:
        # Do not transmit provider/connection exception strings (may contain credentials).
        output.send({"error": getattr(exc, "refresh_error_code", type(exc).__name__)})
    finally:
        stop.set()
        client.close()
        output.close()


class MarketWorkerRuntime:
    def __init__(self, settings, *, stop=None, process_context=None, child_target=None):
        self.settings, self.config = settings, settings.market_refresh
        self.redis = Redis.from_url(settings.core.resolved_redis_url(), socket_connect_timeout=3, socket_timeout=3)
        self.store = RedisRefreshStore(self.redis, self.config)
        self.stop = stop or threading.Event()
        self.context = process_context or mp.get_context("spawn")
        self.worker_id, self.boot_id = uuid4().hex, uuid4().hex
        self.current_job_id = None
        self.child = None
        self.child_target = child_target or _collect_child

    def announce(self):
        self.store.worker(self.worker_id, time.time(), busy=self.current_job_id is not None,
                          job_id=self.current_job_id, boot_id=self.boot_id)

    def _reap(self, child):
        if child.is_alive():
            child.terminate()
            child.join(self.config.terminate_grace_seconds)
        if child.is_alive():
            child.kill()
            child.join(self.config.kill_grace_seconds)
        if child.is_alive():
            raise RuntimeError("market collection child did not exit")

    def execute(self, job_id):
        if self.stop.is_set():
            return
        job = self.store.claim(job_id, time.time())
        if not job:
            return
        self.current_job_id = job_id
        receive, send = self.context.Pipe(duplex=False)
        child = self.context.Process(target=self.child_target, args=(self.settings, job, send), name="market-collection")
        self.child = child
        outcome = {}
        pipe_open = True
        started, heartbeat = time.monotonic(), 0.0
        try:
            self.announce()
            child.start()
            send.close()
            while child.is_alive():
                if pipe_open:
                    try:
                        if receive.poll(0.1):
                            outcome = receive.recv()
                    except (EOFError, OSError):
                        # Windows poll may raise BrokenPipeError after the final result.
                        pipe_open = False
                if self.stop.is_set() or time.monotonic()-started >= self.config.child_timeout_seconds:
                    outcome = {"error": "WORKER_STOPPED" if self.stop.is_set() else "COLLECTION_TIMEOUT"}
                    break
                if time.monotonic()-heartbeat >= self.config.heartbeat_interval_seconds:
                    if not self.store.heartbeat(job_id, job["run_token"], time.time()):
                        outcome = {"error": "OWNERSHIP_LOST"}
                        break
                    heartbeat = time.monotonic()
                child.join(0.1)
            if pipe_open:
                try:
                    if receive.poll():
                        outcome = receive.recv()
                except (EOFError, OSError):
                    pass
        except Exception as exc:
            outcome = {"error": "WORKER_" + type(exc).__name__}
            logger.warning("market worker interrupted job_id=%s error_type=%s", job_id, type(exc).__name__)
        finally:
            if child.pid is not None:
                self._reap(child)
            receive.close()
            send.close()
            self.child = None
            try:
                if outcome.get("busy"):
                    self.store.release_busy(job_id, job["run_token"], time.time())
                else:
                    verified = outcome.get("verified")
                    if verified is None:
                        # The child may have committed some or all units before dying.
                        # Reap it first so its PG lock is gone, then read durable facts.
                        from backend.modules.market_data.infrastructure.refresh_repository import (
                            RefreshRepository,
                        )
                        from db.instrument.db import get_connection
                        factory = partial(get_connection, self.settings.core.resolved_market_dsn())
                        verified = RefreshRepository(factory).verify_spec(job["target_spec"])
                    complete = verified.get("freshness") == "FRESH"
                    result = {k: verified[k] for k in ("total", "completed", "freshness") if k in verified}
                    result["missing_sample"] = verified.get("missing_units", [])[:20]
                    self.store.finish(job_id, job["run_token"], time.time(), complete=complete,
                                      partial=bool(verified.get("completed")), error_code=None if complete else outcome.get("error", "UPSTREAM_NOT_READY"), result=result)
            except Exception:
                logger.warning("market result awaits PG verification job_id=%s", job_id)
            self.current_job_id = None

    def close(self):
        self.stop.set()
        if self.child is not None and self.child.pid is not None:
            self._reap(self.child)
        self.redis.delete(self.store.key(f"worker:{self.worker_id}"))
        self.redis.close()


def run_market_worker(settings) -> int:
    from backend.bootstrap.observability import init_logging
    from backend.workers.broker import configure_broker
    init_logging(settings.core.log_level)
    settings.validate_for_process("market_worker")
    runtime = MarketWorkerRuntime(settings)
    broker = configure_broker(settings.core.resolved_redis_url(), namespace=settings.market_refresh.broker_namespace)
    dramatiq.actor(runtime.execute, actor_name="market_refresh", queue_name="market-data", broker=broker,
                   max_retries=0, time_limit=settings.market_refresh.actor_timeout_seconds*1000)
    worker = dramatiq.Worker(broker, queues=["market-data"], worker_threads=1, worker_timeout=1000)
    def stop(signum, frame):
        runtime.stop.set()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    worker.start()
    try:
        while not runtime.stop.is_set():
            try:
                runtime.announce()
            except Exception:
                logger.warning("market worker heartbeat unavailable")
            runtime.stop.wait(settings.market_refresh.heartbeat_interval_seconds)
    finally:
        try:
            worker.stop(timeout=(settings.market_refresh.terminate_grace_seconds+settings.market_refresh.kill_grace_seconds+5)*1000)
        finally:
            runtime.close()
    return 0


def main():
    from backend.bootstrap.settings import Settings
    return run_market_worker(Settings())


if __name__ == "__main__":
    raise SystemExit(main())
