"""
Runtime environment and client configuration for shared agent runtime integration.
Follows contract v1.2 specifications and provides typed config and handshake validators.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple


def _bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


# Feature flag: toggles shared runtime vs legacy prism / local agent
USE_SHARED_RUNTIME: bool = _bool_env("USE_SHARED_RUNTIME", default=False)

# Runtime connection URL
LAZYCAT_RUNTIME_URL: str = os.getenv(
    "LAZYCAT_RUNTIME_URL",
    os.getenv("LAZY_AGENT_URL", "http://10.0.0.16:5591")
)

# Canonical agent profile
HTML_NOTES_RUNTIME_PROFILE: str = os.getenv(
    "HTML_NOTES_RUNTIME_PROFILE",
    "html-notes-researcher-v1"
)

# Canonical contract version
HTML_NOTES_CONTRACT_VERSION: str = os.getenv(
    "HTML_NOTES_CONTRACT_VERSION",
    "1.2.0"
)

# Timeouts
RUNTIME_CONNECT_TIMEOUT_SECONDS: float = float(os.getenv("RUNTIME_CONNECT_TIMEOUT_SECONDS", "5.0"))
RUNTIME_READ_TIMEOUT_SECONDS: float = float(os.getenv("RUNTIME_READ_TIMEOUT_SECONDS", "90.0"))

# Context window limits
RUNTIME_MAX_CANVAS_CONTEXT_CHARS: int = int(os.getenv("RUNTIME_MAX_CANVAS_CONTEXT_CHARS", "4000"))


def parse_semver(version: str) -> Tuple[int, int, int]:
    """Parse major, minor, patch semver integers."""
    clean = version.strip().lstrip("v")
    parts = clean.split(".")
    major = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    patch = int(parts[2].split("-")[0]) if len(parts) > 2 and parts[2].split("-")[0].isdigit() else 0
    return major, minor, patch


def is_contract_compatible(required_version: str, reported_version: str) -> bool:
    """
    Checks semver compatibility:
    - Same major version required.
    - Reported minor must be >= required minor.
    """
    req_maj, req_min, _ = parse_semver(required_version)
    rep_maj, rep_min, _ = parse_semver(reported_version)
    if req_maj != rep_maj:
        return False
    return rep_min >= req_min


def validate_profile_against_manifest(profile_id: str, manifest_profile: dict) -> Tuple[bool, Optional[str]]:
    """Validates that requested profile ID matches declared application profile."""
    declared_id = manifest_profile.get("profile_id") or manifest_profile.get("id")
    if declared_id and declared_id != profile_id:
        return False, f"Requested profile '{profile_id}' does not match declared profile '{declared_id}'"
    return True, None
