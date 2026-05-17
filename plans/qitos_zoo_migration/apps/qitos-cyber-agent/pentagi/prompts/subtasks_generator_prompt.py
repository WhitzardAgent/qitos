"""Question prompt for GeneratorAgent — subtask generation context."""

SUBTASKS_GENERATOR_PROMPT = """\
<task_context>
  <instruction>
  Your goal is to generate optimized subtasks that will accomplish the PRIMARY USER REQUEST provided in the <user_task><input> field below.

  The <user_task><input> contains the MAIN OBJECTIVE that the user requested - this is the ultimate goal you must achieve.
  All subtasks you create MUST be designed to work together to accomplish this exact user request.
  Focus your subtasks on solving what the user asked for in <user_task><input>, not on tangential activities.
  </instruction>

  <user_task>
    <input>{task_input}</input>
  </user_task>

{previous_tasks_section}

{previous_subtasks_section}
</task_context>"""
