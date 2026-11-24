"""Google Antigravity agent adapter."""

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


class AntigravityAdapter(BaseAgentAdapter):
    """Adapter for Google Antigravity."""

    agent_id = AgentId.ANTIGRAVITY
    display_name = "Google Antigravity"
    aliases = ("antigravity", "agy", "google-antigravity")
    executable_names = ("agy", "antigravity")
    capabilities = AgentCapabilities(
        supports_agent_skills_spec=True,
        supports_global_scope=True,
        supports_project_scope=True,
        supports_zip_import_documented=False,
        zip_install_note=(
            "Extract the Agent Skills ZIP into ~/.gemini/config/skills/ (global) or "
            ".agents/skills/ (project)."
        ),
        extra_frontmatter_fields=(),
        limitations=(
            "Workspace skills may also live in legacy .agent/skills, _agents/skills, or "
            "explicit skills.json declarations; v0.1.0 scans .agents/skills and .agent/skills.",
            "Global path varies between ~/.gemini/config/skills (current customization guide) "
            "and ~/.gemini/antigravity/skills (app data layouts).",
        ),
    )

    def extra_executable_candidates(self, env: EnvironmentContext) -> list[Path]:
        candidates: list[Path] = []
        if env.is_macos:
            candidates.append(Path("/Applications/Antigravity.app"))
        elif env.is_windows:
            local_appdata = env.resolve_env_path("LOCALAPPDATA")
            if local_appdata is not None:
                candidates.append(local_appdata / "Programs" / "Antigravity" / "Antigravity.exe")
        return candidates

    def config_directories(self, env: EnvironmentContext) -> list[Path]:
        return [
            env.home_dir / ".gemini" / "config",
            env.home_dir / ".gemini" / "antigravity",
            env.project_dir / ".agent",
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
                    path=env.project_dir / ".agents" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=True,
                    is_shared_standard=True,
                    note="Current Antigravity workspace skills directory (shared standard).",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.project_dir / ".agent" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=False,
                    is_compatibility_alias=True,
                    note="Antigravity backward-compatibility workspace skills directory.",
                )
            )
        if scope in (None, AgentScope.GLOBAL):
            specs.append(
                SkillDirectorySpec(
                    path=env.home_dir / ".gemini" / "config" / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=True,
                    note="Antigravity global customization skills directory.",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.home_dir / ".gemini" / "antigravity" / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=False,
                    is_compatibility_alias=True,
                    note="Antigravity app-data global skills path variant.",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.home_dir / ".agents" / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=False,
                    is_shared_standard=True,
                    is_compatibility_alias=True,
                    note="Shared CLI global skills directory.",
                )
            )
        return specs
