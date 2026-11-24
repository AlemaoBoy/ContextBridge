"""OpenAI Codex CLI agent adapter."""

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


class CodexAdapter(BaseAgentAdapter):
    """Adapter for OpenAI Codex CLI."""

    agent_id = AgentId.CODEX
    display_name = "OpenAI Codex"
    aliases = ("codex", "openai-codex", "codex-cli")
    executable_names = ("codex",)
    capabilities = AgentCapabilities(
        supports_agent_skills_spec=True,
        supports_global_scope=True,
        supports_project_scope=True,
        supports_zip_import_documented=False,
        zip_install_note=(
            "Extract the Agent Skills ZIP into ~/.agents/skills/ (global) or "
            ".agents/skills/ (project)."
        ),
        extra_frontmatter_fields=(),
        limitations=(
            "Uses shared .agents/skills standard directory; skills placed there are also visible "
            "to other agents that scan .agents/skills.",
            "Custom [[skills.config]] entries inside ~/.codex/config.toml are not parsed "
            "in v0.1.0.",
        ),
    )

    def _codex_home(self, env: EnvironmentContext) -> Path:
        override = env.resolve_env_path("CODEX_HOME")
        if override is not None:
            return override
        return env.home_dir / ".codex"

    def config_directories(self, env: EnvironmentContext) -> list[Path]:
        return [
            self._codex_home(env),
            env.project_dir / ".codex",
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
                    note="Official Codex repository skills directory (shared standard).",
                )
            )
        if scope in (None, AgentScope.GLOBAL):
            specs.append(
                SkillDirectorySpec(
                    path=env.home_dir / ".agents" / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=True,
                    is_shared_standard=True,
                    note="Official Codex user-level skills directory (shared standard).",
                )
            )
            codex_skills = self._codex_home(env) / "skills"
            specs.append(
                SkillDirectorySpec(
                    path=codex_skills,
                    scope=AgentScope.GLOBAL,
                    is_primary=False,
                    is_compatibility_alias=True,
                    note="CODEX_HOME skills path (commonly configured via ~/.codex/skills).",
                )
            )
        return specs
