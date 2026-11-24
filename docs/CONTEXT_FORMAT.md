# ContextBridge Context Format (CBCF) — Technical Specification (Draft v0.2.0)

> **Status:** Architectural Proposal (Not Implemented in v0.1.0). Awaiting approval before implementation.

## 1. Purpose & Design Invariants

The **ContextBridge Context Format (CBCF)** is a universal, versioned, JSON-serializable intermediate representation for AI sessions, instructions, messages, tool traces, file references, and task state across AI coding agents and model APIs.

CBCF enforces six strict invariants:

1. **Explicit Provenance**: Every CBCF document declares *where* the context came from, distinguishing live **API responses**, **local agent session histories**, **manually exported contexts**, and **converted snapshots**.
2. **No Fabrication**: Converters never invent missing messages, timestamps, tool outputs, token counts, or system instructions that were unavailable in the source. Unavailable fields remain `null` or omitted.
3. **Lossless Preservation Where Possible**: Vendor-specific fields (such as Anthropic `thinking.signature`, Google Gemini `thoughtSignature`, or OpenAI Responses `previous_response_id`) are preserved verbatim inside namespaced `provider_extensions` blocks.
4. **Explicit Lossy Conversion Reporting**: Whenever converting to a target protocol drops or downgrades structured information (e.g., dropping cryptographic thinking signatures or flattening multi-part tool outputs), the converter emits structured `compatibility.warnings` and sets `compatibility.lossless = false`.
5. **Non-Executable Data Only**: Tool calls, shell snippets, and instructions stored inside a CBCF document are **passive data**. Importing, inspecting, or receiving a CBCF document **never** executes tools, scripts, or embedded instructions.
6. **Privacy by Default**: File references store paths and optional metadata/hashes—never automatically embedding raw workspace file contents or credentials.

---

## 2. Versioning & Media Type

- **Schema identifier**: `cbcf/1.0` (SemVer: `MAJOR.MINOR`, where minor bumps are strictly backward-compatible additions).
- **File extensions**: `.cbcf.json` (single document) or `.cbcf.jsonl` (optional future multi-session stream).
- **Encoding**: UTF-8 JSON without BOM, parsed with strict byte-size and nesting-depth caps.

---

## 3. Top-Level Schema (`cbcf/1.0`)

```json
{
  "schema_version": "cbcf/1.0",
  "context_id": "ctx_01j9x8k2m4p7q9r2s5t8v0w3y6",
  "created_at": "2026-11-24T18:00:00Z",
  "updated_at": "2026-11-24T18:15:00Z",
  "provenance": {
    "source_kind": "agent_local_history",
    "source_category": "agent",
    "source_id": "claude",
    "protocol": null,
    "model": "claude-sonnet-4-5",
    "endpoint_hint": null,
    "redacted": true,
    "redaction_count": 1,
    "notes": "Exported read-only from local project session log."
  },
  "session": {
    "session_id": "sess_abc123",
    "title": "Refactor backup restore authorization",
    "workspace_hint": "ContextBridge",
    "parent_session_id": null,
    "previous_response_id": null,
    "started_at": "2026-11-24T17:45:00Z",
    "ended_at": "2026-11-24T18:15:00Z",
    "status": "completed"
  },
  "instructions": [
    {
      "instruction_id": "inst_1",
      "scope": "project",
      "role": "system",
      "source_ref": "AGENTS.md",
      "content": "Follow strict typing and never execute user skill scripts.",
      "priority": 10
    }
  ],
  "messages": [
    {
      "message_id": "msg_1",
      "role": "user",
      "timestamp": "2026-11-24T17:45:02Z",
      "parts": [
        {
          "type": "text",
          "text": "Audit backup restore target validation."
        }
      ],
      "provider_extensions": {}
    },
    {
      "message_id": "msg_2",
      "role": "assistant",
      "timestamp": "2026-11-24T17:45:10Z",
      "parts": [
        {
          "type": "reasoning",
          "text": "Check whether target_override bypasses allowed_roots.",
          "opaque_signature": null,
          "signature_provider": null
        },
        {
          "type": "tool_call",
          "call_id": "call_001",
          "tool_name": "read_file",
          "arguments": {
            "path": "src/contextbridge/backup/manager.py"
          }
        }
      ],
      "provider_extensions": {}
    },
    {
      "message_id": "msg_3",
      "role": "tool",
      "timestamp": "2026-11-24T17:45:11Z",
      "parts": [
        {
          "type": "tool_result",
          "call_id": "call_001",
          "tool_name": "read_file",
          "output": "[truncated or summarized output]",
          "is_error": false
        }
      ],
      "provider_extensions": {}
    }
  ],
  "file_references": [
    {
      "path": "src/contextbridge/backup/manager.py",
      "media_type": "text/x-python",
      "line_start": 310,
      "line_end": 350,
      "sha256": null,
      "content_included": false,
      "snippet": null
    }
  ],
  "tasks": [
    {
      "task_id": "task_1",
      "title": "Enforce skills-root protection on --target",
      "status": "completed",
      "notes": null,
      "updated_at": "2026-11-24T18:10:00Z"
    }
  ],
  "metadata": {
    "token_usage": {
      "input_tokens": 1240,
      "output_tokens": 310,
      "reasoning_tokens": 95,
      "estimated": false
    },
    "tags": ["security", "audit"],
    "custom": {}
  },
  "compatibility": {
    "lossless": true,
    "target_tested": [],
    "warnings": [],
    "dropped_fields": [],
    "preserved_extensions": []
  },
  "provider_extensions": {}
}
```

---

## 4. Field Semantics & Pydantic v2 Model Structure

### 4.1 Provenance (`ContextProvenance`)

Differentiates source origins explicitly so users and downstream converters never confuse an API reply with a full local agent transcript:

| Field | Type | Allowed Values / Description |
| --- | --- | --- |
| `source_kind` | `StrEnum` | `api_response` \| `agent_local_history` \| `manual_export` \| `converted_context` |
| `source_category` | `StrEnum` | `agent` \| `provider` \| `user` |
| `source_id` | `str` | Adapter or provider ID (e.g., `claude`, `codex`, `my-vllm`, `openai-main`) |
| `protocol` | `str \| None` | API protocol used when `source_category == "provider"` (e.g., `openai_responses`, `anthropic_messages`) |
| `model` | `str \| None` | Model identifier if known; `null` if unavailable |
| `endpoint_hint` | `str \| None` | Sanitized host/path hint (never contains query tokens or credentials) |
| `redacted` | `bool` | Whether ContextBridge secret redaction was applied |
| `redaction_count` | `int` | Count of redacted matches (never stores original secret values) |
| `notes` | `str \| None` | Human-readable provenance note |

### 4.2 Roles & Typed Content Parts

- **Canonical Roles**: `system`, `developer`, `user`, `assistant`, `tool`.
  - Protocols without a distinct `developer` role (e.g., Anthropic Messages, Ollama, Gemini) fold `developer` instructions into top-level `system` / `systemInstruction` and record an informational `ROLE_NORMALIZED` warning.
  - Protocols that require tool outputs inside a `user` turn (Anthropic `tool_result`, Gemini `functionResponse`) convert `role: "tool"` turns automatically and preserve original correlation via `call_id` + `tool_name`.
- **Canonical Part Types** (discriminated union on `type`):
  - `text`: Plain or Markdown text content (`text: str`).
  - `reasoning`: Internal reasoning/thinking text plus optional vendor-bound `opaque_signature` and `signature_provider` (e.g., `"anthropic"` for `thinking.signature`, `"gemini"` for `thoughtSignature`).
  - `tool_call`: Declarative record of a requested tool invocation (`call_id`, `tool_name`, `arguments: dict[str, Any]`). **Never auto-executed.**
  - `tool_result`: Declarative record of a tool output (`call_id`, `tool_name`, `output: str | dict[str, Any]`, `is_error: bool`).
  - `file_ref`: Reference to an entry in `file_references` (path-safe relative POSIX path).
  - `unknown_block`: Catch-all container (`vendor: str`, `raw_type: str`, `payload: dict[str, Any]`) guaranteeing unknown vendor blocks (e.g., citations, server-side web search results, audio/image metadata) are never silently discarded during parsing.

### 4.3 Session, Instructions, File References & Tasks

- **`session`**: Optional session envelope (`session_id`, `title`, `workspace_hint`, `parent_session_id`, `previous_response_id`, `started_at`, `ended_at`, `status`). `workspace_hint` stores a basename or user-chosen label rather than exposing absolute personal paths by default.
- **`instructions`**: Ordered list separated from chat turns so protocols with top-level instruction fields (`OpenAI Responses instructions`, `Anthropic system`, `Gemini systemInstruction`) map cleanly without parsing them out of message arrays.
- **`file_references`**: Path-validated references (checked via `validate_relative_path` when relative; no NTFS ADS, no traversal). `content_included` defaults to `false`; explicit opt-in is required to attach snippets.
- **`tasks`**: Lightweight structured checklist state (`pending`, `in_progress`, `completed`, `blocked`, `cancelled`) for cross-agent task handoff.

### 4.4 Compatibility & Lossy Conversion Diagnostics

```json
{
  "lossless": false,
  "warnings": [
    {
      "code": "LOSSY_THINKING_SIGNATURE_DROPPED",
      "severity": "warning",
      "source_field": "messages[1].parts[0].opaque_signature",
      "target_protocol": "openai_chat",
      "message": "Anthropic cryptographic thinking signature is not supported by OpenAI Chat Completions and was omitted from the request payload (retained in CBCF provider_extensions)."
    }
  ],
  "dropped_fields": ["messages[1].parts[0].opaque_signature"],
  "preserved_extensions": ["anthropic.thinking_signature"]
}
```

---

## 5. Selective Transfer & Inclusion Controls

CBCF supports **independent selective inclusion/exclusion** during `export`, `convert`, `send`, and `transfer` operations so users never transmit or copy more context than intended.

| Selector Key | CBCF Section | Default in Export | Default in API Send | Notes |
| --- | --- | --- | --- | --- |
| `messages` | `messages[]` (user/assistant text turns) | Included | Included | Core conversational turns |
| `instructions` | `instructions[]` | Included | Included | System/developer/project instructions |
| `tasks` | `tasks[]` | Included | Opt-in (`--include tasks`) | Checklist/plan state |
| `tool_calls` | `tool_call` parts | Included | Opt-in | Passive records only; never executed |
| `tool_results` | `tool_result` parts | Included | Opt-in | Often large; truncated/redacted on opt-in |
| `file_references` | `file_references[]` | Metadata only | Opt-in | Paths/line ranges only; never auto-embeds file bodies |
| `metadata` | `metadata` | Included | Excluded by default | Token usage, tags, custom metadata |
| `reasoning` | `reasoning` parts | Only if exposed by source API | Excluded cross-provider by default | **Never fabricated**; only preserves reasoning blocks explicitly returned by a source API/log |
| `skills` | `associated_skills[]` | Opt-in (`--include skills`) | Opt-in | References/metadata of active Agent Skills (`SKILL.md` name/version/summary) |

---

## 6. Universal Handoff Format (Markdown + CBCF)

When native session import is unavailable (e.g., Cursor, Codex, Antigravity, or cross-agent handoffs), ContextBridge defines a dual-representation **Universal Handoff** (structured `handoff` object inside CBCF + deterministic rendered Markdown).

### 6.1 Provenance Tagging: Extracted vs. Manual

Every handoff item carries an explicit `origin` marker (`"extracted"` vs. `"manual"`) so recipients and agents can always distinguish facts extracted from real logs/files from notes added manually by the user:

```json
{
  "handoff": {
    "objectives": [
      {"text": "Raise v0.1.0 branch coverage to >= 95%", "origin": "manual"}
    ],
    "decisions": [
      {"text": "Use httpx with follow_redirects=False and trust_env=False", "origin": "extracted", "source_ref": "msg_14"}
    ],
    "completed_tasks": [
      {"text": "Enforce authorized restore target checks (AUD-01)", "origin": "extracted", "source_ref": "task_1"}
    ],
    "pending_tasks": [
      {"text": "Implement Phase 2B CBCF Pydantic models", "origin": "manual"}
    ],
    "modified_files": [
      {"path": "src/contextbridge/backup/manager.py", "summary": "Added non-destructive rollback", "origin": "extracted"}
    ],
    "known_errors": [
      {"text": "Windows unprivileged symlinks require Developer Mode", "origin": "extracted", "source_ref": "msg_22"}
    ],
    "constraints": [
      {"text": "Never execute scripts found inside user skills", "origin": "extracted", "source_ref": "AGENTS.md"}
    ]
  }
}
```

### 6.2 Rendered Markdown Universal Handoff

`contextbridge context convert <file.cbcf.json> --to-format handoff-md` renders a clean, human-auditable, non-executable Markdown brief containing clearly labeled sections (`## Objectives`, `## Key Decisions`, `## Completed Tasks`, `## Pending Tasks`, `## Modified Files`, `## Known Errors`, `## Constraints`), annotating each bullet with `[extracted]` or `[manual]` and never fabricating unavailable items.

---

## 7. Cross-Protocol Conversion Rules & Known Lossy Mappings

| Feature | Preservation Strategy | Lossy Warning Code (When Target Lacks Support) |
| --- | --- | --- |
| `instructions` (`system` / `developer`) | Maps to top-level `instructions` (Responses), `system` (Anthropic), `systemInstruction` (Gemini), or leading `system` messages (Chat Completions / Ollama) | `INFO_ROLE_MERGED` |
| `reasoning` + `opaque_signature` | Preserved verbatim when round-tripping to same provider (`anthropic` / `gemini`); stripped from outbound history by default for other providers (per vendor guidance) | `LOSSY_REASONING_OMITTED` / `LOSSY_SIGNATURE_DROPPED` |
| `tool_call` / `tool_result` | Mapped to protocol-native tool structures (`tool_calls`/`tool`, `tool_use`/`tool_result`, `functionCall`/`functionResponse`, Responses `function_call`/`function_call_output`); missing `call_id` synthesized deterministically when target requires IDs (e.g., Ollama -> Anthropic) | `LOSSY_SYNTHETIC_CALL_ID` / `LOSSY_TOOL_FLATTENED` (if target lacks tool support) |
| `session.previous_response_id` | Sent only to the matching OpenAI Responses provider; ignored on all other providers | `LOSSY_SERVER_STATE_ID_IGNORED` |
| `tasks` & `file_references` | Rendered into an explicit, clearly delimited non-executable summary block only when the user opts into context handoff formatting; otherwise stored as metadata | `INFO_METADATA_NOT_SENT` |
| `unknown_block` / `provider_extensions` | Kept inside CBCF file; omitted from incompatible outbound API requests with explicit warning | `LOSSY_EXTENSION_OMITTED` |

---

## 8. Security Rules for CBCF Files

1. **Parsing limits**: Enforce maximum CBCF file size (default 10 MiB), maximum JSON nesting depth (24), and maximum message count (5,000) before Pydantic validation.
2. **Zero code execution**: Importing or inspecting a CBCF file never invokes `tool_call` entries, shell scripts, or embedded prompt instructions.
3. **Secret redaction**: Before `context export` or `context send`, run the ContextBridge secret scanner over `instructions`, `messages`, `tasks`, and optional `snippet` fields; redact matched tokens with `[REDACTED:<RULE_ID>]` when redaction is enabled (default for outbound network sends) and block credential files.
4. **Path hygiene**: All file paths in `file_references` are validated with `security/paths.py` (blocking `..`, NTFS ADS, null bytes, and sensitive credential paths like `.env` or `~/.ssh`).
