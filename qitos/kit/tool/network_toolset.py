"""
Network and traffic analysis tools.

Provides network operations: packet_capture, traffic_analyze, dns_sniff,
arp_scan, traceroute, tcp_connect, http_request, scapy_craft.
Uses subprocess to call industry-standard network tools (tcpdump, tshark, traceroute, curl).
All operations MUST be performed within authorized scope only.
"""

import json
import re
import subprocess
import os
import time
from typing import Any, Dict, List, Optional

from qitos.core.tool import tool


class NetworkToolSet:
    """
    Network toolset providing traffic analysis and network operation capabilities.

    Supports packet capture (tcpdump), traffic analysis (tshark), DNS sniffing,
    ARP scanning, traceroute, TCP connections, HTTP requests, and Scapy-based
    packet crafting helpers.
    All targets must be within authorized scope.
    """

    def __init__(self, authorized_targets: Optional[List[str]] = None, workspace_root: str = "."):
        """
        Initialize network toolset.

        :param authorized_targets: List of authorized target IPs/networks.
        :param workspace_root: Root directory for storing capture files and results.
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

    def _parse_tcpdump_output(self, text: str, max_lines: int = 100) -> List[Dict[str, Any]]:
        """
        Parse tcpdump verbose output into structured packet summaries.

        Extracts timestamp, source/destination IPs and ports, protocol, flags,
        and packet length from tcpdump's textual output.

        :param text: Raw tcpdump output.
        :param max_lines: Maximum number of packets to parse.
        :return: List of parsed packet summaries.
        """
        packets = []
        # tcpdump -nn -vv format:
        # timestamp IP proto src_port > dst_port: flags, length
        pattern = r'(\d{2}:\d{2}:\d{2}\.\d+)\s+IP\s+(\S+)\.(\d+)\s+>\s+(\S+)\.(\d+):\s+(Flags\s+\[([^\]]+)\].*?,\s+(\d+))?'

        for line in text.split("\n")[:max_lines]:
            match = re.search(pattern, line)
            if match:
                packets.append({
                    "time": match.group(1),
                    "src_ip": match.group(2),
                    "src_port": match.group(3),
                    "dst_ip": match.group(4),
                    "dst_port": match.group(5),
                    "flags": match.group(7) or "",
                    "length": match.group(8) or "",
                })

        return packets

    @tool(name='packet_capture')
    def packet_capture(self, interface: str = "", target: str = "", capture_filter: str = "",
                       duration: int = 30, count: int = 0, output_file: str = "",
                       verbose: bool = True) -> Dict[str, Any]:
        """
        Capture network packets using tcpdump.

        Captures live network traffic for analysis. Supports BPF filters for
        selective capture and can save to PCAP format for later analysis.

        :param interface: Network interface to capture on (e.g., 'eth0'). Auto-detects if empty.
        :param target: Target IP to filter for (only capture traffic to/from this IP).
        :param capture_filter: Custom BPF filter expression (e.g., 'port 80', 'tcp', 'icmp').
        :param duration: Capture duration in seconds (default: 30). Set 0 for manual stop.
        :param count: Number of packets to capture then stop (0 = no limit, use duration instead).
        :param output_file: Path to save PCAP file. If empty, uses workspace_root/capture_<timestamp>.pcap.
        :param verbose: Enable verbose output with protocol details (default: True).
        :return: Capture summary with packet count, protocol distribution, and key observations.
        """
        if target and not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        if not output_file:
            timestamp = int(time.time())
            output_file = os.path.join(self._workspace_root, f"capture_{timestamp}.pcap")

        cmd = ["tcpdump", "-nn"]

        if verbose:
            cmd.append("-vv")
        if interface:
            cmd.extend(["-i", interface])
        if output_file:
            cmd.extend(["-w", output_file])
        if count > 0:
            cmd.extend(["-c", str(count)])

        # Build filter
        filter_parts = []
        if target:
            filter_parts.append(f"host {target}")
        if capture_filter:
            filter_parts.append(capture_filter)
        if filter_parts:
            cmd.append(" and ".join(filter_parts))

        # Run capture with timeout
        if duration > 0:
            cmd_str = " ".join(cmd)
            full_cmd = f"timeout {duration} {cmd_str}"
            result = self._run_command(["bash", "-c", full_cmd], timeout=duration + 10)
        else:
            return {"status": "error", "message": "Non-timed captures require manual execution. Set duration > 0."}

        output = f"### 📡 Packet Capture\n\n"
        output += f"**Interface:** {interface or 'auto'}\n"
        output += f"**Duration:** {duration}s\n"
        output += f"**Filter:** {capture_filter or 'none'}"
        if target:
            output += f" (target: {target})"
        output += f"\n**Output:** `{output_file}`\n\n"

        stderr = result.get("stderr", "")

        # Parse capture statistics from tcpdump output
        stats = {}
        stats_patterns = {
            "packets_captured": r"(\d+)\s+packets\s+captured",
            "packets_received": r"(\d+)\s+packets\s+received\s+by\s+filter",
            "packets_dropped": r"(\d+)\s+packets\s+dropped\s+by\s+kernel",
        }

        for key, pattern in stats_patterns.items():
            match = re.search(pattern, stderr)
            if match:
                stats[key] = int(match.group(1))

        if stats:
            output += "#### Capture Statistics\n\n"
            output += f"- **Packets captured:** {stats.get('packets_captured', 'N/A')}\n"
            output += f"- **Packets received by filter:** {stats.get('packets_received', 'N/A')}\n"
            output += f"- **Packets dropped by kernel:** {stats.get('packets_dropped', 'N/A')}\n\n"
        else:
            output += "Capture completed. No statistics available.\n\n"

        # File size
        try:
            file_size = os.path.getsize(output_file)
            output += f"**PCAP file size:** {file_size:,} bytes\n"
        except OSError:
            output += "⚠️ PCAP file may not have been created.\n"

        output += f"\nTo analyze: `tshark -r {output_file}` or open in Wireshark.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "interface": interface,
                "duration": duration,
                "output_file": output_file,
                "stats": stats,
                "target": target,
            }
        }

    @tool(name='traffic_analyze')
    def traffic_analyze(self, pcap_file: str, display_filter: str = "",
                        protocol: str = "", count: int = 50) -> Dict[str, Any]:
        """
        Analyze captured PCAP file using tshark.

        Parses and analyzes a PCAP file to extract protocol statistics,
        conversation details, and specific packet data.

        :param pcap_file: Path to the PCAP file to analyze.
        :param display_filter: Wireshark display filter (e.g., 'http.request', 'tcp.port==80', 'dns').
        :param protocol: Focus on specific protocol statistics (e.g., 'http', 'dns', 'tcp', 'tls').
        :param count: Number of packets to display in detail (default: 50).
        :return: Structured analysis with protocol distribution, top talkers, and filtered results.
        """
        if not os.path.isfile(pcap_file):
            return {"status": "error", "message": f"PCAP file not found: {pcap_file}"}

        output = f"### 📊 Traffic Analysis: `{pcap_file}`\n\n"

        # Protocol hierarchy
        proto_cmd = ["tshark", "-r", pcap_file, "-q", "-z", "io,phs"]
        proto_result = self._run_command(proto_cmd, timeout=120)

        if proto_result["stdout"]:
            output += "#### Protocol Hierarchy\n\n```\n"
            output += proto_result["stdout"][:2000]
            output += "```\n\n"

        # Top conversations
        conv_cmd = ["tshark", "-r", pcap_file, "-q", "-z", "conv,ip"]
        conv_result = self._run_command(conv_cmd, timeout=120)

        if conv_result["stdout"]:
            output += "#### Top Conversations (IP)\n\n```\n"
            conv_lines = conv_result["stdout"].strip().split("\n")[:15]
            output += "\n".join(conv_lines)
            output += "```\n\n"

        # Apply display filter if specified
        if display_filter:
            filter_cmd = ["tshark", "-r", pcap_file, "-Y", display_filter, "-c", str(count), "-T", "fields",
                          "-e", "frame.time", "-e", "ip.src", "-e", "ip.dst",
                          "-e", "_ws.col.Protocol", "-e", "frame.len"]
            filter_result = self._run_command(filter_cmd, timeout=120)

            if filter_result["stdout"]:
                output += f"#### Filtered Packets (`{display_filter}`)\n\n"
                output += "| Time | Source | Destination | Protocol | Length |\n"
                output += "|------|--------|-------------|----------|--------|\n"
                for line in filter_result["stdout"].strip().split("\n")[:count]:
                    fields = line.split("\t")
                    if len(fields) >= 5:
                        output += f"| {fields[0]} | {fields[1]} | {fields[2]} | {fields[3]} | {fields[4]} |\n"
            else:
                output += f"No packets matched filter: `{display_filter}`\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "pcap_file": pcap_file,
                "display_filter": display_filter,
            }
        }

    @tool(name='dns_sniff')
    def dns_sniff(self, interface: str = "", duration: int = 30, output_file: str = "") -> Dict[str, Any]:
        """
        Capture and analyze DNS queries on the network.

        Sniffs DNS traffic to identify domains being queried, DNS servers being used,
        and potential DNS-related security issues (e.g., DNS tunneling indicators).

        :param interface: Network interface (auto-detect if empty).
        :param duration: Capture duration in seconds (default: 30).
        :param output_file: Path to save PCAP file.
        :return: Structured DNS query log with queried domains, query types, and response codes.
        """
        if not output_file:
            timestamp = int(time.time())
            output_file = os.path.join(self._workspace_root, f"dns_capture_{timestamp}.pcap")

        # Capture DNS traffic (port 53)
        capture_filter = "port 53"
        cmd = ["tcpdump", "-nn", "-vv", "-i", interface if interface else "any"]
        cmd.extend(["-w", output_file, capture_filter])

        full_cmd = f"timeout {duration} {' '.join(cmd)}"
        result = self._run_command(["bash", "-c", full_cmd], timeout=duration + 10)

        # Now analyze the captured DNS traffic
        tshark_cmd = [
            "tshark", "-r", output_file, "-Y", "dns",
            "-T", "fields",
            "-e", "frame.time", "-e", "ip.src", "-e", "ip.dst",
            "-e", "dns.qry.name", "-e", "dns.qry.type", "-e", "dns.flags.response",
            "-e", "dns.a",
        ]
        tshark_result = self._run_command(tshark_cmd, timeout=60)

        queries = []
        for line in tshark_result["stdout"].strip().split("\n"):
            fields = line.split("\t")
            if len(fields) >= 5:
                is_response = fields[4] == "1"
                queries.append({
                    "time": fields[0],
                    "src": fields[1],
                    "dst": fields[2],
                    "domain": fields[3] or "",
                    "query_type": fields[4] if not is_response else "",
                    "is_response": is_response,
                    "answer": fields[5] if len(fields) > 5 else "",
                })

        output = f"### 🌐 DNS Sniff: {duration}s\n\n"
        output += f"**Interface:** {interface or 'any'}\n"
        output += f"**Queries captured:** {len(queries)}\n\n"

        # Count unique domains
        domains = set(q["domain"] for q in queries if q["domain"])
        output += f"**Unique domains:** {len(domains)}\n\n"

        if queries:
            output += "| Time | Source | Domain | Answer |\n"
            output += "|------|--------|--------|--------|\n"
            for q in queries[:30]:
                output += f"| {q['time']} | {q['src']} | `{q['domain']}` | {q.get('answer', '')} |\n"

            if len(queries) > 30:
                output += f"\nShowing first 30 of {len(queries)} queries.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "interface": interface,
                "duration": duration,
                "output_file": output_file,
                "queries": queries,
                "unique_domains": list(domains),
                "query_count": len(queries),
            }
        }

    @tool(name='arp_scan')
    def arp_scan(self, target: str, interface: str = "") -> Dict[str, Any]:
        """
        Perform ARP scan to discover hosts on a local network.

        Uses ARP requests to discover live hosts on the same broadcast domain.
        This is faster and more reliable than ICMP ping for local network discovery.

        :param target: Target subnet or IP range (e.g., '192.168.1.0/24', '10.0.0.1/16').
        :param interface: Network interface to use (auto-detect if empty).
        :return: List of discovered hosts with IP and MAC addresses.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        cmd = ["arp-scan", "--interface=" + interface if interface else "", target]
        cmd = [c for c in cmd if c]

        result = self._run_command(cmd, timeout=120)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"ARP scan failed: {result['stderr']}"}

        hosts = []
        for line in result["stdout"].split("\n"):
            # arp-scan format: IP	MAC	Hardware/Manufacturer
            match = re.match(r'^(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:]+)\s+(.+)', line.strip())
            if match:
                hosts.append({
                    "ip": match.group(1),
                    "mac": match.group(2),
                    "manufacturer": match.group(3).strip(),
                })

        output = f"### 📡 ARP Scan: {target}\n\n"
        output += f"Found **{len(hosts)}** host(s)\n\n"

        if hosts:
            output += "| IP Address | MAC Address | Manufacturer |\n"
            output += "|------------|-------------|---------------|\n"
            for h in hosts:
                output += f"| {h['ip']} | `{h['mac']}` | {h['manufacturer']} |\n"
        else:
            output += "No hosts discovered. Check the interface and target subnet.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "hosts": hosts,
                "host_count": len(hosts),
            }
        }

    @tool(name='traceroute')
    def traceroute(self, target: str, max_hops: int = 30, method: str = "udp") -> Dict[str, Any]:
        """
        Trace the network path to a target host.

        Shows each hop (router/intermediate node) between your machine and the target,
        with response times. Useful for network mapping and identifying firewalls.

        :param target: Target IP or hostname.
        :param max_hops: Maximum number of hops to trace (default: 30).
        :param method: Trace method. Options:
            - 'udp': UDP packets (default, may be blocked by firewalls).
            - 'tcp': TCP SYN packets (bypasses some firewalls, uses port 80).
            - 'icmp': ICMP packets.
        :return: Traceroute output with hop-by-hop details including latency.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        if method == "tcp":
            cmd = ["traceroute", "-T", "-m", str(max_hops), target]
        elif method == "icmp":
            cmd = ["traceroute", "-I", "-m", str(max_hops), target]
        else:
            cmd = ["traceroute", "-m", str(max_hops), target]

        result = self._run_command(cmd, timeout=120)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"Traceroute failed: {result['stderr']}"}

        output = f"### 🗺️ Traceroute: {target} (method: {method})\n\n"
        output += "```\n"
        output += result["stdout"][:3000]
        output += "```\n\n"

        hops = []
        for line in result["stdout"].split("\n"):
            match = re.match(r'^\s*(\d+)\s+(\S+)', line)
            if match:
                hops.append({
                    "hop": int(match.group(1)),
                    "address": match.group(2),
                })

        if hops:
            output += f"**Total hops:** {len(hops)}\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "method": method,
                "hops": hops,
                "hop_count": len(hops),
            }
        }

    @tool(name='http_request')
    def http_request(self, url: str, method: str = "GET", headers: Dict[str, str] = None,
                     data: str = "", follow_redirects: bool = True, timeout_sec: int = 15) -> Dict[str, Any]:
        """
        Send an HTTP request and display the full response.

        A flexible HTTP client for testing web services, checking endpoints,
        and analyzing server responses including headers, status codes, and body.

        :param url: Target URL (e.g., 'http://example.com/api/endpoint').
        :param method: HTTP method ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS').
        :param headers: Dictionary of HTTP headers to include (e.g., {'Content-Type': 'application/json', 'Authorization': 'Bearer token'}).
        :param data: Request body data (for POST/PUT/PATCH).
        :param follow_redirects: Whether to follow HTTP redirects (default: True).
        :param timeout_sec: Request timeout in seconds (default: 15).
        :return: Full HTTP response with status code, headers, and body.
        """
        cmd = ["curl", "-s", "-i", "-X", method]

        if headers:
            for key, value in headers.items():
                cmd.extend(["-H", f"{key}: {value}"])

        if data:
            cmd.extend(["-d", data])

        if not follow_redirects:
            cmd.append("--max-redirs 0")

        cmd.extend(["--max-time", str(timeout_sec), url])

        result = self._run_command(cmd, timeout=timeout_sec + 10)

        raw_output = result["stdout"] or ""

        # Split headers and body
        parts = raw_output.split("\r\n\r\n", 1)
        header_section = parts[0] if parts else ""
        body = parts[1] if len(parts) > 1 else ""

        # Parse status line
        status_line = ""
        response_headers = {}
        for i, line in enumerate(header_section.split("\r\n")):
            if i == 0:
                status_line = line
            elif ":" in line:
                key, _, value = line.partition(":")
                response_headers[key.strip().lower()] = value.strip()

        # Status code
        status_code = 0
        status_match = re.search(r'HTTP/\d\.\d\s+(\d+)', status_line)
        if status_match:
            status_code = int(status_match.group(1))

        output = f"### 🌐 HTTP Request: {method} {url}\n\n"
        output += f"**Status:** {status_line}\n"
        output += f"**Response time:** {timeout_sec}s max\n\n"

        # Headers
        output += "**Response Headers:**\n```\n"
        for key, value in response_headers.items():
            output += f"{key}: {value}\n"
        output += "```\n\n"

        # Body (truncated if too long)
        if body:
            max_body = 5000
            if len(body) > max_body:
                output += f"**Response Body** (truncated, {len(body)} chars total):\n```\n"
                output += body[:max_body]
                output += f"\n... ({len(body) - max_body} more characters)\n```\n\n"
            else:
                output += f"**Response Body** ({len(body)} chars):\n```\n"
                output += body
                output += "\n```\n\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "url": url,
                "method": method,
                "status_code": status_code,
                "status_line": status_line,
                "headers": response_headers,
                "body": body[:10000],
                "body_length": len(body),
            }
        }

    @tool(name='scapy_craft')
    def scapy_craft(self, script: str) -> Dict[str, Any]:
        """
    Execute a Scapy packet crafting script.

    Scapy is a powerful Python-based packet manipulation tool. This function
    allows execution of pre-written Scapy scripts for custom packet crafting,
    protocol testing, and network reconnaissance tasks.

    :param script: Python code using Scapy. The script has access to the `scapy` module
        (imported as `sc`). Common operations:
        - Create packets: `sc.IP(dst='target')/sc.TCP(dport=80, flags='S')`
        - Send packets: `sc.send(packet)`, `sc.sr1(packet)` (send and receive)
        - Sniff packets: `sc.sniff(filter='port 80', count=10)`
        - ARP requests: `sc.arping('192.168.1.0/24')`
    :return: Script execution result with output and any captured responses.
    """
        # Build a safe execution environment
        exec_script = f"""
import sys
from scapy.all import *
from scapy.utils import wrpcap, rdpcap

output = []
capture_file = "{os.path.join(self._workspace_root, '_scapy_capture.pcap')}"

try:
    result = {script}
    if result is not None:
        if isinstance(result, (list, tuple)):
            for item in result:
                output.append(repr(item))
                if hasattr(item, 'summary'):
                    output.append(item.summary())
        else:
            if hasattr(result, 'summary'):
                output.append(result.summary())
            else:
                output.append(str(result))
except Exception as e:
    output.append(f"Error: {{str(e)}}")

print("\\n".join(output))
"""

        # Write script to temp file
        script_path = os.path.join(self._workspace_root, "_scapy_script.py")
        with open(script_path, "w") as f:
            f.write(exec_script)

        cmd = ["python3", script_path]
        result = self._run_command(cmd, timeout=120)

        # Clean up
        try:
            os.remove(script_path)
        except OSError:
            pass

        output_text = result.get("stdout", "")

        output = f"### 📦 Scapy Script Execution\n\n"
        output += "**Script:**\n```python\n"
        output += script[:500]
        if len(script) > 500:
            output += "\n..."
        output += "\n```\n\n"

        output += "**Output:**\n```\n"
        output += output_text[:3000]
        if len(output_text) > 3000:
            output += "\n... (truncated)"
        output += "\n```\n\n"

        if result.get("stderr"):
            output += f"**Errors:**\n```\n{result['stderr'][:1000]}\n```\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "script": script,
                "output": output_text,
                "errors": result.get("stderr", ""),
            }
        }
