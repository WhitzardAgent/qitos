"""PentAGI prompt templates — replicating pentagi's prompt architecture."""

# Agent system prompts
from .primary_prompt import PRIMARY_SYSTEM_PROMPT
from .pentester_prompt import PENTESTER_SYSTEM_PROMPT
from .coder_prompt import CODER_SYSTEM_PROMPT
from .installer_prompt import INSTALLER_SYSTEM_PROMPT
from .searcher_prompt import SEARCHER_SYSTEM_PROMPT
from .memorist_prompt import MEMORIST_SYSTEM_PROMPT
from .generator_prompt import GENERATOR_SYSTEM_PROMPT
from .refiner_prompt import REFINER_SYSTEM_PROMPT
from .reporter_prompt import REPORTER_SYSTEM_PROMPT
from .adviser_prompt import ADVISER_SYSTEM_PROMPT
from .enricher_prompt import ENRICHER_SYSTEM_PROMPT

# Agent question/human prompts
from .question_pentester_prompt import QUESTION_PENTESTER_PROMPT
from .question_coder_prompt import QUESTION_CODER_PROMPT
from .question_installer_prompt import QUESTION_INSTALLER_PROMPT
from .question_searcher_prompt import QUESTION_SEARCHER_PROMPT
from .question_memorist_prompt import QUESTION_MEMORIST_PROMPT
from .question_adviser_prompt import QUESTION_ADVISER_PROMPT
from .question_enricher_prompt import QUESTION_ENRICHER_PROMPT
from .subtasks_generator_prompt import SUBTASKS_GENERATOR_PROMPT
from .subtasks_refiner_prompt import SUBTASKS_REFINER_PROMPT
from .task_reporter_prompt import TASK_REPORTER_PROMPT

# Utility prompts
from .execution_context import (
    FULL_EXECUTION_CONTEXT_TEMPLATE,
    SHORT_EXECUTION_CONTEXT_TEMPLATE,
)
from .reflector_prompt import REFLECTOR_SYSTEM_PROMPT, REFLECTOR_QUESTION_PROMPT
from .toolcall_fixer_prompt import TOOLCALL_FIXER_SYSTEM_PROMPT, TOOLCALL_FIXER_USER_PROMPT
from .summarizer_prompt import SUMMARIZER_SYSTEM_PROMPT

# Shared prompt sections (reusable building blocks)
from .shared_sections import (
    TOOL_PLACEHOLDER,
    AUTHORIZATION_STATUS_SECTION,
    MEMORY_PROTOCOL_SECTION,
    SUMMARIZATION_AWARENESS_SECTION,
    EXECUTION_CONTEXT_USAGE_SECTION,
    MENTOR_PROTOCOL_SECTION,
    CURRENT_TIME_SECTION,
    TERMINAL_PROTOCOL_SECTION,
    CONTAINER_CONSTRAINTS_SECTION,
)

__all__ = [
    # Agent system prompts
    "PRIMARY_SYSTEM_PROMPT",
    "PENTESTER_SYSTEM_PROMPT",
    "CODER_SYSTEM_PROMPT",
    "INSTALLER_SYSTEM_PROMPT",
    "SEARCHER_SYSTEM_PROMPT",
    "MEMORIST_SYSTEM_PROMPT",
    "GENERATOR_SYSTEM_PROMPT",
    "REFINER_SYSTEM_PROMPT",
    "REPORTER_SYSTEM_PROMPT",
    "ADVISER_SYSTEM_PROMPT",
    "ENRICHER_SYSTEM_PROMPT",
    # Agent question/human prompts
    "QUESTION_PENTESTER_PROMPT",
    "QUESTION_CODER_PROMPT",
    "QUESTION_INSTALLER_PROMPT",
    "QUESTION_SEARCHER_PROMPT",
    "QUESTION_MEMORIST_PROMPT",
    "QUESTION_ADVISER_PROMPT",
    "QUESTION_ENRICHER_PROMPT",
    "SUBTASKS_GENERATOR_PROMPT",
    "SUBTASKS_REFINER_PROMPT",
    "TASK_REPORTER_PROMPT",
    # Utility prompts
    "FULL_EXECUTION_CONTEXT_TEMPLATE",
    "SHORT_EXECUTION_CONTEXT_TEMPLATE",
    "REFLECTOR_SYSTEM_PROMPT",
    "REFLECTOR_QUESTION_PROMPT",
    "TOOLCALL_FIXER_SYSTEM_PROMPT",
    "TOOLCALL_FIXER_USER_PROMPT",
    "SUMMARIZER_SYSTEM_PROMPT",
    # Shared prompt sections
    "TOOL_PLACEHOLDER",
    "AUTHORIZATION_STATUS_SECTION",
    "MEMORY_PROTOCOL_SECTION",
    "SUMMARIZATION_AWARENESS_SECTION",
    "EXECUTION_CONTEXT_USAGE_SECTION",
    "MENTOR_PROTOCOL_SECTION",
    "CURRENT_TIME_SECTION",
    "TERMINAL_PROTOCOL_SECTION",
    "CONTAINER_CONSTRAINTS_SECTION",
]
