# Releasing Etch

## Pre-release checklist

1. **All tests pass** on the main branch:
   ```bash
   pip install -e ".[dev]"
   pytest
   ruff check etch/ tests/
   ```

2. **Version bumped** in `pyproject.toml`:
   ```toml
   version = "X.Y.Z"
   ```
   Follow [Semantic Versioning](https://semver.org/): bump major for breaking changes, minor for features, patch for fixes.

3. **CHANGELOG.md updated** with a section for the new version describing what changed.

4. **Commit the version bump and changelog** to `main`:
   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit -m "release: vX.Y.Z"
   git push origin main
   ```

## Tagging and publishing

The release workflow is fully automated via GitHub Actions using PyPI trusted publishing (OIDC). No API keys are needed.

1. **Create and push a git tag**:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

2. The `release.yml` workflow will automatically:
   - Run lint and tests
   - Build sdist and wheel
   - Publish to PyPI

3. Monitor the workflow run at:
   ```
   https://github.com/maco144/Etch/actions
   ```

## Post-release verification

1. **Check PyPI** — the package should appear within a few minutes:
   ```
   https://pypi.org/project/etch/X.Y.Z/
   ```

2. **Test installation** from PyPI in a clean environment:
   ```bash
   python -m venv /tmp/etch-test && source /tmp/etch-test/bin/activate
   pip install etch==X.Y.Z
   python -c "import etch; print('OK')"
   ```

3. **Create a GitHub Release** (optional but recommended):
   ```bash
   gh release create vX.Y.Z --generate-notes
   ```

## PyPI trusted publisher setup (one-time)

Before the first release, configure trusted publishing on PyPI:

1. Go to https://pypi.org/manage/account/publishing/
2. Add a new pending publisher:
   - PyPI project name: `etch`
   - Owner: `maco144`
   - Repository: `Etch`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
