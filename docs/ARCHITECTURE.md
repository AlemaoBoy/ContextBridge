# ContextBridge Architecture (v0.1.0)

## 1. Design Principles

ContextBridge is designed around five non-negotiable architectural invariants:

1. **Local-First & Zero Telemetry**: All operations execute locally over the filesystem with zero external network calls, no background daemons, and no telemetry.
2. **Read-Only Inspection Guarantee**: Inspection commands (`scan`, `skills list`, `skills validate`, `doctor`, `backup list`) never create directories or modify files on disk.
3. **Defense-in-Depth Security**: All paths, archives, YAML frontmatter, symlinks/junctions, and credential files are validated before any read, export, or write. Skill scripts and MCP servers are never executed.
4. **Transactional Safety**: Mutations default to `--dry-run`, prohibit silent overwrites, stage files atomically into a sibling directory, verify SHA-256 digests, back up existing targets first, and roll back cleanly if any write step fails.
5. **Testability by Dependency Injection**: Every agent adapter and core service receives an immutable `EnvironmentContext` (`home_dir`, `project_dir`, `env`, `platform`, `which`, `state_dir_override`), allowing deterministic cross-platform tests without touching real user agent configurations.

## 2. Layered Module Architecture

```text
+---------------------------------------------------------------+
|                         CLI Layer                             |
|       cli.py (Typer) + utils/console.py (Rich / JSON)         |
+---------------------------------------------------------------+
                                |
                                v
+---------------------------------------------------------------+
|                   Application / Core Layer                    |
|  core/discovery.py  |  core/doctor.py  |  skills/migrator.py  |
|  skills/exporter.py |  skills/validator.py | backup/manager.py|
+---------------------------------------------------------------+
          |                     |                      |
          v                     v                      v
+-------------------+  +------------------+  +------------------+
|  Adapters Layer   |  |  Security Layer  |  |   Models Layer   |
|  adapters/base.py |  |  security/paths  |  |  models/agent    |
|  claude, codex,   |  |  security/secrets|  |  models/skill    |
|  antigravity,     |  |  security/archive|  |  models/migration|
|  opencode,        |  |  security/audit  |  |  models/backup   |
|  gemini_cli,      |  |  security/limits |  |  models/doctor   |
|  cursor + registry|  |                  |  |                  |
+-------------------+  +------------------+  +------------------+
                                |
                                v
+---------------------------------------------------------------+
|                       Utilities Layer                         |
|         utils/fs.py (atomic writes, junction check)           |
|         utils/hashing.py (streaming SHA-256)                  |
+---------------------------------------------------------------+
```

## 3. Core Flows

### 3.1 Agent Detection & Shared-Directory Deduplication

Multiple agents scan overlapping directories (notably `.agents/skills` and `~/.agents/skills`, plus `.claude/skills` compatibility paths in OpenCode and Cursor).

- Each adapter implements `BaseAgentAdapter.skill_directories(env, scope)` returning typed `SkillDirectorySpec` entries tagged with `is_primary`, `is_shared_standard`, and `is_compatibility_alias`.
- `core/discovery.py` walks existing readable directories across all adapters and indexes skills by `canonical_path_key(entry)` (`os.path.normcase(os.path.normpath(os.path.realpath(path)))`).
- A single physical skill directory is validated **once** and emitted **once** as a `SkillRecord`, listing both native `associated_agents` and `compatibility_agents`.

### 3.2 Safe Validation Pipeline

`skills/validator.py` evaluates skills strictly read-only:

1. Root checks: directory exists, is not a symlink or Windows junction, exact `SKILL.md` filename check.
2. Frontmatter parsing (`skills/parser.py`): checks size limit (1 MiB), decodes UTF-8, limits YAML anchors/aliases, parses strictly with `yaml.safe_load`, validates schema via Pydantic `SkillFrontmatter`.
3. Agent Skills Spec rules (`agentskills.io`): `name` format (`^[a-z0-9]+(?:-[a-z0-9]+)*$`, <= 64 chars, exact match with parent directory), `description` (non-empty, <= 1024 chars), `compatibility` (<= 500 chars), informational notices for `allowed-tools` and agent-specific frontmatter fields.
4. Local reference checks: extracts Markdown links outside fenced code blocks, rejects path traversal/absolute paths, verifies target files exist inside the skill root.
5. Filesystem & heuristic security checks: rejects symlinks/junctions, enforces file size/count/depth limits, scans for sensitive credential filenames and regex-based secret tokens (never exposing values), and flags risky shell patterns as heuristic warnings.

### 3.3 10-Step Atomic Migration Flow

`skills/migrator.py` implements:

1. Locate skill in source agent directories.
2. Validate source skill.
3. Evaluate target compatibility warnings and agent limitations.
4. Detect conflicts (existing destination) or shared physical path (no-op).
5. Present complete `MigrationPlan` (file list, sizes, SHA-256 digests, compatibility notes).
6. Require explicit `--apply` (and confirmation / `--yes`); default is `--dry-run`.
7. Create a verified SHA-256 local backup via `BackupManager` before overwriting any existing target.
8. Copy files into a temporary sibling staging directory inside the authorized target root, verifying SHA-256 of each file, then atomically swap (`os.replace`) into the live target.
9. Re-verify SHA-256 hashes on the live destination directory.
10. Clean up displaced target or roll back to original state if any error occurs.

### 3.4 Backup & Restore Subsystem

Backups live under `~/.contextbridge/backups/<backup_id>/` (overrideable via `CONTEXTBRIDGE_HOME`):

```text
<backup_id>/
├── manifest.json   # Schema version, UTC timestamp, operation, source_path, file SHA-256 list
└── payload/        # Exact preserved directory tree
```

Restorations verify every payload file hash against `manifest.json` (and reject unexpected extra files or tampered payloads), enforce authorized destination checks independently of the manifest (refusing skills root directories, ancestors, system paths, or arbitrary non-skill folders), default to dry-run preview, take an automatic `pre-restore` snapshot if the destination currently exists, and swap atomically without deleting surviving recovery files if rollback fails. Note that local SHA-256 manifests verify integrity against corruption and drift, not cryptographic authenticity against an attacker with local write access to the backup store.

## 4. Exit Codes

| Exit Code | Meaning |
| --- | --- |
| `0` | Success (or dry-run preview completed) |
| `1` | Generic error or `doctor` reported error findings |
| `2` | Invalid arguments, unknown agent, skill not found, validation failure, or missing confirmation |
| `3` | Security policy violation (path traversal, symlink/junction, ZIP Slip/bomb, secret exposure, size limit) |
| `4` | Migration destination conflict without `--overwrite` |
| `5` | Filesystem write, migration execution, or backup error |
| `6` | Backup SHA-256 integrity verification failure |
