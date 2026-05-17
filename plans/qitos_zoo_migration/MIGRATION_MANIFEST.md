# qitos-zoo Migration Manifest

## Proposed Repository Layout

```text
qitos-zoo/
  README.md
  apps/
    qitos-coder/
      README.md
      src/
      configs/
      prompts/
      examples/
      tests/
    qitos-cyber-agent/
      README.md
      src/
      configs/
      prompts/
      examples/
      tests/
    experimental/
  shared/
    qitos_zoo_common/
  docs/
    adding_a_new_agent.md
    app_template.md
    safety_and_scope.md
```

## Current Staging Map

| Current QitOS path | Staged destination |
| --- | --- |
| `examples/real/claude_code_agent.py` | `apps/qitos-coder/` |
| `examples/real/claude_code/` | `apps/qitos-coder/` |
| `qitos/examples/pentagi/` | `apps/qitos-cyber-agent/` |
| `examples/real/code_security_audit_agent.py` | `apps/qitos-cyber-agent/` |
| `examples/real/swe_agent.py` | `apps/experimental/` |
| `examples/real/computer_use_agent.py` | `apps/experimental/` |
| `examples/real/openai_cua_agent.py` | `apps/experimental/` |
| `examples/real/epub_reader_agent.py` | `apps/experimental/` |
| `examples/real/whitzard_agent.py` | `apps/experimental/` |
| `examples/real/_whitzard_memory.py` | `apps/experimental/` |
| `examples/real/skillhub_github_agent.py` | `apps/experimental/` |
| `examples/real/visual_inspect_agent.py` | `apps/experimental/` |
| `examples/real/terminus_2.py` | `apps/experimental/` |

## qitos-coder

Use the app name `qitos-coder`. Describe it as a Claude Code-inspired coding agent built with QitOS. Do not use `claude` in Python package names, distribution names, or app identifiers.

## qitos-cyber-agent

Use the app name `qitos-cyber-agent`. Describe it as a PentAGI-inspired cybersecurity agent built with QitOS. Do not name the package `qitos-pent`.

Safety scope:

> This app is for controlled security research workflows, defensive evaluation, CTF-style sandboxes, and authorized environments only.

## Shared Code Policy

- Generic abstractions move down into QitOS only after two or more zoo apps need them.
- Product-specific code stays in qitos-zoo.
- QitOS core must not import from qitos-zoo.
- qitos-zoo may depend on QitOS, but QitOS must not depend on qitos-zoo.

## Release Policy

- QitOS core has its own semver.
- qitos-zoo apps may evolve faster.
- qitos-zoo apps may pin QitOS versions.
