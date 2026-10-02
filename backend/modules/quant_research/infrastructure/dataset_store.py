"""Immutable Parquet snapshots; a manifest is published only after all files exist."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import pandas as pd

from backend.modules.quant_research.application.dataset_builder import ResearchDataset


class DatasetIntegrityError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DatasetStore:
    """Separate permanent research root, outside short-lived task artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def publish(self, snapshot_id: uuid.UUID, dataset: ResearchDataset, *, source: str) -> Path:
        if dataset.quality.status != "READY":
            raise DatasetIntegrityError("only READY datasets may be published")
        final = self.root / str(snapshot_id)
        if final.exists():
            raise DatasetIntegrityError("snapshot identity already published")
        staging = self.root / f".staging-{snapshot_id}-{uuid.uuid4()}"
        staging.mkdir()
        try:
            entries = {}
            for name, frame in sorted(dataset.tables.items()):
                if not name.isidentifier():
                    raise DatasetIntegrityError("invalid table name")
                path = staging / f"{name}.parquet"
                frame.to_parquet(path, index=False, engine="pyarrow")
                entries[name] = {"file": path.name, "rows": len(frame), "sha256": _sha256(path)}
            manifest = {
                "snapshot_id": str(snapshot_id),
                "as_of": dataset.quality.as_of.isoformat(),
                "source": source,
                "quality": dataset.quality.status,
                "certifiable": dataset.quality.certifiable,
                "certification_issues": list(dataset.quality.certification_issues),
                "source_rows": dict(dataset.quality.source_rows),
                "instrument_types": list(dataset.quality.instrument_types),
                "coverage": _coverage_manifest(dataset),
                "tables": entries,
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, ensure_ascii=False), encoding="utf-8",
            )
            for path in staging.iterdir():
                with path.open("r+b") as stream:
                    os.fsync(stream.fileno())
            os.replace(staging, final)
            if hasattr(os, "O_DIRECTORY"):
                descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return final
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def manifest_checksum(self, snapshot_id: uuid.UUID) -> str:
        path = self.root / str(snapshot_id) / "manifest.json"
        if not path.is_file():
            raise DatasetIntegrityError("snapshot manifest missing")
        return _sha256(path)

    def read(
        self, snapshot_id: uuid.UUID, *, expected_manifest_checksum: str | None = None,
    ) -> tuple[dict, dict[str, pd.DataFrame]]:
        final = self.root / str(snapshot_id)
        try:
            manifest_path = final / "manifest.json"
            if expected_manifest_checksum is not None and _sha256(manifest_path) != expected_manifest_checksum:
                raise DatasetIntegrityError("snapshot manifest checksum mismatch")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["snapshot_id"] != str(snapshot_id) or manifest["quality"] != "READY":
                raise DatasetIntegrityError("invalid snapshot manifest")
            tables = {}
            for name, entry in manifest["tables"].items():
                if not name.isidentifier() or entry["file"] != f"{name}.parquet":
                    raise DatasetIntegrityError("invalid snapshot path")
                path = final / entry["file"]
                if _sha256(path) != entry["sha256"]:
                    raise DatasetIntegrityError(f"{name}: checksum mismatch")
                frame = pd.read_parquet(path, engine="pyarrow")
                if len(frame) != entry["rows"]:
                    raise DatasetIntegrityError(f"{name}: row count mismatch")
                tables[name] = frame
            return manifest, tables
        except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
            raise DatasetIntegrityError("snapshot missing or corrupt") from exc


def _coverage_manifest(dataset: ResearchDataset) -> dict | None:
    audit = dataset.quality.coverage
    if audit is None:
        return None
    return {
        "expected_symbol_days": audit.expected_symbol_days,
        "missing_counts": dict(audit.missing_counts),
        "not_applicable_counts": dict(audit.not_applicable_counts),
        "sample_gaps": [
            {"component": gap.component, "ts_code": gap.ts_code,
             "trade_date": gap.trade_date.isoformat()}
            for gap in audit.sample_gaps
        ],
        "sample_truncated": audit.sample_truncated,
    }
