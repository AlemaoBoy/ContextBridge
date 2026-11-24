"""Registry for all built-in AI coding agent adapters."""

from __future__ import annotations

from contextbridge.adapters.antigravity import AntigravityAdapter
from contextbridge.adapters.base import BaseAgentAdapter
from contextbridge.adapters.claude import ClaudeCodeAdapter
from contextbridge.adapters.codex import CodexAdapter
from contextbridge.adapters.cursor import CursorAdapter
from contextbridge.adapters.gemini_cli import GeminiCliAdapter
from contextbridge.adapters.opencode import OpenCodeAdapter
from contextbridge.core.exceptions import UnknownAgentError
from contextbridge.models.agent import AgentId

_ADAPTERS: tuple[BaseAgentAdapter, ...] = (
    ClaudeCodeAdapter(),
    CodexAdapter(),
    AntigravityAdapter(),
    OpenCodeAdapter(),
    GeminiCliAdapter(),
    CursorAdapter(),
)

_BY_ID: dict[AgentId, BaseAgentAdapter] = {adapter.agent_id: adapter for adapter in _ADAPTERS}

_BY_ALIAS: dict[str, BaseAgentAdapter] = {}
for _adapter in _ADAPTERS:
    _BY_ALIAS[_adapter.agent_id.value.lower()] = _adapter
    for _alias in _adapter.aliases:
        _BY_ALIAS[_alias.lower()] = _adapter


def list_adapters() -> list[BaseAgentAdapter]:
    """Return all registered agent adapters in deterministic display order."""
    return list(_ADAPTERS)


def get_adapter(identifier: str | AgentId) -> BaseAgentAdapter:
    """Resolve an adapter by canonical AgentId or alias."""
    if isinstance(identifier, AgentId):
        return _BY_ID[identifier]
    key = identifier.strip().lower()
    adapter = _BY_ALIAS.get(key)
    if adapter is None:
        supported = ", ".join(a.agent_id.value for a in _ADAPTERS)
        raise UnknownAgentError(f"Unknown agent {identifier!r}. Supported agents: {supported}.")
    return adapter
