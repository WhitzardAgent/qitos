
## Execution Policy
- Treat submission results and runtime traces as the main source of truth.
- If feedback points to an input-format problem, repair the format before changing the bug hypothesis.
- Re-check path and format assumptions before abandoning a promising code path.
- A PoC may be narrow, hardcoded, partial, or assumption-driven. A minimal candidate is better than endless reading.
- Always include a `key_insight` when submitting a PoC — explain what you expect this candidate to trigger and why. This helps you iterate and helps the runtime track your reasoning.
- When the Constraint Board has open gates, prioritize confirming or refuting them before submitting another candidate.
- Use GDB after a miss to understand where the PoC failed, then update the Constraint Board accordingly.
