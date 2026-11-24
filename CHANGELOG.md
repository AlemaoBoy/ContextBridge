# Changelog

All notable changes to **ContextBridge** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Offline `context inspect`, `export`, `import`, and `convert` with typed CBCF 1.0, selective transfer, and deterministic Markdown and CBCF handoffs.
- Read-only support for explicitly supplied Claude Code JSONL, OpenCode session export JSON, and Gemini CLI JSON/JSONL. Manual CBCF and portable handoffs work with all six agent IDs.
- Bounded JSON parsing, automatic heuristic redaction, portable file reference checks, atomic output without overwrites, and individual CBCF coverage gates.

## [0.1.0] - 2026-11-24

### Added

- Read-only agent detection (`contextbridge scan`) supporting **Claude Code**, **OpenAI Codex**, **Google Antigravity**, **OpenCode**, **Gemini CLI**, and **Cursor**, distinguishing installed binaries, existing config directories, and missing paths.
- Physically deduplicated skill listing (`contextbridge skills list`, `--agent`, `--scope`, `--json`) preventing double-counting of shared directories such as `.agents/skills`.
- Official Agent Skills specification (`agentskills.io`) validator (`contextbridge skills validate`) with safe `yaml.safe_load` parsing, local Markdown reference checks, directory structure validation, and static heuristic security auditing.
- Portable ZIP exporter (`contextbridge skills export <path> --format zip`) preserving `SKILL.md`, `scripts/`, `references/`, and `assets/` with deterministic metadata, ZIP safety verification, and credential/symlink blocking.
- 10-step skill migration workflow (`contextbridge skills migrate <skill> --from <src> --to <dst>`) with dry-run preview by default, explicit `--apply` confirmation, conflict protection (`--overwrite`), automatic SHA-256 backups, post-write integrity verification, and atomic rollback on write failures.
- Local backup management (`contextbridge backup list`, `contextbridge backup restore <id>`) with SHA-256 manifests, tamper detection, preview mode, and atomic restoration.
- Read-only environment diagnostics (`contextbridge doctor`) checking runtime, permissions, invalid skills, duplicate skill conflicts, cross-agent frontmatter caveats, and local backup integrity.
- Security hardening against path traversal, ZIP Slip, ZIP bombs, symlinks and Windows junctions, unsafe YAML deserialization, secret/credential exposure, oversized files, and silent overwrites.
- Bilingual documentation (`README.md`, `README.pt-BR.md`), architecture and research docs (`docs/`), example skill (`examples/sample-skill`), and multi-OS GitHub Actions CI matrix.
