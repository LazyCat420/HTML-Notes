import httpx
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from app import database
from app.config import PORT, PRISM_URL, VLLM_URL, LAZY_TOOL_SERVICE_URL
from app.tools_schema import HTML_NOTES_TOOLS
import json
import uuid
import asyncio
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="HTML-Notes Engine",
    description="Local-first AI knowledge journal with constrained HTML rendering"
)

# Request / Response Schemas

class MessageRequest(BaseModel):
    session_id: str
    message: str
    target_note_id: Optional[str] = None

class CreateNoteRequest(BaseModel):
    title: str
    tags: List[str] = []
    links: List[str] = []
    canonical_blocks: List[Dict[str, Any]] = []
    rendered_html: str

class UpdateNoteRequest(BaseModel):
    note_id: str
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    links: Optional[List[str]] = None
    canonical_blocks: Optional[List[Dict[str, Any]]] = None
    rendered_html: Optional[str] = None

class LinkNotesRequest(BaseModel):
    source_note_id: str
    target_note_id: str

class TranscribeRequest(BaseModel):
    audio: str # Base64 audio payload

# API Endpoints

@app.post("/session/message")
async def send_message(req: MessageRequest):
    try:
        # Save user message
        user_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
        database.save_chat_message(
            message_id=user_msg_id,
            session_id=req.session_id,
            role="user",
            content=req.message
        )

        history = database.get_session_messages(req.session_id)

        # Build initial messages list
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a UI component renderer for an open HTML canvas. "
                    "When you have data to display, you MUST call render_component() — "
                    "never output raw JSON or plain text. "
                    "When the user asks to save or note something, call html_notes_create_note(). "
                    "Use dark theme inline styles: bg #0d1117, accent #00ff88, text #e6edf3. "
                    "After all tool calls complete, respond with a short plain HTML confirmation string only."
                )
            }
        ]
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})

        async def loop_and_stream():
            MAX_ITERATIONS = 10
            current_messages = list(messages)
            final_html = ""
            all_rendered_components_html = ""

            for iteration in range(MAX_ITERATIONS):
                # Status event to frontend
                yield f'data: {json.dumps({"type": "status", "message": f"thinking (turn {iteration + 1})"})}\n\n'

                # Call Prism — NON-STREAMING for loop turns
                payload = {
                    "provider": "vllm-2",
                    "model": "cyankiwi/MiniMax-M2.7-AWQ-4bit",
                    "messages": current_messages,
                    "stream": False,                    # <-- non-streaming during loop
                    "maxTokens": 4096,
                    "tools": HTML_NOTES_TOOLS,
                    "project": "html-notes-client",
                    "username": "lazycat",
                    "webSearch": True,
                    "webFetch": True
                }

                try:
                    async with httpx.AsyncClient(timeout=600.0) as client:
                        resp = await client.post(f"{PRISM_URL}/agent", json=payload)
                        if resp.status_code != 200:
                            yield f'data: {json.dumps({"type": "error", "message": f"Prism error: {resp.status_code}"})}\n\n'
                            return
                        response_data = resp.json()
                except Exception as e:
                    logger.error(f"Prism network error: {e}")
                    yield f'data: {json.dumps({"type": "error", "message": f"Prism network error: {str(e)}"})}\n\n'
                    return

                finish_reason = response_data.get("finish_reason") or response_data.get("finishReason", "stop")
                tool_calls = response_data.get("tool_calls") or response_data.get("toolCalls") or []
                assistant_content = response_data.get("content") or response_data.get("message") or ""

                # Always append assistant turn verbatim
                current_messages.append({
                    "role": "assistant",
                    "content": assistant_content,
                    "tool_calls": tool_calls
                })

                # No tool calls → final answer
                if not tool_calls or finish_reason == "stop":
                    final_html = assistant_content
                    break

                # Notify frontend which tools are firing
                for tc in tool_calls:
                    fn = tc.get("function", {}).get("name", "unknown")
                    yield f'data: {json.dumps({"type": "tool_call", "tool": fn})}\n\n'

                # Execute all tool calls in PARALLEL
                async def call_one_tool(tc):
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"].get("arguments", "{}"))
                    except Exception:
                        fn_args = {}

                    async with httpx.AsyncClient(timeout=30.0) as c:
                        r = await c.post(
                            f"{LAZY_TOOL_SERVICE_URL}/execute/{fn_name}",
                            json=fn_args,
                            headers={"x-agent": "html-notes", "x-conversation-id": req.session_id}
                        )
                        if r.status_code == 200:
                            return {"tool_call_id": tc["id"], "result": r.json(), "is_error": False}
                        else:
                            return {"tool_call_id": tc["id"], "result": {"error": r.text}, "is_error": True}

                results = await asyncio.gather(*[call_one_tool(tc) for tc in tool_calls])

                # Collect render_component results to forward to frontend
                render_results = []

                # Append tool results as single user message
                tool_result_messages = []
                for res in results:
                    content = json.dumps(res["result"])
                    tool_result_messages.append({
                        "role": "tool",
                        "tool_call_id": res["tool_call_id"],
                        "content": content,
                        "is_error": res["is_error"]
                    })
                    # If this was a render_component call, capture the HTML
                    result_data = res["result"]
                    if isinstance(result_data, dict) and result_data.get("rendered_html"):
                        render_results.append(result_data["rendered_html"])

                current_messages.append({"role": "user", "content": tool_result_messages})

                # Stream any rendered components immediately to frontend
                for html_chunk in render_results:
                    all_rendered_components_html += f'<div class="canvas-element rendered-component">{html_chunk}</div>'
                    yield f'data: {json.dumps({"type": "component", "content": html_chunk})}\n\n'

            # Stream final assistant reply
            if final_html:
                yield f'data: {json.dumps({"type": "chunk", "content": final_html})}\n\n'

            # Save to DB
            asst_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
            
            saved_content = all_rendered_components_html + (final_html or "")
            if not saved_content:
                saved_content = "[tool-only turn]"
                
            database.save_chat_message(
                message_id=asst_msg_id,
                session_id=req.session_id,
                role="assistant",
                content=saved_content
            )

            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(loop_and_stream(), media_type="text/event-stream")

    except Exception as e:
        logger.error(f"Error processing session message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/notes/create")
async def api_create_note(req: CreateNoteRequest):
    import uuid
    from app.agents.auditor import audit_html_fragment
    
    # Audit before manual creation
    audit_res = audit_html_fragment(req.rendered_html)
    if not audit_res["is_valid"]:
        raise HTTPException(
            status_code=400,
            detail=f"HTML content failed security audit: {', '.join(audit_res['errors'])}"
        )
        
    try:
        note_id = f"note_{uuid.uuid4().hex[:8]}"
        note = database.create_note(
            note_id=note_id,
            title=req.title,
            tags=req.tags,
            links=req.links,
            source_messages=["api-manual-create"],
            canonical_blocks=req.canonical_blocks,
            rendered_html=req.rendered_html
        )
        return note
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/notes/update")
async def api_update_note(req: UpdateNoteRequest):
    if req.rendered_html is not None:
        from app.agents.auditor import audit_html_fragment
        audit_res = audit_html_fragment(req.rendered_html)
        if not audit_res["is_valid"]:
            raise HTTPException(
                status_code=400,
                detail=f"HTML content failed security audit: {', '.join(audit_res['errors'])}"
            )
            
    try:
        note = database.update_note(
            note_id=req.note_id,
            title=req.title,
            tags=req.tags,
            links=req.links,
            canonical_blocks=req.canonical_blocks,
            rendered_html=req.rendered_html,
            source_message="api-manual-update"
        )
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return note
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/notes/link")
async def api_link_notes(req: LinkNotesRequest):
    try:
        note_a = database.get_note_by_id(req.source_note_id)
        note_b = database.get_note_by_id(req.target_note_id)
        if not note_a or not note_b:
            raise HTTPException(status_code=404, detail="One or both notes not found")
            
        links = note_a.get("links", [])
        if req.target_note_id not in links:
            links.append(req.target_note_id)
            database.update_note(note_id=req.source_note_id, links=links)
            
        return {"status": "success", "detail": f"Linked {req.source_note_id} to {req.target_note_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/notes/{id}")
async def get_note(id: str):
    note = database.get_note_by_id(id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    history = database.get_note_history(id)
    return {"note": note, "history": history}

@app.get("/search")
async def api_search(q: str):
    return database.search_notes(q)

@app.get("/graph")
async def get_graph():
    """
    Returns nodes and edges formatted for visual graph rendering.
    """
    try:
        notes = database.list_all_notes()
        nodes = []
        edges = []
        
        for n in notes:
            nodes.append({
                "data": {
                    "id": n["id"],
                    "label": n["title"],
                    "version": n["version"]
                }
            })
            for link_target in n.get("links", []):
                edges.append({
                    "data": {
                        "id": f"edge_{n['id']}_{link_target}",
                        "source": n["id"],
                        "target": link_target
                    }
                })
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/session/transcribe")
async def transcribe_audio(req: TranscribeRequest):
    """
    Proxies base64 audio transcription to Prism service STT endpoint.
    """
    try:
        url = f"{PRISM_URL}/audio-to-text"
        payload = {
            "provider": "openai",
            "audio": req.audio,
            "skipConversation": True,
            "project": "html-notes",
            "username": "lazycat"
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code != 200:
                logger.error(f"Prism STT failed with code {res.status_code}: {res.text}")
                raise HTTPException(status_code=500, detail=f"Prism transcription failed: {res.text}")
            return res.json()
    except Exception as e:
        logger.error(f"Failed proxying to STT service: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription service unavailable: {str(e)}")

@app.get("/health/model")
async def health_model():
    """
    Pings local vLLM health metrics endpoint.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{VLLM_URL}/health")
            if res.status_code == 200:
                return {"status": "ok", "vllm": "healthy"}
            return {"status": "unhealthy", "code": res.status_code}
    except Exception as e:
        return {"status": "offline", "detail": str(e)}

@app.get("/health/app")
async def health_app():
    return {"status": "ok", "service": "html-notes"}

class InternalToolRequest(BaseModel):
    tool: str
    args: Dict[str, Any] = {}

@app.post("/internal/execute")
async def internal_tool_execute(req: InternalToolRequest):
    """
    Internal tool dispatcher. Called by lazy-tool-service when the model
    fires an html_notes_* or render_component tool call.
    """
    t = req.tool
    a = req.args

    try:
        if t == "html_notes_create_note":
            from app.agents.auditor import audit_html_fragment
            audit = audit_html_fragment(a.get("rendered_html", ""))
            if not audit["is_valid"]:
                return {"error": f"HTML audit failed: {audit['errors']}", "is_error": True}
            note_id = f"note_{uuid.uuid4().hex[:8]}"
            note = database.create_note(
                note_id=note_id,
                title=a["title"],
                tags=a.get("tags", []),
                links=a.get("links", []),
                source_messages=["tool-call"],
                canonical_blocks=[],
                rendered_html=a["rendered_html"]
            )
            return {"success": True, "note_id": note["id"], "title": note["title"]}

        elif t == "html_notes_update_note":
            from app.agents.auditor import audit_html_fragment
            if "rendered_html" in a:
                audit = audit_html_fragment(a["rendered_html"])
                if not audit["is_valid"]:
                    return {"error": f"HTML audit failed: {audit['errors']}", "is_error": True}
            note = database.update_note(note_id=a["note_id"], **{k: v for k, v in a.items() if k != "note_id"})
            return {"success": True, "note_id": a["note_id"]} if note else {"error": "Note not found", "is_error": True}

        elif t == "html_notes_get_note":
            note = database.get_note_by_id(a["note_id"])
            return note if note else {"error": "Note not found", "is_error": True}

        elif t == "html_notes_search_notes":
            results = database.search_notes(a["query"])
            return {"results": results, "count": len(results)}

        elif t == "html_notes_link_notes":
            note_a = database.get_note_by_id(a["source_note_id"])
            if not note_a:
                return {"error": "Source note not found", "is_error": True}
            links = note_a.get("links", [])
            if a["target_note_id"] not in links:
                links.append(a["target_note_id"])
                database.update_note(note_id=a["source_note_id"], links=links)
            return {"success": True}

        elif t == "html_notes_modify_dom":
            # fetch note, apply BeautifulSoup DOM operation, update
            note = database.get_note_by_id(a["note_id"])
            if not note:
                return {"error": "Note not found", "is_error": True}
            soup = BeautifulSoup(note["rendered_html"], "html.parser")
            target = soup.select_one(a["css_selector"])
            if not target:
                return {"error": f"Selector '{a['css_selector']}' not found", "is_error": True}
            snippet_soup = BeautifulSoup(a["html_snippet"], "html.parser")
            action = a["action"]
            if action == "append":      target.append(snippet_soup)
            elif action == "prepend":   target.insert(0, snippet_soup)
            elif action == "insert_before": target.insert_before(snippet_soup)
            elif action == "insert_after":  target.insert_after(snippet_soup)
            elif action == "replace":   target.replace_with(snippet_soup)
            database.update_note(note_id=a["note_id"], rendered_html=str(soup))
            return {"success": True}

        elif t == "render_component":
            # No DB write needed — just echo back so the loop can forward to frontend
            return {
                "success": True,
                "rendered_html": a["rendered_html"],
                "component_type": a.get("component_type"),
                "title": a.get("title")
            }

        else:
            return {"error": f"Unknown tool: {t}", "is_error": True}

    except Exception as e:
        logger.error(f"Internal tool execution error: {e}")
        return {"error": str(e), "is_error": True}

@app.get("/session/{session_id}/history")
async def get_session_history(session_id: str):
    try:
        history = database.get_session_messages(session_id)
        return {"messages": history}
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Mount UI static files at root
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
def read_root():
    # Redirect base URL to static client UI
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")
