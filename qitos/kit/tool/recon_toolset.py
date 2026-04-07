"""
Reconnaissance tools.

Provides information gathering operations: port_scan, service_scan, os_detect,
host_discovery, subnet_scan, dns_enum, dns_lookup, whois_lookup, subdomain_enum,
subdomain_brute.
Uses subprocess to call industry-standard recon tools (nmap, dig, whois, dnsrecon, subfinder, amass).
All operations MUST be performed within authorized scope only.
"""

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from qitos.core.tool import tool


class ReconToolSet:
    """
    Reconnaissance toolset providing comprehensive information gathering capabilities.

    Supports network scanning, service identification, OS fingerprinting,
    DNS enumeration, WHOIS lookups, and subdomain discovery.
    All targets must be within authorized scope.
    """

    def __init__(self, authorized_targets: Optional[List[str]] = None, workspace_root: str = "."):
        """
        Initialize reconnaissance toolset.

        :param authorized_targets: List of authorized target IPs/domains/subnets. All operations will be validated against this list.
        :param workspace_root: Root directory for storing scan results, defaults to current directory.
        """
        self._authorized_targets = authorized_targets or []
        self._workspace_root = workspace_root

    def _validate_target(self, target: str) -> bool:
        """
        Validate that a target is within the authorized scope.

        Performs basic format validation and checks against authorized_targets if configured.
        :param target: Target IP, domain, or subnet to validate.
        :return: True if target is authorized, False otherwise.
        """
        # Basic format validation
        ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$'
        domain_pattern = r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'

        is_valid = bool(re.match(ip_pattern, target)) or bool(re.match(domain_pattern, target))
        if not is_valid:
            return False

        # Check against authorized targets if configured
        if not self._authorized_targets:
            return True

        for auth_target in self._authorized_targets:
            if target == auth_target:
                return True
            # Check if target is within an authorized subnet
            if '/' in auth_target and self._ip_in_subnet(target, auth_target):
                return True
            # Check if target is a subdomain of an authorized domain
            if target.endswith('.' + auth_target):
                return True

        return False

    def _ip_in_subnet(self, ip: str, subnet: str) -> bool:
        """
        Check if an IP address is within a given subnet.

        :param ip: IP address to check.
        :param subnet: Subnet in CIDR notation (e.g., '192.168.1.0/24').
        :return: True if IP is within the subnet.
        """
        try:
            import ipaddress
            return ipaddress.ip_address(ip) in ipaddress.ip_network(subnet, strict=False)
        except (ValueError, ImportError):
            return False

    def _run_command(self, cmd: List[str], timeout: int = 300) -> Dict[str, Any]:
        """
        Execute a shell command safely and capture output.

        :param cmd: Command and arguments as a list.
        :param timeout: Maximum execution time in seconds.
        :return: Dictionary with stdout, stderr, and return_code.
        """
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "return_code": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Command timed out", "return_code": -1}
        except FileNotFoundError:
            return {"stdout": "", "stderr": f"Tool not found: {cmd[0]}. Please ensure it is installed.", "return_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": f"Error executing command: {str(e)}", "return_code": -1}

    def _parse_nmap_xml(self, xml_output: str) -> Dict[str, Any]:
        """
        Parse Nmap XML output into a structured dictionary.

        Extracts host status, open ports with service versions, and OS fingerprint data.

        :param xml_output: Raw XML output from Nmap.
        :return: Structured dictionary with parsed results.
        """
        result = {"hosts": [], "scan_info": {}}

        try:
            root = ET.fromstring(xml_output)

            # Extract scan info
            scan_info = root.find("scaninfo")
            if scan_info is not None:
                result["scan_info"] = {
                    "type": scan_info.get("type", ""),
                    "protocol": scan_info.get("protocol", ""),
                    "num_services": scan_info.get("numservices", ""),
                    "start_time": scan_info.get("start", ""),
                }

            # Extract host data
            for host in root.findall("host"):
                host_info = {"addresses": [], "ports": [], "os": []}

                # Status
                status = host.find("status")
                if status is not None:
                    host_info["status"] = status.get("state", "unknown")

                # Addresses
                for addr in host.findall("address"):
                    host_info["addresses"].append({
                        "addr": addr.get("addr", ""),
                        "type": addr.get("addrtype", ""),
                    })

                # Ports
                for ports_elem in host.findall("ports"):
                    for port in ports_elem.findall("port"):
                        port_info = {
                            "port_id": port.get("portid", ""),
                            "protocol": port.get("protocol", ""),
                            "state": "",
                            "service": {},
                            "scripts": [],
                        }

                        state = port.find("state")
                        if state is not None:
                            port_info["state"] = state.get("state", "")

                        service = port.find("service")
                        if service is not None:
                            port_info["service"] = {
                                "name": service.get("name", ""),
                                "product": service.get("product", ""),
                                "version": service.get("version", ""),
                                "extrainfo": service.get("extrainfo", ""),
                                "banner": service.get("banner", ""),
                            }

                        # NSE script results
                        for script in port.findall("script"):
                            port_info["scripts"].append({
                                "id": script.get("id", ""),
                                "output": script.get("output", ""),
                            })

                        host_info["ports"].append(port_info)

                # OS detection
                for os_elem in host.findall("os"):
                    for osmatch in os_elem.findall("osmatch"):
                        os_info = {
                            "name": osmatch.get("name", ""),
                            "accuracy": osmatch.get("accuracy", ""),
                        }
                        host_info["os"].append(os_info)

                # Host scripts
                host_info["host_scripts"] = []
                for hs in host.findall("hostscript"):
                    for script in hs.findall("script"):
                        host_info["host_scripts"].append({
                            "id": script.get("id", ""),
                            "output": script.get("output", ""),
                        })

                result["hosts"].append(host_info)

        except ET.ParseError as e:
            result["parse_error"] = f"Failed to parse XML: {str(e)}"

        return result

    def _parse_whois(self, text: str) -> Dict[str, str]:
        """
        Parse raw WHOIS output into a structured dictionary.

        Extracts common fields such as domain name, registrar, creation/expiry dates,
        nameservers, and status codes.

        :param text: Raw WHOIS output.
        :return: Dictionary of extracted WHOIS fields.
        """
        fields = {}

        # Common WHOIS field patterns
        patterns = {
            "domain_name": r"(?:Domain Name|domain):[^\S\n]+(.+)",
            "registrar": r"(?:Registrar|Registrar Name):[^\S\n]+(.+)",
            "creation_date": r"(?:Creation Date|Created On|Registered On):[^\S\n]+(.+)",
            "expiry_date": r"(?:Registry Expiry Date|Expiry Date|Expires On):[^\S\n]+(.+)",
            "updated_date": r"(?:Updated Date|Last Updated On):[^\S\n]+(.+)",
            "name_servers": r"(?:Name Server|nserver):[^\S\n]+(.+)",
            "status": r"(?:Domain Status|status):[^\S\n]+(.+)",
            "registrant": r"(?:Registrant Organization|Registrant Name):[^\S\n]+(.+)",
            "dnssec": r"(?:DNSSEC):[^\S\n]+(.+)",
        }

        for field_name, pattern in patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                if field_name in ("name_servers", "status"):
                    fields[field_name] = [m.strip().lower() for m in matches if m.strip()]
                else:
                    fields[field_name] = matches[0].strip()

        return fields

    def _parse_dns_records(self, text: str) -> List[Dict[str, str]]:
        """
        Parse dig output into structured DNS record list.

        Extracts record type, name, TTL, class, and data from dig's textual output.

        :param text: Raw dig output.
        :return: List of dictionaries, each representing a DNS record.
        """
        records = []
        # Match ANSWER SECTION lines: name ttl class type data
        pattern = r'^([^\s]+)\s+(\d+)\s+(IN|CH|HS)\s+(A|AAAA|MX|NS|TXT|CNAME|SOA|SRV|PTR|CAA)\s+(.+)$'

        in_answer = False
        for line in text.split("\n"):
            if "ANSWER SECTION" in line:
                in_answer = True
                continue
            if in_answer and line.strip() == "":
                break
            if in_answer:
                match = re.match(pattern, line.strip())
                if match:
                    records.append({
                        "name": match.group(1),
                        "ttl": int(match.group(2)),
                        "class": match.group(3),
                        "type": match.group(4),
                        "data": match.group(5).strip(),
                    })

        return records

    @tool(name='host_discovery')
    def host_discovery(self, target: str, scan_type: str = "ping") -> Dict[str, Any]:
        """
        Discover live hosts on a network.

        Performs host discovery (ping sweep, ARP scan, etc.) to identify live machines.
        Supports multiple scan types for different network environments.

        :param target: Target IP range or subnet (e.g., '192.168.1.0/24'). Must be within authorized scope.
        :param scan_type: Discovery method. Options:
            - 'ping': ICMP echo request (default, may be blocked by firewalls).
            - 'arp': ARP scan (fast, local network only).
            - 'syn': SYN scan (stealthy, requires root).
            - 'udp': UDP scan (slow, can bypass some firewalls).
            - 'list': Combine multiple methods for thorough discovery.
        :return: Structured result with list of live hosts and their response times.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        scan_args = {
            "ping": ["-sn", "-PE"],        # Ping sweep
            "arp": ["-sn", "-PR"],          # ARP ping
            "syn": ["-sn", "-PS22,80,443"], # SYN ping on common ports
            "udp": ["-sn", "-PU53,67,68"],  # UDP ping on common ports
            "list": ["-sn", "-PE", "-PA", "-PU53"],  # Combined
        }

        if scan_type not in scan_args:
            return {"status": "error", "message": f"Invalid scan_type '{scan_type}'. Choose from: {', '.join(scan_args.keys())}"}

        args = scan_args[scan_type]
        cmd = ["nmap"] + args + [target, "-oX", "-"]

        result = self._run_command(cmd, timeout=600)

        if result["return_code"] not in (0, 1):
            return {"status": "error", "message": f"Scan failed: {result['stderr']}"}

        parsed = self._parse_nmap_xml(result["stdout"])

        live_hosts = []
        for host in parsed.get("hosts", []):
            if host.get("status") == "up":
                host_data = {
                    "status": "up",
                    "addresses": host.get("addresses", []),
                }
                # Extract response time from host scripts
                for script in host.get("host_scripts", []):
                    if script["id"] == "latency":
                        host_data["latency"] = script["output"]
                live_hosts.append(host_data)

        output = f"### 🖥️ Host Discovery: {target} (type: {scan_type})\n\n"
        output += f"Found **{len(live_hosts)}** live host(s)\n\n"

        for i, host in enumerate(live_hosts, 1):
            addrs = ", ".join([f"{a['addr']} ({a['type']})" for a in host["addresses"]])
            latency = host.get("latency", "N/A")
            output += f"**{i}.** {addrs} — latency: {latency}\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "scan_type": scan_type,
                "live_count": len(live_hosts),
                "live_hosts": live_hosts,
            }
        }

    @tool(name='port_scan')
    def port_scan(self, target: str, ports: str = "1-1000", scan_type: str = "syn") -> Dict[str, Any]:
        """
        Scan target for open ports.

        Discovers open ports on target hosts. SYN scan is the default and most common choice
        as it is fast and relatively stealthy. TCP connect scan is used when privileges are limited.

        :param target: Target IP or hostname. Must be within authorized scope.
        :param ports: Port specification. Examples:
            - '1-1000': Scan first 1000 ports.
            - '80,443,8080': Scan specific ports.
            - 'top-100': Scan top 100 most common ports.
            - 'U:53,161,T:1-1024': Mix of UDP and TCP ports.
        :param scan_type: Port scan technique. Options:
            - 'syn': SYN half-open scan (default, requires root, stealthy).
            - 'connect': Full TCP connect scan (no root needed).
            - 'udp': UDP port scan (slow, requires root).
            - 'fin': FIN scan (stealthy, can bypass firewalls).
            - 'null': NULL scan (sends packets with no flags set).
            - 'xmas': XMAS scan (FIN+URG+PSH flags set).
        :return: Structured result with open ports, states, and protocol information.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        scan_flags = {
            "syn": ["-sS"],
            "connect": ["-sT"],
            "udp": ["-sU"],
            "fin": ["-sF"],
            "null": ["-sN"],
            "xmas": ["-sX"],
        }

        if scan_type not in scan_flags:
            return {"status": "error", "message": f"Invalid scan_type '{scan_type}'. Choose from: {', '.join(scan_flags.keys())}"}

        cmd = ["nmap"] + scan_flags[scan_type] + ["-p", ports, "--open", target, "-oX", "-"]
        result = self._run_command(cmd, timeout=600)

        if result["return_code"] not in (0, 1):
            return {"status": "error", "message": f"Scan failed: {result['stderr']}"}

        parsed = self._parse_nmap_xml(result["stdout"])

        all_ports = []
        for host in parsed.get("hosts", []):
            for port in host.get("ports", []):
                if port.get("state") == "open":
                    all_ports.append(port)

        output = f"### 🔓 Port Scan: {target} (type: {scan_type}, ports: {ports})\n\n"
        output += f"Found **{len(all_ports)}** open port(s)\n\n"

        if all_ports:
            output += "| Port | Protocol | State | Service | Version |\n"
            output += "|------|----------|-------|---------|----------|\n"
            for p in all_ports:
                svc = p.get("service", {})
                version = svc.get("version", "") or svc.get("product", "")
                output += f"| {p['port_id']} | {p['protocol']} | {p['state']} | {svc.get('name', 'unknown')} | {version} |\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "scan_type": scan_type,
                "ports_spec": ports,
                "open_ports": all_ports,
                "open_count": len(all_ports),
            }
        }

    @tool(name='service_scan')
    def service_scan(self, target: str, ports: str = "1-10000", intensity: int = 5) -> Dict[str, Any]:
        """
        Perform service and version detection on open ports.

        Probes open ports to determine the service name, version, and potentially
        the operating system. This is essential for identifying known vulnerabilities
        associated with specific service versions.

        :param target: Target IP or hostname. Must be within authorized scope.
        :param ports: Port specification (same format as port_scan). Defaults to '1-10000'.
        :param intensity: Version detection intensity (0-9). Higher values are more thorough but slower.
            - 0: Light (fast, limited detection).
            - 5: Default (balanced).
            - 9: Intense (very thorough, slow, may be intrusive).
        :return: Structured result with detailed service version information for each open port.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        if not 0 <= intensity <= 9:
            return {"status": "error", "message": "Intensity must be between 0 and 9."}

        cmd = [
            "nmap", "-sV",
            f"--version-intensity={intensity}",
            "-p", ports,
            "--open",
            target, "-oX", "-"
        ]
        result = self._run_command(cmd, timeout=900)

        if result["return_code"] not in (0, 1):
            return {"status": "error", "message": f"Service scan failed: {result['stderr']}"}

        parsed = self._parse_nmap_xml(result["stdout"])

        services = []
        for host in parsed.get("hosts", []):
            for port in host.get("ports", []):
                if port.get("state") == "open" and port.get("service"):
                    services.append({
                        "port": port["port_id"],
                        "protocol": port["protocol"],
                        "state": port["state"],
                        **port["service"],
                        "scripts": port.get("scripts", []),
                    })

        output = f"### 🔍 Service Scan: {target} (intensity: {intensity})\n\n"
        output += f"Detected **{len(services)}** service(s)\n\n"

        for svc in services:
            product = svc.get("product", "")
            version = svc.get("version", "")
            extra = svc.get("extrainfo", "")
            output += f"**Port {svc['port']}/{svc['protocol']}:** {svc.get('name', 'unknown')}"
            if product:
                output += f" — {product}"
            if version:
                output += f" v{version}"
            if extra:
                output += f" ({extra})"
            output += "\n"

            for script in svc.get("scripts", []):
                output += f"  - Script `{script['id']}`: {script['output']}\n"
            output += "\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "intensity": intensity,
                "services": services,
                "service_count": len(services),
            }
        }

    @tool(name='os_detect')
    def os_detect(self, target: str) -> Dict[str, Any]:
        """
        Detect the operating system of the target host.

        Uses TCP/IP fingerprinting to guess the target's operating system.
        Requires root privileges and works best with at least one open and one closed port found.

        :param target: Target IP or hostname. Must be within authorized scope.
        :return: Structured result with OS guesses ranked by accuracy percentage.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        cmd = ["nmap", "-O", "--osscan-guess", target, "-oX", "-"]
        result = self._run_command(cmd, timeout=600)

        if result["return_code"] not in (0, 1):
            return {"status": "error", "message": f"OS detection failed: {result['stderr']}"}

        parsed = self._parse_nmap_xml(result["stdout"])

        os_results = []
        for host in parsed.get("hosts", []):
            os_results = host.get("os", [])

        output = f"### 💻 OS Detection: {target}\n\n"

        if not os_results:
            output += "No OS matches found. Possible reasons: target is firewalled, no open/closed ports found, or insufficient responses.\n"
        else:
            output += "| # | OS Name | Accuracy |\n"
            output += "|---|---------|----------|\n"
            for i, os_info in enumerate(os_results, 1):
                output += f"| {i} | {os_info['name']} | {os_info['accuracy']}% |\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "os_matches": os_results,
            }
        }

    @tool(name='subnet_scan')
    def subnet_scan(self, target: str, scan_types: str = "default") -> Dict[str, Any]:
        """
        Comprehensive network scan combining host discovery, port scanning, service detection, and OS fingerprinting.

        This is the primary reconnaissance tool that runs a full scan profile against a target
        subnet or individual host. It combines multiple scan phases into a single execution.

        :param target: Target IP, range, or subnet (e.g., '192.168.1.0/24', '10.0.0.1-50'). Must be within authorized scope.
        :param scan_types: Scan profile. Options:
            - 'default': Port scan + service detection + OS detection + default scripts.
            - 'quick': Top 100 ports + service detection only (fast).
            - 'stealth': SYN scan + aggressive OS detection + version detection (slow, stealthy).
            - 'aggressive': All ports + OS detection + version detection + aggressive scripts (slow, noisy).
            - 'vuln': Service detection + vulnerability detection scripts (focuses on known vulnerabilities).
        :return: Comprehensive structured result with all scan data combined.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        profiles = {
            "default": ["-sS", "-sV", "-O", "--default-script-level", "-T4"],
            "quick": ["-sS", "-sV", "--top-ports 100", "-T4"],
            "stealth": ["-sS", "-sV", "-O", "--version-intensity 7", "-T2"],
            "aggressive": ["-sS", "-sV", "-O", "-A", "-p-", "-T4"],
            "vuln": ["-sV", "--script=vuln", "--script-args=unsafe=1", "-T4"],
        }

        if scan_types not in profiles:
            return {"status": "error", "message": f"Invalid profile '{scan_types}'. Choose from: {', '.join(profiles.keys())}"}

        cmd = ["nmap"] + profiles[scan_types] + [target, "-oX", "-"]
        timeout = 1800 if scan_types == "aggressive" else 900

        result = self._run_command(cmd, timeout=timeout)

        if result["return_code"] not in (0, 1):
            return {"status": "error", "message": f"Scan failed: {result['stderr']}"}

        parsed = self._parse_nmap_xml(result["stdout"])

        output = f"### 🌐 Subnet Scan: {target} (profile: {scan_types})\n\n"

        total_hosts = len(parsed.get("hosts", []))
        live_hosts = [h for h in parsed.get("hosts", []) if h.get("status") == "up"]
        output += f"Total hosts scanned: {total_hosts} | Live hosts: {len(live_hosts)}\n\n"

        for host in live_hosts:
            addrs = ", ".join([a["addr"] for a in host.get("addresses", [])])
            output += f"#### Host: {addrs}\n\n"

            # OS info
            if host.get("os"):
                top_os = host["os"][0] if host["os"] else None
                if top_os:
                    output += f"**OS:** {top_os['name']} ({top_os['accuracy']}% accuracy)\n\n"

            # Ports
            open_ports = [p for p in host.get("ports", []) if p.get("state") == "open"]
            if open_ports:
                output += f"**Open Ports ({len(open_ports)}):**\n\n"
                output += "| Port | Protocol | Service | Version |\n"
                output += "|------|----------|---------|----------|\n"
                for p in open_ports:
                    svc = p.get("service", {})
                    version = svc.get("version", "") or svc.get("product", "")
                    output += f"| {p['port_id']} | {p['protocol']} | {svc.get('name', 'unknown')} | {version} |\n"

            # Scripts
            for script in host.get("host_scripts", []):
                output += f"\n**Script `{script['id']}`:**\n```\n{script['output']}\n```\n"

            output += "\n---\n\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "profile": scan_types,
                "scan_info": parsed.get("scan_info", {}),
                "hosts": parsed.get("hosts", []),
            }
        }

    @tool(name='dns_lookup')
    def dns_lookup(self, domain: str, record_types: str = "ANY", dns_server: str = "") -> Dict[str, Any]:
        """
        Perform DNS lookups for a domain.

        Queries DNS records for a given domain. Supports multiple record types
        and can query specific DNS servers.

        :param domain: Domain name to query (e.g., 'example.com').
        :param record_types: Comma-separated DNS record types. Options:
            - 'A': IPv4 address.
            - 'AAAA': IPv6 address.
            - 'MX': Mail exchange records.
            - 'NS': Name server records.
            - 'TXT': Text records (SPF, DKIM, DMARC).
            - 'CNAME': Canonical name records.
            - 'SOA': Start of authority records.
            - 'ANY': All record types (default).
            - 'AXFR': Zone transfer attempt (may be blocked).
        :param dns_server: Specific DNS server to query (e.g., '8.8.8.8'). Defaults to system resolver.
        :return: Structured DNS records with type, name, TTL, and data for each record.
        """
        if not self._validate_target(domain):
            return {"status": "error", "message": f"Domain '{domain}' is not in the authorized scope."}

        cmd = ["dig", "+noall", "+answer", domain, record_types]
        if dns_server:
            cmd.append(f"@{dns_server}")

        result = self._run_command(cmd, timeout=60)

        if result["return_code"] != 0:
            # dig may return non-zero for some record types even with results
            pass

        records = self._parse_dns_records(result["stdout"])

        output = f"### 📡 DNS Lookup: {domain} (types: {record_types})\n\n"

        if dns_server:
            output += f"DNS Server: {dns_server}\n\n"

        if not records:
            output += f"No {record_types} records found for {domain}.\n"
        else:
            # Group by type
            by_type = {}
            for rec in records:
                by_type.setdefault(rec["type"], []).append(rec)

            for rtype, recs in sorted(by_type.items()):
                output += f"**{rtype} Records ({len(recs)}):**\n\n"
                for rec in recs:
                    output += f"- `{rec['name']}` → **{rec['data']}** (TTL: {rec['ttl']}s)\n"
                output += "\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "domain": domain,
                "record_types": record_types,
                "dns_server": dns_server or "default",
                "records": records,
                "record_count": len(records),
            }
        }

    @tool(name='dns_enum')
    def dns_enum(self, domain: str, wordlist: str = "") -> Dict[str, Any]:
        """
        Enumerate DNS records using DNSRecon.

        Performs comprehensive DNS enumeration including zone transfer attempts,
        brute-force subdomain discovery, and record gathering.

        :param domain: Target domain to enumerate.
        :param wordlist: Path to custom subdomain wordlist. If empty, uses DNSRecon's default.
        :return: Structured result with discovered subdomains, IPs, and DNS records.
        """
        if not self._validate_target(domain):
            return {"status": "error", "message": f"Domain '{domain}' is not in the authorized scope."}

        cmd = ["dnsrecon", "-d", domain, "-t", "std,brt,srv"]
        if wordlist:
            cmd.extend(["-D", wordlist])

        result = self._run_command(cmd, timeout=300)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"DNS enumeration failed: {result['stderr']}"}

        # Parse DNSRecon output
        records = []
        for line in result["stdout"].split("\n"):
            line = line.strip()
            if not line or line.startswith("[*]") or line.startswith("[+]"):
                # Extract records from [*] lines
                match = re.search(r'\[\*\]\s+(.+)', line)
                if match:
                    records.append(match.group(1).strip())

        output = f"### 🔎 DNS Enumeration: {domain}\n\n"
        output += f"Discovered **{len(records)}** record(s)\n\n"

        for rec in records:
            output += f"- {rec}\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "domain": domain,
                "records": records,
                "record_count": len(records),
            }
        }

    @tool(name='whois_lookup')
    def whois_lookup(self, target: str) -> Dict[str, Any]:
        """
        Perform WHOIS lookup for a domain or IP address.

        Retrieves registration information, including registrar details, creation/expiry dates,
        nameservers, and registrant information.

        :param target: Domain name or IP address to look up.
        :return: Structured WHOIS information with key registration fields.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        cmd = ["whois", target]
        result = self._run_command(cmd, timeout=120)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"WHOIS lookup failed: {result['stderr']}"}

        parsed = self._parse_whois(result["stdout"])

        output = f"### 📋 WHOIS Lookup: {target}\n\n"

        important_fields = [
            "domain_name", "registrar", "creation_date", "updated_date",
            "expiry_date", "registrant", "name_servers", "status", "dnssec"
        ]

        for field in important_fields:
            if field in parsed:
                label = field.replace("_", " ").title()
                value = parsed[field]
                if isinstance(value, list):
                    output += f"**{label}:**\n"
                    for item in value:
                        output += f"  - {item}\n"
                else:
                    output += f"**{label}:** {value}\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "whois_data": parsed,
            }
        }

    @tool(name='subdomain_enum')
    def subdomain_enum(self, domain: str, sources: str = "all", depth: int = 2) -> Dict[str, Any]:
        """
        Enumerate subdomains using Subfinder.

        Uses multiple data sources (passive DNS, certificates, search engines, etc.)
        to discover subdomains associated with a target domain. This is a passive
        reconnaissance technique.

        :param domain: Target domain (e.g., 'example.com').
        :param sources: Comma-separated list of sources. Use 'all' for all available sources.
            Common sources: 'crtsh', 'certspotter', 'amass', 'passivedns', 'wayback', 'archiveis'.
        :param depth: Discovery depth level. Higher values may find more subdomains but take longer.
        :return: List of discovered subdomains with their source information.
        """
        if not self._validate_target(domain):
            return {"status": "error", "message": f"Domain '{domain}' is not in the authorized scope."}

        cmd = ["subfinder", "-d", domain, "-silent", "-json"]
        if sources != "all":
            cmd.extend(["-sources", sources])

        result = self._run_command(cmd, timeout=600)

        if result["return_code"] != 0 and not result["stdout"]:
            return {"status": "error", "message": f"Subdomain enumeration failed: {result['stderr']}"}

        subdomains = []
        try:
            for line in result["stdout"].strip().split("\n"):
                if line.strip():
                    data = json.loads(line)
                    subdomains.append({
                        "host": data.get("host", ""),
                        "source": data.get("source", ""),
                    })
        except json.JSONDecodeError:
            # Fallback: treat output as plain text lines
            for line in result["stdout"].strip().split("\n"):
                if line.strip():
                    subdomains.append({"host": line.strip(), "source": "unknown"})

        # Deduplicate by host
        seen = set()
        unique_subdomains = []
        for sub in subdomains:
            if sub["host"] not in seen:
                seen.add(sub["host"])
                unique_subdomains.append(sub)

        # Group by source
        by_source = {}
        for sub in unique_subdomains:
            by_source.setdefault(sub["source"], []).append(sub["host"])

        output = f"### 🌐 Subdomain Enumeration: {domain}\n\n"
        output += f"Found **{len(unique_subdomains)}** unique subdomain(s)\n\n"

        for source, hosts in sorted(by_source.items(), key=lambda x: len(x[1]), reverse=True):
            output += f"**Source: {source}** ({len(hosts)} results)\n"
            for host in hosts[:20]:
                output += f"  - {host}\n"
            if len(hosts) > 20:
                output += f"  - ... and {len(hosts) - 20} more\n"
            output += "\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "domain": domain,
                "subdomains": unique_subdomains,
                "subdomain_count": len(unique_subdomains),
                "sources_used": list(by_source.keys()),
            }
        }
