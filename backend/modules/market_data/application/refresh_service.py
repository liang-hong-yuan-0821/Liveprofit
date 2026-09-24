"""Shared admission/read service for the API, dispatcher and market worker."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from redis.exceptions import RedisError

from backend.modules.market_data.application.refresh_policy import (
    INTERNAL_RESOURCES,
    PUBLIC_RESOURCES,
    RefreshPolicy,
    Resource,
)
from backend.modules.market_data.infrastructure.redis_refresh_store import (
    ACTIVE,
    RedisRefreshStore,
)
from backend.shared.clock import SystemClock

logger = logging.getLogger(__name__)
_PRIVATE = {"target_spec", "missing_units", "coverage_digest", "block_reason", "history_gap"}


class RefreshUnavailable(RuntimeError):
    pass


class RefreshService:
    def __init__(self, store, repository, policy, *, clock=None, publisher=None, lock_available=None):
        self.store, self.repository, self.policy = store, repository, policy
        self.clock = clock or SystemClock()
        self.publisher = publisher
        self.lock_available = lock_available or (lambda: True)
        self._scheduler_snapshots = {}

    def snapshot(self, resource, *, cache=False, at=None):
        now = self.clock.now()
        target = self.policy.target(resource, at or now)
        if cache:
            try:
                raw = self.store.redis.get(self.store.key(f"coverage:{resource}"))
                cached = json.loads(raw) if raw else None
                if cached and cached.get("expected_trade_date") == str(target.expected_trade_date) and cached.get("market_date") == str(target.market_date):
                    return cached
            except RedisError:
                pass
        return self.repository.snapshot(resource, target)

    def status(self):
        now = self.clock.now()
        available = True
        try:
            self.store.ping()
            workers = self.store.workers()
        except RedisError:
            available, workers = False, []
        groups = []
        for resource in PUBLIC_RESOURCES:
            resource = resource.value
            snapshot = self.snapshot(resource, cache=available)
            group = {k: v for k, v in snapshot.items() if k not in _PRIVATE}
            group["resource"] = resource
            job, change = None, ""
            blocked = snapshot.get("block_reason") or ("UP_TO_DATE" if snapshot["freshness"] == "FRESH" else None)
            try:
                if not available:
                    raise RefreshUnavailable()
                ctx = self.store.context(resource, snapshot.get("expected_trade_date"), now.timestamp())
                job = ctx["job"]
                change = self.store.redis.get(self.store.key(f"changed:{resource}")) or ""
                for mode, key in (("auto", "auto_eligibility"), ("retry", "manual_eligibility")):
                    group[key] = dict(allowed=False, reason=blocked, next_retry_at=None) if blocked else self.store.eligibility(ctx, mode, now.timestamp())
            except (RedisError, RefreshUnavailable):
                available = False
                for key in ("auto_eligibility", "manual_eligibility"):
                    group[key] = dict(allowed=False, reason="REFRESH_UNAVAILABLE", next_retry_at=None)
            if isinstance(change, bytes):
                change = change.decode()
            group["data_version"] = hashlib.sha256(f"{snapshot.get('coverage_digest', '')}:{change}".encode()).hexdigest()[:24]
            group["job"] = self.public_job(job) if job else None
            groups.append(group)
        if not available:
            for group in groups:
                for key in ("auto_eligibility", "manual_eligibility"):
                    group[key] = dict(allowed=False, reason="REFRESH_UNAVAILABLE", next_retry_at=None)
        return dict(server_time=now.isoformat(), refresh_available=available, groups=groups,
                    concept_display_date=self.repository.concept_display_date(), worker_online=bool(workers))

    @staticmethod
    def public_job(job):
        fields = ("id", "resource", "target_trade_date", "status", "attempt", "processed", "total", "started_at", "heartbeat_at", "created_at", "finished_at", "error_code", "error_summary", "result")
        return {key: job.get(key) for key in fields}

    def ensure(self, resources, mode="auto", *, trigger="PAGE"):
        try:
            self.store.ping()
            decisions = []
            for resource in dict.fromkeys(resources):
                resource = Resource(resource)
                if resource not in PUBLIC_RESOURCES:
                    raise ValueError("internal market refresh resource is not public")
                resource = resource.value
                snapshot = self.snapshot(resource)
                reason = snapshot.get("block_reason")
                if snapshot["freshness"] == "FRESH" or reason:
                    decisions.append(dict(resource=resource, decision="BLOCKED" if reason else "UP_TO_DATE", job_id=None, reason=reason or "UP_TO_DATE", next_retry_at=None))
                    continue
                result = self.store.ensure(resource, str(snapshot["expected_trade_date"]), snapshot["target_spec"], mode, self.clock.now().timestamp(), trigger)
                decisions.append(result)
                if result.get("job_id"):
                    self.dispatch(result["job_id"])
            return decisions
        except RedisError as exc:
            raise RefreshUnavailable("更新服务暂不可用") from exc

    def ensure_quant_inputs(self, *, at, mode="auto", trigger="QUANT"):
        """Privately admit quant's frozen target into the shared refresh lifecycle."""
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("quant refresh anchor must be timezone-aware")
        if mode not in {"auto", "retry"}:
            raise ValueError("invalid quant refresh mode")
        resource = Resource.CN_STOCK_QUANT_INPUTS.value
        snapshot = self.snapshot(resource, at=at)
        target_date = snapshot.get("expected_trade_date")
        spec = snapshot.get("target_spec") or {}
        result = {
            "state": "ready" if snapshot.get("freshness") == "FRESH" else "pending",
            "effective_trade_date": target_date,
            "decision": "UP_TO_DATE" if snapshot.get("freshness") == "FRESH" else "BLOCKED",
            "job_id": None,
            "coverage": {key: value for key, value in snapshot.items()
                         if key not in _PRIVATE and key != "target_spec"},
            "universe_digest": spec.get("universe_digest"),
        }
        if result["state"] == "ready" or snapshot.get("block_reason") or not target_date:
            return result
        try:
            self.store.ping()
            decision = self.store.ensure(
                resource, str(target_date), spec, mode, self.clock.now().timestamp(), trigger,
            )
            result.update(decision)
            result["state"] = "pending"
            result["effective_trade_date"] = target_date
            result["universe_digest"] = spec.get("universe_digest")
            if decision.get("job_id"):
                self.dispatch(decision["job_id"])
            return result
        except RedisError as exc:
            raise RefreshUnavailable("量化行情补齐服务暂不可用") from exc

    def dispatch(self, job_id):
        if not self.publisher:
            return False
        workers = self.store.workers()
        online, idle = bool(workers), any(w["state"] == "idle" for w in workers)
        job = self.store.job(job_id)
        if not job or (job.get("dispatch_count", 0) and not self.lock_available()):
            return False
        if not self.store.dispatch(job_id, self.clock.now().timestamp(), online=online, idle=idle):
            return False
        try:
            self.publisher(job_id)
        except Exception:
            # The reservation is intentionally retained: delivery may have succeeded.
            logger.warning("market refresh delivery unconfirmed: job_id=%s", job_id)
        return True

    def tick(self):
        now = self.clock.now().timestamp()
        if not self.store.config.enabled:
            return
        self.store.ping()
        for resource in PUBLIC_RESOURCES:
            name = resource.value
            # Only the scheduler writes coverage caches; status GET never mutates state.
            last, snapshot = self._scheduler_snapshots.get(name, (0, None))
            target = self.policy.target(name, self.clock.now())
            expected = str(target.expected_trade_date) if target.expected_trade_date else None
            if (snapshot is None or now-last >= self.store.config.coverage_check_interval_seconds
                    or snapshot.get("expected_trade_date") != expected):
                snapshot = self.snapshot(name)
                self._scheduler_snapshots[name] = (now, snapshot)
                self.store.redis.set(self.store.key(f"coverage:{name}"), json.dumps(snapshot, default=str), ex=self.store.config.coverage_ttl_seconds)
            ctx = self.store.context(name, snapshot.get("expected_trade_date"), now)
            job = ctx["job"]
            if job and job["status"] in ACTIVE:
                if job["status"] == "RUNNING":
                    heartbeat = datetime.fromisoformat(job["heartbeat_at"]).timestamp()
                    if now-heartbeat >= self.store.config.lease_ttl_seconds and self.lock_available():
                        verified = self.repository.verify_spec(job["target_spec"])
                        complete = verified.get("freshness") == "FRESH"
                        result = {key: verified[key] for key in ("total", "completed", "freshness") if key in verified}
                        result["missing_sample"] = verified.get("missing_units", [])[:20]
                        self.store.finish(
                            job["id"], job.get("run_token"), now, complete=complete,
                            partial=bool(verified.get("completed")),
                            error_code=None if complete else "WORKER_LOST",
                            result=result, recovered=True,
                        )
                elif job["status"] == "RETRY_WAIT":
                    self.store.retry_due(job["id"], now)
                self.dispatch(job["id"])
            elif self.store.config.auto_enabled and snapshot["freshness"] != "FRESH" and not snapshot.get("block_reason"):
                result = self.store.ensure(name, str(snapshot["expected_trade_date"]), snapshot["target_spec"], "auto", now, "SCHEDULE")
                if result.get("job_id"):
                    self.dispatch(result["job_id"])

        # Quant inputs are admitted by the quant task, which supplies its own
        # aware-time target. The scheduler only recovers already-admitted jobs.
        for resource in INTERNAL_RESOURCES:
            name = resource.value
            job = self.store.context(name, None, now)["job"]
            if not job or job["status"] not in ACTIVE:
                continue
            if job["status"] == "RUNNING":
                heartbeat = datetime.fromisoformat(job["heartbeat_at"]).timestamp()
                if now-heartbeat >= self.store.config.lease_ttl_seconds and self.lock_available():
                    verified = self.repository.verify_spec(job["target_spec"])
                    complete = verified.get("freshness") == "FRESH"
                    result = {key: verified[key] for key in ("total", "completed", "freshness") if key in verified}
                    result["missing_sample"] = verified.get("missing_units", [])[:20]
                    self.store.finish(
                        job["id"], job.get("run_token"), now, complete=complete,
                        partial=bool(verified.get("completed")),
                        error_code=None if complete else "WORKER_LOST",
                        result=result, recovered=True,
                    )
            elif job["status"] == "RETRY_WAIT":
                self.store.retry_due(job["id"], now)
            self.dispatch(job["id"])


def build_refresh_service(settings, redis, connection_factory, *, publisher=None):
    from backend.modules.market_data.infrastructure.calendar_adapter import (
        MarketCalendarAdapter,
    )
    from backend.modules.market_data.infrastructure.refresh_repository import (
        RefreshRepository,
    )
    from db.instrument.ingest.guard import is_ingest_lock_available
    def lock_available():
        with connection_factory() as conn:
            return is_ingest_lock_available(conn)
    return RefreshService(RedisRefreshStore(redis, settings.market_refresh), RefreshRepository(connection_factory),
                          RefreshPolicy(MarketCalendarAdapter(), publish_lag_seconds=settings.market_refresh.publish_lag_seconds,
                                        window_sessions=settings.market_refresh.window_sessions),
                          publisher=publisher, lock_available=lock_available)


def best_effort_market_changed(resource):
    """Old maintenance entrypoints may notify Redis but never depend on it."""
    from redis import Redis

    from backend.bootstrap.settings import Settings
    settings = Settings()
    url = settings.core.resolved_redis_url()
    if not url:
        return
    try:
        with Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1) as client:
            RedisRefreshStore(client, settings.market_refresh).changed(resource)
    except (RedisError, OSError):
        logger.debug("market changed notification unavailable: resource=%s", resource)
