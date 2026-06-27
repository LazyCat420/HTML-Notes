import json
import re
import httpx
from typing import Dict, Any, List, Optional
from app.config import VLLM_URL, VLLM_FAST_URL

async def call_llm(
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    response_format: Optional[Dict[str, Any]] = None,
    tier: int = 1
) -> str:
    """
    Calls the vLLM OpenAI-compatible endpoint.
    Routes to VLLM_FAST_URL for tier 0 tasks (Intake, Librarian, Auditor).
    Routes to VLLM_URL for tier 1+ tasks (Writer).
    """
    url = VLLM_FAST_URL if tier == 0 else VLLM_URL
    
    payload = {
        "model": "meta-llama/Meta-Llama-3-8B-Instruct" if tier == 0 else "google/gemma-2-27b-it", # Placeholder names, typically ignored by local vLLM if single model
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    if response_format:
        payload["response_format"] = response_format

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{url}/v1/chat/completions",
            json=payload
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


def extract_json_block(text: str) -> Dict[str, Any]:
    """
    Extracts a JSON block from a string that might contain markdown fences.
    """
    # Try to find ```json ... ```
    match = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
    else:
        # Fallback to the whole string
        json_str = text.strip()
        
    # Strip any leading/trailing non-json characters if it's slightly malformed
    start = json_str.find('{')
    end = json_str.rfind('}')
    if start != -1 and end != -1:
        json_str = json_str[start:end+1]
        
    return json.loads(json_str)
