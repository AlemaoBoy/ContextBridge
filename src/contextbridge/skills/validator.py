"""Validator for the official Agent Skills specification (agentskills.io)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from contextbridge.core.exceptions import (
    FileSizeLimitError,
    PathTraversalError,
    UnsafeSymlinkError,
)
from contextbridge.models.skill import (
    IssueSeverity,
    SkillValidationIssue,
    SkillValidationReport,
    ValidationStatus,
)
from contextbridge.security.audit import audit_skill_file
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits
from contextbridge.security.paths import (
    collect_safe_skill_files,
    ensure_within_root,
    validate_relative_path,
)
from contextbridge.skills.parser import parse_skill_markdown
from contextbridge.utils.fs import is_symlink_or_junction

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500
STANDARD_SUBDIRS = frozenset({"scripts", "references", "assets", "examples", "resources"})


def _find_exact_skill_md(skill_dir: Path) -> tuple[Path | None, str | None]:
    """Check exact casing of SKILL.md in skill_dir even on case-insensitive filesystems."""
    try:
        entries = list(os.scandir(skill_dir))
    except OSError:
        return None, None

    exact: Path | None = None
    case_mismatch_name: str | None = None
    for entry in entries:
        if entry.name == "SKILL.md" and entry.is_file(follow_symlinks=False):
            exact = Path(entry.path)
            break
        if entry.name.lower() == "skill.md" and entry.is_file(follow_symlinks=False):
            case_mismatch_name = entry.name
    return exact, case_mismatch_name


def validate_skill(
    skill_dir: Path,
    *,
    limits: SecurityLimits = DEFAULT_LIMITS,
) -> SkillValidationReport:
    """Validate a skill directory against the official Agent Skills specification and heuristics.

    Strictly read-only; never executes scripts or modifies files.
    """
    issues: list[SkillValidationIssue] = []

    if not skill_dir.exists():
        issues.append(
            SkillValidationIssue(
                code="SKILL_DIR_MISSING",
                severity=IssueSeverity.ERROR,
                message=f"Skill directory does not exist: {skill_dir}",
            )
        )
        return SkillValidationReport(
            skill_path=skill_dir,
            status=ValidationStatus.INVALID,
            issues=issues,
        )

    if not skill_dir.is_dir():
        issues.append(
            SkillValidationIssue(
                code="SKILL_NOT_DIRECTORY",
                severity=IssueSeverity.ERROR,
                message=f"Skill path must be a directory: {skill_dir}",
            )
        )
        return SkillValidationReport(
            skill_path=skill_dir,
            status=ValidationStatus.INVALID,
            issues=issues,
        )

    if is_symlink_or_junction(skill_dir):
        issues.append(
            SkillValidationIssue(
                code="SEC_SYMLINK_ROOT",
                severity=IssueSeverity.ERROR,
                message="Skill root directory is a symlink or Windows junction, which is blocked.",
            )
        )
        return SkillValidationReport(
            skill_path=skill_dir,
            status=ValidationStatus.INVALID,
            issues=issues,
        )

    resolved_root = skill_dir.resolve()
    dir_name = resolved_root.name

    # 1. Check SKILL.md presence and exact case
    skill_md, mismatch_name = _find_exact_skill_md(resolved_root)
    if skill_md is None:
        if mismatch_name is not None:
            issues.append(
                SkillValidationIssue(
                    code="SKILL_MD_CASE",
                    severity=IssueSeverity.ERROR,
                    message=(
                        f"Found {mismatch_name!r}, but Agent Skills spec requires exact filename "
                        "'SKILL.md' for cross-platform compatibility."
                    ),
                    path=mismatch_name,
                )
            )
        else:
            issues.append(
                SkillValidationIssue(
                    code="SKILL_MD_MISSING",
                    severity=IssueSeverity.ERROR,
                    message="Required file 'SKILL.md' is missing from the skill directory.",
                    path="SKILL.md",
                )
            )
        return SkillValidationReport(
            skill_path=resolved_root,
            skill_name=dir_name,
            status=ValidationStatus.INVALID,
            issues=issues,
        )

    # 2. Parse SKILL.md safely
    parsed = parse_skill_markdown(skill_md, limits=limits)
    issues.extend(parsed.issues)
    frontmatter = parsed.frontmatter

    skill_name: str | None = dir_name
    if frontmatter is not None:
        # Validate `name`
        if not frontmatter.name or not isinstance(frontmatter.name, str):
            issues.append(
                SkillValidationIssue(
                    code="NAME_MISSING",
                    severity=IssueSeverity.ERROR,
                    message="Required frontmatter field 'name' is missing or empty.",
                    path="SKILL.md",
                )
            )
        else:
            skill_name = frontmatter.name
            if len(frontmatter.name) > MAX_NAME_LENGTH:
                issues.append(
                    SkillValidationIssue(
                        code="NAME_TOO_LONG",
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"Field 'name' length ({len(frontmatter.name)}) exceeds "
                            f"spec maximum of {MAX_NAME_LENGTH} characters."
                        ),
                        path="SKILL.md",
                    )
                )
            if not SKILL_NAME_RE.fullmatch(frontmatter.name):
                issues.append(
                    SkillValidationIssue(
                        code="NAME_FORMAT",
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"Field 'name' ({frontmatter.name!r}) must contain only lowercase "
                            "letters (a-z), digits (0-9), and single hyphens (-), without leading "
                            "or trailing hyphens."
                        ),
                        path="SKILL.md",
                    )
                )
            if frontmatter.name != dir_name:
                issues.append(
                    SkillValidationIssue(
                        code="NAME_DIR_MISMATCH",
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"Frontmatter 'name' ({frontmatter.name!r}) must match parent "
                            f"directory name ({dir_name!r})."
                        ),
                        path="SKILL.md",
                    )
                )

        # Validate `description`
        if not frontmatter.description or not frontmatter.description.strip():
            issues.append(
                SkillValidationIssue(
                    code="DESCRIPTION_MISSING",
                    severity=IssueSeverity.ERROR,
                    message="Required frontmatter field 'description' is missing or empty.",
                    path="SKILL.md",
                )
            )
        else:
            desc_len = len(frontmatter.description)
            if desc_len > MAX_DESCRIPTION_LENGTH:
                issues.append(
                    SkillValidationIssue(
                        code="DESCRIPTION_TOO_LONG",
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"Field 'description' length ({desc_len}) exceeds spec maximum of "
                            f"{MAX_DESCRIPTION_LENGTH} characters."
                        ),
                        path="SKILL.md",
                    )
                )
            elif desc_len < 15:
                issues.append(
                    SkillValidationIssue(
                        code="DESCRIPTION_BRIEF",
                        severity=IssueSeverity.WARNING,
                        message=(
                            "Field 'description' is very short; include what the skill does and "
                            "when agents should activate it."
                        ),
                        path="SKILL.md",
                    )
                )

        # Validate optional `compatibility`
        if (
            frontmatter.compatibility is not None
            and len(frontmatter.compatibility) > MAX_COMPATIBILITY_LENGTH
        ):
            issues.append(
                SkillValidationIssue(
                    code="COMPATIBILITY_TOO_LONG",
                    severity=IssueSeverity.ERROR,
                    message=(
                        f"Field 'compatibility' length ({len(frontmatter.compatibility)}) exceeds "
                        f"spec maximum of {MAX_COMPATIBILITY_LENGTH} characters."
                    ),
                    path="SKILL.md",
                )
            )

        # Informational notices for experimental and agent-specific frontmatter fields
        if frontmatter.allowed_tools is not None:
            issues.append(
                SkillValidationIssue(
                    code="FIELD_ALLOWED_TOOLS_EXPERIMENTAL",
                    severity=IssueSeverity.INFO,
                    message=(
                        "Frontmatter uses experimental 'allowed-tools' field; support varies "
                        "across AI coding agents."
                    ),
                    path="SKILL.md",
                )
            )

        extras = frontmatter.extra_fields()
        if extras:
            extra_keys = ", ".join(sorted(extras))
            issues.append(
                SkillValidationIssue(
                    code="FRONTMATTER_EXTRA_FIELDS",
                    severity=IssueSeverity.INFO,
                    message=(
                        f"Non-standard frontmatter field(s) detected ({extra_keys}); these may "
                        "be agent-specific and ignored by other agents."
                    ),
                    path="SKILL.md",
                )
            )

    # 3. Validate Markdown body content
    if parsed.body.strip() == "":
        issues.append(
            SkillValidationIssue(
                code="BODY_EMPTY",
                severity=IssueSeverity.WARNING,
                message="SKILL.md has an empty Markdown body; add actionable instructions.",
                path="SKILL.md",
            )
        )

    # 4. Validate local references in SKILL.md
    refs_checked = 0
    for ref in parsed.local_references:
        refs_checked += 1
        try:
            rel_posix = validate_relative_path(ref.raw_target)
        except PathTraversalError:
            issues.append(
                SkillValidationIssue(
                    code="REF_PATH_TRAVERSAL",
                    severity=IssueSeverity.ERROR,
                    message=(
                        f"Local reference {ref.raw_target!r} attempts path traversal or uses an "
                        "absolute path outside the skill directory."
                    ),
                    path="SKILL.md",
                    line=ref.line,
                )
            )
            continue

        candidate = resolved_root.joinpath(*rel_posix.parts)
        try:
            ensure_within_root(resolved_root, candidate)
        except PathTraversalError:
            issues.append(
                SkillValidationIssue(
                    code="REF_OUTSIDE_ROOT",
                    severity=IssueSeverity.ERROR,
                    message=f"Local reference {ref.raw_target!r} resolves outside skill root.",
                    path="SKILL.md",
                    line=ref.line,
                )
            )
            continue

        if not candidate.exists():
            issues.append(
                SkillValidationIssue(
                    code="REF_MISSING_FILE",
                    severity=IssueSeverity.ERROR,
                    message=f"Referenced local file does not exist: {ref.raw_target!r}",
                    path="SKILL.md",
                    line=ref.line,
                )
            )

    # 5. Inspect directory structure, symlinks, limits, and heuristic security checks
    files_checked = 0
    try:
        safe_files = collect_safe_skill_files(resolved_root, limits=limits, allow_symlinks=False)
    except UnsafeSymlinkError as exc:
        issues.append(
            SkillValidationIssue(
                code="SEC_SYMLINK_DETECTED",
                severity=IssueSeverity.ERROR,
                message=exc.message,
            )
        )
        safe_files = collect_safe_skill_files(resolved_root, limits=limits, allow_symlinks=True)
    except FileSizeLimitError as exc:
        issues.append(
            SkillValidationIssue(
                code="SEC_SIZE_OR_COUNT_LIMIT",
                severity=IssueSeverity.ERROR,
                message=exc.message,
            )
        )
        safe_files = []
    except PathTraversalError as exc:
        issues.append(
            SkillValidationIssue(
                code="SEC_PATH_TRAVERSAL",
                severity=IssueSeverity.ERROR,
                message=exc.message,
            )
        )
        safe_files = []

    # Check top-level directory conventions
    try:
        for child in sorted(resolved_root.iterdir()):
            if (
                child.is_dir()
                and not is_symlink_or_junction(child)
                and child.name not in STANDARD_SUBDIRS
                and not child.name.startswith(".")
            ):
                issues.append(
                    SkillValidationIssue(
                        code="DIR_NONSTANDARD",
                        severity=IssueSeverity.INFO,
                        message=(
                            f"Subdirectory '{child.name}/' is outside standard Agent Skills "
                            "folders (scripts/, references/, assets/)."
                        ),
                        path=child.name,
                    )
                )
    except OSError:
        pass

    for rel_posix, abs_path, _size in safe_files:
        files_checked += 1
        issues.extend(audit_skill_file(abs_path, rel_posix.as_posix()))

    has_errors = any(i.severity == IssueSeverity.ERROR for i in issues)
    has_warnings = any(i.severity == IssueSeverity.WARNING for i in issues)

    if has_errors:
        status = ValidationStatus.INVALID
    elif has_warnings:
        status = ValidationStatus.VALID_WITH_WARNINGS
    else:
        status = ValidationStatus.VALID

    return SkillValidationReport(
        skill_path=resolved_root,
        skill_name=skill_name,
        status=status,
        frontmatter=frontmatter,
        issues=issues,
        files_checked=files_checked,
        local_references_checked=refs_checked,
    )
