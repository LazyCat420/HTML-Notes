"""Automated Judge for the Research Protocol Bake-Off.

Evaluates research outputs across relevance, factual grounding, causal discipline,
and latency efficiency using local vLLM with deterministic heuristic fallback.
STRICTLY NO CLOUD MODELS.
"""
from __future__ import annotations

import json
import re
from typing import Dict, Any, Optional


_JUDGE_PROMPT = """You are a rigorous, objective research evaluator.
Grade the provided system response to the user query on a scale of 0 to 10 for each dimension:

1. relevance: Does the response directly answer the specific question asked without irrelevant clutter?
2. factual_grounding: Are the claims supported by concrete facts, price numbers, or reputable news?
3. causal_discipline: Does the response avoid false causation (e.g. claiming a rumor caused a move when the whole market fell)?
4. substance: Does the response provide meaningful depth rather than empty filler?

Output strictly valid JSON:
{
  "relevance": 9.0,
  "factual_grounding": 8.5,
  "causal_discipline": 9.0,
  "substance": 8.0,
  "overall": 8.6,
  "reasoning": "one sentence"
}
"""


def _heuristic_judge(query: str, answer_text: str, evidence_count: int,
                     has_provisional: bool, latency_ms: float) -> Dict[str, float]:
    """Deterministic heuristic grading fallback."""
    q_low = query.lower()
    ans_low = answer_text.lower()

    # Relevance check
    words = [w for w in re.findall(r"[a-z0-9]+", q_low) if len(w) > 2 and w not in {"why", "how", "what", "today", "the", "and"}]
    matched_words = sum(1 for w in words if w in ans_low)
    relevance = min(10.0, 5.0 + (5.0 * (matched_words / max(len(words), 1)))) if words else 9.0

    # Grounding check (presence of citations, numbers, and evidence items)
    has_numbers = bool(re.search(r"\$?\d+(?:\.\d+)?%?", answer_text))
    grounding = 5.0
    if evidence_count >= 1:
        grounding += 2.0
    if evidence_count >= 3:
        grounding += 1.5
    if has_numbers:
        grounding += 1.5
    grounding = min(10.0, grounding)

    # Causal discipline (checks for hedges vs naked claims)
    has_hedge = bool(re.search(r"\b(sector|breadth|macro|correlated|pressured|market-wide|divergence)\b", ans_low))
    causality = 8.5 if has_hedge else 7.5

    substance = min(10.0, 4.0 + (len(answer_text) / 50.0))
    overall = round((relevance * 0.35) + (grounding * 0.35) + (causality * 0.2) + (substance * 0.1), 2)

    return {
        "relevance": round(relevance, 1),
        "factual_grounding": round(grounding, 1),
        "causal_discipline": round(causality, 1),
        "substance": round(substance, 1),
        "overall": overall,
    }


async def grade_research_output(query: str, answer_text: str, evidence_count: int,
                                has_provisional: bool, latency_ms: float) -> Dict[str, float]:
    """Grade output using local vLLM or deterministic heuristic."""
    import app.llm as llm

    if not answer_text or not answer_text.strip():
        return {
            "relevance": 0.0,
            "factual_grounding": 0.0,
            "causal_discipline": 0.0,
            "substance": 0.0,
            "overall": 0.0,
        }

    prompt = (
        f"{_JUDGE_PROMPT}\n\n"
        f"USER QUERY: {query}\n"
        f"EVIDENCE COUNT: {evidence_count}\n"
        f"SYSTEM RESPONSE:\n{answer_text[:1200]}\n\n"
        "Return JSON:"
    )

    try:
        res = await llm.fast_llm_json(prompt, max_tokens=300)
        if isinstance(res, dict) and "overall" in res:
            return {
                "relevance": float(res.get("relevance", 7.0)),
                "factual_grounding": float(res.get("factual_grounding", 7.0)),
                "causal_discipline": float(res.get("causal_discipline", 7.0)),
                "substance": float(res.get("substance", 7.0)),
                "overall": float(res.get("overall", 7.0)),
            }
    except Exception:
        pass

    return _heuristic_judge(query, answer_text, evidence_count, has_provisional, latency_ms)
