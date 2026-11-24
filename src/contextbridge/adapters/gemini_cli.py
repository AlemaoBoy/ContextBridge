"""Google Gemini CLI agent adapter."""

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


class GeminiCliAdapter(BaseAgentAdapter):
    """Adapter for Google Gemini CLI."""

    agent_id = AgentId.GEMINI_CLI
    display_name = "Gemini CLI"
    aliases = ("gemini", "gemini-cli", "geminicli")
    executable_names = ("gemini",)
    capabilities = AgentCapabilities(
        supports_agent_skills_spec=True,
        supports_global_scope=True,
        supports_project_scope=True,
        supports_zip_import_documented=False,
        zip_install_note=(
            "Extract the Agent Skills ZIP into ~/.gemini/skills/ (global) or "
            ".gemini/skills/ (project). Gemini CLI also supports `gemini skills install <repo>`."
        ),
        extra_frontmatter_fields=(),
        limitations=(
            "Workspace skills require the folder to be marked trusted in Gemini CLI (/trust).",
            "Extension-bundled and built-in skills are managed by Gemini CLI and not scanned "
            "as user directories.",
        ),
    )

    def _gemini_root(self, env: EnvironmentContext) -> Path:
        override = env.resolve_env_path("GEMINI_CLI_HOME")
        if override is not None:
            return override / ".gemini"
        return env.home_dir / ".gemini"

    def _has_gemini_cli_markers(self, root: Path) -> bool:
        """Distinguish Gemini CLI config inside ~/.gemini from Antigravity-only subfolders."""
        if not root.is_dir():
            return False
        antigravity_only_dirs = {"antigravity", "config"}
        try:
            entries = list(root.iterdir())
        except OSError:
            return True
        if not entries:
            return False
        return any(e.name.lower() not in antigravity_only_dirs for e in entries)

    def config_directories(self, env: EnvironmentContext) -> list[Path]:
        dirs: list[Path] = []
        gemini_root = self._gemini_root(env)
        if not gemini_root.exists() or self._has_gemini_cli_markers(gemini_root):
            dirs.append(gemini_root)
        dirs.append(env.project_dir / ".gemini")
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
                    path=env.project_dir / ".gemini" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=True,
                    note="Native Gemini CLI workspace skills directory.",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.project_dir / ".agents" / "skills",
                    scope=AgentScope.PROJECT,
                    is_primary=False,
                    is_shared_standard=True,
                    note="Shared multi-agent workspace skills alias.",
                )
            )
        if scope in (None, AgentScope.GLOBAL):
            specs.append(
                SkillDirectorySpec(
                    path=self._gemini_root(env) / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=True,
                    note="Native Gemini CLI user skills directory (honors GEMINI_CLI_HOME).",
                )
            )
            specs.append(
                SkillDirectorySpec(
                    path=env.home_dir / ".agents" / "skills",
                    scope=AgentScope.GLOBAL,
                    is_primary=False,
                    is_shared_standard=True,
                    note="Shared multi-agent user skills alias.",
                )
            )
        return specs
