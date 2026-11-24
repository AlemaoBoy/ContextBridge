"""Integration tests for read-only `contextbridge doctor` diagnostics."""

from __future__ import annotations

import pytest
from tests.conftest import make_skill

from contextbridge.backup.manager import BackupManager
from contextbridge.core.doctor import run_doctor
from contextbridge.core.paths import EnvironmentContext
from contextbridge.models.doctor import CheckStatus


@pytest.mark.integration
def test_doctor_identifies_invalid_skills_duplicates_compat_and_corrupted_backups(
    isolated_env: EnvironmentContext,
) -> None:
    # 1. Duplicate skill across two distinct directories
    make_skill(isolated_env.project_dir / ".claude" / "skills", "dup-skill")
    make_skill(isolated_env.project_dir / ".cursor" / "skills", "dup-skill")

    # 2. Invalid skill (missing description)
    bad = isolated_env.project_dir / ".claude" / "skills" / "broken-skill"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: broken-skill\n---\n", encoding="utf-8")

    # 3. Shared directory skill using Claude-specific frontmatter
    make_skill(
        isolated_env.project_dir / ".agents" / "skills",
        "claude-extra",
        extra_frontmatter="context: fork",
    )

    # 4. Corrupted backup in backups dir
    manager = BackupManager(isolated_env.backups_dir)
    manifest = manager.create_backup(
        isolated_env.project_dir / ".claude" / "skills" / "dup-skill",
        operation="test",
        skill_name="dup-skill",
    )
    (isolated_env.backups_dir / manifest.backup_id / "payload" / "SKILL.md").write_text(
        "tampered", encoding="utf-8"
    )

    report = run_doctor(isolated_env)
    names = {c.name: c for c in report.checks}

    assert names["invalid-skill:broken-skill"].status == CheckStatus.ERROR
    assert names["duplicate-skill:dup-skill"].status == CheckStatus.WARN
    assert names["frontmatter-compat:claude-extra"].status == CheckStatus.WARN
    assert names[f"backup-integrity:{manifest.backup_id}"].status == CheckStatus.ERROR
    assert report.error_count >= 2


@pytest.mark.integration
def test_doctor_is_strictly_read_only(isolated_env: EnvironmentContext) -> None:
    report = run_doctor(isolated_env)
    assert report.error_count == 0
    assert not isolated_env.contextbridge_home.exists()
    assert not (isolated_env.home_dir / ".claude").exists()
    assert not (isolated_env.project_dir / ".agents").exists()
