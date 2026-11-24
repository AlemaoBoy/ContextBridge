# Pre-Release Checklist

- [ ] Unit and integration tests pass (`python -m pytest`).
- [ ] Linter and formatter pass (`python -m ruff check .` and `python -m ruff format --check .`).
- [ ] Static type checker passes (`python -m mypy src`).
- [ ] Wheel and sdist build cleanly (`python -m build`).
- [ ] No credentials or temporary files are staged in Git.
