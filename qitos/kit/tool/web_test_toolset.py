"""
Web application testing tools.

Provides web security testing operations: sqlmap_scan, dir_bruteforce,
xss_detect, header_check, ssl_check, js_analyze, screenshot.
Uses subprocess to call industry-standard web testing tools (sqlmap, gobuster, whatweb).
All operations MUST be performed within authorized scope only.
"""

import json
import re
import subprocess
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from qitos.core.tool import tool


class WebTestToolSet:
    """
    Web application testing toolset providing comprehensive web security capabilities.

    Supports SQL injection testing (SQLMap), directory brute-forcing (Gobuster/Dirb),
    security header analysis, SSL/TLS configuration checks, and JavaScript analysis.
    All targets must be within authorized scope.
    """

    def __init__(self, authorized_targets: Optional[List[str]] = None, workspace_root: str = "."):
        """
        Initialize web testing toolset.

        :param authorized_targets: List of authorized target URLs/domains. All operations validated.
        :param workspace_root: Root directory for storing scan results and payloads.
        """
        self._authorized_targets = authorized_targets or []
        self._workspace_root = workspace_root

    def _validate_target(self, target: str) -> bool:
        """Validate target URL/domain is within authorized scope."""
        if not self._authorized_targets:
            return True
        parsed = urlparse(target)
        hostname = parsed.hostname or target
        for auth in self._authorized_targets:
            if hostname == auth or hostname.endswith('.' + auth):
                return True
        return False

    def _normalize_url(self, url: str) -> str:
        """Normalize URL to ensure it has a scheme."""
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        return url.rstrip("/")

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

    def _parse_sqlmap_output(self, text: str) -> Dict[str, Any]:
        """
        Parse SQLMap output to extract injection findings.

        Identifies injected parameters, database type, and payload information
        from SQLMap's console output.

        :param text: Raw SQLMap output text.
        :return: Structured dictionary with injection details.
        """
        result = {
            "injections": [],
            "db_type": "",
            "parameters": [],
            "databases": [],
            "tables": [],
            "current_db": "",
        }

        # Extract parameter-based injections
        param_pattern = r"Parameter:\s+(\S+)\s+\((\w+)\)"
        for match in re.finditer(param_pattern, text):
            result["parameters"].append({
                "name": match.group(1),
                "type": match.group(2),
            })

        # Extract backend DBMS
        dbms_pattern = r"back-end DBMS:\s+(.+)"
        dbms_match = re.search(dbms_pattern, text)
        if dbms_match:
            result["db_type"] = dbms_match.group(1).strip()

        # Extract current database
        current_db_pattern = r"current database:\s+'?([^'\n]+)"
        current_db_match = re.search(current_db_pattern, text)
        if current_db_match:
            result["current_db"] = current_db_match.group(1).strip()

        # Extract injection type
        injection_pattern = r"Type:\s+(.+?injection)"
        for match in re.finditer(injection_pattern, text, re.IGNORECASE):
            result["injections"].append(match.group(1).strip())

        return result

    def _parse_gobuster_output(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse Gobuster JSON output into structured directory findings.

        :param text: Raw Gobuster JSON output.
        :return: List of discovered directories/paths with status codes and sizes.
        """
        results = []
        for line in text.strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                results.append({
                    "path": data.get("path", ""),
                    "status": data.get("status", 0),
                    "length": data.get("length", 0),
                    "words": data.get("words", 0),
                    "lines": data.get("lines", 0),
                    "content_type": data.get("content_type", ""),
                })
            except json.JSONDecodeError:
                continue
        return results

    def _parse_headers(self, text: str) -> Dict[str, str]:
        """
        Parse HTTP response headers from curl output.

        :param text: Raw curl -I output.
        :return: Dictionary of header name to value.
        """
        headers = {}
        for line in text.split("\n"):
            if ":" in line and not line.startswith("*") and not line.startswith("HTTP/"):
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        return headers

    def _check_security_headers(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        Analyze security headers and provide recommendations.

        Checks for presence and proper configuration of standard security headers
        such as CSP, HSTS, X-Frame-Options, etc.

        :param headers: Dictionary of HTTP response headers.
        :return: List of findings with status (present/missing/misconfigured) and recommendations.
        """
        security_headers = {
            "strict-transport-security": {
                "name": "Strict-Transport-Security (HSTS)",
                "recommendation": "Add 'Strict-Transport-Security: max-age=31536000; includeSubDomains' to enforce HTTPS.",
                "risk": "Without HSTS, browsers may connect via HTTP on first visit, vulnerable to downgrade attacks.",
            },
            "content-security-policy": {
                "name": "Content-Security-Policy (CSP)",
                "recommendation": "Implement a restrictive CSP policy to prevent XSS and data injection attacks.",
                "risk": "Without CSP, the page is vulnerable to Cross-Site Scripting (XSS) and code injection.",
            },
            "x-content-type-options": {
                "name": "X-Content-Type-Options",
                "recommendation": "Set 'X-Content-Type-Options: nosniff' to prevent MIME type sniffing.",
                "risk": "Browsers may 'sniff' content types, potentially executing uploaded files as scripts.",
            },
            "x-frame-options": {
                "name": "X-Frame-Options",
                "recommendation": "Set 'X-Frame-Options: DENY' or 'SAMEORIGIN' to prevent clickjacking.",
                "risk": "Page can be embedded in iframes on other sites, enabling clickjacking attacks.",
            },
            "x-xss-protection": {
                "name": "X-XSS-Protection",
                "recommendation": "Set 'X-XSS-Protection: 1; mode=block' for legacy browser XSS filtering.",
                "risk": "Legacy browsers without this header may not activate their XSS auditor.",
            },
            "referrer-policy": {
                "name": "Referrer-Policy",
                "recommendation": "Set 'Referrer-Policy: strict-origin-when-cross-origin' to control referrer leakage.",
                "risk": "Sensitive URL paths may be leaked to third-party sites via the Referer header.",
            },
            "permissions-policy": {
                "name": "Permissions-Policy (Feature-Policy)",
                "recommendation": "Restrict browser features like camera, microphone, geolocation to prevent unauthorized use.",
                "risk": "Without restrictions, malicious scripts may access device features.",
            },
        }

        findings = []
        for header_key, info in security_headers.items():
            if header_key in headers:
                findings.append({
                    "header": info["name"],
                    "status": "present",
                    "value": headers[header_key],
                    "recommendation": "",
                })
            else:
                findings.append({
                    "header": info["name"],
                    "status": "missing",
                    "value": None,
                    "recommendation": info["recommendation"],
                    "risk": info["risk"],
                })

        return findings

    @tool(name='sqlmap_scan')
    def sqlmap_scan(self, target_url: str, method: str = "GET", data: str = "", level: int = 3,
                    risk: int = 2, dbms: str = "", threads: int = 5) -> Dict[str, Any]:
        """
        Run SQLMap to detect and exploit SQL injection vulnerabilities.

        SQLMap automates the process of detecting and exploiting SQL injection flaws.
        It supports multiple SQL injection techniques and can enumerate databases,
        tables, and columns.

        :param target_url: Target URL with injectable parameter (e.g., 'http://example.com/page?id=1').
        :param method: HTTP method ('GET' or 'POST'). Default: 'GET'.
        :param data: POST data string (required if method is 'POST', e.g., 'username=admin&password=test').
        :param level: Detection level (1-5). Higher levels test more parameters/payloads. Default: 3.
        :param risk: Risk level (1-3). Higher levels use more risky payloads that may cause side effects. Default: 2.
        :param dbms: Force specific DBMS (e.g., 'mysql', 'postgresql', 'mssql', 'oracle'). Empty for auto-detect.
        :param threads: Number of concurrent threads (default: 5).
        :return: Structured result with injection findings, database type, and optionally enumerated data.
        """
        target_url = self._normalize_url(target_url)
        if not self._validate_target(target_url):
            return {"status": "error", "message": f"Target '{target_url}' is not in the authorized scope."}

        if not 1 <= level <= 5:
            return {"status": "error", "message": "Level must be between 1 and 5."}
        if not 1 <= risk <= 3:
            return {"status": "error", "message": "Risk must be between 1 and 3."}

        cmd = [
            "sqlmap",
            "-u", target_url,
            "--level", str(level),
            "--risk", str(risk),
            "--threads", str(threads),
            "--batch",
            "--random-agent",
            "--output-dir", self._workspace_root,
        ]

        if method.upper() == "POST" and data:
            cmd.extend(["--method", "POST", "--data", data])

        if dbms:
            cmd.extend(["--dbms", dbms])

        result = self._run_command(cmd, timeout=1800)

        if result["return_code"] not in (0, 1):
            return {"status": "error", "message": f"SQLMap failed: {result['stderr']}"}

        parsed = self._parse_sqlmap_output(result.get("stdout", "") + result.get("stderr", ""))

        output = f"### 💉 SQLMap Scan: {target_url}\n\n"
        output += f"Method: {method} | Level: {level} | Risk: {risk}\n\n"

        if parsed["injections"]:
            output += "#### ✅ SQL Injection Detected!\n\n"
            output += f"**Injection Types:** {', '.join(parsed['injections'])}\n"
            if parsed["db_type"]:
                output += f"**Backend DBMS:** {parsed['db_type']}\n"
            if parsed["current_db"]:
                output += f"**Current Database:** `{parsed['current_db']}`\n"
            if parsed["parameters"]:
                output += "\n**Vulnerable Parameters:**\n"
                for p in parsed["parameters"]:
                    output += f"- `{p['name']}` ({p['type']})\n"
        else:
            output += "#### ❌ No SQL Injection Detected\n\n"
            output += "The target parameters do not appear to be vulnerable to SQL injection at the tested level.\n"
            output += "Consider increasing the detection level or risk level for more thorough testing.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target_url": target_url,
                "method": method,
                "level": level,
                "risk": risk,
                **parsed,
                "vulnerable": len(parsed["injections"]) > 0,
            }
        }

    @tool(name='dir_bruteforce')
    def dir_bruteforce(self, target_url: str, wordlist: str = "/usr/share/wordlists/dirb/common.txt",
                       extensions: str = "", threads: int = 10, status_codes: str = "200,204,301,302,403") -> Dict[str, Any]:
        """
        Discover hidden directories and files using Gobuster.

        Performs directory and file enumeration by brute-forcing common paths against
        a web server. This helps discover admin panels, backup files, configuration files,
        and other sensitive resources.

        :param target_url: Target base URL (e.g., 'http://example.com'). Do not include trailing slash.
        :param wordlist: Path to wordlist file. Defaults to dirb's common wordlist.
        :param extensions: Comma-separated file extensions to append (e.g., 'php,html,txt,bak').
        :param threads: Number of concurrent threads (default: 10).
        :param status_codes: Comma-separated HTTP status codes to include in results.
        :return: Structured list of discovered paths with status codes, sizes, and content types.
        """
        target_url = self._normalize_url(target_url)
        if not self._validate_target(target_url):
            return {"status": "error", "message": f"Target '{target_url}' is not in the authorized scope."}

        cmd = [
            "gobuster", "dir",
            "-u", target_url,
            "-w", wordlist,
            "-t", str(threads),
            "-s", status_codes,
            "--no-error",
            "-json",
        ]

        if extensions:
            cmd.extend(["-x", extensions])

        result = self._run_command(cmd, timeout=1200)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"Directory brute-force failed: {result['stderr']}"}

        findings = self._parse_gobuster_output(result["stdout"])

        # Group by status code
        by_status = {}
        for f in findings:
            by_status.setdefault(str(f["status"]), []).append(f)

        output = f"### 📂 Directory Brute-Force: {target_url}\n\n"
        output += f"Wordlist: {wordlist} | Threads: {threads}\n\n"
        output += f"Found **{len(findings)}** path(s)\n\n"

        for status, items in sorted(by_status.items()):
            status_emoji = "✅" if status.startswith("2") else ("🔄" if status.startswith("3") else "⛔")
            output += f"#### {status_emoji} Status {status} ({len(items)} results)\n\n"
            output += "| Path | Size | Content-Type |\n"
            output += "|------|------|-------------|\n"
            for item in items:
                size = f"{item['length']} bytes" if item['length'] else "-"
                ct = item.get('content_type', '-')[:50]
                output += f"| `{item['path']}` | {size} | {ct} |\n"
            output += "\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target_url": target_url,
                "wordlist": wordlist,
                "findings": findings,
                "finding_count": len(findings),
                "by_status": {k: len(v) for k, v in by_status.items()},
            }
        }

    @tool(name='header_check')
    def header_check(self, target_url: str) -> Dict[str, Any]:
        """
        Analyze HTTP security headers of a target.

        Fetches the HTTP response headers and analyzes them for missing or
        misconfigured security headers. Provides recommendations for each issue found.

        :param target_url: Target URL (e.g., 'https://example.com').
        :return: Structured analysis of security headers with findings and recommendations.
        """
        target_url = self._normalize_url(target_url)
        if not self._validate_target(target_url):
            return {"status": "error", "message": f"Target '{target_url}' is not in the authorized scope."}

        cmd = ["curl", "-sI", "-L", "--max-time", "15", target_url]
        result = self._run_command(cmd, timeout=30)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"Failed to fetch headers: {result['stderr']}"}

        headers = self._parse_headers(result["stdout"])
        findings = self._check_security_headers(headers)

        output = f"### 🛡️ Security Header Analysis: {target_url}\n\n"

        # Show raw headers
        output += "**Raw Response Headers:**\n```\n"
        for key, value in headers.items():
            output += f"{key}: {value}\n"
        output += "```\n\n"

        # Security analysis
        present = [f for f in findings if f["status"] == "present"]
        missing = [f for f in findings if f["status"] == "missing"]

        output += f"### Summary: {len(present)}/{len(findings)} headers present\n\n"

        if missing:
            output += f"#### ⚠️ Missing Headers ({len(missing)})\n\n"
            for f in missing:
                output += f"**{f['header']}** — MISSING\n"
                output += f"  Risk: {f.get('risk', 'N/A')}\n"
                output += f"  Recommendation: {f.get('recommendation', 'N/A')}\n\n"

        if present:
            output += f"#### ✅ Present Headers ({len(present)})\n\n"
            for f in present:
                output += f"**{f['header']}** — `{f['value']}`\n\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target_url": target_url,
                "headers": headers,
                "findings": findings,
                "present_count": len(present),
                "missing_count": len(missing),
            }
        }

    @tool(name='ssl_check')
    def ssl_check(self, target_url: str) -> Dict[str, Any]:
        """
        Check SSL/TLS configuration of a target.

        Analyzes the SSL/TLS certificate and configuration, including certificate chain,
        supported protocols, cipher suites, and known vulnerabilities.

        :param target_url: Target HTTPS URL (e.g., 'https://example.com').
        :return: Structured SSL/TLS analysis with certificate details and configuration issues.
        """
        target_url = self._normalize_url(target_url)
        if not self._validate_target(target_url):
            return {"status": "error", "message": f"Target '{target_url}' is not in the authorized scope."}

        parsed = urlparse(target_url)
        host = parsed.hostname or target_url.replace("https://", "").replace("http://", "")
        port = str(parsed.port or 443)

        # Use openssl to check SSL
        cmd = ["openssl", "s_client", "-connect", f"{host}:{port}", "-servername", host, "-brief"]
        result = self._run_command(cmd, timeout=30)

        output = f"### 🔒 SSL/TLS Check: {host}:{port}\n\n"

        if result["stdout"]:
            output += "**OpenSSL Brief Output:**\n```\n"
            output += result["stdout"][:2000]
            output += "```\n\n"

        # Extract certificate details
        cert_info = {}
        cert_patterns = {
            "subject": r"subject=\s*(.+)",
            "issuer": r"issuer=\s*(.+)",
            "not_before": r"notBefore=(.+)",
            "not_after": r"notAfter=(.+)",
            "protocol": r"Protocol\s*:\s*(.+)",
            "cipher": r"Cipher\s*:\s*(.+)",
            "verify": r"Verify return code:\s*(.+)",
        }

        for field, pattern in cert_patterns.items():
            match = re.search(pattern, result["stdout"])
            if match:
                cert_info[field] = match.group(1).strip()

        if cert_info.get("subject"):
            output += "**Certificate Details:**\n\n"
            output += f"- **Subject:** {cert_info.get('subject', 'N/A')}\n"
            output += f"- **Issuer:** {cert_info.get('issuer', 'N/A')}\n"
            output += f"- **Valid From:** {cert_info.get('not_before', 'N/A')}\n"
            output += f"- **Valid Until:** {cert_info.get('not_after', 'N/A')}\n"
            output += f"- **Protocol:** {cert_info.get('protocol', 'N/A')}\n"
            output += f"- **Cipher:** {cert_info.get('cipher', 'N/A')}\n"
            output += f"- **Verification:** {cert_info.get('verify', 'N/A')}\n"
        elif not result["stdout"]:
            output += "⚠️ Could not establish SSL/TLS connection. The target may not support HTTPS or uses a self-signed certificate.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "host": host,
                "port": port,
                "certificate": cert_info,
            }
        }

    @tool(name='tech_detect')
    def tech_detect(self, target_url: str) -> Dict[str, Any]:
        """
        Detect technologies used by a web application.

        Identifies web servers, CMS, programming languages, frameworks,
        JavaScript libraries, and other technologies running on the target.

        :param target_url: Target URL (e.g., 'http://example.com').
        :return: Structured list of detected technologies with versions.
        """
        target_url = self._normalize_url(target_url)
        if not self._validate_target(target_url):
            return {"status": "error", "message": f"Target '{target_url}' is not in the authorized scope."}

        cmd = ["whatweb", "-q", "--color=never", target_url]
        result = self._run_command(cmd, timeout=60)

        if result["return_code"] != 0 and not result["stdout"]:
            # Fallback to curl-based detection
            cmd_curl = ["curl", "-sI", "--max-time", "15", target_url]
            curl_result = self._run_command(cmd_curl, timeout=30)

            if curl_result["stdout"]:
                headers = self._parse_headers(curl_result["stdout"])
                technologies = []

                server_map = {
                    "apache": "Apache HTTP Server",
                    "nginx": "Nginx",
                    "iis": "Microsoft IIS",
                    "cloudflare": "Cloudflare CDN",
                    "express": "Express.js",
                    "php": "PHP",
                }

                for key, value in headers.items():
                    for sig, name in server_map.items():
                        if sig in value.lower():
                            technologies.append({"name": name, "version": value, "source": "header"})

                # Check common technology indicators
                for header in headers:
                    if "x-powered-by" in header:
                        technologies.append({"name": headers[header], "version": "", "source": "x-powered-by"})

                output = f"### 🔧 Technology Detection: {target_url}\n\n"
                output += f"Detected **{len(technologies)}** technologies via HTTP headers\n\n"

                for tech in technologies:
                    output += f"- **{tech['name']}**"
                    if tech.get("version"):
                        output += f" ({tech['version']})"
                    output += f" [source: {tech['source']}]\n"

                return {
                    "status": "success",
                    "stdout": output,
                    "data": {"technologies": technologies, "target_url": target_url},
                }

            return {"status": "error", "message": f"Technology detection failed: {result['stderr']}"}

        # Parse WhatWeb output
        technologies = []
        for line in result["stdout"].strip().split("\n"):
            line = line.strip()
            if line:
                # WhatWeb format: URL [tech1, tech2[v], ...]
                match = re.search(r'\[(.+)\]', line)
                if match:
                    techs = match.group(1)
                    for item in re.findall(r'([^\[,]+)(?:\[([^\]]+)\])?', techs):
                        technologies.append({
                            "name": item[0].strip(),
                            "version": item[1].strip() if item[1] else "",
                        })

        output = f"### 🔧 Technology Detection: {target_url}\n\n"
        output += f"Detected **{len(technologies)}** technologies\n\n"

        if technologies:
            for tech in technologies:
                output += f"- **{tech['name']}**"
                if tech.get("version"):
                    output += f" v{tech['version']}"
                output += "\n"
        else:
            output += "No specific technologies identified.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target_url": target_url,
                "technologies": technologies,
                "tech_count": len(technologies),
            }
        }

    @tool(name='vhost_enum')
    def vhost_enum(self, target_url: str, wordlist: str = "/usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt",
                   threads: int = 20) -> Dict[str, Any]:
        """
        Enumerate virtual hosts on a target server.

        Discovers virtual hosts (subdomains) that resolve to the same IP address
        as the target, which can reveal hidden applications and administrative interfaces.

        :param target_url: Target base URL (e.g., 'http://example.com').
        :param wordlist: Path to vhost wordlist.
        :param threads: Number of concurrent threads (default: 20).
        :return: Structured list of discovered virtual hosts with status codes and sizes.
        """
        target_url = self._normalize_url(target_url)
        if not self._validate_target(target_url):
            return {"status": "error", "message": f"Target '{target_url}' is not in the authorized scope."}

        cmd = [
            "gobuster", "vhost",
            "-u", target_url,
            "-w", wordlist,
            "-t", str(threads),
            "--no-error",
            "-json",
        ]

        result = self._run_command(cmd, timeout=1200)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"VHost enumeration failed: {result['stderr']}"}

        vhosts = []
        for line in result["stdout"].strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                vhosts.append({
                    "vhost": data.get("input", ""),
                    "url": data.get("url", ""),
                    "status": data.get("status", 0),
                    "length": data.get("length", 0),
                })
            except json.JSONDecodeError:
                continue

        output = f"### 🏠 Virtual Host Enumeration: {target_url}\n\n"
        output += f"Found **{len(vhosts)}** virtual host(s)\n\n"

        if vhosts:
            output += "| VHost | URL | Status | Size |\n"
            output += "|-------|-----|--------|------|\n"
            for v in vhosts:
                output += f"| {v['vhost']} | {v['url']} | {v['status']} | {v['length']} bytes |\n"
        else:
            output += "No additional virtual hosts discovered.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target_url": target_url,
                "vhosts": vhosts,
                "vhost_count": len(vhosts),
            }
        }
