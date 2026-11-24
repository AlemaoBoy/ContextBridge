"""Centralized security limits for file parsing, export, migration, and archives."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityLimits:
    """Conservative resource limits to mitigate DoS, YAML bombs, and ZIP bombs."""

    max_skill_md_bytes: int = 1 * 1024 * 1024  # 1 MiB
    max_single_file_bytes: int = 10 * 1024 * 1024  # 10 MiB
    max_total_skill_bytes: int = 50 * 1024 * 1024  # 50 MiB
    max_files_per_skill: int = 500
    max_zip_uncompressed_bytes: int = 50 * 1024 * 1024  # 50 MiB
    max_zip_entries: int = 500
    max_zip_compression_ratio: float = 100.0
    max_directory_depth: int = 12


DEFAULT_LIMITS = SecurityLimits()
