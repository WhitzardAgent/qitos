"""Question prompt for SearcherAgent — user-facing task context."""

QUESTION_SEARCHER_PROMPT = """\
<question_searcher_context>
  <instruction>
  Deliver relevant information with maximum efficiency by prioritizing search tools in this order: internal memory → specialized tools → general search engines. Start with checking existing knowledge, then use precise technical terms in your searches.

  Limit yourself to 3-5 search actions maximum. STOP searching once you have sufficient information to answer the query completely. Structure your response by relevance and provide actionable solutions without unnecessary details.
  </instruction>

  <user_question>
  {question}
  </user_question>
</question_searcher_context>"""
