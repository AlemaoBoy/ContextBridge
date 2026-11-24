"""Injectable cross-platform environment context and path helpers."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path


def _default_which(command: str) -> str | None:
    return shutil.which(command)


@dataclass(frozen=True)
class EnvironmentContext:
    """Immutable view of host OS paths and environment variables.

    Injecting this context enables deterministic, isolated tests that never read
    or modify real user agent configurations.
    """

    home_dir: Path = field(default_factory=Path.home)
    project_dir: Path = field(default_factory=Path.cwd)
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    platform: str = field(default_factory=lambda: sys.platform)
    which: Callable[[str], str | None] = field(default=_default_which)
    state_dir_override: Path | None = None

    @property
    def is_windows(self) -> bool:
        return self.platform.startswith("win")

    @property
    def is_macos(self) -> bool:
        return self.platform == "darwin"

    @property
    def is_linux(self) -> bool:
        return self.platform.startswith("linux")

    @property
    def xdg_config_home(self) -> Path:
        raw = self.env.get("XDG_CONFIG_HOME", "").strip()
        if raw:
            return Path(raw).expanduser()
        return self.home_dir / ".config"

    @property
    def contextbridge_home(self) -> Path:
        """Local state directory for ContextBridge (backups, metadata). Never created on reads."""
        if self.state_dir_override is not None:
            return self.state_dir_override
        env_override = self.env.get("CONTEXTBRIDGE_HOME", "").strip()
        if env_override:
            return Path(env_override).expanduser()
        return self.home_dir / ".contextbridge"

    @property
    def backups_dir(self) -> Path:
        return self.contextbridge_home / "backups"

    def resolve_env_path(self, var_name: str) -> Path | None:
        raw = self.env.get(var_name, "").strip()
        if not raw:
            return None
        return Path(raw).expanduser()


def default_environment(project_dir: Path | None = None) -> EnvironmentContext:
    """Create an EnvironmentContext bound to the live local system."""
    return EnvironmentContext(
        home_dir=Path.home(),
        project_dir=(project_dir or Path.cwd()).resolve(),
        env=dict(os.environ),
        platform=sys.platform,
        which=_default_which,
    )
