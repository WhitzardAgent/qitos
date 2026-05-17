"""Execution context templates — structured XML context for agent invocations.

PentAGI dynamically builds XML execution context for every agent invocation,
providing awareness of the global task, completed subtasks, current subtask,
and planned subtasks. Two template variants exist:
- Full: Used during subtask execution with all details
- Short: Used for task-level context with minimal info
"""

FULL_EXECUTION_CONTEXT_TEMPLATE = """\
<execution_context>
  <global_task>
  {global_task}
  </global_task>

{previous_tasks_section}

  <completed_subtasks>
{completed_subtasks_xml}
  </completed_subtasks>

{current_subtask_section}

  <planned_subtasks>
{planned_subtasks_xml}
  </planned_subtasks>
</execution_context>"""

SHORT_EXECUTION_CONTEXT_TEMPLATE = """\
<execution_context>
  <global_task>
  {global_task}
  </global_task>

{previous_tasks_section}

  <completed_subtasks>
{completed_subtasks_short}
  </completed_subtasks>

{current_subtask_section_short}

  <planned_subtasks>
{planned_subtasks_short}
  </planned_subtasks>
</execution_context>"""

# Template for individual subtask in full context
SUBTASK_FULL_TEMPLATE = """    <subtask>
      <id>{id}</id>
      <title>{title}</title>
      <description>{description}</description>
      <status>{status}</status>
{result_line}
    </subtask>"""

# Template for individual subtask in short context
SUBTASK_SHORT_TEMPLATE = """    <subtask>
      <id>{id}</id>
      <title>{title}</title>
      <status>{status}</status>
    </subtask>"""

# Template for current subtask in full context
CURRENT_SUBTASK_TEMPLATE = """  <current_subtask>
    <id>{id}</id>
    <title>{title}</title>
    <description>{description}</description>
  </current_subtask>"""

CURRENT_SUBTASK_SHORT_TEMPLATE = """  <current_subtask>
    <id>{id}</id>
    <title>{title}</title>
  </current_subtask>"""

# Template for previous tasks
TASK_FULL_TEMPLATE = """    <task>
      <id>{id}</id>
      <title>{title}</title>
      <input>{input}</input>
      <status>{status}</status>
      <result>{result}</result>
    </task>"""

PREVIOUS_TASKS_SECTION = """  <previous_tasks>
{tasks_xml}
  </previous_tasks>"""

PREVIOUS_TASKS_SECTION_EMPTY = "  <previous_tasks></previous_tasks>"


__all__ = [
    "FULL_EXECUTION_CONTEXT_TEMPLATE",
    "SHORT_EXECUTION_CONTEXT_TEMPLATE",
    "SUBTASK_FULL_TEMPLATE",
    "SUBTASK_SHORT_TEMPLATE",
    "CURRENT_SUBTASK_TEMPLATE",
    "CURRENT_SUBTASK_SHORT_TEMPLATE",
    "TASK_FULL_TEMPLATE",
    "PREVIOUS_TASKS_SECTION",
    "PREVIOUS_TASKS_SECTION_EMPTY",
]
