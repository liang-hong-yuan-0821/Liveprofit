# test-catalog-begin
# {
#   "purpose": "量化策略 / instrument_rule_certificates（品种规则）：Certificate tests migrate/write only conftest's isolated quant strategy DB.",
#   "keywords": [
#     "量化策略",
#     "认证证书",
#     "证券数据",
#     "品种规则",
#     "来源证据",
#     "instrument_rule_certificates",
#     "certificate",
#     "instrument",
#     "rule",
#     "source"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/domain/instrument_rules.py",
#     "backend/modules/quant_strategy/infrastructure/instrument_rule_certificates.py",
#     "backend/modules/quant_strategy/infrastructure/instrument_rule_models.py",
#     "backend/modules/quant_strategy/infrastructure/instrument_rule_repository.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Certificate tests migrate/write only conftest's isolated quant strategy DB."""

from tests.backend.quant_strategy.support.instrument_rule_certificates import (
    DAY,
    NEXT,
    FETCHED,
    KEY,
    _capture,
    _seed_evidence,
    _signed,
)

import base64
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import hmac
import json
from uuid import uuid4

import pytest

from backend.modules.quant_strategy.domain.instrument_rules import (
    InstrumentRuleUnavailable, InstrumentTradingRule,
)
from backend.modules.quant_strategy.infrastructure.instrument_rule_certificates import (
    InstrumentRuleCertificateRepository, _payload,
)
from backend.modules.quant_strategy.infrastructure.instrument_rule_models import InstrumentRuleCapture
from backend.modules.quant_strategy.infrastructure.instrument_rule_repository import (
    InstrumentRuleRepository, source_reference,
)










def test_signed_forward_rule_requires_committed_two_source_evidence(env, monkeypatch):
    from backend.modules.quant_strategy.infrastructure import instrument_rule_certificates as module
    monkeypatch.setattr(module, "_authorization_now", lambda: datetime(2030, 1, 2, 12, tzinfo=timezone.utc))
    monkeypatch.setenv("LIVEPROFIT_RULE_REVIEW_KEYS", json.dumps({
        "rule-reviewer": base64.b64encode(KEY).decode(),
    }))
    with env["session_factory"]() as session:
        certificate_id, observation, rule_capture, identity_capture, signature, rule = _signed(session)
        repository = InstrumentRuleCertificateRepository(session)
        with pytest.raises(InstrumentRuleUnavailable, match="identity interpretation"):
            repository.issue(
                certificate_id=certificate_id, observation_id=observation.id,
                rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
                reviewer_id="rule-reviewer", interpretation_note="verified fixture",
                identity_symbol="688000.SH", identity_asset_type="stock",
                identity_locator="fixture identity row 1", review_signature=signature,
            )
        with pytest.raises(InstrumentRuleUnavailable, match="signature"):
            repository.issue(
                certificate_id=certificate_id, observation_id=observation.id,
                rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
                reviewer_id="rule-reviewer", interpretation_note="verified fixture",
                identity_symbol=rule.symbol, identity_asset_type="stock",
                identity_locator="fixture identity row 1",
                review_signature="0" * 64,
            )
        repository.issue(
            certificate_id=certificate_id, observation_id=observation.id,
            rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
            reviewer_id="rule-reviewer", interpretation_note="verified fixture",
            identity_symbol=rule.symbol, identity_asset_type="stock",
            identity_locator="fixture identity row 1",
            review_signature=signature,
        )
        with pytest.raises(InstrumentRuleUnavailable, match="not committed"):
            repository.resolve_live_now(symbol=rule.symbol, decision_date=DAY,
                                        execution_date=NEXT)
        session.commit()
    with env["session_factory"]() as fresh:
        authorized = InstrumentRuleCertificateRepository(fresh).resolve_live_now(
            symbol=rule.symbol, decision_date=DAY, execution_date=NEXT)
        assert authorized.rule == rule and authorized.certificate_id == certificate_id
        assert authorized.authorized_at == datetime(2030, 1, 2, 12, tzinfo=timezone.utc)
        midnight = iter((datetime(2030, 1, 2, 15, 59, tzinfo=timezone.utc),
                         datetime(2030, 1, 2, 16, 0, tzinfo=timezone.utc)))
        monkeypatch.setattr(module, "_authorization_now", lambda: next(midnight))
        with pytest.raises(InstrumentRuleUnavailable, match="date changed"):
            InstrumentRuleCertificateRepository(fresh).resolve_live_now(
                symbol=rule.symbol, decision_date=DAY, execution_date=NEXT)
        with pytest.raises(InstrumentRuleUnavailable, match="no unique"):
            monkeypatch.setattr(module, "_authorization_now", lambda: datetime(2030, 1, 2, 12, tzinfo=timezone.utc))
            InstrumentRuleCertificateRepository(fresh).resolve_live_now(
                symbol="688778.SH", decision_date=DAY, execution_date=NEXT)


def test_certificate_late_or_missing_key_remains_unavailable(env, monkeypatch):
    from backend.modules.quant_strategy.infrastructure import instrument_rule_certificates as module
    monkeypatch.setattr(module, "_authorization_now", lambda: datetime(2030, 1, 2, 12, tzinfo=timezone.utc))
    monkeypatch.setenv("LIVEPROFIT_RULE_REVIEW_KEYS", json.dumps({
        "rule-reviewer": base64.b64encode(KEY).decode(),
    }))
    with env["session_factory"]() as session:
        certificate_id, observation, rule_capture, identity_capture, signature, rule = _signed(
            session, symbol="688779.SH")
        InstrumentRuleCertificateRepository(session).issue(
            certificate_id=certificate_id, observation_id=observation.id,
            rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
            reviewer_id="rule-reviewer", interpretation_note="verified fixture",
            identity_symbol=rule.symbol, identity_asset_type="stock",
            identity_locator="fixture identity row 1",
            review_signature=signature,
        )
        session.commit()
        with pytest.raises(InstrumentRuleUnavailable, match="historical replay"):
            InstrumentRuleCertificateRepository(session).resolve_live_now(
                symbol=rule.symbol, decision_date=date(2026, 9, 25),
                execution_date=date(2026, 9, 28))
        monkeypatch.delenv("LIVEPROFIT_RULE_REVIEW_KEYS")
        with pytest.raises(InstrumentRuleUnavailable, match="keys unavailable"):
            InstrumentRuleCertificateRepository(session).resolve_live_now(
                symbol=rule.symbol, decision_date=DAY, execution_date=NEXT)


def test_two_signed_overlapping_rules_fail_closed(env, monkeypatch):
    from backend.modules.quant_strategy.infrastructure import instrument_rule_certificates as module
    monkeypatch.setattr(module, "_authorization_now", lambda: datetime(2030, 1, 2, 12, tzinfo=timezone.utc))
    monkeypatch.setenv("LIVEPROFIT_RULE_REVIEW_KEYS", json.dumps({
        "rule-reviewer": base64.b64encode(KEY).decode(),
    }))
    with env["session_factory"]() as session:
        repository = InstrumentRuleCertificateRepository(session)
        for step in (1, 100):
            certificate_id, observation, rule_capture, identity_capture, signature, rule = _signed(
                session, symbol="688780.SH", step=step)
            repository.issue(
                certificate_id=certificate_id, observation_id=observation.id,
                rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
                reviewer_id="rule-reviewer", interpretation_note="verified fixture",
                identity_symbol=rule.symbol, identity_asset_type="stock",
                identity_locator="fixture identity row 1", review_signature=signature,
            )
        session.commit()
    with env["session_factory"]() as fresh:
        with pytest.raises(InstrumentRuleUnavailable, match="no unique"):
            InstrumentRuleCertificateRepository(fresh).resolve_live_now(
                symbol="688780.SH", decision_date=DAY, execution_date=NEXT)
