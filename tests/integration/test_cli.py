"""End-to-end CLI integration tests using Typer CliRunner against isolated environments."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.conftest import make_skill
from typer.testing import CliRunner

from contextbridge import __version__
from contextbridge.cli import app
from contextbridge.core.paths import EnvironmentContext

runner = CliRunner()


@pytest.mark.integration
def test_cli_version_and_help() -> None:
    ver_res = runner.invoke(app, ["--version"])
    assert ver_res.exit_code == 0
    assert __version__ in ver_res.stdout

    help_res = runner.invoke(app, ["--help"])
    assert help_res.exit_code == 0
    assert "scan" in help_res.stdout
    assert "skills" in help_res.stdout
    assert "backup" in help_res.stdout
    assert "doctor" in help_res.stdout

    # Cold-start subprocess check guards against circular imports at CLI startup
    proc = subprocess.run(
        [sys.executable, "-m", "contextbridge", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert __version__ in proc.stdout


@pytest.mark.integration
def test_cli_scan_list_validate_export_migrate_backup_and_doctor(
    isolated_env: EnvironmentContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Patch CLI environment builder to use our isolated tmp_path environment exclusively
    monkeypatch.setattr("contextbridge.cli._build_env", lambda _p: isolated_env)
    monkeypatch.setattr("contextbridge.cli.default_environment", lambda: isolated_env)

    skill_path = make_skill(
        isolated_env.project_dir / ".claude" / "skills",
        "cli-demo-skill",
        extra_files={"references/notes.md": "# Notes\n"},
    )

    # 1. scan --json
    scan_res = runner.invoke(app, ["scan", "--json"])
    assert scan_res.exit_code == 0
    scan_data = json.loads(scan_res.stdout)
    assert scan_data["unique_physical_skills"] == 1
    assert len(scan_data["agents"]) == 6

    # 2. skills list --json & --agent filter
    list_res = runner.invoke(app, ["skills", "list", "--agent", "claude", "--json"])
    assert list_res.exit_code == 0
    list_data = json.loads(list_res.stdout)
    assert len(list_data) == 1
    assert list_data[0]["name"] == "cli-demo-skill"

    # 3. skills validate
    val_ok = runner.invoke(app, ["skills", "validate", str(skill_path)])
    assert val_ok.exit_code == 0

    val_bad = runner.invoke(app, ["skills", "validate", str(tmp_path / "does-not-exist")])
    assert val_bad.exit_code == 2

    # 4. skills export --format zip
    out_zip = tmp_path / "cli-demo.zip"
    exp_res = runner.invoke(
        app,
        ["skills", "export", str(skill_path), "--format", "zip", "--output", str(out_zip)],
    )
    assert exp_res.exit_code == 0
    assert out_zip.is_file()

    # Unsupported format returns exit code 2
    bad_fmt = runner.invoke(app, ["skills", "export", str(skill_path), "--format", "tar"])
    assert bad_fmt.exit_code == 2

    # 5. skills migrate dry-run default
    mig_dry = runner.invoke(
        app,
        ["skills", "migrate", "cli-demo-skill", "--from", "claude", "--to", "codex"],
    )
    assert mig_dry.exit_code == 0
    assert not (isolated_env.project_dir / ".agents" / "skills" / "cli-demo-skill").exists()

    # Non-interactive --apply without --yes must refuse with exit code 2
    mig_no_yes = runner.invoke(
        app,
        [
            "skills",
            "migrate",
            "cli-demo-skill",
            "--from",
            "claude",
            "--to",
            "codex",
            "--apply",
        ],
    )
    assert mig_no_yes.exit_code == 2

    # Apply with --yes succeeds
    mig_apply = runner.invoke(
        app,
        [
            "skills",
            "migrate",
            "cli-demo-skill",
            "--from",
            "claude",
            "--to",
            "codex",
            "--apply",
            "--yes",
        ],
    )
    assert mig_apply.exit_code == 0
    assert (
        isolated_env.project_dir / ".agents" / "skills" / "cli-demo-skill" / "SKILL.md"
    ).is_file()

    # Overwrite migration creates a backup we can list and restore
    mig_overwrite = runner.invoke(
        app,
        [
            "skills",
            "migrate",
            "cli-demo-skill",
            "--from",
            "claude",
            "--to",
            "codex",
            "--apply",
            "--overwrite",
            "--yes",
            "--json",
        ],
    )
    assert mig_overwrite.exit_code == 0
    mig_json = json.loads(mig_overwrite.stdout)
    backup_id = mig_json["backup_id"]
    assert backup_id

    # 6. backup list --json
    bkp_list = runner.invoke(app, ["backup", "list", "--json"])
    assert bkp_list.exit_code == 0
    backups = json.loads(bkp_list.stdout)
    assert any(b["backup_id"] == backup_id for b in backups)

    # 7. backup restore (dry-run then --apply --yes)
    rst_dry = runner.invoke(app, ["backup", "restore", backup_id])
    assert rst_dry.exit_code == 0

    rst_apply = runner.invoke(app, ["backup", "restore", backup_id, "--apply", "--yes"])
    assert rst_apply.exit_code == 0

    # 8. doctor --json
    doc_res = runner.invoke(app, ["doctor", "--json"])
    assert doc_res.exit_code == 0
    doc_data = json.loads(doc_res.stdout)
    assert doc_data["checks"]

    # 9. Human-readable Rich table commands execute cleanly without errors
    assert runner.invoke(app, ["scan"]).exit_code == 0
    assert runner.invoke(app, ["skills", "list"]).exit_code == 0
    assert runner.invoke(app, ["backup", "list"]).exit_code == 0
    assert runner.invoke(app, ["doctor"]).exit_code == 0
