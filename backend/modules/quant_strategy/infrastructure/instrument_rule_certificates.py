"""Signed, captured exchange rules for forward-only production decisions."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from uuid import UUID
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from sqlalchemy import select

from backend.modules.quant_strategy.domain.instrument_rules import (
    InstrumentRuleUnavailable, InstrumentTradingRule, resolve_instrument_rule,
)
from .instrument_rule_models import (
    InstrumentRuleCapture, InstrumentRuleCertificate, InstrumentRuleObservation,
    InstrumentRuleSource,
)
from .instrument_rule_repository import InstrumentRuleRepository
from .official_rule_artifact import exchange_url


def _review_key(reviewer_id: str) -> bytes:
    try:
        configured = json.loads(os.environ.get("LIVEPROFIT_RULE_REVIEW_KEYS", ""))
    except (ValueError, TypeError):
        configured = None
    if (not isinstance(configured, dict) or not configured
            or any(not isinstance(name, str) or not name.strip()
                   or not isinstance(value, str) for name, value in configured.items())
            or len(set(configured.values())) != len(configured)):
        raise InstrumentRuleUnavailable("rule reviewer keys unavailable or duplicated")
    encoded = configured.get(reviewer_id)
    try:
        key = base64.b64decode(encoded, validate=True)
    except (TypeError, ValueError, binascii.Error):
        key = None
    if key is None or len(key) < 32 or base64.b64encode(key).decode("ascii") != encoded:
        raise InstrumentRuleUnavailable("rule reviewer key unavailable")
    return key


def _payload(*, certificate_id: UUID, observation: InstrumentRuleObservation,
             rule_capture: InstrumentRuleCapture, identity_capture: InstrumentRuleCapture,
             identity_source: InstrumentRuleSource, reviewer_id: str,
             interpretation_note: str, identity_symbol: str,
             identity_asset_type: str, identity_locator: str) -> bytes:
    value = {
        "certificate_id": str(certificate_id), "observation_id": str(observation.id),
        "rule_capture_id": str(rule_capture.id),
        "identity_capture_id": str(identity_capture.id),
        "symbol": observation.symbol, "asset_type": observation.asset_type,
        "rule_sha256": observation.rule_sha256,
        "rule_source_sha256": rule_capture.content_sha256,
        "identity_source_sha256": identity_source.content_sha256,
        "identity_symbol": identity_symbol,
        "identity_asset_type": identity_asset_type,
        "identity_locator": identity_locator.strip(),
        "reviewer_id": reviewer_id.strip(), "interpretation_note": interpretation_note.strip(),
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _authorization_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class LiveRuleAuthorization:
    rule: InstrumentTradingRule
    certificate_id: UUID
    authorized_at: datetime


class InstrumentRuleCertificateRepository:
    def __init__(self, session) -> None:
        self.session = session

    def _evidence(self, observation_id: UUID, rule_capture_id: UUID,
                  identity_capture_id: UUID):
        observation = self.session.get(InstrumentRuleObservation, observation_id)
        rule_capture = self.session.get(InstrumentRuleCapture, rule_capture_id)
        identity_capture = self.session.get(InstrumentRuleCapture, identity_capture_id)
        if observation is None or rule_capture is None or identity_capture is None:
            raise InstrumentRuleUnavailable("certificate evidence missing")
        if rule_capture.id == identity_capture.id or observation.source_id != rule_capture.source_id:
            raise InstrumentRuleUnavailable("rule capture is not tied to observation")
        rule_source = self.session.get(InstrumentRuleSource, observation.source_id)
        identity_source = self.session.get(InstrumentRuleSource, identity_capture.source_id)
        if rule_source is None or identity_source is None or rule_source.id == identity_source.id:
            raise InstrumentRuleUnavailable("independent identity source required")
        for capture, source in ((rule_capture, rule_source), (identity_capture, identity_source)):
            if (sha256(bytes(source.raw_content)).hexdigest() != source.content_sha256
                    or source.content_sha256 != capture.content_sha256
                    or exchange_url(capture.final_uri) != source.source_uri
                    or exchange_url(capture.requested_uri) != capture.requested_uri
                    or capture.fetched_at.tzinfo is None):
                raise InstrumentRuleUnavailable("captured original identity mismatch")
        rule = InstrumentRuleRepository._to_rule(observation, rule_source)
        exchange = {"SH": "sse.com.cn", "SZ": "szse.cn", "BJ": "bse.cn"}.get(
            rule.symbol.rsplit(".", 1)[-1])
        if exchange is None or any(
            not (host == exchange or host.endswith("." + exchange))
            for host in (urlsplit(rule_source.source_uri).hostname,
                         urlsplit(identity_source.source_uri).hostname)
            if host is not None
        ):
            raise InstrumentRuleUnavailable("certificate exchange does not match security")
        return observation, rule_capture, identity_capture, identity_source, rule

    def issue(self, *, certificate_id: UUID, observation_id: UUID,
              rule_capture_id: UUID, identity_capture_id: UUID,
              reviewer_id: str, interpretation_note: str,
              identity_symbol: str, identity_asset_type: str,
              identity_locator: str,
              review_signature: str) -> InstrumentRuleCertificate:
        """Append signed interpretation; caller must commit before forward use."""
        if (not isinstance(certificate_id, UUID) or not isinstance(reviewer_id, str)
                or not reviewer_id.strip() or not isinstance(interpretation_note, str)
                or not interpretation_note.strip() or not isinstance(review_signature, str)
                or not isinstance(identity_locator, str) or not identity_locator.strip()):
            raise ValueError("complete certificate review required")
        observation, rule_capture, identity_capture, identity_source, rule = self._evidence(
            observation_id, rule_capture_id, identity_capture_id)
        if identity_symbol != rule.symbol or identity_asset_type != rule.asset_type:
            raise InstrumentRuleUnavailable("identity interpretation differs from rule observation")
        value = _payload(certificate_id=certificate_id, observation=observation,
                         rule_capture=rule_capture, identity_capture=identity_capture,
                         identity_source=identity_source, reviewer_id=reviewer_id,
                         interpretation_note=interpretation_note,
                         identity_symbol=identity_symbol, identity_asset_type=identity_asset_type,
                         identity_locator=identity_locator)
        expected = hmac.new(_review_key(reviewer_id.strip()), value, "sha256").hexdigest()
        if not hmac.compare_digest(expected, review_signature):
            raise InstrumentRuleUnavailable("invalid rule certificate signature")
        certificate = InstrumentRuleCertificate(
            id=certificate_id, observation_id=observation.id,
            rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
            symbol=rule.symbol, asset_type=rule.asset_type,
            identity_symbol=identity_symbol, identity_asset_type=identity_asset_type,
            identity_locator=identity_locator.strip(),
            reviewer_id=reviewer_id.strip(), interpretation_note=interpretation_note.strip(),
            review_signature=review_signature,
        )
        self.session.add(certificate)
        self.session.flush()
        return certificate

    def resolve_live_now(self, *, symbol: str, decision_date: date,
                         execution_date: date) -> LiveRuleAuthorization:
        """Authorize a new decision at this read, never a frozen earlier as-of."""
        if type(decision_date) is not date or type(execution_date) is not date:
            raise InstrumentRuleUnavailable("explicit decision and execution dates required")
        if decision_date != _authorization_now().astimezone(ZoneInfo("Asia/Shanghai")).date():
            raise InstrumentRuleUnavailable("forward certificates cannot authorize historical replay")
        rows = list(self.session.scalars(select(InstrumentRuleCertificate).where(
            InstrumentRuleCertificate.symbol == symbol,
        ).execution_options(populate_existing=True)))
        eligible: list[tuple[InstrumentRuleCertificate, InstrumentTradingRule]] = []
        for certificate in rows:
            # A certificate written in this planning transaction is not yet a
            # separately visible authorization, even if recorded_at is old.
            with self.session.get_bind().connect() as independent:
                committed = independent.scalar(select(InstrumentRuleCertificate.id).where(
                    InstrumentRuleCertificate.id == certificate.id))
            if committed is None:
                raise InstrumentRuleUnavailable("certificate is not committed independently")
            observation, rule_capture, identity_capture, identity_source, rule = self._evidence(
                certificate.observation_id, certificate.rule_capture_id,
                certificate.identity_capture_id)
            if (certificate.symbol != rule.symbol or certificate.asset_type != rule.asset_type
                    or certificate.identity_symbol != rule.symbol
                    or certificate.identity_asset_type != rule.asset_type
                    or not certificate.identity_locator.strip()):
                raise InstrumentRuleUnavailable("certificate security identity mismatch")
            value = _payload(certificate_id=certificate.id, observation=observation,
                             rule_capture=rule_capture, identity_capture=identity_capture,
                             identity_source=identity_source, reviewer_id=certificate.reviewer_id,
                             interpretation_note=certificate.interpretation_note,
                             identity_symbol=certificate.identity_symbol,
                             identity_asset_type=certificate.identity_asset_type,
                             identity_locator=certificate.identity_locator)
            expected = hmac.new(_review_key(certificate.reviewer_id), value, "sha256").hexdigest()
            if not hmac.compare_digest(expected, certificate.review_signature):
                raise InstrumentRuleUnavailable("certificate signature mismatch")
            if (rule.effective_from <= execution_date
                    and (rule.effective_through is None or execution_date <= rule.effective_through)
                    and rule.published_on < decision_date
                    and rule_capture.fetched_at.date() < decision_date
                    and identity_capture.fetched_at.date() < decision_date
                    and certificate.recorded_at.date() <= decision_date):
                eligible.append((certificate, rule))
        resolved = resolve_instrument_rule(
            [rule for _, rule in eligible], symbol=symbol,
            decision_date=decision_date, execution_date=execution_date)
        authorized_at = _authorization_now()
        if authorized_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != decision_date:
            raise InstrumentRuleUnavailable("decision date changed during rule authorization")
        return LiveRuleAuthorization(
            rule=resolved, certificate_id=eligible[0][0].id,
            authorized_at=authorized_at,
        )
