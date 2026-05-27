# MoA Template Notes

## Source idea
Mixture-of-Agents (Wang et al. 2024) uses a layered architecture where multiple LLM agents independently generate proposals, and an aggregator synthesizes the best insights:

1. **Proposers** in earlier layers independently generate candidate responses.
2. Each agent in a layer receives **all outputs from the previous layer** as context.
3. The **aggregator** (final layer) synthesizes proposals into one unified answer.
4. Multiple rounds refine the output progressively.

Key insight: diversity of proposals improves quality, even when individual proposers are weaker models.

## Mapping in QitOS
- `MoAOrchestrator` manages the proposal collection and aggregation phases.
- `MoARecipeState` tracks proposals and synthesis across rounds.
- `MoACritic` drives the loop: prompts for proposal collection when proposals are missing, then prompts for aggregation.
- For actual parallel execution, use `qitos.kit.patterns.moa.build_moa_system()` which leverages `FanOutTool` for concurrent proposer runs.

## Key differences from the paper
- The paper uses multiple distinct LLMs as proposers. QitOS can use the same or different models.
- The paper's multi-layer architecture (Layer 1 → Layer 2 → ... → Aggregator) maps to QitOS's `max_rounds` parameter.
- The recipe template uses sequential single-agent orchestration. For parallel proposer execution, use `build_moa_system()`.

## Scope in this template
This template provides the orchestration interface for MoA. For full parallel execution with multiple LLMs, combine with `build_moa_system()`.
