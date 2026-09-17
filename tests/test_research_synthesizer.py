"""Unit tests for bounded research synthesizer."""
import pytest
from app.services.research.models import EvidenceItem, ResearchIntent, TimeWindow
from app.services.research.synthesizer import _deterministic_fallback_synthesis, synthesize_research_answer


def _sample_evidence():
    return [
        EvidenceItem(
            evidence_id="ev_price_nvda_123",
            url="https://example.com/nvda",
            canonical_url="https://example.com/nvda",
            title="NVDA Quote",
            publisher="Market Data",
            extracted_text="NVDA is down 3.2% to $120.50.",
            source_provider="yahoo_quote",
        ),
        EvidenceItem(
            evidence_id="ev_news_semi_456",
            url="https://example.com/news",
            canonical_url="https://example.com/news",
            title="Semiconductor Sector Slips",
            publisher="Reuters",
            extracted_text="Chipmakers face broad pressure amid macro rate concerns.",
            source_provider="reuters",
        )
    ]


def test_deterministic_fallback_synthesis():
    intent = ResearchIntent(mode="explain_move", raw_query="why is NVDA down today?")
    ev = _sample_evidence()

    ans = _deterministic_fallback_synthesis(intent, ev, version=1)
    assert ans.version == 1
    assert ans.status == "preliminary"
    assert "ev_price_nvda_123" in ans.evidence_ids
    assert "ev_news_semi_456" in ans.evidence_ids
    assert "120.50" in ans.text


@pytest.mark.asyncio
async def test_synthesizer_citation_filtering(monkeypatch):
    intent = ResearchIntent(mode="explain_move", raw_query="why is NVDA down today?")
    ev = _sample_evidence()

    # Mock fast_llm_json to return a hallucinated citation "ev_hallucinated_999"
    import app.llm as llm
    async def mock_llm_json(prompt, max_tokens=600):
        return {
            "answer": "NVDA fell 3.2% [ev_price_nvda_123] due to phantom rumors [ev_hallucinated_999].",
            "cited_evidence_ids": ["ev_price_nvda_123", "ev_hallucinated_999"],
            "delta_summary": "Initial synthesis."
        }

    monkeypatch.setattr(llm, "fast_llm_json", mock_llm_json)

    ans = await synthesize_research_answer(intent, ev, version=1)
    assert ans.version == 1
    assert "ev_price_nvda_123" in ans.evidence_ids
    assert "ev_hallucinated_999" not in ans.evidence_ids, "Hallucinated citation must be filtered!"
