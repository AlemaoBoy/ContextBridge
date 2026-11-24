"""Phase 2A hardening tests for migrator, exporter, parser, validator, adapters, core, and utils."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.conftest import make_skill

from contextbridge.adapters.base import BaseAgentAdapter
from contextbridge.adapters.registry import get_adapter
from contextbridge.core.discovery import discover_skills
from contextbridge.core.doctor import run_doctor
from contextbridge.core.exceptions import (
    MigrationConflictError,
    MigrationExecutionError,
    SkillNotFoundError,
    SkillValidationError,
    UnsafeSymlinkError,
)
from contextbridge.core.paths import EnvironmentContext, default_environment
from contextbridge.models.agent import AgentId, AgentScope, SkillDirectorySpec
from contextbridge.models.doctor import CheckStatus
from contextbridge.models.skill import SkillFrontmatter
from contextbridge.security.limits import SecurityLimits
from contextbridge.skills.exporter import export_skill_zip
from contextbridge.skills.migrator import execute_migration, plan_migration
from contextbridge.skills.parser import parse_skill_markdown
from contextbridge.skills.validator import validate_skill
from contextbridge.utils.fs import (
    atomic_write_bytes,
    get_file_mode_is_executable,
    is_readable_dir,
    is_symlink_or_junction,
    is_writable_dir,
)
from contextbridge.utils.hashing import sha256_bytes

# --- skills/migrator.py ---


@pytest.mark.unit
def test_migrator_error_and_rollback_branches(
    isolated_env: EnvironmentContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Multi-segment skill name rejected & missing skill rejected
    with pytest.raises(SkillNotFoundError, match="single directory name"):
        plan_migration(
            "nested/skill", source_agent="claude", target_agent="codex", env=isolated_env
        )

    with pytest.raises(SkillNotFoundError, match="not found"):
        plan_migration(
            "missing-skill", source_agent="claude", target_agent="codex", env=isolated_env
        )

    # Non-security invalid skill blocked with SkillValidationError
    bad_skill = isolated_env.project_dir / ".claude" / "skills" / "bad-desc"
    bad_skill.mkdir(parents=True)
    (bad_skill / "SKILL.md").write_text("---\nname: bad-desc\n---\n", encoding="utf-8")
    bad_plan = plan_migration(
        "bad-desc", source_agent="claude", target_agent="codex", env=isolated_env
    )
    with pytest.raises(SkillValidationError, match="validation errors"):
        execute_migration(bad_plan, env=isolated_env, dry_run=False)

    # Source deleted after plan triggers MigrationExecutionError
    src = make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "vanish-skill",
        extra_files={"references/r.md": "v1"},
    )
    vanish_plan = plan_migration(
        "vanish-skill", source_agent="claude", target_agent="codex", env=isolated_env
    )
    shutil.rmtree(src)
    with pytest.raises(MigrationExecutionError, match="unreadable or unsafe"):
        execute_migration(vanish_plan, env=isolated_env, dry_run=False)

    # Staging copy hash mismatch caught before swap
    make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "corrupt-stage",
        extra_files={"references/r.md": "v1"},
    )
    cs_plan = plan_migration(
        "corrupt-stage", source_agent="claude", target_agent="codex", env=isolated_env
    )

    def bad_copy(_s: Path, d: Path) -> None:
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text("tampered", encoding="utf-8")

    with pytest.raises(
        MigrationExecutionError, match="Integrity verification failed during staging"
    ):
        execute_migration(cs_plan, env=isolated_env, dry_run=False, copy_fn=bad_copy)

    # Post-swap verification failure triggers clean rollback & removes .cb_migrate_failed_*
    make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "swap-rb",
        extra_files={"references/r.md": "v2"},
    )
    target = make_skill(
        isolated_env.project_dir / ".agents" / "skills",
        "swap-rb",
        extra_files={"references/r.md": "v1"},
    )
    rb_plan = plan_migration(
        "swap-rb", source_agent="claude", target_agent="codex", env=isolated_env
    )
    step = 0

    def corrupt_post_swap(s: Path, d: Path) -> None:
        nonlocal step
        step += 1
        os.replace(s, d)
        if step == 2:
            (d / "references" / "r.md").write_text("corrupted", encoding="utf-8")

    with pytest.raises(MigrationExecutionError, match="rolled_back=True"):
        execute_migration(
            rb_plan,
            env=isolated_env,
            dry_run=False,
            overwrite=True,
            replace_fn=corrupt_post_swap,
        )
    assert (target / "references" / "r.md").read_text(encoding="utf-8") == "v1"
    assert list(target.parent.glob(".cb_migrate_failed_*")) == []

    # Target parent symlink blocked
    monkeypatch.setattr(
        "contextbridge.skills.migrator.is_symlink_or_junction",
        lambda p: Path(p) == target.parent,
    )
    with pytest.raises(MigrationExecutionError, match="parent directory is a symlink"):
        execute_migration(rb_plan, env=isolated_env, dry_run=False, overwrite=True)

    # Target itself becomes symlink after planning
    monkeypatch.setattr(
        "contextbridge.skills.migrator.is_symlink_or_junction",
        lambda p: Path(p) == target,
    )
    with pytest.raises(MigrationExecutionError, match="Target skill path is a symlink"):
        execute_migration(rb_plan, env=isolated_env, dry_run=False, overwrite=True)
    monkeypatch.setattr("contextbridge.skills.migrator.is_symlink_or_junction", lambda _p: False)

    # Target exists as a regular file (not dir) during planning -> conflict detected
    make_skill(isolated_env.project_dir / ".claude" / "skills", "file-conflict")
    file_target = isolated_env.project_dir / ".agents" / "skills" / "file-conflict"
    file_target.parent.mkdir(parents=True, exist_ok=True)
    file_target.write_text("i am a file", encoding="utf-8")
    fc_plan = plan_migration(
        "file-conflict", source_agent="claude", target_agent="codex", env=isolated_env
    )
    assert fc_plan.conflict is not None
    assert fc_plan.conflict.existing_files_count == 1
    with pytest.raises(MigrationConflictError):
        execute_migration(fc_plan, env=isolated_env, dry_run=False, overwrite=False)


# --- skills/exporter.py ---


@pytest.mark.unit
def test_exporter_defaults_ads_symlink_and_extra_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = make_skill(
        tmp_path / "src",
        "exp-skill",
        extra_frontmatter="context: fork",
    )

    # output_path = existing directory -> writes <dir>/<skill>.zip
    out_dir = tmp_path / "out_dir"
    out_dir.mkdir()
    res_dir = export_skill_zip(skill, output_path=out_dir)
    assert res_dir.output_path == (out_dir / "exp-skill.zip").resolve()
    assert "agent-specific frontmatter" in res_dir.format_note

    # output_path = None -> writes into cwd
    cwd_dir = tmp_path / "cwd"
    cwd_dir.mkdir()
    monkeypatch.chdir(cwd_dir)
    res_cwd = export_skill_zip(skill, output_path=None)
    assert res_cwd.output_path == (cwd_dir / "exp-skill.zip").resolve()

    # ADS output path rejected
    with pytest.raises(UnsafeSymlinkError, match="Alternate Data Stream"):
        export_skill_zip(skill, output_path=Path(str(tmp_path / "a.zip") + ":stream"))

    # Symlink output path rejected
    sym_out = tmp_path / "sym.zip"
    sym_out.write_bytes(b"")
    monkeypatch.setattr(
        "contextbridge.skills.exporter.is_symlink_or_junction", lambda p: Path(p) == sym_out
    )
    with pytest.raises(UnsafeSymlinkError, match="symlink or junction"):
        export_skill_zip(skill, output_path=sym_out, overwrite=True)


# --- skills/parser.py & validator.py ---


@pytest.mark.unit
def test_parser_and_validator_exhaustive_edge_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Missing SKILL.md stat error
    missing_md = parse_skill_markdown(tmp_path / "missing" / "SKILL.md")
    assert any(i.code == "SKILL_MD_UNREADABLE" for i in missing_md.issues)

    # 2. Oversized SKILL.md
    big_md = tmp_path / "big.md"
    big_md.write_text("x" * 200, encoding="utf-8")
    big_parsed = parse_skill_markdown(big_md, limits=SecurityLimits(max_skill_md_bytes=50))
    assert any(i.code == "SKILL_MD_TOO_LARGE" for i in big_parsed.issues)

    # 3. Invalid UTF-8
    bad_utf8 = tmp_path / "bad_utf8.md"
    bad_utf8.write_bytes(b"\xff\xfe\xfd")
    utf8_parsed = parse_skill_markdown(bad_utf8)
    assert any(i.code == "SKILL_MD_ENCODING" for i in utf8_parsed.issues)

    # 4. Missing frontmatter, unclosed, empty, non-mapping, schema mismatch, BOM + fenced code
    cases = {
        "no_fm.md": ("# Just markdown\n[link](./a.md)\n", "FRONTMATTER_MISSING"),
        "unclosed.md": ("---\nname: a\n", "FRONTMATTER_UNCLOSED"),
        "empty.md": ("---\n---\nBody\n", "FRONTMATTER_EMPTY"),
        "list.md": ("---\n- item1\n- item2\n---\nBody\n", "FRONTMATTER_NOT_MAPPING"),
        "schema.md": (
            "---\nname: a\ndescription: b\nmetadata: not-a-dict\n---\nBody\n",
            "FRONTMATTER_SCHEMA",
        ),
    }
    for fname, (content, expected_code) in cases.items():
        p = tmp_path / fname
        p.write_text(content, encoding="utf-8")
        doc = parse_skill_markdown(p)
        assert any(i.code == expected_code for i in doc.issues), fname

    # UTF-8 BOM + fenced code links ignored + anchor/query stripped
    bom_md = tmp_path / "bom.md"
    bom_md.write_bytes(
        (
            "\ufeff---\nname: bom\ndescription: Valid BOM skill description.\n---\n"
            "```md\n[ignored](./not-a-ref.md)\n```\n"
            "[anchor](#section)\n"
            "[local](./references/a.md#top?v=1)\n"
        ).encode("utf-8")
    )
    bom_doc = parse_skill_markdown(bom_md)
    assert bom_doc.frontmatter is not None
    assert [r.raw_target for r in bom_doc.local_references] == ["./references/a.md"]

    # 5. Validator: file (not directory), symlink root, case mismatch, long name/desc/compat,
    # non-standard subdir, symlink inside tree, size limit exceeded
    not_dir = tmp_path / "file.txt"
    not_dir.write_text("hi", encoding="utf-8")
    assert any(i.code == "SKILL_NOT_DIRECTORY" for i in validate_skill(not_dir).errors)

    sym_dir = tmp_path / "sym-root-val"
    sym_dir.mkdir()
    monkeypatch.setattr(
        "contextbridge.skills.validator.is_symlink_or_junction", lambda p: Path(p) == sym_dir
    )
    assert any(i.code == "SEC_SYMLINK_ROOT" for i in validate_skill(sym_dir).errors)
    monkeypatch.setattr("contextbridge.skills.validator.is_symlink_or_junction", lambda _p: False)

    # Case-mismatched skill.md
    case_dir = tmp_path / "case-skill"
    case_dir.mkdir()
    (case_dir / "skill.md").write_text(
        "---\nname: case-skill\ndescription: ok\n---\n", encoding="utf-8"
    )
    assert any(i.code == "SKILL_MD_CASE" for i in validate_skill(case_dir).errors)

    # Missing name & description, then overly long name/description/compatibility + nonstandard dir
    long_dir = tmp_path / ("a" * 65)
    long_dir.mkdir()
    (long_dir / "custom_folder").mkdir()
    (long_dir / "SKILL.md").write_text(
        f"---\nname: {'a' * 65}\n"
        f"description: {'d' * 1025}\n"
        f"compatibility: {'c' * 501}\n---\n# Ok\n",
        encoding="utf-8",
    )
    long_rep = validate_skill(long_dir)
    err_codes = {i.code for i in long_rep.errors}
    assert {"NAME_TOO_LONG", "DESCRIPTION_TOO_LONG", "COMPATIBILITY_TOO_LONG"} <= err_codes
    assert any(i.code == "DIR_NONSTANDARD" for i in long_rep.infos)

    # Missing name and description
    empty_fields = tmp_path / "empty-fields"
    empty_fields.mkdir()
    (empty_fields / "SKILL.md").write_text("---\nlicense: MIT\n---\n# Body\n", encoding="utf-8")
    ef_codes = {i.code for i in validate_skill(empty_fields).errors}
    assert {"NAME_MISSING", "DESCRIPTION_MISSING"} <= ef_codes

    # Size limit error surfaced by validator
    oversize_dir = make_skill(tmp_path, "oversize-skill", extra_files={"big.txt": "x" * 200})
    ov_rep = validate_skill(oversize_dir, limits=SecurityLimits(max_single_file_bytes=100))
    assert any(i.code == "SEC_SIZE_OR_COUNT_LIMIT" for i in ov_rep.errors)

    # Symlink inside tree triggers SEC_SYMLINK_DETECTED
    sym_inside = make_skill(tmp_path, "sym-inside", extra_files={"link.txt": "x"})
    monkeypatch.setattr(
        "contextbridge.security.paths.is_symlink_or_junction",
        lambda p: Path(p).name == "link.txt",
    )
    si_rep = validate_skill(sym_inside)
    assert any(i.code == "SEC_SYMLINK_DETECTED" for i in si_rep.errors)


# --- adapters, core, utils ---


@pytest.mark.unit
def test_adapters_core_doctor_and_utils_edge_branches(
    tmp_path: Path,
    make_env: Callable[..., EnvironmentContext],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # macOS & Windows extra paths on Cursor, Antigravity, OpenCode
    mac_env = make_env(platform_name="darwin")
    assert mac_env.is_macos is True
    assert mac_env.is_linux is False
    assert Path("/Applications/Cursor.app") in get_adapter(
        AgentId.CURSOR
    ).extra_executable_candidates(mac_env)
    assert Path("/Applications/Antigravity.app") in get_adapter(
        AgentId.ANTIGRAVITY
    ).extra_executable_candidates(mac_env)

    win_appdata = tmp_path / "roaming"
    win_env = make_env(platform_name="win32", env_vars={"APPDATA": str(win_appdata)})
    assert win_appdata / "opencode" in get_adapter(AgentId.OPENCODE).config_directories(win_env)

    # XDG_CONFIG_HOME & CONTEXTBRIDGE_HOME env overrides
    xdg_dir = tmp_path / "xdg"
    cb_dir = tmp_path / "cb_custom"
    env_overrides = EnvironmentContext(
        home_dir=tmp_path / "h",
        project_dir=tmp_path / "p",
        env={"XDG_CONFIG_HOME": str(xdg_dir), "CONTEXTBRIDGE_HOME": str(cb_dir)},
        platform="linux",
        which=lambda _c: None,
    )
    assert env_overrides.xdg_config_home == xdg_dir
    assert env_overrides.contextbridge_home == cb_dir
    assert env_overrides.is_linux is True
    assert default_environment(project_dir=tmp_path).project_dir == tmp_path.resolve()

    # BaseAgentAdapter compatibility_warnings_for_skill(None) & allowed-tools
    codex = get_adapter(AgentId.CODEX)
    assert codex.compatibility_warnings_for_skill(None) == []
    fm = SkillFrontmatter.model_validate({"name": "s", "description": "d", "allowed-tools": "Read"})
    assert any("allowed-tools" in w for w in codex.compatibility_warnings_for_skill(fm))

    # Empty adapter primary_write_directory raises ValueError
    class _EmptyAdapter(BaseAgentAdapter):
        agent_id = AgentId.CLAUDE
        display_name = "Empty"

        def config_directories(self, env: EnvironmentContext) -> list[Path]:
            return []

        def skill_directories(
            self, env: EnvironmentContext, scope: AgentScope | None = None
        ) -> list[SkillDirectorySpec]:
            return []

    with pytest.raises(ValueError, match="does not support scope"):
        _EmptyAdapter().primary_write_directory(mac_env, AgentScope.PROJECT)

    # Doctor checks: missing project dir, installed_no_config, warn-skill, symlinked/readonly dir
    missing_proj_env = EnvironmentContext(
        home_dir=tmp_path / "home_doc",
        project_dir=tmp_path / "does_not_exist_proj",
        env={},
        platform="linux",
        which=lambda cmd: "/usr/bin/codex" if cmd == "codex" else None,
        state_dir_override=tmp_path / "state_doc",
    )
    doc_rep = run_doctor(missing_proj_env)
    by_name = {c.name: c for c in doc_rep.checks}
    assert by_name["project-directory"].status == CheckStatus.ERROR
    assert "codex-project-dir" in by_name

    # Doctor with valid-with-warnings skill + read-only skills dir + symlinked skills dir
    doc_env = make_env()
    make_skill(doc_env.project_dir / ".claude" / "skills", "brief-skill", description="Hi", body="")
    claude_skills = doc_env.project_dir / ".claude" / "skills"
    monkeypatch.setattr("contextbridge.core.doctor.is_writable_dir", lambda _p: False)
    monkeypatch.setattr(
        "contextbridge.core.doctor.is_symlink_or_junction", lambda p: Path(p) == claude_skills
    )
    doc_rep2 = run_doctor(doc_env)
    names2 = {c.name: c for c in doc_rep2.checks}
    assert names2["warn-skill:brief-skill"].status == CheckStatus.WARN
    assert names2["claude-readonly"].status == CheckStatus.WARN
    assert names2["claude-symlink-dir"].status == CheckStatus.WARN

    # discover_skills skips unreadable dirs cleanly
    monkeypatch.setattr("contextbridge.core.discovery.is_readable_dir", lambda _p: False)
    assert discover_skills(doc_env) == []

    # utils/fs.py & hashing.py & Gemini CLI markers edge cases
    gem_env = make_env()
    empty_gem = gem_env.home_dir / ".gemini"
    empty_gem.mkdir(exist_ok=True)
    assert get_adapter(AgentId.GEMINI_CLI).detect(gem_env).config_directories == []
    (empty_gem / "settings.json").write_text("{}", encoding="utf-8")
    assert empty_gem in get_adapter(AgentId.GEMINI_CLI).detect(gem_env).config_directories

    assert sha256_bytes(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert is_readable_dir(tmp_path / "no_dir") is False
    assert is_writable_dir(tmp_path / "no_dir") is False
    assert is_symlink_or_junction(tmp_path / "no_dir") is False
    assert get_file_mode_is_executable(tmp_path / "no_file") is False
    target_bin = tmp_path / "atomic.bin"
    atomic_write_bytes(target_bin, b"ok")
    assert target_bin.read_bytes() == b"ok"
    assert isinstance(get_file_mode_is_executable(target_bin), bool)

    # atomic_write_bytes cleans up temp file on replace failure
    monkeypatch.setattr(
        "contextbridge.utils.fs.os.replace",
        lambda _a, _b: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_bytes(tmp_path / "atomic_fail.bin", b"data")
    assert list(tmp_path.glob(".cb_tmp_*")) == []
