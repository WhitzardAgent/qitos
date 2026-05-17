"""Question prompt for EnricherAgent — user-facing task context for enrichment."""

QUESTION_ENRICHER_PROMPT = """\
<question_enricher_context>
  <instruction>
  Thoroughly enhance the user's question by incorporating the given context and utilizing the provided tools effectively.
  Ensure the enriched question is comprehensive and precise. Use <code_snippet> and <command_output> to provide examples of how to use the tools.
  </instruction>

  <user_question>
  {question}
  </user_question>

{code_section}

{output_section}
</question_enricher_context>"""
