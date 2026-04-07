# Changelog

This project keeps a human-curated changelog so users and contributors can see how QitOS evolves over time.

Format:
- `Added`: new features and capabilities
- `Changed`: behavior changes, refactors, and structural improvements
- `Fixed`: bug fixes
- `Deprecated`: old paths or APIs that will be removed later
- `Removed`: deleted features
- `Breaking`: upgrade notes for incompatible changes

How to update:
- Add high-signal entries under `Unreleased` while work is in progress
- Move `Unreleased` notes into a dated or versioned section when publishing a release
- Prefer user-facing changes, upgrade notes, and important engineering changes over low-level edit logs

## Unreleased

### Added

- Added PR/push CI gates covering tests, packaging validation, stable-surface linting, and stable-surface type checking.
- Added dedicated maturity docs for architecture, development workflow, security reporting, community conduct, and environment configuration.
- Added an explicit `qitos.kit.tool.experimental.security_research` namespace for opt-in security research tool imports and registry builders.
- Added thin module boundaries for `qita` data/server/views and `render` terminal/themes façades to make future maintenance easier.
- Added a root-level changelog to document ongoing project evolution.
- Added a dedicated `requirements-dev.txt` entrypoint for full contributor installs from a local clone.

### Changed

- Normalized the class-based tool contract around `execute(args, runtime_context)` while keeping `run(...)` as a compatibility path.
- Removed deprecated editor/codebase/file/shell compatibility shims in favor of the canonical `CodingToolSet` surface.
- Tightened default public exports from `qitos.kit` and `qitos.kit.tool` so experimental and higher-risk tool families are no longer part of the default surface.
- Preserved old security research import paths as short-term deprecation shims instead of keeping them as primary public entrypoints.
- Extracted shared coding-tool helper logic into internal utility modules to reduce coupling inside the canonical coding toolset.
- Slimmed `qita` and `render` entry modules so public behavior stays the same while implementation can evolve behind clearer boundaries.
- Reworked root installation guidance so `requirements.txt` is now a lightweight repo install path instead of a drifting copy of runtime and dev dependencies.
- Added coverage, dependency audit, and pre-commit tooling to the standard contributor workflow.
- Removed legacy root planning/audit scratch files, obsolete MkDocs configuration, and local phase-artifact directories so the repository surface matches the current Mintlify-based docs flow.

### Fixed

- Fixed compatibility issues in direct `.run(...)` calls after the tool execution contract was normalized.
- Fixed the known undefined `target` reference in the exploit payload generation flow.
- Fixed stable-surface lint and mypy failures across `qitos/core`, `qitos/engine`, `qitos/models`, and `qitos/trace`.

### Deprecated

- Deprecated legacy security research import paths under `qitos.kit.tool.*_toolset` and `qitos.kit.tool.security_audit` in favor of explicit imports from `qitos.kit.tool.experimental.security_research`.

### Breaking

- Default root exports from `qitos.kit` and `qitos.kit.tool` no longer include advanced/security-audit convenience surfaces; import those explicitly from their module paths when needed.
