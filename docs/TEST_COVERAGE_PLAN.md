# Test Coverage & Quality Plan (v0.1.0 -> v0.2.0)

> **Status:** Baseline measured on v0.1.0 (`93 passed`, `85%` statement + branch coverage on Windows / Python 3.15.2).
> **Policy:** No artificial tests to game percentages, no removing existing tests, and no `# pragma: no cover` exclusions without documented justification (such as OS-exclusive defensive branches).

---

## 1. Progressive Coverage Targets

| Milestone | Overall Target (Stmts + Branches) | Critical Modules (`security/`, `backup/`, `skills/migrator.py`) | Test Pass Rate |
| --- | --- | --- | --- |
| **Current Baseline (v0.1.0)** | **85%** (`2316` stmts, `744` branches) | `79%` – `94%` | **100%** (`93/93`) |
| **Phase 2A (Pre-v0.2.0 Hardening)** | **>= 92%** | **>= 95%** | **100%** |
| **Phase 2B (v0.2.0 Release Gate)** | **>= 95%** | **>= 95%** (including new `providers/` HTTP security & `context/` CBCF) | **100%** |

---

## 2. Exact Baseline & Relevant Uncovered Branches (v0.1.0)

Below is the real, audited breakdown of every module currently below `95%` and the **concrete, meaningful branches** that need real regression tests:

### 2.1 Critical Security, Backup & Migration Modules (Priority 1 — Target >= 95%)

| Module | Current Cover | Missing Stmts / Partial Branches | Concrete Meaningful Scenarios to Test |
| --- | --- | --- | --- |
| `backup/manager.py` | **79%** | `40` missed stmts, `25` partial branches | 1. Symlink/junction backup directory or `manifest.json` rejected in `list_backups()` and `get_manifest()`.<br>2. Corrupted JSON in `manifest.json` skipped in `list_backups()` and rejected in `get_manifest()`.<br>3. `manifest.total_files != len(manifest.files)` mismatch in `verify_backup()`.<br>4. Case-insensitive duplicate entry in `manifest.files` caught in `verify_backup()`.<br>5. Missing `payload/` dir or unexpected extra file added inside `payload/` detected in `verify_backup()`.<br>6. Hash mismatch during `create_backup` (corrupted copy_fn) and during `restore_backup` post-swap check.<br>7. Non-existent `source_dir` passed to `create_backup()`.<br>8. Restore when `target` is a brand-new directory (no pre-restore backup needed) vs existing symlink parent.<br>9. Non-destructive rollback path where `target` existed post-swap and is moved to `.cb_restore_failed_*` then cleaned up after rollback succeeds. |
| `security/archive.py` | **80%** | `12` missed stmts, `9` partial branches | 1. Non-existent archive & corrupted non-ZIP (`BadZipFile`) rejected.<br>2. `max_zip_entries` exceeded rejected.<br>3. Empty/root ZIP member (`"/"`) rejected.<br>4. Single-member `max_single_file_bytes` exceeded in `inspect_zip_safety`.<br>5. Malicious header spoof: member reports `compress_size == 0` with `file_size > 0`.<br>6. Streaming byte-count enforcement in `safe_extract_zip()` (mocked ZipInfo with spoofed small `file_size` whose actual decompressed stream exceeds `max_single_file_bytes` or `max_zip_uncompressed_bytes`). |
| `security/paths.py` | **85%** | `23` missed stmts, `18` partial branches | 1. `ensure_no_symlink_in_ancestors()` when root itself is symlink or target is non-relative (fallback `abspath` branch).<br>2. `validate_authorized_restore_target()` with null byte, filesystem root (`/` or `C:\`), `home_dir` itself, protected POSIX prefixes (`/etc`, `/usr`), and protected Windows dir (`%SYSTEMROOT%`).<br>3. `collect_safe_skill_files()` with non-existent dir, symlink root, `max_directory_depth` exceeded, `max_total_skill_bytes` exceeded, and case-insensitive filename collision (simulated via monkeypatched `os.scandir` entries so it runs deterministically on both Windows and Linux!). |
| `security/secrets.py` | **87%** | `7` missed stmts, `3` partial branches | 1. Empty relative path to `is_sensitive_filename("")`.<br>2. Binary file containing null bytes (`\x00`) skipped cleanly by `_looks_like_text()` without crash.<br>3. Unreadable file (`OSError` on open/read) handled gracefully without raising unhandled exception. |
| `skills/migrator.py` | **88%** | `20` missed stmts, `9` partial branches | 1. `_locate_skill_for_agent()` with multi-segment skill name (`a/b`) and missing skill error message.<br>2. `plan_migration()` blocking on symlinked source or unreadable target during fingerprint snapshot.<br>3. `execute_migration()` when source becomes unsafe/deleted between plan and apply.<br>4. `execute_migration()` when target turns into a symlink between plan and apply.<br>5. Staging hash mismatch (corrupted `copy_fn` writing wrong bytes) caught before swap.<br>6. Post-swap verification failure triggering clean rollback (where rollback succeeds and cleans up `.cb_migrate_failed_*`). |
| `skills/exporter.py` | **86%** | `9` missed stmts, `6` partial branches | 1. Exporting with `output_path=None` (defaults to `<cwd>/<skill>.zip`) and `output_path=<existing_dir>` (writes `<dir>/<skill>.zip`).<br>2. Output destination containing NTFS ADS (`out.zip:stream`) or symlink output rejected.<br>3. Skill with extra frontmatter fields appends compatibility note to `ExportResult.format_note`.<br>4. Temp ZIP cleanup if `zipfile.ZipFile` write raises an I/O error. |

### 2.2 Parsing, Validation, Core & CLI Modules (Priority 2 — Target >= 95% Overall)

| Module | Current Cover | Concrete Meaningful Scenarios to Test |
| --- | --- | --- |
| `skills/parser.py` | **78%** | Missing frontmatter (`---` absent), unclosed frontmatter, empty frontmatter (`---\n---`), non-mapping YAML (`- list`), invalid UTF-8 bytes, UTF-8 BOM stripping, oversized `SKILL.md` (>1 MiB), Markdown links inside fenced code blocks ignored, image/fragment/query links stripped cleanly, `frontmatter` Pydantic type mismatch (e.g. `metadata: "not-a-dict"`). |
| `skills/validator.py` | **80%** | Non-directory skill path, symlinked skill root, case-mismatched `skill.md` on disk, `name` > 64 chars, missing `description` / `description` > 1024 chars, `compatibility` > 500 chars, non-standard subdirectory (`DIR_NONSTANDARD` info), symlink inside skill tree (`SEC_SYMLINK_DETECTED` + continued scan of remaining files), oversized skill file (`SEC_SIZE_OR_COUNT_LIMIT`). |
| `adapters/base.py`, `cursor.py`, `opencode.py`, `gemini_cli.py`, `antigravity.py` | **85% – 95%** | macOS `/Applications/Cursor.app` and `/Applications/Antigravity.app` detection branches (with `platform="darwin"`), Windows `%APPDATA%\opencode` branch, `primary_write_directory()` fallback/error, unreadable skill directory skipped gracefully in `count_skills_in_dirs`, `compatibility_warnings_for_skill()` for `allowed-tools` and `frontmatter=None`. |
| `core/discovery.py` & `core/doctor.py` | **90% / 87%** | Unreadable skill directories skipped without crashing, `doctor` checks on missing project dir, symlinked skill dir (`WARN`), read-only skill dir (`WARN`), `installed_no_config` info check, valid-with-warnings skill check, and empty environment (`no-agents-detected`). |
| `core/paths.py` | **79%** | `XDG_CONFIG_HOME` override, `CONTEXTBRIDGE_HOME` override, `is_macos` / `is_linux` properties, `default_environment()`. |
| `cli.py` | **77%** | Interactive `typer.confirm` accept & decline flows (using `CliRunner(input="y\n")` and `input="n\n"`), error panel rendering on `ContextBridgeError` across `scan`, `skills list`, `skills export`, `skills migrate`, `backup restore`, empty `skills list` & `backup list` human output, `doctor` exit code `1` on errors, `skills validate --json` exit code `2` on invalid skill. |
| `utils/fs.py` | **66%** | `atomic_write_bytes` cleanup on mid-write failure, `is_readable_dir` / `is_writable_dir` on missing paths, `is_symlink_or_junction` on missing path (`OSError`), `get_file_mode_is_executable`. |
| `__main__.py` | **0%** | Already executed in `test_cli_subprocess.py`; subprocess coverage is not aggregated unless `COVERAGE_PROCESS_START` or in-process `runpy` smoke test is enabled. Tracked transparently (4 lines). |

---

## 3. Testing Strategy for New v0.2.0 Modules (Providers & CBCF)

Every v0.2.0 module will ship with >= 95% statement + branch coverage from day one, strictly offline and deterministic:

1. **Zero Live Network Calls in Automated Tests**:
   - Inject `httpx.MockTransport` into all AI Provider Adapters (`openai_chat`, `openai_responses`, `anthropic_messages`, `google_gemini`, `ollama_native`, `openai_compatible`, `custom_http`).
2. **Contract & Golden-Fixture Tests for All 7 Protocols**:
   - Verify exact HTTP method, URL path, required headers (`anthropic-version`, `x-goog-api-key`, `Authorization`), request body serialization, streaming SSE/NDJSON reassembly, and response-to-CBCF parsing.
3. **Adversarial Network & Privacy Security Tests**:
   - Block remote `http://` (non-localhost) URLs; allow explicit `http://127.0.0.1` / `http://localhost` / `http://[::1]`.
   - Block SSRF targets (`http://169.254.169.254/...`, metadata hosts).
   - Verify `301/302/307` redirects **never** forward `Authorization` or API key headers.
   - Verify **zero automatic retries** on `POST` generation timeouts/5xx errors (billing safety).
   - Verify streaming response exceeding `max_response_bytes` aborts immediately without loading unbounded memory.
   - Verify returned `tool_calls` and embedded prompt injections are stored as passive CBCF records and **never executed**.
   - Verify pre-send redaction masks secrets before building HTTP payloads and never logs API keys or raw secrets.
4. **CBCF Round-Trip & Lossiness Tests**:
   - Lossless round-trip for same-provider extensions (`anthropic.thinking.signature`, `gemini.thoughtSignature`, `openai_responses.previous_response_id`).
   - Explicit `lossless=False` + structured `warnings` when converting across incompatible protocols.
   - Strict JSON size/depth rejection on malformed CBCF inputs.
5. **Cross-OS CI Enforcement**:
   - Enforce `--cov-fail-under=95` in `.github/workflows/ci.yml` once Phase 2A hardening lands, verified across Windows, Ubuntu, and macOS (Python 3.11–3.15).
