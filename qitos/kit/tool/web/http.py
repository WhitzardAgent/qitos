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
            allowed_methods=frozenset(
                {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
            ),
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

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute an HTTP request and return a structured response payload.

        :param method: HTTP method such as `GET` or `POST`.
        :param url: Absolute `http` or `https` URL.
        :param params: Optional query parameters.
        :param data: Optional form-like request body.
        :param json_data: Optional JSON request body.
        :param headers: Optional per-request headers.
        :param timeout: Optional timeout override in seconds.
        :param verify_tls: Whether TLS certificates should be verified.
        :param allow_redirects: Whether redirects should be followed automatically.
        :param max_content_chars: Maximum number of response characters to keep.

        Returns status code, headers, body text, and parsed JSON when available.
        """
        _ = runtime_context
        method = str(args.get("method", "") or "").upper().strip()
        url = str(args.get("url", ""))
        params = args.get("params")
        data = args.get("data")
        json_data = args.get("json_data")
        headers = args.get("headers")
        timeout = args.get("timeout")
        verify_tls = bool(args.get("verify_tls", True))
        allow_redirects = bool(args.get("allow_redirects", True))
        max_content_chars = int(args.get("max_content_chars", 120_000))
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
    """Issue one HTTP GET request and return a structured response payload.

    Use this tool when the agent needs raw page content, API data, headers, or
    status codes without browser-style stateful navigation.
    """

    def __init__(
        self,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        max_retries: int = 2,
    ):
        self._request = HTTPRequest(
            headers=headers, timeout=timeout, max_retries=max_retries
        )
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

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute one HTTP GET request.

        :param url: Absolute URL to request.
        :param params: Optional query parameters.
        :param headers: Optional request headers.
        :param timeout: Optional timeout override in seconds.
        :param verify_tls: Whether TLS certificates should be verified.
        :param allow_redirects: Whether redirects should be followed automatically.
        """
        _ = runtime_context
        return self._request.run(
            method="GET",
            url=str(args.get("url", "")),
            params=args.get("params"),
            headers=args.get("headers"),
            timeout=args.get("timeout"),
            verify_tls=bool(args.get("verify_tls", True)),
            allow_redirects=bool(args.get("allow_redirects", True)),
        )


class HTTPPost(BaseTool):
    """Issue one HTTP POST request and return a structured response payload.

    Use this tool for form-like submissions or JSON API calls where the agent
    needs direct control over request bodies and headers.
    """

    def __init__(
        self,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        max_retries: int = 2,
    ):
        self._request = HTTPRequest(
            headers=headers, timeout=timeout, max_retries=max_retries
        )
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

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute one HTTP POST request.

        :param url: Absolute URL to request.
        :param data: Optional form-like request body.
        :param json_data: Optional JSON request body.
        :param headers: Optional request headers.
        :param timeout: Optional timeout override in seconds.
        :param verify_tls: Whether TLS certificates should be verified.
        :param allow_redirects: Whether redirects should be followed automatically.
        """
        _ = runtime_context
        return self._request.run(
            method="POST",
            url=str(args.get("url", "")),
            data=args.get("data"),
            json_data=args.get("json_data"),
            headers=args.get("headers"),
            timeout=args.get("timeout"),
            verify_tls=bool(args.get("verify_tls", True)),
            allow_redirects=bool(args.get("allow_redirects", True)),
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

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extract readable text from raw HTML.

        :param html: Raw HTML document text.
        :param max_chars: Maximum number of output characters to keep.
        :param keep_links: Whether anchor URLs should be preserved inline.

        Returns cleaned text and the detected page title when available.
        """
        _ = runtime_context
        html = str(args.get("html", ""))
        max_chars = int(args.get("max_chars", 6000))
        keep_links = bool(args.get("keep_links", False))
        if not html:
            return {"status": "error", "message": "html cannot be empty"}
        try:
            text, title = self._to_text(html, keep_links=keep_links)
            if max_chars > 0 and len(text) > max_chars:
                text = text[:max_chars] + "\n... [truncated]"
            return {
                "status": "success",
                "content": text,
                "length": len(text),
                "title": title,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _to_text(
        self, html: str, keep_links: bool = False
    ) -> tuple[str, Optional[str]]:
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
