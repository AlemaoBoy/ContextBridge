---
name: sample-skill
description: >-
  Guides developers through a safe pre-release checklist for Python CLI packages.
  Use when preparing a release, verifying package metadata, or running local QA checks.
license: MIT
compatibility: Requires Python 3.11+
metadata:
  author: contextbridge-maintainers
  version: "0.1.0"
---

# Sample Release Checklist Skill

Use this skill when preparing a local open-source Python CLI release:

1. Review [the release checklist](./references/checklist.md).
2. Inspect the helper script at [check_env.py](./scripts/check_env.py) before running it manually.
3. Verify tests, linting, type checking, and packaging build cleanly.
