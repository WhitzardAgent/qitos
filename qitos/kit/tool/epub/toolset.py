"""EPUB reading tools."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET

from qitos.core.tool import tool


class EpubToolSet:
    """Bundle tools for listing, reading, and searching EPUB chapters."""

    name = "epub"
    version = "1.0"

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root).resolve()

    def setup(self, context: Dict[str, Any]) -> None:
        return None

    def teardown(self, context: Dict[str, Any]) -> None:
        return None

    def tools(self) -> List[Any]:
        return [self.list_chapters, self.read_chapter, self.search]

    @tool(
        name="list_chapters", description="List chapter files and titles from an EPUB"
    )
    def list_chapters(self, path: str) -> Dict[str, Any]:
        """
        List chapter files and detected titles from one EPUB archive.

        :param path: EPUB path relative to the workspace root.

        Returns chapter indices, internal chapter hrefs, and best-effort titles.
        """
        try:
            epub_path = self._resolve(path)
            chapter_files = self._chapter_files(epub_path)
            titles = [self._extract_title(epub_path, item) for item in chapter_files]
            rows = [
                {"index": i, "href": href, "title": title or f"Chapter {i + 1}"}
                for i, (href, title) in enumerate(zip(chapter_files, titles))
            ]
            return {
                "status": "success",
                "path": str(epub_path),
                "count": len(rows),
                "chapters": rows,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @tool(name="read_chapter", description="Read one chapter text from an EPUB")
    def read_chapter(
        self, path: str, chapter_index: int, max_chars: int = 8000
    ) -> Dict[str, Any]:
        """
        Read one EPUB chapter as plain text.

        :param path: EPUB path relative to the workspace root.
        :param chapter_index: Zero-based chapter index to read.
        :param max_chars: Maximum number of characters to keep in the output.

        Converts the chapter HTML into plain text and truncates overly long output.
        """
        try:
            epub_path = self._resolve(path)
            chapter_files = self._chapter_files(epub_path)
            if chapter_index < 0 or chapter_index >= len(chapter_files):
                return {
                    "status": "error",
                    "message": f"Invalid chapter_index: {chapter_index}",
                }
            href = chapter_files[chapter_index]
            raw = self._read_zip_text(epub_path, href)
            text = self._html_to_text(raw)
            if len(text) > max_chars:
                text = text[:max_chars] + "\n... [truncated]"
            return {
                "status": "success",
                "chapter_index": chapter_index,
                "href": href,
                "title": self._extract_title(epub_path, href),
                "content": text,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @tool(name="search", description="Search keyword in EPUB chapters")
    def search(
        self, path: str, query: str, top_k: int = 3, snippet_chars: int = 240
    ) -> Dict[str, Any]:
        """
        Search all EPUB chapters for a keyword and return matching snippets.

        :param path: EPUB path relative to the workspace root.
        :param query: Keyword or phrase to search for.
        :param top_k: Maximum number of search hits to return.
        :param snippet_chars: Approximate snippet length around each match.
        """
        if not query.strip():
            return {"status": "error", "message": "query cannot be empty"}
        try:
            epub_path = self._resolve(path)
            chapter_files = self._chapter_files(epub_path)
            hits: List[Dict[str, Any]] = []
            q = query.lower()
            for idx, href in enumerate(chapter_files):
                text = self._html_to_text(self._read_zip_text(epub_path, href))
                pos = text.lower().find(q)
                if pos < 0:
                    continue
                left = max(0, pos - snippet_chars // 2)
                right = min(len(text), pos + len(query) + snippet_chars // 2)
                snippet = text[left:right].replace("\n", " ").strip()
                hits.append(
                    {
                        "chapter_index": idx,
                        "href": href,
                        "title": self._extract_title(epub_path, href)
                        or f"Chapter {idx + 1}",
                        "snippet": snippet,
                    }
                )
            return {
                "status": "success",
                "query": query,
                "hits": hits[:top_k],
                "hit_count": len(hits),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _resolve(self, path: str) -> Path:
        p = (
            (self.workspace_root / path).resolve()
            if not Path(path).is_absolute()
            else Path(path).resolve()
        )
        if (
            not str(p).startswith(str(self.workspace_root))
            and not Path(path).is_absolute()
        ):
            raise PermissionError(f"Path outside workspace: {path}")
        if not p.exists():
            raise FileNotFoundError(str(p))
        return p

    def _chapter_files(self, epub_path: Path) -> List[str]:
        with zipfile.ZipFile(epub_path, "r") as zf:
            container = ET.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            if rootfile is None:
                raise ValueError("Invalid EPUB: missing rootfile")
            opf_path = rootfile.attrib.get("full-path")
            if not opf_path:
                raise ValueError("Invalid EPUB: missing OPF path")
            opf = ET.fromstring(zf.read(opf_path))
            manifest = {
                item.attrib["id"]: item.attrib.get("href", "")
                for item in opf.findall(".//{*}manifest/{*}item")
            }
            spine_ids = [
                item.attrib.get("idref", "")
                for item in opf.findall(".//{*}spine/{*}itemref")
            ]
            base = str(Path(opf_path).parent).replace("\\", "/")
            out: List[str] = []
            for sid in spine_ids:
                href = manifest.get(sid, "")
                if not href:
                    continue
                full = f"{base}/{href}" if base and base != "." else href
                out.append(full)
            return out

    def _read_zip_text(self, epub_path: Path, inner_path: str) -> str:
        with zipfile.ZipFile(epub_path, "r") as zf:
            data = zf.read(inner_path)
        return data.decode("utf-8", errors="ignore")

    def _extract_title(self, epub_path: Path, chapter_path: str) -> Optional[str]:
        try:
            html = self._read_zip_text(epub_path, chapter_path)
            m = re.search(
                r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL
            )
            if m:
                return re.sub(r"\s+", " ", m.group(1)).strip()
        except Exception:
            return None
        return None

    def _html_to_text(self, html: str) -> str:
        html = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
        html = re.sub(r"(?is)<style.*?>.*?</style>", " ", html)
        text = re.sub(r"(?is)<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()


__all__ = ["EpubToolSet"]
