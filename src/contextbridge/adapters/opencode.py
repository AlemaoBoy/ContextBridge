"""OpenCode agent adapter."""

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


class OpenCodeAdapter(BaseAgentAdapter):
    """Adapter for OpenCode."""

    agent_id = AgentId.OPENCODE
    display_name = "OpenCode"
    aliases = ("opencode", "open-code")
    executable_names = ("opencode",)
    capabilities = AgentCapabilities(
        supports_agent_skills_spec=True,
        supports_global_scope=True,
        supports_project_scope=True,
        supports_zip_import_documented=False,
        zip_install_note=(
            "Extract the Agent Skills ZIP into ~/.config/opencode/skills/ (global) or "
            ".opencode/skills/ (project)."
        ),
        extra_frontmatter_fields=(),
        limitations=(
            "Also reads .agents/skills and .claude/skills compatibility paths.",
            "On Windows, legacy OpenCode wrappers may read %APPDATA%\\opencode instead of "
            "~/.config/opencode.",
        ),
    )

    def _global_config_root(self, env: EnvironmentContext) -> Path:
        override = env.resolve_env_path("OPENCODE_CONFIG_DIR")
        if override is not None:
            return override
        return env.xdg_config_home / "opencode"

    def config_directories(self, env: EnvironmentContext) -> list[Path]:
        dirs = [
            self._global_config_root(env),
            env.project_dir / ".opencode",
        ]
        if env.is_windows:
            appdata = env.resolve_env_path("APPDATA")
            if appdata is not None:
                dirs.append(appdata / "opencode")
        return dirs

    def skill_directories(
        self,
        env: EnvironmentContext,
        scope: AgentScope | None = None,
    ) -> list[SkillDirectorySpec]:
        specs: list[SkillDirectorySpec] = []
        if scope in (None, AgentScope.PROJECT):
            specs.append(
                SkillDirectorySpec(
                    path=env.project_dir / ".opencode" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=True,
                    note="Native OpenCode project skills directory.",
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
        if scope in (None, AgentScope.GLOBAL):
            specs.append(
                SkillDirectorySpec(
                    path=self._global_config_root(env) / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=True,
                    note=(
                        "Native OpenCode global skills directory "
                        "(honors OPENCODE_CONFIG_DIR / XDG_CONFIG_HOME)."
                    ),
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
        return specs
