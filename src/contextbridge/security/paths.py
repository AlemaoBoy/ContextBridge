"""Filesystem boundary guards: path traversal, ADS, symlink/junction, and collision checks."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from contextbridge.core.exceptions import (
    FileSizeLimitError,
    PathTraversalError,
    SecurityViolationError,
    UnsafeSymlinkError,
)
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits
from contextbridge.utils.fs import is_symlink_or_junction

WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")

FORBIDDEN_RESTORE_DIR_NAMES = frozenset(
    {
        ".ssh",
        ".aws",
        ".gnupg",
        ".kube",
        "system32",
        "syswow64",
    }
)

FORBIDDEN_SYSTEM_PREFIXES = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
)


def has_ntfs_ads_component(path: Path | str) -> bool:
    """Return True if any non-drive path component contains ':' (NTFS Alternate Data Stream)."""
    raw = str(path)
    # Strip leading Windows drive letter (e.g., C:\ or C:/) before scanning for colons
    if DRIVE_PREFIX_RE.match(raw):
        raw = raw[2:]
    return ":" in raw


def validate_relative_path(raw_rel_path: str) -> PurePosixPath:
    """Validate that a relative path string is safe and portable across OSes.

    Rejects null bytes, control characters, NTFS Alternate Data Streams (':'),
    absolute/UNC paths, Windows drive letters, parent traversal ('..'),
    trailing dots/spaces, and Windows reserved device names.
    """
    if "\x00" in raw_rel_path:
        raise PathTraversalError("Path contains null byte.")

    if any(ord(ch) < 32 for ch in raw_rel_path):
        raise PathTraversalError(f"Path contains control character: {raw_rel_path!r}")

    normalized = raw_rel_path.replace("\\", "/")
    if not normalized or normalized.startswith("/") or normalized.startswith("//"):
        raise PathTraversalError(f"Absolute or UNC path is not permitted: {raw_rel_path!r}")

    if DRIVE_PREFIX_RE.match(normalized):
        raise PathTraversalError(f"Drive-prefixed path is not permitted: {raw_rel_path!r}")

    if ":" in normalized:
        raise PathTraversalError(
            f"Colon / NTFS Alternate Data Stream is not permitted in path: {raw_rel_path!r}"
        )

    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute():
        raise PathTraversalError(f"Absolute path is not permitted: {raw_rel_path!r}")

    cleaned_parts: list[str] = []
    for part in posix_path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise PathTraversalError(f"Path traversal ('..') is not permitted: {raw_rel_path!r}")
        if part.endswith(" ") or part.endswith("."):
            raise PathTraversalError(
                f"Trailing space or dot in filename component is not permitted: {part!r}"
            )
        stem = part.split(".", maxsplit=1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise PathTraversalError(
                f"Windows reserved filename component is not permitted: {part!r}"
            )
        cleaned_parts.append(part)

    if not cleaned_parts:
        raise PathTraversalError(f"Empty relative path is not permitted: {raw_rel_path!r}")

    return PurePosixPath(*cleaned_parts)


def ensure_within_root(root: Path, candidate: Path) -> Path:
    """Resolve `candidate` and verify it stays strictly within `root`."""
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise PathTraversalError(
            f"Path {candidate} escapes authorized root {resolved_root}."
        ) from exc
    return resolved_candidate


def ensure_no_symlink_in_ancestors(root: Path, target: Path) -> None:
    """Check `target` and all components between `root` and `target` for symlinks/junctions."""
    if is_symlink_or_junction(root):
        raise UnsafeSymlinkError(f"Root directory itself is a symlink or junction: {root}")

    try:
        rel = target.relative_to(root)
    except ValueError:
        abs_root = Path(os.path.abspath(root))
        abs_target = Path(os.path.abspath(target))
        try:
            rel = abs_target.relative_to(abs_root)
        except ValueError as exc:
            raise PathTraversalError(f"Path {target} is not inside {root}.") from exc
        current = abs_root
    else:
        current = root

    for part in rel.parts:
        current = current / part
        if (current.exists() or current.is_symlink()) and is_symlink_or_junction(current):
            raise UnsafeSymlinkError(
                f"Symlink or Windows junction is not allowed inside skill tree: {current}"
            )


def validate_authorized_restore_target(
    target: Path,
    *,
    backups_root: Path,
    allowed_roots: Sequence[Path] | None = None,
    protected_roots: Sequence[Path] | None = None,
    home_dir: Path | None = None,
    require_within_allowed_roots: bool = True,
) -> Path:
    """Validate that a backup restore destination is safe and within authorized boundaries.

    Always rejects:
    - null bytes, NTFS Alternate Data Streams (':'), and symlinks/junctions
    - filesystem roots, user home root, and paths inside `backups_root`
    - sensitive OS/credential directories (.ssh, .aws, .gnupg, .kube, /etc, C:\\Windows)
    - any known skills root directory itself (or an ancestor directory of a skills root),
      preventing overwriting an entire skills directory as if it were a single skill

    When `allowed_roots` is provided and `require_within_allowed_roots=True`, requires
    `target` to be a direct single-level child directory under an authorized skills root.
    When `require_within_allowed_roots=False` (explicit `--target`), still blocks all
    protected/system/skills-root paths and refuses overwriting an existing non-empty
    directory that is not a skill directory (contains no `SKILL.md`).
    """
    if "\x00" in str(target):
        raise PathTraversalError("Restore target contains null byte.")

    if has_ntfs_ads_component(target):
        raise PathTraversalError(f"Restore target contains an NTFS Alternate Data Stream: {target}")

    if target.exists() and is_symlink_or_junction(target):
        raise UnsafeSymlinkError(f"Restore destination is a symlink or junction: {target}")

    resolved = target.resolve()

    # Never allow filesystem root (e.g. '/' or 'C:\')
    if resolved == resolved.parent:
        raise PathTraversalError(f"Refusing to restore into filesystem root: {resolved}")

    # Never allow restoring directly into user home directory
    if home_dir is not None and resolved == home_dir.resolve():
        raise PathTraversalError(f"Refusing to restore directly into home directory: {resolved}")

    # Never allow restoring inside the backups store itself
    resolved_backups = backups_root.resolve()
    try:
        resolved.relative_to(resolved_backups)
        raise PathTraversalError(
            f"Refusing to restore into ContextBridge backups directory: {resolved}"
        )
    except ValueError:
        pass

    # Block sensitive system and credential directory segments
    lower_parts = [p.lower() for p in resolved.parts]
    for forbidden in FORBIDDEN_RESTORE_DIR_NAMES:
        if forbidden in lower_parts:
            raise SecurityViolationError(
                f"Refusing to restore into sensitive system/credential path: {resolved}"
            )

    posix_str = resolved.as_posix().lower()
    for prefix in FORBIDDEN_SYSTEM_PREFIXES:
        if posix_str == prefix or posix_str.startswith(prefix + "/"):
            raise SecurityViolationError(
                f"Refusing to restore into protected OS directory: {resolved}"
            )

    if os.name == "nt":
        win_dir = os.environ.get("SYSTEMROOT", "C:\\Windows").lower().replace("\\", "/")
        if posix_str == win_dir or posix_str.startswith(win_dir + "/"):
            raise SecurityViolationError(
                f"Refusing to restore into protected Windows directory: {resolved}"
            )

    # Never allow overwriting a skills root directory itself or any ancestor of a skills root
    roots_to_protect: list[Path] = []
    if allowed_roots is not None:
        roots_to_protect.extend(allowed_roots)
    if protected_roots is not None:
        roots_to_protect.extend(protected_roots)

    for root in roots_to_protect:
        resolved_root = root.resolve()
        if resolved == resolved_root:
            raise PathTraversalError(
                f"Refusing to overwrite skills root directory as a single skill: {resolved}"
            )
        try:
            resolved_root.relative_to(resolved)
            raise PathTraversalError(
                f"Refusing to overwrite parent/ancestor directory of skills root: {resolved}"
            )
        except ValueError:
            pass

    if allowed_roots is not None and require_within_allowed_roots:
        authorized = False
        for root in allowed_roots:
            resolved_root = root.resolve()
            try:
                rel = resolved.relative_to(resolved_root)
            except ValueError:
                continue
            # Must be a direct single-level skill child inside an authorized skills root
            if len(rel.parts) == 1:
                authorized = True
                break
        if not authorized:
            raise PathTraversalError(
                f"Restore destination {resolved} is outside authorized agent skills directories. "
                "Refusing restore from manifest; pass --target explicitly if you intend to "
                "restore into a custom directory."
            )
    elif not require_within_allowed_roots and resolved.exists() and resolved.is_dir():
        # Explicit --target must not clobber an arbitrary non-empty non-skill directory
        entries = list(resolved.iterdir())
        if entries and not (resolved / "SKILL.md").is_file():
            raise SecurityViolationError(
                f"Refusing --target restore into existing non-empty non-skill directory: "
                f"{resolved}. Point --target to a new directory or an existing skill directory."
            )

    return resolved


def collect_safe_skill_files(
    skill_dir: Path,
    *,
    limits: SecurityLimits = DEFAULT_LIMITS,
    allow_symlinks: bool = False,
) -> list[tuple[PurePosixPath, Path, int]]:
    """Walk a skill directory safely, enforcing boundary, symlink, depth, size, and collisions.

    Returns a deterministically sorted list of `(relative_posix_path, abs_path, size_bytes)`.
    """
    if not skill_dir.exists() or not skill_dir.is_dir():
        raise PathTraversalError(f"Skill directory does not exist: {skill_dir}")

    if not allow_symlinks and is_symlink_or_junction(skill_dir):
        raise UnsafeSymlinkError(f"Skill root cannot be a symlink or junction: {skill_dir}")

    resolved_root = skill_dir.resolve()
    collected: list[tuple[PurePosixPath, Path, int]] = []
    seen_casefolded: dict[str, str] = {}
    total_bytes = 0

    def _walk(current_dir: Path, depth: int) -> None:
        nonlocal total_bytes
        if depth > limits.max_directory_depth:
            raise FileSizeLimitError(
                f"Directory nesting depth exceeds maximum of {limits.max_directory_depth}: "
                f"{current_dir}"
            )
        entries = sorted(os.scandir(current_dir), key=lambda e: e.name)
        for entry in entries:
            entry_path = Path(entry.path)
            if is_symlink_or_junction(entry_path):
                if not allow_symlinks:
                    raise UnsafeSymlinkError(
                        f"Symlink or junction detected and blocked: {entry_path}"
                    )
                continue

            if entry.is_dir(follow_symlinks=False):
                _walk(entry_path, depth + 1)
            elif entry.is_file(follow_symlinks=False):
                resolved_file = ensure_within_root(resolved_root, entry_path)
                rel_str = resolved_file.relative_to(resolved_root).as_posix()
                rel_posix = validate_relative_path(rel_str)

                folded = rel_posix.as_posix().lower()
                if folded in seen_casefolded:
                    raise PathTraversalError(
                        "Case-insensitive filename collision detected between "
                        f"{seen_casefolded[folded]!r} and {rel_posix.as_posix()!r}."
                    )
                seen_casefolded[folded] = rel_posix.as_posix()

                size = entry.stat(follow_symlinks=False).st_size

                if size > limits.max_single_file_bytes:
                    raise FileSizeLimitError(
                        f"File {rel_posix} ({size} bytes) exceeds maximum allowed single file size "
                        f"({limits.max_single_file_bytes} bytes)."
                    )

                total_bytes += size
                if total_bytes > limits.max_total_skill_bytes:
                    raise FileSizeLimitError(
                        "Skill total size exceeds maximum allowed "
                        f"({limits.max_total_skill_bytes} bytes)."
                    )

                if len(collected) + 1 > limits.max_files_per_skill:
                    raise FileSizeLimitError(
                        f"Skill file count exceeds maximum allowed ({limits.max_files_per_skill})."
                    )

                collected.append((rel_posix, resolved_file, size))

    _walk(resolved_root, 0)
    collected.sort(key=lambda item: item[0].as_posix())
    return collected
