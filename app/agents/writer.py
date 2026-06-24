from typing import Dict, Any, List, Optional
from app.agents.llm_client import call_llm, extract_json_block

WRITER_SYSTEM_PROMPT = """You are a Note Writer Agent for a local-first AI knowledge journal.
Your task is to write or update note content, outputting ONLY valid structured JSON.
You must adhere to the following HTML contract:
- Only output HTML fragments containing the following approved tags:
  Structural: `article`, `section`, `header`, `p`, `ul`, `ol`, `li`, `blockquote`, `hr`
  Inline: `strong`, `em`, `code`, `pre`, `a`, `mark`, `small`
  Metadata: `time`, `data`, `span`
- App-specific attributes allowed:
  `data-note-id` (references internal note id, e.g. "note_123")
  `data-tag` (categorization)
  `data-source` (message id or voice/text source indicator)
  `data-timestamp` (ISO datetime)
  `href` (ONLY for `a` tags, must start with "#note_")
- STRICTLY FORBIDDEN: `script`, `style`, `iframe`, inline event handlers (e.g., onclick), arbitrary classes, arbitrary IDs, and custom CSS styling.
- Do not output a full HTML page. Output only a fragment starting with `<article>` or `<section>`.

For hyperlinks, ALWAYS use internal links like `<a href="#note_123" data-note-id="note_123">Link Text</a>` instead of raw URLs when referring to other journal entries.

Output format must be a single JSON object with this schema:
{
  "title": "Title of the note",
  "summary": "Brief 1-2 sentence description of the content",
  "html_fragment": "<article>...</article>",
  "links_to_add": ["note_id_1", "note_id_2"],
  "tags": ["tag1", "tag2"],
  "canonical_blocks": [
    {"type": "paragraph", "text": "text content"},
    {"type": "list", "items": ["item 1", "item 2"]}
  ],
  "confidence": float (between 0.0 and 1.0)
}
Ensure the `canonical_blocks` reflect the semantic content of the `html_fragment`.
Never delete content outright; suggest archival or replacements.
Do not output any explanation outside the JSON object.
"""

async def write_note(
    user_request: str,
    intent: str,
    existing_note: Optional[Dict[str, Any]] = None,
    related_context: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generates or updates a note content using the Note Writer Agent.
    """
    messages = [
        {"role": "system", "content": WRITER_SYSTEM_PROMPT}
    ]
    
    prompt = f"User Request: {user_request}\nIntent: {intent}\n"
    if existing_note:
        prompt += f"Existing Note ID: {existing_note['id']}\n"
        prompt += f"Existing Note Title: {existing_note['title']}\n"
        prompt += f"Existing Note Version: {existing_note['version']}\n"
        prompt += f"Existing Note Tags: {existing_note['tags']}\n"
        prompt += f"Existing Note Content: {existing_note['rendered_html']}\n"
        
    if related_context:
        prompt += f"Related Context / Search Results:\n{related_context}\n"
        
    messages.append({"role": "user", "content": prompt})
    
    response = await call_llm(
        messages=messages,
        temperature=0.2,
        max_tokens=2048,
        response_format={"type": "json_object"}
    )
    
    return extract_json_block(response)
