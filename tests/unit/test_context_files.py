"""Trust-boundary, schema and atomic write tests for offline contexts."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from contextbridge.context import files
from contextbridge.context.files import (
    json_records,
    load_document,
    local_path,
    output_path,
    read_source,
    validate_document,
    write_artifact,
)
from contextbridge.core.exceptions import ContextBridgeError

DOCUMENT = {
    "schema_version": "cbcf/1.0",
    "provenance": {"source_kind": "manual_export", "source_category": "user", "source_id": "test"},
}


@pytest.mark.parametrize(
    ("raw", "lines", "code", "message"),
    [
        (b"", True, 2, "no records"),
        (b"{", False, 2, "Invalid context JSON"),
        (b'{"x":{"y":1,"y":2}}', False, 2, "duplicate key"),
        (b'{"x":1,"\\u0078":2}', False, 2, "duplicate key"),
        (b'\xef\xbb\xbf{"x":1}', False, 2, "Invalid context JSON"),
        (b'{"x":"\xff"}', False, 2, "UTF-8"),
        (b'{"x":"\\ud800"}', False, 2, "unsupported JSON value"),
        (b'{"x": NaN}', False, 2, "unsupported JSON value"),
        (b'{"x": Infinity}', False, 2, "unsupported JSON value"),
        (b'{"x": 1e999}', False, 2, "unsupported JSON value"),
        (b"[]", False, 2, "contain an object"),
        (b'{"x":' + b"[" * 24 + b"0" + b"]" * 24 + b"}", False, 3, "nesting"),
        (b'{"x":[' + b"0," * files.MAX_ELEMENTS + b"0]}", False, 3, "elements"),
        (b"{}\n" * (files.MAX_MESSAGES * 2 + 1), True, 3, "records or elements"),
    ],
)
def test_json_rejections(raw: bytes, lines: bool, code: int, message: str) -> None:
    with pytest.raises(ContextBridgeError, match=message) as exc:
        json_records(raw, lines=lines)
    assert exc.value.exit_code == code


def test_jsonl_uses_physical_lines_and_enforces_aggregate_budget(monkeypatch) -> None:
    assert json_records('  \n{"x":"{[\\"\\\\}\u2028"}\r\n{}\n'.encode(), lines=True) == [
        {"x": '{["\\}\u2028'},
        {},
    ]
    monkeypatch.setattr(files, "MAX_ELEMENTS", 8)
    with pytest.raises(ContextBridgeError, match="records or elements"):
        json_records(b'{"x":1}\n{"x":2}\n{"x":3}', lines=True)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"schema_version": "cbcf/2.0"}, 2),
        ({"schema_version": None}, 2),
        ({"created_at": "2026-01-01T00:00:00"}, 2),
        ({"created_at": 1}, 2),
        ({"metadata": {"token_usage": {"input_tokens": "12"}}}, 2),
        ({"metadata": {"token_usage": {"input_tokens": -1}}}, 2),
        ({"messages": [{"role": "bad-role"}]}, 2),
        ({"messages": [{"role": "user", "parts": [{"type": "vendor_raw"}]}]}, 2),
        ({"messages": [{"role": "user", "parts": [{"type": "file_ref", "path": "a.py"}]}]}, 2),
        ({"messages": [{"role": "user"}] * 5001}, 3),
        ({"file_references": [{"path": "a.py", "sha256": "bad"}]}, 2),
        ({"file_references": [{"path": "a.py", "line_start": 4, "line_end": 2}]}, 2),
        ({"file_references": [{"path": "a.py", "line_start": 0}]}, 2),
        ({"file_references": [{"path": "a.py", "snippet": "body"}]}, 2),
        ({"file_references": [{"path": "a.py", "content_included": True}]}, 2),
        ({"file_references": [{"path": "../risky"}]}, 3),
        ({"file_references": [{"path": "C:\\risky"}]}, 3),
        ({"file_references": [{"path": "a.py:stream"}]}, 3),
        ({"file_references": [{"path": ".ssh"}]}, 3),
        ({"file_references": [{"path": "docs/.env"}]}, 3),
        ({"file_references": [{"path": "docs/credentials.json/a"}]}, 3),
        ({"handoff": {"modified_files": [{"path": "\\\\host\\x", "origin": "manual"}]}}, 3),
        ({"unexpected": {"secret": "example"}}, 2),
    ],
)
def test_schema_rejects_unsafe_or_untyped_values(change, code: int) -> None:
    with pytest.raises(ContextBridgeError) as exc:
        validate_document({**DOCUMENT, **change})
    assert exc.value.exit_code == code


def test_load_preserves_namespaced_unknown_fields_and_normalizes_paths(tmp_path: Path) -> None:
    file = tmp_path.resolve() / "input.cbcf.json"
    file.write_text(
        json.dumps(
            {
                **DOCUMENT,
                "created_at": "2026-01-01T00:00:00Z",
                "provider_extensions": {"vendor": {"future": [None, True, {"x": "value"}]}},
                "file_references": [{"path": "src\\test.py", "line_start": 3, "line_end": 3}],
            }
        ),
        encoding="utf-8",
    )
    doc = load_document(file)
    assert doc.created_at.isoformat() == "2026-01-01T00:00:00+00:00"
    assert doc.file_references[0].path == "src/test.py"
    assert doc.provider_extensions["vendor"]["future"] == [None, True, {"x": "value"}]


def test_file_errors_and_explicit_inputs(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    assert local_path(Path(Path.cwd().anchor)).is_absolute()
    assert local_path(Path(".")) == Path.cwd()
    with pytest.raises(ContextBridgeError, match=r"local files|unsafe relative"):
        local_path(Path("//server/share/document.json"))
    with pytest.raises(ContextBridgeError, match="Cannot read"):
        read_source(root / "missing.json")
    with pytest.raises(ContextBridgeError, match="CBCF input must be a JSON file"):
        load_document(root / "agent.db")
    with pytest.raises(ContextBridgeError, match="regular file"):
        read_source(root)
    path = root / "data.json"
    path.write_bytes(b"12345")
    monkeypatch.setattr(files, "MAX_BYTES", 4)
    with pytest.raises(ContextBridgeError, match="exceeds"):
        read_source(path)
    with pytest.raises(ContextBridgeError, match="credential path"):
        read_source(root / ".env")


def test_refuses_link_in_existing_or_missing_target_path(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    monkeypatch.setattr(files, "is_symlink_or_junction", lambda p: p.name == "linked")
    for path in (root / "linked", root / "linked" / "absent.cbcf.json"):
        with pytest.raises(ContextBridgeError, match="Symlink or junction") as exc:
            read_source(path)
        assert exc.value.exit_code == 3
        with pytest.raises(ContextBridgeError, match="Symlink or junction"):
            output_path(path)


def test_replaced_source_handle_is_rejected(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path.resolve() / "data.json"
    path.write_bytes(b"{}")
    monkeypatch.setattr(os.path, "samestat", lambda *_: False)
    with pytest.raises(ContextBridgeError, match="Context path changed"):
        read_source(path)


def test_output_guards_and_atomic_publish(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path.resolve()
    for blocked in (root / ".claude" / "export.md", root / "GEMINI.md", root / ".git" / "x.md"):
        with pytest.raises(ContextBridgeError, match="portable output"):
            write_artifact(blocked, b"{}")
    monkeypatch.setenv("CODEX_HOME", str(root / "agent-state"))
    with pytest.raises(ContextBridgeError, match="configured agent state"):
        output_path(root / "agent-state" / "out.md")
    monkeypatch.setenv("XDG_DATA_HOME", str(root / "xdg"))
    with pytest.raises(ContextBridgeError, match="outside application state"):
        output_path(root / "xdg" / "opencode" / "out.md")
    monkeypatch.setenv("GEMINI_CLI_HOME", str(root))
    with pytest.raises(ContextBridgeError, match="already exist"):
        output_path(root / "missing" / "out.md")
    target = root / "out.md"
    write_artifact(target, b"complete")
    with pytest.raises(ContextBridgeError, match="already exists") as exc:
        write_artifact(target, b"replacement")
    assert exc.value.exit_code == 4
    assert target.read_bytes() == b"complete"
    assert sorted(p.name for p in root.iterdir()) == ["out.md"]


@pytest.mark.parametrize(("error", "code"), [(FileExistsError, 4), (PermissionError, 5)])
def test_failed_publish_preserves_competing_output_and_cleans_temp(
    tmp_path: Path, monkeypatch, error, code: int
) -> None:
    root = tmp_path.resolve()
    target = root / "out.md"

    def fail_link(source, destination):
        assert source.read_bytes() == b"complete"
        destination.write_bytes(b"competing writer")
        raise error

    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(ContextBridgeError) as exc:
        write_artifact(target, b"complete")
    assert exc.value.exit_code == code
    assert target.read_bytes() == b"competing writer"
    assert sorted(p.name for p in root.iterdir()) == ["out.md"]
