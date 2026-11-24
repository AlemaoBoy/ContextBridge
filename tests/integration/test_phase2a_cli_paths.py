"""Phase 2A CLI integration tests covering interactive confirms, JSON modes, and error exits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import make_skill
from typer.testing import CliRunner

from contextbridge.cli import app
from contextbridge.core.paths import EnvironmentContext

runner = CliRunner()


@pytest.mark.integration
def test_cli_empty_outputs_errors_and_interactive_confirmations(
    isolated_env: EnvironmentContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("contextbridge.cli._build_env", lambda _p: isolated_env)
    monkeypatch.setattr("contextbridge.cli.default_environment", lambda: isolated_env)

    # Empty skills list & empty backup list (human-readable)
    assert runner.invoke(app, ["skills", "list"]).exit_code == 0
    assert runner.invoke(app, ["backup", "list"]).exit_code == 0

    # Unknown agent error panel returns exit code 2
    bad_agent = runner.invoke(app, ["skills", "list", "--agent", "unknown-xyz"])
    assert bad_agent.exit_code == 2

    # Invalid skill validate --json returns exit code 2 with JSON payload
    bad_skill = isolated_env.project_dir / ".claude" / "skills" / "broken"
    bad_skill.mkdir(parents=True)
    (bad_skill / "SKILL.md").write_text("---\nname: broken\n---\n", encoding="utf-8")
    val_json = runner.invoke(app, ["skills", "validate", str(bad_skill), "--json"])
    assert val_json.exit_code == 2
    assert json.loads(val_json.stdout)["status"] == "invalid"

    # Doctor with invalid skill returns exit code 1 in both human and JSON modes
    assert runner.invoke(app, ["doctor"]).exit_code == 1
    doc_json = runner.invoke(app, ["doctor", "--json"])
    assert doc_json.exit_code == 1
    assert json.loads(doc_json.stdout)["checks"]

    # Export --json on valid skill + error on invalid skill
    good = make_skill(isolated_env.project_dir / ".claude" / "skills", "good-cli-skill")
    out_zip = tmp_path / "good.zip"
    exp_json = runner.invoke(
        app, ["skills", "export", str(good), "--format", "zip", "--output", str(out_zip), "--json"]
    )
    assert exp_json.exit_code == 0
    assert json.loads(exp_json.stdout)["skill_name"] == "good-cli-skill"
    assert (
        runner.invoke(app, ["skills", "export", str(bad_skill), "--format", "zip"]).exit_code == 2
    )

    # Conflicting --dry-run --apply returns exit code 2
    assert (
        runner.invoke(
            app,
            [
                "skills",
                "migrate",
                "good-cli-skill",
                "--from",
                "claude",
                "--to",
                "codex",
                "--dry-run",
                "--apply",
            ],
        ).exit_code
        == 2
    )

    # Missing skill migrate returns exit code 2
    assert (
        runner.invoke(
            app, ["skills", "migrate", "nope", "--from", "claude", "--to", "codex"]
        ).exit_code
        == 2
    )

    # Simulate interactive TTY for typer.confirm decline ("n") then accept ("y")
    monkeypatch.setattr("contextbridge.cli._is_interactive", lambda: True)
    declined = runner.invoke(
        app,
        ["skills", "migrate", "good-cli-skill", "--from", "claude", "--to", "codex", "--apply"],
        input="n\n",
    )
    assert declined.exit_code == 0
    assert not (isolated_env.project_dir / ".agents" / "skills" / "good-cli-skill").exists()

    accepted = runner.invoke(
        app,
        ["skills", "migrate", "good-cli-skill", "--from", "claude", "--to", "codex", "--apply"],
        input="y\n",
    )
    assert accepted.exit_code == 0
    assert (
        isolated_env.project_dir / ".agents" / "skills" / "good-cli-skill" / "SKILL.md"
    ).is_file()

    # Overwrite without --overwrite returns exit code 4 (also prints Conflict in human panel)
    conflict = runner.invoke(
        app,
        [
            "skills",
            "migrate",
            "good-cli-skill",
            "--from",
            "claude",
            "--to",
            "codex",
            "--apply",
            "--yes",
        ],
    )
    assert conflict.exit_code == 4

    # Invalid skill migration prints Blocking issues in human panel and returns exit code 2
    blocked = runner.invoke(
        app,
        ["skills", "migrate", "broken", "--from", "claude", "--to", "codex"],
    )
    assert blocked.exit_code == 2

    # Create a backup via overwrite, then test backup restore interactive decline/accept/missing
    ov = runner.invoke(
        app,
        [
            "skills",
            "migrate",
            "good-cli-skill",
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
    backup_id = json.loads(ov.stdout)["backup_id"]

    assert runner.invoke(app, ["backup", "restore", "nonexistent-id"]).exit_code == 5

    rst_decline = runner.invoke(app, ["backup", "restore", backup_id, "--apply"], input="n\n")
    assert rst_decline.exit_code == 0

    rst_accept = runner.invoke(app, ["backup", "restore", backup_id, "--apply"], input="y\n")
    assert rst_accept.exit_code == 0

    rst_json = runner.invoke(app, ["backup", "restore", backup_id, "--apply", "--yes", "--json"])
    assert rst_json.exit_code == 0
    assert json.loads(rst_json.stdout)["applied"] is True

    # Non-interactive backup restore without --yes refuses with exit code 2
    monkeypatch.setattr("contextbridge.cli._is_interactive", lambda: False)
    assert runner.invoke(app, ["backup", "restore", backup_id, "--apply"]).exit_code == 2
