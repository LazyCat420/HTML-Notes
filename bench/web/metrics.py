"""Retrieval metrics for bench/web.

Judgments are heuristic (term overlap) for the smoke scorecard; the corpus
carries `must_cover` terms per query acting as the human reference sheet.
Replace grader with human labels once rubric agreement is measured — the
metric functions take graded relevance lists, so they don't change.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .schema import ProviderResponse, SOFT_EMPTY

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _grade(result_title: str, result_snippet: str, must_cover: list[str]) -> int:
    """0-3 graded relevance: 3 = covers >=2 must-cover terms, 2 = 1,
    1 = query tokens overlap but no must-cover, 0 = nothing."""
    doc = _tokens(result_title) | _tokens(result_snippet)
    hits = sum(1 for term in must_cover if _tokens(term) & doc)
    if hits >= 2:
        return 3
    if hits == 1:
        return 2
    return 1 if doc else 0


def dcg(grades: list[int]) -> float:
    return sum((2 ** g - 1) / (i + 2) for i, g in enumerate(grades))


def ndcg_at_k(grades: list[int], k: int) -> float:
    grades = grades[:k]
    if not grades or max(grades) == 0:
        return 0.0
    ideal = sorted(grades, reverse=True)
    return dcg(grades) / dcg(ideal)


def precision_at_k(grades: list[int], k: int) -> float:
    if not grades[:k]:
        return 0.0
    return sum(1 for g in grades[:k] if g >= 2) / min(k, len(grades))


def recall_at_k(grades: list[int], k: int) -> float:
    """Fraction of must-cover terms (>=2 hits) reachable in top-k judged docs.
    Approximated by: did any top-k doc grade 3, or two distinct grade-2 docs."""
    top = grades[:k]
    if not top:
        return 0.0
    if 3 in top:
        return 1.0
    return 1.0 if top.count(2) >= 2 else (0.5 if 2 in top else 0.0)


def dup_cluster_rate(results) -> float:
    """Share of slots consumed by same-domain + near-identical-title items."""
    if len(results) < 2:
        return 0.0
    clusters, seen = 0, []
    for r in results:
        key = (r.display_domain, " ".join(sorted(_tokens(r.title))[:8]))
        if key in seen:
            clusters += 1
        seen.append(key)
    return clusters / len(results)


def unique_source_rate(results) -> float:
    if not results:
        return 0.0
    domains = {r.display_domain or urlparse(r.url).netloc for r in results}
    return len(domains) / len(results)


def evaluate(resp: ProviderResponse, must_cover: list[str], k: int = 5) -> dict:
    grades = [_grade(r.title, r.snippet, must_cover) for r in resp.results]
    return {
        "query_id": resp.query_id,
        "provider": resp.provider,
        "status": resp.status,
        "latency_ms": round(resp.latency_ms, 1),
        "n_results": len(resp.results),
        f"precision@{k}": round(precision_at_k(grades, k), 3),
        f"ndcg@{k}": round(ndcg_at_k(grades, k), 3),
        f"recall@{k}": round(recall_at_k(grades, k), 3),
        "dup_cluster_rate": round(dup_cluster_rate(resp.results), 3),
        "unique_source_rate": round(unique_source_rate(resp.results), 3),
        "dated_results": sum(1 for r in resp.results if r.published_at),
    }


def aggregate(rows: list[dict], k: int = 5) -> dict:
    ok_rows = [r for r in rows if r["status"] == "ok"]
    out = {
        "n_queries": len(rows),
        "success_rate": round(len(ok_rows) / len(rows), 3) if rows else 0.0,
        "soft_empty_rate": round(
            sum(1 for r in rows if r["status"] == SOFT_EMPTY) / len(rows), 3) if rows else 0.0,
        "error_classes": {},
        f"mean_precision@{k}": round(sum(r[f"precision@{k}"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        f"mean_ndcg@{k}": round(sum(r[f"ndcg@{k}"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        f"mean_recall@{k}": round(sum(r[f"recall@{k}"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        "mean_dup_cluster_rate": round(sum(r["dup_cluster_rate"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        "mean_unique_source_rate": round(sum(r["unique_source_rate"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        "p50_latency_ms": _pct([r["latency_ms"] for r in ok_rows], 50),
        "p95_latency_ms": _pct([r["latency_ms"] for r in ok_rows], 95),
        "dated_result_share": round(
            sum(r["dated_results"] for r in ok_rows) / max(1, sum(r["n_results"] for r in ok_rows)), 3),
    }
    for r in rows:
        if r["status"] != "ok":
            out["error_classes"][r["status"]] = out["error_classes"].get(r["status"], 0) + 1
    return out


def _pct(xs: list[float], p: int) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p / 100))], 1)
