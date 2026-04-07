"""Terminus-style defensive code-security audit agent for large repositories."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from qitos import AgentModule, Decision, StateSchema, StopReason, ToolRegistry
from qitos.kit import (
    CodingToolSet,
    ReportToolSet,
    SecurityAuditToolSet,
    SendTerminalKeys,
    TokenBudgetSummaryHistory,
    TmuxEnv,
)
from qitos.models import OpenAICompatibleModel

# TASK = (
#     "Review this repository for evidence-backed high-severity security risks "
#     "related to opening untrusted files. "
#     "Prioritize file parsing, modelines, command execution, plugin loading, "
#     "unsafe boundary crossings, and any other "
#     "file-open paths that could lead to severe impact. "
#     "Produce a ranked markdown report with concrete file/line evidence."
# )

TASK = "Somebody told me in this repository there is an RCE 0-day when you open a file. Find it."

WORKSPACE = Path("/Users/morinop/coding/yoga_framework/playground/vim")
SESSION_NAME = "qitos_whitzard"
PARSER_FORMAT = os.getenv("QITOS_TERMINUS_FORMAT", "").strip().lower()
MODEL_NAME = os.getenv("QITOS_MODEL", "MiniMax-M2.5")
MODEL_BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
MAX_STEPS = 300
MAX_TERMINAL_BYTES = 10000

WHITZARD_BASE_PROMPT = """# IDENTITY & EXPERTISE

You are **Whitzard**, an elite defensive code-security audit agent. You operate \
on a Linux terminal with direct repository tool access.

You possess deep expertise in:
- OWASP Top 10, CWE/SANS Top 25, and MITRE ATT&CK technique mapping
- Language-specific vulnerability patterns (C/C++ memory corruption, \
Python injection, JS prototype pollution, Java deserialization, etc.)
- Supply-chain attack vectors, dependency confusion, and typosquatting
- Authentication/authorization bypass, cryptographic misuse, and secrets exposure
- File format exploitation, path traversal, symlink attacks, and parser differentials

You think like an attacker but report like a defender.

---

# MISSION

Perform a **read-only, evidence-first** security audit of the target repository. \
Your goal is to identify vulnerabilities that a real attacker could exploit, \
ranked by actual exploitability and impact — not theoretical possibility.

**Primary focus for this engagement:** Security risks arising from opening or \
processing untrusted files — including but not limited to file parsing, modeline \
interpretation, command execution triggered by file content, plugin/extension \
auto-loading, unsafe deserialization, and trust boundary violations.

---

# THREAT MODEL

Assume the following attacker profile:
- **Attacker capability:** Can craft arbitrary file content and file names; \
can control repository content (e.g., malicious PR, compromised dependency)
- **Attack surface:** Any code path reachable by opening, reading, parsing, \
or rendering a file
- **Impact tiers (use for severity rating):**
  - **CRITICAL**: Remote Code Execution, arbitrary command execution, \
full system compromise
  - **HIGH**: Arbitrary file read/write, privilege escalation, authentication \
bypass, secrets exfiltration
  - **MEDIUM**: Partial information disclosure, denial of service, limited \
injection without full control
  - **LOW**: Information leak of non-sensitive data, minor logic errors \
with limited security impact

---

# EVIDENCE STANDARD

A finding is **valid** only when ALL of the following are present:
1. **Sink identification** — the exact dangerous function/API call \
(file path + line number)
2. **Source-to-sink trace** — how attacker-controlled input reaches the sink \
(data flow, even if abbreviated)
3. **Exploitability argument** — a concrete explanation of how an attacker \
would trigger this in practice
4. **Impact statement** — what the attacker gains upon successful exploitation

**Reject a finding** if:
- The dangerous call is unreachable from any external input
- Input is properly validated/sanitized before reaching the sink
- The code path requires privileges the attacker model does not have
- You cannot articulate a realistic attack scenario

---

# AUDIT METHODOLOGY

Execute the following phases. Each phase builds on the previous. \
**Do not skip phases.** Adapt depth based on repository size and complexity.

## Phase 1: Reconnaissance (1–2 steps)
- Inventory the repository structure, languages, and frameworks
- Identify the build system, dependency manifests, and configuration files
- Determine the application type (CLI tool, server, library, editor plugin, etc.)
- Estimate repository scale to calibrate depth of subsequent phases

## Phase 2: Attack Surface Mapping (2–3 steps)
- Enumerate all entrypoints: main functions, request handlers, CLI parsers, \
file-open hooks, event handlers, plugin load points
- Map trust boundaries: where does external/untrusted data enter the system?
- Identify file-processing pipelines: open → read → parse → interpret → execute
- Flag any dynamic code evaluation patterns (eval, exec, system, popen, \
dlopen, deserialize, template render)

## Phase 3: Sink Analysis & Taint Tracking (3–5 steps)
For each identified sink category, systematically search and trace:

| Sink Category | Example Patterns |
|---|---|
| Command injection | system(), popen(), exec*(), subprocess, backtick, os.system |
| Code injection | eval(), exec(), Function(), compile(), __import__() |
| File operations | open(), fopen(), readFile(), path construction from user input |
| Deserialization | pickle.loads, yaml.load, unserialize(), ObjectInputStream |
| SQL/NoSQL injection | Raw query construction, string interpolation in queries |
| Path traversal | Concatenation with user input without canonicalization |
| Secrets/credentials | Hardcoded keys, tokens, passwords; insecure storage |
| Crypto misuse | Weak algorithms, static IVs, predictable randomness |
| Buffer issues (C/C++) | Unchecked memcpy, strcpy, sprintf, stack buffers with external size |
| Plugin/modeline exec | Auto-sourced config, modeline parsing, dynamic plugin loading |

For each potential hit:
1. Use grep_files or terminal grep to locate pattern occurrences
2. Use read_file_range to examine surrounding context (±30 lines)
3. Trace the data flow backward from sink to source
4. Determine if sanitization/validation exists on the path
5. Record or discard based on evidence standard above

## Phase 4: Dependency & Configuration Audit (1–2 steps)
- Check dependency manifests for known-vulnerable versions
- Review configuration files for insecure defaults
- Check for development secrets committed to the repository

## Phase 5: Cross-Cutting Concerns (1–2 steps)
- Race conditions in file operations (TOCTOU)
- Error handling that leaks sensitive information
- Logging of sensitive data
- Missing security headers or transport security

## Phase 6: Finding Consolidation & Report Generation (1–2 steps)
- Deduplicate findings
- Re-verify top findings by re-reading the exact code
- Assign final severity using the impact tier definitions
- Generate the ranked markdown report
- Confirm report was written successfully

---

# REASONING DISCIPLINE

At each step, your analysis must contain:
1. What was learned from the previous action's results
2. Confidence assessment — confirmed vs. needs more evidence
3. Coverage tracking — which phases/sink categories have been covered

Your plan must contain:
1. Specific next action with rationale
2. Expected outcome
3. Fallback if expected outcome is not achieved

**Anti-patterns to avoid:**
- Do NOT grep and immediately record without reading surrounding code
- Do NOT report theoretical vulnerabilities in dead/unreachable code
- Do NOT inflate severity
- Do NOT waste steps repeating searches
- Do NOT mark task complete without a written report file

---

# TOOL USAGE STRATEGY

**Prefer direct tools over terminal commands** when available:
- Use glob_files for file discovery instead of find
- Use grep_files for pattern search instead of grep
- Use read_file_range for targeted code inspection instead of cat/head
- Use audit_* tools for structured security analysis
- Use report_* tools for finding recording and report generation

**Use terminal commands** when you need:
- Complex piped commands that tools cannot replicate
- Build system / git history inspection
- Anything not covered by available tools

**Efficiency rules:**
- Batch related searches in a single step
- Read focused line ranges (50–100 lines) rather than entire files
- For large files, grep first, then read targeted ranges
"""


# ═══════════════════════════════════════════════════════════════════════════════
# State & Agent (unchanged logic, updated references)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class WhitzardState(StateSchema):
    parser_format: str = PARSER_FORMAT
    terminal_output: str = ""
    terminal_screen: str = ""
    parser_feedback: str = ""
    timeout_feedback: str = ""
    pending_completion: bool = False
    last_analysis: str = ""
    last_plan: str = ""
    markers: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    hotspots: list[dict[str, Any]] = field(default_factory=list)
    reviewed_files: list[str] = field(default_factory=list)
    final_report_path: str = ""


class WhitzardAgent(AgentModule[WhitzardState, dict[str, Any], dict[str, Any]]):
    name = "whitzard"

    def __init__(self, llm: Any, workspace_root: str):
        registry = ToolRegistry()
        registry.register(SendTerminalKeys())

        coding = CodingToolSet(
            workspace_root=workspace_root,
            include_notebook=False,
            enable_lsp=False,
            enable_tasks=False,
            enable_web=False,
            expose_legacy_aliases=True,
            expose_modern_names=False,
            profile="codebase",
        )
        for item in (
            coding.glob_files,
            coding.grep_files,
            coding.read_file_range,
            coding.read_file,
            coding.list_files,
        ):
            registry.register(item)

        registry.register_toolset(
            SecurityAuditToolSet(
                workspace_root=workspace_root,
                include_external=False,
                max_matches=80,
            ),
            namespace="",
        )
        registry.register_toolset(
            ReportToolSet(workspace_root=workspace_root), namespace=""
        )

        protocol_override = None
        if PARSER_FORMAT == "json":
            protocol_override = "terminus_json_v1"
        elif PARSER_FORMAT == "xml":
            protocol_override = "terminus_xml_v1"
        history = TokenBudgetSummaryHistory(
            llm=llm, max_tokens=14000, keep_last=8, hard_window=64
        )
        super().__init__(
            tool_registry=registry,
            llm=llm,
            model_protocol=protocol_override,
            history=history,
        )
        self.workspace_root = workspace_root

    # ── lifecycle ──────────────────────────────────────────────────────────

    def init_state(self, task: str, **kwargs: Any) -> WhitzardState:
        return WhitzardState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            parser_format=str(kwargs.get("parser_format", PARSER_FORMAT)),
        )

    def base_persona_prompt(self, state: WhitzardState) -> str:
        _ = state
        return WHITZARD_BASE_PROMPT

    def task_policy_prompt(self, state: WhitzardState) -> str:
        _ = state
        return (
            "Audit flow:\n"
            "1. inventory repository structure\n"
            "2. identify entrypoints and trust boundaries\n"
            "3. inspect hotspots with focused code reads\n"
            "4. only record evidence-backed findings with file and line references\n"
            "5. write the final markdown report before requesting completion"
        )

    def extra_instructions_prompt(self, state: WhitzardState) -> str:
        _ = state
        return (
            "Repository-local audit constraints:\n"
            "- Stay inside the workspace root unless the user explicitly broadens scope\n"
            "- Prefer repo-local codebase and audit tools over terminal wandering\n"
            "- Use terminal commands mainly for repository inspection, build checks, grep, or git history\n"
            "- When a shell command should execute immediately, call send_terminal_keys with submit=true\n"
            "- Keep analysis and plan concrete: say what evidence was found, what phase is covered, and why the next step is justified"
        )

    def prepare(self, state: WhitzardState) -> str:
        observation = getattr(self, "_runtime_observation", None)
        terminal = self._extract_terminal_payload(observation) or {
            "output": state.terminal_output,
            "screen": state.terminal_screen,
            "timestamp": None,
        }
        terminal_output = self._limit_output_length(
            terminal.get("output") or terminal.get("screen") or state.terminal_output
        )
        terminal_screen = self._limit_output_length(
            terminal.get("screen") or terminal_output
        )

        # ── completion-confirmation prompt ─────────────────────────────
        if state.pending_completion:
            summary = self._render_findings(state.findings)
            return (
                f"Audit task:\n{state.task}\n\n"
                f"Current terminal state:\n"
                f"{terminal_output or terminal_screen}\n\n"
                f"Current findings:\n{summary}\n\n"
                f"Report path: "
                f"{state.final_report_path or '(missing)'}\n\n"
                "Are you sure you want to mark the task as complete? "
                "Only confirm completion if the report has already "
                "been written and the findings are ranked."
            )

        # ── normal audit prompt ────────────────────────────────────────
        lines = [
            f"Audit task:\n{state.task}",
            "",
            f"Workspace root:\n{self.workspace_root}",
            "",
            f"Step: {state.current_step}/{state.max_steps}",
            "",
            f"Current terminal state:\n"
            f"{terminal_output or terminal_screen or '(no output yet)'}",
        ]

        # phase progress tracker
        lines.extend(["", self._render_phase_progress(state)])

        if state.findings:
            lines.extend(
                [
                    "",
                    f"Confirmed findings ({len(state.findings)}):\n"
                    f"{self._render_findings(state.findings)}",
                ]
            )
        if state.hotspots:
            lines.extend(
                [
                    "",
                    f"Top hotspots:\n{self._render_hotspots(state.hotspots)}",
                ]
            )
        if state.reviewed_files:
            lines.extend(
                [
                    "",
                    "Recently reviewed files:",
                    *[f"- {p}" for p in state.reviewed_files[-8:]],
                ]
            )
        if state.final_report_path:
            lines.extend(
                [
                    "",
                    f"Current report path:\n{state.final_report_path}",
                ]
            )
        if state.last_plan:
            lines.extend(
                [
                    "",
                    f"Previous plan:\n{state.last_plan}",
                ]
            )

        # budget warning
        remaining = state.max_steps - state.current_step
        if remaining <= 4 and not state.final_report_path:
            lines.extend(
                [
                    "",
                    f"⚠ BUDGET WARNING: Only {remaining} steps remaining. "
                    f"Begin report generation NOW if you have not already.",
                ]
            )

        return "\n".join(lines)

    def reduce(
        self,
        state: WhitzardState,
        observation: dict[str, Any],
        decision: Decision[dict[str, Any]],
    ) -> WhitzardState:
        terminal = self._extract_terminal_payload(observation) or {}
        latest_output = str(terminal.get("output") or terminal.get("screen") or "")
        latest_screen = str(terminal.get("screen") or latest_output)

        state.terminal_output = self._limit_output_length(latest_output)
        state.terminal_screen = self._limit_output_length(latest_screen)

        meta = decision.meta if isinstance(decision.meta, dict) else {}
        state.last_analysis = str(
            meta.get("analysis") or decision.rationale or state.last_analysis
        )
        state.last_plan = str(meta.get("plan") or state.last_plan)

        parser_feedback = str(meta.get("parser_feedback") or "").strip()
        parser_warning = str(meta.get("parser_warning") or "").strip()
        state.timeout_feedback = self._extract_timeout_feedback(observation)

        if meta.get("parser_error"):
            state.parser_feedback = parser_feedback or parser_warning
            state.pending_completion = False
        else:
            state.parser_feedback = parser_warning

        self._consume_action_results(state, observation)

        if meta.get("task_complete_requested"):
            if not state.final_report_path:
                state.pending_completion = False
                state.parser_feedback = (
                    "Generate the final markdown report before "
                    "requesting task completion."
                )
            elif state.pending_completion:
                final_result = (
                    f"Completed defensive audit with "
                    f"{len(state.findings)} finding(s). "
                    f"Top hotspots: "
                    f"{', '.join(h.get('file', '?') for h in state.hotspots[:3]) or 'none'}. "
                    f"Report: {state.final_report_path}"
                )
                state.set_stop(StopReason.SUCCESS, final_result=final_result)
            else:
                state.pending_completion = True
                state.parser_feedback = ""
        elif decision.mode == "act":
            state.pending_completion = False

        state.markers.append(
            {
                "step": state.current_step,
                "timestamp": terminal.get("timestamp"),
                "session_alive": terminal.get("session_alive"),
                "analysis": state.last_analysis,
                "plan": state.last_plan,
                "finding_count": len(state.findings),
                "report_path": state.final_report_path,
            }
        )

        state.markers = state.markers[-100:]
        state.findings = state.findings[-25:]
        state.hotspots = state.hotspots[:10]
        state.reviewed_files = state.reviewed_files[-20:]
        return state

    def should_stop(self, state: WhitzardState) -> bool:
        return bool(state.stop_reason)

    # ── helpers ────────────────────────────────────────────────────────────

    def _consume_action_results(
        self, state: WhitzardState, observation: dict[str, Any]
    ) -> None:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        for item in action_results:
            if not isinstance(item, dict):
                continue
            data = item.get("data")
            if not isinstance(data, dict):
                continue

            findings = data.get("findings")
            if isinstance(findings, list):
                for finding in findings:
                    if isinstance(finding, dict):
                        self._add_finding(state, finding)

            hotspot_rows = data.get("hotspots")
            if isinstance(hotspot_rows, list):
                state.hotspots = [r for r in hotspot_rows if isinstance(r, dict)][:10]

            entrypoints = data.get("entrypoints")
            if isinstance(entrypoints, list):
                for row in entrypoints[:10]:
                    if isinstance(row, dict) and isinstance(row.get("file"), str):
                        state.reviewed_files.append(str(row["file"]))

            finding = data.get("finding")
            if isinstance(finding, dict):
                self._add_finding(state, finding)

            output_file = data.get("output_file")
            if isinstance(output_file, str) and output_file.strip():
                state.final_report_path = output_file.strip()

            self._capture_reviewed_files(state, item)

    def _capture_reviewed_files(
        self, state: WhitzardState, result: dict[str, Any]
    ) -> None:
        for key in ("path", "file", "target_file"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                state.reviewed_files.append(value.strip())
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("path", "file"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    state.reviewed_files.append(value.strip())
            matches = data.get("matches")
            if isinstance(matches, list):
                for match in matches[:10]:
                    if isinstance(match, dict) and isinstance(match.get("path"), str):
                        state.reviewed_files.append(str(match["path"]))
            files = data.get("files")
            if isinstance(files, list):
                for path in files[:10]:
                    if isinstance(path, str):
                        state.reviewed_files.append(path)

    def _add_finding(self, state: WhitzardState, finding: dict[str, Any]) -> None:
        file_path = str(finding.get("file") or finding.get("affected_component") or "")
        line = finding.get("line") or ""
        evidence = str(finding.get("evidence") or finding.get("description") or "")
        fingerprint = (
            str(finding.get("title") or ""),
            str(finding.get("severity") or ""),
            file_path,
            str(line),
            evidence[:120],
        )
        seen = {
            (
                str(item.get("title") or ""),
                str(item.get("severity") or ""),
                str(item.get("file") or item.get("affected_component") or ""),
                str(item.get("line") or ""),
                str(item.get("evidence") or item.get("description") or "")[:120],
            )
            for item in state.findings
        }
        if fingerprint not in seen:
            state.findings.append(dict(finding))
        if file_path:
            state.reviewed_files.append(file_path)

    def _extract_terminal_payload(self, observation: Any) -> Dict[str, Any]:
        if not isinstance(observation, dict):
            return {}
        env_payload = observation.get("env")
        if not isinstance(env_payload, dict):
            return {}
        env_observation = env_payload.get("observation")
        if not isinstance(env_observation, dict):
            return {}
        data = env_observation.get("data")
        if not isinstance(data, dict):
            return {}
        terminal = data.get("terminal")
        return terminal if isinstance(terminal, dict) else {}

    def _limit_output_length(
        self, output: str, max_bytes: int = MAX_TERMINAL_BYTES
    ) -> str:
        encoded = output.encode("utf-8", errors="ignore")
        if len(encoded) <= max_bytes:
            return output
        portion = max_bytes // 2
        first = encoded[:portion].decode("utf-8", errors="ignore")
        last = encoded[-portion:].decode("utf-8", errors="ignore")
        omitted = len(encoded) - len(first.encode("utf-8")) - len(last.encode("utf-8"))
        return (
            f"{first}\n"
            f"[... output limited to {max_bytes} bytes; "
            f"{omitted} interior bytes omitted ...]\n"
            f"{last}"
        )

    def _extract_timeout_feedback(self, observation: dict[str, Any]) -> str:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        for item in action_results:
            if isinstance(item, dict):
                message = str(item.get("error") or item.get("message") or "")
                if "timed out" in message.lower():
                    return message
        return ""

    def _render_findings(self, findings: List[dict[str, Any]]) -> str:
        rows = []
        for item in findings[-6:]:
            title = str(item.get("title") or "finding")
            severity = str(item.get("severity") or "info")
            file_path = str(item.get("file") or item.get("affected_component") or "?")
            line = item.get("line")
            suffix = f":{line}" if line not in (None, "") else ""
            cwe = item.get("cwe") or ""
            cwe_tag = f" (CWE: {cwe})" if cwe else ""
            rows.append(
                f"- [{severity.upper()}] {title}{cwe_tag} " f"@ {file_path}{suffix}"
            )
        if len(findings) > 6:
            rows.insert(
                0,
                f"(showing last 6 of {len(findings)} findings)",
            )
        return "\n".join(rows) or "(none yet)"

    def _render_hotspots(self, hotspots: List[dict[str, Any]]) -> str:
        rows = []
        for item in hotspots[:6]:
            categories = ", ".join(item.get("categories", []))
            rows.append(
                f"- {item.get('file', '?')} "
                f"(score={item.get('score', '?')}, "
                f"categories={categories})"
            )
        return "\n".join(rows) or "(none yet)"

    def _render_phase_progress(self, state: WhitzardState) -> str:
        """Render a lightweight phase-progress tracker so the LLM knows
        where it stands in the methodology."""
        has_inventory = len(state.reviewed_files) > 0
        has_hotspots = len(state.hotspots) > 0
        has_findings = len(state.findings) > 0
        has_report = bool(state.final_report_path)

        phases = [
            ("Phase 1: Reconnaissance", has_inventory),
            ("Phase 2: Attack Surface Mapping", has_hotspots),
            ("Phase 3: Sink Analysis & Taint Tracking", has_findings),
            (
                "Phase 4: Dependency & Config Audit",
                has_findings and len(state.findings) >= 2,
            ),
            (
                "Phase 5: Cross-Cutting Concerns",
                has_findings and len(state.findings) >= 3,
            ),
            ("Phase 6: Report Generation", has_report),
        ]

        lines = ["Audit phase progress:"]
        for label, done in phases:
            marker = "✅" if done else "⬜"
            lines.append(f"  {marker} {label}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Model builder & bootstrap
# ═══════════════════════════════════════════════════════════════════════════════


def build_model() -> OpenAICompatibleModel:
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "Set OPENAI_API_KEY or QITOS_API_KEY before running this example."
        )
    return OpenAICompatibleModel(
        model=MODEL_NAME,
        api_key=api_key,
        base_url=MODEL_BASE_URL,
        temperature=0.2,
        max_tokens=2048,
    )


def main() -> None:
    env = TmuxEnv(
        workspace_root=str(WORKSPACE),
        session_name=SESSION_NAME,
        auto_kill=True,
    )
    agent = WhitzardAgent(llm=build_model(), workspace_root=str(WORKSPACE))

    result = agent.run(
        task=TASK,
        workspace=str(WORKSPACE),
        env=env,
        max_steps=MAX_STEPS,
        parser_format=PARSER_FORMAT,
        return_state=True,
    )

    print("=" * 60)
    print("WHITZARD AUDIT COMPLETE")
    print("=" * 60)
    print(f"  workspace:    {WORKSPACE}")
    print(f"  stop_reason:  {result.state.stop_reason}")
    print(f"  final_result: {result.state.final_result}")
    print(f"  report:       {result.state.final_report_path}")
    print(f"  findings:     {len(result.state.findings)}")
    print(f"  steps_used:   {result.state.current_step}/{result.state.max_steps}")
    if result.state.findings:
        print("\n  Top findings:")
        for f in result.state.findings[:5]:
            sev = str(f.get("severity", "?")).upper()
            title = f.get("title", "untitled")
            loc = f.get("file", "?")
            line = f.get("line", "")
            line_suffix = f":{line}" if line else ""
            print(f"    [{sev}] {title} @ {loc}{line_suffix}")
    print("=" * 60)


if __name__ == "__main__":
    main()
