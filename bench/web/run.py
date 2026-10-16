"""Bench/web orchestrator: run a query corpus across providers, store artifacts,
publish a per-provider scorecard.

Usage:  python3 -m bench.web.run [--corpus bench/web/corpus.jsonl] [--limit 10]
Artifacts: bench/web/results/<run_id>/{raw/<provider>/<query_id>.json, rows.jsonl, scorecard.json}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .adapters import ADAPTERS, safe_search
from .metrics import evaluate, aggregate

ROOT = Path(__file__).resolve().parents[2]     # html-notes repo root
RESULTS = Path(__file__).resolve().parent / "results"
DEFAULT_PROVIDERS = ["searxng", "ddg_lite", "google_news_rss", "gdelt"]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def load_corpus(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def run(providers: list[str], corpus_path: Path, limit: int, delay_s: float) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / run_id
    (out / "raw").mkdir(parents=True, exist_ok=True)
    queries = load_corpus(corpus_path)
    sha = git_sha()
    all_rows = []

    for provider in providers:
        if provider not in ADAPTERS:
            print(f"unknown provider {provider!r}; skip", file=sys.stderr)
            continue
        pdir = out / "raw" / provider
        pdir.mkdir(exist_ok=True)
        try:
            for q in queries:
                resp = safe_search(provider, q["query"], q["query_id"], limit=limit)
                (pdir / f"{q['query_id']}.json").write_text(
                    json.dumps(resp.to_dict(), indent=1, default=str))
                row = evaluate(resp, q.get("must_cover", []), k=min(5, limit))
                row["category"] = q.get("category", "")
                all_rows.append(row)
                print(f"[{provider}] {q['query_id']}: {row['status']} "
                      f"n={row['n_results']} ndcg={row['ndcg@5']} {row['latency_ms']}ms")
                time.sleep(delay_s)   # be polite to keyless endpoints
        except Exception as e:
            print(f"[{provider}] FATAL {type(e).__name__}: {e}", file=sys.stderr)

    (out / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in all_rows))
    scorecard = {
        "run_id": run_id, "git_sha": sha,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "providers": {p: aggregate([r for r in all_rows if r["provider"] == p]) for p in providers},
    }
    (out / "scorecard.json").write_text(json.dumps(scorecard, indent=1))
    print(json.dumps(scorecard["providers"], indent=1))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(Path(__file__).parent / "corpus.jsonl"))
    ap.add_argument("--providers", default=",".join(DEFAULT_PROVIDERS))
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--delay", type=float, default=1.0)
    a = ap.parse_args()
    run(a.providers.split(","), Path(a.corpus), a.limit, a.delay)
