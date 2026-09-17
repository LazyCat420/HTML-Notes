"""Research Budget Calculation & Policy Registry.

Enforces finite execution budgets based on question shape, preventing unconstrained
multi-agent loops or runaway latencies.
"""
from __future__ import annotations

from .models import ResearchIntent, ResearchBudget, ResearchMode


def calculate_research_budget(intent: ResearchIntent) -> ResearchBudget:
    """Derive a bounded budget from the typed research intent."""
    mode: ResearchMode = intent.mode
    depth = intent.evidence_depth

    if mode == "fast_market_brief":
        return ResearchBudget(
            foreground_deadline_ms=6000,
            background_deadline_ms=20000,
            max_parallel_tasks=3,
            max_full_text_fetches=2,
            max_evidence_items=12,
            max_llm_calls=1,
            cache_policy="market_intraday",
            stop_when=[
                "market snapshot available",
                "two reputable publisher sources",
            ],
        )

    if mode == "explain_move":
        fg_ms = 7000 if depth == "deep" else 6500
        return ResearchBudget(
            foreground_deadline_ms=fg_ms,
            background_deadline_ms=45000,
            max_parallel_tasks=4,
            max_full_text_fetches=4,
            max_evidence_items=18,
            max_llm_calls=2,
            cache_policy="intraday_catalyst",
            stop_when=[
                "price snapshot confirmed",
                "at least two catalyst candidates",
                "peer and sector divergence checked",
            ],
        )

    if mode == "event_report":
        return ResearchBudget(
            foreground_deadline_ms=7500,
            background_deadline_ms=45000,
            max_parallel_tasks=4,
            max_full_text_fetches=4,
            max_evidence_items=20,
            max_llm_calls=2,
            cache_policy="event_immutable",
            stop_when=[
                "one primary source (IR, filing, or transcript)",
                "headline consensus confirmed",
            ],
        )

    if mode == "comparison":
        return ResearchBudget(
            foreground_deadline_ms=8000,
            background_deadline_ms=60000,
            max_parallel_tasks=4,
            max_full_text_fetches=4,
            max_evidence_items=24,
            max_llm_calls=2,
            cache_policy="comparison_paired",
            stop_when=[
                "fundamentals for all compared entities",
                "relative divergence metrics calculated",
            ],
        )

    if mode == "dossier":
        return ResearchBudget(
            foreground_deadline_ms=10000,
            background_deadline_ms=90000,
            max_parallel_tasks=5,
            max_full_text_fetches=6,
            max_evidence_items=30,
            max_llm_calls=3,
            cache_policy="thesis_depth",
            stop_when=[
                "bull and bear arguments documented",
                "primary sources verified",
                "skeptic contradiction review completed",
            ],
        )

    if mode == "monitor":
        return ResearchBudget(
            foreground_deadline_ms=4000,
            background_deadline_ms=15000,
            max_parallel_tasks=2,
            max_full_text_fetches=1,
            max_evidence_items=8,
            max_llm_calls=1,
            cache_policy="watchlist_delta",
            stop_when=[
                "delta threshold evaluated",
            ],
        )

    if mode == "quant_signal":
        return ResearchBudget(
            foreground_deadline_ms=5000,
            background_deadline_ms=60000,
            max_parallel_tasks=2,
            max_full_text_fetches=2,
            max_evidence_items=10,
            max_llm_calls=2,
            cache_policy="offline_signal",
            stop_when=[
                "methodology and dataset proposal complete",
            ],
        )

    # General research fallback
    return ResearchBudget(
        foreground_deadline_ms=6500,
        background_deadline_ms=30000,
        max_parallel_tasks=3,
        max_full_text_fetches=3,
        max_evidence_items=15,
        max_llm_calls=2,
        cache_policy="standard_news",
        stop_when=[
            "three independent sources",
            "confidence >= 0.78",
        ],
    )
