# ContextBridge Roadmap

This document outlines the phased evolution of **ContextBridge** from the **v0.1.0** local skills foundation to a universal, local-first AI context portability engine.

> **Scope Boundary:** Items listed under **v0.2.0 and beyond** are planned architectural proposals and are **not implemented in v0.1.0**.

---

## v0.1.0 (Implemented — Local Skills Foundation)

- Read-only detection for 6 AI coding agents: Claude Code, OpenAI Codex, Google Antigravity, OpenCode, Gemini CLI, and Cursor (`scan`).
- Physically deduplicated skill listing (`skills list`) preventing double-counting across shared `.agents/skills` and compatibility paths.
- Official Agent Skills specification (`agentskills.io`) validator with hardened `yaml.SafeLoader`, local reference checks, and static heuristic security auditing (`skills validate`).
- Deterministic portable ZIP export with ZIP safety verification and overwrite protection (`skills export --format zip`).
- Transactional 10-step migration with dry-run default, TOCTOU guards, SHA-256 verification, automatic local backups, and safe rollback (`skills migrate`).
- Local SHA-256 verified backups with authorized destination enforcement (`backup list`, `backup restore`).
- Read-only environment diagnostics (`doctor`).

---

## v0.2.0 (Proposed — Universal Context Engine & Custom Providers)

Detailed specifications: [CONTEXT_FORMAT.md](CONTEXT_FORMAT.md), [PROVIDER_ARCHITECTURE.md](PROVIDER_ARCHITECTURE.md), [PROVIDER_COMPATIBILITY.md](PROVIDER_COMPATIBILITY.md), [TEST_COVERAGE_PLAN.md](TEST_COVERAGE_PLAN.md).

### Phase 2A — Quality & Coverage Hardening (Pre-Feature Gate)
- Raise v0.1.0 test coverage from `85%` to **`>= 95%`** overall and **`>= 95%`** across `security/`, `backup/`, and `skills/migrator.py` by testing real uncovered branches (no artificial tests).
- Add CI coverage gate (`--cov-fail-under=95`) across Windows, Linux, and macOS.

### Phase 2B — ContextBridge Context Format (CBCF v1.0), Universal Handoff & Offline Transfer
- Implement versioned Pydantic v2 CBCF v1.0 models (`schema_version: "cbcf/1.0"`), provenance differentiation (`api_response`, `agent_local_history`, `manual_export`, `converted_context`), **selective transfer** filters, **Universal Handoff** (CBCF + Markdown differentiating `extracted` vs `manual` items), extension preservation, and explicit lossy-conversion diagnostics.
- Implement **Offline Transfer** CLI commands (zero network calls, zero token cost): `contextbridge context inspect`, `contextbridge context convert`, `contextbridge context export` (read-only local session export for verified formats such as Claude Code JSONL and OpenCode), and `contextbridge context import` (non-executable handoff output).

### Phase 2C — Custom Provider Registry & Built-In Protocol Drivers (Opt-In Network)
- Explicitly separate **Agent Adapters** (local filesystem) from **AI Provider Adapters** (opt-in HTTP) with granular capability declarations (`can_send_context`, `can_receive_response`, `can_retrieve_stored_response`, `can_retrieve_remote_history`, `supports_streaming`, `supports_model_discovery`, `supports_server_session_continuation`).
- Implement `~/.contextbridge/providers.json` declarative configuration with per-provider isolated `env:` and optional OS `keyring:` credential resolution (never storing plaintext keys in repositories or URLs).
- Implement protocol drivers over `httpx` with strict TLS, DNS rebinding + IPv6/IPv4-mapped IPv6 SSRF protection, `trust_env=False` proxy isolation, `follow_redirects=False` redirect credential protection, explicit localhost/private-LAN opt-in, billing-safe zero-retry on generation `POST`, and response/stream-interruption limits:
  - OpenAI Chat Completions (`openai_chat`)
  - OpenAI Responses (`openai_responses`)
  - Anthropic Messages (`anthropic_messages`)
  - Google Gemini (`google_gemini`)
  - Ollama Native (`ollama_native`)
  - OpenAI-Compatible (`openai_compatible`)
  - Declarative Custom HTTP (`custom_http`, data-only mapping, zero arbitrary code execution)
- Implement provider CLI commands: `provider add`, `provider list`, `provider test`, `provider models`, `provider remove`.

### Phase 2D — API Transfer (`context send`, `receive`, `transfer`)
- Implement preview-first, selective, confirmation-gated, secret-redacting **API Transfer** commands:
  - `contextbridge context send`
  - `contextbridge context receive`
  - `contextbridge context transfer` (supporting custom providers as source or target according to declared capabilities)
- Explicit token/cost warnings before remote transmission and strict non-execution of received `tool_calls` and instructions.

---

## v0.3.x (Planned — Instructions, MCP, Synchronization & Profiles)

- **Instruction file conversion**: Structured conversion and diffing between `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and workspace rule files (`.cursor/rules`, `.agents/rules`).
- **MCP server configuration migration**: Safe, secret-redacting migration of MCP server definitions (never executing MCP servers or copying credentials).
- **Extended agent adapter parsing**: Codex `config.toml` (`[[skills.config]]`), Antigravity `skills.json`, and Cursor nested monorepo `.cursor/skills` discovery.
- **Cross-agent synchronization & redundancy detection**: Declarative local sync workflows and detection of duplicated/shadowed skills and rules.
- **Profile management**: Named local profiles (e.g., `work`, `personal`, `minimal`).

---

## v0.4.x (Planned — Smart Transfer, Context Intelligence & Advanced Security)

- **Smart Transfer (optional, consent-gated)**: Model-assisted context compaction and reorganization with explicit opt-in consent, cost disclosure, and before/after diff preview (never enabled automatically).
- **Advanced static security auditing**: Deeper AST/rule-based static inspection of bundled scripts, prompt-injection heuristics, and customizable organizational policy packs (strictly non-executing).

---

## v0.5.x+ (Planned — Optional Local GUI)

- **Graphical User Interface (GUI)**: Optional local-only UI (`[gui]` extra) featuring an OpenCode-inspired Custom Provider manager (add, edit, remove, test connection, discover models, inspect CBCF diffs & redaction previews).
- **CLI-First Guarantee**: Every feature remains 100% usable via the CLI without installing or running the GUI.
