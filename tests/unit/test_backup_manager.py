"""Unit tests for BackupManager: manifests, SHA-256 verification, restore, and rollback."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.conftest import make_skill

from contextbridge.backup.manager import BackupManager
from contextbridge.core.exceptions import (
    BackupError,
    BackupIntegrityError,
    PathTraversalError,
)


@pytest.mark.unit
def test_backup_list_is_read_only_when_store_missing(tmp_path: Path) -> None:
    backups_root = tmp_path / "nonexistent_backups"
    manager = BackupManager(backups_root)
    assert manager.list_backups() == []
    assert not backups_root.exists()


@pytest.mark.unit
def test_backup_create_verify_and_restore_with_preview(tmp_path: Path) -> None:
    backups_root = tmp_path / "backups"
    manager = BackupManager(backups_root)
    source = make_skill(
        tmp_path / "skills",
        "alpha-skill",
        extra_files={"references/info.md": "original-content"},
    )

    manifest = manager.create_backup(
        source,
        operation="unit-test",
        skill_name="alpha-skill",
        agent_id="claude",
    )
    assert manifest.total_files == 2
    assert manager.verify_backup(manifest.backup_id).valid is True
    assert len(manager.list_backups()) == 1

    # Mutate live directory
    (source / "references" / "info.md").write_text("modified-content", encoding="utf-8")

    # Dry-run restore must NOT change live directory
    dry_result = manager.restore_backup(manifest.backup_id, dry_run=True)
    assert dry_result.dry_run is True
    assert dry_result.applied is False
    assert (source / "references" / "info.md").read_text(encoding="utf-8") == "modified-content"

    # Apply restore
    applied = manager.restore_backup(manifest.backup_id, dry_run=False)
    assert applied.applied is True
    assert applied.verified is True
    assert applied.pre_restore_backup_id is not None
    assert (source / "references" / "info.md").read_text(encoding="utf-8") == "original-content"


@pytest.mark.unit
def test_backup_detects_tampering_and_blocks_restore(tmp_path: Path) -> None:
    backups_root = tmp_path / "backups"
    manager = BackupManager(backups_root)
    source = make_skill(tmp_path / "skills", "tamper-skill")

    manifest = manager.create_backup(source, operation="test", skill_name="tamper-skill")
    payload_md = backups_root / manifest.backup_id / "payload" / "SKILL.md"
    payload_md.write_text("CORRUPTED!", encoding="utf-8")

    verification = manager.verify_backup(manifest.backup_id)
    assert verification.valid is False
    assert any("SHA-256 mismatch" in e for e in verification.errors)

    with pytest.raises(BackupIntegrityError):
        manager.restore_backup(manifest.backup_id, dry_run=False)


@pytest.mark.unit
def test_backup_rejects_traversal_in_backup_id(tmp_path: Path) -> None:
    manager = BackupManager(tmp_path / "backups")
    with pytest.raises(PathTraversalError):
        manager.get_manifest("../escape")


@pytest.mark.unit
def test_backup_create_cleans_up_partial_state_on_write_failure(tmp_path: Path) -> None:
    backups_root = tmp_path / "backups"
    manager = BackupManager(backups_root)
    source = make_skill(
        tmp_path / "skills",
        "partial-skill",
        extra_files={"references/a.md": "aaa", "references/b.md": "bbb"},
    )

    calls = 0

    def failing_copy(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        if calls == 2:
            raise OSError("Simulated disk full error")
        shutil.copy2(src, dst)

    with pytest.raises(BackupError, match="Simulated disk full"):
        manager.create_backup(source, operation="test", copy_fn=failing_copy)

    # No partial backup directories or staging folders left behind
    assert manager.list_backups() == []
    assert list(backups_root.iterdir()) == []


@pytest.mark.unit
def test_restore_recovers_cleanly_on_mid_write_failure(tmp_path: Path) -> None:
    backups_root = tmp_path / "backups"
    manager = BackupManager(backups_root)
    source = make_skill(
        tmp_path / "skills",
        "recover-skill",
        extra_files={"references/a.md": "v1", "references/b.md": "v1"},
    )
    manifest = manager.create_backup(source, operation="test", skill_name="recover-skill")

    # Update live files to v2
    (source / "references" / "a.md").write_text("v2", encoding="utf-8")
    (source / "references" / "b.md").write_text("v2", encoding="utf-8")

    calls = 0

    def failing_restore_copy(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        if calls == 2:
            raise OSError("Simulated I/O interruption during restore")
        shutil.copy2(src, dst)

    with pytest.raises(BackupError, match="Simulated I/O interruption"):
        manager.restore_backup(manifest.backup_id, dry_run=False, copy_fn=failing_restore_copy)

    # Live directory must remain intact at v2 without partial v1/v2 corruption
    assert (source / "references" / "a.md").read_text(encoding="utf-8") == "v2"
    assert (source / "references" / "b.md").read_text(encoding="utf-8") == "v2"
