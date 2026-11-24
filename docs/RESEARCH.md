# Technical Research & Sources (v0.1.0)

This document records the technical research conducted prior to implementing ContextBridge v0.1.0, including official specifications, verified filesystem paths, OS differences, ecosystem competitors, PyPI naming conflicts, and explicit gaps/unconfirmed items.

## 1. Agent Skills Open Specification (`agentskills.io`)

- **Structure**: A skill is a directory named identically to its skill identifier containing a required `SKILL.md` file and optional subdirectories (`scripts/`, `references/`, `assets/`).
- **Progressive Disclosure**: Agents index YAML frontmatter (`name`, `description`) at startup and load full Markdown instructions and bundled resources on demand.
- **Frontmatter Fields**:
  - `name` *(required)*: <= 64 characters; lowercase `a-z`, `0-9`, single hyphens `-`; no leading/trailing/consecutive hyphens; must match parent directory name.
  - `description` *(required)*: non-empty, <= 1024 characters; describes what the skill does and when to activate it.
  - `license` *(optional)*: license name or reference to bundled license file.
  - `compatibility` *(optional)*: <= 500 characters; host/runtime requirements.
  - `metadata` *(optional)*: arbitrary key-value metadata mapping (`author`, `version`, etc.).
  - `allowed-tools` *(optional, experimental)*: pre-approved tool list/string; support varies across agents.

## 2. Verified Agent Configuration & Skill Paths

### 2.1 Claude Code
- **Project**: `.claude/skills/<skill>/SKILL.md`
- **Global**: `~/.claude/skills/<skill>/SKILL.md` (override: `CLAUDE_CONFIG_DIR`)
- **Cross-OS**: Uses `~/.claude` (`%USERPROFILE%\.claude` on Windows, `/Users/<user>/.claude` on macOS, `/home/<user>/.claude` on Linux).
- **Agent-specific frontmatter**: `disable-model-invocation`, `user-invocable`, `context` (`fork`), `agent`, `argument-hint`, `allowed-tools`, `model`.
- **Shared dirs**: Does **not** natively scan `.agents/skills` without explicit symlinks or plugins.

### 2.2 OpenAI Codex
- **Project**: `.agents/skills/<skill>/SKILL.md`
- **Global**: `~/.agents/skills/<skill>/SKILL.md`; `~/.codex` (`CODEX_HOME`) stores `config.toml` and state, and `~/.codex/skills` is commonly used/configured as a secondary path.
- **Cross-OS**: `~/.codex` and `~/.agents` resolve relative to the user home directory across Windows, macOS, and Linux.
- **Limitations**: Custom `[[skills.config]]` mappings in `config.toml` are not parsed in v0.1.0.

### 2.3 Google Antigravity
- **Project**: `.agents/skills/<skill>/SKILL.md` (primary); `.agent/skills/<skill>/SKILL.md` (backward-compatibility alias; `_agents/` and `_agent/` also documented in workspace discovery).
- **Global**: `~/.gemini/config/skills/<skill>/SKILL.md` (documented in Antigravity Customization System guide); `~/.gemini/antigravity/skills/` observed in some app-data layouts; `~/.agents/skills/` used for CLI scope.
- **Limitations**: Workspace `skills.json` / `plugins.json` explicit declarations and built-in mounted skills are not parsed in v0.1.0.

### 2.4 OpenCode
- **Project**: `.opencode/skills/<skill>/SKILL.md` (native), `.agents/skills/` (shared), `.claude/skills/` (compatibility).
- **Global**: `~/.config/opencode/skills/` (native; honors `OPENCODE_CONFIG_DIR` and `XDG_CONFIG_HOME`), `~/.agents/skills/`, `~/.claude/skills/`.
- **Cross-OS**: Uses XDG-style `~/.config/opencode` across Linux/macOS/Windows V2 CLI; legacy Windows wrappers may inspect `%APPDATA%\opencode`.

### 2.5 Gemini CLI
- **Project**: `.gemini/skills/<skill>/SKILL.md` (native), `.agents/skills/` (shared alias).
- **Global**: `~/.gemini/skills/<skill>/SKILL.md` (native; honors `GEMINI_CLI_HOME` as `<GEMINI_CLI_HOME>/.gemini`), `~/.agents/skills/` (shared alias).
- **Note**: Workspace skills require marking the project folder trusted (`/trust`). Gemini CLI also offers `gemini skills install <repo>` with explicit consent prompts.

### 2.6 Cursor
- **Project**: `.cursor/skills/` (native), `.agents/skills/` (shared), `.claude/skills/` and `.codex/skills/` (compatibility).
- **Global**: `~/.cursor/skills/` (native), `~/.agents/skills/` (shared), `~/.claude/skills/` and `~/.codex/skills/` (compatibility).
- **Limitations**: Cursor supports nested monorepo skill paths (e.g., `apps/web/.cursor/skills/`); v0.1.0 only scans the workspace root.

## 3. Shared Directory Overlap Analysis

| Shared Directory | Agents Scanning It |
| --- | --- |
| `<project>/.agents/skills` | OpenAI Codex, Google Antigravity, OpenCode, Gemini CLI, Cursor |
| `~/.agents/skills` | OpenAI Codex, Google Antigravity (CLI), OpenCode, Gemini CLI, Cursor |
| `<project>/.claude/skills` | Claude Code (native), OpenCode (compat), Cursor (compat) |
| `~/.claude/skills` | Claude Code (native), OpenCode (compat), Cursor (compat) |
| `<project>/.codex/skills` | Cursor (compat) |
| `~/.codex/skills` | OpenAI Codex (`CODEX_HOME`/compat), Cursor (compat) |

This overlap is the direct architectural reason ContextBridge deduplicates skills by canonical resolved physical path rather than summing per-agent directory listings.

## 4. Ecosystem & Competitor Landscape

| Tool | Model | Primary Focus | How ContextBridge Differs |
| --- | --- | --- | --- |
| `npx skills` (`vercel-labs/skills`) | Node.js / Registry CLI (`skills.sh`) | Remote package registry discovery & installation | ContextBridge is 100% offline/local, Python-native, focused on validation, safe migration, SHA-256 backups, and diagnostics without a remote registry. |
| `openskills` | Node.js CLI loader | Injecting `<available_skills>` XML into `AGENTS.md` | ContextBridge manages native agent skill directories directly without rewriting `AGENTS.md`. |
| `ruler` | Node.js rule compiler | Compiling centralized `.ruler/` rules to agent rule files | ContextBridge v0.1.0 focuses on Agent Skills (`SKILL.md`), physical deduplication, security validation, and atomic migration/backups. |
| `asm` (`agent-skill-manager`) | Security-focused CLI | Auditing & syncing skills | ContextBridge emphasizes formal Agent Skills spec validation, transactional 10-step migration with SHA-256 rollback, and zero-telemetry local workflows. |
| `apm-cli` | Python manifest/lockfile CLI | Deterministic enterprise lockfile setups | ContextBridge is zero-lockfile and works immediately against existing agent directories on disk. |

## 5. PyPI Package Name Conflict Analysis

A read-only query against the PyPI JSON API (`https://pypi.org/pypi/<name>/json`) confirmed:

| Candidate Name | PyPI Status | Existing Package Summary |
| --- | --- | --- |
| `context-bridge` | **TAKEN (HTTP 200)** | v0.2.1 — RAG documentation workflows (collides with `contextbridge` under PEP 503 name normalization) |
| `contextbridge-cli` | **TAKEN (HTTP 200)** | v0.3.3 — Project memory CLI for AI coding sessions |
| `contextbridge-agents` | **AVAILABLE (HTTP 404)** | No package registered |
| `agent-contextbridge` | **AVAILABLE (HTTP 404)** | No package registered |

**Decision**: Keep **ContextBridge** as the project identity, `contextbridge` as the Python import package and CLI command, and use **`contextbridge-agents`** as the exclusive PyPI distribution name in `pyproject.toml` (not registered or published in v0.1.0).

## 6. Unconfirmed / Variable Items

- **Native one-click ZIP/archive import**: While `.zip` / `.skill` archives are widely used to distribute skill folders for manual extraction into each agent's `skills/` directory, uniform native CLI ZIP import commands are not officially documented across all six agents. Therefore, ContextBridge v0.1.0 exports standard portable Agent Skills ZIPs and explicitly documents manual extraction.
- **Linux and macOS runtime verification**: Local development and testing of v0.1.0 occurred on Windows; Linux and macOS path handling is implemented cleanly via `pathlib` + `EnvironmentContext` and verified via GitHub Actions CI matrix rather than claimed as locally tested.
- **Desktop binary paths**: App installation locations (e.g., Cursor on Linux AppImage/Flatpak or custom Windows install paths) vary widely; detection checks `PATH` plus standard macOS/Windows Cursor paths and reports `config_only` when directories exist without a detected executable.
