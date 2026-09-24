"""Real Redis atomicity/failure tests. All keys use the isolated run namespace."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError

from backend.bootstrap.settings import MarketRefreshSettings
from backend.modules.market_data.infrastructure.redis_refresh_store import RedisRefreshStore
from backend.modules.market_data.application.refresh_service import RefreshService, RefreshUnavailable

RESOURCE = "CN_INDEX_BARS"
TARGET = "2026-09-22"
NOW = datetime(2026, 9, 23, tzinfo=timezone.utc).timestamp()
SPEC = dict(resource=RESOURCE, target_trade_date=TARGET, codes=["000001.SH"], dates=[TARGET],
            units=[dict(code="000001.SH", trade_date=TARGET)])


@pytest.fixture
def store(refresh_env):
    return RedisRefreshStore(refresh_env.redis, MarketRefreshSettings(key_prefix=refresh_env.key_prefix))


def admit(store, now=NOW, target=TARGET, mode="auto"):
    return store.ensure(RESOURCE, target, {**SPEC, "target_trade_date": target}, mode, now)["job_id"]


def test_concurrent_pages_share_one_job_and_single_claim(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: admit(store), range(32)))
    assert len(set(ids)) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim(ids[0], NOW), range(16)))
    assert len([c for c in claims if c]) == 1
    ctx = store.context(RESOURCE, TARGET, NOW)
    assert len(ctx["budget"]) == ctx["attempts"]["auto_count"] == 1


def test_private_quant_qfq_status_group_counts_as_one_redis_operation(store):
    resource = "CN_STOCK_QUANT_INPUTS"
    spec = {
        "resource": resource, "target_trade_date": TARGET,
        "codes": ["000001.SZ"], "dates": [TARGET],
        "component_missing_units": [
            {"component": "qfq", "code": "000001.SZ", "trade_date": TARGET},
            {"component": "trade_status", "code": "000001.SZ", "trade_date": TARGET},
        ],
        "units": [{"operation": "qfq_status", "trade_date": TARGET}],
    }
    decision = store.ensure(resource, TARGET, spec, "auto", NOW)
    job = store.job(decision["job_id"])

    assert job["total"] == len(job["target_spec"]["units"]) == 1
    claim = store.claim(job["id"], NOW)
    assert store.heartbeat(job["id"], claim["run_token"], NOW + 1,
                           {"processed": 1, "total": 1, "operation": "qfq_status"})
    assert store.job(job["id"])["processed"] == 1
    assert store.finish(job["id"], claim["run_token"], NOW + 2, complete=True)
    assert store.job(job["id"])["status"] == "SUCCEEDED"


def test_bounded_delivery_offline_busy_and_loss(store):
    job_id = admit(store)
    for minute in range(60):
        assert not store.dispatch(job_id, NOW+minute*60, online=False, idle=False)
    assert store.job(job_id)["dispatch_count"] == 0
    assert store.dispatch(job_id, NOW+3600, online=True, idle=False)
    for minute in range(90):
        assert not store.dispatch(job_id, NOW+3600+minute*60, online=True, idle=False)
    assert store.job(job_id)["dispatch_count"] == 1
    assert store.dispatch(job_id, NOW+10000, online=True, idle=True)
    assert not store.dispatch(job_id, NOW+10001, online=True, idle=True)
    assert store.dispatch(job_id, NOW+10900, online=True, idle=True)
    assert not store.dispatch(job_id, NOW+11800, online=True, idle=True)
    assert store.job(job_id)["error_code"] == "DELIVERY_UNCONFIRMED"
    assert admit(store, NOW+20000) is None
    assert admit(store, NOW+12099, mode="retry") is None
    assert admit(store, NOW+12100, mode="retry") is not None


def test_multiple_dispatchers_reserve_once(store):
    job_id = admit(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.dispatch(job_id, NOW, online=True, idle=True), range(16)))
    assert sum(bool(r) for r in results) == 1


def test_target_attempts_survive_long_holiday_and_job_expiry(store):
    job_id = admit(store)
    now = NOW
    for attempt in range(3):
        if attempt:
            assert store.retry_due(job_id, now)
        job = store.claim(job_id, now)
        assert job
        assert store.finish(job_id, job["run_token"], now, error_code="UPSTREAM_NOT_READY")
        now += (900, 3600, 0)[attempt]
    assert store.job(job_id)["status"] == "FAILED"
    store.redis.delete(store.key(f"job:{job_id}"))
    assert store.redis.ttl(store.key(f"attempts:{RESOURCE}:{TARGET}")) == -1
    assert admit(store, NOW+10*86400) is None
    ctx = store.context(RESOURCE, TARGET, NOW+10*86400)
    assert not store.eligibility(ctx, "auto", NOW+10*86400)["allowed"]
    assert store.eligibility(ctx, "retry", NOW+10*86400)["allowed"]
    assert admit(store, NOW+11*86400, target="2026-10-09")
    assert 0 < store.redis.ttl(store.key(f"attempts:{RESOURCE}:{TARGET}")) <= 7*86400


def test_local_source_access_denial_blocks_auto_retry_until_manual_action(store):
    job_id = admit(store)
    job = store.claim(job_id, NOW)
    assert job
    assert store.finish(job_id, job["run_token"], NOW,
                        error_code="SOURCE_NETWORK_ACCESS_DENIED")

    failed = store.job(job_id)
    assert failed["status"] == "FAILED"
    assert failed["error_summary"] and "本机策略拒绝" in failed["error_summary"]
    auto = store.eligibility(store.context(RESOURCE, TARGET, NOW+3600), "auto", NOW+3600)
    assert not auto["allowed"] and auto["reason"] == "SOURCE_NETWORK_ACCESS_DENIED"
    assert store.eligibility(store.context(RESOURCE, TARGET, NOW+300), "retry", NOW+300)["allowed"]

    manual = store.ensure(RESOURCE, TARGET, SPEC, "retry", NOW+300)
    assert manual["decision"] == "QUEUED"
    assert "auto_blocked_error" not in store.context(RESOURCE, TARGET, NOW+300)["attempts"]




def test_busy_refunds_attempt_without_resetting_dispatch_and_fences_old_token(store):
    job_id = admit(store)
    assert store.dispatch(job_id, NOW, online=True, idle=True)
    job = store.claim(job_id, NOW)
    assert store.release_busy(job_id, job["run_token"], NOW)
    ctx = store.context(RESOURCE, TARGET, NOW)
    assert ctx["attempts"] == {} and ctx["budget"] == []
    assert ctx["job"]["dispatch_count"] == 1
    newer = store.claim(job_id, NOW+1)
    assert newer["run_token"] != job["run_token"]
    assert not store.heartbeat(job_id, job["run_token"], NOW+2)
    assert not store.finish(job_id, job["run_token"], NOW+2, complete=True)
    assert store.owns(job_id, newer["run_token"])
    assert store.finish(job_id, newer["run_token"], NOW+2, complete=True)
    assert not store.claim(job_id, NOW+3)


def test_manual_and_automatic_eligibility_are_independent(store):
    store.config.auto_enabled = False
    assert admit(store) is None
    job_id = admit(store, mode="retry")
    job = store.claim(job_id, NOW)
    assert job
    store.finish(job_id, job["run_token"], NOW, complete=False)
    assert not store.eligibility(store.context(RESOURCE, TARGET, NOW+299), "retry", NOW+299)["allowed"]
    assert store.eligibility(store.context(RESOURCE, TARGET, NOW+300), "retry", NOW+300)["allowed"]


def test_disabling_auto_does_not_trap_manual_behind_offline_queued_job(store):
    queued = admit(store)
    store.config.auto_enabled = False
    ctx = store.context(RESOURCE, TARGET, NOW)
    assert store.eligibility(ctx, "retry", NOW)["allowed"]
    manual = admit(store, mode="retry")
    assert manual != queued
    assert store.job(queued)["status"] == "CANCELLED"
    assert store.claim(queued, NOW) is None
    assert store.claim(manual, NOW)


def test_dispatcher_recovers_verified_commit_and_old_recovery_cannot_finish_new_owner(store):
    now = [NOW]
    class Repo:
        def snapshot(self, resource, target):
            return dict(resource=resource, freshness="FRESH", block_reason=None, expected_trade_date=TARGET)
        def verify_spec(self, spec):
            return dict(missing_units=[], total=1, completed=1, freshness="FRESH")
    clock = SimpleNamespace(now=lambda: datetime.fromtimestamp(now[0], timezone.utc))
    policy = SimpleNamespace(target=lambda r, n: SimpleNamespace(expected_trade_date=TARGET))
    locked = [True]
    service = RefreshService(store, Repo(), policy, clock=clock, lock_available=lambda: not locked[0])
    job_id = admit(store)
    job = store.claim(job_id, NOW)
    now[0] += 181
    service.tick()
    assert store.job(job_id)["status"] == "RUNNING"
    locked[0] = False
    service.tick()
    assert store.job(job_id)["status"] == "SUCCEEDED"
    assert store.job(job_id)["result"] == {
        "total": 1, "completed": 1, "freshness": "FRESH", "missing_sample": [],
    }
    # A delayed recovery carrying the former token cannot finalize a new claim.
    second = admit(store, now[0]+1, target="2026-09-23")
    claim = store.claim(second, now[0]+1)
    assert not store.finish(second, job["run_token"], now[0]+2, complete=True, recovered=True)
    assert store.owns(second, claim["run_token"])


def test_rolling_budget_applies_to_manual_attempts(store):
    for i in range(6):
        now = NOW+i*300
        job = store.claim(admit(store, now, mode="retry"), now)
        assert job
        store.finish(job["id"], job["run_token"], now)
    ctx = store.context(RESOURCE, TARGET, NOW+2000)
    assert store.eligibility(ctx, "retry", NOW+2000)["reason"] == "RETRY_LIMIT"
    assert admit(store, NOW+86401, mode="retry")


def test_factor_cooldown_and_changed_are_scoped(store):
    assert store.factor_gate("000001.SZ", "2026-09-01", TARGET)
    assert not store.factor_gate("000001.SZ", "2026-09-01", TARGET)
    assert store.factor_gate("000002.SZ", "2026-09-01", TARGET)
    store.redis.set(store.key(f"coverage:{RESOURCE}"), "cached")
    store.changed(RESOURCE)
    assert not store.redis.exists(store.key(f"coverage:{RESOURCE}"))
    assert store.redis.get(store.key(f"changed:{RESOURCE}"))


def test_new_target_does_not_mutate_running_spec(store):
    job_id = admit(store)
    job = store.claim(job_id, NOW)
    assert admit(store, NOW+86400, target="2026-09-23") == job_id
    assert store.job(job_id)["target_spec"] == job["target_spec"]


def test_status_never_enqueues_or_writes_state_and_redis_failure_degrades(store):
    class Repo:
        def concept_display_date(self):
            return "2026-09-21"

        def snapshot(self, resource, target):
            return dict(resource=resource, market=resource.split('_')[0], freshness="STALE", block_reason=None,
                        expected_trade_date=TARGET, market_date="2026-09-23", coverage_digest="abc", latest_observed_date="2026-09-21")
    policy = SimpleNamespace(target=lambda r, n: SimpleNamespace(expected_trade_date=TARGET, market_date="2026-09-23"))
    clock = SimpleNamespace(now=lambda: datetime.fromtimestamp(NOW, timezone.utc))
    service = RefreshService(store, Repo(), policy, clock=clock, publisher=lambda job: pytest.fail("GET published"))
    before = set(store.redis.scan_iter(store.key("*")))
    assert service.status()["refresh_available"]
    assert set(store.redis.scan_iter(store.key("*"))) == before
    store.ping = lambda: (_ for _ in ()).throw(ConnectionError("unavailable"))
    result = service.status()
    assert not result["refresh_available"]
    assert all(not g["auto_eligibility"]["allowed"] and not g["manual_eligibility"]["allowed"] for g in result["groups"])
    with pytest.raises(RefreshUnavailable):
        service.ensure([RESOURCE])


def test_dispatcher_tick_orders_resources_and_keeps_queued_work_bounded(store):
    now = [NOW]
    class Repo:
        calls = 0
        def snapshot(self, resource, target):
            self.calls += 1
            spec = {**SPEC, "resource": resource}
            return dict(resource=resource, market=resource.split("_")[0], market_date="2026-09-23",
                        expected_trade_date=TARGET, freshness="STALE", block_reason=None,
                        target_spec=spec, coverage_digest="missing")
    repo = Repo()
    clock = SimpleNamespace(now=lambda: datetime.fromtimestamp(now[0], timezone.utc))
    policy = SimpleNamespace(target=lambda resource, _: SimpleNamespace(expected_trade_date=TARGET, market_date="2026-09-23"))
    published = []
    store.worker("idle", NOW, busy=False)
    service = RefreshService(store, repo, policy, clock=clock,
                             publisher=lambda job_id: published.append(store.job(job_id)["resource"]))
    service.tick()
    assert published == ["CN_INDEX_BARS", "CN_INDEX_FACTORS", "US_INDEX_BARS",
                         "KR_INDEX_BARS", "CN_STOCK_DAILY", "CN_SECTOR_DAILY"]
    assert repo.calls == 6
    now[0] += 60
    service.tick()
    assert repo.calls == 6
    assert len(published) == 6
    now[0] += 241
    service.tick()
    assert repo.calls == 12
    assert len(published) <= 12  # Bounded resend after the five-minute delay.


def test_publish_exception_keeps_delivery_reservation_for_reconciliation(store):
    clock = SimpleNamespace(now=lambda: datetime.fromtimestamp(NOW, timezone.utc))
    policy = SimpleNamespace(target=lambda resource, _: SimpleNamespace(expected_trade_date=TARGET))
    service = RefreshService(store, SimpleNamespace(), policy, clock=clock,
                             publisher=lambda _: (_ for _ in ()).throw(ConnectionError("after-send uncertain")))
    store.worker("idle", NOW, busy=False)
    job_id = admit(store)
    assert service.dispatch(job_id)
    assert store.job(job_id)["dispatch_count"] == 1
    assert not service.dispatch(job_id)


def test_lost_redis_lease_fences_stale_worker_and_reconciles_pg(store):
    now = [NOW]
    class Repo:
        def snapshot(self, resource, target):
            return dict(resource=resource, freshness="FRESH", block_reason=None,
                        expected_trade_date=TARGET)
        def verify_spec(self, spec):
            return dict(missing_units=[], completed=1, freshness="FRESH")
    clock = SimpleNamespace(now=lambda: datetime.fromtimestamp(now[0], timezone.utc))
    policy = SimpleNamespace(target=lambda resource, _: SimpleNamespace(expected_trade_date=TARGET))
    service = RefreshService(store, Repo(), policy, clock=clock, lock_available=lambda: True)
    job_id = admit(store)
    claim = store.claim(job_id, NOW)
    store.redis.delete(store.key(f"lock:{RESOURCE}"))
    assert not store.owns(job_id, claim["run_token"])
    assert not store.heartbeat(job_id, claim["run_token"], NOW+1)
    now[0] += 181
    service.tick()
    assert store.job(job_id)["status"] == "SUCCEEDED"
