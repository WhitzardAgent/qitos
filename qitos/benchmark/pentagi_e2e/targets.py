"""Vulnerable target definitions for PentAGI e2e testing.

Each target is a deliberately vulnerable Docker container used as a
pentest practice target. Targets define the Docker image, port mappings,
health checks, and initialization commands needed for automated testing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VulnerableTarget:
    """A containerized vulnerable application for pentest testing.

    Attributes
    ----------
    name : str
        Unique identifier for the target (e.g., "dvwa").
    docker_image : str
        Docker Hub image name.
    ports : dict
        Mapping of host_port -> container_port.
    env : dict
        Environment variables for container setup.
    health_path : str
        HTTP path to GET for readiness check.
    health_port : int
        Container port for the health check.
    startup_timeout : int
        Max seconds to wait for the health check.
    setup_commands : list[str]
        Shell commands to run inside the container after startup.
    min_tier : int
        Minimum test tier this target supports (1=smoke, 4=exploit).
    description : str
        Human-readable description of the target.
    """

    name: str
    docker_image: str
    ports: Dict[int, int] = field(default_factory=dict)
    env: Dict[str, str] = field(default_factory=dict)
    health_path: str = "/"
    health_port: int = 80
    startup_timeout: int = 60
    setup_commands: List[str] = field(default_factory=list)
    min_tier: int = 1
    max_tier: int = 4
    description: str = ""


# ---------------------------------------------------------------------------
# Target definitions
# ---------------------------------------------------------------------------

DVWA = VulnerableTarget(
    name="dvwa",
    docker_image="vulnerables/web-dvwa",
    ports={80: 80},
    env={},
    health_path="/login.php",
    health_port=80,
    startup_timeout=90,
    # After container starts, set DVWA security to "low" for reproducible vulns
    setup_commands=[
        # DVWA setup via its setup page — the container auto-initializes
        # but we need to configure security level
        'php -r "file_put_contents(\'/var/www/html/config/config.inc.php\', str_replace(\'DVWA_SECURITY_LEVEL\\\',\\\'\\\'\', \'DVWA_SECURITY_LEVEL\\\',\\\'low\\\'\', file_get_contents(\'/var/www/html/config/config.inc.php\')));"',
    ],
    min_tier=1,
    max_tier=4,
    description="Damn Vulnerable Web Application — SQL injection, XSS, command injection, file upload",
)

JUICE_SHOP = VulnerableTarget(
    name="juice-shop",
    docker_image="bkimminich/juice-shoop",
    ports={3000: 3000},
    env={},
    health_path="/",
    health_port=3000,
    startup_timeout=120,
    setup_commands=[],
    min_tier=1,
    max_tier=3,
    description="OWASP Juice Shop — XSS, SQL injection, broken access control",
)

METASPLOITABLE2 = VulnerableTarget(
    name="metasploitable2",
    docker_image="tleemcjr/metasploitable2",
    ports={21: 21, 22: 22, 80: 80, 445: 445, 3306: 3306},
    env={},
    health_path="/",
    health_port=80,
    startup_timeout=180,
    setup_commands=[],
    min_tier=2,
    max_tier=4,
    description="Metasploitable2 — VSFTPD backdoor, Samba vulns, weak credentials, unpatched services",
)

WEBGOAT = VulnerableTarget(
    name="webgoat",
    docker_image="webgoat/webgoat",
    ports={8080: 8080, 9090: 9090},
    env={},
    health_path="/WebGoat/login",
    health_port=8080,
    startup_timeout=120,
    setup_commands=[],
    min_tier=1,
    max_tier=3,
    description="WebGoat — SQL injection, auth bypass, XSS",
)

# Registry of all targets
TARGETS: Dict[str, VulnerableTarget] = {
    "dvwa": DVWA,
    "juice-shop": JUICE_SHOP,
    "metasploitable2": METASPLOITABLE2,
    "webgoat": WEBGOAT,
}


def get_targets_for_tier(tier: int) -> List[VulnerableTarget]:
    """Return targets that support the given test tier."""
    return [t for t in TARGETS.values() if t.min_tier <= tier <= t.max_tier]


__all__ = [
    "VulnerableTarget",
    "TARGETS",
    "DVWA",
    "JUICE_SHOP",
    "METASPLOITABLE2",
    "WEBGOAT",
    "get_targets_for_tier",
]
