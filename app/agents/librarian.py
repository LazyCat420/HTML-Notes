from typing import List, Dict, Any, Optional
from app.agents.llm_client import call_llm, extract_json_block

LIBRARIAN_SYSTEM_PROMPT = """You are a Librarian Agent for an AI knowledge journal.
Your task is to select appropriate tags, sections, or search strategies for note retrieval.

You will be given:
1. The user's input/query.
2. A list of existing tags currently used in the system.

Determine:
1. The best search terms/queries to locate relevant notes.
2. A list of tags to categorize the current request.

Output ONLY a valid JSON object matching this schema:
{
  "search_queries": [string],
  "suggested_tags": [string],
  "recommended_sections": [string]
}
Do not include any extra text outside the JSON.
"""

async def organize_note_metadata(
    user_input: str,
    existing_tags: List[str]
) -> Dict[str, Any]:
    """
    Helps organize, categorize, and extract search keywords from the user input.
    """
    messages = [
        {"role": "system", "content": LIBRARIAN_SYSTEM_PROMPT}
    ]
    
    context = f"User Input: {user_input}\n"
    context += f"Existing System Tags: {', '.join(existing_tags) if existing_tags else 'None'}\n"
    
    messages.append({"role": "user", "content": context})
    
    try:
        response = await call_llm(
            messages=messages,
            temperature=0.1,
            max_tokens=256,
            response_format={"type": "json_object"}
        )
        return extract_json_block(response)
    except Exception:
        return {
            "search_queries": [user_input],
            "suggested_tags": [],
            "recommended_sections": []
        }
