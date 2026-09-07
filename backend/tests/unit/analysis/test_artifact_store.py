"""ArtifactStore 生命周期单测（§2.4：staging 校验/同卷原子发布/幂等/fencing/回收）。"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.modules.analysis.infrastructure.artifact_store import (
    ArtifactFencingError,
    ArtifactValidationError,
    PlatformArtifactStore,
)


def _store(tmp_path, **kwargs) -> PlatformArtifactStore:
    return PlatformArtifactStore(tmp_path / "runs", **kwargs)


def test_write_validated_rejects_bad_extension_and_oversize(tmp_path):
    store = _store(tmp_path, max_file_bytes=100)
    staging = store.staging_dir(uuid.uuid4(), 1, "token-1")
    staging.mkdir(parents=True)
    with pytest.raises(ArtifactValidationError, match="扩展名"):
        store.write_validated(staging, "evil.exe", b"x")
    with pytest.raises(ArtifactValidationError, match="超限"):
        store.write_validated(staging, "big.json", b"x" * 101)
    with pytest.raises(ArtifactValidationError, match="逃逸"):
        store.write_validated(staging, "../escape.json", b"x")


def test_publish_is_atomic_and_manifest_complete(tmp_path):
    store = _store(tmp_path)
    task_id, attempt_no, token = uuid.uuid4(), 1, "lease-1"
    staging = store.staging_dir(task_id, attempt_no, token)
    staging.mkdir(parents=True)
    store.write_validated(staging, "report.json", b'{"ok": true}')

    final = store.publish(staging, task_id=task_id, attempt_no=attempt_no, lease_token=token, core_version="0.1.0")
    assert final == store.final_dir(task_id, attempt_no)
    assert (final / "report.json").exists()
    manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["task_id"] == str(task_id)
    assert manifest["lease_token"] == token
    assert manifest["files"][0]["path"] == "report.json"
    assert manifest["files"][0]["checksum"]
    assert store.verify(final)
    assert not staging.exists()  # os.replace 后 staging 已消失


def test_republish_same_content_is_idempotent(tmp_path):
    store = _store(tmp_path)
    task_id, token = uuid.uuid4(), "lease-1"
    for _ in range(2):
        staging = store.staging_dir(task_id, 1, token)
        staging.mkdir(parents=True)
        store.write_validated(staging, "report.json", b'{"ok": true}')
        final = store.publish(staging, task_id=task_id, attempt_no=1, lease_token=token, core_version="0.1.0")
    assert store.verify(final)


def test_republish_different_content_is_fencing_conflict(tmp_path):
    store = _store(tmp_path)
    task_id, token = uuid.uuid4(), "lease-1"
    staging = store.staging_dir(task_id, 1, token)
    staging.mkdir(parents=True)
    store.write_validated(staging, "report.json", b'{"ok": true}')
    store.publish(staging, task_id=task_id, attempt_no=1, lease_token=token, core_version="0.1.0")

    # 迟到 attempt 内容不同：绝不覆盖
    staging2 = store.staging_dir(task_id, 1, "lease-2")
    staging2.mkdir(parents=True)
    store.write_validated(staging2, "report.json", b'{"ok": false}')
    with pytest.raises(ArtifactFencingError):
        store.publish(staging2, task_id=task_id, attempt_no=1, lease_token="lease-2", core_version="0.1.0")
    assert (store.final_dir(task_id, 1) / "report.json").read_bytes() == b'{"ok": true}'


def test_persist_artifact_returns_uri_and_checksum(tmp_path):
    store = _store(tmp_path)
    task_id, token = uuid.uuid4(), "lease-1"
    uri, checksum = store.persist_artifact(
        task_id=task_id,
        attempt_no=1,
        lease_token=token,
        core_version="0.1.0",
        final_state={"decision": "买入"},
        report_json={"sections": []},
    )
    assert uri == str(store.final_dir(task_id, 1))
    assert checksum
    assert store.verify(store.final_dir(task_id, 1))


def _make_stale(path: Path, days: int = 2) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


def test_reconcile_removes_stale_staging_only(tmp_path):
    store = _store(tmp_path, retention_days=90)
    task_id, token = uuid.uuid4(), "lease-1"
    staging = store.staging_dir(task_id, 1, token)
    staging.mkdir(parents=True)
    store.write_validated(staging, "report.json", b"{}")
    _make_stale(staging)

    result = store.reconcile()
    assert result["staging"] >= 1
    assert not staging.exists()


def test_reconcile_keeps_corrupted_final_with_warning(tmp_path):
    """verify 失败（损坏）只告警不删除：报告读模型降级由上层处理（§2.4）。"""
    store = _store(tmp_path, retention_days=90)
    final = store.final_dir(uuid.uuid4(), 1)
    final.mkdir(parents=True)
    (final / "orphan.json").write_text("{}", encoding="utf-8")
    _make_stale(final, days=10)

    result = store.reconcile()
    assert final.exists()
    assert result["warnings"] >= 1
    assert result["final"] == 0


def test_reconcile_deletes_unreferenced_expired_and_keeps_referenced(tmp_path):
    store = _store(tmp_path, retention_days=7)
    # 被 DB manifest 引用的目录：无论多旧都不删除
    referenced_id, token = uuid.uuid4(), "lease-ref"
    ref_uri, _ = store.persist_artifact(
        task_id=referenced_id, attempt_no=1, lease_token=token,
        core_version="0.1.0", final_state={}, report_json={"sections": []},
    )
    _make_stale(Path(ref_uri), days=30)
    # 未引用且超过保留期：删除
    orphan_id, token2 = uuid.uuid4(), "lease-orphan"
    orphan_uri, _ = store.persist_artifact(
        task_id=orphan_id, attempt_no=1, lease_token=token2,
        core_version="0.1.0", final_state={}, report_json={"sections": []},
    )
    _make_stale(Path(orphan_uri), days=30)

    result = store.reconcile(referenced={ref_uri})
    assert Path(ref_uri).exists()  # 引用目录保留
    assert not Path(orphan_uri).exists()  # 未引用 + 超保留期删除
    assert result["final"] >= 1


def test_reconcile_keeps_fresh_unreferenced_final(tmp_path):
    store = _store(tmp_path, retention_days=90)
    task_id, token = uuid.uuid4(), "lease-1"
    uri, _ = store.persist_artifact(
        task_id=task_id, attempt_no=1, lease_token=token, core_version="0.1.0",
        final_state={}, report_json={"sections": []},
    )
    store.reconcile()
    assert Path(uri).exists()  # 未过期：保留


def test_reconcile_high_water_lru_evicts_unreferenced(tmp_path):
    store = _store(tmp_path, retention_days=365)
    uris = []
    for i in range(3):
        uri, _ = store.persist_artifact(
            task_id=uuid.uuid4(), attempt_no=1, lease_token=f"lease-{i}",
            core_version="0.1.0", final_state={}, report_json={"sections": ["x" * 2000]},
        )
        uris.append(Path(uri))
        _make_stale(uris[-1], days=90 - i)  # 依次更旧
    # 高水位小于总量：按最旧优先回收
    result = store.reconcile(high_water_bytes=2000)
    assert result["final"] >= 1
    assert not uris[0].exists()  # 最旧被回收
