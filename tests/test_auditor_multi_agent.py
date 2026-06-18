"""Tests for qitos_auditor multi-agent pipeline and critics."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

try:
    from qitos_zoo.qitos_auditor import (
        AuditAgent,
        ReconAuditAgent,
        AnalysisAuditAgent,
        VerificationAuditAgent,
    )
    from qitos_zoo.qitos_auditor.critic import (
        severity_consistency_critic,
        false_positive_critic,
    )
except (ImportError, ModuleNotFoundError) as exc:
    pytest.skip(f"qitos-zoo auditor migration package is unavailable: {exc}", allow_module_level=True)


# ---------------------------------------------------------------------------
# Agent structure tests
# ---------------------------------------------------------------------------


def test_audit_agent_has_handoff_targets():
    """AuditAgent declares handoff_targets for multi-agent pipeline."""
    assert hasattr(AuditAgent, "handoff_targets")
    targets = AuditAgent.handoff_targets
    assert "recon_agent" in targets
    assert "analysis_agent" in targets
    assert "verification_agent" in targets


def test_audit_agent_name():
    """AuditAgent has explicit name attribute."""
    assert AuditAgent.name == "audit_agent"


def test_recon_agent_instantiable():
    """ReconAuditAgent can be instantiated."""
    agent = ReconAuditAgent(llm=MagicMock(), workspace_root=".")
    assert agent.name == "recon_agent"


def test_analysis_agent_instantiable():
    """AnalysisAuditAgent can be instantiated."""
    agent = AnalysisAuditAgent(llm=MagicMock(), workspace_root=".")
    assert agent.name == "analysis_agent"


def test_verification_agent_instantiable():
    """VerificationAuditAgent can be instantiated."""
    agent = VerificationAuditAgent(llm=MagicMock(), workspace_root=".")
    assert agent.name == "verification_agent"


# ---------------------------------------------------------------------------
# Critic tests
# ---------------------------------------------------------------------------


def test_severity_consistency_critic_clean():
    """severity_consistency_critic returns continue when findings are consistent."""
    state = MagicMock()
    state.findings = [
        {"id": "F1", "file": "a.py", "line": 10, "severity": "high"},
        {"id": "F2", "file": "a.py", "line": 20, "severity": "medium"},
    ]
    result = severity_consistency_critic.evaluate(state, MagicMock(), [])
    assert result.action == "continue"


def test_severity_consistency_critic_flags_gap():
    """severity_consistency_critic flags findings with severity gaps."""
    state = MagicMock()
    state.findings = [
        {"id": "F1", "file": "a.py", "line": 10, "severity": "critical"},
        {"id": "F2", "file": "a.py", "line": 20, "severity": "low"},
    ]
    result = severity_consistency_critic.evaluate(state, MagicMock(), [])
    assert result.action == "retry"
    assert "gap" in result.reason.lower() or "severity" in result.reason.lower()


def test_false_positive_critic_clean():
    """false_positive_critic returns continue for well-formed findings."""
    state = MagicMock()
    state.findings = [
        {
            "id": "F1",
            "file": "a.py",
            "line": 10,
            "description": "SQL injection vulnerability in query construction",
        },
    ]
    result = false_positive_critic.evaluate(state, MagicMock(), [])
    assert result.action == "continue"


def test_false_positive_critic_flags_missing_file():
    """false_positive_critic flags findings without file path."""
    state = MagicMock()
    state.findings = [
        {"id": "F1", "file": "", "line": 10, "description": "Some finding about code quality"},
    ]
    result = false_positive_critic.evaluate(state, MagicMock(), [])
    assert result.action == "retry"


def test_false_positive_critic_flags_vague_description():
    """false_positive_critic flags findings with vague descriptions."""
    state = MagicMock()
    state.findings = [
        {"id": "F1", "file": "a.py", "line": 10, "description": "bad"},
    ]
    result = false_positive_critic.evaluate(state, MagicMock(), [])
    assert result.action == "retry"
