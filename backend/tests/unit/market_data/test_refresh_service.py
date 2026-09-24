from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from backend.modules.market_data.application.refresh_policy import Resource
from backend.modules.market_data.application.refresh_service import RefreshService


class _Store:
    def __init__(self):
        self.ensure_calls = []

    def ping(self):
        return True

    def ensure(self, *args):
        self.ensure_calls.append(args)
        return {"resource": args[0], "decision": "QUEUED", "job_id": "job-1"}


def test_quant_refresh_uses_immutable_aware_anchor_and_private_resource():
    store = _Store()
    anchor = datetime(2026, 9, 23, 13, tzinfo=timezone.utc)
    policy_calls = []

    class Policy:
        def target(self, resource, now):
            policy_calls.append((resource, now))
            return SimpleNamespace(
                expected_trade_date=date(2026, 9, 23), market_date=date(2026, 9, 23),
            )

    class Repository:
        def snapshot(self, resource, target):
            assert resource == Resource.CN_STOCK_QUANT_INPUTS.value
            return {
                "resource": resource, "expected_trade_date": "2026-09-23",
                "freshness": "STALE", "block_reason": None,
                "target_spec": {"resource": resource, "codes": ["000001.SZ"],
                                "units": [{"operation": "qfq_status", "trade_date": "2026-09-23"}],
                                "universe_digest": "abc"},
                "coverage_digest": "coverage-1", "qfq_coverage": {"expected": 1},
            }

    service = RefreshService(
        store, Repository(), Policy(),
        clock=SimpleNamespace(now=lambda: datetime(2026, 9, 24, tzinfo=timezone.utc)),
    )

    result = service.ensure_quant_inputs(at=anchor, mode="auto")

    assert policy_calls == [(Resource.CN_STOCK_QUANT_INPUTS.value, anchor)]
    assert result["state"] == "pending"
    assert result["effective_trade_date"] == "2026-09-23"
    assert result["universe_digest"] == "abc"
    assert result["decision"] == "QUEUED"
    assert store.ensure_calls[0][0:2] == ("CN_STOCK_QUANT_INPUTS", "2026-09-23")
    assert store.ensure_calls[0][3] == "auto"


def test_quant_refresh_rejects_naive_anchor():
    service = RefreshService(_Store(), object(), object())
    with pytest.raises(ValueError, match="timezone-aware"):
        service.ensure_quant_inputs(at=datetime(2026, 9, 23, 21), mode="auto")


def test_public_ensure_rejects_internal_resource():
    service = RefreshService(_Store(), object(), object())
    with pytest.raises(ValueError, match="not public"):
        service.ensure([Resource.CN_STOCK_QUANT_INPUTS.value])


def test_scheduler_recovers_internal_quant_job_but_never_creates_one(monkeypatch):
    from backend.modules.market_data.application import refresh_service as module

    now = datetime(2026, 9, 24, 0, tzinfo=timezone.utc)
    job = {
        "id": "quant-job", "resource": Resource.CN_STOCK_QUANT_INPUTS.value,
        "target_trade_date": "2026-09-23", "target_spec": {"units": []},
        "status": "RUNNING", "heartbeat_at": "2026-09-23T23:59:00+00:00",
        "run_token": "lease-token",
    }

    class Store:
        config = SimpleNamespace(enabled=True, lease_ttl_seconds=10)
        finished = []
        dispatched = []
        ensured = []

        def ping(self):
            return True

        def context(self, resource, target, _now):
            assert resource == Resource.CN_STOCK_QUANT_INPUTS.value
            assert target is None  # recovery uses the job's frozen target, not today's target
            return {"job": job}

        def finish(self, *args, **kwargs):
            self.finished.append((args, kwargs))

        def dispatch(self, job_id):
            self.dispatched.append(job_id)

        def ensure(self, *args):
            self.ensured.append(args)

    store = Store()

    class Repository:
        def verify_spec(self, spec):
            assert spec is job["target_spec"]
            return {"freshness": "FRESH", "total": 0, "completed": 0, "missing_units": []}

    monkeypatch.setattr(module, "PUBLIC_RESOURCES", ())
    monkeypatch.setattr(module, "INTERNAL_RESOURCES", (Resource.CN_STOCK_QUANT_INPUTS,))
    service = RefreshService(
        store, Repository(), object(),
        clock=SimpleNamespace(now=lambda: now), lock_available=lambda: True,
    )

    service.tick()

    assert store.finished[0][1]["complete"] is True
    assert store.finished[0][1]["recovered"] is True
    assert store.ensured == []
