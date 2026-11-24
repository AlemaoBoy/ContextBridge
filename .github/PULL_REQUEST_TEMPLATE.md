## Summary

<!-- Briefly explain what this pull request changes and why. -->

## Related Issues

<!-- Closes #... -->

## Type of Change

- [ ] Bug fix
- [ ] Security hardening
- [ ] New feature / adapter
- [ ] Documentation update
- [ ] Tests / CI

## Checklist

- [ ] Read and followed `AGENTS.md` and `CONTRIBUTING.md`.
- [ ] Read-only commands (`scan`, `skills list`, `skills validate`, `doctor`, `backup list`) remain strictly read-only.
- [ ] Automated tests use isolated `tmp_path` and injected `EnvironmentContext` (never real agent configs).
- [ ] No real secrets, tokens, or personal paths are included in code, fixtures, or docs.
- [ ] Verified locally:
  - [ ] `python -m pytest --cov=contextbridge`
  - [ ] `python -m ruff check .`
  - [ ] `python -m ruff format --check .`
  - [ ] `python -m mypy src`
  - [ ] `python -m build` and `python -m twine check dist/*`
