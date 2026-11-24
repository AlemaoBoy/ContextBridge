"""Unit tests for SKILL.md parsing and Agent Skills specification validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import make_skill

from contextbridge.models.skill import IssueSeverity, ValidationStatus
from contextbridge.skills.validator import validate_skill


@pytest.mark.unit
def test_validate_valid_fixture_skill(fixtures_dir: Path) -> None:
    report = validate_skill(fixtures_dir / "valid-skill")
    assert report.status == ValidationStatus.VALID
    assert report.is_valid is True
    assert report.skill_name == "valid-skill"
    assert report.files_checked == 3
    assert report.local_references_checked == 2
    assert len(report.errors) == 0


@pytest.mark.unit
def test_validate_invalid_yaml_fixture(fixtures_dir: Path) -> None:
    report = validate_skill(fixtures_dir / "invalid-yaml-skill")
    assert report.status == ValidationStatus.INVALID
    assert report.is_valid is False
    assert any(i.code == "YAML_INVALID" for i in report.errors)


@pytest.mark.unit
def test_validate_missing_skill_md(tmp_path: Path) -> None:
    empty = tmp_path / "no-md"
    empty.mkdir()
    report = validate_skill(empty)
    assert report.status == ValidationStatus.INVALID
    assert any(i.code == "SKILL_MD_MISSING" for i in report.errors)


@pytest.mark.unit
def test_validate_name_rules_and_directory_mismatch(tmp_path: Path) -> None:
    skill_dir = tmp_path / "proper-name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Bad--Name_\n"
        "description: Detailed valid description for testing name format rules.\n"
        "---\n\n# Body\n",
        encoding="utf-8",
    )
    report = validate_skill(skill_dir)
    codes = {i.code for i in report.errors}
    assert "NAME_FORMAT" in codes
    assert "NAME_DIR_MISMATCH" in codes


@pytest.mark.unit
def test_validate_missing_and_traversal_local_references(tmp_path: Path) -> None:
    skill = make_skill(
        tmp_path,
        "ref-check",
        body=(
            "# Check refs\n"
            "- Missing: [missing](./references/does-not-exist.md)\n"
            "- Escape: [escape](../../etc/passwd)\n"
            "- External ignored: [site](https://agentskills.io)\n"
        ),
    )
    report = validate_skill(skill)
    codes = {i.code for i in report.errors}
    assert "REF_MISSING_FILE" in codes
    assert "REF_PATH_TRAVERSAL" in codes
    assert report.local_references_checked == 2


@pytest.mark.unit
def test_validate_differentiates_errors_warnings_and_info(tmp_path: Path) -> None:
    skill = make_skill(
        tmp_path,
        "severity-skill",
        description="Short.",
        extra_frontmatter="context: fork\nallowed-tools: Read Grep",
        body="",
    )
    report = validate_skill(skill)
    assert report.status == ValidationStatus.VALID_WITH_WARNINGS
    assert len(report.errors) == 0
    assert any(
        i.severity == IssueSeverity.WARNING and i.code == "BODY_EMPTY" for i in report.issues
    )
    assert any(
        i.severity == IssueSeverity.WARNING and i.code == "DESCRIPTION_BRIEF" for i in report.issues
    )
    assert any(
        i.severity == IssueSeverity.INFO and i.code == "FRONTMATTER_EXTRA_FIELDS"
        for i in report.issues
    )
    assert any(
        i.severity == IssueSeverity.INFO and i.code == "FIELD_ALLOWED_TOOLS_EXPERIMENTAL"
        for i in report.issues
    )


@pytest.mark.unit
def test_validate_rejects_yaml_alias_bomb(tmp_path: Path) -> None:
    skill = tmp_path / "bomb-skill"
    skill.mkdir()
    anchors = "\n".join(f"  k{i}: &a{i} [val, *a{max(0, i - 1)}]" for i in range(1, 25))
    (skill / "SKILL.md").write_text(
        f"---\nname: bomb-skill\ndescription: test\nmetadata:\n  k0: &a0 [x]\n{anchors}\n---\n",
        encoding="utf-8",
    )
    report = validate_skill(skill)
    assert report.status == ValidationStatus.INVALID
    assert any(i.code == "YAML_ALIAS_LIMIT" for i in report.errors)
