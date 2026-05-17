"""Question prompt for ReporterAgent — task report generation context."""

TASK_REPORTER_PROMPT = """\
<task_report_context>
  <instruction>Generate a comprehensive evaluation report for the user's task</instruction>

  <user_task>
    <input>{task_input}</input>
  </user_task>

{completed_subtasks_section}

{planned_subtasks_section}
</task_report_context>"""
