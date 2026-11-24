"""Safe SKILL.md frontmatter and Markdown reference parser (read-only, strict SafeLoader)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from contextbridge.models.skill import IssueSeverity, SkillFrontmatter, SkillValidationIssue
from contextbridge.security.limits import DEFAULT_LIMITS, SecurityLimits

FRONTMATTER_OPEN = "---"
FRONTMATTER_CLOSE_SET = frozenset({"---", "..."})
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
FENCED_CODE_RE = re.compile(r"^```")
EXTERNAL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
YAML_ALIAS_RE = re.compile(r"(?:^|\s)[&*][A-Za-z0-9_-]+")
MAX_YAML_DEPTH = 10


class _DuplicateKeyError(yaml.YAMLError):
    """Raised when a YAML mapping contains duplicate keys."""


class _YamlDepthError(yaml.YAMLError):
    """Raised when a YAML document exceeds maximum safe nesting depth."""


class _StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader subclass that rejects duplicate keys and enforces nesting depth limits."""


def _construct_mapping_no_duplicates(
    loader: _StrictSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise _DuplicateKeyError(f"Duplicate YAML key detected: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_no_duplicates,
)


def _check_yaml_depth(node: yaml.Node | None, current_depth: int = 0) -> None:
    """Iteratively check YAML AST nesting depth without risking Python recursion overflow."""
    if node is None:
        return
    stack: list[tuple[yaml.Node, int]] = [(node, current_depth)]
    while stack:
        curr, depth = stack.pop()
        if depth > MAX_YAML_DEPTH:
            raise _YamlDepthError(f"YAML nesting depth exceeds maximum allowed ({MAX_YAML_DEPTH}).")
        if isinstance(curr, yaml.MappingNode):
            for k_node, v_node in curr.value:
                stack.append((k_node, depth + 1))
                stack.append((v_node, depth + 1))
        elif isinstance(curr, yaml.SequenceNode):
            for item_node in curr.value:
                stack.append((item_node, depth + 1))


def _strict_safe_load(yaml_text: str) -> Any:
    """Parse YAML using SafeLoader while rejecting excessive depth and duplicate keys."""
    loader = _StrictSafeLoader(yaml_text)
    try:
        root_node = loader.get_single_node()
        _check_yaml_depth(root_node)
        if root_node is None:
            return None
        return loader.construct_document(root_node)
    finally:
        loader.dispose()


@dataclass(frozen=True)
class LocalReference:
    """A relative file reference extracted from SKILL.md Markdown body."""

    raw_target: str
    line: int


@dataclass
class ParsedSkillDocument:
    """Result of parsing a SKILL.md file safely."""

    raw_frontmatter: dict[str, Any] | None = None
    frontmatter: SkillFrontmatter | None = None
    body: str = ""
    local_references: list[LocalReference] = field(default_factory=list)
    issues: list[SkillValidationIssue] = field(default_factory=list)


def extract_local_references(body: str, body_start_line: int = 1) -> list[LocalReference]:
    """Extract local Markdown links from SKILL.md body, skipping fenced code blocks."""
    refs: list[LocalReference] = []
    in_code_block = False

    for offset, line in enumerate(body.splitlines()):
        stripped = line.lstrip()
        if FENCED_CODE_RE.match(stripped):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        for match in MARKDOWN_LINK_RE.finditer(line):
            target = match.group(1).strip().strip("<>")
            if not target or target.startswith("#"):
                continue
            if EXTERNAL_SCHEME_RE.match(target) and not re.match(r"^[A-Za-z]:[/\\]", target):
                continue
            clean_target = target.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0]
            if not clean_target:
                continue
            refs.append(LocalReference(raw_target=clean_target, line=body_start_line + offset))

    return refs


def parse_skill_markdown(
    skill_md_path: Path,
    *,
    limits: SecurityLimits = DEFAULT_LIMITS,
) -> ParsedSkillDocument:
    """Parse SKILL.md frontmatter and body safely using strict SafeLoader only."""
    result = ParsedSkillDocument()

    try:
        size = skill_md_path.stat().st_size
    except OSError as exc:
        result.issues.append(
            SkillValidationIssue(
                code="SKILL_MD_UNREADABLE",
                severity=IssueSeverity.ERROR,
                message=f"Could not stat SKILL.md: {exc}",
                path="SKILL.md",
            )
        )
        return result

    if size > limits.max_skill_md_bytes:
        result.issues.append(
            SkillValidationIssue(
                code="SKILL_MD_TOO_LARGE",
                severity=IssueSeverity.ERROR,
                message=(
                    f"SKILL.md size ({size} bytes) exceeds maximum allowed "
                    f"({limits.max_skill_md_bytes} bytes)."
                ),
                path="SKILL.md",
            )
        )
        return result

    try:
        raw_text = skill_md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        result.issues.append(
            SkillValidationIssue(
                code="SKILL_MD_ENCODING",
                severity=IssueSeverity.ERROR,
                message=f"SKILL.md must be valid UTF-8 text: {exc}",
                path="SKILL.md",
            )
        )
        return result
    except OSError as exc:
        result.issues.append(
            SkillValidationIssue(
                code="SKILL_MD_UNREADABLE",
                severity=IssueSeverity.ERROR,
                message=f"Could not read SKILL.md: {exc}",
                path="SKILL.md",
            )
        )
        return result

    if raw_text.startswith("\ufeff"):
        raw_text = raw_text[1:]

    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_OPEN:
        result.issues.append(
            SkillValidationIssue(
                code="FRONTMATTER_MISSING",
                severity=IssueSeverity.ERROR,
                message="SKILL.md must begin with YAML frontmatter delimited by '---' on line 1.",
                path="SKILL.md",
                line=1,
            )
        )
        result.body = raw_text
        result.local_references = extract_local_references(raw_text, body_start_line=1)
        return result

    closing_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in FRONTMATTER_CLOSE_SET:
            closing_idx = idx
            break

    if closing_idx is None:
        result.issues.append(
            SkillValidationIssue(
                code="FRONTMATTER_UNCLOSED",
                severity=IssueSeverity.ERROR,
                message="YAML frontmatter opening '---' has no closing '---' delimiter.",
                path="SKILL.md",
                line=1,
            )
        )
        return result

    yaml_block = "\n".join(lines[1:closing_idx])
    body_lines = lines[closing_idx + 1 :]
    result.body = "\n".join(body_lines)
    result.local_references = extract_local_references(result.body, body_start_line=closing_idx + 2)

    alias_count = len(YAML_ALIAS_RE.findall(yaml_block))
    if alias_count > 20:
        result.issues.append(
            SkillValidationIssue(
                code="YAML_ALIAS_LIMIT",
                severity=IssueSeverity.ERROR,
                message=(
                    f"YAML frontmatter contains {alias_count} anchors/aliases, "
                    "which is blocked to prevent alias-expansion attacks."
                ),
                path="SKILL.md",
                line=1,
            )
        )
        return result

    try:
        loaded = _strict_safe_load(yaml_block)
    except _DuplicateKeyError as exc:
        result.issues.append(
            SkillValidationIssue(
                code="YAML_DUPLICATE_KEY",
                severity=IssueSeverity.ERROR,
                message=str(exc),
                path="SKILL.md",
                line=1,
            )
        )
        return result
    except (_YamlDepthError, RecursionError) as exc:
        result.issues.append(
            SkillValidationIssue(
                code="YAML_TOO_DEEP",
                severity=IssueSeverity.ERROR,
                message=f"YAML frontmatter exceeds safe nesting depth: {exc}",
                path="SKILL.md",
                line=1,
            )
        )
        return result
    except yaml.YAMLError as exc:
        result.issues.append(
            SkillValidationIssue(
                code="YAML_INVALID",
                severity=IssueSeverity.ERROR,
                message=f"Invalid YAML frontmatter: {exc}",
                path="SKILL.md",
                line=1,
            )
        )
        return result

    if loaded is None:
        result.issues.append(
            SkillValidationIssue(
                code="FRONTMATTER_EMPTY",
                severity=IssueSeverity.ERROR,
                message=(
                    "YAML frontmatter is empty; required fields 'name' and 'description' "
                    "are missing."
                ),
                path="SKILL.md",
                line=1,
            )
        )
        return result

    if not isinstance(loaded, dict):
        result.issues.append(
            SkillValidationIssue(
                code="FRONTMATTER_NOT_MAPPING",
                severity=IssueSeverity.ERROR,
                message="YAML frontmatter must be a key-value mapping.",
                path="SKILL.md",
                line=1,
            )
        )
        return result

    result.raw_frontmatter = loaded
    try:
        result.frontmatter = SkillFrontmatter.model_validate(loaded)
    except Exception as exc:
        result.issues.append(
            SkillValidationIssue(
                code="FRONTMATTER_SCHEMA",
                severity=IssueSeverity.ERROR,
                message=f"Frontmatter field types do not match Agent Skills schema: {exc}",
                path="SKILL.md",
                line=1,
            )
        )

    return result
