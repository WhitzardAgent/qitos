"""Professional HTTP and web content tools."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec

try:  # optional dependency, with graceful fallback
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - optional dependency path
    BeautifulSoup = None  # type: ignore[assignment]


class HTTPRequest(BaseTool):
    """Generic HTTP request tool with retries, timeout, and structured output."""

    def __init__(
        self,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        max_retries: int = 2,
        backoff_factor: float = 0.4,
        user_agent: str = "QitOS-WebTool/1.0",
    ):
        self._headers = dict(headers or {})
        if "User-Agent" not in self._headers:
            self._headers["User-Agent"] = user_agent
        self._timeout = timeout
        self._session = requests.Session()
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[408, 409, 429, 500, 502, 503, 504],
            allowed_methods=frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        super().__init__(
            ToolSpec(
                name="http_request",
                description="HTTP request with retries and structured response payload",
                parameters={
                    "method": {"type": "string"},
                    "url": {"type": "string"},
                    "params": {"type": "object"},
                    "data": {"type": "object"},
                    "json_data": {"type": "object"},
                    "headers": {"type": "object"},
                    "timeout": {"type": "integer"},
                    "verify_tls": {"type": "boolean"},
                    "allow_redirects": {"type": "boolean"},
                    "max_content_chars": {"type": "integer"},
                },
                required=["method", "url"],
                permissions=ToolPermission(network=True),
            )
        )

    def run(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        verify_tls: bool = True,
        allow_redirects: bool = True,
        max_content_chars: int = 120_000,
    ) -> Dict[str, Any]:
        method = str(method or "").upper().strip()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            return {"status": "error", "message": f"Unsupported HTTP method: {method}"}
        err = self._validate_url(url)
        if err:
            return {"status": "error", "message": err, "url": url}

        merged_headers = dict(self._headers)
        if headers:
            merged_headers.update(headers)

        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                data=data,
                json=json_data,
                headers=merged_headers,
                timeout=int(timeout or self._timeout),
                verify=verify_tls,
                allow_redirects=allow_redirects,
            )
            content = self._safe_text(response)
            truncated = False
            if max_content_chars > 0 and len(content) > max_content_chars:
                content = content[:max_content_chars] + "\n... [truncated]"
                truncated = True
            payload: Dict[str, Any] = {
                "status": "success" if response.status_code < 400 else "error",
                "ok": bool(response.ok),
                "method": method,
                "url": response.url,
                "status_code": response.status_code,
                "reason": response.reason,
                "headers": dict(response.headers),
                "content_type": response.headers.get("Content-Type", ""),
                "content": content,
                "content_length": len(content),
                "truncated": truncated,
                "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
                "history": [h.url for h in response.history],
            }
            parsed_json = self._try_parse_json(response)
            if parsed_json is not None:
                payload["json"] = parsed_json
            return payload
        except requests.RequestException as e:
            return {
                "status": "error",
                "message": str(e),
                "method": method,
                "url": url,
                "error_type": e.__class__.__name__,
            }
        except Exception as e:  # pragma: no cover - defensive path
            return {
                "status": "error",
                "message": str(e),
                "method": method,
                "url": url,
                "error_type": e.__class__.__name__,
            }

    def _validate_url(self, url: str) -> Optional[str]:
        if not url:
            return "URL cannot be empty"
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return "URL scheme must be http or https"
        if not parsed.netloc:
            return "URL host is missing"
        return None

    def _safe_text(self, response: requests.Response) -> str:
        response.encoding = response.encoding or response.apparent_encoding
        return response.text

    def _try_parse_json(self, response: requests.Response) -> Any:
        ctype = (response.headers.get("Content-Type") or "").lower()
        if "application/json" not in ctype and "json" not in ctype:
            return None
        try:
            return response.json()
        except Exception:
            try:
                return json.loads(response.text)
            except Exception:
                return None


class HTTPGet(BaseTool):
    def __init__(self, headers: Optional[Dict[str, str]] = None, timeout: int = 30, max_retries: int = 2):
        self._request = HTTPRequest(headers=headers, timeout=timeout, max_retries=max_retries)
        super().__init__(
            ToolSpec(
                name="http_get",
                description="HTTP GET request with retries and structured output",
                parameters={
                    "url": {"type": "string"},
                    "params": {"type": "object"},
                    "headers": {"type": "object"},
                    "timeout": {"type": "integer"},
                    "verify_tls": {"type": "boolean"},
                    "allow_redirects": {"type": "boolean"},
                },
                required=["url"],
                permissions=ToolPermission(network=True),
            )
        )

    def run(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        verify_tls: bool = True,
        allow_redirects: bool = True,
    ) -> Dict[str, Any]:
        return self._request.run(
            method="GET",
            url=url,
            params=params,
            headers=headers,
            timeout=timeout,
            verify_tls=verify_tls,
            allow_redirects=allow_redirects,
        )


class HTTPPost(BaseTool):
    def __init__(self, headers: Optional[Dict[str, str]] = None, timeout: int = 30, max_retries: int = 2):
        self._request = HTTPRequest(headers=headers, timeout=timeout, max_retries=max_retries)
        super().__init__(
            ToolSpec(
                name="http_post",
                description="HTTP POST request with retries and structured output",
                parameters={
                    "url": {"type": "string"},
                    "data": {"type": "object"},
                    "json_data": {"type": "object"},
                    "headers": {"type": "object"},
                    "timeout": {"type": "integer"},
                    "verify_tls": {"type": "boolean"},
                    "allow_redirects": {"type": "boolean"},
                },
                required=["url"],
                permissions=ToolPermission(network=True),
            )
        )

    def run(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        verify_tls: bool = True,
        allow_redirects: bool = True,
    ) -> Dict[str, Any]:
        return self._request.run(
            method="POST",
            url=url,
            data=data,
            json_data=json_data,
            headers=headers,
            timeout=timeout,
            verify_tls=verify_tls,
            allow_redirects=allow_redirects,
        )


class HTMLExtractText(BaseTool):
    """Extract readable text snippets from raw HTML."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="extract_web_text",
                description="Extract readable text from HTML content",
                parameters={
                    "html": {"type": "string"},
                    "max_chars": {"type": "integer"},
                    "keep_links": {"type": "boolean"},
                },
                required=["html"],
                permissions=ToolPermission(),
            )
        )

    def run(self, html: str, max_chars: int = 6000, keep_links: bool = False) -> Dict[str, Any]:
        if not html:
            return {"status": "error", "message": "html cannot be empty"}
        try:
            text, title = self._to_text(html, keep_links=keep_links)
            if max_chars > 0 and len(text) > max_chars:
                text = text[:max_chars] + "\n... [truncated]"
            return {"status": "success", "content": text, "length": len(text), "title": title}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _to_text(self, html: str, keep_links: bool = False) -> tuple[str, Optional[str]]:
        if BeautifulSoup is not None:
            # Prefer the stdlib parser to avoid third-party parser deprecation noise
            # in the supported extraction path while preserving extraction behavior.
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
                tag.decompose()
            title = None
            if soup.title and soup.title.string:
                title = str(soup.title.string).strip()
            if keep_links:
                for a in soup.find_all("a"):
                    href = a.get("href")
                    if href:
                        a.append(f" ({href})")
            text = soup.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text)
            return text.strip(), title

        # Fallback extraction without bs4.
        data = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
        data = re.sub(r"(?is)<style.*?>.*?</style>", " ", data)
        data = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", data)
        title = None
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", data)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
        data = re.sub(r"(?is)<[^>]+>", " ", data)
        data = re.sub(r"&nbsp;", " ", data)
        data = re.sub(r"&amp;", "&", data)
        data = re.sub(r"\s+", " ", data)
        return data.strip(), title


__all__ = ["HTTPRequest", "HTTPGet", "HTTPPost", "HTMLExtractText"]
