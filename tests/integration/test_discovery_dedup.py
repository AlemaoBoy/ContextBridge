"""Integration tests for shared-directory deduplication in scan and skills list."""

from __future__ import annotations

import pytest
from tests.conftest import make_skill

from contextbridge.core.discovery import discover_skills, scan_agents
from contextbridge.core.paths import EnvironmentContext
from contextbridge.models.agent import AgentId, AgentScope


@pytest.mark.integration
def test_shared_agents_skills_directory_deduplicated_across_agents(
    isolated_env: EnvironmentContext,
) -> None:
    # A single physical skill inside .agents/skills/ is visible to Codex, Antigravity,
    # OpenCode, Gemini CLI, and Cursor — must be counted ONCE as a unique physical skill.
    make_skill(isolated_env.project_dir / ".agents" / "skills", "shared-skill")
    # A Claude-native skill inside .claude/skills/ is also visible to OpenCode and Cursor (compat).
    make_skill(isolated_env.project_dir / ".claude" / "skills", "claude-native")

    all_skills = discover_skills(isolated_env)
    assert len(all_skills) == 2

    by_name = {s.name: s for s in all_skills}
    shared = by_name["shared-skill"]
    assert shared.is_shared_directory is True
    assert AgentId.CODEX in shared.associated_agents
    assert AgentId.ANTIGRAVITY in shared.associated_agents
    assert AgentId.CURSOR in shared.associated_agents
    assert AgentId.CLAUDE not in shared.associated_agents

    claude_native = by_name["claude-native"]
    assert AgentId.CLAUDE in claude_native.associated_agents
    assert AgentId.CURSOR in claude_native.compatibility_agents
    assert AgentId.OPENCODE in claude_native.compatibility_agents

    # Filtering by agent returns only skills discovered by that agent
    claude_only = discover_skills(isolated_env, agent_filter="claude")
    assert [s.name for s in claude_only] == ["claude-native"]

    codex_only = discover_skills(isolated_env, agent_filter="codex")
    assert [s.name for s in codex_only] == ["shared-skill"]

    # Scan summary reports deduplicated physical count and identifies shared directories
    summary = scan_agents(isolated_env)
    assert summary.unique_physical_skills == 2
    assert len(summary.shared_directories) >= 1


@pytest.mark.integration
def test_scope_filter_separates_project_and_global_skills(
    isolated_env: EnvironmentContext,
) -> None:
    make_skill(isolated_env.project_dir / ".claude" / "skills", "proj-skill")
    make_skill(isolated_env.home_dir / ".claude" / "skills", "global-skill")

    proj_only = discover_skills(isolated_env, scope_filter=AgentScope.PROJECT)
    glob_only = discover_skills(isolated_env, scope_filter=AgentScope.GLOBAL)

    assert [s.name for s in proj_only] == ["proj-skill"]
    assert [s.name for s in glob_only] == ["global-skill"]
