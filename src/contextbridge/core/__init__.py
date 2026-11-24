"""Core primitives for ContextBridge.

Keep this package initializer free of cross-package eager imports so importing
`contextbridge.core.exceptions` or `contextbridge.core.paths` cannot create cycles.
"""

from contextbridge.core.exceptions import ContextBridgeError
from contextbridge.core.paths import EnvironmentContext, default_environment

__all__ = [
    "ContextBridgeError",
    "EnvironmentContext",
    "default_environment",
]
