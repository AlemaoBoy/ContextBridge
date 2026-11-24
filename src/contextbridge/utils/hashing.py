"""SHA-256 hashing utilities for file and directory integrity verification."""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK_SIZE = 65_536


def sha256_file(path: Path) -> str:
    """Compute the lowercase hex SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute the lowercase hex SHA-256 digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()
