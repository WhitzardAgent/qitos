"""Question prompt for InstallerAgent — user-facing task context."""

QUESTION_INSTALLER_PROMPT = """\
<question_installer_context>
  <instruction>Develop a detailed infrastructure solution for the user's request, focusing on secure installation, configuration, and maintenance. Utilize available tools, follow Docker constraints, and deliver practical, environment-specific instructions.</instruction>

  <user_question>
  {question}
  </user_question>
</question_installer_context>"""
