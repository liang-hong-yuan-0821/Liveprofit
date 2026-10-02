"""Bind published files to durable PG identity in the caller's transaction."""

from __future__ import annotations

import uuid

from backend.modules.quant_research.application.dataset_builder import ResearchDataset
from backend.modules.quant_research.infrastructure.dataset_store import (
    DatasetIntegrityError,
    DatasetStore,
    _coverage_manifest,
)
from backend.modules.quant_research.infrastructure.models import QuantResearchDataset


class DatasetRepository:
    def __init__(self, store: DatasetStore) -> None:
        self.store = store

    def publish(self, session, dataset: ResearchDataset, *, source: str) -> QuantResearchDataset:
        """Caller commits. A failed DB transaction leaves only an unreferenced file."""
        snapshot_id = uuid.uuid4()
        self.store.publish(snapshot_id, dataset, source=source)
        record = QuantResearchDataset(
            id=snapshot_id,
            as_of=dataset.quality.as_of,
            status="READY",
            source=source,
            manifest_path=f"{snapshot_id}/manifest.json",
            manifest_sha256=self.store.manifest_checksum(snapshot_id),
            quality_json={
                "issues": list(dataset.quality.issues),
                "certifiable": dataset.quality.certifiable,
                "certification_issues": list(dataset.quality.certification_issues),
                "source_rows": dict(dataset.quality.source_rows),
                "instrument_types": list(dataset.quality.instrument_types),
                "coverage": _coverage_manifest(dataset),
            },
        )
        session.add(record)
        session.flush()
        return record

    def read(self, session, snapshot_id: uuid.UUID):
        record = session.get(QuantResearchDataset, snapshot_id)
        if record is None or record.status != "READY":
            raise DatasetIntegrityError("research dataset is not READY")
        if record.manifest_path != f"{snapshot_id}/manifest.json":
            raise DatasetIntegrityError("research dataset path mismatch")
        return self.store.read(
            snapshot_id, expected_manifest_checksum=record.manifest_sha256,
        )
