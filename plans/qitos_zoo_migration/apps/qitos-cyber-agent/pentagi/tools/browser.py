"""Browser tool — fetch web content from URLs.

Replicates pentagi's browser tool which calls an external scraper microservice.
Our implementation fetches directly via HTTP with three actions:
- markdown: fetch URL → convert HTML to markdown
- html: fetch URL → return raw HTML
- links: fetch URL → parse <a> tags → return link list

Also supports public/private URL resolution for internal network targets.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from qitos.core.tool import BaseTool, ToolSpec


def _is_private_host(hostname: str) -> bool:
    """Check if a hostname points to a private/internal network."""
    if not hostname:
        return False
    # Localhost
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    # .local, .htb (HackTheBox), .internal
    if hostname.endswith((".local", ".htb", ".internal")):
        return True
    # RFC1918 private ranges
    if hostname.startswith(("10.", "192.168.")):
        return True
    # 172.16.0.0/12 range
    if hostname.startswith("172."):
        parts = hostname.split(".")
        if len(parts) >= 2:
            try:
                second = int(parts[1])
                if 16 <= second <= 31:
                    return True
            except ValueError:
                pass
    return False


class BrowserTool(BaseTool):
    """Fetch web content from a URL in various formats.

    Supports three actions:
    - markdown: fetch and convert to markdown
    - html: fetch and return raw HTML
    - links: fetch and extract all links

    Supports public/private scraper URL resolution for penetration testing
    against internal network targets.
    """

    def __init__(
        self,
        scraper_url: Optional[str] = None,
        scraper_private_url: Optional[str] = None,
        timeout: int = 65,
        verify_ssl: bool = False,
    ):
        self._scraper_url = scraper_url
        self._scraper_private_url = scraper_private_url
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        super().__init__(
            ToolSpec(
                name="browser",
                description=(
                    "Fetch web content from a URL. "
                    "Use 'markdown' action for readable content, "
                    "'html' for raw HTML, "
                    "'links' to extract all links from the page."
                ),
                parameters={
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    },
                    "action": {
                        "type": "string",
                        "description": "Action to perform: 'markdown', 'html', or 'links'",
                    },
                    "message": {
                        "type": "string",
                        "description": "Task result message describing what you found",
                    },
                },
                required=["url", "action", "message"],
            )
        )

    def _resolve_scraper_url(self, target_url: str) -> Optional[str]:
        """Determine the appropriate scraper URL based on target host."""
        parsed = urlparse(target_url)
        hostname = parsed.hostname or ""

        if _is_private_host(hostname):
            # Private target → use private scraper, fallback to public
            return self._scraper_private_url or self._scraper_url
        else:
            # Public target → use public scraper, fallback to private
            return self._scraper_url or self._scraper_private_url

    def _fetch_via_scraper(self, url: str, action: str) -> str:
        """Fetch content via external scraper microservice."""
        scraper_url = self._resolve_scraper_url(url)
        if not scraper_url:
            return self._fetch_directly(url, action)

        try:
            if action == "markdown":
                resp = requests.get(
                    f"{scraper_url}/markdown",
                    params={"url": url},
                    timeout=self._timeout,
                    verify=self._verify_ssl,
                )
            elif action == "html":
                resp = requests.get(
                    f"{scraper_url}/html",
                    params={"url": url},
                    timeout=self._timeout,
                    verify=self._verify_ssl,
                )
            elif action == "links":
                resp = requests.get(
                    f"{scraper_url}/links",
                    params={"url": url},
                    timeout=self._timeout,
                    verify=self._verify_ssl,
                )
            else:
                return f"Unknown action: {action}"
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            # Fallback to direct fetching
            return self._fetch_directly(url, action)

    def _fetch_directly(self, url: str, action: str) -> str:
        """Fetch content directly via HTTP."""
        try:
            resp = requests.get(
                url,
                timeout=self._timeout,
                verify=self._verify_ssl,
                headers={"User-Agent": "Mozilla/5.0 (compatible; PentAGI/1.0)"},
            )
            resp.raise_for_status()
        except Exception as e:
            return f"Error fetching URL: {e}"

        content_type = resp.headers.get("Content-Type", "")

        if action == "html":
            return resp.text

        if action == "markdown":
            if "text/html" in content_type:
                try:
                    import markdownify
                    return markdownify.markdownify(resp.text, heading_style="ATX")
                except ImportError:
                    # Fallback: strip HTML tags
                    return re.sub(r"<[^>]+>", "", resp.text)
            return resp.text

        if action == "links":
            if "text/html" in content_type:
                return self._extract_links(resp.text, url)
            return "No links found (non-HTML content)"

        return resp.text

    def _extract_links(self, html: str, base_url: str) -> str:
        """Extract all links from HTML content."""
        from html.parser import HTMLParser
        from urllib.parse import urljoin

        class LinkExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links: List[Dict[str, str]] = []

            def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
                if tag == "a":
                    href = None
                    title = None
                    for attr_name, attr_value in attrs:
                        if attr_name == "href":
                            href = attr_value
                        elif attr_name == "title":
                            title = attr_value
                    if href:
                        self.links.append({
                            "title": title or href,
                            "link": urljoin(base_url, href),
                        })

        parser = LinkExtractor()
        try:
            parser.feed(html)
        except Exception:
            pass

        if not parser.links:
            return "No links found"

        lines = []
        for link in parser.links:
            lines.append(f"- [{link['title']}]({link['link']})")
        return "\n".join(lines)

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = str(args.get("url", ""))
        action = str(args.get("action", "markdown")).lower()
        message = str(args.get("message", ""))

        if not url:
            return {"status": "error", "message": "URL is required"}

        if action not in ("markdown", "html", "links"):
            return {"status": "error", "message": f"Unknown action: {action}. Use markdown, html, or links."}

        # Try scraper first, fallback to direct
        if self._scraper_url or self._scraper_private_url:
            content = self._fetch_via_scraper(url, action)
        else:
            content = self._fetch_directly(url, action)

        return {
            "status": "ok",
            "url": url,
            "action": action,
            "content": content,
            "message": message,
        }

    @property
    def is_available(self) -> bool:
        """Browser tool is available when at least one scraper URL is configured
        or when direct fetching is possible (always true)."""
        return True


__all__ = ["BrowserTool"]
