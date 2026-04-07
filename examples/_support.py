"""Reusable helpers for local example smoke runs."""

from __future__ import annotations

import contextlib
import http.server
import socketserver
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from qitos.core import TerminalCapability


class SequenceModel:
    """Deterministic callable model that returns one scripted output per call."""

    def __init__(
        self,
        outputs: Iterable[str | Callable[[list[dict[str, Any]]], str]],
        *,
        model: str = "smoke-model",
    ):
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, Any]]] = []
        self.model = model

    def __call__(self, messages: list[dict[str, Any]], **_: Any) -> str:
        self.calls.append(list(messages))
        if not self.outputs:
            return "Final Answer: smoke complete"
        item = self.outputs.pop(0)
        return item(messages) if callable(item) else str(item)


class FakeTerminal(TerminalCapability):
    """Minimal in-memory terminal backend for deterministic terminal-agent smoke runs."""

    def __init__(self):
        self.alive = True
        self.screen = "$ "
        self.buffer = "$ "
        self.previous: str | None = None
        self.sent: list[str] = []
        self.waits: list[float] = []
        self.closed = False
        self.ts = 0.0

    def reset_session(self, cwd: str | None = None) -> None:
        _ = cwd
        self.screen = "$ "
        self.buffer = "$ "
        self.previous = None
        self.alive = True

    def close_session(self) -> None:
        self.closed = True
        self.alive = False

    def send_keys(
        self,
        keys: str | list[str],
        min_timeout_sec: float = 0.0,
        block: bool = False,
        max_timeout_sec: float = 180.0,
    ) -> dict[str, Any]:
        _ = block
        _ = max_timeout_sec
        text = "".join(keys) if isinstance(keys, list) else str(keys)
        self.sent.append(text)
        self.waits.append(float(min_timeout_sec))
        self.ts += 1.0
        stripped = text.strip()
        if stripped == "pwd":
            update = "/workspace\n$ "
        elif stripped.startswith("ls"):
            update = "README.txt\nnotes.txt\n$ "
        elif stripped:
            update = f"executed: {stripped}\n$ "
        else:
            update = self.screen
        self.buffer += update
        self.screen = update
        return {
            "status": "success",
            "keys": text,
            "waited_seconds": min_timeout_sec,
            "block": block,
        }

    def capture_screen(self) -> str:
        return self.screen

    def capture_buffer(self) -> str:
        return self.buffer

    def get_incremental_output(self) -> str:
        current = self.buffer
        if self.previous is None:
            self.previous = current
            return f"Current Terminal Screen:\n{self.screen}"
        if self.previous in current:
            idx = current.index(self.previous) + len(self.previous)
            delta = current[idx:].lstrip("\n")
        else:
            delta = self.screen
        self.previous = current
        if delta.strip():
            return f"New Terminal Output:\n{delta}"
        return f"Current Terminal Screen:\n{self.screen}"

    def is_session_alive(self) -> bool:
        return self.alive

    def get_timestamp(self) -> float | None:
        return self.ts


@contextlib.contextmanager
def local_html_server(html: str) -> Iterator[str]:
    """Serve one local HTML file over a temporary loopback HTTP server."""

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            _ = format
            _ = args

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "index.html").write_text(html, encoding="utf-8")
        handler = lambda *args, **kwargs: QuietHandler(  # noqa: E731
            *args, directory=tmpdir, **kwargs
        )
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
            finally:
                server.shutdown()
                thread.join(timeout=2.0)


def write_minimal_epub(path: Path) -> None:
    """Write a tiny valid EPUB with one XHTML chapter for smoke tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            "<?xml version=\"1.0\"?>"
            "<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">"
            "<rootfiles><rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\"/>"
            "</rootfiles></container>",
        )
        zf.writestr(
            "OEBPS/content.opf",
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<package xmlns=\"http://www.idpf.org/2007/opf\" version=\"2.0\">"
            "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
            "<dc:title>Smoke Book</dc:title></metadata>"
            "<manifest><item id=\"chap1\" href=\"chapter1.xhtml\" media-type=\"application/xhtml+xml\"/></manifest>"
            "<spine><itemref idref=\"chap1\"/></spine></package>",
        )
        zf.writestr(
            "OEBPS/chapter1.xhtml",
            "<html xmlns=\"http://www.w3.org/1999/xhtml\"><head><title>Chapter 1</title></head>"
            "<body><h1>Chapter 1</h1><p>The main argument of chapter 1 is that tests should be small and reliable.</p></body></html>",
        )


__all__ = ["FakeTerminal", "SequenceModel", "local_html_server", "write_minimal_epub"]
