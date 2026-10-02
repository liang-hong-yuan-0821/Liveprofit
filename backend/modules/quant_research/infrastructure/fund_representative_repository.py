"""Freeze reproducible ETF month diagnostics without granting execution rights."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from typing import Mapping
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.modules.quant_research.application.coverage_audit import CoverageAudit, CoverageGap
from backend.modules.quant_research.application.dataset_builder import DatasetQuality, ResearchDataset
from backend.modules.quant_research.application.fund_candidate_builder import FundClassificationFacts
from backend.modules.quant_research.application.fund_representative_readiness import (
    inspect_monthly_fund_representatives,
)
from backend.modules.quant_research.infrastructure.dataset_repository import DatasetRepository
from backend.modules.quant_research.infrastructure.models import (
    QuantFundRepresentativeFreeze, QuantResearchDataset,
)


def _canonical(value):
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite diagnostic value")
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical(item) for item in value)
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if value is None or type(value) in (str, int, bool):
        return value
    raise TypeError(f"unsupported diagnostic value: {type(value).__name__}")


def _quality(manifest: dict, record: QuantResearchDataset) -> DatasetQuality:
    if (manifest["as_of"] != record.as_of.isoformat()
            or manifest["quality"] != "READY"
            or manifest["instrument_types"] != record.quality_json["instrument_types"]):
        raise ValueError("research snapshot metadata mismatch")
    raw = manifest.get("coverage")
    coverage = None if raw is None else CoverageAudit(
        expected_symbol_days=raw["expected_symbol_days"],
        missing_counts=raw["missing_counts"],
        not_applicable_counts=raw["not_applicable_counts"],
        sample_gaps=tuple(CoverageGap(item["component"], item["ts_code"],
                                      date.fromisoformat(item["trade_date"]))
                          for item in raw["sample_gaps"]),
        sample_truncated=raw["sample_truncated"],
    )
    return DatasetQuality(
        status="READY", issues=tuple(record.quality_json["issues"]), as_of=record.as_of,
        source_rows=manifest["source_rows"],
        certification_issues=tuple(manifest["certification_issues"]),
        coverage=coverage, instrument_types=tuple(manifest["instrument_types"]),
    )


class FundRepresentativeRepository:
    def __init__(self, datasets: DatasetRepository) -> None:
        self.datasets = datasets

    def freeze(
        self, session, *, snapshot_id: UUID, trial_id: str, evaluation_as_of: date,
        decision_date: date, trading_days: tuple[date, ...],
        classification_facts: Mapping[str, FundClassificationFacts],
        is_month_end_selection: bool,
    ) -> QuantFundRepresentativeFreeze:
        """Caller commits; same identity/content is idempotent, drift is a conflict."""
        if not isinstance(snapshot_id, UUID):
            raise TypeError("published snapshot UUID required")
        record = session.get(QuantResearchDataset, snapshot_id)
        if record is None:
            raise ValueError("published research snapshot required")
        manifest, tables = self.datasets.read(session, snapshot_id)
        dataset = ResearchDataset(tables, _quality(manifest, record))
        if dataset.quality.as_of != evaluation_as_of:
            raise ValueError("selection date differs from published snapshot")
        diagnostic = inspect_monthly_fund_representatives(
            trial_id, dataset, as_of=evaluation_as_of, decision_date=decision_date,
            trading_days=trading_days, classification_facts=classification_facts,
            is_month_end_selection=is_month_end_selection,
        )
        if diagnostic.disposition not in ("SELECTED", "NO_ELIGIBLE_REPRESENTATIVE"):
            raise ValueError(f"monthly representative result cannot be frozen: {diagnostic.disposition}")
        input_json = _canonical({
            "trading_days": trading_days, "classification_facts": classification_facts,
            "diagnostic": diagnostic,
        })
        payload = json.dumps(input_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = sha256(payload.encode("utf-8")).hexdigest()
        result = {
            "trading_days": [day.isoformat() for day in trading_days],
            "classification_facts": _canonical(classification_facts),
            "dual_codes": sorted(diagnostic.dual_codes) if diagnostic.dual_codes is not None else None,
            "defensive_symbols": [list(pair) for pair in diagnostic.defensive_symbols]
            if diagnostic.defensive_symbols is not None else None,
            "expected_codes": list(diagnostic.universe.expected_codes),
            "candidate_codes": list(diagnostic.universe.candidate_codes),
            "excluded_codes": list(diagnostic.universe.excluded_codes),
            "local_issues": list(diagnostic.local_issues),
            "certification_issues": list(diagnostic.certification_issues),
        }
        values = dict(
            id=uuid4(), snapshot_id=snapshot_id, manifest_sha256=record.manifest_sha256,
            trial_id=diagnostic.trial_id, definition_hash=diagnostic.definition_hash,
            evaluation_as_of=evaluation_as_of, decision_date=decision_date,
            status="DIAGNOSTIC", disposition=diagnostic.disposition,
            input_sha256=digest, result_json=result,
        )
        session.execute(insert(QuantFundRepresentativeFreeze).values(**values).on_conflict_do_nothing(
            constraint="uq_fund_rep_freeze_identity",
        ))
        frozen = session.execute(select(QuantFundRepresentativeFreeze).where(
            QuantFundRepresentativeFreeze.snapshot_id == snapshot_id,
            QuantFundRepresentativeFreeze.trial_id == diagnostic.trial_id,
            QuantFundRepresentativeFreeze.evaluation_as_of == evaluation_as_of,
            QuantFundRepresentativeFreeze.decision_date == decision_date,
        )).scalar_one()
        if (frozen.manifest_sha256 != record.manifest_sha256
                or frozen.definition_hash != diagnostic.definition_hash
                or frozen.input_sha256 != digest or frozen.result_json != result
                or frozen.status != "DIAGNOSTIC" or frozen.disposition != diagnostic.disposition):
            raise ValueError("monthly representative freeze identity conflict")
        return frozen
