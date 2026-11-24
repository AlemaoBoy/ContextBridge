"""Unit tests for all six agent adapters, env overrides, and read-only detection."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.conftest import make_skill

from contextbridge.adapters.registry import get_adapter, list_adapters
from contextbridge.core.exceptions import UnknownAgentError
from contextbridge.core.paths import EnvironmentContext
from contextbridge.models.agent import AgentId, AgentScope, DetectionStatus


@pytest.mark.unit
def test_registry_contains_six_supported_agents() -> None:
    adapters = list_adapters()
    ids = [a.agent_id for a in adapters]
    assert ids == [
        AgentId.CLAUDE,
        AgentId.CODEX,
        AgentId.ANTIGRAVITY,
        AgentId.OPENCODE,
        AgentId.GEMINI_CLI,
        AgentId.CURSOR,
    ]
    assert get_adapter("claude-code").agent_id == AgentId.CLAUDE
    assert get_adapter("agy").agent_id == AgentId.ANTIGRAVITY
    assert get_adapter("gemini").agent_id == AgentId.GEMINI_CLI
    with pytest.raises(UnknownAgentError):
        get_adapter("nonexistent-agent")


@pytest.mark.unit
def test_detection_distinguishes_installed_config_only_and_not_found(
    make_env: Callable[..., EnvironmentContext],
) -> None:
    env = make_env(executables={"claude": "/usr/local/bin/claude", "codex": "/usr/local/bin/codex"})

    # Claude has binary + existing config dir with 1 skill
    make_skill(env.project_dir / ".claude" / "skills", "claude-skill")

    # Cursor has config dir only (no binary on PATH)
    make_skill(env.home_dir / ".cursor" / "skills", "cursor-global")

    # Codex has binary only (no config dir created)
    claude_res = get_adapter(AgentId.CLAUDE).detect(env)
    codex_res = get_adapter(AgentId.CODEX).detect(env)
    cursor_res = get_adapter(AgentId.CURSOR).detect(env)
    gemini_res = get_adapter(AgentId.GEMINI_CLI).detect(env)

    assert claude_res.status == DetectionStatus.INSTALLED_AND_CONFIGURED
    assert claude_res.skills_found == 1

    assert codex_res.status == DetectionStatus.INSTALLED_NO_CONFIG
    assert codex_res.skills_found == 0

    assert cursor_res.status == DetectionStatus.CONFIG_ONLY
    # Cursor sees both its own ~/.cursor/skills/cursor-global AND .claude/skills/claude-skill
    assert cursor_res.skills_found == 2

    assert gemini_res.status == DetectionStatus.NOT_FOUND
    assert gemini_res.skills_found == 0

    # Read-only invariant: scanning MUST NOT create missing directories
    assert not (env.home_dir / ".codex").exists()
    assert not (env.home_dir / ".gemini").exists()
    assert not (env.project_dir / ".agents").exists()


@pytest.mark.unit
def test_environment_variable_overrides_resolve_custom_paths(
    tmp_path: Path,
    make_env: Callable[..., EnvironmentContext],
) -> None:
    custom_claude = tmp_path / "custom_claude"
    custom_codex = tmp_path / "custom_codex"
    custom_opencode = tmp_path / "custom_opencode"
    custom_gemini_home = tmp_path / "custom_gemini_home"

    env = make_env(
        env_vars={
            "CLAUDE_CONFIG_DIR": str(custom_claude),
            "CODEX_HOME": str(custom_codex),
            "OPENCODE_CONFIG_DIR": str(custom_opencode),
            "GEMINI_CLI_HOME": str(custom_gemini_home),
        }
    )

    claude_global = get_adapter(AgentId.CLAUDE).primary_write_directory(env, AgentScope.GLOBAL)
    assert claude_global == custom_claude / "skills"

    codex_dirs = [
        s.path for s in get_adapter(AgentId.CODEX).skill_directories(env, AgentScope.GLOBAL)
    ]
    assert custom_codex / "skills" in codex_dirs

    opencode_global = get_adapter(AgentId.OPENCODE).primary_write_directory(env, AgentScope.GLOBAL)
    assert opencode_global == custom_opencode / "skills"

    gemini_global = get_adapter(AgentId.GEMINI_CLI).primary_write_directory(env, AgentScope.GLOBAL)
    assert gemini_global == custom_gemini_home / ".gemini" / "skills"
