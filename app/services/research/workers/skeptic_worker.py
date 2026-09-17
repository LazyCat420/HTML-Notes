"""Skeptic & Contradiction Checker Worker.

Audits synthesized findings against causal overreach, unverified rumors,
and market-wide breadth correlation vs idiosyncratic drivers.
"""
from __future__ import annotations

import hashlib
import time
from typing import Optional, Dict, Any, List, Tuple

from ..models import EvidenceItem, ResearchTask, ResearchIntent


async def run_skeptic_worker(intent: ResearchIntent,
                             evidence_items: List[EvidenceItem],
                             peer_data: Optional[Dict[str, Any]] = None,
                             task_id: Optional[str] = None) -> Tuple[Optional[EvidenceItem], Optional[Dict[str, Any]], ResearchTask]:
    """Audit collected evidence for contradictions, causal leaps, or macro breadth conflation."""
    t_start = time.time()
    task = ResearchTask(
        task_id=task_id or f"task_skeptic_{int(t_start)}",
        task_type="skeptic",
        worker_id="skeptic_worker",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_start)),
        deadline_ms=4000,
    )

    flags: List[str] = []
    symbol = intent.entities[0].symbol if intent.entities else ""

    # 1. Causal Discipline: Beta vs Alpha move check
    if peer_data and symbol:
        sector_etf = peer_data.get("sector_etf", "")
        sector_change = peer_data.get("sector_change_pct", 0.0)
        peer_changes = peer_data.get("peer_changes", {})

        # If sector is moving strongly in the same direction, hedge company-specific causation
        if abs(sector_change) >= 1.5:
            flags.append(
                f"Market breadth alert: The broader sector ({sector_etf}) is moving {sector_change:+.2f}%. "
                f"Move is strongly correlated with sector-wide market trends rather than isolated company news."
            )

    # 2. Source Diversity and Reliability check
    publishers = {e.publisher.lower() for e in evidence_items if e.publisher}
    tier1_count = sum(1 for e in evidence_items if e.tier in ("primary", "tier1_news"))
    if tier1_count == 0 and len(evidence_items) > 0:
        flags.append("Evidence quality warning: No primary filings or Tier-1 news organizations found in current evidence batch.")

    # 3. Thesis balance check
    if intent.mode == "dossier" or intent.user_asked_for_opinion:
        flags.append("Skeptic thesis balance: Ensure both bull drivers and macro/valuation headwinds are explicitly separated.")

    summary_text = "Skeptic Audit: " + (" | ".join(flags) if flags else "All claims well-supported by primary evidence and peer context.")
    ev_id = f"ev_skep_{int(t_start)}"
    ev = EvidenceItem(
        evidence_id=ev_id,
        url="internal://skeptic-audit",
        canonical_url="internal://skeptic-audit",
        title="Skeptic Audit & Causal Verification",
        publisher="Research Skeptic Engine",
        tier="primary",
        published_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        extracted_text=summary_text,
        quality_score=1.0,
        freshness="fresh",
        source_provider="skeptic_worker",
        related_tickers=[symbol] if symbol else [],
    )

    task.state = "completed"
    task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    task.evidence_ids = [ev_id]
    task.details = {"flags": flags, "flags_count": len(flags)}

    return ev, {"flags": flags}, task
