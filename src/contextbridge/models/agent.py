"""Pydantic models representing AI coding agents and their detection state."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class AgentId(StrEnum):
    """Supported AI coding agent identifiers in v0.1.0."""

    CLAUDE = "claude"
    CODEX = "codex"
    ANTIGRAVITY = "antigravity"
    OPENCODE = "opencode"
    GEMINI_CLI = "gemini-cli"
    CURSOR = "cursor"


class AgentScope(StrEnum):
    """Scope of a skill or configuration directory."""

    GLOBAL = "global"
    PROJECT = "project"


class DetectionStatus(StrEnum):
    """Distinguishes binary installation from existing configuration directories."""

    INSTALLED_AND_CONFIGURED = "installed_and_configured"
    INSTALLED_NO_CONFIG = "installed_no_config"
    CONFIG_ONLY = "config_only"
    NOT_FOUND = "not_found"


class SkillDirectorySpec(BaseModel):
    """Describes a directory an agent scans for skills."""

    model_config = ConfigDict(frozen=True)

    path: Path
    scope: AgentScope
    is_primary: bool = True
    is_shared_standard: bool = False
    is_compatibility_alias: bool = False
    exists: bool = False
    readable: bool = False
    note: str | None = None


class AgentCapabilities(BaseModel):
    """Declares verified capabilities and format behavior for an agent."""

    model_config = ConfigDict(frozen=True)

    supports_agent_skills_spec: bool = True
    supports_global_scope: bool = True
    supports_project_scope: bool = True
    supports_zip_import_documented: bool = False
    zip_install_note: str = (
        "Standard Agent Skills ZIP archive; manual extraction into the agent's skills directory."
    )
    extra_frontmatter_fields: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class AgentDetectionResult(BaseModel):
    """Read-only detection report for a single agent."""

    agent_id: AgentId
    display_name: str
    status: DetectionStatus
    executable_paths: list[Path] = Field(default_factory=list)
    config_directories: list[Path] = Field(default_factory=list)
    skill_directories: list[SkillDirectorySpec] = Field(default_factory=list)
    supported_scopes: list[AgentScope] = Field(default_factory=list)
    skills_found: int = 0
    limitations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
