"""qita CLI: web board, trace viewer, replay, and export."""

from __future__ import annotations

import argparse
import html
import json
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
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet">"""

_DESIGN_TOKENS = """\
:root{
  --bg:#010102;--surface-1:#0f1011;--surface-2:#141516;--surface-3:#18191a;--surface-4:#191a1b;
  --accent:#5e6ad2;--accent-hover:#828fff;--accent-focus:#5e69d1;
  --txt:#f7f8f8;--muted:#d0d6e0;--subtle:#8a8f98;--tertiary:#62666d;
  --line:#23252a;--line-strong:#34343a;--line-tertiary:#3e3e44;
  --ok:#27a644;--err:#e5484d;--warn:#e5c100;
  --kind-thinking:#8b8fe0;--kind-action:#2da46a;--kind-observation:#5a8fbf;
  --kind-critic:#bfa04e;--kind-handoff:#bfa04e;--kind-delegation:#6b8fc4;
  --kind-fanout:#9b7fd4;--kind-parser:#bfa04e;--kind-memory:#3da89c;
  --kind-done:#c47070;--kind-error:#e5484d;--kind-other:#5a6578;--kind-plan:#7a80cc;
  --radius-xs:4px;--radius-sm:6px;--radius-md:8px;--radius-lg:12px;--radius-xl:16px;--radius-pill:9999px;
  --font-body:'Inter','SF Pro Display',-apple-system,system-ui,'Segoe UI',Roboto,sans-serif;
  --font-mono:'JetBrains Mono','Geist Mono',ui-monospace,'SF Mono',Menlo,monospace;
}"""

_DESIGN_FONT_BODY = "var(--font-body)"
_DESIGN_FONT_MONO = "var(--font-mono)"


def main(argv: Optional[List[str]] = None) -> int:
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

    args = parser.parse_args(argv)
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
                    "token_usage": summary.get("token_usage"),
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
    return {
        "run": str(run_dir),
        "run_id": run_dir.name,
        "manifest": manifest,
        "events": events,
        "steps": steps,
        "events_by_step": grouped_events,
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
    <div class="chip" id="summary">Loading...</div>
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
    el.innerHTML = `
      <div class="id">${r.id} ${agentBadge} ${handoffBadge}</div>
      <div class="meta">${liveIndicator}<span class="state">${status}</span> steps=${r.step_count||0} events=${r.event_count||0}</div>
      <div class="meta">stop=${r.stop_reason||''}</div>
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
}
document.getElementById('q').addEventListener('input', paint);
document.getElementById('status').addEventListener('change', paint);
document.getElementById('sort').addEventListener('change', paint);
document.getElementById('compareBtn').addEventListener('click', openCompare);
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


def _render_run_html(payload: Dict[str, Any], embedded: bool) -> str:
    run_id = html.escape(str(payload.get("run_id", "")))
    run_path = html.escape(str(payload.get("run", "")))
    manifest = html.escape(
        json.dumps(payload.get("manifest", {}), ensure_ascii=False, indent=2)
    )
    payload_json = _json_for_script(payload)
    buttons = ""
    if not embedded:
        buttons = (
            f'<a class="btn" href="/export/raw/{run_id}">export raw</a>'
            f'<a class="btn" href="/export/html/{run_id}">export html</a>'
            f'<a class="btn" href="/replay/{run_id}">replay</a>'
            f'<button class="btn" id="streamBtn" onclick="startStream()">live</button>'
            '<a class="btn ghost" href="/">board</a>'
        )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>qita run {run_id}</title>
{_DESIGN_HEAD}
<style>
{_DESIGN_TOKENS}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--txt);font-family:var(--font-body)}}
.wrap{{max-width:1460px;margin:0 auto;padding:18px}}
.top{{position:sticky;top:0;background:rgba(1,1,2,.9);backdrop-filter:blur(8px);padding:12px 0 14px;z-index:10;border-bottom:1px solid var(--line)}}
.title{{font-size:22px;font-weight:700;letter-spacing:-.4px}} .muted{{color:var(--muted);font-size:12px}}
.toolbar{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
.btn{{display:inline-block;border:1px solid var(--line);padding:7px 11px;border-radius:var(--radius-md);text-decoration:none;color:var(--txt);background:var(--surface-1);font-size:12px}}
.btn:hover{{border-color:var(--accent)}} .btn.ghost{{background:transparent}}
.layout{{display:grid;grid-template-columns:260px 1fr;gap:12px;margin-top:12px}}
.side{{position:sticky;top:84px;height:calc(100vh - 120px);overflow:auto;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:10px}}
.main{{min-width:0}}
.manifest{{background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:12px;margin-top:0}}
.tabs{{display:flex;gap:8px;margin-bottom:10px}}
.tab{{border:1px solid var(--line);background:var(--surface-1);color:var(--txt);padding:8px 12px;border-radius:var(--radius-pill);cursor:pointer;font-size:13px}}
.tab.active{{background:var(--surface-2);border-color:var(--accent)}}
.panel{{display:none}}
.panel.active{{display:block}}
.controls{{display:grid;grid-template-columns:1.2fr .8fr .8fr .8fr .8fr auto auto auto;gap:8px;margin:12px 0}}
.controls input,.controls select{{border:1px solid var(--line);background:var(--surface-1);color:var(--txt);border-radius:var(--radius-md);padding:8px 10px;font-size:12px}}
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
.vdot{{position:absolute;width:12px;height:12px;border-radius:var(--radius-pill);background:rgba(229,72,77,.85);border:2px solid var(--txt);transform:translate(-50%,-50%)}}
.vbox{{position:absolute;border:2px solid rgba(94,106,210,.9);background:rgba(94,106,210,.08);border-radius:var(--radius-xs)}}
.trow{{display:grid;grid-template-columns:82px 1fr 64px;gap:8px;align-items:center;margin:6px 0}}
.tlabel{{font-size:12px;color:var(--muted)}}
.track{{height:16px;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-pill);overflow:hidden;position:relative}}
.seg{{height:100%;display:inline-block}}
.heat0{{filter:brightness(0.85)}} .heat1{{filter:brightness(1)}} .heat2{{filter:brightness(1.15)}} .heat3{{filter:brightness(1.3)}}
.tdur{{font-size:11px;color:var(--muted);text-align:right}}
.context-chart{{display:grid;gap:10px}}
.context-head{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--muted)}}
.context-svg{{width:100%;height:auto;display:block;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg)}}
.context-axis{{stroke:var(--line-strong);stroke-width:1}}
.context-grid{{stroke:var(--line);stroke-width:1;stroke-dasharray:4 6}}
.context-line{{fill:none;stroke:var(--accent);stroke-width:3;stroke-linecap:round;stroke-linejoin:round}}
.context-fill{{fill:rgba(94,106,210,.12)}}
.context-point{{fill:var(--surface-1);stroke:var(--accent);stroke-width:2}}
.context-label{{fill:var(--subtle);font-size:11px}}
.compact-dot{{stroke:var(--surface-1);stroke-width:1.5}}
.compact-list{{display:grid;gap:6px}}
.compact-item{{display:grid;grid-template-columns:92px 1fr;gap:8px;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-md);padding:8px}}
.compact-step{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px}}
.compact-desc{{font-size:12px;color:var(--txt);word-break:break-word}}
.flow{{display:grid;grid-template-columns:1fr;gap:12px}}
@media (max-width:1180px){{.layout{{grid-template-columns:1fr}} .side{{position:relative;top:0;height:auto}} .controls{{grid-template-columns:1fr 1fr}}}}
.card{{break-inside:avoid;background:var(--surface-1);border:1px solid var(--line);border-radius:var(--radius-lg);padding:12px;margin:0 0 12px}}
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
      <div style="font-size:12px;color:var(--muted);margin-bottom:8px">Step Navigator</div>
      <div id="toc"></div>
    </aside>
    <section class="main">
      <div class="tabs">
        <button class="tab active" id="tabTraj" type="button">Traj</button>
        <button class="tab" id="tabManifest" type="button">Manifest</button>
      </div>
      <section class="panel active" id="panelTraj">
        <section class="overview" id="overview"></section>
        <div class="controls">
          <input id="q" placeholder="Filter by text in observation/decision/action/critic/events"/>
          <select id="eventFilter"><option value="">All events</option></select>
          <select id="agentFilter"><option value="">All agents</option></select>
          <select id="sort"><option value="asc">step asc</option><option value="desc">step desc</option></select>
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)"><input type="checkbox" id="showObs" checked/>obs</label>
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)"><input type="checkbox" id="showCritic" checked/>critic</label>
          <button class="btn" id="foldAll" type="button">Fold all</button>
          <button class="btn" id="fontDown" type="button">A-</button>
          <button class="btn" id="fontReset" type="button">A</button>
          <button class="btn" id="fontUp" type="button">A+</button>
        </div>
        <section class="timeline">
          <h4>visual timeline</h4>
          <div id="visualTimeline"></div>
        </section>
        <section class="timeline">
          <h4>phase timeline (gantt-like)</h4>
          <div id="timeline"></div>
        </section>
        <section class="timeline">
          <h4>context timeline</h4>
          <div id="contextTimeline"></div>
        </section>
        <section class="timeline">
          <h4>parser timeline</h4>
          <div id="parserTimeline"></div>
        </section>
        <section class="timeline">
          <h4>critic timeline</h4>
          <div id="criticTimeline"></div>
        </section>
        <section class="flow" id="flow"></section>
      </section>
      <section class="panel" id="panelManifest">
        <section class="manifest"><h4>manifest</h4><pre>{manifest}</pre></section>
      </section>
    </section>
  </div>
</div>
<script id="payload" type="application/json">{payload_json}</script>
<script>
const embedded = {str(bool(embedded)).lower()};
const payload = JSON.parse(document.getElementById('payload').textContent || '{{}}');
const steps = Array.isArray(payload.steps) ? payload.steps : [];
const eventsByStep = payload.events_by_step || {{}};
const flow = document.getElementById('flow');
const toc = document.getElementById('toc');
const timelineRoot = document.getElementById('timeline');
const visualTimelineRoot = document.getElementById('visualTimeline');
const contextTimelineRoot = document.getElementById('contextTimeline');
const parserTimelineRoot = document.getElementById('parserTimeline');
const criticTimelineRoot = document.getElementById('criticTimeline');
const overview = document.getElementById('overview');
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
    return truncateText(raw, 220);
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
  if(!item || typeof item !== 'object') return {{kind: 'tool_result', title: 'Observation', body: truncateText(String(item), 220), raw: item}};
  const flat = flattenResults([item]);
  const rows = [];
  for(const it of flat){{
    if(!it || typeof it !== 'object') continue;
    const title = it.title || it.name || '';
    const url = it.url || it.link || it.href || '';
    if(title && url) rows.push({{title:String(title), url:String(url)}});
  }}
  if(rows.length) return {{kind: 'search_results', title: 'Search Results', table: renderSearchTable(rows), raw: item}};
  if('error' in item && item.error) return {{kind: 'error', title: String(item.error), body: truncateText(String(item.content || ''), 220), raw: item}};
  return {{
    kind: 'tool_result',
    title: String(item.title || item.name || item.status || 'Tool Observation'),
    body: truncateText(JSON.stringify(item, null, 2), 1200),
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
      if(output && !terminalOutput) terminalOutput = {{kind: 'terminal_output', title: 'Terminal Output', body: truncateText(output, 2000), raw: terminal}};
      else if(!output && screen && !terminalScreen) terminalScreen = {{kind: 'terminal_screen', title: 'Terminal Screen', body: truncateText(screen, 2000), raw: terminal}};
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
  if(summary.kind === 'error') return '<div style="margin-bottom:12px;color:var(--err)">' + title + '<div>' + esc(String(summary.title || summary.body || 'Error')) + '</div></div>';
  return '<div style="margin-bottom:12px">' + title + '<pre>' + esc(String(summary.body || '')) + '</pre></div>';
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
function buildVisualTimeline(items){{
  const rows = Array.isArray(payload.visual_timeline) ? payload.visual_timeline : [];
  if(!rows.length){{
    visualTimelineRoot.innerHTML = '<div class="muted">No screenshot timeline recorded.</div>';
    return;
  }}
  const cards = [];
  for(const item of rows){{
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
  const label = firstActionLabel(st.actions || []);
  if(label) headerRows.push(kvRow('action taken', label));
  let htmlBlocks = headerRows.length ? kvBlock(headerRows) : '';
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
      preview = '<div class="vthumb" style="margin-top:8px"><img src="' + esc(assetHref(path)) + '" alt="visual asset"/>' + renderVisualOverlay(timelineItem) + '</div>';
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
function renderThought(decision, events, step){{
  const thought = extractThought(decision, events);
  const summary = renderModelResponseSummary(latestModelResponse(events), step);
  if(!thought) return (summary || '<div class="muted">No explicit thought.</div>');
  return '<div style="white-space:pre-wrap;line-height:1.6;background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius-md);padding:10px;color:var(--txt)">'+esc(thought)+'</div>' + summary;
}}
function renderAction(actions){{
  if(!Array.isArray(actions) || !actions.length) return '<div class="muted">No action.</div>';
  const first = actions[0] || {{}};
  const tool = first.tool || first.name || first.action || first.type || 'action';
  const args = (first.args && typeof first.args === 'object') ? first.args : (first.kwargs && typeof first.kwargs === 'object') ? first.kwargs : {{}};
  // Special rendering for delegate/fanout
  if(String(tool).toLowerCase() === 'delegate'){{
    const agent = args.agent_name || args.agent || '?';
    const task = args.task || '';
    return '<div style="font-size:13px;color:var(--kind-delegation)">↗ <b>Delegate:</b> → <b>' + esc(agent) + '</b>' + (task ? '<div style="margin-top:4px;font-size:12px;color:var(--muted)">' + esc(truncateText(task, 300)) + '</div>' : '') + '</div>';
  }}
  if(String(tool).toLowerCase() === 'fanout'){{
    const tasks = Array.isArray(args.tasks) ? args.tasks : [];
    const count = tasks.length || args.num_tasks || args.task_count || 0;
    const taskList = tasks.slice(0, 5).map(function(t){{ return '<div style="font-size:11px;color:var(--muted);padding:2px 0">· ' + esc(truncateText(String(t), 120)) + '</div>'; }}).join('');
    return '<div style="font-size:13px;color:var(--kind-fanout)">⊛ <b>FanOut:</b> ' + esc(String(count)) + ' task(s)' + (taskList ? '<div style="margin-top:4px">' + taskList + '</div>' : '') + '</div>';
  }}
  // General action with full params
  const label = firstActionLabel(actions);
  const argKeys = Object.keys(args);
  let paramHtml = '';
  if(argKeys.length > 1){{
    paramHtml = '<details style="margin-top:4px"><summary style="cursor:pointer;color:var(--muted);font-size:11px">Show all params (' + argKeys.length + ')</summary><div class="kv" style="margin-top:4px">' + argKeys.map(function(k){{ return kvRow(k, args[k]); }}).join('') + '</div></details>';  }}
  return '<div style="font-size:13px;color:var(--kind-critic)">🛠️ <b>Action:</b> ' + esc(label) + '</div>' + paramHtml;
}}
function renderMemoryUpdate(observeOut){{
  const mem = observeOut && typeof observeOut === 'object' ? observeOut.memory : null;
  if(!mem || typeof mem !== 'object') return '<div class="muted">No memory update.</div>';
  const rows = [];
  if('enabled' in mem) rows.push(kvRow('enabled', mem.enabled));
  if(Array.isArray(mem.records)) rows.push(kvRow('records', mem.records.length));
  if(typeof mem.summary === 'string' && mem.summary.trim()) rows.push(kvRow('summary', truncateText(mem.summary, 220)));
  return rows.length ? kvBlock(rows) : '<div class="muted">No memory update.</div>';
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
  if(diag.details) rows.push(kvRow('details', truncateText(diag.details, 260)));
  if(diag.expected_shape) rows.push(kvRow('expected', truncateText(diag.expected_shape, 220)));
  if(diag.repair_instruction) rows.push(kvRow('repair', truncateText(diag.repair_instruction, 220)));
  if(diag.salvage_summary) rows.push(kvRow('salvage', truncateText(diag.salvage_summary, 220)));
  if(diag.raw_output_preview) rows.push(kvRow('raw_preview', truncateText(diag.raw_output_preview, 240)));
  return kvBlock(rows);
}}
function renderCritic(data){{
  if(!Array.isArray(data) || !data.length) return '<div class="muted">No critic outputs.</div>';
  const cards = [];
  for(let i = 0; i < data.length; i++){{
    const c = data[i];
    if(!c || typeof c !== 'object') continue;
    const actionColors = {{continue:'#4ade80', stop:'#f87171', retry:'#fbbf24'}};
    const actionColor = actionColors[c.action] || '#94a3b8';
    const rows = [];
    if('action' in c) rows.push('<div style="display:flex;align-items:center;gap:6px"><span style="background:'+actionColor+';color:#000;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:600">'+esc(c.action)+'</span><span style="color:#94a3b8;font-size:11px">critic #'+(i+1)+'</span></div>');
    if('reason' in c) rows.push(kvRow('reason', c.reason));
    if(typeof c.score === 'number'){{
      const pct = Math.max(0, Math.min(100, Math.round(c.score * 100)));
      const barColor = pct >= 70 ? '#4ade80' : pct >= 40 ? '#fbbf24' : '#f87171';
      rows.push('<div style="display:flex;align-items:center;gap:8px"><span style="color:#8b949e;min-width:44px;font-size:12px">score</span><div style="flex:1;background:#1e293b;border-radius:3px;height:10px;max-width:120px"><div style="width:'+pct+'%;background:'+barColor+';border-radius:3px;height:10px"></div></div><span style="color:#c9d1d9;font-size:12px">'+c.score.toFixed(2)+'</span></div>');
    }}
    if(c.modified_prompt) rows.push(kvRow('modified_prompt', '<span style="color:#fbbf24">✎ prompt modified</span>'));
    if(c.instruction_patch) rows.push(kvRow('instruction_patch', '<span style="color:#7dd3fc">+'+esc(String(c.instruction_patch).substring(0,200))+'</span>'));
    if(c.state_patch && typeof c.state_patch === 'object') rows.push(kvRow('state_patch', '<span style="color:#c4b5fd">'+esc(JSON.stringify(c.state_patch).substring(0,200))+'</span>'));
    if(c.details) rows.push(kvRow('details', typeof c.details === 'string' ? c.details : JSON.stringify(c.details).substring(0,300)));
    cards.push('<div style="border:1px solid #21262d;border-radius:6px;padding:8px;margin-bottom:4px;background:#0d1117">'+(rows.length ? rows.join('') : kvRow('critic', JSON.stringify(c)))+'</div>');
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
    if(!evs.length) continue;
    const marks = evs.map(function(e){{ return parseTs(e.ts); }}).filter(function(x){{ return x !== null; }});
    const first = marks.length ? Math.min.apply(null, marks) : null;
    const last = marks.length ? Math.max.apply(null, marks) : null;
    const total = (first !== null && last !== null && last > first) ? (last - first) : null;
    const segs = [];
    for(let i=0;i<evs.length;i+=1){{
      const e = evs[i];
      const t0 = parseTs(e.ts);
      const t1 = (i+1<evs.length) ? parseTs(evs[i+1].ts) : t0;
      let dur = 100;
      if(t0 !== null && t1 !== null && t1 >= t0) dur = Math.max(20, t1 - t0);
      if(total && t0 !== null && t1 !== null && t1 >= t0){{
        dur = Math.max(2, ((t1 - t0) / total) * 100);
      }}
      let heat = 'heat0';
      if(dur > 40) heat = 'heat3';
      else if(dur > 20) heat = 'heat2';
      else if(dur > 8) heat = 'heat1';
      segs.push('<span class="seg '+heat+'" title="'+esc((e.phase||'unknown') + ' · ' + String(e.ts||''))+'" style="width:'+dur+'%;background:'+phaseColor(e.phase)+'"></span>');
    }}
    const d = total !== null ? (total + 'ms') : '-';
    rows.push('<div class="trow"><div class="tlabel">STEP '+it.sid+'</div><div class="track">'+segs.join('')+'</div><div class="tdur">'+d+'</div></div>');
  }}
  timelineRoot.innerHTML = rows.join('') || '<div class="muted">No event timing data.</div>';
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
  const actionColors = {{continue:'#4ade80', stop:'#f87171', retry:'#fbbf24'}};
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
    '<span style="color:#4ade80">● continue ' + continueCount + '</span>' +
    '<span style="color:#f87171">● stop ' + stopCount + '</span>' +
    '<span style="color:#fbbf24">● retry ' + retryCount + '</span>' +
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
function render(){{
  const q = (document.getElementById('q').value||'').toLowerCase();
  const eventFilter = document.getElementById('eventFilter').value;
  const agentFilter = document.getElementById('agentFilter').value;
  const sort = document.getElementById('sort').value;
  const showObs = document.getElementById('showObs').checked;
  const showCritic = document.getElementById('showCritic').checked;
  let items = steps.map(function(s){{ return {{step:s, sid:String(s.step_id), events:(eventsByStep[String(s.step_id)]||[])}}; }});
  if(eventFilter) items = items.filter(function(it){{ return it.events.some(function(e){{ return String(e.phase||'')===eventFilter; }}); }});
  if(agentFilter) items = items.filter(function(it){{ return (it.step.agent_id || '') === agentFilter; }});
  if(q) items = items.filter(function(it){{ return cardText(it.step,it.events).includes(q); }});
  items.sort(function(a,b){{ return sort==='desc' ? Number(b.sid)-Number(a.sid) : Number(a.sid)-Number(b.sid); }});
  paintOverview(items);
  buildVisualTimeline(items);
  buildTimeline(items);
  buildContextTimeline(items);
  buildParserTimeline(items);
  buildCriticTimeline(items);
  flow.innerHTML = '';
  toc.innerHTML = '';
  let lastAgentId = null;
  for(const it of items){{
    const d = it.step.decision || {{}};
    const obsInput = {{
      observe_output: it.step.observation || {{}},
      context: it.step.context || {{}},
    }};
    const card = document.createElement('article');
    card.className = 'card kind-' + inferPrimaryKind(it.events);
    card.id = 'step-' + it.sid;
    const agentId = it.step.agent_id || '';
    const agentBadge = agentId ? '<span style="display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;background:' + agentColor(agentId) + '22;border:1px solid ' + agentColor(agentId) + '66;color:' + agentColor(agentId) + ';margin-left:8px">' + esc(agentId) + '</span>' : '';
    let agentSwitch = '';
    if(agentId && lastAgentId && lastAgentId !== agentId){{
      agentSwitch = '<div style="padding:6px 12px;margin:0 0 4px;border-radius:var(--radius-md);background:var(--surface-2);border:1px solid var(--line);font-size:12px;color:var(--kind-handoff)">&#x26A1; Agent switched: <b>' + esc(lastAgentId) + '</b> &rarr; <b>' + esc(agentId) + '</b></div>';
    }}
    lastAgentId = agentId || lastAgentId;
    let h = agentSwitch + '<div class="card-head"><div class="step">STEP ' + it.sid + agentBadge + '</div><div class="muted">events ' + it.events.length + '</div></div>';
    // Multi-agent event banners
    const maHtml = renderMultiAgentEvent(it.events);
    if(maHtml) h += '<div style="margin-bottom:8px">' + maHtml + '</div>';
    if(showObs) h += sectionHtml('State', renderState(obsInput), obsInput, 'state', collapsedAll);
    h += sectionHtml('Prompt', renderPromptMetadata(it.step.prompt_metadata || {{}}), it.step.prompt_metadata || {{}}, 'prompt', collapsedAll);
    h += sectionHtml('Visual Assets', renderVisualAssets(it.step), {{visual_assets: it.step.visual_assets || [], observation_modalities: it.step.observation_modalities || [], model_input_modalities: it.step.model_input_modalities || [], model_input_visual_count: it.step.model_input_visual_count || 0}}, 'visual_assets', collapsedAll);
    h += sectionHtml('Thought', renderThought(d, it.events, it.step), d, 'thought', collapsedAll);
    h += sectionHtml('Parser Diagnostics', renderParserDiagnostics(it.step.parser_diagnostics || {{}}), it.step.parser_diagnostics || {{}}, 'parser', collapsedAll);
    h += sectionHtml('Action', renderAction(it.step.actions||[]), it.step.actions||[], 'action', collapsedAll);
    h += sectionHtml('Direct Observation', renderDirectObservation(it.step.action_results||[]), it.step.action_results||[], 'direct_observation', collapsedAll);
    h += sectionHtml('Memory Update', renderMemoryUpdate(obsInput.observe_output || {{}}), obsInput.observe_output || {{}}, 'memory', collapsedAll);
    if(showCritic) h += sectionHtml('Critic', renderCritic(it.step.critic_outputs||[]), it.step.critic_outputs||[], 'critic', collapsedAll);
    h += sectionHtml('Trace Events', renderEvents(it.events), it.events, 'events', true);
    card.innerHTML = h;
    flow.appendChild(card);
    const b = document.createElement('button');
    b.className = 'toc-item';
    b.type = 'button';
    const tocLabel = 'STEP ' + it.sid + (agentId ? ' [' + agentId + ']' : '');
    b.textContent = tocLabel;
    b.onclick = function(){{ const target = document.getElementById('step-' + it.sid); if(target) target.scrollIntoView({{behavior:'smooth',block:'start'}}); highlightToc(b); }};
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
}}
function highlightToc(el){{
  document.querySelectorAll('.toc-item').forEach(function(x){{ x.classList.remove('active'); }});
  el.classList.add('active');
}}
document.getElementById('q').addEventListener('input', render);
document.getElementById('eventFilter').addEventListener('change', render);
document.getElementById('agentFilter').addEventListener('change', render);
document.getElementById('sort').addEventListener('change', render);
document.getElementById('showObs').addEventListener('change', render);
document.getElementById('showCritic').addEventListener('change', render);
document.getElementById('foldAll').addEventListener('click', function(){{
  collapsedAll = !collapsedAll;
  document.getElementById('foldAll').textContent = collapsedAll ? 'Expand all' : 'Fold all';
  render();
}});
fontDownBtn.addEventListener('click', function(){{ fontScale -= 0.1; applyFontScale(); }});
fontUpBtn.addEventListener('click', function(){{ fontScale += 0.1; applyFontScale(); }});
fontResetBtn.addEventListener('click', function(){{ fontScale = 1.1; applyFontScale(); }});
tabTraj.addEventListener('click', function(){{ activeTab = 'traj'; applyTab(); }});
tabManifest.addEventListener('click', function(){{ activeTab = 'manifest'; applyTab(); }});
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
  document.getElementById('streamBtn').style.borderColor = '#4ade80';
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
.replay-dot{{position:absolute;width:12px;height:12px;border-radius:var(--radius-pill);background:rgba(229,72,77,.85);border:2px solid var(--txt);transform:translate(-50%,-50%)}}
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
  <div class="top"><div>QitOS Replay · {run_id}</div><div><a class="btn" href="/run/{run_id}">view</a> <a class="btn" href="/">board</a></div></div>
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
  return '<article class="card kind-'+esc(r.kind)+'">' +
    '<div class="ctitle"><span>'+esc(r.title)+'</span><span><span class="tag">'+esc(r.phase||'')+'</span> <span class="tag kind-'+esc(r.kind)+'">'+esc(r.kind)+'</span> '+agentTag+' '+err+'</span></div>' +
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
tick();
</script></body></html>"""
