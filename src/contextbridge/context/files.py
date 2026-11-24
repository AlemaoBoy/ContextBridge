"""Bounded local file and JSON handling for untrusted context artifacts."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from io import StringIO
from pathlib import Path
from typing import Never

from pydantic import JsonValue, ValidationError

from contextbridge.core.exceptions import (
    ContextBridgeError,
    FileSizeLimitError,
    PathTraversalError,
    SecretExposureError,
    UnsafeSymlinkError,
)
from contextbridge.models.context import MAX_MESSAGES, ContextDocument, portable_path
from contextbridge.utils.fs import FILE_ATTRIBUTE_REPARSE_POINT, is_symlink_or_junction

MAX_BYTES = 10 * 1024 * 1024
MAX_DEPTH = 24
MAX_ELEMENTS = 100_000
PROTECTED_DIRS = frozenset(
    {
        ".agent",
        ".agents",
        ".antigravity",
        ".claude",
        ".codex",
        ".contextbridge",
        ".cursor",
        ".gemini",
        ".git",
        ".opencode",
    }
)
STATE_DIR_OVERRIDES = (
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "OPENCODE_CONFIG_DIR",
    "CONTEXTBRIDGE_HOME",
)
INSTRUCTION_NAMES = frozenset({"agents.md", "claude.md", "gemini.md", "copilot-instructions.md"})


def invalid(message: str) -> ContextBridgeError:
    return ContextBridgeError(message, exit_code=2)


def local_path(path: Path) -> Path:
    if path.anchor.startswith(("\\\\", "//")) or (path.anchor and not path.is_absolute()):
        raise PathTraversalError("Context paths must use local files and directories.")
    parts = path.parts[1:] if path.anchor else path.parts
    if parts:
        portable_path("/".join(parts))
    absolute = path.absolute()
    for entry in (*reversed(absolute.parents), absolute):
        if is_symlink_or_junction(entry):
            raise UnsafeSymlinkError("Symlink or junction in context path.")
    return absolute


def _check_handle(path: Path, fd: int) -> None:
    current = path.lstat()
    if (
        not stat.S_ISREG(current.st_mode)
        or getattr(current, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
        or not os.path.samestat(current, os.fstat(fd))
    ):
        raise UnsafeSymlinkError("Context path changed or is not a regular file.")


def read_source(path: Path) -> bytes:
    path = local_path(path)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise invalid("Context input must be a regular file.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags | getattr(os, "O_BINARY", 0)), "rb") as handle:
            _check_handle(path, handle.fileno())
            local_path(path)
            data = handle.read(MAX_BYTES + 1)
    except OSError:
        raise invalid("Cannot read the requested context file.") from None
    if len(data) > MAX_BYTES:
        raise FileSizeLimitError("Context input exceeds 10 MiB.")
    return data


def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise invalid("Context input must be UTF-8.") from None


def _pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise invalid("Context JSON contains a duplicate key.")
        result[key] = value
    return result


def _reject_number(_: str) -> Never:
    raise ValueError("Non-finite JSON number.")


def _scan_json(text: str) -> int:
    depth = 0
    elements = 1
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise FileSizeLimitError("Context JSON nesting exceeds 24 containers.")
        elif char in "]}":
            depth = max(0, depth - 1)
        if not quoted and char in "[{:,":
            elements += 1
            if elements > MAX_ELEMENTS:
                raise FileSizeLimitError("Context JSON contains too many elements.")
    return elements


def _json_object(text: str) -> dict[str, JsonValue]:
    try:
        value: JsonValue = json.loads(text, object_pairs_hook=_pairs, parse_constant=_reject_number)
        # Also reject escaped lone surrogates and float overflow (such as 1e999).
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except ValueError:
        raise invalid("Invalid context JSON or unsupported JSON value.") from None
    if not isinstance(value, dict):
        raise invalid("Context JSON must contain an object.")
    return value


def json_records(data: bytes, *, lines: bool = False) -> list[dict[str, JsonValue]]:
    text = decode_text(data)
    records: list[dict[str, JsonValue]] = []
    elements = 0
    for line in StringIO(text) if lines else [text]:
        if lines and not line.strip():
            continue
        elements += _scan_json(line)
        if elements > MAX_ELEMENTS or len(records) >= MAX_MESSAGES * 2:
            raise FileSizeLimitError("Context JSON contains too many records or elements.")
        records.append(_json_object(line))
    if not records:
        raise invalid("Context file contains no records.")
    return records


def validate_document(data: dict[str, JsonValue]) -> ContextDocument:
    if data.get("schema_version") != "cbcf/1.0":
        raise invalid("Unsupported or missing context schema version. Expected cbcf/1.0.")
    messages = data.get("messages")
    if isinstance(messages, list) and len(messages) > MAX_MESSAGES:
        raise FileSizeLimitError("Context has more than 5,000 messages.")
    try:
        return ContextDocument.model_validate_json(json.dumps(data, allow_nan=False))
    except ValidationError as exc:
        kinds = sorted({entry["type"] for entry in exc.errors(include_input=False)})
        raise invalid(f"CBCF schema validation failed ({', '.join(kinds)}).") from None


def load_document(path: Path) -> ContextDocument:
    if path.suffix.casefold() != ".json":
        raise invalid("A CBCF input must be a JSON file.")
    return validate_document(json_records(read_source(path))[0])


def output_path(path: Path) -> Path:
    target = local_path(path)
    parts = {part.casefold() for part in target.parent.parts}
    if parts & PROTECTED_DIRS or target.name.casefold() in INSTRUCTION_NAMES:
        raise SecretExposureError("Choose a portable output outside agent and repository state.")
    for name in STATE_DIR_OVERRIDES:
        override = os.environ.get(name)
        if override and target.is_relative_to(Path(override).expanduser().absolute()):
            raise SecretExposureError(
                "Choose a portable output outside the configured agent state."
            )
    home = Path.home()
    app_roots = [home / ".config", home / ".local/share", home / "Library/Application Support"]
    app_roots.extend(
        Path(value).expanduser().absolute()
        for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "APPDATA", "LOCALAPPDATA")
        if (value := os.environ.get(name))
    )
    for root in app_roots:
        if any(
            target.is_relative_to(root / agent) for agent in ("opencode", "Cursor", "Antigravity")
        ):
            raise SecretExposureError("Choose a portable output outside application state.")
    if not target.parent.is_dir():
        raise invalid("Context output directory must already exist.")
    if target.exists():
        raise ContextBridgeError("Context output already exists.", exit_code=4)
    return target


def write_artifact(path: Path, data: bytes) -> None:
    target = output_path(path)
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".cb-context-", dir=target.parent, delete=False
        ) as f:
            temporary = Path(f.name)
            try:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
                f.close()
                local_path(target.parent)
                # Publish an entire file or fail, even if a competing writer takes the name.
                os.link(temporary, target)
            finally:
                f.close()
                temporary.unlink(missing_ok=True)
    except FileExistsError:
        raise ContextBridgeError("Context output already exists.", exit_code=4) from None
    except OSError:
        raise ContextBridgeError("Cannot safely write the context artifact.", exit_code=5) from None
