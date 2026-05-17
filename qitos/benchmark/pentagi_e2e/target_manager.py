"""Target manager — Docker lifecycle for vulnerable test targets.

Handles starting/stopping vulnerable Docker containers, health checking,
ground-truth collection via independent scanning, and flag planting
for Tier 4 exploitation tests.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .targets import VulnerableTarget

logger = logging.getLogger(__name__)


class TargetManager:
    """Manages lifecycle of a vulnerable Docker container for pentest tests.

    Usage::

        manager = TargetManager(TARGETS["dvwa"])
        try:
            address = manager.start()
            ground_truth = manager.get_ground_truth()
            # ... run PentAGI against address ...
        finally:
            manager.stop()
    """

    def __init__(self, target: VulnerableTarget):
        self.target = target
        self.container_id: Optional[str] = None
        self.container_name: str = f"pentagi_target_{target.name}_{uuid4().hex[:8]}"
        self.network_name: str = f"pentagi_e2e_{uuid4().hex[:8]}"
        self.target_address: Optional[str] = None
        self.planted_flags: Dict[str, str] = {}  # flag_name -> flag_content
        self._ground_truth: Optional[Dict[str, Any]] = None

    def start(self) -> str:
        """Start the target container and return its reachable address.

        Returns
        -------
        str
            The address that PentAGI can use as an authorized target.
            Either a container name (for Docker network) or localhost:port.
        """
        self._create_network()
        self._start_container()
        self._wait_for_health()
        self._run_setup_commands()

        # Return container name as address (works within Docker network)
        self.target_address = self.container_name
        return self.target_address

    def get_ground_truth(self) -> Dict[str, Any]:
        """Independently scan the target to establish ground truth.

        Returns ground-truth data about the target that can be used
        for objective scoring of agent reconnaissance results.
        """
        if self._ground_truth is not None:
            return self._ground_truth

        ground_truth: Dict[str, Any] = {
            "ports": [],
            "services": [],
            "technologies": [],
        }

        # Try to scan using a Kali container (or direct nmap if available)
        try:
            nmap_output = self._nmap_scan()
            if nmap_output:
                ground_truth = self._parse_nmap_output(nmap_output)
        except Exception as e:
            logger.warning(f"Ground truth scan failed: {e}")

        # Fallback: use target definition ports as ground truth
        if not ground_truth["ports"]:
            ground_truth["ports"] = list(self.target.ports.keys())

        self._ground_truth = ground_truth
        return ground_truth

    def plant_flag(self, path: str, content: str, flag_name: str = "default") -> None:
        """Inject a flag file into the running container.

        Parameters
        ----------
        path : str
            File path inside the container.
        content : str
            Flag content (unique marker string).
        flag_name : str
            Name for this flag (for later retrieval).
        """
        if not self.container_id:
            raise RuntimeError("Container not started")

        escaped = content.replace("'", "'\"'\"'")
        cmd = [
            "docker", "exec", self.container_id,
            "sh", "-c", f"mkdir -p {shlex.quote(str(path.rsplit('/', 1)[0] or '/'))} && printf '%s' '{escaped}' > {shlex.quote(path)}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.warning(f"Flag planting failed: {result.stderr}")
        else:
            self.planted_flags[flag_name] = content
            logger.info(f"Planted flag '{flag_name}' at {path}")

    def stop(self) -> None:
        """Stop and remove the container and network."""
        if self.container_id:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", self.container_id],
                    capture_output=True, text=True, timeout=30,
                )
            except Exception:
                pass
            self.container_id = None

        try:
            subprocess.run(
                ["docker", "network", "rm", self.network_name],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _create_network(self) -> None:
        """Create an isolated Docker network for this test."""
        try:
            subprocess.run(
                ["docker", "network", "create", self.network_name],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.CalledProcessError:
            pass  # Network may already exist

    def _start_container(self) -> None:
        """Start the vulnerable target container."""
        cmd = ["docker", "run", "-d",
               "--name", self.container_name,
               "--network", self.network_name]

        # Port mappings
        for host_port, container_port in self.target.ports.items():
            cmd.extend(["-p", f"{host_port}:{container_port}"])

        # Environment variables
        for key, value in self.target.env.items():
            cmd.extend(["-e", f"{key}={value}"])

        cmd.append(self.target.docker_image)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr}")

        self.container_id = result.stdout.strip()[:12]
        logger.info(f"Started {self.target.name} container: {self.container_id}")

    def _wait_for_health(self) -> None:
        """Wait for the target to become healthy via HTTP health check."""
        # For container-name-based access, we need to check via localhost
        # since the caller may not be on the Docker network
        host_port = self.target.health_port
        health_url = f"http://localhost:{host_port}{self.target.health_path}"
        timeout = self.target.startup_timeout
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                req = urllib.request.Request(health_url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status < 500:
                        logger.info(f"Target {self.target.name} is healthy")
                        return
            except (urllib.error.URLError, ConnectionError, OSError):
                pass

            time.sleep(3)

        # Not an error for Tier 1+ — some targets may not have HTTP
        logger.warning(
            f"Target {self.target.name} health check timed out after {timeout}s"
        )

    def _run_setup_commands(self) -> None:
        """Run post-startup setup commands in the container."""
        if not self.target.setup_commands or not self.container_id:
            return

        for cmd_str in self.target.setup_commands:
            try:
                result = subprocess.run(
                    ["docker", "exec", self.container_id, "sh", "-c", cmd_str],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    logger.warning(f"Setup command failed: {cmd_str}: {result.stderr}")
            except Exception as e:
                logger.warning(f"Setup command error: {cmd_str}: {e}")

    def _nmap_scan(self) -> Optional[str]:
        """Run nmap scan against the target, return stdout."""
        if not self.container_id:
            return None

        # Try running nmap directly (if available on host)
        try:
            result = subprocess.run(
                ["nmap", "-sV", "--open", "-p", "1-10000",
                 self.container_name, "-oX", "-"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: try from a Kali container on the same network
        try:
            result = subprocess.run(
                ["docker", "run", "--rm", "--network", self.network_name,
                 "kalilinux/kali-rolling",
                 "sh", "-c",
                 "apt-get update -qq && apt-get install -y -qq nmap > /dev/null 2>&1 && "
                 f"nmap -sV --open -p 1-10000 {self.container_name} -oX -"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            logger.warning(f"Kali nmap scan failed: {e}")

        return None

    def _parse_nmap_output(self, xml_output: str) -> Dict[str, Any]:
        """Parse nmap XML output into ground truth dict."""
        ground_truth: Dict[str, Any] = {"ports": [], "services": [], "technologies": []}

        # Simple regex parsing (avoid xml.etree for robustness)
        # Match: <port protocol="tcp" portid="80"><state state="open" .../>
        #        <service name="http" product="Apache httpd" version="2.4.49" .../>
        port_pattern = re.compile(
            r'portid="(\d+)".*?state="open".*?'
            r'service name="([^"]*)"'
            r'(?:\s+product="([^"]*)")?'
            r'(?:\s+version="([^"]*)")?',
            re.DOTALL,
        )

        for match in port_pattern.finditer(xml_output):
            port = int(match.group(1))
            service_name = match.group(2) or ""
            product = match.group(3) or ""
            version = match.group(4) or ""

            ground_truth["ports"].append(port)
            ground_truth["services"].append({
                "port": port,
                "name": service_name,
                "product": product,
                "version": version,
            })

        return ground_truth


__all__ = ["TargetManager"]
