"""Content-first extraction helpers for terminal render hooks."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from rich import box
from rich.syntax import Syntax
from rich.table import Table

from .events import RenderEvent


def _is_env_result(result: Any) -> bool:
    """Return True if this result is an environment step result (not a tool result)."""
    if not isinstance(result, dict):
        return False
    metadata = result.get("metadata")
    if isinstance(metadata, dict) and str(metadata.get("source") or "").lower() == "env":
        return True
    output = result.get("output")
    return isinstance(output, dict) and set(output) == {"env"}


_THOUGHT_RE = re.compile(
    r"Thought\s*:\s*(.*?)(?:\n[A-Za-z_ ]+\s*:|\Z)", re.IGNORECASE | re.DOTALL
)
_NOISE_KEYS = {
    "latency_ms",
    "run_id",
    "error_category",
    "ts",
    "step_id",
    "phase",
    "hook",
}


class ContentFirstRenderer:
    """Extract concise thought/action/observation/memory blocks from events."""

    def __init__(self, max_preview_chars: int = 500):
        self.max_preview_chars = max(120, int(max_preview_chars))

    def task_text(self, task: str, max_steps: Optional[int] = None) -> str:
        if max_steps is None:
            return task
        return f"{task} [max_steps={max_steps}]"

    def thought_text(self, event: RenderEvent) -> Optional[str]:
        payload = event.payload or {}
        if event.node == "decision":
            rationale = payload.get("rationale")
            if isinstance(rationale, str) and rationale.strip():
                return self._truncate(rationale.strip(), self.max_preview_chars)
            return None
        if event.node == "model_output":
            # Prefer API-level reasoning_content (DeepSeek, QwQ, etc.)
            reasoning = payload.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                return self._truncate(reasoning.strip(), self.max_preview_chars)
            # Fallback: ReAct-style "Thought: ..." in raw text
            raw = payload.get("raw_output")
            if not isinstance(raw, str):
                return None
            m = _THOUGHT_RE.search(raw)
            if m:
                return self._truncate(m.group(1).strip(), self.max_preview_chars)
            return self._truncate(raw.strip(), self.max_preview_chars)
        return None

    def model_response_summary(self, event: RenderEvent) -> Optional[str]:
        payload = event.payload or {}
        response = payload.get("model_response")
        if not isinstance(response, dict):
            return None
        parts: List[str] = []
        if response.get("provider"):
            parts.append(f"provider={response.get('provider')}")
        if response.get("model_name"):
            parts.append(f"model={response.get('model_name')}")
        if response.get("finish_reason"):
            parts.append(f"finish={response.get('finish_reason')}")
        tool_calls = response.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            parts.append(f"tool_calls={len(tool_calls)}")
        usage = response.get("usage")
        if isinstance(usage, dict):
            total = usage.get("total_tokens")
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            if total is not None:
                parts.append(f"tokens={total}")
            elif prompt is not None or completion is not None:
                parts.append(f"usage={prompt or 0}/{completion or 0}")
        if not parts:
            return None
        return self._truncate(" · ".join(parts), self.max_preview_chars)

    def action_summary(self, event: RenderEvent) -> Optional[Dict[str, Any]]:
        payload = event.payload or {}
        if event.node == "planned_actions":
            actions = payload.get("actions")
            if isinstance(actions, list) and actions:
                if len(actions) == 1:
                    return self._action_from_dict(actions[0])
                summaries = [self._action_from_dict(a) for a in actions]
                labels = [s["label"] for s in summaries]
                details = [s["detail"] for s in summaries if s.get("detail")]
                has_error = any(s.get("status") == "error" for s in summaries)
                return {
                    "label": f"{len(actions)} ACTIONS",
                    "detail": " | ".join(labels) if labels else "",
                    "status": "error" if has_error else "neutral",
                    "action_count": len(actions),
                    "actions": summaries,
                }
            return None

        if event.node == "tool_invocations":
            items = payload.get("tool_invocations")
            if isinstance(items, list) and items:
                if len(items) == 1:
                    first = items[0] if isinstance(items[0], dict) else {}
                    name = str(first.get("tool_name") or first.get("name") or "tool")
                    status = str(first.get("status") or "").lower()
                    return {
                        "label": name.upper().replace("_", " "),
                        "detail": "",
                        "status": "error" if status == "error" else "success",
                    }
                names = []
                has_error = False
                for item in items:
                    d = item if isinstance(item, dict) else {}
                    name = str(d.get("tool_name") or d.get("name") or "tool")
                    names.append(name.upper().replace("_", " "))
                    if str(d.get("status") or "").lower() == "error":
                        has_error = True
                return {
                    "label": f"{len(items)} INVOCATIONS",
                    "detail": " | ".join(names),
                    "status": "error" if has_error else "success",
                    "action_count": len(items),
                    "actions": [{"label": n, "detail": "", "status": "neutral"} for n in names],
                }
            return None
        return None

    def observation_summary(self, event: RenderEvent) -> Optional[Dict[str, Any]]:
        payload = event.payload or {}
        data = (
            payload.get("observation")
            if event.node == "observation"
            else payload.get("action_results")
        )
        if data is None:
            return None

        items = data if isinstance(data, list) else [data]
        # Filter out environment results so the count matches actual tool
        # actions (N actions should produce N results, not N+1).
        items = [it for it in items if not _is_env_result(it)]
        primary = self._select_primary_observation(items)
        if primary is None:
            return None

        secondary = self._select_secondary_observation(
            items, primary_kind=str(primary.get("primary_kind") or "")
        )
        if secondary:
            primary["secondary"] = secondary

        # Attach multi-observation metadata when there are multiple results
        if len(items) > 1:
            all_summaries = []
            for item in items:
                summary = self._summarize_tool_observation(item) if isinstance(item, dict) else None
                if summary:
                    all_summaries.append(summary)
            if all_summaries:
                primary["all_observations"] = all_summaries
                primary["observation_count"] = len(items)

        return primary

    def state_summary(self, event: RenderEvent) -> Optional[Dict[str, Any]]:
        """Extract compact state stats from state snapshot payload."""
        if event.node == "model_input":
            payload = event.payload or {}
            stats = dict(payload.get("state_stats") or {})
            ctx = (
                payload.get("context")
                if isinstance(payload.get("context"), dict)
                else {}
            )
            if ctx:
                stats.setdefault("input_tokens_total", ctx.get("input_tokens_total"))
                stats.setdefault("history_tokens", ctx.get("history_tokens"))
                stats.setdefault("output_tokens", ctx.get("output_tokens"))
                stats.setdefault("occupancy_ratio", ctx.get("occupancy_ratio"))
                stats.setdefault("context_window", ctx.get("context_window"))
            return stats or None
        if event.node not in {"state", "observation"}:
            return None
        payload = event.payload or {}
        obs = payload.get("observation")
        if not isinstance(obs, dict):
            return None

        scratchpad = obs.get("scratchpad")
        scratchpad_items = 0
        scratchpad_tokens = 0
        if isinstance(scratchpad, list):
            scratchpad_items = len(scratchpad)
            scratchpad_tokens = self._estimate_tokens(self._to_text(scratchpad))
        elif isinstance(scratchpad, str):
            scratchpad_items = 1
            scratchpad_tokens = self._estimate_tokens(scratchpad)

        mem = obs.get("memory")
        memory_records = 0
        if isinstance(mem, dict):
            recs = mem.get("records")
            if isinstance(recs, list):
                memory_records = len(recs)

        stats: Dict[str, Any] = {
            "scratchpad_items": scratchpad_items,
            "scratchpad_tokens": scratchpad_tokens,
            "memory_records": memory_records,
        }

        workspace_files = obs.get("workspace_files")
        if isinstance(workspace_files, list):
            stats["workspace_files"] = len(workspace_files)
        return stats

    def compact_summary(self, event: RenderEvent) -> Optional[Dict[str, Any]]:
        payload = event.payload or {}
        if event.node != "context_history":
            return None
        ctx = payload.get("context")
        if not isinstance(ctx, dict):
            return None
        stage = str(ctx.get("stage") or "")
        before = ctx.get("before_tokens")
        after = ctx.get("after_tokens")
        saved = ctx.get("saved_tokens")
        budget = ctx.get("budget")
        occupancy = ctx.get("occupancy_ratio")
        if stage == "warning":
            ratio = ""
            if isinstance(occupancy, (int, float)):
                ratio = f" ({occupancy * 100:.1f}%)"
            return {
                "color": "yellow",
                "text": (
                    f"Context warning · {before:,} / {budget:,}{ratio}"
                    if isinstance(before, int) and isinstance(budget, int)
                    else "Context warning"
                ),
            }
        if stage == "microcompact_applied":
            return {
                "color": "blue",
                "text": (
                    f"Compacted history · {before:,} -> {after:,} · saved {saved:,}"
                    if all(isinstance(x, int) for x in (before, after, saved))
                    else "Compacted history"
                ),
            }
        if stage == "summary_compact_applied":
            return {
                "color": "cyan",
                "text": (
                    f"Summarized earlier rounds · {before:,} -> {after:,} · saved {saved:,}"
                    if all(isinstance(x, int) for x in (before, after, saved))
                    else "Summarized earlier rounds"
                ),
            }
        if stage == "compact_skipped":
            reason = str(ctx.get("reason") or "skipped")
            return {"color": "gray50", "text": f"Compact skipped · {reason}"}
        if stage == "within_budget":
            return None
        return {"color": "gray50", "text": stage}

    def parser_diagnostic_summary(self, event: RenderEvent) -> Optional[Dict[str, Any]]:
        payload = event.payload or {}
        diagnostics = payload.get("diagnostics")
        if not isinstance(diagnostics, dict):
            return None
        severity = str(diagnostics.get("severity") or "error").lower()
        return {
            "color": "red" if severity == "error" else "yellow",
            "severity": severity,
            "summary": self._truncate(
                str(diagnostics.get("summary") or "Parser diagnostic").strip(), 220
            ),
            "details": self._truncate(
                str(diagnostics.get("details") or "").strip(), 280
            ),
            "extraction_mode": str(diagnostics.get("extraction_mode") or "").strip(),
            "protocol": str(diagnostics.get("protocol") or "").strip(),
            "selected_parser": str(diagnostics.get("selected_parser") or "").strip(),
            "fallback_used": bool(diagnostics.get("fallback_used")),
            "expected_shape": self._truncate(
                str(diagnostics.get("expected_shape") or "").strip(), 240
            ),
            "repair_instruction": self._truncate(
                str(diagnostics.get("repair_instruction") or "").strip(), 240
            ),
            "raw_output_preview": self._truncate(
                str(diagnostics.get("raw_output_preview") or "").strip(), 320
            ),
            "salvage_summary": self._truncate(
                str(diagnostics.get("salvage_summary") or "").strip(), 220
            ),
            "code": str(diagnostics.get("code") or "").strip(),
            "parser": str(diagnostics.get("parser") or "").strip(),
            "contract": str(diagnostics.get("contract") or "").strip(),
        }

    def memory_summary(self, event: RenderEvent) -> Optional[str]:
        if event.node != "memory_context":
            return None
        payload = event.payload or {}
        records = (
            payload.get("records") if isinstance(payload.get("records"), list) else []
        )
        summary = str(payload.get("summary", "")).strip()
        if summary:
            return f"records={len(records)} · {self._truncate(summary, 180)}"
        return f"records={len(records)}"

    def done_summary(self, stop_reason: Any, final_result: Any) -> str:
        return f"stop={stop_reason} · result={self._truncate(self._to_text(final_result), 180)}"

    def _action_from_dict(self, action: Any) -> Dict[str, str]:
        if not isinstance(action, dict):
            return {
                "label": "ACTION",
                "detail": self._truncate(self._to_text(action), 120),
                "status": "neutral",
            }
        name = str(
            action.get("name") or action.get("tool") or action.get("action") or "action"
        )
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        detail = ""
        if args:
            for key in ("query", "url", "path", "command", "prompt", "file"):
                if key in args:
                    detail = self._truncate(self._to_text(args[key]), 120)
                    break
            if not detail:
                k = next(iter(args.keys()))
                detail = self._truncate(f"{k}={self._to_text(args[k])}", 120)
        return {
            "label": name.upper().replace("_", " "),
            "detail": self._compress_detail(detail),
            "status": "neutral",
        }

    def _extract_search_rows(self, data: Any) -> List[Tuple[str, str]]:
        rows: List[Tuple[str, str]] = []
        candidates: List[Any] = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            for key in ("results", "items", "search_results", "web_results", "hits"):
                if isinstance(data.get(key), list):
                    candidates = data.get(key)
                    break
        for item in candidates:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "")
            url = str(item.get("url") or item.get("link") or item.get("href") or "")
            if not title or not url:
                continue
            rows.append((title, self._short_url(url)))
        return rows

    def _select_primary_observation(
        self, items: List[Any]
    ) -> Optional[Dict[str, Any]]:
        terminal_output: Optional[Dict[str, Any]] = None
        terminal_screen: Optional[Dict[str, Any]] = None
        tool_error: Optional[Dict[str, Any]] = None
        rich_tool: Optional[Dict[str, Any]] = None
        generic: Optional[Dict[str, Any]] = None

        for item in items:
            terminal = self._extract_terminal_observation(item)
            if terminal:
                summary = self._summarize_terminal_observation(terminal)
                kind = str(summary.get("primary_kind") or "")
                if kind == "terminal_output" and terminal_output is None:
                    terminal_output = summary
                elif kind == "terminal_screen" and terminal_screen is None:
                    terminal_screen = summary
                continue

            summary = self._summarize_tool_observation(item)
            if summary is None:
                continue
            status = str(summary.get("status") or "neutral")
            kind = str(summary.get("primary_kind") or "")
            if status == "error" and tool_error is None:
                tool_error = summary
            elif kind in {"search_results", "tool_syntax", "tool_result"} and rich_tool is None:
                rich_tool = summary
            elif generic is None:
                generic = summary

        return terminal_output or terminal_screen or tool_error or rich_tool or generic

    def _select_secondary_observation(
        self, items: List[Any], primary_kind: str
    ) -> Optional[Dict[str, Any]]:
        if primary_kind.startswith("terminal_"):
            for item in items:
                if self._extract_terminal_observation(item):
                    continue
                summary = self._summarize_tool_observation(item, secondary=True)
                if summary is not None:
                    return summary
            return None

        for item in items:
            terminal = self._extract_terminal_observation(item)
            if terminal:
                return self._summarize_terminal_observation(terminal, secondary=True)
        return None

    def _extract_terminal_observation(self, item: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        if isinstance(item.get("terminal"), dict):
            return item.get("terminal")

        data = item.get("data")
        if isinstance(data, dict) and isinstance(data.get("terminal"), dict):
            return data.get("terminal")

        env = item.get("env")
        if not isinstance(env, dict):
            return None
        observation = env.get("observation")
        if not isinstance(observation, dict):
            return None
        payload = observation.get("data")
        if not isinstance(payload, dict):
            return None
        terminal = payload.get("terminal")
        if isinstance(terminal, dict):
            return terminal
        return None

    def _summarize_terminal_observation(
        self, terminal: Dict[str, Any], secondary: bool = False
    ) -> Dict[str, Any]:
        output = self._clean_terminal_text(terminal.get("output"))
        screen = self._clean_terminal_text(terminal.get("screen"))
        body = output or screen
        title = "Terminal Output" if output else "Terminal Screen"
        return {
            "status": "success",
            "title": title,
            "body": self._truncate(body, 2000) if body else "",
            "primary_kind": "terminal_output" if output else "terminal_screen",
            "secondary_only": secondary,
        }

    def _summarize_tool_observation(
        self, item: Any, secondary: bool = False
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return {
                "status": "neutral",
                "title": "Observation",
                "body": self._truncate(self._to_text(item), 220),
                "primary_kind": "tool_result",
                "secondary_only": secondary,
            }

        cleaned = self._strip_noise(item)
        rows = self._extract_search_rows(cleaned)
        if rows:
            table = Table(
                show_header=True, header_style="bold", box=box.SIMPLE, show_edge=False
            )
            table.add_column("Title")
            table.add_column("URL")
            for title, short_url in rows[:6]:
                table.add_row(self._truncate(title, 80), short_url)
            return {
                "status": "success",
                "title": "Search Results" if not secondary else "Tool Observation",
                "table": table,
                "primary_kind": "search_results",
                "secondary_only": secondary,
            }

        syntax = self._extract_syntax(cleaned)
        if syntax is not None:
            return {
                "status": "success",
                "title": "Tool Observation" if secondary else "Structured Output",
                "syntax": syntax,
                "primary_kind": "tool_syntax",
                "secondary_only": secondary,
            }

        title = str(
            cleaned.get("title")
            or cleaned.get("name")
            or ("Tool Observation" if secondary else cleaned.get("status"))
            or ("Tool Observation" if secondary else "Observation")
        )
        url = str(
            cleaned.get("url")
            or cleaned.get("source_url")
            or cleaned.get("target_url")
            or ""
        )
        err = cleaned.get("error")
        if err:
            return {
                "status": "error",
                "title": self._truncate(str(err), 120),
                "url": self._short_url(url) if url else "",
                "body": self._truncate(self._to_text(cleaned.get("content", "")), 180),
                "primary_kind": "error",
                "secondary_only": secondary,
            }

        body = self._best_body(cleaned)
        return {
            "status": "success",
            "title": self._truncate(title, 120),
            "url": self._short_url(url) if url else "",
            "body": self._truncate(body, 220) if body else "",
            "primary_kind": "tool_result",
            "secondary_only": secondary,
        }

    def _extract_syntax(self, data: Dict[str, Any]) -> Optional[Syntax]:
        for key in ("content", "file_content", "source", "text"):
            value = data.get(key)
            if not isinstance(value, str):
                continue
            if "\n" not in value or len(value) < 40:
                continue
            return Syntax(
                self._truncate(value, 2000), self._guess_language(data), word_wrap=True
            )
        return None

    def _clean_terminal_text(self, value: Any) -> str:
        text = str(value or "")
        if not text.strip():
            return ""
        prefixes = (
            "New Terminal Output:\n",
            "Current Terminal Screen:\n",
        )
        for prefix in prefixes:
            if text.startswith(prefix):
                return text[len(prefix) :].lstrip("\n")
        return text

    def _best_body(self, data: Dict[str, Any]) -> str:
        for key in ("content", "summary", "message", "observation", "text"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val
        lite: Dict[str, Any] = {}
        for k, v in data.items():
            if k in _NOISE_KEYS:
                continue
            if isinstance(v, (dict, list)):
                continue
            lite[k] = v
        return self._to_text(lite)

    def _strip_noise(self, data: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in data.items():
            if key in _NOISE_KEYS:
                continue
            if key == "error_category" and value is None:
                continue
            out[key] = value
        return out

    def _guess_language(self, data: Dict[str, Any]) -> str:
        path = str(data.get("path") or data.get("file") or "")
        if path.endswith(".py"):
            return "python"
        if path.endswith(".md"):
            return "markdown"
        if path.endswith(".json"):
            return "json"
        if path.endswith(".sh"):
            return "bash"
        return "text"

    def _short_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            host = parsed.netloc or parsed.path
            path = parsed.path if parsed.netloc else ""
            if len(path) > 24:
                path = path[:24] + "..."
            return f"{host}{path}"
        except Exception:
            return self._truncate(url, 36)

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[:limit]}... (truncated)"

    def _to_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:
            return str(value)

    def _estimate_tokens(self, text: str) -> int:
        # Lightweight estimator for on-screen stats only.
        if not text:
            return 0
        return len(re.findall(r"\w+|[^\s\w]", text, flags=re.UNICODE))

    def _compress_detail(self, detail: str) -> str:
        if not detail:
            return ""
        d = detail.strip()
        if d.startswith("http://") or d.startswith("https://"):
            try:
                parsed = urlparse(d)
                host = parsed.netloc or d
                path = parsed.path or ""
                return self._truncate(f"{host}{path[:16]}", 40)
            except Exception:
                return self._truncate(d, 40)
        return self._truncate(d, 70)


__all__ = ["ContentFirstRenderer"]
