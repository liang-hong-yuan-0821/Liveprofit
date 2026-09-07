"""平台产物归档（§2.4：staging 校验 → manifest → 同卷 os.replace 原子发布）。

- 平台产物根目录：var/runs/{task_id}/{attempt_no}/；staging 在
  var/runs/.staging/{task_id}/{attempt_no}/{lease_token}/。
- 发布仅允许同一文件系统内 os.replace；最终目录已存在时仅 checksum 与 manifest
  完全一致视为幂等成功，否则视为 fencing 冲突，绝不覆盖。
- 清理/reconciliation 见 reconcile_unreferenced()（T10 接入 Dispatcher 循环）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".json", ".md", ".txt", ".log", ".csv"}
MANIFEST_NAME = "manifest.json"

_FENCING = "fencing 冲突：最终目录已存在且内容不一致，绝不覆盖"


class ArtifactFencingError(Exception):
    """最终目录已存在且 checksum/manifest 不一致：过期 attempt 不得覆盖。"""


class ArtifactValidationError(Exception):
    """staging 文件校验失败：大小/扩展名/路径逃逸。"""


class PlatformArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = 10 * 1024 * 1024,
        max_task_bytes: int = 100 * 1024 * 1024,
        retention_days: int = 90,
    ) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)  # 首次启动自动建卷（无共享卷时也不阻塞 reconcile）
        self._max_file_bytes = max_file_bytes
        self._max_task_bytes = max_task_bytes
        self._retention_days = retention_days

    @property
    def root(self) -> Path:
        return self._root

    def staging_dir(self, task_id: uuid.UUID, attempt_no: int, lease_token: str) -> Path:
        return self._root / ".staging" / str(task_id) / str(attempt_no) / lease_token

    def final_dir(self, task_id: uuid.UUID, attempt_no: int) -> Path:
        return self._root / str(task_id) / str(attempt_no)

    # ---- 写入与发布 ----

    def write_validated(self, staging: Path, rel_path: str, content: bytes) -> Path:
        """逐文件执行路径/扩展名/大小校验后写入 staging。"""
        target = (staging / rel_path).resolve()
        if staging not in target.parents and target != staging:
            raise ArtifactValidationError(f"路径逃逸拒绝：{rel_path}")
        if target.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ArtifactValidationError(f"不允许的扩展名：{rel_path}")
        if len(content) > self._max_file_bytes:
            raise ArtifactValidationError(f"单文件超限：{rel_path} {len(content)} 字节")
        total = sum(f.stat().st_size for f in staging.rglob("*") if f.is_file())
        if total + len(content) > self._max_task_bytes:
            raise ArtifactValidationError(f"任务产物超限：{total + len(content)} 字节")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def build_manifest(
        self, staging: Path, *, task_id: uuid.UUID, attempt_no: int, lease_token: str, core_version: str | None
    ) -> dict:
        files = []
        for file in sorted(staging.rglob("*")):
            if not file.is_file():
                continue
            rel = str(file.relative_to(staging)).replace(os.sep, "/")
            files.append(
                {
                    "path": rel,
                    "size": file.stat().st_size,
                    "checksum": _sha256_file(file),
                }
            )
        return {
            "task_id": str(task_id),
            "attempt_no": attempt_no,
            "lease_token": lease_token,
            "core_version": core_version,
            "files": files,
        }

    def publish(
        self,
        staging: Path,
        *,
        task_id: uuid.UUID,
        attempt_no: int,
        lease_token: str,
        core_version: str | None,
    ) -> Path:
        """写 manifest → fsync → 同卷 os.replace 原子发布；重复发布 checksum 一致视为幂等成功。"""
        manifest = self.build_manifest(
            staging, task_id=task_id, attempt_no=attempt_no, lease_token=lease_token, core_version=core_version
        )
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        _fsync_dir(staging)

        final = self.final_dir(task_id, attempt_no)
        if final.exists():
            existing = final / MANIFEST_NAME
            if existing.exists() and _manifests_equal(existing, manifest_path):
                shutil.rmtree(staging, ignore_errors=True)  # 幂等成功：丢弃 staging
                return final
            raise ArtifactFencingError(_FENCING)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)  # 同卷原子发布
        _fsync_dir(final.parent)
        return final

    def persist_artifact(
        self,
        *,
        task_id: uuid.UUID,
        attempt_no: int,
        lease_token: str,
        core_version: str | None,
        final_state: dict,
        report_json: dict,
    ) -> tuple[str, str]:
        """执行器使用：写 staging（final_state/report）→ manifest → 原子发布 → 返回 (artifact_uri, checksum)。"""
        staging = self.staging_dir(task_id, attempt_no, lease_token)
        staging.mkdir(parents=True, exist_ok=False)
        try:
            self.write_validated(
                staging,
                "final_state.json",
                json.dumps(final_state, ensure_ascii=False, default=str).encode("utf-8"),
            )
            report_bytes = json.dumps(report_json, ensure_ascii=False, default=str).encode("utf-8")
            self.write_validated(staging, "report.json", report_bytes)
            final = self.publish(
                staging,
                task_id=task_id,
                attempt_no=attempt_no,
                lease_token=lease_token,
                core_version=core_version,
            )
        except ArtifactFencingError:
            raise
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return str(final), hashlib.sha256(report_bytes).hexdigest()

    def verify(self, final: Path) -> bool:
        """校验最终目录与 manifest 完全一致（reconciliation 使用）。"""
        manifest_path = final / MANIFEST_NAME
        if not manifest_path.exists():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            return False
        for entry in manifest.get("files", []):
            file = final / entry["path"]
            if not file.is_file() or file.stat().st_size != entry["size"]:
                return False
            if _sha256_file(file) != entry["checksum"]:
                return False
        return True

    # ---- 清理（§2.4：reconciliation 由 Dispatcher 恢复循环调用） ----

    def reconcile(
        self,
        *,
        now: datetime | None = None,
        grace: timedelta = timedelta(days=1),
        referenced: set[str] | None = None,
        high_water_bytes: int | None = None,
    ) -> dict:
        """清理语义（§2.4 / §5.1 P-6）：

        - staging：宽限期后删除（无有效租约的过期目录）。
        - 最终目录：DB manifest 引用（referenced 集合由调用方注入）**绝不删除**；
          未引用且超过保留期（retention_days）的删除；verify 失败（损坏）只告警不删除
          （报告读模型降级由上层告警链处理，不伪造内容）。
        - 高水位：总大小超过 high_water_bytes 时，按最旧优先（LRU）回收未引用目录。
        """
        now = now or datetime.now(timezone.utc)
        referenced = referenced or set()
        removed_staging = 0
        removed_final = 0
        warnings = 0
        final_dirs: list[tuple[datetime, Path]] = []

        staging_root = self._root / ".staging"
        if staging_root.exists():
            # 粒度到租约叶子目录（task/attempt 父目录仅作分组）
            for task_dir in staging_root.iterdir():
                if not task_dir.is_dir():
                    continue
                for attempt_dir in task_dir.iterdir():
                    if not attempt_dir.is_dir():
                        continue
                    for lease_dir in attempt_dir.iterdir():
                        if not lease_dir.is_dir():
                            continue
                        try:
                            age = now - datetime.fromtimestamp(lease_dir.stat().st_mtime, tz=timezone.utc)
                            if age > grace:
                                shutil.rmtree(lease_dir, ignore_errors=True)
                                removed_staging += 1
                        except OSError:
                            continue

        for task_dir in self._root.iterdir():
            if task_dir.name.startswith(".") or not task_dir.is_dir():
                continue
            for attempt_dir in task_dir.iterdir():
                if not attempt_dir.is_dir():
                    continue
                try:
                    mtime = datetime.fromtimestamp(attempt_dir.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    continue
                if not self.verify(attempt_dir):
                    # 损坏目录：只告警，不删除（报告降级 UNAVAILABLE 由上层按 DB manifest 处理）
                    logger.warning("产物目录校验失败（保留待人工处理）：%s", attempt_dir)
                    warnings += 1
                    continue
                final_dirs.append((mtime, attempt_dir))

        retention = timedelta(days=self._retention_days)
        unreferenced = [(mtime, d) for mtime, d in final_dirs if str(d) not in referenced]
        for mtime, directory in unreferenced:
            if now - mtime <= retention:
                continue
            shutil.rmtree(directory, ignore_errors=True)
            removed_final += 1

        if high_water_bytes is not None:
            remaining = [entry for entry in unreferenced if entry[1].exists()]
            total = _dirs_total_size(final_dirs)
            # LRU：按 mtime 最旧优先回收，直到低于高水位
            for mtime, directory in sorted(remaining, key=lambda entry: entry[0]):
                if total <= high_water_bytes:
                    break
                size = _dir_size(directory)
                shutil.rmtree(directory, ignore_errors=True)
                total -= size
                removed_final += 1

        return {"staging": removed_staging, "final": removed_final, "warnings": warnings}


def _dir_size(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


def _dirs_total_size(dirs: list[tuple[datetime, Path]]) -> int:
    return sum(_dir_size(directory) for _, directory in dirs if directory.exists())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifests_equal(existing: Path, incoming: Path) -> bool:
    existing_data = json.loads(existing.read_text(encoding="utf-8"))
    incoming_data = json.loads(incoming.read_text(encoding="utf-8"))
    return json.dumps(existing_data, sort_keys=True) == json.dumps(incoming_data, sort_keys=True)


def _fsync_dir(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:  # Windows 目录 fsync 不可用：忽略
        pass
