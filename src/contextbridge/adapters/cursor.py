"""Cursor agent adapter."""

from __future__ import annotations

from pathlib import Path

from contextbridge.adapters.base import BaseAgentAdapter
from contextbridge.core.paths import EnvironmentContext
from contextbridge.models.agent import (
    AgentCapabilities,
    AgentId,
    AgentScope,
    SkillDirectorySpec,
)


class CursorAdapter(BaseAgentAdapter):
    """Adapter for Cursor editor / agent."""

    agent_id = AgentId.CURSOR
    display_name = "Cursor"
    aliases = ("cursor", "cursor-ide", "cursor-agent")
    executable_names = ("cursor", "cursor-agent")
    capabilities = AgentCapabilities(
        supports_agent_skills_spec=True,
        supports_global_scope=True,
        supports_project_scope=True,
        supports_zip_import_documented=False,
        zip_install_note=(
            "Extract the Agent Skills ZIP into ~/.cursor/skills/ (global) or "
            ".cursor/skills/ (project)."
        ),
        extra_frontmatter_fields=(),
        limitations=(
            "Nested monorepo skill folders (e.g., apps/web/.cursor/skills) are not walked "
            "in v0.1.0.",
            "Also reads .agents/skills, .claude/skills, and .codex/skills compatibility "
            "directories.",
        ),
    )

    def extra_executable_candidates(self, env: EnvironmentContext) -> list[Path]:
        candidates: list[Path] = []
        if env.is_macos:
            candidates.append(Path("/Applications/Cursor.app"))
        elif env.is_windows:
            local_appdata = env.resolve_env_path("LOCALAPPDATA")
            if local_appdata is not None:
                candidates.append(local_appdata / "Programs" / "cursor" / "Cursor.exe")
        return candidates

    def config_directories(self, env: EnvironmentContext) -> list[Path]:
        return [
            env.home_dir / ".cursor",
            env.project_dir / ".cursor",
        ]

    def skill_directories(
        self,
        env: EnvironmentContext,
        scope: AgentScope | None = None,
    ) -> list[SkillDirectorySpec]:
        specs: list[SkillDirectorySpec] = []
        if scope in (None, AgentScope.PROJECT):
            specs.append(
                SkillDirectorySpec(
                    path=env.project_dir / ".cursor" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=True,
                    note="Native Cursor project skills directory.",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.project_dir / ".agents" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=False,
                    is_shared_standard=True,
                    note="Shared multi-agent project skills directory.",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.project_dir / ".claude" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=False,
                    is_compatibility_alias=True,
                    note="Claude Code compatibility project skills directory.",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.project_dir / ".codex" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=False,
                    is_compatibility_alias=True,
                    note="Codex compatibility project skills directory.",
                )
            )
        if scope in (None, AgentScope.GLOBAL):
            specs.append(
                SkillDirectorySpec(
                    path=env.home_dir / ".cursor" / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=True,
                    note="Native Cursor global skills directory.",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.home_dir / ".agents" / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=False,
                    is_shared_standard=True,
                    note="Shared multi-agent global skills directory.",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.home_dir / ".claude" / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=False,
                    is_compatibility_alias=True,
                    note="Claude Code compatibility global skills directory.",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.home_dir / ".codex" / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=False,
                    is_compatibility_alias=True,
                    note="Codex compatibility global skills directory.",
                )
            )
        return specs
