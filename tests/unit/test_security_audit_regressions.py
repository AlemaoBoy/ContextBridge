"""Security and functional regression tests from the v0.1.0 pre-release audit."""

from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.conftest import make_skill

from contextbridge.adapters.registry import get_adapter
from contextbridge.backup.manager import BackupManager
from contextbridge.core.exceptions import (
    ArchiveSecurityError,
    BackupError,
    BackupIntegrityError,
    MigrationConflictError,
    MigrationExecutionError,
    PathTraversalError,
    SecurityViolationError,
)
from contextbridge.core.paths import EnvironmentContext
from contextbridge.models.agent import AgentId, DetectionStatus
from contextbridge.models.skill import ValidationStatus
from contextbridge.security.archive import inspect_zip_safety
from contextbridge.security.paths import (
    validate_authorized_restore_target,
    validate_relative_path,
)
from contextbridge.security.secrets import is_sensitive_filename
from contextbridge.skills.exporter import export_skill_zip
from contextbridge.skills.migrator import execute_migration, plan_migration
from contextbridge.skills.validator import validate_skill


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_path",
    [
        "SKILL.md:hidden.exe",
        "references/doc.md:Zone.Identifier",
        "scripts/run.py:$DATA",
        "references/guide.md.",
        "references/guide.md ",
        "scripts/bad\x07name.py",
        ".env.",
    ],
)
def test_validate_relative_path_blocks_ntfs_ads_and_win32_tricks(bad_path: str) -> None:
    with pytest.raises(PathTraversalError):
        validate_relative_path(bad_path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        (".ssh/id_ed25519", True),
        (".aws/credentials", True),
        (".gnupg/secring.gpg", True),
        (".kube/config", True),
        (".git-credentials", True),
        (".pgpass", True),
        (".vault-token", True),
        (".hidden_api_token", True),
        (".my_secret_file", True),
        ("oauth_creds.json", True),
        (".env.example", False),
        (".env.template", False),
        (".github/workflows/ci.yml", False),
    ],
)
def test_hidden_credential_files_and_dirs_detected(rel_path: str, expected: bool) -> None:
    assert is_sensitive_filename(rel_path) is expected


@pytest.mark.unit
def test_zip_case_insensitive_collision_blocked(tmp_path: Path) -> None:
    zpath = tmp_path / "collision.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("my-skill/SKILL.md", "---\nname: my-skill\ndescription: a\n---\n")
        zf.writestr("my-skill/skill.md", "---\nname: my-skill\ndescription: b\n---\n")

    with pytest.raises(ArchiveSecurityError, match="case-insensitive"):
        inspect_zip_safety(zpath)


@pytest.mark.unit
def test_parser_rejects_duplicate_yaml_keys_and_deep_nesting(tmp_path: Path) -> None:
    dup_dir = tmp_path / "dup-key"
    dup_dir.mkdir()
    (dup_dir / "SKILL.md").write_text(
        "---\n"
        "name: dup-key\n"
        "name: spoofed-key\n"
        "description: Duplicate key test skill.\n"
        "---\n\n# Body\n",
        encoding="utf-8",
    )
    dup_report = validate_skill(dup_dir)
    assert dup_report.status == ValidationStatus.INVALID
    assert any(i.code == "YAML_DUPLICATE_KEY" for i in dup_report.errors)

    deep_dir = tmp_path / "deep-yaml"
    deep_dir.mkdir()
    nested = "[" * 18 + "1" + "]" * 18
    (deep_dir / "SKILL.md").write_text(
        "---\n"
        "name: deep-yaml\n"
        "description: Deeply nested YAML test.\n"
        f"metadata:\n  deep: {nested}\n"
        "---\n\n# Body\n",
        encoding="utf-8",
    )
    deep_report = validate_skill(deep_dir)
    assert deep_report.status == ValidationStatus.INVALID
    assert any(i.code == "YAML_TOO_DEEP" for i in deep_report.errors)


@pytest.mark.unit
def test_tampered_backup_manifest_malicious_destination_blocked(tmp_path: Path) -> None:
    backups_root = tmp_path / "backups"
    allowed_root = tmp_path / "project" / ".claude" / "skills"
    source = make_skill(allowed_root, "safe-skill")

    manager = BackupManager(
        backups_root,
        allowed_restore_roots=[allowed_root],
        home_dir=tmp_path / "home",
    )
    manifest = manager.create_backup(source, operation="test", skill_name="safe-skill")

    # Tamper manifest source_path to point outside authorized skill roots
    manifest_file = backups_root / manifest.backup_id / "manifest.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    outside_target = tmp_path / "outside_evil_dir"
    data["source_path"] = str(outside_target)
    manifest_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PathTraversalError, match="outside authorized"):
        manager.plan_restore(manifest.backup_id)

    with pytest.raises(PathTraversalError, match="outside authorized"):
        manager.restore_backup(manifest.backup_id, dry_run=False)
    assert not outside_target.exists()

    # Tamper manifest source_path to sensitive ~/.ssh directory
    ssh_target = tmp_path / "home" / ".ssh" / "keys"
    data["source_path"] = str(ssh_target)
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SecurityViolationError, match="sensitive"):
        manager.restore_backup(manifest.backup_id, dry_run=False)

    # Even explicit --target cannot restore into backups_root or ADS or .ssh
    with pytest.raises(PathTraversalError, match="backups directory"):
        validate_authorized_restore_target(backups_root / "nested", backups_root=backups_root)
    with pytest.raises(PathTraversalError, match="Alternate Data Stream"):
        validate_authorized_restore_target(
            Path(str(tmp_path / "ok") + ":stream"), backups_root=backups_root
        )
    with pytest.raises(SecurityViolationError, match="sensitive"):
        validate_authorized_restore_target(ssh_target, backups_root=backups_root)


@pytest.mark.unit
def test_tampered_manifest_backup_id_mismatch_blocked(tmp_path: Path) -> None:
    backups_root = tmp_path / "backups"
    source = make_skill(tmp_path / "skills", "id-skill")
    manager = BackupManager(backups_root)
    manifest = manager.create_backup(source, operation="test", skill_name="id-skill")

    manifest_file = backups_root / manifest.backup_id / "manifest.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["backup_id"] = "different-id"
    manifest_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(BackupIntegrityError, match="does not match"):
        manager.get_manifest(manifest.backup_id)


@pytest.mark.unit
def test_migration_detects_source_mutation_and_target_post_dry_run_mutation(
    isolated_env: EnvironmentContext,
) -> None:
    source = make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "toctou-skill",
        extra_files={"references/doc.md": "v1"},
    )
    plan = plan_migration(
        "toctou-skill",
        source_agent="claude",
        target_agent="codex",
        env=isolated_env,
    )

    # 1. Source modified between plan and apply -> blocked!
    (source / "references" / "injected.md").write_text("injected", encoding="utf-8")
    with pytest.raises(MigrationExecutionError, match="Source skill changed"):
        execute_migration(plan, env=isolated_env, dry_run=False)

    # Restore source to clean state and re-plan
    (source / "references" / "injected.md").unlink()
    plan2 = plan_migration(
        "toctou-skill",
        source_agent="claude",
        target_agent="codex",
        env=isolated_env,
    )
    assert plan2.conflict is None

    # 2. Target unexpectedly created AFTER dry-run planning -> refuses silent overwrite!
    make_skill(isolated_env.project_dir / ".agents" / "skills", "toctou-skill")
    with pytest.raises(MigrationConflictError):
        execute_migration(plan2, env=isolated_env, dry_run=False, overwrite=False)
    with pytest.raises(MigrationConflictError, match="appeared after"):
        execute_migration(plan2, env=isolated_env, dry_run=False, overwrite=True)

    # 3. Target existed at plan time, then mutated before apply -> blocked!
    plan3 = plan_migration(
        "toctou-skill",
        source_agent="claude",
        target_agent="codex",
        env=isolated_env,
    )
    assert plan3.conflict is not None
    (isolated_env.project_dir / ".agents" / "skills" / "toctou-skill" / "SKILL.md").write_text(
        "changed-externally", encoding="utf-8"
    )
    with pytest.raises(MigrationConflictError, match="changed after planning"):
        execute_migration(plan3, env=isolated_env, dry_run=False, overwrite=True)


@pytest.mark.unit
def test_migration_and_restore_rollback_failure_reports_recovery_paths(
    isolated_env: EnvironmentContext,
) -> None:
    make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "rb-skill",
        extra_files={"references/a.md": "new"},
    )
    make_skill(
        isolated_env.project_dir / ".agents" / "skills",
        "rb-skill",
        extra_files={"references/a.md": "old"},
    )
    plan = plan_migration("rb-skill", source_agent="claude", target_agent="codex", env=isolated_env)

    replace_calls = 0

    def fail_swap_and_rollback(src: Path, dst: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            # Displace old target succeeds
            os.replace(src, dst)
        else:
            # Both staging->target AND rollback displaced->target fail!
            raise OSError("Simulated lock during swap and rollback")

    with pytest.raises(MigrationExecutionError) as mig_exc:
        execute_migration(
            plan,
            env=isolated_env,
            dry_run=False,
            overwrite=True,
            replace_fn=fail_swap_and_rollback,
        )
    msg = mig_exc.value.message
    assert "rolled_back=False" in msg
    assert "Rollback failed" in msg
    assert "and backup " in msg

    # Test same explicit rollback error reporting on BackupManager.restore_backup
    manager = BackupManager(isolated_env.backups_dir)
    manifests = manager.list_backups()
    assert manifests
    restore_target = isolated_env.project_dir / ".claude" / "skills" / "rb-skill"
    r_calls = 0

    def fail_restore_swap_and_rollback(src: Path, dst: Path) -> None:
        nonlocal r_calls
        r_calls += 1
        if r_calls == 1:
            os.replace(src, dst)
        else:
            raise OSError("Simulated lock on restore")

    with pytest.raises(BackupError) as rst_exc:
        manager.restore_backup(
            manifests[0].backup_id,
            dry_run=False,
            target_override=restore_target,
            replace_fn=fail_restore_swap_and_rollback,
        )
    assert "Rollback failed" in rst_exc.value.message
    assert "pre-restore backup" in rst_exc.value.message


@pytest.mark.unit
def test_export_prevents_silent_overwrite_by_default(fixtures_dir: Path, tmp_path: Path) -> None:
    out_zip = tmp_path / "existing.zip"
    export_skill_zip(fixtures_dir / "valid-skill", output_path=out_zip)

    with pytest.raises(MigrationConflictError, match="already exists"):
        export_skill_zip(fixtures_dir / "valid-skill", output_path=out_zip, overwrite=False)

    # Explicit overwrite succeeds
    res = export_skill_zip(fixtures_dir / "valid-skill", output_path=out_zip, overwrite=True)
    assert res.output_path == out_zip.resolve()


@pytest.mark.unit
def test_adapter_detection_avoids_shared_dir_and_antigravity_false_positives(
    make_env: Callable[..., EnvironmentContext],
) -> None:
    env = make_env(platform_name="win32", env_vars={"LOCALAPPDATA": ""})
    # Shared ~/.agents/skills + Antigravity-only folders inside ~/.gemini
    make_skill(env.home_dir / ".agents" / "skills", "shared-one")
    (env.home_dir / ".gemini" / "antigravity").mkdir(parents=True)
    (env.home_dir / ".gemini" / "config").mkdir(parents=True)

    # Cursor has no .cursor dir and no binary -> MUST be NOT_FOUND (not false config_only)
    cursor = get_adapter(AgentId.CURSOR).detect(env)
    assert cursor.status == DetectionStatus.NOT_FOUND

    # Gemini CLI has no Gemini CLI artifacts inside ~/.gemini -> MUST be NOT_FOUND
    gemini = get_adapter(AgentId.GEMINI_CLI).detect(env)
    assert gemini.status == DetectionStatus.NOT_FOUND

    # Antigravity has ~/.gemini/antigravity and ~/.gemini/config -> CONFIG_ONLY
    agy = get_adapter(AgentId.ANTIGRAVITY).detect(env)
    assert agy.status == DetectionStatus.CONFIG_ONLY

    # Adding Windows Antigravity.exe switches status to INSTALLED_AND_CONFIGURED
    local_app = env.home_dir / "AppData" / "Local"
    agy_exe = local_app / "Programs" / "Antigravity" / "Antigravity.exe"
    agy_exe.parent.mkdir(parents=True)
    agy_exe.write_bytes(b"")
    env_with_app = make_env(
        platform_name="win32",
        env_vars={"LOCALAPPDATA": str(local_app)},
    )
    agy_installed = get_adapter(AgentId.ANTIGRAVITY).detect(env_with_app)
    assert agy_installed.status == DetectionStatus.INSTALLED_AND_CONFIGURED


@pytest.mark.unit
def test_restore_target_cannot_overwrite_skills_root_ancestor_or_non_skill_dir(
    tmp_path: Path,
) -> None:
    backups_root = tmp_path / "backups"
    project_dir = tmp_path / "project"
    skills_root = project_dir / ".claude" / "skills"
    source = make_skill(skills_root, "my-skill")
    make_skill(skills_root, "other-skill")

    manager = BackupManager(
        backups_root,
        allowed_restore_roots=[skills_root],
        home_dir=tmp_path / "home",
    )
    manifest = manager.create_backup(source, operation="test", skill_name="my-skill")

    # 1. --target pointing at skills root itself MUST be refused
    with pytest.raises(PathTraversalError, match="skills root directory"):
        manager.restore_backup(manifest.backup_id, dry_run=False, target_override=skills_root)

    # 2. --target pointing at ancestor of skills root (.claude or project_dir) MUST be refused
    with pytest.raises(PathTraversalError, match="ancestor directory"):
        manager.restore_backup(
            manifest.backup_id, dry_run=False, target_override=project_dir / ".claude"
        )
    with pytest.raises(PathTraversalError, match="ancestor directory"):
        manager.restore_backup(manifest.backup_id, dry_run=False, target_override=project_dir)

    # 3. --target pointing at an existing non-empty non-skill directory MUST be refused
    user_docs = tmp_path / "user_docs"
    user_docs.mkdir()
    (user_docs / "important.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(SecurityViolationError, match="non-empty non-skill"):
        manager.restore_backup(manifest.backup_id, dry_run=False, target_override=user_docs)
    assert (user_docs / "important.txt").read_text(encoding="utf-8") == "do not overwrite"

    # 4. Tampered manifest pointing to skills_root itself or nested subpath MUST be refused
    manifest_file = backups_root / manifest.backup_id / "manifest.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["source_path"] = str(skills_root)
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PathTraversalError, match="skills root directory"):
        manager.plan_restore(manifest.backup_id)

    data["source_path"] = str(skills_root / "nested" / "deep")
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PathTraversalError, match="outside authorized"):
        manager.plan_restore(manifest.backup_id)

    # Verify sibling skill inside skills_root remained completely untouched
    assert (skills_root / "other-skill" / "SKILL.md").is_file()


@pytest.mark.unit
def test_rollback_failure_preserves_surviving_files_without_deletion(
    isolated_env: EnvironmentContext,
) -> None:
    make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "preserve-skill",
        extra_files={"references/data.md": "new-v2"},
    )
    target = make_skill(
        isolated_env.project_dir / ".agents" / "skills",
        "preserve-skill",
        extra_files={"references/data.md": "original-v1"},
    )
    plan = plan_migration(
        "preserve-skill", source_agent="claude", target_agent="codex", env=isolated_env
    )

    calls = 0

    def fail_on_rollback_after_swap(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            # Displace original target
            os.replace(src, dst)
        elif calls == 2:
            # Swap staging -> target, then corrupt one file so post-swap check fails
            os.replace(src, dst)
            (dst / "references" / "data.md").write_text("corrupted-post-swap", encoding="utf-8")
        elif calls == 3:
            # Moving failed target aside succeeds
            os.replace(src, dst)
        else:
            # Final rollback move displaced -> target fails!
            raise OSError("Simulated lock preventing rollback rename")

    with pytest.raises(MigrationExecutionError) as exc_info:
        execute_migration(
            plan,
            env=isolated_env,
            dry_run=False,
            overwrite=True,
            replace_fn=fail_on_rollback_after_swap,
        )

    assert "Rollback failed" in exc_info.value.message
    # Both displaced_dir (original-v1) AND backup MUST survive intact on disk
    displaced_candidates = list(target.parent.glob(".cb_migrate_old_*"))
    assert len(displaced_candidates) == 1
    assert (displaced_candidates[0] / "references" / "data.md").read_text(encoding="utf-8") == (
        "original-v1"
    )
    assert len(BackupManager(isolated_env.backups_dir).list_backups()) == 1
