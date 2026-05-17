"""Canonical tool registry with function and ToolSet support."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .tool import BaseTool, FunctionTool, ToolMeta, get_tool_meta


@dataclass
class ToolOrigin:
    source: str  # function | toolset
    toolset_name: Optional[str] = None
    toolset_version: Optional[str] = None


class ToolRegistry:
    """Registry for function tools, bound methods, tool objects, and ToolSets."""

    def __init__(self, *, auto_short_aliases: bool = True):
        self._tools: Dict[str, BaseTool] = {}
        self._origins: Dict[str, ToolOrigin] = {}
        self._toolsets: List[Any] = []
        self._setup_done: bool = False
        self._aliases: Dict[str, str] = {}
        self._normalized_map: Dict[str, str] = {}
        self.auto_short_aliases = bool(auto_short_aliases)

    def register(
        self, item: Any, name: Optional[str] = None, meta: Optional[ToolMeta] = None
    ) -> "ToolRegistry":
        tool_obj = self._to_tool(item, meta=meta)
        resolved_name = (
            name or getattr(item, "name", None) or getattr(item, "_name", None)
        )
        if resolved_name:
            tool_obj.spec.name = str(resolved_name)
        self._register_tool_object(tool_obj, origin=ToolOrigin(source="function"))
        return self

    def register_toolset(
        self, toolset: Any, namespace: Optional[str] = None
    ) -> "ToolRegistry":
        if not hasattr(toolset, "tools"):
            raise TypeError("register_toolset() expects an object with tools()")

        toolset_name = str(getattr(toolset, "name", toolset.__class__.__name__.lower()))
        toolset_version = str(getattr(toolset, "version", "0"))
        prefix = namespace if namespace is not None else toolset_name

        if toolset not in self._toolsets:
            self._toolsets.append(toolset)

        for item in toolset.tools():
            tool_obj = self._to_tool(item)
            base_name = tool_obj.spec.name
            full_name = f"{prefix}.{base_name}" if prefix else base_name
            tool_obj.spec.name = full_name
            self._register_tool_object(
                tool_obj,
                origin=ToolOrigin(
                    source="toolset",
                    toolset_name=toolset_name,
                    toolset_version=toolset_version,
                ),
            )

        return self

    def include_toolset(self, items: Any) -> "ToolRegistry":
        """Include tools, toolsets, registries, or nested collections as one bundle.

        This is the default composition-oriented API for end users. It accepts:

        - one atomic tool
        - one toolset object with ``tools()``
        - one existing ``ToolRegistry``
        - a nested list/tuple/set containing any mix of the above
        """
        for item in self._iter_toolset_items(items):
            self._include_toolset_item(item)
        return self

    def include(self, obj: Any) -> "ToolRegistry":
        if hasattr(obj, "tools") and callable(getattr(obj, "tools")):
            if obj not in self._toolsets:
                self._toolsets.append(obj)
            for item in obj.tools():
                self.register(item)
            return self
        for attr_name in dir(obj):
            if attr_name.startswith("_"):
                continue
            attr = getattr(obj, attr_name)
            if isinstance(attr, BaseTool):
                self.register(attr)
                continue
            if not callable(attr):
                continue

            meta = get_tool_meta(attr)
            if meta is not None:
                self.register(attr, meta=meta)

        return self

    def get(self, name: str) -> Optional[BaseTool]:
        resolved = self.resolve_name(name)
        if not resolved:
            return None
        return self._tools.get(resolved)

    def resolve_name(self, name: str) -> Optional[str]:
        key = str(name or "").strip()
        if not key:
            return None
        if key in self._tools:
            return key
        alias = self._aliases.get(key)
        if alias and alias in self._tools:
            return alias
        normalized = self._normalize_tool_key(key)
        if not normalized:
            return None
        resolved = self._normalized_map.get(normalized)
        if resolved and resolved in self._tools:
            return resolved
        return None

    def resolve(self, name: str) -> Optional[BaseTool]:
        resolved = self.resolve_name(name)
        return self._tools.get(resolved) if resolved else None

    def register_alias(self, alias: str, canonical_name: str) -> None:
        alias_name = str(alias or "").strip()
        canonical = str(canonical_name or "").strip()
        if not alias_name:
            raise ValueError("Alias cannot be empty")
        if canonical not in self._tools:
            raise ValueError(f"Cannot alias unknown tool: '{canonical}'")
        if alias_name in self._tools and alias_name != canonical:
            raise ValueError(f"Alias collides with existing tool name: '{alias_name}'")
        existing = self._aliases.get(alias_name)
        if existing and existing != canonical:
            raise ValueError(
                f"Alias '{alias_name}' is already bound to canonical tool '{existing}'"
            )
        self._aliases[alias_name] = canonical
        normalized_alias = self._normalize_tool_key(alias_name)
        if normalized_alias:
            self._normalized_map.setdefault(normalized_alias, canonical)

    def suggest(self, name: str, limit: int = 3) -> List[str]:
        needle = str(name or "").strip()
        if not needle:
            return []
        candidates = self.list_tools()
        normalized_candidates = {self._normalize_tool_key(x): x for x in candidates}
        normalized_needle = self._normalize_tool_key(needle)
        ordered: List[str] = []
        if normalized_needle in normalized_candidates:
            ordered.append(normalized_candidates[normalized_needle])
        close = difflib.get_close_matches(needle, candidates, n=max(1, int(limit)), cutoff=0.5)
        for item in close:
            if item not in ordered:
                ordered.append(item)
        close_norm = difflib.get_close_matches(
            normalized_needle,
            list(normalized_candidates.keys()),
            n=max(1, int(limit)),
            cutoff=0.5,
        )
        for key in close_norm:
            name_item = normalized_candidates.get(key)
            if name_item and name_item not in ordered:
                ordered.append(name_item)
        return ordered[: max(1, int(limit))]

    def list_tools(self) -> List[str]:
        return sorted(self._tools.keys())

    def list_toolsets(self) -> List[str]:
        names: List[str] = []
        for toolset in self._toolsets:
            names.append(
                str(getattr(toolset, "name", toolset.__class__.__name__.lower()))
            )
        return names

    def describe_tool(self, name: str) -> Dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Tool '{name}' not found")
        origin = self._origins.get(name, ToolOrigin(source="function"))
        return {
            "name": tool.name,
            "description": tool.spec.description,
            "prompt": tool.spec.prompt,
            "required_ops": list(tool.spec.required_ops),
            "input_schema": dict(tool.spec.input_schema or {}),
            "output_schema": dict(tool.spec.output_schema or {}),
            "read_only": bool(tool.spec.read_only),
            "concurrency_safe": bool(tool.spec.concurrency_safe),
            "requires_user_interaction": bool(tool.spec.requires_user_interaction),
            "supports_background": bool(tool.spec.supports_background),
            "result_max_chars": tool.spec.result_max_chars,
            "produces_artifact": bool(tool.spec.produces_artifact),
            "origin": {
                "source": origin.source,
                "toolset_name": origin.toolset_name,
                "toolset_version": origin.toolset_version,
            },
        }

    def call(
        self, name: str, runtime_context: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> Any:
        tool = self.get(name)
        if tool is None:
            suggestions = self.suggest(name)
            payload = {
                "status": "error",
                "error_category": "tool_not_found",
                "message": f"Tool '{name}' not found",
                "tool_name": name,
                "suggestions": suggestions,
            }
            raise ValueError(str(payload))
        return tool.execute(kwargs, runtime_context=runtime_context)

    def setup(self, context: Optional[Dict[str, Any]] = None) -> None:
        if self._setup_done:
            return
        payload = context or {}
        for toolset in self._toolsets:
            if hasattr(toolset, "setup"):
                toolset.setup(payload)
        self._setup_done = True

    def teardown(self, context: Optional[Dict[str, Any]] = None) -> None:
        payload = context or {}
        for toolset in reversed(self._toolsets):
            if hasattr(toolset, "teardown"):
                toolset.teardown(payload)
        self._setup_done = False

    def get_tool_descriptions(self, protocol: Any = None, renderer: Any = None) -> str:
        if renderer is not None:
            return str(renderer(self))
        if protocol is not None:
            try:
                from qitos.protocols import render_protocol_tool_schema

                return render_protocol_tool_schema(self, protocol)
            except Exception:
                pass
        lines: List[str] = []
        for name in self.list_tools():
            tool = self._tools[name]
            origin = self._origins.get(name, ToolOrigin(source="function"))
            lines.append(f"## {tool.name}")
            lines.append(f"Description: {tool.spec.description}")
            lines.append(f"Source: {origin.source}")
            if tool.spec.required_ops:
                lines.append(f"Required Ops: {', '.join(tool.spec.required_ops)}")
            if origin.toolset_name:
                lines.append(f"ToolSet: {origin.toolset_name}@{origin.toolset_version}")
            lines.append("Parameters:")
            for param, p_spec in tool.spec.parameters.items():
                t = p_spec.get("type", "any")
                lines.append(f"  - {param} ({t})")
            lines.append("")
        return "\n".join(lines)

    def render_tool_schema(self, protocol: Any = None, renderer: Any = None) -> str:
        return self.get_tool_descriptions(protocol=protocol, renderer=renderer)

    def get_all_specs(self) -> List[Dict[str, Any]]:
        specs = []
        for name in self.list_tools():
            tool = self._tools[name]
            origin = self._origins.get(name, ToolOrigin(source="function"))
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.spec.name,
                        "description": tool.spec.description,
                        "parameters": tool.spec.input_schema
                        or {
                            "type": "object",
                            "properties": tool.spec.parameters,
                            "required": tool.spec.required,
                        },
                        "output_schema": tool.spec.output_schema,
                    },
                    "origin": {
                        "source": origin.source,
                        "toolset_name": origin.toolset_name,
                        "toolset_version": origin.toolset_version,
                    },
                    "permissions": {
                        "filesystem_read": tool.spec.permissions.filesystem_read,
                        "filesystem_write": tool.spec.permissions.filesystem_write,
                        "network": tool.spec.permissions.network,
                        "command": tool.spec.permissions.command,
                    },
                    "required_ops": list(tool.spec.required_ops),
                    "capabilities": {
                        "read_only": bool(tool.spec.read_only),
                        "concurrency_safe": bool(tool.spec.concurrency_safe),
                        "requires_user_interaction": bool(
                            tool.spec.requires_user_interaction
                        ),
                        "supports_background": bool(tool.spec.supports_background),
                        "result_max_chars": tool.spec.result_max_chars,
                        "produces_artifact": bool(tool.spec.produces_artifact),
                    },
                }
            )
        return specs

    def _to_tool(self, item: Any, meta: Optional[ToolMeta] = None) -> BaseTool:
        if isinstance(item, BaseTool):
            return item
        if callable(item):
            return FunctionTool(item, meta=meta or get_tool_meta(item))
        raise TypeError("register() expects BaseTool or callable")

    def _register_tool_object(self, tool_obj: BaseTool, origin: ToolOrigin) -> None:
        if tool_obj.name in self._tools:
            raise ValueError(f"Tool name collision: '{tool_obj.name}'")
        self._tools[tool_obj.name] = tool_obj
        self._origins[tool_obj.name] = origin
        normalized = self._normalize_tool_key(tool_obj.name)
        if normalized and normalized not in self._normalized_map:
            self._normalized_map[normalized] = tool_obj.name
        if self.auto_short_aliases and "." in tool_obj.name:
            prefix, short_name = tool_obj.name.rsplit(".", 1)
            if short_name not in self._tools:
                try:
                    self.register_alias(short_name, tool_obj.name)
                except ValueError:
                    pass
            eq_name = f"{prefix}={short_name}"
            if eq_name not in self._tools:
                try:
                    self.register_alias(eq_name, tool_obj.name)
                except ValueError:
                    pass

    def _include_toolset_item(self, item: Any) -> None:
        if item is None:
            return
        if isinstance(item, ToolRegistry):
            self._merge_registry(item)
            return
        if isinstance(item, BaseTool) or callable(item):
            self.register(item)
            return
        if hasattr(item, "tools") and callable(getattr(item, "tools")):
            if item not in self._toolsets:
                self._toolsets.append(item)
            for nested in item.tools():
                self._include_toolset_item(nested)
            return
        raise TypeError(
            "include_toolset() accepts tools, toolsets, registries, or nested collections"
        )

    def _iter_toolset_items(self, items: Any) -> Iterable[Any]:
        if isinstance(items, (list, tuple, set)):
            for item in items:
                yield from self._iter_toolset_items(item)
            return
        yield items

    def _merge_registry(self, other: "ToolRegistry") -> None:
        for toolset in getattr(other, "_toolsets", []):
            if toolset not in self._toolsets:
                self._toolsets.append(toolset)
        for name in other.list_tools():
            tool = other.get(name)
            if tool is None:
                continue
            origin = getattr(other, "_origins", {}).get(
                name, ToolOrigin(source="function")
            )
            self._register_tool_object(tool, origin=origin)
        for alias, canonical in dict(getattr(other, "_aliases", {}) or {}).items():
            if canonical in self._tools:
                try:
                    self.register_alias(alias, canonical)
                except ValueError:
                    continue

    def __contains__(self, name: str) -> bool:
        return self.resolve_name(name) is not None

    def __len__(self) -> int:
        return len(self._tools)

    def _normalize_tool_key(self, value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return re.sub(r"[.\-_=]+", "", text)


__all__ = ["ToolOrigin", "ToolRegistry"]
