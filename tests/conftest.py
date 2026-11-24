"""Shared pytest fixtures creating isolated, temporary environments."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from contextbridge.core.paths import EnvironmentContext


def make_skill(
    parent_dir: Path,
    name: str,
    *,
    description: str = "Automated synthetic skill used strictly for isolated testing.",
    body: str = "# Instructions\n\nFollow the documented workflow carefully.\n",
    extra_frontmatter: str = "",
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Create a synthetic skill directory inside `parent_dir`."""
    skill_dir = parent_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    fm_lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
    ]
    if extra_frontmatter.strip():
        fm_lines.append(extra_frontmatter.strip())
    fm_lines.append("---")
    content = "\n".join(fm_lines) + "\n\n" + body
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    if extra_files:
        for rel, file_content in extra_files.items():
            target = skill_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file_content, encoding="utf-8")

    return skill_dir


@pytest.fixture()
def isolated_env(tmp_path: Path) -> EnvironmentContext:
    """Provide a fully isolated EnvironmentContext backed by tmp_path."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    state = tmp_path / "cb_state"
    home.mkdir()
    project.mkdir()
    return EnvironmentContext(
        home_dir=home,
        project_dir=project,
        env={},
        platform="linux",
        which=lambda _cmd: None,
        state_dir_override=state,
    )


@pytest.fixture()
def make_env(tmp_path: Path) -> Callable[..., EnvironmentContext]:
    """Factory fixture for customized isolated EnvironmentContext instances."""

    def _factory(
        *,
        executables: dict[str, str] | None = None,
        env_vars: dict[str, str] | None = None,
        platform_name: str = "linux",
    ) -> EnvironmentContext:
        home = tmp_path / "home"
        project = tmp_path / "project"
        state = tmp_path / "cb_state"
        home.mkdir(exist_ok=True)
        project.mkdir(exist_ok=True)
        bins = executables or {}
        return EnvironmentContext(
            home_dir=home,
            project_dir=project,
            env=dict(env_vars or {}),
            platform=platform_name,
            which=lambda cmd: bins.get(cmd),
            state_dir_override=state,
        )

    return _factory


@pytest.fixture()
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "skills"
