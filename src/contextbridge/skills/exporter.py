"""Safe portable ZIP exporter for Agent Skills."""

from __future__ import annotations

import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from contextbridge.core.exceptions import (
    MigrationConflictError,
    SecretExposureError,
    SkillValidationError,
    UnsafeSymlinkError,
)
from contextbridge.models.skill import IssueSeverity, SkillValidationReport
from contextbridge.security.archive import inspect_zip_safety
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits
from contextbridge.security.paths import (
    collect_safe_skill_files,
    has_ntfs_ads_component,
    validate_relative_path,
)
from contextbridge.security.secrets import is_sensitive_filename
from contextbridge.skills.validator import validate_skill
from contextbridge.utils.fs import is_symlink_or_junction
from contextbridge.utils.hashing import sha256_file

SECURITY_CODE_PREFIX = "SEC_"


@dataclass(frozen=True)
class ExportResult:
    """Summary of an exported portable Agent Skill archive."""

    skill_name: str
    source_path: Path
    output_path: Path
    archive_format: str
    files_archived: int
    archive_size_bytes: int
    archive_sha256: str
    validation_report: SkillValidationReport
    format_note: str


def export_skill_zip(
    skill_dir: Path,
    output_path: Path | None = None,
    *,
    limits: SecurityLimits = DEFAULT_LIMITS,
    allow_validation_errors: bool = False,
    overwrite: bool = False,
) -> ExportResult:
    """Export a skill directory as a safe, deterministic Agent Skills ZIP archive.

    Always refuses symlinks/junctions, path traversal, sensitive credential files,
    detected secrets, and oversized files — even if `allow_validation_errors=True`.
    """
    resolved_dir = skill_dir.resolve()
    report = validate_skill(resolved_dir, limits=limits)

    # Always block on security errors (secrets, sensitive files, symlinks, path traversal)
    security_errors = [
        issue
        for issue in report.errors
        if issue.code.startswith(SECURITY_CODE_PREFIX) or issue.code == "REF_PATH_TRAVERSAL"
    ]
    if security_errors:
        reasons = "; ".join(f"[{e.code}] {e.message}" for e in security_errors)
        raise SecretExposureError(f"Export blocked by security policy: {reasons}")

    if not report.is_valid and not allow_validation_errors:
        reasons = "; ".join(f"[{e.code}] {e.message}" for e in report.errors)
        raise SkillValidationError(f"Skill failed validation and cannot be exported: {reasons}")

    skill_name = report.skill_name or resolved_dir.name
    safe_root_prefix = validate_relative_path(skill_name)

    files = collect_safe_skill_files(resolved_dir, limits=limits, allow_symlinks=False)
    for rel_posix, _, _ in files:
        if is_sensitive_filename(rel_posix):
            raise SecretExposureError(
                f"Refusing to archive sensitive credential/key file: {rel_posix}"
            )

    if output_path is None:
        destination = Path.cwd() / f"{skill_name}.zip"
    elif output_path.exists() and output_path.is_dir():
        destination = output_path / f"{skill_name}.zip"
    else:
        destination = output_path

    if has_ntfs_ads_component(destination):
        raise UnsafeSymlinkError(
            f"Output path contains an NTFS Alternate Data Stream: {destination}"
        )

    destination = destination.resolve()
    if destination.exists() or destination.is_symlink():
        if is_symlink_or_junction(destination):
            raise UnsafeSymlinkError(f"Output destination is a symlink or junction: {destination}")
        if not overwrite:
            raise MigrationConflictError(
                f"Output archive already exists: {destination}. Pass --overwrite to replace it."
            )
    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=".cb_export_",
        suffix=".zip",
        dir=str(destination.parent),
    )
    os.close(fd)
    tmp_zip = Path(tmp_name)

    try:
        with zipfile.ZipFile(tmp_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel_posix, abs_path, _size in files:
                arcname = (safe_root_prefix / rel_posix).as_posix()
                validate_relative_path(arcname)
                zinfo = zipfile.ZipInfo(filename=arcname)
                # Deterministic timestamp (2026-01-01 00:00:00) for reproducible builds
                zinfo.date_time = (2026, 1, 1, 0, 0, 0)
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                zinfo.external_attr = 0o644 << 16
                zf.writestr(zinfo, abs_path.read_bytes())

        # Post-write ZIP safety verification
        inspect_zip_safety(tmp_zip, limits=limits)
        os.replace(tmp_zip, destination)
    except Exception:
        tmp_zip.unlink(missing_ok=True)
        raise

    archive_sha256 = sha256_file(destination)
    archive_size = destination.stat().st_size

    has_extra_fields = any(
        i.code == "FRONTMATTER_EXTRA_FIELDS" and i.severity == IssueSeverity.INFO
        for i in report.issues
    )
    note = (
        "Portable Agent Skills ZIP layout (<skill-name>/SKILL.md + scripts/references/assets). "
        "Extract into a target agent's skills directory; native one-click ZIP import is not "
        "uniformly documented across all six agents."
    )
    if has_extra_fields:
        note += " Note: skill contains agent-specific frontmatter fields."

    return ExportResult(
        skill_name=skill_name,
        source_path=resolved_dir,
        output_path=destination,
        archive_format="zip",
        files_archived=len(files),
        archive_size_bytes=archive_size,
        archive_sha256=archive_sha256,
        validation_report=report,
        format_note=note,
    )
