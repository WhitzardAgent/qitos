# Dependency Audit

## Current Packaging

`setup.py` keeps the core runtime small:

- `requests`
- `beautifulsoup4`
- `rich`
- `pyyaml`

Extras:

- `models`: `openai`, `litellm`
- `benchmarks`: `datasets`, `huggingface_hub`
- `dev`: build/test/lint/type/audit tools

## Classification

| Dependency | Classification | Notes |
| --- | --- | --- |
| `pyyaml` | core runtime dependency | Used by config/skill manifests; currently small enough to keep. |
| `rich` | core/runtime UX dependency | Used by render and REPL helpers. Keep unless render becomes optional. |
| `requests` | optional provider/browser/benchmark dependency | Used by provider adapters, web/search tools, OSWorld/CyberGym paths. Candidate for future optional split. |
| `beautifulsoup4` | optional browser/tool dependency | Used for web extraction. Candidate for future optional split. |
| `openai` | optional model/provider dependency | Already in `[models]`. |
| `litellm` | optional model/provider dependency | Already in `[models]`. |
| `datasets` | optional benchmark dependency | Already in `[benchmarks]`. |
| `huggingface_hub` | optional benchmark dependency | Already in `[benchmarks]`. |
| `pytest`, `black`, `flake8`, `mypy`, `build`, `twine`, `pre-commit`, `pip-audit` | docs/dev dependency | Correctly isolated in `[dev]`. |
| desktop/browser GUI dependencies | optional desktop/browser dependency | Current code avoids hard dependency; future GUI packages should go into `[desktop]`. |
| security research dependencies such as `scapy` | should move to qitos-zoo or optional security extra | Do not add to core runtime. Current imports are lazy inside explicit experimental modules. |
| product app dependencies | should move to qitos-zoo | qitos-coder and qitos-cyber-agent should own app-specific deps. |

## Packaging Changes

- `qitos.examples*` is excluded from installable packages.
- `plans*` and `plans/qitos_zoo_migration` are excluded from packages/sdists.

## Recommended Next Split

Avoid overcomplicating this PR. A later dependency PR should consider:

- `qitos[models]`: provider SDKs.
- `qitos[benchmarks]`: benchmark runners and dataset SDKs.
- `qitos[desktop]`: desktop/browser controller dependencies.
- `qitos[web]`: web extraction/search dependencies if core install needs to become smaller.

Cybersecurity product dependencies should live in `qitos-zoo`, not QitOS core.
