"""Common benchmark schema for web-retrieval providers (bench/web).

Every adapter returns the same SearchResult shape or raises ProviderError,
so providers are comparable under one contract. Mirrors the normalized
result schema proposed in docs/audit/AUDIT_2026-09-21_WEB_DATA_COLLECTION_BENCHMARK.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

# Machine-readable error taxonomy. `soft_empty` exists specifically to expose
# audit finding F2: a provider answering 200-with-zero-results is NOT evidence
# the backend is alive.
OUTAGE = "outage"              # transport/network failure, provider unreachable
RATE_LIMITED = "rate_limited"  # HTTP 429 or explicit throttle body
CAPTCHA = "captcha"            # block page / anti-bot interstitial
MALFORMED = "malformed"        # 2xx but unparseable payload
SOFT_EMPTY = "soft_empty"      # 2xx, parsed fine, zero results
OK = "ok"


@dataclass
class SearchResult:
    provider: str
    provider_rank: int
    title: str
    url: str
    snippet: str = ""
    display_domain: str = ""
    published_at: Optional[str] = None   # only if provider genuinely supplies it
    retrieved_at: str = ""
    engine: str = ""                     # SearXNG per-result engine
    result_type: str = "web"             # web | news
    fields_genuine: frozenset = frozenset()  # field names supplied vs inferred


@dataclass
class ProviderResponse:
    provider: str
    query_id: str
    query: str
    status: str                    # OK or a taxonomy constant above
    results: list = field(default_factory=list)
    latency_ms: float = 0.0
    error: str = ""
    raw_path: str = ""             # artifact path of the raw response

    def to_dict(self):
        d = asdict(self)
        d["results"] = [asdict(r) for r in self.results]
        return d


class ProviderError(Exception):
    def __init__(self, status: str, detail: str = ""):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail
