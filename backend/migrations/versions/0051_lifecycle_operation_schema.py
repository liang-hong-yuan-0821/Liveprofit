"""Six local history tables and UNKNOWN_PRIOR baselines; LIVE capture is closed.

Schema definitions below are frozen, independent of runtime models. This step
does not claim a continuous history while legacy business writers are unmigrated.
"""
import hashlib
import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

from alembic import op
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.schema import CreateIndex, CreateTable

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

def _define_operation_tables(metadata):
    def col(name, kind, *, nullable=False, primary_key=False):
        if kind is JSONB:
            kind = JSONB(none_as_null=True)
        return Column(name, kind, nullable=nullable, primary_key=primary_key)

    def uid(name, target=None, *, nullable=False, primary_key=False, deferred=False):
        args = [name, UUID(as_uuid=True)]
        if target:
            args.append(ForeignKey(target, ondelete="RESTRICT", deferrable=deferred,
                                   initially="DEFERRED" if deferred else None))
        return Column(*args, nullable=nullable, primary_key=primary_key)

    def stamp(name):
        return Column(name, DateTime(timezone=True), nullable=False,
                      server_default=func.clock_timestamp())

    def sha(name, *, nullable=False):
        return col(name, String(64), nullable=nullable)

    def sha_checks(prefix, *names):
        return [CheckConstraint(f"{name} ~ '^[0-9a-f]{{64}}$'", name=f"ck_{prefix}_{name}")
                for name in names]

    commands = Table(
        "lifecycle_business_commands", metadata,
        uid("id", primary_key=True), uid("portfolio_id", "portfolios.id"),
        col("request_key", String(128)), col("command_seq", BigInteger),
        uid("previous_command_id", nullable=True), sha("previous_manifest_hash", nullable=True),
        col("command_kind", String(40)), col("request_schema_version", Integer),
        col("canonical_request", LargeBinary), sha("request_hash"),
        col("selector_version", String(64)), col("expected_steps", JSONB),
        col("expected_unbound_orders", JSONB), sha("manifest_hash"),
        col("expected_step_count", Integer), col("expected_unbound_order_count", Integer),
        Column("captured_transaction_id", Text, nullable=False,
               server_default=text("pg_current_xact_id()::text")),
        col("actor_type", String(16)), col("actor_ref", String(128)), stamp("recorded_at"),
        UniqueConstraint("portfolio_id", "request_key", name="uq_lc_command_request"),
        UniqueConstraint("portfolio_id", "command_seq", name="uq_lc_command_sequence"),
        UniqueConstraint("id", "portfolio_id", name="uq_lc_command_portfolio"),
        UniqueConstraint("previous_command_id", name="uq_lc_command_previous"),
        ForeignKeyConstraint(["previous_command_id", "portfolio_id"],
                             ["lifecycle_business_commands.id", "lifecycle_business_commands.portfolio_id"],
                             ondelete="RESTRICT", name="fk_lc_command_previous_portfolio"),
        CheckConstraint("command_seq > 0 AND request_schema_version = 1 AND "
                        "length(trim(request_key)) > 0 AND length(trim(actor_ref)) > 0",
                        name="ck_lc_command_identity"),
        CheckConstraint("(command_seq = 1 AND previous_command_id IS NULL AND previous_manifest_hash IS NULL) OR "
                        "(command_seq > 1 AND previous_command_id IS NOT NULL AND previous_manifest_hash IS NOT NULL)",
                        name="ck_lc_command_predecessor"),
        CheckConstraint("jsonb_typeof(expected_steps) = 'array' AND jsonb_typeof(expected_unbound_orders) = 'array' "
                        "AND expected_step_count >= 0 AND expected_unbound_order_count >= 0 "
                        "AND expected_step_count = jsonb_array_length(expected_steps) "
                        "AND expected_unbound_order_count = jsonb_array_length(expected_unbound_orders)",
                        name="ck_lc_command_counts"),
        *sha_checks("lc_command", "request_hash", "manifest_hash", "previous_manifest_hash"),
    )
    operations = Table(
        "position_lifecycle_operations", metadata,
        uid("id", primary_key=True), uid("lifecycle_id"), uid("portfolio_id"),
        col("operation_seq", BigInteger), uid("previous_operation_id", nullable=True),
        uid("business_command_id"), col("command_step_no", Integer),
        col("operation_kind", String(32)), col("outcome", String(24)), col("reason_code", String(64)),
        col("actor_type", String(16)), col("actor_ref", String(128)),
        col("effective_at", DateTime(timezone=True), nullable=True),
        col("effective_trade_date", Date, nullable=True), stamp("recorded_at"),
        uid("policy_version_id", "lifecycle_policy_versions.id"), sha("policy_content_hash"),
        col("formula_version", String(64)), col("snapshot_schema_version", Integer),
        col("before_snapshot", JSONB, nullable=True), col("after_snapshot", JSONB),
        col("state_version_before", Integer, nullable=True), col("state_version_after", Integer, nullable=True),
        col("source_count", Integer), col("change_count", Integer),
        sha("sources_sha256"), sha("changes_sha256"), sha("initial_input_hash", nullable=True),
        sha("previous_hash", nullable=True), sha("before_hash"), sha("after_hash"),
        col("canonical_payload", LargeBinary), sha("operation_hash"),
        UniqueConstraint("lifecycle_id", "operation_seq", name="uq_lc_operation_sequence"),
        UniqueConstraint("previous_operation_id", name="uq_lc_operation_previous"),
        UniqueConstraint("business_command_id", "command_step_no", name="uq_lc_operation_command_step"),
        UniqueConstraint("id", "business_command_id", name="uq_lc_operation_command"),
        UniqueConstraint("id", "lifecycle_id", name="uq_lc_operation_lifecycle"),
        ForeignKeyConstraint(["lifecycle_id", "portfolio_id"],
                             ["position_lifecycle_states.id", "position_lifecycle_states.portfolio_id"],
                             ondelete="RESTRICT", name="fk_lc_operation_portfolio"),
        ForeignKeyConstraint(["business_command_id", "portfolio_id"],
                             ["lifecycle_business_commands.id", "lifecycle_business_commands.portfolio_id"],
                             ondelete="RESTRICT", name="fk_lc_operation_command_portfolio"),
        ForeignKeyConstraint(["previous_operation_id", "lifecycle_id"],
                             ["position_lifecycle_operations.id", "position_lifecycle_operations.lifecycle_id"],
                             ondelete="RESTRICT", name="fk_lc_operation_previous_lifecycle"),
        CheckConstraint("operation_seq > 0 AND command_step_no > 0 AND snapshot_schema_version = 1 "
                        "AND source_count >= 0 AND change_count >= 0 AND length(trim(reason_code)) > 0",
                        name="ck_lc_operation_identity"),
        CheckConstraint("outcome IN ('APPLIED','NO_STATE_CHANGE','BLOCKED')", name="ck_lc_operation_outcome"),
        CheckConstraint("operation_kind IN ('INITIALIZE','FILL_APPLIED','DAILY_EVALUATED','BATCH_MATERIALIZED',"
                        "'INTENT_OR_ORDER_CHANGED','SOURCE_REFERENCE_CLEARED','RECONCILIATION','MIGRATED_BASELINE')",
                        name="ck_lc_operation_kind"),
        CheckConstraint("(operation_seq = 1 AND previous_operation_id IS NULL AND previous_hash IS NULL "
                        "AND operation_kind IN ('INITIALIZE','MIGRATED_BASELINE')) OR "
                        "(operation_seq > 1 AND previous_operation_id IS NOT NULL AND previous_hash IS NOT NULL "
                        "AND operation_kind NOT IN ('INITIALIZE','MIGRATED_BASELINE'))",
                        name="ck_lc_operation_predecessor"),
        CheckConstraint("(operation_kind = 'MIGRATED_BASELINE' AND before_snapshot IS NULL "
                        "AND state_version_before IS NULL AND initial_input_hash IS NULL) OR "
                        "(operation_kind = 'INITIALIZE' AND before_snapshot IS NOT NULL "
                        "AND state_version_before IS NULL AND state_version_after = 1 AND initial_input_hash IS NOT NULL) OR "
                        "(operation_kind NOT IN ('INITIALIZE','MIGRATED_BASELINE') AND before_snapshot IS NOT NULL "
                        "AND state_version_before IS NOT NULL AND state_version_before > 0 AND initial_input_hash IS NULL)",
                        name="ck_lc_operation_shape"),
        CheckConstraint("state_version_after IS NOT NULL AND state_version_after > 0 "
                        "AND jsonb_typeof(after_snapshot) = 'object' "
                        "AND after_snapshot ?& ARRAY['managed_state','account_observations','evidence'] "
                        "AND after_snapshot - ARRAY['managed_state','account_observations','evidence'] = '{}'::jsonb "
                        "AND jsonb_typeof(after_snapshot->'managed_state') = 'object' "
                        "AND jsonb_typeof(after_snapshot->'account_observations') = 'object' "
                        "AND jsonb_typeof(after_snapshot->'evidence') = 'object' "
                        "AND after_snapshot#>>'{managed_state,lifecycle,state_version}' IS NOT NULL "
                        "AND (after_snapshot#>>'{managed_state,lifecycle,state_version}')::integer = state_version_after",
                        name="ck_lc_operation_snapshot"),
        CheckConstraint("before_snapshot IS NULL OR (jsonb_typeof(before_snapshot) = 'object' "
                        "AND before_snapshot ?& ARRAY['managed_state','account_observations','evidence'] "
                        "AND before_snapshot - ARRAY['managed_state','account_observations','evidence'] = '{}'::jsonb "
                        "AND jsonb_typeof(before_snapshot->'managed_state') = 'object' "
                        "AND jsonb_typeof(before_snapshot->'account_observations') = 'object' "
                        "AND jsonb_typeof(before_snapshot->'evidence') = 'object')",
                        name="ck_lc_operation_before_snapshot"),
        CheckConstraint("operation_kind <> 'INITIALIZE' OR "
                        "(before_snapshot#>'{managed_state,lifecycle}' IS NOT NULL "
                        "AND before_snapshot#>'{managed_state,lifecycle}' = 'null'::jsonb)",
                        name="ck_lc_operation_initial_before"),
        CheckConstraint("operation_kind IN ('INITIALIZE','MIGRATED_BASELINE') OR "
                        "(before_snapshot#>>'{managed_state,lifecycle,state_version}' IS NOT NULL "
                        "AND (before_snapshot#>>'{managed_state,lifecycle,state_version}')::integer = state_version_before)",
                        name="ck_lc_operation_before_version"),
        CheckConstraint("outcome <> 'NO_STATE_CHANGE' OR "
                        "(before_snapshot IS NOT NULL AND before_snapshot->'managed_state' IS NOT NULL "
                        "AND before_snapshot->'managed_state' = after_snapshot->'managed_state')",
                        name="ck_lc_operation_unchanged"),
        *sha_checks("lc_op", "policy_content_hash", "sources_sha256", "changes_sha256", "initial_input_hash",
                    "previous_hash", "before_hash", "after_hash", "operation_hash"),
    )
    numeric_names = ("signal_planned_shares", "total_assets", "risk_per_trade_pct",
                     "config_reward_multiple", "template_reward_multiple", "arc_neckline_price")
    inputs = Table(
        "suggested_order_initial_inputs", metadata,
        uid("order_id", primary_key=True), uid("business_command_id"), col("source_kind", String(32)),
        uid("portfolio_id"), col("market", String(8)), col("symbol", String(32)),
        uid("strategy_version_id", "quant_strategy_versions.id", nullable=True),
        uid("policy_version_id", "lifecycle_policy_versions.id", nullable=True), sha("policy_content_hash", nullable=True),
        col("source_signal_id_snapshot", BigInteger, nullable=True), uid("source_task_id_snapshot", nullable=True),
        uid("source_lifecycle_id", "position_lifecycle_states.id", nullable=True),
        col("template_id", String(64), nullable=True), col("management_policy_snapshot", JSONB, nullable=True),
        col("order_quantity", Numeric()), col("initial_stop_price", Numeric(), nullable=True),
        col("initial_exposure", Numeric(), nullable=True),
        *[item for name in numeric_names for item in (
            col(name, Numeric(), nullable=True), col(name + "_presence", String(8)))],
        *[col("legacy_trailing_" + name, Numeric(), nullable=True) for name in ("b", "a", "d")],
        col("legacy_trailing_presence", String(8)), col("trailing_atr_multiple", Numeric(), nullable=True),
        col("ma5_confirmed_cross_raw", JSONB, nullable=True), col("ma5_confirmed_cross_presence", String(8)),
        col("confirmation_window_trading_days", Integer, nullable=True),
        col("confirmation_window_trading_days_presence", String(8)),
        col("policy_config_snapshot", JSONB), col("template_params_snapshot", JSONB),
        col("required_fields_snapshot", JSONB), col("input_schema_version", Integer),
        col("formula_version", String(64)), col("canonical_payload", LargeBinary), sha("input_hash"), stamp("captured_at"),
        ForeignKeyConstraint(["order_id", "portfolio_id"], ["suggested_orders.id", "suggested_orders.portfolio_id"],
                             ondelete="RESTRICT", name="fk_lc_order_input_order"),
        ForeignKeyConstraint(["business_command_id", "portfolio_id"],
                             ["lifecycle_business_commands.id", "lifecycle_business_commands.portfolio_id"],
                             ondelete="RESTRICT", name="fk_lc_order_input_command"),
        CheckConstraint("order_quantity > 0 AND order_quantity::text NOT IN ('NaN','Infinity','-Infinity') "
                        "AND input_schema_version = 1 AND jsonb_typeof(policy_config_snapshot) = 'object' "
                        "AND jsonb_typeof(template_params_snapshot) = 'object' "
                        "AND jsonb_typeof(required_fields_snapshot) = 'array'", name="ck_lc_order_input_values"),
        CheckConstraint("(source_kind = 'SIGNAL_ENTRY' AND strategy_version_id IS NOT NULL "
                        "AND policy_version_id IS NOT NULL AND policy_content_hash IS NOT NULL "
                        "AND template_id IS NOT NULL AND management_policy_snapshot IS NOT NULL "
                        "AND jsonb_typeof(management_policy_snapshot) = 'object' "
                        "AND source_signal_id_snapshot IS NOT NULL AND source_task_id_snapshot IS NOT NULL "
                        "AND source_lifecycle_id IS NULL AND initial_stop_price IS NOT NULL AND initial_stop_price > 0 "
                        "AND initial_exposure IS NOT NULL AND initial_exposure > 0 AND initial_exposure <= 1) OR "
                        "(source_kind = 'LIFECYCLE_INTENT' AND source_lifecycle_id IS NOT NULL "
                        "AND strategy_version_id IS NOT NULL AND policy_version_id IS NOT NULL "
                        "AND policy_content_hash IS NOT NULL AND template_id IS NOT NULL "
                        "AND management_policy_snapshot IS NOT NULL "
                        "AND jsonb_typeof(management_policy_snapshot) = 'object') OR "
                        "(source_kind = 'MANUAL_PROTECTION' AND strategy_version_id IS NULL "
                        "AND policy_version_id IS NULL AND policy_content_hash IS NULL AND template_id IS NULL "
                        "AND management_policy_snapshot IS NULL AND source_lifecycle_id IS NULL "
                        "AND source_signal_id_snapshot IS NULL AND source_task_id_snapshot IS NULL "
                        "AND initial_exposure IS NULL)", name="ck_lc_order_input_kind"),
        *[CheckConstraint(f"({name}_presence IN ('ABSENT','NULL') AND {name} IS NULL) OR "
                          f"({name}_presence = 'VALUE' AND {name} IS NOT NULL)", name=f"ck_lc_input_{name}")
          for name in (*numeric_names, "confirmation_window_trading_days")],
        CheckConstraint("ma5_confirmed_cross_presence IN ('ABSENT','NULL','VALUE') "
                        "AND ((ma5_confirmed_cross_presence IN ('ABSENT','NULL') AND ma5_confirmed_cross_raw IS NULL) "
                        "OR (ma5_confirmed_cross_presence = 'VALUE' AND ma5_confirmed_cross_raw IS NOT NULL "
                        "AND ma5_confirmed_cross_raw IN ('true'::jsonb,'false'::jsonb,'0'::jsonb,'1'::jsonb)))",
                        name="ck_lc_input_ma5"),
        CheckConstraint("(legacy_trailing_presence IN ('ABSENT','NULL') AND legacy_trailing_b IS NULL "
                        "AND legacy_trailing_a IS NULL AND legacy_trailing_d IS NULL) OR "
                        "(legacy_trailing_presence = 'VALUE' AND legacy_trailing_b IS NOT NULL "
                        "AND legacy_trailing_a IS NOT NULL AND legacy_trailing_d IS NOT NULL)",
                        name="ck_lc_input_trailing"),
        *sha_checks("lc_order_input", "policy_content_hash", "input_hash"),
    )
    for column in inputs.columns:
        if isinstance(column.type, Numeric):
            inputs.append_constraint(CheckConstraint(
                f"{column.name} IS NULL OR {column.name}::text NOT IN ('NaN','Infinity','-Infinity')",
                name=f"ck_lc_input_finite_{column.name}"))
    source_identities = {
        "FILL": ("fill_event_id", "order_fill_events.id"),
        "REPORT": ("report_id", "account_fill_reports.id"),
        "RESOLUTION": ("resolution_id", "account_fill_report_resolutions.id"),
        "POSTING": ("posting_id", "account_fill_postings.id"),
        "LEDGER_MOVEMENT": ("ledger_movement_id", "account_ledger_movements.id"),
        "DAILY_REVISION": ("daily_revision_id", "position_daily_fact_revisions.id"),
        "INTENT_REVISION": ("intent_revision_id", "position_intent_revisions.id"),
        "ORDER": ("order_id", "suggested_orders.id"),
        "ALLOCATION_BATCH": ("allocation_batch_id", "quant_allocation_batches.id"),
        "ORDER_INITIAL_INPUT": ("order_initial_input_id", "suggested_order_initial_inputs.order_id"),
        "RISK_EVENT": ("risk_event_id", "quant_portfolio_risk_events.id"),
        "ADMISSION_EVENT": ("admission_event_id", "strategy_admission_events.id"),
        "RULE_CERTIFICATE": ("rule_certificate_id", "quant_instrument_rule_certificates.id"),
        "TASK": ("task_id_snapshot", None), "SIGNAL": ("signal_id_snapshot", None),
        "EXTERNAL": ("source_ref_snapshot", None),
    }
    identity_names = [pair[0] for pair in source_identities.values()]
    source_kind_sql = " OR ".join(
        f"(source_type = '{kind}' AND {name} IS NOT NULL)" for kind, (name, _) in source_identities.items())
    source_kind_sql += " OR (source_type = 'UNBOUND_ORDER_RESULT' AND order_id IS NOT NULL)"
    sources = Table(
        "quant_execution_operation_sources", metadata,
        uid("id", primary_key=True), uid("business_command_id", "lifecycle_business_commands.id"),
        uid("operation_id", nullable=True), col("source_role", String(32)), col("source_ordinal", Integer),
        col("source_type", String(32)), col("scope", String(16)),
        *[col(name, BigInteger if name == "signal_id_snapshot" else Text, nullable=True)
          if name in ("signal_id_snapshot", "source_ref_snapshot") else uid(name, target, nullable=True)
          for name, target in source_identities.values()],
        sha("source_content_hash"), col("source_revision", Integer, nullable=True),
        col("source_snapshot", JSONB, nullable=True), col("result_outcome", String(24), nullable=True),
        col("result_reason_code", String(64), nullable=True), col("result_schema_version", Integer, nullable=True),
        ForeignKeyConstraint(["operation_id", "business_command_id"],
                             ["position_lifecycle_operations.id", "position_lifecycle_operations.business_command_id"],
                             ondelete="RESTRICT", deferrable=True, initially="DEFERRED", name="fk_lc_source_operation"),
        UniqueConstraint("operation_id", "source_role", "source_ordinal", name="uq_lc_source_ordinal"),
        CheckConstraint("source_ordinal > 0 AND length(trim(source_role)) > 0", name="ck_lc_source_ordinal"),
        CheckConstraint("(scope = 'COMMAND' AND operation_id IS NULL) OR "
                        "(scope = 'LIFECYCLE' AND operation_id IS NOT NULL)", name="ck_lc_source_scope"),
        CheckConstraint(f"num_nonnulls({','.join(identity_names)}) = 1 AND ({source_kind_sql})",
                        name="ck_lc_source_identity"),
        CheckConstraint("source_type NOT IN ('ORDER','UNBOUND_ORDER_RESULT') OR "
                        "(source_revision IS NOT NULL AND source_revision > 0 AND source_snapshot IS NOT NULL "
                        "AND jsonb_typeof(source_snapshot) = 'object')", name="ck_lc_source_order_image"),
        CheckConstraint("(source_type = 'UNBOUND_ORDER_RESULT' AND scope = 'COMMAND' "
                        "AND result_outcome IS NOT NULL AND result_outcome IN ('APPLIED','NO_STATE_CHANGE','BLOCKED') "
                        "AND result_reason_code IS NOT NULL AND length(trim(result_reason_code)) > 0 "
                        "AND result_schema_version IS NOT NULL AND result_schema_version = 1) OR (source_type <> 'UNBOUND_ORDER_RESULT' "
                        "AND result_outcome IS NULL AND result_reason_code IS NULL AND result_schema_version IS NULL)",
                        name="ck_lc_source_result"),
        *sha_checks("lc_source", "source_content_hash"),
    )
    Index("uq_lc_command_source_ordinal", sources.c.business_command_id, sources.c.source_role,
          sources.c.source_ordinal, unique=True, postgresql_where=sources.c.operation_id.is_(None))
    Index("uq_lc_unbound_order_result", sources.c.business_command_id, sources.c.order_id,
          unique=True, postgresql_where=sources.c.source_type == "UNBOUND_ORDER_RESULT")
    Index("ix_lc_source_fill", sources.c.fill_event_id, postgresql_where=sources.c.fill_event_id.is_not(None))
    Index("ix_lc_source_order", sources.c.order_id, postgresql_where=sources.c.order_id.is_not(None))
    changes = Table(
        "quant_execution_operation_changes", metadata,
        uid("id", primary_key=True), uid("business_command_id", "lifecycle_business_commands.id"),
        uid("operation_id", nullable=True), col("change_seq", BigInteger), col("scope", String(16)),
        col("entity_type", String(32)), uid("row_id"), col("change_kind", String(8)),
        col("binding_side", String(8)), col("before_row", JSONB, nullable=True),
        col("after_row", JSONB, nullable=True), stamp("captured_at"),
        ForeignKeyConstraint(["operation_id", "business_command_id"],
                             ["position_lifecycle_operations.id", "position_lifecycle_operations.business_command_id"],
                             ondelete="RESTRICT", deferrable=True, initially="DEFERRED", name="fk_lc_change_operation"),
        UniqueConstraint("business_command_id", "change_seq", name="uq_lc_change_sequence"),
        CheckConstraint("change_seq > 0 AND binding_side IN ('NONE','OLD','NEW','BOTH')", name="ck_lc_change_sequence"),
        CheckConstraint("entity_type IN ('LIFECYCLE','TRAILING_STOP','EXPECTATION','DAILY_FACT','INTENT','ORDER','POSITION')",
                        name="ck_lc_change_entity"),
        CheckConstraint("(scope = 'COMMAND' AND operation_id IS NULL AND entity_type = 'ORDER') OR "
                        "(scope = 'LIFECYCLE' AND operation_id IS NOT NULL)", name="ck_lc_change_scope"),
        CheckConstraint("(change_kind = 'INSERT' AND before_row IS NULL AND after_row IS NOT NULL) OR "
                        "(change_kind = 'DELETE' AND before_row IS NOT NULL AND after_row IS NULL) OR "
                        "(change_kind = 'UPDATE' AND before_row IS NOT NULL AND after_row IS NOT NULL)",
                        name="ck_lc_change_shape"),
        CheckConstraint("(before_row IS NULL OR jsonb_typeof(before_row) = 'object') "
                        "AND (after_row IS NULL OR jsonb_typeof(after_row) = 'object')",
                        name="ck_lc_change_row_image"),
    )
    Index("ix_lc_changes_operation", changes.c.operation_id, changes.c.change_seq)
    Index("ix_lc_changes_entity", changes.c.entity_type, changes.c.row_id,
          changes.c.business_command_id, changes.c.change_seq)
    initial = Table(
        "position_lifecycle_initial_inputs", metadata,
        uid("operation_id", primary_key=True), uid("business_command_id", "lifecycle_business_commands.id"),
        uid("lifecycle_id", "position_lifecycle_states.id"), uid("initial_fill_id", "order_fill_events.id", deferred=True),
        uid("order_id", "suggested_orders.id"), uid("order_initial_input_id", "suggested_order_initial_inputs.order_id"),
        sha("order_input_hash"), col("input_schema_version", Integer), col("formula_version", String(64)),
        col("canonical_payload", LargeBinary), sha("input_hash"), stamp("captured_at"),
        UniqueConstraint("lifecycle_id", name="uq_lc_initial_lifecycle"),
        UniqueConstraint("initial_fill_id", name="uq_lc_initial_fill"),
        ForeignKeyConstraint(["operation_id", "business_command_id"],
                             ["position_lifecycle_operations.id", "position_lifecycle_operations.business_command_id"],
                             ondelete="RESTRICT", deferrable=True, initially="DEFERRED", name="fk_lc_initial_operation"),
        ForeignKeyConstraint(["operation_id", "lifecycle_id"],
                             ["position_lifecycle_operations.id", "position_lifecycle_operations.lifecycle_id"],
                             ondelete="RESTRICT", deferrable=True, initially="DEFERRED", name="fk_lc_initial_lifecycle"),
        CheckConstraint("order_id = order_initial_input_id AND input_schema_version = 1", name="ck_lc_initial_identity"),
        *sha_checks("lc_initial", "order_input_hash", "input_hash"),
    )
    return commands, operations, sources, changes, inputs, initial


HISTORY_TABLES = (
    "lifecycle_business_commands", "position_lifecycle_operations",
    "quant_execution_operation_sources", "quant_execution_operation_changes",
    "suggested_order_initial_inputs", "position_lifecycle_initial_inputs",
)
LINKED_TABLES = ("position_daily_fact_revisions", "position_intent_revisions", "fill_lifecycle_version_steps")


def _normalize(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _canonical(value):
    return json.dumps(_normalize(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _rows(table, where="", params=None):
    # Decimal parsing must precede normalization: the JSON driver's float
    # decoding would already have lost NUMERIC precision.
    rows = op.get_bind().execute(text(f"SELECT row_to_json(r)::text FROM {table} r {where} ORDER BY id"), params or {})
    result = []
    for raw in rows.scalars():
        decoded = _normalize(json.loads(raw, parse_float=Decimal))
        # This exact original is authoritative for physical types and Decimal
        # precision. The decoded view is for interpretation only: JSONB numbers
        # are not to be reconstructed from its normalized numeric strings.
        decoded["__raw_row_json__"] = raw
        result.append(decoded)
    return result


def _insert(table, values):
    op.get_bind().execute(table.insert().values(**values))


def _baseline(tables):
    commands, operations, sources, _, _, _ = tables
    lifecycles = _rows("position_lifecycle_states")
    unbound = _rows("suggested_orders", "WHERE lifecycle_id IS NULL")
    portfolios = sorted({row["portfolio_id"] for row in (*lifecycles, *unbound)})
    for portfolio in portfolios:
        members = [row for row in lifecycles if row["portfolio_id"] == portfolio]
        unbound_members = [row for row in unbound if row["portfolio_id"] == portfolio]
        command_id = uuid.uuid4()
        steps = [{"operation_id": str(uuid.uuid4()), "lifecycle_id": row["id"],
                  "command_step_no": index + 1, "operation_kind": "MIGRATED_BASELINE"}
                 for index, row in enumerate(members)]
        request = {"schema_version": 1, "migration": "0051", "portfolio_id": portfolio}
        expected_unbound = [{"order_id": row["id"], "original_revision": row["revision"],
                             "allowed_results": ["BLOCKED"]} for row in unbound_members]
        _insert(commands, {
            "id": command_id, "portfolio_id": uuid.UUID(portfolio), "request_key": "migration:0051:baseline",
            "command_seq": 1, "command_kind": "MIGRATED_BASELINE", "request_schema_version": 1,
            "canonical_request": _canonical(request), "request_hash": _hash(request), "selector_version": "migration:0051",
            "expected_steps": steps, "expected_unbound_orders": expected_unbound,
            "manifest_hash": _hash({"steps": steps, "unbound_orders": expected_unbound}),
            "expected_step_count": len(steps), "expected_unbound_order_count": len(expected_unbound),
            "actor_type": "MIGRATION", "actor_ref": "0051"})
        for index, order in enumerate(unbound_members):
            result = {"origin": "UNKNOWN_PRIOR", "before_row": order, "after_row": order,
                      "revision_before": order["revision"], "revision_after": order["revision"],
                      "missing_sources": ["ORDER_CREATION_INPUTS", "PRIOR_OPERATIONS"]}
            _insert(sources, {
                "id": uuid.uuid4(), "business_command_id": command_id, "operation_id": None,
                "source_role": "MIGRATION_ORDER_RESULT", "source_ordinal": index + 1,
                "source_type": "UNBOUND_ORDER_RESULT", "scope": "COMMAND", "order_id": uuid.UUID(order["id"]),
                "source_revision": order["revision"], "source_snapshot": result,
                "source_content_hash": _hash(result), "result_outcome": "BLOCKED",
                "result_reason_code": "UNKNOWN_PRIOR", "result_schema_version": 1})
        for row, step in zip(members, steps):
            identity = uuid.UUID(row["id"])
            children = {table: _rows(table, "WHERE lifecycle_id = :id", {"id": identity}) for table in (
                "position_trailing_stops", "position_expectations", "position_intents", "suggested_orders",
                "position_daily_facts")}
            def managed(physical):
                return {key: value for key, value in physical.items() if key not in (
                    "created_at", "updated_at", "source_signal_id", "strategy_version_id", "lifecycle_policy_version_id", "__raw_row_json__")}
            state = {
                "lifecycle": managed(row),
                "trailing_stops": [managed(item) for item in children["position_trailing_stops"]],
                "expectations": [managed(item) for item in children["position_expectations"]],
                "active_intents": [managed(item) for item in children["position_intents"]
                                   if item["status"] in ("ACTIVE", "EXECUTING", "RECONCILIATION_REQUIRED")],
                "active_orders": [managed(item) for item in children["suggested_orders"]
                                  if item["status"] in ("PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED")],
            }
            positions = _rows("portfolio_positions", "WHERE portfolio_id=:portfolio AND market=:market AND symbol=:symbol",
                              {"portfolio": uuid.UUID(portfolio), "market": row["market"], "symbol": row["symbol"]})
            snapshot = {"managed_state": state,
                        "account_observations": {"origin": "ACCOUNT_OBSERVED", "positions": positions},
                        "evidence": {"origin": "UNKNOWN_PRIOR", "missing_sources": ["PRIOR_OPERATIONS", "ORDER_CREATION_INPUTS"],
                                     "migration_rows": {"lifecycle": row, **children}}}
            policy = _rows("lifecycle_policy_versions", "WHERE id=:id", {"id": uuid.UUID(row["lifecycle_policy_version_id"])})[0]
            source_values = []
            for index, order in enumerate(children["suggested_orders"]):
                value = {"id": uuid.uuid4(), "business_command_id": command_id, "operation_id": uuid.UUID(step["operation_id"]),
                             "source_role": "MIGRATION_ORDER", "source_ordinal": index + 1, "source_type": "ORDER", "scope": "LIFECYCLE",
                             "order_id": uuid.UUID(order["id"]), "source_revision": order["revision"], "source_snapshot": order,
                             "source_content_hash": _hash(order)}
                _insert(sources, value)
                source_values.append({column.name: value.get(column.name) for column in sources.columns})
            values = {
                "id": uuid.UUID(step["operation_id"]), "lifecycle_id": identity, "portfolio_id": uuid.UUID(portfolio),
                "operation_seq": 1, "business_command_id": command_id, "command_step_no": step["command_step_no"],
                "operation_kind": "MIGRATED_BASELINE", "outcome": "BLOCKED", "reason_code": "UNKNOWN_PRIOR",
                "actor_type": "MIGRATION", "actor_ref": "0051", "policy_version_id": uuid.UUID(policy["id"]),
                "policy_content_hash": policy["content_hash"], "formula_version": "unknown:prior", "snapshot_schema_version": 1,
                "before_snapshot": None, "after_snapshot": snapshot, "state_version_before": None,
                "state_version_after": row["state_version"], "source_count": len(source_values), "change_count": 0,
                "sources_sha256": _hash(source_values), "changes_sha256": _hash([]), "before_hash": _hash(None), "after_hash": _hash(snapshot)}
            payload = {column.name: values.get(column.name) for column in operations.columns
                       if column.name not in ("recorded_at", "canonical_payload", "operation_hash")}
            values.update(canonical_payload=_canonical(payload), operation_hash=_hash(payload))
            _insert(operations, values)


def upgrade():
    # Lock producers before capturing the baseline; order matches normal writers.
    op.execute("LOCK TABLE portfolios IN SHARE ROW EXCLUSIVE MODE")
    op.execute("LOCK TABLE position_lifecycle_states, suggested_orders, position_intents, "
               "position_trailing_stops, position_expectations, position_daily_facts, portfolio_positions "
               "IN SHARE ROW EXCLUSIVE MODE")
    op.create_unique_constraint("uq_position_lifecycle_portfolio_identity", "position_lifecycle_states", ["id", "portfolio_id"])
    metadata = MetaData()
    metadata.reflect(bind=op.get_bind())
    tables = _define_operation_tables(metadata)
    for table in (tables[0], tables[1], tables[4], tables[2], tables[3], tables[5]):
        op.execute(CreateTable(table))
        for index in table.indexes:
            op.execute(CreateIndex(index))
    for table in LINKED_TABLES:
        op.add_column(table, Column("operation_id", UUID(as_uuid=True), nullable=True))
        op.add_column(table, Column("business_command_id", UUID(as_uuid=True), nullable=True))
        op.add_column(table, Column("operation_link_origin", String(24), nullable=False, server_default="LEGACY_UNLINKED"))
        op.create_foreign_key(f"fk_{table}_operation", table, "position_lifecycle_operations",
                              ["operation_id", "business_command_id"], ["id", "business_command_id"],
                              ondelete="RESTRICT", deferrable=True, initially="DEFERRED")
        op.create_check_constraint(f"ck_{table}_operation_link", table,
            "(operation_link_origin = 'LEGACY_UNLINKED' AND operation_id IS NULL AND business_command_id IS NULL) OR "
            "(operation_link_origin = 'LINKED' AND operation_id IS NOT NULL AND business_command_id IS NOT NULL)")
    _baseline(tables)
    op.execute("""CREATE FUNCTION reject_lifecycle_history_mutation() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ BEGIN
            RAISE EXCEPTION 'lifecycle operation history is immutable';
        END; $$""")
    op.execute("""CREATE FUNCTION lifecycle_history_capture_not_ready() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ BEGIN
            RAISE EXCEPTION 'LIVE lifecycle capture requires completed command coordinator';
        END; $$""")
    for table in HISTORY_TABLES:
        op.execute(f"CREATE TRIGGER lc_history_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_lifecycle_history_mutation()")
        op.execute(f"CREATE TRIGGER lc_history_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION reject_lifecycle_history_mutation()")
        op.execute(f"CREATE TRIGGER lc_history_capture_closed BEFORE INSERT ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION lifecycle_history_capture_not_ready()")


def _export_history():
    records = []
    counts = {}
    for table in HISTORY_TABLES:
        # BYTEA in row_to_json is represented as an exact hexadecimal string.
        primary = "order_id" if table == "suggested_order_initial_inputs" else (
            "operation_id" if table == "position_lifecycle_initial_inputs" else "id")
        rows = list(op.get_bind().execute(text(
            f"SELECT row_to_json(r)::text FROM {table} r ORDER BY {primary}")).scalars())
        counts[table] = len(rows)
        # Keep the exact JSON document as text instead of changing embedded JSON
        # numeric types via recursive Decimal normalization.
        records.extend(_canonical({"table": table, "row_json": row}) for row in rows)
    if not records:
        return
    raw = os.environ.get("LIVEPROFIT_LIFECYCLE_HISTORY_EXPORT_PATH", "")
    path = Path(raw)
    if not raw or not path.is_absolute():
        raise RuntimeError("absolute lifecycle history export path required for downgrade")
    body = b"\n".join(records) + b"\n"
    header = _canonical({"format": "liveprofit_lifecycle_history_0051_v1", "counts": counts,
                         "sha256": hashlib.sha256(body).hexdigest()}) + b"\n"
    payload = header + body
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    if path.read_bytes() != payload:
        raise RuntimeError("lifecycle history export readback mismatch")


def downgrade():
    op.execute("LOCK TABLE portfolios IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE position_lifecycle_states, suggested_orders IN ACCESS EXCLUSIVE MODE")
    for table in HISTORY_TABLES:
        op.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")
    _export_history()
    # 3ar-2 onward must first export the linked legacy streams too. At this
    # baseline-only stage there can be no LINKED facts; fail rather than sever one.
    for table in LINKED_TABLES:
        if op.get_bind().scalar(text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE operation_link_origin <> 'LEGACY_UNLINKED')")):
            raise RuntimeError("linked lifecycle facts require later coordinated downgrade")
    for table in LINKED_TABLES:
        op.drop_constraint(f"fk_{table}_operation", table, type_="foreignkey")
        op.drop_constraint(f"ck_{table}_operation_link", table, type_="check")
        for column in ("operation_link_origin", "business_command_id", "operation_id"):
            op.drop_column(table, column)
    for table in (HISTORY_TABLES[5], HISTORY_TABLES[2], HISTORY_TABLES[3],
                  HISTORY_TABLES[4], HISTORY_TABLES[1], HISTORY_TABLES[0]):
        op.drop_table(table)
    op.execute("DROP FUNCTION lifecycle_history_capture_not_ready()")
    op.execute("DROP FUNCTION reject_lifecycle_history_mutation()")
    op.drop_constraint("uq_position_lifecycle_portfolio_identity", "position_lifecycle_states", type_="unique")
