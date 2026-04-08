"""Run-scoped audit board memory for the Whitzard example."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from qitos.core.memory import Memory, MemoryRecord

NOISE_TOKENS = (
    "/test/",
    "/tests/",
    "testdir/",
    "/fixture",
    "/fixtures/",
    "/sample",
    "/samples/",
    "/corpus/",
    "/spec/",
    "/specs/",
)

CORE_PREFIXES = (
    "src/",
    "runtime/plugin/",
    "runtime/ftplugin/",
    "runtime/autoload/",
    "runtime/",
)


class AuditBoardMemory(Memory):
    """Structured working memory specialized for long-running code audits."""

    def __init__(self, keep_last: int = 80):
        self.keep_last = int(keep_last)
        self._records: List[MemoryRecord] = []
        self.reset()

    def append(self, record: MemoryRecord) -> None:
        self._records.append(record)
        self.evict()

    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> List[MemoryRecord]:
        _ = state, observation
        query = query or {}
        max_items = int(query.get("max_items", self.keep_last))
        roles = query.get("roles")
        items = self._records[-max_items:] if max_items > 0 else list(self._records)
        if roles:
            role_set = {str(role) for role in roles}
            items = [item for item in items if item.role in role_set]
        return items

    def summarize(self, max_items: int = 5) -> str:
        targets = self.snapshot().get("repo_targets", [])[:max_items]
        findings = self.snapshot().get("confirmed_findings", [])[:max_items]
        target_text = ", ".join(str(item.get("path")) for item in targets) or "none"
        finding_text = ", ".join(
            str(item.get("title") or item.get("file") or "?") for item in findings
        ) or "none"
        return f"targets={target_text} | findings={finding_text}"

    def evict(self) -> int:
        if self.keep_last <= 0 or len(self._records) <= self.keep_last:
            return 0
        removed = len(self._records) - self.keep_last
        self._records = self._records[-self.keep_last :]
        return removed

    def reset(self, run_id: Optional[str] = None) -> None:
        _ = run_id
        self._records = []
        self._board: Dict[str, Any] = {
            "repo_targets": [],
            "entrypoints": [],
            "hotspots": [],
            "hypotheses": [],
            "failed_searches": [],
            "focused_reads": [],
            "confirmed_findings": [],
            "phase_status": {},
            "report_path": "",
        }
        self._target_index: Dict[str, Dict[str, Any]] = {}

    def snapshot(self) -> Dict[str, Any]:
        board = dict(self._board)
        board["repo_targets"] = list(self._board["repo_targets"])
        board["entrypoints"] = list(self._board["entrypoints"])
        board["hotspots"] = list(self._board["hotspots"])
        board["hypotheses"] = list(self._board["hypotheses"])
        board["failed_searches"] = list(self._board["failed_searches"])
        board["focused_reads"] = list(self._board["focused_reads"])
        board["confirmed_findings"] = list(self._board["confirmed_findings"])
        board["phase_status"] = dict(self._board["phase_status"])
        return board

    def remember_hypothesis(
        self, *, analysis: str = "", plan: str = "", step_id: int
    ) -> None:
        text = " | ".join(part for part in (analysis.strip(), plan.strip()) if part)
        if not text:
            return
        item = {"step": int(step_id), "text": text[:320]}
        rows = [
            row
            for row in self._board["hypotheses"]
            if str(row.get("text", "")).strip() != item["text"]
        ]
        rows.append(item)
        self._board["hypotheses"] = rows[-8:]

    def ingest_inventory(self, rows: List[Dict[str, Any]], step_id: int) -> None:
        self._board["entrypoints"] = self._normalize_rows(rows, limit=12)
        for row in rows:
            path = self._row_path(row)
            if not path:
                continue
            self._upsert_target(
                path,
                step_id=step_id,
                reason="inventory_entrypoint",
                status="candidate",
                source="audit_inventory",
                bonus=8,
            )

    def ingest_entrypoints(self, rows: List[Dict[str, Any]], step_id: int) -> None:
        self._board["entrypoints"] = self._normalize_rows(rows, limit=16)
        for row in rows:
            path = self._row_path(row)
            if not path:
                continue
            self._upsert_target(
                path,
                step_id=step_id,
                reason="entrypoint",
                status="candidate",
                source="audit_entrypoints",
                bonus=22,
            )

    def ingest_hotspots(self, rows: List[Dict[str, Any]], step_id: int) -> None:
        self._board["hotspots"] = self._normalize_rows(rows, limit=12)
        for row in rows:
            path = self._row_path(row)
            if not path:
                continue
            bonus = int(row.get("score") or 0) + 18
            self._upsert_target(
                path,
                step_id=step_id,
                reason="hotspot",
                status="candidate",
                source="audit_hotspots",
                bonus=bonus,
            )

    def ingest_grep_result(self, result: Dict[str, Any], step_id: int) -> None:
        status = str(result.get("status") or "")
        pattern = str(result.get("pattern") or "")
        regex = bool(result.get("context", {}).get("regex", True))
        if status == "error":
            self._board["failed_searches"] = (
                self._board["failed_searches"]
                + [
                    {
                        "step": int(step_id),
                        "pattern": pattern,
                        "regex": regex,
                        "message": str(
                            result.get("message") or result.get("error") or ""
                        )[:220],
                    }
                ]
            )[-8:]
            return

        for match in result.get("matches", [])[:20]:
            if not isinstance(match, dict):
                continue
            path = self._row_path(match)
            if not path:
                continue
            self._upsert_target(
                path,
                step_id=step_id,
                reason="grep_hit",
                status="search_hit",
                source="grep_files",
                bonus=16,
            )

    def ingest_read(
        self,
        *,
        path: str,
        offset: int,
        limit: int,
        content: str,
        step_id: int,
    ) -> None:
        if not path:
            return
        rows = [
            row
            for row in self._board["focused_reads"]
            if not (
                row.get("path") == path
                and int(row.get("offset", -1)) == int(offset)
                and int(row.get("limit", -1)) == int(limit)
            )
        ]
        rows.append(
            {
                "path": path,
                "offset": int(offset),
                "limit": int(limit),
                "step": int(step_id),
                "preview": str(content or "").strip()[:180],
            }
        )
        self._board["focused_reads"] = rows[-10:]
        self._upsert_target(
            path,
            step_id=step_id,
            reason="focused_read",
            status="inspected",
            source="read_file_range",
            bonus=30,
        )

    def ingest_finding(self, finding: Dict[str, Any], step_id: int) -> None:
        path = self._row_path(finding)
        title = str(finding.get("title") or "").strip()
        fingerprint = (
            title,
            path,
            str(finding.get("line") or ""),
            str(finding.get("evidence") or finding.get("description") or "")[:120],
        )
        existing = []
        seen = set()
        for row in self._board["confirmed_findings"]:
            fp = (
                str(row.get("title") or ""),
                str(row.get("file") or ""),
                str(row.get("line") or ""),
                str(row.get("evidence") or row.get("description") or "")[:120],
            )
            if fp in seen:
                continue
            seen.add(fp)
            existing.append(row)
        if fingerprint not in seen:
            row = dict(finding)
            row.setdefault("step", int(step_id))
            existing.append(row)
        self._board["confirmed_findings"] = existing[-12:]
        if path:
            self._upsert_target(
                path,
                step_id=step_id,
                reason="confirmed_finding",
                status="confirmed",
                source="finding_add",
                bonus=120,
            )

    def ingest_finding_batch(self, findings: List[Dict[str, Any]], step_id: int) -> None:
        for finding in findings:
            if isinstance(finding, dict):
                self.ingest_finding(finding, step_id)

    def ingest_report(self, output_path: str) -> None:
        if output_path and output_path.strip():
            self._board["report_path"] = output_path.strip()

    def update_phase_status(self, phase_status: Dict[str, Any]) -> None:
        self._board["phase_status"] = dict(phase_status)

    def guidance(self) -> List[str]:
        lines: List[str] = []
        failed = self._board["failed_searches"]
        if failed:
            last = failed[-1]
            message = str(last.get("message") or "").lower()
            if "invalid regex" in message or "unterminated subpattern" in message:
                lines.append(
                    "Recent grep failed due to regex syntax. Retry with regex=false for literals like system(, eval(, modeline, or :execute, or escape regex metacharacters explicitly."
                )

        targets = self.top_targets(limit=3, unexplored_only=True)
        if targets:
            top = targets[0]
            if top.get("status") in {"candidate", "search_hit"}:
                lines.append(
                    f"Best next evidence move: read_file_range on {top['path']} before launching another broad grep."
                )

        if self._all_targets_are_noise():
            lines.append(
                "Current leads are dominated by test or sample paths. Re-anchor on src/ and runtime/{plugin,ftplugin,autoload} before continuing."
            )

        if not lines:
            lines.append(
                "Prefer evidence convergence: once grep_files hits a core file, switch to read_file_range and verify exploitability before searching again."
            )
        return lines

    def top_targets(
        self, limit: int = 5, *, unexplored_only: bool = False
    ) -> List[Dict[str, Any]]:
        rows = list(self._target_index.values())
        if unexplored_only:
            rows = [
                row
                for row in rows
                if str(row.get("status") or "") not in {"inspected", "confirmed"}
            ]
        rows.sort(
            key=lambda row: (
                -int(row.get("score") or 0),
                self._noise_weight(str(row.get("path") or "")),
                str(row.get("path") or ""),
            )
        )
        return [self._clean_target(row) for row in rows[:limit]]

    def _all_targets_are_noise(self) -> bool:
        targets = self.top_targets(limit=4)
        if not targets:
            return False
        return all(self._is_noise_path(str(item.get("path") or "")) for item in targets)

    def _normalize_rows(
        self, rows: List[Dict[str, Any]], *, limit: int
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = self._row_path(row)
            key = (path, str(row.get("symbol") or ""))
            if not path or key in seen:
                continue
            seen.add(key)
            normalized.append(dict(row))
            if len(normalized) >= limit:
                break
        return normalized

    def _upsert_target(
        self,
        path: str,
        *,
        step_id: int,
        reason: str,
        status: str,
        source: str,
        bonus: int,
    ) -> None:
        item = self._target_index.get(path, {"path": path, "reasons": [], "sources": []})
        item["path"] = path
        item["last_step"] = int(step_id)
        item["status"] = status if status == "confirmed" else item.get("status", status)
        if status == "inspected" and item.get("status") != "confirmed":
            item["status"] = "inspected"
        if status == "search_hit" and item.get("status") not in {"inspected", "confirmed"}:
            item["status"] = "search_hit"
        if status == "candidate" and not item.get("status"):
            item["status"] = "candidate"
        reasons = list(item.get("reasons", []))
        if reason not in reasons:
            reasons.append(reason)
        item["reasons"] = reasons[-6:]
        sources = list(item.get("sources", []))
        if source not in sources:
            sources.append(source)
        item["sources"] = sources[-6:]
        item["score"] = self._score_path(path, reasons=item["reasons"], bonus=bonus)
        self._target_index[path] = item
        self._board["repo_targets"] = self.top_targets(limit=8)

    def _score_path(self, path: str, *, reasons: List[str], bonus: int = 0) -> int:
        score = 10 + int(bonus)
        if self._is_core_path(path):
            score += 40
        if self._is_noise_path(path):
            score -= 35
        if "entrypoint" in reasons:
            score += 18
        if "hotspot" in reasons:
            score += 20
        if "grep_hit" in reasons:
            score += 10
        if "focused_read" in reasons:
            score += 8
        if "confirmed_finding" in reasons:
            score += 100
        if "entrypoint" in reasons and "hotspot" in reasons:
            score += 22
        return score

    def _clean_target(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "path": row.get("path"),
            "score": int(row.get("score") or 0),
            "status": row.get("status"),
            "reasons": list(row.get("reasons", [])),
        }

    def _row_path(self, row: Dict[str, Any]) -> str:
        for key in ("file", "path", "affected_component"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _is_core_path(self, path: str) -> bool:
        normalized = path.strip().lower()
        return any(normalized.startswith(prefix) for prefix in CORE_PREFIXES)

    def _is_noise_path(self, path: str) -> bool:
        normalized = f"/{path.strip().lower().lstrip('/')}"
        return any(token in normalized for token in NOISE_TOKENS)

    def _noise_weight(self, path: str) -> int:
        return 1 if self._is_noise_path(path) else 0


__all__ = ["AuditBoardMemory"]
