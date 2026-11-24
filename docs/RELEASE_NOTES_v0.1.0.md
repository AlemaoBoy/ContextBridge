# ContextBridge v0.1.0 — Release Notes

**Your context. Any agent. No lock-in.**

ContextBridge **v0.1.0** is the initial open-source release of a 100% local, cross-platform CLI for discovering, validating, exporting, and safely migrating **Agent Skills** (`SKILL.md`) across AI coding agents—with zero telemetry and no external services.

> **Disclaimer:** ContextBridge is an independent open-source project and is not affiliated with Anthropic, OpenAI, Google, Cursor, or OpenCode.

## Highlights

- **Read-Only Agent Detection (`contextbridge scan`)**: Detects **Claude Code**, **OpenAI Codex**, **Google Antigravity**, **OpenCode**, **Gemini CLI**, and **Cursor**, distinguishing installed executables, existing configuration directories, and missing paths without ever creating directories.
- **Physical-Path Deduplication (`contextbridge skills list`)**: Eliminates double-counting across shared skill directories (such as `.agents/skills` and `.claude/skills`), with `--agent`, `--scope`, and `--json` support.
- **Official Agent Skills Validator (`contextbridge skills validate`)**: Validates `SKILL.md` against the [agentskills.io](https://agentskills.io) specification using a hardened `yaml.SafeLoader` (blocking duplicate keys, excessive nesting depth, and alias bombs), verifies local Markdown references, and runs static heuristic security checks without executing scripts.
- **Safe Portable ZIP Export (`contextbridge skills export --format zip`)**: Creates deterministic Agent Skills ZIP archives verified against ZIP Slip, ZIP bombs, symlinks, credentials, and silent overwrites (`--overwrite` required to replace existing archives).
- **Transactional 10-Step Migration (`contextbridge skills migrate`)**: Dry-run preview by default, explicit `--apply` confirmation (`--yes` for non-interactive runs), TOCTOU source and destination verification between plan and apply, automatic local backups before `--overwrite`, post-swap SHA-256 verification, and non-destructive rollback reporting.
- **Verified Local Backups (`contextbridge backup list`, `contextbridge backup restore`)**: Local backups with `manifest.json`, SHA-256 integrity verification, strict authorized restore target boundaries (blocking tampered manifests, skills roots, ancestors, system paths, and non-skill directories), preview mode, and pre-restore safety snapshots.
- **Read-Only Diagnostics (`contextbridge doctor`)**: Checks runtime health, permissions, invalid skills, duplicate skill names across scopes/agents, agent-specific frontmatter in shared directories, and backup store integrity.

## Security & Privacy

- 100% local execution, zero telemetry, zero network calls at runtime.
- Never executes skill scripts, MCP servers, or dynamic Python plugins.
- Protects against path traversal, symlinks/Windows junctions, NTFS Alternate Data Streams (`:`), Windows reserved device names, case-insensitive path collisions, ZIP Slip, ZIP bombs, unsafe YAML, silent overwrites, and credential exposure (never printing matched secret values).
- Static checks are explicitly presented as heuristic aids, and unsigned local SHA-256 manifests verify integrity against corruption/drift rather than cryptographic authenticity.

## Known Limitations

- Local v0.1.0 verification was executed on **Windows (Python 3.15.2)**; Ubuntu, macOS, and Python `3.11`–`3.14` are configured in GitHub Actions CI.
- Advanced agent-specific config parsers (Codex `config.toml`, Antigravity `skills.json`, Cursor nested monorepo folders, Gemini CLI `/trust` & extension skills) and future capabilities (MCP migration, `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` conversion, sync, compaction, GUI) are planned for future releases.
- Distribution package name is **`contextbridge-agents`** (`pip install -e .` from source in v0.1.0; not published to PyPI yet).
