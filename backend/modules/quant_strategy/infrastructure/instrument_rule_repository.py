"""Append-only rule observations; reads are diagnostic until sources are certified."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.modules.quant_strategy.domain.instrument_rules import (
    InstrumentRuleUnavailable, InstrumentTradingRule, resolve_instrument_rule,
)
from backend.modules.quant_strategy.infrastructure.instrument_rule_models import (
    InstrumentRuleCapture, InstrumentRuleObservation, InstrumentRuleSource,
)
from backend.modules.quant_strategy.infrastructure.official_rule_artifact import (
    exchange_url, fetch_exchange_rule_artifact,
)


def source_reference(source: InstrumentRuleSource) -> str:
    return f"{source.source_uri}#sha256={source.content_sha256}"


def _rule_payload(rule: InstrumentTradingRule) -> dict:
    return {
        "symbol": rule.symbol, "asset_type": rule.asset_type,
        "effective_from": rule.effective_from.isoformat(),
        "effective_through": rule.effective_through.isoformat() if rule.effective_through else None,
        "price_tick": format(rule.price_tick.normalize(), "f"),
        "min_buy_quantity": rule.min_buy_quantity,
        "buy_quantity_step": rule.buy_quantity_step,
        "max_buy_quantity": rule.max_buy_quantity,
        "roundtrip_days": rule.roundtrip_days,
    }


def _digest_rule(rule: InstrumentTradingRule) -> str:
    payload = json.dumps(_rule_payload(rule), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


class InstrumentRuleRepository:
    def __init__(self, session) -> None:
        self.session = session

    def capture_exchange_source(self, *, source_url: str,
                                claimed_published_on: date) -> InstrumentRuleSource:
        """Persist current official-host bytes; the claimed date is unverified."""
        requested = exchange_url(source_url)
        artifact = fetch_exchange_rule_artifact(requested)
        if (artifact.requested_uri != requested
                or exchange_url(artifact.source_uri) != artifact.source_uri
                or sha256(artifact.raw_content).hexdigest() != artifact.content_sha256
                or not isinstance(artifact.fetched_at, datetime)
                or artifact.fetched_at.tzinfo is None):
            raise ValueError("captured rule artifact identity mismatch")
        source = self.register_source(
            source_uri=artifact.source_uri, media_type=artifact.media_type,
            raw_content=artifact.raw_content, published_on=claimed_published_on,
        )
        self.session.add(InstrumentRuleCapture(
            id=uuid4(), source_id=source.id, requested_uri=artifact.requested_uri,
            final_uri=artifact.source_uri, content_sha256=artifact.content_sha256,
            fetched_at=artifact.fetched_at,
        ))
        self.session.flush()
        return source

    def read_capture_diagnostics(self, *, source_id: UUID) -> tuple[InstrumentRuleCapture, ...]:
        """Read local capture events; none certifies historical publication."""
        source = self.session.get(InstrumentRuleSource, source_id)
        if source is None or sha256(bytes(source.raw_content)).hexdigest() != source.content_sha256:
            raise InstrumentRuleUnavailable("rule source artifact unavailable or corrupt")
        rows = self.session.scalars(select(InstrumentRuleCapture).where(
            InstrumentRuleCapture.source_id == source_id,
        ).order_by(InstrumentRuleCapture.recorded_at, InstrumentRuleCapture.id)
            .execution_options(populate_existing=True)).all()
        for row in rows:
            if (row.content_sha256 != source.content_sha256
                    or row.final_uri != source.source_uri
                    or exchange_url(row.requested_uri) != row.requested_uri
                    or exchange_url(row.final_uri) != row.final_uri):
                raise InstrumentRuleUnavailable("rule capture/source identity mismatch")
        return tuple(rows)

    def register_source(self, *, source_uri: str, media_type: str,
                        raw_content: bytes, published_on: date) -> InstrumentRuleSource:
        if (not isinstance(source_uri, str) or not source_uri.strip()
                or not isinstance(media_type, str) or not media_type.strip()
                or not isinstance(raw_content, bytes) or not raw_content
                or type(published_on) is not date):
            raise ValueError("complete original rule artifact required")
        digest = sha256(raw_content).hexdigest()
        self.session.execute(insert(InstrumentRuleSource).values(
            id=uuid4(), source_uri=source_uri, media_type=media_type,
            raw_content=raw_content, content_sha256=digest, published_on=published_on,
        ).on_conflict_do_nothing(constraint="uq_instrument_rule_source_content"))
        source = self.session.execute(select(InstrumentRuleSource).where(
            InstrumentRuleSource.source_uri == source_uri,
            InstrumentRuleSource.content_sha256 == digest,
        ).execution_options(populate_existing=True)).scalar_one()
        if (source.media_type != media_type or source.published_on != published_on
                or bytes(source.raw_content) != raw_content):
            raise ValueError("rule source identity conflict")
        return source

    def observe_rule(self, *, source_id: UUID,
                     rule: InstrumentTradingRule) -> InstrumentRuleObservation:
        if not isinstance(source_id, UUID):
            raise ValueError("source UUID required")
        rule.validate()
        if (rule.price_tick.normalize().as_tuple().exponent < -8
                or rule.price_tick >= Decimal("10000000000")
                or rule.max_buy_quantity > 2147483647
                or rule.min_buy_quantity > 2147483647
                or rule.buy_quantity_step > 2147483647):
            raise ValueError("rule exceeds persisted numeric precision")
        source = self.session.get(InstrumentRuleSource, source_id)
        if source is None or sha256(bytes(source.raw_content)).hexdigest() != source.content_sha256:
            raise ValueError("original rule artifact unavailable or corrupt")
        if rule.published_on != source.published_on or rule.source_ref != source_reference(source):
            raise ValueError("rule/source evidence mismatch")
        digest = _digest_rule(rule)
        self.session.execute(insert(InstrumentRuleObservation).values(
            id=uuid4(), source_id=source_id,
            symbol=rule.symbol, asset_type=rule.asset_type,
            effective_from=rule.effective_from, effective_through=rule.effective_through,
            price_tick=rule.price_tick,
            min_buy_quantity=rule.min_buy_quantity,
            buy_quantity_step=rule.buy_quantity_step,
            max_buy_quantity=rule.max_buy_quantity,
            roundtrip_days=rule.roundtrip_days, rule_sha256=digest,
        ).on_conflict_do_nothing(constraint="uq_instrument_rule_source_symbol_from"))
        observation = self.session.execute(select(InstrumentRuleObservation).where(
            InstrumentRuleObservation.source_id == source_id,
            InstrumentRuleObservation.symbol == rule.symbol,
            InstrumentRuleObservation.effective_from == rule.effective_from,
        ).execution_options(populate_existing=True)).scalar_one()
        if observation.rule_sha256 != digest or _rule_payload(self._to_rule(observation, source)) != _rule_payload(rule):
            raise ValueError("rule observation identity conflict")
        return observation

    @staticmethod
    def _to_rule(observation: InstrumentRuleObservation,
                 source: InstrumentRuleSource) -> InstrumentTradingRule:
        if sha256(bytes(source.raw_content)).hexdigest() != source.content_sha256:
            raise InstrumentRuleUnavailable("rule source artifact checksum mismatch")
        rule = InstrumentTradingRule(
            symbol=observation.symbol, asset_type=observation.asset_type,
            effective_from=observation.effective_from,
            effective_through=observation.effective_through,
            published_on=source.published_on, source_ref=source_reference(source),
            price_tick=observation.price_tick,
            min_buy_quantity=observation.min_buy_quantity,
            buy_quantity_step=observation.buy_quantity_step,
            max_buy_quantity=observation.max_buy_quantity,
            roundtrip_days=observation.roundtrip_days,
        )
        rule.validate()
        if _digest_rule(rule) != observation.rule_sha256:
            raise InstrumentRuleUnavailable("rule observation checksum mismatch")
        return rule

    def read_diagnostic_rules(self, *, symbol: str, decision_date: date,
                              execution_date: date) -> tuple[InstrumentTradingRule, ...]:
        rows = self.session.execute(select(InstrumentRuleObservation, InstrumentRuleSource).join(
            InstrumentRuleSource, InstrumentRuleObservation.source_id == InstrumentRuleSource.id,
        ).where(
            InstrumentRuleObservation.symbol == symbol,
            InstrumentRuleObservation.effective_from <= execution_date,
            ((InstrumentRuleObservation.effective_through.is_(None))
             | (InstrumentRuleObservation.effective_through >= execution_date)),
            InstrumentRuleSource.published_on < decision_date,
        ).execution_options(populate_existing=True)).all()
        return tuple(self._to_rule(observation, source) for observation, source in rows)

    def resolve_diagnostic(self, *, symbol: str, decision_date: date,
                           execution_date: date) -> InstrumentTradingRule:
        return resolve_instrument_rule(
            self.read_diagnostic_rules(symbol=symbol, decision_date=decision_date,
                                       execution_date=execution_date),
            symbol=symbol, decision_date=decision_date, execution_date=execution_date,
        )
