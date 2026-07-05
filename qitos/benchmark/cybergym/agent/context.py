"""Four-level progressive compaction pipeline for CyberGym context management.

Levels:
  1. Snip      -- Replace old tool results with clearance markers (no LLM call)
  2. MicroCompact -- Compress long messages to preview + tail (no LLM call)
  3. Collapse  -- Proactive restructuring at 90% context utilization
  4. AutoCompact -- LLM-based summarization of older rounds

Plus: CompactionCircuitBreaker, PostCompactRestorer, and optional LLM summarizer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from qitos.core.history import HistoryMessage
from qitos.kit.history.compact_history import (
    CompactConfig,
    CompactHistory,
)

PROJECT_ARTIFACT_ROOT = Path(".agent") / "memory" / "project"

# ---------------------------------------------------------------------------
# Environment flags
# ---------------------------------------------------------------------------

#: When set to "1", SnipCompactor will use LLM summarization for large
#: tool outputs instead of head+tail truncation. Default: disabled.
LLM_COMPACT_ENABLED = os.environ.get("CYBERGYM_LLM_COMPACT", "0") == "1"

#: When set to "1" (default), aggressively snip old READ messages after
#: only a few turns, freeing context for fresh tool outputs.  Normal-priority
#: READs (exploratory) are snipped after 3 turns; high/critical-priority
#: READs (parser/field/seed) are still protected.
EARLY_READ_SNIP = os.environ.get(
    "CYBERGYM_EARLY_READ_SNIP", "1"
).strip().lower() not in {"0", "false", "no", "off"}

#: Character threshold above which LLM summarization is attempted.
LLM_SUMMARY_THRESHOLD = 8_000

#: Prompt for LLM-based tool output summarization.
_TOOL_SUMMARY_PROMPT = (
    "Summarize this tool output for a vulnerability PoC agent. "
    "Extract ONLY facts: function signatures, buffer sizes, field offsets, "
    "magic numbers, struct layouts, error messages, crash types. "
    "Do NOT include recommendations, suggestions, or action plans. "
    "Format as bullet points."
)


# ---------------------------------------------------------------------------
# Level 1: Snip
# ---------------------------------------------------------------------------

class SnipCompactor:
    """Replace old tool results with clearance markers (Level 1 compaction).

    Preserves message chain continuity (important for tool call ID references)
    while freeing context budget by replacing the content of old tool/observation
    messages with a short marker.
    """

    COMPRESSIBLE_ROLES = {"tool", "observation"}
    PREVIEW_HEAD_CHARS = 160
    PREVIEW_TAIL_CHARS = 160
    PROJECT_ARTIFACT_ROOT = PROJECT_ARTIFACT_ROOT

    def __init__(self, keep_recent: int = 10, llm: Any = None):
        self.keep_recent = keep_recent
        self.llm = llm  # Optional LLM for summarization (requires CYBERGYM_LLM_COMPACT=1)

    def snip(
        self,
        messages: List[HistoryMessage],
        state: Any = None,
        predicate: Any = None,
    ) -> List[HistoryMessage]:
        """Snip old compressible messages, keeping the most recent N."""
        compressible = [
            (i, msg)
            for i, msg in enumerate(messages)
            if msg.role in self.COMPRESSIBLE_ROLES
            and not msg.metadata.get("summary")
            and not msg.metadata.get("snipped")
            and (predicate is None or bool(predicate(msg)))
        ]

        # Skip the most recent N compressible messages
        if self.keep_recent <= 0:
            to_snip = compressible
        else:
            to_snip = (
                compressible[: -self.keep_recent]
                if len(compressible) > self.keep_recent
                else []
            )

        result = list(messages)
        for i, msg in to_snip:
            serialized = self._serialize_content(msg.content)
            index_metadata = self._index_metadata_for_message(msg, serialized)
            saved_path = self._persist_snip_payload(
                content=serialized,
                step_id=int(getattr(msg, "step_id", 0) or 0),
                ordinal=i,
                role=str(msg.role or "tool"),
                state=state,
                index_metadata=index_metadata,
            )
            # Optional LLM summarization for large tool outputs
            summary = None
            if (
                LLM_COMPACT_ENABLED
                and self.llm is not None
                and len(serialized) > LLM_SUMMARY_THRESHOLD
            ):
                summary = self._llm_summarize_tool_output(serialized)
            if summary:
                compacted = (
                    f"[compact:start kind=tool_summary path={saved_path} "
                    f"original_chars={len(serialized)}]\n"
                    f"{summary}\n"
                    f"[compact:end]"
                )
            else:
                preview_head, preview_tail = self._preview_parts(serialized)
                compacted = self._render_snipped_message(
                    saved_path=saved_path,
                    original_chars=len(serialized),
                    preview_head=preview_head,
                    preview_tail=preview_tail,
                )
            result[i] = HistoryMessage(
                role=msg.role,
                content=compacted,
                step_id=msg.step_id,
                metadata={
                    **msg.metadata,
                    "snipped": True,
                    "original_chars": len(serialized),
                    "snip_saved_path": saved_path,
                    **(
                        {"snip_preview_head": preview_head, "snip_preview_tail": preview_tail}
                        if not summary
                        else {"snip_summary": True}
                    ),
                },
            )
        return result

    # ------------------------------------------------------------------
    # READ-specific early snipping
    # ------------------------------------------------------------------

    def snip_reads_early(
        self,
        messages: List[HistoryMessage],
        keep_recent: int = 3,
        state: Any = None,
    ) -> List[HistoryMessage]:
        """Snip READ tool messages more aggressively than other tools.

        Normal-priority READs beyond the most recent *keep_recent* are
        replaced with fact-oriented previews.  High/critical priority READs
        (parser/field/seed paths) are preserved — they are critical for
        PoC construction.
        """
        read_indices = [
            (i, msg)
            for i, msg in enumerate(messages)
            if msg.role in self.COMPRESSIBLE_ROLES
            and not msg.metadata.get("summary")
            and not msg.metadata.get("snipped")
            and str(
                getattr(msg, "name", None)
                or msg.metadata.get("tool_name", "")
            ).strip().upper() == "READ"
        ]

        # Skip the most recent N READ messages
        if keep_recent <= 0:
            to_snip = read_indices
        elif len(read_indices) > keep_recent:
            to_snip = read_indices[: -keep_recent]
        else:
            return messages  # All recent enough

        result = list(messages)
        for i, msg in to_snip:
            # Skip high/critical priority READs (parser/field/seed paths)
            priority = str(msg.metadata.get("compaction_priority", "normal")).lower()
            if priority in ("high", "critical"):
                continue

            serialized = self._serialize_content(msg.content)
            index_metadata = self._index_metadata_for_message(msg, serialized)
            saved_path = self._persist_snip_payload(
                content=serialized,
                step_id=int(getattr(msg, "step_id", 0) or 0),
                ordinal=i,
                role=str(msg.role or "tool"),
                state=state,
                index_metadata=index_metadata,
            )

            # Fact-oriented preview for READ
            compacted = self._render_read_snipped_message(
                saved_path=saved_path,
                original_chars=len(serialized),
                content=serialized,
                state=state,
            )

            result[i] = HistoryMessage(
                role=msg.role,
                content=compacted,
                step_id=msg.step_id,
                metadata={
                    **msg.metadata,
                    "snipped": True,
                    "snip_reason": "early_read",
                    "original_chars": len(serialized),
                    "snip_saved_path": saved_path,
                },
            )
        return result

    @classmethod
    def _render_read_snipped_message(
        cls,
        *,
        saved_path: str,
        original_chars: int,
        content: str,
        state: Any = None,
    ) -> str:
        """Render a snipped READ with fact-oriented preview.

        Instead of raw head/tail (useless for source code), extract:
        - First function signature / struct / #define
        - Last significant line
        - Line count
        - Any durable_code_facts that reference this READ's path
        """
        lines = content.splitlines()

        # Extract first function signature / struct definition / #define
        first_sig = ""
        for line in lines[:50]:
            stripped = line.strip()
            if any(
                stripped.startswith(kw)
                for kw in (
                    "int ", "void ", "char ", "static ", "struct ",
                    "#define ", "typedef ", "enum ", "unsigned ",
                )
            ):
                first_sig = stripped[:140]
                break

        # Extract last significant line (not comment/blank)
        last_sig = ""
        for line in reversed(lines[-40:]):
            stripped = line.strip()
            if stripped and not stripped.startswith(("//", "/*", "*", "*/")):
                last_sig = stripped[:140]
                break

        parts = [
            f"[compact:start kind=read_snipped path={saved_path} "
            f"original_chars={original_chars}]",
        ]
        if first_sig:
            parts.append(f"  signature: {first_sig}")
        if last_sig and last_sig != first_sig:
            parts.append(f"  end: {last_sig}")
        parts.append(f"  lines: {len(lines)}")

        # Include extracted facts from durable_code_facts if available
        if state is not None:
            facts = list(getattr(state, "durable_code_facts", None) or [])
            # Extract source path from the content header
            # Format: [READ(path=src/foo.c, offset=0, limit=240)]
            read_path = ""
            for line in lines[:5]:
                if "path=" in line:
                    idx = line.index("path=")
                    rest = line[idx + 5:].strip()
                    # Strip leading quotes/parens, take first token
                    rest = rest.lstrip('("\'')
                    if rest:
                        read_path = rest.split()[0].rstrip('")\',')
                        break
            if read_path and facts:
                matching = [
                    f for f in facts
                    if read_path in f or any(
                        seg in f
                        for seg in read_path.replace("/", " ").split()
                        if len(seg) > 4
                    )
                ]
                if matching:
                    parts.append("  extracted_facts:")
                    for f in matching[:5]:
                        parts.append(f"    - {f}")

        parts.append("  [re-READ if original content needed]")
        parts.append("[compact:end]")
        return "\n".join(parts)

    @classmethod
    def _serialize_content(cls, content: Any) -> str:
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, ensure_ascii=False, indent=2, default=str)
        except Exception:
            return str(content)

    @classmethod
    def _preview_parts(cls, text: str) -> tuple[str, str]:
        head = text[: cls.PREVIEW_HEAD_CHARS].strip()
        tail = text[-cls.PREVIEW_TAIL_CHARS :].strip() if text else ""
        return head, tail

    def _llm_summarize_tool_output(self, content: str) -> str | None:
        """Use LLM to extract key facts from a large tool output.

        Returns a summary string or None on failure. Only called when
        CYBERGYM_LLM_COMPACT=1 and self.llm is set.
        """
        if not self.llm:
            return None
        try:
            # Cap input to avoid excessive token cost
            input_text = content[:20_000]
            response = self.llm.invoke(
                f"{_TOOL_SUMMARY_PROMPT}\n\n---\n{input_text}\n---"
            )
            summary = str(getattr(response, "content", response) or "").strip()
            if not summary:
                return None
            # Cap summary length
            if len(summary) > 2_000:
                summary = summary[:2_000]
            return summary
        except Exception:
            return None

    def _persist_snip_payload(
        self,
        *,
        content: str,
        step_id: int,
        ordinal: int,
        role: str,
        state: Any = None,
        index_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        root = self._snip_root(state)
        step_dir = root / f"step-{step_id:04d}"
        step_dir.mkdir(parents=True, exist_ok=True)
        path = step_dir / f"{role}-{ordinal:04d}.txt"
        path.write_text(content, encoding="utf-8")
        display_path = self._display_path(path, state=state)
        self._append_project_index(
            state=state,
            kind="tool_result",
            path=display_path,
            step_id=step_id,
            original_chars=len(content),
            metadata=index_metadata,
        )
        return display_path

    @classmethod
    def _workspace_root(cls, state: Any = None) -> Path | None:
        if state is not None:
            raw = str(getattr(state, "workspace_root", "") or "").strip()
            if raw:
                return Path(raw).expanduser().resolve()
        return None

    @classmethod
    def _project_root(cls, state: Any = None) -> Path:
        workspace = cls._workspace_root(state)
        if workspace is not None:
            return workspace / cls.PROJECT_ARTIFACT_ROOT
        return Path.cwd() / cls.PROJECT_ARTIFACT_ROOT

    @classmethod
    def _snip_root(cls, state: Any = None) -> Path:
        return cls._project_root(state) / "tool_results"

    @classmethod
    def _display_path(cls, path: Path, state: Any = None) -> str:
        workspace = cls._workspace_root(state)
        if workspace is None:
            return str(path)
        try:
            return str(path.resolve().relative_to(workspace))
        except Exception:
            return str(path)

    @classmethod
    def _append_project_index(
        cls,
        *,
        state: Any = None,
        kind: str,
        path: str,
        step_id: int,
        original_chars: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            project_root = cls._project_root(state)
            project_root.mkdir(parents=True, exist_ok=True)
            index_path = project_root / "INDEX.md"
            if not index_path.exists():
                index_path.write_text(
                    "# Externalized Context Index\n\n"
                    "Paths below are relative to the task workspace.\n",
                    encoding="utf-8",
                )
            attrs: Dict[str, Any] = {
                "kind": kind,
                "step": int(step_id),
                **dict(metadata or {}),
                "path": path,
                "chars": int(original_chars),
            }
            line = "- " + " ".join(
                f"{key}={cls._format_index_value(value)}"
                for key, value in attrs.items()
                if value not in (None, "")
            ) + "\n"
            if line.rstrip("\n") in index_path.read_text(encoding="utf-8").splitlines():
                return
            with index_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception:
            return

    @classmethod
    def _index_metadata_for_message(
        cls, message: HistoryMessage, serialized: str
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        tool_name = str(
            getattr(message, "name", None)
            or message.metadata.get("tool_name")
            or ""
        ).strip()
        if tool_name:
            metadata["tool"] = tool_name
        try:
            payload = json.loads(serialized)
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            return metadata
        status = str(payload.get("status") or "").strip()
        if status:
            metadata["status"] = status
        source_path = str(payload.get("path") or "").strip()
        if source_path:
            metadata["source_path"] = source_path
        if "offset" in payload and payload.get("offset") is not None:
            metadata["offset"] = payload.get("offset")
        if "limit" in payload and payload.get("limit") is not None:
            metadata["limit"] = payload.get("limit")
        if "total_lines" in payload and payload.get("total_lines") is not None:
            metadata["total_lines"] = payload.get("total_lines")
        if "has_more" in payload and payload.get("has_more") is not None:
            metadata["has_more"] = payload.get("has_more")
        command = str(payload.get("command") or "").strip()
        if command:
            metadata["command_preview"] = cls._compact_one_line(command, 160)
        return metadata

    @classmethod
    def _format_index_value(cls, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value)
        if text and all(ch.isalnum() or ch in "._/:@+-" for ch in text):
            return text
        return json.dumps(text, ensure_ascii=False)

    @staticmethod
    def _compact_one_line(text: str, limit: int) -> str:
        line = " ".join(str(text or "").split())
        if len(line) <= limit:
            return line
        return line[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _render_snipped_message(
        *,
        saved_path: str,
        original_chars: int,
        preview_head: str,
        preview_tail: str,
    ) -> str:
        return (
            f"[compact:start kind=tool_result path={saved_path} original_chars={original_chars}]\n"
            "preview_head:\n"
            f"{preview_head or '[empty]'}\n"
            "preview_tail:\n"
            f"{preview_tail or '[empty]'}\n"
            "[compact:end]"
        )


# ---------------------------------------------------------------------------
# Level 3: Collapse
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

MAX_CONSECUTIVE_COMPACTION_FAILURES = 3


class CompactionCircuitBreaker:
    """Stop compaction after 3 consecutive failures.

    Prevents the compress-expand-recompress cycle that can occur when
    compaction repeatedly fails to free enough space.
    """

    def __init__(self):
        self._consecutive_failures: int = 0
        self._state: str = "closed"  # closed | half_open | open

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= MAX_CONSECUTIVE_COMPACTION_FAILURES:
            self._state = "open"

    def should_attempt(self) -> bool:
        return self._state != "open"


# ---------------------------------------------------------------------------
# Post-Compact File Restoration
# ---------------------------------------------------------------------------

POST_COMPACT_MAX_FILES_TO_RESTORE = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 10_000


class PostCompactRestorer:
    """Restore critical files after compaction.

    After AutoCompact, restores the vulnerability description, current PoC draft,
    and last error trace as system-role messages so the agent doesn't lose
    critical context during the verification phase.
    """

    def restore(
        self,
        state: Any,
        compacted_messages: List[HistoryMessage],
    ) -> List[HistoryMessage]:
        """Restore critical context from state into compacted messages."""
        from .state import CyberGymState

        if not isinstance(state, CyberGymState):
            return compacted_messages

        restored: List[HistoryMessage] = []
        budget_remaining = POST_COMPACT_TOKEN_BUDGET

        # Always restore: vulnerability description
        if state.vulnerability_description:
            vuln_msg = HistoryMessage(
                role="system",
                content=(
                    f"[Restored context] Vulnerability description:\n"
                    f"{state.vulnerability_description}"
                ),
                step_id=0,
                metadata={
                    "source": "post_compact_restore",
                    "restored_file": "description",
                },
            )
            restored.append(vuln_msg)
            budget_remaining -= len(state.vulnerability_description) // 4

        # Restore the first ready PoC if one exists.
        ready_paths = [
            str(getattr(candidate, "file_path", "") or "").strip()
            for candidate in list(getattr(state, "ready_pocs", []) or [])
            if str(getattr(candidate, "file_path", "") or "").strip()
        ]
        if ready_paths and budget_remaining > 0:
            ready_path = ready_paths[0]
            poc_content = self._read_truncated(ready_path, state)
            if poc_content:
                restored.append(
                    HistoryMessage(
                        role="system",
                        content=(
                            f"[Restored context] Ready PoC ({ready_path}):\n"
                            f"{poc_content}"
                        ),
                        step_id=0,
                        metadata={
                            "source": "post_compact_restore",
                            "restored_file": "ready_poc",
                        },
                    )
                )

        # Restore last error trace if it exists
        if state.last_error_trace and budget_remaining > 0:
            max_chars = POST_COMPACT_MAX_TOKENS_PER_FILE * 4
            trace_content = state.last_error_trace[:max_chars]
            restored.append(
                HistoryMessage(
                    role="system",
                    content=f"[Restored context] Last error trace:\n{trace_content}",
                    step_id=0,
                    metadata={
                        "source": "post_compact_restore",
                        "restored_file": "error_trace",
                    },
                )
            )

        # Restore best PoC path if we have one (regression protection)
        if state.best_poc_path and budget_remaining > 0:
            restored.append(
                HistoryMessage(
                    role="system",
                    content=(
                        f"[Restored context] Best PoC so far: {state.best_poc_path} "
                        f"(score={state.best_poc_score})"
                    ),
                    step_id=0,
                    metadata={
                        "source": "post_compact_restore",
                        "restored_file": "best_poc",
                    },
                )
            )

        # Restore last submit_poc result (high-value feedback)
        if state.last_verification_result and budget_remaining > 0:
            result = state.last_verification_result
            vul_exit = result.get("vul_exit_code")
            crash_type = getattr(state, "crash_type", "") or ""
            crash_loc = getattr(state, "crash_location", "") or ""
            submit_summary = f"vul_exit={vul_exit}"
            if crash_type:
                submit_summary += f", crash_type={crash_type}"
            if crash_loc:
                submit_summary += f", crash_location={crash_loc}"
            restored.append(
                HistoryMessage(
                    role="system",
                    content=f"[Restored context] Last submit_poc result: {submit_summary}",
                    step_id=0,
                    metadata={
                        "source": "post_compact_restore",
                        "restored_file": "submit_result",
                    },
                )
            )

        # Restore input format model summary
        if hasattr(state, "input_format") and state.input_format and state.input_format.format_type and budget_remaining > 0:
            fmt = state.input_format
            fmt_parts = [f"format={fmt.format_type}"]
            if fmt.entry_point:
                status = "confirmed" if fmt.confirmed else "inferred"
                fmt_parts.append(f"entry={fmt.entry_point} ({status})")
            if fmt.input_path:
                fmt_parts.append(f"input_via={fmt.input_path}")
            if fmt.magic_bytes:
                fmt_parts.append(f"magic={fmt.magic_bytes}")
            restored.append(
                HistoryMessage(
                    role="system",
                    content=f"[Restored context] Input format: {' | '.join(fmt_parts)}",
                    step_id=0,
                    metadata={
                        "source": "post_compact_restore",
                        "restored_file": "input_format",
                    },
                )
            )

        # Restore investigation brief summary after compaction
        # This ensures the agent doesn't lose its confirmed understanding
        # of the vulnerability, sinks, and constraints.
        if budget_remaining > 0:
            brief_parts = []
            # Sinks
            confirmed_sinks = state.confirmed_sink_candidates() if hasattr(state, "confirmed_sink_candidates") else []
            if confirmed_sinks:
                sink_strs = [f"`{s.function}` @{s.location} (conf={s.confidence:.1f})" for s in confirmed_sinks[:3]]
                brief_parts.append(f"Confirmed sinks: {'; '.join(sink_strs)}")
            # Harness
            if getattr(state, "harness_entry_confirmed", False):
                hcs = list(getattr(state, "harness_candidates", []) or [])
                if hcs:
                    hc = hcs[0]
                    brief_parts.append(f"Harness: `{hc.entry_function or 'entry'}` @{hc.source_path}:{hc.line}")
            # Crash type
            crash_type = str(getattr(state, "crash_type", "") or "").strip()
            if crash_type and crash_type != "UNSET":
                brief_parts.append(f"Crash type: {crash_type}")
            # Gates
            open_gates = state.open_gates() if hasattr(state, "open_gates") else []
            if open_gates:
                gate_strs = [f"{g.gate_type}: {g.required_condition or g.description}" for g in open_gates[:3]]
                brief_parts.append(f"Open gates: {'; '.join(gate_strs)}")
            # PoC attempts
            poc_attempts = int(getattr(state, "poc_attempts", 0) or 0)
            consecutive = int(getattr(state, "consecutive_misses", 0) or 0)
            if poc_attempts > 0:
                brief_parts.append(f"PoC attempts: {poc_attempts} ({consecutive} consecutive NO_TRIGGER)")
            # Key feedback facts (crash_type, crash_location from ASAN)
            feedback_facts = list(getattr(state, "durable_feedback_facts", []) or [])
            if feedback_facts:
                fact_strs = [str(f or "").strip()[:80] for f in feedback_facts[-3:]]
                brief_parts.append(f"Feedback facts: {'; '.join(f for f in fact_strs if len(f) > 3)}")
            # Key code facts (function signatures, buffer sizes)
            code_facts = list(getattr(state, "durable_code_facts", []) or [])
            if code_facts:
                fact_strs = [str(f or "").strip()[:80] for f in code_facts[-3:]]
                brief_parts.append(f"Code facts: {'; '.join(f for f in fact_strs if len(f) > 3)}")

            if brief_parts:
                # Force full brief regeneration on next observation
                meta = getattr(state, "metadata", {}) or {}
                meta.pop("_obs_last_sections", None)
                meta.pop("_obs_last_events", None)
                meta["_obs_last_step"] = -1  # Force full refresh
                restored.append(
                    HistoryMessage(
                        role="system",
                        content=f"[Restored context] Investigation brief: {' | '.join(brief_parts)}",
                        step_id=0,
                        metadata={
                            "source": "post_compact_restore",
                            "restored_file": "v13_brief",
                        },
                    )
                )

        return [*compacted_messages, *restored]

    def _read_truncated(self, poc_path: str, state: Any) -> str:
        """Attempt to read the PoC file, truncated to token budget."""
        max_chars = POST_COMPACT_MAX_TOKENS_PER_FILE * 4
        try:
            from pathlib import Path

            p = Path(poc_path)
            if not p.is_absolute() and hasattr(state, "workspace_root"):
                p = Path(state.workspace_root) / poc_path
            if p.exists():
                content = p.read_text(encoding="utf-8", errors="replace")
                return content[:max_chars]
        except Exception:
            pass
        # Fallback: return a reference instead of content
        return f"[PoC file at {poc_path} -- could not read content for restoration]"


# ---------------------------------------------------------------------------
# CyberGym-specific compaction prompt
# ---------------------------------------------------------------------------

CYBERGYM_COMPACT_PROMPT = """Summarize the prior input PoC generation interaction.
Preserve these sections concisely:

1. **Vulnerability**: Bug type, affected component, trigger condition
2. **Investigation findings**: Vulnerable files, functions, data flow path
3. **Failed attempts**: What PoC strategies were tried and why they failed
4. **Current PoC state**: Current draft, last error trace, last verification result
5. **Next step**: What the agent should do next

Discard raw file contents, verbose shell output, and redundant search results.
Keep function signatures, error traces, and verification server responses.
CRITICAL: Preserve exact numeric values — buffer sizes, field offsets, magic numbers,
constant names with values, array dimensions, struct sizes. These are essential for
PoC construction and cannot be recovered once lost."""


# ---------------------------------------------------------------------------
# CyberGymContextHistory -- the full pipeline
# ---------------------------------------------------------------------------

class CyberGymContextHistory(CompactHistory):
    """Context management with four-level progressive compaction for CyberGym.

    Extends QitOS CompactHistory with:
    - Level 1 Snip (pre-compaction pass replacing old tool results with markers)
    - Level 3 Collapse (proactive restructuring at 90% utilization)
    - Circuit breaker for compaction failures
    - Post-compact restoration of critical context
    """

    KEEP_RECENT_TURNS_UNCOMPRESSED = 10
    KEEP_INITIAL_TURNS_UNCOMPRESSED = 3
    SPAN_HIGHLIGHT_LIMIT = 8
    SPAN_EVIDENCE_LIMIT = 16
    SPAN_EVIDENCE_RANGES_PER_PATH = 6
    SPAN_INDEX_LIMIT = 12
    SPAN_BLOCK_MAX_CHARS = 5000
    SPAN_SUMMARY_MIN_NEW_STEPS = 30
    SNIP_THRESHOLD_TOKENS = 80_000
    MICROCOMPACT_THRESHOLD_TOKENS = 95_000
    SEGMENT_SUMMARY_THRESHOLD_TOKENS = 115_000
    SPAN_REPLACEMENT_THRESHOLD_TOKENS = 150_000
    SPAN_REPLACEMENT_PROJECTED_THRESHOLD_TOKENS = 170_000
    SPAN_REPLACEMENT_COOLDOWN_STEPS = 60
    MICROCOMPACT_ASSISTANT_CHARS = 8_000
    MICROCOMPACT_MESSAGE_CHARS = 40_000
    SEGMENT_REPLACEMENT_STEP_COUNT = 8

    def __init__(
        self,
        llm=None,
        *,
        disable_snip: bool = False,
        disable_compaction: bool = False,
        span_summary_provider: Any = None,
        **kwargs,
    ):
        super().__init__(llm=llm, **kwargs)
        self.disable_snip = bool(disable_snip)
        self.disable_compaction = bool(disable_compaction)
        self.span_summary_provider = span_summary_provider
        self.snip_compactor = SnipCompactor(keep_recent=0)
        self.circuit_breaker = CompactionCircuitBreaker()
        self.post_compact_restorer = PostCompactRestorer()
        self._state_ref: Any = None  # set during agent init
        self._last_span_replacement_step: int = -10_000

    def set_state(self, state: Any) -> None:
        """Set a reference to the CyberGymState for post-compact restoration."""
        self._state_ref = state

    def _filter_messages(self, query: Dict[str, Any]) -> List[HistoryMessage]:
        items = list(self._messages)
        roles = query.get("roles")
        step_min = query.get("step_min")
        step_max = query.get("step_max")
        if roles:
            role_set = {str(x) for x in roles}
            items = [
                m
                for m in items
                if m.role in role_set
                or self._is_span_compaction(m)
            ]
        if step_min is not None:
            items = [m for m in items if m.step_id >= int(step_min)]
        if step_max is not None:
            items = [m for m in items if m.step_id <= int(step_max)]
        return items

    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> List[HistoryMessage]:
        query = query or {}
        items = self._filter_messages(query)
        raw_budget = query.get("max_tokens")
        budget = int(raw_budget) if raw_budget is not None else int(self.config.max_tokens)
        configured_budget = int(self.config.max_tokens)
        if configured_budget > 0:
            budget = min(budget, configured_budget)

        if self.disable_compaction:
            self._pending_runtime_events = []
            self._last_message_metadata = [self._controller._metadata_for_message(m) for m in items]
            return items

        pending = str(query.get("pending_content") or "")
        before_tokens, counting_mode = self._count_messages_with_pending(items, pending)
        snip_threshold = self._effective_threshold(
            budget=budget,
            configured=self.SNIP_THRESHOLD_TOKENS,
            fallback_ratio=0.40,
            cap_ratio=0.80,
        )
        micro_threshold = self._effective_threshold(
            budget=budget,
            configured=self.MICROCOMPACT_THRESHOLD_TOKENS,
            fallback_ratio=float(self.config.warning_ratio),
            cap_ratio=0.90,
        )
        segment_threshold = self._effective_threshold(
            budget=budget,
            configured=self.SEGMENT_SUMMARY_THRESHOLD_TOKENS,
            fallback_ratio=float(self.config.warning_ratio),
            cap_ratio=0.88,
        )
        span_threshold = self._effective_threshold(
            budget=budget,
            configured=self.SPAN_REPLACEMENT_THRESHOLD_TOKENS,
            fallback_ratio=float(self.config.warning_ratio),
            cap_ratio=0.92,
        )
        span_projected_threshold = self._effective_threshold(
            budget=budget,
            configured=self.SPAN_REPLACEMENT_PROJECTED_THRESHOLD_TOKENS,
            fallback_ratio=0.95,
            cap_ratio=0.95,
        )
        older_items, recent_items, keep_steps = self._split_recent_turns(items)
        raw_older_items = list(older_items)
        effective_state = state or self._state_ref
        if not older_items:
            self._pending_runtime_events = [
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "within_budget",
                        "before_tokens": before_tokens,
                        "after_tokens": before_tokens,
                        "saved_tokens": 0,
                        "budget": budget,
                        "pending_tokens": self._count_text_tokens(pending)[0],
                        "messages_before": len(items),
                        "messages_after": len(items),
                        "strategy": "compact_history",
                        "warning_ratio": float(self.config.warning_ratio),
                        "reason": "recent_turns_preserved",
                    },
                }
            ]
            self._last_message_metadata = [self._controller._metadata_for_message(m) for m in items]
            return items

        events: List[Dict[str, Any]] = []
        current_items = list(items)
        current_older = list(older_items)
        current_tokens = before_tokens
        pending_tokens = self._count_text_tokens(pending)[0]

        if not self.disable_snip and current_tokens >= snip_threshold:
            snipped_older = self.snip_compactor.snip(
                current_older,
                state=effective_state,
                predicate=self._should_snip_message,
            )
            if not self._same_message_contents(current_older, snipped_older):
                current_items = self._merge_old_recent(
                    original_items=current_items,
                    older_replacements=snipped_older,
                    recent_items=recent_items,
                    keep_steps=keep_steps,
                )
                current_older = snipped_older
                after_snip_tokens = self._count_messages_with_pending(
                    current_items, pending
                )[0]
                events.append(
                    self._runtime_event(
                        stage="snip_applied",
                        before_tokens=current_tokens,
                        after_tokens=after_snip_tokens,
                        budget=budget,
                        pending_tokens=pending_tokens,
                        messages_before=len(items),
                        messages_after=len(current_items),
                        reason="old_tool_results_snipped",
                        extra={
                            "snip_threshold": snip_threshold,
                            "threshold_chars": int(
                                getattr(
                                    self.config,
                                    "compact_long_messages_over_chars",
                                    0,
                                )
                                or 0
                            ),
                            "counting_mode": counting_mode,
                        },
                    )
                )
                current_tokens = after_snip_tokens

        # --- Early READ snipping (Level 0.5) ---
        # Aggressively snip old normal-priority READ messages even when
        # below the regular snip threshold.  High/critical-priority READs
        # (parser/field/seed paths) are protected and never snipped here.
        if EARLY_READ_SNIP and not self.disable_snip and current_older:
            read_snipped_older = self.snip_compactor.snip_reads_early(
                current_older,
                keep_recent=3,
                state=effective_state,
            )
            if not self._same_message_contents(current_older, read_snipped_older):
                current_items = self._merge_old_recent(
                    original_items=current_items,
                    older_replacements=read_snipped_older,
                    recent_items=recent_items,
                    keep_steps=keep_steps,
                )
                current_older = read_snipped_older
                after_read_snip_tokens = self._count_messages_with_pending(
                    current_items, pending
                )[0]
                events.append(
                    self._runtime_event(
                        stage="early_read_snip_applied",
                        before_tokens=current_tokens,
                        after_tokens=after_read_snip_tokens,
                        budget=budget,
                        pending_tokens=pending_tokens,
                        messages_before=len(items),
                        messages_after=len(current_items),
                        reason="old_read_messages_snipped_early",
                        extra={
                            "early_read_keep_recent": 3,
                        },
                    )
                )
                current_tokens = after_read_snip_tokens

        if current_tokens < micro_threshold:
            reason = (
                "below_microcompact_threshold_after_snip"
                if events
                else "below_microcompact_threshold"
            )
            events.append(
                self._runtime_event(
                    stage="within_budget",
                    before_tokens=before_tokens,
                    after_tokens=current_tokens,
                    budget=budget,
                    pending_tokens=pending_tokens,
                    messages_before=len(items),
                    messages_after=len(current_items),
                    reason=reason,
                    extra={
                        "microcompact_threshold": micro_threshold,
                        "counting_mode": counting_mode,
                    },
                )
            )
            self._pending_runtime_events = events
            if events and self._should_persist_compacted_history(query, len(items)):
                self._messages = list(current_items)
            self._last_message_metadata = [self._controller._metadata_for_message(m) for m in current_items]
            return current_items

        micro_older = self._microcompact_older_messages(current_older, state=effective_state)
        if not self._same_message_contents(current_older, micro_older):
            current_items = self._merge_old_recent(
                original_items=current_items,
                older_replacements=micro_older,
                recent_items=recent_items,
                keep_steps=keep_steps,
            )
            current_older = micro_older
            after_micro_tokens = self._count_messages_with_pending(
                current_items, pending
            )[0]
            events.append(
                self._runtime_event(
                    stage="microcompact_applied",
                    before_tokens=current_tokens,
                    after_tokens=after_micro_tokens,
                    budget=budget,
                    pending_tokens=pending_tokens,
                    messages_before=len(items),
                    messages_after=len(current_items),
                    reason="single_message_microcompact",
                    extra={
                        "microcompact_threshold": micro_threshold,
                        "counting_mode": counting_mode,
                    },
                )
            )
            current_tokens = after_micro_tokens

        if current_tokens < segment_threshold:
            events.append(
                self._runtime_event(
                    stage="within_budget",
                    before_tokens=before_tokens,
                    after_tokens=current_tokens,
                    budget=budget,
                    pending_tokens=pending_tokens,
                    messages_before=len(items),
                    messages_after=len(current_items),
                    reason="below_segment_summary_threshold",
                    extra={
                        "segment_summary_threshold": segment_threshold,
                        "counting_mode": counting_mode,
                    },
                )
            )
            self._pending_runtime_events = events
            if events and self._should_persist_compacted_history(query, len(items)):
                self._messages = list(current_items)
            self._last_message_metadata = [self._controller._metadata_for_message(m) for m in current_items]
            return current_items

        if current_older and all(self._is_span_compaction(msg) for msg in current_older):
            self._pending_runtime_events = [
                {
                    "stage": "context_history",
                    "context": {
                        "stage": "within_budget",
                        "before_tokens": current_tokens,
                        "after_tokens": current_tokens,
                        "saved_tokens": 0,
                        "budget": budget,
                        "pending_tokens": pending_tokens,
                        "messages_before": len(current_items),
                        "messages_after": len(current_items),
                        "strategy": "compact_history",
                        "warning_ratio": float(self.config.warning_ratio),
                        "reason": "existing_span_preserved",
                    },
                }
            ]
            self._last_message_metadata = [self._controller._metadata_for_message(m) for m in current_items]
            return current_items

        events.append(
            self._runtime_event(
                stage="segment_summary_ready",
                before_tokens=current_tokens,
                after_tokens=current_tokens,
                budget=budget,
                pending_tokens=pending_tokens,
                messages_before=len(items),
                messages_after=len(current_items),
                reason="segment_summary_threshold_reached",
                extra={"segment_summary_threshold": segment_threshold},
            )
        )

        current_step = self._max_step_id(current_items)
        hard_budget_exceeded = budget > 0 and current_tokens + pending_tokens >= budget
        hard_budget_critical = (
            budget >= 10_000
            and current_tokens + pending_tokens >= int(budget * 1.10)
        )
        span_allowed = (
            hard_budget_exceeded
            or current_tokens >= span_threshold
            or current_tokens + pending_tokens >= span_projected_threshold
        )
        cooldown_remaining = max(
            0,
            self.SPAN_REPLACEMENT_COOLDOWN_STEPS
            - max(0, current_step - self._last_span_replacement_step),
        )
        if not span_allowed or (cooldown_remaining > 0 and not hard_budget_critical):
            events.append(
                self._runtime_event(
                    stage="within_budget",
                    before_tokens=before_tokens,
                    after_tokens=current_tokens,
                    budget=budget,
                    pending_tokens=pending_tokens,
                    messages_before=len(items),
                    messages_after=len(current_items),
                    reason="below_span_replacement_threshold" if not span_allowed else "span_replacement_cooldown",
                    extra={
                        "span_replacement_threshold": span_threshold,
                        "hard_budget_exceeded": hard_budget_exceeded,
                        "hard_budget_critical": hard_budget_critical,
                        "cooldown_remaining": cooldown_remaining,
                    },
                )
            )
            self._pending_runtime_events = events
            if events and self._should_persist_compacted_history(query, len(items)):
                self._messages = list(current_items)
            self._last_message_metadata = [self._controller._metadata_for_message(m) for m in current_items]
            return current_items

        compact_blocks = self._build_segment_compaction_blocks(
            original_older_items=raw_older_items,
            snipped_older_items=list(current_older),
            state=effective_state,
        )
        if not compact_blocks:
            events.append(
                self._runtime_event(
                    stage="compact_skipped",
                    before_tokens=before_tokens,
                    after_tokens=current_tokens,
                    budget=budget,
                    pending_tokens=pending_tokens,
                    messages_before=len(items),
                    messages_after=len(current_items),
                    reason="missing_segment_summary",
                )
            )
            self._pending_runtime_events = events
            self._last_message_metadata = [self._controller._metadata_for_message(m) for m in current_items]
            return current_items

        result = self._merge_with_compaction_blocks(
            original_items=current_items,
            compact_blocks=compact_blocks,
            recent_items=recent_items,
            keep_steps=keep_steps,
        )
        after_tokens = self._count_messages_with_pending(result, pending)[0]
        events.append(
            self._runtime_event(
                stage="span_replacement_applied",
                before_tokens=current_tokens,
                after_tokens=after_tokens,
                budget=budget,
                pending_tokens=pending_tokens,
                messages_before=len(current_items),
                messages_after=len(result),
                reason="segment_span_replacement",
                extra={
                    "segments_compacted": len(compact_blocks),
                    "span_replacement_threshold": span_threshold,
                    "hard_budget_exceeded": hard_budget_exceeded,
                    "hard_budget_critical": hard_budget_critical,
                    "cooldown_steps": self.SPAN_REPLACEMENT_COOLDOWN_STEPS,
                },
            )
        )
        self._last_span_replacement_step = current_step
        if self._should_persist_compacted_history(query, len(items)):
            self._messages = list(result)
        self._pending_runtime_events = events
        self._last_message_metadata = [self._controller._metadata_for_message(m) for m in result]
        return result

    @staticmethod
    def _should_persist_compacted_history(query: Dict[str, Any], item_count: int) -> bool:
        if "step_min" in query:
            return False
        try:
            max_items = int(query.get("max_items", 0) or 0)
        except Exception:
            max_items = 0
        if max_items > 0 and max_items < int(item_count):
            return False
        roles = query.get("roles")
        if roles is None:
            return True
        try:
            role_set = {str(role) for role in roles}
        except Exception:
            return False
        return {"user", "assistant", "tool"}.issubset(role_set)

    @classmethod
    def _runtime_event(
        cls,
        *,
        stage: str,
        before_tokens: int,
        after_tokens: int,
        budget: int,
        pending_tokens: int,
        messages_before: int,
        messages_after: int,
        reason: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context = {
            "stage": stage,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": max(0, before_tokens - after_tokens),
            "budget": budget,
            "pending_tokens": pending_tokens,
            "messages_before": messages_before,
            "messages_after": messages_after,
            "strategy": "compact_history",
            "reason": reason,
        }
        context.update(dict(extra or {}))
        return {"stage": "context_history", "context": context}

    @staticmethod
    def _effective_threshold(
        *,
        budget: int,
        configured: int,
        fallback_ratio: float,
        cap_ratio: Optional[float] = None,
    ) -> int:
        """Use production thresholds, bounded by the active history budget.

        Absolute thresholds are useful for stable prompt-cache behavior, but they
        must never sit above the history budget. If they do, compaction can keep
        returning an over-budget message list until the provider rejects it.
        """
        configured = int(configured)
        budget = int(budget)
        if budget > 0 and budget < 10_000:
            return max(1, int(budget * float(fallback_ratio)))
        if budget > 0 and configured >= budget:
            ratio = float(cap_ratio if cap_ratio is not None else fallback_ratio)
            return max(1, int(budget * ratio))
        return configured

    @staticmethod
    def _same_message_contents(
        left: List[HistoryMessage],
        right: List[HistoryMessage],
    ) -> bool:
        if len(left) != len(right):
            return False
        return all(a is b or (a.content == b.content and a.metadata == b.metadata) for a, b in zip(left, right))

    def _should_snip_message(self, message: HistoryMessage) -> bool:
        text = str(message.content or "")
        chars = len(text)
        if chars <= 0:
            return False
        # Priority-based compaction: protect critical messages
        priority = str(
            message.metadata.get("compaction_priority", "normal")
        ).lower()
        if priority == "critical" and chars < 20_000:
            return False
        if priority == "high" and chars < 8_000:
            return False
        configured = int(getattr(self.config, "compact_long_messages_over_chars", 0) or 0)
        if configured > 0 and chars >= configured:
            return True
        tool_name = str(
            getattr(message, "name", None)
            or message.metadata.get("tool_name")
            or ""
        ).strip().upper()
        line_count = text.count("\n") + 1
        if tool_name == "READ":
            # Normal-priority (exploratory) READs become snippable much
            # earlier than high-priority (parser/field/seed) READs.
            if priority == "normal":
                return chars >= 15_000 or line_count >= 300
            return chars >= 40_000 or line_count >= 800
        if tool_name == "BASH":
            return chars >= 40_000
        if tool_name == "SUBMIT_POC":
            return chars >= 12_000
        return chars >= 40_000

    @staticmethod
    def _merge_old_recent(
        *,
        original_items: List[HistoryMessage],
        older_replacements: List[HistoryMessage],
        recent_items: List[HistoryMessage],
        keep_steps: set[int],
    ) -> List[HistoryMessage]:
        recent_iter = iter(recent_items)
        older_iter = iter(older_replacements)
        merged: List[HistoryMessage] = []
        for msg in original_items:
            try:
                step = int(getattr(msg, "step_id", 0) or 0)
            except Exception:
                step = 0
            if step in keep_steps:
                merged.append(next(recent_iter))
            else:
                merged.append(next(older_iter))
        return merged

    def _microcompact_older_messages(
        self,
        messages: List[HistoryMessage],
        *,
        state: Any = None,
    ) -> List[HistoryMessage]:
        result: List[HistoryMessage] = []
        for msg in messages:
            result.append(self._microcompact_message(msg, state=state))
        return result

    def _microcompact_message(self, message: HistoryMessage, *, state: Any = None) -> HistoryMessage:
        if message.metadata.get("compaction_mode") or message.metadata.get("summary"):
            return message
        text = str(message.content or "")
        if not text:
            return message
        role = str(message.role or "")
        should_compact = False
        kind = "message_micro"
        if role == "assistant" and len(text) >= self.MICROCOMPACT_ASSISTANT_CHARS:
            should_compact = True
            kind = "assistant_micro"
        elif role in {"tool", "observation"} and len(text) >= self.MICROCOMPACT_MESSAGE_CHARS:
            should_compact = True
            kind = "tool_micro"
        if not should_compact:
            return message

        if role == "assistant":
            compact_text = self._assistant_micro_summary(text, state=state)
        else:
            preview = max(60, int(getattr(self.config, "microcompact_preview_chars", 180) or 180))
            head = text[:preview].rstrip()
            tail = text[-min(preview, len(text)) :].lstrip()
            compact_text = head if head == tail else f"{head}\n...\n{tail}"
        content = (
            f"[compact:start kind={kind} step={message.step_id} original_chars={len(text)}]\n"
            f"{compact_text.strip() or '[empty]'}\n"
            "[compact:end]"
        )
        metadata = dict(message.metadata)
        metadata.update(
            {
                "compacted": True,
                "compaction_mode": "micro",
                "original_chars": len(text),
                "source": metadata.get("source", "cybergym_microcompact"),
            }
        )
        return HistoryMessage(
            role=message.role,
            content=content,
            step_id=message.step_id,
            tool_calls=[dict(x) for x in list(message.tool_calls or [])],
            tool_call_id=message.tool_call_id,
            name=message.name,
            metadata=metadata,
        )

    def _assistant_micro_summary(self, text: str, *, state: Any = None) -> str:
        provider = getattr(self, "span_summary_provider", None)
        if provider is not None:
            try:
                summary = provider(
                    original_older_items=[
                        HistoryMessage(role="assistant", content=text, step_id=0)
                    ],
                    state=state,
                )
                coerced = self._compact_summary_text(summary)
                if coerced:
                    return coerced
            except Exception:
                pass
        head = self._compact_line(text[:900], 420)
        tail = self._compact_line(text[-900:], 420)
        if head == tail:
            return f"Assistant reasoning summary: {head}"
        return f"Assistant reasoning summary: {head} ... {tail}"

    @classmethod
    def _is_span_compaction(cls, message: HistoryMessage) -> bool:
        return str(message.metadata.get("compaction_mode") or "") in {
            "span_micro",
            "segment_span",
        }

    @staticmethod
    def _max_step_id(messages: List[HistoryMessage]) -> int:
        best = 0
        for msg in messages:
            try:
                best = max(best, int(getattr(msg, "step_id", 0) or 0))
            except Exception:
                continue
        return best

    def _build_segment_compaction_blocks(
        self,
        *,
        original_older_items: List[HistoryMessage],
        snipped_older_items: List[HistoryMessage],
        state: Any = None,
    ) -> List[HistoryMessage]:
        blocks: List[HistoryMessage] = []
        for original_segment, snipped_segment in self._split_segment_item_pairs(
            original_older_items,
            snipped_older_items,
        ):
            block = self._build_span_compaction_block(
                original_older_items=original_segment,
                snipped_older_items=snipped_segment,
                state=state,
                compaction_mode="segment_span",
                compact_kind="history_segment",
            )
            if not block.metadata.get("has_model_summary"):
                return []
            blocks.append(block)
        return blocks

    def _split_segment_item_pairs(
        self,
        original_items: List[HistoryMessage],
        snipped_items: List[HistoryMessage],
    ) -> List[tuple[List[HistoryMessage], List[HistoryMessage]]]:
        items = list(zip(original_items, snipped_items))
        step_ids = sorted({self._message_span_bounds(original)[0] for original, _ in items})
        if not step_ids:
            return []
        chunks = [
            set(step_ids[i : i + self.SEGMENT_REPLACEMENT_STEP_COUNT])
            for i in range(0, len(step_ids), self.SEGMENT_REPLACEMENT_STEP_COUNT)
        ]
        segments: List[tuple[List[HistoryMessage], List[HistoryMessage]]] = []
        for chunk in chunks:
            original_segment: List[HistoryMessage] = []
            snipped_segment: List[HistoryMessage] = []
            for original, snipped in items:
                start, end = self._message_span_bounds(original)
                if start in chunk or end in chunk:
                    original_segment.append(original)
                    snipped_segment.append(snipped)
            if original_segment:
                segments.append((original_segment, snipped_segment))
        return segments

    def _merge_with_compaction_blocks(
        self,
        *,
        original_items: List[HistoryMessage],
        compact_blocks: List[HistoryMessage],
        recent_items: List[HistoryMessage],
        keep_steps: set[int],
    ) -> List[HistoryMessage]:
        ranges: List[tuple[int, int, HistoryMessage]] = []
        for block in compact_blocks:
            ranges.append(
                (
                    int(block.metadata.get("summarized_step_start") or 0),
                    int(block.metadata.get("summarized_step_end") or 0),
                    block,
                )
            )
        recent_iter = iter(recent_items)
        inserted: set[int] = set()
        merged: List[HistoryMessage] = []
        for msg in original_items:
            try:
                step = int(getattr(msg, "step_id", 0) or 0)
            except Exception:
                step = 0
            if step in keep_steps:
                merged.append(next(recent_iter))
                continue
            matched = next(((start, end, block) for start, end, block in ranges if start <= step <= end), None)
            if matched is None:
                continue
            start, _end, block = matched
            if start not in inserted:
                merged.append(block)
                inserted.add(start)
        return merged

    def _maybe_snip_below_warning(
        self,
        *,
        items: List[HistoryMessage],
        older_items: List[HistoryMessage],
        recent_items: List[HistoryMessage],
        keep_steps: set[int],
        state: Any = None,
    ) -> Optional[List[HistoryMessage]]:
        if self.disable_snip:
            return None
        threshold = int(getattr(self.config, "compact_long_messages_over_chars", 0) or 0)
        if threshold <= 0:
            return None
        should_snip = any(
            msg.role in self.snip_compactor.COMPRESSIBLE_ROLES
            and not msg.metadata.get("snipped")
            and not msg.metadata.get("summary")
            and len(str(msg.content or "")) >= threshold
            for msg in older_items
        )
        if not should_snip:
            return None
        snipped_older = self.snip_compactor.snip(older_items, state=state)
        if all(a is b or a.content == b.content for a, b in zip(older_items, snipped_older)):
            return None
        recent_iter = iter(recent_items)
        older_iter = iter(snipped_older)
        merged: List[HistoryMessage] = []
        for msg in items:
            try:
                step = int(getattr(msg, "step_id", 0) or 0)
            except Exception:
                step = 0
            if step in keep_steps:
                merged.append(next(recent_iter))
            else:
                merged.append(next(older_iter))
        return merged

    def _count_messages_with_pending(
        self,
        messages: List[HistoryMessage],
        pending: Any = "",
    ) -> tuple[int, str]:
        """Count history plus pending text with the same counter used by engine telemetry."""
        history_tokens, history_mode = self._count_message_tokens(messages)
        pending_tokens, pending_mode = self._count_text_tokens(pending)
        return history_tokens + pending_tokens, self._merge_counting_modes(
            [history_mode, pending_mode]
        )

    def _count_message_tokens(self, messages: List[HistoryMessage]) -> tuple[int, str]:
        counter = getattr(self.llm, "count_tokens", None)
        if callable(counter):
            try:
                value = counter([self._count_payload_for_message(msg) for msg in messages])
                if isinstance(value, int) and value >= 0:
                    return int(value), "model_count"
            except Exception:
                pass
        return self._estimate_tokens_local(messages), "local_estimate"

    def _count_text_tokens(self, text: Any) -> tuple[int, str]:
        counter = getattr(self.llm, "count_tokens", None)
        if callable(counter):
            try:
                value = counter(str(text or ""))
                if isinstance(value, int) and value >= 0:
                    return int(value), "model_count"
            except Exception:
                pass
        return self._estimate_text_tokens_local(text), "local_estimate"

    @staticmethod
    def _merge_counting_modes(modes: List[str]) -> str:
        cleaned = [str(mode) for mode in modes if mode and str(mode) != "disabled"]
        if not cleaned:
            return "disabled"
        if "provider_usage" in cleaned:
            return "provider_usage"
        if "model_count" in cleaned:
            return "model_count"
        return cleaned[0]

    @staticmethod
    def _count_payload_for_message(message: HistoryMessage) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "role": str(message.role or ""),
            "content": message.content,
        }
        payload["_step_id"] = int(getattr(message, "step_id", 0) or 0)
        if message.tool_calls:
            payload["tool_calls"] = [
                dict(x)
                for x in list(message.tool_calls or [])
                if isinstance(x, dict)
            ]
        if message.tool_call_id:
            payload["tool_call_id"] = str(message.tool_call_id)
        if message.name:
            payload["name"] = str(message.name)
        return payload

    def _estimate_tokens_local(self, messages: List[HistoryMessage]) -> int:
        """Estimate token count for a list of messages when no model counter is available."""
        total = 0
        for m in messages:
            text = str(m.content or "")
            total += max(1, len(text) // 4) if text else 0
        return total

    @staticmethod
    def _estimate_text_tokens_local(text: Any) -> int:
        raw = str(text or "")
        if not raw:
            return 0
        return max(1, len(raw) // 4)

    def evict(self) -> int:
        # Preserve the full message chain; CyberGym compacts content, not rounds.
        return 0

    @classmethod
    def _split_recent_turns(
        cls,
        items: List[HistoryMessage],
    ) -> tuple[List[HistoryMessage], List[HistoryMessage], set[int]]:
        step_ids: List[int] = []
        for msg in items:
            try:
                step = int(getattr(msg, "step_id", 0) or 0)
            except Exception:
                continue
            step_ids.append(step)
        if not step_ids:
            return [], list(items), set()
        distinct_steps = sorted(set(step_ids))
        keep_steps = set(distinct_steps[: cls.KEEP_INITIAL_TURNS_UNCOMPRESSED])
        keep_steps.update(distinct_steps[-cls.KEEP_RECENT_TURNS_UNCOMPRESSED :])
        older: List[HistoryMessage] = []
        recent: List[HistoryMessage] = []
        for msg in items:
            try:
                step = int(getattr(msg, "step_id", 0) or 0)
            except Exception:
                step = 0
            if step in keep_steps:
                recent.append(msg)
            else:
                older.append(msg)
        return older, recent, keep_steps

    @staticmethod
    def _merge_with_compaction_block(
        *,
        original_items: List[HistoryMessage],
        compact_block: HistoryMessage,
        recent_items: List[HistoryMessage],
        keep_steps: set[int],
    ) -> List[HistoryMessage]:
        recent_iter = iter(recent_items)
        merged: List[HistoryMessage] = []
        block_inserted = False
        for msg in original_items:
            try:
                step = int(getattr(msg, "step_id", 0) or 0)
            except Exception:
                step = 0
            if step in keep_steps:
                merged.append(next(recent_iter))
            elif not block_inserted:
                merged.append(compact_block)
                block_inserted = True
        return merged

    def _build_span_compaction_block(
        self,
        *,
        original_older_items: List[HistoryMessage],
        snipped_older_items: List[HistoryMessage],
        state: Any = None,
        compaction_mode: str = "span_micro",
        compact_kind: str = "history_span",
    ) -> HistoryMessage:
        spans = [self._message_span_bounds(msg) for msg in original_older_items]
        step_start = min(start for start, _ in spans) if spans else 0
        step_end = max(end for _, end in spans) if spans else 0
        original_chars = sum(self._message_original_chars(msg) for msg in original_older_items)
        index_path = PROJECT_ARTIFACT_ROOT / "INDEX.md"
        evidence_memory = self._evidence_memory_lines(
            original_older_items=original_older_items,
            snipped_older_items=snipped_older_items,
        )
        carried_summary, carried_summary_path, carried_summary_end = self._previous_model_summary(
            original_older_items
        )
        should_reuse_summary = bool(
            carried_summary
            and carried_summary_path
            and step_end - carried_summary_end < self.SPAN_SUMMARY_MIN_NEW_STEPS
        )
        if should_reuse_summary:
            model_summary = carried_summary
            summary_path = carried_summary_path
        else:
            model_summary = self._span_model_summary(
                original_older_items=original_older_items,
                state=state,
            ) or carried_summary
            summary_path = carried_summary_path if model_summary == carried_summary else ""
        if model_summary and not summary_path:
            summary_path = self._persist_span_summary(
                summary=model_summary,
                step_start=step_start,
                step_end=step_end,
                original_chars=original_chars,
                state=state,
            )
        highlights = self._span_highlights(original_older_items)
        evidence_paths, evidence_total = self._index_line_sample(
            state=state,
            limit=self.SPAN_INDEX_LIMIT,
        )
        snipped_paths = [
            str(msg.metadata.get("snip_saved_path"))
            for msg in snipped_older_items
            if msg.metadata.get("snip_saved_path")
        ]
        if not evidence_paths and snipped_paths:
            evidence_paths = [
                f"- path={path}" for path in snipped_paths[-self.SPAN_INDEX_LIMIT :]
            ]
            evidence_total = len(snipped_paths)

        lines = [
            (
                f"[compact:start kind={compact_kind} steps={step_start}..{step_end} "
                f"messages={len(original_older_items)} original_chars={original_chars}]"
            ),
            "Older interaction segment was compacted into this marker.",
            "Recent turns remain verbatim after this block.",
            f"Complete Raw Evidence Index: `{index_path.as_posix()}` ({evidence_total} entries)",
            "Use READ on exact relative paths from the index only when original text is needed.",
        ]
        if evidence_memory:
            lines.append("Evidence Memory:")
            lines.extend(evidence_memory)
        if model_summary:
            lines.append("Model Summary:")
            lines.extend(self._summary_lines(model_summary))
            if summary_path:
                lines.append(f"Summary File: `{summary_path}`")
        if highlights:
            lines.append("Highlights:")
            lines.extend(f"- {line}" for line in highlights)
        if evidence_paths:
            lines.append(
                f"Externalized Evidence Sample (last {len(evidence_paths)} of {evidence_total}):"
            )
            lines.extend(evidence_paths)
        lines.append("[compact:end]")
        content = "\n".join(lines)
        if len(content) > self.SPAN_BLOCK_MAX_CHARS:
            keep = max(500, self.SPAN_BLOCK_MAX_CHARS - 80)
            content = content[:keep].rstrip() + "\n[compact:end]"

        return HistoryMessage(
            role="system",
            content=content,
            step_id=step_end,
            metadata={
                "compacted": True,
                "compaction_mode": compaction_mode,
                "original_chars": original_chars,
                "summarized_message_count": len(original_older_items),
                "summarized_step_start": step_start,
                "summarized_step_end": step_end,
                "has_model_summary": bool(model_summary),
                "summary_path": summary_path,
                "source": "cybergym_span_compaction",
            },
        )

    def _persist_span_summary(
        self,
        *,
        summary: str,
        step_start: int,
        step_end: int,
        original_chars: int,
        state: Any = None,
    ) -> str:
        try:
            root = SnipCompactor._project_root(state) / "summaries"
            root.mkdir(parents=True, exist_ok=True)
            path = root / f"summary-step-{int(step_end):04d}.md"
            content = (
                f"# Compact Summary steps {int(step_start)}..{int(step_end)}\n\n"
                f"{str(summary or '').strip()}\n"
            )
            path.write_text(content, encoding="utf-8")
            display_path = SnipCompactor._display_path(path, state=state)
            SnipCompactor._append_project_index(
                state=state,
                kind="summary",
                path=display_path,
                step_id=int(step_end),
                original_chars=int(original_chars),
                metadata={"steps": f"{int(step_start)}..{int(step_end)}"},
            )
            return display_path
        except Exception:
            return ""

    def _span_model_summary(
        self,
        *,
        original_older_items: List[HistoryMessage],
        state: Any = None,
    ) -> str:
        provider = getattr(self, "span_summary_provider", None)
        if provider is not None:
            try:
                return self._compact_summary_text(
                    provider(original_older_items=original_older_items, state=state)
                )
            except Exception:
                return ""
        llm = getattr(self, "llm", None)
        if llm is None:
            if int(getattr(self.config, "max_tokens", 0) or 0) < 10_000:
                return self._heuristic_span_summary(original_older_items)
            return ""
        prompt = self._build_span_summary_prompt(original_older_items, state=state)
        try:
            if hasattr(llm, "call_raw"):
                response = llm.call_raw([{"role": "user", "content": prompt}])
            else:
                response = llm([{"role": "user", "content": prompt}])
            return self._compact_summary_text(self._coerce_llm_text(response))
        except Exception:
            return ""

    def _heuristic_span_summary(self, messages: List[HistoryMessage]) -> str:
        highlights = self._span_highlights(messages)
        if highlights:
            return self._compact_summary_text(" ".join(highlights[:4]))
        steps = [str(getattr(msg, "step_id", 0)) for msg in messages[-6:]]
        return self._compact_summary_text(
            "Heuristic test summary for compacted steps " + ", ".join(steps)
        )

    def _build_span_summary_prompt(
        self,
        messages: List[HistoryMessage],
        *,
        state: Any = None,
    ) -> str:
        task = str(getattr(state, "vulnerability_description", "") or getattr(state, "task", "") or "")
        rendered: List[str] = []
        for msg in messages[-80:]:
            content = self._compact_line(str(msg.content or ""), 900)
            if not content:
                continue
            rendered.append(
                f"[step={getattr(msg, 'step_id', 0)} role={msg.role}] {content}"
            )
        return (
            "Summarize the older exploit-development interaction for future continuation.\n"
            "Keep durable facts only. Do not invent details.\n"
            "Sections: Task Facts, Read Coverage, Search Coverage, Exploit Hypotheses, "
            "Attempts And Feedback, Do Not Reread, Next Best Actions.\n\n"
            f"Task:\n{self._compact_line(task, 1200)}\n\n"
            "Older interaction:\n" + "\n".join(rendered)
        )

    @classmethod
    def _previous_model_summary(
        cls,
        messages: List[HistoryMessage],
    ) -> tuple[str, str, int]:
        best_summary = ""
        best_path = ""
        best_end = -1
        for msg in messages:
            if not cls._is_span_compaction(msg):
                continue
            try:
                step_end = int(msg.metadata.get("summarized_step_end") or 0)
            except Exception:
                step_end = 0
            if step_end < best_end:
                continue
            summary = cls._extract_section(str(msg.content or ""), "Model Summary:")
            summary_path = str(msg.metadata.get("summary_path") or "").strip()
            if not summary or not summary_path:
                continue
            best_summary = summary
            best_path = summary_path
            best_end = step_end
        return best_summary, best_path, max(best_end, 0)

    @staticmethod
    def _extract_section(text: str, header: str) -> str:
        lines: List[str] = []
        in_section = False
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if line == header:
                in_section = True
                continue
            if not in_section:
                continue
            if line.endswith(":") or line == "[compact:end]":
                break
            if line.startswith("Summary File:"):
                continue
            if line.startswith("- "):
                line = line[2:].strip()
            if line:
                lines.append(line)
        return "\n".join(lines).strip()

    @classmethod
    def _coerce_llm_text(cls, response: Any) -> str:
        if isinstance(response, str):
            return response
        text = getattr(response, "text", None)
        if isinstance(text, str):
            return text
        if isinstance(response, dict):
            choices = response.get("choices")
        else:
            choices = getattr(response, "choices", None)
        if isinstance(choices, list) and choices:
            choice = choices[0]
            message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
            if isinstance(message, dict):
                content = message.get("content")
            else:
                content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
        return str(response or "")

    @classmethod
    def _compact_summary_text(cls, value: Any) -> str:
        text = " ".join(str(value or "").replace("\r", "\n").split())
        return cls._compact_line(text, 1800)

    @classmethod
    def _summary_lines(cls, summary: str) -> List[str]:
        text = str(summary or "").strip()
        if not text:
            return []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 1:
            return [f"- {text}"]
        return [f"- {cls._compact_line(line, 260)}" for line in lines[:10]]

    @staticmethod
    def _message_span_bounds(message: HistoryMessage) -> tuple[int, int]:
        if CyberGymContextHistory._is_span_compaction(message):
            try:
                return (
                    int(message.metadata.get("summarized_step_start")),
                    int(message.metadata.get("summarized_step_end")),
                )
            except Exception:
                pass
        try:
            step = int(getattr(message, "step_id", 0) or 0)
        except Exception:
            step = 0
        return step, step

    @staticmethod
    def _message_original_chars(message: HistoryMessage) -> int:
        if CyberGymContextHistory._is_span_compaction(message):
            try:
                return int(message.metadata.get("original_chars"))
            except Exception:
                pass
        return len(str(message.content or ""))

    def _evidence_memory_lines(
        self,
        *,
        original_older_items: List[HistoryMessage],
        snipped_older_items: List[HistoryMessage],
    ) -> List[str]:
        lines = self._previous_evidence_memory_lines(original_older_items)
        read_records = self._read_evidence_records(
            original_older_items=original_older_items,
            snipped_older_items=snipped_older_items,
        )
        lines.extend(self._render_read_evidence_lines(read_records))

        deduped: List[str] = []
        seen: set[str] = set()
        for line in lines:
            normalized = " ".join(str(line or "").split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped[-self.SPAN_EVIDENCE_LIMIT :]

    @staticmethod
    def _previous_evidence_memory_lines(messages: List[HistoryMessage]) -> List[str]:
        carried: List[str] = []
        for msg in messages:
            if not CyberGymContextHistory._is_span_compaction(msg):
                continue
            in_section = False
            for raw_line in str(msg.content or "").splitlines():
                line = raw_line.strip()
                if line == "Evidence Memory:":
                    in_section = True
                    continue
                if not in_section:
                    continue
                if not line.startswith("- "):
                    if line.endswith(":") or line == "[compact:end]":
                        break
                    continue
                carried.append(line)
        return carried

    @staticmethod
    def _read_evidence_records(
        *,
        original_older_items: List[HistoryMessage],
        snipped_older_items: List[HistoryMessage],
    ) -> Dict[str, Dict[str, Any]]:
        records: Dict[str, Dict[str, Any]] = {}
        for original, snipped in zip(original_older_items, snipped_older_items):
            if str(original.role) != "tool":
                continue
            tool_name = str(
                getattr(original, "name", None)
                or original.metadata.get("tool_name")
                or ""
            ).strip()
            if tool_name and tool_name.upper() != "READ":
                continue
            try:
                payload = json.loads(str(original.content or ""))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            source_path = str(payload.get("path") or "").strip()
            if not source_path:
                continue
            record = records.setdefault(
                source_path,
                {
                    "ranges": [],
                    "artifacts": [],
                    "total_lines": None,
                    "has_more": None,
                },
            )
            offset = payload.get("offset")
            limit = payload.get("limit")
            range_text = "full" if offset in (None, "") else f"{offset}+{limit or '?'}"
            if range_text not in record["ranges"]:
                record["ranges"].append(range_text)
            saved_path = str(snipped.metadata.get("snip_saved_path") or "").strip()
            if saved_path and saved_path not in record["artifacts"]:
                record["artifacts"].append(saved_path)
            if payload.get("total_lines") is not None:
                record["total_lines"] = payload.get("total_lines")
            if payload.get("has_more") is not None:
                record["has_more"] = payload.get("has_more")
        return records

    @classmethod
    def _render_read_evidence_lines(
        cls, records: Dict[str, Dict[str, Any]]
    ) -> List[str]:
        lines: List[str] = []
        for source_path, record in records.items():
            ranges = list(record.get("ranges") or [])[-cls.SPAN_EVIDENCE_RANGES_PER_PATH :]
            artifacts = list(record.get("artifacts") or [])
            parts = [
                f"- READ `{source_path}`",
                f"ranges={', '.join(ranges) if ranges else 'unknown'}",
            ]
            if record.get("total_lines") is not None:
                parts.append(f"total_lines={record.get('total_lines')}")
            if record.get("has_more") is not None:
                parts.append(
                    f"has_more={'true' if bool(record.get('has_more')) else 'false'}"
                )
            if artifacts:
                parts.append(f"latest_raw=`{artifacts[-1]}`")
            lines.append(cls._compact_line("; ".join(parts), 420))
        return lines

    def _span_highlights(self, messages: List[HistoryMessage]) -> List[str]:
        highlights: List[str] = self._previous_highlight_lines(messages)
        for msg in messages:
            if self._is_span_compaction(msg):
                continue
            content = str(msg.content or "").strip()
            if not content:
                continue
            extracted = self._extract_recorded_highlight(content)
            if extracted:
                highlights.append(extracted)
                continue
            if str(msg.role) == "assistant":
                lowered = content.lower()
                if any(
                    token in lowered
                    for token in (
                        "key insight",
                        "vulnerability",
                        "trigger",
                        "failed",
                        "success",
                        "no crash",
                        "next",
                    )
                ):
                    highlights.append(self._compact_line(content, 220))
        deduped: List[str] = []
        seen: set[str] = set()
        for item in highlights:
            normalized = " ".join(item.split())
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped[-self.SPAN_HIGHLIGHT_LIMIT :]

    @staticmethod
    def _previous_highlight_lines(messages: List[HistoryMessage]) -> List[str]:
        carried: List[str] = []
        for msg in messages:
            if not CyberGymContextHistory._is_span_compaction(msg):
                continue
            in_section = False
            for raw_line in str(msg.content or "").splitlines():
                line = raw_line.strip()
                if line == "Highlights:":
                    in_section = True
                    continue
                if not in_section:
                    continue
                if not line.startswith("- "):
                    if line.endswith(":") or line == "[compact:end]":
                        break
                    continue
                carried.append(line[2:].strip())
        return carried

    @classmethod
    def _extract_recorded_highlight(cls, content: str) -> str:
        try:
            obj = json.loads(content)
        except Exception:
            return ""
        recorded = obj.get("recorded") if isinstance(obj, dict) else None
        if not isinstance(recorded, dict):
            return ""
        parts: List[str] = []
        strategy = str(recorded.get("strategy_family") or "").strip()
        poc_path = str(recorded.get("poc_path") or "").strip()
        observed = str(recorded.get("observed_result") or "").strip()
        next_hypothesis = str(recorded.get("next_hypothesis") or "").strip()
        summary = str(recorded.get("summary") or "").strip()
        if strategy or poc_path:
            parts.append(f"attempt {strategy or 'unknown'} {poc_path}".strip())
        if observed:
            parts.append(f"observed: {observed}")
        if summary:
            parts.append(f"reflection: {summary}")
        if next_hypothesis:
            parts.append(f"next: {next_hypothesis}")
        return cls._compact_line("; ".join(parts), 260)

    @staticmethod
    def _compact_line(text: str, limit: int) -> str:
        line = " ".join(str(text or "").split())
        if len(line) <= limit:
            return line
        return line[: max(0, limit - 3)].rstrip() + "..."

    @classmethod
    def _recent_index_lines(cls, *, state: Any = None, limit: int = 12) -> List[str]:
        lines, _ = cls._index_line_sample(state=state, limit=limit)
        return lines

    @classmethod
    def _index_line_sample(
        cls,
        *,
        state: Any = None,
        limit: int = 12,
    ) -> tuple[List[str], int]:
        try:
            index_path = SnipCompactor._project_root(state) / "INDEX.md"
            lines = [
                line.strip()
                for line in index_path.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("- ")
            ]
            return lines[-max(0, int(limit)) :], len(lines)
        except Exception:
            return [], 0
