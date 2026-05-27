# Magentic-One Template Notes

## Source idea
Magentic-One (Furtado et al. 2024) is a generalist multi-agent system with a dual-loop architecture:

1. **Outer Loop (Task Ledger)**: The orchestrator creates a plan, gathers facts and educated guesses into a Fact Bank, and maintains a Task Ledger.
2. **Inner Loop (Progress Ledger)**: At each step, the orchestrator self-reflects on progress, delegates a subtask to a specialist, and updates the Progress Ledger.
3. **Re-planning**: When progress stalls for enough steps, the orchestrator updates the Fact Bank and creates a new plan.

Specialist agents: Websurfer (browser), FileSurfer (local files), Coder (code), ComputerTerminal (execution).

## Mapping in QitOS
- `MagenticOneOrchestrator` maintains the Fact Bank and Task Ledger in state.
- `ProgressCritic` implements the Progress Ledger: detects stalls, triggers re-planning via `instruction_patch`, and stops when max stalls are reached.
- `MagenticOneState` tracks facts, tasks, completed tasks, and stall count.
- Handoff to specialists uses QitOS's `HandoffTool` and `AgentRegistry`.

## Key differences from the paper
- The paper uses a specific JSON-based ledger format. QitOS uses structured state fields and the critic's evaluation.
- The paper's specialist agents (Websurfer, FileSurfer, etc.) are domain-specific. QitOS's template provides a generic specialist pattern that callers customize.
- The paper distinguishes between Task Ledger and Progress Ledger as separate prompts. QitOS merges progress tracking into the `ProgressCritic`.

## Scope in this template
This template provides the orchestrator loop with progress detection and re-planning. Callers define their own specialist agents and register them via `AgentRegistry`.
