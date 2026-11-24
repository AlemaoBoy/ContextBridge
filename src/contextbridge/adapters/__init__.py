"""AI coding agent adapters."""

from contextbridge.adapters.antigravity import AntigravityAdapter
from contextbridge.adapters.base import BaseAgentAdapter
from contextbridge.adapters.claude import ClaudeCodeAdapter
from contextbridge.adapters.codex import CodexAdapter
from contextbridge.adapters.cursor import CursorAdapter
from contextbridge.adapters.gemini_cli import GeminiCliAdapter
from contextbridge.adapters.opencode import OpenCodeAdapter
from contextbridge.adapters.registry import get_adapter, list_adapters

__all__ = [
    "AntigravityAdapter",
    "BaseAgentAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "GeminiCliAdapter",
    "OpenCodeAdapter",
    "get_adapter",
    "list_adapters",
]
