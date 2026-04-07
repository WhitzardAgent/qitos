"""
Native Google Gemini API model implementation.

This adapter talks to the official Gemini `generateContent` endpoint directly.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from .base import Model, ModelFactory


class GeminiModel(Model):
    """
    Google Gemini native REST model.

    Environment variables:
    - GEMINI_API_KEY or GOOGLE_API_KEY
    - GEMINI_BASE_URL (optional, default https://generativelanguage.googleapis.com/v1beta)
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
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
        self.api_key = (
            api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        )
        resolved_base_url = base_url or os.getenv(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        )
        self.base_url = str(resolved_base_url).rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY or GOOGLE_API_KEY not set. Please set one or pass api_key."
            )

    def _call_api(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        payload: Dict[str, Any] = {
            "contents": self._gemini_contents(messages),
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        _ = kwargs
        system_text = self._system_text(messages)
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        try:
            response = requests.post(
                f"{self.base_url}/models/{quote(self.model, safe='')}:generateContent",
                params={"key": self.api_key},
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

    def _system_text(self, messages: List[Dict[str, str]]) -> str:
        parts: List[str] = []
        if self.system_prompt:
            parts.append(str(self.system_prompt))
        for msg in messages:
            if str(msg.get("role", "")) == "system":
                content = str(msg.get("content", "")).strip()
                if content:
                    parts.append(content)
        return "\n\n".join(parts).strip()

    def _gemini_contents(self, messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        contents: List[Dict[str, Any]] = []
        for msg in messages:
            role = str(msg.get("role", ""))
            if role == "system":
                continue
            gemini_role = "model" if role == "assistant" else "user"
            text = str(msg.get("content", ""))
            contents.append({"role": gemini_role, "parts": [{"text": text}]})
        return contents

    def _parse_response(self, response: Dict[str, Any]) -> str:
        candidates = list(response.get("candidates") or [])
        if not candidates:
            prompt_feedback = response.get("promptFeedback")
            if isinstance(prompt_feedback, dict) and prompt_feedback:
                return f"Error: {prompt_feedback}"
            return ""

        content = candidates[0].get("content") or {}
        parts = list(content.get("parts") or [])
        texts: List[str] = []
        actions: List[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = str(part.get("text", "")).strip()
            if text:
                texts.append(text)
                continue
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                name = str(function_call.get("name", "")).strip()
                args = function_call.get("args", {})
                if name:
                    if not isinstance(args, dict):
                        args = {"input": args}
                    actions.append(self.format_action(name, args))
        if actions:
            return "\n".join(actions)
        return "\n".join(texts).strip()

    def _usage_from_response(
        self, response: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        usage = response.get("usageMetadata")
        if not isinstance(usage, dict):
            return None
        prompt_tokens = usage.get("promptTokenCount")
        completion_tokens = usage.get("candidatesTokenCount")
        total_tokens = usage.get("totalTokenCount")
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }


ModelFactory.register("gemini")(GeminiModel)
ModelFactory.register("google")(GeminiModel)


__all__ = ["GeminiModel"]
