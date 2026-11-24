"""Security utilities and guards for ContextBridge."""

from contextbridge.security.archive import inspect_zip_safety, safe_extract_zip
from contextbridge.security.audit import audit_skill_file
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits
from contextbridge.security.paths import (
    collect_safe_skill_files,
    ensure_no_symlink_in_ancestors,
    ensure_within_root,
    has_ntfs_ads_component,
    validate_authorized_restore_target,
    validate_relative_path,
)
from contextbridge.security.secrets import is_sensitive_filename, scan_file_for_secrets

__all__ = [
    "DEFAULT_LIMITS",
    "SecurityLimits",
    "audit_skill_file",
    "collect_safe_skill_files",
    "ensure_no_symlink_in_ancestors",
    "ensure_within_root",
    "has_ntfs_ads_component",
    "inspect_zip_safety",
    "is_sensitive_filename",
    "safe_extract_zip",
    "scan_file_for_secrets",
    "validate_authorized_restore_target",
    "validate_relative_path",
]
