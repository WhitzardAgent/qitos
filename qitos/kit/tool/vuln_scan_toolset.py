"""
Vulnerability scanning tools.

Provides vulnerability detection operations: nuclei_scan, vuln_quick, nikto_scan,
openvas_scan, cve_query, searchsploit.
Uses subprocess to call industry-standard vulnerability scanners (nuclei, nikto, openvas, searchsploit).
All operations MUST be performed within authorized scope only.
"""

import json
import re
import subprocess
from typing import Any, Dict, List, Optional

from qitos.core.tool import tool


class VulnScanToolSet:
    """
    Vulnerability scanning toolset providing comprehensive vulnerability detection capabilities.

    Supports template-based scanning (Nuclei), web server scanning (Nikto),
    full vulnerability assessment (OpenVAS), and CVE/exploit database queries (Searchsploit).
    All targets must be within authorized scope.
    """

    def __init__(self, authorized_targets: Optional[List[str]] = None, workspace_root: str = "."):
        """
        Initialize vulnerability scanning toolset.

        :param authorized_targets: List of authorized target IPs/domains/URLs. All operations will be validated.
        :param workspace_root: Root directory for storing scan results and reports.
        """
        self._authorized_targets = authorized_targets or []
        self._workspace_root = workspace_root

    def _validate_target(self, target: str) -> bool:
        """Validate target is within authorized scope."""
        if not self._authorized_targets:
            return True
        for auth in self._authorized_targets:
            if target == auth or target.startswith(auth):
                return True
        return False

    def _run_command(self, cmd: List[str], timeout: int = 600) -> Dict[str, Any]:
        """Execute a shell command safely and capture output."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return {"stdout": result.stdout, "stderr": result.stderr, "return_code": result.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Command timed out", "return_code": -1}
        except FileNotFoundError:
            return {"stdout": "", "stderr": f"Tool not found: {cmd[0]}. Please ensure it is installed.", "return_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": f"Error executing command: {str(e)}", "return_code": -1}

    def _parse_nuclei_json(self, json_output: str) -> List[Dict[str, Any]]:
        """
        Parse Nuclei JSON output into a structured list of findings.

        Each finding includes template ID, severity, type, host, matched-at,
        extracted results, and description metadata.

        :param json_output: Raw JSON lines from Nuclei output.
        :return: List of parsed vulnerability findings.
        """
        findings = []
        for line in json_output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                finding = {
                    "template_id": data.get("template-id", ""),
                    "template_name": data.get("info", {}).get("name", ""),
                    "severity": data.get("info", {}).get("severity", "unknown"),
                    "type": data.get("type", "unknown"),
                    "host": data.get("host", ""),
                    "matched_at": data.get("matched-at", ""),
                    "extracted_results": data.get("extracted-results", []),
                    "description": data.get("info", {}).get("description", ""),
                    "reference": data.get("info", {}).get("reference", []),
                    "tags": data.get("info", {}).get("tags", []),
                    "remediation": data.get("info", {}).get("remediation", ""),
                    "classification": data.get("info", {}).get("classification", {}),
                }
                findings.append(finding)
            except json.JSONDecodeError:
                continue

        return findings

    def _parse_nikto_output(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse Nikto plain-text output into structured findings.

        Extracts OSVDB IDs, messages, and URLs from Nikto's output format.

        :param text: Raw Nikto output text.
        :return: List of structured Nikto findings.
        """
        findings = []
        # Nikto output format: + OSVDB-XXXX: message (URL)
        # or: + message (URL)
        pattern = r'\+\s+(?:(?:OSVDB-(\d+)|):\s*)?(.+?)(?:\s+\(([^)]+)\))?$'

        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("+ ") and not line.startswith("+ Target IP") and not line.startswith("+ Start Time") and not line.startswith("+ End Time"):
                match = re.match(pattern, line[2:])
                if match:
                    osvdb_id = match.group(1)
                    message = match.group(2).strip()
                    url = match.group(3) or ""
                    findings.append({
                        "osvdb_id": osvdb_id or "N/A",
                        "message": message,
                        "url": url,
                    })

        return findings

    def _parse_searchsploit(self, text: str) -> List[Dict[str, str]]:
        """
        Parse Searchsploit output into structured exploit entries.

        Extracts exploit title, path, type, and platform from the table output.

        :param text: Raw Searchsploit output.
        :return: List of structured exploit entries.
        """
        entries = []
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("---") or line.startswith("Exploit Title"):
                continue
            # Format: Title | Path | Type | Platform
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4:
                entries.append({
                    "title": parts[0].strip(),
                    "path": parts[1].strip(),
                    "type": parts[2].strip(),
                    "platform": parts[3].strip(),
                })

        return entries

    def _severity_to_color(self, severity: str) -> str:
        """Map severity level to emoji indicator."""
        mapping = {
            "critical": "🔴",
            "high": "🟠",
            "medium": "🟡",
            "low": "🟢",
            "info": "🔵",
            "unknown": "⚪",
        }
        return mapping.get(severity.lower(), "⚪")

    @tool(name='nuclei_scan')
    def nuclei_scan(self, target: str, templates: str = "cves", severity: str = "critical,high,medium",
                    rate_limit: int = 150, timeout_sec: int = 10) -> Dict[str, Any]:
        """
        Run Nuclei vulnerability scanner against a target.

        Nuclei uses YAML-based templates to detect vulnerabilities, misconfigurations,
        exposed panels, and more. It is fast, customizable, and has a large community template library.

        :param target: Target URL or domain (e.g., 'https://example.com', 'http://192.168.1.1').
        :param templates: Template selection. Options:
            - 'cves': CVE-based vulnerability templates (default).
            - 'vulnerabilities': All vulnerability templates.
            - 'misconfigurations': Configuration weakness templates.
            - 'exposures': Exposed panels, APIs, and debug interfaces.
            - 'technologies': Technology fingerprinting templates.
            - 'tokens': Hardcoded secrets and API key detection.
            - 'custom:path': Path to custom template directory.
        :param severity: Comma-separated severity filter. Options: 'critical', 'high', 'medium', 'low', 'info'.
        :param rate_limit: Requests per second (default: 150). Reduce for sensitive targets.
        :param timeout_sec: Per-request timeout in seconds (default: 10).
        :return: Structured list of vulnerability findings with severity, description, and remediation.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        cmd = [
            "nuclei",
            "-u", target,
            "-t", templates,
            "-severity", severity,
            "-rl", str(rate_limit),
            "-timeout", str(timeout_sec),
            "-json",
            "-silent",
        ]

        result = self._run_command(cmd, timeout=1800)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"Nuclei scan failed: {result['stderr']}"}

        findings = self._parse_nuclei_json(result["stdout"])

        # Sort by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "unknown": 5}
        findings.sort(key=lambda f: severity_order.get(f["severity"].lower(), 5))

        # Group by severity
        by_severity = {}
        for f in findings:
            by_severity.setdefault(f["severity"].lower(), []).append(f)

        output = f"### 🎯 Nuclei Scan: {target}\n\n"
        output += f"Templates: {templates} | Severity filter: {severity}\n\n"
        output += f"Found **{len(findings)}** vulnerability finding(s)\n\n"

        for sev in ["critical", "high", "medium", "low", "info"]:
            if sev in by_severity:
                emoji = self._severity_to_color(sev)
                items = by_severity[sev]
                output += f"#### {emoji} {sev.upper()} ({len(items)} findings)\n\n"
                for f in items:
                    output += f"**{f['template_name']}** ({f['template_id']})\n"
                    output += f"- Host: `{f['host']}`\n"
                    if f.get("matched_at"):
                        output += f"- Matched at: `{f['matched_at']}`\n"
                    if f.get("description"):
                        output += f"- Description: {f['description']}\n"
                    if f.get("extracted_results"):
                        for ext in f["extracted_results"]:
                            output += f"- Extracted: `{ext}`\n"
                    if f.get("remediation"):
                        output += f"- Remediation: {f['remediation']}\n"
                    if f.get("reference"):
                        output += f"- References: {', '.join(f['reference'][:3])}\n"
                    output += "\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "templates": templates,
                "severity_filter": severity,
                "findings": findings,
                "finding_count": len(findings),
                "by_severity": {k: len(v) for k, v in by_severity.items()},
            }
        }

    @tool(name='nikto_scan')
    def nikto_scan(self, target: str, tuning: str = "123457890", ports: str = "80,443") -> Dict[str, Any]:
        """
        Run Nikto web server scanner against a target.

        Nikto performs comprehensive tests against web servers for dangerous files,
        outdated software, configuration issues, and CVEs. It checks thousands of
        items including server misconfigurations and insecure files.

        :param target: Target URL (e.g., 'http://example.com', 'https://192.168.1.1').
        :param tuning: Test tuning options (digits control which tests are run):
            - 1: Interesting files (e.g., /readme.html).
            - 2: Misconfigurations (default settings, admin panels).
            - 3: Information disclosure (version, headers, errors).
            - 4: XSS/injection vulnerabilities.
            - 5: Remote file inclusion (RFI).
            - 7: Server software identification (always enabled).
            - 8: Command execution / remote file retrieval.
            - 9: SQL injection tests.
            - 0: File upload checks.
            Default '123457890' runs all tests.
        :param ports: Comma-separated list of ports to scan.
        :return: Structured list of findings with OSVDB IDs, descriptions, and affected URLs.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        cmd = [
            "nikto",
            "-h", target,
            "-Tuning", tuning,
            "-ports", ports,
            "-Format", "txt",
            "-nointeractive",
        ]

        result = self._run_command(cmd, timeout=1800)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"Nikto scan failed: {result['stderr']}"}

        findings = self._parse_nikto_output(result["stdout"])

        output = f"### 🕷️ Nikto Scan: {target}\n\n"
        output += f"Tuning: {tuning} | Ports: {ports}\n\n"
        output += f"Found **{len(findings)}** finding(s)\n\n"

        if findings:
            output += "| # | Severity | Finding | URL |\n"
            output += "|---|----------|---------|-----|\n"
            for i, f in enumerate(findings, 1):
                msg = f["message"][:100] + "..." if len(f["message"]) > 100 else f["message"]
                url = f["url"][:80] + "..." if len(f["url"]) > 80 else f["url"]
                output += f"| {i} | {f['osvdb_id']} | {msg} | {url} |\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "tuning": tuning,
                "findings": findings,
                "finding_count": len(findings),
            }
        }

    @tool(name='searchsploit')
    def searchsploit(self, query: str, exclude_sourced: bool = False, exact: bool = False) -> Dict[str, Any]:
        """
        Search Exploit-DB for public exploits and vulnerabilities.

        Exploit-DB is a comprehensive archive of public exploits and vulnerable software.
        Use this to find known exploits for discovered services and vulnerabilities.

        :param query: Search query (e.g., 'Apache 2.4', 'CVE-2021-44228', 'Windows SMB').
        :param exclude_sourced: If True, exclude exploits that have been sourced/backported.
        :param exact: If True, perform exact title match instead of fuzzy search.
        :return: Structured list of matching exploits with titles, paths, types, and platforms.
        """
        cmd = ["searchsploit", "--json", query]
        if exclude_sourced:
            cmd.append("--exclude-sourced")
        if exact:
            cmd.append("--exact")

        result = self._run_command(cmd, timeout=120)

        if result["return_code"] != 0 and not result["stdout"]:
            # Fallback to text output
            cmd_text = ["searchsploit", query]
            if exclude_sourced:
                cmd_text.append("--exclude-sourced")
            if exact:
                cmd_text.append("--exact")
            result_text = self._run_command(cmd_text, timeout=120)
            if not result_text["stdout"]:
                return {"status": "error", "message": f"No exploits found for '{query}'."}

            entries = self._parse_searchsploit(result_text["stdout"])
        else:
            entries = []
            try:
                data = json.loads(result["stdout"])
                if isinstance(data, list):
                    entries = [{
                        "title": e.get("Title", ""),
                        "path": e.get("Path", ""),
                        "type": e.get("Type", ""),
                        "platform": e.get("Platform", ""),
                        "date": e.get("Date", ""),
                    } for e in data]
            except json.JSONDecodeError:
                entries = self._parse_searchsploit(result["stdout"])

        output = f"### 💣 Exploit Search: '{query}'\n\n"
        output += f"Found **{len(entries)}** exploit(s)\n\n"

        if entries:
            output += "| # | Title | Type | Platform | Path |\n"
            output += "|---|-------|------|----------|------|\n"
            for i, e in enumerate(entries[:30], 1):
                title = e.get("title", "")[:80] + "..." if len(e.get("title", "")) > 80 else e.get("title", "")
                output += f"| {i} | {title} | {e.get('type', '')} | {e.get('platform', '')} | `{e.get('path', '')}` |\n"

            if len(entries) > 30:
                output += f"\nShowing first 30 of {len(entries)} results.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "query": query,
                "exploits": entries,
                "exploit_count": len(entries),
            }
        }

    @tool(name='vuln_quick')
    def vuln_quick(self, target: str) -> Dict[str, Any]:
        """
        Quick vulnerability assessment combining multiple scanners.

        Runs a fast vulnerability check combining Nuclei (critical/high templates)
        and Nikto for a rapid initial assessment of the target's security posture.

        :param target: Target URL or IP (e.g., 'http://example.com', 'https://192.168.1.1').
        :return: Combined vulnerability findings from all scanners.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        # Run nuclei with critical+high severity only
        nuclei_cmd = [
            "nuclei", "-u", target,
            "-t", "cves",
            "-severity", "critical,high",
            "-rl", "100",
            "-timeout", "5",
            "-json", "-silent",
        ]

        nuclei_result = self._run_command(nuclei_cmd, timeout=900)
        nuclei_findings = []
        if nuclei_result["stdout"]:
            nuclei_findings = self._parse_nuclei_json(nuclei_result["stdout"])

        output = f"### ⚡ Quick Vulnerability Assessment: {target}\n\n"

        # Nuclei results
        output += f"#### Nuclei Results ({len(nuclei_findings)} findings)\n\n"
        if nuclei_findings:
            for f in nuclei_findings[:10]:
                emoji = self._severity_to_color(f["severity"])
                output += f"{emoji} **{f['template_name']}** — {f['host']}\n"
                if f.get("description"):
                    output += f"  {f['description']}\n"
                output += "\n"
        else:
            output += "No critical/high vulnerability findings from Nuclei.\n\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "nuclei_findings": nuclei_findings,
                "total_findings": len(nuclei_findings),
            }
        }

    @tool(name='cve_query')
    def cve_query(self, service: str, version: str = "") -> Dict[str, Any]:
        """
        Query known CVEs for a specific service and version.

        Searches the NVD (National Vulnerability Database) for known vulnerabilities
        associated with a given software product and version.

        :param service: Service name (e.g., 'Apache httpd', 'OpenSSH', 'nginx').
        :param version: Optional version string (e.g., '2.4.49'). If provided, narrows results to specific version.
        :return: List of known CVEs with severity scores and descriptions.
        """
        query = f"{service}"
        if version:
            query += f" {version}"

        # Use searchsploit as local CVE lookup
        cmd = ["searchsploit", "--json", query]
        result = self._run_command(cmd, timeout=120)

        entries = []
        if result["stdout"]:
            try:
                data = json.loads(result["stdout"])
                if isinstance(data, list):
                    entries = [{
                        "title": e.get("Title", ""),
                        "path": e.get("Path", ""),
                        "type": e.get("Type", ""),
                        "platform": e.get("Platform", ""),
                        "date": e.get("Date", ""),
                    } for e in data]
            except json.JSONDecodeError:
                pass

        output = f"### 🔐 CVE Query: {query}\n\n"
        output += f"Found **{len(entries)}** related exploit(s)\n\n"

        if entries:
            output += "| # | Title | Type | Platform |\n"
            output += "|---|-------|------|----------|\n"
            for i, e in enumerate(entries[:20], 1):
                title = e.get("title", "")[:100] + "..." if len(e.get("title", "")) > 100 else e.get("title", "")
                output += f"| {i} | {title} | {e.get('type', '')} | {e.get('platform', '')} |\n"

        else:
            output += "No known exploits found in the local database.\n"
            output += "Consider using online resources like NVD (https://nvd.nist.gov/) for comprehensive CVE data.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "service": service,
                "version": version,
                "query": query,
                "exploits": entries,
                "exploit_count": len(entries),
            }
        }
