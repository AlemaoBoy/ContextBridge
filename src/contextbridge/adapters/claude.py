"""Claude Code agent adapter."""

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


class ClaudeCodeAdapter(BaseAgentAdapter):
    """Adapter for Anthropic Claude Code."""

    agent_id = AgentId.CLAUDE
    display_name = "Claude Code"
    aliases = ("claude", "claude-code", "claudecode")
    executable_names = ("claude",)
    capabilities = AgentCapabilities(
        supports_agent_skills_spec=True,
        supports_global_scope=True,
        supports_project_scope=True,
        supports_zip_import_documented=False,
        zip_install_note=(
            "Extract the Agent Skills ZIP into ~/.claude/skills/ (global) or "
            ".claude/skills/ (project)."
        ),
        extra_frontmatter_fields=(
            "disable-model-invocation",
            "user-invocable",
            "context",
            "agent",
            "argument-hint",
            "allowed-tools",
            "model",
        ),
        limitations=(
            "Does not natively scan .agents/skills without symlinks or plugins.",
            "Claude-specific frontmatter fields (context: fork, agent, disable-model-invocation) "
            "are not portable to other agents.",
        ),
    )

    def _global_config_root(self, env: EnvironmentContext) -> Path:
        override = env.resolve_env_path("CLAUDE_CONFIG_DIR")
        if override is not None:
            return override
        return env.home_dir / ".claude"

    def config_directories(self, env: EnvironmentContext) -> list[Path]:
        return [
            self._global_config_root(env),
            env.project_dir / ".claude",
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
                    path=env.project_dir / ".claude" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=True,
                    note="Native Claude Code project skills directory.",
                )
            )
        if scope in (None, AgentScope.GLOBAL):
            specs.append(
                SkillDirectorySpec(
                    path=self._global_config_root(env) / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=True,
                    note="Native Claude Code global skills directory (honors CLAUDE_CONFIG_DIR).",
                )
            )
        return specs
