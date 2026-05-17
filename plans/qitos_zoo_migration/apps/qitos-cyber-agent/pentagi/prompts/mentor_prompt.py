"""Mentor prompt — periodic adviser review of agent progress."""

MENTOR_QUESTION_PROMPT = """\
A specialist agent has been executing a subtask for {step_count} steps. Review their progress and provide guidance.

## Current Subtask
{subtask_title}
{subtask_description}

## Recent Actions (last {recent_count} steps)
{recent_actions}

## Key Findings So Far
{findings_summary}

## Execution Context (summarized)
{execution_context}

## Your Task

As a senior mentor, provide:
1. **Progress Assessment**: Is the agent making meaningful progress toward the subtask objective?
2. **Identified Issues**: Any problems, loops, or missed opportunities?
3. **Alternative Approaches**: If current approach isn't working, suggest alternatives
4. **Next Steps**: Concrete recommendations for what the agent should do next

Keep your analysis concise (under 300 words). Focus on actionable guidance.
If the agent appears stuck in a loop, explicitly suggest a different approach.
If the agent is making good progress, confirm and suggest what to focus on next."""
