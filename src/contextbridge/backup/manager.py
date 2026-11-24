"""Local backup manager with SHA-256 manifests, destination authorization, and atomic restore."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from contextbridge.core.exceptions import (
    BackupError,
    BackupIntegrityError,
    PathTraversalError,
)
from contextbridge.models.backup import (
    BackupFileEntry,
    BackupManifest,
    BackupVerificationResult,
    RestorePlan,
    RestoreResult,
)
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits
from contextbridge.security.paths import (
    collect_safe_skill_files,
    ensure_no_symlink_in_ancestors,
    ensure_within_root,
    validate_authorized_restore_target,
    validate_relative_path,
)
from contextbridge.utils.fs import atomic_write_text, is_symlink_or_junction
from contextbridge.utils.hashing import sha256_file

BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CopyFileFn = Callable[[Path, Path], None]
ReplaceFn = Callable[[Path, Path], None]


def _default_copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _default_replace(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def _validate_backup_id(backup_id: str) -> str:
    if not BACKUP_ID_RE.fullmatch(backup_id) or ".." in backup_id:
        raise PathTraversalError(f"Invalid backup identifier: {backup_id!r}")
    return backup_id


class BackupManager:
    """Manages local, SHA-256 verified directory backups under a dedicated backups root."""

    def __init__(
        self,
        backups_root: Path,
        *,
        limits: SecurityLimits = DEFAULT_LIMITS,
        allowed_restore_roots: Sequence[Path] | None = None,
        home_dir: Path | None = None,
    ) -> None:
        self.backups_root = backups_root
        self.limits = limits
        self.allowed_restore_roots = (
            tuple(allowed_restore_roots) if allowed_restore_roots is not None else None
        )
        self.home_dir = home_dir

    def _backup_dir(self, backup_id: str) -> Path:
        safe_id = _validate_backup_id(backup_id)
        candidate = self.backups_root / safe_id
        if self.backups_root.exists():
            ensure_within_root(self.backups_root, candidate)
        return candidate

    def generate_backup_id(self, prefix: str = "bkp") -> str:
        """Generate a sortable, collision-resistant UTC backup identifier."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", prefix).strip("-")[:24] or "bkp"
        token = secrets.token_hex(4)
        return f"{stamp}_{safe_prefix}_{token}"

    def list_backups(self) -> list[BackupManifest]:
        """List all valid backup manifests sorted newest-first. Read-only; never creates dirs."""
        if not self.backups_root.exists() or not self.backups_root.is_dir():
            return []

        manifests: list[BackupManifest] = []
        for entry in sorted(self.backups_root.iterdir()):
            if not entry.is_dir() or is_symlink_or_junction(entry):
                continue
            manifest_path = entry / "manifest.json"
            if not manifest_path.is_file() or is_symlink_or_junction(manifest_path):
                continue
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifests.append(BackupManifest.model_validate(raw))
            except (OSError, ValueError):
                continue

        manifests.sort(key=lambda m: m.created_at, reverse=True)
        return manifests

    def get_manifest(self, backup_id: str) -> BackupManifest:
        """Load the manifest for a specific backup ID. Read-only."""
        bdir = self._backup_dir(backup_id)
        if bdir.exists() and is_symlink_or_junction(bdir):
            raise BackupIntegrityError(f"Backup directory is a symlink or junction: {backup_id}")
        manifest_path = bdir / "manifest.json"
        if not manifest_path.is_file():
            raise BackupError(f"Backup not found: {backup_id}")
        if is_symlink_or_junction(manifest_path):
            raise BackupIntegrityError(f"Backup manifest is a symlink or junction: {backup_id}")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = BackupManifest.model_validate(raw)
        except (OSError, ValueError) as exc:
            raise BackupIntegrityError(f"Corrupted backup manifest for {backup_id}: {exc}") from exc

        if manifest.backup_id != backup_id:
            raise BackupIntegrityError(
                f"Manifest backup_id ({manifest.backup_id!r}) does not match "
                f"directory identifier ({backup_id!r})."
            )
        return manifest

    def create_backup(
        self,
        source_dir: Path,
        *,
        operation: str,
        skill_name: str | None = None,
        agent_id: str | None = None,
        notes: str | None = None,
        copy_fn: CopyFileFn | None = None,
    ) -> BackupManifest:
        """Create a complete, SHA-256 verified local backup of `source_dir`."""
        if not source_dir.exists() or not source_dir.is_dir():
            raise BackupError(f"Cannot back up non-existent directory: {source_dir}")

        files = collect_safe_skill_files(source_dir, limits=self.limits, allow_symlinks=False)
        self.backups_root.mkdir(parents=True, exist_ok=True)

        backup_id = self.generate_backup_id(prefix=skill_name or operation)
        final_dir = self._backup_dir(backup_id)
        staging_dir = Path(
            tempfile.mkdtemp(prefix=f".staging_{backup_id}_", dir=str(self.backups_root))
        )
        payload_dir = staging_dir / "payload"
        payload_dir.mkdir(parents=True, exist_ok=True)
        copier = copy_fn or _default_copy_file

        entries: list[BackupFileEntry] = []
        total_bytes = 0

        try:
            for rel_posix, src_file, size in files:
                dst_file = payload_dir.joinpath(*rel_posix.parts)
                ensure_within_root(payload_dir, dst_file)
                copier(src_file, dst_file)

                src_hash = sha256_file(src_file)
                dst_hash = sha256_file(dst_file)
                if src_hash != dst_hash:
                    raise BackupIntegrityError(
                        f"Hash mismatch while creating backup for {rel_posix}."
                    )

                entries.append(
                    BackupFileEntry(
                        relative_path=rel_posix.as_posix(),
                        size_bytes=size,
                        sha256=dst_hash,
                    )
                )
                total_bytes += size

            manifest = BackupManifest(
                backup_id=backup_id,
                created_at=datetime.now(UTC),
                operation=operation,
                source_path=source_dir.resolve(),
                skill_name=skill_name,
                agent_id=agent_id,
                total_files=len(entries),
                total_bytes=total_bytes,
                files=entries,
                notes=notes,
            )

            atomic_write_text(
                staging_dir / "manifest.json",
                manifest.model_dump_json(indent=2) + "\n",
            )
            os.replace(staging_dir, final_dir)
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"Failed to create backup for {source_dir}: {exc}") from exc

        verification = self.verify_backup(backup_id)
        if not verification.valid:
            raise BackupIntegrityError(
                f"Backup {backup_id} failed post-write verification: "
                + "; ".join(verification.errors)
            )
        return manifest

    def verify_backup(self, backup_id: str) -> BackupVerificationResult:
        """Verify every file in a backup against its recorded SHA-256 digest. Read-only."""
        errors: list[str] = []
        try:
            manifest = self.get_manifest(backup_id)
        except BackupError as exc:
            return BackupVerificationResult(
                backup_id=backup_id,
                valid=False,
                checked_files=0,
                errors=[exc.message],
            )

        bdir = self._backup_dir(backup_id)
        payload_dir = bdir / "payload"
        if not payload_dir.is_dir() or is_symlink_or_junction(payload_dir):
            return BackupVerificationResult(
                backup_id=backup_id,
                valid=False,
                checked_files=0,
                errors=["Backup payload directory is missing or unsafe."],
            )

        if manifest.total_files != len(manifest.files):
            errors.append(
                f"Manifest total_files ({manifest.total_files}) does not match "
                f"entries count ({len(manifest.files)})."
            )

        checked = 0
        expected_rel_paths: set[str] = set()
        seen_casefolded: dict[str, str] = {}
        for entry in manifest.files:
            try:
                rel_posix = validate_relative_path(entry.relative_path)
            except PathTraversalError as exc:
                errors.append(f"Unsafe path in manifest: {exc.message}")
                continue

            folded = rel_posix.as_posix().lower()
            if folded in seen_casefolded:
                errors.append(
                    "Case-insensitive collision in manifest between "
                    f"{seen_casefolded[folded]!r} and {rel_posix.as_posix()!r}."
                )
                continue
            seen_casefolded[folded] = rel_posix.as_posix()

            expected_rel_paths.add(rel_posix.as_posix())
            payload_file = payload_dir.joinpath(*rel_posix.parts)
            try:
                ensure_no_symlink_in_ancestors(payload_dir, payload_file)
                ensure_within_root(payload_dir, payload_file)
            except Exception as exc:
                errors.append(f"Unsafe payload file {entry.relative_path}: {exc}")
                continue

            if not payload_file.is_file():
                errors.append(f"Missing payload file: {entry.relative_path}")
                continue

            actual_hash = sha256_file(payload_file)
            if actual_hash != entry.sha256:
                errors.append(
                    f"SHA-256 mismatch for {entry.relative_path}: "
                    f"expected {entry.sha256[:12]}..., got {actual_hash[:12]}..."
                )
                continue
            checked += 1

        # Detect unexpected extra files in payload
        try:
            actual_files = collect_safe_skill_files(
                payload_dir, limits=self.limits, allow_symlinks=False
            )
            actual_rel_paths = {rel.as_posix() for rel, _, _ in actual_files}
            extra = sorted(actual_rel_paths - expected_rel_paths)
            for extra_path in extra:
                errors.append(f"Unexpected file in backup payload: {extra_path}")
        except Exception as exc:
            errors.append(f"Failed scanning backup payload: {exc}")

        return BackupVerificationResult(
            backup_id=backup_id,
            valid=len(errors) == 0,
            checked_files=checked,
            errors=errors,
        )

    def plan_restore(
        self,
        backup_id: str,
        *,
        target_override: Path | None = None,
        allowed_roots: Sequence[Path] | None = None,
    ) -> RestorePlan:
        """Build a read-only restore preview with integrity and authorized destination checks."""
        manifest = self.get_manifest(backup_id)
        integrity = self.verify_backup(backup_id)

        effective_roots: Sequence[Path] | None = (
            allowed_roots if allowed_roots is not None else self.allowed_restore_roots
        )
        raw_target = target_override if target_override is not None else manifest.source_path
        restore_target = validate_authorized_restore_target(
            raw_target,
            backups_root=self.backups_root,
            allowed_roots=effective_roots,
            protected_roots=effective_roots,
            home_dir=self.home_dir,
            require_within_allowed_roots=(target_override is None),
        )

        warnings: list[str] = []
        if not integrity.valid:
            warnings.append("Backup failed integrity verification and cannot be restored safely.")
        if restore_target.exists():
            warnings.append(
                "Restore destination currently exists; applying will take a safety backup first "
                "and replace its contents."
            )

        return RestorePlan(
            backup_id=backup_id,
            manifest=manifest,
            restore_target=restore_target,
            target_exists=restore_target.exists(),
            integrity=integrity,
            files_to_restore=manifest.total_files,
            total_bytes=manifest.total_bytes,
            warnings=warnings,
        )

    def restore_backup(
        self,
        backup_id: str,
        *,
        dry_run: bool = True,
        target_override: Path | None = None,
        allowed_roots: Sequence[Path] | None = None,
        copy_fn: CopyFileFn | None = None,
        replace_fn: ReplaceFn | None = None,
    ) -> RestoreResult:
        """Restore a backup with dry-run preview, destination authorization, and rollback."""
        plan = self.plan_restore(
            backup_id,
            target_override=target_override,
            allowed_roots=allowed_roots,
        )
        if not plan.integrity.valid:
            raise BackupIntegrityError(
                f"Cannot restore corrupted backup {backup_id}: " + "; ".join(plan.integrity.errors)
            )

        if dry_run:
            return RestoreResult(
                plan=plan,
                applied=False,
                dry_run=True,
                message="Dry-run preview only. Pass --apply to restore files.",
            )

        bdir = self._backup_dir(backup_id)
        payload_dir = bdir / "payload"
        target = plan.restore_target
        if target.exists() and is_symlink_or_junction(target):
            raise BackupError(f"Restore destination is a symlink or junction: {target}")
        if target.parent.exists() and is_symlink_or_junction(target.parent):
            raise BackupError(f"Restore parent directory is a symlink or junction: {target.parent}")

        target.parent.mkdir(parents=True, exist_ok=True)
        copier = copy_fn or _default_copy_file
        replacer = replace_fn or _default_replace

        pre_restore_backup_id: str | None = None
        if target.exists() and target.is_dir():
            pre_manifest = self.create_backup(
                target,
                operation="pre-restore",
                skill_name=plan.manifest.skill_name,
                agent_id=plan.manifest.agent_id,
                notes=f"Automatic snapshot before restoring {backup_id}",
            )
            pre_restore_backup_id = pre_manifest.backup_id

        staging_dir = Path(tempfile.mkdtemp(prefix=".cb_restore_stage_", dir=str(target.parent)))
        displaced_dir: Path | None = None
        rolled_back = False
        rollback_error: str | None = None

        try:
            restored_count = 0
            for entry in plan.manifest.files:
                rel_posix = validate_relative_path(entry.relative_path)
                src_file = payload_dir.joinpath(*rel_posix.parts)
                dst_file = staging_dir.joinpath(*rel_posix.parts)
                ensure_within_root(payload_dir, src_file)
                ensure_within_root(staging_dir, dst_file)

                copier(src_file, dst_file)
                if sha256_file(dst_file) != entry.sha256:
                    raise BackupIntegrityError(
                        f"Restored file failed SHA-256 verification: {entry.relative_path}"
                    )
                restored_count += 1

            if target.exists():
                displaced_dir = target.parent / f".cb_restore_old_{secrets.token_hex(4)}"
                replacer(target, displaced_dir)

            replacer(staging_dir, target)

            for entry in plan.manifest.files:
                rel_posix = validate_relative_path(entry.relative_path)
                live_file = target.joinpath(*rel_posix.parts)
                if not live_file.is_file() or sha256_file(live_file) != entry.sha256:
                    raise BackupIntegrityError(
                        f"Post-swap SHA-256 verification failed for {entry.relative_path}"
                    )

            if displaced_dir is not None and displaced_dir.exists():
                shutil.rmtree(displaced_dir, ignore_errors=True)

            return RestoreResult(
                plan=plan,
                applied=True,
                dry_run=False,
                pre_restore_backup_id=pre_restore_backup_id,
                files_restored=restored_count,
                verified=True,
                rolled_back=False,
                message=f"Restored {restored_count} files to {target} and verified SHA-256 hashes.",
            )
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            if displaced_dir is not None and displaced_dir.exists():
                failed_dir: Path | None = None
                try:
                    if target.exists():
                        failed_dir = target.parent / f".cb_restore_failed_{secrets.token_hex(4)}"
                        replacer(target, failed_dir)
                    replacer(displaced_dir, target)
                    rolled_back = True
                    if failed_dir is not None and failed_dir.exists():
                        shutil.rmtree(failed_dir, ignore_errors=True)
                except OSError as rb_exc:
                    rollback_error = (
                        f"Rollback failed ({rb_exc}); original files preserved at {displaced_dir}"
                        + (
                            f" and pre-restore backup {pre_restore_backup_id}"
                            if pre_restore_backup_id
                            else ""
                        )
                    )
            detail = f"rolled_back={rolled_back}"
            if rollback_error:
                detail += f"; {rollback_error}"
            if isinstance(exc, BackupError):
                raise BackupError(f"{exc.message} ({detail})") from exc
            raise BackupError(f"Restore failed ({detail}): {exc}") from exc
