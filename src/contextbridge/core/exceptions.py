"""Structured exception hierarchy for ContextBridge."""

from __future__ import annotations


class ContextBridgeError(Exception):
    """Base exception for all ContextBridge domain errors."""

    exit_code: int = 1

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if exit_code is not None:
            self.exit_code = exit_code


class SecurityViolationError(ContextBridgeError):
    """Raised when a security invariant is violated."""

    exit_code = 3


class PathTraversalError(SecurityViolationError):
    """Raised when a path escapes its authorized boundary."""


class UnsafeSymlinkError(SecurityViolationError):
    """Raised when a symlink or Windows junction is encountered in a restricted operation."""


class ArchiveSecurityError(SecurityViolationError):
    """Raised on ZIP Slip, ZIP bomb, or unsafe archive member detection."""


class SecretExposureError(SecurityViolationError):
    """Raised when credential files or sensitive secrets block an operation."""


class FileSizeLimitError(SecurityViolationError):
    """Raised when file count or size exceeds configured safety limits."""


class SkillNotFoundError(ContextBridgeError):
    """Raised when a requested skill cannot be located."""

    exit_code = 2


class SkillValidationError(ContextBridgeError):
    """Raised when a skill fails validation."""

    exit_code = 2


class UnknownAgentError(ContextBridgeError):
    """Raised when an unsupported or unknown agent identifier is specified."""

    exit_code = 2


class MigrationConflictError(ContextBridgeError):
    """Raised when a migration target already exists without explicit overwrite authorization."""

    exit_code = 4


class MigrationExecutionError(ContextBridgeError):
    """Raised when writing or verifying migrated files fails."""

    exit_code = 5


class BackupError(ContextBridgeError):
    """Raised when creating, inspecting, or restoring a backup fails."""

    exit_code = 5


class BackupIntegrityError(BackupError):
    """Raised when SHA-256 verification of backup artifacts fails."""

    exit_code = 6
