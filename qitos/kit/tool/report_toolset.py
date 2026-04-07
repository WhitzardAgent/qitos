"""
Report generation and MITRE ATT&CK mapping tools.

Provides reporting operations: generate_report, attack_map, finding_add,
finding_export, attack_tree, summary_generate.
All operations help produce professional security assessment reports.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from qitos.core.tool import tool


class ReportToolSet:
    """
    Report generation toolset providing comprehensive security reporting capabilities.

    Supports Markdown and JSON report generation, MITRE ATT&CK framework mapping,
    finding management, attack tree visualization, and executive summary generation.
    """

    # MITRE ATT&CK Enterprise Tactics
    ATTACK_TACTICS = {
        "TA0001": {"name": "Initial Access", "description": "The adversary is trying to get into your network."},
        "TA0002": {"name": "Execution", "description": "The adversary is trying to run malicious code."},
        "TA0003": {"name": "Persistence", "description": "The adversary is trying to maintain their foothold."},
        "TA0004": {"name": "Privilege Escalation", "description": "The adversary is trying to gain higher-level permissions."},
        "TA0005": {"name": "Defense Evasion", "description": "The adversary is trying to avoid being detected."},
        "TA0006": {"name": "Credential Access", "description": "The adversary is trying to steal account names and passwords."},
        "TA0007": {"name": "Discovery", "description": "The adversary is trying to figure out your environment."},
        "TA0008": {"name": "Lateral Movement", "description": "The adversary is trying to move into your network."},
        "TA0009": {"name": "Collection", "description": "The adversary is trying to gather data of interest."},
        "TA0010": {"name": "Exfiltration", "description": "The adversary is trying to steal data."},
        "TA0011": {"name": "Command and Control", "description": "The adversary is trying to communicate with compromised systems."},
        "TA0040": {"name": "Impact", "description": "The adversary is trying to manipulate, interrupt, or destroy your systems and data."},
    }

    # Common ATT&CK Technique to Tactic mappings
    TECHNIQUE_MAP = {
        "T1190": {"name": "Exploit Public-Facing Application", "tactic": "TA0001"},
        "T1133": {"name": "External Remote Services", "tactic": "TA0001"},
        "T1078": {"name": "Valid Accounts", "tactic": "TA0001"},
        "T1595": {"name": "Active Scanning", "tactic": "TA0040"},
        "T1046": {"name": "Network Service Discovery", "tactic": "TA0007"},
        "T1049": {"name": "System Network Connections Discovery", "tactic": "TA0007"},
        "T1087": {"name": "Account Discovery", "tactic": "TA0007"},
        "T1218": {"name": "System Binary Proxy Execution", "tactic": "TA0005"},
        "T1059": {"name": "Command and Scripting Interpreter", "tactic": "TA0002"},
        "T1055": {"name": "Process Injection", "tactic": "TA0002"},
        "T1110": {"name": "Brute Force", "tactic": "TA0006"},
        "T1003": {"name": "OS Credential Dumping", "tactic": "TA0006"},
        "T1558": {"name": "Steal or Forge Kerberos Tickets", "tactic": "TA0006"},
        "T1021": {"name": "Remote Services", "tactic": "TA0008"},
        "T1080": {"name": "Taint Shared Content", "tactic": "TA0008"},
        "T1210": {"name": "Exploitation of Remote Services", "tactic": "TA0008"},
        "T1071": {"name": "Application Layer Protocol", "tactic": "TA0011"},
        "T1573": {"name": "Encrypted Channel", "tactic": "TA0011"},
        "T1486": {"name": "Data Encrypted for Impact", "tactic": "TA0040"},
        "T1489": {"name": "Service Stop", "tactic": "TA0040"},
    }

    def __init__(self, workspace_root: str = "."):
        """
        Initialize report toolset.

        :param workspace_root: Root directory for storing generated reports.
        """
        self._workspace_root = workspace_root
        self._findings: List[Dict[str, Any]] = []
        self._metadata: Dict[str, Any] = {}

    def _utc_now(self) -> datetime:
        """Return a timezone-aware UTC timestamp."""
        return datetime.now(timezone.utc)

    def setup(self, context: Dict[str, Any]) -> None:
        """Prepare report resources before runtime starts."""
        _ = context

    def teardown(self, context: Dict[str, Any]) -> None:
        """Release report resources after runtime ends."""
        _ = context

    def tools(self) -> List[Any]:
        """Return the public reporting tools in their canonical registration order."""
        return [
            self.finding_add,
            self.attack_map,
            self.summary_generate,
            self.generate_report,
            self.finding_export,
        ]

    def _severity_to_cvss(self, severity: str) -> Dict[str, Any]:
        """Map severity string to CVSS-like score and rating."""
        mapping = {
            "critical": {"score": "9.0 - 10.0", "color": "🔴", "rating": "Critical"},
            "high": {"score": "7.0 - 8.9", "color": "🟠", "rating": "High"},
            "medium": {"score": "4.0 - 6.9", "color": "🟡", "rating": "Medium"},
            "low": {"score": "0.1 - 3.9", "color": "🟢", "rating": "Low"},
            "info": {"score": "0.0", "color": "🔵", "rating": "Informational"},
        }
        return mapping.get(severity.lower(), mapping["info"])

    def _load_findings(self) -> List[Dict[str, Any]]:
        """Load findings from internal storage."""
        findings_path = os.path.join(self._workspace_root, "_findings.json")
        if os.path.isfile(findings_path):
            try:
                with open(findings_path, "r") as f:
                    self._findings = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._findings = []
        return self._findings

    def _save_findings(self) -> None:
        """Save findings to internal storage."""
        findings_path = os.path.join(self._workspace_root, "_findings.json")
        with open(findings_path, "w") as f:
            json.dump(self._findings, f, indent=2, default=str)

    @tool(name='finding_add')
    def finding_add(self, title: str, severity: str = "medium", description: str = "",
                    evidence: str = "", affected_component: str = "", remediation: str = "",
                    cve: str = "", attack_technique: str = "", references: List[str] = None) -> Dict[str, Any]:
        """
        Add a security finding to the report.

        Records a discovered vulnerability or security issue with full details
        including severity, evidence, and remediation recommendations.

        :param title: Short descriptive title for the finding.
        :param severity: Severity level: 'critical', 'high', 'medium', 'low', 'info'.
        :param description: Detailed description of the vulnerability/issue.
        :param evidence: Evidence of the finding (command output, screenshots, proof).
        :param affected_component: The system, service, or component affected (e.g., 'Web Server (Apache 2.4.49)').
        :param remediation: Steps to remediate the vulnerability.
        :param cve: Associated CVE ID if applicable (e.g., 'CVE-2021-44228').
        :param attack_technique: MITRE ATT&CK technique ID (e.g., 'T1190'). Auto-maps to tactic.
        :param references: List of reference URLs for further reading.
        :return: Confirmation with finding ID and summary.
        """
        valid_severities = ["critical", "high", "medium", "low", "info"]
        if severity.lower() not in valid_severities:
            return {"status": "error", "message": f"Invalid severity '{severity}'. Choose from: {', '.join(valid_severities)}"}

        findings = self._load_findings()

        finding = {
            "id": f"FIN-{len(findings) + 1:04d}",
            "title": title,
            "severity": severity.lower(),
            "description": description,
            "evidence": evidence,
            "affected_component": affected_component,
            "remediation": remediation,
            "cve": cve,
            "attack_technique": attack_technique,
            "references": references or [],
            "discovered_at": self._utc_now().isoformat(),
        }

        # Map to ATT&CK tactic
        if attack_technique and attack_technique in self.TECHNIQUE_MAP:
            tech = self.TECHNIQUE_MAP[attack_technique]
            finding["attack_technique_name"] = tech["name"]
            finding["attack_tactic_id"] = tech["tactic"]
            finding["attack_tactic_name"] = self.ATTACK_TACTICS.get(tech["tactic"], {}).get("name", "Unknown")

        findings.append(finding)
        self._save_findings()

        sev_info = self._severity_to_cvss(severity)

        output = f"### 📝 Finding Added: {finding['id']}\n\n"
        output += f"**Title:** {sev_info['color']} {title}\n"
        output += f"**Severity:** {severity.upper()}\n"
        output += f"**Component:** {affected_component or 'N/A'}\n"
        if cve:
            output += f"**CVE:** {cve}\n"
        if attack_technique:
            output += f"**ATT&CK:** {attack_technique}"
            if finding.get("attack_technique_name"):
                output += f" ({finding['attack_technique_name']})"
            if finding.get("attack_tactic_name"):
                output += f" → {finding['attack_tactic_name']}"
            output += "\n"
        output += f"\n{description}\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {"finding_id": finding["id"], "finding": finding},
        }

    @tool(name='attack_map')
    def attack_map(self, techniques: List[str] = None) -> Dict[str, Any]:
        """
        Map findings and techniques to the MITRE ATT&CK framework.

        Generates a visual mapping of attack techniques used/discovered during the
        assessment, organized by ATT&CK tactics. This helps standardize reporting
        and communicate findings to defensive teams.

        :param techniques: List of ATT&CK technique IDs to map (e.g., ['T1190', 'T1110']).
            If empty, auto-maps from existing findings.
        :return: Structured ATT&CK mapping organized by tactics.
        """
        findings = self._load_findings()

        # Collect techniques
        if not techniques:
            techniques = []
            for f in findings:
                if f.get("attack_technique"):
                    techniques.append(f["attack_technique"])

        # Build mapping
        tactic_mapping = {}
        for tech_id in techniques:
            if tech_id in self.TECHNIQUE_MAP:
                tech = self.TECHNIQUE_MAP[tech_id]
                tactic_id = tech["tactic"]
                tactic_info = self.ATTACK_TACTICS.get(tactic_id, {"name": "Unknown", "description": ""})

                if tactic_id not in tactic_mapping:
                    tactic_mapping[tactic_id] = {
                        "tactic_name": tactic_info["name"],
                        "tactic_description": tactic_info["description"],
                        "techniques": [],
                    }

                # Find related finding
                related_finding = None
                for f in findings:
                    if f.get("attack_technique") == tech_id:
                        related_finding = f
                        break

                technique_entry = {
                    "id": tech_id,
                    "name": tech["name"],
                    "related_finding": related_finding.get("id") if related_finding else None,
                    "severity": related_finding.get("severity") if related_finding else "info",
                }

                if technique_entry not in tactic_mapping[tactic_id]["techniques"]:
                    tactic_mapping[tactic_id]["techniques"].append(technique_entry)

        output = f"### 🎯 MITRE ATT&CK Mapping\n\n"

        if not tactic_mapping:
            output += "No ATT&CK techniques have been mapped yet.\n"
            output += "Add findings with `attack_technique` parameter or specify techniques directly.\n\n"
            output += "**Available techniques for mapping:**\n\n"
            for tech_id, tech in sorted(self.TECHNIQUE_MAP.items()):
                tactic_name = self.ATTACK_TACTICS.get(tech["tactic"], {}).get("name", "Unknown")
                output += f"- `{tech_id}`: {tech['name']} ({tactic_name})\n"

            return {
                "status": "success",
                "stdout": output,
                "data": {"tactics": {}, "technique_count": 0},
            }

        # Order by tactic number
        ordered_tactics = sorted(tactic_mapping.items())

        for tactic_id, tactic_data in ordered_tactics:
            output += f"#### {tactic_data['tactic_name']} ({tactic_id})\n\n"
            output += f"*{tactic_data['tactic_description']}*\n\n"

            for tech in tactic_data["techniques"]:
                sev_info = self._severity_to_cvss(tech["severity"])
                output += f"- {sev_info['color']} **{tech['id']}: {tech['name']}**"
                if tech.get("related_finding"):
                    output += f" → [{tech['related_finding']}]"
                output += "\n"

            output += "\n"

        total_techniques = sum(len(t["techniques"]) for t in tactic_mapping.values())
        output += f"**Total techniques mapped:** {total_techniques}\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "tactics": {k: v for k, v in ordered_tactics},
                "technique_count": total_techniques,
            }
        }

    @tool(name='summary_generate')
    def summary_generate(self, title: str = "Security Assessment Report",
                         target: str = "", scope: str = "",
                         assessor: str = "") -> Dict[str, Any]:
        """
        Generate an executive summary from all recorded findings.

        Produces a management-ready summary of the security assessment including
        scope, methodology, findings overview, risk score, and top recommendations.

        :param title: Report title.
        :param target: Target system/organization being assessed.
        :param scope: Description of the assessment scope.
        :param assessor: Name of the assessor/team.
        :return: Executive summary in structured Markdown format.
        """
        findings = self._load_findings()

        # Calculate statistics
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in findings:
            sev = f.get("severity", "info").lower()
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        total = sum(severity_counts.values())
        risk_score = (severity_counts["critical"] * 10 + severity_counts["high"] * 5 +
                      severity_counts["medium"] * 2 + severity_counts["low"] * 1)

        # Risk rating
        if risk_score >= 30:
            risk_rating = "🔴 CRITICAL"
        elif risk_score >= 15:
            risk_rating = "🟠 HIGH"
        elif risk_score >= 5:
            risk_rating = "🟡 MEDIUM"
        else:
            risk_rating = "🟢 LOW"

        now = self._utc_now().strftime("%Y-%m-%d %H:%M UTC")

        output = f"# {title}\n\n"
        output += f"**Target:** {target or 'Not specified'}\n"
        output += f"**Assessor:** {assessor or 'Security Team'}\n"
        output += f"**Date:** {now}\n\n"

        if scope:
            output += f"## Scope\n\n{scope}\n\n"

        output += f"## Executive Summary\n\n"
        output += f"A security assessment was conducted against the target environment. "
        output += f"The assessment identified **{total}** findings across {len(set(f.get('affected_component', '') for f in findings))} components.\n\n"

        output += f"### Risk Score: {risk_rating} ({risk_score})\n\n"

        # Findings by severity
        output += f"### Findings Overview\n\n"
        output += "| Severity | Count | CVSS Range |\n"
        output += "|----------|-------|------------|\n"
        for sev in ["critical", "high", "medium", "low", "info"]:
            sev_info = self._severity_to_cvss(sev)
            count = severity_counts.get(sev, 0)
            output += f"| {sev_info['color']} {sev.upper()} | {count} | {sev_info['score']} |\n"

        output += f"| **Total** | **{total}** | |\n\n"

        # Top critical/high findings
        critical_high = [f for f in findings if f.get("severity") in ("critical", "high")]
        if critical_high:
            output += f"### Critical & High Severity Findings\n\n"
            for f in critical_high[:10]:
                sev_info = self._severity_to_cvss(f["severity"])
                output += f"{sev_info['color']} **{f['id']}: {f['title']}** ({f['severity'].upper()})\n"
                if f.get("affected_component"):
                    output += f"  - **Component:** {f['affected_component']}\n"
                if f.get("cve"):
                    output += f"  - **CVE:** {f['cve']}\n"
                output += f"  - **Description:** {f.get('description', 'N/A')[:200]}\n"
                if f.get("remediation"):
                    output += f"  - **Fix:** {f['remediation'][:200]}\n"
                output += "\n"

        # Top recommendations
        output += f"### Key Recommendations\n\n"
        recommendations = []
        for f in findings:
            if f.get("remediation") and f.get("severity") in ("critical", "high", "medium"):
                recommendations.append({
                    "title": f["title"],
                    "severity": f["severity"],
                    "remediation": f["remediation"],
                })

        # Sort by severity
        sev_order = {"critical": 0, "high": 1, "medium": 2}
        recommendations.sort(key=lambda r: sev_order.get(r["severity"], 3))

        for i, rec in enumerate(recommendations[:10], 1):
            sev_info = self._severity_to_cvss(rec["severity"])
            output += f"{i}. {sev_info['color']} **{rec['title']}**: {rec['remediation'][:300]}\n\n"

        if not recommendations:
            output += "No specific recommendations generated. Add findings with remediation steps.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "title": title,
                "target": target,
                "total_findings": total,
                "severity_counts": severity_counts,
                "risk_score": risk_score,
                "risk_rating": risk_rating,
                "summary_text": output,
            }
        }

    @tool(name='generate_report')
    def generate_report(self, format: str = "markdown", output_file: str = "") -> Dict[str, Any]:
        """
        Generate a comprehensive security assessment report.

        Compiles all findings, ATT&CK mappings, and summaries into a complete
        professional report suitable for delivery to stakeholders.

        :param format: Output format. Options:
            - 'markdown': Markdown report (default).
            - 'json': Structured JSON report for programmatic use.
        :param output_file: Path to save the report. If empty, auto-generates in workspace_root.
        :return: Full report content with file path.
        """
        findings = self._load_findings()

        if not output_file:
            timestamp = self._utc_now().strftime("%Y%m%d_%H%M%S")
            ext = "md" if format == "markdown" else "json"
            output_file = os.path.join(self._workspace_root, f"security_report_{timestamp}.{ext}")

        if format == "json":
            report_data = {
                "report_type": "Security Assessment Report",
                "generated_at": self._utc_now().isoformat(),
                "findings": findings,
                "summary": {
                    "total": len(findings),
                    "by_severity": {},
                },
            }

            for f in findings:
                sev = f.get("severity", "info")
                report_data["summary"]["by_severity"][sev] = report_data["summary"]["by_severity"].get(sev, 0) + 1

            with open(output_file, "w") as f:
                json.dump(report_data, f, indent=2, default=str)

            output = f"### 📄 JSON Report Generated\n\n"
            output += f"**File:** `{output_file}`\n"
            output += f"**Findings:** {len(findings)}\n"

            return {
                "status": "success",
                "stdout": output,
                "data": {"output_file": output_file, "report_data": report_data},
            }

        # Markdown format
        now = self._utc_now().strftime("%Y-%m-%d %H:%M UTC")

        report = f"# Security Assessment Report\n\n"
        report += f"**Generated:** {now}\n"
        report += f"**Total Findings:** {len(findings)}\n\n"

        # Table of Contents
        report += "## Table of Contents\n\n"
        report += "1. [Findings by Severity](#findings-by-severity)\n"
        report += "2. [Detailed Findings](#detailed-findings)\n"
        report += "3. [ATT&CK Mapping](#mitre-attack-mapping)\n"
        report += "4. [Raw Data](#raw-data)\n\n"

        # Findings by Severity
        report += "## Findings by Severity\n\n"
        sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        sorted_findings = sorted(findings, key=lambda f: sev_order.get(f.get("severity", "info"), 5))

        for sev in ["critical", "high", "medium", "low", "info"]:
            sev_findings = [f for f in sorted_findings if f.get("severity") == sev]
            if sev_findings:
                sev_info = self._severity_to_cvss(sev)
                report += f"### {sev_info['color']} {sev.upper()} ({len(sev_findings)})\n\n"
                for f in sev_findings:
                    report += f"- **{f['id']}: {f['title']}**"
                    if f.get("affected_component"):
                        report += f" — {f['affected_component']}"
                    report += "\n"
                report += "\n"

        # Detailed Findings
        report += "## Detailed Findings\n\n"
        for f in sorted_findings:
            sev_info = self._severity_to_cvss(f["severity"])
            report += f"---\n\n"
            report += f"### {sev_info['color']} {f['id']}: {f['title']}\n\n"
            report += f"| Field | Value |\n|-------|-------|\n"
            report += f"| **Severity** | {f['severity'].upper()} |\n"
            if f.get("affected_component"):
                report += f"| **Component** | {f['affected_component']} |\n"
            if f.get("cve"):
                report += f"| **CVE** | {f['cve']} |\n"
            if f.get("attack_technique"):
                report += f"| **ATT&CK** | {f['attack_technique']}"
                if f.get("attack_technique_name"):
                    report += f" ({f['attack_technique_name']})"
                report += " |\n"
            report += "\n"

            if f.get("description"):
                report += f"**Description:**\n\n{f['description']}\n\n"
            if f.get("evidence"):
                report += f"**Evidence:**\n\n```\n{f['evidence']}\n```\n\n"
            if f.get("remediation"):
                report += f"**Remediation:**\n\n{f['remediation']}\n\n"
            if f.get("references"):
                report += f"**References:**\n\n"
                for ref in f["references"]:
                    report += f"- {ref}\n"
                report += "\n"

        # ATT&CK Mapping
        report += "## MITRE ATT&CK Mapping\n\n"
        techniques_found = [f["attack_technique"] for f in findings if f.get("attack_technique")]
        if techniques_found:
            by_tactic = {}
            for tech_id in techniques_found:
                if tech_id in self.TECHNIQUE_MAP:
                    tech = self.TECHNIQUE_MAP[tech_id]
                    tactic_name = self.ATTACK_TACTICS.get(tech["tactic"], {}).get("name", "Unknown")
                    by_tactic.setdefault(tactic_name, []).append(f"{tech_id}: {tech['name']}")

            for tactic, techs in by_tactic.items():
                report += f"**{tactic}:**\n"
                for t in techs:
                    report += f"- {t}\n"
                report += "\n"
        else:
            report += "No ATT&CK techniques mapped. Add findings with `attack_technique` parameter.\n\n"

        with open(output_file, "w") as f:
            f.write(report)

        output = f"### 📄 Report Generated\n\n"
        output += f"**File:** `{output_file}`\n"
        output += f"**Format:** Markdown\n"
        output += f"**Findings:** {len(findings)}\n"
        output += f"**Size:** {os.path.getsize(output_file):,} bytes\n\n"
        output += f"Report preview:\n\n{report[:3000]}...\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "output_file": output_file,
                "finding_count": len(findings),
            }
        }

    @tool(name='finding_export')
    def finding_export(self, format: str = "json", output_file: str = "") -> Dict[str, Any]:
        """
        Export all findings in various formats.

        Exports recorded findings to different formats for integration with
        other tools, ticketing systems, or reporting platforms.

        :param format: Export format. Options:
            - 'json': Structured JSON array.
            - 'csv': Comma-separated values with headers.
            - 'sarif': SARIF format for GitHub Security tab integration.
            - 'summary': Brief text summary of all findings.
        :param output_file: Path to save export. Auto-generated if empty.
        :return: Export confirmation with file path and format details.
        """
        findings = self._load_findings()

        if not findings:
            return {"status": "error", "message": "No findings to export. Add findings first using finding_add."}

        if not output_file:
            timestamp = self._utc_now().strftime("%Y%m%d_%H%M%S")
            ext_map = {"json": "json", "csv": "csv", "sarif": "sarif.json", "summary": "txt"}
            ext = ext_map.get(format, "txt")
            output_file = os.path.join(self._workspace_root, f"findings_export_{timestamp}.{ext}")

        if format == "json":
            with open(output_file, "w") as f:
                json.dump(findings, f, indent=2, default=str)

        elif format == "csv":
            headers = ["id", "title", "severity", "affected_component", "cve", "attack_technique", "remediation"]
            with open(output_file, "w") as f:
                f.write(",".join(headers) + "\n")
                for finding in findings:
                    values = []
                    for h in headers:
                        val = str(finding.get(h, "")).replace('"', '""').replace('\n', ' ')
                        values.append(f'"{val}"')
                    f.write(",".join(values) + "\n")

        elif format == "sarif":
            sarif = {
                "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
                "version": "2.1.0",
                "runs": [{
                    "tool": {"driver": {"name": "Security Assessment", "version": "1.0.0"}},
                    "results": [],
                }],
            }
            severity_map = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
            for finding in findings:
                result = {
                    "ruleId": finding["id"],
                    "level": severity_map.get(finding.get("severity", "info"), "note"),
                    "message": {"text": finding.get("description", finding.get("title", ""))},
                    "properties": {
                        "title": finding.get("title", ""),
                        "severity": finding.get("severity", "info"),
                        "remediation": finding.get("remediation", ""),
                    },
                }
                if finding.get("cve"):
                    result["properties"]["cve"] = finding["cve"]
                sarif["runs"][0]["results"].append(result)

            with open(output_file, "w") as f:
                json.dump(sarif, f, indent=2)

        elif format == "summary":
            lines = []
            for f in sorted(findings, key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(x.get("severity", "info"), 5)):
                lines.append(f"[{f['severity'].upper()}] {f['id']}: {f['title']}")
                if f.get("affected_component"):
                    lines.append(f"  Component: {f['affected_component']}")
                if f.get("cve"):
                    lines.append(f"  CVE: {f['cve']}")
                if f.get("remediation"):
                    lines.append(f"  Fix: {f['remediation'][:200]}")
                lines.append("")

            with open(output_file, "w") as f:
                f.write("\n".join(lines))

        else:
            return {"status": "error", "message": f"Unsupported format '{format}'. Choose from: json, csv, sarif, summary"}

        output = f"### 📤 Findings Exported\n\n"
        output += f"**Format:** {format.upper()}\n"
        output += f"**File:** `{output_file}`\n"
        output += f"**Findings:** {len(findings)}\n"
        output += f"**Size:** {os.path.getsize(output_file):,} bytes\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "output_file": output_file,
                "format": format,
                "finding_count": len(findings),
            }
        }


__all__ = ["ReportToolSet"]
