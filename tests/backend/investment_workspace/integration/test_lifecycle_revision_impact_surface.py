# test-catalog-begin
# {
#   "purpose": "投资工作区 / lifecycle_revision_impact_surface（持仓生命周期、版本修订）：3an local revision impact inventory in the isolated workspace database.",
#   "keywords": [
#     "投资工作区",
#     "每日",
#     "指数",
#     "交易意图",
#     "持仓生命周期",
#     "收益",
#     "版本修订",
#     "lifecycle_revision_impact_surface",
#     "daily",
#     "index",
#     "intent",
#     "lifecycle",
#     "returns",
#     "revision"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/fill_posting_stage.py",
#     "backend/modules/investment_workspace/infrastructure/fill_report_models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/lifecycle_revision_impact_surface.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""3an local revision impact inventory in the isolated workspace database."""

from tests.backend.investment_workspace.support.lifecycle_revision_impact_surface import (
    _historical_corrected_anchor,
    _daily_fact,
)

import base64
import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from backend.modules.investment_workspace.application.fill_posting_stage import AccountFillPostingStage
from backend.modules.investment_workspace.infrastructure.fill_report_models import AccountFillPostingRow
from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.application.lifecycle_revision_impact_surface import (
    load_local_lifecycle_revision_impact_surface,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    FillLifecycleVersionStep, LifecyclePolicyVersion, PositionDailyFact,
    PositionDailyFactRevision, PositionDailyFactInputProposal, PositionIntent,
    PositionIntentRevision, PositionLifecycleState, SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from tests.backend.investment_workspace.support.fill_posting_stage import EXECUTED, SECRET, VOID_SECRET, _post_and_review_correction, _seed
from tests.backend.investment_workspace.support.lifecycle_persisted_diagnosis import _reviewed_fill






def test_historical_signed_correction_has_empty_local_surface_then_indexes_real_rows(
        env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, initial_id = _historical_corrected_anchor(
        factory, monkeypatch)
    with factory() as session:
        empty = load_local_lifecycle_revision_impact_surface(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert empty.status == "LOCAL_IMPACT"
        assert empty.impact_date == EXECUTED.date()
        assert (empty.daily_state, empty.intent_state, empty.old_step_state) == (
            "LOCAL_EMPTY", "LOCAL_EMPTY", "LOCAL_EMPTY")
        assert empty.daily_facts == empty.intents == empty.old_steps == ()
        assert empty.original_root_fill_ids == (initial_id,)
        assert "GLOBAL_TRADING_CALENDAR_UNCERTIFIED" in empty.issues
        assert "HISTORICAL_SOURCE_AVAILABILITY_UNCERTIFIED" in empty.issues
        assert not session.new and not session.dirty and not session.deleted

    with factory() as session:
        earlier = _daily_fact(session, lifecycle_id, date(2026, 9, 27))
        same_day = _daily_fact(session, lifecycle_id, date(2026, 9, 28))
        same_day.final_target_shares = Decimal(4)
        session.flush()  # 0044 creates revision 2; the index must name latest.
        later = _daily_fact(session, lifecycle_id, date(2026, 9, 29))
        prior_intent = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle_id,
            trade_date=date(2026, 9, 27), target_shares=Decimal(5),
            reason_code="PRIOR_INTENT", state_version=1,
            status="ACTIVE", revision=1)
        later_intent = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle_id,
            trade_date=date(2026, 9, 29), target_shares=Decimal(0),
            reason_code="LATER_INTENT", state_version=1,
            status="ACTIVE", revision=1)
        session.add(prior_intent)
        session.flush()
        prior_intent.status = "COMPLETED"
        prior_intent.revision = 2
        session.flush()
        session.add(later_intent)
        session.commit()
        earlier_id, same_day_id, later_id = earlier.id, same_day.id, later.id
        intent_ids = {prior_intent.id, later_intent.id}
    with factory() as session:
        indexed = load_local_lifecycle_revision_impact_surface(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert indexed.status == "LOCAL_IMPACT"
        assert (indexed.daily_state, indexed.intent_state, indexed.old_step_state) == (
            "PRESENT", "PRESENT", "LOCAL_EMPTY")
        assert tuple(row.fact_id for row in indexed.daily_facts) == (
            same_day_id, later_id)
        assert earlier_id not in {row.fact_id for row in indexed.daily_facts}
        same_day_latest = session.scalar(select(PositionDailyFactRevision).where(
            PositionDailyFactRevision.daily_fact_id == same_day_id,
            PositionDailyFactRevision.revision_no == 2))
        assert same_day_latest is not None
        assert indexed.daily_facts[0].current_revision_id == same_day_latest.id
        assert {row.intent_id for row in indexed.intents} == intent_ids
        assert all(row.first_live_revision_id is not None for row in indexed.intents)
        assert indexed.original_root_fill_ids == (initial_id,)
        assert not session.new and not session.dirty and not session.deleted


def test_pending_daily_proposal_clears_every_surface_group(
        env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, _ = _historical_corrected_anchor(
        factory, monkeypatch)
    with factory() as session:
        fact = _daily_fact(session, lifecycle_id, date(2026, 9, 29))
        session.commit()
        fact_id = fact.id
    with factory() as session:
        first_revision = session.scalar(select(PositionDailyFactRevision).where(
            PositionDailyFactRevision.daily_fact_id == fact_id))
        proposal = PositionDailyFactInputProposal(
            id=uuid.uuid4(), daily_fact_id=fact_id,
            base_revision_id=first_revision.id, request_key="pending-3an",
            proposed_price_basis="raw", proposed_data_as_of=datetime(
                2026, 9, 29, 2, tzinfo=timezone.utc),
            proposed_input_payload={"close": "11"},
            canonical_input_bytes=b'{"close":"11"}',
            proposed_input_hash=hashlib.sha256(b'{"close":"11"}').hexdigest(),
            reason_code="CORRECT_INPUT", source_ref="fixture:proposal",
            source_sha256="c" * 64)
        session.add(proposal)
        session.commit()
    with factory() as session:
        pending = load_local_lifecycle_revision_impact_surface(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert pending.status == "UNKNOWN"
        assert pending.daily_facts == pending.intents == pending.old_steps == ()
        assert pending.original_root_fill_ids == ()
        assert "REVISION_SURFACE_DAILY_CHAIN_UNKNOWN" in pending.issues


def test_broken_daily_revision_predecessor_clears_every_surface_group(
        env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, _ = _historical_corrected_anchor(
        factory, monkeypatch)
    with factory() as session:
        fact = _daily_fact(session, lifecycle_id, date(2026, 9, 29))
        fact.final_target_shares = Decimal(4)
        session.commit()  # 0044 creates revision 2 from the parent mutation.
        fact_id = fact.id
    with factory() as session:
        second = session.scalar(select(PositionDailyFactRevision).where(
            PositionDailyFactRevision.daily_fact_id == fact_id,
            PositionDailyFactRevision.revision_no == 2))
        # In the disposable test DB only, corrupt an immutable predecessor.
        session.execute(text("ALTER TABLE position_daily_fact_revisions "
                             "DISABLE TRIGGER daily_fact_revision_immutable"))
        session.execute(text("UPDATE position_daily_fact_revisions "
                             "SET previous_revision_id=id WHERE id=:id"),
                        {"id": second.id})
        session.execute(text("ALTER TABLE position_daily_fact_revisions "
                             "ENABLE TRIGGER daily_fact_revision_immutable"))
        session.commit()
    with factory() as session:
        broken = load_local_lifecycle_revision_impact_surface(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert broken.status == "UNKNOWN"
        assert broken.daily_facts == broken.intents == broken.old_steps == ()
        assert any(issue.startswith("DAILY_FACT_REVISION_CHAIN_INVALID:")
                   for issue in broken.issues)


def test_unrevised_signed_root_returns_no_impact_index(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, report_id = _seed(factory, monkeypatch)
    with factory() as session:
        posting, _ = AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        strategy = QuantStrategy(name=f"surface-unrevised-{uuid.uuid4()}")
        policy = LifecyclePolicyVersion(
            policy_key=f"surface-unrevised-{uuid.uuid4()}", version_no=1,
            status="PUBLISHED", required_fields=[], config={},
            content_hash="a" * 64)
        session.add_all((strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        lifecycle = PositionLifecycleState(
            portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            initial_fill_id=posting.fill_event_id,
            initial_fill_price=Decimal(10), initial_stop_price=Decimal(8),
            risk_capacity_shares=Decimal(5), target_exposure_pct=Decimal("0.5"),
            target_shares=Decimal(5), phase="ACTIVE")
        session.add(lifecycle)
        session.commit()
        lifecycle_id = lifecycle.id
    with factory() as session:
        index = load_local_lifecycle_revision_impact_surface(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert index.status == "NO_REVISION"
        assert index.impact_date is None
        assert index.daily_facts == index.intents == index.old_steps == ()
        assert index.original_root_fill_ids == ()


def test_broken_first_live_intent_chain_clears_every_surface_group(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, _ = _historical_corrected_anchor(
        factory, monkeypatch)
    with factory() as session:
        earlier = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle_id,
            trade_date=date(2026, 9, 27), target_shares=Decimal(5),
            reason_code="EARLIER", state_version=1,
            status="ACTIVE", revision=1)
        session.add(earlier)
        session.flush()
        earlier.status = "COMPLETED"
        earlier.revision = 2
        session.flush()
        later = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle_id,
            trade_date=date(2026, 9, 29), target_shares=Decimal(0),
            reason_code="LATER", state_version=1,
            status="ACTIVE", revision=1)
        session.add(later)
        session.commit()
        earlier_id, later_id = earlier.id, later.id
    with factory() as session:
        earlier_second = session.scalar(select(PositionIntentRevision).where(
            PositionIntentRevision.intent_id == earlier_id,
            PositionIntentRevision.revision_no == 2))
        later_first = session.scalar(select(PositionIntentRevision).where(
            PositionIntentRevision.intent_id == later_id,
            PositionIntentRevision.revision_no == 1))
        session.execute(text("ALTER TABLE position_intent_revisions "
                             "DISABLE TRIGGER position_intent_revision_immutable"))
        session.execute(text("UPDATE position_intent_revisions "
                             "SET previous_revision_id=:wrong WHERE id=:id"),
                        {"wrong": later_first.id, "id": earlier_second.id})
        session.execute(text("ALTER TABLE position_intent_revisions "
                             "ENABLE TRIGGER position_intent_revision_immutable"))
        session.commit()
    with factory() as session:
        surface = load_local_lifecycle_revision_impact_surface(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert surface.status == "UNKNOWN"
        assert surface.daily_facts == surface.intents == surface.old_steps == ()
        assert surface.original_root_fill_ids == ()
        assert any(issue.startswith("INTENT_REVISION_CHAIN_INVALID:")
                   for issue in surface.issues)


@pytest.mark.parametrize("broken_step", (None, "MISSING", "UNATTRIBUTED", "GAP"))
def test_real_later_posting_old_causal_step_is_indexed_or_fails_closed(
        env, monkeypatch, broken_step):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, initial_id = _historical_corrected_anchor(
        factory, monkeypatch)
    later_key = b"impact-surface-later-review-key-32!"
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
        "void-auditor": base64.b64encode(VOID_SECRET).decode(),
        "replay-reviewer": base64.b64encode(later_key).decode(),
    }))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        intent = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle_id,
            trade_date=date(2026, 9, 29), target_shares=Decimal(5),
            reason_code="PROFIT_TARGET_TRIM", state_version=1,
            status="ACTIVE", revision=1)
        session.add(intent)
        session.flush()
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            position_id=lifecycle.position_id, lifecycle_id=lifecycle_id,
            intent_id=intent.id, market="CN", symbol="000001.SZ", side="SELL",
            quantity=Decimal(1), filled_quantity=Decimal(0),
            limit_price=Decimal(10), reason_code="PROFIT_TARGET_TRIM",
            status="PROPOSED", revision=1)
        session.add(order)
        session.flush()
        later_fill = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=order,
            day=date(2026, 9, 29),
            executed_at=datetime(2026, 9, 29, 3, tzinfo=timezone.utc),
            source_ref="impact-surface-later", review_key=later_key,
            quantity="1", captured_at=datetime(
                2026, 9, 29, 8, tzinfo=timezone.utc))
        step = session.get(FillLifecycleVersionStep, later_fill.id)
        assert step is not None
        assert (step.lifecycle_id, step.origin, step.version_before,
                step.version_after) == (lifecycle_id, "LOCAL_CAUSAL", 1, 2)
        session.commit()
        later_fill_id = later_fill.id

    if broken_step is not None:
        with factory() as session:
            # The disposable test DB simulates a damaged immutable history.
            session.execute(text("ALTER TABLE fill_lifecycle_version_steps "
                                 "DISABLE TRIGGER fill_lifecycle_version_step_immutable"))
            if broken_step == "MISSING":
                session.execute(text("DELETE FROM fill_lifecycle_version_steps "
                                     "WHERE fill_event_id=:id"), {"id": later_fill_id})
            elif broken_step == "UNATTRIBUTED":
                session.execute(text("UPDATE fill_lifecycle_version_steps "
                                     "SET origin='UNATTRIBUTED', version_before=NULL, "
                                     "version_after=NULL WHERE fill_event_id=:id"),
                                {"id": later_fill_id})
            else:
                session.execute(text("UPDATE fill_lifecycle_version_steps "
                                     "SET version_before=3, version_after=4 "
                                     "WHERE fill_event_id=:id"), {"id": later_fill_id})
                # The current row can be locally ahead without proving which
                # two causal events supplied the missing version advances.
                session.execute(text("UPDATE position_lifecycle_states "
                                     "SET state_version=4 WHERE id=:id"),
                                {"id": lifecycle_id})
            session.execute(text("ALTER TABLE fill_lifecycle_version_steps "
                                 "ENABLE TRIGGER fill_lifecycle_version_step_immutable"))
            session.commit()

    with factory() as session:
        surface = load_local_lifecycle_revision_impact_surface(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        if broken_step is None:
            assert surface.status == "LOCAL_IMPACT", surface.issues
            assert surface.original_root_fill_ids == tuple(sorted((initial_id, later_fill_id)))
            assert surface.old_step_state == "PRESENT"
            assert len(surface.old_steps) == 1
            assert (surface.old_steps[0].root_fill_event_id,
                    surface.old_steps[0].version_before,
                    surface.old_steps[0].version_after) == (later_fill_id, 1, 2)
        else:
            assert surface.status == "UNKNOWN"
            expected_issue = ("REVISION_SURFACE_OLD_VERSION_BOUNDS_UNKNOWN"
                              if broken_step == "GAP" else
                              "REVISION_SURFACE_ORIGINAL_STEP_SET_UNKNOWN")
            assert expected_issue in surface.issues
            assert surface.daily_facts == surface.intents == surface.old_steps == ()
            assert surface.original_root_fill_ids == ()
