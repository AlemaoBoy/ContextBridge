"""Read-only agent scanning and physically deduplicated skill discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from contextbridge.adapters.registry import get_adapter, list_adapters
from contextbridge.core.paths import EnvironmentContext
from contextbridge.models.agent import AgentDetectionResult, AgentId, AgentScope
from contextbridge.models.skill import SkillRecord
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits
from contextbridge.skills.validator import validate_skill
from contextbridge.utils.fs import canonical_path_key, is_readable_dir, is_symlink_or_junction


@dataclass(frozen=True)
class ScanSummary:
    """Complete read-only scan output across all supported agents."""

    agents: list[AgentDetectionResult]
    unique_physical_skills: int
    shared_directories: list[Path] = field(default_factory=list)


def discover_skills(
    env: EnvironmentContext,
    *,
    agent_filter: str | AgentId | None = None,
    scope_filter: AgentScope | None = None,
    limits: SecurityLimits = DEFAULT_LIMITS,
) -> list[SkillRecord]:
    """Discover skills across all agents and deduplicate by canonical physical directory path.

    Strictly read-only; never creates directories or modifies files.
    """
    target_agent_id: AgentId | None = None
    if agent_filter is not None:
        target_agent_id = get_adapter(agent_filter).agent_id

    # Always scan all adapters first so shared-directory ownership is accurate even when filtering
    records_by_key: dict[str, SkillRecord] = {}
    dir_owners: dict[str, set[AgentId]] = {}

    for adapter in list_adapters():
        specs = adapter.skill_directories(env, scope=scope_filter)
        for spec in specs:
            if not spec.path.exists() or not spec.path.is_dir():
                continue
            if not is_readable_dir(spec.path):
                continue

            dir_key = canonical_path_key(spec.path)
            dir_owners.setdefault(dir_key, set()).add(adapter.agent_id)

            try:
                entries = sorted(spec.path.iterdir(), key=lambda p: p.name)
            except OSError:
                continue

            for entry in entries:
                if not entry.is_dir() or is_symlink_or_junction(entry):
                    continue
                if not (entry / "SKILL.md").is_file():
                    continue

                skill_key = canonical_path_key(entry)
                existing = records_by_key.get(skill_key)
                if existing is not None:
                    if spec.is_compatibility_alias:
                        if adapter.agent_id not in existing.compatibility_agents:
                            existing.compatibility_agents.append(adapter.agent_id)
                    else:
                        if adapter.agent_id not in existing.associated_agents:
                            existing.associated_agents.append(adapter.agent_id)
                    if (
                        len(existing.associated_agents) + len(existing.compatibility_agents)
                    ) > 1 or spec.is_shared_standard:
                        existing.is_shared_directory = True
                    continue

                report = validate_skill(entry, limits=limits)
                fm = report.frontmatter
                name = (fm.name if fm and fm.name else None) or entry.name
                description = (fm.description if fm and fm.description else "") or ""
                extras = sorted(fm.extra_fields()) if fm else []

                primary_agents = [] if spec.is_compatibility_alias else [adapter.agent_id]
                compat_agents = [adapter.agent_id] if spec.is_compatibility_alias else []

                records_by_key[skill_key] = SkillRecord(
                    name=name,
                    description=description,
                    canonical_path=entry.resolve(),
                    display_path=entry,
                    scope=spec.scope,
                    associated_agents=primary_agents,
                    compatibility_agents=compat_agents,
                    validation_status=report.status,
                    error_count=len(report.errors),
                    warning_count=len(report.warnings),
                    is_shared_directory=spec.is_shared_standard,
                    extra_frontmatter_keys=extras,
                )

    results = list(records_by_key.values())
    if target_agent_id is not None:
        results = [
            rec
            for rec in results
            if target_agent_id in rec.associated_agents
            or target_agent_id in rec.compatibility_agents
        ]

    results.sort(key=lambda r: (r.name.lower(), r.scope.value, str(r.canonical_path)))
    return results


def scan_agents(
    env: EnvironmentContext,
    *,
    limits: SecurityLimits = DEFAULT_LIMITS,
) -> ScanSummary:
    """Detect all configured/installed agents and compute deduplicated physical skill count."""
    detections = [adapter.detect(env) for adapter in list_adapters()]

    dir_usage: dict[str, tuple[Path, int]] = {}
    for det in detections:
        for spec in det.skill_directories:
            if spec.exists:
                key = canonical_path_key(spec.path)
                prev_path, prev_count = dir_usage.get(key, (spec.path, 0))
                dir_usage[key] = (prev_path, prev_count + 1)

    shared_dirs = sorted(
        (path for path, count in dir_usage.values() if count > 1),
        key=lambda p: str(p),
    )

    unique_skills = discover_skills(env, limits=limits)
    return ScanSummary(
        agents=detections,
        unique_physical_skills=len(unique_skills),
        shared_directories=shared_dirs,
    )
