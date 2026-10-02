"""Unverified external fill reports, separate from accepted order fills."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class AccountFillReportRow(Base):
    __tablename__ = "account_fill_reports"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "portfolio_id"],
                             ["suggested_orders.id", "suggested_orders.portfolio_id"],
                             ondelete="RESTRICT", name="fk_account_fill_report_order_portfolio"),
        UniqueConstraint("portfolio_id", "source_type", "source_ref", name="uq_account_fill_report_source"),
        UniqueConstraint("id", "portfolio_id", name="uq_account_fill_report_identity"),
        CheckConstraint("executed_at IS NULL OR "
                        "(executed_at <= captured_at AND "
                        "(market <> 'CN' OR (executed_at AT TIME ZONE 'Asia/Shanghai')::date = fill_trade_date))",
                        name="ck_account_fill_report_execution_time"),
        CheckConstraint("quantity > 0 AND fill_price > 0 AND "
                        "(fee IS NULL OR fee >= 0) AND "
                        "side IN ('BUY','SELL') AND length(trim(market)) > 0 AND length(trim(symbol)) > 0 AND "
                        "source_type IN ('MANUAL_REPORT','BROKER_EXPORT','BROKER_API') AND "
                        "length(trim(source_ref)) > 0 AND "
                        "payload_sha256 ~ '^[0-9a-f]{64}$' AND "
                        "(evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$')",
                        name="ck_account_fill_report_values"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    fill_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    fee: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    reporter_claim: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class AccountFillReportAssessmentRow(Base):
    """Immutable diagnostic snapshot; it never authorizes a trade projection."""

    __tablename__ = "account_fill_report_assessments"
    __table_args__ = (
        ForeignKeyConstraint(["report_id", "portfolio_id"],
                             ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                             ondelete="RESTRICT", name="fk_fill_assessment_report_portfolio"),
        UniqueConstraint("portfolio_id", "assessment_ref", name="uq_fill_assessment_ref"),
        CheckConstraint("jsonb_typeof(issues) = 'array' AND length(trim(assessment_ref)) > 0 AND "
                        "payload_sha256 ~ '^[0-9a-f]{64}$'",
                        name="ck_fill_assessment_values"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    order_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issues: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    assessment_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())


class AccountFillReportResolutionRow(Base):
    """An unverified correction or void declaration, never a reversal of a booked fill."""

    __tablename__ = "account_fill_report_resolutions"
    __table_args__ = (
        ForeignKeyConstraint(["report_id", "portfolio_id"],
                             ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                             ondelete="RESTRICT", name="fk_fill_resolution_report_portfolio"),
        ForeignKeyConstraint(["replacement_report_id", "portfolio_id"],
                             ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                             ondelete="RESTRICT", name="fk_fill_resolution_replacement_portfolio"),
        UniqueConstraint("report_id", name="uq_fill_resolution_report"),
        UniqueConstraint("id", "portfolio_id", name="uq_fill_resolution_identity"),
        UniqueConstraint("replacement_report_id", name="uq_fill_resolution_replacement"),
        UniqueConstraint("portfolio_id", "source_type", "source_ref", name="uq_fill_resolution_source"),
        CheckConstraint("source_type IN ('MANUAL_REPORT','BROKER_EXPORT','BROKER_API') AND "
                        "report_id IS DISTINCT FROM replacement_report_id AND "
                        "(action = 'CORRECT' AND replacement_report_id IS NOT NULL OR "
                        "action = 'VOID' AND replacement_report_id IS NULL) AND "
                        "length(trim(reason)) > 0 AND length(trim(source_ref)) > 0 AND "
                        "payload_sha256 ~ '^[0-9a-f]{64}$' AND "
                        "(evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$')",
                        name="ck_fill_resolution_values"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    replacement_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    reporter_claim: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class AccountFillPostingRow(Base):
    """One-to-one identity link; the row alone proves no external authentication."""

    __tablename__ = "account_fill_postings"
    __table_args__ = (
        ForeignKeyConstraint(["report_id", "portfolio_id"],
                             ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                             ondelete="RESTRICT", name="fk_fill_posting_report_portfolio"),
        ForeignKeyConstraint(["fill_event_id", "portfolio_id"],
                             ["order_fill_events.id", "order_fill_events.portfolio_id"],
                             ondelete="RESTRICT", name="fk_fill_posting_event_portfolio"),
        ForeignKeyConstraint(["ledger_movement_id", "portfolio_id"],
                             ["account_ledger_movements.id", "account_ledger_movements.portfolio_id"],
                             ondelete="RESTRICT", name="fk_fill_posting_ledger_portfolio"),
        UniqueConstraint("report_id", name="uq_fill_posting_report"),
        UniqueConstraint("fill_event_id", name="uq_fill_posting_event"),
        UniqueConstraint("ledger_movement_id", name="uq_fill_posting_ledger"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fill_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ledger_movement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position_quantity_before: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    position_average_cost_before: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                server_default=func.clock_timestamp())


class AccountFillPostingVoidRow(Base):
    """One-to-one immutable link between a declared void and reversal facts."""

    __tablename__ = "account_fill_posting_voids"
    __table_args__ = (
        UniqueConstraint("resolution_id", name="uq_fill_posting_void_resolution"),
        UniqueConstraint("posting_id", name="uq_fill_posting_void_posting"),
        UniqueConstraint("fill_event_id", name="uq_fill_posting_void_event"),
        UniqueConstraint("ledger_movement_id", name="uq_fill_posting_void_ledger"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    resolution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_fill_report_resolutions.id", ondelete="RESTRICT"),
        nullable=False)
    posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_fill_postings.id", ondelete="RESTRICT"), nullable=False)
    fill_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_fill_events.id", ondelete="RESTRICT"), nullable=False)
    ledger_movement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_ledger_movements.id", ondelete="RESTRICT"),
        nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())


class AccountFillPostingCorrectionRow(Base):
    """Atomic identity link for a reversed fill and accepted replacement."""

    __tablename__ = "account_fill_posting_corrections"
    __table_args__ = (
        UniqueConstraint("resolution_id", name="uq_fill_posting_correction_resolution"),
        UniqueConstraint("original_posting_id", name="uq_fill_posting_correction_original"),
        UniqueConstraint("void_fill_event_id", name="uq_fill_posting_correction_void_event"),
        UniqueConstraint("void_ledger_movement_id", name="uq_fill_posting_correction_void_ledger"),
        UniqueConstraint("replacement_posting_id", name="uq_fill_posting_correction_replacement"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    resolution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_fill_report_resolutions.id", ondelete="RESTRICT"),
        nullable=False)
    original_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_fill_postings.id", ondelete="RESTRICT"), nullable=False)
    void_fill_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_fill_events.id", ondelete="RESTRICT"), nullable=False)
    void_ledger_movement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_ledger_movements.id", ondelete="RESTRICT"),
        nullable=False)
    replacement_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account_fill_postings.id", ondelete="RESTRICT"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())


class AccountFillReportReviewRow(Base):
    """Signed manual review claim; account and evidence authenticity need separate checks."""

    __tablename__ = "account_fill_report_reviews"
    __table_args__ = (
        ForeignKeyConstraint(["report_id", "portfolio_id"],
                             ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                             ondelete="RESTRICT", name="fk_fill_review_report_portfolio"),
        UniqueConstraint("report_id", name="uq_fill_review_report"),
        UniqueConstraint("portfolio_id", "review_ref", name="uq_fill_review_ref"),
        CheckConstraint(
            "report_sha256 ~ '^[0-9a-f]{64}$' AND evidence_sha256 ~ '^[0-9a-f]{64}$' AND "
            "review_signature ~ '^[0-9a-f]{64}$' AND length(trim(review_ref)) > 0 AND "
            "length(trim(reviewed_by)) > 0 AND length(trim(reason)) > 0",
            name="ck_fill_review_values"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    review_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    review_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())


class AccountFillResolutionReviewRow(Base):
    """Signed review of a resolution claim; broker authenticity is separate."""

    __tablename__ = "account_fill_resolution_reviews"
    __table_args__ = (
        ForeignKeyConstraint(["resolution_id", "portfolio_id"],
                             ["account_fill_report_resolutions.id",
                              "account_fill_report_resolutions.portfolio_id"],
                             ondelete="RESTRICT", name="fk_fill_resolution_review_identity"),
        UniqueConstraint("resolution_id", name="uq_fill_resolution_review_resolution"),
        UniqueConstraint("portfolio_id", "review_ref", name="uq_fill_resolution_review_ref"),
        CheckConstraint(
            "resolution_sha256 ~ '^[0-9a-f]{64}$' AND evidence_sha256 ~ '^[0-9a-f]{64}$' AND "
            "review_signature ~ '^[0-9a-f]{64}$' AND length(trim(review_ref)) > 0 AND "
            "length(trim(reviewed_by)) > 0 AND length(trim(reason)) > 0",
            name="ck_fill_resolution_review_values"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    resolution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    resolution_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    review_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    review_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())


class AccountFillEvidenceArtifactRow(Base):
    """Raw imported bytes; neither broker origin nor account identity is certified."""

    __tablename__ = "account_fill_evidence_artifacts"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "source_ref", name="uq_fill_evidence_source"),
        UniqueConstraint("portfolio_id", "content_sha256", name="uq_fill_evidence_content"),
        CheckConstraint(
            "length(trim(source_ref)) > 0 AND length(trim(media_type)) > 0 AND "
            "content_sha256 ~ '^[0-9a-f]{64}$' AND "
            "size_bytes BETWEEN 1 AND 8388608 AND octet_length(raw_bytes) = size_bytes",
            name="ck_fill_evidence_values"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())
