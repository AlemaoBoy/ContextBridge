"""Native format contracts checked with synthetic transcripts and exports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextbridge.context.local_sessions import load_session
from contextbridge.core.exceptions import ContextBridgeError
from contextbridge.models.agent import AgentId

STAMP = "2026-06-01T12:00:00Z"


@pytest.fixture
def source(tmp_path: Path):
    def save(data, suffix=".json"):
        file = tmp_path.resolve() / f"session{suffix}"
        text = "\n".join(map(json.dumps, data)) + "\n" if suffix == ".jsonl" else json.dumps(data)
        file.write_text(text, encoding="utf-8")
        return file

    return save


def test_claude_transcript_maps_order_roles_and_passive_tool_parts(source) -> None:
    path = source(
        [
            {
                "type": "user",
                "sessionId": "cl1",
                "uuid": "user",
                "timestamp": STAMP,
                "message": {"role": "user", "content": "First prompt"},
            },
            {
                "type": "assistant",
                "sessionId": "cl1",
                "uuid": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "example-model",
                    "usage": {"output_tokens": 3},
                    "content": [
                        {"type": "text", "text": "Reply", "citations": ["opaque"]},
                        {"type": "thinking", "thinking": "explicitly recorded", "signature": "sig"},
                        {"type": "redacted_thinking", "data": "encrypted"},
                        {
                            "type": "tool_use",
                            "id": "call",
                            "name": "Bash",
                            "input": {"command": "echo synthetic"},
                        },
                        {"type": "future_block", "value": "unknown value"},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "id": "result",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call",
                            "content": [{"type": "text", "text": "synthetic"}],
                            "is_error": False,
                        },
                    ],
                },
            },
            {"type": "progress", "value": "unknown event"},
        ],
        ".jsonl",
    )
    before = path.read_bytes()
    context = load_session(AgentId.CLAUDE, path)
    assert context.provenance.source_kind == "agent_local_history"
    assert context.provenance.model == "example-model"
    assert context.metadata.custom["reported_message_usage"] == [
        {"message_id": "assistant", "counts": {"output_tokens": 3}}
    ]
    assert context.session.session_id == "cl1"
    assert context.created_at is None and context.session.started_at is None
    assert [item.role for item in context.messages] == ["user", "assistant", "tool"]
    assert context.messages[0].timestamp.isoformat() == "2026-06-01T12:00:00+00:00"
    assert context.messages[1].parts[1].payload == {"citations": ["opaque"]}
    assert context.messages[1].parts[2].opaque_signature == "sig"
    assert context.messages[1].parts[3].raw_type == "redacted_thinking"
    assert context.messages[1].parts[3].payload["data"] == "encrypted"
    assert context.messages[1].parts[4].arguments == {"command": "echo synthetic"}
    assert context.messages[1].parts[5].raw_type == "future_block"
    assert context.messages[2].parts[0].tool_name == "Bash"
    assert context.messages[2].parts[0].output == [{"type": "text", "text": "synthetic"}]
    assert context.provider_extensions["claude"]["events"][0]["value"] == "unknown event"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "records",
    [
        [{"type": "event", "sessionId": "s"}],
        [{"type": "user", "sessionId": "s", "message": {"role": "assistant", "content": "text"}}],
        [{"type": "user", "sessionId": "s", "message": {"content": 42}}],
        [
            {"type": "user", "sessionId": "s", "message": {"content": "text"}},
            {"type": "event", "sessionId": "other"},
        ],
        [{"type": "user", "message": {"content": "text"}}],
    ],
)
def test_claude_rejects_invalid_or_mixed_records(source, records) -> None:
    with pytest.raises(ContextBridgeError) as exc:
        load_session(AgentId.CLAUDE, source(records, ".jsonl"))
    assert exc.value.exit_code == 2


def test_claude_result_without_available_call_preserves_missing_name(source) -> None:
    path = source(
        [
            {
                "type": "user",
                "sessionId": "cl1",
                "message": {
                    "content": [
                        {"type": "text", "text": "comment"},
                        {"type": "tool_result", "tool_use_id": "missing", "content": "observed"},
                    ]
                },
            }
        ],
        ".jsonl",
    )
    context = load_session(AgentId.CLAUDE, path)
    assert context.messages[0].role == "user"
    result = context.messages[0].parts[1]
    assert result.call_id == "missing" and result.tool_name is None and result.is_error is None
    assert not context.provider_extensions


def test_opencode_export_session_time_roles_instructions_and_tools(source) -> None:
    path = source(
        {
            "info": {
                "id": "oc1",
                "title": "Local export",
                "parentID": "parent",
                "directory": "/synthetic/workspace",
                "time": {"created": 1000, "updated": 2500.5, "archived": 4000},
            },
            "messages": [
                {
                    "info": {
                        "id": "q",
                        "sessionID": "oc1",
                        "role": "user",
                        "time": {"created": 2000},
                        "model": {"modelID": "example"},
                        "system": "Follow recorded project constraints",
                    },
                    "parts": [
                        {
                            "id": "p",
                            "sessionID": "oc1",
                            "messageID": "q",
                            "type": "text",
                            "text": "User text",
                        },
                    ],
                },
                {
                    "info": {
                        "id": "a",
                        "role": "assistant",
                        "modelID": "second-model",
                        "time": {"completed": 3000},
                    },
                    "parts": [
                        {"type": "reasoning", "text": "Recorded trace", "time": {"start": 10}},
                        {
                            "type": "tool",
                            "callID": "c1",
                            "tool": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": "echo synthetic"},
                                "output": "synthetic",
                            },
                        },
                        {
                            "type": "tool",
                            "callID": "c2",
                            "tool": "read",
                            "state": {
                                "status": "error",
                                "input": {"path": "safe.txt"},
                                "error": "not present",
                            },
                        },
                        {
                            "type": "tool",
                            "callID": "c3",
                            "tool": "edit",
                            "state": {"status": "pending", "input": {}},
                        },
                        {
                            "type": "tool",
                            "callID": "c4",
                            "tool": "future",
                            "state": {"status": "future", "output": 1},
                        },
                        {"type": "file", "url": "file:///private/do-not-open"},
                        {"some": "unknown discriminator"},
                    ],
                },
            ],
        }
    )
    context = load_session(AgentId.OPENCODE, path)
    assert context.session.parent_session_id == "parent"
    assert context.session.started_at.isoformat() == "1970-01-01T00:00:01+00:00"
    assert context.updated_at.isoformat() == "1970-01-01T00:00:02.500500+00:00"
    assert context.session.ended_at is None
    assert context.provenance.model is None
    assert context.provider_extensions["opencode"]["info"]["time"] == {"archived": 4000}
    assert context.messages[1].provider_extensions["opencode"]["time"] == {"completed": 3000}
    assert context.instructions[0].source_ref == "q"
    results = [p for p in context.messages[1].parts if p.type == "tool_result"]
    assert [(p.call_id, p.output, p.is_error) for p in results] == [
        ("c1", "synthetic", False),
        ("c2", "not present", True),
        ("c4", 1, None),
    ]
    assert context.messages[1].parts[-1].raw_type == "untyped"
    assert context.file_references == []


@pytest.mark.parametrize("times", [{"created": True}, {"created": "10"}, {"created": 10**100}])
def test_opencode_rejects_unsupported_times(source, times) -> None:
    with pytest.raises(ContextBridgeError, match="OpenCode timestamp"):
        load_session(
            AgentId.OPENCODE, source({"info": {"id": "id", "time": times}, "messages": []})
        )


def test_opencode_rejects_mixed_sessions_and_accepts_empty_export(source) -> None:
    context = load_session(AgentId.OPENCODE, source({"info": {"id": "s"}, "messages": []}))
    assert context.messages == [] and not context.provider_extensions
    mixed = source({"info": {"id": "s"}, "messages": [{"info": {"sessionID": "else"}}]})
    with pytest.raises(ContextBridgeError, match="mixes multiple sessions"):
        load_session(AgentId.OPENCODE, mixed)


def test_gemini_checkpoint_preserves_thoughts_tool_correlation_and_events(source) -> None:
    context = load_session(
        AgentId.GEMINI_CLI,
        source(
            {
                "sessionId": "g1",
                "projectHash": "synthetic",
                "startTime": STAMP,
                "lastUpdated": STAMP,
                "messages": [
                    {"id": "q", "type": "user", "content": "Initial user text"},
                    {
                        "id": "a",
                        "type": "gemini",
                        "timestamp": STAMP,
                        "content": [
                            {"text": "Answer", "thoughtSignature": "opaque"},
                            {"text": "Observed thought", "thought": True},
                            {"inlineData": {"mimeType": "test/media", "data": "opaque-inline"}},
                            {
                                "functionCall": {
                                    "id": "provider-call",
                                    "name": "fn",
                                    "args": {"path": "demo.py"},
                                }
                            },
                            {
                                "functionResponse": {
                                    "id": "provider-call",
                                    "name": "fn",
                                    "response": {"value": 1},
                                    "future": 1,
                                }
                            },
                        ],
                        "thoughts": [
                            {"subject": "Title", "description": "Actual note"},
                            {"future": 1},
                        ],
                        "toolCalls": [
                            {
                                "id": "t",
                                "name": "tool",
                                "args": {},
                                "result": [{"text": "data"}],
                                "status": "success",
                            },
                            {
                                "id": "e",
                                "name": "tool",
                                "args": {},
                                "result": "failed",
                                "status": "error",
                            },
                            {"id": "f", "name": "tool", "result": "future result"},
                            {"id": "p", "name": "tool"},
                        ],
                    },
                    {"id": "notice", "type": "info", "content": "local event"},
                ],
            }
        ),
    )
    assert context.session.session_id == "g1"
    parts = context.messages[1].parts
    assert [part.text for part in parts if part.type == "reasoning"] == [
        None,
        "Observed thought",
        "Title\nActual note",
    ]
    assert [part.text for part in parts if part.type == "text"] == ["Answer"]
    assert parts[0].opaque_signature == "opaque"
    assert [part.is_error for part in parts if part.type == "tool_result"] == [
        None,
        False,
        True,
        None,
    ]
    assert next(part.output for part in parts if part.type == "tool_result") == {"value": 1}
    assert context.provider_extensions["gemini-cli"]["events"][0]["id"] == "notice"


def test_gemini_jsonl_replays_patches_and_does_not_export_rewound_history(source) -> None:
    path = source(
        [
            {
                "sessionId": "g1",
                "projectHash": "synthetic",
                "startTime": STAMP,
                "messages": [
                    {"id": "old", "type": "user", "content": "clear legacy history"},
                ],
            },
            {
                "$set": {
                    "lastUpdated": STAMP,
                    "messages": [
                        {"id": "q", "type": "user", "content": "Original question"},
                    ],
                }
            },
            {"id": "rewound", "type": "user", "content": "rewound prompt"},
            {"id": "rewound-too", "type": "gemini", "content": "rewound answer"},
            {"$rewindTo": "rewound"},
            {"id": "removed", "type": "user", "content": "removed question"},
            {
                "id": "a",
                "type": "gemini",
                "content": [{"text": "initial answer"}],
                "toolCalls": [
                    {"id": "t", "name": "synthetic", "args": {}, "result": "old result"},
                    {"id": "u", "name": "unpatched"},
                ],
            },
            {
                "$patch": {
                    "id": "q",
                    "content": "Patched question",
                    "updates": [
                        {
                            "id": "a",
                            "content": [{"text": "Patched answer"}],
                            "toolCalls": [
                                {"id": "t", "result": "updated result"},
                                {"id": "u"},
                            ],
                        },
                        {"id": "already-missing", "content": "ignored patch"},
                    ],
                    "removeIds": ["removed", "missing"],
                    "orderIds": ["a", "q", "missing"],
                }
            },
            {"future": "unknown record"},
        ],
        ".jsonl",
    )
    context = load_session(AgentId.GEMINI_CLI, path)
    assert [message.message_id for message in context.messages] == ["a", "q"]
    assert context.messages[0].parts[0].text == "Patched answer"
    assert context.messages[0].parts[2].output == "updated result"
    assert context.messages[1].parts[0].text == "Patched question"
    assert context.provider_extensions["gemini-cli"]["unrecognized_records"] == [
        {"future": "unknown record"}
    ]
    assert "rewound" not in context.model_dump_json()
    assert "old result" not in context.model_dump_json()


def test_gemini_jsonl_unknown_rewind_and_mixed_metadata(source) -> None:
    meta = {"sessionId": "id", "projectHash": "synthetic"}
    doc = load_session(
        AgentId.GEMINI_CLI,
        source(
            [
                meta,
                {"id": "q", "type": "user", "content": "discard"},
                {"$rewindTo": "absent"},
            ],
            ".jsonl",
        ),
    )
    assert not doc.messages
    with pytest.raises(ContextBridgeError, match="mixes multiple sessions"):
        load_session(
            AgentId.GEMINI_CLI, source([meta, {"$set": {"sessionId": "another"}}], ".jsonl")
        )


@pytest.mark.parametrize(
    "record", [{"$future": {}}, {"$patch": {"future": []}}, {"$set": {}, "$patch": {}}]
)
def test_gemini_rejects_unknown_history_mutations(source, record) -> None:
    with pytest.raises(ContextBridgeError, match="Unsupported Gemini CLI session"):
        load_session(AgentId.GEMINI_CLI, source([{"sessionId": "id"}, record], ".jsonl"))


@pytest.mark.parametrize("agent", list(AgentId))
def test_agent_allows_manual_cbcf(source, agent) -> None:
    file = source(
        {
            "schema_version": "cbcf/1.0",
            "provenance": {
                "source_kind": "manual_export",
                "source_category": "user",
                "source_id": "manual",
            },
        },
        ".cbcf.json",
    )
    assert load_session(agent, file).provenance.source_kind == "manual_export"


@pytest.mark.parametrize("agent", list(AgentId))
def test_unsupported_source_does_not_open_native_database(tmp_path: Path, agent) -> None:
    with pytest.raises(
        ContextBridgeError, match=r"Unsupported source format|Native sessions are unsupported"
    ):
        load_session(agent, tmp_path.resolve() / "nonexistent.db")
