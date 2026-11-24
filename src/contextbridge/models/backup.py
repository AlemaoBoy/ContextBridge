"""Pydantic models for local backups, manifests, and restoration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


class BackupFileEntry(BaseModel):
    """Single file recorded in a backup manifest with its SHA-256 digest."""

    relative_path: str
    size_bytes: int
    sha256: str


class BackupManifest(BaseModel):
    """Manifest persisted alongside every local ContextBridge backup."""

    schema_version: str = "1.0"
    backup_id: str
    created_at: datetime
    operation: str
    source_path: Path
    skill_name: str | None = None
    agent_id: str | None = None
    total_files: int
    total_bytes: int
    files: list[BackupFileEntry] = Field(default_factory=list)
    notes: str | None = None


class BackupVerificationResult(BaseModel):
    """Result of checking SHA-256 integrity of a stored backup."""

    backup_id: str
    valid: bool
    checked_files: int = 0
    errors: list[str] = Field(default_factory=list)


class RestorePlan(BaseModel):
    """Preview of restoring a backup to its original or target path."""

    backup_id: str
    manifest: BackupManifest
    restore_target: Path
    target_exists: bool
    integrity: BackupVerificationResult
    files_to_restore: int
    total_bytes: int
    warnings: list[str] = Field(default_factory=list)


class RestoreResult(BaseModel):
    """Outcome of a backup restoration."""

    plan: RestorePlan
    applied: bool
    dry_run: bool
    pre_restore_backup_id: str | None = None
    files_restored: int = 0
    verified: bool = False
    rolled_back: bool = False
    message: str = ""
