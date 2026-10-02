"""Shared fixtures/builders for tests.backend.quant_strategy.integration.test_instrument_rule_certificates; no test cases."""

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


DAY = date(2030, 1, 2)


NEXT = date(2030, 1, 3)


FETCHED = datetime(2026, 9, 25, tzinfo=timezone.utc)


KEY = b"isolated-rule-review-key-at-least-32bytes"


def _capture(session, repository, *, url, raw):
    source = repository.register_source(
        source_uri=url, media_type="application/pdf", raw_content=raw,
        published_on=date(2026, 4, 24),
    )
    capture = InstrumentRuleCapture(
        id=uuid4(), source_id=source.id, requested_uri=url, final_uri=url,
        content_sha256=sha256(raw).hexdigest(), fetched_at=FETCHED,
    )
    session.add(capture)
    session.flush()
    return source, capture


def _seed_evidence(session, *, symbol="688777.SH", step=1):
    repository = InstrumentRuleRepository(session)
    host = "www.szse.cn" if symbol.endswith(".SZ") else "www.sse.com.cn"
    minimum = 100 if symbol.endswith(".SZ") else 200
    rule_source, rule_capture = _capture(
        session, repository, url=f"https://{host}/rules/test-rule-{step}.pdf",
        raw=f"isolated test official-rule fixture {step}".encode(),
    )
    identity_source, identity_capture = _capture(
        session, repository, url=f"https://{host}/list/test-security.pdf",
        raw=f"isolated test identity fixture {symbol}".encode(),
    )
    rule = InstrumentTradingRule(
        symbol=symbol, asset_type="stock", effective_from=date(2026, 4, 24),
        effective_through=None, published_on=rule_source.published_on,
        source_ref=source_reference(rule_source), price_tick=Decimal(".01"),
        min_buy_quantity=minimum, buy_quantity_step=step,
        max_buy_quantity=100000, roundtrip_days=1,
    )
    observation = repository.observe_rule(source_id=rule_source.id, rule=rule)
    return observation, rule_capture, identity_capture, identity_source, rule


def _signed(session, *, symbol="688777.SH", step=1):
    observation, rule_capture, identity_capture, identity_source, rule = _seed_evidence(
        session, symbol=symbol, step=step)
    certificate_id = uuid4()
    signature = hmac.new(KEY, _payload(
        certificate_id=certificate_id, observation=observation,
        rule_capture=rule_capture, identity_capture=identity_capture,
        identity_source=identity_source, reviewer_id="rule-reviewer",
        interpretation_note="verified fixture", identity_symbol=symbol,
        identity_asset_type="stock", identity_locator="fixture identity row 1",
    ), "sha256").hexdigest()
    return (certificate_id, observation, rule_capture, identity_capture,
            signature, rule)
