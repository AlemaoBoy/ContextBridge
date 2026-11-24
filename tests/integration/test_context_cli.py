"""End-to-end offline context commands against isolated synthetic data."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

from contextbridge.cli import app
from contextbridge.context.files import load_document

runner = CliRunner()
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "context-handoff.cbcf.json"
SECRET = "ghp_" + "A" * 36


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "OPENCODE_CONFIG_DIR", "GEMINI_CLI_HOME"):
        monkeypatch.setenv(name, str(root / "mock-agent-state"))
    return root


def run(*args: str, code: int = 0):
    result = runner.invoke(app, ["context", *args, "--json"])
    assert result.exit_code == code, (result.output, result.exception)
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_cli_offline_preview_and_portable_import(sandbox: Path, monkeypatch) -> None:
    sample = sandbox / "project.cbcf.json"
    data = json.loads(EXAMPLE.read_bytes())
    data["messages"] = [
        {
            "role": "tool",
            "parts": [
                {
                    "type": "tool_call",
                    "call_id": "would-run",
                    "tool_name": "shell",
                    "arguments": {
                        "command": "echo SHOULD_NEVER_RUN",
                        "authorization": SECRET,
                    },
                },
            ],
        }
    ]
    sample.write_text(json.dumps(data), encoding="utf-8")
    original = sample.read_bytes()

    def forbid(*_, **__):
        pytest.fail("A command invoked network or executed a tool")

    monkeypatch.setattr(socket, "socket", forbid)
    monkeypatch.setattr(socket, "create_connection", forbid)
    monkeypatch.setattr(socket, "getaddrinfo", forbid)
    monkeypatch.setattr(subprocess, "Popen", forbid)
    monkeypatch.setattr(os, "system", forbid)

    inspected = run("inspect", sample.name, "--workspace", str(sandbox))
    assert inspected["schema_version"] == "cbcf/1.0"
    assert inspected["counts"]["tool_calls"] == 1
    assert inspected["redaction_matches"] == 1
    assert SECRET not in json.dumps(inspected)
    result = run("import", sample.name, "--to", "codex")
    assert result["status"] == "preview"
    assert result["destination_kind"] == "portable_handoff"
    assert result["artifact_name"] == "contextbridge-handoff.md"
    assert sorted(p.name for p in sandbox.iterdir()) == [sample.name]
    written = run("import", sample.name, "--to", "codex", "--apply", "--exclude", "tool_calls")
    assert written["status"] == "written"
    assert written["omitted"]["tool_calls"] == 1
    handoff = sandbox / written["artifact_name"]
    assert "[manual]" in handoff.read_text(encoding="utf-8")
    assert "[extracted]" in handoff.read_text(encoding="utf-8")
    assert SECRET not in handoff.read_text(encoding="utf-8")
    assert "SHOULD_NEVER_RUN" not in handoff.read_text(encoding="utf-8")
    assert sample.read_bytes() == original
    assert not (sandbox / "mock-agent-state").exists()


def test_cli_native_export_then_round_trip(sandbox: Path) -> None:
    original = sandbox / "synthetic.jsonl"
    original.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "synthetic-session",
                "message": {"content": "Continue the project."},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = sandbox / "local.cbcf.json"
    arguments = [
        "export",
        "--from",
        "claude",
        "--source",
        original.name,
        "--session",
        "synthetic-session",
        "--output",
        output.name,
    ]
    assert run(*arguments, "--dry-run")["status"] == "preview"
    assert not output.exists()
    assert run(*arguments)["included"]["messages"] == 1
    doc = load_document(output)
    assert doc.provenance.source_id == "claude"
    assert doc.messages[0].parts[0].text == "Continue the project."
    assert (
        run("convert", output.name, "--to-format", "markdown", "--output", "draft.md", "--dry-run")[
            "status"
        ]
        == "preview"
    )
    assert not (sandbox / "draft.md").exists()
    assert (
        run("convert", output.name, "--to-format", "handoff-md", "--output", "draft.md")["status"]
        == "written"
    )
    assert "Continue the project." in (sandbox / "draft.md").read_text(encoding="utf-8")
    assert (
        run("import", output.name, "--to", str(sandbox), "--format", "cbcf", "--apply")["status"]
        == "written"
    )
    assert output.read_bytes() == (sandbox / "contextbridge-handoff.cbcf.json").read_bytes()


@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        (["export", "--from", "claude", "--output", "out.cbcf.json"], 2, "requires an explicit"),
        (
            [
                "export",
                "--from",
                "src.cbcf.json",
                "--source",
                "s.json",
                "--output",
                "out.cbcf.json",
            ],
            2,
            "only with an agent",
        ),
        (
            [
                "export",
                "--from",
                "src.cbcf.json",
                "--session",
                "missing",
                "--output",
                "out.cbcf.json",
            ],
            2,
            "session ID",
        ),
        (
            ["export", "--from", "cursor", "--source", "state.db", "--output", "out.cbcf.json"],
            2,
            "Native sessions are unsupported",
        ),
        (
            ["convert", "src.cbcf.json", "--to-format", "cbcf", "--output", "wrong.md"],
            2,
            "must end in",
        ),
        (
            ["convert", "src.cbcf.json", "--to-format", "cbcf", "--output", "src.cbcf.json"],
            4,
            "already exists",
        ),
        (
            ["convert", "src.cbcf.json", "--to-format", "handoff-md", "--output", ".codex/out.md"],
            3,
            "outside agent",
        ),
        (
            [
                "convert",
                "src.cbcf.json",
                "--to-format",
                "handoff-md",
                "--output",
                "out.md",
                "--include",
                "skills",
                "--exclude",
                "skills",
            ],
            2,
            "both included and excluded",
        ),
        (
            ["import", "src.cbcf.json", "--to", "cursor", "--dry-run", "--apply"],
            2,
            "either --apply or --dry-run",
        ),
        (
            ["import", "src.cbcf.json", "--to", ".", "--output", "my.md"],
            2,
            "Use --output with an agent",
        ),
        (["inspect", "malformed.cbcf.json"], 2, "CBCF schema validation failed"),
    ],
)
def test_cli_json_errors_are_safe(sandbox: Path, args, code, message) -> None:
    source = sandbox / "src.cbcf.json"
    source.write_bytes(EXAMPLE.read_bytes())
    malformed = sandbox / "malformed.cbcf.json"
    payload = json.loads(source.read_bytes())
    payload["messages"] = [{"role": SECRET}]
    malformed.write_text(json.dumps(payload), encoding="utf-8")
    result = run(*args, code=code)
    assert result["status"] == "error" and result["exit_code"] == code
    assert message in result["message"]
    assert SECRET not in json.dumps(result)


def test_cli_text_output_and_custom_portable_path(sandbox: Path) -> None:
    input_file = sandbox / "src.cbcf.json"
    input_file.write_bytes(EXAMPLE.read_bytes())
    command = [
        "context",
        "import",
        input_file.name,
        "--to",
        "cursor",
        "--output",
        "custom.md",
        "--apply",
    ]
    result = runner.invoke(app, command)
    assert result.exit_code == 0 and "Context written." in result.stdout
    assert (sandbox / "custom.md").is_file()
    duplicate = runner.invoke(app, command)
    assert duplicate.exit_code == 4 and "already exists" in duplicate.stderr


@pytest.mark.parametrize("command", ["inspect", "export", "import", "convert"])
def test_context_help_explains_command(command: str) -> None:
    result = runner.invoke(app, ["context", command, "--help"])
    assert result.exit_code == 0
    help_text = Text.from_ansi(result.stdout).plain
    assert "--json" in help_text and "--workspace" in help_text


def test_context_entry_point_in_subprocess(sandbox: Path) -> None:
    subprocess_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "contextbridge",
            "context",
            "convert",
            str(EXAMPLE),
            "--to-format",
            "handoff-md",
            "--output",
            "example.md",
            "--json",
        ],
        cwd=sandbox,
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    assert json.loads(subprocess_result.stdout)["status"] == "written"
    expected = EXAMPLE.with_name("context-handoff.md").read_text(encoding="utf-8")
    assert (sandbox / "example.md").read_text(encoding="utf-8") == expected
