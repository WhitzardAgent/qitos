"""Thin qita CLI entrypoint that re-exports the canonical app implementation."""

from __future__ import annotations

from ._cli_app import (
    _build_run_diff,
    _build_handler,
    _cmd_export,
    _discover_runs,
    _render_board_html,
    _render_diff_html,
    _render_replay_html,
    _render_run_html,
    main,
)

__all__ = [
    "main",
    "_build_run_diff",
    "_build_handler",
    "_cmd_export",
    "_discover_runs",
    "_render_board_html",
    "_render_diff_html",
    "_render_replay_html",
    "_render_run_html",
]


if __name__ == "__main__":
    raise SystemExit(main())
