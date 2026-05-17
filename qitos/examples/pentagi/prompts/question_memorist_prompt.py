"""Question prompt for MemoristAgent — user-facing task context."""

QUESTION_MEMORIST_PROMPT = """\
<question_memorist_context>
  <instruction>
  Retrieve and synthesize historical information relevant to the user's question. Split complex queries into precise vector database searches using exact sentence matching for optimal retrieval.

  Combine multiple search results into a cohesive response that provides comprehensive historical context. Focus on extracting precise information from vector database storage that directly addresses the user's query.
  </instruction>

  <user_question>
  {question}
  </user_question>
</question_memorist_context>"""
