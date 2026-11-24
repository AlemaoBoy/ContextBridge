"""Base abstract adapter defining cross-platform detection and skill directory resolution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from contextbridge.core.paths import EnvironmentContext
from contextbridge.models.agent import (
    AgentCapabilities,
    AgentDetectionResult,
    AgentId,
    AgentScope,
    DetectionStatus,
    SkillDirectorySpec,
)
from contextbridge.models.skill import SkillFrontmatter
from contextbridge.utils.fs import canonical_path_key, is_readable_dir, is_symlink_or_junction


class BaseAgentAdapter(ABC):
    """Contract implemented by every supported AI coding agent adapter."""

    agent_id: AgentId
    display_name: str
    aliases: tuple[str, ...] = ()
    executable_names: tuple[str, ...] = ()
    capabilities: AgentCapabilities = AgentCapabilities()

    @abstractmethod
    def config_directories(self, env: EnvironmentContext) -> list[Path]:
        """Return known configuration root directories for this agent (read-only)."""

    @abstractmethod
    def skill_directories(
        self,
        env: EnvironmentContext,
        scope: AgentScope | None = None,
    ) -> list[SkillDirectorySpec]:
        """Return known skill directories for this agent (read-only, never creates dirs)."""

    def extra_executable_candidates(self, env: EnvironmentContext) -> list[Path]:
        """Optional OS-specific desktop/app install locations checked read-only."""
        return []

    def find_executables(self, env: EnvironmentContext) -> list[Path]:
        """Locate installed CLI binaries or desktop executables without running them."""
        found: list[Path] = []
        seen: set[str] = set()
        for cmd in self.executable_names:
            located = env.which(cmd)
            if located:
                p = Path(located)
                key = canonical_path_key(p)
                if key not in seen:
                    seen.add(key)
                    found.append(p)
        for candidate in self.extra_executable_candidates(env):
            if candidate.exists():
                key = canonical_path_key(candidate)
                if key not in seen:
                    seen.add(key)
                    found.append(candidate)
        return found

    def primary_write_directory(
        self,
        env: EnvironmentContext,
        scope: AgentScope,
    ) -> Path:
        """Return the canonical target skills directory used when migrating into this agent."""
        dirs = self.skill_directories(env, scope=scope)
        for spec in dirs:
            if spec.is_primary:
                return spec.path
        if dirs:
            return dirs[0].path
        raise ValueError(f"Agent {self.agent_id} does not support scope {scope}.")

    def _populate_spec_fs_state(self, spec: SkillDirectorySpec) -> SkillDirectorySpec:
        exists = spec.path.exists() and spec.path.is_dir()
        readable = is_readable_dir(spec.path) if exists else False
        return spec.model_copy(update={"exists": exists, "readable": readable})

    def count_skills_in_dirs(self, specs: list[SkillDirectorySpec]) -> int:
        """Count unique physical skill directories visible to this agent."""
        seen: set[str] = set()
        for spec in specs:
            if not spec.exists or not spec.readable:
                continue
            try:
                for entry in sorted(spec.path.iterdir()):
                    if not entry.is_dir() or is_symlink_or_junction(entry):
                        continue
                    if (entry / "SKILL.md").is_file():
                        seen.add(canonical_path_key(entry))
            except OSError:
                continue
        return len(seen)

    def detect(self, env: EnvironmentContext) -> AgentDetectionResult:
        """Detect installation status, config dirs, skill dirs, and skill counts (read-only)."""
        executables = self.find_executables(env)
        existing_configs = [p for p in self.config_directories(env) if p.exists()]
        skill_specs = [self._populate_spec_fs_state(s) for s in self.skill_directories(env)]
        native_existing_skill_dirs = [
            s
            for s in skill_specs
            if s.exists and not s.is_shared_standard and not s.is_compatibility_alias
        ]

        has_binary = len(executables) > 0
        has_config = len(existing_configs) > 0 or len(native_existing_skill_dirs) > 0

        if has_binary and has_config:
            status = DetectionStatus.INSTALLED_AND_CONFIGURED
        elif has_binary:
            status = DetectionStatus.INSTALLED_NO_CONFIG
        elif has_config:
            status = DetectionStatus.CONFIG_ONLY
        else:
            status = DetectionStatus.NOT_FOUND

        supported_scopes: list[AgentScope] = []
        if self.capabilities.supports_project_scope:
            supported_scopes.append(AgentScope.PROJECT)
        if self.capabilities.supports_global_scope:
            supported_scopes.append(AgentScope.GLOBAL)

        skills_count = self.count_skills_in_dirs(skill_specs)

        notes: list[str] = []
        if any(s.is_shared_standard and s.exists for s in skill_specs):
            notes.append("Reads shared .agents/skills standard directory.")
        if status == DetectionStatus.NOT_FOUND and skills_count > 0:
            notes.append(
                "Skills detected only via shared/compatibility directories; agent not installed."
            )

        return AgentDetectionResult(
            agent_id=self.agent_id,
            display_name=self.display_name,
            status=status,
            executable_paths=executables,
            config_directories=existing_configs,
            skill_directories=skill_specs,
            supported_scopes=supported_scopes,
            skills_found=skills_count,
            limitations=list(self.capabilities.limitations),
            notes=notes,
        )

    def compatibility_warnings_for_skill(
        self,
        frontmatter: SkillFrontmatter | None,
    ) -> list[str]:
        """Evaluate frontmatter compatibility when targeting this agent."""
        warnings: list[str] = []
        if frontmatter is None:
            return warnings

        supported_extras = set(self.capabilities.extra_frontmatter_fields)
        for key in sorted(frontmatter.extra_fields()):
            if key not in supported_extras:
                warnings.append(
                    f"Non-standard frontmatter field '{key}' may be ignored by {self.display_name}."
                )

        if frontmatter.allowed_tools is not None and "allowed-tools" not in supported_extras:
            warnings.append(
                "Experimental 'allowed-tools' field support is not confirmed for "
                f"{self.display_name}."
            )

        return warnings
