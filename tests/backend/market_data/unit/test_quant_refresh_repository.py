# test-catalog-begin
# {
#   "purpose": "行情服务 / quant_refresh_repository（行情刷新、持久层）",
#   "keywords": [
#     "行情服务",
#     "行情数据",
#     "每日",
#     "复权因子",
#     "行情刷新",
#     "状态",
#     "停牌",
#     "quant_refresh_repository",
#     "daily",
#     "factor",
#     "refresh",
#     "status",
#     "suspension"
#   ],
#   "covers": [
#     "backend/modules/market_data/application/refresh_policy.py",
#     "backend/modules/market_data/infrastructure/refresh_repository.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from backend.modules.market_data.application.refresh_policy import Resource
from backend.modules.market_data.infrastructure.refresh_repository import (
    RefreshRepository,
)

TRADE_DAY = date(2026, 9, 23)
CODES = [f"{number:06d}.SZ" for number in range(1, 21)]


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _QuantConn:
    def __init__(self, *, daily=None, qfq=None, adj=None, status=None,
                 suspended=(), intraday=()):
        self.daily = set(CODES if daily is None else daily)
        self.qfq = set(CODES if qfq is None else qfq)
        self.adj = set(CODES if adj is None else adj)
        self.status = set(CODES if status is None else status)
        self.suspended = set(suspended)
        self.intraday = set(intraday)

    def execute(self, sql, params=()):
        if "FROM market.instrument WHERE" in sql:
            return _Rows([(code,) for code in CODES])
        if "FROM market.instrument_daily d" in sql:
            return _Rows([(code, code in self.daily) for code in CODES])
        if "SELECT ts_code, (is_suspended AND suspension_scope='full_day'), source" in sql:
            return _Rows([
                (code, code in self.suspended, "tushare")
                for code in self.status | self.suspended | self.intraday
            ])
        if "FROM market.factor_daily f" in sql:
            return _Rows([(code, True) for code in sorted(self.qfq)])
        if "FROM market.adj_factor a" in sql:
            return _Rows([(code, True) for code in sorted(self.adj)])
        if "FROM market.trade_status_effective" in sql:
            return _Rows([(code, True) for code in sorted(self.status)])
        raise AssertionError(f"unexpected SQL: {sql}")


def _target():
    return SimpleNamespace(
        resource=Resource.CN_STOCK_QUANT_INPUTS,
        expected_trade_date=TRADE_DAY,
        as_dict=lambda: {
            "resource": Resource.CN_STOCK_QUANT_INPUTS.value,
            "market": "CN", "market_date": TRADE_DAY.isoformat(),
            "expected_trade_date": TRADE_DAY.isoformat(),
        },
    )


def test_quant_components_use_separate_95_percent_denominators():
    # Each 95% component has a different missing stock: joint row intersection
    # is below 95%, while each required component individually meets its gate.
    conn = _QuantConn(
        qfq=CODES[1:],
        adj=CODES[:1] + CODES[2:],
        status=CODES[:2] + CODES[3:],
    )

    snapshot = RefreshRepository().snapshot(
        Resource.CN_STOCK_QUANT_INPUTS, _target(), conn=conn,
    )

    assert snapshot["freshness"] == "FRESH"
    metrics = snapshot["component_coverage"]
    assert {key: value["available_count"] for key, value in metrics.items()} == {
        "daily": 20, "qfq": 19, "adj_factor": 19, "trade_status": 19,
    }
    assert all(value["ready"] for value in metrics.values())
    assert snapshot["target_spec"]["units"] == []


def test_quant_daily_component_accepts_only_trusted_suspension_exemption():
    conn = _QuantConn(daily=CODES[1:], suspended=[CODES[0]])

    snapshot = RefreshRepository().snapshot(
        Resource.CN_STOCK_QUANT_INPUTS, _target(), conn=conn,
    )

    metric = snapshot["component_coverage"]["daily"]
    assert metric["available_count"] == 19
    assert metric["exempt_count"] == 1
    assert metric["missing_count"] == 0
    assert metric["ready"]
    assert not [unit for unit in snapshot["target_spec"]["units"]
                if unit["operation"] == "daily"]


def test_intraday_halt_cannot_exempt_missing_daily_bar():
    conn = _QuantConn(daily=CODES[1:], intraday=[CODES[0]])
    snapshot = RefreshRepository().snapshot(
        Resource.CN_STOCK_QUANT_INPUTS, _target(), conn=conn,
    )
    metric = snapshot["component_coverage"]["daily"]
    assert metric["available_count"] == 19
    assert metric["exempt_count"] == 0
    assert metric["missing_count"] == 1
    assert [unit for unit in snapshot["target_spec"]["units"]
            if unit["operation"] == "daily"]


def test_quant_target_spec_keeps_component_gaps_and_one_daily_qfq_status_operation():
    conn = _QuantConn(qfq=CODES[:-2], status=CODES[1:-1])

    snapshot = RefreshRepository().snapshot(
        Resource.CN_STOCK_QUANT_INPUTS, _target(), conn=conn,
    )

    spec = snapshot["target_spec"]
    assert snapshot["freshness"] == "PARTIAL"
    assert len([u for u in spec["component_missing_units"] if u["component"] == "qfq"]) == 2
    assert len([u for u in spec["component_missing_units"] if u["component"] == "trade_status"]) == 2
    assert spec["units"] == [{"operation": "qfq_status", "trade_date": TRADE_DAY.isoformat()}]
    assert spec["universe_digest"] == RefreshRepository._sha_codes(CODES)


def test_quant_adj_factor_group_action_completes_at_95_percent_gate():
    conn = _QuantConn(adj=CODES[:-2])

    snapshot = RefreshRepository().snapshot(
        Resource.CN_STOCK_QUANT_INPUTS, _target(), conn=conn,
    )

    assert snapshot["target_spec"]["units"] == [
        {"operation": "adj_factor", "trade_date": TRADE_DAY.isoformat()},
    ]
    conn.adj = set(CODES[:-1])
    verified = RefreshRepository().verify_spec(snapshot["target_spec"], conn=conn)
    assert verified["freshness"] == "FRESH"
    assert verified["completed"] == verified["total"] == 1
    assert verified["missing_units"] == []


def test_quant_daily_gap_schedules_status_discovery_operation():
    conn = _QuantConn(daily=CODES[:-1])

    snapshot = RefreshRepository().snapshot(
        Resource.CN_STOCK_QUANT_INPUTS, _target(), conn=conn,
    )

    operations = snapshot["target_spec"]["units"]
    assert operations == [
        {"operation": "daily", "code": CODES[-1], "trade_date": TRADE_DAY.isoformat()},
        {"operation": "qfq_status", "trade_date": TRADE_DAY.isoformat(), "force_refresh": True},
    ]


def test_quant_qfq_status_action_is_complete_when_both_95_percent_gates_pass():
    conn = _QuantConn(qfq=CODES[:-1], adj=CODES[:-1], status=CODES[1:])
    spec = {
        "resource": Resource.CN_STOCK_QUANT_INPUTS.value,
        "target_trade_date": TRADE_DAY.isoformat(),
        "codes": CODES,
        "universe_digest": RefreshRepository._sha_codes(CODES),
        "component_thresholds": {
            "daily": 1.0, "qfq": 0.95, "adj_factor": 0.95, "trade_status": 0.95,
        },
        "units": [{"operation": "qfq_status", "trade_date": TRADE_DAY.isoformat()}],
    }

    verified = RefreshRepository().verify_spec(spec, conn=conn)

    assert verified["freshness"] == "FRESH"
    assert verified["component_coverage"]["qfq"]["available_count"] == 19
    assert verified["component_coverage"]["trade_status"]["available_count"] == 19
    assert verified["completed"] == verified["total"] == 1
    assert verified["missing_units"] == []
    assert verified["current_universe_digest"] == spec["universe_digest"]
