# test-catalog-begin
# {
#   "purpose": "投资工作区 / lifecycle_revision_replay_manifest（持仓生命周期、版本修订、重放）：3ap read-only mapping in the disposable liveprofit_workspace_test DB.",
#   "keywords": [
#     "投资工作区",
#     "成交",
#     "持仓生命周期",
#     "重放",
#     "收益",
#     "版本修订",
#     "lifecycle_revision_replay_manifest",
#     "fill",
#     "lifecycle",
#     "replay",
#     "returns",
#     "revision"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/fill_posting_stage.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_revision_replay_manifest.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""3ap read-only mapping in the disposable liveprofit_workspace_test DB."""

from backend.modules.investment_workspace.application.fill_posting_stage import (
    AccountFillPostingStage,
)
from backend.modules.quant_strategy.application.lifecycle_persisted_revision_replay_manifest import (
    load_local_revision_replay_manifest,
)
from tests.backend.investment_workspace.support.fill_posting_stage import BASELINE, _post_and_review_correction, _seed
from tests.backend.investment_workspace.support.lifecycle_persisted_resolved_accounting import _anchor_existing_fill
from tests.backend.investment_workspace.support.lifecycle_revision_impact_surface import _historical_corrected_anchor


def _manifest(session, portfolio_id, lifecycle_id):
    from decimal import Decimal

    return load_local_revision_replay_manifest(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
        baseline_as_of=BASELINE, baseline_quantity=Decimal(10),
        baseline_total_cost=Decimal(100),
        baseline_source_ref="fixture:explicit-opening-cost",
    )


def test_historical_signed_correction_maps_old_sell_anchor_without_seed(
        env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, old_anchor = _historical_corrected_anchor(
        factory, monkeypatch)

    with factory() as session:
        result = _manifest(session, portfolio_id, lifecycle_id)
        assert result.status == "LOCAL_MAPPING", result.issues
        assert result.initial_gate == "NONBUY_INITIAL"
        assert len(result.roots) == 1
        root = result.roots[0]
        assert root.root_fill_event_id == old_anchor
        assert root.disposition == "CORRECT"
        assert root.terminal_fill_event_id != old_anchor
        assert root.old_version_before is root.old_version_after is None
        assert result.effective_root_execution_order == (old_anchor,)
        assert "REPLAY_BLOCKED:NONBUY_INITIAL" in result.issues
        assert "BROKER_FILL_SET_UNCERTIFIED" in result.issues
        assert not session.new and not session.dirty and not session.deleted


def test_unrevised_signed_fill_has_no_replay_work_list(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, report_id = _seed(factory, monkeypatch)
    with factory() as session:
        AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        session.commit()
    lifecycle_id, _ = _anchor_existing_fill(
        factory, portfolio_id, report_id)

    with factory() as session:
        result = _manifest(session, portfolio_id, lifecycle_id)
        assert result.status == "NO_REVISION", result.issues
        assert result.roots == result.effective_root_execution_order == ()
        assert result.daily_facts_to_recompute == result.intents_to_recompute == ()
        assert not session.new and not session.dirty and not session.deleted


def test_pending_signed_correction_returns_unknown_without_partial_mapping(
        env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, old_report_id, _, _ = _post_and_review_correction(
        factory, monkeypatch)
    lifecycle_id, _ = _anchor_existing_fill(
        factory, portfolio_id, old_report_id)

    with factory() as session:
        result = _manifest(session, portfolio_id, lifecycle_id)
        assert result.status == "UNKNOWN"
        assert result.initial_gate == "UNKNOWN"
        assert result.roots == result.effective_root_execution_order == ()
        assert result.daily_facts_to_recompute == result.intents_to_recompute == ()
        assert "FILL_REVISION_REPORT_UNPOSTED" in result.issues
        assert not session.new and not session.dirty and not session.deleted
