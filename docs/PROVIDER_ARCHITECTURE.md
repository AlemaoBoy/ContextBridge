# Provider Architecture & Universal Context Engine — Technical Proposal (v0.2.0)

> **Status:** Architectural Proposal (Not Implemented in v0.1.0). Preserves v0.1.0 local-first, zero-telemetry behavior by default.

## 1. Architectural Separation: Agent Adapters vs. AI Provider Adapters

ContextBridge v0.2.0 draws a strict boundary between **local coding agents** and **model inference APIs**:

```mermaid
flowchart LR
    CLI["CLI (scan, skills, backup, doctor, provider, context)"] --> Engine["Universal Context Engine (CBCF v1.0)"]

    subgraph LocalOnly ["Local-Only Layer (Zero Network — v0.1.0 Preserved)"]
        Agents["Agent Adapters\n(claude, codex, antigravity,\nopencode, gemini-cli, cursor)"]
        Skills["Skills / Backup / Doctor"]
        Agents --> LocalFS["Local Filesystem\n(skills, rules, sessions)"]
    end

    subgraph OptInNetwork ["Explicit Opt-In Network Layer (v0.2.0)"]
        Registry["Provider Registry\n(~/.contextbridge/providers.json)"]
        Providers["AI Provider Adapters\n(OpenAI Chat, OpenAI Responses,\nAnthropic, Gemini, Ollama,\nOpenAI-Compatible, Custom HTTP)"]
        Guard["Network & Privacy Guard\n(Preview, Confirm, Redact,\nTLS, Redirect Strip, Size/Timeout)"]
        Secrets["Credential Resolver\n(env:VAR | keyring:REF)"]
        Registry --> Providers
        Secrets --> Providers
        Providers --> Guard
        Guard --> APIs["Remote APIs (HTTPS)\nor Local Runtimes (localhost)"]
    end

    Engine --> Agents
    Engine --> Providers
    CLI --> Skills
```

| Dimension | Agent Adapters (`contextbridge.adapters`) | AI Provider Adapters (`contextbridge.providers`) |
| --- | --- | --- |
| **Responsibility** | Discover & manage local skills, instructions (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`), MCP configs, and read-only local session transcripts when supported. | Communicate with model APIs to test connectivity, list models, send CBCF context, and receive responses into CBCF. |
| **Network I/O** | **Never** performs network I/O. | **Only** on explicit user-invoked `provider test/models` or `context send/receive/transfer` commands. |
| **Extensibility** | Built-in modular subclasses of `BaseAgentAdapter`. | Built-in protocol drivers + **user-defined declarative providers** stored in `providers.json` (no core code edits, no arbitrary Python execution). |
| **Security Boundary** | Filesystem boundaries (traversal, symlinks/junctions, ADS, backups). | Network + privacy boundaries (preview, confirmation, redaction, TLS, redirect protection, billing-safe retries, response caps). |

---

## 2. Core Provider Abstraction (`BaseProviderAdapter`)

Every protocol driver implements a clean, typed interface backed by an injectable HTTP transport (enabling 100% deterministic offline unit tests via `httpx.MockTransport`):

```python
class ProviderProtocol(StrEnum):
    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GOOGLE_GEMINI = "google_gemini"
    OLLAMA_NATIVE = "ollama_native"
    OPENAI_COMPATIBLE = "openai_compatible"
    CUSTOM_HTTP = "custom_http"


class BaseProviderAdapter(ABC):
    protocol: ProviderProtocol

    @abstractmethod
    def build_health_request(self, config: ProviderConfig) -> HttpRequestSpec: ...

    @abstractmethod
    def build_models_request(self, config: ProviderConfig) -> HttpRequestSpec | None: ...

    @abstractmethod
    def parse_models_response(self, payload: dict[str, Any]) -> list[ProviderModelInfo]: ...

    @abstractmethod
    def cbcf_to_request(
        self,
        context: CBCFDocument,
        config: ProviderConfig,
        options: SendOptions,
    ) -> tuple[HttpRequestSpec, ConversionReport]: ...

    @abstractmethod
    def response_to_cbcf(
        self,
        payload: dict[str, Any],
        config: ProviderConfig,
        *,
        source_context: CBCFDocument | None = None,
    ) -> CBCFDocument: ...
```

### Capability Matrix as First-Class Metadata

A custom or built-in provider can act as a **context source** (`context receive`, `context transfer --from <provider>`) and/or **context target** (`context send`, `context transfer --to <provider>`) strictly according to its granular `ProviderCapabilities`:

- `can_send_context: bool` — Can send CBCF context to generate a model response (e.g., `POST /v1/chat/completions`, `POST /v1/responses`, `POST /v1/messages`, `:generateContent`, `/api/chat`).
- `can_receive_response: bool` — Can capture the immediate response of a user-initiated API call into a CBCF document (`source_kind: "api_response"`).
- `can_retrieve_stored_response: bool` — Can fetch a specific previously stored response by ID (e.g., OpenAI Responses `GET /v1/responses/{id}` when `store: true`).
- `can_retrieve_remote_history: bool` — Can retrieve full multi-turn remote conversation history from server storage (**`false` by default**; never declared `true` without explicit official API documentation confirming a history retrieval endpoint).
- `supports_streaming: bool` — Supports incremental streaming (SSE or NDJSON).
- `supports_model_discovery: bool` — Supports listing models via API (`GET /v1/models`, `GET /v1beta/models`, `GET /api/tags`).
- `supports_server_session_continuation: bool` — Supports continuing a server-maintained session state (e.g., OpenAI Responses `previous_response_id`).
- `supports_tools: bool` / `supports_reasoning: bool` — Protocol support for structured tool blocks and exposed reasoning blocks.

### Transfer Modes

ContextBridge defines three explicit transfer modes so users always control cost, network usage, and determinism:

| Mode | Network / API Calls | Token Cost | Behavior |
| --- | --- | --- | --- |
| **Offline Transfer** *(Default for local conversions)* | **None (0 network calls)** | **0 tokens** | Deterministic conversion between local agents, CBCF files, protocol JSON payloads, and Universal Handoff Markdown (`context export`, `context import`, `context convert`, offline `context transfer`). Never depends on external services. |
| **API Transfer** *(Explicit opt-in)* | Explicit user-confirmed HTTP call | Provider-billed (for remote APIs) | Sends selected, redacted CBCF context to a configured AI provider (`context send`, `context receive`, API `context transfer`) after preview and confirmation. |
| **Smart Transfer** *(Future — v0.4.x, optional)* | Optional model-assisted compaction/reorganization | Explicitly disclosed before consent | Future optional mode that compacts or reorganizes verbose context using a user-selected model with explicit opt-in consent and before/after diff preview. Never enabled automatically. |

---

## 3. Declarative Custom Provider Configuration

Users configure custom providers in `~/.contextbridge/providers.json` (or `$CONTEXTBRIDGE_HOME/providers.json`) via CLI commands (`contextbridge provider add`) or direct JSON editing. **Plaintext API keys are rejected in repository files and never written to Git.**

### Complete `ProviderConfig` Schema

```json
{
  "schema_version": "1.0",
  "providers": {
    "local-ollama": {
      "id": "local-ollama",
      "display_name": "Local Ollama",
      "protocol": "ollama_native",
      "base_url": "http://127.0.0.1:11434",
      "allow_insecure_localhost": true,
      "auth": {
        "method": "none",
        "secret_ref": null,
        "header_name": null,
        "prefix": null
      },
      "custom_headers": {},
      "models": {
        "default_model": "qwen3:8b",
        "configured_models": [
          {
            "id": "qwen3:8b",
            "display_name": "Qwen 3 8B (Local)",
            "max_input_tokens": 32768,
            "max_output_tokens": 8192
          }
        ],
        "discovery": {
          "enabled": true,
          "endpoint_override": null
        }
      },
      "timeout_seconds": {
        "connect": 10.0,
        "read": 120.0,
        "total": 180.0
      },
      "streaming": true,
      "capabilities": {
        "can_send_context": true,
        "can_receive_response": true,
        "can_retrieve_stored_response": false,
        "can_retrieve_remote_history": false,
        "supports_model_discovery": true,
        "supports_streaming": true,
        "supports_tools": true,
        "supports_reasoning": true,
        "supports_server_session_continuation": false
      },
      "context_limits": {
        "max_input_tokens": 32768,
        "max_output_tokens": 8192,
        "max_request_bytes": 4194304,
        "max_response_bytes": 8388608
      },
      "generation_params": {
        "temperature": 0.2,
        "top_p": null,
        "max_tokens": null,
        "max_completion_tokens": null,
        "num_ctx": 32768,
        "extra_body": {}
      }
    }
  }
}
```

### Safe Declarative Custom HTTP Adapter (`protocol: "custom_http"`)

For proprietary or internal gateways that do not match OpenAI/Anthropic/Gemini/Ollama schemas, users can define a **purely declarative** `http_mapping` object—with **zero arbitrary Python execution, zero `eval`, and zero template code execution**:

```json
{
  "http_mapping": {
    "send_method": "POST",
    "send_path": "/api/v2/generate",
    "models_path": "/api/v2/models",
    "health_path": "/health",
    "request_body_template": {
      "model_name": "$cb.model",
      "system_prompt": "$cb.instructions_text",
      "history": "$cb.messages_simple",
      "parameters": "$cb.generation_params"
    },
    "response_text_path": "result.output.text",
    "response_model_path": "result.model",
    "response_usage_input_path": "result.usage.prompt_tokens",
    "response_usage_output_path": "result.usage.completion_tokens",
    "models_list_path": "items",
    "model_id_field": "id"
  }
}
```

Allowed bindings are strictly a fixed allowlist of scalar/structured tokens (`$cb.model`, `$cb.instructions_text`, `$cb.messages_simple`, `$cb.messages_openai`, `$cb.last_user_text`, `$cb.generation_params`) and restricted dot-separated JSON path lookups (`^[a-zA-Z0-9_.\[\]]+$`).

---

## 4. Proposed CLI Commands (v0.2.0)

### 4.1 Provider Management

| Command | Behavior |
| --- | --- |
| `contextbridge provider add [ID]` | Interactive wizard or flag-driven creation (`--name`, `--protocol`, `--base-url`, `--auth`, `--secret-ref env:KEY`, `--header K=V`, `--model ID`, `--timeout`). Validates URL, auth scheme, and schema without sending network traffic unless `--test` is passed. |
| `contextbridge provider list [--json]` | Read-only listing of configured providers, protocols, capabilities, auth method (shows `env:VAR` or `keyring:...`, **never** secret values), and default model. |
| `contextbridge provider test <ID> [--json]` | Explicit network connectivity/auth test (uses non-billable health or models endpoint where available; warns before any billable fallback probe). |
| `contextbridge provider models <ID> [--refresh] [--json]` | Lists configured models; with `--refresh`, queries the provider's model discovery endpoint (if supported) and optionally updates local metadata. |
| `contextbridge provider remove <ID> [--apply] [--yes]` | Removes provider entry from `providers.json` (dry-run preview by default; backs up `providers.json` before modifying; optionally offers keyring entry deletion). |

### 4.2 Universal Context Engine

| Command | Behavior |
| --- | --- |
| `contextbridge context inspect <FILE> [--json]` | Read-only inspection of a `.cbcf.json` file: schema version, provenance, message/tool/file counts, token estimate, lossy warnings, and heuristic secret scan. |
| `contextbridge context export --from <AGENT_OR_FILE> [--session ID] --output <FILE.cbcf.json>` | Exports context into CBCF v1.0. For agents with documented local session formats (e.g., Claude Code JSONL, OpenCode, Gemini CLI), reads strictly read-only, runs redaction preview, and refuses silent overwrites. |
| `contextbridge context import <FILE.cbcf.json> --to <TARGET_AGENT_OR_DIR> [--dry-run\|--apply]` | Converts a CBCF file into a portable handoff format or supported agent import target (explicitly non-executable; never injects secrets or runs tool calls). |
| `contextbridge context convert <FILE.cbcf.json> --to-format <PROTOCOL\|markdown> --output <OUT>` | Offline conversion preview/export from CBCF into a target protocol payload or sanitized Markdown handoff document, reporting all lossy warnings. |
| `contextbridge context send <FILE.cbcf.json> --to <PROVIDER> [--model M] [--redact/--no-redact] [--dry-run\|--apply] [--yes]` | **Preview-first** outbound transmission: displays exact endpoint URL, model, redacted payload summary, byte size, estimated input tokens, and cost warning before prompting for confirmation (`--apply` + confirmation/`--yes`). Saves response as CBCF if `--save-response` is set. |
| `contextbridge context receive --from <PROVIDER> [--response-id ID \| --prompt TEXT] --output <FILE.cbcf.json>` | Captures a provider response (either retrieving a stored response ID on protocols like OpenAI Responses, or executing an explicit user-confirmed request) into a validated CBCF document with `source_kind: "api_response"`. |
| `contextbridge context transfer --from <SOURCE> --to <TARGET> [--dry-run\|--apply] [--yes]` | Orchestrates `source -> CBCF -> redact & compatibility check -> preview -> confirm -> target`. Supports Agent->Provider, Provider->Provider, Provider->Agent/File, and Agent->Agent (when both sides support the required capability). |

---

## 5. Security, Privacy & Network Hardening Architecture

1. **Offline by Default**: Every v0.1.0 command and every Offline Transfer (`export`, `import`, `convert`, Universal Handoff) remains 100% local with zero network imports/calls and zero token usage. Network operations only occur when the user explicitly invokes `provider test`, `provider models --refresh`, or API Transfer (`context send/receive/transfer --apply`).
2. **Mandatory Pre-Send Preview, Selective Inclusion & Confirmation**:
   - `context send` and `context transfer` support granular `--include` / `--exclude` selectors (`messages`, `instructions`, `tasks`, `tool_calls`, `tool_results`, `file_references`, `metadata`, `reasoning`, `skills`) and default to `--dry-run` preview showing target URL, resolved destination IP class, protocol, model, message count, payload bytes, redaction findings, and an explicit token/cost warning for non-localhost endpoints.
   - Non-interactive execution requires explicit `--apply --yes`.
3. **Automatic Redaction Pipeline**:
   - Scans outbound context for secrets/API keys/PEM headers/credentials before transmission using `security/secrets.py`, replacing matches with `[REDACTED:<RULE_ID>]` by default (never logging original values).
   - Never auto-attaches workspace files, `.env` files, or full history unless explicitly selected by the user.
4. **Credential Isolation & Storage (`env:` and optional `keyring:`)**:
   - `env:OPENAI_API_KEY` (default, zero extra dependencies): resolves at runtime from `os.environ`.
   - `keyring:contextbridge/<provider_id>` (optional extra `pip install contextbridge-agents[keyring]`): stores secrets in Windows Credential Manager, macOS Keychain, or Linux Secret Service.
   - **Strict Per-Provider Credential Isolation**: Each provider resolves strictly its own configured `secret_ref` and never falls back to another provider's credentials or transmits one provider's key to another host.
   - **No Credentials Embedded in URLs**: Config validation unconditionally rejects `userinfo` in URLs (`https://user:pass@host/...`) and sensitive query parameters (`?api_key=`, `?key=`, `?token=`, `?access_token=`) so secrets never leak into URLs, access logs, or tracebacks.
   - `providers.json` is written with restricted POSIX permissions (`0600`) and rejects inline plaintext API keys matching secret patterns.
5. **Endpoint Validation, DNS Rebinding, IPv6 & Effective Destination Verification**:
   - Remote hosts **must** use `https://` with strict TLS certificate verification (`verify=True`, never disabled globally).
   - **IPv6 & IPv4-Mapped IPv6 Normalization**: Every IP literal and every DNS-resolved IP address (`socket.getaddrinfo`) is parsed via Python's `ipaddress.ip_address()`, unwrapping `ip.ipv4_mapped` (e.g., `::ffff:169.254.169.254` -> `169.254.169.254`, `::ffff:127.0.0.1` -> `127.0.0.1`) before applying network policy!
   - **Unconditionally Blocked Infrastructure Addresses**: Link-local (`169.254.0.0/16`, `fe80::/10`), AWS/GCP/Azure metadata IPs & hosts (`169.254.169.254`, `169.254.170.2`, `fd00:ec2::254`, `metadata.google.internal`, `metadata.azure.internal`), unspecified (`0.0.0.0/8`, `::`), and multicast/reserved ranges are **always blocked**, even if a hostname resolves to them dynamically.
   - **DNS Rebinding Mitigation & Effective Peer Verification**: Before opening a connection, ContextBridge resolves the target hostname, verifies **all** returned A/AAAA records against the IP policy, pins the connection to the validated IP (preserving the original hostname for TLS SNI and `Host` header) or verifies the connected socket's effective peer IP immediately post-connect before writing any request bytes/headers!
   - **Explicit Localhost & Private Network Opt-In**: Loopback (`127.0.0.0/8`, `::1`) is allowed over `http://` only when `allow_insecure_localhost: true`; RFC1918 / IPv6 ULA (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) corporate/LAN inference servers require explicit `allow_private_network: true` (while still blocking link-local metadata endpoints).
6. **Ambient Environment Proxy Isolation**:
   - `httpx.Client` is instantiated with **`trust_env=False` by default** so inherited `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` environment variables cannot silently intercept API keys or bypass effective-destination validation. Users who require a corporate proxy must configure `proxy_mode: "env"` or an explicit validated `proxy_url` per provider.
7. **HTTP Redirect Credential Protection**:
   - HTTP client runs with `follow_redirects=False` so an HTTP `301/302/307/308` redirect can **never** forward `Authorization`, `x-api-key`, `x-goog-api-key`, or custom auth headers to another origin or downgrade HTTPS to HTTP.
8. **Billing-Safe Retry Policy**:
   - **Zero automatic retries** on billable generation `POST` endpoints (`send`, `transfer`, `receive` with generation) to prevent duplicate token charges when a request times out after server-side acceptance.
   - Safe bounded retries (max 2 attempts with backoff) are permitted **only** on idempotent `GET` discovery/health checks.
9. **Malicious, Malformed & Interrupted Response Protection**:
   - Enforces `max_response_bytes` incrementally while reading/streaming chunks (aborting immediately if exceeded), strict connect/read/total timeouts, UTF-8 validation, and JSON nesting-depth caps.
   - **Interrupted Streaming**: If an SSE/NDJSON stream closes unexpectedly before the terminal marker (`[DONE]` / `done: true` / `message_stop`), ContextBridge flags the output explicitly as `status: "interrupted"` / raises `StreamInterruptedError` and never fabricates a completed response.
   - **Malformed Payloads**: Unexpected schemas, non-JSON error pages, or invalid event frames fail cleanly with sanitized diagnostic errors (never echoing auth headers).
   - **Zero Execution**: Returned `tool_calls` / `functionCall` / `tool_use` payloads and instructions inside imported contexts are stored strictly as passive CBCF records and **never executed**.

---

## 6. Future GUI Architecture (OpenCode-Inspired — Deferred Post-v0.2.0)

Planned as an optional local-only interface (`contextbridge ui` or companion desktop wrapper) layered cleanly over the Python core without duplicating business logic:

- **Inspired by OpenCode's Custom Provider model**, closing OpenCode's current JSON-only GUI gap with a full CRUD form:
  - **Identity**: Display Name, Provider ID (validated slug), Protocol selector (`OpenAI Chat`, `OpenAI Responses`, `Anthropic`, `Gemini`, `Ollama`, `OpenAI-Compatible`, `Custom HTTP`).
  - **Connection & Security**: Base URL with live localhost/HTTPS indicator, Auth Method selector (`None`, `Bearer`, `API Key Header`, `Custom Headers`), Secret Reference picker (`env:VAR` or OS `keyring:` — masked input, never written to plaintext config), Custom Headers key-value table.
  - **Models & Limits**: Model list manager, **"Discover Models"** button (`provider models --refresh`), context window & token limit inputs, optional generation parameters (`temperature`, `num_ctx`, `max_completion_tokens`), streaming toggle, timeout controls.
  - **Diagnostics & Preview**: **"Test Connection"** button (`provider test`) showing latency, TLS status, and capability badges; visual CBCF diff & redaction preview before any outbound transfer.
- **CLI Independence Guarantee**: The GUI is strictly optional (`[gui]` extra); 100% of provider and context functionality works headlessly via the CLI.

---

## 7. Dependencies Assessment (v0.2.0)

| Dependency | Scope | Justification |
| --- | --- | --- |
| `httpx>=0.27.0` | Core (or `[providers]` extra evaluated during v0.2.0 kickoff) | Pure-Python/modern HTTP client with strict TLS, granular connect/read timeouts, `follow_redirects=False`, streaming (SSE/NDJSON), and built-in `MockTransport` for deterministic offline testing. |
| `keyring>=25.0.0` | Optional extra (`[keyring]`) | Native OS credential store integration (Windows Credential Manager, macOS Keychain, Linux Secret Service) without bloating headless/CI environments where `env:` suffices. |
| No heavy AI vendor SDKs | Explicit non-dependency | Calling documented REST endpoints directly via `httpx` + Pydantic v2 avoids pulling 6 heavy vendor SDKs, keeps install footprint small, and guarantees uniform security enforcement (redirects, byte caps, timeouts, redaction). |
