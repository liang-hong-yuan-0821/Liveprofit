# test-catalog-begin
# {
#   "purpose": "量化策略 / fund_representative_freeze：Research freeze persists only in the isolated quant strategy test database.",
#   "keywords": [
#     "量化策略",
#     "历史审计",
#     "不可变历史",
#     "重放",
#     "fund_representative_freeze",
#     "history",
#     "immutable",
#     "replay"
#   ],
#   "covers": [
#     "backend/modules/quant_research/infrastructure/dataset_repository.py",
#     "backend/modules/quant_research/infrastructure/dataset_store.py",
#     "backend/modules/quant_research/infrastructure/fund_representative_repository.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Research freeze persists only in the isolated quant strategy test database."""

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.modules.quant_research.infrastructure.dataset_repository import DatasetRepository
from backend.modules.quant_research.infrastructure.dataset_store import DatasetIntegrityError, DatasetStore
from backend.modules.quant_research.infrastructure.fund_representative_repository import FundRepresentativeRepository
from tests.backend.quant_research.support.fund_representative_readiness import _snapshot


TRIAL_ID = "etf_dual_momentum:60:1:WEEKLY"


def _published(env, tmp_path: Path):
    dataset, days, facts = _snapshot()
    datasets = DatasetRepository(DatasetStore(tmp_path / uuid4().hex))
    with env["session_factory"]() as session:
        row = datasets.publish(session, dataset, source="isolated-test")
        session.commit()
    return FundRepresentativeRepository(datasets), row.id, days, facts


def _freeze(repository, session, snapshot_id, days, facts, **changes):
    args = dict(snapshot_id=snapshot_id, trial_id=TRIAL_ID,
                evaluation_as_of=days[-1], decision_date=days[-1] + timedelta(days=1),
                trading_days=days, classification_facts=facts, is_month_end_selection=True)
    args.update(changes)
    return repository.freeze(session, **args)


def test_freeze_replay_and_same_identity_drift_conflict(env, tmp_path):
    repository, snapshot_id, days, facts = _published(env, tmp_path)
    with env["session_factory"]() as session:
        first = _freeze(repository, session, snapshot_id, days, facts)
        session.commit()
        assert first.status == "DIAGNOSTIC"
        assert first.result_json["dual_codes"] == ["510310.SH", "511010.SH"]
        assert first.result_json["trading_days"][-1] == days[-1].isoformat()
        assert first.result_json["classification_facts"]["510300.SH"]["category"] == "EQUITY_300"
        assert first.result_json["certification_issues"]
        assert _freeze(repository, session, snapshot_id, days, facts).id == first.id
        session.commit()
        facts = {**facts, "510300.SH": type(facts["510300.SH"])("000300.SH", "OTHER", False)}
        with pytest.raises(ValueError, match="identity conflict"):
            _freeze(repository, session, snapshot_id, days, facts)
        session.rollback()
        assert _freeze(repository, session, snapshot_id, days,
                       _snapshot()[2]).id == first.id


def test_blocked_and_nonselection_results_are_not_persisted(env, tmp_path):
    repository, snapshot_id, days, facts = _published(env, tmp_path)
    with env["session_factory"]() as session:
        with pytest.raises(ValueError, match="NOT_SELECTION_DAY"):
            _freeze(repository, session, snapshot_id, days, facts, is_month_end_selection=False)
        facts.pop("510310.SH")
        with pytest.raises(ValueError, match="BLOCKED"):
            _freeze(repository, session, snapshot_id, days, facts)
        assert session.execute(text("SELECT count(*) FROM quant_fund_representative_freezes "
                                    "WHERE snapshot_id=:snapshot_id"),
                               {"snapshot_id": snapshot_id}).scalar() == 0


def test_snapshot_checksum_and_immutable_history(env, tmp_path):
    repository, snapshot_id, days, facts = _published(env, tmp_path)
    with env["session_factory"]() as session:
        frozen = _freeze(repository, session, snapshot_id, days, facts)
        session.commit()
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM quant_fund_representative_freezes WHERE id=:id"),
                            {"id": frozen.id})
        session.rollback()
    manifest = repository.datasets.store.root / str(snapshot_id) / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with env["session_factory"]() as session:
        with pytest.raises(DatasetIntegrityError, match="corrupt"):
            _freeze(repository, session, snapshot_id, days, facts)


def test_no_eligible_rep_is_explicit_diagnostic_empty_result(env, tmp_path):
    dataset, days, facts = _snapshot()
    dataset.tables["daily"]["amount"] = 1000.
    datasets = DatasetRepository(DatasetStore(tmp_path / uuid4().hex))
    with env["session_factory"]() as session:
        source = datasets.publish(session, dataset, source="isolated-test")
        session.commit()
        frozen = _freeze(FundRepresentativeRepository(datasets), session, source.id, days, facts)
        assert frozen.disposition == "NO_ELIGIBLE_REPRESENTATIVE"
        assert frozen.result_json["dual_codes"] == []
        assert frozen.result_json["certification_issues"]
