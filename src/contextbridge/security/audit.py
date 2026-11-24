"""Static heuristic security auditor for Agent Skills.

IMPORTANT: Static heuristics are best-effort warnings and NEVER a complete security guarantee.
ContextBridge never executes any scripts or commands discovered during auditing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from contextbridge.models.skill import IssueSeverity, SkillValidationIssue
from contextbridge.security.secrets import scan_file_for_secrets


@dataclass(frozen=True)
class HeuristicCommandRule:
    """Static pattern flagging potentially risky shell/script constructs."""

    code: str
    severity: IssueSeverity
    message: str
    pattern: re.Pattern[str]


HEURISTIC_RULES: tuple[HeuristicCommandRule, ...] = (
    HeuristicCommandRule(
        code="HEUR_PIPE_TO_SHELL",
        severity=IssueSeverity.WARNING,
        message=(
            "Heuristic warning: downloads and pipes remote content directly into a shell "
            "(e.g., curl/wget | sh/bash). Review manually before trusting."
        ),
        pattern=re.compile(r"(?i)\b(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash|zsh|pwsh|powershell)\b"),
    ),
    HeuristicCommandRule(
        code="HEUR_POWERSHELL_ENCODED",
        severity=IssueSeverity.WARNING,
        message=(
            "Heuristic warning: obfuscated/encoded PowerShell invocation detected "
            "(-EncodedCommand / iex)."
        ),
        pattern=re.compile(
            r"(?i)\b(?:powershell|pwsh)\b[^\n]*(?:-enc(?:odedcommand)?\b|\biex\b|invoke-expression)"
        ),
    ),
    HeuristicCommandRule(
        code="HEUR_DESTRUCTIVE_RM",
        severity=IssueSeverity.WARNING,
        message="Heuristic warning: potentially destructive recursive delete command detected.",
        pattern=re.compile(r"(?i)\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+(?:/|~|\$HOME)\b"),
    ),
    HeuristicCommandRule(
        code="HEUR_ENV_EXFIL",
        severity=IssueSeverity.WARNING,
        message=(
            "Heuristic warning: references sensitive local credential paths "
            "(~/.ssh, ~/.aws, ~/.gnupg)."
        ),
        pattern=re.compile(r"(?:~/|\$HOME/|%USERPROFILE%\\)\.(?:ssh|aws|gnupg|kube)\b"),
    ),
    HeuristicCommandRule(
        code="HEUR_BASE64_EXEC",
        severity=IssueSeverity.WARNING,
        message="Heuristic warning: base64 decoding piped into shell execution detected.",
        pattern=re.compile(r"(?i)\bbase64\s+(?:-d|--decode)[^\n|]*\|\s*(?:sh|bash|python|eval)\b"),
    ),
)

AUDITABLE_EXTENSIONS = frozenset(
    {
        ".md",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
        ".cmd",
        ".py",
        ".js",
        ".ts",
        ".rb",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".txt",
    }
)


def audit_skill_file(file_path: Path, relative_path: str) -> list[SkillValidationIssue]:
    """Run static heuristic security checks (secrets + risky command patterns) on one file."""
    issues = scan_file_for_secrets(file_path, relative_path)

    suffix = file_path.suffix.lower()
    if suffix not in AUDITABLE_EXTENSIONS and file_path.name != "SKILL.md":
        return issues

    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return issues

    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule in HEURISTIC_RULES:
            if rule.pattern.search(line):
                issues.append(
                    SkillValidationIssue(
                        code=rule.code,
                        severity=rule.severity,
                        message=rule.message,
                        path=relative_path,
                        line=line_no,
                        is_heuristic=True,
                    )
                )

    return issues
