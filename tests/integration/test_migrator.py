"""Integration tests for the 10-step skill migration workflow."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.conftest import make_skill

from contextbridge.backup.manager import BackupManager
from contextbridge.core.exceptions import (
    MigrationConflictError,
    MigrationExecutionError,
    SecretExposureError,
)
from contextbridge.core.paths import EnvironmentContext
from contextbridge.skills.migrator import execute_migration, plan_migration


@pytest.mark.integration
def test_migration_dry_run_default_then_apply_and_verify(
    isolated_env: EnvironmentContext,
) -> None:
    make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "deploy-helper",
        extra_frontmatter="context: fork\nagent: reviewer",
        extra_files={
            "references/runbook.md": "# Runbook\nStep 1\n",
            "scripts/check.py": "print('ok')\n",
        },
    )

    plan = plan_migration(
        "deploy-helper",
        source_agent="claude",
        target_agent="codex",
        env=isolated_env,
    )
    assert plan.can_apply is True
    assert plan.conflict is None
    assert len(plan.actions) == 3
    assert any("context" in w for w in plan.compatibility_warnings)

    # Dry-run must NOT create destination files
    dry = execute_migration(plan, env=isolated_env, dry_run=True)
    assert dry.dry_run is True
    assert dry.applied is False
    assert not plan.target_path.exists()

    # Apply copies and verifies SHA-256
    applied = execute_migration(plan, env=isolated_env, dry_run=False)
    assert applied.applied is True
    assert applied.files_written == 3
    assert (plan.target_path / "SKILL.md").is_file()
    assert (plan.target_path / "references" / "runbook.md").read_text(encoding="utf-8") == (
        "# Runbook\nStep 1\n"
    )
    assert (plan.target_path / "scripts" / "check.py").is_file()


@pytest.mark.integration
def test_migration_conflict_prevents_silent_overwrite_and_creates_backup_on_overwrite(
    isolated_env: EnvironmentContext,
) -> None:
    make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "shared-workflow",
        extra_files={"references/data.md": "new-version"},
    )
    make_skill(
        isolated_env.project_dir / ".agents" / "skills",
        "shared-workflow",
        extra_files={"references/data.md": "old-version"},
    )

    plan = plan_migration(
        "shared-workflow",
        source_agent="claude",
        target_agent="codex",
        env=isolated_env,
    )
    assert plan.conflict is not None
    assert plan.requires_backup is True

    # Without overwrite=True, migration MUST refuse
    with pytest.raises(MigrationConflictError):
        execute_migration(plan, env=isolated_env, dry_run=False, overwrite=False)

    assert (
        isolated_env.project_dir
        / ".agents"
        / "skills"
        / "shared-workflow"
        / "references"
        / "data.md"
    ).read_text(encoding="utf-8") == "old-version"

    # With overwrite=True, creates verified backup first and replaces cleanly
    result = execute_migration(plan, env=isolated_env, dry_run=False, overwrite=True)
    assert result.applied is True
    assert result.backup_id is not None
    assert (
        isolated_env.project_dir
        / ".agents"
        / "skills"
        / "shared-workflow"
        / "references"
        / "data.md"
    ).read_text(encoding="utf-8") == "new-version"

    # Verify backup contains the old version and can be restored
    manager = BackupManager(isolated_env.backups_dir)
    assert manager.verify_backup(result.backup_id).valid is True
    restore_res = manager.restore_backup(result.backup_id, dry_run=False)
    assert restore_res.applied is True
    assert (
        isolated_env.project_dir
        / ".agents"
        / "skills"
        / "shared-workflow"
        / "references"
        / "data.md"
    ).read_text(encoding="utf-8") == "old-version"


@pytest.mark.integration
def test_migration_detects_same_physical_directory_as_noop(
    isolated_env: EnvironmentContext,
) -> None:
    # Both Codex and Antigravity use .agents/skills as primary project directory
    make_skill(isolated_env.project_dir / ".agents" / "skills", "common-skill")

    plan = plan_migration(
        "common-skill",
        source_agent="codex",
        target_agent="antigravity",
        env=isolated_env,
    )
    assert plan.same_physical_target is True
    assert plan.can_apply is False

    result = execute_migration(plan, env=isolated_env, dry_run=False)
    assert result.applied is False
    assert "No-op" in result.message


@pytest.mark.integration
def test_migration_write_failure_preserves_existing_target(
    isolated_env: EnvironmentContext,
) -> None:
    make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "resilient-skill",
        extra_files={"references/one.md": "new1", "references/two.md": "new2"},
    )
    target = make_skill(
        isolated_env.project_dir / ".agents" / "skills",
        "resilient-skill",
        extra_files={"references/one.md": "old1", "references/two.md": "old2"},
    )

    plan = plan_migration(
        "resilient-skill",
        source_agent="claude",
        target_agent="codex",
        env=isolated_env,
    )

    calls = 0

    def fail_on_second(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        if calls == 2:
            raise OSError("Simulated write crash")
        shutil.copy2(src, dst)

    with pytest.raises(MigrationExecutionError, match="Simulated write crash"):
        execute_migration(
            plan,
            env=isolated_env,
            dry_run=False,
            overwrite=True,
            copy_fn=fail_on_second,
        )

    # Target directory remains completely untouched at old state
    assert (target / "references" / "one.md").read_text(encoding="utf-8") == "old1"
    assert (target / "references" / "two.md").read_text(encoding="utf-8") == "old2"


@pytest.mark.integration
def test_migration_blocks_skill_with_secrets(isolated_env: EnvironmentContext) -> None:
    make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "leaky-skill",
        extra_files={".env": "SECRET_KEY=topsecret1234567890"},
    )
    plan = plan_migration(
        "leaky-skill",
        source_agent="claude",
        target_agent="codex",
        env=isolated_env,
    )
    assert plan.can_apply is False
    with pytest.raises(SecretExposureError):
        execute_migration(plan, env=isolated_env, dry_run=False)
