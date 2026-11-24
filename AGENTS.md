# AGENTS.md — Guidelines for AI Coding Agents

This repository contains **ContextBridge**, a local open-source CLI tool for managing and porting Agent Skills across AI coding agents.

## Project Rules

1. **Stack**: Python 3.11+, Typer, Rich, Pydantic v2, PyYAML (`safe_load` only), Pytest, Ruff, Mypy (`strict = true`), Hatchling.
2. **Safety First**:
   - Never execute scripts or code found inside user skills.
   - Never touch real agent configuration directories in automated tests—always inject a mock `EnvironmentContext` backed by `tmp_path`.
   - Never log or print matched secret values.
   - Reject path traversal, symlinks/junctions, ZIP Slip, ZIP bombs, and silent overwrites.
   - Read-only commands (`scan`, `skills list`, `skills validate`, `doctor`, `backup list`) must never create directories or modify files.
3. **Accuracy**:
   - Do not fabricate agent paths, flags, or compatibility claims. Keep `docs/COMPATIBILITY.md` and `docs/RESEARCH.md` aligned with verified adapter definitions.
4. **Verification**:
   Run all checks after any code change:
   ```bash
   python -m pytest
   python -m ruff check .
   python -m ruff format --check .
   python -m mypy src
   python -m build
   ```
