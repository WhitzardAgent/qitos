"""Question prompt for CoderAgent — user-facing task context."""

QUESTION_CODER_PROMPT = """\
<question_coder_context>
  <instruction>Generate a comprehensive and detailed code for the user's question, utilizing the provided context and tools effectively.</instruction>

  <user_question>
  {question}
  </user_question>
</question_coder_context>"""
