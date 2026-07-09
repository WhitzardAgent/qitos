"""Shared constants for CyberGym agent mixins.

These were previously module-level in agent.py. Centralising them here
avoids circular imports when mixins need access to the same constants.
"""

from __future__ import annotations

import os
import re

from ..tool_names import EXPLORE_DELEGATE, INSIGHT_DELEGATE

# ---------------------------------------------------------------------------
# Context / history
# ---------------------------------------------------------------------------

CYBERGYM_HISTORY_MAX_TOKENS = 100_000
CYBERGYM_HISTORY_WARNING_RATIO = 0.80

# ---------------------------------------------------------------------------
# READ budget
# ---------------------------------------------------------------------------

DEFAULT_READ_LINE_LIMIT = 240
DEFAULT_READ_MAX_CHARS = 20_000
# P42: raised from 8 to 12 — Level 1 tasks have no patch.diff and require
# 7-9 READs just to trace the entry→sink path before PoC construction.
NO_CANDIDATE_READ_ACTION_LIMIT = 12
ACTIVE_CANDIDATE_READ_ACTION_LIMIT = 10
ACTIVE_CANDIDATE_TARGETED_READ_LIMIT = 6

# ---------------------------------------------------------------------------
# Force-submit hard block (removed — now uses soft guidance instead)
# See _one_shot_reminder_lines in observations.py for the soft BUDGET NOTE.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Seed corpus
# ---------------------------------------------------------------------------

SEED_CORPUS_ENABLED = os.environ.get(
    "CYBERGYM_SEED_CORPUS", "1"
).strip().lower() not in {"0", "false", "no", "off"}

# ---------------------------------------------------------------------------
# Reinvestigation
# ---------------------------------------------------------------------------

REINVESTIGATE_ENABLED = os.environ.get(
    "CYBERGYM_REINVESTIGATE", "1"
).strip().lower() not in {"0", "false", "no", "off"}
# P42: lowered from 12 to 6 — by 12 failed submits the agent has wasted
# too many steps on blind generation. 6 is early enough to redirect.
REINVESTIGATE_AFTER_SUBMITS = 6

# ---------------------------------------------------------------------------
# Feedback mode
# ---------------------------------------------------------------------------

VUL_ONLY_FEEDBACK = os.environ.get(
    "CYBERGYM_VUL_ONLY_FEEDBACK", "1"
).strip().lower() not in {"0", "false", "no", "off"}

# ---------------------------------------------------------------------------
# Failure reflection
# ---------------------------------------------------------------------------

FAILURE_REFLECTION_ACK_KEY = "failure_reflection_ack"
REPEATED_FAILURE_REFLECTION_THRESHOLD = 5
REFLECTION_ATTEMPT_COOLDOWN = 5
FAILURE_REFLECTION_ATTEMPT_KEY = "failure_reflection_poc_attempts"

# ---------------------------------------------------------------------------
# Delegate
# ---------------------------------------------------------------------------

DELEGATE_EXPLORATION_REPORT_SEEN_KEY = "delegate_exploration_report_seen"
DELEGATE_TOOL_AGENT_NAMES = {
    EXPLORE_DELEGATE: "explore_delegate",
    INSIGHT_DELEGATE: "insight_delegate",
    "delegate_to_explore_delegate": "explore_delegate",
    "delegate_to_insight_delegate": "insight_delegate",
}

# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

LOOP_REMINDER_TEXT = (
    "You may be looping. Re-check the exact trigger condition and input entry path "
    "before producing more variants."
)
CANDIDATE_REQUIRED_REMINDER_TEXT = (
    "Stop continuing general investigation. The next useful step must create or modify "
    "a concrete raw-input PoC under `pocs/`, then submit it. Use at most one targeted "
    "READ/GREP only if a specific blocking detail is missing."
)

# ---------------------------------------------------------------------------
# PoC output
# ---------------------------------------------------------------------------

POC_OUTPUT_DIR = "pocs"
POC_PLACEHOLDER_CHARS = set("{}<>[]$*?")

# ---------------------------------------------------------------------------
# Suggested constraints (auto-extracted by tree-sitter)
# ---------------------------------------------------------------------------

SUGGESTED_CONSTRAINTS_ENABLED = os.environ.get(
    "CYBERGYM_SUGGESTED_CONSTRAINTS", "1"
).strip().lower() not in {"0", "false", "no", "off"}
# Default ON — tree-sitter extractor produces useful suggestions.
# Set to 0 to disable if quality regresses.

CYBERGYM_VNEXT_ANALYSIS = os.environ.get(
    "CYBERGYM_VNEXT_ANALYSIS", "1"
).strip().lower() not in {"0", "false", "no", "off"}

# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------

_BENCHMARK_NAME_RE = re.compile("cybergym", re.IGNORECASE)
