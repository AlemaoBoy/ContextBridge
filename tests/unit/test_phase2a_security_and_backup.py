"""Phase 2A hardening tests for security/ and backup/ real error & edge branches."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.conftest import make_skill

from contextbridge.backup.manager import BackupManager
from contextbridge.core.exceptions import (
    ArchiveSecurityError,
    BackupError,
    BackupIntegrityError,
    FileSizeLimitError,
    PathTraversalError,
    SecurityViolationError,
    UnsafeSymlinkError,
)
from contextbridge.security.archive import inspect_zip_safety, safe_extract_zip
from contextbridge.security.audit import audit_skill_file
from contextbridge.security.limits import SecurityLimits
from contextbridge.security.paths import (
    collect_safe_skill_files,
    ensure_no_symlink_in_ancestors,
    validate_authorized_restore_target,
)
from contextbridge.security.secrets import is_sensitive_filename, scan_file_for_secrets

# --- security/paths.py ---


@pytest.mark.unit
def test_ensure_no_symlink_in_ancestors_root_symlink_and_outside_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")

    with pytest.raises(PathTraversalError, match="not inside"):
        ensure_no_symlink_in_ancestors(root, outside)

    monkeypatch.setattr("contextbridge.security.paths.is_symlink_or_junction", lambda p: p == root)
    with pytest.raises(UnsafeSymlinkError, match="Root directory itself"):
        ensure_no_symlink_in_ancestors(root, root / "a.txt")


@pytest.mark.unit
def test_validate_authorized_restore_target_system_and_edge_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backups_root = tmp_path / "backups"
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    # Null byte rejected
    with pytest.raises(PathTraversalError, match="null byte"):
        validate_authorized_restore_target(Path("bad\x00path"), backups_root=backups_root)

    # Filesystem root rejected
    fs_root = Path(tmp_path.anchor)
    with pytest.raises(PathTraversalError, match="filesystem root"):
        validate_authorized_restore_target(fs_root, backups_root=backups_root)

    # Home root rejected
    with pytest.raises(PathTraversalError, match="home directory"):
        validate_authorized_restore_target(home_dir, backups_root=backups_root, home_dir=home_dir)

    # Symlink target rejected
    sym_target = tmp_path / "sym_target"
    sym_target.mkdir()
    monkeypatch.setattr(
        "contextbridge.security.paths.is_symlink_or_junction",
        lambda p: Path(p) == sym_target,
    )
    with pytest.raises(UnsafeSymlinkError, match="symlink or junction"):
        validate_authorized_restore_target(sym_target, backups_root=backups_root)

    # Protected OS prefixes (/etc on POSIX or %SYSTEMROOT% on Windows)
    monkeypatch.setattr("contextbridge.security.paths.is_symlink_or_junction", lambda _p: False)
    if os.name == "nt":
        win_dir = Path(os.environ.get("SYSTEMROOT", "C:\\Windows"))
        with pytest.raises(SecurityViolationError, match="protected Windows directory"):
            validate_authorized_restore_target(win_dir / "TempSkill", backups_root=backups_root)
    else:
        with pytest.raises(SecurityViolationError, match="protected OS directory"):
            validate_authorized_restore_target(
                Path("/etc/contextbridge"), backups_root=backups_root
            )


@pytest.mark.unit
def test_collect_safe_skill_files_missing_symlink_depth_total_and_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Missing root
    with pytest.raises(PathTraversalError, match="does not exist"):
        collect_safe_skill_files(tmp_path / "missing")

    # Symlink root
    sym_root = tmp_path / "sym_root"
    sym_root.mkdir()
    monkeypatch.setattr(
        "contextbridge.security.paths.is_symlink_or_junction", lambda p: Path(p) == sym_root
    )
    with pytest.raises(UnsafeSymlinkError, match="Skill root cannot be a symlink"):
        collect_safe_skill_files(sym_root)
    monkeypatch.setattr("contextbridge.security.paths.is_symlink_or_junction", lambda _p: False)

    # Max nesting depth exceeded
    deep = tmp_path / "deep"
    nested = deep / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(FileSizeLimitError, match="nesting depth"):
        collect_safe_skill_files(deep, limits=SecurityLimits(max_directory_depth=2))

    # Max total bytes exceeded (each single file under single limit)
    total_dir = tmp_path / "total"
    total_dir.mkdir()
    (total_dir / "a.txt").write_text("a" * 60, encoding="utf-8")
    (total_dir / "b.txt").write_text("b" * 60, encoding="utf-8")
    with pytest.raises(FileSizeLimitError, match="total size"):
        collect_safe_skill_files(
            total_dir,
            limits=SecurityLimits(max_single_file_bytes=100, max_total_skill_bytes=100),
        )

    # Case-insensitive collision simulated deterministically across OSes
    col_dir = tmp_path / "collision"
    col_dir.mkdir()
    f1 = col_dir / "Guide.md"
    f1.write_text("one", encoding="utf-8")

    class _FakeEntry:
        def __init__(self, name: str, path: Path) -> None:
            self.name = name
            self.path = str(path)

        def is_dir(self, *, follow_symlinks: bool = False) -> bool:
            return False

        def is_file(self, *, follow_symlinks: bool = False) -> bool:
            return True

        def stat(self, *, follow_symlinks: bool = False) -> os.stat_result:
            return f1.stat()

    fake_entries = [_FakeEntry("Guide.md", f1), _FakeEntry("guide.md", col_dir / "guide.md")]
    monkeypatch.setattr("contextbridge.security.paths.os.scandir", lambda _d: fake_entries)
    monkeypatch.setattr(
        "contextbridge.security.paths.ensure_within_root",
        lambda root, cand: root / Path(cand).name,
    )
    with pytest.raises(PathTraversalError, match="Case-insensitive filename collision"):
        collect_safe_skill_files(col_dir)


# --- security/archive.py ---


@pytest.mark.unit
def test_archive_inspect_and_extract_error_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Missing archive & invalid ZIP
    with pytest.raises(ArchiveSecurityError, match="does not exist"):
        inspect_zip_safety(tmp_path / "nope.zip")

    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_text("not a zip", encoding="utf-8")
    with pytest.raises(ArchiveSecurityError, match="Invalid or corrupted"):
        inspect_zip_safety(bad_zip)

    # Entry count limit
    many_zip = tmp_path / "many.zip"
    with zipfile.ZipFile(many_zip, "w") as zf:
        zf.writestr("s/a.txt", "1")
        zf.writestr("s/b.txt", "2")
        zf.writestr("s/c.txt", "3")
    with pytest.raises(ArchiveSecurityError, match="entry count"):
        inspect_zip_safety(many_zip, limits=SecurityLimits(max_zip_entries=2))

    # Root/empty member entry ("/")
    root_zip = tmp_path / "root.zip"
    with zipfile.ZipFile(root_zip, "w") as zf:
        zf.writestr("/", "bad")
    with pytest.raises(ArchiveSecurityError, match="empty or root"):
        inspect_zip_safety(root_zip)

    # Single member size limit + pure directory entry skipped cleanly
    dir_and_big = tmp_path / "big.zip"
    with zipfile.ZipFile(dir_and_big, "w") as zf:
        zf.writestr("my-skill/", "")
        zf.writestr("my-skill/big.txt", b"x" * 200)
    with pytest.raises(ArchiveSecurityError, match="uncompressed size"):
        inspect_zip_safety(dir_and_big, limits=SecurityLimits(max_single_file_bytes=100))

    # Pure directory entry in safe_extract_zip succeeds
    clean_dir_zip = tmp_path / "dir_clean.zip"
    with zipfile.ZipFile(clean_dir_zip, "w") as zf:
        zf.writestr("my-skill/", "")
        zf.writestr("my-skill/SKILL.md", "ok")
    extracted = safe_extract_zip(clean_dir_zip, tmp_path / "extract_ok")
    assert len(extracted) == 1

    # Spoofed compress_size=0 with file_size>0 rejected
    spoof_zip = tmp_path / "spoof.zip"
    with zipfile.ZipFile(spoof_zip, "w") as zf:
        zf.writestr("my-skill/a.txt", "hello")

    real_infolist = zipfile.ZipFile.infolist

    def fake_zero_compress(self: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        infos = real_infolist(self)
        infos[0].compress_size = 0
        infos[0].file_size = 50
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", fake_zero_compress)
    with pytest.raises(ArchiveSecurityError, match="zero compressed size"):
        inspect_zip_safety(spoof_zip)
    monkeypatch.setattr(zipfile.ZipFile, "infolist", real_infolist)

    # Streaming read-time byte limits in safe_extract_zip (header spoofed small, payload exceeds)
    stream_zip = tmp_path / "stream.zip"
    with zipfile.ZipFile(stream_zip, "w") as zf:
        zf.writestr("my-skill/a.txt", b"a" * 80)
        zf.writestr("my-skill/b.txt", b"b" * 80)

    monkeypatch.setattr("contextbridge.security.archive.inspect_zip_safety", lambda *a, **k: [])
    with pytest.raises(ArchiveSecurityError, match="single-file limit"):
        safe_extract_zip(
            stream_zip,
            tmp_path / "ext_single",
            limits=SecurityLimits(max_single_file_bytes=50, max_zip_uncompressed_bytes=500),
        )
    with pytest.raises(ArchiveSecurityError, match="total extracted bytes"):
        safe_extract_zip(
            stream_zip,
            tmp_path / "ext_total",
            limits=SecurityLimits(max_single_file_bytes=100, max_zip_uncompressed_bytes=100),
        )


# --- security/secrets.py & audit.py ---


@pytest.mark.unit
def test_secrets_and_audit_edge_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert is_sensitive_filename("") is False

    # Binary file skipped cleanly
    bin_file = tmp_path / "img.bin"
    bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00IHDR")
    assert scan_file_for_secrets(bin_file, "assets/img.bin") == []
    assert audit_skill_file(bin_file, "assets/img.bin") == []

    # Unreadable file handled gracefully
    txt = tmp_path / "unreadable.md"
    txt.write_text("clean", encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("denied")),
    )
    assert scan_file_for_secrets(txt, "unreadable.md") == []
    assert audit_skill_file(txt, "unreadable.md") == []


# --- backup/manager.py ---


@pytest.mark.unit
def test_backup_manager_corrupted_entries_and_integrity_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backups_root = tmp_path / "backups"
    manager = BackupManager(backups_root)

    # create_backup on missing dir
    with pytest.raises(BackupError, match="non-existent"):
        manager.create_backup(tmp_path / "nope", operation="test")

    # Create valid backup first
    src = make_skill(tmp_path / "skills", "edge-skill", extra_files={"references/a.md": "hello"})
    manifest = manager.create_backup(src, operation="test", skill_name="edge-skill")

    # Add noise in backups_root: stray file, dir without manifest, dir with corrupted manifest
    (backups_root / "stray.txt").write_text("ignore", encoding="utf-8")
    (backups_root / "no_manifest_dir").mkdir()
    bad_dir = backups_root / "bad_json_dir"
    bad_dir.mkdir()
    (bad_dir / "manifest.json").write_text("{broken json", encoding="utf-8")

    # list_backups ignores invalid entries cleanly
    listed = manager.list_backups()
    assert [m.backup_id for m in listed] == [manifest.backup_id]

    # get_manifest errors: missing, corrupted json, symlink dir, symlink manifest
    with pytest.raises(BackupError, match="Backup not found"):
        manager.get_manifest("does-not-exist")
    with pytest.raises(BackupIntegrityError, match="Corrupted backup manifest"):
        manager.get_manifest("bad_json_dir")

    bdir = backups_root / manifest.backup_id
    monkeypatch.setattr(
        "contextbridge.backup.manager.is_symlink_or_junction", lambda p: Path(p) == bdir
    )
    with pytest.raises(BackupIntegrityError, match="Backup directory is a symlink"):
        manager.get_manifest(manifest.backup_id)

    mpath = bdir / "manifest.json"
    monkeypatch.setattr(
        "contextbridge.backup.manager.is_symlink_or_junction", lambda p: Path(p) == mpath
    )
    with pytest.raises(BackupIntegrityError, match="Backup manifest is a symlink"):
        manager.get_manifest(manifest.backup_id)
    monkeypatch.setattr("contextbridge.backup.manager.is_symlink_or_junction", lambda _p: False)

    # verify_backup on missing manifest
    missing_ver = manager.verify_backup("does-not-exist")
    assert missing_ver.valid is False

    # verify_backup detects unexpected extra file in payload
    extra_file = bdir / "payload" / "extra.txt"
    extra_file.write_text("sneaky", encoding="utf-8")
    ver_extra = manager.verify_backup(manifest.backup_id)
    assert ver_extra.valid is False
    assert any("Unexpected file" in e for e in ver_extra.errors)
    extra_file.unlink()

    # verify_backup detects missing payload file, total_files mismatch, unsafe path, collision
    orig_manifest_text = mpath.read_text(encoding="utf-8")
    data = json.loads(orig_manifest_text)
    data["total_files"] = 99
    data["files"].append({"relative_path": "../escape.txt", "size_bytes": 1, "sha256": "0" * 64})
    data["files"].append({"relative_path": "skill.md", "size_bytes": 1, "sha256": "0" * 64})
    data["files"].append({"relative_path": "missing.txt", "size_bytes": 1, "sha256": "0" * 64})
    mpath.write_text(json.dumps(data), encoding="utf-8")
    ver_multi = manager.verify_backup(manifest.backup_id)
    assert ver_multi.valid is False
    joined = "\n".join(ver_multi.errors)
    assert "total_files" in joined
    assert "Unsafe path" in joined
    assert "Case-insensitive collision" in joined
    assert "Missing payload file" in joined

    # Restore valid manifest & test missing payload dir
    mpath.write_text(orig_manifest_text, encoding="utf-8")
    shutil.move(str(bdir / "payload"), str(tmp_path / "moved_payload"))
    ver_nopayload = manager.verify_backup(manifest.backup_id)
    assert ver_nopayload.valid is False
    assert any("missing or unsafe" in e for e in ver_nopayload.errors)
    shutil.move(str(tmp_path / "moved_payload"), str(bdir / "payload"))

    # create_backup hash mismatch when copy_fn alters bytes
    def corrupting_copy(_src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("CORRUPTED_BYTES", encoding="utf-8")

    with pytest.raises(BackupIntegrityError, match="Hash mismatch"):
        manager.create_backup(src, operation="test", copy_fn=corrupting_copy)

    # create_backup post-write verification failure
    monkeypatch.setattr(
        manager,
        "verify_backup",
        lambda bid: SimpleNamespace(valid=False, errors=["forced failure"]),
    )
    with pytest.raises(BackupIntegrityError, match="failed post-write verification"):
        manager.create_backup(src, operation="test")


@pytest.mark.unit
def test_backup_restore_fresh_target_hash_mismatch_and_clean_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backups_root = tmp_path / "backups"
    manager = BackupManager(backups_root)
    src = make_skill(tmp_path / "skills", "rst-skill", extra_files={"references/v.md": "v1"})
    manifest = manager.create_backup(src, operation="test", skill_name="rst-skill")

    # 1. Restore to brand-new target (no pre-restore backup created)
    fresh_target = tmp_path / "fresh" / "rst-skill"
    res = manager.restore_backup(manifest.backup_id, dry_run=False, target_override=fresh_target)
    assert res.applied is True
    assert res.pre_restore_backup_id is None
    assert (fresh_target / "references" / "v.md").read_text(encoding="utf-8") == "v1"

    # 2. Restore with staging copy corruption triggers BackupIntegrityError
    def corrupt_copy(_s: Path, d: Path) -> None:
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text("bad", encoding="utf-8")

    with pytest.raises(BackupError, match="SHA-256 verification"):
        manager.restore_backup(
            manifest.backup_id,
            dry_run=False,
            target_override=tmp_path / "fresh2" / "rst-skill",
            copy_fn=corrupt_copy,
        )

    # 3. Restore post-swap verification failure triggers clean rollback (removes .cb_restore_failed)
    (fresh_target / "references" / "v.md").write_text("v2-live", encoding="utf-8")
    swap_step = 0

    def corrupt_after_swap(s: Path, d: Path) -> None:
        nonlocal swap_step
        swap_step += 1
        os.replace(s, d)
        if swap_step == 2:
            # Corrupt newly swapped target so post-swap SHA-256 check fails
            (d / "references" / "v.md").write_text("corrupted-post-swap", encoding="utf-8")

    with pytest.raises(BackupError, match="rolled_back=True"):
        manager.restore_backup(
            manifest.backup_id,
            dry_run=False,
            target_override=fresh_target,
            replace_fn=corrupt_after_swap,
        )
    # Original v2-live restored cleanly & no .cb_restore_failed left behind
    assert (fresh_target / "references" / "v.md").read_text(encoding="utf-8") == "v2-live"
    assert list(fresh_target.parent.glob(".cb_restore_failed_*")) == []

    # 4. Symlinked restore parent blocked
    monkeypatch.setattr(
        "contextbridge.backup.manager.is_symlink_or_junction",
        lambda p: Path(p) == fresh_target.parent,
    )
    with pytest.raises(BackupError, match="parent directory is a symlink"):
        manager.restore_backup(manifest.backup_id, dry_run=False, target_override=fresh_target)
