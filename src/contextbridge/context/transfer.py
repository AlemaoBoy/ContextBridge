"""Selective transfer, secret redaction and compatibility diagnostics."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import JsonValue

from contextbridge.context.files import invalid, local_path, validate_document
from contextbridge.models.context import ContextDocument, Role
from contextbridge.security.secrets import SECRET_PATTERNS


class Category(StrEnum):
    MESSAGES = "messages"
    INSTRUCTIONS = "instructions"
    TASKS = "tasks"
    TOOL_CALLS = "tool_calls"
    TOOL_RESULTS = "tool_results"
    FILE_REFERENCES = "file_references"
    METADATA = "metadata"
    REASONING = "reasoning"
    SKILLS = "skills"


DEFAULT_CATEGORIES = frozenset(Category) - {Category.REASONING, Category.SKILLS}
PART_CATEGORIES = {
    "text": Category.MESSAGES,
    "unknown_block": Category.MESSAGES,
    "tool_call": Category.TOOL_CALLS,
    "tool_result": Category.TOOL_RESULTS,
    "file_ref": Category.FILE_REFERENCES,
    "reasoning": Category.REASONING,
}
HANDOFF_CATEGORIES = {
    Category.INSTRUCTIONS: ("constraints",),
    Category.TASKS: ("completed_tasks", "pending_tasks"),
    Category.FILE_REFERENCES: ("modified_files", "references"),
}
PEM = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----[\s\S]*?"
    r"(?:-----END (?:[A-Z0-9]+ )?PRIVATE KEY-----|\Z)"
)
BEARER = re.compile(r"\bBearer[ \t]+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
AUTH_HEADER = re.compile(
    r"(?im)^[ \t]*(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)[ \t]*:[^\r\n]+"
)
URL_AUTH = re.compile(r"\b(?:https?|socks5)://[^\s/?#@]+@", re.IGNORECASE)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"""(?i)\b(?:api[_-]?key|password|client[_-]?secret|(?:access|refresh)[_-]?token)\s*=\s*"""
    r"""(?:"[^"\r\n]+"|'[^'\r\n]+'|[^\s&;,\[\]{}<>"']+)"""
)
SECRET_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "proxyauthorization",
        "password",
        "passwd",
        "secret",
        "secretkey",
        "clientsecret",
        "accesstoken",
        "authtoken",
        "refreshtoken",
        "token",
        "privatekey",
        "credentials",
        "cookie",
        "setcookie",
    }
)
REDACTED_FIELD = "[REDACTED:SEC_CREDENTIAL_FIELD]"
REDACTED_WORKSPACE = "[REDACTED:SEC_WORKSPACE_PATH]"
REDACTION_RULES = (
    ("SEC_PRIVATE_KEY", PEM),
    ("SEC_AUTH_HEADER", AUTH_HEADER),
    ("SEC_BEARER", BEARER),
    ("SEC_URL_AUTH", URL_AUTH),
    ("SEC_CREDENTIAL_ASSIGNMENT", CREDENTIAL_ASSIGNMENT),
    *((rule.rule_id, rule.pattern) for rule in SECRET_PATTERNS),
)


@dataclass(frozen=True)
class Selection:
    include: frozenset[Category] = frozenset()
    exclude: frozenset[Category] = frozenset()
    snippets: bool = False
    extensions: bool = False

    def enabled(self, context: ContextDocument) -> frozenset[Category]:
        if self.include & self.exclude:
            raise invalid("A context category cannot be both included and excluded.")
        defaults = DEFAULT_CATEGORIES
        if context.provenance.source_kind == "api_response":
            defaults |= {Category.REASONING}
        enabled = (defaults | self.include) - self.exclude
        if self.snippets and Category.FILE_REFERENCES not in enabled:
            raise invalid("Including snippets requires file_references.")
        if self.extensions and enabled != frozenset(Category):
            raise invalid("Opaque extensions require every category to be enabled.")
        return enabled


def check_references(context: ContextDocument, workspace: Path | None) -> None:
    if workspace is None:
        return
    root = local_path(workspace)
    if not root.is_dir():
        raise invalid("Reference workspace must be an existing local directory.")
    paths = {reference.path for reference in context.file_references}
    if context.handoff is not None:
        paths.update(entry.path for entry in context.handoff.modified_files)
    for path in sorted(paths):
        local_path(root / path)


def _part_category(role: Role, kind: str) -> Category:
    if kind == "text":
        if role in ("system", "developer"):
            return Category.INSTRUCTIONS
        if role == "tool":
            return Category.TOOL_RESULTS
    return PART_CATEGORIES[kind]


def category_counts(context: ContextDocument) -> dict[str, int]:
    counts: Counter[Category] = Counter(
        _part_category(message.role, part.type)
        for message in context.messages
        for part in message.parts
    )
    counts.update(
        _part_category(message.role, "text") for message in context.messages if not message.parts
    )
    counts.update(
        {
            Category.INSTRUCTIONS: len(context.instructions),
            Category.TASKS: len(context.tasks),
            Category.FILE_REFERENCES: len(context.file_references),
            Category.METADATA: int(context.metadata is not None),
            Category.SKILLS: len(context.associated_skills),
        }
    )
    if context.handoff is not None:
        for category, fields in HANDOFF_CATEGORIES.items():
            counts[category] += sum(len(getattr(context.handoff, field)) for field in fields)
    return {category.value: counts[category] for category in Category}


def select_context(context: ContextDocument, selection: Selection) -> ContextDocument:
    enabled = selection.enabled(context)
    result = context.model_copy(deep=True)
    original_counts = category_counts(context)
    for category in Category:
        if category not in enabled and original_counts[category.value]:
            result.compatibility.report_loss(
                category.value, "CATEGORY_OMITTED", "Context selection omitted this category."
            )
    if Category.INSTRUCTIONS not in enabled:
        result.instructions.clear()
    if Category.TASKS not in enabled:
        result.tasks.clear()
    if Category.FILE_REFERENCES not in enabled:
        result.file_references.clear()
    if Category.METADATA not in enabled:
        result.metadata = None
    if Category.SKILLS not in enabled:
        result.associated_skills.clear()
    if result.handoff is not None:
        for category, fields in HANDOFF_CATEGORIES.items():
            if category not in enabled:
                for field in fields:
                    getattr(result.handoff, field).clear()
    if not selection.snippets and any(ref.content_included for ref in result.file_references):
        for ref in result.file_references:
            ref.content_included = False
            ref.snippet = None
        result.compatibility.report_loss(
            "file_references.snippet", "SNIPPETS_OMITTED", "Only reference metadata was selected."
        )

    has_extensions = bool(result.provider_extensions or result.compatibility.preserved_extensions)
    selected_messages = []
    for message in result.messages:
        parts = [
            part for part in message.parts if _part_category(message.role, part.type) in enabled
        ]
        if not selection.extensions:
            has_extensions |= bool(
                message.provider_extensions or any(p.type == "unknown_block" for p in message.parts)
            )
            message.provider_extensions.clear()
            parts = [part for part in parts if part.type != "unknown_block"]
        if (
            parts
            or (not message.parts and _part_category(message.role, "text") in enabled)
            or message.provider_extensions
        ):
            message.parts = parts
            selected_messages.append(message)
    result.messages = selected_messages
    if not selection.extensions and has_extensions:
        result.provider_extensions.clear()
        result.compatibility.preserved_extensions.clear()
        result.compatibility.report_loss(
            "provider_extensions",
            "EXTENSIONS_OMITTED",
            "Opaque vendor data requires explicit opt-in.",
        )
    return result


class _Redactor:
    def __init__(self) -> None:
        self.count = 0

    def text(self, text: str, *, path: bool = False) -> str:
        for code, pattern in REDACTION_RULES:
            marker = f"REDACTED-{code}" if path else f"[REDACTED:{code}]"
            text, count = pattern.subn(marker, text)
            self.count += count
        return text

    def walk(self, value: JsonValue, *, field: str = "") -> JsonValue:
        if isinstance(value, str):
            if field == "endpoint_hint" and any(mark in value for mark in ("@", "?", "#")):
                self.count += 1
                return "[REDACTED:SEC_ENDPOINT]"
            if (
                field == "workspace_hint"
                and value != REDACTED_WORKSPACE
                and any(mark in value for mark in ("/", "\\", ":"))
            ):
                self.count += 1
                return REDACTED_WORKSPACE
            return self.text(value, path=field == "path")
        if isinstance(value, list):
            return [self.walk(item) for item in value]
        if isinstance(value, dict):
            cleaned: dict[str, JsonValue] = {}
            for key, item in value.items():
                if self.text(key) != key:
                    continue
                normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                if normalized in SECRET_KEYS and item not in (None, REDACTED_FIELD):
                    self.count += 1
                    cleaned[key] = REDACTED_FIELD
                else:
                    cleaned[key] = self.walk(item, field=key)
            return cleaned
        return value


def redact_context(context: ContextDocument) -> tuple[ContextDocument, int]:
    redactor = _Redactor()
    data = redactor.walk(context.model_dump(mode="json"))
    assert isinstance(data, dict)
    result = validate_document(data)
    if redactor.count:
        result.provenance.redacted = True
        result.provenance.redaction_count += redactor.count
        result.compatibility.report_loss(
            "context", "SECRETS_REDACTED", "Sensitive data was redacted."
        )
    return result, redactor.count


def safe_artifact_name(name: str) -> str:
    return _Redactor().text(name)
