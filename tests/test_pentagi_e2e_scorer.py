"""Unit tests for PentAGI e2e scorer — no LLM or Docker required."""

from __future__ import annotations

import pytest

from qitos.benchmark.pentagi_e2e.criteria import (
    TierCriterion,
    TIER1_CRITERIA,
    TIER2_DVWA_CRITERIA,
    TIER3_DVWA_CRITERIA,
    TIER4_DVWA_CRITERIA,
    get_criteria,
)
from qitos.benchmark.pentagi_e2e.report import CriterionScore, ScoreReport
from qitos.benchmark.pentagi_e2e.scorer import PentagiE2EScorer, _collect_text
from qitos.benchmark.pentagi_e2e.targets import TARGETS, VulnerableTarget


# ---------------------------------------------------------------------------
# Helpers: fake PentAGIResult
# ---------------------------------------------------------------------------

class FakeResult:
    """Minimal stand-in for PentAGIResult."""

    def __init__(
        self,
        report: str = "",
        findings: list | None = None,
        completed_subtasks: list | None = None,
        subtasks: list | None = None,
        status: str = "completed",
        total_steps: int = 0,
    ):
        self.report = report
        self.findings = findings or []
        self.completed_subtasks = completed_subtasks or []
        self.subtasks = subtasks or []
        self.status = status
        self.total_steps = total_steps


class FakeTargetManager:
    """Stand-in for TargetManager with planted flags."""

    def __init__(self, flags: dict | None = None):
        self.planted_flags = flags or {}


# ---------------------------------------------------------------------------
# _collect_text tests
# ---------------------------------------------------------------------------

class TestCollectText:
    def test_collects_report(self):
        result = FakeResult(report="Hello world")
        assert "Hello world" in _collect_text(result)

    def test_collects_findings(self):
        result = FakeResult(
            findings=[{"title": "SQL Injection", "description": "Found sqli"}]
        )
        text = _collect_text(result)
        assert "SQL Injection" in text
        assert "Found sqli" in text

    def test_collects_completed_subtasks(self):
        result = FakeResult(
            completed_subtasks=[
                {"title": "Recon", "result": "Port 80 open, Apache httpd"}
            ]
        )
        text = _collect_text(result)
        assert "Port 80 open" in text
        assert "Apache httpd" in text

    def test_handles_empty_result(self):
        result = FakeResult()
        assert _collect_text(result) == ""

    def test_skips_non_string_values(self):
        result = FakeResult(findings=[{"title": 123, "description": None}])
        # Should not crash
        _collect_text(result)


# ---------------------------------------------------------------------------
# TierCriterion structure tests
# ---------------------------------------------------------------------------

class TestCriteriaStructure:
    def test_tier1_criteria_all_required(self):
        for c in TIER1_CRITERIA:
            assert c.required, f"TIER1 criterion '{c.name}' should be required"

    def test_tier2_dvwa_has_required_and_optional(self):
        required = [c for c in TIER2_DVWA_CRITERIA if c.required]
        optional = [c for c in TIER2_DVWA_CRITERIA if not c.required]
        assert len(required) >= 1, "At least one required criterion"
        assert len(optional) >= 1, "At least one optional criterion"

    def test_get_criteria_fallback(self):
        # Unknown target at tier 1 should still return TIER1_CRITERIA
        criteria = get_criteria(1, "nonexistent")
        assert len(criteria) > 0

    def test_get_criteria_empty_for_invalid(self):
        criteria = get_criteria(5, "dvwa")
        assert criteria == []


# ---------------------------------------------------------------------------
# ScoreReport tests
# ---------------------------------------------------------------------------

class TestScoreReport:
    def test_pass_rate_calculation(self):
        report = ScoreReport(
            tier=2,
            scores=[
                CriterionScore("a", True, 1.0, True),
                CriterionScore("b", True, 1.0, False),
                CriterionScore("c", False, 0.0, False),
            ],
        )
        assert report.pass_rate == pytest.approx(2 / 3)

    def test_required_passed(self):
        report = ScoreReport(
            scores=[
                CriterionScore("a", True, 1.0, True),
                CriterionScore("b", False, 0.0, False),
            ]
        )
        assert report.required_passed

    def test_required_failed(self):
        report = ScoreReport(
            scores=[
                CriterionScore("a", False, 0.0, True),
                CriterionScore("b", True, 1.0, False),
            ]
        )
        assert not report.required_passed
        assert report.failure_reasons == ["a"]

    def test_tier_passed(self):
        report = ScoreReport(
            tier=2,
            scores=[
                CriterionScore("a", True, 1.0, True),
                CriterionScore("b", True, 1.0, False),
            ]
        )
        assert report.tier_passed(0.6)

    def test_tier_failed_low_pass_rate(self):
        report = ScoreReport(
            tier=2,
            scores=[
                CriterionScore("a", True, 1.0, True),
                CriterionScore("b", False, 0.0, False),
                CriterionScore("c", False, 0.0, False),
            ]
        )
        # Pass rate = 1/3 = 33%, threshold = 60%
        assert not report.tier_passed(0.6)

    def test_earned_points(self):
        report = ScoreReport(
            scores=[
                CriterionScore("a", True, 1.0, True),
                CriterionScore("b", False, 0.0, True),
                CriterionScore("c", True, 2.0, False),
            ]
        )
        assert report.total_points == 3.0
        assert report.earned_points == 3.0

    def test_summary_output(self):
        report = ScoreReport(
            tier=1,
            target_name="dvwa",
            scores=[
                CriterionScore("pipeline_completed", True, 1.0, True, "Pipeline status: completed"),
            ],
        )
        s = report.summary()
        assert "PASS" in s
        assert "pipeline_completed" in s

    def test_to_dict(self):
        report = ScoreReport(tier=1, target_name="dvwa")
        d = report.to_dict()
        assert d["tier"] == 1
        assert "scores" in d


# ---------------------------------------------------------------------------
# PentagiE2EScorer check method tests
# ---------------------------------------------------------------------------

class TestPentagiE2EScorer:
    def setup_method(self):
        self.scorer = PentagiE2EScorer()
        self.ground_truth = {"ports": [80], "services": []}

    # -- pipeline_completed --

    def test_pipeline_completed_success(self):
        result = FakeResult(status="completed")
        criterion = TierCriterion("p", "", "pipeline_completed")
        passed, detail = self.scorer._check_pipeline_completed(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_pipeline_completed_failure(self):
        result = FakeResult(status="error")
        criterion = TierCriterion("p", "", "pipeline_completed")
        passed, detail = self.scorer._check_pipeline_completed(
            criterion, result, self.ground_truth, None
        )
        assert not passed

    # -- subtasks_generated --

    def test_subtasks_generated_success(self):
        result = FakeResult(subtasks=[{"id": "1"}])
        criterion = TierCriterion("s", "", "subtasks_generated")
        passed, detail = self.scorer._check_subtasks_generated(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_subtasks_generated_empty(self):
        result = FakeResult(subtasks=[])
        criterion = TierCriterion("s", "", "subtasks_generated")
        passed, detail = self.scorer._check_subtasks_generated(
            criterion, result, self.ground_truth, None
        )
        assert not passed

    # -- report_produced --

    def test_report_produced_success(self):
        result = FakeResult(report="x" * 300)
        criterion = TierCriterion("r", "", "report_produced")
        passed, detail = self.scorer._check_report_produced(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_report_produced_too_short(self):
        result = FakeResult(report="short")
        criterion = TierCriterion("r", "", "report_produced")
        passed, detail = self.scorer._check_report_produced(
            criterion, result, self.ground_truth, None
        )
        assert not passed

    # -- port_found --

    def test_port_found_80_tcp(self):
        result = FakeResult(report="80/tcp open http")
        criterion = TierCriterion("p80", "", "port_found", {"port": 80})
        passed, detail = self.scorer._check_port_found(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_port_found_port_keyword(self):
        result = FakeResult(report="Port 80 is open")
        criterion = TierCriterion("p80", "", "port_found", {"port": 80})
        passed, detail = self.scorer._check_port_found(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_port_found_in_findings(self):
        result = FakeResult(
            findings=[{"title": "Open Ports", "description": "Discovered port 80 open"}]
        )
        criterion = TierCriterion("p80", "", "port_found", {"port": 80})
        passed, detail = self.scorer._check_port_found(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_port_not_found(self):
        result = FakeResult(report="No ports discovered")
        criterion = TierCriterion("p80", "", "port_found", {"port": 80})
        passed, detail = self.scorer._check_port_found(
            criterion, result, self.ground_truth, None
        )
        assert not passed

    # -- port_count_found --

    def test_port_count_found(self):
        result = FakeResult(report="21/tcp open ftp\n22/tcp open ssh\n80/tcp open http\n445/tcp open smb\n3306/tcp open mysql")
        criterion = TierCriterion("multi", "", "port_count_found", {"min_count": 5})
        passed, detail = self.scorer._check_port_count_found(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_port_count_insufficient(self):
        result = FakeResult(report="80/tcp open http")
        criterion = TierCriterion("multi", "", "port_count_found", {"min_count": 5})
        passed, detail = self.scorer._check_port_count_found(
            criterion, result, self.ground_truth, None
        )
        assert not passed

    # -- service_identified --

    def test_service_identified_apache(self):
        result = FakeResult(report="Apache httpd 2.4.49 detected")
        criterion = TierCriterion("apache", "", "service_identified", {"keywords": ["apache", "httpd"]})
        passed, detail = self.scorer._check_service_identified(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_service_identified_case_insensitive(self):
        result = FakeResult(report="APACHE HTTPD found")
        criterion = TierCriterion("apache", "", "service_identified", {"keywords": ["apache"]})
        passed, detail = self.scorer._check_service_identified(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_service_not_identified(self):
        result = FakeResult(report="Nginx web server found")
        criterion = TierCriterion("apache", "", "service_identified", {"keywords": ["apache"]})
        passed, detail = self.scorer._check_service_identified(
            criterion, result, self.ground_truth, None
        )
        assert not passed

    # -- vuln_found --

    def test_vuln_found_sqli(self):
        result = FakeResult(report="Discovered SQL Injection vulnerability")
        criterion = TierCriterion("sqli", "", "vuln_found", {"keywords": ["sql injection", "sqli"]})
        passed, detail = self.scorer._check_vuln_found(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_vuln_found_case_insensitive(self):
        result = FakeResult(report="Found SQLI vulnerability on login page")
        criterion = TierCriterion("sqli", "", "vuln_found", {"keywords": ["sql injection", "sqli"]})
        passed, detail = self.scorer._check_vuln_found(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_vuln_not_found(self):
        result = FakeResult(report="No vulnerabilities found")
        criterion = TierCriterion("sqli", "", "vuln_found", {"keywords": ["sql injection", "sqli"]})
        passed, detail = self.scorer._check_vuln_found(
            criterion, result, self.ground_truth, None
        )
        assert not passed

    # -- exploit_succeeded --

    def test_exploit_sqli_evidence(self):
        result = FakeResult(
            report="Successfully exploited SQL injection. Retrieved admin user table with password hashes."
        )
        criterion = TierCriterion(
            "sqli_exploit", "", "exploit_succeeded",
            {"vuln_type": "sqli", "evidence_keywords": ["database", "table", "admin", "password"]},
        )
        passed, detail = self.scorer._check_exploit_succeeded(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_exploit_insufficient_evidence(self):
        result = FakeResult(report="Tried SQL injection on the login form")
        criterion = TierCriterion(
            "sqli_exploit", "", "exploit_succeeded",
            {"vuln_type": "sqli", "evidence_keywords": ["database", "table", "admin", "password"]},
        )
        passed, detail = self.scorer._check_exploit_succeeded(
            criterion, result, self.ground_truth, None
        )
        assert not passed

    # -- flag_retrieved --

    def test_flag_retrieved(self):
        result = FakeResult(report="Found flag: PENTAGI_FLAG_abc123456789")
        manager = FakeTargetManager(flags={"exploit_flag": "PENTAGI_FLAG_abc123456789"})
        criterion = TierCriterion("flag", "", "flag_retrieved")
        passed, detail = self.scorer._check_flag_retrieved(
            criterion, result, self.ground_truth, manager
        )
        assert passed

    def test_flag_not_retrieved(self):
        result = FakeResult(report="Could not find any flag")
        manager = FakeTargetManager(flags={"exploit_flag": "PENTAGI_FLAG_abc123456789"})
        criterion = TierCriterion("flag", "", "flag_retrieved")
        passed, detail = self.scorer._check_flag_retrieved(
            criterion, result, self.ground_truth, manager
        )
        assert not passed

    def test_flag_no_manager(self):
        result = FakeResult(report="Found flag")
        criterion = TierCriterion("flag", "", "flag_retrieved")
        passed, detail = self.scorer._check_flag_retrieved(
            criterion, result, self.ground_truth, None
        )
        assert not passed

    # -- report_contains --

    def test_report_contains_match(self):
        result = FakeResult(report="The target runs DVWA application")
        criterion = TierCriterion("dvwa", "", "report_contains", {"pattern": "dvwa"})
        passed, detail = self.scorer._check_report_contains(
            criterion, result, self.ground_truth, None
        )
        assert passed

    def test_report_contains_regex(self):
        result = FakeResult(report="Damn Vulnerable Web Application")
        criterion = TierCriterion("dvwa", "", "report_contains", {"pattern": "dvwa|damn vulnerable"})
        passed, detail = self.scorer._check_report_contains(
            criterion, result, self.ground_truth, None
        )
        assert passed

    # -- Full integration test --

    def test_full_tier1_scoring(self):
        result = FakeResult(
            status="completed",
            subtasks=[{"id": "1", "title": "Recon"}],
            report="x" * 300,
        )
        report = self.scorer.score(
            result, TIER1_CRITERIA, {}, None,
            tier=1, target_name="dvwa",
        )
        assert report.tier_passed(1.0)
        assert report.required_passed

    def test_full_tier1_failed(self):
        result = FakeResult(
            status="error",
            subtasks=[],
            report="short",
        )
        report = self.scorer.score(
            result, TIER1_CRITERIA, {}, None,
            tier=1, target_name="dvwa",
        )
        assert not report.required_passed


# ---------------------------------------------------------------------------
# Target definitions tests
# ---------------------------------------------------------------------------

class TestTargetDefinitions:
    def test_all_targets_have_required_fields(self):
        for name, target in TARGETS.items():
            assert target.name == name
            assert target.docker_image
            assert target.health_path
            assert target.health_port > 0

    def test_dvwa_supports_all_tiers(self):
        assert TARGETS["dvwa"].min_tier == 1
        assert TARGETS["dvwa"].max_tier == 4

    def test_metasploitable2_starts_at_tier2(self):
        assert TARGETS["metasploitable2"].min_tier == 2
