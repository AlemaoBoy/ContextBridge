"""Strongly typed ContextBridge Context Format (CBCF 1.0)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from contextbridge.core.exceptions import SecurityViolationError
from contextbridge.security.paths import validate_relative_path
from contextbridge.security.secrets import SENSITIVE_HIDDEN_DIRS, is_sensitive_filename


def portable_path(value: str) -> str:
    try:
        path = validate_relative_path(value)
    except SecurityViolationError:
        raise SecurityViolationError("Context contains an unsafe relative reference.") from None
    if any(
        part.casefold() in SENSITIVE_HIDDEN_DIRS or is_sensitive_filename(part)
        for part in path.parts
    ):
        raise SecurityViolationError("Context references a credential path.")
    return path.as_posix()


RelativePath = Annotated[str, AfterValidator(portable_path)]
Role = Literal["system", "developer", "user", "assistant", "tool"]
Origin = Literal["manual", "extracted"]
TaskStatus = Literal["pending", "in_progress", "completed", "blocked", "cancelled"]


class ContextModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)


class ContextProvenance(ContextModel):
    source_kind: Literal[
        "api_response", "agent_local_history", "manual_export", "converted_context"
    ]
    source_category: Literal["agent", "provider", "user"]
    source_id: str = Field(min_length=1)
    protocol: str | None = None
    model: str | None = None
    endpoint_hint: str | None = None
    redacted: bool = False
    redaction_count: NonNegativeInt = 0
    notes: str | None = None


class ContextSession(ContextModel):
    session_id: str | None = None
    title: str | None = None
    workspace_hint: str | None = None
    parent_session_id: str | None = None
    previous_response_id: str | None = None
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    status: str | None = None


class Instruction(ContextModel):
    instruction_id: str | None = None
    scope: Literal["global", "user", "project", "session", "message"] | None = None
    role: Literal["system", "developer"] | None = None
    source_ref: str | None = None
    content: str
    priority: int | None = None


class TextPart(ContextModel):
    type: Literal["text"] = "text"
    text: str


class ReasoningPart(ContextModel):
    type: Literal["reasoning"] = "reasoning"
    text: str | None = None
    opaque_signature: str | None = None
    signature_provider: str | None = None


class ToolCallPart(ContextModel):
    type: Literal["tool_call"] = "tool_call"
    call_id: str | None = None
    tool_name: str | None = None
    arguments: dict[str, JsonValue] | None = None


class ToolResultPart(ContextModel):
    type: Literal["tool_result"] = "tool_result"
    call_id: str | None = None
    tool_name: str | None = None
    output: JsonValue = None
    is_error: bool | None = None


class FileRefPart(ContextModel):
    type: Literal["file_ref"] = "file_ref"
    path: RelativePath


class UnknownBlock(ContextModel):
    type: Literal["unknown_block"] = "unknown_block"
    vendor: str
    raw_type: str
    payload: dict[str, JsonValue]


ContentPart = Annotated[
    TextPart | ReasoningPart | ToolCallPart | ToolResultPart | FileRefPart | UnknownBlock,
    Field(discriminator="type"),
]


class ContextMessage(ContextModel):
    message_id: str | None = None
    role: Role
    timestamp: AwareDatetime | None = None
    parts: list[ContentPart] = Field(default_factory=list)
    provider_extensions: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)


class FileReference(ContextModel):
    path: RelativePath
    media_type: str | None = None
    line_start: PositiveInt | None = None
    line_end: PositiveInt | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    content_included: bool = False
    snippet: str | None = None

    @model_validator(mode="after")
    def check_range_and_snippet(self) -> FileReference:
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("File reference line range is reversed.")
        if self.content_included != (self.snippet is not None):
            raise ValueError("File reference content flag and snippet disagree.")
        return self


class ContextTask(ContextModel):
    task_id: str | None = None
    title: str
    status: TaskStatus
    notes: str | None = None
    updated_at: AwareDatetime | None = None


class TokenUsage(ContextModel):
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    reasoning_tokens: NonNegativeInt | None = None
    estimated: bool | None = None


class ContextMetadata(ContextModel):
    token_usage: TokenUsage | None = None
    tags: list[str] = Field(default_factory=list)
    custom: dict[str, JsonValue] = Field(default_factory=dict)


class CompatibilityWarning(ContextModel):
    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    source_field: str | None = None
    target_protocol: str | None = None
    message: str


class ContextCompatibility(ContextModel):
    lossless: bool = True
    target_tested: list[str] = Field(default_factory=list)
    warnings: list[CompatibilityWarning] = Field(default_factory=list)
    dropped_fields: list[str] = Field(default_factory=list)
    preserved_extensions: list[str] = Field(default_factory=list)

    def report_loss(self, field: str, code: str, message: str) -> None:
        self.lossless = False
        if field not in self.dropped_fields:
            self.dropped_fields.append(field)
        warning = CompatibilityWarning(code=code, source_field=field, message=message)
        if warning not in self.warnings:
            self.warnings.append(warning)


class AssociatedSkill(ContextModel):
    name: str
    version: str | None = None
    summary: str | None = None


class HandoffItem(ContextModel):
    text: str
    origin: Origin
    source_ref: str | None = None


class ModifiedFile(ContextModel):
    path: RelativePath
    summary: str | None = None
    origin: Origin
    source_ref: str | None = None


class UniversalHandoff(ContextModel):
    objectives: list[HandoffItem] = Field(default_factory=list)
    decisions: list[HandoffItem] = Field(default_factory=list)
    constraints: list[HandoffItem] = Field(default_factory=list)
    completed_tasks: list[HandoffItem] = Field(default_factory=list)
    pending_tasks: list[HandoffItem] = Field(default_factory=list)
    modified_files: list[ModifiedFile] = Field(default_factory=list)
    known_errors: list[HandoffItem] = Field(default_factory=list)
    validation_results: list[HandoffItem] = Field(default_factory=list)
    references: list[HandoffItem] = Field(default_factory=list)


MAX_MESSAGES = 5_000


class ContextDocument(ContextModel):
    schema_version: Literal["cbcf/1.0"]
    context_id: str | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    provenance: ContextProvenance
    session: ContextSession | None = None
    instructions: list[Instruction] = Field(default_factory=list)
    messages: list[ContextMessage] = Field(default_factory=list, max_length=MAX_MESSAGES)
    file_references: list[FileReference] = Field(default_factory=list)
    tasks: list[ContextTask] = Field(default_factory=list)
    metadata: ContextMetadata | None = None
    compatibility: ContextCompatibility = Field(default_factory=ContextCompatibility)
    provider_extensions: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    associated_skills: list[AssociatedSkill] = Field(default_factory=list)
    handoff: UniversalHandoff | None = None

    @model_validator(mode="after")
    def check_reference_targets(self) -> ContextDocument:
        paths = {reference.path for reference in self.file_references}
        for message in self.messages:
            for part in message.parts:
                if isinstance(part, FileRefPart) and part.path not in paths:
                    raise ValueError("A file_ref part must point to a declared file reference.")
        return self
