# Contributing to ContextBridge

Thank you for your interest in contributing to **ContextBridge**!

## Development Setup

ContextBridge targets **Python 3.11+**.

```bash
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
```

## Quality Checks

Before opening a pull request, please ensure all checks pass locally:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m build
```

## Coding & Architecture Guidelines

- Keep logic modular: separate models, adapters, security guards, skills processing, backup management, and CLI presentation.
- **Never invent** agent paths, compatibility claims, or installation mechanisms—cite official documentation in `docs/RESEARCH.md` and `docs/COMPATIBILITY.md`.
- Maintain **read-only safety**: inspection commands (`scan`, `skills list`, `skills validate`, `doctor`, `backup list`) must never create or modify files.
- Keep tests isolated using `tmp_path` and injected `EnvironmentContext`; never touch real user agent configurations in tests.
- Follow the security principles documented in `SECURITY.md`.

## Adding a New Agent Adapter

1. Verify official paths across Windows, Linux, and macOS (global and project scopes) and document sources in `docs/RESEARCH.md`.
2. Subclass `BaseAgentAdapter` in `src/contextbridge/adapters/`.
3. Register the adapter in `src/contextbridge/adapters/registry.py`.
4. Add unit and integration tests covering detection, skill discovery, deduplication, and migration compatibility.
5. Update `docs/COMPATIBILITY.md`.
