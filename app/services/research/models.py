"""Typed data contracts for the HTML-Notes Research Protocol.

Covers:
- ResearchIntent: deterministic & classified request representation
- ResearchBudget: bounded latency, task, and iteration limits
- EvidenceItem: normalized, scored, and provenance-tracked facts/articles
- ResearchTask: individual sub-task tracking in the run ledger
- ResearchRun: full lifecycle ledger of a research query
- AnswerVersion: versioned syntheses with delta tracking
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal, Optional, List, Dict, Any


ResearchMode = Literal[
    "fast_market_brief",
    "explain_move",
    "event_report",
    "comparison",
    "dossier",
    "monitor",
    "quant_signal",
    "general",
]

EvidenceDepth = Literal["fast", "standard", "deep"]
FreshnessState = Literal["fresh", "cached", "stale", "unknown"]
SourceTier = Literal["primary", "tier1_news", "secondary", "social", "internal"]
TaskState = Literal["pending", "running", "completed", "failed", "cancelled", "timed_out"]
RunStatus = Literal[
    "foreground_running",
    "foreground_settled",
    "background_running",
    "completed",
    "cancelled",
    "partial_final",
    "failed",
]
AnswerStatus = Literal["preliminary", "updated", "final", "partial_final", "superseded"]


@dataclass
class Entity:
    symbol: str                         # e.g. "NVDA", "^GSPC", "BTC-USD"
    name: str                           # e.g. "NVIDIA Corporation"
    kind: Literal["equity", "etf", "index", "crypto", "commodity", "macro", "company"] = "equity"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TimeWindow:
    label: Literal["today", "1d", "5d", "1m", "since_event", "custom"] = "today"
    start_iso: Optional[str] = None
    end_iso: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchIntent:
    mode: ResearchMode
    entities: List[Entity] = field(default_factory=list)
    geography: str = "us"               # us, global, europe, crypto
    asset_classes: List[str] = field(default_factory=lambda: ["equity"])
    time_window: TimeWindow = field(default_factory=TimeWindow)
    evidence_depth: EvidenceDepth = "standard"
    user_asked_for_opinion: bool = False
    user_asked_for_sources: bool = False
    needs_live_price: bool = False
    needs_primary_sources: bool = False
    needs_comparison: bool = False
    event_types: List[str] = field(default_factory=list)  # earnings, cpi, fomc, filing, guidance, m&a
    raw_query: str = ""
    subject_hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entities"] = [e.to_dict() if isinstance(e, Entity) else e for e in self.entities]
        d["time_window"] = self.time_window.to_dict() if isinstance(self.time_window, TimeWindow) else self.time_window
        return d


@dataclass
class ResearchBudget:
    foreground_deadline_ms: int = 6500
    background_deadline_ms: int = 60000
    max_parallel_tasks: int = 4
    max_full_text_fetches: int = 4
    max_evidence_items: int = 24
    max_llm_calls: int = 2
    cache_policy: str = "market_intraday"
    stop_when: List[str] = field(default_factory=lambda: [
        "three independent relevant sources",
        "one primary source for material claim",
        "confidence >= 0.8"
    ])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceItem:
    evidence_id: str
    url: str
    canonical_url: str
    title: str
    publisher: str
    tier: SourceTier = "secondary"
    published_at: Optional[str] = None
    extracted_text: str = ""
    entity_relevance: float = 1.0       # 0.0 to 1.0
    quality_score: float = 1.0          # 0.0 to 1.0
    freshness: FreshnessState = "fresh"
    source_provider: str = ""           # yahoo, finnews, brave, edgar, scraper
    age_seconds: Optional[float] = None
    related_tickers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchTask:
    task_id: str
    task_type: str                      # price, news, primary_source, peer_sector, fundamentals, skeptic
    worker_id: str
    state: TaskState = "pending"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    deadline_ms: int = 5000
    cache_hit: bool = False
    evidence_ids: List[str] = field(default_factory=list)
    error_class: Optional[str] = None
    used_in_answer: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnswerVersion:
    version: int
    status: AnswerStatus
    text: str
    evidence_ids: List[str] = field(default_factory=list)
    delta_summary: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchRun:
    run_id: str
    session_id: str
    message_id: str
    intent: ResearchIntent
    budget: ResearchBudget
    status: RunStatus = "foreground_running"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    foreground_deadline_at: Optional[str] = None
    background_deadline_at: Optional[str] = None
    tasks: List[ResearchTask] = field(default_factory=list)
    evidence: Dict[str, EvidenceItem] = field(default_factory=dict)
    answer_versions: List[AnswerVersion] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "intent": self.intent.to_dict(),
            "budget": self.budget.to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "foreground_deadline_at": self.foreground_deadline_at,
            "background_deadline_at": self.background_deadline_at,
            "tasks": [t.to_dict() for t in self.tasks],
            "evidence": {k: v.to_dict() for k, v in self.evidence.items()},
            "answer_versions": [a.to_dict() for a in self.answer_versions],
            "metrics": self.metrics,
        }
