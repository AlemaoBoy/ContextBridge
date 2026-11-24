"""Cross-platform filesystem utilities with junction/symlink detection and atomic writes."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path

FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def is_symlink_or_junction(path: Path) -> bool:
    """Return True if path is a symbolic link or Windows reparse point / junction."""
    try:
        if path.is_symlink():
            return True
        if os.name == "nt":
            st = os.lstat(path)
            attrs = getattr(st, "st_file_attributes", 0)
            if bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT):
                return True
            if hasattr(os.path, "isjunction") and os.path.isjunction(path):
                return True
    except OSError:
        return False
    return False


def is_readable_dir(path: Path) -> bool:
    """Check whether a directory exists and can be listed without modifying it."""
    if not path.exists() or not path.is_dir():
        return False
    try:
        with os.scandir(path) as entries:
            next(entries, None)
        return True
    except OSError:
        return False


def is_writable_dir(path: Path) -> bool:
    """Non-mutating permission check using os.access (best-effort)."""
    if not path.exists() or not path.is_dir():
        return False
    return os.access(path, os.W_OK)


def canonical_path_key(path: Path) -> str:
    """Return a normalized real-path key suitable for cross-platform deduplication."""
    real = os.path.realpath(path)
    return os.path.normcase(os.path.normpath(real))


def atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write bytes atomically using a temporary file in the same directory."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".cb_tmp_", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(target: Path, text: str, encoding: str = "utf-8") -> None:
    """Atomically write UTF-8 text to a file."""
    atomic_write_bytes(target, text.encode(encoding))


def get_file_mode_is_executable(path: Path) -> bool:
    """Check if a file has executable permission bits set on POSIX."""
    try:
        mode = os.lstat(path).st_mode
        return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    except OSError:
        return False
