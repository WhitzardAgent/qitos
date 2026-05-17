"""Question prompt for AdviserAgent — user-facing task context with enrichment data."""

QUESTION_ADVISER_PROMPT = """\
<question_adviser_context>
  <instruction>Generate comprehensive and detailed advice for the user's question, utilizing the provided context and tools effectively.</instruction>

  <initiator_agent>{initiator_agent}</initiator_agent>

{enrichment_section}

  <user_question>
  {question}
  </user_question>

{code_section}

{output_section}
</question_adviser_context>"""
