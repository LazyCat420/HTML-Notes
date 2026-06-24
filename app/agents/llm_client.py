import httpx
import json
import logging
import re
from typing import Dict, Any, List, Optional
from app.config import VLLM_URL

logger = logging.getLogger(__name__)

_cached_model = None

async def _get_model_name(client: httpx.AsyncClient) -> str:
    global _cached_model
    if _cached_model:
        return _cached_model
        
    url = f"{VLLM_URL}/v1/models"
    try:
        response = await client.get(url)
        if response.status_code == 200:
            data = response.json()
            if "data" in data and len(data["data"]) > 0:
                _cached_model = data["data"][0]["id"]
                return _cached_model
    except Exception as e:
        logger.error(f"Failed to fetch models from vLLM: {e}")
        
    return "minimax" # fallback to minimax/gold spark

async def call_llm(
    messages: List[Dict[str, str]],
    temperature: float = 0.1,
    max_tokens: int = 1024,
    response_format: Optional[Dict[str, str]] = None
) -> str:
    """
    Calls the vLLM chat completions API with the given messages.
    Returns the string content of the completion.
    """
    url = f"{VLLM_URL}/v1/chat/completions"
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            model_name = await _get_model_name(client)
            
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            if response_format:
                payload["response_format"] = response_format
                
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                logger.error(f"vLLM API returned status {response.status_code}: {response.text}")
                raise RuntimeError(f"vLLM error: {response.text}")
            
            result = response.json()
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Error calling vLLM at {url}: {e}")
        raise RuntimeError(f"Failed to communicate with vLLM: {str(e)}")

def extract_json_block(text: str) -> Dict[str, Any]:
    """
    Extracts and parses JSON object from a markdown code block or loose text.
    """
    text = text.strip()
    
    # Try looking for markdown json block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        # Fallback to finding first '{' and last '}'
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            json_str = text[start:end+1]
        else:
            json_str = text
            
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from extracted block. Original text:\n{text}")
        raise ValueError(f"Invalid JSON format returned by LLM: {str(e)}")
