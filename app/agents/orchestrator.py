import uuid
import logging
from typing import Dict, Any, List, Optional
from app import database
from app.agents.intake import classify_intent
from app.agents.writer import write_note
from app.agents.linker import propose_links
from app.agents.librarian import organize_note_metadata
from app.agents.auditor import audit_html_fragment

logger = logging.getLogger(__name__)

async def process_user_turn(
    session_id: str,
    user_input: str,
    target_note_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Orchestrates the multi-agent turn processing loop:
    1. Classifies intent (Intake)
    2. Gathers metadata/context (Librarian/DB)
    3. Generates note changes (Writer)
    4. Validates changes against contract (Auditor)
    5. Saves changes (DB)
    6. Identifies potential links (Linker)
    """
    # 1. Save user message to database
    user_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
    database.save_chat_message(
        message_id=user_msg_id,
        session_id=session_id,
        role="user",
        content=user_input
    )
    
    # Get conversation history for intent classification
    history = database.get_session_messages(session_id)
    history_payload = [{"role": h["role"], "content": h["content"]} for h in history[:-1]]
    
    # 2. Classify intent
    classification = await classify_intent(user_input, history_payload)
    intent = classification.get("intent", "CHAT")
    classified_note_id = classification.get("note_id") or target_note_id
    
    # 3. Retrieve note context
    existing_note = None
    if classified_note_id:
        existing_note = database.get_note_by_id(classified_note_id)
        
    # Get all tags for Librarian
    all_notes = database.list_all_notes()
    all_tags = list(set([t for n in all_notes for t in n.get("tags", [])]))
    
    # Let Librarian extract search ideas & suggested tags
    lib_meta = await organize_note_metadata(user_input, all_tags)
    
    # Pull related search context if needed
    search_context = ""
    search_queries = lib_meta.get("search_queries", [])
    if search_queries:
        search_results = database.search_notes(search_queries[0])
        if search_results:
            search_context = "\n".join([
                f"Note ID: {n['id']}, Title: {n['title']}, Tags: {n.get('tags')}"
                for n in search_results[:3]
            ])
            
    # Initialize response fields
    assistant_reply = ""
    proposed_note = None
    audit_res = {"is_valid": True, "errors": []}
    proposed_links = []
    
    # 4. Handle intents
    if intent in ["CREATE", "APPEND", "REVISE"]:
        try:
            # Note Writer proposes changes
            proposal = await write_note(
                user_request=user_input,
                intent=intent,
                existing_note=existing_note,
                related_context=search_context
            )
            
            # Auditor validates the generated HTML fragment
            html_fragment = proposal.get("html_fragment", "")
            audit_res = audit_html_fragment(html_fragment)
            
            if audit_res["is_valid"]:
                # Commit note to database
                if existing_note:
                    # Update note
                    proposed_note = database.update_note(
                        note_id=existing_note["id"],
                        title=proposal.get("title"),
                        tags=proposal.get("tags"),
                        links=proposal.get("links_to_add"),
                        canonical_blocks=proposal.get("canonical_blocks"),
                        rendered_html=html_fragment,
                        source_message=user_msg_id
                    )
                    assistant_reply = f"<div class='note-update'><h3>Updated Note: {proposed_note['title']}</h3>{html_fragment}</div>"
                else:
                    # Create new note
                    new_id = f"note_{uuid.uuid4().hex[:8]}"
                    proposed_note = database.create_note(
                        note_id=new_id,
                        title=proposal.get("title", "Untitled Note"),
                        tags=proposal.get("tags", []),
                        links=proposal.get("links_to_add", []),
                        source_messages=[user_msg_id],
                        canonical_blocks=proposal.get("canonical_blocks", []),
                        rendered_html=html_fragment
                    )
                    assistant_reply = f"<div class='note-create'><h3>Created Note: {proposed_note['title']}</h3>{html_fragment}</div>"
                
                # Propose further linkages using the Linker Agent
                proposed_links = await propose_links(proposed_note, all_notes)
            else:
                # Auditor caught violations
                assistant_reply = "<h3>Security Audit Failed</h3><p>I proposed a note edit, but it failed the security audit checks:</p><ul>"
                assistant_reply += "".join([f"<li>{err}</li>" for err in audit_res["errors"]])
                assistant_reply += "</ul><p>Please revise your request to avoid custom styling, scripts, or external links.</p>"
                
        except Exception as e:
            logger.error(f"Error in note creation/update flow: {e}")
            assistant_reply = f"Failed to process note edit request: {str(e)}"
            audit_res = {"is_valid": False, "errors": [str(e)]}
            
    elif intent == "LINK" and classified_note_id:
        # User wants to link two notes. Let's inspect target link
        try:
            # Simple link extraction or Linker
            # We look for a secondary note mentioned in the input
            target_link_id = None
            for n in all_notes:
                if n["id"] != classified_note_id and (n["id"] in user_input or n["title"].lower() in user_input.lower()):
                    target_link_id = n["id"]
                    break
            
            if target_link_id:
                # Update link arrays
                note_a = database.get_note_by_id(classified_note_id)
                note_b = database.get_note_by_id(target_link_id)
                
                if note_a and note_b:
                    links_a = note_a.get("links", [])
                    if target_link_id not in links_a:
                        links_a.append(target_link_id)
                        database.update_note(note_id=classified_note_id, links=links_a)
                        
                    assistant_reply = f"<p>Linked note <strong>{note_a['title']}</strong> to <strong>{note_b['title']}</strong> successfully.</p>"
                else:
                    assistant_reply = "<p>Could not locate one or both of the target notes for linking.</p>"
            else:
                assistant_reply = "<p>I couldn't identify the secondary note you want to link to. Please mention its ID or title clearly.</p>"
        except Exception as e:
            assistant_reply = f"Failed to link notes: {str(e)}"
            
    elif intent == "SEARCH":
        # Return search results
        query = classification.get("query", user_input)
        results = database.search_notes(query)
        if results:
            assistant_reply = f"<h3>Search Results for '{query}'</h3><ul style='list-style: none; padding: 0;'>"
            for n in results:
                assistant_reply += f"<li style='margin-bottom: 0.5rem;'><a href='#note_{n['id']}'><strong>{n['title']}</strong></a> (ID: {n['id']})</li>"
            assistant_reply += "</ul>"
        else:
            assistant_reply = f"<p>No journal notes found matching <strong>'{query}'</strong>.</p>"
            
    elif intent == "SUMMARIZE":
        # Summarize note index
        if all_notes:
            assistant_reply = f"<h3>Journal Summary ({len(all_notes)} entries)</h3><ul style='list-style: none; padding: 0;'>"
            for n in all_notes[:5]:
                assistant_reply += f"<li style='margin-bottom: 0.5rem;'><strong>{n['title']}</strong><br><small>Tags: {', '.join(n['tags'])}</small></li>"
            assistant_reply += "</ul>"
        else:
            assistant_reply = "<p>You don't have any journal notes saved yet.</p>"
            
    else:
        # CHAT or general conversation
        from lazycat.html_skills import COMPONENT_SKILL_LIBRARY
        
        system_prompt = (
            "You are a UI component generator for an open HTML canvas. "
            "The user will ask you to build or show things. You must respond ONLY with raw HTML. "
            "Do not use markdown code blocks like ```html. Just return the raw HTML string.\n\n"
            "RULES:\n"
            "1. Interactivity: All interactivity MUST use inline <script> tags (e.g., buttons, sorting, filtering, navigation must all work via vanilla JS embedded in the output HTML).\n"
            "2. No Dead Links: NEVER use `onclick=\"return false\"`, it kills button functionality.\n"
            "3. Self-Contained Elements: Every interactive element must be fully self-contained with its JS in the same HTML blob.\n"
            "4. Namespaced IDs: Use unique IDs to avoid conflicts with the outer app.\n\n"
            f"{COMPONENT_SKILL_LIBRARY}"
        )
        
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        messages.extend(history_payload)
        messages.append({"role": "user", "content": user_input})
        
        from app.agents.llm_client import call_llm
        try:
            raw_html = await call_llm(messages, temperature=0.7, max_tokens=4096)
            
            # Apply self-review loop from lazycat SDK
            from lazycat.validators import Validator
            from lazycat.llm import prism_client
            assistant_reply = await Validator.self_review_html(raw_html, prism_client)
            
        except Exception as e:
            assistant_reply = f"Hello! I am ready to help you edit your notes. (vLLM error: {str(e)})"
            
    # 5. Save assistant reply to database
    asst_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
    database.save_chat_message(
        message_id=asst_msg_id,
        session_id=session_id,
        role="assistant",
        content=assistant_reply
    )
    
    return {
        "intent": intent,
        "note": proposed_note,
        "message": assistant_reply,
        "audit": audit_res,
        "proposed_links": proposed_links
    }
