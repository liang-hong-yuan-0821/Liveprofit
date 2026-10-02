"""Append-only local operation history; source certification is independent.

0051 freezes a copy of the schema factory so historical migrations never import
the evolving application models. 0052/0053 add separately gated unbound/bound
order status commands; creation-input writes and production wiring remain closed.
"""
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
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.shared.db import Base


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
    Index("ix_lc_unbound_command_transaction", commands.c.captured_transaction_id,
          postgresql_where=commands.c.command_kind == "ORDER_STATUS_CHANGED")
    Index("ix_lc_portfolio_command_transaction", commands.c.captured_transaction_id,
          postgresql_where=commands.c.command_kind == "PORTFOLIO_DRAWDOWN")
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


_tables = _define_operation_tables(Base.metadata)


class LifecycleBusinessCommand(Base):
    __table__ = _tables[0]


class PositionLifecycleOperation(Base):
    __table__ = _tables[1]


class QuantExecutionOperationSource(Base):
    __table__ = _tables[2]


class QuantExecutionOperationChange(Base):
    __table__ = _tables[3]


class SuggestedOrderInitialInput(Base):
    __table__ = _tables[4]


class PositionLifecycleInitialInput(Base):
    __table__ = _tables[5]
