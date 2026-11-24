"""Pydantic models for `contextbridge doctor` diagnostics."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class CheckStatus(StrEnum):
    """Diagnostic check result status."""

    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    INFO = "info"


class DiagnosticCheck(BaseModel):
    """Single read-only diagnostic check finding."""

    category: str
    name: str
    status: CheckStatus
    message: str
    path: Path | None = None
    remediation: str | None = None


class DiagnosticReport(BaseModel):
    """Aggregated output from `contextbridge doctor`."""

    platform: str
    python_version: str
    project_dir: Path
    checks: list[DiagnosticCheck] = Field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.OK)

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.WARN)

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.ERROR)

    @property
    def info_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.INFO)
