"""Pydantic models for skill migration planning and execution."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from contextbridge.models.agent import AgentId, AgentScope
from contextbridge.models.skill import SkillValidationReport


class FileActionType(StrEnum):
    """Type of filesystem operation planned for a file."""

    CREATE = "create"
    OVERWRITE = "overwrite"
    SKIP = "skip"


class FileAction(BaseModel):
    """Single planned file copy/overwrite action."""

    relative_path: str
    action: FileActionType
    size_bytes: int
    source_sha256: str


class ConflictInfo(BaseModel):
    """Describes an existing destination conflict."""

    target_path: Path
    existing_files_count: int
    reason: str


class MigrationPlan(BaseModel):
    """Full preview of a skill migration before applying changes."""

    skill_name: str
    source_agent: AgentId
    target_agent: AgentId
    source_scope: AgentScope
    target_scope: AgentScope
    source_path: Path
    target_path: Path
    source_validation: SkillValidationReport
    same_physical_target: bool = False
    conflict: ConflictInfo | None = None
    target_fingerprint: dict[str, str] | None = None
    requires_backup: bool = False
    actions: list[FileAction] = Field(default_factory=list)
    compatibility_warnings: list[str] = Field(default_factory=list)
    portability_notice: str = (
        "Porting skill files preserves the Agent Skills format, but does not guarantee "
        "identical runtime behavior, tool permissions, or invocation semantics across agents."
    )
    can_apply: bool = True
    blocking_reasons: list[str] = Field(default_factory=list)


class MigrationResult(BaseModel):
    """Outcome of executing (or dry-running) a migration plan."""

    plan: MigrationPlan
    applied: bool = False
    dry_run: bool = True
    backup_id: str | None = None
    files_written: int = 0
    verified_hashes: dict[str, str] = Field(default_factory=dict)
    rolled_back: bool = False
    message: str = ""
