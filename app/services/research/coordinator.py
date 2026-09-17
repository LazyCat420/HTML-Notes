"""Research Coordinator & Stream Orchestrator.

Manages the dual foreground/background execution lifecycle:
- Instant acknowledgement (<250ms)
- Fast retrieval race & provisional widget commit (<2.5s)
- Preliminary answer synthesis (<6s)
- Asynchronous background workers (peer context, primary sources, skeptic audit)
- Material update promotion & research completion
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator, Optional, Dict, Any, List
from bs4 import BeautifulSoup

from .models import (
    ResearchIntent,
    ResearchBudget,
    ResearchRun,
    EvidenceItem,
    AnswerVersion,
)
from .ledger import research_ledger
from .cache import research_cache, single_flight
from .workers.price_worker import run_price_worker
from .workers.news_worker import run_news_worker
from .workers.peer_sector_worker import run_peer_sector_worker
from .workers.primary_source_worker import run_primary_source_worker
from .workers.skeptic_worker import run_skeptic_worker
from .synthesizer import synthesize_research_answer


async def execute_research_stream(
    session_id: str,
    message_id: str,
    intent: ResearchIntent,
    budget: ResearchBudget,
    raw_message: str,
) -> AsyncGenerator[str, None]:
    """Execute the full dual-track research lifecycle and yield SSE lines."""
    import app.main as main
    from app.canvas_manager import commit_canvas

    t0 = time.time()
    run = research_ledger.create_run(session_id, message_id, intent, budget)

    # 1. Instant Acknowledgement (<250ms)
    symbol_str = intent.entities[0].symbol if intent.entities else ""
    summary_msg = f"Checking {symbol_str}'s intraday move and today's catalysts." if intent.mode == "explain_move" and symbol_str else f"Gathering evidence for: {intent.raw_query[:50]}"

    yield f"data: {json.dumps({'type': 'research.started', 'run_id': run.run_id, 'mode': intent.mode, 'summary': summary_msg})}\n\n"
    yield f"data: {json.dumps({'type': 'status', 'message': summary_msg, 'phase': 'research'})}\n\n"

    tasks_planned = ["news"]
    if intent.needs_live_price and intent.entities:
        tasks_planned.insert(0, "price")
    if intent.needs_comparison:
        tasks_planned.append("peer_comparison")
    if intent.needs_primary_sources:
        tasks_planned.append("primary_sources")
    if intent.mode in ("explain_move", "dossier"):
        tasks_planned.append("skeptic_review")

    yield f"data: {json.dumps({'type': 'research.plan', 'run_id': run.run_id, 'tasks': tasks_planned, 'foreground_deadline_ms': budget.foreground_deadline_ms})}\n\n"

    # Check cache
    cache_key = research_cache.make_key("synthesis", entity=symbol_str, query=intent.raw_query)
    cached_val, freshness, age_s = await research_cache.get(cache_key, allow_stale=True)
    if cached_val and freshness in ("fresh", "cached"):
        yield f"data: {json.dumps({'type': 'research.cache_hit', 'kind': 'synthesis', 'freshness': freshness, 'age_s': round(age_s, 1)})}\n\n"

    # 2. Foreground Retrieval Race
    evidence_collected: List[EvidenceItem] = []
    provisional_placed = False
    widget_id: Optional[str] = None
    widget_type = "data_card"

    # Define workers
    symbol = intent.entities[0].symbol if intent.entities else ""

    async def _do_price():
        if not symbol:
            return None, None, None
        return await run_price_worker(symbol, range_=intent.time_window.label)

    async def _do_news():
        syms = [e.symbol for e in intent.entities]
        return await run_news_worker(query=intent.raw_query, tickers=syms, limit=6)

    price_task_fut = asyncio.create_task(_do_price()) if symbol else None
    news_task_fut = asyncio.create_task(_do_news())

    # Wait on first available quality evidence or foreground deadline
    deadline_s = min(budget.foreground_deadline_ms / 1000.0, 5.5)
    t_limit = t0 + deadline_s

    # Poll / race for fast provisional render
    while time.time() < t_limit:
        # Check price result
        if price_task_fut and price_task_fut.done() and not provisional_placed:
            try:
                p_ev, p_cfg, p_task = price_task_fut.result()
                if p_task:
                    research_ledger.record_task(run.run_id, p_task)
                if p_ev:
                    evidence_collected.append(p_ev)
                    research_ledger.record_evidence(run.run_id, p_ev)
                if p_cfg and not provisional_placed:
                    widget_id = f"stock-{symbol.lower()}-{run.run_id[:6]}"
                    widget_type = "data_card"
                    p_cfg["provisional"] = True

                    def _place_prov(soup):
                        html = main.render_widget("data_card", widget_id, p_cfg)
                        grid = soup.select_one("#dashboard-grid") or soup
                        grid.insert(0, BeautifulSoup(html, "html.parser"))

                    evt = await commit_canvas(session_id, _place_prov)
                    if evt:
                        provisional_placed = True
                        yield evt
                        yield f"data: {json.dumps({'type': 'widget.provisional', 'widget_id': widget_id, 'config': p_cfg})}\n\n"
                        yield f"data: {json.dumps({'type': 'status', 'message': f'Live {symbol} quote ready · checking headlines & catalysts…'})}\n\n"
            except Exception as e:
                main.logger.warning(f"[RESEARCH] Price provisional error: {e}")

        # Check news result
        if news_task_fut.done():
            try:
                n_evs, n_cfg, n_task = news_task_fut.result()
                if n_task:
                    research_ledger.record_task(run.run_id, n_task)
                for nev in (n_evs or []):
                    if nev.evidence_id not in {e.evidence_id for e in evidence_collected}:
                        evidence_collected.append(nev)
                        research_ledger.record_evidence(run.run_id, nev)

                if n_cfg and not provisional_placed:
                    widget_id = f"news-{symbol.lower() if symbol else 'market'}-{run.run_id[:6]}"
                    n_cfg["provisional"] = True

                    def _place_news_prov(soup):
                        html = main.render_widget("data_card", widget_id, n_cfg)
                        grid = soup.select_one("#dashboard-grid") or soup
                        grid.insert(0, BeautifulSoup(html, "html.parser"))

                    evt = await commit_canvas(session_id, _place_news_prov)
                    if evt:
                        provisional_placed = True
                        yield evt
                        yield f"data: {json.dumps({'type': 'widget.provisional', 'widget_id': widget_id, 'config': n_cfg})}\n\n"
                        yield f"data: {json.dumps({'type': 'status', 'message': 'Top headlines loaded · composing preliminary answer…'})}\n\n"
                break
            except Exception as e:
                main.logger.warning(f"[RESEARCH] News result error: {e}")
                break

        await asyncio.sleep(0.1)

    # Harvest remaining foreground task if finished
    if price_task_fut and price_task_fut.done():
        try:
            p_ev, p_cfg, p_task = price_task_fut.result()
            if p_ev and p_ev.evidence_id not in {e.evidence_id for e in evidence_collected}:
                evidence_collected.append(p_ev)
                research_ledger.record_evidence(run.run_id, p_ev)
        except Exception:
            pass

    yield f"data: {json.dumps({'type': 'research.source_batch', 'run_id': run.run_id, 'accepted': len(evidence_collected), 'publishers': list({e.publisher for e in evidence_collected})})}\n\n"

    # 3. First-Answer Gate: Preliminary Synthesis (v1)
    ans_v1 = await synthesize_research_answer(intent, evidence_collected, version=1)
    research_ledger.record_answer_version(run.run_id, ans_v1)

    yield f"data: {json.dumps({'type': 'answer.partial', 'run_id': run.run_id, 'version': 1, 'text': ans_v1.text, 'evidence_ids': ans_v1.evidence_ids})}\n\n"

    # Stream preliminary text chunks to chat bubble
    words = ans_v1.text.split(" ")
    for i in range(0, len(words), 3):
        chunk = " ".join(words[i:i + 3]) + (" " if i + 3 < len(words) else "")
        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        await asyncio.sleep(0.02)

    research_ledger.update_status(run.run_id, "foreground_settled")

    # 4. Background Track: Deep Investigation & Late Result Merge
    needs_background = intent.mode in ("explain_move", "event_report", "comparison", "dossier") and symbol
    if needs_background:
        yield f"data: {json.dumps({'type': 'status', 'message': 'Preliminary answer shown · checking peer moves, filings, and skeptic context…'})}\n\n"
        research_ledger.update_status(run.run_id, "background_running")

        # Launch background tasks concurrently
        peer_fut = asyncio.create_task(run_peer_sector_worker(symbol))
        primary_fut = asyncio.create_task(run_primary_source_worker(symbol, event_types=intent.event_types))

        bg_results = await asyncio.gather(peer_fut, primary_fut, return_exceptions=True)

        peer_ev, peer_details, peer_task = bg_results[0] if not isinstance(bg_results[0], BaseException) else (None, None, None)
        pri_evs, pri_details, pri_task = bg_results[1] if not isinstance(bg_results[1], BaseException) else ([], None, None)

        if peer_task:
            research_ledger.record_task(run.run_id, peer_task)
        if peer_ev:
            evidence_collected.append(peer_ev)
            research_ledger.record_evidence(run.run_id, peer_ev)

        if pri_task:
            research_ledger.record_task(run.run_id, pri_task)
        for pev in (pri_evs or []):
            evidence_collected.append(pev)
            research_ledger.record_evidence(run.run_id, pev)

        # Run Skeptic Review
        skep_ev, skep_details, skep_task = await run_skeptic_worker(
            intent, evidence_collected, peer_data=peer_details
        )
        if skep_task:
            research_ledger.record_task(run.run_id, skep_task)
        if skep_ev:
            evidence_collected.append(skep_ev)
            research_ledger.record_evidence(run.run_id, skep_ev)

        # 5. Final Answer Synthesis (v2)
        delta_note = "Added peer sector comparison, primary filings, and skeptic audit."
        ans_v2 = await synthesize_research_answer(
            intent, evidence_collected, version=2, delta_notes=delta_note
        )
        research_ledger.record_answer_version(run.run_id, ans_v2)

        # Promote provisional widget to final on canvas if placed
        if widget_id and provisional_placed:
            def _promote_final(soup):
                target = soup.find(id=widget_id)
                if target is not None:
                    # Strip data-provisional attribute to settle card
                    if target.has_attr("data-provisional"):
                        del target["data-provisional"]

            evt_final = await commit_canvas(session_id, _promote_final)
            if evt_final:
                yield evt_final

        yield f"data: {json.dumps({'type': 'answer.final', 'run_id': run.run_id, 'version': 2, 'text': ans_v2.text, 'evidence_ids': ans_v2.evidence_ids, 'delta_summary': ans_v2.delta_summary})}\n\n"

        # Stream update delta to bubble
        delta_banner = f"\n\n---\n**Updated Explanation** ({time.strftime('%I:%M %p')})\n{ans_v2.text}"
        yield f"data: {json.dumps({'type': 'chunk', 'content': delta_banner})}\n\n"

    total_latency_ms = int((time.time() - t0) * 1000)
    research_ledger.update_status(run.run_id, "completed", {"latency_ms": total_latency_ms})

    yield f"data: {json.dumps({'type': 'research.completed', 'run_id': run.run_id, 'status': 'complete', 'latency_ms': total_latency_ms})}\n\n"
    yield f"data: {json.dumps({'type': 'status', 'message': f'Research complete ({len(evidence_collected)} sources verified).'})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
