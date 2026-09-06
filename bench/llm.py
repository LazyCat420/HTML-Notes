"""Minimal standalone LLM client for the benchmark — mirrors main.fast_llm_json
but with no FastAPI dependency, plus a chat() that returns raw text and tracks
token usage so the bake-off can report cost, not just quality."""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import httpx

# The Jetson, matching the app's own default. This used to point at Gold Spark
# (10.0.0.141), which as of 2026-09-05 serves GLM behind a head-of-line-blocked
# queue: /v1/models answers instantly while chat completions never get prefilled,
# so a bench run against it hangs rather than failing.
VLLM_URL = os.getenv("VLLM_URL", "http://10.0.0.30:8000")

# nemotron35 spends its whole token budget on a reasoning trace and returns
# EMPTY content unless this is set — see app/llm.py NO_THINKING for the measured
# table. A judge that returns nothing scores every strategy 0 equally, which
# looks like a tie rather than like a broken instrument.
NO_THINKING = {"enable_thinking": False, "thinking": False}

_model: dict = {"name": os.getenv("BENCH_MODEL")}
# Cumulative token usage across a run, so run_bench can price each strategy.
usage = {"prompt": 0, "completion": 0, "calls": 0}


async def _resolve_model(client: httpx.AsyncClient) -> str:
    if not _model["name"]:
        resp = await client.get(f"{VLLM_URL}/v1/models")
        _model["name"] = resp.json()["data"][0]["id"]
    return _model["name"]


async def chat(prompt: str, max_tokens: int = 512, temperature: float = 0.3,
               system: Optional[str] = None) -> str:
    """One completion. Returns text ('' on failure). Accumulates token usage."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            model = await _resolve_model(client)
            resp = await client.post(f"{VLLM_URL}/v1/chat/completions", json={
                "model": model, "temperature": temperature,
                "max_tokens": max_tokens, "messages": messages,
                "chat_template_kwargs": dict(NO_THINKING),
            })
            data = resp.json()
            u = data.get("usage", {})
            usage["prompt"] += u.get("prompt_tokens", 0)
            usage["completion"] += u.get("completion_tokens", 0)
            usage["calls"] += 1
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return ""


async def chat_json(prompt: str, max_tokens: int = 512, temperature: float = 0.2,
                    system: Optional[str] = None) -> Optional[dict]:
    """chat() but parse the first {...} as JSON. None on failure."""
    text = await chat(prompt, max_tokens=max_tokens, temperature=temperature, system=system)
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def gateway_up() -> bool:
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{VLLM_URL}/v1/models")
            return r.status_code == 200
    except Exception:
        return False
