"""Minimal standalone LLM client for the benchmark — mirrors main.fast_llm_json
but with no FastAPI dependency, plus a chat() that returns raw text and tracks
token usage so the bake-off can report cost, not just quality."""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import httpx

VLLM_URL = os.getenv("VLLM_URL", "http://10.0.0.141:8000")

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
