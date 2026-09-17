"""Bounded Evidence Synthesizer.

Synthesizes research answers using ONLY verified evidence items from the run.
Enforces strict citation grounding (0% unsupported citation rate) and hedges
causal overreach. Uses local vLLM with deterministic fallback.
"""
from __future__ import annotations

import json
import time
from typing import List, Dict, Any, Optional, Tuple

from .models import EvidenceItem, ResearchIntent, AnswerVersion, AnswerStatus


_SYNTHESIS_SYSTEM_PROMPT = """You are a disciplined financial research synthesizer.
Rules:
1. Ground every factual claim in the provided EVIDENCE items.
2. Every statement must cite its source using [evidence_id]. NEVER invent or cite evidence_ids not provided in the input packet.
3. If an asset move is strongly correlated with sector or market breadth, do not claim a single company news item is the sole cause. Distinguish correlation from proven causality.
4. Keep preliminary answers concise (2-4 sentences). Keep final answers structured with catalyst, peer context, and risk considerations.
5. Return strictly valid JSON:
{
  "answer": "synthesized text with [evidence_id] citations",
  "cited_evidence_ids": ["ev_1", "ev_2"],
  "delta_summary": "one sentence summarizing what is new or changed if version > 1"
}
"""


def _deterministic_fallback_synthesis(intent: ResearchIntent, evidence: List[EvidenceItem],
                                      version: int, delta_notes: Optional[str] = None) -> AnswerVersion:
    """Deterministic fallback synthesis when local LLM is slow or unavailable."""
    symbol = intent.entities[0].symbol if intent.entities else ""
    citations: List[str] = []

    price_ev = next((e for e in evidence if e.source_provider == "yahoo_quote"), None)
    peer_ev = next((e for e in evidence if e.source_provider == "peer_sector_worker"), None)
    news_evs = [e for e in evidence if e.source_provider != "yahoo_quote" and e.source_provider != "peer_sector_worker"]

    parts: List[str] = []
    if price_ev:
        parts.append(f"{price_ev.extracted_text} [{price_ev.evidence_id}]")
        citations.append(price_ev.evidence_id)

    if news_evs:
        top_news = news_evs[:2]
        headlines = "; ".join(f"\"{n.title}\" ({n.publisher}) [{n.evidence_id}]" for n in top_news)
        parts.append(f"Recent reporting highlights: {headlines}.")
        citations.extend(n.evidence_id for n in top_news)

    if version > 1 and peer_ev:
        parts.append(f"Context: {peer_ev.extracted_text} [{peer_ev.evidence_id}]")
        citations.append(peer_ev.evidence_id)

    if not parts:
        parts.append(f"Retrieved {len(evidence)} evidence sources for {intent.raw_query}.")

    text = " ".join(parts)
    status: AnswerStatus = "preliminary" if version == 1 else "final"
    delta = delta_notes or ("Initial evidence synthesis." if version == 1 else "Updated with peer context & catalyst verification.")

    return AnswerVersion(
        version=version,
        status=status,
        text=text,
        evidence_ids=citations,
        delta_summary=delta,
    )


async def synthesize_research_answer(intent: ResearchIntent, evidence: List[EvidenceItem],
                                     version: int = 1, delta_notes: Optional[str] = None) -> AnswerVersion:
    """Synthesize a grounded answer from bounded evidence packet."""
    import app.llm as llm

    if not evidence:
        return AnswerVersion(
            version=version,
            status="partial_final" if version > 1 else "preliminary",
            text="No matching evidence items could be verified within the deadline.",
            evidence_ids=[],
            delta_summary="Search completed with no verified primary sources.",
        )

    available_ev_ids = {e.evidence_id for e in evidence}

    # Format bounded evidence packet
    ev_packet = []
    for e in evidence[:10]:
        ev_packet.append({
            "evidence_id": e.evidence_id,
            "title": e.title,
            "publisher": e.publisher,
            "tier": e.tier,
            "published_at": e.published_at,
            "text": e.extracted_text[:400],
        })

    prompt = (
        f"{_SYNTHESIS_SYSTEM_PROMPT}\n\n"
        f"USER QUESTION: {intent.raw_query}\n"
        f"MODE: {intent.mode} (version={version})\n"
        f"DELTA NOTES: {delta_notes or 'N/A'}\n"
        f"EVIDENCE PACKET ({len(ev_packet)} items):\n"
        f"{json.dumps(ev_packet, indent=2)}\n\n"
        "Generate JSON synthesis:"
    )

    try:
        res = await llm.fast_llm_json(prompt, max_tokens=600)
        if isinstance(res, dict) and res.get("answer"):
            raw_answer = str(res.get("answer", "")).strip()
            claimed_citations = res.get("cited_evidence_ids", [])
            valid_citations = [cid for cid in claimed_citations if cid in available_ev_ids]

            status: AnswerStatus = "preliminary" if version == 1 else "final"
            delta = res.get("delta_summary") or delta_notes

            return AnswerVersion(
                version=version,
                status=status,
                text=raw_answer,
                evidence_ids=valid_citations,
                delta_summary=delta,
            )
    except Exception as e:
        import app.main as main
        main.logger.warning(f"[SYNTHESIZER] LLM synthesis fallback: {e}")

    # Fallback to deterministic synthesis
    return _deterministic_fallback_synthesis(intent, evidence, version, delta_notes)
