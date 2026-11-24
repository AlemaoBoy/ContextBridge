"""Pydantic models for Agent Skills parsing, discovery, and validation."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contextbridge.models.agent import AgentId, AgentScope


class IssueSeverity(StrEnum):
    """Validation and audit issue severity levels."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationStatus(StrEnum):
    """Overall validation status of a skill."""

    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    INVALID = "invalid"


class SkillValidationIssue(BaseModel):
    """Single validation or heuristic security finding."""

    model_config = ConfigDict(frozen=True)

    code: str
    severity: IssueSeverity
    message: str
    path: str | None = None
    line: int | None = None
    is_heuristic: bool = False


class SkillFrontmatter(BaseModel):
    """Parsed YAML frontmatter from SKILL.md following the Agent Skills spec."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    description: str | None = None
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, Any] | None = None
    allowed_tools: str | list[str] | None = Field(default=None, alias="allowed-tools")

    def extra_fields(self) -> dict[str, Any]:
        """Return non-standard/agent-specific frontmatter keys."""
        if self.model_extra is None:
            return {}
        return dict(self.model_extra)


class SkillFileEntry(BaseModel):
    """Represents a single file inside a skill directory."""

    model_config = ConfigDict(frozen=True)

    relative_path: str
    size_bytes: int
    sha256: str | None = None
    is_symlink: bool = False


class SkillValidationReport(BaseModel):
    """Complete validation output for a skill directory."""

    skill_path: Path
    skill_name: str | None = None
    status: ValidationStatus
    frontmatter: SkillFrontmatter | None = None
    issues: list[SkillValidationIssue] = Field(default_factory=list)
    files_checked: int = 0
    local_references_checked: int = 0
    heuristic_notice: str = (
        "Static security checks are heuristic only and do not guarantee complete safety."
    )

    @property
    def errors(self) -> list[SkillValidationIssue]:
        return [issue for issue in self.issues if issue.severity == IssueSeverity.ERROR]

    @property
    def warnings(self) -> list[SkillValidationIssue]:
        return [issue for issue in self.issues if issue.severity == IssueSeverity.WARNING]

    @property
    def infos(self) -> list[SkillValidationIssue]:
        return [issue for issue in self.issues if issue.severity == IssueSeverity.INFO]

    @property
    def is_valid(self) -> bool:
        return self.status != ValidationStatus.INVALID


class SkillRecord(BaseModel):
    """Deduplicated physical skill discovered across one or more agents."""

    name: str
    description: str = ""
    canonical_path: Path
    display_path: Path
    scope: AgentScope
    associated_agents: list[AgentId] = Field(default_factory=list)
    compatibility_agents: list[AgentId] = Field(default_factory=list)
    validation_status: ValidationStatus = ValidationStatus.VALID
    error_count: int = 0
    warning_count: int = 0
    is_shared_directory: bool = False
    extra_frontmatter_keys: list[str] = Field(default_factory=list)
