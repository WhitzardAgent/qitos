"""Question prompt for RefinerAgent — subtask refinement context."""

SUBTASKS_REFINER_PROMPT = """\
<refinement_context>
  <instruction>
  Your goal is to optimize the remaining subtasks to accomplish the PRIMARY USER REQUEST provided in the <user_task><input> field below.

  The <user_task><input> contains the MAIN OBJECTIVE that the user requested - this is the ultimate goal you must achieve.
  Based on completed subtask results, refine the remaining plan to accomplish this exact user request more efficiently.
  All modifications (add/remove/modify) must be focused on achieving what the user asked for in <user_task><input>.
  Remove subtasks that don't contribute to the user's goal, add subtasks that fill critical gaps toward the goal.
  </instruction>

  <user_task>
    <input>{task_input}</input>
  </user_task>

{completed_subtasks_section}

{planned_subtasks_section}
</refinement_context>"""
