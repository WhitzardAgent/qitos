"""
Password attack tools.

Provides password cracking operations: john_crack, hashcat_crack, hydra_bruteforce,
hash_identify, wordlist_manage, mask_generate.
Uses subprocess to call industry-standard password tools (john, hashcat, hydra).
All operations MUST be performed within authorized scope only.
"""

import re
import subprocess
import os
from typing import Any, Dict, List, Optional

from qitos.core.tool import tool


class PasswordToolSet:
    """
    Password attack toolset providing comprehensive password cracking capabilities.

    Supports offline hash cracking (John the Ripper, Hashcat), online service
    brute-forcing (Hydra), hash identification, and wordlist management.
    All operations must be performed within an authorized penetration testing engagement.
    """

    def __init__(self, authorized_targets: Optional[List[str]] = None, workspace_root: str = "."):
        """
        Initialize password attack toolset.

        :param authorized_targets: List of authorized target IPs/domains/services.
        :param workspace_root: Root directory for storing hash files, wordlists, and results.
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

    def _run_command(self, cmd: List[str], timeout: int = 3600) -> Dict[str, Any]:
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

    def _detect_hash_type(self, hash_str: str) -> Dict[str, Any]:
        """
        Attempt to identify the hash type based on its format.

        Uses pattern matching and length heuristics to identify common hash types
        including MD5, SHA variants, bcrypt, NTLM, and others.

        :param hash_str: The hash string to identify.
        :return: Dictionary with detected hash type, format details, and corresponding tool codes.
        """
        hash_str = hash_str.strip()
        length = len(hash_str)
        result = {"hash": hash_str, "length": length, "detected_types": [], "confidence": "low"}

        # MD5: 32 hex chars
        if re.match(r'^[a-fA-F0-9]{32}$', hash_str):
            result["detected_types"].append({
                "type": "MD5",
                "john_format": "raw-md5",
                "hashcat_mode": "0",
                "confidence": "high",
            })

        # SHA-1: 40 hex chars
        if re.match(r'^[a-fA-F0-9]{40}$', hash_str):
            result["detected_types"].append({
                "type": "SHA-1",
                "john_format": "raw-sha1",
                "hashcat_mode": "100",
                "confidence": "high",
            })

        # SHA-256: 64 hex chars
        if re.match(r'^[a-fA-F0-9]{64}$', hash_str):
            result["detected_types"].append({
                "type": "SHA-256",
                "john_format": "raw-sha256",
                "hashcat_mode": "1400",
                "confidence": "high",
            })

        # SHA-512: 128 hex chars (or crypt format)
        if re.match(r'^[a-fA-F0-9]{128}$', hash_str):
            result["detected_types"].append({
                "type": "SHA-512",
                "john_format": "raw-sha512",
                "hashcat_mode": "1700",
                "confidence": "high",
            })

        # NTLM: 32 hex chars (same length as MD5, but check format $NT$)
        if re.match(r'^\$NT\$', hash_str) or (re.match(r'^[a-fA-F0-9]{32}$', hash_str) and length == 32):
            result["detected_types"].append({
                "type": "NTLM",
                "john_format": "nt",
                "hashcat_mode": "1000",
                "confidence": "medium",
            })

        # bcrypt: starts with $2b$, $2a$, $2y$
        if re.match(r'^\$2[aby]\$\d{2}\$', hash_str):
            result["detected_types"].append({
                "type": "bcrypt",
                "john_format": "bcrypt",
                "hashcat_mode": "3200",
                "confidence": "high",
            })

        # SHA-512 crypt: $6$...
        if hash_str.startswith("$6$"):
            result["detected_types"].append({
                "type": "sha512crypt",
                "john_format": "sha512crypt",
                "hashcat_mode": "1800",
                "confidence": "high",
            })

        # MD5 crypt: $1$...
        if hash_str.startswith("$1$"):
            result["detected_types"].append({
                "type": "md5crypt",
                "john_format": "md5crypt",
                "hashcat_mode": "500",
                "confidence": "high",
            })

        # Blowfish crypt: $2a$ or $2y$ (already covered by bcrypt above)

        # WordPress hash: $P$B...
        if hash_str.startswith("$P$"):
            result["detected_types"].append({
                "type": "WordPress (phpass)",
                "john_format": "phpass",
                "hashcat_mode": "400",
                "confidence": "high",
            })

        # MySQL: starts with * followed by 40 hex
        if re.match(r'^\*[a-fA-F0-9]{40}$', hash_str):
            result["detected_types"].append({
                "type": "MySQL",
                "john_format": "mysql",
                "hashcat_mode": "300",
                "confidence": "high",
            })

        # SHA-512 crypt Linux: $6$salt$hash
        if re.match(r'^\$6\$[^\$]+\$[a-zA-Z0-9./]{86}$', hash_str):
            result["detected_types"].append({
                "type": "sha512crypt (Linux /etc/shadow)",
                "john_format": "sha512crypt",
                "hashcat_mode": "1800",
                "confidence": "high",
            })

        if result["detected_types"]:
            result["confidence"] = "high"
        else:
            result["detected_types"].append({
                "type": "Unknown",
                "john_format": "",
                "hashcat_mode": "",
                "confidence": "low",
            })

        return result

    @tool(name='hash_identify')
    def hash_identify(self, hash_str: str) -> Dict[str, Any]:
        """
        Identify the type of a hash string.

        Attempts to determine the hash algorithm used based on format analysis.
        Supports MD5, SHA family, NTLM, bcrypt, crypt formats, and more.

        :param hash_str: The hash string to identify.
        :return: Identified hash type(s) with corresponding tool format codes.
        """
        detection = self._detect_hash_type(hash_str)

        output = f"### 🔎 Hash Identification\n\n"
        output += f"**Input Hash:** `{hash_str[:50]}{'...' if len(hash_str) > 50 else ''}`\n"
        output += f"**Length:** {detection['length']} characters\n\n"

        if detection["detected_types"] and detection["detected_types"][0]["type"] != "Unknown":
            output += f"#### Detected Hash Type(s)\n\n"
            output += "| Hash Type | John Format | Hashcat Mode | Confidence |\n"
            output += "|-----------|-------------|--------------|------------|\n"
            for dt in detection["detected_types"]:
                output += f"| {dt['type']} | `{dt['john_format']}` | {dt['hashcat_mode']} | {dt['confidence']} |\n"

            best = detection["detected_types"][0]
            output += f"\n**Best match:** {best['type']} (confidence: {best['confidence']})\n"
        else:
            output += "⚠️ Could not identify the hash type. Try using `hash-identifier` or `name-that-hash` for more options.\n"
            output += "You may also need to specify the format manually when using John or Hashcat.\n"

        return {
            "status": "success",
            "stdout": output,
            "data": detection,
        }

    @tool(name='john_crack')
    def john_crack(self, hash_file: str, wordlist: str = "/usr/share/wordlists/rockyou.txt",
                   format: str = "", rules: str = "", extra_args: str = "") -> Dict[str, Any]:
        """
        Crack password hashes using John the Ripper.

        John the Ripper is a fast password cracker that supports many hash types
        and uses dictionary, rule-based, and brute-force attacks.

        :param hash_file: Path to file containing hashes (one per line, or in proper format).
        :param wordlist: Path to wordlist file (default: rockyou.txt).
        :param format: John hash format (e.g., 'raw-md5', 'nt', 'sha512crypt'). If empty, auto-detect.
        :param rules: John rule set to apply (e.g., 'single', 'wordlist', 'dumb', 'jumbo'). Empty for no rules.
        :param extra_args: Additional arguments to pass to John (e.g., '--incremental=Lower').
        :return: Cracking results with cracked passwords and statistics.
        """
        if not os.path.isfile(hash_file):
            return {"status": "error", "message": f"Hash file not found: {hash_file}"}

        if not os.path.isfile(wordlist):
            return {"status": "error", "message": f"Wordlist not found: {wordlist}"}

        cmd = ["john"]

        if format:
            cmd.extend(["--format", format])
        if rules:
            cmd.append(f"--rules={rules}")
        if extra_args:
            cmd.extend(extra_args.split())

        cmd.extend(["--wordlist", wordlist, hash_file])

        result = self._run_command(cmd, timeout=3600)

        output = f"### 🔑 John the Ripper: Cracking\n\n"
        output += f"**Hash File:** `{hash_file}`\n"
        output += f"**Wordlist:** `{wordlist}`\n"
        if format:
            output += f"**Format:** `{format}`\n"
        if rules:
            output += f"**Rules:** `{rules}`\n"
        output += "\n"

        # Show cracked passwords
        cmd_show = ["john", "--show", hash_file]
        if format:
            cmd_show.extend(["--format", format])
        show_result = self._run_command(cmd_show, timeout=60)

        stdout = show_result["stdout"].strip()
        if stdout:
            lines = stdout.split("\n")
            # Parse cracked entries
            cracked = []
            summary_line = ""
            for line in lines:
                if ":" in line:
                    cracked.append(line.strip())
                else:
                    summary_line = line.strip()

            if cracked:
                output += f"#### ✅ Cracked Passwords ({len(cracked)})\n\n"
                output += "| Username | Password |\n"
                output += "|----------|----------|\n"
                for entry in cracked:
                    parts = entry.split(":", 1)
                    user = parts[0]
                    pw = parts[1] if len(parts) > 1 else ""
                    output += f"| `{user}` | `{pw}` |\n"
            else:
                output += "#### ❌ No Passwords Cracked\n\n"

            if summary_line:
                output += f"\n{summary_line}\n"
        else:
            output += "#### ❌ No Passwords Cracked\n\n"
            output += "John did not crack any passwords. Consider:\n"
            output += "1. Using a different wordlist\n"
            output += "2. Enabling rules (--rules=single, --rules=wordlist)\n"
            output += "3. Using incremental mode (--incremental)\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "hash_file": hash_file,
                "wordlist": wordlist,
                "format": format,
                "john_output": result["stdout"],
                "show_output": stdout,
            }
        }

    @tool(name='hashcat_crack')
    def hashcat_crack(self, hash_file: str, attack_mode: int = 0, hash_type: int = 0,
                      wordlist: str = "", mask: str = "", rules_file: str = "",
                      force: bool = True) -> Dict[str, Any]:
        """
        Crack password hashes using Hashcat.

        Hashcat is the world's fastest password recovery tool, leveraging GPU acceleration
        for high-speed cracking. Supports hundreds of hash types and multiple attack modes.

        :param hash_file: Path to file containing hashes (one per line).
        :param attack_mode: Attack mode:
            - 0: Dictionary attack (requires wordlist).
            - 1: Combinator attack (combines two wordlists).
            - 3: Mask attack (brute-force with pattern, e.g., '?l?l?l?l?d?d?d?d').
            - 6: Dictionary + mask hybrid.
            - 7: Mask + dictionary hybrid.
        :param hash_type: Hash type code (e.g., 0=MD5, 1000=NTLM, 1800=sha512crypt, 3200=bcrypt).
        :param wordlist: Path to wordlist file (required for modes 0, 1, 6).
        :param mask: Mask pattern for mask attack (mode 3). Placeholders:
            - ?l: lowercase letter, ?u: uppercase, ?d: digit, ?s: special, ?a: all.
        :param rules_file: Path to Hashcat rules file for dictionary transformation.
        :param force: Force execution even if hash type validation fails (default: True).
        :return: Cracking results with cracked passwords and performance statistics.
        """
        if not os.path.isfile(hash_file):
            return {"status": "error", "message": f"Hash file not found: {hash_file}"}

        if attack_mode in (0, 1, 6) and not wordlist:
            return {"status": "error", "message": f"Wordlist required for attack mode {attack_mode}."}
        if attack_mode == 3 and not mask:
            return {"status": "error", "message": "Mask pattern required for mask attack (mode 3)."}

        cmd = [
            "hashcat",
            "-m", str(hash_type),
            "-a", str(attack_mode),
            hash_file,
        ]

        if wordlist and attack_mode in (0, 1, 6):
            cmd.append(wordlist)
        if mask and attack_mode in (3, 6, 7):
            cmd.append(mask)
        if rules_file:
            cmd.extend(["-r", rules_file])
        if force:
            cmd.append("--force")

        # Output to file for clean results
        potfile = os.path.join(self._workspace_root, "_hashcat_potfile.txt")
        cmd.extend(["-o", potfile, "--potfile-disable"])

        result = self._run_command(cmd, timeout=3600)

        output = f"### ⚡ Hashcat: GPU-Accelerated Cracking\n\n"
        output += f"**Hash File:** `{hash_file}`\n"
        output += f"**Hash Type:** {hash_type}\n"
        output += f"**Attack Mode:** {attack_mode}\n"
        if wordlist:
            output += f"**Wordlist:** `{wordlist}`\n"
        if mask:
            output += f"**Mask:** `{mask}`\n"
        output += "\n"

        # Parse output file
        cracked = []
        if os.path.isfile(potfile):
            with open(potfile, "r") as f:
                for line in f:
                    line = line.strip()
                    if ":" in line:
                        parts = line.split(":", 1)
                        cracked.append({"hash": parts[0], "password": parts[1]})

        if cracked:
            output += f"#### ✅ Cracked ({len(cracked)} hash(es))\n\n"
            output += "| Hash | Password |\n"
            output += "|------|----------|\n"
            for c in cracked:
                short_hash = c["hash"][:30] + "..." if len(c["hash"]) > 30 else c["hash"]
                output += f"| `{short_hash}` | `{c['password']}` |\n"
        else:
            output += "#### ❌ No Passwords Cracked\n\n"

        # Extract performance info from stderr
        stderr = result.get("stderr", "")
        speed_match = re.search(r'(\d+\.\d+\s*[GMKH]H/s)', stderr)
        if speed_match:
            output += f"**Speed:** {speed_match.group(1)}\n"

        # Clean up
        try:
            os.remove(potfile)
        except OSError:
            pass

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "hash_file": hash_file,
                "hash_type": hash_type,
                "attack_mode": attack_mode,
                "cracked": cracked,
                "cracked_count": len(cracked),
                "hashcat_output": result["stderr"],
            }
        }

    @tool(name='hydra_bruteforce')
    def hydra_bruteforce(self, target: str, service: str, username: str = "",
                         wordlist: str = "/usr/share/wordlists/rockyou.txt",
                         threads: int = 4, port: int = 0,
                         extra_args: str = "") -> Dict[str, Any]:
        """
        Brute-force login credentials using Hydra.

        Hydra is an online password cracking tool that supports many protocols
        including SSH, FTP, HTTP, MySQL, PostgreSQL, RDP, SMB, and more.

        :param target: Target IP address or hostname.
        :param service: Service/protocol to attack. Options:
            - 'ssh': SSH (default port 22).
            - 'ftp': FTP (default port 21).
            - 'http-post-form': HTTP POST form login.
            - 'http-get-form': HTTP GET form login.
            - 'mysql': MySQL database (default port 3306).
            - 'postgresql': PostgreSQL (default port 5432).
            - 'rdp': Remote Desktop Protocol (default port 3389).
            - 'smb': SMB/CIFS (default port 445).
            - 'smtp': SMTP (default port 25).
            - 'vnc': VNC (default port 5900).
        :param username: Single username to use (e.g., 'admin'). If empty, Hydra will prompt.
        :param wordlist: Path to password wordlist (default: rockyou.txt).
        :param threads: Number of concurrent threads (default: 4, keep low for sensitive services).
        :param port: Custom port number (0 = use protocol default).
        :param extra_args: Additional Hydra arguments (e.g., '/login.php:username=^USER^&password=^PASS^:Login failed' for HTTP forms).
        :return: Brute-force results with discovered credentials.
        """
        if not self._validate_target(target):
            return {"status": "error", "message": f"Target '{target}' is not in the authorized scope."}

        if not os.path.isfile(wordlist):
            return {"status": "error", "message": f"Wordlist not found: {wordlist}"}

        valid_services = ["ssh", "ftp", "http-post-form", "http-get-form", "mysql",
                          "postgresql", "rdp", "smb", "smtp", "vnc", "telnet", "mssql"]

        if service not in valid_services:
            return {"status": "error", "message": f"Invalid service '{service}'. Choose from: {', '.join(valid_services)}"}

        cmd = [
            "hydra",
            "-l", username,
            "-P", wordlist,
            "-t", str(threads),
            "-f",  # Stop on first valid pair
            "-v",  # Verbose
        ]

        if port:
            cmd.extend(["-s", str(port)])

        if extra_args:
            cmd.append(extra_args)

        cmd.append(f"{target}")
        cmd.append(service)

        result = self._run_command(cmd, timeout=3600)

        output = f"### 🥊 Hydra: Online Brute-Force\n\n"
        output += f"**Target:** {target}\n"
        output += f"**Service:** {service}\n"
        output += f"**Username:** `{username}`\n"
        output += f"**Wordlist:** `{wordlist}`\n"
        output += f"**Threads:** {threads}\n\n"

        stderr = result.get("stderr", "")
        stdout = result.get("stdout", "")

        # Check for successful login
        combined = stdout + stderr
        success_pattern = r'\[({port or ""}+?port)\]\s*\[({service})\]\s*host:\s*({target}?)\s*\S*\s*login:\s*(\S+)\s*password:\s*(\S+)'
        match = re.search(r'login:\s*(\S+)\s+password:\s*(\S+)', combined)

        if match or "1 valid password found" in combined.lower():
            user_match = re.search(r'login:\s*(\S+)', combined)
            pass_match = re.search(r'password:\s*(\S+)', combined)
            found_user = user_match.group(1) if user_match else username
            found_pass = pass_match.group(1) if pass_match else "unknown"

            output += "#### ✅ Valid Credentials Found!\n\n"
            output += f"| Username | Password | Service | Target |\n"
            output += f"|----------|----------|---------|--------|\n"
            output += f"| `{found_user}` | `{found_pass}` | {service} | {target} |\n"
        else:
            output += "#### ❌ No Valid Credentials Found\n\n"
            output += "Hydra did not find valid credentials. Consider:\n"
            output += "1. Trying a different wordlist\n"
            output += "2. Checking if the service is accessible\n"
            output += "3. Verifying the username is correct\n"

        return {
            "status": "success",
            "stdout": output,
            "data": {
                "target": target,
                "service": service,
                "username": username,
                "hydra_output": combined,
                "success": bool(match or "1 valid password found" in combined.lower()),
            }
        }

    @tool(name='wordlist_manage')
    def wordlist_manage(self, action: str, input_file: str = "", output_file: str = "",
                        min_length: int = 0, max_length: int = 0,
                        charset: str = "", rules: str = "") -> Dict[str, Any]:
        """
        Manage and transform password wordlists.

        Provides operations to filter, transform, and generate password wordlists
        for use with cracking tools.

        :param action: Operation to perform. Options:
            - 'filter': Filter wordlist by length and character set.
            - 'unique': Remove duplicate entries from a wordlist.
            - 'sort': Sort a wordlist alphabetically.
            - 'combine': Combine multiple wordlists into one.
            - 'stats': Show statistics about a wordlist (line count, min/max length, charset).
            - 'generate': Generate a basic wordlist from a charset and length range.
        :param input_file: Path to input wordlist file.
        :param output_file: Path to output file (defaults to workspace_root/result.txt).
        :param min_length: Minimum password length (for filter/generate).
        :param max_length: Maximum password length (for filter/generate).
        :param charset: Character set for generation/filter (e.g., 'lowercase', 'digits', 'alpha', 'alphanum', 'all').
        :param rules: Transformation rules to apply (e.g., 'capitalize', 'append_digits', 'leet').
        :return: Operation results with statistics.
        """
        if not output_file:
            output_file = os.path.join(self._workspace_root, "wordlist_result.txt")

        valid_actions = ["filter", "unique", "sort", "combine", "stats", "generate"]
        if action not in valid_actions:
            return {"status": "error", "message": f"Invalid action '{action}'. Choose from: {', '.join(valid_actions)}"}

        if action != "generate" and action != "combine" and not os.path.isfile(input_file):
            return {"status": "error", "message": f"Input file not found: {input_file}"}

        if action == "stats":
            # Count lines and analyze wordlist
            with open(input_file, "r", errors="ignore") as f:
                lines = [l.strip() for l in f if l.strip()]

            if not lines:
                return {"status": "error", "message": "Wordlist is empty."}

            lengths = [len(l) for l in lines]
            unique = set(lines)

            charset_chars = set()
            for l in lines:
                charset_chars.update(l)

            output = f"### 📊 Wordlist Statistics: `{input_file}`\n\n"
            output += f"- **Total entries:** {len(lines):,}\n"
            output += f"- **Unique entries:** {len(unique):,}\n"
            output += f"- **Duplicates:** {len(lines) - len(unique):,}\n"
            output += f"- **Min length:** {min(lengths)}\n"
            output += f"- **Max length:** {max(lengths)}\n"
            output += f"- **Avg length:** {sum(lengths) / len(lengths):.1f}\n"
            output += f"- **Character set:** {sorted(charset_chars)}\n"

            return {
                "status": "success",
                "stdout": output,
                "data": {
                    "total_entries": len(lines),
                    "unique_entries": len(unique),
                    "duplicates": len(lines) - len(unique),
                    "min_length": min(lengths),
                    "max_length": max(lengths),
                    "avg_length": round(sum(lengths) / len(lengths), 1),
                    "charset": sorted(charset_chars),
                }
            }

        if action == "unique":
            cmd = ["sort", input_file, "|", "uniq", ">", output_file]
            result = self._run_command(["bash", "-c", f"sort '{input_file}' | uniq > '{output_file}'"], timeout=300)
            if result["return_code"] != 0:
                return {"status": "error", "message": f"Failed to process: {result['stderr']}"}

            with open(output_file, "r", errors="ignore") as f:
                count = sum(1 for _ in f)

            output = f"### ✂️ Wordlist Deduplicated\n\n"
            output += f"**Input:** `{input_file}`\n"
            output += f"**Output:** `{output_file}`\n"
            output += f"**Unique entries:** {count:,}\n"

            return {"status": "success", "stdout": output, "data": {"output_file": output_file, "unique_count": count}}

        if action == "sort":
            result = self._run_command(["sort", input_file, "-o", output_file], timeout=300)
            if result["return_code"] != 0:
                return {"status": "error", "message": f"Failed to sort: {result['stderr']}"}

            output = f"### 📋 Wordlist Sorted\n\n"
            output += f"**Output:** `{output_file}`\n"

            return {"status": "success", "stdout": output, "data": {"output_file": output_file}}

        if action == "filter":
            filter_cmd = f"cat '{input_file}'"
            if min_length > 0:
                filter_cmd += f" | awk 'length >= {min_length}'"
            if max_length > 0:
                filter_cmd += f" | awk 'length <= {max_length}'"
            if charset == "alpha":
                filter_cmd += " | grep -E '^[a-zA-Z]+$'"
            elif charset == "digits":
                filter_cmd += " | grep -E '^[0-9]+$'"
            elif charset == "alphanum":
                filter_cmd += " | grep -E '^[a-zA-Z0-9]+$'"
            filter_cmd += f" > '{output_file}'"

            result = self._run_command(["bash", "-c", filter_cmd], timeout=300)

            with open(output_file, "r", errors="ignore") as f:
                count = sum(1 for _ in f)

            output = f"### 🔍 Wordlist Filtered\n\n"
            output += f"**Filters:** min_length={min_length}, max_length={max_length}, charset={charset}\n"
            output += f"**Output:** `{output_file}` ({count:,} entries)\n"

            return {"status": "success", "stdout": output, "data": {"output_file": output_file, "filtered_count": count}}

        return {"status": "error", "message": f"Action '{action}' not yet implemented."}
