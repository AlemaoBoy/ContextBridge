"""Safe 10-step skill migration engine with TOCTOU guards, backup, SHA-256, and rollback."""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from contextbridge.adapters.registry import get_adapter
from contextbridge.backup.manager import BackupManager
from contextbridge.core.exceptions import (
    MigrationConflictError,
    MigrationExecutionError,
    SecretExposureError,
    SkillNotFoundError,
    SkillValidationError,
)
from contextbridge.core.paths import EnvironmentContext
from contextbridge.models.agent import AgentId, AgentScope
from contextbridge.models.migration import (
    ConflictInfo,
    FileAction,
    FileActionType,
    MigrationPlan,
    MigrationResult,
)
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits
from contextbridge.security.paths import (
    collect_safe_skill_files,
    ensure_within_root,
    validate_relative_path,
)
from contextbridge.security.secrets import is_sensitive_filename
from contextbridge.skills.validator import validate_skill
from contextbridge.utils.fs import canonical_path_key, is_symlink_or_junction
from contextbridge.utils.hashing import sha256_file

CopyFileFn = Callable[[Path, Path], None]
ReplaceFn = Callable[[Path, Path], None]


def _default_copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _default_replace(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def _snapshot_tree_hashes(directory: Path, *, limits: SecurityLimits) -> dict[str, str]:
    """Compute a deterministic mapping of relative POSIX path -> SHA-256 for a directory."""
    files = collect_safe_skill_files(directory, limits=limits, allow_symlinks=False)
    return {rel.as_posix(): sha256_file(abs_file) for rel, abs_file, _ in files}


def _locate_skill_for_agent(
    skill_name: str,
    source_agent: AgentId,
    env: EnvironmentContext,
    source_scope: AgentScope | None = None,
) -> tuple[Path, AgentScope]:
    """Locate a named skill inside the source agent's configured skill directories."""
    safe_name = validate_relative_path(skill_name)
    if len(safe_name.parts) != 1:
        raise SkillNotFoundError(f"Skill name must be a single directory name: {skill_name!r}")

    adapter = get_adapter(source_agent)
    specs = adapter.skill_directories(env, scope=source_scope)
    for spec in specs:
        candidate = spec.path / safe_name.name
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            return candidate.resolve(), spec.scope

    searched = ", ".join(str(s.path) for s in specs)
    raise SkillNotFoundError(
        f"Skill {skill_name!r} not found in {adapter.display_name} directories ({searched})."
    )


def plan_migration(
    skill_name: str,
    *,
    source_agent: str | AgentId,
    target_agent: str | AgentId,
    env: EnvironmentContext,
    source_scope: AgentScope | None = None,
    target_scope: AgentScope | None = None,
    limits: SecurityLimits = DEFAULT_LIMITS,
) -> MigrationPlan:
    """Build a read-only migration plan (steps 1-5 of the 10-step migration workflow)."""
    src_adapter = get_adapter(source_agent)
    dst_adapter = get_adapter(target_agent)

    # Step 1: Locate skill
    source_path, resolved_source_scope = _locate_skill_for_agent(
        skill_name, src_adapter.agent_id, env, source_scope=source_scope
    )
    resolved_target_scope = target_scope or resolved_source_scope

    # Step 2: Validate source skill
    validation = validate_skill(source_path, limits=limits)

    # Step 3: Check target compatibility
    compatibility_warnings = dst_adapter.compatibility_warnings_for_skill(validation.frontmatter)
    compatibility_warnings.extend(dst_adapter.capabilities.limitations)

    target_root = dst_adapter.primary_write_directory(env, resolved_target_scope)
    safe_dir_name = validate_relative_path(validation.skill_name or source_path.name)
    target_path = target_root / safe_dir_name.name

    same_physical = canonical_path_key(source_path) == canonical_path_key(target_path)

    blocking_reasons: list[str] = []
    if not validation.is_valid:
        for err in validation.errors:
            blocking_reasons.append(f"[{err.code}] {err.message}")

    actions: list[FileAction] = []
    try:
        files = collect_safe_skill_files(source_path, limits=limits, allow_symlinks=False)
        for rel_posix, abs_file, size in files:
            if is_sensitive_filename(rel_posix):
                blocking_reasons.append(
                    f"Sensitive credential file blocked from migration: {rel_posix}"
                )
                continue
            dst_file = target_path.joinpath(*rel_posix.parts)
            action_type = FileActionType.OVERWRITE if dst_file.exists() else FileActionType.CREATE
            actions.append(
                FileAction(
                    relative_path=rel_posix.as_posix(),
                    action=action_type,
                    size_bytes=size,
                    source_sha256=sha256_file(abs_file),
                )
            )
    except Exception as exc:
        blocking_reasons.append(str(exc))

    # Step 4: Detect conflicts and record target fingerprint for TOCTOU detection
    conflict: ConflictInfo | None = None
    target_fingerprint: dict[str, str] | None = None
    requires_backup = False
    if same_physical:
        compatibility_warnings.insert(
            0,
            f"Source ({src_adapter.display_name}) and target ({dst_adapter.display_name}) resolve "
            f"to the SAME physical directory ({source_path}). No file copy is needed.",
        )
    elif target_path.exists():
        if target_path.is_dir() and not is_symlink_or_junction(target_path):
            try:
                target_fingerprint = _snapshot_tree_hashes(target_path, limits=limits)
                existing_count = len(target_fingerprint)
            except Exception:
                existing_count = sum(1 for p in target_path.rglob("*") if p.is_file())
        else:
            existing_count = 1
        conflict = ConflictInfo(
            target_path=target_path,
            existing_files_count=existing_count,
            reason=(
                f"Destination already exists at {target_path} ({existing_count} file(s)). "
                "Silent overwrites are forbidden; pass --overwrite with --apply to replace "
                "(an automatic verified backup will be created first)."
            ),
        )
        requires_backup = True

    can_apply = len(blocking_reasons) == 0 and not same_physical

    return MigrationPlan(
        skill_name=validation.skill_name or source_path.name,
        source_agent=src_adapter.agent_id,
        target_agent=dst_adapter.agent_id,
        source_scope=resolved_source_scope,
        target_scope=resolved_target_scope,
        source_path=source_path,
        target_path=target_path,
        source_validation=validation,
        same_physical_target=same_physical,
        conflict=conflict,
        target_fingerprint=target_fingerprint,
        requires_backup=requires_backup,
        actions=actions,
        compatibility_warnings=compatibility_warnings,
        can_apply=can_apply,
        blocking_reasons=blocking_reasons,
    )


def execute_migration(
    plan: MigrationPlan,
    *,
    env: EnvironmentContext,
    dry_run: bool = True,
    overwrite: bool = False,
    limits: SecurityLimits = DEFAULT_LIMITS,
    copy_fn: CopyFileFn | None = None,
    replace_fn: ReplaceFn | None = None,
) -> MigrationResult:
    """Execute (or dry-run) a migration plan (steps 6-10) with TOCTOU guards and rollback."""
    if plan.same_physical_target:
        return MigrationResult(
            plan=plan,
            applied=False,
            dry_run=dry_run,
            message=(
                "No-op: source and target agents share the same physical skill directory "
                f"({plan.source_path})."
            ),
        )

    if plan.blocking_reasons:
        if any("SEC_" in r or "Sensitive credential" in r for r in plan.blocking_reasons):
            raise SecretExposureError(
                "Migration blocked by security checks: " + "; ".join(plan.blocking_reasons)
            )
        raise SkillValidationError(
            "Migration blocked by validation errors: " + "; ".join(plan.blocking_reasons)
        )

    if dry_run:
        return MigrationResult(
            plan=plan,
            applied=False,
            dry_run=True,
            message="Dry-run preview complete. Pass --apply to execute this migration.",
        )

    # Re-verify source state against plan to prevent TOCTOU mutations between plan and apply
    try:
        live_source_hashes = _snapshot_tree_hashes(plan.source_path, limits=limits)
    except Exception as exc:
        raise MigrationExecutionError(
            f"Source skill became unreadable or unsafe after planning: {exc}"
        ) from exc

    planned_source_hashes = {a.relative_path: a.source_sha256 for a in plan.actions}
    if live_source_hashes != planned_source_hashes:
        raise MigrationExecutionError(
            "Source skill changed between planning and execution (files added, removed, "
            "or modified). Re-run migration preview before applying."
        )

    # Re-verify destination state live against dry-run / planning snapshot
    target_exists_now = plan.target_path.exists() or plan.target_path.is_symlink()
    if target_exists_now and not overwrite:
        reason = (
            plan.conflict.reason
            if plan.conflict is not None
            else (
                f"Destination {plan.target_path} was created after planning. "
                "Refusing silent overwrite; re-run preview and pass --overwrite if intended."
            )
        )
        raise MigrationConflictError(reason)

    if plan.conflict is None and target_exists_now:
        raise MigrationConflictError(
            f"Destination {plan.target_path} appeared after dry-run planning. "
            "Re-run preview before applying."
        )

    if plan.target_fingerprint is not None and target_exists_now:
        if is_symlink_or_junction(plan.target_path):
            raise MigrationExecutionError(
                f"Target skill path is a symlink or junction: {plan.target_path}"
            )
        try:
            live_target_hashes = _snapshot_tree_hashes(plan.target_path, limits=limits)
        except Exception as exc:
            raise MigrationConflictError(
                f"Destination changed or became unsafe after planning: {exc}"
            ) from exc
        if live_target_hashes != plan.target_fingerprint:
            raise MigrationConflictError(
                f"Destination {plan.target_path} changed after planning. "
                "Re-run preview before applying."
            )

    target_root = plan.target_path.parent
    if target_root.exists() and is_symlink_or_junction(target_root):
        raise MigrationExecutionError(
            f"Target parent directory is a symlink or junction: {target_root}"
        )

    if plan.target_path.exists() and is_symlink_or_junction(plan.target_path):
        raise MigrationExecutionError(
            f"Target skill path is a symlink or junction: {plan.target_path}"
        )

    target_root.mkdir(parents=True, exist_ok=True)
    ensure_within_root(target_root, plan.target_path)

    # Step 7: Create backup when destination already exists
    backup_id: str | None = None
    if plan.target_path.exists():
        backup_manager = BackupManager(env.backups_dir, limits=limits, home_dir=env.home_dir)
        manifest = backup_manager.create_backup(
            plan.target_path,
            operation=f"migrate-{plan.source_agent.value}-to-{plan.target_agent.value}",
            skill_name=plan.skill_name,
            agent_id=plan.target_agent.value,
            notes=(
                f"Automatic backup before migrating {plan.skill_name} to {plan.target_agent.value}"
            ),
        )
        backup_id = manifest.backup_id

    # Step 8: Atomic staging write
    copier = copy_fn or _default_copy_file
    replacer = replace_fn or _default_replace
    staging_dir = Path(tempfile.mkdtemp(prefix=".cb_migrate_stage_", dir=str(target_root)))
    displaced_dir: Path | None = None
    rolled_back = False
    rollback_error: str | None = None

    try:
        verified_hashes: dict[str, str] = {}
        for action in plan.actions:
            rel_posix = validate_relative_path(action.relative_path)
            src_file = plan.source_path.joinpath(*rel_posix.parts)
            dst_file = staging_dir.joinpath(*rel_posix.parts)
            ensure_within_root(plan.source_path, src_file)
            ensure_within_root(staging_dir, dst_file)

            copier(src_file, dst_file)
            written_hash = sha256_file(dst_file)
            if written_hash != action.source_sha256:
                raise MigrationExecutionError(
                    f"Integrity verification failed during staging for {action.relative_path}."
                )
            verified_hashes[action.relative_path] = written_hash

        if plan.target_path.exists():
            displaced_dir = target_root / f".cb_migrate_old_{secrets.token_hex(4)}"
            replacer(plan.target_path, displaced_dir)

        replacer(staging_dir, plan.target_path)

        # Step 9: Post-swap integrity verification on live destination
        for action in plan.actions:
            rel_posix = validate_relative_path(action.relative_path)
            live_file = plan.target_path.joinpath(*rel_posix.parts)
            if not live_file.is_file() or sha256_file(live_file) != action.source_sha256:
                raise MigrationExecutionError(
                    f"Post-swap SHA-256 verification failed for {action.relative_path}."
                )

        if displaced_dir is not None and displaced_dir.exists():
            shutil.rmtree(displaced_dir, ignore_errors=True)

        # Step 10: Return structured result
        return MigrationResult(
            plan=plan,
            applied=True,
            dry_run=False,
            backup_id=backup_id,
            files_written=len(verified_hashes),
            verified_hashes=verified_hashes,
            rolled_back=False,
            message=(
                f"Migrated '{plan.skill_name}' from {plan.source_agent.value} to "
                f"{plan.target_agent.value} ({len(verified_hashes)} file(s) verified via SHA-256)."
            ),
        )
    except Exception as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        if displaced_dir is not None and displaced_dir.exists():
            failed_dir: Path | None = None
            try:
                if plan.target_path.exists():
                    failed_dir = target_root / f".cb_migrate_failed_{secrets.token_hex(4)}"
                    replacer(plan.target_path, failed_dir)
                replacer(displaced_dir, plan.target_path)
                rolled_back = True
                if failed_dir is not None and failed_dir.exists():
                    shutil.rmtree(failed_dir, ignore_errors=True)
            except OSError as rb_exc:
                rollback_error = (
                    f"Rollback failed ({rb_exc}); original files preserved at {displaced_dir}"
                    + (f" and backup {backup_id}" if backup_id else "")
                )
        detail = f"rolled_back={rolled_back}"
        if rollback_error:
            detail += f"; {rollback_error}"
        if isinstance(exc, MigrationExecutionError):
            raise MigrationExecutionError(f"{exc.message} ({detail})") from exc
        raise MigrationExecutionError(f"Migration write failed ({detail}): {exc}") from exc
