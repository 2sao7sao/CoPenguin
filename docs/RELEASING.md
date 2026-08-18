# Releasing CoPenguin

CoPenguin uses Semantic Versioning. A package version in `pyproject.toml` is a
release candidate until a matching signed or annotated tag has produced a
GitHub Release.

## Release checklist

1. Work from a clean, up-to-date `main` after the convergence pull request has
   merged.
2. Confirm the intended version appears in `pyproject.toml`, package
   `__version__` exports, and `CHANGELOG.md`.
3. Move release notes from `Unreleased` into a dated version section.
4. Run all quality gates:

   ```bash
   python -m pip install --upgrade pip "setuptools>=83"
   ruff format --check src tests scripts
   ruff check src tests scripts
   pytest -q
   mypy --ignore-missing-imports src/super_agent_runtime src/copenguin
   mypy --ignore-missing-imports --follow-imports=skip src/feishu_computer_agent/server.py
   python -m build
   python -m pip_audit --local
   docker build -t copenguin:release-candidate .
   docker run --rm copenguin:release-candidate copenguin demo --json
   ```

5. Verify the tag locally and create it:

   ```bash
   python scripts/verify_release_tag.py v0.1.0
   git tag -a v0.1.0 -m "CoPenguin v0.1.0"
   git push origin v0.1.0
   ```

The tag triggers `.github/workflows/release.yml`, which reruns formatting,
linting, tests, and package build before creating the GitHub Release with wheel
and source distribution assets. Do not publish a release from an unmerged
feature branch.
