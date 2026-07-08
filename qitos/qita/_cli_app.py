"""qita CLI: web board, trace viewer, replay, and export."""

from __future__ import annotations

import argparse
import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse


# ---------------------------------------------------------------------------
# Design tokens — DESIGN.md Linear-inspired visual system
# ---------------------------------------------------------------------------

_DESIGN_HEAD = """\
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet">
<script>
(function(){
  var key = 'qita_theme';
  function systemTheme(){
    try { return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; }
    catch(e) { return 'dark'; }
  }
  function apply(theme){
    var next = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    var label = document.getElementById('qitaThemeLabel');
    if(label) label.textContent = next === 'light' ? 'Dark' : 'Light';
  }
  window.qitaSetTheme = function(theme){
    try { localStorage.setItem(key, theme === 'light' ? 'light' : 'dark'); } catch(e) {}
    apply(theme);
  };
  window.qitaToggleTheme = function(){
    var current = document.documentElement.getAttribute('data-theme') || systemTheme();
    window.qitaSetTheme(current === 'light' ? 'dark' : 'light');
  };
  try { apply(localStorage.getItem(key) || systemTheme()); } catch(e) { apply(systemTheme()); }
  document.addEventListener('DOMContentLoaded', function(){ apply(document.documentElement.getAttribute('data-theme') || systemTheme()); });
})();
</script>"""

_DESIGN_TOKENS = """\
:root,:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#010102;--surface-1:#0f1011;--surface-2:#141516;--surface-3:#18191a;--surface-4:#191a1b;
  --accent:#5e6ad2;--accent-hover:#828fff;--accent-focus:#5e69d1;
  --txt:#f7f8f8;--muted:#d0d6e0;--subtle:#8a8f98;--tertiary:#62666d;
  --line:#23252a;--line-strong:#34343a;--line-tertiary:#3e3e44;
  --ok:#27a644;--err:#e5484d;--warn:#e5c100;
  --ok-soft:rgba(39,166,68,.12);--ok-border:rgba(39,166,68,.45);
  --err-soft:rgba(229,72,77,.10);--err-border:rgba(229,72,77,.45);
  --warn-soft:rgba(229,193,0,.12);--warn-border:rgba(229,193,0,.45);
  --accent-soft:rgba(94,106,210,.14);--accent-border:rgba(94,106,210,.50);
  --top-bg:rgba(1,1,2,.90);--shadow-soft:0 18px 42px rgba(0,0,0,.26);
  --kind-thinking:#8b8fe0;--kind-action:#2da46a;--kind-observation:#5a8fbf;
  --kind-critic:#bfa04e;--kind-handoff:#bfa04e;--kind-delegation:#6b8fc4;
  --kind-fanout:#9b7fd4;--kind-parser:#bfa04e;--kind-memory:#3da89c;
  --kind-done:#c47070;--kind-error:#e5484d;--kind-other:#5a6578;--kind-plan:#7a80cc;
  --radius-xs:4px;--radius-sm:6px;--radius-md:8px;--radius-lg:12px;--radius-xl:16px;--radius-pill:9999px;
  --font-body:'Inter','SF Pro Display',-apple-system,system-ui,'Segoe UI',Roboto,sans-serif;
  --font-mono:'JetBrains Mono','Geist Mono',ui-monospace,'SF Mono',Menlo,monospace;
}
:root[data-theme="light"]{
  color-scheme:light;
  --bg:#f7f8fb;--surface-1:#ffffff;--surface-2:#f2f4f8;--surface-3:#e9edf5;--surface-4:#e3e8f2;
  --accent:#4f46e5;--accent-hover:#4338ca;--accent-focus:#6366f1;
  --txt:#111827;--muted:#4b5563;--subtle:#6b7280;--tertiary:#9ca3af;
  --line:#d9dee8;--line-strong:#c4ccda;--line-tertiary:#aeb8ca;
  --ok:#15803d;--err:#dc2626;--warn:#b7791f;
  --ok-soft:rgba(21,128,61,.10);--ok-border:rgba(21,128,61,.34);
  --err-soft:rgba(220,38,38,.09);--err-border:rgba(220,38,38,.32);
  --warn-soft:rgba(183,121,31,.12);--warn-border:rgba(183,121,31,.36);
  --accent-soft:rgba(79,70,229,.10);--accent-border:rgba(79,70,229,.34);
  --top-bg:rgba(247,248,251,.88);--shadow-soft:0 18px 42px rgba(15,23,42,.10);
  --kind-thinking:#5b5bd6;--kind-action:#16834f;--kind-observation:#2563a8;
  --kind-critic:#946200;--kind-handoff:#946200;--kind-delegation:#3f6aa3;
  --kind-fanout:#7c4db2;--kind-parser:#946200;--kind-memory:#0f766e;
  --kind-done:#b45309;--kind-error:#dc2626;--kind-other:#64748b;--kind-plan:#4f46e5;
}
.theme-toggle{min-width:64px;justify-content:center}
.theme-toggle span{font-weight:600}
*{transition:background-color .12s ease,border-color .12s ease,color .12s ease}
"""

_THEME_TOGGLE_HTML = (
    '<button class="btn theme-toggle" type="button" onclick="qitaToggleTheme()" '
    'title="Toggle light/dark theme"><span id="qitaThemeLabel">Light</span></button>'
)

_DESIGN_FONT_BODY = "var(--font-body)"
_DESIGN_FONT_MONO = "var(--font-mono)"


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--version":
        from qitos import __version__
        print(f"qita {__version__}")
        return 0
    parser = argparse.ArgumentParser(prog="qita", description="QitOS trace tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p_board = sub.add_parser("board", help="Start qita web board")
    p_board.add_argument("--logdir", default="./runs", help="Trace runs root directory")
    p_board.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_board.add_argument("--port", type=int, default=8765, help="Bind port")

    p_replay = sub.add_parser("replay", help="Open one run in web replay mode")
    p_replay.add_argument("--run", required=True, help="Run directory path")
    p_replay.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_replay.add_argument("--port", type=int, default=8765, help="Bind port")

    p_export = sub.add_parser("export", help="Export one run to standalone HTML")
    p_export.add_argument("--run", required=True, help="Run directory path")
    p_export.add_argument("--html", required=True, help="Output html file path")

    args = parser.parse_args(args)
    if args.command == "board":
        return _cmd_board(
            logdir=args.logdir,
            host=args.host,
            port=args.port,
            focus_run_id=None,
            replay=False,
        )
    if args.command == "replay":
        return _cmd_replay(run=args.run, host=args.host, port=args.port)
    if args.command == "export":
        return _cmd_export(run=args.run, html_path=args.html)
    return 1


def _cmd_board(
    logdir: str, host: str, port: int, focus_run_id: Optional[str], replay: bool
) -> int:
    root = Path(logdir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    handler_cls = _build_handler(root=root)
    server = ThreadingHTTPServer((host, port), handler_cls)
    path = "/"
    if focus_run_id:
        safe_id = _slug_run_id(focus_run_id)
        path = f"/replay/{safe_id}" if replay else f"/run/{safe_id}"
    print(f"[qita] board logdir: {root}")
    print(f"[qita] runs discovered: {len(_discover_runs(root))}")
    print(f"[qita] open: http://{host}:{port}{path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[qita] board stopped")
    return 0


def _cmd_replay(run: str, host: str, port: int) -> int:
    run_dir = Path(run).resolve()
    if not run_dir.exists():
        raise FileNotFoundError(f"Run dir not found: {run_dir}")
    root = run_dir.parent
    run_id = run_dir.name
    return _cmd_board(
        logdir=str(root), host=host, port=port, focus_run_id=run_id, replay=True
    )


def _cmd_export(run: str, html_path: str) -> int:
    run_dir = Path(run).resolve()
    if not run_dir.exists():
        raise FileNotFoundError(f"Run dir not found: {run_dir}")
    payload = _load_run_payload(run_dir)
    out = Path(html_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render_run_html(payload, embedded=True), encoding="utf-8")
    print(f"[qita] exported: {out}")
    return 0


def _build_handler(root: Path):
    class QitaHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path
            qs = parse_qs(parsed.query)
            if route == "/":
                self._send_html(_render_board_html())
                return
            if route == "/favicon.ico":
                self._send_bytes(b"", content_type="image/x-icon", status=204)
                return
            if route == "/compare":
                left_id = _slug_run_id((qs.get("left") or [""])[0])
                right_id = _slug_run_id((qs.get("right") or [""])[0])
                if not left_id or not right_id:
                    self._send_html(_render_compare_prompt(), status=400)
                    return
                left_dir = _resolve_run(root, left_id)
                right_dir = _resolve_run(root, right_id)
                if left_dir is None or right_dir is None:
                    self._send_html(_render_not_found(left_id if left_dir is None else right_id), status=404)
                    return
                self._send_html(
                    _render_diff_html(
                        _build_run_diff(
                            _load_run_payload(left_dir),
                            _load_run_payload(right_dir),
                        ),
                        embedded=False,
                    )
                )
                return
            if route.startswith("/compare-branches/"):
                # /compare-branches/{run_id}/{step_id}
                parts = route.split("/")
                if len(parts) >= 4:
                    cb_run_id = _slug_run_id(parts[2])
                    cb_step_id = parts[3]
                    cb_dir = _resolve_run(root, cb_run_id)
                    if cb_dir is None:
                        self._send_html(_render_not_found(cb_run_id), status=404)
                        return
                    cb_payload = _load_run_payload(cb_dir)
                    self._send_html(
                        _render_branch_comparison_html(cb_payload, cb_step_id)
                    )
                    return
                self._send_html(_render_compare_prompt(), status=400)
                return
            if route == "/api/runs":
                self._send_json(_discover_runs(root))
                return
            if route == "/api/diff":
                left_id = _slug_run_id((qs.get("left") or [""])[0])
                right_id = _slug_run_id((qs.get("right") or [""])[0])
                left_dir = _resolve_run(root, left_id)
                right_dir = _resolve_run(root, right_id)
                if left_dir is None or right_dir is None:
                    self._send_json(
                        {
                            "error": "run not found",
                            "left": left_id,
                            "right": right_id,
                        },
                        status=404,
                    )
                    return
                self._send_json(
                    _build_run_diff(
                        _load_run_payload(left_dir),
                        _load_run_payload(right_dir),
                    )
                )
                return
            if route.startswith("/api/run/"):
                run_id = _slug_run_id(route.split("/", 3)[-1])
                run_dir = _resolve_run(root, run_id)
                if run_dir is None:
                    self._send_json(
                        {"error": "run not found", "run_id": run_id}, status=404
                    )
                    return
                self._send_json(_load_run_payload(run_dir))
                return
            if route.startswith("/api/stream/"):
                run_id = _slug_run_id(route.split("/", 3)[-1])
                run_dir = _resolve_run(root, run_id)
                if run_dir is None:
                    self._send_json(
                        {"error": "run not found", "run_id": run_id}, status=404
                    )
                    return
                self._send_sse_events(run_dir)
                return
            if route.startswith("/api/live/"):
                run_id = _slug_run_id(route.split("/", 3)[-1])
                run_dir = _resolve_run(root, run_id)
                if run_dir is None:
                    self._send_json(
                        {"error": "run not found", "run_id": run_id}, status=404
                    )
                    return
                self._send_live_sse(run_dir)
                return
            if route == "/asset":
                path = str((qs.get("path") or [""])[0]).strip()
                if not path:
                    self._send_json({"error": "missing asset path"}, status=400)
                    return
                asset_path = Path(path).expanduser().resolve()
                if not asset_path.exists() or not asset_path.is_file():
                    self._send_json(
                        {"error": "asset not found", "path": str(asset_path)},
                        status=404,
                    )
                    return
                body = asset_path.read_bytes()
                guessed = mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream"
                self._send_bytes(body, content_type=guessed)
                return
            if route.startswith("/run/"):
                run_id = _slug_run_id(route.split("/", 2)[-1])
                run_dir = _resolve_run(root, run_id)
                if run_dir is None:
                    self._send_html(_render_not_found(run_id), status=404)
                    return
                self._send_html(
                    _render_run_html(_load_run_payload(run_dir), embedded=False)
                )
                return
            if route.startswith("/replay/"):
                run_id = _slug_run_id(route.split("/", 2)[-1])
                run_dir = _resolve_run(root, run_id)
                if run_dir is None:
                    self._send_html(_render_not_found(run_id), status=404)
                    return
                speed = int((qs.get("speed") or ["500"])[0])
                self._send_html(
                    _render_replay_html(
                        _load_run_payload(run_dir), speed_ms=max(100, speed)
                    )
                )
                return
            if route.startswith("/export/raw/"):
                run_id = _slug_run_id(route.split("/", 3)[-1])
                run_dir = _resolve_run(root, run_id)
                if run_dir is None:
                    self._send_json(
                        {"error": "run not found", "run_id": run_id}, status=404
                    )
                    return
                payload = _load_run_payload(run_dir)
                body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self._send_bytes(
                    body,
                    content_type="application/json; charset=utf-8",
                    headers={
                        "Content-Disposition": f'attachment; filename="{run_id}.json"'
                    },
                )
                return
            if route.startswith("/export/html/"):
                run_id = _slug_run_id(route.split("/", 3)[-1])
                run_dir = _resolve_run(root, run_id)
                if run_dir is None:
                    self._send_json(
                        {"error": "run not found", "run_id": run_id}, status=404
                    )
                    return
                payload = _load_run_payload(run_dir)
                body = _render_run_html(payload, embedded=True).encode("utf-8")
                self._send_bytes(
                    body,
                    content_type="text/html; charset=utf-8",
                    headers={
                        "Content-Disposition": f'attachment; filename="{run_id}.html"'
                    },
                )
                return
            if route.startswith("/export/diff/"):
                parts = route.split("/")
                if len(parts) < 5:
                    self._send_json({"error": "invalid diff export route"}, status=400)
                    return
                left_id = _slug_run_id(parts[-2])
                right_id = _slug_run_id(parts[-1])
                left_dir = _resolve_run(root, left_id)
                right_dir = _resolve_run(root, right_id)
                if left_dir is None or right_dir is None:
                    self._send_json(
                        {
                            "error": "run not found",
                            "left": left_id,
                            "right": right_id,
                        },
                        status=404,
                    )
                    return
                body = _render_diff_html(
                    _build_run_diff(
                        _load_run_payload(left_dir),
                        _load_run_payload(right_dir),
                    ),
                    embedded=True,
                ).encode("utf-8")
                self._send_bytes(
                    body,
                    content_type="text/html; charset=utf-8",
                    headers={
                        "Content-Disposition": f'attachment; filename="{left_id}_vs_{right_id}.html"'
                    },
                )
                return
            self._send_json({"error": "not found", "route": route}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path

            # POST /api/fork/{run_id}/{step_id}
            import re as _re
            fork_match = _re.match(r"^/api/fork/([^/]+)/(\d+)$", route)
            if fork_match:
                run_id = _slug_run_id(fork_match.group(1))
                step_id = int(fork_match.group(2))
                # Read body
                content_length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(content_length) if content_length > 0 else b""
                try:
                    body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
                except (json.JSONDecodeError, UnicodeDecodeError):
                    body = {}

                override_decision = body.get("override_decision")
                override_observation = body.get("override_observation")

                # Resolve run directory
                run_dir = None
                for candidate in _discover_runs(logdir_root):
                    if candidate.get("run_id") == run_id or Path(candidate.get("path", "")).name == run_id:
                        run_dir = Path(candidate["path"])
                        break

                if run_dir is None or not run_dir.is_dir():
                    self._send_json({"error": f"Run not found: {run_id}"}, status=404)
                    return

                # Use ReplaySession to fork
                from qitos.debug.replay import ReplaySession
                try:
                    session = ReplaySession(str(run_dir))
                    override = {}
                    if override_decision:
                        override["decision"] = override_decision
                    if override_observation:
                        override["observation"] = override_observation
                    forked = session.fork_with_step_override(step_id, override)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=500)
                    return

                # Write forked run as a new run directory
                fork_run_id = f"{run_id}_fork_s{step_id}"
                fork_dir = run_dir.parent / fork_run_id
                fork_dir.mkdir(parents=True, exist_ok=True)
                if "manifest" in forked:
                    (fork_dir / "manifest.json").write_text(
                        json.dumps(forked["manifest"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                if "events" in forked:
                    lines = [json.dumps(e, ensure_ascii=False) for e in forked["events"]]
                    (fork_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
                if "steps" in forked:
                    lines = [json.dumps(s, ensure_ascii=False) for s in forked["steps"]]
                    (fork_dir / "steps.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

                self._send_json({
                    "fork_run_id": fork_run_id,
                    "fork_dir": str(fork_dir),
                    "step_id": step_id,
                })
                return

            self._send_json({"error": "not found", "route": route}, status=404)

        def log_message(self, fmt: str, *args: Any) -> None:
            # Keep console clean; qita already prints startup summary.
            _ = fmt
            _ = args

        def _send_html(self, body: str, status: int = 200) -> None:
            self._send_bytes(
                body.encode("utf-8"),
                content_type="text/html; charset=utf-8",
                status=status,
            )

        def _send_json(self, obj: Any, status: int = 200) -> None:
            self._send_bytes(
                json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                content_type="application/json; charset=utf-8",
                status=status,
            )

        def _send_bytes(
            self,
            body: bytes,
            content_type: str,
            status: int = 200,
            headers: Optional[Dict[str, str]] = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_sse_events(self, run_dir: Path) -> None:
            """Stream run events as Server-Sent Events for real-time UI updates."""
            import time as _time

            payload = _load_run_payload(run_dir)
            steps = payload.get("steps", [])
            events = payload.get("events", [])

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            # Emit run_start
            self._sse_write("run_start", {
                "run_id": payload.get("run_id", ""),
                "task": payload.get("task", ""),
                "agent_name": payload.get("agent_name", ""),
            })

            # Emit step events with small delays for visual effect
            for step in steps:
                step_id = step.get("step_id", 0)
                agent_id = step.get("agent_id")
                self._sse_write("step_start", {
                    "step_id": step_id,
                    "agent_id": agent_id,
                })

                # Emit phase events for this step
                step_events = [
                    e for e in events
                    if e.get("step_id") == step_id
                ]
                for event in step_events:
                    phase = event.get("phase", "")
                    if "HANDOFF" in phase:
                        self._sse_write("handoff", event)
                    elif "DELEGATE" in phase:
                        self._sse_write("delegate", event)
                    elif "FANOUT" in phase:
                        self._sse_write("fanout", event)
                    else:
                        self._sse_write("phase", event)

                self._sse_write("step_end", {
                    "step_id": step_id,
                    "agent_id": agent_id,
                })

            # Emit run_end
            self._sse_write("run_end", {
                "step_count": len(steps),
                "stop_reason": payload.get("stop_reason", ""),
            })

        def _sse_write(self, event_type: str, data: Any) -> None:
            """Write a single SSE event to the response stream."""
            import struct

            payload = json.dumps(data, ensure_ascii=False, default=str)
            msg = f"event: {event_type}\ndata: {payload}\n\n"
            try:
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, struct.error):
                pass

        def _send_live_sse(self, run_dir: Path) -> None:
            """Tail events.jsonl for a running run and push new lines as SSE events."""
            import struct
            import time as _time

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            events_path = run_dir / "events.jsonl"
            sent = 0
            max_poll = 300  # 5 minutes at 1s intervals

            # First, emit any existing events
            if events_path.exists():
                for line in events_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    phase = str(event.get("phase", "unknown")).upper()
                    event_type = "phase"
                    if "HANDOFF" in phase:
                        event_type = "handoff"
                    elif "DELEGATE" in phase:
                        event_type = "delegate"
                    elif "FANOUT" in phase:
                        event_type = "fanout"
                    self._sse_write(event_type, event)
                    sent += 1

            # Now tail for new events
            for _ in range(max_poll):
                _time.sleep(1.0)
                if not events_path.exists():
                    continue
                try:
                    lines = events_path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                new_lines = lines[sent:]
                for line in new_lines:
                    line = line.strip()
                    if not line:
                        sent += 1
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        sent += 1
                        continue
                    phase = str(event.get("phase", "unknown")).upper()
                    event_type = "phase"
                    if "HANDOFF" in phase:
                        event_type = "handoff"
                    elif "DELEGATE" in phase:
                        event_type = "delegate"
                    elif "FANOUT" in phase:
                        event_type = "fanout"
                    self._sse_write(event_type, event)
                    sent += 1

                # Check if run is completed
                manifest_path = run_dir / "manifest.json"
                if manifest_path.exists():
                    try:
                        manifest = json.loads(
                            manifest_path.read_text(encoding="utf-8")
                        )
                        status = str(manifest.get("status", "")).lower()
                        if status in ("completed", "success", "failed", "error", "stopped"):
                            self._sse_write("run_end", {
                                "status": status,
                                "stop_reason": (manifest.get("summary") or {}).get("stop_reason", ""),
                            })
                            return
                    except (json.JSONDecodeError, OSError):
                        pass

            # Timeout
            self._sse_write("run_end", {"status": "timeout", "stop_reason": "live_stream_timeout"})

    return QitaHandler


def _resolve_run(root: Path, run_id: str) -> Optional[Path]:
    run_dir = (root / run_id).resolve()
    if run_dir.exists() and run_dir.is_dir() and run_dir.parent == root:
        return run_dir
    return None


def _slug_run_id(run_id: str) -> str:
    return "".join(c for c in run_id if c.isalnum() or c in ("-", "_", "."))


def _discover_runs(logdir: Path) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    if not logdir.exists():
        return runs
    for p in sorted(logdir.iterdir()):
        if not p.is_dir():
            continue
        manifest_path = p / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = _load_json(manifest_path)
        events = _load_jsonl(p / "events.jsonl")
        steps = _load_jsonl(p / "steps.jsonl")
        grouped_events = _group_events_by_step(events)
        step_summaries = _build_step_summaries(steps, grouped_events)
        tool_stats = _build_tool_stats(steps)
        phase_stats = _build_phase_stats(events)
        insights = _build_insights(manifest, step_summaries, tool_stats)
        step_focus = _build_step_focus(steps, step_summaries, grouped_events)
        cybergym_focus = _build_cybergym_focus(manifest, step_focus)
        run_focus = _build_run_focus(
            manifest=manifest,
            insights=insights,
            step_focus=step_focus,
            cybergym_focus=cybergym_focus,
        )
        summary = manifest.get("summary") or {}
        agent_topology = manifest.get("agent_topology")
        agent_names = []
        if isinstance(agent_topology, dict):
            agent_names = agent_topology.get("agents", [])
        elif manifest.get("agent_name"):
            agent_names = [manifest["agent_name"]]
        runs.append(
            {
                "id": p.name,
                "path": str(p),
                "status": manifest.get("status"),
                "updated_at": manifest.get("updated_at"),
                "step_count": manifest.get("step_count", 0),
                "event_count": manifest.get("event_count", 0),
                "stop_reason": summary.get("stop_reason"),
                "final_result": summary.get("final_result"),
                "agent_name": manifest.get("agent_name"),
                "agent_topology": agent_topology,
                "handoff_count": manifest.get("handoff_count"),
                "agent_count": len(agent_names) if agent_names else 0,
                "insights": insights,
                "run_focus": run_focus,
                "risk_flags": insights.get("risk_flags", []),
                "next_inspect_step": insights.get("next_inspect_step"),
                "tool_stats": tool_stats,
                "phase_stats": phase_stats,
                "manifest_meta": {
                    "schema_version": manifest.get("schema_version"),
                    "model_id": manifest.get("model_id"),
                    "model_family": manifest.get("model_family"),
                    "family_preset": (((summary.get("run_meta") or {}).get("harness") or {}).get("family_preset")),
                    "prompt_hash": manifest.get("prompt_hash"),
                    "benchmark_name": manifest.get("benchmark_name"),
                    "benchmark_split": manifest.get("benchmark_split"),
                    "prompt_builder": ((summary.get("run_meta") or {}).get("prompt") or {}).get("prompt_builder"),
                    "protocol": (summary.get("run_meta") or {}).get("protocol"),
                    "protocol_resolution_source": (summary.get("run_meta") or {}).get("protocol_resolution_source"),
                    "prompt_protocol": manifest.get("prompt_protocol"),
                    "parser_name": manifest.get("parser_name"),
                    "run_config_hash": manifest.get("run_config_hash"),
                    "seed": manifest.get("seed"),
                    "git_sha": manifest.get("git_sha"),
                    "package_version": manifest.get("package_version"),
                    "official_run": manifest.get("official_run"),
                    "replay_mode": manifest.get("replay_mode"),
                    "replay_note": manifest.get("replay_note"),
                    "summary_steps": summary.get("steps"),
                    "token_usage": _summary_metric(manifest, "token_usage", 0),
                    "latency_seconds": manifest.get("latency_seconds"),
                    "cost": manifest.get("cost"),
                    "context": summary.get("context"),
                    "parser": summary.get("parser"),
                    "run_spec": manifest.get("run_spec"),
                    "experiment_spec": manifest.get("experiment_spec"),
                },
            }
        )
    return runs


def _load_run_payload(run_dir: Path) -> Dict[str, Any]:
    manifest = _load_json(run_dir / "manifest.json")
    events = _load_jsonl(run_dir / "events.jsonl")
    steps = _load_jsonl(run_dir / "steps.jsonl")
    grouped_events = _group_events_by_step(events)
    step_interactions = _build_step_interactions(steps, grouped_events)
    step_summaries = _build_step_summaries(steps, grouped_events)
    tool_stats = _build_tool_stats(steps)
    phase_stats = _build_phase_stats(events)
    insights = _build_insights(manifest, step_summaries, tool_stats)
    step_focus = _build_step_focus(steps, step_summaries, grouped_events)
    cybergym_focus = _build_cybergym_focus(manifest, step_focus)
    run_focus = _build_run_focus(
        manifest=manifest,
        insights=insights,
        step_focus=step_focus,
        cybergym_focus=cybergym_focus,
    )
    return {
        "run": str(run_dir),
        "run_id": run_dir.name,
        "manifest": manifest,
        "events": events,
        "steps": steps,
        "events_by_step": grouped_events,
        "step_interactions": step_interactions,
        "insights": insights,
        "step_summaries": step_summaries,
        "tool_stats": tool_stats,
        "phase_stats": phase_stats,
        "run_focus": run_focus,
        "step_focus": step_focus,
        "cybergym_focus": cybergym_focus,
        "visual_timeline": _build_visual_timeline(steps),
    }


def _group_events_by_step(
    events: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        sid = str(ev.get("step_id", "none"))
        grouped.setdefault(sid, []).append(ev)
    return grouped


def _result_action_id(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return str(
        result.get("action_id")
        or result.get("tool_call_id")
        or metadata.get("action_id")
        or metadata.get("tool_call_id")
        or ""
    )


def _is_environment_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    metadata = result.get("metadata")
    if isinstance(metadata, dict) and str(metadata.get("source") or "").lower() == "env":
        return True
    output = result.get("output")
    return isinstance(output, dict) and set(output) == {"env"}


def _model_visible_action_results(events: List[Dict[str, Any]]) -> List[Any]:
    for event in reversed(events):
        payload = event.get("payload")
        if not isinstance(payload, dict) or str(payload.get("stage") or "") != "observation_ready":
            continue
        observation = payload.get("observation")
        if not isinstance(observation, dict):
            continue
        results = observation.get("action_results")
        if isinstance(results, list):
            return results
    for event in reversed(events):
        payload = event.get("payload")
        if not isinstance(payload, dict) or str(payload.get("stage") or "") != "action_results":
            continue
        results = payload.get("action_results")
        if isinstance(results, list):
            return results
    return []


def _interaction_status(tool_name: str, result: Any, raw_result: Any) -> str:
    probes = [raw_result, result]
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        explicit_status = str(probe.get("status") or "").lower()
        if explicit_status in {
            "blocked",
            "submission_error",
            "no_trigger",
            "verified",
        }:
            return explicit_status
        if _result_error(probe):
            return "error"
        output = probe.get("output")
        if not isinstance(output, dict):
            continue
        verification = str(
            output.get("verification_status")
            or output.get("failure_type")
            or ""
        ).strip()
        if verification:
            return verification.lower()
        if str(tool_name).rsplit(".", 1)[-1] == "submit_poc":
            if output.get("accepted") is True or output.get("verified") is True:
                return "verified"
            if output.get("accepted") is False:
                return "no_trigger"
    for probe in probes:
        if isinstance(probe, dict) and probe.get("status"):
            return str(probe.get("status")).lower()
    return "not_recorded"


def _build_step_interactions(
    steps: List[Dict[str, Any]], events_by_step: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    interactions: List[Dict[str, Any]] = []
    for step in steps:
        sid = step.get("step_id")
        actions = step.get("actions") if isinstance(step.get("actions"), list) else []
        invocations = (
            step.get("tool_invocations")
            if isinstance(step.get("tool_invocations"), list)
            else []
        )
        raw_results = (
            step.get("action_results")
            if isinstance(step.get("action_results"), list)
            else []
        )
        recorded_visible_results = _model_visible_action_results(
            events_by_step.get(str(sid), [])
        )
        has_model_visible_results = bool(recorded_visible_results)
        visible_results = recorded_visible_results or list(raw_results)

        raw_tool_results = [
            (index, result)
            for index, result in enumerate(raw_results)
            if not _is_environment_result(result)
        ]
        visible_tool_results = [
            (index, result)
            for index, result in enumerate(visible_results)
            if not _is_environment_result(result)
        ]
        raw_by_action_id = {
            _result_action_id(result): (index, result)
            for index, result in raw_tool_results
            if _result_action_id(result)
        }
        visible_by_action_id = {
            _result_action_id(result): (index, result)
            for index, result in visible_tool_results
            if _result_action_id(result)
        }
        used_raw: set[int] = set()
        used_visible: set[int] = set()
        calls: List[Dict[str, Any]] = []
        unmatched_actions: List[Dict[str, Any]] = []

        for action_index, action in enumerate(actions):
            action = action if isinstance(action, dict) else {"value": action}
            action_id = str(action.get("action_id") or action.get("id") or "")
            raw_match = raw_by_action_id.get(action_id) if action_id else None
            visible_match = visible_by_action_id.get(action_id) if action_id else None
            pairing_method = "action_id" if raw_match or visible_match else "ordered"
            if raw_match is None and action_index < len(raw_tool_results):
                raw_match = raw_tool_results[action_index]
            if visible_match is None and action_index < len(visible_tool_results):
                visible_match = visible_tool_results[action_index]
            raw_result = raw_match[1] if raw_match else None
            visible_result = visible_match[1] if visible_match else None
            if raw_match:
                used_raw.add(raw_match[0])
            if visible_match:
                used_visible.add(visible_match[0])
            if raw_result is None and visible_result is None:
                pairing_method = "unmatched"
                unmatched_actions.append(
                    {"index": action_index, "action": action}
                )
            invocation = (
                invocations[action_index]
                if action_index < len(invocations)
                and isinstance(invocations[action_index], dict)
                else {}
            )
            tool_name = str(
                action.get("name")
                or action.get("tool")
                or action.get("action")
                or action.get("type")
                or invocation.get("tool_name")
                or "action"
            )
            args = action.get("args")
            if not isinstance(args, dict):
                args = action.get("kwargs") if isinstance(action.get("kwargs"), dict) else {}
            status = _interaction_status(tool_name, visible_result, raw_result)
            if status == "not_recorded" and invocation.get("status"):
                status = str(invocation.get("status")).lower()
            result_for_summary = visible_result if visible_result is not None else raw_result
            result_summary = _result_error(result_for_summary) or _result_success_summary(
                result_for_summary
            )
            if status in {"blocked", "no_trigger", "submission_error", "verified"}:
                result_summary = " · ".join(
                    part for part in (status, result_summary) if part
                )
            calls.append(
                {
                    "index": action_index,
                    "action_id": action_id,
                    "tool_name": tool_name,
                    "args": args,
                    "action": action,
                    "invocation": invocation,
                    "result": visible_result,
                    "raw_result": raw_result,
                    "result_source": (
                        "model_visible"
                        if has_model_visible_results and visible_result is not None
                        else "raw_fallback"
                        if visible_result is not None
                        else "not_recorded"
                    ),
                    "status": status,
                    "result_summary": result_summary or "not recorded",
                    "latency_ms": invocation.get("latency_ms"),
                    "attempts": invocation.get("attempts"),
                    "pairing_method": pairing_method,
                }
            )

        environment_results: List[Dict[str, Any]] = []
        visible_env = [result for result in visible_results if _is_environment_result(result)]
        raw_env = [result for result in raw_results if _is_environment_result(result)]
        for index in range(max(len(visible_env), len(raw_env))):
            environment_results.append(
                {
                    "index": index,
                    "result": visible_env[index] if index < len(visible_env) else None,
                    "raw_result": raw_env[index] if index < len(raw_env) else None,
                }
            )

        unmatched_results = (
            [
                {"index": index, "result": result, "visibility": "model_visible"}
                for index, result in visible_tool_results
                if index not in used_visible
            ]
            if has_model_visible_results
            else []
        )
        unmatched_results.extend(
            {
                "index": index,
                "result": result,
                "visibility": "raw",
            }
            for index, result in raw_tool_results
            if index not in used_raw
        )
        interactions.append(
            {
                "step_id": sid,
                "calls": calls,
                "environment_results": environment_results,
                "unmatched_actions": unmatched_actions,
                "unmatched_results": unmatched_results,
            }
        )
    return interactions


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"raw": line, "error": "invalid_json"})
    return out


def _shorten_for_summary(value: Any, limit: int = 140) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _event_model_output(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    for event in reversed(events):
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if str(payload.get("stage") or "") == "model_output":
            return payload
    return {}


def _extract_summary_thought(step: Dict[str, Any], events: List[Dict[str, Any]]) -> str:
    decision = step.get("decision")
    if isinstance(decision, dict):
        for key in ("rationale", "thought", "analysis"):
            value = decision.get(key)
            if isinstance(value, str) and value.strip():
                return _shorten_for_summary(value)
    payload = _event_model_output(events)
    raw = str(payload.get("raw_output") or "")
    if not raw:
        response = payload.get("model_response")
        if isinstance(response, dict):
            raw = str(response.get("text") or "")
    if not raw:
        response = step.get("model_response")
        if isinstance(response, dict):
            raw = str(response.get("text") or "")
    if not raw:
        return ""
    marker = "Thought:"
    idx = raw.lower().find(marker.lower())
    if idx >= 0:
        raw = raw[idx + len(marker) :]
    for stop in ("\nAction:", "\nFinal:", "\nObservation:", "\nCritic:", "\nPlan:"):
        pos = raw.lower().find(stop.lower())
        if pos >= 0:
            raw = raw[:pos]
    return _shorten_for_summary(raw)


def _action_name(action: Dict[str, Any]) -> str:
    return str(
        action.get("tool")
        or action.get("name")
        or action.get("action")
        or action.get("type")
        or "action"
    )


def _step_action_summary(step: Dict[str, Any]) -> str:
    actions = step.get("actions")
    if not isinstance(actions, list) or not actions:
        return ""
    action = actions[0]
    if not isinstance(action, dict):
        return _shorten_for_summary(action)
    name = _action_name(action)
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    for key in ("query", "url", "path", "command", "prompt", "file", "text", "reason"):
        if key in args:
            return f"{name}({key}={_shorten_for_summary(args.get(key), 80)})"
    return name


def _result_error(result: Any) -> str:
    if isinstance(result, dict):
        status = str(result.get("status") or "").lower()
        if status == "error" or result.get("error"):
            return _shorten_for_summary(
                result.get("error") or result.get("message") or result
            )
        for key in ("result", "data", "payload"):
            nested = result.get(key)
            if isinstance(nested, dict):
                err = _result_error(nested)
                if err:
                    return err
    return ""


def _summary_value(value: Any, *, limit: int = 120) -> str:
    if isinstance(value, str):
        return _shorten_for_summary(value, limit)
    if isinstance(value, (int, float, bool)):
        return str(value)
    if value is None:
        return ""
    try:
        return _shorten_for_summary(json.dumps(value, ensure_ascii=False), limit)
    except TypeError:
        return _shorten_for_summary(value, limit)


def _result_success_summary(result: Any) -> str:
    if not isinstance(result, dict):
        return _summary_value(result)
    if _result_error(result):
        return ""
    status = str(result.get("status") or "").strip()
    output = result.get("output")
    if output is None:
        output = result.get("result")
    if output is None:
        output = result.get("data")

    parts: List[str] = []
    if status:
        parts.append(status)
    if isinstance(output, dict):
        nested_status = str(output.get("status") or "").strip()
        if nested_status and nested_status not in parts:
            parts.append(nested_status)
        for key in ("path", "file", "url", "command", "poc_path"):
            if output.get(key):
                parts.append(f"{key}={_summary_value(output.get(key), limit=80)}")
                break
        if output.get("total_lines") is not None:
            parts.append(f"total_lines={output.get('total_lines')}")
        if output.get("offset") is not None:
            parts.append(f"offset={output.get('offset')}")
        for key in ("content", "stdout", "stderr", "text", "message", "summary"):
            if output.get(key):
                parts.append(_summary_value(output.get(key), limit=160))
                break
        if not parts:
            parts.append(_summary_value(output, limit=180))
    elif output not in (None, ""):
        parts.append(_summary_value(output, limit=180))

    if not parts:
        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            tool_name = metadata.get("tool_name")
            latency = metadata.get("latency_ms")
            if tool_name:
                parts.append(f"{tool_name} completed")
            if latency is not None:
                try:
                    parts.append(f"{float(latency):.1f}ms")
                except (TypeError, ValueError):
                    pass
    return _shorten_for_summary(" · ".join(part for part in parts if part), 220)


def _step_observation_summary(step: Dict[str, Any]) -> str:
    action_results = step.get("action_results")
    if isinstance(action_results, list):
        for result in action_results:
            summary = _result_success_summary(result)
            if summary:
                return summary
    observation = step.get("observation")
    if isinstance(observation, dict):
        env = observation.get("env")
        if isinstance(env, dict):
            env_observation = env.get("observation")
            if isinstance(env_observation, dict):
                data = env_observation.get("data")
                if data not in (None, {}, ""):
                    return _shorten_for_summary(
                        f"observation data: {_summary_value(data, limit=180)}",
                        220,
                    )
        if observation:
            return _shorten_for_summary(
                f"observation: {_summary_value(observation, limit=180)}",
                220,
            )
    return ""


def _after_value(value: Any) -> Any:
    if isinstance(value, dict):
        after = value.get("after")
        if after is not None:
            return after
        return value
    return value


def _cybergym_signals(step: Dict[str, Any]) -> Dict[str, Any]:
    state_diff = step.get("state_diff")
    if not isinstance(state_diff, dict):
        state_diff = {}
    actions = step.get("actions")
    if not isinstance(actions, list):
        actions = []
    action_names = [
        _action_name(action)
        for action in actions
        if isinstance(action, dict)
    ]
    submit_action = any(name == "submit_poc" for name in action_names)

    phase = ""
    phase_change = state_diff.get("current_phase")
    if isinstance(phase_change, dict):
        phase = str(phase_change.get("after") or "")
    elif isinstance(phase_change, str):
        phase = phase_change

    attempts = None
    attempts_change = state_diff.get("poc_attempts")
    if isinstance(attempts_change, dict):
        attempts = attempts_change.get("after")
    elif attempts_change is not None:
        attempts = attempts_change

    verification = _after_value(state_diff.get("last_verification_result"))
    verification_history = _after_value(state_diff.get("verification_history"))
    failures = _after_value(state_diff.get("failure_history"))
    latest_failure: Dict[str, Any] = {}
    if isinstance(failures, list) and failures:
        last = failures[-1]
        if isinstance(last, dict):
            latest_failure = last
    latest_verification: Dict[str, Any] = {}
    if isinstance(verification, dict) and verification:
        latest_verification = verification
    elif isinstance(verification_history, list) and verification_history:
        last = verification_history[-1]
        if isinstance(last, dict):
            latest_verification = last

    status = str(
        latest_verification.get("verification_status")
        or latest_verification.get("status")
        or ""
    )
    failure_type = str(latest_failure.get("failure_type") or latest_failure.get("summary") or "")
    failure_detail = _shorten_for_summary(
        latest_failure.get("evidence_excerpt")
        or latest_failure.get("summary")
        or latest_failure.get("failure_type")
        or ""
    )
    return {
        "phase": phase,
        "poc_attempts": attempts,
        "submit_action": submit_action,
        "verification_status": status,
        "vul_exit_code": latest_verification.get("vul_exit_code"),
        "fix_exit_code": latest_verification.get("fix_exit_code"),
        "poc_path": latest_verification.get("poc_path") or latest_failure.get("related_poc_id") or "",
        "failure_type": failure_type,
        "failure_detail": failure_detail,
        "has_signal": bool(
            submit_action
            or phase
            or attempts is not None
            or status
            or failure_type
            or failure_detail
        ),
    }


def _parser_flag(step: Dict[str, Any]) -> Dict[str, Any]:
    diagnostics = step.get("parser_diagnostics")
    if not isinstance(diagnostics, dict) or not diagnostics:
        return {}
    severity = str(diagnostics.get("severity") or "").lower()
    return {
        "severity": severity or "info",
        "code": diagnostics.get("code"),
        "summary": _shorten_for_summary(
            diagnostics.get("summary")
            or diagnostics.get("details")
            or diagnostics.get("code")
            or "parser diagnostic"
        ),
        "is_error": severity == "error",
    }


def _context_flag(step: Dict[str, Any]) -> Dict[str, Any]:
    context = step.get("context")
    if not isinstance(context, dict):
        return {}
    ratio = context.get("occupancy_ratio")
    try:
        occupancy = float(ratio)
    except (TypeError, ValueError):
        occupancy = 0.0
    compact_events = context.get("compact_events")
    compact_count = len(compact_events) if isinstance(compact_events, list) else 0
    if occupancy >= 0.85 or compact_count:
        return {
            "occupancy_ratio": occupancy,
            "compact_count": compact_count,
            "is_pressure": occupancy >= 0.85,
        }
    return {}


def _build_step_summaries(
    steps: List[Dict[str, Any]], events_by_step: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for step in steps:
        sid = step.get("step_id")
        events = events_by_step.get(str(sid), [])
        parser = _parser_flag(step)
        context = _context_flag(step)
        action_results = step.get("action_results")
        errors: List[str] = []
        if isinstance(action_results, list):
            for result in action_results:
                err = _result_error(result)
                if err:
                    errors.append(err)
        for event in events:
            if not bool(event.get("ok", True)) or event.get("error"):
                errors.append(
                    _shorten_for_summary(
                        event.get("error")
                        or (event.get("payload") or {}).get("stage")
                        or event.get("phase")
                        or "event error"
                    )
                )
        critic_outputs = step.get("critic_outputs")
        critic_retry_count = 0
        critic_stop_count = 0
        critic_reasons: List[str] = []
        if isinstance(critic_outputs, list):
            for item in critic_outputs:
                if not isinstance(item, dict):
                    continue
                action = str(item.get("action") or "").lower()
                if action == "retry":
                    critic_retry_count += 1
                if action == "stop":
                    critic_stop_count += 1
                if item.get("reason"):
                    critic_reasons.append(_shorten_for_summary(item.get("reason")))
        visual_assets = step.get("visual_assets")
        has_visual = bool(visual_assets) or bool(step.get("has_screenshot"))
        cybergym = _cybergym_signals(step)
        risk_flags: List[str] = []
        if parser.get("is_error"):
            risk_flags.append("parser_error")
        elif parser:
            risk_flags.append("parser_warning")
        if cybergym.get("failure_type") or cybergym.get("failure_detail"):
            risk_flags.append("cybergym_verification_failure")
        elif cybergym.get("submit_action"):
            risk_flags.append("cybergym_poc_submission")
        if errors:
            risk_flags.append("tool_or_event_error")
        if critic_retry_count:
            risk_flags.append("critic_retry")
        if critic_stop_count:
            risk_flags.append("critic_stop")
        if context.get("is_pressure"):
            risk_flags.append("context_pressure")
        elif context.get("compact_count"):
            risk_flags.append("context_compact")
        if has_visual:
            risk_flags.append("visual_evidence")
        observation_summary = _step_observation_summary(step)
        summaries.append(
            {
                "step_id": sid,
                "agent_id": step.get("agent_id"),
                "thought": _extract_summary_thought(step, events),
                "action": _step_action_summary(step),
                "observation": observation_summary,
                "event_count": len(events),
                "parser": parser,
                "context": context,
                "errors": errors,
                "critic_retry_count": critic_retry_count,
                "critic_stop_count": critic_stop_count,
                "critic_reasons": critic_reasons,
                "visual_asset_count": step.get("visual_asset_count", 0),
                "has_visual": has_visual,
                "cybergym": cybergym if cybergym.get("has_signal") else {},
                "risk_flags": risk_flags,
            }
        )
    return summaries


def _build_tool_stats(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_tool: Dict[str, Dict[str, Any]] = {}
    total = 0
    errors = 0
    for step in steps:
        actions = step.get("actions")
        if not isinstance(actions, list):
            actions = []
        step_error = False
        action_results = step.get("action_results")
        if isinstance(action_results, list):
            step_error = any(bool(_result_error(result)) for result in action_results)
        for action in actions:
            if not isinstance(action, dict):
                continue
            name = _action_name(action)
            item = by_tool.setdefault(name, {"count": 0, "errors": 0})
            item["count"] += 1
            total += 1
            if step_error:
                item["errors"] += 1
                errors += 1
    return {"total": total, "errors": errors, "by_tool": by_tool}


def _build_phase_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_phase: Dict[str, Dict[str, Any]] = {}
    for event in events:
        phase = str(event.get("phase") or "unknown")
        item = by_phase.setdefault(phase, {"count": 0, "errors": 0})
        item["count"] += 1
        if not bool(event.get("ok", True)) or event.get("error"):
            item["errors"] += 1
    return {"total": len(events), "by_phase": by_phase}


def _build_insights(
    manifest: Dict[str, Any],
    step_summaries: List[Dict[str, Any]],
    tool_stats: Dict[str, Any],
) -> Dict[str, Any]:
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    status = str(manifest.get("status") or "unknown")
    stop_reason = str(summary.get("stop_reason") or manifest.get("stop_reason") or "")
    final_result = summary.get("final_result")
    critical_steps: List[Dict[str, Any]] = []
    risk_flags: List[str] = []
    priority = {
        "cybergym_verification_failure": 0,
        "parser_error": 0,
        "tool_or_event_error": 1,
        "critic_stop": 2,
        "cybergym_poc_submission": 3,
        "critic_retry": 4,
        "context_pressure": 5,
        "context_compact": 6,
        "visual_evidence": 7,
    }
    for item in step_summaries:
        flags = list(item.get("risk_flags") or [])
        if not flags:
            continue
        risk_flags.extend(flags)
        reason = sorted(flags, key=lambda f: priority.get(str(f), 99))[0]
        detail = ""
        if reason == "parser_error":
            detail = ((item.get("parser") or {}).get("summary")) or ""
        elif reason == "cybergym_verification_failure":
            cybergym = item.get("cybergym") or {}
            detail = (
                cybergym.get("failure_detail")
                or cybergym.get("failure_type")
                or cybergym.get("verification_status")
                or "CyberGym verification did not accept the candidate"
            )
        elif reason == "cybergym_poc_submission":
            cybergym = item.get("cybergym") or {}
            detail = (
                f"submit_poc attempt {cybergym.get('poc_attempts')}"
                if cybergym.get("poc_attempts") is not None
                else "submit_poc candidate submitted"
            )
        elif reason == "tool_or_event_error":
            detail = (item.get("errors") or [""])[0]
        elif reason in ("critic_stop", "critic_retry"):
            detail = (item.get("critic_reasons") or [""])[0]
        elif reason.startswith("context"):
            context = item.get("context") or {}
            ratio = context.get("occupancy_ratio")
            detail = f"context occupancy {float(ratio or 0) * 100:.1f}%"
        elif reason == "visual_evidence":
            detail = "visual evidence recorded"
        critical_steps.append(
            {
                "step_id": item.get("step_id"),
                "reason": reason,
                "detail": detail,
                "agent_id": item.get("agent_id"),
            }
        )
    critical_steps.sort(
        key=lambda item: (
            priority.get(str(item.get("reason")), 99),
            int(item.get("step_id") or 0),
        )
    )
    unique_flags = sorted(set(risk_flags), key=lambda f: priority.get(str(f), 99))
    likely_failure = ""
    if critical_steps:
        first = critical_steps[0]
        likely_failure = f"Step {first.get('step_id')} · {first.get('reason')}"
        if first.get("detail"):
            likely_failure += f": {first.get('detail')}"
    elif stop_reason:
        likely_failure = stop_reason
    else:
        likely_failure = "No explicit failure signal recorded."
    outcome = _derive_outcome(status=status, stop_reason=stop_reason, summary=summary)
    return {
        "outcome": outcome,
        "status": status,
        "stop_reason": stop_reason or None,
        "final_result": final_result,
        "likely_failure": likely_failure,
        "critical_steps": critical_steps[:8],
        "next_inspect_step": (
            critical_steps[0].get("step_id") if critical_steps else None
        ),
        "risk_flags": unique_flags,
        "tool_error_count": tool_stats.get("errors", 0),
    }


def _phase_from_events(events: List[Dict[str, Any]]) -> str:
    for event in reversed(events):
        phase = str(event.get("phase") or "").strip()
        if phase:
            return phase
    return ""


def _first_action_name(step: Dict[str, Any]) -> str:
    actions = step.get("actions")
    if isinstance(actions, list) and actions:
        first = actions[0]
        if isinstance(first, dict):
            return _action_name(first)
    return ""


def _step_role(step: Dict[str, Any], summary: Dict[str, Any]) -> str:
    flags = set(str(flag) for flag in (summary.get("risk_flags") or []))
    action_name = _first_action_name(step)
    cybergym = summary.get("cybergym") if isinstance(summary.get("cybergym"), dict) else {}
    state_diff = step.get("state_diff") if isinstance(step.get("state_diff"), dict) else {}
    if "cybergym_verification_failure" in flags:
        return "verification_failure"
    if cybergym.get("submit_action") or action_name == "submit_poc":
        return "poc_submission"
    if "parser_error" in flags:
        return "parser_error"
    if "tool_or_event_error" in flags:
        return "error"
    if "critic_stop" in flags or "critic_retry" in flags:
        return "critic"
    if state_diff.get("current_phase"):
        return "phase_change"
    if action_name:
        return "action"
    return "observation"


def _attention_level(role: str, flags: List[str]) -> str:
    if role in {"verification_failure", "parser_error"}:
        return "critical"
    if role in {"poc_submission", "error"}:
        return "important"
    if any(str(flag).startswith("context") for flag in flags):
        return "watch"
    return "normal"


def _outcome_label(summary: Dict[str, Any]) -> str:
    cybergym = summary.get("cybergym") if isinstance(summary.get("cybergym"), dict) else {}
    if cybergym.get("failure_type"):
        return str(cybergym.get("failure_type"))
    if cybergym.get("verification_status"):
        return str(cybergym.get("verification_status"))
    errors = summary.get("errors")
    if isinstance(errors, list) and errors:
        return str(errors[0])
    if summary.get("observation"):
        return str(summary.get("observation"))
    if cybergym.get("phase"):
        return f"phase {cybergym.get('phase')}"
    parser = summary.get("parser") if isinstance(summary.get("parser"), dict) else {}
    if parser.get("summary"):
        return str(parser.get("summary"))
    return "no direct result"


def _build_step_focus(
    steps: List[Dict[str, Any]],
    step_summaries: List[Dict[str, Any]],
    events_by_step: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    summary_by_step = {str(item.get("step_id")): item for item in step_summaries}
    focus_rows: List[Dict[str, Any]] = []
    for step in steps:
        sid = str(step.get("step_id"))
        summary = summary_by_step.get(sid, {})
        flags = [str(flag) for flag in (summary.get("risk_flags") or [])]
        role = _step_role(step, summary)
        cybergym = summary.get("cybergym") if isinstance(summary.get("cybergym"), dict) else {}
        phase = str(cybergym.get("phase") or _phase_from_events(events_by_step.get(sid, [])) or "")
        evidence_refs: List[str] = []
        if summary.get("thought"):
            evidence_refs.append("thought")
        if summary.get("action"):
            evidence_refs.append("action")
        if summary.get("errors") or summary.get("observation"):
            evidence_refs.append("observation")
        if (summary.get("parser") or {}).get("summary"):
            evidence_refs.append("parser")
        if summary.get("critic_reasons"):
            evidence_refs.append("critic")
        if cybergym:
            evidence_refs.append("cybergym")
        evidence_refs.append("raw")
        focus_rows.append(
            {
                "step_id": step.get("step_id"),
                "step_role": role,
                "phase": phase,
                "action_label": summary.get("action") or _step_action_summary(step),
                "thought": summary.get("thought") or "",
                "outcome_label": _outcome_label(summary),
                "evidence_refs": sorted(set(evidence_refs), key=evidence_refs.index),
                "attention_level": _attention_level(role, flags),
                "risk_flags": flags,
                "cybergym": cybergym,
            }
        )
    return focus_rows


def _failure_category(text: str, stop_reason: str = "") -> str:
    probe = f"{text} {stop_reason}".lower()
    if "connection refused" in probe or "connection reset" in probe or "could not connect" in probe:
        return "server_connectivity"
    if "no_trigger" in probe or "did not trigger" in probe or "vul_exit_code': 0" in probe:
        return "no_trigger"
    if "submission_error" in probe or "submit" in probe and "error" in probe:
        return "submission_error"
    if "budget" in probe or "max_step" in probe or "timeout" in probe:
        return "budget_exhausted"
    if "parser" in probe:
        return "parser_error"
    if "error" in probe or "fail" in probe:
        return "tool_error"
    return "needs_review"


def _build_cybergym_focus(
    manifest: Dict[str, Any],
    step_focus: List[Dict[str, Any]],
) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    latest_status = ""
    last_poc_path = ""
    failure_text = ""
    max_attempt = 0
    for row in step_focus:
        cybergym = row.get("cybergym") if isinstance(row.get("cybergym"), dict) else {}
        if not cybergym:
            continue
        attempt_value = cybergym.get("poc_attempts")
        try:
            attempt_num = int(attempt_value or 0)
        except (TypeError, ValueError):
            attempt_num = 0
        max_attempt = max(max_attempt, attempt_num)
        if cybergym.get("verification_status"):
            latest_status = str(cybergym.get("verification_status"))
        if cybergym.get("poc_path"):
            last_poc_path = str(cybergym.get("poc_path"))
        if cybergym.get("failure_detail"):
            failure_text = str(cybergym.get("failure_detail"))
        if cybergym.get("submit_action") or cybergym.get("failure_type") or cybergym.get("verification_status"):
            category_text = " ".join(
                str(part or "")
                for part in (
                    cybergym.get("verification_status"),
                    cybergym.get("failure_type"),
                    cybergym.get("failure_detail"),
                )
            )
            attempts.append(
                {
                    "step_id": row.get("step_id"),
                    "attempt": attempt_num or None,
                    "status": cybergym.get("verification_status") or cybergym.get("failure_type") or row.get("outcome_label"),
                    "poc_path": cybergym.get("poc_path") or "",
                    "failure": cybergym.get("failure_detail") or "",
                    "category": _failure_category(category_text, ""),
                }
            )
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    stop_reason = str(summary.get("stop_reason") or "")
    return {
        "poc_attempts": max_attempt or len(attempts),
        "last_verification_status": latest_status or "not recorded",
        "last_poc_path": last_poc_path,
        "server_connectivity_failure": _failure_category(failure_text, stop_reason) == "server_connectivity",
        "failure_category": _failure_category(
            " ".join([latest_status, failure_text]).strip(),
            stop_reason,
        ),
        "attempt_ladder": attempts[-12:],
    }


def _build_run_focus(
    *,
    manifest: Dict[str, Any],
    insights: Dict[str, Any],
    step_focus: List[Dict[str, Any]],
    cybergym_focus: Dict[str, Any],
) -> Dict[str, Any]:
    critical = [
        row
        for row in step_focus
        if row.get("attention_level") in ("critical", "important")
        or row.get("step_role") in ("phase_change", "poc_submission")
    ]
    primary_failure = insights.get("likely_failure") or "No explicit failure signal recorded."
    if cybergym_focus.get("failure_category") and cybergym_focus.get("failure_category") != "needs_review":
        primary_failure = f"{cybergym_focus.get('failure_category')}: {primary_failure}"
    next_step = insights.get("next_inspect_step")
    if next_step is None and critical:
        next_step = critical[0].get("step_id")
    metadata_sections = [
        "run metadata",
        "cost/context",
        "parser telemetry",
        "prompt metadata",
        "trace events",
        "raw JSON",
    ]
    return {
        "outcome": insights.get("outcome") or _derive_outcome(
            status=str(manifest.get("status") or ""),
            stop_reason=str(((manifest.get("summary") or {}).get("stop_reason") or "")),
            summary=manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {},
        ),
        "primary_failure": primary_failure,
        "next_actionable_step": next_step,
        "critical_evidence": critical[:8],
        "hidden_metadata_count": len(metadata_sections),
    }


def _derive_outcome(*, status: str, stop_reason: str, summary: Dict[str, Any]) -> str:
    status_l = str(status or "").lower()
    stop_l = str(stop_reason or "").lower()
    if status_l == "running":
        return "running"
    task_result = summary.get("task_result")
    if isinstance(task_result, dict) and task_result.get("success") is True:
        return "success"
    failure_words = (
        "budget",
        "max_step",
        "max_steps",
        "max_runtime",
        "timeout",
        "error",
        "exception",
        "fail",
        "cancel",
        "abort",
    )
    if any(word in stop_l for word in failure_words):
        return "needs_review"
    success_reasons = {"success", "succeeded", "solved", "verified", "final", "completed"}
    if stop_l in success_reasons or status_l in {"success", "succeeded"}:
        return "success"
    if summary.get("final_result") not in (None, "", False):
        return "success"
    return "needs_review"


def _summary_metric(manifest: Dict[str, Any], key: str, default: Any = None) -> Any:
    if key in manifest:
        return manifest.get(key, default)
    summary = manifest.get("summary") or {}
    if isinstance(summary, dict) and key in summary:
        return summary.get(key, default)
    task_result = summary.get("task_result") if isinstance(summary, dict) else None
    metrics = task_result.get("metrics") if isinstance(task_result, dict) else None
    if isinstance(metrics, dict):
        if key in metrics:
            return metrics.get(key, default)
        if key == "latency_seconds":
            return metrics.get("elapsed_seconds", default)
    return default


def _first_failure_step(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    steps = payload.get("steps") or []
    events_by_step = payload.get("events_by_step") or {}
    for step in steps:
        diagnostics = step.get("parser_diagnostics") or {}
        if isinstance(diagnostics, dict) and str(diagnostics.get("severity")) == "error":
            return {
                "step_id": step.get("step_id"),
                "reason": "parser_error",
                "summary": diagnostics.get("summary"),
                "code": diagnostics.get("code"),
            }
        action_results = step.get("action_results") or []
        for result in action_results:
            if isinstance(result, dict) and str(result.get("status")) == "error":
                return {
                    "step_id": step.get("step_id"),
                    "reason": "action_error",
                    "summary": result.get("error") or result.get("message") or str(result),
                }
        for event in events_by_step.get(str(step.get("step_id")), []):
            if not bool(event.get("ok", True)) or event.get("error"):
                return {
                    "step_id": step.get("step_id"),
                    "reason": "event_error",
                    "summary": event.get("error") or (event.get("payload") or {}).get("stage") or event.get("phase"),
                }
    return None


def _flatten_dict(value: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            out.update(_flatten_dict(item, prefix=name))
        else:
            out[name] = item
    return out


def _config_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    manifest = payload.get("manifest") or {}
    run_spec = manifest.get("run_spec") if isinstance(manifest.get("run_spec"), dict) else {}
    experiment_spec = (
        manifest.get("experiment_spec")
        if isinstance(manifest.get("experiment_spec"), dict)
        else {}
    )
    run_meta = ((manifest.get("summary") or {}).get("run_meta") or {})
    snapshot = {
        "model_id": manifest.get("model_id"),
        "model_family": manifest.get("model_family"),
        "family_preset": (((run_meta.get("harness") or {}).get("family_preset")) or ((run_spec.get("metadata") or {}).get("family_preset"))),
        "prompt_protocol": manifest.get("prompt_protocol"),
        "parser_name": manifest.get("parser_name"),
        "benchmark_name": manifest.get("benchmark_name"),
        "benchmark_split": manifest.get("benchmark_split"),
        "official_run": manifest.get("official_run"),
        "replay_mode": manifest.get("replay_mode"),
        "run_spec": run_spec,
        "experiment_spec": experiment_spec,
        "run_meta": run_meta,
    }
    return _flatten_dict(snapshot)


def _step_action_label(step: Dict[str, Any]) -> str:
    actions = list(step.get("actions") or [])
    if not actions:
        return ""
    action = actions[0] or {}
    tool = str(action.get("tool") or action.get("name") or action.get("action") or "")
    args = dict(action.get("args") or {}) if isinstance(action, dict) else {}
    for key in ("text", "path", "command", "reason"):
        if key in args:
            return f"{tool}({str(args.get(key))[:60]})"
    return tool


def _build_visual_timeline(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    timeline: List[Dict[str, Any]] = []
    for step in steps:
        assets = list(step.get("visual_assets") or [])
        screenshot = None
        for asset in assets:
            if isinstance(asset, dict) and str(asset.get("kind") or "") == "screenshot":
                screenshot = dict(asset)
                break
        multimodal = {}
        observation = step.get("observation")
        if isinstance(observation, dict):
            env = observation.get("env")
            if isinstance(env, dict):
                env_observation = env.get("observation")
                if isinstance(env_observation, dict):
                    data = env_observation.get("data")
                    if isinstance(data, dict) and isinstance(data.get("multimodal"), dict):
                        multimodal = dict(data.get("multimodal") or {})
        grounding = multimodal.get("grounding_metadata")
        critic_outputs = list(step.get("critic_outputs") or [])
        retry_count = sum(
            1
            for item in critic_outputs
            if isinstance(item, dict) and str(item.get("action") or "") == "retry"
        )
        timeline.append(
            {
                "step_id": step.get("step_id"),
                "screenshot": screenshot,
                "action_label": _step_action_label(step),
                "grounding_present": bool(grounding),
                "grounding_metadata": grounding if isinstance(grounding, dict) else {},
                "critic_retry_count": retry_count,
                "visual_asset_count": step.get("visual_asset_count", 0),
            }
        )
    return timeline


def _build_run_diff(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    left_manifest = left.get("manifest") or {}
    right_manifest = right.get("manifest") or {}
    left_summary = left_manifest.get("summary") or {}
    right_summary = right_manifest.get("summary") or {}
    left_config = _config_snapshot(left)
    right_config = _config_snapshot(right)
    all_config_keys = sorted(set(left_config) | set(right_config))
    config_diff = []
    for key in all_config_keys:
        left_value = left_config.get(key)
        right_value = right_config.get(key)
        if left_value == right_value:
            continue
        config_diff.append({"field": key, "left": left_value, "right": right_value})
    return {
        "left": {
            "run_id": left.get("run_id"),
            "status": left_manifest.get("status"),
            "stop_reason": left_summary.get("stop_reason"),
            "final_result": left_summary.get("final_result"),
            "step_count": left_manifest.get("step_count", 0),
            "event_count": left_manifest.get("event_count", 0),
            "token_usage": _summary_metric(left_manifest, "token_usage", 0),
            "latency_seconds": _summary_metric(left_manifest, "latency_seconds", 0.0),
            "cost": _summary_metric(left_manifest, "cost", 0.0),
            "official_run": bool(left_manifest.get("official_run", False)),
            "replay_mode": left_manifest.get("replay_mode"),
            "parser": left_summary.get("parser", {}),
            "first_failure_step": _first_failure_step(left),
        },
        "right": {
            "run_id": right.get("run_id"),
            "status": right_manifest.get("status"),
            "stop_reason": right_summary.get("stop_reason"),
            "final_result": right_summary.get("final_result"),
            "step_count": right_manifest.get("step_count", 0),
            "event_count": right_manifest.get("event_count", 0),
            "token_usage": _summary_metric(right_manifest, "token_usage", 0),
            "latency_seconds": _summary_metric(right_manifest, "latency_seconds", 0.0),
            "cost": _summary_metric(right_manifest, "cost", 0.0),
            "official_run": bool(right_manifest.get("official_run", False)),
            "replay_mode": right_manifest.get("replay_mode"),
            "parser": right_summary.get("parser", {}),
            "first_failure_step": _first_failure_step(right),
        },
        "config_diff": config_diff,
    }


def _render_board_html() -> str:
    return """<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>qita board</title>
""" + _DESIGN_HEAD + """
<style>
""" + _DESIGN_TOKENS + """
*{box-sizing:border-box} body{margin:0;font-family:var(--font-body);background:var(--bg);color:var(--txt)}
.wrap{max-width:1320px;margin:0 auto;padding:24px 18px 32px}
.head{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:16px}
.title{font-size:28px;font-weight:700;letter-spacing:-.6px}.sub{color:var(--muted);font-size:13px;margin-top:4px}
.chip{border:1px solid var(--line);background:var(--surface-2);border-radius:var(--radius-pill);padding:8px 12px;font-size:12px;color:var(--muted)}
.toolbar{display:grid;grid-template-columns:1.2fr .9fr .9fr 1fr 1fr auto auto;gap:10px;margin:12px 0 18px}
.toolbar input,.toolbar select{border:1px solid var(--line);background:var(--surface-1);color:var(--txt);border-radius:var(--radius-md);padding:9px 10px;font-size:13px}
.toolbar label{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}
.toolbar .btn{justify-content:center}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.card{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:14px}
.id{font-weight:700;font-size:16px}
.meta{font-size:12px;color:var(--muted);margin-top:6px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.btn{display:inline-flex;align-items:center;border:1px solid var(--line);color:var(--txt);background:var(--surface-1);padding:6px 10px;border-radius:var(--radius-md);font-size:12px;text-decoration:none;cursor:pointer}
.btn:hover{border-color:var(--accent)}
.state{display:inline-block;padding:2px 8px;border-radius:var(--radius-pill);font-size:11px;background:var(--surface-2);color:var(--ok);border:1px solid var(--line)}
.manifest-mini{margin-top:8px;border:1px dashed var(--line-strong);border-radius:var(--radius-md);padding:8px;background:var(--surface-1)}
.manifest-mini .meta{margin-top:2px}
.manifest-meta-tree{margin-top:6px;padding-top:6px;border-top:1px dashed var(--line-strong)}
.manifest-meta-tree details{margin:4px 0}
.manifest-meta-tree summary{cursor:pointer;color:var(--muted);font-size:12px}
.manifest-meta-leaf{display:grid;grid-template-columns:110px 1fr;gap:8px;margin:4px 0}
.manifest-meta-k{font-size:11px;color:var(--subtle)}
.manifest-meta-v{font-size:11px;color:var(--txt);word-break:break-word}
.empty{padding:18px;border:1px dashed var(--line);border-radius:var(--radius-lg);color:var(--muted)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.live-dot{display:inline-block;width:8px;height:8px;border-radius:9999px;background:var(--ok);animation:pulse 1.5s ease infinite;margin-right:6px;vertical-align:middle}
@media (max-width:980px){.toolbar{grid-template-columns:1fr 1fr}}
</style></head>
<body>
<div class="wrap">
  <div class="head">
    <div>
      <div class="title">QitOS · qita board</div>
      <div class="sub">Runs, trace inspection, replay, and export</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <div class="chip" id="summary">Loading...</div>
      """ + _THEME_TOGGLE_HTML + """
    </div>
  </div>
  <div class="toolbar">
    <input id="q" placeholder="Search run id / stop reason / final result"/>
    <select id="status"><option value="">All status</option></select>
    <select id="sort">
      <option value="updated_desc">Sort: updated desc</option>
      <option value="updated_asc">Sort: updated asc</option>
      <option value="events_desc">Sort: events desc</option>
      <option value="steps_desc">Sort: steps desc</option>
    </select>
    <input id="cmpLeft" placeholder="Compare A: run id"/>
    <input id="cmpRight" placeholder="Compare B: run id"/>
    <label><input type="checkbox" id="auto" checked/>Auto refresh</label>
    <button class="btn" id="compareBtn">Compare</button>
    <button class="btn" id="refresh">Refresh</button>
  </div>
  <div id="stats" class="grid" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr));margin-bottom:12px"></div>
  <section id="trendSection" style="margin-bottom:14px;display:none">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
      <span style="font-size:13px;font-weight:600;color:var(--txt)">trend</span>
      <select id="trendMetric" style="border:1px solid var(--line);background:var(--surface-1);color:var(--txt);border-radius:var(--radius-md);padding:4px 8px;font-size:12px">
        <option value="tokens">tokens</option>
        <option value="steps">steps</option>
        <option value="runtime">runtime (s)</option>
        <option value="cost">cost ($)</option>
      </select>
    </div>
    <div id="trendChart" style="background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:10px;overflow-x:auto"></div>
  </section>
  <div id="runs" class="grid"></div>
</div>
<script>
let allRuns = [];
function pickCompare(side, runId){
  if(side === 'left'){ document.getElementById('cmpLeft').value = runId; }
  else { document.getElementById('cmpRight').value = runId; }
}
function openCompare(){
  const left = (document.getElementById('cmpLeft').value || '').trim();
  const right = (document.getElementById('cmpRight').value || '').trim();
  if(!left || !right){ return; }
  window.location.href = '/compare?left=' + encodeURIComponent(left) + '&right=' + encodeURIComponent(right);
}
function parseTime(s){
  if(!s){ return 0; }
  const v = Date.parse(s);
  return Number.isNaN(v) ? 0 : v;
}
function esc(s){
  return String(s).replace(/[&<>]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]; });
}
function preview(v){
  if(v === null || v === undefined) return '-';
  if(typeof v === 'string') return v.length > 120 ? (v.slice(0, 120) + '...') : v;
  if(typeof v === 'number' || typeof v === 'boolean') return String(v);
  if(Array.isArray(v)) return '[' + v.length + ' items]';
  if(typeof v === 'object') return '{...}';
  return String(v);
}
function metaLeaf(k, v){
  return '<div class="manifest-meta-leaf"><div class="manifest-meta-k">'+esc(k)+'</div><div class="manifest-meta-v">'+esc(preview(v))+'</div></div>';
}
function metaTree(k, v, depth){
  if(v === null || v === undefined) return metaLeaf(k, v);
  if(Array.isArray(v)){
    const lim = Math.min(v.length, 50);
    let inner = '';
    for(let i=0;i<lim;i+=1) inner += metaTree('['+i+']', v[i], depth + 1);
    if(v.length > lim) inner += metaLeaf('...', '+' + (v.length - lim) + ' more items');
    const open = depth < 1 ? ' open' : '';
    return '<details'+open+'><summary>'+esc(k)+' <span class="meta">array('+v.length+')</span></summary>' + inner + '</details>';
  }
  if(typeof v === 'object'){
    const keys = Object.keys(v);
    const lim = Math.min(keys.length, 50);
    let inner = '';
    for(let i=0;i<lim;i+=1){ const kk = keys[i]; inner += metaTree(kk, v[kk], depth + 1); }
    if(keys.length > lim) inner += metaLeaf('...', '+' + (keys.length - lim) + ' more keys');
    const open = depth < 1 ? ' open' : '';
    return '<details'+open+'><summary>'+esc(k)+' <span class="meta">object('+keys.length+')</span></summary>' + inner + '</details>';
  }
  return metaLeaf(k, v);
}
function riskChip(flag){
  const f = String(flag || '');
  const cls = (f.includes('error') || f.includes('stop') || f.includes('failure')) ? 'color:var(--err);border-color:var(--err-border);background:var(--err-soft)' : (f.includes('retry') || f.includes('context') || f.includes('submission')) ? 'color:var(--warn);border-color:var(--warn-border);background:var(--warn-soft)' : 'color:var(--muted)';
  return '<span class="state" style="margin-right:4px;'+cls+'">'+esc(f.replaceAll('_',' '))+'</span>';
}
function paint(){
  const q = (document.getElementById('q').value || '').toLowerCase();
  const status = document.getElementById('status').value;
  const sort = document.getElementById('sort').value;
  let runs = allRuns.filter((r)=>{
    const blob = `${r.id||''} ${r.stop_reason||''} ${r.final_result||''}`.toLowerCase();
    if(q && !blob.includes(q)){ return false; }
    if(status && (r.status||'') !== status){ return false; }
    return true;
  });
  runs = runs.slice();
  if(sort === 'updated_desc'){ runs.sort((a,b)=>parseTime(b.updated_at)-parseTime(a.updated_at)); }
  else if(sort === 'updated_asc'){ runs.sort((a,b)=>parseTime(a.updated_at)-parseTime(b.updated_at)); }
  else if(sort === 'events_desc'){ runs.sort((a,b)=>(b.event_count||0)-(a.event_count||0)); }
  else if(sort === 'steps_desc'){ runs.sort((a,b)=>(b.step_count||0)-(a.step_count||0)); }
  const root = document.getElementById('runs');
  const statsRoot = document.getElementById('stats');
  document.getElementById('summary').textContent = `${runs.length} shown / ${allRuns.length} total`;
  const stats = calcStats(allRuns);
  statsRoot.innerHTML = `
    <div class="card"><div class="meta">Success Rate</div><div class="id">${stats.successRate}</div><div class="meta">${stats.successCount}/${stats.total} runs</div></div>
    <div class="card"><div class="meta">Avg Steps</div><div class="id">${stats.avgSteps}</div><div class="meta">per run</div></div>
    <div class="card"><div class="meta">Avg Events</div><div class="id">${stats.avgEvents}</div><div class="meta">per run</div></div>
    <div class="card"><div class="meta">Failure Top-3</div><div class="meta">${stats.failureTop}</div></div>
    <div class="card"><div class="meta">Avg Tokens</div><div class="id">${stats.avgTokens}</div><div class="meta">per run</div></div>
    <div class="card"><div class="meta">Peak Ctx</div><div class="id">${stats.maxPeakCtx}</div><div class="meta">highest occupancy</div></div>
  `;
  if(!runs.length){ root.innerHTML = `<div class="empty">No runs found in current logdir.</div>`; return; }
  root.innerHTML = '';
  for(const r of runs){
    const el = document.createElement('div');
    el.className = 'card';
    const status = (r.status || 'unknown');
    const isRunning = status === 'running';
    const liveIndicator = isRunning ? '<span class="live-dot"></span>' : '';
    const m = r.manifest_meta || {};
    const agentCount = r.agent_count || 0;
    const agentBadge = agentCount > 1 ? `<span class="state" style="background:var(--surface-2);color:var(--accent);border-color:var(--line-strong)">[${agentCount} agents]</span>` : (r.agent_name ? `<span class="state" style="background:var(--surface-2);color:var(--accent);border-color:var(--line-strong)">[${esc(r.agent_name)}]</span>` : '');
    const handoffBadge = r.handoff_count ? `<span class="state" style="background:var(--surface-2);color:var(--kind-handoff);border-color:var(--line-strong)">handoffs=${r.handoff_count}</span>` : '';
    const topoInfo = (r.agent_topology && typeof r.agent_topology === 'object') ? (r.agent_topology.type || '') : '';
    const topoBadge = topoInfo ? `<div class="meta">topology=${esc(topoInfo)}${r.agent_topology.agents ? ' agents=' + esc(r.agent_topology.agents.join(',')) : ''}</div>` : '';
    const insights = r.insights || {};
    const flags = Array.isArray(r.risk_flags) ? r.risk_flags : (Array.isArray(insights.risk_flags) ? insights.risk_flags : []);
    const failureCause = insights.likely_failure || r.stop_reason || '';
    const nextStep = insights.next_inspect_step !== undefined && insights.next_inspect_step !== null ? insights.next_inspect_step : '';
    const riskHtml = flags.length ? flags.map(riskChip).join('') : '<span class="state">no risk flags</span>';
    el.innerHTML = `
      <div class="id">${r.id} ${agentBadge} ${handoffBadge}</div>
      <div class="meta">${liveIndicator}<span class="state">${status}</span> steps=${r.step_count||0} events=${r.event_count||0}</div>
      <div class="meta">stop=${r.stop_reason||''}</div>
      <div class="manifest-mini">
        <div class="meta">Failure Cause</div>
        <div class="meta" style="color:var(--txt);line-height:1.45">${esc(failureCause || 'No explicit failure signal recorded.')}</div>
        <div class="meta">Next Inspect Step=${esc(String(nextStep || '-'))}</div>
        <div style="margin-top:6px">${riskHtml}</div>
      </div>
      <div class="meta">updated=${r.updated_at||''}</div>
      ${topoBadge}
      <div class="manifest-mini">
        <div class="meta">manifest meta</div>
        <div class="meta">model=${m.model_id||''}</div>
        <div class="meta">schema=${m.schema_version||''} seed=${m.seed===null?'null':(m.seed||'')}</div>
        <div class="meta">official=${m.official_run ? 'yes' : 'no'} replay=${m.replay_mode||'-'}</div>
        <div class="meta">prompt_hash=${m.prompt_hash||''}</div>
        <div class="meta">protocol=${m.protocol||''} builder=${m.prompt_builder||''}</div>
        <div class="meta">resolution=${m.protocol_resolution_source||''}</div>
        <div class="meta">git=${m.git_sha||''} pkg=${m.package_version||''}</div>
        <div class="meta">tokens=${(m.token_usage||0)} peak_ctx=${ctxPeak(m.context)}</div>
        <div class="meta">parser_err=${((m.parser||{}).error_count||0)} salvage=${((m.parser||{}).salvage_count||0)}</div>
        <details class="manifest-meta-tree">
          <summary>Full manifest meta</summary>
          ${metaTree('manifest_meta', m, 0)}
        </details>
      </div>
      <div class="row">
        <a class="btn" href="/run/${encodeURIComponent(r.id)}">view</a>
        <a class="btn" href="/replay/${encodeURIComponent(r.id)}">replay</a>
        <button class="btn" type="button" onclick="pickCompare('left', '${r.id}')">pick A</button>
        <button class="btn" type="button" onclick="pickCompare('right', '${r.id}')">pick B</button>
        <a class="btn" href="/export/raw/${encodeURIComponent(r.id)}">export raw</a>
        <a class="btn" href="/export/html/${encodeURIComponent(r.id)}">export html</a>
      </div>`;
    root.appendChild(el);
  }
}
function calcStats(runs){
  const total = runs.length;
  let successCount = 0;
  let stepSum = 0;
  let eventSum = 0;
  let tokenSum = 0;
  let maxPeakCtx = 0;
  const fail = new Map();
  for(const r of runs){
    const status = String(r.status||'').toLowerCase();
    const stop = String(r.stop_reason||'').toLowerCase();
    const ok = (status === 'completed' || status === 'success') && !stop.includes('error') && !stop.includes('fail');
    if(ok) successCount += 1;
    stepSum += Number(r.step_count||0);
    eventSum += Number(r.event_count||0);
    tokenSum += Number((r.manifest_meta||{}).token_usage || 0);
    maxPeakCtx = Math.max(maxPeakCtx, Number(ctxPeakNum((r.manifest_meta||{}).context)));
    if(!ok){
      const k = stop || status || 'unknown_failure';
      fail.set(k, (fail.get(k)||0) + 1);
    }
  }
  const top = Array.from(fail.entries()).sort((a,b)=>b[1]-a[1]).slice(0,3).map(([k,v])=>`${k}:${v}`).join(' | ') || 'none';
  return {
    total,
    successCount,
    successRate: total ? `${((successCount/total)*100).toFixed(1)}%` : '0%',
    avgSteps: total ? (stepSum/total).toFixed(2) : '0.00',
    avgEvents: total ? (eventSum/total).toFixed(2) : '0.00',
    avgTokens: total ? Math.round(tokenSum/total).toString() : '0',
    maxPeakCtx: total ? ((maxPeakCtx*100).toFixed(1) + '%') : '0%',
    failureTop: top,
  };
}
function ctxPeakNum(ctx){
  if(!ctx || typeof ctx !== 'object') return 0;
  const v = Number(ctx.peak_occupancy_ratio || 0);
  return Number.isFinite(v) ? v : 0;
}
function ctxPeak(ctx){
  const v = ctxPeakNum(ctx);
  return v ? ((v*100).toFixed(1) + '%') : '-';
}
function buildTrendChart(){
  const el = document.getElementById('trendChart');
  const section = document.getElementById('trendSection');
  if(!el || !section) return;
  if(allRuns.length < 2){ section.style.display = 'none'; return; }
  section.style.display = '';
  const metric = document.getElementById('trendMetric').value;
  const sorted = allRuns.slice().sort((a,b)=>parseTime(a.updated_at)-parseTime(b.updated_at));
  const pts = sorted.map(function(r){
    const m = r.manifest_meta || {};
    let val = 0;
    if(metric === 'tokens') val = Number(m.token_usage || 0);
    else if(metric === 'steps') val = Number(r.step_count || 0);
    else if(metric === 'runtime') val = Number(m.latency_seconds || 0);
    else if(metric === 'cost') val = Number(m.cost || 0);
    return {id: r.id || '', val: val};
  });
  const maxVal = Math.max(...pts.map(p=>p.val), 1);
  const w = 900, h = 140, padL = 50, padR = 16, padT = 12, padB = 28;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  function xAt(i){ return pts.length === 1 ? padL + plotW/2 : padL + (plotW * i / (pts.length - 1)); }
  function yAt(v){ return padT + plotH - (v / maxVal) * plotH; }
  const colors = {tokens:'var(--accent)', steps:'var(--kind-memory)', runtime:'var(--warn)', cost:'var(--err)'};
  const color = colors[metric] || 'var(--accent)';
  let polyPts = [], dots = [], labels = [];
  for(let i = 0; i < pts.length; i++){
    const x = xAt(i), y = yAt(pts[i].val);
    polyPts.push(x+','+y);
    dots.push('<circle cx="'+x+'" cy="'+y+'" r="3" fill="'+color+'"><title>'+esc(pts[i].id)+': '+pts[i].val+'</title></circle>');
    if(pts.length <= 20 || i % Math.ceil(pts.length / 20) === 0){
      labels.push('<text class="gantt-step-label" x="'+x+'" y="'+(h-4)+'" text-anchor="middle" fill="var(--muted)" font-size="9">'+esc(pts[i].id.slice(0,8))+'</text>');
    }
  }
  const gridLines = [];
  for(let g = 0; g <= 4; g++){
    const yVal = (maxVal * g / 4);
    const y = yAt(yVal);
    gridLines.push('<line x1="'+padL+'" y1="'+y+'" x2="'+(w-padR)+'" y2="'+y+'" stroke="var(--line)" stroke-width="0.5"/>');
    gridLines.push('<text x="'+(padL-4)+'" y="'+(y+3)+'" text-anchor="end" fill="var(--muted)" font-size="9">'+Math.round(yVal)+'</text>');
  }
  el.innerHTML = '<svg viewBox="0 0 '+w+' '+h+'" role="img" aria-label="Trend chart" style="width:100%;max-width:'+w+'px">' +
    gridLines.join('') +
    '<polyline points="'+polyPts.join(' ')+'" fill="none" stroke="'+color+'" stroke-width="1.5" stroke-linejoin="round"/>' +
    dots.join('') + labels.join('') +
    '</svg>';
}
async function loadRuns(){
  const rsp = await fetch('/api/runs');
  const data = await rsp.json();
  allRuns = Array.isArray(data) ? data : [];
  const statusEl = document.getElementById('status');
  const keep = statusEl.value;
  const statusSet = new Set(allRuns.map((r)=>r.status||'unknown'));
  statusEl.innerHTML = '<option value="">All status</option>' + Array.from(statusSet).sort().map((s)=>`<option value="${s}">${s}</option>`).join('');
  if(keep){ statusEl.value = keep; }
  paint();
  buildTrendChart();
}
document.getElementById('q').addEventListener('input', paint);
document.getElementById('status').addEventListener('change', paint);
document.getElementById('sort').addEventListener('change', paint);
document.getElementById('compareBtn').addEventListener('click', openCompare);
document.getElementById('trendMetric').addEventListener('change', buildTrendChart);
document.getElementById('refresh').addEventListener('click', ()=>loadRuns().catch((e)=>{document.getElementById('runs').innerHTML=`<div class="empty">Load failed: ${e}</div>`;}));
setInterval(()=>{ if(document.getElementById('auto').checked){ loadRuns().catch(()=>{}); } }, 2500);
loadRuns().catch((e)=>{document.getElementById('runs').innerHTML=`<div class="empty">Load failed: ${e}</div>`;});
</script>
</body></html>"""


def _render_not_found(run_id: str) -> str:
    safe = html.escape(run_id)
    return f"""<!doctype html><html><head><meta charset="utf-8"/><title>run not found</title>
<style>{_DESIGN_TOKENS}</style></head>
<body style="font-family:var(--font-body);background:var(--bg);color:var(--txt);padding:24px">
<h2>Run not found: {safe}</h2><a href="/" style="color:var(--accent)">Back to board</a></body></html>"""


def _render_compare_prompt() -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"/><title>qita compare</title>
<style>{_DESIGN_TOKENS}</style></head>
<body style="font-family:var(--font-body);background:var(--bg);color:var(--txt);padding:24px">
<h2>Missing compare target</h2><p>Provide <code>?left=RUN_A&amp;right=RUN_B</code> to compare two runs.</p>
<a href="/" style="color:var(--accent)">Back to board</a></body></html>"""


def _render_diff_html(diff: Dict[str, Any], embedded: bool) -> str:
    left = diff.get("left") or {}
    right = diff.get("right") or {}
    config_rows = "".join(
        f"<tr><td>{html.escape(str(item.get('field')))}</td><td>{html.escape(str(item.get('left')))}</td><td>{html.escape(str(item.get('right')))}</td></tr>"
        for item in (diff.get("config_diff") or [])
    )
    if not config_rows:
        config_rows = '<tr><td colspan="3">No config differences.</td></tr>'

    def metric_rows(side: Dict[str, Any]) -> str:
        failure = side.get("first_failure_step") or {}
        return "".join(
            f"<tr><td>{html.escape(label)}</td><td>{html.escape(str(value))}</td></tr>"
            for label, value in [
                ("status", side.get("status")),
                ("official_run", side.get("official_run")),
                ("replay_mode", side.get("replay_mode")),
                ("stop_reason", side.get("stop_reason")),
                ("final_result", side.get("final_result")),
                ("step_count", side.get("step_count")),
                ("event_count", side.get("event_count")),
                ("token_usage", side.get("token_usage")),
                ("latency_seconds", side.get("latency_seconds")),
                ("cost", side.get("cost")),
                ("parser", json.dumps(side.get("parser") or {}, ensure_ascii=False)),
                (
                    "first_failure_step",
                    json.dumps(failure, ensure_ascii=False) if failure else "-",
                ),
            ]
        )

    left_id = html.escape(str(left.get("run_id", "")))
    right_id = html.escape(str(right.get("run_id", "")))
    buttons = ""
    if not embedded:
        buttons = (
            f'<a class="btn" href="/run/{left_id}">view {left_id}</a>'
            f'<a class="btn" href="/run/{right_id}">view {right_id}</a>'
            f'<a class="btn" href="/export/diff/{left_id}/{right_id}">export html</a>'
            '<a class="btn ghost" href="/">board</a>'
            + _THEME_TOGGLE_HTML
        )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>qita diff {left_id} vs {right_id}</title>
{_DESIGN_HEAD}
<style>
{_DESIGN_TOKENS}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--txt);font-family:var(--font-body)}}
.wrap{{max-width:1240px;margin:0 auto;padding:18px}} .top{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center}}
.btn{{display:inline-block;border:1px solid var(--line);padding:7px 11px;border-radius:var(--radius-md);text-decoration:none;color:var(--txt);background:var(--surface-1);font-size:12px}}
.btn:hover{{border-color:var(--accent)}} .btn.ghost{{background:transparent}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}}
.card{{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:12px}} .meta{{color:var(--muted);font-size:12px}}
table{{width:100%;border-collapse:collapse;margin-top:10px}} td,th{{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top;font-size:12px}}
th{{color:var(--muted);font-weight:700}} .full{{margin-top:12px}} code{{background:var(--surface-2);padding:2px 5px;border-radius:var(--radius-sm)}}
@media (max-width:980px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<div class="wrap">
  <div class="top">
    <div><div style="font-size:24px;font-weight:800">QitOS Diff</div><div class="meta">{left_id} vs {right_id}</div></div>
    <div>{buttons}</div>
  </div>
  <div class="grid">
    <div class="card"><div style="font-size:18px;font-weight:700">{left_id}</div><table>{metric_rows(left)}</table></div>
    <div class="card"><div style="font-size:18px;font-weight:700">{right_id}</div><table>{metric_rows(right)}</table></div>
  </div>
  <div class="card full">
    <div style="font-size:18px;font-weight:700">Run Config Diff</div>
    <table>
      <thead><tr><th>field</th><th>{left_id}</th><th>{right_id}</th></tr></thead>
      <tbody>{config_rows}</tbody>
    </table>
  </div>
</div>
</body></html>"""


def _render_branch_comparison_html(payload: Dict[str, Any], step_id: str) -> str:
    """Render a page comparing branch candidates at a given step."""
    import html as _html

    safe_run = _html.escape(str(payload.get("run_id", "")))
    safe_step = _html.escape(str(step_id))
    steps = payload.get("steps", [])
    step_data = None
    for s in steps:
        if str(getattr(s, "step_id", s.get("step_id", ""))) == str(step_id):
            step_data = s
            break

    step_info = "Step not found"
    if step_data is not None:
        sd = step_data if isinstance(step_data, dict) else {}
        step_info = _html.escape(json.dumps(sd, ensure_ascii=False, indent=2)[:2000])

    candidates = []
    if isinstance(step_data, dict):
        candidates = step_data.get("candidates", [])

    cand_rows = ""
    for i, c in enumerate(candidates):
        c_escaped = _html.escape(json.dumps(c, ensure_ascii=False, indent=2)[:1000])
        cand_rows += f'<div class="card"><div style="font-weight:700">Candidate {i}</div><pre style="font-size:11px;white-space:pre-wrap;max-height:300px;overflow:auto">{c_escaped}</pre></div>'

    if not cand_rows:
        cand_rows = '<div class="muted">No branch candidates recorded for this step.</div>'

    # Check for grounding failure
    grounding_banner = ""
    if isinstance(step_data, dict):
        critic_outputs = step_data.get("critic_outputs", [])
        for co in critic_outputs:
            if isinstance(co, dict) and co.get("action") == "retry":
                reason = str(co.get("reason", ""))
                if "grounding" in reason.lower() or "element not found" in reason.lower() or "coordinates" in reason.lower():
                    grounding_banner = f'<div style="padding:10px;margin:8px 0;border-radius:var(--radius-md);background:var(--err-soft);border:2px solid var(--err);color:var(--err);font-weight:600">Grounding failure: {_html.escape(reason)}</div>'
                    break

    return f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>branch compare · {safe_run} · step {safe_step}</title>
{_DESIGN_HEAD}
<style>{_DESIGN_TOKENS}</style>
<style>
*{{box-sizing:border-box}} body{{margin:0;font-family:var(--font-body);background:var(--bg);color:var(--txt)}}
.wrap{{max-width:960px;margin:0 auto;padding:24px 18px}}
.card{{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:14px;margin:8px 0}}
.id{{font-weight:700;font-size:18px}} .muted{{color:var(--muted);font-size:12px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.btn{{display:inline-flex;align-items:center;border:1px solid var(--line);color:var(--txt);background:var(--surface-1);padding:6px 10px;border-radius:var(--radius-md);font-size:12px;text-decoration:none;cursor:pointer}}
.btn:hover{{border-color:var(--accent)}}
</style></head>
<body>
<div class="wrap">
  <div class="id">Branch compare · {safe_run} · step {safe_step}</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0"><a class="btn" href="/run/{safe_run}">back to run</a>{_THEME_TOGGLE_HTML}</div>
  {grounding_banner}
  <div class="grid">{cand_rows}</div>
  <div class="card" style="margin-top:12px">
    <div style="font-weight:700;margin-bottom:8px">Step data</div>
    <pre style="font-size:11px;white-space:pre-wrap;max-height:300px;overflow:auto">{step_info}</pre>
  </div>
</div>
</body></html>"""


def _render_run_html(payload: Dict[str, Any], embedded: bool) -> str:
    run_id = html.escape(str(payload.get("run_id", "")))
    run_path = html.escape(str(payload.get("run", "")))
    manifest = html.escape(
        json.dumps(payload.get("manifest", {}), ensure_ascii=False, indent=2)
    )
    payload_json = _json_for_script(payload)
    buttons = ""
    buttons = _THEME_TOGGLE_HTML
    if not embedded:
        buttons = (
            f'<a class="btn" href="/export/raw/{run_id}">export raw</a>'
            f'<a class="btn" href="/export/html/{run_id}">export html</a>'
            f'<a class="btn" href="/replay/{run_id}">replay</a>'
            f'<button class="btn" id="streamBtn" onclick="startStream()">live</button>'
            '<a class="btn ghost" href="/">board</a>'
            + _THEME_TOGGLE_HTML
        )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>qita run {run_id}</title>
{_DESIGN_HEAD}
<style>
{_DESIGN_TOKENS}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--txt);font-family:var(--font-body)}}
.wrap{{max-width:1680px;margin:0 auto;padding:18px}}
.top{{position:sticky;top:0;background:var(--top-bg);backdrop-filter:blur(8px);padding:12px 0 14px;z-index:10;border-bottom:1px solid var(--line)}}
.title{{font-size:22px;font-weight:700;letter-spacing:-.4px;overflow-wrap:anywhere}} .muted{{color:var(--muted);font-size:12px;overflow-wrap:anywhere}}
.toolbar{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
.btn{{display:inline-block;border:1px solid var(--line);padding:7px 11px;border-radius:var(--radius-md);text-decoration:none;color:var(--txt);background:var(--surface-1);font-size:12px}}
.btn:hover{{border-color:var(--accent)}} .btn.ghost{{background:transparent}}
.layout{{display:grid;grid-template-columns:260px minmax(0,1fr) 360px;gap:12px;margin-top:12px;align-items:start}}
.side{{position:sticky;top:84px;height:calc(100vh - 120px);overflow:auto;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:10px}}
.inspector{{position:sticky;top:84px;height:calc(100vh - 120px);overflow:auto;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:12px}}
.inspector h3{{margin:0 0 8px;font-size:13px;color:var(--txt)}} .inspector-tabs{{display:flex;gap:6px;margin:8px 0}}
.inspector-tab{{border:1px solid var(--line);background:var(--surface-2);color:var(--muted);border-radius:var(--radius-md);font-size:11px;padding:4px 7px;cursor:pointer}}
.inspector-tab.active{{color:var(--txt);border-color:var(--accent)}}
.main{{min-width:0}}
.manifest{{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:12px;margin-top:0}}
.tabs{{display:flex;gap:8px;margin-bottom:10px}}
.tab{{border:1px solid var(--line);background:var(--surface-1);color:var(--txt);padding:8px 12px;border-radius:var(--radius-pill);cursor:pointer;font-size:13px}}
.tab.active{{background:var(--surface-2);border-color:var(--accent)}}
.panel{{display:none}}
.panel.active{{display:block}}
.controls{{display:grid;grid-template-columns:1.2fr .8fr .8fr .8fr auto auto auto;gap:8px;margin:12px 0}}
.controls input,.controls select,.controls button{{min-width:0}}
.controls input,.controls select{{border:1px solid var(--line);background:var(--surface-1);color:var(--txt);border-radius:var(--radius-md);padding:8px 10px;font-size:12px}}
.diagnosis-label{{font-size:12px;color:var(--subtle);text-transform:uppercase;letter-spacing:.35px;margin:2px 0 8px}}
.run-summary{{display:grid;grid-template-columns:1.1fr 1.4fr .9fr .9fr;gap:10px;margin:10px 0 12px}}
.summary-panel{{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:12px;min-width:0}}
.summary-panel.primary{{border-color:var(--err-border);background:linear-gradient(180deg,var(--err-soft),var(--surface-1))}}
.summary-title{{font-size:11px;color:var(--subtle);text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px}}
.summary-value{{font-size:14px;line-height:1.45;color:var(--txt);word-break:break-word}}
.summary-actions{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}}
.focus-tabs{{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px}}
.focus-tab{{border:1px solid var(--line);background:var(--surface-2);color:var(--muted);border-radius:var(--radius-md);font-size:11px;padding:6px 7px;cursor:pointer;text-align:center}}
.focus-tab.active{{color:var(--txt);border-color:var(--accent);background:var(--surface-3)}}
.story-title{{display:flex;justify-content:space-between;align-items:center;margin:4px 0 10px}}
.story-title h3{{font-size:14px;margin:0;color:var(--txt)}}
.story-rail{{display:grid;gap:8px}}
.story-step{{display:grid;grid-template-columns:76px minmax(0,1fr) 74px;gap:10px;align-items:center;border:1px solid var(--line);border-radius:var(--radius-md);background:var(--surface-2);padding:8px 10px;cursor:pointer}}
.story-step:hover{{border-color:var(--accent)}}
.story-step.critical{{border-color:var(--err-border);background:linear-gradient(180deg,var(--err-soft),var(--surface-2))}}
.story-step.important{{border-color:var(--warn-border)}}
.story-step.watch{{border-color:var(--accent-border)}}
.story-step-id{{font-size:12px;font-weight:700;color:var(--txt)}}
.story-step-main{{min-width:0;display:grid;gap:4px}}
.story-step-title{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;font-size:12px;color:var(--txt)}}
.story-step-summary{{font-size:12px;color:var(--muted);line-height:1.35;word-break:break-word}}
.story-step-meta{{display:flex;flex-wrap:wrap;gap:4px}}
.story-step-time{{font-size:11px;color:var(--muted);text-align:right}}
.story-card{{display:grid;gap:8px}}
.story-line{{display:grid;grid-template-columns:90px 1fr;gap:10px;align-items:start}}
.story-line .k{{padding-top:1px}}
.causal-stack{{display:grid;gap:10px}}
.causal-stage{{display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px;align-items:start}}
.causal-stage>.k{{padding-top:9px}}
.call-section{{border-top:1px solid var(--line);padding-top:10px;min-width:0}}
.call-section-head{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}}
.call-section-title{{font-size:12px;font-weight:700;color:var(--txt)}}
.call-count{{font-size:10px;color:var(--muted);font-family:var(--font-mono)}}
.call-badges{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}}
.call-badge{{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line-strong);background:var(--surface-2);color:var(--txt);border-radius:var(--radius-md);padding:5px 8px;font-size:11px;font-weight:600;cursor:pointer;max-width:100%}}
.call-badge:hover,.call-badge.active{{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent-soft)}}
.call-badge-index{{display:inline-grid;place-items:center;width:17px;height:17px;border-radius:50%;background:var(--surface-3);font-family:var(--font-mono);font-size:9px}}
.call-badge-status{{font-size:9px;text-transform:uppercase;color:var(--muted)}}
.call-badge.error,.call-badge.no_trigger,.call-badge.submission_error{{border-color:var(--err-border);background:var(--err-soft)}}
.call-badge.verified,.call-badge.success{{border-color:var(--ok-border);background:var(--ok-soft)}}
.call-list{{display:grid;gap:8px}}
.call-unit{{border:1px solid var(--line);border-left:3px solid var(--line-strong);border-radius:var(--radius-md);background:var(--surface-1);min-width:0;overflow:hidden}}
.call-unit.active{{border-color:var(--accent);border-left-color:var(--accent)}}
.call-unit.error,.call-unit.no_trigger,.call-unit.submission_error,.call-unit.blocked{{border-left-color:var(--err);background:var(--err-soft)}}
.call-unit.success,.call-unit.verified{{border-left-color:var(--ok)}}
.call-unit summary{{cursor:pointer;list-style:none}}
.call-unit summary::-webkit-details-marker{{display:none}}
.call-head{{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 10px}}
.call-identity{{display:flex;align-items:center;gap:7px;min-width:0}}
.call-tool{{font-size:12px;font-weight:700;color:var(--txt);overflow-wrap:anywhere}}
.call-meta{{display:flex;align-items:center;justify-content:flex-end;gap:7px;flex-wrap:wrap;color:var(--muted);font-size:10px;font-family:var(--font-mono)}}
.call-status{{font-family:var(--font-body);font-weight:700;text-transform:uppercase}}
.call-status.error,.call-status.no_trigger,.call-status.submission_error,.call-status.blocked{{color:var(--err)}}
.call-status.success,.call-status.verified{{color:var(--ok)}}
.call-body{{border-top:1px solid var(--line);padding:9px 10px;display:grid;gap:8px;min-width:0;background:var(--surface-1)}}
.param-strip{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:6px}}
.param-pair{{display:grid;grid-template-columns:auto minmax(0,1fr);gap:6px;align-items:start;border:1px solid var(--line);background:var(--surface-2);padding:6px 7px;border-radius:var(--radius-sm);min-width:0}}
.param-key{{font-size:10px;color:var(--subtle);font-family:var(--font-mono)}}
.param-value{{font-size:11px;color:var(--txt);font-family:var(--font-mono);white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}}
.paired-result{{border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--surface-2);min-width:0}}
.paired-result[open]{{background:var(--surface-1)}}
.paired-result>summary{{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 8px;font-size:11px;color:var(--txt)}}
.result-summary{{overflow-wrap:anywhere;min-width:0}}
.pairing-note{{font-size:10px;color:var(--muted)}}
.env-lane{{border:1px dashed var(--line-strong);border-radius:var(--radius-md);background:var(--surface-2);min-width:0}}
.env-lane>summary{{cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;color:var(--muted);font-size:11px}}
.unmatched-lane{{border-color:var(--warn-border);background:var(--warn-soft)}}
.inspector-call-list{{display:grid;gap:6px;margin:8px 0}}
.inspector-call{{width:100%;display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:7px;align-items:center;text-align:left;border:1px solid var(--line);background:var(--surface-2);color:var(--txt);border-radius:var(--radius-md);padding:7px 8px;cursor:pointer}}
.inspector-call:hover,.inspector-call.active{{border-color:var(--accent)}}
.role-chip{{display:inline-flex;align-items:center;border:1px solid var(--line-strong);border-radius:var(--radius-pill);padding:2px 8px;font-size:10px;color:var(--txt);background:var(--surface-2)}}
.role-chip.critical{{color:var(--err);border-color:var(--err-border);background:var(--err-soft)}} .role-chip.important{{color:var(--warn);border-color:var(--warn-border);background:var(--warn-soft)}} .role-chip.watch{{color:var(--kind-observation);border-color:var(--accent-border);background:var(--accent-soft)}}
.metadata-drawer{{margin:0 0 12px;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:10px 12px}}
.metadata-drawer summary{{cursor:pointer;color:var(--muted);font-size:12px;font-weight:600}}
.risk-chip{{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--line-strong);border-radius:var(--radius-pill);padding:2px 7px;font-size:10px;color:var(--muted);background:var(--surface-2)}}
.risk-chip.error{{color:var(--err);border-color:var(--err-border);background:var(--err-soft)}} .risk-chip.warn{{color:var(--warn);border-color:var(--warn-border);background:var(--warn-soft)}} .risk-chip.ok{{color:var(--ok);border-color:var(--ok-border);background:var(--ok-soft)}}
.overview{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin:10px 0 12px}}
.ov{{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-md);padding:8px 10px}}
.ov .k{{font-size:11px;color:var(--subtle);text-transform:uppercase;letter-spacing:.3px}}
.ov .v{{font-size:14px;color:var(--txt);font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.timeline{{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:12px;margin:0 0 12px}}
.vtimeline{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}}
.vcard{{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-md);padding:8px}}
.vthumb{{position:relative;border:1px solid var(--line-strong);border-radius:var(--radius-md);overflow:hidden;background:var(--bg);min-height:110px;display:flex;align-items:center;justify-content:center}}
.vthumb img{{max-width:100%;display:block}}
.voverlay{{position:absolute;inset:0;pointer-events:none}}
.vdot{{position:absolute;width:12px;height:12px;border-radius:var(--radius-pill);background:var(--err);border:2px solid var(--txt);transform:translate(-50%,-50%)}}
.vbox{{position:absolute;border:2px solid var(--accent);background:var(--accent-soft);border-radius:var(--radius-xs)}}
.trow{{display:grid;grid-template-columns:82px 1fr 64px;gap:8px;align-items:center;margin:6px 0}}
.tlabel{{font-size:12px;color:var(--muted)}}
.track{{height:16px;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-pill);overflow:hidden;position:relative}}
.gantt-svg{{width:100%;height:auto;display:block;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg)}}
.gantt-lane{{fill:var(--surface-2);stroke:var(--line);stroke-width:1}}
.gantt-bar{{fill-opacity:0.7;rx:4;ry:4}}
.gantt-arrow{{fill:none;stroke:#bfa04e;stroke-width:2;marker-end:url(#hArrow)}}
.gantt-label{{fill:var(--muted);font-size:11px;font-family:var(--font-body)}}
.gantt-step-label{{fill:var(--subtle);font-size:10px;font-family:var(--font-mono)}}
.seg{{height:100%;display:inline-block}}
.heat0{{filter:brightness(0.85)}} .heat1{{filter:brightness(1)}} .heat2{{filter:brightness(1.15)}} .heat3{{filter:brightness(1.3)}}
.tdur{{font-size:11px;color:var(--muted);text-align:right}}
.context-chart{{display:grid;gap:10px}}
.context-head{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--muted)}}
.context-svg{{width:100%;height:auto;display:block;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg)}}
.context-axis{{stroke:var(--line-strong);stroke-width:1}}
.context-grid{{stroke:var(--line);stroke-width:1;stroke-dasharray:4 6}}
.context-line{{fill:none;stroke:var(--accent);stroke-width:3;stroke-linecap:round;stroke-linejoin:round}}
.context-fill{{fill:var(--accent-soft)}}
.context-point{{fill:var(--surface-1);stroke:var(--accent);stroke-width:2}}
.context-label{{fill:var(--subtle);font-size:11px}}
.compact-dot{{stroke:var(--surface-1);stroke-width:1.5}}
.compact-list{{display:grid;gap:6px}}
.compact-item{{display:grid;grid-template-columns:92px 1fr;gap:8px;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-md);padding:8px}}
.compact-step{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px}}
.compact-desc{{font-size:12px;color:var(--txt);word-break:break-word}}
.flow{{display:grid;grid-template-columns:1fr;gap:12px}}
@media (max-width:1180px){{.layout{{grid-template-columns:minmax(0,1fr)}} .main{{order:1}} .side{{order:2}} .inspector{{order:3}} .side,.inspector{{position:relative;top:0;height:auto;min-width:0}} .controls{{grid-template-columns:1fr 1fr}} .run-summary{{grid-template-columns:1fr}}}}
@media (max-width:560px){{.wrap{{padding:14px}} .toolbar{{gap:6px}} .btn{{white-space:normal;text-align:center}} .controls{{grid-template-columns:1fr}} .focus-tabs{{grid-template-columns:1fr}} .story-step{{grid-template-columns:58px minmax(0,1fr);gap:8px}} .story-step-time{{grid-column:2;text-align:left}} .story-line,.causal-stage{{grid-template-columns:1fr}} .causal-stage>.k{{padding-top:0}} .call-head{{align-items:flex-start;flex-direction:column}} .call-meta{{justify-content:flex-start}} .param-strip{{grid-template-columns:1fr}} .kv{{grid-template-columns:92px minmax(0,1fr)}} .tree-leaf{{grid-template-columns:1fr}}}}
.card{{break-inside:avoid;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:12px;margin:0 0 12px;min-width:0}}
.kind-thinking{{border-left:4px solid var(--kind-thinking)}} .kind-action{{border-left:4px solid var(--kind-action)}}
.kind-observation{{border-left:4px solid var(--kind-observation)}} .kind-critic{{border-left:4px solid var(--kind-critic)}}
.kind-handoff{{border-left:4px solid var(--kind-handoff)}} .kind-delegation{{border-left:4px solid var(--kind-delegation)}}
.kind-fanout{{border-left:4px solid var(--kind-fanout)}} .kind-other{{border-left:4px solid var(--kind-other)}}
.card-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.step{{font-weight:700}}
h4{{margin:8px 0 6px;font-size:12px;color:var(--subtle);text-transform:uppercase;letter-spacing:.3px;display:flex;justify-content:space-between;align-items:center}}
pre{{margin:0;background:var(--surface-2);border:1px solid var(--line);padding:10px;border-radius:var(--radius-md);max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-word;color:var(--txt);font-size:12px}}
.sbtn{{border:1px solid var(--line);background:var(--surface-2);color:var(--txt);padding:2px 6px;border-radius:var(--radius-sm);font-size:11px;cursor:pointer}}
.kv{{display:grid;grid-template-columns:120px 1fr;gap:6px 10px;background:var(--surface-2);border:1px solid var(--line);padding:8px;border-radius:var(--radius-md)}}
.k{{font-size:11px;color:var(--subtle);text-transform:uppercase;letter-spacing:.3px}}
.v{{font-size:12px;color:var(--txt);word-break:break-word}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}}
.chip{{font-size:11px;padding:2px 8px;border-radius:var(--radius-pill);border:1px solid var(--line-strong);background:var(--surface-2);color:var(--muted)}}
.list{{display:grid;gap:8px}}
.item{{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius-md);padding:8px}}
.raw{{margin-top:6px}}
.tree-wrap{{margin-top:8px}}
.tree{{border:1px solid var(--line);border-radius:var(--radius-md);padding:8px;background:var(--surface-2)}}
.tree details{{margin:4px 0}}
.tree summary{{cursor:pointer;color:var(--muted);font-size:12px}}
.tree-children{{margin-left:10px;border-left:1px dashed var(--line-strong);padding-left:10px}}
.tree-leaf{{display:grid;grid-template-columns:130px 1fr;gap:8px;margin:4px 0}}
.tree-key{{font-size:12px;color:var(--subtle)}}
.tree-val{{font-size:12px;color:var(--txt);word-break:break-word}}
.toc-item{{display:block;width:100%;text-align:left;border:1px solid var(--line);background:var(--surface-1);color:var(--txt);padding:7px 8px;border-radius:var(--radius-md);font-size:12px;cursor:pointer;margin-bottom:6px}}
.toc-item:hover{{border-color:var(--accent)}} .toc-item.active{{border-color:var(--accent);background:var(--surface-2)}}
.toc-flags{{display:flex;flex-wrap:wrap;gap:3px;margin-top:5px}} .toc-flag{{width:7px;height:7px;border-radius:999px;background:var(--line-strong)}} .toc-flag.parser_error,.toc-flag.tool_or_event_error,.toc-flag.critic_stop,.toc-flag.cybergym_verification_failure{{background:var(--err)}} .toc-flag.critic_retry,.toc-flag.context_pressure,.toc-flag.context_compact,.toc-flag.cybergym_poc_submission{{background:var(--warn)}} .toc-flag.visual_evidence{{background:var(--kind-observation)}}
.full-text{{margin:0;background:var(--surface-2);border:1px solid var(--line);padding:10px;border-radius:var(--radius-md);max-height:420px;overflow:auto;white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;color:var(--txt);font-size:12px;line-height:1.55}}
.summary-line{{color:var(--muted);font-size:12px;line-height:1.45;margin:0 0 8px;word-break:break-word}}
.evidence-details{{border:1px solid var(--line);border-radius:var(--radius-md);background:var(--surface-2);min-width:0}}
.evidence-details[open]{{background:var(--surface-1)}}
.evidence-details.input-evidence[open]{{border-color:var(--kind-thinking)}}
.evidence-details.outcome-evidence[open]{{border-color:var(--kind-observation)}}
.evidence-details>summary{{cursor:pointer;list-style:none;padding:8px 10px;color:var(--txt);font-size:12px;font-weight:600;display:flex;align-items:center;justify-content:space-between;gap:8px}}
.evidence-details>summary::-webkit-details-marker{{display:none}}
.evidence-details>summary::after{{content:'+';color:var(--muted);font-family:var(--font-mono);font-size:14px}}
.evidence-details[open]>summary::after{{content:'-'}}
.input-evidence .evidence-label{{color:var(--kind-thinking)}}
.outcome-evidence .evidence-label{{color:var(--kind-observation)}}
.evidence-meta{{color:var(--muted);font-size:10px;font-weight:400}}
.evidence-shell{{border-top:1px solid var(--line);min-width:0}}
.evidence-toolbar{{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 9px;color:var(--muted);font-size:10px}}
.evidence-code{{margin:0;border:0;border-top:1px solid var(--line);border-radius:0;max-height:min(62vh,620px);overflow:auto;white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;tab-size:2;font-family:var(--font-mono);font-size:11px;line-height:1.55}}
.evidence-code code{{font:inherit;color:inherit}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(-4px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.4}}}}
.live-dot{{display:inline-block;width:8px;height:8px;border-radius:9999px;background:var(--ok);animation:pulse 1.5s ease infinite;margin-right:6px}}
</style></head><body>
<div class="top"><div class="wrap">
  <div class="title">QitOS Trace · {run_id}</div>
  <div class="muted">{run_path}</div>
  <div class="toolbar">{buttons}</div>
</div></div>
<div class="wrap">
  <div class="layout">
    <aside class="side">
      <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Focus Navigator</div>
      <div class="focus-tabs" id="focusTabs">
        <button class="focus-tab active" type="button" data-focus-mode="focus">Critical</button>
        <button class="focus-tab" type="button" data-focus-mode="submissions">Submissions</button>
        <button class="focus-tab" type="button" data-focus-mode="errors">Errors</button>
        <button class="focus-tab" type="button" data-focus-mode="phase">Phase Changes</button>
        <button class="focus-tab" type="button" data-focus-mode="all">All Steps</button>
      </div>
      <div id="toc"></div>
    </aside>
    <section class="main">
      <div class="tabs">
        <button class="tab active" id="tabTraj" type="button">Traj</button>
        <button class="tab" id="tabManifest" type="button">Manifest</button>
      </div>
      <section class="panel active" id="panelTraj">
        <div class="diagnosis-label">Diagnosis Strip</div>
        <section class="run-summary" id="runSummary"></section>
        <details class="metadata-drawer">
          <summary>Run Metadata · config, cost, context, parser, critic, and raw telemetry</summary>
          <section class="overview" id="overview"></section>
          <section class="timeline" id="costPanelSection">
            <h4>cost summary</h4>
            <div id="costPanel"></div>
          </section>
          <section class="timeline" id="contextTimelineSection">
            <h4>context timeline</h4>
            <div id="contextTimeline"></div>
          </section>
          <section class="timeline" id="parserTimelineSection">
            <h4>parser timeline</h4>
            <div id="parserTimeline"></div>
          </section>
          <section class="timeline" id="criticTimelineSection">
            <h4>critic timeline</h4>
            <div id="criticTimeline"></div>
          </section>
        </details>
        <div class="controls">
          <input id="q" placeholder="Filter by text in observation/decision/action/critic/events"/>
          <select id="eventFilter"><option value="">All events</option></select>
          <select id="agentFilter"><option value="">All agents</option></select>
          <select id="sort"><option value="asc">step asc</option><option value="desc">step desc</option></select>
          <button class="btn" id="fontDown" type="button">A-</button>
          <button class="btn" id="fontReset" type="button">A</button>
          <button class="btn" id="fontUp" type="button">A+</button>
        </div>
        <section class="timeline" id="agentBehaviorTimeline">
          <div class="story-title"><h3>Agent Behavior Story</h3><div class="muted">focused steps first; full evidence lives in Inspector</div></div>
          <div id="timeline"></div>
        </section>
        <section class="timeline" id="screenshotStripSection" style="display:none">
          <h4>screenshot strip</h4>
          <div id="screenshotStrip" style="display:flex;gap:6px;overflow-x:auto;padding:8px 0"></div>
        </section>
        <section class="timeline" id="visualTimelineSection">
          <h4>visual timeline</h4>
          <div id="visualTimeline"></div>
        </section>
        <section class="timeline" id="handoffGanttSection">
          <h4>handoff gantt</h4>
          <div id="handoffGantt"></div>
        </section>
        <section class="flow" id="flow"></section>
      </section>
      <section class="panel" id="panelManifest">
        <section class="manifest"><h4>manifest</h4><pre>{manifest}</pre></section>
      </section>
    </section>
    <aside class="inspector">
      <h3>Inspector</h3>
      <div class="muted">Select a step from the navigator, timeline, or trajectory cards.</div>
      <div id="inspector"></div>
    </aside>
  </div>
</div>
<script id="payload" type="application/json">{payload_json}</script>
<script>
const embedded = {str(bool(embedded)).lower()};
const payload = JSON.parse(document.getElementById('payload').textContent || '{{}}');
const steps = Array.isArray(payload.steps) ? payload.steps : [];
const stepInteractions = Array.isArray(payload.step_interactions) ? payload.step_interactions : [];
const eventsByStep = payload.events_by_step || {{}};
const flow = document.getElementById('flow');
const toc = document.getElementById('toc');
const timelineRoot = document.getElementById('timeline');
const visualTimelineRoot = document.getElementById('visualTimeline');
const contextTimelineRoot = document.getElementById('contextTimeline');
const parserTimelineRoot = document.getElementById('parserTimeline');
const criticTimelineRoot = document.getElementById('criticTimeline');
const overview = document.getElementById('overview');
const runSummary = document.getElementById('runSummary');
const inspector = document.getElementById('inspector');
const fontDownBtn = document.getElementById('fontDown');
const fontResetBtn = document.getElementById('fontReset');
const fontUpBtn = document.getElementById('fontUp');
const tabTraj = document.getElementById('tabTraj');
const tabManifest = document.getElementById('tabManifest');
const panelTraj = document.getElementById('panelTraj');
const panelManifest = document.getElementById('panelManifest');
let collapsedAll = false;
let fontScale = Number(localStorage.getItem('qita_view_font_scale') || '1.1');
let activeTab = localStorage.getItem('qita_view_tab') || 'traj';
let activeStepId = steps.length ? String(steps[0].step_id) : '';
let activeCallIndex = null;
let focusMode = localStorage.getItem('qita_focus_mode') || 'focus';
const runFocus = payload.run_focus || {{}};
const cybergymFocus = payload.cybergym_focus || {{}};
function esc(s){{
  return String(s).replace(/[&<>]/g, function(c){{ return {{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]; }});
}}
function cardText(step, events){{
  return JSON.stringify(step).toLowerCase() + ' ' + JSON.stringify(events).toLowerCase();
}}
function toPreview(v){{
  if(v === null || v === undefined) return '-';
  if(typeof v === 'string') return v.length > 180 ? (v.slice(0, 180) + '...') : v;
  if(typeof v === 'number' || typeof v === 'boolean') return String(v);
  if(Array.isArray(v)) return '[' + v.length + ' items]';
  if(typeof v === 'object') return '{{...}}';
  return String(v);
}}
function kvRow(k, v){{
  return '<div class="k">'+esc(k)+'</div><div class="v">'+esc(toPreview(v))+'</div>';
}}
function kvBlock(rows){{
  return '<div class="kv">' + rows.join('') + '</div>';
}}
function truncateText(v, n){{
  const s = String(v === null || v === undefined ? '' : v);
  const lim = Number(n || 260);
  if(s.length <= lim) return s;
  return s.slice(0, lim) + '...';
}}
function firstLine(v, n){{
  const s = String(v === null || v === undefined ? '' : v).replace(/\\s+/g, ' ').trim();
  const lim = Number(n || 180);
  if(s.length <= lim) return s;
  return s.slice(0, lim) + '...';
}}
function fullTextBlock(text, label, open){{
  const value = String(text === null || text === undefined ? '' : text);
  if(!value) return '<div class="muted">No content recorded.</div>';
  const summary = esc(label || ('full content · ' + value.length + ' chars'));
  const openAttr = open ? ' open' : '';
  return '<details class="raw"'+openAttr+'><summary class="muted">'+summary+'</summary><pre class="full-text">'+esc(value)+'</pre></details>';
}}
function fullJsonBlock(value, label, open){{
  return fullTextBlock(JSON.stringify(value, null, 2), label || 'full JSON', open);
}}
function copyCodeBlock(button){{
  const shell = button && button.closest ? button.closest('.evidence-shell') : null;
  const code = shell ? shell.querySelector('code') : null;
  const value = code ? String(code.textContent || '') : '';
  if(!value) return;
  const done = function(){{
    const previous = button.textContent;
    button.textContent = 'Copied';
    window.setTimeout(function(){{ button.textContent = previous; }}, 1200);
  }};
  if(navigator.clipboard && navigator.clipboard.writeText){{
    navigator.clipboard.writeText(value).then(done).catch(function(){{}});
    return;
  }}
  const area = document.createElement('textarea');
  area.value = value;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  try {{ document.execCommand('copy'); done(); }} catch (_e) {{}}
  document.body.removeChild(area);
}}
function recordedInput(step, events){{
  const rows = Array.isArray(events) ? events : [];
  for(const event of rows){{
    const eventPayload = event && typeof event.payload === 'object' ? event.payload : {{}};
    if(String(eventPayload.stage || '') !== 'model_input') continue;
    if(typeof eventPayload.prepared_full === 'string' && eventPayload.prepared_full){{
      return {{text: eventPayload.prepared_full, source: 'Agent-visible input · prepared_full', format: 'text'}};
    }}
    if(typeof eventPayload.prepared === 'string' && eventPayload.prepared){{
      return {{text: eventPayload.prepared, source: 'Agent-visible input · prepared', format: 'text'}};
    }}
  }}
  const observation = step && step.observation;
  if(observation && typeof observation === 'object' && Object.keys(observation).length){{
    return {{text: JSON.stringify(observation, null, 2), source: 'Recorded step observation fallback', format: 'json'}};
  }}
  return {{text: '', source: 'No input recorded', format: 'text'}};
}}
function renderCodeEvidence(kind, title, status, evidence, meta, open){{
  const text = String((evidence && evidence.text) || '');
  const openAttr = open ? ' open' : '';
  const classes = 'evidence-details '+kind+'-evidence '+kind+'-details';
  const heading = '<span class="evidence-label">'+esc(title)+'</span>' + (status ? ' · '+esc(status) : '');
  if(!text){{
    return '<details class="'+classes+'"'+openAttr+'><summary><span>'+heading+'</span><span class="evidence-meta">not recorded</span></summary><div class="evidence-shell"><div class="muted" style="padding:10px">No '+esc(kind)+' recorded.</div></div></details>';
  }}
  return '<details class="'+classes+'"'+openAttr+'>' +
    '<summary><span>'+heading+'</span><span class="evidence-meta">'+esc(meta)+'</span></summary>' +
    '<div class="evidence-shell"><div class="evidence-toolbar"><span>'+esc(evidence.source)+'</span><button class="sbtn" type="button" title="Copy complete '+esc(kind)+'" onclick="copyCodeBlock(this)">Copy</button></div>' +
    '<pre class="evidence-code"><code>'+esc(text)+'</code></pre></div></details>';
}}
function renderInputBlock(step, open){{
  const sid = String((step && step.step_id) === undefined ? '' : step.step_id);
  const evidence = recordedInput(step || {{}}, eventsByStep[sid] || []);
  const text = String(evidence.text || '');
  return renderCodeEvidence('input', 'Input', '', evidence, text.length + ' chars', open);
}}
function stepInteractionFor(sid){{
  const derived = stepInteractions.find(function(item){{ return String(item.step_id) === String(sid); }});
  if(derived) return derived;
  const step = steps.find(function(item){{ return String(item.step_id) === String(sid); }}) || {{}};
  const actions = Array.isArray(step.actions) ? step.actions : [];
  const results = Array.isArray(step.action_results) ? step.action_results : [];
  const invocations = Array.isArray(step.tool_invocations) ? step.tool_invocations : [];
  const environmentResults = results.filter(function(result){{
    const metadata = result && typeof result === 'object' && result.metadata && typeof result.metadata === 'object' ? result.metadata : {{}};
    const output = result && typeof result === 'object' && result.output && typeof result.output === 'object' ? result.output : null;
    return String(metadata.source || '').toLowerCase() === 'env' || (!!output && Object.keys(output).length === 1 && Object.prototype.hasOwnProperty.call(output, 'env'));
  }});
  const toolResults = results.filter(function(result){{ return !environmentResults.includes(result); }});
  const calls = actions.map(function(action, index){{
    const item = action && typeof action === 'object' ? action : {{}};
    const result = index < toolResults.length ? toolResults[index] : null;
    const status = result && typeof result === 'object' ? (result.status || (result.error ? 'error' : 'success')) : 'not recorded';
    return {{index:index,tool_name:item.tool||item.name||item.action||item.type||'action',args:item.args||item.kwargs||{{}},action:item,invocation:invocations[index]||null,result:result,raw_result:result,result_source:result === null ? 'not_recorded' : 'raw_fallback',status:status,result_summary:result && typeof result === 'object' ? (result.error || result.message || result.status || 'recorded') : 'not recorded',latency_ms:(invocations[index]||{{}}).latency_ms,attempts:(invocations[index]||{{}}).attempts,pairing_method:'ordered_fallback'}};
  }});
  return {{step_id:sid,calls:calls,environment_results:environmentResults.map(function(result,index){{return {{index:index,result:result,raw_result:result}};}}),unmatched_actions:[],unmatched_results:toolResults.slice(actions.length)}};
}}
function statusClass(value){{
  const status = String(value || 'not_recorded').toLowerCase();
  if(status === 'verified' || status === 'success') return status;
  if(status === 'no_trigger' || status === 'submission_error' || status === 'error' || status === 'blocked') return status;
  return 'not_recorded';
}}
function isAbnormalCall(call){{
  const status = String((call && call.status) || '').toLowerCase();
  return status !== 'success' && status !== 'verified';
}}
function formatLatency(value){{
  const ms = Number(value);
  if(!Number.isFinite(ms)) return 'latency not recorded';
  if(ms >= 1000) return (ms / 1000).toFixed(ms >= 10000 ? 1 : 2) + 's';
  return Math.round(ms) + 'ms';
}}
function fullEvidenceValue(value){{
  if(typeof value === 'string') return value;
  if(value === undefined) return '';
  return JSON.stringify(value, null, 2);
}}
function renderThoughtBlock(step, open){{
  const sid = String((step && step.step_id) === undefined ? '' : step.step_id);
  const thought = extractThought((step && step.decision) || {{}}, eventsByStep[sid] || []);
  const evidence = {{text: thought, source: 'Agent reasoning recorded for this step', format: 'text'}};
  return renderCodeEvidence('thought', 'Thought', '', evidence, thought.length + ' chars', open);
}}
function scalarParamEntries(args){{
  if(!args || typeof args !== 'object' || Array.isArray(args)) return [];
  return Object.entries(args).filter(function(entry){{
    const value = entry[1];
    return value === null || ['string','number','boolean'].includes(typeof value);
  }});
}}
function renderParamStrip(args){{
  const entries = scalarParamEntries(args);
  if(!entries.length) return '';
  return '<div class="param-strip">' + entries.map(function(entry){{
    const value = String(entry[1] === null ? 'null' : entry[1]);
    return '<div class="param-pair"><span class="param-key">'+esc(entry[0])+'</span><span class="param-value">'+esc(firstLine(value, 160))+'</span></div>';
  }}).join('') + '</div>';
}}
function renderCallBadge(call, sid){{
  const index = Number(call.index || 0);
  const status = String(call.status || 'not recorded');
  const klass = statusClass(status);
  return '<button class="call-badge '+klass+'" type="button" data-call-badge="'+index+'" data-select-call="true" data-call-step="'+esc(String(sid))+'" data-call-scroll="true">' +
    '<span class="call-badge-index">'+(index+1)+'</span><span>'+esc(call.tool_name || 'action')+'</span><span class="call-badge-status">'+esc(status)+'</span></button>';
}}
function renderCallResult(call, open){{
  const hasResult = call.result !== undefined && call.result !== null;
  const resultText = hasResult ? fullEvidenceValue(call.result) : '';
  const summary = String(call.result_summary || (hasResult ? 'Recorded result' : 'Result not recorded'));
  const source = call.result_source === 'model_visible' ? 'Agent-visible result · observation_ready.action_results' : call.result_source === 'raw_fallback' ? 'Recorded result fallback · step.action_results' : 'Result not recorded';
  const evidence = {{text: resultText, source: source, format: 'json'}};
  return '<details class="paired-result"'+(open ? ' open' : '')+'><summary><span class="result-summary"><b>Result</b> · '+esc(summary)+'</span><span class="pairing-note">'+esc(call.pairing_method || 'not recorded')+'</span></summary>' +
    '<div style="padding:0 8px 8px">'+renderCodeEvidence('result', 'Complete result', call.status || '', evidence, resultText.length+' chars', true)+'</div></details>';
}}
function renderActionCall(call, sid){{
  const index = Number(call.index || 0);
  const status = String(call.status || 'not recorded');
  const klass = statusClass(status);
  const args = (call.args && typeof call.args === 'object') ? call.args : {{}};
  const argsText = fullEvidenceValue(args);
  const argsEvidence = {{text: argsText, source: 'Complete action arguments', format: 'json'}};
  const open = isAbnormalCall(call);
  const attempts = call.attempts === null || call.attempts === undefined ? 'attempts not recorded' : String(call.attempts)+' attempt'+(Number(call.attempts) === 1 ? '' : 's');
  return '<details class="call-unit '+klass+'" id="call-'+esc(String(sid))+'-'+index+'" data-call-index="'+index+'" data-select-call="true" data-call-step="'+esc(String(sid))+'" data-call-scroll="false"'+(open ? ' open' : '')+'>' +
    '<summary class="call-head" data-select-call="true" data-call-step="'+esc(String(sid))+'" data-call-index="'+index+'" data-call-scroll="false">' +
      '<span class="call-identity"><span class="call-badge-index">'+(index+1)+'</span><span class="call-tool">'+esc(call.tool_name || 'action')+'</span></span>' +
      '<span class="call-meta"><span class="call-status '+klass+'">'+esc(status)+'</span><span>'+esc(formatLatency(call.latency_ms))+'</span><span>'+esc(attempts)+'</span></span>' +
    '</summary><div class="call-body">' +
      renderParamStrip(args) +
      renderCodeEvidence('params', 'Complete parameters', '', argsEvidence, Object.keys(args).length+' fields · '+argsText.length+' chars', false) +
      renderCallResult(call, open) +
    '</div></details>';
}}
function renderUnmatchedEvidence(interaction){{
  const actions = Array.isArray(interaction.unmatched_actions) ? interaction.unmatched_actions : [];
  const results = Array.isArray(interaction.unmatched_results) ? interaction.unmatched_results : [];
  if(!actions.length && !results.length) return '';
  return '<details class="env-lane unmatched-lane"><summary><span>Unmatched evidence · relationship not inferred</span><span>'+(actions.length+results.length)+' items</span></summary>' +
    '<div style="padding:0 8px 8px">'+fullJsonBlock({{actions:actions, results:results}}, 'Complete unmatched actions/results', true)+'</div></details>';
}}
function renderActionCalls(step, interaction){{
  const sid = String(step.step_id);
  const calls = Array.isArray(interaction.calls) ? interaction.calls : [];
  if(!calls.length){{
    return '<section class="call-section"><div class="call-section-head"><span class="call-section-title">Action Calls</span><span class="call-count">0 calls</span></div><div class="muted">No action call recorded.</div>'+renderUnmatchedEvidence(interaction)+'</section>';
  }}
  return '<section class="call-section"><div class="call-section-head"><span class="call-section-title">Action Calls</span><span class="call-count">'+calls.length+' call'+(calls.length === 1 ? '' : 's')+'</span></div>' +
    '<div class="call-badges">'+calls.map(function(call){{ return renderCallBadge(call, sid); }}).join('')+'</div>' +
    '<div class="call-list">'+calls.map(function(call){{ return renderActionCall(call, sid); }}).join('')+renderUnmatchedEvidence(interaction)+'</div></section>';
}}
function renderEnvironmentObservation(interaction){{
  const rows = Array.isArray(interaction.environment_results) ? interaction.environment_results : [];
  if(!rows.length) return '';
  const values = rows.map(function(row){{ return row.result; }});
  const value = values.length === 1 ? values[0] : values;
  const text = fullEvidenceValue(value);
  const evidence = {{text:text, source:'Environment-only result, not paired with a tool action', format:'json'}};
  return '<details class="env-lane"><summary><span>Environment Observation</span><span>'+rows.length+' result'+(rows.length === 1 ? '' : 's')+' · '+text.length+' chars</span></summary>' +
    '<div style="padding:0 8px 8px">'+renderCodeEvidence('environment', 'Complete environment observation', '', evidence, text.length+' chars', true)+'</div></details>';
}}
function shortUrl(url){{
  try {{
    const u = new URL(String(url));
    const p = u.pathname || '';
    return u.host + (p.length > 24 ? (p.slice(0, 24) + '...') : p);
  }} catch (_e) {{
    return truncateText(url, 36);
  }}
}}
function extractThought(decision, events){{
  if(decision && typeof decision === 'object' && typeof decision.rationale === 'string' && decision.rationale.trim()) {{
    return decision.rationale.trim();
  }}
  const es = Array.isArray(events) ? events : [];
  for(let i = es.length - 1; i >= 0; i -= 1){{
    const p = es[i] && es[i].payload;
    if(!p || typeof p !== 'object') continue;
    if(String(p.stage || '') !== 'model_output') continue;
    const raw = String(p.raw_output || '');
    if(!raw) continue;
    const m = raw.match(/Thought\\s*:\\s*([\\s\\S]*?)(?:\\n(?:Action|Final|Observation|Critic|Plan)\\s*:|$)/i);
    if(m && m[1]) return m[1].trim();
    return raw;
  }}
  return '';
}}
function latestModelResponse(events){{
  const es = Array.isArray(events) ? events : [];
  for(let i = es.length - 1; i >= 0; i -= 1){{
    const p = es[i] && es[i].payload;
    if(!p || typeof p !== 'object') continue;
    if(String(p.stage || '') !== 'model_output') continue;
    const response = p.model_response;
    if(response && typeof response === 'object') return response;
  }}
  return null;
}}
function renderModelResponseSummary(response, step){{
  if(!response || typeof response !== 'object') return '';
  const rows = [];
  const st = (step && typeof step === 'object') ? step : {{}};
  if(response.provider) rows.push(kvRow('provider', response.provider));
  if(response.model_name) rows.push(kvRow('model', response.model_name));
  if(response.finish_reason) rows.push(kvRow('finish_reason', response.finish_reason));
  if(Array.isArray(response.tool_calls) && response.tool_calls.length) rows.push(kvRow('tool_calls', response.tool_calls.length));
  if(st.decision_source) rows.push(kvRow('decision_source', st.decision_source));
  if(st.native_tool_call_used !== undefined) rows.push(kvRow('native_tool_call_used', st.native_tool_call_used));
  if(st.native_tool_call_fallback_reason) rows.push(kvRow('native_fallback', st.native_tool_call_fallback_reason));
  const promptMeta = (st.prompt_metadata && typeof st.prompt_metadata === 'object') ? st.prompt_metadata : {{}};
  if(promptMeta.tool_schema_delivery) rows.push(kvRow('tool_delivery', promptMeta.tool_schema_delivery));
  const usage = response.usage;
  if(usage && typeof usage === 'object'){{
    if(usage.prompt_tokens !== undefined) rows.push(kvRow('prompt_tokens', usage.prompt_tokens));
    if(usage.completion_tokens !== undefined) rows.push(kvRow('completion_tokens', usage.completion_tokens));
    if(usage.total_tokens !== undefined) rows.push(kvRow('total_tokens', usage.total_tokens));
  }}
  return rows.length ? ('<div style="margin-top:8px">' + kvBlock(rows) + '</div>') : '';
}}
function firstActionLabel(actions){{
  if(!Array.isArray(actions) || !actions.length) return '';
  const a = actions[0] || {{}};
  const tool = a.tool || a.name || a.action || a.type || 'action';
  const args = (a.args && typeof a.args === 'object') ? a.args : (a.kwargs && typeof a.kwargs === 'object' ? a.kwargs : {{}});
  const pick = ['query','url','path','command','prompt','file'];
  const parts = [];
  for(const k of pick){{ if(k in args) parts.push(k + '=' + truncateText(args[k], 80)); }}
  if(!parts.length){{
    const ks = Object.keys(args);
    if(ks.length) parts.push(ks[0] + '=' + truncateText(args[ks[0]], 80));
  }}
  return parts.length ? (tool + '(' + parts.join(', ') + ')') : String(tool);
}}
function actionSummaryLabel(actions){{
  if(!Array.isArray(actions) || !actions.length) return '';
  if(actions.length === 1) return firstActionLabel(actions);
  const names = actions.map(function(action){{
    const item = action && typeof action === 'object' ? action : {{}};
    return String(item.tool || item.name || item.action || item.type || 'action');
  }});
  return actions.length + ' actions · ' + names.join(' + ');
}}
function flattenResults(input){{
  const out = [];
  function walk(x, d){{
    if(d > 3) return;
    if(Array.isArray(x)){{ for(const it of x) walk(it, d + 1); return; }}
    if(!x || typeof x !== 'object') return;
    if(Array.isArray(x.results)) out.push(...x.results);
    if(Array.isArray(x.items)) out.push(...x.items);
    if(Array.isArray(x.search_results)) out.push(...x.search_results);
    for(const k of Object.keys(x)) walk(x[k], d + 1);
  }}
  walk(input, 0);
  return out;
}}
function renderSearchTable(rows){{
  if(!rows.length) return '';
  let h = '<table style="width:100%;border-collapse:collapse;font-size:12px;background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius-md);overflow:hidden">';
  h += '<thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid var(--line);color:var(--muted)">Title</th><th style="text-align:left;padding:8px;border-bottom:1px solid var(--line);color:var(--muted)">URL</th></tr></thead><tbody>';
  for(const r of rows.slice(0, 8)){{
    h += '<tr><td style="padding:8px;border-bottom:1px solid var(--line)">'+esc(truncateText(r.title, 90))+'</td><td style="padding:8px;border-bottom:1px solid var(--line);color:var(--accent)">'+esc(shortUrl(r.url))+'</td></tr>';
  }}
  h += '</tbody></table>';
  return h;
}}
function cleanTerminalText(text){{
  const value = String(text || '');
  if(!value.trim()) return '';
  const prefixes = ['New Terminal Output:\\n', 'Current Terminal Screen:\\n'];
  for(const prefix of prefixes){{
    if(value.startsWith(prefix)) return value.slice(prefix.length).replace(/^\\n+/, '');
  }}
  return value;
}}
function extractTerminalObservation(item){{
  if(!item || typeof item !== 'object') return null;
  if(item.terminal && typeof item.terminal === 'object') return item.terminal;
  if(item.data && typeof item.data === 'object' && item.data.terminal && typeof item.data.terminal === 'object') return item.data.terminal;
  const env = item.env;
  if(!env || typeof env !== 'object') return null;
  const observation = env.observation;
  if(!observation || typeof observation !== 'object') return null;
  const data = observation.data;
  if(!data || typeof data !== 'object') return null;
  return (data.terminal && typeof data.terminal === 'object') ? data.terminal : null;
}}
function summarizeToolObservation(item){{
  if(!item || typeof item !== 'object') return {{kind: 'tool_result', title: 'Observation', body: String(item), raw: item}};
  const flat = flattenResults([item]);
  const rows = [];
  for(const it of flat){{
    if(!it || typeof it !== 'object') continue;
    const title = it.title || it.name || '';
    const url = it.url || it.link || it.href || '';
    if(title && url) rows.push({{title:String(title), url:String(url)}});
  }}
  if(rows.length) return {{kind: 'search_results', title: 'Search Results', table: renderSearchTable(rows), raw: item}};
  if('error' in item && item.error) return {{kind: 'error', title: String(item.error), body: String(item.content || ''), raw: item}};
  return {{
    kind: 'tool_result',
    title: String(item.title || item.name || item.status || 'Tool Observation'),
    body: JSON.stringify(item, null, 2),
    raw: item,
  }};
}}
function pickObservation(actionResults){{
  const ars = Array.isArray(actionResults) ? actionResults : [];
  if(!ars.length) return null;
  let terminalOutput = null;
  let terminalScreen = null;
  let toolError = null;
  let toolResult = null;
  for(const item of ars){{
    const terminal = extractTerminalObservation(item);
    if(terminal){{
      const output = cleanTerminalText(terminal.output);
      const screen = cleanTerminalText(terminal.screen);
      if(output && !terminalOutput) terminalOutput = {{kind: 'terminal_output', title: 'Terminal Output', body: output, raw: terminal}};
      else if(!output && screen && !terminalScreen) terminalScreen = {{kind: 'terminal_screen', title: 'Terminal Screen', body: screen, raw: terminal}};
      continue;
    }}
    const summary = summarizeToolObservation(item);
    if(!summary) continue;
    if(summary.kind === 'error' && !toolError) toolError = summary;
    else if(!toolResult) toolResult = summary;
  }}
  const primary = terminalOutput || terminalScreen || toolError || toolResult;
  if(!primary) return null;
  let secondary = null;
  if(String(primary.kind || '').startsWith('terminal_')){{
    secondary = toolError || toolResult;
  }} else {{
    secondary = terminalOutput || terminalScreen;
  }}
  return {{
    primary,
    secondary,
    primary_kind: String(primary.kind || 'tool_result'),
  }};
}}
function renderObservationBlock(summary, label){{
  if(!summary || typeof summary !== 'object') return '';
  const title = summary.title ? ('<div style="font-weight:600;margin-bottom:6px">' + esc(String(label || summary.title)) + ' · ' + esc(String(summary.title)) + '</div>') : '';
  if(summary.table) return '<div style="margin-bottom:12px">' + title + summary.table + '</div>';
  const body = String(summary.body || summary.title || '');
  if(summary.kind === 'error') return '<div style="margin-bottom:12px;color:var(--err)">' + title + '<div class="summary-line">' + esc(firstLine(body || 'Error')) + '</div>' + fullTextBlock(body, 'full error / observation', false) + '</div>';
  return '<div style="margin-bottom:12px">' + title + '<div class="summary-line">' + esc(firstLine(body)) + '</div>' + fullTextBlock(body, 'full observation', false) + '</div>';
}}
function renderState(obs){{
  if(!obs || typeof obs !== 'object') return '<div class="muted">No state.</div>';
  const observeOut = (obs.observe_output && typeof obs.observe_output === 'object') ? obs.observe_output : {{}};
  const context = (obs.context && typeof obs.context === 'object') ? obs.context : {{}};
  const parts = [];
  if(context.input_tokens_total !== undefined) parts.push(kvRow('ctx_used', context.input_tokens_total));
  if(context.occupancy_ratio !== undefined) parts.push(kvRow('ctx_pct', ((Number(context.occupancy_ratio) || 0) * 100).toFixed(1) + '%'));
  if(context.history_tokens !== undefined) parts.push(kvRow('hist_toks', context.history_tokens));
  if(context.output_tokens !== undefined) parts.push(kvRow('out_toks', context.output_tokens));
  const keys = Object.keys(observeOut);
  for(const k of keys.slice(0, 12)){{
    if(['run_id','latency_ms','error_category','ts','step_id','phase'].includes(k)) continue;
    const v = observeOut[k];
    if(typeof v === 'object') continue;
    parts.push(kvRow(k, v));
  }}
  if(parts.length) return kvBlock(parts);
  return '<div class="muted">No scalar state fields.</div>';
}}
function renderDirectObservation(actionResults){{
  const ars = Array.isArray(actionResults) ? actionResults : [];
  if(!ars.length) return '<div class="muted">No direct observation from action.</div>';
  // Check for delegate/fanout structured results
  for(const item of ars){{
    if(!item || typeof item !== 'object') continue;
    // Delegate result
    if(item.handoff === true || (item.status && item.agent_name)){{
      const rows = [];
      if(item.agent_name) rows.push(kvRow('agent', item.agent_name));
      if(item.status) rows.push(kvRow('status', item.status));
      if(item.final_result) rows.push(kvRow('result', truncateText(String(item.final_result), 300)));
      if(item.stop_reason) rows.push(kvRow('stop_reason', item.stop_reason));
      if(item.steps) rows.push(kvRow('steps', item.steps));
      return '<div style="margin-bottom:12px"><div style="font-weight:600;margin-bottom:6px;color:var(--kind-delegation)">↗ Delegate Result</div>' + (rows.length ? kvBlock(rows) : '<div class="muted">No details.</div>') + '</div>';
    }}
    // Fanout result
    if(item.succeeded !== undefined && (item.failed !== undefined || item.partial !== undefined)){{
      const ok = Number(item.succeeded) || 0;
      const fail = Number(item.failed) || 0;
      const partial = Number(item.partial) || 0;
      const rows = [
        kvRow('succeeded', '<span style="color:var(--ok)">' + ok + '</span>'),
        kvRow('failed', '<span style="color:var(--err)">' + fail + '</span>'),
      ];
      if(partial) rows.push(kvRow('partial', '<span style="color:var(--warn)">' + partial + '</span>'));
      if(Array.isArray(item.results)){{
        const taskRows = item.results.slice(0, 5).map(function(r, i){{
          if(!r || typeof r !== 'object') return kvRow('task ' + i, truncateText(JSON.stringify(r), 100));
          return kvRow('task ' + i, (r.status || 'done') + (r.agent_name ? ' (' + r.agent_name + ')' : '') + (r.final_result ? ': ' + truncateText(String(r.final_result), 80) : ''));
        }});
        rows.push(...taskRows);
      }}
      return '<div style="margin-bottom:12px"><div style="font-weight:600;margin-bottom:6px;color:var(--kind-fanout)">⊛ FanOut Result</div>' + kvBlock(rows) + '</div>';
    }}
  }}
  const picked = pickObservation(actionResults);
  if(!picked) return '<div class="muted">No direct observation from action.</div>';
  const blocks = [];
  blocks.push(renderObservationBlock(picked.primary, picked.primary_kind.startsWith('terminal_') ? 'Terminal Observation' : 'Direct Observation'));
  if(picked.secondary) blocks.push(renderObservationBlock(picked.secondary, 'Tool Observation'));
  return blocks.join('');
}}
function assetHref(path){{
  if(!path) return '';
  if(embedded) return '';
  return '/asset?path=' + encodeURIComponent(String(path));
}}
function renderVisualOverlay(item){{
  if(!item || typeof item !== 'object') return '';
  const parts = [];
  const grounding = (item.grounding_metadata && typeof item.grounding_metadata === 'object') ? item.grounding_metadata : {{}};
  const boxes = Array.isArray(grounding.boxes) ? grounding.boxes : [];
  for(const box of boxes.slice(0,6)){{
    if(!box || typeof box !== 'object') continue;
    const x = Number(box.x !== undefined ? box.x : (Array.isArray(box.bounds) ? box.bounds[0] : 0));
    const y = Number(box.y !== undefined ? box.y : (Array.isArray(box.bounds) ? box.bounds[1] : 0));
    const w = Number(box.width !== undefined ? box.width : (Array.isArray(box.bounds) ? (box.bounds[2] - box.bounds[0]) : 0));
    const h = Number(box.height !== undefined ? box.height : (Array.isArray(box.bounds) ? (box.bounds[3] - box.bounds[1]) : 0));
    if(!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(w) || !Number.isFinite(h)) continue;
    parts.push('<div class="vbox" style="left:' + x + 'px;top:' + y + 'px;width:' + w + 'px;height:' + h + 'px"></div>');
  }}
  const actionLabel = String(item.action_label || '');
  const matched = actionLabel.match(/\\b(click|move_to|double_click|right_click|drag_to)\\((.*?)\\)/);
  if(matched){{
    const step = Array.isArray(payload.steps) ? payload.steps.find(function(st){{ return String(st.step_id) === String(item.step_id); }}) : null;
    const actions = step && Array.isArray(step.actions) ? step.actions : [];
    if(actions.length){{
      const args = (actions[0] && typeof actions[0] === 'object' && typeof actions[0].args === 'object') ? actions[0].args : {{}};
      const x = Number(args.x);
      const y = Number(args.y);
      if(Number.isFinite(x) && Number.isFinite(y)){{
        parts.push('<div class="vdot" style="left:' + x + 'px;top:' + y + 'px"></div>');
      }}
    }}
  }}
  return parts.length ? ('<div class="voverlay">' + parts.join('') + '</div>') : '';
}}
function buildScreenshotStrip(){{
  const strip = document.getElementById('screenshotStrip');
  const section = document.getElementById('screenshotStripSection');
  if(!strip || !section) return;
  const rows = Array.isArray(payload.visual_timeline) ? payload.visual_timeline : [];
  const meaningful = rows.filter(function(item){{
    const shot = (item && typeof item === 'object') ? item.screenshot : null;
    const path = shot && typeof shot === 'object' ? String(shot.path || '') : '';
    return path || Number((item && item.visual_asset_count) || 0) > 0;
  }});
  if(!meaningful.length || embedded){{ section.style.display = 'none'; return; }}
  section.style.display = '';
  strip.innerHTML = '';
  for(const item of meaningful){{
    const shot = (item && typeof item === 'object') ? item.screenshot : null;
    const path = shot && typeof shot === 'object' ? String(shot.path || '') : '';
    const hasRetry = (item.critic_retry_count || 0) > 0;
    const groundOk = item.grounding_present;
    const div = document.createElement('div');
    div.style.cssText = 'flex:0 0 80px;cursor:pointer;text-align:center;border:1px solid var(--line);border-radius:var(--radius-md);overflow:hidden;position:relative;';
    if(path){{
      div.innerHTML = '<img src="' + esc(assetHref(path)) + '" style="width:80px;height:45px;object-fit:cover;display:block" alt="step '+esc(String(item.step_id))+'"/><div style="font-size:9px;padding:2px 4px;background:var(--surface-2);color:var(--muted)">S'+esc(String(item.step_id))+'</div>' + (hasRetry ? '<div style="position:absolute;top:2px;right:2px;width:8px;height:8px;border-radius:50%;background:var(--err)"></div>' : '') + (groundOk === false ? '<div style="position:absolute;top:2px;left:2px;width:8px;height:8px;border-radius:50%;background:var(--warn)"></div>' : '');
    }} else {{
      div.innerHTML = '<div style="width:80px;height:45px;background:var(--surface-2);display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:10px">S'+esc(String(item.step_id))+'</div><div style="font-size:9px;padding:2px 4px;background:var(--surface-2);color:var(--muted)">S'+esc(String(item.step_id))+'</div>';
    }}
    const sid = String(item.step_id);
    div.addEventListener('click', function(){{
      const card = document.getElementById('step-'+sid);
      if(card) card.scrollIntoView({{behavior:'smooth',block:'center'}});
    }});
    strip.appendChild(div);
  }}
}}
function buildVisualTimeline(items){{
  const rows = Array.isArray(payload.visual_timeline) ? payload.visual_timeline : [];
  const meaningful = rows.filter(function(item){{
    const shot = (item && typeof item === 'object') ? item.screenshot : null;
    const path = shot && typeof shot === 'object' ? String(shot.path || '') : '';
    return item && (path || Number(item.visual_asset_count || 0) > 0);
  }});
  const section = document.getElementById('visualTimelineSection');
  if(!meaningful.length){{
    if(section) section.style.display = 'none';
    visualTimelineRoot.innerHTML = '<div class="muted">No screenshot timeline recorded.</div>';
    return;
  }}
  if(section) section.style.display = '';
  const cards = [];
  for(const item of meaningful){{
    const shot = (item && typeof item === 'object') ? item.screenshot : null;
    const path = shot && typeof shot === 'object' ? String(shot.path || '') : '';
    let preview = '<div class="muted">No screenshot</div>';
    if(path && !embedded){{
      preview = '<div class="vthumb"><img src="' + esc(assetHref(path)) + '" alt="screenshot step ' + esc(String(item.step_id)) + '"/>' + renderVisualOverlay(item) + '</div>';
    }}
    cards.push(
      '<div class="vcard">' +
      '<div style="font-size:11px;color:var(--muted);margin-bottom:6px">STEP ' + esc(String(item.step_id)) + '</div>' +
      preview +
      kvBlock([
        kvRow('action', item.action_label || '-'),
        kvRow('grounding', item.grounding_present ? 'yes' : 'no'),
        kvRow('critic retries', item.critic_retry_count || 0),
        kvRow('visual assets', item.visual_asset_count || 0),
      ]) +
      '</div>'
    );
  }}
  visualTimelineRoot.innerHTML = '<div class="vtimeline">' + cards.join('') + '</div>';
}}
function renderActionOverlay(step){{
  const st = (step && typeof step === 'object') ? step : {{}};
  const actions = Array.isArray(st.actions) ? st.actions : [];
  const retryCount = Array.isArray(st.critic_outputs) ? st.critic_outputs.filter(function(x){{ return x && typeof x === 'object' && x.action === 'retry'; }}).length : 0;
  let parts = [];
  // Grounding failure banner
  const criticOutputs = Array.isArray(st.critic_outputs) ? st.critic_outputs : [];
  for(const co of criticOutputs){{
    if(co && typeof co === 'object' && co.action === 'retry'){{
      const reason = String(co.reason || '').toLowerCase();
      if(reason.includes('grounding') || reason.includes('element not found') || reason.includes('coordinates') || reason.includes('out of bounds')){{
        parts.push('<div style="padding:6px 10px;margin:4px 0;border-radius:var(--radius-md);background:var(--err-soft);border:2px solid var(--err);color:var(--err);font-size:11px;font-weight:600">Grounding failure: '+esc(String(co.reason || ''))+'</div>');
        break;
      }}
    }}
  }}
  if(!actions.length && !parts.length) return '';
  for(const a of actions){{
    if(!a || typeof a !== 'object') continue;
    const at = String(a.action_type || a.name || '');
    const args = a.args || a;
    if((at === 'click' || at === 'move_to' || at === 'right_click' || at === 'double_click') && args.x !== undefined && args.y !== undefined){{
      const color = retryCount > 0 ? 'var(--err)' : 'var(--ok)';
      parts.push('<div style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;margin:2px;border-radius:var(--radius-pill);font-size:10px;background:var(--surface-2);border:1px solid '+color+';color:'+color+'"><span style="width:6px;height:6px;border-radius:50%;background:'+color+'"></span>'+esc(at)+' ('+esc(String(args.x))+','+esc(String(args.y))+')</div>');
    }} else if(at === 'type_text'){{
      const text = String(args.text || '').slice(0,40);
      parts.push('<div style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;margin:2px;border-radius:var(--radius-pill);font-size:10px;background:var(--surface-2);border:1px solid var(--line);color:var(--txt)">type "'+esc(text)+'"</div>');
    }} else if(at === 'navigate'){{
      const url = String(args.url || '').slice(0,60);
      parts.push('<div style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;margin:2px;border-radius:var(--radius-pill);font-size:10px;background:var(--surface-2);border:1px solid var(--accent);color:var(--accent)">&#x2192; '+esc(url)+'</div>');
    }} else if(at === 'scroll'){{
      parts.push('<div style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;margin:2px;border-radius:var(--radius-pill);font-size:10px;background:var(--surface-2);border:1px solid var(--line);color:var(--muted)">&#x2195; scroll</div>');
    }}
  }}
  return parts.length ? '<div style="margin:4px 0">'+parts.join('')+'</div>' : '';
}}
function renderVisualAssets(step){{
  const st = (step && typeof step === 'object') ? step : {{}};
  const assets = Array.isArray(st.visual_assets) ? st.visual_assets : [];
  const modalities = Array.isArray(st.observation_modalities) ? st.observation_modalities : [];
  const inputModalities = Array.isArray(st.model_input_modalities) ? st.model_input_modalities : [];
  const headerRows = [];
  if(modalities.length) headerRows.push(kvRow('observation modalities', modalities.join(', ')));
  if(inputModalities.length) headerRows.push(kvRow('model input modalities', inputModalities.join(', ')));
  if(st.model_input_visual_count !== undefined) headerRows.push(kvRow('model input images', st.model_input_visual_count));
  if(st.visual_asset_count !== undefined) headerRows.push(kvRow('visual assets', st.visual_asset_count));
  const multimodal = (((st.observation || {{}}).env || {{}}).observation || {{}}).data || {{}};
  const grounding = multimodal.multimodal && multimodal.multimodal.grounding_metadata;
  headerRows.push(kvRow('grounding metadata', grounding ? 'present' : 'none'));
  const retryCount = Array.isArray(st.critic_outputs) ? st.critic_outputs.filter(function(x){{ return x && typeof x === 'object' && x.action === 'retry'; }}).length : 0;
  headerRows.push(kvRow('critic retries', retryCount));
  const label = actionSummaryLabel(st.actions || []);
  if(label) headerRows.push(kvRow('action taken', label));
  let htmlBlocks = headerRows.length ? kvBlock(headerRows) : '';
  // Action overlay markers
  htmlBlocks += renderActionOverlay(st);
  // Observation pack viewer toggle
  const obsPack = multimodal.multimodal || {{}};
  const hasObsData = obsPack.text || obsPack.dom || obsPack.accessibility_tree || (Array.isArray(obsPack.ocr) && obsPack.ocr.length) || (Array.isArray(obsPack.ui_candidates) && obsPack.ui_candidates.length) || obsPack.grounding_metadata;
  if(hasObsData){{
    const sid = String(st.step_id || 0);
    htmlBlocks += '<button class="btn" style="margin:6px 0;font-size:11px" onclick="var el=document.getElementById(\\'obspack-'+sid+'\\');if(el){{el.style.display=el.style.display===\\'none\\'?\\'block\\':\\'none\\';}}">observation pack</button>';
    htmlBlocks += '<div id="obspack-'+sid+'" style="display:none;margin-top:4px;border:1px dashed var(--line-strong);border-radius:var(--radius-md);padding:8px;background:var(--surface-1)">';
    if(obsPack.text) htmlBlocks += fullTextBlock(String(obsPack.text), 'full observation text', false);
    if(obsPack.dom){{
      const domStr = typeof obsPack.dom === 'string' ? obsPack.dom : JSON.stringify(obsPack.dom, null, 2);
      htmlBlocks += '<details style="margin:4px 0"><summary style="cursor:pointer;font-size:12px;color:var(--muted)">DOM</summary><pre style="font-size:10px;max-height:300px;overflow:auto;white-space:pre-wrap">'+esc(domStr)+'</pre></details>';
    }}
    if(obsPack.accessibility_tree){{
      const a11yStr = typeof obsPack.accessibility_tree === 'string' ? obsPack.accessibility_tree : JSON.stringify(obsPack.accessibility_tree, null, 2);
      htmlBlocks += '<details style="margin:4px 0"><summary style="cursor:pointer;font-size:12px;color:var(--muted)">a11y tree</summary><pre style="font-size:10px;max-height:300px;overflow:auto;white-space:pre-wrap">'+esc(a11yStr)+'</pre></details>';
    }}
    if(Array.isArray(obsPack.ocr) && obsPack.ocr.length){{
      let ocrRows = '';
      for(const span of obsPack.ocr.slice(0,20)){{
        if(!span || typeof span !== 'object') continue;
        ocrRows += '<tr><td style="font-size:10px;padding:2px 6px">'+esc(String(span.text||''))+'</td><td style="font-size:10px;padding:2px 6px">'+esc(JSON.stringify(span.x!==undefined?{{x:span.x,y:span.y,w:span.w,h:span.h}}:span))+'</td></tr>';
      }}
      htmlBlocks += '<details style="margin:4px 0"><summary style="cursor:pointer;font-size:12px;color:var(--muted)">OCR ('+obsPack.ocr.length+')</summary><table style="font-size:10px;border-collapse:collapse">'+ocrRows+'</table></details>';
    }}
    if(Array.isArray(obsPack.ui_candidates) && obsPack.ui_candidates.length){{
      let uiRows = '';
      for(const c of obsPack.ui_candidates.slice(0,20)){{
        if(!c || typeof c !== 'object') continue;
        uiRows += '<tr><td style="font-size:10px;padding:2px 6px">'+esc(String(c.type||''))+'</td><td style="font-size:10px;padding:2px 6px">'+esc(String(c.text||''))+'</td></tr>';
      }}
      htmlBlocks += '<details style="margin:4px 0"><summary style="cursor:pointer;font-size:12px;color:var(--muted)">UI candidates ('+obsPack.ui_candidates.length+')</summary><table style="font-size:10px;border-collapse:collapse">'+uiRows+'</table></details>';
    }}
    if(obsPack.grounding_metadata){{
      const gm = obsPack.grounding_metadata;
      const boxes = Array.isArray(gm.boxes) ? gm.boxes : [];
      const spans = Array.isArray(gm.ocr_spans) ? gm.ocr_spans : [];
      htmlBlocks += kvBlock([kvRow('grounding boxes', boxes.length), kvRow('OCR spans', spans.length)]);
    }}
    htmlBlocks += fullJsonBlock(obsPack, 'full observation pack JSON', false);
    htmlBlocks += '</div>';
  }}
  if(!assets.length){{
    return htmlBlocks || '<div class="muted">No visual assets recorded.</div>';
  }}
  const cards = [];
  for(const asset of assets){{
    if(!asset || typeof asset !== 'object') continue;
    const path = asset.path || '';
    const mime = String(asset.mime_type || '');
    const imageLike = mime.startsWith('image/');
    let preview = '';
    if(imageLike && !embedded && path){{
      const timelineItem = (Array.isArray(payload.visual_timeline) ? payload.visual_timeline.find(function(it){{ return String(it.step_id) === String(st.step_id); }}) : null) || {{}};
      preview = '<div class="vthumb" style="margin-top:8px;position:relative"><img src="' + esc(assetHref(path)) + '" alt="visual asset"/>' + renderVisualOverlay(timelineItem) + '</div>';
    }} else if(path) {{
      preview = '<div style="margin-top:8px"><pre>' + esc(String(path)) + '</pre></div>';
    }}
    cards.push(
      '<div class="item">' +
      kvBlock([
        kvRow('kind', asset.kind || '-'),
        kvRow('path', path || '-'),
        kvRow('mime_type', mime || '-'),
        kvRow('size', ((asset.width || '-') + ' × ' + (asset.height || '-'))),
        kvRow('source_step', asset.source_step !== undefined ? asset.source_step : '-'),
      ]) +
      preview +
      '</div>'
    );
  }}
  return htmlBlocks + '<div class="list" style="margin-top:8px">' + cards.join('') + '</div>';
}}
function renderMemoryUpdate(observeOut){{
  const mem = observeOut && typeof observeOut === 'object' ? observeOut.memory : null;
  if(!mem || typeof mem !== 'object') return '<div class="muted">No memory update.</div>';
  const rows = [];
  if('enabled' in mem) rows.push(kvRow('enabled', mem.enabled));
  if(Array.isArray(mem.records)) rows.push(kvRow('records', mem.records.length));
  const body = typeof mem.summary === 'string' && mem.summary.trim() ? fullTextBlock(mem.summary, 'full memory summary', false) : '';
  return rows.length ? kvBlock(rows) + body : '<div class="muted">No memory update.</div>';
}}
function renderParserDiagnostics(diag){{
  if(!diag || typeof diag !== 'object' || !Object.keys(diag).length) return '<div class="muted">No parser diagnostics.</div>';
  const rows = [];
  if(diag.protocol) rows.push(kvRow('protocol', diag.protocol));
  if(diag.parser) rows.push(kvRow('parser', diag.parser));
  if(diag.selected_parser) rows.push(kvRow('selected_parser', diag.selected_parser));
  if(diag.fallback_used !== undefined) rows.push(kvRow('fallback_used', diag.fallback_used));
  if(diag.contract) rows.push(kvRow('contract', diag.contract));
  if(diag.code) rows.push(kvRow('code', diag.code));
  if(diag.severity) rows.push(kvRow('severity', diag.severity));
  if(diag.extraction_mode) rows.push(kvRow('extraction', diag.extraction_mode));
  if(diag.summary) rows.push(kvRow('summary', diag.summary));
  if(diag.details) rows.push(kvRow('details', firstLine(diag.details, 180)));
  if(diag.expected_shape) rows.push(kvRow('expected', firstLine(diag.expected_shape, 180)));
  if(diag.repair_instruction) rows.push(kvRow('repair', firstLine(diag.repair_instruction, 180)));
  if(diag.salvage_summary) rows.push(kvRow('salvage', firstLine(diag.salvage_summary, 180)));
  if(diag.raw_output_preview) rows.push(kvRow('raw_preview', firstLine(diag.raw_output_preview, 180)));
  return kvBlock(rows) + fullJsonBlock(diag, 'full parser diagnostics', false);
}}
function renderCritic(data){{
  if(!Array.isArray(data) || !data.length) return '<div class="muted">No critic outputs.</div>';
  const cards = [];
  for(let i = 0; i < data.length; i++){{
    const c = data[i];
    if(!c || typeof c !== 'object') continue;
    const actionColors = {{continue:'var(--ok)', stop:'var(--err)', retry:'var(--warn)'}};
    const actionColor = actionColors[c.action] || 'var(--subtle)';
    const rows = [];
    if('action' in c) rows.push('<div style="display:flex;align-items:center;gap:6px"><span style="background:'+actionColor+';color:var(--surface-1);border-radius:4px;padding:1px 6px;font-size:11px;font-weight:600">'+esc(c.action)+'</span><span style="color:var(--subtle);font-size:11px">critic #'+(i+1)+'</span></div>');
    if('reason' in c) rows.push(kvRow('reason', firstLine(c.reason, 180)));
    if(typeof c.score === 'number'){{
      const pct = Math.max(0, Math.min(100, Math.round(c.score * 100)));
      const barColor = pct >= 70 ? 'var(--ok)' : pct >= 40 ? 'var(--warn)' : 'var(--err)';
      rows.push('<div style="display:flex;align-items:center;gap:8px"><span style="color:var(--subtle);min-width:44px;font-size:12px">score</span><div style="flex:1;background:var(--surface-3);border-radius:3px;height:10px;max-width:120px"><div style="width:'+pct+'%;background:'+barColor+';border-radius:3px;height:10px"></div></div><span style="color:var(--muted);font-size:12px">'+c.score.toFixed(2)+'</span></div>');
    }}
    if(c.modified_prompt) rows.push(kvRow('modified_prompt', '<span style="color:var(--warn)">✎ prompt modified</span>'));
    if(c.instruction_patch) rows.push(kvRow('instruction_patch', firstLine(c.instruction_patch, 180)));
    if(c.state_patch && typeof c.state_patch === 'object') rows.push(kvRow('state_patch', firstLine(JSON.stringify(c.state_patch), 180)));
    if(c.details) rows.push(kvRow('details', firstLine(typeof c.details === 'string' ? c.details : JSON.stringify(c.details), 180)));
    cards.push('<div style="border:1px solid var(--line);border-radius:6px;padding:8px;margin-bottom:4px;background:var(--surface-1)">'+(rows.length ? rows.join('') : kvRow('critic', JSON.stringify(c)))+fullJsonBlock(c, 'full critic output', false)+'</div>');
  }}
  return cards.length ? cards.join('') : '<div class="muted">No critic outputs.</div>';
}}
function renderEvents(events){{
  if(!Array.isArray(events) || !events.length) return '<div class="muted">No events.</div>';
  const items = [];
  for(const e of events){{
    const rows = [
      kvRow('phase', e.phase || ''),
      kvRow('ok', e.ok),
      kvRow('error', e.error || ''),
    ];
    const payload = e.payload && typeof e.payload === 'object' ? e.payload : null;
    if(payload && String(payload.stage || '') === 'model_output'){{
      const response = payload.model_response;
      if(response && typeof response === 'object'){{
        if(response.finish_reason) rows.push(kvRow('finish_reason', response.finish_reason));
        if(Array.isArray(response.tool_calls) && response.tool_calls.length) rows.push(kvRow('tool_calls', response.tool_calls.length));
        const usage = response.usage;
        if(usage && typeof usage === 'object' && usage.total_tokens !== undefined) rows.push(kvRow('total_tokens', usage.total_tokens));
      }}
    }}
    items.push('<div class="item">' + kvBlock(rows) + '</div>');
  }}
  return '<div class="list">' + items.join('') + '</div>';
}}
function sectionHtml(title, bodyHtml, rawData, key, collapsed){{
  const txt = esc(JSON.stringify(rawData, null, 2));
  const isCollapsed = !!collapsed;
  const display = isCollapsed ? 'none' : 'block';
  const btn = isCollapsed ? 'expand' : 'collapse';
  const tree = '<details class="tree-wrap"><summary class="muted">Structured View</summary>' + renderTree(rawData) + '</details>';
  return '<section data-key="' + key + '" style="display:' + display + '">' +
    '<h4>' + title + ' <button class="sbtn tgl" data-key="' + key + '" type="button">' + btn + '</button></h4>' +
    bodyHtml +
    tree +
    '<details class="raw"><summary class="muted">Raw JSON</summary><pre>' + txt + '</pre></details></section>';
}}
function applyFontScale(){{
  if(!Number.isFinite(fontScale)) fontScale = 1.1;
  fontScale = Math.max(0.8, Math.min(2.0, fontScale));
  document.body.style.zoom = String(fontScale);
  localStorage.setItem('qita_view_font_scale', String(fontScale));
}}
function applyTab(){{
  const traj = activeTab === 'traj';
  tabTraj.classList.toggle('active', traj);
  tabManifest.classList.toggle('active', !traj);
  panelTraj.classList.toggle('active', traj);
  panelManifest.classList.toggle('active', !traj);
  localStorage.setItem('qita_view_tab', activeTab);
}}
function typeName(v){{
  if(v === null) return 'null';
  if(Array.isArray(v)) return 'array';
  return typeof v;
}}
function treeLeaf(key, val){{
  return '<div class="tree-leaf"><div class="tree-key">'+esc(key)+'</div><div class="tree-val">'+esc(toPreview(val))+'</div></div>';
}}
function treeNode(key, val, depth){{
  const t = typeName(val);
  if(t !== 'object' && t !== 'array') return treeLeaf(key, val);
  const open = depth < 2 ? ' open' : '';
  if(t === 'array'){{
    const n = val.length;
    const lim = Math.min(n, 80);
    let inner = '';
    for(let i=0;i<lim;i+=1) inner += treeNode('[' + i + ']', val[i], depth + 1);
    if(n > lim) inner += treeLeaf('...', '+' + (n - lim) + ' more items');
    return '<details'+open+'><summary>'+esc(key)+' <span class="muted">array(' + n + ')</span></summary><div class="tree-children">' + inner + '</div></details>';
  }}
  const ks = Object.keys(val);
  const lim = Math.min(ks.length, 80);
  let inner = '';
  for(let i=0;i<lim;i+=1){{ const k = ks[i]; inner += treeNode(k, val[k], depth + 1); }}
  if(ks.length > lim) inner += treeLeaf('...', '+' + (ks.length - lim) + ' more keys');
  return '<details'+open+'><summary>'+esc(key)+' <span class="muted">object(' + ks.length + ')</span></summary><div class="tree-children">' + inner + '</div></details>';
}}
function renderTree(data){{
  return '<div class="tree">' + treeNode('value', data, 0) + '</div>';
}}
function parseTs(ts){{
  const v = Date.parse(String(ts||''));
  return Number.isNaN(v) ? null : v;
}}
function agentColor(agentId){{
  if(!agentId) return '#6b8fc4';
  let hash = 0;
  for(let i = 0; i < agentId.length; i++) hash = agentId.charCodeAt(i) + ((hash << 5) - hash);
  const colors = ['#6b8fc4','#bfa04e','#2da46a','#9b7fd4','#c47070','#3da89c','#c47070','#5a8fbf'];
  return colors[Math.abs(hash) % colors.length];
}}
function phaseColor(phase){{
  const p = String(phase||'').toLowerCase();
  if(p.includes('handoff')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-handoff').trim();
  if(p.includes('delegate')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-delegation').trim();
  if(p.includes('fanout')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-fanout').trim();
  if(p.includes('state') || p.includes('observe')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-observation').trim();
  if(p.includes('decide') || p.includes('model')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-thinking').trim();
  if(p.includes('action') || p.includes('tool')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-action').trim();
  if(p.includes('critic') || p.includes('reflect')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-critic').trim();
  if(p.includes('memory')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-memory').trim();
  if(p.includes('done') || p.includes('stop')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-done').trim();
  return getComputedStyle(document.documentElement).getPropertyValue('--kind-other').trim();
}}
function phaseFromEvents(events){{
  for(let i=(events || []).length - 1; i >= 0; i -= 1){{
    const phase = String((events[i] && events[i].phase) || '').trim();
    if(phase) return phase;
  }}
  return '';
}}
function inferPrimaryKind(events){{
  const es = Array.isArray(events) ? events : [];
  for(let i = es.length - 1; i >= 0; i -= 1){{
    const p = String(es[i] && es[i].phase || '').toLowerCase();
    if(p.includes('fanout')) return 'fanout';
    if(p.includes('handoff')) return 'handoff';
    if(p.includes('delegate')) return 'delegation';
    if(p.includes('critic')) return 'critic';
    if(p.includes('act') || p.includes('tool')) return 'action';
    if(p.includes('state') || p.includes('observe')) return 'observation';
    if(p.includes('decide') || p.includes('model')) return 'thinking';
  }}
  return 'other';
}}
function compactStageColor(stage){{
  const s = String(stage || '').toLowerCase();
  if(s.includes('summary')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-memory').trim();
  if(s.includes('microcompact')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-observation').trim();
  if(s.includes('warning')) return getComputedStyle(document.documentElement).getPropertyValue('--kind-critic').trim();
  if(s.includes('overflow')) return getComputedStyle(document.documentElement).getPropertyValue('--err').trim();
  return getComputedStyle(document.documentElement).getPropertyValue('--kind-other').trim();
}}
function compactStageLabel(stage){{
  const s = String(stage || '').toLowerCase();
  if(s === 'summary_compact_applied') return 'summary compact';
  if(s === 'microcompact_applied') return 'micro compact';
  if(s === 'warning') return 'warning';
  if(s === 'context_overflow') return 'overflow';
  if(s === 'compact_skipped') return 'compact skipped';
  if(s === 'within_budget') return 'within budget';
  return stage || 'context';
}}
function compactEventText(event){{
  if(!event || typeof event !== 'object') return '';
  const bits = [compactStageLabel(event.stage)];
  if(event.before_tokens !== undefined && event.after_tokens !== undefined){{
    bits.push(String(event.before_tokens) + ' → ' + String(event.after_tokens));
  }}
  if(event.saved_tokens !== undefined && Number(event.saved_tokens)){{
    bits.push('saved ' + String(event.saved_tokens));
  }}
  return bits.join(' · ');
}}
function riskChip(flag){{
  const f = String(flag || '');
  const cls = (f.includes('error') || f.includes('stop') || f.includes('failure')) ? 'error' : (f.includes('retry') || f.includes('context') || f.includes('submission')) ? 'warn' : 'ok';
  return '<span class="risk-chip '+cls+'">' + esc(f.replaceAll('_', ' ')) + '</span>';
}}
function paintRunSummary(){{
  if(!runSummary) return;
  const insights = payload.insights || {{}};
  const flags = Array.isArray(insights.risk_flags) ? insights.risk_flags : [];
  const nextStep = runFocus.next_actionable_step !== undefined && runFocus.next_actionable_step !== null ? runFocus.next_actionable_step : insights.next_inspect_step;
  const chips = flags.length ? flags.map(riskChip).join('') : '<span class="risk-chip ok">no risk flags</span>';
  const pocBits = [
    'attempts ' + esc(String(cybergymFocus.poc_attempts || 0)),
    'status ' + esc(String(cybergymFocus.last_verification_status || 'not recorded')),
    cybergymFocus.last_poc_path ? ('poc ' + esc(String(cybergymFocus.last_poc_path))) : '',
  ].filter(Boolean).join(' · ');
  runSummary.innerHTML =
    '<div class="summary-panel"><div class="summary-title">Outcome</div><div class="summary-value">' + esc(String(runFocus.outcome || insights.outcome || 'needs_review')) + '</div><div class="summary-line">stop=' + esc(String(insights.stop_reason || 'not recorded')) + '</div></div>' +
    '<div class="summary-panel primary"><div class="summary-title">Primary Failure</div><div class="summary-value">' + esc(String(runFocus.primary_failure || insights.likely_failure || 'No explicit failure signal recorded.')) + '</div></div>' +
    '<div class="summary-panel"><div class="summary-title">Next Inspect</div><div class="summary-value">' + (nextStep !== null && nextStep !== undefined ? 'Step ' + esc(String(nextStep)) : 'not recorded') + '</div><div class="summary-actions">' + (nextStep !== null && nextStep !== undefined ? '<button class="btn" type="button" onclick="selectStep(\\'' + esc(String(nextStep)) + '\\', true)">Open evidence</button>' : '') + '</div></div>' +
    '<div class="summary-panel"><div class="summary-title">CyberGym / Risk</div><div class="summary-value">' + (pocBits || esc(String(cybergymFocus.failure_category || 'not recorded'))) + '</div><div class="summary-actions">' + chips + '</div></div>';
}}
function stepSummaryFor(sid){{
  const rows = Array.isArray(payload.step_summaries) ? payload.step_summaries : [];
  return rows.find(function(item){{ return String(item.step_id) === String(sid); }}) || null;
}}
function stepFocusFor(sid){{
  const rows = Array.isArray(payload.step_focus) ? payload.step_focus : [];
  return rows.find(function(item){{ return String(item.step_id) === String(sid); }}) || null;
}}
function focusMatches(item, mode){{
  const focus = stepFocusFor(item.sid) || {{}};
  const role = String(focus.step_role || '');
  const flags = Array.isArray(focus.risk_flags) ? focus.risk_flags.map(String) : [];
  if(mode === 'all') return true;
  if(mode === 'submissions') return role === 'poc_submission' || flags.includes('cybergym_poc_submission') || flags.includes('cybergym_verification_failure');
  if(mode === 'errors') return flags.some(function(f){{ return f.includes('error') || f.includes('failure'); }});
  if(mode === 'phase') return role === 'phase_change' || !!focus.phase;
  return focus.attention_level === 'critical' || role === 'phase_change' || role === 'poc_submission' || flags.includes('cybergym_poc_submission');
}}
function roleChip(focus){{
  const level = String((focus && focus.attention_level) || 'normal');
  const role = String((focus && focus.step_role) || 'step').replaceAll('_', ' ');
  return '<span class="role-chip '+esc(level)+'">'+esc(role)+'</span>';
}}
function renderEvidenceRefs(focus){{
  const refs = Array.isArray(focus && focus.evidence_refs) ? focus.evidence_refs : [];
  if(!refs.length) return '';
  return '<div class="summary-actions">' + refs.map(function(ref){{ return '<span class="chip">'+esc(String(ref))+'</span>'; }}).join('') + '</div>';
}}
function renderInspectorTabButton(name, active){{
  return '<button class="inspector-tab '+(active ? 'active' : '')+'" type="button" data-inspector-tab="'+esc(name)+'">'+esc(name)+'</button>';
}}
function renderInspectorCallList(step, interaction){{
  const calls = Array.isArray(interaction.calls) ? interaction.calls : [];
  if(!calls.length) return '<div class="muted">No action calls recorded.</div>';
  return '<div class="inspector-call-list">'+calls.map(function(call){{
    const index = Number(call.index || 0);
    const klass = statusClass(call.status);
    return '<button class="inspector-call '+(activeCallIndex === index ? 'active' : '')+'" type="button" data-select-call="true" data-call-step="'+esc(String(step.step_id))+'" data-call-index="'+index+'" data-call-scroll="true">' +
      '<span class="call-badge-index">'+(index+1)+'</span><span><b>'+esc(call.tool_name || 'action')+'</b><br><span class="muted">'+esc(call.result_summary || 'result not recorded')+'</span></span><span class="call-status '+klass+'">'+esc(call.status || 'not recorded')+'</span></button>';
  }}).join('')+'</div>';
}}
function renderCallInspector(step, interaction, call, tab){{
  if(!inspector || !call) return;
  const currentTab = tab || 'summary';
  const tabs = ['summary','params','result','raw'];
  const args = (call.args && typeof call.args === 'object') ? call.args : {{}};
  const resultText = fullEvidenceValue(call.result);
  const rawText = fullEvidenceValue(call.raw_result);
  let body = '';
  if(currentTab === 'params'){{
    body = renderCodeEvidence('params', 'Complete parameters', '', {{text:fullEvidenceValue(args),source:'Complete action arguments'}}, Object.keys(args).length+' fields', true);
  }} else if(currentTab === 'result'){{
    const resultSource = call.result_source === 'model_visible' ? 'observation_ready.action_results' : call.result_source === 'raw_fallback' ? 'step.action_results fallback' : 'not recorded';
    body = renderCodeEvidence('result', 'Action result', call.status || '', {{text:resultText,source:resultSource}}, resultText.length+' chars', true) +
      '<div style="margin-top:8px">'+renderCodeEvidence('raw-result', 'Canonical raw result', '', {{text:rawText,source:'step.action_results'}}, rawText.length+' chars', false)+'</div>';
  }} else if(currentTab === 'raw'){{
    body = fullJsonBlock({{action:call.action, invocation:call.invocation, result:call.result, raw_result:call.raw_result}}, 'Complete selected call JSON', true);
  }} else {{
    body = '<div class="kv">'+
      kvRow('call', Number(call.index || 0)+1)+
      kvRow('tool', call.tool_name || '-')+
      kvRow('status', call.status || 'not recorded')+
      kvRow('latency', formatLatency(call.latency_ms))+
      kvRow('attempts', call.attempts === null || call.attempts === undefined ? 'not recorded' : call.attempts)+
      kvRow('pairing', call.pairing_method || 'not recorded')+
      '</div>'+renderParamStrip(args)+'<div class="summary-line" style="margin-top:9px"><b>Result</b> · '+esc(call.result_summary || 'not recorded')+'</div>';
  }}
  inspector.innerHTML = '<div class="summary-title">Selected Call</div><div class="summary-value">Step '+esc(String(step.step_id))+' · Call '+(Number(call.index || 0)+1)+' · '+esc(call.tool_name || 'action')+'</div>' +
    '<div class="inspector-tabs">'+tabs.map(function(name){{ return renderInspectorTabButton(name, name === currentTab); }}).join('')+'</div><div id="inspectorBody">'+body+'</div>';
  inspector.querySelectorAll('[data-inspector-tab]').forEach(function(btn){{
    btn.addEventListener('click', function(){{ renderCallInspector(step, interaction, call, btn.getAttribute('data-inspector-tab') || 'summary'); }});
  }});
}}
function renderInspector(step, tab){{
  if(!inspector) return;
  if(!step){{ inspector.innerHTML = '<div class="muted">No step selected.</div>'; return; }}
  const sid = String(step.step_id);
  const events = eventsByStep[sid] || [];
  const interaction = stepInteractionFor(sid);
  const summary = stepSummaryFor(sid) || {{}};
  const focus = stepFocusFor(sid) || {{}};
  const currentTab = tab || 'summary';
  const tabs = ['summary','evidence','metadata','raw'];
  let body = '';
  if(currentTab === 'raw'){{
    body = fullJsonBlock({{step: step, events: events, summary: summary, focus: focus, interaction: interaction}}, 'full selected step JSON', true);
  }} else if(currentTab === 'evidence'){{
    body =
      '<h4>Input</h4>' + renderInputBlock(step, false) +
      '<h4>Thought</h4>' + renderThoughtBlock(step, false) +
      '<h4>Action Calls</h4>' + renderInspectorCallList(step, interaction) + renderEnvironmentObservation(interaction) +
      '<h4>Parser Diagnostics</h4>' + renderParserDiagnostics(step.parser_diagnostics || {{}}) +
      '<h4>Critic</h4>' + renderCritic(step.critic_outputs || []);
  }} else if(currentTab === 'metadata'){{
    const obsInput = {{observe_output: step.observation || {{}}, context: step.context || {{}}}};
    body =
      '<h4>Step Metadata</h4>' + fullJsonBlock(focus, 'full focus model', false) +
      '<h4>State / Context</h4>' + renderState(obsInput) + fullJsonBlock(step.context || {{}}, 'full context', false) +
      '<h4>Prompt Metadata</h4>' + renderPromptMetadata(step.prompt_metadata || {{}}) + fullJsonBlock(step.prompt_metadata || {{}}, 'full prompt metadata', false) +
      '<h4>Visual Assets</h4>' + renderVisualAssets(step) +
      '<h4>Memory Update</h4>' + renderMemoryUpdate(step.observation || {{}}) +
      '<h4>Trace Events</h4>' + renderEvents(events);
  }} else {{
    const flags = Array.isArray(summary.risk_flags) ? summary.risk_flags : [];
    body =
      '<div class="kv">' +
      kvRow('step', sid) +
      kvRow('agent', step.agent_id || '-') +
      kvRow('role', focus.step_role || '-') +
      kvRow('phase', focus.phase || '-') +
      kvRow('attention', focus.attention_level || '-') +
      kvRow('events', events.length) +
      kvRow('calls', Array.isArray(interaction.calls) ? interaction.calls.length : 0) +
      kvRow('parser', ((summary.parser || {{}}).summary) || '-') +
      kvRow('errors', Array.isArray(summary.errors) ? summary.errors.join(' | ') : '-') +
      '</div><div class="summary-actions">' + roleChip(focus) + (flags.length ? flags.map(riskChip).join('') : '<span class="risk-chip ok">no risk flags</span>') + '</div>' +
      renderEvidenceRefs(focus) +
      '<h4>Action Calls</h4>' + renderInspectorCallList(step, interaction) +
      '<div style="margin-top:10px">' + fullJsonBlock(summary, 'full step summary JSON', false) + '</div>';
  }}
  inspector.innerHTML =
    '<div class="summary-title">Selected Step</div><div class="summary-value">Step ' + esc(sid) + '</div>' +
    '<div class="inspector-tabs">' + tabs.map(function(t){{ return renderInspectorTabButton(t, t === currentTab); }}).join('') + '</div>' +
    '<div id="inspectorBody">' + body + '</div>';
  inspector.querySelectorAll('[data-inspector-tab]').forEach(function(btn){{
    btn.addEventListener('click', function(){{ renderInspector(step, btn.getAttribute('data-inspector-tab') || 'summary'); }});
  }});
}}
function selectStep(sid, scroll){{
  activeStepId = String(sid);
  activeCallIndex = null;
  document.querySelectorAll('.toc-item').forEach(function(x){{ x.classList.toggle('active', x.getAttribute('data-step') === activeStepId); }});
  document.querySelectorAll('.card[data-step]').forEach(function(x){{ x.style.borderColor = x.getAttribute('data-step') === activeStepId ? 'var(--accent)' : 'var(--line)'; }});
  document.querySelectorAll('.call-unit,.call-badge').forEach(function(x){{ x.classList.remove('active'); }});
  const step = steps.find(function(s){{ return String(s.step_id) === activeStepId; }});
  renderInspector(step || null, 'summary');
  if(scroll){{
    const target = document.getElementById('step-' + activeStepId);
    if(target) target.scrollIntoView({{behavior:'smooth', block:'start'}});
  }}
}}
function selectCall(sid, index, scroll){{
  activeStepId = String(sid);
  activeCallIndex = Number(index);
  document.querySelectorAll('.toc-item').forEach(function(x){{ x.classList.toggle('active', x.getAttribute('data-step') === activeStepId); }});
  document.querySelectorAll('.card[data-step]').forEach(function(x){{ x.style.borderColor = x.getAttribute('data-step') === activeStepId ? 'var(--accent)' : 'var(--line)'; }});
  document.querySelectorAll('.call-unit').forEach(function(x){{ x.classList.toggle('active', x.id === 'call-'+activeStepId+'-'+activeCallIndex); }});
  document.querySelectorAll('.call-badge').forEach(function(x){{
    const card = x.closest('.card[data-step]');
    x.classList.toggle('active', !!card && card.getAttribute('data-step') === activeStepId && Number(x.getAttribute('data-call-badge')) === activeCallIndex);
  }});
  const step = steps.find(function(item){{ return String(item.step_id) === activeStepId; }});
  const interaction = stepInteractionFor(activeStepId);
  const call = (Array.isArray(interaction.calls) ? interaction.calls : []).find(function(item){{ return Number(item.index) === activeCallIndex; }});
  if(step && call) renderCallInspector(step, interaction, call, 'summary');
  if(scroll){{
    const target = document.getElementById('call-'+activeStepId+'-'+activeCallIndex);
    if(target) target.scrollIntoView({{behavior:'smooth', block:'center'}});
  }}
}}
function paintOverview(items){{
  const m = payload.manifest || {{}};
  const s = m.summary || {{}};
  const c = s.context || {{}};
  const p = s.parser || {{}};
  const rs = (m.run_spec && typeof m.run_spec === 'object') ? m.run_spec : {{}};
  const total = items.length;
  const avgEvents = total ? (items.reduce((a,it)=>a + (it.events||[]).length, 0) / total).toFixed(1) : '0.0';
  const agentIds = new Set(items.map(function(it){{ return it.step && it.step.agent_id; }}).filter(Boolean));
  const agentList = Array.from(agentIds);
  const topo = (m.agent_topology && typeof m.agent_topology === 'object') ? m.agent_topology : null;
  const handoffCount = m.handoff_count || 0;
  const multiAgentRows = [];
  if(agentList.length > 0) multiAgentRows.push(['agents', agentList.join(', ')]);
  if(topo) multiAgentRows.push(['agent_topology', (topo.type || '') + (topo.agents ? ' (' + topo.agents.join(', ') + ')' : '')]);
  if(handoffCount) multiAgentRows.push(['handoff_count', String(handoffCount)]);
  // Count delegate/fanout events
  let delegateCount = 0, fanoutCount = 0;
  for(const it of items){{
    const es = it.events || [];
    for(const e of es){{
      const ph = String(e.phase||'').toLowerCase();
      if(ph.includes('delegate') && ph.includes('start')) delegateCount++;
      if(ph.includes('fanout') && ph.includes('start')) fanoutCount++;
    }}
  }}
  if(delegateCount) multiAgentRows.push(['delegate_count', String(delegateCount)]);
  if(fanoutCount) multiAgentRows.push(['fanout_count', String(fanoutCount)]);
  overview.innerHTML = [
    ['run', payload.run_id || '-'],
    ['status', m.status || '-'],
    ['official run', m.official_run ? 'yes' : 'no'],
    ['replay mode', m.replay_mode || '-'],
    ['stop', s.stop_reason || '-'],
    ['steps', String(total)],
    ['avg events/step', String(avgEvents)],
  ].concat(multiAgentRows).concat([
    ['model', m.model_id || '-'],
    ['model family', m.model_family || rs.model_family || '-'],
    ['family preset', ((rs.metadata || {{}}).family_preset) || (((s.run_meta || {{}}).harness || {{}}).family_preset) || '-'],
    ['decision lane', (((rs.metadata || {{}}).harness_policy || {{}}).decision_lane_preference) || ((((s.run_meta || {{}}).harness || {{}}).decision_lane_preference)) || '-'],
    ['tool delivery', (((rs.metadata || {{}}).harness_policy || {{}}).effective_tool_delivery) || (((((s.run_meta || {{}}).harness || {{}}).effective_tool_delivery))) || '-'],
    ['git SHA', m.git_sha || rs.git_sha || '-'],
    ['package', m.package_version || rs.package_version || '-'],
    ['seed', m.seed === null ? 'null' : (m.seed || rs.seed || '-')],
    ['prompt protocol', m.prompt_protocol || rs.prompt_protocol || '-'],
    ['parser', m.parser_name || rs.parser_name || '-'],
    ['tokens total', String(c.tokens_total || s.token_usage || 0)],
    ['avg tokens/step', String(total > 0 ? Math.round(Number(c.tokens_total || s.token_usage || 0) / total) : 0)],
    ['runtime (s)', String(m.latency_seconds ? Number(m.latency_seconds).toFixed(1) : '-')],
    ['cost ($)', m.cost != null ? Number(m.cost).toFixed(4) : '-'],
    ['peak ctx', c.peak_occupancy_ratio ? ((Number(c.peak_occupancy_ratio) * 100).toFixed(1) + '%') : '-'],
    ['compacts', JSON.stringify(c.compact_counts || {{}})],
    ['parser errors', String(p.error_count || 0)],
    ['parser salvage', String(p.salvage_count || 0)],
    ['critic interventions', String(items.reduce(function(a,it){{ return a + (Array.isArray(it.step.critic_outputs) ? it.step.critic_outputs.length : 0); }}, 0))],
    ['critic retries', String(items.reduce(function(a,it){{ return a + (Array.isArray(it.step.critic_outputs) ? it.step.critic_outputs.filter(function(c){{ return c && c.action === 'retry'; }}).length : 0); }}, 0))],
    ['critic stops', String(items.reduce(function(a,it){{ return a + (Array.isArray(it.step.critic_outputs) ? it.step.critic_outputs.filter(function(c){{ return c && c.action === 'stop'; }}).length : 0); }}, 0))],
    ['critic avg score', (function(){{ const scores = []; items.forEach(function(it){{ (Array.isArray(it.step.critic_outputs) ? it.step.critic_outputs : []).forEach(function(c){{ if(c && typeof c.score === 'number') scores.push(c.score); }}); }}); return scores.length ? (scores.reduce(function(a,b){{ return a+b; }},0)/scores.length).toFixed(2) : '-'; }})()],
    ['replay note', m.replay_note || '-'],
  ]).map(([k,v])=>'<div class="ov"><div class="k">'+esc(k)+'</div><div class="v">'+esc(v)+'</div></div>').join('');
}}
function buildTimeline(items){{
  const rows = [];
  for(const it of items){{
    const evs = (it.events || []).slice().sort(function(a,b){{
      const ta = parseTs(a.ts); const tb = parseTs(b.ts);
      if(ta === null && tb === null) return 0;
      if(ta === null) return 1;
      if(tb === null) return -1;
      return ta - tb;
    }});
    const marks = evs.map(function(e){{ return parseTs(e.ts); }}).filter(function(x){{ return x !== null; }});
    const first = marks.length ? Math.min.apply(null, marks) : null;
    const last = marks.length ? Math.max.apply(null, marks) : null;
    const total = (first !== null && last !== null && last > first) ? (last - first) : null;
    const summary = stepSummaryFor(it.sid) || {{}};
    const focus = stepFocusFor(it.sid) || {{}};
    const flags = Array.isArray(summary.risk_flags) ? summary.risk_flags : [];
    const focusFlags = Array.isArray(focus.risk_flags) ? focus.risk_flags : flags;
    const role = String(focus.step_role || 'step').replaceAll('_', ' ');
    const phase = String(focus.phase || phaseFromEvents(evs) || '-');
    const action = String(actionSummaryLabel(it.step.actions || []) || focus.action_label || summary.action || '-');
    const outcome = String(focus.outcome_label || (Array.isArray(summary.errors) && summary.errors[0]) || '-');
    const headline = role + ' · ' + action;
    const detail = outcome && outcome !== 'recorded' ? outcome : (summary.thought || phase);
    const chips = focusFlags.slice(0, 4).map(riskChip).join('');
    const level = String(focus.attention_level || 'normal');
    const d = total !== null ? (total + 'ms') : (evs.length ? evs.length + ' events' : '-');
    rows.push(
      '<div class="story-step '+esc(level)+'" role="button" tabindex="0" onclick="selectStep(\\''+esc(it.sid)+'\\', true)">' +
        '<div class="story-step-id">STEP '+esc(it.sid)+'</div>' +
        '<div class="story-step-main">' +
          '<div class="story-step-title"><span>'+esc(headline)+'</span><span class="chip">'+esc(phase)+'</span></div>' +
          '<div class="story-step-summary">'+esc(firstLine(detail, 170))+'</div>' +
          (chips ? '<div class="story-step-meta">'+chips+'</div>' : '') +
        '</div>' +
        '<div class="story-step-time">'+esc(d)+'</div>' +
      '</div>'
    );
  }}
  timelineRoot.innerHTML = rows.length ? '<div class="story-rail">'+rows.join('')+'</div>' : '<div class="muted">No focused behavior steps.</div>';
}}
function buildHandoffGantt(items){{
  const el = document.getElementById('handoffGantt');
  if(!el) return;
  const agentOrder = [];
  const agentSteps = {{}};
  const handoffs = [];
  const agentColors = ['#5e6ad2','#27a644','#e5c100','#e5484d','#6b8fc4','#d97bf0','#3dc9b0','#f59e42'];
  function agentColor(aid){{ return agentColors[agentOrder.indexOf(aid) % agentColors.length]; }}
  items.forEach(function(it){{
    const aid = it.step && it.step.agent_id;
    if(aid && !agentOrder.includes(aid)) agentOrder.push(aid);
    if(aid){{ if(!agentSteps[aid]) agentSteps[aid] = []; agentSteps[aid].push(it.sid); }}
    (it.events || []).forEach(function(e){{
      const ph = String(e.phase || '').toLowerCase();
      if(ph === 'handoff_start'){{
        handoffs.push({{ sid: it.sid, from: (e.payload && e.payload.from) || aid || '?', to: (e.payload && e.payload.to) || '?' }});
      }}
    }});
  }});
  if(agentOrder.length < 2 && handoffs.length === 0){{
    el.innerHTML = '<div class="muted">No handoff events recorded.</div>';
    document.getElementById('handoffGanttSection').style.display = 'none';
    return;
  }}
  const laneH = 36, labelW = 120, padTop = 20, padBottom = 28, padRight = 18, width = 980;
  const totalSteps = items.length;
  const plotW = width - labelW - padRight;
  const totalH = padTop + agentOrder.length * laneH + padBottom;
  const stepW = totalSteps > 1 ? plotW / (totalSteps - 1) : plotW;
  function stepX(sid){{ const idx = items.findIndex(function(it){{ return it.sid === String(sid); }}); return labelW + (idx >= 0 ? idx * stepW : 0); }}
  function laneY(ai){{ return padTop + ai * laneH + laneH / 2; }}
  const p = [];
  p.push('<svg class="gantt-svg" viewBox="0 0 '+width+' '+totalH+'" role="img" aria-label="Handoff gantt chart">');
  p.push('<defs><marker id="hArrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="#bfa04e"/></marker></defs>');
  items.forEach(function(it){{
    p.push('<text class="gantt-step-label" x="'+stepX(it.sid)+'" y="'+(totalH-6)+'" text-anchor="middle">S'+esc(it.sid)+'</text>');
  }});
  agentOrder.forEach(function(aid, i){{
    const y = padTop + i * laneH;
    p.push('<rect class="gantt-lane" x="'+labelW+'" y="'+y+'" width="'+plotW+'" height="'+laneH+'"/>');
    p.push('<text class="gantt-label" x="'+(labelW-8)+'" y="'+(y+laneH/2+4)+'" text-anchor="end">'+esc(aid)+'</text>');
    const sids = agentSteps[aid] || [];
    if(sids.length > 0){{
      const mn = Math.min.apply(null, sids.map(Number));
      const mx = Math.max.apply(null, sids.map(Number));
      const x1 = stepX(String(mn)) - Math.min(10, stepW/2);
      const x2 = stepX(String(mx)) + Math.min(10, stepW/2);
      p.push('<rect class="gantt-bar" x="'+x1+'" y="'+(y+6)+'" width="'+(x2-x1)+'" height="'+(laneH-12)+'" fill="'+agentColor(aid)+'"/>');
    }}
  }});
  handoffs.forEach(function(h){{
    const fi = agentOrder.indexOf(h.from), ti = agentOrder.indexOf(h.to);
    if(fi < 0 || ti < 0) return;
    const x = stepX(h.sid), y1 = laneY(fi), y2 = laneY(ti);
    const cd = y2 > y1 ? -20 : 20;
    p.push('<path class="gantt-arrow" d="M'+x+','+y1+' C'+(x+cd)+','+((y1+y2)/2)+' '+(x+cd)+','+((y1+y2)/2)+' '+x+','+y2+'"><title>STEP '+h.sid+': '+h.from+' -> '+h.to+'</title></path>');
  }});
  p.push('</svg>');
  el.innerHTML = p.join('');
}}
function buildCostPanel(items){{
  const el = document.getElementById('costPanel');
  if(!el) return;
  const m = (payload.manifest && typeof payload.manifest === 'object') ? payload.manifest : {{}};
  const s = m.summary || {{}};
  const c = s.context || {{}};
  const totalSteps = Number(m.step_count || items.length);
  const tokenValue = c.tokens_total !== undefined ? c.tokens_total : (typeof m.token_usage === 'object' ? (m.token_usage.total || m.token_usage.tokens_total || 0) : m.token_usage);
  const tokensTotal = Number(tokenValue || 0);
  const avgTokens = totalSteps > 0 ? Math.round(tokensTotal / totalSteps) : 0;
  const runtimeSec = Number(m.latency_seconds || 0);
  const costVal = m.cost != null ? Number(m.cost) : 0;
  if(!tokensTotal && !runtimeSec && !costVal){{
    el.innerHTML = '<div class="muted">No cost/performance data available.</div>';
    document.getElementById('costPanelSection').style.display = 'none';
    return;
  }}
  const barH = 24, maxBar = 280, gap = 18, labelW = 130, padTop = 10, padBottom = 8;
  const rows = [
    {{label: 'tokens total', value: tokensTotal, max: Math.max(tokensTotal, 1), color: 'var(--accent)'}},
    {{label: 'avg tokens/step', value: avgTokens, max: Math.max(avgTokens, 1), color: 'var(--kind-memory)'}},
    {{label: 'runtime (s)', value: runtimeSec, max: Math.max(runtimeSec, 1), color: 'var(--warn)'}},
    {{label: 'cost ($)', value: costVal, max: Math.max(costVal, 0.001), color: 'var(--err)'}},
  ];
  const totalH = padTop + rows.length * (barH + gap) + padBottom;
  const width = labelW + maxBar + 80;
  let p = ['<svg class="gantt-svg" viewBox="0 0 '+width+' '+totalH+'" role="img" aria-label="Cost summary">'];
  rows.forEach(function(r, i){{
    const y = padTop + i * (barH + gap);
    const barW = Math.max(4, (r.value / r.max) * maxBar);
    p.push('<text class="gantt-label" x="'+(labelW-8)+'" y="'+(y+barH/2+4)+'" text-anchor="end">'+esc(r.label)+'</text>');
    p.push('<rect x="'+labelW+'" y="'+y+'" width="'+barW+'" height="'+barH+'" fill="'+r.color+'" rx="4" ry="4" fill-opacity="0.7"/>');
    const valText = r.label.includes('cost') ? Number(r.value).toFixed(4) : String(r.value);
    p.push('<text class="gantt-step-label" x="'+(labelW+barW+8)+'" y="'+(y+barH/2+4)+'" fill="'+r.color+'">'+esc(valText)+'</text>');
  }});
  p.push('</svg>');
  el.innerHTML = p.join('');
}}
function buildContextTimeline(items){{
  const points = items.map(function(it){{
    const ctx = (it.step && typeof it.step.context === 'object') ? it.step.context : {{}};
    const ratio = Number(ctx.occupancy_ratio);
    return {{
      sid: String(it.sid),
      ratio: Number.isFinite(ratio) ? Math.max(0, Math.min(1, ratio)) : null,
      tokens: ctx.input_tokens_total,
      window: ctx.context_window,
      events: Array.isArray(ctx.compact_events) ? ctx.compact_events : [],
    }};
  }}).filter(function(p){{ return p.ratio !== null; }});
  if(!points.length){{
    contextTimelineRoot.innerHTML = '<div class="muted">No context telemetry available.</div>';
    return;
  }}
  const width = 980;
  const height = 220;
  const left = 50;
  const right = 18;
  const top = 18;
  const bottom = 36;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  function xAt(index){{
    if(points.length === 1) return left + (plotWidth / 2);
    return left + ((plotWidth * index) / (points.length - 1));
  }}
  function yAt(ratio){{
    return top + ((1 - ratio) * plotHeight);
  }}
  const poly = [];
  const area = [];
  const compactRows = [];
  const circles = [];
  const labels = [];
  const compactDots = [];
  const grid = [];
  for(let g = 0; g <= 4; g += 1){{
    const ratio = g / 4;
    const y = yAt(ratio);
    grid.push('<line class="context-grid" x1="' + left + '" y1="' + y + '" x2="' + (width - right) + '" y2="' + y + '"></line>');
    labels.push('<text class="context-label" x="6" y="' + (y + 4) + '">' + Math.round((1 - ratio) * 100) + '%</text>');
  }}
  points.forEach(function(p, index){{
    const x = xAt(index);
    const y = yAt(p.ratio);
    poly.push(x + ',' + y);
    area.push((index === 0 ? 'M' : 'L') + x + ' ' + y);
    // Check for multi-agent events at this step
    const step = steps.find(function(s){{ return String(s.step_id) === p.sid; }});
    const stepEvents = step ? (eventsByStep[p.sid] || []) : [];
    let hasHandoff = false, hasDelegate = false, hasFanout = false;
    for(const e of stepEvents){{
      const ph = String(e.phase||'').toLowerCase();
      if(ph.includes('handoff')) hasHandoff = true;
      if(ph.includes('delegate')) hasDelegate = true;
      if(ph.includes('fanout')) hasFanout = true;
    }}
    const agentId = step ? (step.agent_id || '') : '';
    const maColor = hasHandoff ? 'var(--kind-handoff)' : hasDelegate ? 'var(--kind-delegation)' : hasFanout ? 'var(--kind-fanout)' : '';
    if(maColor){{
      // Draw a diamond marker for multi-agent events
      const s = 6;
      circles.push('<polygon points="' + x + ',' + (y-s) + ' ' + (x+s) + ',' + y + ' ' + x + ',' + (y+s) + ' ' + (x-s) + ',' + y + '" fill="' + maColor + '" style="stroke:var(--surface-1)" stroke-width="1.5"><title>' + esc('STEP ' + p.sid + (agentId ? ' agent=' + agentId : '') + (hasHandoff ? ' HANDOFF' : '') + (hasDelegate ? ' DELEGATE' : '') + (hasFanout ? ' FANOUT' : '')) + '</title></polygon>');
    }} else {{
      circles.push('<circle class="context-point" cx="' + x + '" cy="' + y + '" r="4"' + (agentId ? ' fill="' + agentColor(agentId) + '"' : '') + '><title>' + esc('STEP ' + p.sid + (agentId ? ' agent=' + agentId : '')) + '</title></circle>');
    }}
    labels.push('<text class="context-label" x="' + (x - 14) + '" y="' + (height - 10) + '">S' + esc(p.sid) + '</text>');
    if(Array.isArray(p.events) && p.events.length){{
      const seen = new Set();
      p.events.forEach(function(ev, dotIndex){{
        const color = compactStageColor(ev.stage);
        const stage = compactStageLabel(ev.stage);
        if(!seen.has(stage)){{
          compactRows.push('<div class="compact-item"><div class="compact-step">STEP ' + esc(p.sid) + '</div><div class="compact-desc">' + esc(compactEventText(ev)) + '</div></div>');
          seen.add(stage);
        }}
        const dy = 14 + (dotIndex * 9);
        compactDots.push('<circle class="compact-dot" cx="' + x + '" cy="' + dy + '" r="4.5" fill="' + color + '"><title>' + esc('STEP ' + p.sid + ' · ' + compactEventText(ev)) + '</title></circle>');
      }});
    }}
  }});
  const lastX = xAt(points.length - 1);
  const baseY = top + plotHeight;
  const areaPath = area.join(' ') + ' L ' + lastX + ' ' + baseY + ' L ' + xAt(0) + ' ' + baseY + ' Z';
  const peak = points.reduce(function(acc, p){{ return Math.max(acc, p.ratio || 0); }}, 0);
  const latest = points[points.length - 1];
  const compactCount = points.reduce(function(acc, p){{ return acc + (Array.isArray(p.events) ? p.events.length : 0); }}, 0);
  const svg = '<svg class="context-svg" viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Context occupancy timeline">' +
    '<line class="context-axis" x1="' + left + '" y1="' + top + '" x2="' + left + '" y2="' + (height - bottom) + '"></line>' +
    '<line class="context-axis" x1="' + left + '" y1="' + (height - bottom) + '" x2="' + (width - right) + '" y2="' + (height - bottom) + '"></line>' +
    grid.join('') +
    '<path class="context-fill" d="' + areaPath + '"></path>' +
    '<polyline class="context-line" points="' + poly.join(' ') + '"></polyline>' +
    circles.join('') +
    compactDots.join('') +
    labels.join('') +
    '</svg>';
  const head = '<div class="context-head">' +
    '<div>peak ' + esc((peak * 100).toFixed(1) + '%') + '</div>' +
    '<div>latest ' + esc(((Number(latest.ratio) || 0) * 100).toFixed(1) + '%') + ' · ' + esc(String(latest.tokens || 0)) + ' tokens</div>' +
    '<div>compact markers ' + esc(String(compactCount)) + '</div>' +
    '<div style="display:flex;gap:10px;font-size:11px"><span style="color:var(--kind-handoff)">◆ handoff</span> <span style="color:var(--kind-delegation)">◆ delegate</span> <span style="color:var(--kind-fanout)">◆ fanout</span></div>' +
    '</div>';
  const list = compactRows.length ? ('<div class="compact-list">' + compactRows.join('') + '</div>') : '<div class="muted">No compact or warning markers recorded.</div>';
  contextTimelineRoot.innerHTML = '<div class="context-chart">' + head + svg + list + '</div>';
}}
function buildParserTimeline(items){{
  const rows = [];
  for(const it of items){{
    const diag = (it.step && typeof it.step.parser_diagnostics === 'object') ? it.step.parser_diagnostics : null;
    if(!diag || !Object.keys(diag).length) continue;
    const sev = String(diag.severity || 'error').toLowerCase();
    const color = sev === 'error' ? 'var(--err)' : 'var(--kind-critic)';
    const marker = diag.salvage_applied ? ' · salvage' : '';
    const protocol = diag.protocol ? (' · ' + String(diag.protocol)) : '';
    const fallback = diag.fallback_used ? ' · fallback' : '';
    const extraction = diag.extraction_mode ? (' · ' + String(diag.extraction_mode)) : '';
    rows.push(
      '<div class="compact-item"><div class="compact-step">STEP ' + esc(it.sid) + '</div><div class="compact-desc">' +
      '<span style="color:' + color + ';font-weight:700">' + esc(String(diag.code || sev)) + '</span> · ' +
      esc(truncateText(diag.summary || 'Parser diagnostic', 220)) + protocol + extraction + fallback + marker + '</div></div>'
    );
  }}
  parserTimelineRoot.innerHTML = rows.length ? ('<div class="compact-list">' + rows.join('') + '</div>') : '<div class="muted">No parser diagnostics recorded.</div>';
}}
function buildCriticTimeline(items){{
  // Collect all critic outputs across steps
  const points = [];
  for(const it of items){{
    const cs = Array.isArray(it.step.critic_outputs) ? it.step.critic_outputs : [];
    if(!cs.length) continue;
    for(let ci = 0; ci < cs.length; ci++){{
      const c = cs[ci];
      if(!c || typeof c !== 'object') continue;
      points.push({{
        sid: it.sid,
        action: String(c.action || ''),
        reason: String(c.reason || ''),
        score: typeof c.score === 'number' ? c.score : null,
        has_patch: !!(c.modified_prompt || c.instruction_patch || c.state_patch),
        index: ci,
      }});
    }}
  }}
  if(!points.length){{
    criticTimelineRoot.innerHTML = '<div class="muted">No critic interventions recorded.</div>';
    return;
  }}
  // Build SVG timeline
  const width = 980;
  const barH = 28;
  const gap = 4;
  const left = 56;
  const right = 14;
  const top = 28;
  const bottom = 32;
  const plotWidth = width - left - right;
  const n = points.length;
  const totalH = top + n * (barH + gap) + bottom;
  const actionColors = {{continue:'var(--ok)', stop:'var(--err)', retry:'var(--warn)'}};
  const rows = [];
  const labels = [];
  // X axis labels
  const steps = new Set(points.map(function(p){{ return p.sid; }}));
  const stepList = Array.from(steps).sort(function(a,b){{ return Number(a)-Number(b); }});
  const stepToX = function(sid){{
    const idx = stepList.indexOf(sid);
    if(idx < 0) return left;
    if(stepList.length === 1) return left + plotWidth/2;
    return left + (plotWidth * idx) / (stepList.length - 1);
  }};
  // Grid lines for steps
  for(let i = 0; i < stepList.length; i++){{
    const x = stepToX(stepList[i]);
    rows.push('<line x1="'+x+'" y1="'+top+'" x2="'+x+'" y2="'+(totalH-bottom)+'" stroke="var(--line)" stroke-width="1" stroke-dasharray="4 4"/>');
    labels.push('<text x="'+x+'" y="'+(totalH-8)+'" fill="var(--subtle)" font-size="11" text-anchor="middle">S'+esc(stepList[i])+'</text>');
  }}
  // Score axis
  for(let g = 0; g <= 4; g++){{
    const ratio = g / 4;
    const y = top + (1 - ratio) * (totalH - top - bottom);
    // not used for bar chart, skip
  }}
  // Draw critic bars
  const barRows = [];
  for(let i = 0; i < n; i++){{
    const p = points[i];
    const y = top + i * (barH + gap);
    const x = stepToX(p.sid);
    const color = actionColors[p.action] || '#94a3b8';
    const halfBar = barH / 2;
    // Dot at step position
    rows.push('<circle cx="'+x+'" cy="'+(y+halfBar)+'" r="'+(p.has_patch?8:5)+'" fill="'+color+'" stroke="var(--surface-1)" stroke-width="1.5"><title>STEP '+esc(p.sid)+' critic #'+(p.index+1)+' action='+esc(p.action)+(p.score!==null?' score='+p.score.toFixed(2):'')+(p.has_patch?' [has patch]':'')+'</title></circle>');
    // Patch indicator ring
    if(p.has_patch){{
      rows.push('<circle cx="'+x+'" cy="'+(y+halfBar)+'" r="11" fill="none" stroke="'+color+'" stroke-width="1.5" stroke-dasharray="3 2"/>');
    }}
    // Label
    const scoreText = p.score !== null ? (' '+p.score.toFixed(2)) : '';
    const patchText = p.has_patch ? ' ✎' : '';
    const labelText = 'S'+p.sid+' #'+(p.index+1)+' '+p.action+scoreText+patchText;
    barRows.push('<text x="'+(left-4)+'" y="'+(y+halfBar+4)+'" fill="'+color+'" font-size="10" text-anchor="end" font-weight="600">'+esc(labelText)+'</text>');
    // Reason tooltip line
    if(p.reason){{
      const truncated = p.reason.length > 60 ? p.reason.substring(0,60)+'...' : p.reason;
      barRows.push('<text x="'+(x+14)+'" y="'+(y+halfBar+4)+'" fill="var(--muted)" font-size="10">'+esc(truncated)+'</text>');
    }}
  }}
  // Score trend line (if scores available)
  const scorePoints = points.filter(function(p){{ return p.score !== null; }});
  let trendLine = '';
  if(scorePoints.length >= 2){{
    const minScore = Math.min.apply(null, scorePoints.map(function(p){{ return p.score; }}));
    const maxScore = Math.max.apply(null, scorePoints.map(function(p){{ return p.score; }}));
    const range = maxScore - minScore || 1;
    const trendH = totalH - top - bottom;
    const poly = scorePoints.map(function(p, idx){{
      const x = stepToX(p.sid);
      const normY = (p.score - minScore) / range;
      // Use a separate mini area at top
      const y = top + (1 - normY) * 40;
      return x+','+y;
    }});
    trendLine = '<polyline points="'+poly.join(' ')+'" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.7"/>';
    for(let si = 0; si < scorePoints.length; si++){{
      const x = stepToX(scorePoints[si].sid);
      const normY = (scorePoints[si].score - minScore) / range;
      const y = top + (1 - normY) * 40;
      trendLine += '<circle cx="'+x+'" cy="'+y+'" r="3" fill="var(--accent)"><title>score='+scorePoints[si].score.toFixed(2)+'</title></circle>';
    }}
  }}
  // Summary stats
  const continueCount = points.filter(function(p){{ return p.action === 'continue'; }}).length;
  const stopCount = points.filter(function(p){{ return p.action === 'stop'; }}).length;
  const retryCount = points.filter(function(p){{ return p.action === 'retry'; }}).length;
  const patchCount = points.filter(function(p){{ return p.has_patch; }}).length;
  const scores = points.filter(function(p){{ return p.score !== null; }}).map(function(p){{ return p.score; }});
  const avgScore = scores.length ? (scores.reduce(function(a,b){{ return a+b; }},0)/scores.length).toFixed(2) : '-';
  const head = '<div class="context-head">' +
    '<div>interventions ' + esc(String(n)) + '</div>' +
    '<div style="display:flex;gap:10px;font-size:11px">' +
    '<span style="color:var(--ok)">● continue ' + continueCount + '</span>' +
    '<span style="color:var(--err)">● stop ' + stopCount + '</span>' +
    '<span style="color:var(--warn)">● retry ' + retryCount + '</span>' +
    '</div>' +
    '<div>patches ' + esc(String(patchCount)) + '</div>' +
    '<div>avg score ' + esc(String(avgScore)) + '</div>' +
    '</div>';
  const svg = '<svg class="context-svg" viewBox="0 0 '+width+' '+totalH+'" role="img" aria-label="Critic timeline">' +
    rows.join('') + barRows.join('') + trendLine + labels.join('') +
    '</svg>';
  criticTimelineRoot.innerHTML = '<div class="context-chart">' + head + svg + '</div>';
}}
function renderPromptMetadata(meta){{
  if(!meta || typeof meta !== 'object' || !Object.keys(meta).length){{
    return '<div class="muted">No prompt metadata recorded.</div>';
  }}
  const rows = [];
  if(meta.protocol) rows.push(kvRow('protocol', meta.protocol));
  if(meta.protocol_resolution_source) rows.push(kvRow('resolution', meta.protocol_resolution_source));
  if(meta.prompt_builder) rows.push(kvRow('builder', meta.prompt_builder));
  if(meta.tool_schema_delivery) rows.push(kvRow('tool schema delivery', meta.tool_schema_delivery));
  if(Array.isArray(meta.model_input_modalities) && meta.model_input_modalities.length) rows.push(kvRow('model input modalities', meta.model_input_modalities.join(', ')));
  if(meta.model_input_visual_count !== undefined) rows.push(kvRow('model input images', meta.model_input_visual_count));
  if(Array.isArray(meta.observation_modalities) && meta.observation_modalities.length) rows.push(kvRow('observation modalities', meta.observation_modalities.join(', ')));
  if(Array.isArray(meta.sections_used) && meta.sections_used.length) rows.push(kvRow('sections', meta.sections_used.join(', ')));
  if(meta.prompt_hash_static) rows.push(kvRow('static hash', meta.prompt_hash_static));
  if(meta.prompt_hash_full) rows.push(kvRow('full hash', meta.prompt_hash_full));
  if(meta.repair_injected !== undefined) rows.push(kvRow('repair injected', String(!!meta.repair_injected)));
  if(meta.continuation_injected !== undefined) rows.push(kvRow('continuation injected', String(!!meta.continuation_injected)));
  return rows.length ? rows.join('') : '<div class="muted">No prompt metadata recorded.</div>';
}}
function renderMultiAgentEvent(events){{
  let html = '';
  for(const e of events){{
    const ph = String(e.phase||'').toLowerCase();
    const pl = (e.payload && typeof e.payload === 'object') ? e.payload : {{}};
    if(ph === 'handoff_start'){{
      const from = pl.from || '?';
      const to = pl.to || '?';
      html += '<div style="padding:8px 10px;margin:4px 0;border-radius:var(--radius-md);background:var(--surface-2);border:1px solid var(--line);font-size:12px;color:var(--kind-handoff)">&#x21C4; <b>Handoff</b> ' + esc(from) + ' &rarr; ' + esc(to) + '</div>';
    }} else if(ph === 'handoff_end'){{
      html += '<div style="padding:6px 10px;margin:4px 0;border-radius:var(--radius-md);background:var(--surface-2);border:1px solid var(--line);font-size:11px;color:var(--muted)">&#x21C4; Handoff complete</div>';
    }} else if(ph === 'delegate_start'){{
      const agent = pl.agent_name || pl.agent || '?';
      const task = pl.task ? truncateText(pl.task, 120) : '';
      html += '<div style="padding:8px 10px;margin:4px 0;border-radius:var(--radius-md);background:var(--surface-2);border:1px solid var(--line);font-size:12px;color:var(--kind-delegation)">&#x2197; <b>Delegate</b> &rarr; ' + esc(agent) + (task ? ' <span style="color:var(--muted)">' + esc(task) + '</span>' : '') + '</div>';
    }} else if(ph === 'delegate_end'){{
      const status = pl.status || 'done';
      const color = status === 'done' ? 'var(--ok)' : 'var(--err)';
      html += '<div style="padding:6px 10px;margin:4px 0;border-radius:var(--radius-md);background:var(--surface-2);border:1px solid var(--line);font-size:11px;color:' + color + '">&#x2197; Delegate result: ' + esc(status) + '</div>';
    }} else if(ph === 'fanout_start'){{
      const tc = pl.task_count || pl.num_tasks || 0;
      html += '<div style="padding:8px 10px;margin:4px 0;border-radius:var(--radius-md);background:var(--surface-2);border:1px solid var(--line);font-size:12px;color:var(--kind-fanout)">&#x229B; <b>FanOut</b> ' + esc(String(tc)) + ' task(s) dispatched</div>';
    }} else if(ph === 'fanout_end'){{
      const ok = pl.succeeded || 0;
      const fail = pl.failed || 0;
      html += '<div style="padding:6px 10px;margin:4px 0;border-radius:var(--radius-md);background:var(--surface-2);border:1px solid var(--line);font-size:11px">&#x229B; FanOut: <span style="color:var(--ok)">' + ok + ' ok</span>, <span style="color:var(--err)">' + fail + ' fail</span></div>';
    }}
  }}
  return html;
}}
function renderStoryCard(it){{
  const focus = stepFocusFor(it.sid) || {{}};
  const summary = stepSummaryFor(it.sid) || {{}};
  const interaction = stepInteractionFor(it.sid);
  const flags = Array.isArray(focus.risk_flags) ? focus.risk_flags : (Array.isArray(summary.risk_flags) ? summary.risk_flags : []);
  const phase = focus.phase || '-';
  const flagHtml = flags.length ? flags.map(riskChip).join('') : '<span class="risk-chip ok">no risk flags</span>';
  const agentId = it.step.agent_id || '';
  const agentBadge = agentId ? '<span style="display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;background:' + agentColor(agentId) + '22;border:1px solid ' + agentColor(agentId) + '66;color:' + agentColor(agentId) + ';margin-left:8px">' + esc(agentId) + '</span>' : '';
  return '' +
    '<div class="card-head"><div class="step">STEP ' + esc(it.sid) + agentBadge + '</div><div>' + roleChip(focus) + '</div></div>' +
    '<div class="story-card causal-stack">' +
      '<div class="causal-stage"><div class="k">phase</div><div class="v">' + esc(phase) + '</div></div>' +
      '<div class="causal-stage"><div class="k">input</div><div class="v">' + renderInputBlock(it.step, false) + '</div></div>' +
      '<div class="causal-stage"><div class="k">thought</div><div class="v">' + renderThoughtBlock(it.step, false) + '</div></div>' +
      renderActionCalls(it.step, interaction) +
      renderEnvironmentObservation(interaction) +
      '<div class="summary-actions">' + flagHtml + '</div>' +
      renderEvidenceRefs(focus) +
      '<div class="summary-actions"><button class="btn" type="button" onclick="selectStep(\\'' + esc(it.sid) + '\\', false); renderInspector(steps.find(function(s){{return String(s.step_id)===\\'' + esc(it.sid) + '\\';}}), \\'evidence\\');">Open full evidence</button></div>' +
    '</div>';
}}
function render(){{
  const q = (document.getElementById('q').value||'').toLowerCase();
  const eventFilter = document.getElementById('eventFilter').value;
  const agentFilter = document.getElementById('agentFilter').value;
  const sort = document.getElementById('sort').value;
  let items = steps.map(function(s){{ return {{step:s, sid:String(s.step_id), events:(eventsByStep[String(s.step_id)]||[])}}; }});
  if(eventFilter) items = items.filter(function(it){{ return it.events.some(function(e){{ return String(e.phase||'')===eventFilter; }}); }});
  if(agentFilter) items = items.filter(function(it){{ return (it.step.agent_id || '') === agentFilter; }});
  if(q) items = items.filter(function(it){{ return cardText(it.step,it.events).includes(q); }});
  items.sort(function(a,b){{ return sort==='desc' ? Number(b.sid)-Number(a.sid) : Number(a.sid)-Number(b.sid); }});
  const focusedItems = items.filter(function(it){{ return focusMatches(it, focusMode); }});
  const storyItems = focusedItems.length ? focusedItems : items;
  paintRunSummary();
  paintOverview(items);
  buildScreenshotStrip();
  buildVisualTimeline(storyItems);
  buildTimeline(storyItems);
  buildHandoffGantt(items);
  buildCostPanel(items);
  buildContextTimeline(items);
  buildParserTimeline(items);
  buildCriticTimeline(items);
  flow.innerHTML = '';
  toc.innerHTML = '';
  for(const it of storyItems){{
    const card = document.createElement('article');
    card.className = 'card kind-' + inferPrimaryKind(it.events);
    card.id = 'step-' + it.sid;
    card.setAttribute('data-step', it.sid);
    card.addEventListener('click', function(e){{
      const target = e.target;
      if(target instanceof HTMLElement && (target.closest('button') || target.closest('a') || target.closest('summary'))) return;
      selectStep(it.sid, false);
    }});
    let h = '';
    const maHtml = renderMultiAgentEvent(it.events);
    if(maHtml) h += '<div style="margin-bottom:8px">' + maHtml + '</div>';
    h += renderStoryCard(it);
    card.innerHTML = h;
    flow.appendChild(card);
  }}
  if(!storyItems.length){{
    flow.innerHTML = '<div class="card"><div class="muted">No steps match this focus mode or filter.</div></div>';
  }}
  for(const it of storyItems){{
    const b = document.createElement('button');
    b.className = 'toc-item';
    b.type = 'button';
    b.setAttribute('data-step', it.sid);
    const summary = stepSummaryFor(it.sid) || {{}};
    const focus = stepFocusFor(it.sid) || {{}};
    const flags = Array.isArray(focus.risk_flags) ? focus.risk_flags : (Array.isArray(summary.risk_flags) ? summary.risk_flags : []);
    const flagMarks = flags.map(function(f){{ return '<span class="toc-flag '+esc(String(f))+'" title="'+esc(String(f))+'"></span>'; }}).join('');
    const agentId = it.step.agent_id || '';
    const tocLabel = 'STEP ' + it.sid + (agentId ? ' [' + agentId + ']' : '');
    b.innerHTML = '<div>' + esc(tocLabel) + '</div><div class="muted" style="font-size:10px;margin-top:3px">' + esc(firstLine((focus.step_role || '') + ' · ' + (focus.action_label || summary.action || ''), 44)) + '</div><div class="toc-flags">' + flagMarks + '</div>';
    b.onclick = function(){{ selectStep(it.sid, true); }};
    toc.appendChild(b);
  }}
  const phases = new Set();
  for(const s of steps){{
    const es = eventsByStep[String(s.step_id)] || [];
    for(const e of es){{ if(e.phase) phases.add(String(e.phase)); }}
  }}
  const ef = document.getElementById('eventFilter');
  const keep = ef.value;
  ef.innerHTML = '<option value="">All events</option>';
  Array.from(phases).sort().forEach(function(p){{
    const op = document.createElement('option');
    op.value = p;
    op.textContent = p;
    ef.appendChild(op);
  }});
  if(keep) ef.value = keep;
  // Agent filter
  const agentIds = new Set();
  for(const s of steps){{
    if(s.agent_id) agentIds.add(String(s.agent_id));
  }}
  const af = document.getElementById('agentFilter');
  const keepAgent = af.value;
  af.innerHTML = '<option value="">All agents</option>';
  Array.from(agentIds).sort().forEach(function(a){{
    const op = document.createElement('option');
    op.value = a;
    op.textContent = a;
    af.appendChild(op);
  }});
  if(keepAgent) af.value = keepAgent;
  document.querySelectorAll('[data-focus-mode]').forEach(function(btn){{
    btn.classList.toggle('active', btn.getAttribute('data-focus-mode') === focusMode);
  }});
  if(!storyItems.some(function(it){{ return it.sid === activeStepId; }}) && storyItems.length) activeStepId = storyItems[0].sid;
  selectStep(activeStepId, false);
}}
function highlightToc(el){{
  document.querySelectorAll('.toc-item').forEach(function(x){{ x.classList.remove('active'); }});
  el.classList.add('active');
}}
document.getElementById('q').addEventListener('input', render);
document.getElementById('eventFilter').addEventListener('change', render);
document.getElementById('agentFilter').addEventListener('change', render);
document.getElementById('sort').addEventListener('change', render);
document.querySelectorAll('[data-focus-mode]').forEach(function(btn){{
  btn.addEventListener('click', function(){{
    focusMode = btn.getAttribute('data-focus-mode') || 'focus';
    localStorage.setItem('qita_focus_mode', focusMode);
    render();
  }});
}});
fontDownBtn.addEventListener('click', function(){{ fontScale -= 0.1; applyFontScale(); }});
fontUpBtn.addEventListener('click', function(){{ fontScale += 0.1; applyFontScale(); }});
fontResetBtn.addEventListener('click', function(){{ fontScale = 1.1; applyFontScale(); }});
tabTraj.addEventListener('click', function(){{ activeTab = 'traj'; applyTab(); }});
tabManifest.addEventListener('click', function(){{ activeTab = 'manifest'; applyTab(); }});
document.addEventListener('click', function(e){{
  const t = e.target;
  if(!(t instanceof HTMLElement)) return;
  const callTarget = t.closest('[data-select-call="true"]');
  if(!callTarget) return;
  const sid = callTarget.getAttribute('data-call-step');
  const index = callTarget.getAttribute('data-call-index') || callTarget.getAttribute('data-call-badge');
  if(sid === null || index === null) return;
  selectCall(sid, Number(index), callTarget.getAttribute('data-call-scroll') === 'true');
}});
document.addEventListener('click', function(e){{
  const t = e.target;
  if(!(t instanceof HTMLElement)) return;
  if(!t.classList.contains('tgl')) return;
  const secEl = t.closest('section');
  if(!secEl) return;
  const hidden = secEl.style.display === 'none';
  secEl.style.display = hidden ? 'block' : 'none';
  t.textContent = hidden ? 'collapse' : 'expand';
}});
applyFontScale();
applyTab();
render();

/* SSE live stream */
let _sse = null;
let _liveStepCount = 0;
function startStream(){{
  if(_sse){{ _sse.close(); _sse = null; document.getElementById('streamBtn').textContent = 'live'; document.getElementById('streamBtn').style.borderColor = ''; return; }}
  const runId = location.pathname.split('/run/')[1] || '';
  // Use /api/live/ for running runs (file tailing), /api/stream/ for completed runs (replay)
  const runStatus = ((payload.manifest || {{}}).status || '').toLowerCase();
  const isLive = runStatus === 'running';
  const endpoint = isLive ? '/api/live/' : '/api/stream/';
  _sse = new EventSource(endpoint + runId);
  document.getElementById('streamBtn').textContent = isLive ? 'stop live' : 'stop stream';
  document.getElementById('streamBtn').style.borderColor = 'var(--ok)';
  _liveStepCount = 0;
  _sse.addEventListener('run_start', e => {{
    const d = JSON.parse(e.data);
    _addLiveBanner('Run started: ' + esc(String(d.run_id || '')), 'var(--ok)');
  }});
  _sse.addEventListener('step_start', e => {{
    const d = JSON.parse(e.data);
    _liveStepCount++;
    _addLiveBanner('Step ' + esc(String(d.step_id || _liveStepCount)) + ' started' + (d.agent_id ? ' agent=' + esc(String(d.agent_id)) : ''), 'var(--accent)');
  }});
  _sse.addEventListener('step_end', e => {{
    const d = JSON.parse(e.data);
    _addLiveBanner('Step ' + esc(String(d.step_id || '')) + ' completed', 'var(--kind-action)');
  }});
  _sse.addEventListener('handoff', e => {{
    const d = JSON.parse(e.data);
    const pl = (d && d.payload) || {{}};
    _addLiveBanner('Handoff: ' + esc(String(pl.from || '')) + ' → ' + esc(String(pl.to || '')), 'var(--kind-handoff)');
  }});
  _sse.addEventListener('delegate', e => {{
    const d = JSON.parse(e.data);
    const pl = (d && d.payload) || {{}};
    _addLiveBanner('Delegate → ' + esc(String(pl.agent_name || pl.agent || '')), 'var(--kind-delegation)');
  }});
  _sse.addEventListener('fanout', e => {{
    const d = JSON.parse(e.data);
    const pl = (d && d.payload) || {{}};
    _addLiveBanner('FanOut: ' + esc(String(pl.task_count || pl.num_tasks || 0)) + ' tasks', 'var(--kind-fanout)');
  }});
  _sse.addEventListener('phase', e => {{
    const d = JSON.parse(e.data);
    const phase = String(d.phase || '');
    const color = phaseColor(phase);
    _addLiveBanner('Phase: ' + esc(phase), color);
  }});
  _sse.addEventListener('run_end', e => {{
    const d = JSON.parse(e.data);
    _addLiveBanner('Run completed: ' + esc(String(d.step_count || '')) + ' steps, stop=' + esc(String(d.stop_reason || '')), 'var(--kind-done)');
    _sse.close(); _sse = null;
    document.getElementById('streamBtn').textContent = 'live';
    document.getElementById('streamBtn').style.borderColor = '';
    setTimeout(function(){{ location.reload(); }}, 1500);
  }});
  _sse.onerror = () => {{
    _sse.close(); _sse = null;
    document.getElementById('streamBtn').textContent = 'live';
    document.getElementById('streamBtn').style.borderColor = '';
  }};
}}
function _addLiveBanner(text, color){{
  const banner = document.createElement('div');
  banner.style.cssText = 'padding:6px 12px;margin:2px 0;border-radius:var(--radius-md);background:var(--surface-2);border:1px solid var(--line);font-size:12px;color:'+color+';animation:fadeIn 0.3s ease';
  banner.textContent = text;
  // Add to top of flow
  if(flow.firstChild){{
    flow.insertBefore(banner, flow.firstChild);
  }} else {{
    flow.appendChild(banner);
  }}
  // Auto-remove after 30 seconds to prevent DOM bloat
  setTimeout(function(){{ if(banner.parentNode) banner.parentNode.removeChild(banner); }}, 30000);
}}
</script>
</body></html>"""


def _json_for_script(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False)
    return (
        raw.replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _build_replay_records(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = payload.get("steps") or []
    events = payload.get("events") or []
    records: List[Dict[str, Any]] = []
    for ev in events:
        sid = ev.get("step_id")
        phase = str(ev.get("phase", "unknown"))
        node = str(ev.get("node") or (ev.get("payload") or {}).get("stage") or "")
        kind = _infer_kind(phase, node, ev.get("error"))
        step = _find_step(steps, sid) or {}
        body = {
            "event": ev,
            "observation": step.get("observation", {}),
            "decision": step.get("decision", {}),
            "actions": step.get("actions", []),
            "action_results": step.get("action_results", []),
            "critic_outputs": step.get("critic_outputs", []),
            "context": step.get("context", {}),
            "parser_diagnostics": step.get("parser_diagnostics", {}),
            "event_context": (ev.get("payload") or {}).get("context", {}),
            "event_diagnostics": (ev.get("payload") or {}).get("diagnostics", {}),
        }
        records.append(
            {
                "step_id": sid,
                "phase": phase,
                "node": node,
                "kind": kind,
                "ok": ev.get("ok"),
                "error": ev.get("error"),
                "ts": ev.get("ts"),
                "agent_id": step.get("agent_id"),
                "title": f"[step={sid}] {phase}",
                "body": body,
            }
        )
    records.append(
        {
            "step_id": None,
            "phase": "DONE",
            "node": "engine",
            "kind": "done",
            "ok": True,
            "error": None,
            "ts": None,
            "title": "[done] replay completed",
            "body": {"summary": (payload.get("manifest") or {}).get("summary", {})},
        }
    )
    return records


def _infer_kind(phase: str, node: str, error: Any) -> str:
    if error:
        return "error"
    key = f"{phase} {node}".lower()
    if "fanout" in key:
        return "fanout"
    if "handoff" in key:
        return "handoff"
    if "delegate" in key:
        return "delegation"
    if "parser" in key:
        return "parser"
    if "plan" in key:
        return "plan"
    if "state" in key or "observe" in key:
        return "observation"
    if "context" in key or "compact" in key:
        return "observation"
    if "memory" in key:
        return "memory"
    if "critic" in key or "reflect" in key:
        return "critic"
    if "action" in key or "tool" in key:
        return "action"
    if "decide" in key or "model" in key or "think" in key:
        return "thinking"
    if "done" in key or "stop" in key:
        return "done"
    return "event"


def _find_step(steps: List[Dict[str, Any]], step_id: Any) -> Optional[Dict[str, Any]]:
    for st in steps:
        if str(st.get("step_id")) == str(step_id):
            return st
    return None


def _render_replay_html(payload: Dict[str, Any], speed_ms: int) -> str:
    run_id = html.escape(str(payload.get("run_id", "")))
    records = json.dumps(_build_replay_records(payload), ensure_ascii=False)
    payload_json = _json_for_script(payload)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>qita replay {run_id}</title>
{_DESIGN_HEAD}
<style>
{_DESIGN_TOKENS}
body{{margin:0;background:var(--bg);font-family:var(--font-mono);color:var(--txt)}}
.wrap{{max-width:1260px;margin:0 auto;padding:20px}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;gap:10px;flex-wrap:wrap}}
.btn{{display:inline-block;border:1px solid var(--line);color:var(--txt);text-decoration:none;padding:6px 10px;border-radius:var(--radius-md);background:var(--surface-1);font-size:12px;cursor:pointer}}
.btn:hover{{border-color:var(--accent)}}
.terminal{{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);overflow:hidden}}
.bar{{background:var(--surface-2);border-bottom:1px solid var(--line);padding:8px 10px;color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:10px;align-items:center}}
.stats{{display:flex;gap:8px;flex-wrap:wrap;padding:8px 10px;border-bottom:1px solid var(--line);background:var(--surface-1)}}
.chip{{font-size:11px;color:var(--muted);border:1px solid var(--line-strong);border-radius:var(--radius-pill);padding:3px 8px;background:var(--surface-2)}}
.screen{{padding:14px;min-height:480px;display:grid;gap:10px}}
.replay-preview{{border:1px solid var(--line);background:var(--surface-1);border-radius:var(--radius-md);padding:10px}}
.replay-shot{{position:relative;border:1px solid var(--line);border-radius:var(--radius-md);overflow:hidden;background:var(--bg);min-height:180px;display:flex;align-items:center;justify-content:center}}
.replay-shot img{{max-width:100%;display:block}}
.replay-overlay{{position:absolute;inset:0;pointer-events:none}}
.replay-dot{{position:absolute;width:12px;height:12px;border-radius:var(--radius-pill);background:var(--err);border:2px solid var(--txt);transform:translate(-50%,-50%)}}
.card{{border:1px solid var(--line);background:var(--surface-1);border-radius:var(--radius-md);padding:10px}}
.ctitle{{font-size:12px;font-weight:700;margin-bottom:6px;display:flex;justify-content:space-between;gap:8px}}
.tag{{font-size:10px;border:1px solid var(--line);padding:1px 6px;border-radius:var(--radius-pill);color:var(--subtle)}}
.kind-plan{{border-color:var(--kind-plan)}} .kind-thinking{{border-color:var(--kind-thinking)}} .kind-action{{border-color:var(--kind-action)}}
.kind-parser{{border-color:var(--kind-parser)}} .kind-memory{{border-color:var(--kind-memory)}} .kind-observation{{border-color:var(--kind-observation)}} .kind-critic{{border-color:var(--kind-critic)}}
.kind-handoff{{border-color:var(--kind-handoff)}} .kind-delegation{{border-color:var(--kind-delegation)}} .kind-fanout{{border-color:var(--kind-fanout)}}
.kind-done{{border-color:var(--kind-done)}} .kind-error{{border-color:var(--kind-error)}}
.cbody{{white-space:pre-wrap;word-break:break-word;background:var(--surface-2);border:1px solid var(--line);padding:8px;border-radius:var(--radius-md);font-size:12px}}
.cursor{{display:inline-block;width:8px;height:16px;background:var(--accent);margin-left:3px;animation:blink 1s steps(2,start) infinite}}
.ctl{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.ctl input,.ctl select{{background:var(--surface-1);border:1px solid var(--line);color:var(--txt);padding:4px 6px;border-radius:var(--radius-sm);font-size:12px}}
@keyframes blink{{to{{visibility:hidden}}}}
</style></head><body>
<div class="wrap">
  <div class="top"><div>QitOS Replay · {run_id}</div><div><a class="btn" href="/run/{run_id}">view</a> <a class="btn" href="/">board</a> {_THEME_TOGGLE_HTML}</div></div>
  <div class="terminal">
    <div class="bar">
      <span>qita replay</span>
      <div class="ctl">
        <button class="btn" id="play" type="button">Pause</button>
        <button class="btn" id="step" type="button">Step +1</button>
        <button class="btn" id="reset" type="button">Reset</button>
        <label>Speed
          <select id="speed">
            <option value="100">fast</option>
            <option value="250">normal</option>
            <option value="{int(speed_ms)}" selected>default</option>
            <option value="800">slow</option>
          </select>
        </label>
        <label><input type="checkbox" id="onlyErr"/>only errors</label>
        <label>breakpoint phase<input id="bp" placeholder="ACTION,CRITIC" style="width:150px"/></label>
        <label>Progress <input id="progress" type="range" min="0" max="0" value="0"/></label>
      </div>
    </div>
    <div class="stats" id="stats"></div>
    <div class="stats"><div id="preview" style="width:100%"></div></div>
    <div class="screen" id="screen"></div>
  </div>
</div>
<script>
const records = {records};
const payload = {payload_json};
const screen = document.getElementById('screen');
const stats = document.getElementById('stats');
const preview = document.getElementById('preview');
const progress = document.getElementById('progress');
const speedEl = document.getElementById('speed');
const playBtn = document.getElementById('play');
const stepBtn = document.getElementById('step');
const resetBtn = document.getElementById('reset');
const onlyErr = document.getElementById('onlyErr');
const bp = document.getElementById('bp');
let i = 0;
let playing = true;
let timer = null;
progress.max = String(Math.max(records.length, 1));
function esc(s){{ return String(s).replace(/[&<>]/g, function(c){{ return {{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]; }}); }}
function truncateText(v, n){{
  const s = String(v === null || v === undefined ? '' : v);
  const lim = Number(n || 260);
  return s.length <= lim ? s : (s.slice(0, lim) + '...');
}}
function thoughtFromDecision(d){{
  if(!d || typeof d !== 'object') return '';
  const r = d.rationale;
  if(typeof r !== 'string') return '';
  return truncateText(r, 260);
}}
function modelResponseSummary(response){{
  if(!response || typeof response !== 'object') return '';
  const parts = [];
  if(response.provider) parts.push('provider=' + truncateText(response.provider, 40));
  if(response.model_name) parts.push('model=' + truncateText(response.model_name, 60));
  if(response.finish_reason) parts.push('finish=' + truncateText(response.finish_reason, 40));
  if(Array.isArray(response.tool_calls) && response.tool_calls.length) parts.push('tool_calls=' + response.tool_calls.length);
  const usage = response.usage;
  if(usage && typeof usage === 'object'){{
    if(usage.total_tokens !== undefined) parts.push('tokens=' + usage.total_tokens);
    else if(usage.prompt_tokens !== undefined || usage.completion_tokens !== undefined) parts.push('usage=' + (usage.prompt_tokens || 0) + '/' + (usage.completion_tokens || 0));
  }}
  return parts.join(' · ');
}}
function actionLabel(actions){{
  if(!Array.isArray(actions) || !actions.length) return '';
  const a = actions[0] || {{}};
  const tool = a.tool || a.name || a.action || a.type || 'action';
  const args = (a.args && typeof a.args === 'object') ? a.args : {{}};
  const ks = ['query','url','path','command','prompt','file'];
  const parts = [];
  for(const k of ks){{ if(k in args) parts.push(k + '=' + truncateText(args[k], 80)); }}
  return parts.length ? (tool + '(' + parts.join(', ') + ')') : String(tool);
}}
function stateSummary(observation){{
  if(!observation || typeof observation !== 'object') return 'No state.';
  const keep = [];
  const keys = Object.keys(observation);
  for(const k of keys){{
    if(['run_id','latency_ms','error_category','ts','step_id','phase'].includes(k)) continue;
    const v = observation[k];
    if(typeof v === 'object') continue;
    keep.push(k + '=' + truncateText(v, 60));
    if(keep.length >= 6) break;
  }}
  return keep.length ? keep.join(' · ') : 'No scalar state fields.';
}}
function observationSummary(actionResults){{
  const picked = pickObservation(actionResults);
  if(!picked || !picked.primary) return 'No observation.';
  const p = picked.primary;
  if(p.kind === 'terminal_output') return 'terminal output: ' + truncateText(p.body || '', 180);
  if(p.kind === 'terminal_screen') return 'terminal screen: ' + truncateText(p.body || '', 180);
  if(p.kind === 'error') return 'error: ' + truncateText(p.title || p.body || '', 180);
  return truncateText(p.title || p.body || JSON.stringify(p.raw || {{}}), 180);
}}
function criticSummary(cs){{
  if(!Array.isArray(cs) || !cs.length) return 'No critic output.';
  const c = cs[0];
  if(c && typeof c === 'object'){{
    const action = c.action ? ('action=' + c.action + ' ') : '';
    const reason = c.reason ? ('reason=' + truncateText(c.reason, 180)) : truncateText(JSON.stringify(c), 180);
    return action + reason;
  }}
  return truncateText(c, 180);
}}
function renderRecordBody(r){{
  const phase = String(r.phase||'').toLowerCase();
  if(String(r.node||'').toLowerCase() === 'context_history') {{
    const ctx = (r.body && r.body.event_context) || {{}};
    const stage = ctx.stage || 'context';
    const before = ctx.before_tokens;
    const after = ctx.after_tokens;
    const saved = ctx.saved_tokens;
    if(typeof before === 'number' && typeof after === 'number' && typeof saved === 'number') {{
      return '📦 <b>Context:</b> ' + esc(stage + ' · ' + before + ' -> ' + after + ' · saved ' + saved);
    }}
    return '📦 <b>Context:</b> ' + esc(stage);
  }}
  if(String(r.node||'').toLowerCase() === 'parser_diagnostics') {{
    const d = (r.body && (r.body.event_diagnostics || r.body.parser_diagnostics)) || {{}};
    const protocol = d.protocol ? ('<br/>🧬 <b>Protocol:</b> ' + esc(String(d.protocol))) : '';
    const fallback = d.fallback_used ? '<br/>↪️ <b>Fallback:</b> yes' : '';
    const extraction = d.extraction_mode ? ('<br/>🧲 <b>Extraction:</b> ' + esc(String(d.extraction_mode))) : '';
    const repair = d.repair_instruction ? ('<br/>🛠️ <b>Repair:</b> ' + esc(truncateText(d.repair_instruction, 220))) : '';
    const raw = d.raw_output_preview ? ('<br/>🧾 <b>Raw preview:</b> ' + esc(truncateText(d.raw_output_preview, 220))) : '';
    return '🧩 <b>Parser:</b> ' + esc(String(d.code || 'parser')) + ' · ' + esc(String(d.summary || 'Parser diagnostic')) + protocol + fallback + extraction + repair + raw;
  }}
  if(String(r.node||'').toLowerCase() === 'parser_result') {{
    const p = (r.body && r.body.event && r.body.event.payload) || {{}};
    const protocol = p.protocol ? (' · protocol=' + esc(String(p.protocol))) : '';
    const fallback = p.fallback_used ? ' · fallback=yes' : '';
    return '🧩 <b>Parser Result:</b> ' + esc(String(p.parser || 'parser')) + protocol + fallback + ' · mode=' + esc(String(p.parsed_mode || '-')) + ' · diagnostics=' + esc(String(!!p.has_diagnostics));
  }}
  if(phase.includes('state') || phase.includes('observe')) return '🧭 <b>State:</b> ' + esc(stateSummary(r.body && r.body.observation));
  if(r.body && r.body.context && r.body.context.input_tokens_total !== undefined) {{
    return '🧭 <b>State:</b> ' + esc(stateSummary(r.body && r.body.observation)) + '<br/>📏 <b>Context:</b> ' + esc(
      String(r.body.context.input_tokens_total || 0) + ' tokens · ' + String((((Number(r.body.context.occupancy_ratio) || 0) * 100).toFixed(1))) + '%'
    );
  }}
  if(r.kind === 'thinking') {{
    const eventPayload = (r.body && r.body.event && r.body.event.payload) || {{}};
    const raw = truncateText(eventPayload.raw_output || thoughtFromDecision(r.body && r.body.decision), 220);
    const summary = modelResponseSummary(eventPayload.model_response);
    return '🧠 <b>Thought:</b> ' + esc(raw) + (summary ? ('<br/>📦 <b>Model:</b> ' + esc(summary)) : '');
  }}
  if(r.kind === 'parser') return '🧩 <b>Parser:</b> ' + esc(truncateText(JSON.stringify((r.body && (r.body.event_diagnostics || r.body.parser_diagnostics || (r.body.event && r.body.event.payload) || {{}})), 220), 220));
  if(r.kind === 'action') return '🛠️ <b>Action:</b> ' + esc(actionLabel(r.body && r.body.actions)) + '<br/>✅ <b>Direct Observation:</b> ' + esc(observationSummary(r.body && r.body.action_results));
  if(r.kind === 'observation') return '✅ <b>Direct Observation:</b> ' + esc(observationSummary(r.body && r.body.action_results));
  if(r.kind === 'memory') return '💾 <b>Memory Update:</b> ' + esc('memory context updated');
  if(r.kind === 'handoff'){{
    const pl = (r.body && r.body.event && r.body.event.payload) || {{}};
    const from = pl.from || '?';
    const to = pl.to || '?';
    return '⇄ <b>Handoff:</b> ' + esc(from) + ' → ' + esc(to);
  }}
  if(r.kind === 'delegation'){{
    const pl = (r.body && r.body.event && r.body.event.payload) || {{}};
    const agent = pl.agent_name || pl.agent || '?';
    const task = pl.task ? truncateText(pl.task, 180) : '';
    return '↗ <b>Delegate:</b> → ' + esc(agent) + (task ? ' <span style="color:var(--muted)">' + esc(task) + '</span>' : '');
  }}
  if(r.kind === 'fanout'){{
    const pl = (r.body && r.body.event && r.body.event.payload) || {{}};
    const tc = pl.task_count || pl.num_tasks || 0;
    return '⊛ <b>FanOut:</b> ' + esc(String(tc)) + ' task(s) dispatched';
  }}
  if(r.kind === 'critic') return '🧪 <b>Critic:</b> ' + esc(criticSummary(r.body && r.body.critic_outputs));
  if(r.kind === 'done') return '🏁 <b>Done:</b> ' + esc(truncateText(JSON.stringify((r.body && r.body.summary) || {{}}), 220));
  if(r.error) return '❌ <b>Error:</b> ' + esc(truncateText(r.error, 220));
  return esc(truncateText(r.title || '', 220));
}}
function fmt(r){{
  const err = r.error ? '<span class="tag kind-error">error</span>' : '';
  const raw = esc(JSON.stringify(r.body, null, 2));
  const agentTag = r.agent_id ? '<span class="tag" style="border-color:var(--line-strong);color:var(--accent)">'+esc(r.agent_id)+'</span>' : '';
  const forkBtn = r.step_id !== undefined ? '<button class="btn fork-btn" data-step="'+esc(String(r.step_id))+'" style="font-size:10px;padding:2px 6px" title="Fork from this step">fork</button>' : '';
  return '<article class="card kind-'+esc(r.kind)+'">' +
    '<div class="ctitle"><span>'+esc(r.title)+'</span><span><span class="tag">'+esc(r.phase||'')+'</span> <span class="tag kind-'+esc(r.kind)+'">'+esc(r.kind)+'</span> '+agentTag+' '+err+' '+forkBtn+'</span></div>' +
    '<div class="cbody">'+renderRecordBody(r)+'</div>' +
    '<details style="margin-top:8px"><summary style="cursor:pointer;color:var(--muted)">Raw</summary><pre style="white-space:pre-wrap;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-md);padding:8px">'+raw+'</pre></details>' +
    '</article>';
}}
function buildPreview(r){{
  if(!r){{ preview.innerHTML = '<div class="muted">No visual step selected.</div>'; return; }}
  const step = Array.isArray(payload.steps) ? payload.steps.find(function(st){{ return String(st.step_id) === String(r.step_id); }}) : null;
  const assets = step && Array.isArray(step.visual_assets) ? step.visual_assets : [];
  const shot = assets.find(function(a){{ return a && typeof a === 'object' && a.kind === 'screenshot'; }});
  if(!shot || !shot.path){{ preview.innerHTML = '<div class="muted">No screenshot for this step.</div>'; return; }}
  let overlay = '';
  const actions = step && Array.isArray(step.actions) ? step.actions : [];
  if(actions.length){{
    const args = (actions[0] && typeof actions[0] === 'object' && typeof actions[0].args === 'object') ? actions[0].args : {{}};
    const x = Number(args.x);
    const y = Number(args.y);
    if(Number.isFinite(x) && Number.isFinite(y)){{
      overlay = '<div class="replay-overlay"><div class="replay-dot" style="left:' + x + 'px;top:' + y + 'px"></div></div>';
    }}
  }}
  preview.innerHTML = '<div class="replay-preview"><div style="font-size:12px;color:var(--muted);margin-bottom:8px">step ' + esc(String(r.step_id)) + ' · ' + esc(String(r.phase || '')) + '</div><div class="replay-shot"><img src="/asset?path=' + encodeURIComponent(String(shot.path)) + '" alt="replay screenshot"/>' + overlay + '</div></div>';
}}
function shouldShow(r){{
  if(onlyErr.checked && !r.error) return false;
  return true;
}}
function hitBreakpoint(r){{
  const raw = String(bp.value||'').trim();
  if(!raw) return false;
  const set = new Set(raw.split(',').map(function(x){{ return x.trim().toLowerCase(); }}).filter(Boolean));
  return set.has(String(r.phase||'').toLowerCase());
}}
function render(){{
  const shown = records.slice(0, i).filter(shouldShow);
  const errCount = shown.filter((r)=>!!r.error).length;
  const kindMap = new Map();
  for(const r of shown){{ kindMap.set(r.kind, (kindMap.get(r.kind)||0)+1); }}
  const kindText = Array.from(kindMap.entries()).slice(0,6).map(([k,v])=>k+':'+v).join(' · ') || '-';
  stats.innerHTML =
    '<span class="chip">shown: '+shown.length+'</span>' +
    '<span class="chip">errors: '+errCount+'</span>' +
    '<span class="chip">cursor: '+i+'/'+records.length+'</span>' +
    '<span class="chip">kinds: '+esc(kindText)+'</span>';
  screen.innerHTML = shown.map(fmt).join('') + (i >= records.length ? '<span class="cursor"></span>' : '');
  buildPreview(shown.length ? shown[shown.length - 1] : null);
  progress.value = String(i);
  window.scrollTo(0, document.body.scrollHeight);
}}
function tick(){{
  if(!playing) return;
  if(i >= records.length){{ render(); return; }}
  if(hitBreakpoint(records[i])){{ playing = false; playBtn.textContent = 'Play'; render(); return; }}
  i += 1;
  render();
  timer = setTimeout(tick, Number(speedEl.value || {int(speed_ms)}));
}}
playBtn.onclick = ()=>{{ playing = !playing; playBtn.textContent = playing ? 'Pause' : 'Play'; if(playing) tick(); }};
stepBtn.onclick = ()=>{{ i = Math.min(records.length, i + 1); render(); }};
resetBtn.onclick = ()=>{{ i = 0; render(); if(playing) tick(); }};
progress.oninput = ()=>{{ i = Number(progress.value || 0); render(); }};
speedEl.onchange = ()=>{{ if(playing){{ clearTimeout(timer); tick(); }} }};
onlyErr.onchange = render;
// Fork button handler — delegate clicks via event delegation on the screen
screen.addEventListener('click', function(e){{
  const btn = e.target.closest('.fork-btn');
  if(!btn) return;
  const stepId = btn.getAttribute('data-step');
  if(stepId === null) return;
  fetch('/api/fork/{run_id}/' + stepId, {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.dumps({{}})}})
  .then(function(r){{ return r.json(); }})
  .then(function(data){{
    if(data.error){{ alert('Fork failed: ' + data.error); return; }}
    const msg = 'Forked run created: ' + data.fork_run_id + '\\nView at /run/' + data.fork_run_id;
    alert(msg);
    window.open('/run/' + data.fork_run_id, '_blank');
  }})
  .catch(function(err){{ alert('Fork request failed: ' + err); }});
}});
tick();
</script></body></html>"""
