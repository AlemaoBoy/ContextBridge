"""Read-only diagnostic engine for `contextbridge doctor`."""

from __future__ import annotations

import platform
import sys
from collections import defaultdict
from pathlib import Path

from contextbridge.adapters.registry import get_adapter, list_adapters
from contextbridge.backup.manager import BackupManager
from contextbridge.core.discovery import discover_skills, scan_agents
from contextbridge.core.paths import EnvironmentContext
from contextbridge.models.agent import DetectionStatus
from contextbridge.models.doctor import CheckStatus, DiagnosticCheck, DiagnosticReport
from contextbridge.models.skill import ValidationStatus
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits
from contextbridge.utils.fs import is_readable_dir, is_symlink_or_junction, is_writable_dir


def run_doctor(
    env: EnvironmentContext,
    *,
    limits: SecurityLimits = DEFAULT_LIMITS,
) -> DiagnosticReport:
    """Run strictly read-only environment, directory, skill, conflict, and backup diagnostics."""
    checks: list[DiagnosticCheck] = []

    # 1. Runtime check
    py_ver = platform.python_version()
    checks.append(
        DiagnosticCheck(
            category="runtime",
            name="python-version",
            status=CheckStatus.OK if sys.version_info >= (3, 11) else CheckStatus.ERROR,
            message=f"Python {py_ver} on {env.platform}",
            remediation="Install Python 3.11 or newer." if sys.version_info < (3, 11) else None,
        )
    )

    # 2. Project directory check
    if env.project_dir.exists() and env.project_dir.is_dir():
        if is_readable_dir(env.project_dir):
            checks.append(
                DiagnosticCheck(
                    category="workspace",
                    name="project-directory",
                    status=CheckStatus.OK,
                    message=f"Project directory is accessible: {env.project_dir}",
                    path=env.project_dir,
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    category="workspace",
                    name="project-directory",
                    status=CheckStatus.ERROR,
                    message=(
                        f"Insufficient read permissions for project directory: {env.project_dir}"
                    ),
                    path=env.project_dir,
                    remediation="Check filesystem read permissions on the working directory.",
                )
            )
    else:
        checks.append(
            DiagnosticCheck(
                category="workspace",
                name="project-directory",
                status=CheckStatus.ERROR,
                message=f"Project directory does not exist: {env.project_dir}",
                path=env.project_dir,
            )
        )

    # 3. Agent directories & permissions
    scan = scan_agents(env, limits=limits)
    detected_any = False
    for det in scan.agents:
        if det.status != DetectionStatus.NOT_FOUND:
            detected_any = True

        for spec in det.skill_directories:
            if not spec.exists:
                if det.status == DetectionStatus.INSTALLED_NO_CONFIG and spec.is_primary:
                    checks.append(
                        DiagnosticCheck(
                            category="agents",
                            name=f"{det.agent_id.value}-{spec.scope.value}-dir",
                            status=CheckStatus.INFO,
                            message=(
                                f"{det.display_name} is installed, but its {spec.scope.value} "
                                f"skills directory does not exist yet: {spec.path}"
                            ),
                            path=spec.path,
                        )
                    )
                continue

            if is_symlink_or_junction(spec.path):
                checks.append(
                    DiagnosticCheck(
                        category="security",
                        name=f"{det.agent_id.value}-symlink-dir",
                        status=CheckStatus.WARN,
                        message=(
                            f"Skills directory for {det.display_name} is a symlink/junction: "
                            f"{spec.path}. Migrations into symlinked roots are blocked by default."
                        ),
                        path=spec.path,
                    )
                )

            if not spec.readable:
                checks.append(
                    DiagnosticCheck(
                        category="permissions",
                        name=f"{det.agent_id.value}-unreadable",
                        status=CheckStatus.ERROR,
                        message=f"Cannot read {det.display_name} skills directory: {spec.path}",
                        path=spec.path,
                        remediation="Grant read permissions to your user account.",
                    )
                )
            elif not is_writable_dir(spec.path):
                checks.append(
                    DiagnosticCheck(
                        category="permissions",
                        name=f"{det.agent_id.value}-readonly",
                        status=CheckStatus.WARN,
                        message=(
                            f"Skills directory for {det.display_name} appears read-only: "
                            f"{spec.path}"
                        ),
                        path=spec.path,
                        remediation=(
                            "Migrations targeting this directory will require write access."
                        ),
                    )
                )

    if not detected_any:
        checks.append(
            DiagnosticCheck(
                category="agents",
                name="no-agents-detected",
                status=CheckStatus.INFO,
                message=(
                    "No supported AI coding agent installations or skill directories were found."
                ),
                remediation=(
                    "Install one of the supported agents or pass --project-dir "
                    "to inspect another workspace."
                ),
            )
        )
    else:
        checks.append(
            DiagnosticCheck(
                category="agents",
                name="agent-summary",
                status=CheckStatus.OK,
                message=(
                    f"Scanned {len(list_adapters())} supported agents; found "
                    f"{scan.unique_physical_skills} unique physical skill(s)."
                ),
            )
        )

    # 4. Skills validation, name collisions, and cross-agent frontmatter compatibility
    skills = discover_skills(env, limits=limits)
    skills_by_name: dict[str, list[Path]] = defaultdict(list)

    for skill in skills:
        skills_by_name[skill.name].append(skill.canonical_path)

        if skill.validation_status == ValidationStatus.INVALID:
            checks.append(
                DiagnosticCheck(
                    category="skills",
                    name=f"invalid-skill:{skill.name}",
                    status=CheckStatus.ERROR,
                    message=(
                        f"Skill '{skill.name}' has {skill.error_count} "
                        "validation/security error(s)."
                    ),
                    path=skill.canonical_path,
                    remediation=(
                        f'Run `contextbridge skills validate "{skill.canonical_path}"` for details.'
                    ),
                )
            )
        elif skill.validation_status == ValidationStatus.VALID_WITH_WARNINGS:
            checks.append(
                DiagnosticCheck(
                    category="skills",
                    name=f"warn-skill:{skill.name}",
                    status=CheckStatus.WARN,
                    message=(
                        f"Skill '{skill.name}' is valid with {skill.warning_count} warning(s)."
                    ),
                    path=skill.canonical_path,
                    remediation=(
                        f'Run `contextbridge skills validate "{skill.canonical_path}"` '
                        "to inspect warnings."
                    ),
                )
            )

        # Cross-agent frontmatter incompatibility in shared directories
        if skill.extra_frontmatter_keys and skill.is_shared_directory:
            all_agents = [*skill.associated_agents, *skill.compatibility_agents]
            incompatible_agents = [
                get_adapter(aid).display_name
                for aid in all_agents
                if any(
                    key not in get_adapter(aid).capabilities.extra_frontmatter_fields
                    for key in skill.extra_frontmatter_keys
                )
            ]
            if incompatible_agents:
                checks.append(
                    DiagnosticCheck(
                        category="compatibility",
                        name=f"frontmatter-compat:{skill.name}",
                        status=CheckStatus.WARN,
                        message=(
                            f"Shared skill '{skill.name}' uses agent-specific frontmatter "
                            f"({', '.join(skill.extra_frontmatter_keys)}) which may be ignored by: "
                            f"{', '.join(incompatible_agents)}."
                        ),
                        path=skill.canonical_path,
                    )
                )

    for skill_name, paths in sorted(skills_by_name.items()):
        if len(paths) > 1:
            formatted = ", ".join(str(p) for p in paths)
            checks.append(
                DiagnosticCheck(
                    category="conflicts",
                    name=f"duplicate-skill:{skill_name}",
                    status=CheckStatus.WARN,
                    message=(
                        f"Multiple distinct physical copies of skill '{skill_name}' exist "
                        f"({len(paths)} locations): {formatted}."
                    ),
                    remediation="Review precedence rules for each agent to avoid drift.",
                )
            )

    # 5. Local backup store health (read-only: never creates ~/.contextbridge)
    if env.backups_dir.exists():
        manager = BackupManager(env.backups_dir, limits=limits)
        manifests = manager.list_backups()
        corrupted = 0
        for manifest in manifests:
            ver = manager.verify_backup(manifest.backup_id)
            if not ver.valid:
                corrupted += 1
                checks.append(
                    DiagnosticCheck(
                        category="backups",
                        name=f"backup-integrity:{manifest.backup_id}",
                        status=CheckStatus.ERROR,
                        message=(
                            f"Backup {manifest.backup_id} failed SHA-256 verification: "
                            + "; ".join(ver.errors)
                        ),
                        path=env.backups_dir / manifest.backup_id,
                    )
                )
        if corrupted == 0:
            checks.append(
                DiagnosticCheck(
                    category="backups",
                    name="backups-integrity",
                    status=CheckStatus.OK,
                    message=(
                        f"Verified {len(manifests)} local backup(s) with intact SHA-256 manifests."
                    ),
                    path=env.backups_dir,
                )
            )
    else:
        checks.append(
            DiagnosticCheck(
                category="backups",
                name="backups-store",
                status=CheckStatus.INFO,
                message=(
                    f"Local backup directory does not exist yet ({env.backups_dir}); "
                    "it will be created automatically when needed."
                ),
            )
        )

    return DiagnosticReport(
        platform=env.platform,
        python_version=py_ver,
        project_dir=env.project_dir,
        checks=checks,
    )
