"""Docker image profiles for penetration testing environments."""

from __future__ import annotations

from typing import Any, Dict

DOCKER_PROFILES: Dict[str, Dict[str, Any]] = {
    "kali": {
        "image": "kalilinux/kali-rolling",
        "workspace_root": "/workspace",
        "extra_run_args": ["--cap-add=NET_ADMIN", "--privileged"],
        "description": "Kali Linux rolling release with full pentest toolset",
    },
    "parrot": {
        "image": "parrotsec/security",
        "workspace_root": "/workspace",
        "extra_run_args": ["--cap-add=NET_ADMIN"],
        "description": "Parrot Security OS with pentest tools",
    },
    "ubuntu": {
        "image": "ubuntu:22.04",
        "workspace_root": "/workspace",
        "extra_run_args": [],
        "description": "Ubuntu 22.04 base image (tools must be installed)",
    },
}


def get_docker_config(profile: str) -> Dict[str, Any]:
    """Get Docker configuration for the given profile name."""
    if profile not in DOCKER_PROFILES:
        raise ValueError(
            f"Unknown Docker profile: {profile!r}. "
            f"Available: {list(DOCKER_PROFILES.keys())}"
        )
    return DOCKER_PROFILES[profile]
