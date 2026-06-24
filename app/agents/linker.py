from typing import List, Dict, Any
from app.agents.llm_client import call_llm, extract_json_block

LINKER_SYSTEM_PROMPT = """You are a Linker Agent for an AI knowledge journal.
Your task is to identify relationships between the current note and a list of existing notes.
You will propose hyperlinks or backlinks that should connect them.

Given:
1. The details of the active/current note.
2. A list of existing note titles and IDs in the system.

Determine which existing notes should be linked to/from the current note.
Return a list of proposed note IDs to link, along with brief justifications.

Output ONLY a valid JSON object matching this schema:
{
  "proposed_links": [
    {
      "note_id": "existing_note_id",
      "title": "Existing Note Title",
      "direction": "outbound" | "inbound" | "bidirectional",
      "reason": "Why these notes are related"
    }
  ]
}
Do not include any extra text or explanation outside the JSON.
"""

async def propose_links(
    current_note: Dict[str, Any],
    all_existing_notes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Proposes links between the current note and existing database notes.
    """
    if not all_existing_notes:
        return []
        
    messages = [
        {"role": "system", "content": LINKER_SYSTEM_PROMPT}
    ]
    
    context = f"Current Note:\n"
    context += f"ID: {current_note.get('id')}\n"
    context += f"Title: {current_note.get('title')}\n"
    context += f"Summary: {current_note.get('summary', '')}\n"
    context += f"HTML Preview: {current_note.get('rendered_html')}\n\n"
    
    context += "Existing Notes in System:\n"
    for note in all_existing_notes:
        # Avoid linking to itself
        if note["id"] == current_note.get("id"):
            continue
        context += f"- ID: {note['id']}, Title: {note['title']}, Tags: {note.get('tags', [])}\n"
        
    messages.append({"role": "user", "content": context})
    
    try:
        response = await call_llm(
            messages=messages,
            temperature=0.1,
            max_tokens=512,
            response_format={"type": "json_object"}
        )
        data = extract_json_block(response)
        return data.get("proposed_links", [])
    except Exception:
        # Fallback to no proposed links on error
        return []
