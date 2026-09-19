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


from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import httpx

logger = logging.getLogger(__name__)


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


def validate_runtime_url(url: str) -> Tuple[bool, Optional[str]]:
    """Validates runtime connection URL syntax and scheme."""
    if not url or not isinstance(url, str) or not url.strip():
        return False, "Runtime URL cannot be empty"
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False, f"Invalid runtime URL scheme '{parsed.scheme}': must be http or https"
    if not parsed.netloc:
        return False, f"Invalid runtime URL '{url}': missing host/network location"
    return True, None


def validate_capabilities(profile_manifest: dict, required_capabilities: Optional[List[str]] = None) -> Tuple[bool, Optional[str]]:
    """Validates that expected global capabilities are permitted by the profile manifest."""
    expected = required_capabilities or ["global.web.search", "global.web.read_page"]
    allowed: List[str] = []

    # Check allowed_global_capabilities list
    raw_caps = profile_manifest.get("allowed_global_capabilities") or []
    for cap in raw_caps:
        clean = cap.split("@")[0].strip()
        allowed.append(clean)

    # Check tool_policy whitelist
    tool_policy = profile_manifest.get("tool_policy") or {}
    whitelist = tool_policy.get("whitelist") or []
    for tool in whitelist:
        clean = tool.split("@")[0].strip()
        allowed.append(clean)

    allowed_set = set(allowed)
    for exp in expected:
        if exp not in allowed_set:
            return False, f"Profile is missing required capability: '{exp}'"

    return True, None


def load_local_profile_manifest(custom_path: Optional[str] = None) -> Optional[dict]:
    """Loads local application profile manifest dictionary."""
    candidate_paths = []
    if custom_path:
        candidate_paths.append(Path(custom_path))
    else:
        this_file = Path(__file__).resolve()
        # Look in app/tooling/manifests/html_notes.profile.json
        candidate_paths.append(this_file.parent.parent.parent / "tooling" / "manifests" / "html_notes.profile.json")
        candidate_paths.append(Path("app/tooling/manifests/html_notes.profile.json"))

    for p in candidate_paths:
        if p.exists() and p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to parse profile manifest at {p}: {e}")
                return None
    return None


@dataclass
class RuntimeReadinessResult:
    is_ready: bool
    error: Optional[str] = None
    contract_version: Optional[str] = None
    profile_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


def should_enforce_readiness() -> bool:
    """True when shared runtime feature flag is enabled."""
    return _bool_env("USE_SHARED_RUNTIME", default=False)


async def check_runtime_readiness(
    runtime_url: Optional[str] = None,
    profile_id: Optional[str] = None,
    required_contract_version: Optional[str] = None,
    connect_timeout: Optional[float] = None,
    read_timeout: Optional[float] = None,
    manifest_path: Optional[str] = None,
    manifest_override: Optional[dict] = None,
    client: Optional[httpx.AsyncClient] = None,
    registered_profiles: Optional[List[str]] = None,
) -> RuntimeReadinessResult:
    """
    Validates shared agent runtime readiness before execution:
    1. Runtime URL is valid and well-formed.
    2. Profile ID is configured and non-empty.
    3. Profile matches local manifest html_notes.profile.json.
    4. Profile permits expected global capabilities.
    5. Runtime endpoint is reachable and returns HTTP 200.
    6. Runtime reports compatible v1.2 contract version (same major, reported minor >= required minor).
    7. Profile is registered in runtime.
    """
    url = runtime_url or os.getenv("LAZYCAT_RUNTIME_URL") or os.getenv("LAZY_AGENT_URL") or LAZYCAT_RUNTIME_URL
    prof = profile_id if profile_id is not None else (os.getenv("HTML_NOTES_RUNTIME_PROFILE") or HTML_NOTES_RUNTIME_PROFILE)
    req_version = required_contract_version or os.getenv("HTML_NOTES_CONTRACT_VERSION") or HTML_NOTES_CONTRACT_VERSION
    c_to = connect_timeout if connect_timeout is not None else float(os.getenv("RUNTIME_CONNECT_TIMEOUT_SECONDS", str(RUNTIME_CONNECT_TIMEOUT_SECONDS)))
    r_to = read_timeout if read_timeout is not None else float(os.getenv("RUNTIME_READ_TIMEOUT_SECONDS", str(RUNTIME_READ_TIMEOUT_SECONDS)))

    # 1. URL syntax validation
    url_ok, url_err = validate_runtime_url(url)
    if not url_ok:
        return RuntimeReadinessResult(
            is_ready=False,
            error=f"Invalid runtime URL: {url_err}",
            profile_id=prof,
            details={"phase": "url_validation", "url": url}
        )

    # 2. Profile ID validation
    if not prof or not str(prof).strip():
        return RuntimeReadinessResult(
            is_ready=False,
            error="Missing profile_id: runtime profile cannot be empty",
            profile_id=prof,
            details={"phase": "profile_check"}
        )

    # 3. Local manifest validation
    local_manifest = manifest_override if manifest_override is not None else load_local_profile_manifest(manifest_path)
    if local_manifest is not None:
        prof_ok, prof_err = validate_profile_against_manifest(prof, local_manifest)
        if not prof_ok:
            return RuntimeReadinessResult(
                is_ready=False,
                error=prof_err or f"Configured profile '{prof}' does not match local manifest",
                profile_id=prof,
                details={"phase": "profile_manifest_mismatch", "manifest_profile": local_manifest.get("profile_id")}
            )

        caps_ok, caps_err = validate_capabilities(local_manifest)
        if not caps_ok:
            return RuntimeReadinessResult(
                is_ready=False,
                error=caps_err,
                profile_id=prof,
                details={"phase": "capability_check"}
            )

    # 4. Runtime reachability and contract version check
    # Construct contract spec endpoint URL
    clean_url = url.strip().rstrip("/")
    if clean_url.endswith("/v1/runs"):
        spec_url = clean_url[:-8] + "/v1/contracts/spec"
    else:
        spec_url = f"{clean_url}/v1/contracts/spec"

    timeout_cfg = httpx.Timeout(timeout=r_to, connect=c_to)
    should_close_client = False
    http_client = client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=timeout_cfg)
        should_close_client = True

    try:
        resp = await http_client.get(spec_url)
        if resp.status_code != 200:
            return RuntimeReadinessResult(
                is_ready=False,
                error=f"Runtime health check returned HTTP {resp.status_code}",
                profile_id=prof,
                details={"phase": "reachability", "status_code": resp.status_code}
            )

        spec_data = resp.json()
        reported_version = spec_data.get("version") or resp.headers.get("x-contract-version") or ""
        if not reported_version:
            return RuntimeReadinessResult(
                is_ready=False,
                error="Runtime failed to report contract version in /v1/contracts/spec",
                profile_id=prof,
                details={"phase": "contract_version"}
            )

        # Semver compatibility
        req_maj, req_min, _ = parse_semver(req_version)
        rep_maj, rep_min, _ = parse_semver(reported_version)
        if req_maj != rep_maj:
            return RuntimeReadinessResult(
                is_ready=False,
                error=f"Incompatible contract major version: required {req_version}, runtime reports {reported_version}",
                contract_version=reported_version,
                profile_id=prof,
                details={"phase": "contract_major_mismatch", "reported_version": reported_version}
            )
        if rep_min < req_min:
            return RuntimeReadinessResult(
                is_ready=False,
                error=f"Incompatible contract minor version: required {req_version}, runtime reports older {reported_version}",
                contract_version=reported_version,
                profile_id=prof,
                details={"phase": "contract_minor_mismatch", "reported_version": reported_version}
            )

        # 5. Profile registration check
        # Check explicit registered profiles passed in or returned in spec
        known_profiles = registered_profiles or spec_data.get("registered_profiles") or spec_data.get("profiles")
        if known_profiles is not None:
            if prof not in known_profiles:
                return RuntimeReadinessResult(
                    is_ready=False,
                    error=f"Profile '{prof}' is not registered in runtime",
                    contract_version=reported_version,
                    profile_id=prof,
                    details={"phase": "unregistered_profile", "registered_profiles": known_profiles}
                )

        return RuntimeReadinessResult(
            is_ready=True,
            contract_version=reported_version,
            profile_id=prof,
            details={"spec": spec_data}
        )

    except Exception as exc:
        return RuntimeReadinessResult(
            is_ready=False,
            error=f"Runtime endpoint unreachable: {exc}",
            profile_id=prof,
            details={"phase": "reachability", "exception": str(exc)}
        )
    finally:
        if should_close_client:
            await http_client.aclose()

