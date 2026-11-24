"""Real-subprocess end-to-end CLI tests (no CliRunner mocks, fully isolated temp home/project)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.conftest import make_skill

from contextbridge import __version__


def _run_cli(
    args: list[str],
    *,
    home_dir: Path,
    project_dir: Path,
    state_dir: Path,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["CONTEXTBRIDGE_HOME"] = str(state_dir)
    env["XDG_CONFIG_HOME"] = str(home_dir / ".config")
    # Clear agent override env vars so host machine overrides never leak into tests
    for var in (
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "OPENCODE_CONFIG_DIR",
        "GEMINI_CLI_HOME",
    ):
        env.pop(var, None)
    return subprocess.run(
        [sys.executable, "-m", "contextbridge", *args],
        cwd=str(project_dir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.mark.integration
def test_all_cli_commands_via_real_subprocesses(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    state = tmp_path / "state"
    home.mkdir()
    project.mkdir()

    skill_dir = make_skill(
        project / ".claude" / "skills",
        "subproc-skill",
        extra_files={"references/ref.md": "# Ref\n", "scripts/util.py": "x = 1\n"},
    )

    # --version and --help
    ver = _run_cli(["--version"], home_dir=home, project_dir=project, state_dir=state)
    assert ver.returncode == 0
    assert __version__ in ver.stdout

    hlp = _run_cli(["--help"], home_dir=home, project_dir=project, state_dir=state)
    assert hlp.returncode == 0
    assert "scan" in hlp.stdout

    # scan --json
    scan = _run_cli(["scan", "--json"], home_dir=home, project_dir=project, state_dir=state)
    assert scan.returncode == 0, scan.stderr
    scan_data = json.loads(scan.stdout)
    assert scan_data["unique_physical_skills"] == 1

    # skills list --json
    lst = _run_cli(
        ["skills", "list", "--agent", "claude", "--json"],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert lst.returncode == 0
    assert json.loads(lst.stdout)[0]["name"] == "subproc-skill"

    # skills validate
    val = _run_cli(
        ["skills", "validate", str(skill_dir), "--json"],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert val.returncode == 0
    assert json.loads(val.stdout)["status"] == "valid"

    # skills export (plus overwrite refusal)
    out_zip = tmp_path / "subproc-skill.zip"
    exp = _run_cli(
        ["skills", "export", str(skill_dir), "--format", "zip", "--output", str(out_zip)],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert exp.returncode == 0
    assert out_zip.is_file()

    exp_dup = _run_cli(
        ["skills", "export", str(skill_dir), "--format", "zip", "--output", str(out_zip)],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert exp_dup.returncode == 4

    # skills migrate conflicting --dry-run --apply rejected
    mig_bad_flags = _run_cli(
        [
            "skills",
            "migrate",
            "subproc-skill",
            "--from",
            "claude",
            "--to",
            "codex",
            "--dry-run",
            "--apply",
        ],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert mig_bad_flags.returncode == 2

    # skills migrate dry-run default -> no files written
    mig_dry = _run_cli(
        ["skills", "migrate", "subproc-skill", "--from", "claude", "--to", "codex"],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert mig_dry.returncode == 0
    assert not (project / ".agents" / "skills" / "subproc-skill").exists()

    # skills migrate --apply --yes
    mig_ok = _run_cli(
        [
            "skills",
            "migrate",
            "subproc-skill",
            "--from",
            "claude",
            "--to",
            "codex",
            "--apply",
            "--yes",
        ],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert mig_ok.returncode == 0
    assert (project / ".agents" / "skills" / "subproc-skill" / "SKILL.md").is_file()

    # Second migrate with --overwrite creates backup
    mig_ov = _run_cli(
        [
            "skills",
            "migrate",
            "subproc-skill",
            "--from",
            "claude",
            "--to",
            "codex",
            "--apply",
            "--overwrite",
            "--yes",
            "--json",
        ],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert mig_ov.returncode == 0
    backup_id = json.loads(mig_ov.stdout)["backup_id"]
    assert backup_id

    # backup list --json
    bkp_list = _run_cli(
        ["backup", "list", "--json"], home_dir=home, project_dir=project, state_dir=state
    )
    assert bkp_list.returncode == 0
    assert any(b["backup_id"] == backup_id for b in json.loads(bkp_list.stdout))

    # backup restore dry-run & apply
    rst_dry = _run_cli(
        ["backup", "restore", backup_id],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert rst_dry.returncode == 0

    rst_apply = _run_cli(
        ["backup", "restore", backup_id, "--apply", "--yes"],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert rst_apply.returncode == 0

    # Tampered manifest destination blocked by CLI with exit code 3 (SecurityViolation)
    manifest_file = state / "backups" / backup_id / "manifest.json"
    mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
    evil_dst = tmp_path / "unauthorized_target"
    mdata["source_path"] = str(evil_dst)
    manifest_file.write_text(json.dumps(mdata), encoding="utf-8")

    rst_evil = _run_cli(
        ["backup", "restore", backup_id, "--apply", "--yes"],
        home_dir=home,
        project_dir=project,
        state_dir=state,
    )
    assert rst_evil.returncode == 3
    assert not evil_dst.exists()

    # doctor --json
    doc = _run_cli(["doctor", "--json"], home_dir=home, project_dir=project, state_dir=state)
    assert doc.returncode == 0
    assert json.loads(doc.stdout)["checks"]
