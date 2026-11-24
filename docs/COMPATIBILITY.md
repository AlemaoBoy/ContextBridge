# Compatibility Matrix (v0.1.0)

This matrix reflects only behaviors verified against official documentation and implemented in ContextBridge v0.1.0.

> **Portability vs. Runtime Behavior:** Migrating a skill directory preserves files conforming to the [Agent Skills](https://agentskills.io) standard (`SKILL.md`, `scripts/`, `references/`, `assets/`). It does **not** guarantee identical runtime behavior, tool approval prompts, sandboxing, or invocation triggers across different AI coding agents.

## Supported Agents Overview

| Agent | Adapter ID | CLI / App Detection | Project Scope | Global Scope | Shared `.agents/skills` | ZIP Export Format |
| --- | --- | --- | --- | --- | --- | --- |
| **Claude Code** | `claude` | `claude` on `PATH` | `.claude/skills` | `~/.claude/skills` (`$CLAUDE_CONFIG_DIR/skills`) | No (native only) | Standard Agent Skills ZIP (manual extract) |
| **OpenAI Codex** | `codex` | `codex` on `PATH` | `.agents/skills` | `~/.agents/skills`, `~/.codex/skills` (`$CODEX_HOME/skills`) | Yes (primary) | Standard Agent Skills ZIP (manual extract) |
| **Google Antigravity** | `antigravity` | `agy`, `antigravity` on `PATH`, macOS `/Applications/Antigravity.app`, Windows `%LOCALAPPDATA%\Programs\Antigravity\Antigravity.exe` | `.agents/skills`, `.agent/skills` | `~/.gemini/config/skills`, `~/.gemini/antigravity/skills`, `~/.agents/skills` | Yes (primary project / CLI global) | Standard Agent Skills ZIP (manual extract) |
| **OpenCode** | `opencode` | `opencode` on `PATH` | `.opencode/skills`, `.agents/skills`, `.claude/skills` | `~/.config/opencode/skills` (`$OPENCODE_CONFIG_DIR/skills`), `~/.agents/skills`, `~/.claude/skills` | Yes | Standard Agent Skills ZIP (manual extract) |
| **Gemini CLI** | `gemini-cli` | `gemini` on `PATH` | `.gemini/skills`, `.agents/skills` | `~/.gemini/skills` (`$GEMINI_CLI_HOME/.gemini/skills`), `~/.agents/skills` | Yes | Standard Agent Skills ZIP (manual extract) |
| **Cursor** | `cursor` | `cursor`, `cursor-agent` on `PATH`, macOS `/Applications/Cursor.app`, Windows `%LOCALAPPDATA%\Programs\cursor\Cursor.exe` | `.cursor/skills`, `.agents/skills`, `.claude/skills`, `.codex/skills` | `~/.cursor/skills`, `~/.agents/skills`, `~/.claude/skills`, `~/.codex/skills` | Yes | Standard Agent Skills ZIP (manual extract) |

## Frontmatter Field Portability

| Frontmatter Field | Spec Status | Notes Across Agents |
| --- | --- | --- |
| `name` | Required (Agent Skills) | Universally supported; must match directory name. |
| `description` | Required (Agent Skills) | Universally supported; used for progressive-disclosure discovery. |
| `license` | Optional (Agent Skills) | Metadata only; safe across agents. |
| `compatibility` | Optional (Agent Skills) | Informational runtime requirement hint. |
| `metadata` | Optional (Agent Skills) | Key-value metadata mapping; recommended location for `author` and `version`. |
| `allowed-tools` | Optional (Experimental) | Support varies by client; ContextBridge emits an informational notice during validation and migration. |
| `disable-model-invocation`, `user-invocable`, `context`, `agent`, `argument-hint`, `model` | Claude Code extensions | Supported by Claude Code; likely ignored by other agents. ContextBridge surfaces compatibility warnings when migrating or when detected inside shared `.agents/skills`. |

## Operating System Support

| OS | Status in v0.1.0 | Notes |
| --- | --- | --- |
| **Windows** | Implemented & locally tested | Includes Windows reserved filename checks (`CON`, `NUL`, `COM1..9`, `LPT1..9`), NTFS Alternate Data Stream (`:`) and trailing dot/space blocking, drive/UNC path blocking, case-insensitive collision checks, and reparse point / junction detection. |
| **Linux** | Implemented; CI configured | Honors `XDG_CONFIG_HOME` and POSIX symlink checks. Not executed locally during v0.1.0 development; covered by GitHub Actions `ubuntu-latest`. |
| **macOS** | Implemented; CI configured | Checks `/Applications/Cursor.app` read-only and POSIX symlinks. Not executed locally during v0.1.0 development; covered by GitHub Actions `macos-latest`. |

## Known Pending / Unconfirmed Capabilities

For local session support in the v0.2.0 development tree, see [offline context compatibility](PROVIDER_COMPATIBILITY.md#3-coding-agent-local-session--context-capabilities). The directory table above describes Agent Skills only.

- Parsing custom `[[skills.config]]` paths from Codex's `config.toml`.
- Parsing Antigravity's workspace `skills.json` / `plugins.json` and `_agents/` / `_agent/` legacy folders.
- Walking nested monorepo `.cursor/skills/` directories in Cursor.
- Inspecting Gemini CLI extension-bundled skills and `/trust` state.
- Native one-click ZIP import commands per agent (not uniformly documented).
