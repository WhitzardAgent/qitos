# Development Notes For Agent Assistants

This repository is the source of truth for the CyberGym PoC-generation agent.
The active runtime is QitOS, but benchmark runs import the synced bundled copy:

```text
/data/pxd-team/workspace-149/zwq/qitos-cybergym/qitos/benchmark/cybergym/agent
```

## Quick Setup

```bash
# 1. Clone both repos side by side
cd /path/to/workspace
git clone https://github.com/bmz-q-q/cybergym_agent.git
git clone https://github.com/WhitzardAgent/qitos.git
cd qitos && git checkout qitos_cybergym && cd ..

# 2. Install qitos as editable dependency
pip install -e ./qitos

# 3. Verify
python3 -c "from qitos import AgentModule; print('qitos OK')"
python3 -c "import cybergym_agent; print('agent OK')"
```

Both repos must sit as siblings under the same parent directory.

## Vendored QitOS Dependency

The `qitos/` directory contains a vendored copy of the QitOS framework
(`github.com/WhitzardAgent/qitos`, branch `qitos_cybergym`). It is NOT a git
submodule — the source is committed directly into this repo.

Setup:

```bash
pip install -e ./qitos
```

To update the vendored copy from the upstream qitos repo:

```bash
rsync -a --exclude '.git' --exclude 'qitos_zoo' --exclude '__pycache__' \
  --exclude '*.egg-info' --exclude '*.pyc' --exclude 'runs' \
  /path/to/upstream/qitos/ ./qitos/
```

To sync the agent source into the bundled copy inside qitos for deployment:

```bash
bash scripts/sync_to_qitos.sh
```

## Current Architecture

The active class is `CyberGymAgent` in `agent.py`.

Core loop:

```text
CyberGymAdapter.from_task_dir(...)
  -> cli.build_agent(...)
  -> CyberGymAgent.init_state(...)
  -> QitOS Engine loop
  -> build_system_prompt() + prepare()
  -> tool execution
  -> CyberGymAgent.reduce()
```

The agent is state-first, not profile-first:

- `PhaseEngine` still tracks ingestion/investigation/formulation/verification.
- The model-facing behavior is driven more by prompt-visible state labels such as
  `candidate_ready`, `candidate_required`, `revisiting_after_miss`, and
  `analysis_stalled_no_candidate`.
- `submit_poc` feedback is the oracle.

Legacy CVEBench/web-exploit support has been removed from the source tree.
Treat CyberGym input PoC generation as the only active architecture.

## Important Files

- `agent.py`: prompt, tool registration, action gating, reducer, candidate flow
- `context.py`: snip/microcompact/span compaction and evidence memory
- `tracking_tools.py`: hypothesis, attempt, reflection ledgers
- `submit_tool.py`: verification server adapter
- `state.py`: `CyberGymState`
- `adapter.py`: task directory parsing
- `cli.py`: model defaults and harness construction
- `tests/`: regression tests that define expected behavior

## Runtime Artifacts

Do not commit runtime artifacts:

- `.agent/`
- `.cybergym/`
- `.pytest_cache/`
- task PoCs such as `poc_*`

`.agent/memory/project/` is created inside task workspaces during runs. It is a
runtime evidence store, not source documentation.

## Sync And Verification

After source changes:

```bash
python3 -m pytest tests -q

# Sync to the bundled copy inside the sibling qitos repo
QITOS_ROOT=../qitos bash scripts/sync_to_qitos.sh
```

If the change affects import/runtime behavior, also verify the bundled copy:

```bash
python3 -m py_compile ../qitos/qitos/benchmark/cybergym/agent/agent.py
```

## Current Design Biases

- Prefer early candidate generation and submit-feedback iteration over prolonged
  source reading.
- Keep tool surface narrow.
- Treat `candidate_required` as pressure, not a deadlock.
- Allow `BASH` for direct search/generation that unblocks candidate creation.
- Keep `READ` bounded and targeted.
- Preserve compact evidence pointers; do not rely on raw old tool output staying
  in prompt.
- Update tests when prompt shape intentionally changes; do not keep tests that
  assert old `BOOTSTRAP/VERIFY/ACTION_REQUIRED/update_task_ledger` prompt models.
