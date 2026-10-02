"""Immutable original documents and interpreted per-security trading rules."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, LargeBinary
from sqlalchemy import Numeric, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class InstrumentRuleSource(Base):
    __tablename__ = "quant_instrument_rule_sources"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    published_on: Mapped[date] = mapped_column(Date, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())

    __table_args__ = (
        UniqueConstraint("source_uri", "content_sha256", name="uq_instrument_rule_source_content"),
        CheckConstraint("length(trim(source_uri)) > 0 AND length(trim(media_type)) > 0 "
                        "AND octet_length(raw_content) > 0", name="ck_instrument_rule_source_body"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'",
                        name="ck_instrument_rule_source_hash"),
    )


class InstrumentRuleObservation(Base):
    __tablename__ = "quant_instrument_rule_observations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("quant_instrument_rule_sources.id", ondelete="RESTRICT"),
        nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_through: Mapped[date | None] = mapped_column(Date)
    price_tick: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    min_buy_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_quantity_step: Mapped[int] = mapped_column(Integer, nullable=False)
    max_buy_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    roundtrip_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rule_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())

    __table_args__ = (
        UniqueConstraint("source_id", "symbol", "effective_from",
                         name="uq_instrument_rule_source_symbol_from"),
        CheckConstraint("length(trim(symbol)) > 0 AND asset_type IN ('stock','etf')",
                        name="ck_instrument_rule_identity"),
        CheckConstraint("effective_through IS NULL OR effective_through >= effective_from",
                        name="ck_instrument_rule_dates"),
        CheckConstraint("price_tick > 0 AND min_buy_quantity > 0 AND buy_quantity_step > 0 "
                        "AND max_buy_quantity >= min_buy_quantity AND roundtrip_days IN (0,1)",
                        name="ck_instrument_rule_values"),
        CheckConstraint("rule_sha256 ~ '^[0-9a-f]{64}$'", name="ck_instrument_rule_hash"),
        Index("ix_instrument_rule_symbol_dates", "symbol", "effective_from", "effective_through"),
    )


class InstrumentRuleCapture(Base):
    """A local HTTPS fetch event; independent of a source's claimed publication date."""

    __tablename__ = "quant_instrument_rule_captures"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("quant_instrument_rule_sources.id", ondelete="RESTRICT"),
        nullable=False)
    requested_uri: Mapped[str] = mapped_column(Text, nullable=False)
    final_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())

    __table_args__ = (
        CheckConstraint("length(trim(requested_uri)) > 0 AND length(trim(final_uri)) > 0",
                        name="ck_instrument_rule_capture_uris"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'",
                        name="ck_instrument_rule_capture_hash"),
        Index("ix_instrument_rule_capture_source", "source_id", "recorded_at"),
    )


class InstrumentRuleCertificate(Base):
    """Reviewed forward authorization linking two captured exchange artifacts."""

    __tablename__ = "quant_instrument_rule_certificates"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    observation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey(
        "quant_instrument_rule_observations.id", ondelete="RESTRICT"), nullable=False)
    rule_capture_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey(
        "quant_instrument_rule_captures.id", ondelete="RESTRICT"), nullable=False)
    identity_capture_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey(
        "quant_instrument_rule_captures.id", ondelete="RESTRICT"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    identity_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    identity_locator: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    interpretation_note: Mapped[str] = mapped_column(Text, nullable=False)
    review_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())

    __table_args__ = (
        UniqueConstraint("observation_id", name="uq_instrument_rule_certificate_observation"),
        CheckConstraint("rule_capture_id <> identity_capture_id AND "
                        "length(trim(symbol)) > 0 AND asset_type IN ('stock','etf') AND "
                        "identity_symbol = symbol AND identity_asset_type = asset_type AND "
                        "length(trim(identity_locator)) > 0 AND "
                        "length(trim(reviewer_id)) > 0 AND length(trim(interpretation_note)) > 0 AND "
                        "review_signature ~ '^[0-9a-f]{64}$'",
                        name="ck_instrument_rule_certificate_values"),
        Index("ix_instrument_rule_certificate_symbol", "symbol", "asset_type"),
    )
