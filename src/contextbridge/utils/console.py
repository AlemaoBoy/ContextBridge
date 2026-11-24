"""Rich console presentation helpers for ContextBridge CLI."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contextbridge.models.agent import DetectionStatus
from contextbridge.models.doctor import CheckStatus
from contextbridge.models.skill import IssueSeverity, ValidationStatus

console = Console()
err_console = Console(stderr=True)


def status_badge(status: DetectionStatus) -> Text:
    mapping = {
        DetectionStatus.INSTALLED_AND_CONFIGURED: ("installed + configured", "bold green"),
        DetectionStatus.INSTALLED_NO_CONFIG: ("installed (no config)", "yellow"),
        DetectionStatus.CONFIG_ONLY: ("config found (CLI not on PATH)", "cyan"),
        DetectionStatus.NOT_FOUND: ("not found", "dim"),
    }
    label, style = mapping[status]
    return Text(label, style=style)


def validation_badge(status: ValidationStatus) -> Text:
    mapping = {
        ValidationStatus.VALID: ("VALID", "bold green"),
        ValidationStatus.VALID_WITH_WARNINGS: ("VALID (warnings)", "bold yellow"),
        ValidationStatus.INVALID: ("INVALID", "bold red"),
    }
    label, style = mapping[status]
    return Text(label, style=style)


def severity_badge(severity: IssueSeverity) -> Text:
    mapping = {
        IssueSeverity.ERROR: ("ERROR", "bold red"),
        IssueSeverity.WARNING: ("WARN", "bold yellow"),
        IssueSeverity.INFO: ("INFO", "cyan"),
    }
    label, style = mapping[severity]
    return Text(label, style=style)


def doctor_badge(status: CheckStatus) -> Text:
    mapping = {
        CheckStatus.OK: ("OK", "bold green"),
        CheckStatus.WARN: ("WARN", "bold yellow"),
        CheckStatus.ERROR: ("ERROR", "bold red"),
        CheckStatus.INFO: ("INFO", "cyan"),
    }
    label, style = mapping[status]
    return Text(label, style=style)


def print_json(data: Any) -> None:
    """Print deterministic, UTF-8 formatted JSON directly to stdout."""
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))


def make_table(title: str, columns: list[str]) -> Table:
    table = Table(title=title, show_lines=False, header_style="bold")
    for col in columns:
        table.add_column(col)
    return table


def print_error_panel(title: str, message: str) -> None:
    err_console.print(Panel(message, title=title, border_style="red"))
