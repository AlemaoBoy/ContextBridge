"""Read explicitly supplied, documented agent transcript and export representations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeAlias

from pydantic import JsonValue

from contextbridge.context.files import (
    invalid,
    json_records,
    load_document,
    read_source,
    validate_document,
)
from contextbridge.models.agent import AgentId
from contextbridge.models.context import ContextDocument

Record: TypeAlias = dict[str, JsonValue]
Observation: TypeAlias = tuple[JsonValue, JsonValue, JsonValue]


def obj(value: JsonValue) -> Record:
    if not isinstance(value, dict):
        raise invalid("Unsupported agent export shape: expected an object.")
    return value


def array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise invalid("Unsupported agent export shape: expected an array.")
    return value


def identifier(value: JsonValue) -> str:
    if not isinstance(value, str) or not value:
        raise invalid("Unsupported agent export shape: missing or invalid identifier.")
    return value


def without(value: Record, *keys: str) -> Record:
    return {key: item for key, item in value.items() if key not in keys}


def unknown(vendor: AgentId, raw_type: JsonValue, payload: Record) -> JsonValue:
    return {
        "type": "unknown_block",
        "vendor": vendor.value,
        "raw_type": raw_type if isinstance(raw_type, str) else "untyped",
        "payload": payload,
    }


def message(
    vendor: AgentId,
    role: JsonValue,
    msg_id: JsonValue,
    timestamp: JsonValue,
    parts: list[JsonValue],
    extra: Record,
) -> JsonValue:
    return {
        "role": role,
        "message_id": msg_id,
        "timestamp": timestamp,
        "parts": parts,
        "provider_extensions": {vendor.value: extra} if extra else {},
    }


def document(
    vendor: AgentId,
    session: Record,
    messages: list[JsonValue],
    extra: Record,
    *,
    instructions: list[JsonValue] | None = None,
    updated_at: JsonValue = None,
    observations: list[Observation],
) -> ContextDocument:
    models = {model for _, model, _ in observations if isinstance(model, str) and model}
    usage: list[JsonValue] = [
        {"message_id": msg_id, "counts": counts}
        for msg_id, _, counts in observations
        if isinstance(counts, dict)
    ]
    return validate_document(
        {
            "schema_version": "cbcf/1.0",
            "provenance": {
                "source_category": "agent",
                "source_kind": "agent_local_history",
                "source_id": vendor.value,
                "model": next(iter(models)) if len(models) == 1 else None,
            },
            "session": session,
            "updated_at": updated_at,
            "messages": messages,
            "instructions": instructions or [],
            "metadata": {"custom": {"reported_message_usage": usage}} if usage else None,
            "provider_extensions": {vendor.value: extra} if extra else {},
        }
    )


def _claude_part(raw: JsonValue, names: dict[str, JsonValue]) -> list[JsonValue]:
    part = obj(raw)
    kind = part.get("type")
    match kind:
        case "text":
            result: Record = {"type": "text", "text": part.get("text")}
            remaining = without(part, "type", "text")
        case "thinking":
            result = {
                "type": "reasoning",
                "text": part.get("thinking"),
                "opaque_signature": part.get("signature"),
                "signature_provider": "anthropic",
            }
            remaining = without(part, "type", "thinking", "signature")
        case "tool_use":
            call_id = identifier(part.get("id"))
            names[call_id] = part.get("name")
            result = {
                "type": "tool_call",
                "call_id": call_id,
                "tool_name": part.get("name"),
                "arguments": part.get("input"),
            }
            remaining = without(part, "type", "id", "name", "input")
        case "tool_result":
            call_id = identifier(part.get("tool_use_id"))
            result = {
                "type": "tool_result",
                "call_id": call_id,
                "tool_name": names.get(call_id),
                "output": part.get("content"),
                "is_error": part.get("is_error"),
            }
            remaining = without(part, "type", "tool_use_id", "content", "is_error")
        case _:
            return [unknown(AgentId.CLAUDE, kind, part)]
    return [result, unknown(AgentId.CLAUDE, kind, remaining)] if remaining else [result]


def read_claude(records: list[Record]) -> ContextDocument:
    session_ids: set[str] = set()
    names: dict[str, JsonValue] = {}
    messages: list[JsonValue] = []
    events: list[JsonValue] = []
    observations: list[Observation] = []
    for row in records:
        if "sessionId" in row:
            session_ids.add(identifier(row["sessionId"]))
        kind = row.get("type")
        if kind not in ("user", "assistant"):
            events.append(row)
            continue
        info = obj(row.get("message"))
        content = info.get("content")
        if isinstance(content, str):
            parts: list[JsonValue] = [{"type": "text", "text": content}]
        else:
            parts = [part for block in array(content) for part in _claude_part(block, names)]
        role = info.get("role", kind)
        if role != kind:
            raise invalid("Claude Code transcript contains conflicting message roles.")
        if parts and all(obj(part)["type"] == "tool_result" for part in parts):
            role = "tool"
        msg_id = row.get("uuid", info.get("id"))
        observations.append((msg_id, info.get("model"), info.get("usage")))
        extra = without(row, "uuid", "sessionId", "timestamp", "type", "message")
        info_extra = without(info, "role", "content")
        if info_extra.get("id") == msg_id:
            info_extra.pop("id", None)
        if info_extra:
            extra["message"] = info_extra
        messages.append(
            message(
                AgentId.CLAUDE,
                role,
                msg_id,
                row.get("timestamp"),
                parts,
                extra,
            )
        )
    if len(session_ids) != 1 or not messages:
        raise invalid("Expected a single Claude Code session with transcript messages.")
    return document(
        AgentId.CLAUDE,
        {"session_id": next(iter(session_ids))},
        messages,
        {"events": events} if events else {},
        observations=observations,
    )


def _epoch_millis(value: JsonValue) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise invalid("OpenCode timestamps must be Unix milliseconds.")
    try:
        return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)).isoformat()
    except OverflowError:
        raise invalid("OpenCode timestamp is outside the supported range.") from None


def _opencode_part(part: Record) -> list[JsonValue]:
    kind = part.get("type")
    if kind in ("text", "reasoning"):
        records: list[JsonValue] = [{"type": kind, "text": part.get("text")}]
        remaining = without(part, "type", "text")
    elif kind == "tool":
        state = obj(part.get("state"))
        status = state.get("status")
        raw_result = state.get("error") if status == "error" else state.get("output")
        correlation = {"call_id": part.get("callID"), "tool_name": part.get("tool")}
        records = [{"type": "tool_call", **correlation, "arguments": state.get("input")}]
        if raw_result is not None:
            records.append(
                {
                    "type": "tool_result",
                    **correlation,
                    "output": raw_result,
                    "is_error": True
                    if status == "error"
                    else False
                    if status == "completed"
                    else None,
                }
            )
        remaining = without(part, "type", "callID", "tool", "state")
        remaining["state"] = without(state, "input", "output", "error")
    else:
        return [unknown(AgentId.OPENCODE, kind, part)]
    if remaining:
        records.append(unknown(AgentId.OPENCODE, kind, remaining))
    return records


def read_opencode(raw: Record) -> ContextDocument:
    info = obj(raw.get("info"))
    session_id = identifier(info.get("id"))
    times = obj(info.get("time", {}))
    messages: list[JsonValue] = []
    instructions: list[JsonValue] = []
    observations: list[Observation] = []
    for raw_message in array(raw.get("messages")):
        row = obj(raw_message)
        entry = obj(row.get("info"))
        if entry.get("sessionID", session_id) != session_id:
            raise invalid("OpenCode export mixes multiple sessions.")
        msg_id = entry.get("id")
        model = entry.get("model")
        model_id = model.get("modelID") if isinstance(model, dict) else entry.get("modelID")
        observations.append((msg_id, model_id, entry.get("tokens")))
        if entry.get("system") is not None:
            instructions.append(
                {
                    "role": "system",
                    "scope": "message",
                    "content": entry["system"],
                    "source_ref": msg_id,
                }
            )
        parts = [part for block in array(row.get("parts")) for part in _opencode_part(obj(block))]
        extra = without(entry, "id", "role", "time", "sessionID", "system")
        msg_times = obj(entry.get("time", {}))
        if time_extra := without(msg_times, "created"):
            extra["time"] = time_extra
        extra.update(without(row, "parts", "info"))
        messages.append(
            message(
                AgentId.OPENCODE,
                entry.get("role"),
                msg_id,
                _epoch_millis(msg_times.get("created")),
                parts,
                extra,
            )
        )
    extra = without(raw, "info", "messages")
    info_extra = without(info, "id", "title", "parentID", "time")
    if time_extra := without(times, "created", "updated"):
        info_extra["time"] = time_extra
    if info_extra:
        extra["info"] = info_extra
    return document(
        AgentId.OPENCODE,
        {
            "session_id": session_id,
            "title": info.get("title"),
            "parent_session_id": info.get("parentID"),
            "started_at": _epoch_millis(times.get("created")),
        },
        messages,
        extra,
        instructions=instructions,
        updated_at=_epoch_millis(times.get("updated")),
        observations=observations,
    )


def _gemini_content(content: JsonValue) -> list[JsonValue]:
    parts: list[JsonValue] = []
    for raw in content if isinstance(content, list) else [content]:
        if isinstance(raw, str):
            parts.append({"type": "text", "text": raw})
            continue
        part = obj(raw)
        if part.get("thought") is True or "thoughtSignature" in part:
            parts.append(
                {
                    "type": "reasoning",
                    "text": part.get("text") if part.get("thought") is True else None,
                    "opaque_signature": part.get("thoughtSignature"),
                    "signature_provider": "gemini",
                }
            )
        if "text" in part and part.get("thought") is not True:
            parts.append({"type": "text", "text": part["text"]})
        for key, kind, source_field, field in (
            ("functionCall", "tool_call", "args", "arguments"),
            ("functionResponse", "tool_result", "response", "output"),
        ):
            if key in part:
                function = obj(part[key])
                parts.append(
                    {
                        "type": kind,
                        "call_id": function.get("id"),
                        "tool_name": function.get("name"),
                        field: function.get(source_field),
                    }
                )
                if unmapped := without(function, "id", "name", source_field):
                    parts.append(unknown(AgentId.GEMINI_CLI, key, unmapped))
        extra = without(
            part, "text", "thought", "thoughtSignature", "functionCall", "functionResponse"
        )
        if extra:
            parts.append(unknown(AgentId.GEMINI_CLI, next(iter(extra)), extra))
    return parts


def _gemini_tool(tool: Record) -> list[JsonValue]:
    correlation = {"call_id": tool.get("id"), "tool_name": tool.get("name")}
    parts: list[JsonValue] = [{"type": "tool_call", **correlation, "arguments": tool.get("args")}]
    if tool.get("result") is not None:
        status = tool.get("status")
        parts.append(
            {
                "type": "tool_result",
                **correlation,
                "output": tool["result"],
                "is_error": True if status == "error" else False if status == "success" else None,
            }
        )
    remaining = without(tool, "id", "name", "args", "result")
    if remaining:
        parts.append(unknown(AgentId.GEMINI_CLI, "tool_metadata", remaining))
    return parts


def read_gemini(raw: Record) -> ContextDocument:
    session_id = identifier(raw.get("sessionId"))
    messages: list[JsonValue] = []
    events: list[JsonValue] = []
    observations: list[Observation] = []
    for raw_message in array(raw.get("messages")):
        row = obj(raw_message)
        msg_id = identifier(row.get("id"))
        kind = row.get("type")
        if kind not in ("user", "gemini"):
            events.append(row)
            continue
        parts = _gemini_content(row.get("content"))
        observations.append((msg_id, row.get("model"), row.get("tokens")))
        for tool in array(row.get("toolCalls", [])):
            parts.extend(_gemini_tool(obj(tool)))
        for raw_thought in array(row.get("thoughts", [])):
            thought = obj(raw_thought)
            lines = [
                value
                for key in ("subject", "description")
                if isinstance(value := thought.get(key), str)
            ]
            if lines:
                parts.append({"type": "reasoning", "text": "\n".join(lines)})
            parts.append(unknown(AgentId.GEMINI_CLI, "thought_summary", thought))
        messages.append(
            message(
                AgentId.GEMINI_CLI,
                "user" if kind == "user" else "assistant",
                msg_id,
                row.get("timestamp"),
                parts,
                without(row, "id", "type", "timestamp", "content", "toolCalls", "thoughts"),
            )
        )
    extra = without(raw, "sessionId", "startTime", "lastUpdated", "messages")
    if events:
        extra["events"] = events
    return document(
        AgentId.GEMINI_CLI,
        {"session_id": session_id, "started_at": raw.get("startTime")},
        messages,
        extra,
        updated_at=raw.get("lastUpdated"),
        observations=observations,
    )


def _patch_gemini(messages: dict[str, Record], patch: Record) -> None:
    if patch.keys() - {"id", "content", "toolCalls", "updates", "removeIds", "orderIds"}:
        raise invalid("Unsupported Gemini CLI session patch.")
    updates = ([patch] if "id" in patch else []) + [
        obj(row) for row in array(patch.get("updates", []))
    ]
    for update in updates:
        row = messages.get(identifier(update.get("id")))
        if row is None:
            continue
        if "content" in update:
            row["content"] = update["content"]
        tool_patches = {
            identifier(entry.get("id")): entry["result"]
            for item in array(update.get("toolCalls", []))
            if "result" in (entry := obj(item))
        }
        if tool_patches:
            for item in array(row.get("toolCalls", [])):
                tool = obj(item)
                tool_id = identifier(tool.get("id"))
                if tool_id in tool_patches:
                    tool["result"] = tool_patches[tool_id]
    for removed in array(patch.get("removeIds", [])):
        messages.pop(identifier(removed), None)
    for ordered in array(patch.get("orderIds", [])):
        key = identifier(ordered)
        if key in messages:
            messages[key] = messages.pop(key)


def replay_gemini(records: list[Record]) -> ContextDocument:
    meta: Record = {}
    messages: dict[str, Record] = {}
    events: list[JsonValue] = []
    for record in records:
        control = {key for key in record if key.startswith("$")}
        if control and (len(control) != 1 or control - {"$rewindTo", "$patch", "$set"}):
            raise invalid("Unsupported Gemini CLI session control record.")
        if "$rewindTo" in record:
            target = identifier(record["$rewindTo"])
            if target not in messages:
                messages.clear()
            else:
                while messages.popitem()[0] != target:
                    pass
        elif "$patch" in record:
            _patch_gemini(messages, obj(record["$patch"]))
        elif "id" in record and "type" in record:
            messages[identifier(record["id"])] = record
        elif "$set" in record or "sessionId" in record:
            update = obj(record["$set"]) if "$set" in record else record
            if (
                "sessionId" in meta
                and update.get("sessionId", meta["sessionId"]) != meta["sessionId"]
            ):
                raise invalid("Gemini CLI export mixes multiple sessions.")
            if "$set" in record and "messages" in update:
                messages.clear()
            if "messages" in update:
                for item in array(update["messages"]):
                    row = obj(item)
                    messages[identifier(row.get("id"))] = row
            meta.update(without(update, "messages"))
        else:
            events.append(record)
    meta["messages"] = list(messages.values())
    if events:
        meta["unrecognized_records"] = events
    return read_gemini(meta)


def load_session(agent: AgentId, path: Path) -> ContextDocument:
    if path.name.casefold().endswith(".cbcf.json"):
        return load_document(path)
    if agent in (AgentId.CODEX, AgentId.ANTIGRAVITY, AgentId.CURSOR):
        raise invalid(
            "Native sessions are unsupported for this agent. Supply a manual .cbcf.json file."
        )
    suffix = path.suffix.casefold()
    if agent == AgentId.CLAUDE and suffix == ".jsonl":
        return read_claude(json_records(read_source(path), lines=True))
    if agent == AgentId.OPENCODE and suffix == ".json":
        return read_opencode(json_records(read_source(path))[0])
    if agent == AgentId.GEMINI_CLI and suffix in (".json", ".jsonl"):
        records = json_records(read_source(path), lines=suffix == ".jsonl")
        return replay_gemini(records) if suffix == ".jsonl" else read_gemini(records[0])
    raise invalid("Unsupported source format. Supply a documented local file or a .cbcf.json file.")
