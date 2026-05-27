# LATS Template Notes

## Source idea
LATS (Language Agent Tree Search, Zhou et al. 2023) unifies reasoning, acting, and planning by integrating Monte Carlo Tree Search with language model evaluation:

1. **Select** a node using UCB1 (Upper Confidence Bound).
2. **Expand** the node by generating candidate actions via LLM.
3. **Evaluate** child nodes using LLM-powered value function.
4. **Rollout** the most promising path to a terminal state.
5. **Backpropagate** the reward up the tree.
6. **Reflect** on failed trajectories to guide future exploration.

## Mapping in QitOS
- `LATSCritic` implements the MCTS evaluation loop: computes rewards, tracks visit counts, and generates reflections on failure.
- `LATSState` tracks tree statistics: simulations done, best reward, failed paths, reflections.
- `instruction_patch` guides exploration toward unexplored paths and away from failed approaches.
- `Decision.branch()` is available for explicit branching when multiple candidates are generated.

## Key differences from the paper
- The paper uses a separate LLM call for value evaluation. QitOS uses heuristic reward signals from tool results (return codes, error messages, FINAL ANSWER markers).
- The paper maintains an explicit tree data structure. QitOS abstracts this into state fields and the critic's retry loop.
- Reflection in QitOS is generated from failure descriptions rather than a dedicated reflection prompt.

## Scope in this template
This template provides the core MCTS-style search loop with reflection. For full paper reproduction with LLM-based value functions, override `LATSCritic.evaluate()`.
