# Development Guide

## Prerequisites

- Python 3.10+
- `pip`
- `git`

Optional but recommended:

- `rg` for fast search
- `pre-commit`

## Local Setup

Quickstart from a local clone:

```bash
pip install -r requirements.txt
```

Full contributor setup:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

## Common Commands

Run tests:

```bash
python -m pytest -q
```

Run stable-surface lint:

```bash
python -m flake8 qitos/core qitos/engine qitos/models qitos/trace
```

Run stable-surface type checks:

```bash
python -m mypy qitos/core qitos/engine qitos/models qitos/trace
```

Run coverage gate:

```bash
pytest --cov=qitos.core --cov=qitos.engine --cov=qitos.trace --cov-report=term --cov-fail-under=80 -q
```

Build the package:

```bash
python -m build
```

Audit Python dependencies:

```bash
pip-audit
```

Preview docs locally:

```bash
npx mintlify dev docs
```

Check docs links:

```bash
npx mintlify broken-links docs
```

## Environment Configuration

Copy [.env.example](.env.example) and set only the provider variables you need.

Avoid committing:

- `.env`
- local API keys
- temporary benchmark data
- generated run artifacts

## Common Tasks

### Adding A New Tool

1. Implement it under the appropriate `qitos/kit/tool` module.
2. Give it a clear docstring and tool spec.
3. Register it through the canonical toolset or an explicit registry builder.
4. Add behavior tests.
5. Update docs if it changes the public surface.

Security-sensitive tools must require explicit opt-in imports and must not be exposed from `qitos`, default `qitos.kit` imports, `qit demo`, or quickstart examples.

### Adding A Product Agent

Product-grade agents belong in `qitos-zoo`. Keep QitOS examples small, teaching-first, and focused on one framework concept at a time.

### Changing Engine Behavior

1. Preserve `Decision` as the execution contract.
2. Add or update hook/trace coverage.
3. Verify qita and parser behavior if event payloads change.

### Removing Or Replacing APIs

1. Update examples, templates, and docs in the same change.
2. Remove long-term compatibility layers rather than introducing new ones unless migration risk is high.
3. Record the change in [CHANGELOG.md](CHANGELOG.md).

## Release Checklist

When cutting a GitHub release, verify the repo-facing surfaces before you tag:

1. Run `python -m pytest -q`.
2. Run `python -m build`.
3. Run `python -m twine check dist/*`.
4. Run `npx mintlify broken-links docs`.
5. Confirm `README.md`, `README.zh.md`, `CHANGELOG.md`, and the relevant `docs/` pages match the shipped behavior.
6. Confirm `origin` points to `https://github.com/Qitor/qitos.git`.
7. Confirm GitHub auth works with `gh auth status`.
8. Create or update the release body from `plans/releases/v0.3.0.md`.
9. Push the release branch and tag, then publish with:

```bash
git tag v0.3.0
git push origin HEAD
git push origin v0.3.0
gh release create v0.3.0 \
  --repo Qitor/qitos \
  --title "QitOS v0.3.0" \
  --notes-file plans/releases/v0.3.0.md
```

## Troubleshooting

If coverage flags are unavailable locally, reinstall the contributor environment:

```bash
pip install -r requirements-dev.txt
```

If build artifacts or caches cause confusion, remove:

- `build/`
- `dist/`
- `.pytest_cache/`
- `.mypy_cache/`

If docs links appear stale, run `npx mintlify broken-links docs` and preview the site with `npx mintlify dev docs`.
