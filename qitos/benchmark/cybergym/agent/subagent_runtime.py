from __future__ import annotations

import json
from typing import Any, Dict, List


class PromptMessages(list):
    def __init__(self, items: List[Dict[str, str]], rendered: str) -> None:
        super().__init__(items)
        self._rendered = rendered

    def __str__(self) -> str:
        return self._rendered

    __repr__ = __str__


def _render_prompt_value(value: Any, indent: int = 0) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: List[str] = []
        for key, item in value.items():
            rendered = _render_prompt_value(item, indent + 2)
            if "\n" in rendered:
                lines.append(f"{prefix}{key}:")
                lines.append(rendered)
            else:
                lines.append(f"{prefix}{key}: {rendered}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = [f"{prefix}- {_render_prompt_value(item, indent + 2).lstrip()}" for item in value]
        return "\n".join(lines)
    return f"{prefix}{value}"


def _render_prompt(prompt: Dict[str, Any]) -> str:
    lines = []
    for key, value in prompt.items():
        rendered = _render_prompt_value(value, 2)
        if "\n" in rendered:
            lines.append(f"{key}:")
            lines.append(rendered)
        else:
            lines.append(f"{key}: {rendered.strip()}")
    return "\n".join(lines)


def build_insight_messages(
    *,
    task_description: str,
    family_snapshot: Dict[str, Any],
    candidate_snapshot: Dict[str, Any],
    latest_feedback_raw: str,
    previous_feedback_raw: str,
    evidence_pack: Dict[str, Any],
) -> List[Dict[str, str]]:
    prompt = {
        "task_description": task_description,
        "family_snapshot": family_snapshot,
        "candidate_snapshot": candidate_snapshot,
        "latest_feedback_raw": latest_feedback_raw,
        "previous_feedback_raw": previous_feedback_raw,
        "evidence_pack": evidence_pack,
        "instruction": (
            "Return JSON with assessment, suggested_action, reason, evidence_lines, "
            "hypothesis_revision, mutation_hints, confidence, uncertainty."
        ),
    }
    messages = [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]
    return PromptMessages(messages, _render_prompt(prompt))


def build_candidate_messages(
    *,
    task_description: str,
    family_spec: Dict[str, Any],
    latest_family_feedback_raw: str,
    mutation_hints: List[str],
    evidence_pack: Dict[str, Any],
    candidate_budget: int,
) -> List[Dict[str, str]]:
    prompt = {
        "task_description": task_description,
        "family_spec": family_spec,
        "latest_family_feedback_raw": latest_family_feedback_raw,
        "mutation_hints": mutation_hints,
        "evidence_pack": evidence_pack,
        "candidate_budget": candidate_budget,
        "instruction": (
            "Return JSON with a candidates array. Each candidate must include "
            "candidate_id, family_id, file_path, mutation_summary, expected_signal, "
            "novelty_note, base_seed, generation_method, ready_to_submit."
        ),
    }
    messages = [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]
    return PromptMessages(messages, _render_prompt(prompt))


def _load_json_payload(text: str) -> Any:
    decoder = json.JSONDecoder()
    candidates = []
    stripped = str(text or "").strip()
    if stripped:
        candidates.append(stripped)
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith("```"):
                candidates.append("\n".join(lines[1:-1]).strip())
        for index, char in enumerate(stripped):
            if char not in "{[":
                continue
            candidates.append(stripped[index:])
            break
    seen = set()
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                payload, _ = decoder.raw_decode(candidate)
                return payload
            except json.JSONDecodeError:
                continue
    return json.loads(text)


def parse_insight_json(text: str) -> Dict[str, Any]:
    payload = _load_json_payload(text)
    if not isinstance(payload, dict):
        raise ValueError("Insight response must be a JSON object")
    required = [
        "assessment",
        "suggested_action",
        "reason",
        "evidence_lines",
        "hypothesis_revision",
        "mutation_hints",
        "confidence",
        "uncertainty",
    ]
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"Missing insight keys: {missing}")
    for name in ("assessment", "suggested_action", "reason", "hypothesis_revision", "confidence", "uncertainty"):
        if not isinstance(payload[name], str):
            raise ValueError(f"Insight field {name} must be a string")
    if not isinstance(payload["evidence_lines"], list) or not all(
        isinstance(line, str) for line in payload["evidence_lines"]
    ):
        raise ValueError("Insight field evidence_lines must be a list of strings")
    if not isinstance(payload["mutation_hints"], list) or not all(
        isinstance(item, str) for item in payload["mutation_hints"]
    ):
        raise ValueError("Insight field mutation_hints must be a list of strings")
    return payload


_OPTIONAL_CANDIDATE_FIELDS = {
    "producer_agent",
    "created_at",
    "artifact_ref",
    "hypothesis_ref",
    "fingerprint_mode",
    "artifact_sha256",
}


def parse_candidate_json(text: str) -> Dict[str, Any]:
    payload = _load_json_payload(text)
    if not isinstance(payload, dict):
        raise ValueError("Candidate response must be a JSON object")
    if "candidates" not in payload or not isinstance(payload["candidates"], list):
        raise ValueError("Candidate response must include a candidates list")
    for candidate in payload["candidates"]:
        if not isinstance(candidate, dict):
            raise ValueError("Each candidate must be a JSON object")
        required = (
            "candidate_id",
            "family_id",
            "file_path",
            "mutation_summary",
            "expected_signal",
            "novelty_note",
            "base_seed",
            "generation_method",
            "ready_to_submit",
        )
        missing = [name for name in required if name not in candidate]
        if missing:
            raise ValueError(f"Missing candidate keys: {missing}")
        for name in required[:-1]:
            if not isinstance(candidate[name], str):
                raise ValueError(f"Candidate field {name} must be a string")
        for name in _OPTIONAL_CANDIDATE_FIELDS:
            if name in candidate and not isinstance(candidate[name], str):
                raise ValueError(f"Candidate field {name} must be a string")
        if not isinstance(candidate["ready_to_submit"], bool):
            raise ValueError("Candidate field ready_to_submit must be a boolean")
    return payload


def _coerce_response_text(response: Any) -> str:
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
        if isinstance(choice, dict):
            message = choice.get("message")
        else:
            message = getattr(choice, "message", None)
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    text_value = block.get("text")
                    if isinstance(text_value, str):
                        parts.append(text_value)
            if parts:
                return "\n".join(parts)
    raise ValueError("Subagent response must be plain text or expose a string .text field")


def run_subagent_json(llm: Any, messages: List[Dict[str, Any]], *, use_raw: bool = True) -> str:
    if use_raw and hasattr(llm, "call_raw"):
        return _coerce_response_text(llm.call_raw(messages))
    return _coerce_response_text(llm(messages))
