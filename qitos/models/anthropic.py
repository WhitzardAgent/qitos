"""
Native Anthropic Messages API model implementation.

This adapter talks to Anthropic's `/v1/messages` endpoint directly instead of
going through an OpenAI-compatible proxy.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterator, List, Optional

import requests

from .base import Model, ModelFactory, ModelStreamChunk


class AnthropicModel(Model):
    """
    Anthropic Messages API model.

    Environment variables:
    - ANTHROPIC_API_KEY
    - ANTHROPIC_BASE_URL (optional, default https://api.anthropic.com)
    - ANTHROPIC_API_VERSION (optional, default 2023-06-01)
    """

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-latest",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        api_version: str = "2023-06-01",
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 60,
        context_window: Optional[int] = None,
    ):
        super().__init__(
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window=context_window,
        )
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        resolved_base_url = base_url or os.getenv(
            "ANTHROPIC_BASE_URL", "https://api.anthropic.com"
        )
        self.base_url = str(resolved_base_url).rstrip("/")
        self.api_version = api_version or os.getenv(
            "ANTHROPIC_API_VERSION", "2023-06-01"
        )
        self.timeout = timeout
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. Please set it or pass api_key."
            )

    def _call_api(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "content-type": "application/json",
        }
        _ = kwargs
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": self._anthropic_messages(messages),
        }
        system_text = self._system_text(messages)
        if system_text:
            payload["system"] = system_text

        try:
            response = requests.post(
                f"{self.base_url}/v1/messages",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
            self._set_last_usage(self._usage_from_response(result))
            return self._parse_response(result)
        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else ""
            return f"HTTP Error: {body or str(exc)}"
        except requests.RequestException as exc:
            return f"Connection Error: {str(exc)}"
        except Exception as exc:
            return f"Error: {str(exc)}"

    def _system_text(self, messages: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        if self.system_prompt:
            parts.append(str(self.system_prompt))
        for msg in messages:
            if str(msg.get("role", "")) == "system":
                content = str(msg.get("content", "")).strip()
                if content:
                    parts.append(content)
        return "\n\n".join(parts).strip()

    def _anthropic_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for msg in messages:
            role = str(msg.get("role", ""))
            if role == "system":
                continue
            mapped_role = "assistant" if role == "assistant" else "user"
            converted.append(
                {
                    "role": mapped_role,
                    "content": str(msg.get("content", "")),
                }
            )
        return converted

    def _parse_response(self, response: Dict[str, Any]) -> str:
        blocks = list(response.get("content") or [])
        text_parts: List[str] = []
        tool_parts: List[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type", "")).strip()
            if kind == "text":
                text = str(block.get("text", "")).strip()
                if text:
                    text_parts.append(text)
            elif kind == "tool_use":
                name = str(block.get("name", "")).strip()
                args = block.get("input", {})
                if name:
                    if not isinstance(args, dict):
                        args = {"input": args}
                    tool_parts.append(self.format_action(name, args))
        if tool_parts:
            return "\n".join(tool_parts)
        return "\n".join(text_parts).strip()

    def _usage_from_response(
        self, response: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None
        input_tokens = usage.get("input_tokens")
        cache_creation = usage.get("cache_creation_input_tokens")
        cache_read = usage.get("cache_read_input_tokens")
        output_tokens = usage.get("output_tokens")
        prompt_total = 0
        has_prompt = False
        for value in (input_tokens, cache_creation, cache_read):
            if isinstance(value, int):
                prompt_total += value
                has_prompt = True
        total_tokens = None
        if has_prompt or isinstance(output_tokens, int):
            total_tokens = prompt_total + int(output_tokens or 0)
        return {
            "prompt_tokens": prompt_total if has_prompt else input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

    def stream(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Iterator[ModelStreamChunk]:
        """Stream Anthropic Messages API response as chunks using SSE."""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "content-type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": self._anthropic_messages(messages),
            "stream": True,
        }
        system_text = self._system_text(messages)
        if system_text:
            payload["system"] = system_text
        payload.update(kwargs)

        self._last_usage = None
        try:
            response = requests.post(
                f"{self.base_url}/v1/messages",
                headers=headers,
                json=payload,
                timeout=self.timeout,
                stream=True,
            )
            response.raise_for_status()

            usage_data = None
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                import json as _json

                try:
                    event = _json.loads(data_str)
                except _json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield ModelStreamChunk(text=text, done=False)

                elif event_type == "message_delta":
                    delta = event.get("delta", {})
                    if delta.get("stop_reason"):
                        msg_usage = event.get("usage", {})
                        if msg_usage:
                            output_tokens = msg_usage.get("output_tokens")
                            usage_data = {"completion_tokens": output_tokens}
                        yield ModelStreamChunk(text="", done=True, usage=usage_data)
                        break

                elif event_type == "message_start":
                    msg_data = event.get("message", {})
                    msg_usage = msg_data.get("usage", {})
                    if msg_usage:
                        input_tokens = msg_usage.get("input_tokens")
                        usage_data = {"prompt_tokens": input_tokens}

                elif event_type == "message_stop":
                    yield ModelStreamChunk(text="", done=True, usage=usage_data)
                    break

            if usage_data:
                self._set_last_usage(usage_data)

        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else ""
            yield ModelStreamChunk(text=f"HTTP Error: {body or str(exc)}", done=True)
        except requests.RequestException as exc:
            yield ModelStreamChunk(text=f"Connection Error: {str(exc)}", done=True)
        except Exception as exc:
            yield ModelStreamChunk(text=f"Error: {str(exc)}", done=True)


ModelFactory.register("anthropic")(AnthropicModel)


__all__ = ["AnthropicModel"]
