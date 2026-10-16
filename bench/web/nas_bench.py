#!/usr/bin/env python3
"""Standalone stdlib-only retrieval bench against a SearXNG instance.

Grader logic copied from bench/web/metrics.py (must_cover term-overlap,
precision@5 / ndcg@5 / recall@5). Base URL via SEARXNG_BENCH_URL
(default http://127.0.0.1:8888). Prints one aggregate JSON scorecard.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

K = 5
BASE_URL = os.environ.get("SEARXNG_BENCH_URL", "http://127.0.0.1:8888")
SLEEP_S = float(os.environ.get("SEARXNG_BENCH_SLEEP", "0"))
CORPUS = Path(__file__).resolve().parent / "corpus.jsonl"

ENGINES = os.environ.get("SEARXNG_BENCH_ENGINES", "").strip()
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _grade(title: str, snippet: str, must_cover: list[str]) -> int:
    """0-3 graded relevance: 3 = >=2 must-cover terms, 2 = 1,
    1 = query tokens overlap but no must-cover, 0 = nothing."""
    doc = _tokens(title) | _tokens(snippet)
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
    return dcg(grades) / dcg(sorted(grades, reverse=True))


def precision_at_k(grades: list[int], k: int) -> float:
    if not grades[:k]:
        return 0.0
    return sum(1 for g in grades[:k] if g >= 2) / min(k, len(grades))


def recall_at_k(grades: list[int], k: int) -> float:
    top = grades[:k]
    if not top:
        return 0.0
    if 3 in top:
        return 1.0
    return 1.0 if top.count(2) >= 2 else (0.5 if 2 in top else 0.0)


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.removeprefix("www.")
    except Exception:
        return ""


def run_query(query: str) -> tuple[str, list[dict], float]:
    """Returns (status, results, latency_ms)."""
    params = {"q": query, "format": "json"}
    if ENGINES:
        params["engines"] = ENGINES
    url = BASE_URL.rstrip("/") + "/search?" + urllib.parse.urlencode(params)
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nas-bench/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return f"error:{type(exc).__name__}", [], (time.monotonic() - t0) * 1000.0
    latency = (time.monotonic() - t0) * 1000.0
    results = data.get("results") or []
    if not results:
        return "soft_empty", [], latency
    return "ok", results, latency


def evaluate(query_id: str, status: str, results: list[dict],
             latency_ms: float, must_cover: list[str]) -> dict:
    grades = [_grade(r.get("title", ""), r.get("content", ""), must_cover)
              for r in results]
    domains = {_domain(r.get("url", "")) for r in results}
    clusters, seen = 0, []
    for r in results:
        key = (_domain(r.get("url", "")),
               " ".join(sorted(_tokens(r.get("title", "")))[:8]))
        if key in seen:
            clusters += 1
        seen.append(key)
    return {
        "query_id": query_id,
        "status": status,
        "latency_ms": round(latency_ms, 1),
        "n_results": len(results),
        f"precision@{K}": round(precision_at_k(grades, K), 3),
        f"ndcg@{K}": round(ndcg_at_k(grades, K), 3),
        f"recall@{K}": round(recall_at_k(grades, K), 3),
        "dup_cluster_rate": round(clusters / len(results), 3) if results else 0.0,
        "unique_source_rate": round(len(domains) / len(results), 3) if results else 0.0,
    }


def _pct(xs: list[float], p: int) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p / 100))], 1)


def aggregate(rows: list[dict]) -> dict:
    ok_rows = [r for r in rows if r["status"] == "ok"]
    out = {
        "n_queries": len(rows),
        "success_rate": round(len(ok_rows) / len(rows), 3) if rows else 0.0,
        "soft_empty_rate": round(
            sum(1 for r in rows if r["status"] == "soft_empty") / len(rows), 3)
        if rows else 0.0,
        "error_classes": {},
        f"mean_precision@{K}": round(sum(r[f"precision@{K}"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        f"mean_ndcg@{K}": round(sum(r[f"ndcg@{K}"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        f"mean_recall@{K}": round(sum(r[f"recall@{K}"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        "mean_dup_cluster_rate": round(sum(r["dup_cluster_rate"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        "mean_unique_source_rate": round(sum(r["unique_source_rate"] for r in ok_rows) / len(ok_rows), 3) if ok_rows else 0.0,
        "p50_latency_ms": _pct([r["latency_ms"] for r in ok_rows], 50),
        "p95_latency_ms": _pct([r["latency_ms"] for r in ok_rows], 95),
    }
    for r in rows:
        if r["status"] != "ok":
            out["error_classes"][r["status"]] = out["error_classes"].get(r["status"], 0) + 1
    return out


def main() -> None:
    rows = []
    with CORPUS.open() as fh:
        entries = [json.loads(line) for line in fh if line.strip()]
    for i, entry in enumerate(entries):
        status, results, latency = run_query(entry["query"])
        rows.append(evaluate(entry["query_id"], status, results,
                             latency, entry.get("must_cover", [])))
        print(f"[{i + 1}/{len(entries)}] {entry['query_id']}: {status} "
              f"n={len(results)} {latency:.0f}ms", flush=True)
        if i + 1 < len(entries) and SLEEP_S > 0:
            time.sleep(SLEEP_S)
    print(json.dumps({"provider": "searxng", "base_url": BASE_URL, "engines": ENGINES or "(server default)",
                      "aggregate": aggregate(rows), "per_query": rows}, indent=2))


if __name__ == "__main__":
    main()
