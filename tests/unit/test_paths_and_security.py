"""Unit tests for path resolution, traversal prevention, symlinks, and limits."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextbridge.core.exceptions import (
    FileSizeLimitError,
    PathTraversalError,
    UnsafeSymlinkError,
)
from contextbridge.security.limits import SecurityLimits
from contextbridge.security.paths import (
    collect_safe_skill_files,
    ensure_no_symlink_in_ancestors,
    ensure_within_root,
    validate_relative_path,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.txt",
        "scripts/../../etc/passwd",
        "/etc/passwd",
        "//server/share/file.txt",
        "C:/Windows/System32/cmd.exe",
        "C:\\Windows\\win.ini",
        "dir/file\x00.txt",
        "CON",
        "scripts/NUL.txt",
        "COM1",
        "LPT9.md",
        "",
        ".",
    ],
)
def test_validate_relative_path_rejects_unsafe_inputs(bad_path: str) -> None:
    with pytest.raises(PathTraversalError):
        validate_relative_path(bad_path)


@pytest.mark.unit
def test_validate_relative_path_accepts_portable_paths() -> None:
    assert validate_relative_path("./scripts/run.py").as_posix() == "scripts/run.py"
    assert validate_relative_path("references\\guide.md").as_posix() == "references/guide.md"


@pytest.mark.unit
def test_ensure_within_root_blocks_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")

    with pytest.raises(PathTraversalError):
        ensure_within_root(root, outside)

    inside = root / "ok.txt"
    inside.write_text("ok", encoding="utf-8")
    assert ensure_within_root(root, inside) == inside.resolve()


@pytest.mark.unit
def test_collect_safe_skill_files_enforces_size_and_count_limits(tmp_path: Path) -> None:
    skill = tmp_path / "heavy-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("a" * 200, encoding="utf-8")

    tight = SecurityLimits(max_single_file_bytes=100)
    with pytest.raises(FileSizeLimitError):
        collect_safe_skill_files(skill, limits=tight)

    (skill / "b.txt").write_text("b", encoding="utf-8")
    (skill / "c.txt").write_text("c", encoding="utf-8")
    tight_count = SecurityLimits(max_files_per_skill=2)
    with pytest.raises(FileSizeLimitError):
        collect_safe_skill_files(skill, limits=tight_count)


@pytest.mark.unit
def test_symlink_blocked_in_skill_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill = tmp_path / "sym-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sym-skill\ndescription: test\n---\n", encoding="utf-8"
    )
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = skill / "link.txt"

    created_real_symlink = False
    try:
        link.symlink_to(target)
        created_real_symlink = True
    except OSError:
        # Unprivileged Windows environments without Developer Mode cannot create symlinks;
        # verify symlink guard via mocked reparse-point detection as fallback.
        link.write_text("simulated link", encoding="utf-8")
        monkeypatch.setattr(
            "contextbridge.security.paths.is_symlink_or_junction",
            lambda p: Path(p).name == "link.txt",
        )

    with pytest.raises(UnsafeSymlinkError):
        collect_safe_skill_files(skill, allow_symlinks=False)

    with pytest.raises(UnsafeSymlinkError):
        ensure_no_symlink_in_ancestors(skill, link)

    # Ensure fallback branch also cleans up gracefully
    assert created_real_symlink or link.exists()
