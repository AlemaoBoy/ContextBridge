"""Heuristic credential and secret detection without exposing secret values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from contextbridge.models.skill import IssueSeverity, SkillValidationIssue

SENSITIVE_FILENAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        ".env.secret",
        ".envrc",
        ".git-credentials",
        ".pgpass",
        ".my.cnf",
        ".boto",
        ".vault-token",
        ".secret",
        ".secrets",
        ".credentials",
        ".token",
        "credentials",
        "credentials.json",
        "auth.json",
        "token.json",
        "oauth_creds.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "service-account.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".htpasswd",
    }
)

SAFE_TEMPLATE_FILENAMES = frozenset(
    {
        ".env.example",
        ".env.sample",
        ".env.template",
        ".env.dist",
    }
)

SENSITIVE_HIDDEN_DIRS = frozenset(
    {
        ".ssh",
        ".aws",
        ".gnupg",
        ".kube",
        ".docker",
    }
)

SENSITIVE_HIDDEN_KEYWORDS = ("secret", "token", "cred", "passwd", "apikey", "api_key", "auth")

SENSITIVE_EXTENSIONS = frozenset(
    {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".jks",
        ".kdbx",
        ".keystore",
    }
)


@dataclass(frozen=True)
class SecretPattern:
    """Single compiled regex heuristic rule for secret detection."""

    rule_id: str
    description: str
    pattern: re.Pattern[str]


SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern(
        rule_id="SEC_PRIVATE_KEY",
        description="Private key PEM header detected",
        pattern=re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ),
    SecretPattern(
        rule_id="SEC_AWS_ACCESS_KEY",
        description="Possible AWS access key ID detected",
        pattern=re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    SecretPattern(
        rule_id="SEC_GITHUB_TOKEN",
        description="Possible GitHub personal/OAuth access token detected",
        pattern=re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    SecretPattern(
        rule_id="SEC_ANTHROPIC_KEY",
        description="Possible Anthropic API key detected",
        pattern=re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"),
    ),
    SecretPattern(
        rule_id="SEC_OPENAI_KEY",
        description="Possible OpenAI API key detected",
        pattern=re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9\-_]{24,}\b"),
    ),
    SecretPattern(
        rule_id="SEC_GOOGLE_API_KEY",
        description="Possible Google API key detected",
        pattern=re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    ),
    SecretPattern(
        rule_id="SEC_SLACK_TOKEN",
        description="Possible Slack token detected",
        pattern=re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"),
    ),
    SecretPattern(
        rule_id="SEC_GENERIC_ASSIGNMENT",
        description="Possible hardcoded secret/token/password assignment detected",
        pattern=re.compile(
            r"""(?i)\b(?:api_key|apikey|secret_key|access_token|auth_token|private_key|password)\s*[:=]\s*['"](?!<|\$\{|YOUR_|EXAMPLE|CHANGEME|PLACEHOLDER|DUMMY|TEST_)[A-Za-z0-9/+=._\-]{16,}['"]"""
        ),
    ),
)

TEXT_SAMPLE_BYTES = 8192


def is_sensitive_filename(rel_path: PurePosixPath | Path | str) -> bool:
    """Return True if a path matches known credential/auth/key file or hidden directory patterns."""
    normalized = str(rel_path).replace("\\", "/")
    parts = [p.lower() for p in PurePosixPath(normalized).parts if p not in ("", ".")]
    if not parts:
        return False

    # Check if file resides inside a sensitive hidden directory (e.g. .ssh/, .aws/)
    if any(part in SENSITIVE_HIDDEN_DIRS for part in parts[:-1]):
        return True

    name = parts[-1]
    if name in SAFE_TEMPLATE_FILENAMES:
        return False
    if name in SENSITIVE_FILENAMES:
        return True
    if name.startswith(".env."):
        return True
    if name.startswith(".") and any(kw in name for kw in SENSITIVE_HIDDEN_KEYWORDS):
        return True
    suffix = Path(name).suffix.lower()
    return suffix in SENSITIVE_EXTENSIONS


def _looks_like_text(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            sample = handle.read(TEXT_SAMPLE_BYTES)
        return b"\x00" not in sample
    except OSError:
        return False


def scan_file_for_secrets(
    file_path: Path,
    relative_path: str,
) -> list[SkillValidationIssue]:
    """Scan a single file for potential secrets. Never includes matched secret text in output."""
    findings: list[SkillValidationIssue] = []

    if is_sensitive_filename(relative_path):
        findings.append(
            SkillValidationIssue(
                code="SEC_SENSITIVE_FILE",
                severity=IssueSeverity.ERROR,
                message=(
                    f"Sensitive credential/key file detected ({relative_path}). "
                    "ContextBridge blocks copying or exporting credential files."
                ),
                path=relative_path,
                is_heuristic=True,
            )
        )

    if not file_path.is_file() or not _looks_like_text(file_path):
        return findings

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings

    for line_no, line in enumerate(content.splitlines(), start=1):
        for rule in SECRET_PATTERNS:
            if rule.pattern.search(line):
                findings.append(
                    SkillValidationIssue(
                        code=rule.rule_id,
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"{rule.description} (value redacted for safety; heuristic check)."
                        ),
                        path=relative_path,
                        line=line_no,
                        is_heuristic=True,
                    )
                )
    return findings
