from typing import Dict, Any, List
from app.agents.llm_client import call_llm, extract_json_block

INTENT_SYSTEM_PROMPT = """You are an Intake Agent for a local-first AI knowledge journal.
Your task is to classify the user's voice/text query into one of the following intents:
1. CREATE: The user wants to start a new note (e.g. "Create a note about project ideas", "Start a journal entry about my day").
2. APPEND: The user wants to add details or text to an existing note (e.g. "Add x to the project ideas note").
3. REVISE: The user wants to rewrite, correct, or edit the content of an existing note (e.g. "Change the title of note_123", "Rewrite the second section of my journal").
4. LINK: The user wants to connect/link two notes together (e.g. "Link this note to the project note", "Connect note_123 with note_456").
5. SUMMARIZE: The user wants a summary of one or more notes (e.g. "Summarize my entries from last week").
6. SEARCH: The user wants to find notes or search their history (e.g. "Find notes about LLMs", "Search my journal for meeting notes").
7. CHAT: The user is having a general conversation or asking a question not directly invoking note changes (e.g. "What is my plan for today?", "Hi").

If the query references a specific note, identify its note_id if possible (either from context or name, or set to null if unknown/not mentioned).
Also extract the core query/request text.

Output ONLY a valid JSON object with this schema:
{
  "intent": "CREATE" | "APPEND" | "REVISE" | "LINK" | "SUMMARIZE" | "SEARCH" | "CHAT",
  "note_id": string or null,
  "query": string,
  "reasoning": string
}
Do not include any extra chat or markdown formatting outside the JSON block.
"""

async def classify_intent(user_input: str, conversation_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Classifies the user input to determine the core intent.
    """
    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT}
    ]
    
    if conversation_history:
        # Include a couple of recent turns for context
        messages.extend(conversation_history[-4:])
        
    messages.append({"role": "user", "content": user_input})
    
    response = await call_llm(
        messages=messages,
        temperature=0.1,
        max_tokens=256,
        response_format={"type": "json_object"}
    )
    
    try:
        return extract_json_block(response)
    except Exception:
        # Fallback if parsing fails
        return {
            "intent": "CHAT",
            "note_id": None,
            "query": user_input,
            "reasoning": "Fallback due to JSON extraction failure"
        }
