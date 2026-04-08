"""Default-first prompt building for model-native protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Dict, List, Optional


PROMPT_BUILDER_VERSION = "v1"
DEFAULT_SECTION_ORDER = [
    "persona",
    "task_policy",
    "tool_usage_hint",
    "tool_schema",
    "output_contract",
    "examples",
    "repair_feedback",
    "continuation_feedback",
    "extra",
]
FRAMEWORK_OWNED_SECTIONS = {
    "tool_schema",
    "output_contract",
    "repair_feedback",
    "continuation_feedback",
}


@dataclass(frozen=True)
class PromptSection:
    kind: str
    content: str
    owner: str = "user"
    dynamic: bool = False
    delivery: str = "system"


@dataclass(frozen=True)
class PromptSpec:
    persona_prompt: str = ""
    task_policy: str = ""
    tool_usage_hint: str = ""
    extra_instructions: str = ""
    examples: str = ""
    parser_feedback: str = ""
    continuation_feedback: str = ""
    include_tool_schema: bool = True
    include_contract: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def section_map(self) -> Dict[str, str]:
        return {
            "persona": str(self.persona_prompt or "").strip(),
            "task_policy": str(self.task_policy or "").strip(),
            "tool_usage_hint": str(self.tool_usage_hint or "").strip(),
            "examples": str(self.examples or "").strip(),
            "extra": str(self.extra_instructions or "").strip(),
        }


@dataclass(frozen=True)
class PromptBuildResult:
    system_prompt_static: str = ""
    system_prompt_dynamic: str = ""
    message_injections: List[Dict[str, str]] = field(default_factory=list)
    user_content_blocks: List[Dict[str, Any]] = field(default_factory=list)
    tool_schema_payload: Optional[List[Dict[str, Any]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def system_prompt(self) -> str:
        parts = [
            str(self.system_prompt_static or "").strip(),
            str(self.system_prompt_dynamic or "").strip(),
        ]
        return "\n\n".join(part for part in parts if part)


class PromptBuilder:
    """Assemble protocol-aware prompts with stable defaults."""

    def build(
        self,
        *,
        spec: PromptSpec,
        protocol: Any,
        tool_registry: Any = None,
        llm: Any = None,
        state: Any = None,
        resolution_source: str | None = None,
    ) -> PromptBuildResult:
        delivery_requested = str(
            getattr(protocol, "tool_schema_delivery", "prompt_injection")
            or "prompt_injection"
        )
        delivery_effective = self._effective_delivery(
            delivery_requested, llm=llm, protocol=protocol
        )
        delivery_fallback_used = delivery_effective != delivery_requested
        tool_schema_payload = self._tool_schema_payload(
            tool_registry, delivery_effective
        )
        tool_schema_style = str(getattr(protocol, "id", "react_text_v1") or "react_text_v1")
        section_order = self._section_order(protocol)
        sections = spec.section_map()

        framework_sections = self._framework_sections(
            spec=spec,
            protocol=protocol,
            tool_registry=tool_registry,
            delivery=delivery_effective,
        )
        static_parts: List[str] = []
        dynamic_parts: List[str] = []
        sections_used: List[str] = []
        repair_injected = False
        continuation_injected = False
        repair_mode = str(
            getattr(protocol, "repair_injection_mode", "message_injection")
            or "message_injection"
        )
        continuation_mode = str(
            getattr(protocol, "continuation_injection_mode", "message_injection")
            or "message_injection"
        )
        message_injections: List[Dict[str, str]] = []

        for name in section_order:
            content = framework_sections.get(name) if name in FRAMEWORK_OWNED_SECTIONS else sections.get(name)
            content = str(content or "").strip()
            if not content:
                continue
            sections_used.append(name)
            if name == "repair_feedback":
                repair_injected = True
                if repair_mode == "message_injection":
                    message_injections.append({"role": "user", "content": content})
                    continue
                if repair_mode == "conversation_history_insert":
                    message_injections.append({"role": "assistant", "content": content})
                    continue
                dynamic_parts.append(content)
                continue
            if name == "continuation_feedback":
                continuation_injected = True
                if continuation_mode == "message_injection":
                    message_injections.append({"role": "user", "content": content})
                    continue
                if continuation_mode == "conversation_history_insert":
                    message_injections.append({"role": "assistant", "content": content})
                    continue
                dynamic_parts.append(content)
                continue
            static_parts.append(content)

        system_prompt_static = "\n\n".join(static_parts).strip()
        system_prompt_dynamic = "\n\n".join(dynamic_parts).strip()
        full_prompt = "\n\n".join(
            part for part in (system_prompt_static, system_prompt_dynamic) if part
        )
        metadata = {
            "protocol": getattr(protocol, "id", None),
            "protocol_resolution_source": resolution_source or "unknown",
            "prompt_builder": self.__class__.__name__,
            "prompt_builder_version": PROMPT_BUILDER_VERSION,
            "protocol_contract_version": str(
                getattr(protocol, "contract_version", "v1") or "v1"
            ),
            "sections_used": sections_used,
            "tool_schema_style": tool_schema_style,
            "tool_schema_delivery": delivery_effective,
            "tool_schema_delivery_requested": delivery_requested,
            "delivery_fallback_used": delivery_fallback_used,
            "repair_injected": repair_injected,
            "continuation_injected": continuation_injected,
            "repair_injection_mode": repair_mode,
            "continuation_injection_mode": continuation_mode,
            "prompt_hash_static": self._hash(system_prompt_static),
            "prompt_hash_full": self._hash(full_prompt),
            "estimated_tokens_static": self._estimate_tokens(llm, system_prompt_static),
            "estimated_tokens_full": self._estimate_tokens(llm, full_prompt),
            "message_injection_count": len(message_injections),
            "user_content_block_count": len(getattr(spec, "metadata", {}).get("user_content_blocks", []) or []),
            "state_kind": state.__class__.__name__ if state is not None else None,
        }
        if spec.metadata:
            metadata["spec"] = dict(spec.metadata)
        return PromptBuildResult(
            system_prompt_static=system_prompt_static,
            system_prompt_dynamic=system_prompt_dynamic,
            message_injections=message_injections,
            user_content_blocks=list(
                getattr(spec, "metadata", {}).get("user_content_blocks", []) or []
            ),
            tool_schema_payload=tool_schema_payload,
            metadata=metadata,
        )

    def _effective_delivery(self, requested: str, *, llm: Any, protocol: Any) -> str:
        delivery = str(requested or "prompt_injection")
        if delivery == "prompt_injection":
            return delivery
        supports = getattr(llm, "supports_tool_schema_delivery", None)
        if callable(supports):
            try:
                if bool(supports(delivery, protocol=protocol)):
                    return delivery
            except Exception:
                pass
        return "prompt_injection"

    def _tool_schema_payload(
        self, tool_registry: Any, delivery: str
    ) -> Optional[List[Dict[str, Any]]]:
        if delivery not in {"api_parameter", "hybrid"}:
            return None
        if tool_registry is None or not hasattr(tool_registry, "get_all_specs"):
            return None
        try:
            return list(tool_registry.get_all_specs() or [])
        except Exception:
            return None

    def _framework_sections(
        self,
        *,
        spec: PromptSpec,
        protocol: Any,
        tool_registry: Any,
        delivery: str,
    ) -> Dict[str, str]:
        sections: Dict[str, str] = {}
        if spec.include_tool_schema and delivery in {"prompt_injection", "hybrid"}:
            renderer = getattr(protocol, "tool_schema_renderer", None)
            if callable(renderer):
                try:
                    schema = str(renderer(tool_registry) or "").strip()
                except Exception:
                    schema = ""
                if schema:
                    sections["tool_schema"] = f"Available tools:\n{schema}"
        if spec.include_contract:
            contract_renderer = getattr(protocol, "contract_renderer", None)
            if callable(contract_renderer):
                try:
                    contract = str(contract_renderer(protocol) or "").strip()
                except Exception:
                    contract = ""
                if contract:
                    sections["output_contract"] = contract
        feedback = str(spec.parser_feedback or "").strip()
        if feedback:
            repair_renderer = getattr(protocol, "repair_renderer", None)
            if callable(repair_renderer):
                try:
                    sections["repair_feedback"] = str(repair_renderer(feedback) or "").strip()
                except Exception:
                    sections["repair_feedback"] = feedback
            else:
                sections["repair_feedback"] = feedback
        continuation = str(spec.continuation_feedback or "").strip()
        if continuation:
            continuation_renderer = getattr(protocol, "continuation_renderer", None)
            if callable(continuation_renderer):
                try:
                    sections["continuation_feedback"] = str(continuation_renderer(continuation) or "").strip()
                except Exception:
                    sections["continuation_feedback"] = continuation
            else:
                sections["continuation_feedback"] = continuation
        return sections

    def _section_order(self, protocol: Any) -> List[str]:
        policy = getattr(protocol, "prompt_builder_policy", None)
        if isinstance(policy, dict):
            order = policy.get("section_order")
            if isinstance(order, list) and order:
                return [str(item) for item in order]
        return list(DEFAULT_SECTION_ORDER)

    def _hash(self, value: str) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]

    def _estimate_tokens(self, llm: Any, text: str) -> int:
        if not text:
            return 0
        count_tokens = getattr(llm, "count_tokens", None)
        if callable(count_tokens):
            try:
                value = count_tokens(text)
                if isinstance(value, int):
                    return value
            except Exception:
                pass
        return max(1, len(text.split()))


__all__ = [
    "PromptBuilder",
    "PromptBuildResult",
    "PromptSection",
    "PromptSpec",
    "PROMPT_BUILDER_VERSION",
    "DEFAULT_SECTION_ORDER",
]
