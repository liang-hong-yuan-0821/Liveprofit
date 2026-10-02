# test-catalog-begin
# {
#   "purpose": "量化策略 / instrument_rule_repository（品种规则、持久层）：Only conftest's liveprofit_quant_strategy_test database is migrated/written.",
#   "keywords": [
#     "量化策略",
#     "不可变历史",
#     "证券数据",
#     "品种规则",
#     "来源证据",
#     "instrument_rule_repository",
#     "immutable",
#     "instrument",
#     "rule",
#     "source"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/domain/instrument_rules.py",
#     "backend/modules/quant_strategy/infrastructure/instrument_rule_repository.py",
#     "backend/modules/quant_strategy/infrastructure/official_rule_artifact.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Only conftest's liveprofit_quant_strategy_test database is migrated/written."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.modules.quant_strategy.domain.instrument_rules import (
    InstrumentRuleUnavailable, InstrumentTradingRule,
)
from backend.modules.quant_strategy.infrastructure.instrument_rule_repository import (
    InstrumentRuleRepository, source_reference,
)
from backend.modules.quant_strategy.infrastructure.official_rule_artifact import OfficialRuleArtifact


DAY = date(2026, 9, 28)
NEXT = date(2026, 9, 29)


def _source(repository, *, uri="https://example.invalid/rule/a", raw=b"original rule A",
            published_on=date(2026, 4, 24)):
    return repository.register_source(source_uri=uri, media_type="text/plain",
                                      raw_content=raw, published_on=published_on)


def _rule(source, **changes):
    values = dict(symbol="688001.SH", asset_type="stock",
                  effective_from=date(2026, 4, 24), effective_through=None,
                  published_on=source.published_on, source_ref=source_reference(source),
                  price_tick=Decimal("0.01"), min_buy_quantity=200,
                  buy_quantity_step=1, max_buy_quantity=100000, roundtrip_days=1)
    values.update(changes)
    return InstrumentTradingRule(**values)


def test_original_and_rule_idempotence_drift_and_diagnostic_read(env):
    factory = env["session_factory"]
    with factory() as session:
        repository = InstrumentRuleRepository(session)
        source = _source(repository)
        rule = _rule(source)
        first = repository.observe_rule(source_id=source.id, rule=rule)
        session.commit()
        assert _source(repository).id == source.id
        assert repository.observe_rule(source_id=source.id, rule=rule).id == first.id
        assert repository.resolve_diagnostic(symbol=rule.symbol, decision_date=DAY,
                                             execution_date=NEXT) == rule
        with pytest.raises(ValueError, match="identity conflict"):
            repository.observe_rule(source_id=source.id,
                                    rule=replace(rule, buy_quantity_step=2))
        with pytest.raises(ValueError, match="identity conflict"):
            _source(repository, published_on=date(2026, 4, 25))
        session.rollback()
    with factory() as fresh:
        assert InstrumentRuleRepository(fresh).resolve_diagnostic(
            symbol="688001.SH", decision_date=DAY, execution_date=NEXT) == rule


def test_conflicting_sources_and_late_publication_remain_unknown(env):
    with env["session_factory"]() as session:
        repository = InstrumentRuleRepository(session)
        first = _source(repository, uri="https://example.invalid/rule/conflict-a", raw=b"A")
        second = _source(repository, uri="https://example.invalid/rule/conflict-b", raw=b"B")
        late = _source(repository, uri="https://example.invalid/rule/conflict-c", raw=b"C",
                       published_on=DAY)
        repository.observe_rule(source_id=first.id, rule=_rule(first, symbol="688002.SH"))
        repository.observe_rule(source_id=second.id,
                                rule=_rule(second, symbol="688002.SH", buy_quantity_step=100))
        repository.observe_rule(source_id=late.id, rule=_rule(late, symbol="688002.SH"))
        session.commit()
        with pytest.raises(InstrumentRuleUnavailable, match="no unique"):
            repository.resolve_diagnostic(symbol="688002.SH", decision_date=DAY,
                                           execution_date=NEXT)
        assert len(repository.read_diagnostic_rules(symbol="688002.SH", decision_date=DAY,
                                                    execution_date=NEXT)) == 2
        with pytest.raises(InstrumentRuleUnavailable, match="no unique"):
            repository.resolve_diagnostic(symbol="688003.SH", decision_date=DAY,
                                           execution_date=NEXT)


def test_rule_artifacts_are_immutable_even_to_truncate(env):
    with env["session_factory"]() as session:
        repository = InstrumentRuleRepository(session)
        source = _source(repository, uri="https://example.invalid/rule/immutable", raw=b"immutable")
        row = repository.observe_rule(source_id=source.id, rule=_rule(source, symbol="688009.SH"))
        session.commit()
        with pytest.raises(DBAPIError):
            session.execute(text("UPDATE quant_instrument_rule_observations SET buy_quantity_step=2 "
                                 "WHERE id=:id"), {"id": row.id})
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM quant_instrument_rule_sources WHERE id=:id"),
                            {"id": source.id})
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(text("TRUNCATE quant_instrument_rule_observations"))
        session.rollback()
        assert repository.resolve_diagnostic(symbol="688009.SH", decision_date=DAY,
                                             execution_date=NEXT).buy_quantity_step == 1


def test_rule_source_binding_and_precision_fail_before_write(env):
    with env["session_factory"]() as session:
        repository = InstrumentRuleRepository(session)
        source = _source(repository, uri="https://example.invalid/rule/precision", raw=b"precision")
        for rule in (_rule(source, source_ref="unrelated"),
                     _rule(source, price_tick=Decimal("0.000000001")),
                     _rule(source, max_buy_quantity=2147483648),
                     _rule(source, min_buy_quantity=2147483648,
                           max_buy_quantity=2147483648),
                     _rule(source, buy_quantity_step=2147483648)):
            with pytest.raises(ValueError):
                repository.observe_rule(source_id=source.id, rule=rule)
        assert session.execute(text("SELECT count(*) FROM quant_instrument_rule_observations "
                                    "WHERE source_id=:id"), {"id": source.id}).scalar_one() == 0
        normalized = _rule(source, symbol="688008.SH",
                           price_tick=Decimal("0.010000000"))
        repository.observe_rule(source_id=source.id, rule=normalized)
        assert repository.resolve_diagnostic(symbol=normalized.symbol, decision_date=DAY,
                                             execution_date=NEXT).price_tick == Decimal("0.01")


def test_official_host_capture_persists_exact_bytes_but_claimed_date_is_not_certified(env, monkeypatch):
    from backend.modules.quant_strategy.infrastructure import instrument_rule_repository as module

    url = "https://docs.static.szse.cn/rules/test-original.pdf"
    raw = b"%PDF-1.7\r\nexact source bytes"
    artifact = OfficialRuleArtifact(
        requested_uri=url, source_uri=url, media_type="application/pdf",
        raw_content=raw, content_sha256=sha256(raw).hexdigest(),
        fetched_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(module, "fetch_exchange_rule_artifact", lambda requested: artifact)
    with env["session_factory"]() as session:
        repository = InstrumentRuleRepository(session)
        source = repository.capture_exchange_source(
            source_url=url, claimed_published_on=date(2026, 4, 24),
        )
        session.commit()
        assert bytes(source.raw_content) == raw
        assert source.content_sha256 == sha256(raw).hexdigest()
        assert source.published_on == date(2026, 4, 24)
        captures = repository.read_capture_diagnostics(source_id=source.id)
        assert len(captures) == 1
        assert captures[0].requested_uri == url
        assert captures[0].final_uri == url
        assert captures[0].content_sha256 == source.content_sha256
        assert captures[0].fetched_at.tzinfo is not None
        assert repository.capture_exchange_source(
            source_url=url, claimed_published_on=date(2026, 4, 24),
        ).id == source.id
        assert len(repository.read_capture_diagnostics(source_id=source.id)) == 2
        with pytest.raises(DBAPIError):
            session.execute(text("UPDATE quant_instrument_rule_captures SET requested_uri='changed' "
                                 "WHERE id=:id"), {"id": captures[0].id})
        session.rollback()
        monkeypatch.setattr(module, "fetch_exchange_rule_artifact", lambda requested:
                            replace(artifact, content_sha256="0" * 64))
        with pytest.raises(ValueError, match="identity mismatch"):
            repository.capture_exchange_source(source_url=url,
                                               claimed_published_on=date(2026, 4, 24))
