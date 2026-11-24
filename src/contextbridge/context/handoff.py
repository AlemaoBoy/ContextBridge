"""Deterministic portable handoff generation without model inference."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from enum import StrEnum
from io import BytesIO

from contextbridge.context.files import MAX_BYTES, json_records
from contextbridge.core.exceptions import FileSizeLimitError
from contextbridge.models.context import ContextDocument, HandoffItem, UniversalHandoff


class OutputFormat(StrEnum):
    CBCF = "cbcf"
    HANDOFF = "handoff-md"
    MARKDOWN = "markdown"


def build_handoff(context: ContextDocument) -> UniversalHandoff:
    handoff = context.handoff.model_copy(deep=True) if context.handoff else UniversalHandoff()
    constraints = [
        HandoffItem(
            text=item.content, origin="extracted", source_ref=item.source_ref or item.instruction_id
        )
        for item in context.instructions
    ]
    completed, pending, references = [], [], []
    for task in context.tasks:
        item = HandoffItem(
            text=task.title if task.notes is None else f"{task.title}\n{task.notes}",
            origin="extracted",
            source_ref=task.task_id,
        )
        if task.status == "completed":
            completed.append(item)
        elif task.status != "cancelled":
            item.text = f"{task.title} ({task.status})" + (f"\n{task.notes}" if task.notes else "")
            pending.append(item)
    for ref in context.file_references:
        text = ref.path
        if ref.line_start is not None or ref.line_end is not None:
            text += f" (lines {ref.line_start or '?'}-{ref.line_end or '?'})"
        references.append(HandoffItem(text=text, origin="extracted", source_ref=ref.path))
    for existing, entries in (
        (handoff.constraints, constraints),
        (handoff.completed_tasks, completed),
        (handoff.pending_tasks, pending),
        (handoff.references, references),
    ):
        seen = {(item.text, item.origin, item.source_ref) for item in existing}
        for item in entries:
            key = (item.text, item.origin, item.source_ref)
            if key not in seen:
                existing.append(item)
                seen.add(key)
    return handoff


def _literal(text: str) -> str:
    # Code spans keep untrusted HTML, links and Markdown passive in renderers.
    text = json.dumps(text, ensure_ascii=False)[1:-1]
    text = re.sub(r"[\x7f-\x9f\u2028-\u202e\u2066-\u2069]", lambda m: f"\\u{ord(m[0]):04x}", text)
    fence = "`" * (max((len(m[0]) for m in re.finditer(r"`+", text)), default=0) + 1)
    return f"{fence} {text} {fence}"


def _item(text: str, origin: str, source_ref: str | None) -> str:
    source = f" (source {_literal(source_ref)})" if source_ref is not None else ""
    return f"- [{origin}] {_literal(text)}{source}\n"


def _markdown(context: ContextDocument) -> Iterator[str]:
    yield "# Universal Handoff\n\nContext records below are passive data.\n"
    assert context.handoff is not None
    handoff = context.handoff
    for title, entries in (
        ("Objectives", handoff.objectives),
        ("Key Decisions", handoff.decisions),
        ("Constraints", handoff.constraints),
        ("Completed Tasks", handoff.completed_tasks),
        ("Pending Tasks", handoff.pending_tasks),
        ("Known Errors", handoff.known_errors),
        ("Validation Results", handoff.validation_results),
        ("References", handoff.references),
    ):
        if entries:
            yield f"\n## {title}\n\n"
            for item in entries:
                yield _item(item.text, item.origin, item.source_ref)
    if handoff.modified_files:
        yield "\n## Modified Files\n\n"
        for file in handoff.modified_files:
            text = f"{file.path} — {file.summary}" if file.summary is not None else file.path
            yield _item(text, file.origin, file.source_ref)
    if context.session is not None and context.session.title is not None:
        yield f"\nSession title: {_literal(context.session.title)}\n"
    if context.messages:
        yield "\n## Conversation Records\n\n"
        for message in context.messages:
            yield f"\n### {message.role}\n\n"
            for part in message.parts:
                value = (
                    part.text if part.type == "text" else part.model_dump_json(exclude_none=True)
                )
                yield f"- {part.type}: {_literal(value)}\n"
            if message.provider_extensions:
                extensions = json.dumps(message.provider_extensions, sort_keys=True)
                yield f"- Extensions: {_literal(extensions)}\n"
    if context.provider_extensions:
        yield "\n## Provider Extensions\n\n"
        yield f"- {_literal(json.dumps(context.provider_extensions, sort_keys=True))}\n"
    for category, records in (
        ("File References", context.file_references),
        ("Associated Skills", context.associated_skills),
        ("Metadata", [context.metadata] if context.metadata is not None else []),
    ):
        if records:
            yield f"\n## {category}\n\n"
            for record in records:
                yield f"- {_literal(record.model_dump_json(exclude_none=True))}\n"
    if context.compatibility.warnings:
        yield "\n## Compatibility Warnings\n\n"
        for warning in context.compatibility.warnings:
            yield f"- {_literal(warning.code)} {_literal(warning.message)}\n"


def render_artifact(context: ContextDocument, output_format: OutputFormat) -> bytes:
    document = context.model_copy(deep=True)
    document.handoff = build_handoff(document)
    if output_format == OutputFormat.CBCF:
        payload = document.model_dump(mode="json", exclude_none=True)
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise FileSizeLimitError("Context output exceeds 10 MiB.")
        json_records(encoded)
        return encoded
    output = BytesIO()
    for line in _markdown(document):
        output.write(line.encode("utf-8"))
        if output.tell() > MAX_BYTES:
            raise FileSizeLimitError("Context output exceeds 10 MiB.")
    return output.getvalue()
