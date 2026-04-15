"""Lightweight multimodal core types and message helpers for QitOS."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, is_dataclass
import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, cast


SUPPORTED_CONTENT_TYPES = {"text", "image_url", "image_base64", "image_file"}


@dataclass(frozen=True)
class ContentBlock:
    """Canonical content block for multimodal model input."""

    type: str
    text: Optional[str] = None
    url: Optional[str] = None
    data: Optional[str] = None
    path: Optional[str] = None
    mime_type: Optional[str] = None
    detail: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "type": self.type,
            "text": self.text,
            "url": self.url,
            "data": self.data,
            "path": self.path,
            "mime_type": self.mime_type,
            "detail": self.detail,
            "metadata": dict(self.metadata),
        }
        return {k: v for k, v in payload.items() if v not in (None, {}, [])}


@dataclass(frozen=True)
class MessageEnvelope:
    """One normalized model message with text and/or visual blocks."""

    role: str
    content_blocks: List[ContentBlock] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": [block.to_dict() for block in self.content_blocks],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class GroundingMetadata:
    """Cross-link OCR/DOM/UI candidate grounding metadata for GUI observations."""

    boxes: List[Dict[str, Any]] = field(default_factory=list)
    ocr_spans: List[Dict[str, Any]] = field(default_factory=list)
    dom_refs: List[Dict[str, Any]] = field(default_factory=list)
    ui_refs: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObservationPack:
    """Normalized multimodal environment observation."""

    text: str = ""
    screenshot: Optional[Dict[str, Any]] = None
    dom: Optional[Any] = None
    accessibility_tree: Optional[Any] = None
    ui_candidates: List[Dict[str, Any]] = field(default_factory=list)
    ocr: List[Dict[str, Any]] = field(default_factory=list)
    grounding_metadata: Optional[GroundingMetadata] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "text": self.text,
            "screenshot": dict(self.screenshot) if isinstance(self.screenshot, dict) else None,
            "dom": self.dom,
            "accessibility_tree": self.accessibility_tree,
            "ui_candidates": list(self.ui_candidates),
            "ocr": list(self.ocr),
            "grounding_metadata": (
                self.grounding_metadata.to_dict()
                if isinstance(self.grounding_metadata, GroundingMetadata)
                else self.grounding_metadata
            ),
            "metadata": dict(self.metadata),
        }
        return {
            key: value
            for key, value in payload.items()
            if value not in (None, "", [], {})
        }


@dataclass(frozen=True)
class VisualTraceAsset:
    """Visual artifact tracked in trace/qita."""

    kind: str
    path: str
    mime_type: str = "image/png"
    width: Optional[int] = None
    height: Optional[int] = None
    source_step: Optional[int] = None
    grounding_refs: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "kind": self.kind,
            "path": self.path,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "source_step": self.source_step,
            "grounding_refs": list(self.grounding_refs),
            "metadata": dict(self.metadata),
        }
        return {k: v for k, v in payload.items() if v not in (None, [], {})}


@dataclass(frozen=True)
class ActionSpace:
    """Lightweight action vocabulary contract for multimodal environments."""

    id: str
    allowed_actions: List[str] = field(default_factory=list)
    required_args: Dict[str, List[str]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self, action: Mapping[str, Any]) -> Dict[str, Any]:
        action_name = str(
            action.get("action_type") or action.get("name") or ""
        ).strip()
        args = (
            dict(action.get("args") or {})
            if isinstance(action.get("args"), Mapping)
            else {}
        )
        errors: List[str] = []
        if not action_name:
            errors.append("missing action name")
        if self.allowed_actions and action_name not in set(self.allowed_actions):
            errors.append(f"unsupported action: {action_name}")
        for key in self.required_args.get(action_name, []):
            value = args.get(key)
            if value in (None, ""):
                errors.append(f"missing required arg `{key}`")
        return {
            "ok": not errors,
            "action_name": action_name,
            "errors": errors,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "allowed_actions": list(self.allowed_actions),
            "required_args": {k: list(v) for k, v in self.required_args.items()},
            "metadata": dict(self.metadata),
        }


class EnvironmentAdapter(ABC):
    """Minimal adapter contract for benchmark-facing multimodal environments."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable adapter identifier."""

    @abstractmethod
    def capabilities(self) -> Dict[str, Any]:
        """Return environment capabilities relevant to the benchmark/runtime."""

    @abstractmethod
    def action_space(self) -> ActionSpace:
        """Return the action space used for validation and replay."""

    @abstractmethod
    def reset(self, task: Any, workspace: Optional[str] = None) -> Any:
        """Reset one task episode."""

    @abstractmethod
    def observe(self, state: Any = None) -> Any:
        """Return current observation."""

    @abstractmethod
    def step(self, action: Mapping[str, Any], state: Any = None) -> Any:
        """Execute one normalized action."""


def text_block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": str(text or "")}


def image_url_block(
    url: str,
    *,
    detail: Optional[str] = None,
    mime_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    block = {
        "type": "image_url",
        "url": str(url or ""),
        "metadata": dict(metadata or {}),
    }
    if detail:
        block["detail"] = str(detail)
    if mime_type:
        block["mime_type"] = str(mime_type)
    return block


def image_base64_block(
    data: str,
    *,
    mime_type: str = "image/png",
    detail: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    block = {
        "type": "image_base64",
        "data": str(data or ""),
        "mime_type": str(mime_type or "image/png"),
        "metadata": dict(metadata or {}),
    }
    if detail:
        block["detail"] = str(detail)
    return block


def image_file_block(
    path: str,
    *,
    mime_type: Optional[str] = None,
    detail: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    block = {
        "type": "image_file",
        "path": str(path or ""),
        "metadata": dict(metadata or {}),
    }
    if mime_type:
        block["mime_type"] = str(mime_type)
    if detail:
        block["detail"] = str(detail)
    return block


def normalize_content_block(block: Any) -> Dict[str, Any]:
    if is_dataclass(block):
        block = asdict(cast(Any, block))
    if isinstance(block, str):
        return text_block(block)
    if not isinstance(block, Mapping):
        return text_block(str(block))
    block_type = str(block.get("type") or "text").strip().lower()
    if block_type not in SUPPORTED_CONTENT_TYPES and block_type != "text":
        return text_block(str(block.get("text") or block))
    if block_type == "text":
        return text_block(str(block.get("text") or ""))
    normalized: Dict[str, Any] = {"type": block_type}
    for key in ("text", "url", "data", "path", "mime_type", "detail"):
        value = block.get(key)
        if value not in (None, ""):
            normalized[key] = str(value)
    metadata = block.get("metadata")
    if isinstance(metadata, Mapping) and metadata:
        normalized["metadata"] = dict(metadata)
    return normalized


def normalize_message(message: Mapping[str, Any]) -> Dict[str, Any]:
    role = str(message.get("role") or "user").strip() or "user"
    content = message.get("content")
    payload: Dict[str, Any] = {"role": role}
    for key, value in message.items():
        if key in {"role", "content"}:
            continue
        payload[str(key)] = value
    if isinstance(content, list):
        payload["content"] = [normalize_content_block(block) for block in content]
        return payload
    if isinstance(content, Mapping) and str(content.get("type") or "").strip():
        payload["content"] = [normalize_content_block(content)]
        return payload
    if content is None and role in {"assistant", "tool"}:
        payload["content"] = None
        return payload
    payload["content"] = str(content or "")
    return payload


def normalize_messages(messages: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_message(message) for message in messages if isinstance(message, Mapping)]


def has_nontext_content(message: Mapping[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        str(normalize_content_block(block).get("type") or "text") != "text"
        for block in content
    )


def content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for raw in content:
            block = normalize_content_block(raw)
            block_type = str(block.get("type") or "text")
            if block_type == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    parts.append(text)
                continue
            if block_type in {"image_url", "image_base64", "image_file"}:
                source = (
                    block.get("url")
                    or block.get("path")
                    or ("data:" if block.get("data") else "")
                )
                label = f"[{block_type}: {source}]".strip()
                parts.append(label)
                continue
            parts.append(str(block))
        return "\n".join(part for part in parts if part)
    if isinstance(content, Mapping):
        return content_to_text([content])
    return str(content)


def message_to_text(message: Mapping[str, Any]) -> str:
    role = str(message.get("role") or "user").strip()
    return f"{role}: {content_to_text(message.get('content'))}"


def guess_mime_type(path: str, fallback: str = "image/png") -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or fallback


def file_to_data_url(path: str, mime_type: Optional[str] = None) -> str:
    file_path = Path(path).expanduser().resolve()
    payload = base64.b64encode(file_path.read_bytes()).decode("ascii")
    mime = str(mime_type or guess_mime_type(str(file_path)))
    return f"data:{mime};base64,{payload}"


def ensure_data_url(data: str, *, mime_type: str = "image/png") -> str:
    value = str(data or "")
    if value.startswith("data:"):
        return value
    return f"data:{mime_type};base64,{value}"


def normalize_observation_pack(payload: Any) -> Optional[ObservationPack]:
    if isinstance(payload, ObservationPack):
        return payload
    if not isinstance(payload, Mapping):
        return None
    known_keys = {
        "text",
        "screenshot",
        "dom",
        "accessibility_tree",
        "ui_candidates",
        "ocr",
        "grounding_metadata",
        "metadata",
    }
    if not any(key in payload for key in known_keys):
        return None
    grounding = payload.get("grounding_metadata")
    grounding_obj = None
    if isinstance(grounding, GroundingMetadata):
        grounding_obj = grounding
    elif isinstance(grounding, Mapping):
        grounding_obj = GroundingMetadata(
            boxes=list(grounding.get("boxes") or []),
            ocr_spans=list(grounding.get("ocr_spans") or []),
            dom_refs=list(grounding.get("dom_refs") or []),
            ui_refs=list(grounding.get("ui_refs") or []),
            metadata=dict(grounding.get("metadata") or {}),
        )
    return ObservationPack(
        text=str(payload.get("text") or ""),
        screenshot=dict(payload.get("screenshot") or {})
        if isinstance(payload.get("screenshot"), Mapping)
        else None,
        dom=payload.get("dom"),
        accessibility_tree=payload.get("accessibility_tree"),
        ui_candidates=list(payload.get("ui_candidates") or []),
        ocr=list(payload.get("ocr") or []),
        grounding_metadata=grounding_obj,
        metadata=dict(payload.get("metadata") or {}),
    )


def observation_modalities(payload: Any) -> List[str]:
    pack = normalize_observation_pack(payload)
    if pack is None:
        return []
    kinds: List[str] = []
    if pack.text:
        kinds.append("text")
    if pack.screenshot:
        kinds.append("screenshot")
    if pack.dom is not None:
        kinds.append("dom")
    if pack.accessibility_tree is not None:
        kinds.append("accessibility_tree")
    if pack.ocr:
        kinds.append("ocr")
    if pack.ui_candidates:
        kinds.append("ui_candidates")
    if pack.grounding_metadata is not None:
        kinds.append("grounding_metadata")
    return kinds


def observation_visual_assets(
    payload: Any, *, source_step: Optional[int] = None
) -> List[Dict[str, Any]]:
    pack = normalize_observation_pack(payload)
    if pack is None or not isinstance(pack.screenshot, dict):
        return []
    screenshot = dict(pack.screenshot)
    path = str(screenshot.get("path") or "").strip()
    url = str(screenshot.get("url") or "").strip()
    location = path or url
    if not location:
        return []
    asset = VisualTraceAsset(
        kind="screenshot",
        path=location,
        mime_type=str(
            screenshot.get("mime_type")
            or guess_mime_type(path or url, fallback="image/png")
        ),
        width=(
            int(screenshot["width"])
            if screenshot.get("width") is not None
            else None
        ),
        height=(
            int(screenshot["height"])
            if screenshot.get("height") is not None
            else None
        ),
        source_step=source_step,
        grounding_refs=(
            pack.grounding_metadata.to_dict().get("boxes", [])
            if isinstance(pack.grounding_metadata, GroundingMetadata)
            else []
        ),
        metadata={
            key: value
            for key, value in screenshot.items()
            if key not in {"path", "url", "mime_type", "width", "height"}
        },
    )
    return [asset.to_dict()]


__all__ = [
    "ContentBlock",
    "MessageEnvelope",
    "ObservationPack",
    "GroundingMetadata",
    "VisualTraceAsset",
    "ActionSpace",
    "EnvironmentAdapter",
    "SUPPORTED_CONTENT_TYPES",
    "text_block",
    "image_url_block",
    "image_base64_block",
    "image_file_block",
    "normalize_content_block",
    "normalize_message",
    "normalize_messages",
    "has_nontext_content",
    "content_to_text",
    "message_to_text",
    "guess_mime_type",
    "file_to_data_url",
    "ensure_data_url",
    "normalize_observation_pack",
    "observation_modalities",
    "observation_visual_assets",
]
