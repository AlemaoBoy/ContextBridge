"""Integration tests for safe ZIP export of Agent Skills."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from tests.conftest import make_skill

from contextbridge.core.exceptions import SecretExposureError, SkillValidationError
from contextbridge.security.archive import inspect_zip_safety
from contextbridge.skills.exporter import export_skill_zip


@pytest.mark.integration
def test_export_valid_skill_preserves_structure_and_passes_zip_safety(
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    out_zip = tmp_path / "exported.zip"
    result = export_skill_zip(fixtures_dir / "valid-skill", output_path=out_zip)

    assert result.output_path == out_zip.resolve()
    assert out_zip.is_file()
    assert result.files_archived == 3
    assert len(result.archive_sha256) == 64

    entries = [p.as_posix() for p in inspect_zip_safety(out_zip)]
    assert entries == [
        "valid-skill/SKILL.md",
        "valid-skill/references/guide.md",
        "valid-skill/scripts/helper.py",
    ]
    with zipfile.ZipFile(out_zip, "r") as zf:
        assert "valid-skill" in zf.read("valid-skill/SKILL.md").decode("utf-8")


@pytest.mark.integration
def test_export_blocks_secret_or_sensitive_files(tmp_path: Path) -> None:
    skill_with_env = make_skill(
        tmp_path,
        "env-skill",
        extra_files={".env": "API_TOKEN=supersecretvalue1234567890"},
    )
    with pytest.raises(SecretExposureError):
        export_skill_zip(skill_with_env, output_path=tmp_path / "out.zip")

    assert not (tmp_path / "out.zip").exists()

    fake_token = "ghp_" + ("z" * 36)
    skill_with_token = make_skill(
        tmp_path,
        "token-skill",
        extra_files={"references/notes.md": f"token={fake_token}\n"},
    )
    with pytest.raises(SecretExposureError) as exc_info:
        export_skill_zip(skill_with_token, output_path=tmp_path / "out2.zip")
    assert fake_token not in exc_info.value.message


@pytest.mark.integration
def test_export_blocks_invalid_skill(fixtures_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(SkillValidationError):
        export_skill_zip(fixtures_dir / "invalid-yaml-skill", output_path=tmp_path / "bad.zip")
    assert not (tmp_path / "bad.zip").exists()
