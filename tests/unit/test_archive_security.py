"""Unit tests for ZIP Slip, ZIP bomb, symlink entry, and safe ZIP extraction."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from contextbridge.core.exceptions import ArchiveSecurityError
from contextbridge.security.archive import inspect_zip_safety, safe_extract_zip
from contextbridge.security.limits import SecurityLimits


@pytest.mark.unit
def test_zip_slip_blocked(tmp_path: Path) -> None:
    evil_zip = tmp_path / "slip.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../escape.txt", "pwned")

    with pytest.raises(ArchiveSecurityError, match="ZIP Slip"):
        inspect_zip_safety(evil_zip)

    with pytest.raises(ArchiveSecurityError):
        safe_extract_zip(evil_zip, tmp_path / "out")

    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.unit
def test_zip_symlink_entry_blocked(tmp_path: Path) -> None:
    sym_zip = tmp_path / "sym.zip"
    with zipfile.ZipFile(sym_zip, "w") as zf:
        info = zipfile.ZipInfo("skill/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")

    with pytest.raises(ArchiveSecurityError, match="symbolic link"):
        inspect_zip_safety(sym_zip)


@pytest.mark.unit
def test_zip_bomb_compression_ratio_blocked(tmp_path: Path) -> None:
    bomb_zip = tmp_path / "bomb.zip"
    # Highly compressible 2 MiB payload of zeros
    with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("skill/zeros.txt", b"0" * (2 * 1024 * 1024))

    tight = SecurityLimits(max_zip_compression_ratio=20.0)
    with pytest.raises(ArchiveSecurityError, match="ZIP bomb"):
        inspect_zip_safety(bomb_zip, limits=tight)


@pytest.mark.unit
def test_zip_total_uncompressed_limit_blocked(tmp_path: Path) -> None:
    large_zip = tmp_path / "large.zip"
    with zipfile.ZipFile(large_zip, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("skill/a.txt", b"x" * 2000)
        zf.writestr("skill/b.txt", b"y" * 2000)

    tight = SecurityLimits(max_zip_uncompressed_bytes=3000)
    with pytest.raises(ArchiveSecurityError, match="uncompressed size"):
        inspect_zip_safety(large_zip, limits=tight)


@pytest.mark.unit
def test_safe_extract_zip_succeeds_for_clean_archive(tmp_path: Path) -> None:
    clean_zip = tmp_path / "clean.zip"
    with zipfile.ZipFile(clean_zip, "w") as zf:
        zf.writestr("my-skill/SKILL.md", "---\nname: my-skill\ndescription: ok\n---\n")
        zf.writestr("my-skill/references/doc.md", "# Doc\n")

    out_dir = tmp_path / "extracted"
    files = safe_extract_zip(clean_zip, out_dir)
    assert len(files) == 2
    assert (out_dir / "my-skill" / "SKILL.md").is_file()
    assert (out_dir / "my-skill" / "references" / "doc.md").is_file()
