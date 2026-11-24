"""Selection, redaction and handoff behavior with authored synthetic data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextbridge.context import files, handoff
from contextbridge.context.files import load_document, validate_document
from contextbridge.context.handoff import OutputFormat, build_handoff, render_artifact
from contextbridge.context.transfer import (
    Category,
    Selection,
    category_counts,
    check_references,
    redact_context,
    select_context,
)
from contextbridge.core.exceptions import ContextBridgeError, FileSizeLimitError

TOKEN = "ghp_" + "A" * 36


@pytest.fixture
def context():
    return validate_document(
        {
            "schema_version": "cbcf/1.0",
            "provenance": {
                "source_kind": "manual_export",
                "source_category": "user",
                "source_id": "sample",
                "endpoint_hint": "https://sample.invalid/api?key=faux-query-value",
            },
            "session": {"title": "Fixture", "workspace_hint": "C:/anon/home/repo"},
            "instructions": [{"instruction_id": "ins", "content": f"Rule: {TOKEN}"}],
            "messages": [
                {
                    "role": "assistant",
                    "message_id": "m1",
                    "parts": [
                        {
                            "type": "text",
                            "text": "<img src='https://sample.invalid/pixel'>\n# heading `code`",
                        },
                        {
                            "type": "reasoning",
                            "text": "explicitly recorded",
                            "opaque_signature": "proof",
                        },
                        {
                            "type": "tool_call",
                            "call_id": "call",
                            "tool_name": "shell",
                            "arguments": {
                                "command": "would-run-if-unsafe",
                                "password": 1234,
                                "token": None,
                            },
                        },
                        {"type": "file_ref", "path": f"src/{TOKEN}.py"},
                        {
                            "type": "unknown_block",
                            "vendor": "sample",
                            "raw_type": "future",
                            "payload": {
                                "apiKey": "not-a-real-key",
                                "data": [False, {TOKEN: "hidden alongside key"}],
                            },
                        },
                    ],
                    "provider_extensions": {"vendor": {"extra": 1}},
                },
                {
                    "role": "tool",
                    "parts": [
                        {
                            "type": "tool_result",
                            "call_id": "call",
                            "output": ["Bearer example-bearer", None],
                        },
                        {"type": "text", "text": "tool text"},
                    ],
                },
                {"role": "system", "parts": [{"type": "text", "text": "system constraint"}]},
                {"role": "developer", "parts": [{"type": "text", "text": "developer constraint"}]},
                {"role": "user"},
            ],
            "file_references": [
                {
                    "path": f"src/{TOKEN}.py",
                    "content_included": True,
                    "snippet": "sample snippet",
                    "line_end": 5,
                }
            ],
            "tasks": [
                {
                    "task_id": "done",
                    "title": "Completed task",
                    "status": "completed",
                    "notes": "evidence",
                },
                {"task_id": "todo", "title": "Pending task", "status": "blocked"},
                {
                    "task_id": "later",
                    "title": "Later task",
                    "status": "pending",
                    "notes": "fake note",
                },
                {"task_id": "discard", "title": "Cancelled task", "status": "cancelled"},
            ],
            "metadata": {
                "custom": {
                    "key_block": "-----BEGIN "
                    "PRIVATE KEY-----\nFAKE_PEM_LINE\n-----END PRIVATE KEY-----",
                    "headers": "Authorization: Basic FAKE_HTTP_HEADER\nAccept: text/plain",
                    "query": "https://sample.invalid/?client_secret=shortsynthetic&safe=value",
                    "url": "https://synthetic:credential@sample.invalid/path",
                }
            },
            "associated_skills": [{"name": "sample-skill"}],
            "provider_extensions": {
                "vendor": {"opaque": "round-trip", "authorization": "test-auth"}
            },
            "compatibility": {"preserved_extensions": ["vendor.opaque"]},
            "handoff": {
                "objectives": [{"origin": "manual", "text": "Synthetic objective"}],
                "decisions": [{"origin": "manual", "text": "Çontext \\u202e \u202e note"}],
                "constraints": [{"origin": "manual", "text": "Manual constraint"}],
                "pending_tasks": [{"origin": "manual", "text": "Manual next step"}],
                "known_errors": [
                    {"origin": "extracted", "text": "One synthetic error", "source_ref": "m1"}
                ],
                "validation_results": [{"origin": "manual", "text": "Unit checks passed"}],
                "references": [{"origin": "manual", "text": "Reference source"}],
                "modified_files": [
                    {"origin": "manual", "path": "README.md", "summary": "Updated docs"},
                    {"origin": "extracted", "path": "tests/test_sample.py"},
                ],
            },
        }
    )


def test_conservative_defaults_and_independent_flags(context) -> None:
    before = context.model_dump_json()
    selected = select_context(context, Selection())
    observed = category_counts(selected)
    for category in Category:
        if category in (Category.SKILLS, Category.REASONING):
            assert observed[category] == 0
        else:
            assert observed[category] > 0
    assert not selected.file_references[0].content_included
    assert selected.file_references[0].snippet is None
    assert not selected.provider_extensions
    assert not selected.messages[0].provider_extensions
    assert not selected.compatibility.lossless
    assert {warning.code for warning in selected.compatibility.warnings} == {
        "CATEGORY_OMITTED",
        "SNIPPETS_OMITTED",
        "EXTENSIONS_OMITTED",
    }
    assert context.model_dump_json() == before
    for category in Category:
        only = select_context(
            context, Selection(frozenset({category}), frozenset(Category) - {category})
        )
        counts = category_counts(only)
        assert counts[category] > 0
        assert all(count == 0 for key, count in counts.items() if key != category)
    assert context.model_dump_json() == before


def test_conflicts_and_api_reasoning_default(context) -> None:
    with pytest.raises(ContextBridgeError, match="both included and excluded"):
        Selection(frozenset({Category.TASKS}), frozenset({Category.TASKS})).enabled(context)
    with pytest.raises(ContextBridgeError, match="requires file_references"):
        Selection(exclude=frozenset({Category.FILE_REFERENCES}), snippets=True).enabled(context)
    with pytest.raises(ContextBridgeError, match="every category"):
        Selection(extensions=True).enabled(context)
    context.provenance.source_kind = "api_response"
    assert Category.REASONING in Selection().enabled(context)
    assert Category.REASONING not in Selection(exclude=frozenset({Category.REASONING})).enabled(
        context
    )


def test_opt_in_redaction_and_json_round_trip(tmp_path: Path, context) -> None:
    all_categories = Selection(frozenset(Category), snippets=True, extensions=True)
    selected = select_context(context, all_categories)
    assert selected.model_dump_json() == context.model_dump_json()
    cleaned, matches = redact_context(selected)
    assert matches == 14
    assert cleaned.provenance.redacted
    assert cleaned.provenance.redaction_count == matches
    assert cleaned.compatibility.warnings[-1].code == "SECRETS_REDACTED"
    assert cleaned.file_references[0].path == "src/REDACTED-SEC_GITHUB_TOKEN.py"
    assert cleaned.file_references[0].snippet == "sample snippet"
    assert (
        cleaned.metadata.custom["query"]
        == "https://sample.invalid/?[REDACTED:SEC_CREDENTIAL_ASSIGNMENT]&safe=value"
    )
    artifact = render_artifact(cleaned, OutputFormat.CBCF)
    assert artifact == render_artifact(cleaned, OutputFormat.CBCF)
    for forbidden in (
        TOKEN,
        "FAKE_PEM_LINE",
        "FAKE_HTTP_HEADER",
        "example-bearer",
        "hidden alongside key",
        "faux-query-value",
        "sample.invalid/api?",
        "not-a-real-key",
        "test-auth",
        "shortsynthetic",
        "synthetic:credential",
        "C:/anon/home/repo",
    ):
        assert forbidden.encode() not in artifact
    export_file = tmp_path.resolve() / "result.cbcf.json"
    export_file.write_bytes(artifact)
    restored = load_document(export_file)
    assert restored.provider_extensions["vendor"]["opaque"] == "round-trip"
    assert restored.messages[0].provider_extensions == {"vendor": {"extra": 1}}
    assert restored.messages[0].parts[1].opaque_signature == "proof"
    again, new_count = redact_context(restored)
    assert new_count == 0 and again.provenance.redaction_count == matches
    assert again.model_dump_json() == restored.model_dump_json()
    assert artifact == render_artifact(restored, OutputFormat.CBCF)


def test_markdown_is_deterministic_and_passive(context) -> None:
    cleaned, _ = redact_context(
        select_context(context, Selection(frozenset(Category), extensions=True))
    )
    original = cleaned.model_dump_json()
    result = render_artifact(cleaned, OutputFormat.HANDOFF).decode()
    assert result == render_artifact(cleaned, OutputFormat.MARKDOWN).decode()
    assert cleaned.model_dump_json() == original
    assert "## Objectives\n\n- [manual] ` Synthetic objective `" in result
    assert (
        "## Completed Tasks\n\n- [extracted] ` Completed task\\nevidence ` (source ` done `)"
        in result
    )
    assert "` Later task (pending)\\nfake note `" in result
    assert "Cancelled task" not in result
    assert "## Known Errors" in result and "## Validation Results" in result
    assert "## Modified Files" in result and "README.md" in result
    assert "## Provider Extensions" in result and "- Extensions:" in result
    assert "## Associated Skills" in result and "## References" in result
    assert "`` <img src='https://sample.invalid/pixel'>\\n# heading `code` ``" in result
    assert "\u202e" not in result and "\\u202e" in result
    assert "\n# heading" not in result and "src/REDACTED-SEC_GITHUB_TOKEN.py (lines ?-5)" in result


def test_sparse_context_has_no_fabricated_handoff_facts() -> None:
    empty = validate_document(
        {
            "schema_version": "cbcf/1.0",
            "provenance": {
                "source_kind": "manual_export",
                "source_category": "user",
                "source_id": "user",
            },
        }
    )
    assert not any(build_handoff(empty).model_dump().values())
    assert "## Objectives" not in render_artifact(empty, OutputFormat.HANDOFF).decode()
    assert "created_at" not in json.loads(render_artifact(empty, OutputFormat.CBCF))


@pytest.mark.parametrize("format", list(OutputFormat))
def test_output_size_is_enforced(context, monkeypatch, format) -> None:
    monkeypatch.setattr(handoff, "MAX_BYTES", 100)
    with pytest.raises(FileSizeLimitError, match="output exceeds"):
        render_artifact(context, format)


def test_reference_checks_do_not_read_or_require_referenced_files(
    tmp_path: Path, context, monkeypatch
) -> None:
    root = tmp_path.resolve()

    def forbid_open(*_, **__):
        pytest.fail("Referenced content was opened")

    monkeypatch.setattr(Path, "open", forbid_open)
    check_references(context, root)
    assert list(root.iterdir()) == []
    with pytest.raises(ContextBridgeError, match="existing local directory"):
        check_references(context, root / "missing")
    monkeypatch.setattr(files, "is_symlink_or_junction", lambda p: p.name == "src")
    with pytest.raises(ContextBridgeError, match="Symlink or junction"):
        check_references(context, root)
