# Deterministic Evidence-First News Pipeline (2026-09-17)

## Overview
Replaced the fragmented multi-builder/fallback news behavior with a single deterministic, evidence-first pipeline (`app/services/news_pipeline.py`):

\[
\text{news request} \rightarrow \text{normalize request} \rightarrow \text{retrieve candidates} \rightarrow \text{validate + dedupe} \rightarrow \text{fetch/verify article evidence} \rightarrow \text{rank} \rightarrow \text{render}
\]

## Core Components

### 1. Data Contracts
- `NewsRequest`: `topic: str | None`, `locale: str = "US"`, `recency_hours: int = 24`, `limit: int = 6`, `mode: Literal["general", "topic", "finance"]`.
- `VerifiedArticle`: `id: str`, `title: str`, `url: str`, `publisher: str`, `published_at: datetime | None`, `snippet: str`, `body_excerpt: str | None`, `source_tier: str`, `verified: bool`, `relevance_score: float | None`.
- `NewsResponse`: `card_config: Dict[str, Any]`, `articles: List[VerifiedArticle]`, `trace: NewsTrace`.
- `NewsTrace`: Per-request structured audit trace with candidate counts, rejection breakdowns, timings, and displayed article IDs.

### 2. Request Normalization
- Blank/general asks (`""`, `"news"`, `"headlines"`, `"top stories"`, `"whats going on in the news"`) map to `topic=None`, never a fabricated query such as `"news top stories"`.
- Concrete default locale (`US`), with user override support (`GB`, `CA`, `EU`, `IN`, `AU`, `JP`).
- Recency window: 24h default ("today"), 72h ("latest"), 168h ("background"/"past week").
- Deterministic keyword and ticker logic classifies finance mode before retrieval.
- Literal subject is preserved for topic requests without collapsing to generic keywords.

### 3. Tier 1 Discovery: lazy-agent-service
- Invokes native `news_search` first with normalized topic (`""` on general asks), locale, recency, and result count.
- Results are strictly treated as candidates. Malformed rows (empty title, non-http URL, missing domain) are rejected upfront.

### 4. Evidence Verification: scraper-service
- Promoted `scraper-service` to active verification of candidate URLs.
- Checks:
  1. Content length $\ge 120$ characters.
  2. Page title / first paragraph token overlap $\ge 0.35$ or title substring match.
  3. Topic / entity presence (with alias resolution for tech & market entities).
  4. Publication time within recency window.
- Unverified candidates retain `verified=False` and are strictly barred from appearing as top stories.

### 5. Quality & Diversity Gates
- General news enforces $\ge 3$ distinct publishers in the top 5.
- No single publisher may occupy more than 2 slots in the top 5.
- Excludes foreign country-biased outlets when `locale="US"` and topic is unstated.
- Canonical URL parameter stripping (`utm_*`, `ref`, `fbclid`, etc.) and title normalization prevent duplicate wire stories.

### 6. Restricted Fast LLM Role
- `fast_llm_json` only ranks verified articles and generates overviews from provided excerpts.
- Cannot invent search queries, hallucinate article metadata, or select unverified articles.
- Robust deterministic fallback if the fast model is unavailable or malformed.

### 7. Safe Degradation
- $\ge 5$ verified: Standard card.
- $1-4$ verified: Limited verified coverage card (`"Limited verified coverage (N verified)"`).
- $0$ verified: Honest unavailable state with 0 items; no synthetic headlines.

## Verification Suite (`tests/test_news_pipeline.py`)
10/10 gating tests passing:
1. `test_general_news_normalization_no_synthetic_query`: Confirms topic=None, no synthetic query generation.
2. `test_source_integrity_rejected_on_mismatch`: Confirms title/URL mismatch rejected and marked unverified.
3. `test_locale_enforcement_us_excludes_country_bias`: Confirms US general query excludes country-biased outlets.
4. `test_recency_filter_today_excludes_stale`: Confirms 24h window excludes stale articles.
5. `test_entity_topic_matching_name_handle_alias`: Confirms handle, ticker, and alias resolution.
6. `test_publisher_diversity_gate`: Confirms $\ge 3$ distinct publishers and max 2 per publisher.
7. `test_unverified_candidates_cannot_render_ahead_of_verified`: Confirms hard gate on `verified=True`.
8. `test_safe_degradation_zero_verified_renders_honest_unavailable`: Confirms 0 verified produces honest unavailable state.
9. `test_fast_llm_cannot_alter_facts_or_invent_metadata`: Confirms LLM cannot invent metadata or items.
10. `test_end_to_end_benchmark_current_vs_new_route`: Confirms performance, trace completeness, and verified integrity.
