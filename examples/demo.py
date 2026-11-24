"""Reproducible synthetic demonstration of ContextBridge v0.1.0.

Creates an isolated temporary workspace with synthetic agent directories and skills
(never touching real user configurations) and executes real `contextbridge` CLI
subprocesses for:

1. `contextbridge scan`
2. `contextbridge skills list`
3. `contextbridge skills validate`
4. `contextbridge skills migrate` (dry-run preview)
5. `contextbridge skills migrate --apply --yes` (authorized apply in temp dir)
6. `contextbridge doctor`
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DemoStepResult:
    """Captured output of a real ContextBridge CLI command during the demo."""

    title: str
    display_command: str
    returncode: int
    stdout: str
    stderr: str


def _seed_synthetic_workspace(project_dir: Path, home_dir: Path) -> None:
    """Populate isolated synthetic agent directories and valid Agent Skills."""
    (home_dir / ".claude").mkdir(parents=True, exist_ok=True)
    (home_dir / ".codex").mkdir(parents=True, exist_ok=True)

    # Skill 1: Claude Code native project skill with local references & helper script
    release_skill = project_dir / ".claude" / "skills" / "release-checklist"
    (release_skill / "references").mkdir(parents=True, exist_ok=True)
    (release_skill / "scripts").mkdir(parents=True, exist_ok=True)
    (release_skill / "SKILL.md").write_text(
        "---\n"
        "name: release-checklist\n"
        "description: >-\n"
        "  Guides developers through a safe pre-release QA checklist for Python packages.\n"
        "  Use when preparing a release or verifying local packaging checks.\n"
        "license: MIT\n"
        "compatibility: Requires Python 3.11+\n"
        "metadata:\n"
        "  author: demo-team\n"
        '  version: "1.0.0"\n'
        "---\n\n"
        "# Release Checklist\n\n"
        "1. Review [checklist.md](./references/checklist.md).\n"
        "2. Inspect [check_env.py](./scripts/check_env.py) before running manually.\n",
        encoding="utf-8",
    )
    (release_skill / "references" / "checklist.md").write_text(
        "# Pre-Release Checklist\n\n- Run pytest, ruff, mypy, build, and twine check.\n",
        encoding="utf-8",
    )
    (release_skill / "scripts" / "check_env.py").write_text(
        '"""Synthetic demo script (never executed by ContextBridge)."""\nprint("ok")\n',
        encoding="utf-8",
    )

    # Skill 2: Shared `.agents/skills` skill to demonstrate physical deduplication
    shared_skill = project_dir / ".agents" / "skills" / "code-review"
    shared_skill.mkdir(parents=True, exist_ok=True)
    (shared_skill / "SKILL.md").write_text(
        "---\n"
        "name: code-review\n"
        "description: >-\n"
        "  Structured checklist for reviewing pull requests for security and clarity.\n"
        "  Use when reviewing diffs or auditing changes before merge.\n"
        "license: MIT\n"
        "---\n\n"
        "# Code Review\n\n"
        "Check correctness, error handling, tests, and security invariants.\n",
        encoding="utf-8",
    )


def _isolated_env(home_dir: Path, state_dir: Path) -> dict[str, str]:
    """Build a sanitized environment dictionary isolated from host agent configs."""
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["CONTEXTBRIDGE_HOME"] = str(state_dir)
    env["XDG_CONFIG_HOME"] = str(home_dir / ".config")
    env["LOCALAPPDATA"] = str(home_dir / "AppData" / "Local")
    env["APPDATA"] = str(home_dir / "AppData" / "Roaming")
    # Isolate PATH to the active Python environment so host agent binaries do not leak into the demo
    env["PATH"] = str(Path(sys.executable).parent)
    env["COLUMNS"] = "100"
    env["NO_COLOR"] = "1"
    env["PYTHONUTF8"] = "1"
    for var in (
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "OPENCODE_CONFIG_DIR",
        "GEMINI_CLI_HOME",
    ):
        env.pop(var, None)
    return env


def run_demo(*, base_dir: Path | None = None) -> list[DemoStepResult]:
    """Execute the 6-step synthetic ContextBridge demo using real CLI subprocesses."""
    created_root: Path | None = None
    if base_dir is None:
        root = Path(tempfile.mkdtemp(prefix="contextbridge-demo-"))
        created_root = root
    else:
        root = base_dir
        root.mkdir(parents=True, exist_ok=True)

    home_dir = root / "home"
    project_dir = root / "workspace"
    state_dir = root / "state"
    home_dir.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        _seed_synthetic_workspace(project_dir, home_dir)
        env = _isolated_env(home_dir, state_dir)

        steps: list[tuple[str, str, list[str]]] = [
            ("1. Detect Agents (read-only)", "contextbridge scan", ["scan"]),
            ("2. List Deduplicated Skills", "contextbridge skills list", ["skills", "list"]),
            (
                "3. Validate Skill (Agent Skills spec)",
                "contextbridge skills validate .claude/skills/release-checklist",
                ["skills", "validate", ".claude/skills/release-checklist"],
            ),
            (
                "4. Preview Migration (dry-run default)",
                "contextbridge skills migrate release-checklist --from claude --to codex",
                ["skills", "migrate", "release-checklist", "--from", "claude", "--to", "codex"],
            ),
            (
                "5. Apply Authorized Migration",
                (
                    "contextbridge skills migrate release-checklist "
                    "--from claude --to codex --apply --yes"
                ),
                [
                    "skills",
                    "migrate",
                    "release-checklist",
                    "--from",
                    "claude",
                    "--to",
                    "codex",
                    "--apply",
                    "--yes",
                ],
            ),
            ("6. Run Environment Diagnostics", "contextbridge doctor", ["doctor"]),
        ]

        results: list[DemoStepResult] = []
        for title, display_cmd, cli_args in steps:
            proc = subprocess.run(  # noqa: S603
                [sys.executable, "-m", "contextbridge", *cli_args],
                cwd=str(project_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            results.append(
                DemoStepResult(
                    title=title,
                    display_command=display_cmd,
                    returncode=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                )
            )
        return results
    finally:
        if created_root is not None:
            shutil.rmtree(created_root, ignore_errors=True)


def main() -> int:
    """CLI entrypoint for running the reproducible synthetic demo."""
    parser = argparse.ArgumentParser(description="Run the ContextBridge synthetic CLI demo.")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="Optional temporary base directory to run inside (cleaned up manually if provided).",
    )
    args = parser.parse_args()

    results = run_demo(base_dir=args.base_dir)
    exit_code = 0
    for step in results:
        print(f"\n=== {step.title} ===")
        print(f"$ {step.display_command}\n")
        if step.stdout:
            print(step.stdout.rstrip())
        if step.stderr:
            print(step.stderr.rstrip(), file=sys.stderr)
        if step.returncode != 0:
            exit_code = step.returncode
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
