# Contributing To QitOS

## Code Of Conduct

Participation in this project is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Ways To Contribute

- Improve docs, examples, and walkthroughs
- Add or refine tools, env integrations, and benchmark adapters
- Fix bugs or tighten framework contracts
- Improve tests, release hardening, and observability

## Development Setup

Use the quickstart install if you only want to run examples:

```bash
pip install -r requirements.txt
```

Use the full contributor setup for code changes:

```bash
git clone https://github.com/Qitor/qitos.git
cd qitos
pip install -r requirements-dev.txt
pre-commit install
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for common commands, coverage, troubleshooting, and docs workflow.

## Branch Naming

Use short branch names that reflect the change:

- `feat/<topic>`
- `fix/<topic>`
- `docs/<topic>`
- `refactor/<topic>`
- `chore/<topic>`

When using Codex or other assistants, `codex/<topic>` is also acceptable.

## Commit Messages

Prefer small, reviewable commits with imperative summaries:

- `feat: add model response summary to qita`
- `fix: preserve parser fallback after model interpretation`
- `docs: clarify coding toolset usage`

## Pull Request Process

Before opening a PR:

- Keep changes aligned with the `AgentModule + Engine` mental model
- Preserve or improve examples and docs when behavior changes
- Add or update tests for user-facing behavior
- Update [CHANGELOG.md](CHANGELOG.md) for high-signal user-facing changes
- Avoid unrelated cleanup in the same PR unless it directly unblocks the change

Boundary checklist:

- Is this generic framework code or product-specific app code?
- Does this belong in `qitos-zoo` instead?
- Does this add heavy dependencies to core?
- Does this expand the public API surface?
- Does this introduce security-sensitive behavior?
- Are tests and docs updated?

Before requesting review, run:

```bash
python -m pytest -q
python -m flake8 qitos/core qitos/engine qitos/models qitos/trace
python -m mypy qitos/core qitos/engine qitos/models qitos/trace
pytest --cov=qitos.core --cov=qitos.engine --cov=qitos.trace --cov-report=term --cov-fail-under=80 -q
python -m build
pip-audit
```

## Review Criteria

PRs are reviewed for:

- correctness and regression risk
- clarity of public surface changes
- documentation and migration quality
- test coverage for new behavior
- consistency with existing architecture boundaries

## Good First Areas

- walkthrough clarifications
- example polish
- benchmark docs
- toolset ergonomics
- qita UX improvements
