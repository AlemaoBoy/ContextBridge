# Provider & Agent Context Compatibility Matrix (Proposal v0.2.0)

> **Status:** Research & Architectural Proposal (Not Implemented in v0.1.0). All endpoints and behaviors below are derived from official documentation; unconfirmed behaviors are explicitly flagged.

---

## 1. AI Provider Protocols — Granular Capability Matrix

Capabilities are separated explicitly; **full remote history retrieval is never claimed without official API documentation**.

| Protocol ID | Send Context | Receive Response | Retrieve Stored Response | Retrieve Remote History | Streaming | Model Discovery | Server Session Continuation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **`openai_chat`** | Yes (`POST /v1/chat/completions`) | Yes | No | No | Yes (SSE) | Yes (`GET /v1/models`) | No (stateless) |
| **`openai_responses`** | Yes (`POST /v1/responses`) | Yes | **Yes** (`GET /v1/responses/{id}` if `store: true`) | Partial / input-items endpoint only (not arbitrary full account history) | Yes (typed SSE) | Yes (`GET /v1/models`) | **Yes** (`previous_response_id`) |
| **`anthropic_messages`** | Yes (`POST /v1/messages`) | Yes | No | No | Yes (SSE) | Yes (`GET /v1/models`) | No (stateless) |
| **`google_gemini`** | Yes (`POST /v1beta/models/{m}:generateContent`) | Yes | No | No | Yes (SSE `alt=sse`) | Yes (`GET /v1beta/models`) | No (stateless REST) |
| **`ollama_native`** | Yes (`POST /api/chat`) | Yes | No | No | Yes (NDJSON) | Yes (`GET /api/tags`, `POST /api/show`) | No (stateless) |
| **`openai_compatible`** | Yes (`POST <base>/chat/completions`) | Yes | No (unconfirmed across gateways) | No | Usually (SSE) | Optional (`GET <base>/models`, varies) | No (stateless) |
| **`custom_http`** | Configurable (`http_mapping`) | Configurable | Only if declared | No (unless explicitly declared) | Deferred (non-streaming first) | Optional (`models_path`) | No |

## 1.1 Protocol Wire Format Details

| Protocol ID | Send Endpoint | Auth Header(s) | System Instructions | Tool Calling Format | Reasoning / Thinking |
| --- | --- | --- | --- | --- | --- |
| **`openai_chat`** | `POST /v1/chat/completions` | `Authorization: Bearer <key>` | `role: "system"` / `"developer"` in `messages[]` | `tools[]` + `tool_calls[]` + `role: "tool"` (`tool_call_id`) | Model-dependent (`reasoning_effort`) |
| **`openai_responses`** | `POST /v1/responses` | `Authorization: Bearer <key>` | Top-level `instructions` + typed `input` items | Function tools + built-in tools (`function_call` / `function_call_output`) | Structured reasoning items / summaries |
| **`anthropic_messages`** | `POST /v1/messages` | `x-api-key: <key>` + required `anthropic-version: YYYY-MM-DD` | Top-level `system` string/blocks | `tools[]` (`input_schema`) + `tool_use` / `tool_result` (`tool_use_id`) | `thinking` block + cryptographic `signature` |
| **`google_gemini`** | `POST /v1beta/models/{model}:generateContent` | `x-goog-api-key: <key>` (or Vertex Bearer) | Top-level `systemInstruction` (`parts[]`) | `functionDeclarations` + `functionCall` / `functionResponse` | Thinking parts + opaque encrypted **`thoughtSignature`** |
| **`ollama_native`** | `POST /api/chat` | Optional (`none` on localhost by default) | `role: "system"` in `messages[]` | `tools[]` + `tool_calls[]` (object `arguments`) + `role: "tool"` (`tool_name`) | Model-dependent (`thinking` field) |
| **`openai_compatible`** | `<base_url>/chat/completions` | Configurable (`Bearer`, custom header, or `none`) | `role: "system"` in `messages[]` | OpenAI-like `tools[]` (varies by server/model) | `reasoning_content` (OpenRouter, vLLM, LM Studio, Groq) |
| **`custom_http`** | User-declared path & method | User-declared (`none`, `bearer`, `api_key_header`, `custom_headers`) | Mapped via `$cb.instructions_text` | Optional / flagged lossy if omitted | Mapped only if declared |

---

## 2. Critical Protocol Divergences (Never Assume Uniformity)

ContextBridge **must not** assume all "OpenAI-compatible" or REST providers behave identically. Verified real-world divergences that the v0.2.0 adapters must handle explicitly:

1. **`max_tokens` vs. `max_completion_tokens`**:
   - Newer OpenAI reasoning models reject or deprecate `max_tokens` in favor of `max_completion_tokens`; older OpenAI-compatible servers may only accept `max_tokens`; Anthropic **requires** `max_tokens` on every `POST /v1/messages` call; Ollama places token/context limits inside `options.num_ctx` and `options.num_predict`.
2. **Tool Call Argument Encoding**:
   - OpenAI Chat Completions encodes `tool_calls[].function.arguments` as a **JSON string**, whereas Ollama Native (`function.arguments`), Anthropic (`tool_use.input`), and Gemini (`functionCall.args`) use a **JSON object**.
   - Ollama tool results correlate by `tool_name` and may omit `call_id`; converting Ollama -> Anthropic or OpenAI requires synthesizing deterministic `call_id` values and logging `LOSSY_SYNTHETIC_CALL_ID`.
3. **Opaque Reasoning Signatures**:
   - **Anthropic** returns a cryptographic `signature` on `thinking` blocks that must be returned unaltered in multi-turn Claude requests.
   - **Google Gemini** returns an encrypted `thoughtSignature` on `functionCall` / `text` parts; omitting it in subsequent Gemini REST tool turns causes HTTP `400 Bad Request`.
   - **OpenRouter / vLLM / LM Studio / Groq** expose `reasoning_content` on responses, and explicitly advise **not** sending `reasoning_content` back in subsequent conversation turns.
4. **Required Headers & Versioning**:
   - Anthropic rejects requests missing `anthropic-version`.
   - OpenRouter optionally uses `HTTP-Referer` / `X-Title` custom headers (supported cleanly via `custom_headers`).
5. **Streaming Framing**:
   - OpenAI, Anthropic, and Gemini (`alt=sse`) use Server-Sent Events (`text/event-stream`), while Ollama Native uses newline-delimited JSON (`application/x-ndjson`) and defaults to `stream: true` unless `"stream": false` is sent explicitly.
6. **Model Discovery Gaps**:
   - Not all `openai_compatible` endpoints implement `GET /v1/models`, and some proxies return a filtered or stale catalog. Model discovery must therefore be **optional and gracefully degradable** to user-configured model IDs.

---

## 3. Coding Agent Local Session & Context Capabilities (Reality Check)

ContextBridge will **never** fabricate session history or silently mutate proprietary agent databases. Planned local context extraction/import support for the 6 coding agents:

| Agent | Verified Local Session Storage (Research) | Planned v0.2.0 Export (`agent -> CBCF`) | Planned v0.2.0 Import (`CBCF -> agent`) | Known Limitations & Unconfirmed Details |
| --- | --- | --- | --- | --- |
| **Claude Code** | `~/.claude/projects/<encoded-path>/<session-id>.jsonl` (plain JSONL event log) | **Read-only supported** (Phase 2) | Portable Markdown / `CLAUDE.md` handoff only (no direct mutation of live `.jsonl` session DB) | JSONL schema evolves across Claude Code releases; unknown event types preserved in `provider_extensions.claude`. |
| **OpenCode** | SQLite `opencode.db` + structured JSON (`info/`, `message/`, `part/`) under OS data dir (`~/.local/share/opencode`, `~/Library/Application Support/opencode`, `%APPDATA%\opencode`) | **Read-only supported** (Phase 2, read-only SQLite URI `mode=ro` + JSON) | Portable handoff / documented JSON export format (no direct write into live SQLite DB) | Schema differs between OpenCode versions; read-only inspection only to prevent DB corruption. |
| **Gemini CLI** | `~/.gemini/tmp/<project_hash>/chats/` (structured chat checkpoints) + `~/.gemini/GEMINI.md` | **Read-only best-effort** (Phase 2, after verifying exact schema version on disk) | Portable Markdown / `GEMINI.md` handoff only | Checkpoint JSON layout is internal and may vary across CLI updates; never write directly into `~/.gemini/tmp/`. |
| **OpenAI Codex** | Local CLI/app state directories under `~/.codex` (format varies by harness version) | **Deferred / Best-effort** (only if verified stable structured file is present; otherwise explicit unsupported message) | Portable Markdown / `AGENTS.md` handoff only | No officially stabilized public session file specification confirmed across all Codex CLI builds. |
| **Google Antigravity** | Internal application data under `~/.gemini/antigravity/brain/<id>/` | **Manual export / Markdown handoff only** (do not depend on internal app-private state without official spec) | Portable Markdown / `AGENTS.md` / `GEMINI.md` handoff | App-internal brain logs are private runtime internals; ContextBridge avoids brittle coupling. |
| **Cursor** | Proprietary IDE workspace SQLite/LevelDB state (varies by OS and Cursor version) | **Not supported natively in v0.2.0** (use manual export or `.cbcf.json` input) | Portable Markdown / `.cursor/rules` handoff only | Cursor workspace DB schema is undocumented and encrypted/internal; ContextBridge will not reverse-engineer or mutate it. |

---

## 4. Safety Summary for Cross-Agent / Cross-Provider Transfer

- **Never write directly into an agent's internal session database** (SQLite/JSONL/LevelDB) — context imports into agents produce clean, auditable, non-executable handoff artifacts (Markdown context summaries, CBCF files, or explicit user-confirmed instruction files).
- **Never auto-execute tool calls** returned by any provider or found in any exported agent history.
- **Explicit capability enforcement**: `contextbridge context transfer --from <A> --to <B>` checks capabilities upfront and fails fast with a clear explanation when `<A>` cannot export/receive context or `<B>` cannot import/send context.
