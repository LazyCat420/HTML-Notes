import httpx
import logging
import re

import urllib.parse
import httpx

import urllib.parse
import httpx

async def search_youtube_videos(query: str, limit: int = 5, order: str = "relevance") -> list:
    """Search YouTube and return video dicts with video_id/id, title, and channel.

    order="date" sorts results newest-first (for "latest video from <channel>" asks).
    """
    def _unescape(s: str) -> str:
        try:
            return json.loads('"' + s + '"')
        except Exception:
            return s

    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        if order == "date":
            url += "&sp=CAI%253D"
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Accept-Language": "en-US,en;q=0.9"})
            html = resp.text
            results = []
            seen = set()
            for block in html.split('"videoRenderer":')[1:]:
                vid_match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', block)
                title_match = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}\]', block)
                channel_match = re.search(r'"longBylineText":\{"runs":\[\{"text":"(.*?)"', block)
                if not vid_match or not title_match:
                    continue
                vid = vid_match.group(1)
                if vid in seen:
                    continue
                seen.add(vid)
                results.append({
                    "video_id": vid,
                    "id": vid,
                    "title": _unescape(title_match.group(1)),
                    "channel": _unescape(channel_match.group(1)) if channel_match else None,
                })
                if len(results) >= limit:
                    break
            return results
    except Exception as e:
        logger.error(f"search_youtube_videos error: {e}")
    return []

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app import database
from app.config import PORT, PRISM_URL, LAZY_AGENT_URL, VLLM_URL, LAZY_TOOL_SERVICE_URL, TTS_SERVICE_URL, MUSIC_PLAYER_URL
import json
import uuid
from bs4 import BeautifulSoup
from app.widgets.factory import generate_widget_html


logging.basicConfig(level=logging.INFO)

import logging

logger = logging.getLogger(__name__)

# Global cache to keep track of the latest active canvas HTML for DOM queries
latest_canvas_html = ""

def get_canvas_summary(html: str) -> str:
    """Parses raw canvas HTML and extracts widget details into a tiny, token-efficient summary."""
    if not html or html.strip() == "" or html == "Canvas is empty.":
        return "Canvas is currently empty."
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        widgets = []
        for card in soup.select(".glass-card, .widget-container"):
            widget_id = card.get("id", "unknown")
            xdata = card.get("x-data", "")
            
            # Find title
            title_el = card.select_one(".glass-card-title, h3, h2")
            title = title_el.get_text(strip=True) if title_el else "Untitled"
            
            # Identify widget type
            wtype = "custom"
            classes = card.get("class", [])
            for cls in classes:
                if cls in ("checklist", "clock", "notes", "iframe_app", "mini_music_player", "youtube_player"):
                    wtype = cls
                    break
            if wtype == "custom" and xdata:
                if "checklistWidget" in xdata: wtype = "checklist"
                elif "clockWidget" in xdata: wtype = "clock"
                elif "notesWidget" in xdata: wtype = "notes"
                elif "musicPlayerWidget" in xdata: wtype = "mini_music_player"
                elif "youtubePlayerWidget" in xdata: wtype = "youtube_player"
                
            widgets.append(f"- Widget ID: #{widget_id}, Type: {wtype}, Title: '{title}'")
        
        if not widgets:
            return "Canvas contains no recognizable widgets."
        return "\n".join(widgets)
    except Exception as e:
        logger.error(f"Error getting canvas summary: {e}")
        return html[:2000]


app = FastAPI(
    title="HTML-Notes Engine",
    description="Local-first AI knowledge journal with constrained HTML rendering"
)

# Request / Response Schemas

class MessageRequest(BaseModel):
    session_id: str
    message: str
    target_note_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    current_canvas: Optional[str] = None
    use_lazy_agent: bool = True

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

class TTSSynthesizeRequest(BaseModel):
    text: str

# API Endpoints

@app.get("/models")
async def get_models():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{PRISM_URL}/config?includeLocal=true")
            if resp.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to fetch models from Prism")
            
            data = resp.json()
            models_map = data.get("textToText", {}).get("models", {})
            
            flat_models = []
            for provider, provider_models in models_map.items():
                for model in provider_models:
                    flat_models.append({
                        "provider": provider,
                        "model": model.get("name"),
                        "label": model.get("label") or model.get("name")
                    })
            return JSONResponse(content={"models": flat_models})
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        raise HTTPException(status_code=500, detail=str(e))



def extract_youtube_query(text: str) -> str:
    text_lower = text.lower().strip()
    # Strip common trigger prefixes
    pattern = r'^(?:add|show|open|play|create|get)\s+(?:a\s+)?(?:youtube|yt)\s+(?:player\s+|widget\s+|video\s+)*(?:for\s+|with\s+|of\s+)*'
    cleaned = re.sub(pattern, '', text_lower)
    for prefix in ("youtube", "yt", "play"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    quote_match = re.search(r'["\'“]([^"\'”]+)["\'”]', cleaned)
    if quote_match:
        return quote_match.group(1).strip()
    return cleaned.strip()

def is_query_vague(query_text: str) -> bool:
    """
    Checks if a query text contains meaningful content or is just conversational filler / general widget spawn commands.
    """
    if not query_text:
        return True
    
    # Lowercase & strip punctuation
    text = query_text.lower().strip()
    text = re.sub(r'[^\w\s]', '', text)
    
    # Hybrid Catalog of filler words/synonyms
    filler_words = {
        # Action verbs
        "add", "show", "open", "play", "create", "pull", "up", "get", "find", "search", "insert", "inject", "spawn", "display",
        # Articles & prepositions
        "a", "an", "the", "some", "any", "to", "on", "for", "with", "in", "at",
        # Conversational filler
        "please", "now", "here", "thanks", "thank", "you", "would", "like", "want", "need", "can", "could", "me", "us",
        # Widget type descriptors (predefined categories)
        "widget", "player", "video", "youtube", "yt", "channel", "clip", "stream", "online", "notes", "notepad", "scratchpad", 
        "clock", "time", "checklist", "todo", "todolist", "task", "list", "music", "radio", "song", "audio", "lofi", "beats"
    }
    
    words = text.split()
    meaningful_words = [w for w in words if w not in filler_words]
    
    # Returns True if no meaningful words are left
    return len(meaningful_words) == 0

def is_valid_tool_args(tool_name: str, args: dict) -> bool:
    if not args:
        return False
    if tool_name == "mcp__lazy-tool-service__canvas_add_widget":
        return bool(args.get("widget_type"))
    if tool_name == "mcp__lazy-tool-service__canvas_modify_dom":
        return bool(args.get("css_selector") and args.get("action"))
    if tool_name == "mcp__lazy-tool-service__create_widget":
        return bool(args.get("widgetType") and args.get("htmlContent"))
    if tool_name == "mcp__lazy-tool-service__update_widget":
        return bool(args.get("widgetId"))
    return False

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

        
        text_lower = req.message.lower().strip()
        text_clean = text_lower.strip()
        

        # 2. Clock heuristic matching
        is_clock = "clock" in text_clean
        has_timezone = any(tz in text_clean for tz in ("in ", "for ", "time ", "zone", "city", "york", "london", "tokyo", "paris", "sydney", "canada"))
        if is_clock and not has_timezone:
            async def clock_stream():
                yield f'data: {{"type": "status", "message": "heuristic-path: spawning clock widget..."}}\n\n'
                widget_id = f"clock-{uuid.uuid4().hex[:8]}"
                html_snippet = generate_widget_html("clock", widget_id, {})
                
                global latest_canvas_html
                soup = BeautifulSoup(latest_canvas_html or "Canvas is empty.", 'html.parser')
                target = soup.select_one('#dashboard-grid')
                if target:
                    new_elem = BeautifulSoup(html_snippet, 'html.parser')
                    target.append(new_elem)
                    latest_canvas_html = str(soup)
                
                payload_str = json.dumps({"type": "component", "content": html_snippet, "action": "append", "target": "#dashboard-grid"})
                yield f"data: {payload_str}\n\n"
                yield 'data: {"type": "done"}\n\n'
            return StreamingResponse(clock_stream(), media_type="text/event-stream")

        # 3. Checklist heuristic matching
        if any(w in text_clean for w in ("checklist", "todo", "to-do", "task list")):
            async def checklist_stream():
                yield f'data: {{"type": "status", "message": "heuristic-path: spawning checklist widget..."}}\n\n'
                widget_id = f"checklist-{uuid.uuid4().hex[:8]}"
                html_snippet = generate_widget_html("checklist", widget_id, {})
                
                global latest_canvas_html
                soup = BeautifulSoup(latest_canvas_html or "Canvas is empty.", 'html.parser')
                target = soup.select_one('#dashboard-grid')
                if target:
                    new_elem = BeautifulSoup(html_snippet, 'html.parser')
                    target.append(new_elem)
                    latest_canvas_html = str(soup)
                
                payload_str = json.dumps({"type": "component", "content": html_snippet, "action": "append", "target": "#dashboard-grid"})
                yield f"data: {payload_str}\n\n"
                yield 'data: {"type": "done"}\n\n'
            return StreamingResponse(checklist_stream(), media_type="text/event-stream")

        # 4. Music player heuristic matching
        has_custom_url = "http" in text_clean or "www" in text_clean
        if any(w in text_clean for w in ("music", "player", "radio")) and not has_custom_url:
            async def music_stream():
                yield f'data: {{"type": "status", "message": "heuristic-path: spawning music widget..."}}\n\n'
                widget_id = f"music-{uuid.uuid4().hex[:8]}"
                html_snippet = generate_widget_html("mini_music_player", widget_id, {"genre": "lofi"})
                
                global latest_canvas_html
                soup = BeautifulSoup(latest_canvas_html or "Canvas is empty.", 'html.parser')
                target = soup.select_one('#dashboard-grid')
                if target:
                    new_elem = BeautifulSoup(html_snippet, 'html.parser')
                    target.append(new_elem)
                    latest_canvas_html = str(soup)
                
                payload_str = json.dumps({"type": "component", "content": html_snippet, "action": "append", "target": "#dashboard-grid"})
                yield f"data: {payload_str}\n\n"
                yield 'data: {"type": "done"}\n\n'
            return StreamingResponse(music_stream(), media_type="text/event-stream")

        # 5. Notes heuristic matching
        is_searching_notes = "search" in text_clean or "find" in text_clean or "look for" in text_clean
        if any(w in text_clean for w in ("notes", "notepad", "scratchpad")) and not is_searching_notes:
            async def notes_stream():
                yield f'data: {{"type": "status", "message": "heuristic-path: spawning notes widget..."}}\n\n'
                widget_id = f"notes-{uuid.uuid4().hex[:8]}"
                html_snippet = generate_widget_html("notes", widget_id, {})
                
                global latest_canvas_html
                soup = BeautifulSoup(latest_canvas_html or "Canvas is empty.", 'html.parser')
                target = soup.select_one('#dashboard-grid')
                if target:
                    new_elem = BeautifulSoup(html_snippet, 'html.parser')
                    target.append(new_elem)
                    latest_canvas_html = str(soup)
                
                payload_str = json.dumps({"type": "component", "content": html_snippet, "action": "append", "target": "#dashboard-grid"})
                yield f"data: {payload_str}\n\n"
                yield 'data: {"type": "done"}\n\n'
            return StreamingResponse(notes_stream(), media_type="text/event-stream")

        # Start loading history

        history = database.get_session_messages(req.session_id)

        global latest_canvas_html
        if req.current_canvas:
            latest_canvas_html = req.current_canvas
        canvas_summary = get_canvas_summary(req.current_canvas)

        # Build system prompt with canvas context
        SYSTEM_PROMPT = (
            "You are an agentic OS assistant that manages a live dashboard canvas.\n"
            "CRITICAL: You are a strict TOOL-ONLY JSON agent. You MUST NEVER output any conversational text, thinking process, or explanations. You MUST START your response immediately with the tool call.\n"
            "If you output any text that is not a tool call, the system will crash.\n\n"
            f"CURRENT CANVAS STATE:\n```markdown\n{canvas_summary}\n```\n\n"
            "CANVAS TOOLS:\n"
            "- Inspect what's on screen → mcp__lazy-tool-service__canvas_read_dom()\n"
            "- Plan a widget → mcp__lazy-tool-service__plan_widget(widgetType, title, description, proposedLayout)\n"
            "- Create a widget → mcp__lazy-tool-service__create_widget(widgetType, title, htmlContent, cssContent, jsContent, dependencies, renderTarget)\n"
            "- Update a widget → mcp__lazy-tool-service__update_widget(widgetId, title, htmlContent, cssContent, jsContent, dependencies, renderPhase)\n"
            "- Validate HTML → mcp__lazy-tool-service__validate_widget_html(htmlContent, cssContent, jsContent)\n"
            "- List widget types → mcp__lazy-tool-service__list_widget_types()\n"
            "- Modify/remove an existing widget → mcp__lazy-tool-service__canvas_modify_dom(css_selector='#widget-[UUID]', action='replace' or 'remove')\n"
            "- Search notes → mcp__lazy-tool-service__html_notes_search_notes(query)\n"
            "- Update a note → mcp__lazy-tool-service__html_notes_get_note(note_id) then mcp__lazy-tool-service__html_notes_update_note()\n"
            "- Search YouTube for videos → mcp__lazy-tool-service__html_notes_youtube_search(query, limit, order) — pass order='date' for the newest uploads from a channel\n\n"
            "LIVE DATA TOOLS (for custom widgets):\n"
            "- Current weather → get_weather(location) / get_weather_forecast(location)\n"
            "- Time in a city/timezone → get_time_in_timezone(timezone)\n"
            "- Web lookup → search_web(query), read_web_page(url), search_news(query)\n"
            "When the user asks for a custom live-data widget (e.g. 'weather in Tokyo'), FIRST fetch the data with these tools, THEN render it: plan_widget → create_widget with the fetched values baked into the HTML.\n\n"
            "AGENTIC UI GENERATION RULES:\n"
            "1. PLANNING MANDATORY: Before you call `create_widget`, you MUST ALWAYS first call `plan_widget` with a structured design plan detailing your widget types, behavior, layout, and style. Generation is blocked unless planning succeeds.\n"
            "2. DASHBOARD GRID SYSTEM: The canvas is a CSS Grid (#dashboard-grid).\n"
            "3. CREATING AND UPDATING WIDGETS: Use `create_widget` to append widgets and `update_widget` to update them in place. Make sure to define clean HTML, scope all CSS rules, and encapsulate JavaScript scripts securely. Do NOT use inline script events.\n"
            "4. ADDING WIDGETS: You can also use `mcp__lazy-tool-service__canvas_add_widget(widget_type, widget_id, config)` to spawn pre-built Lego widgets (types: 'checklist', 'clock', 'notes', 'iframe_app', 'mini_music_player', 'youtube_player'). Provide a unique `widget_id`.\n"
            "5. YOUTUBE VIDEOS: To add a YouTube video, YOU MUST FIRST use `mcp__lazy-tool-service__html_notes_youtube_search(query)`. Look at the results, extract the video_id, and then use `mcp__lazy-tool-service__canvas_add_widget` with `widget_type='youtube_player'`. DO NOT explain your plan.\n"
            "6. MODIFYING/REMOVING WIDGETS: Target the specific widget's ID and use `mcp__lazy-tool-service__canvas_modify_dom` with `action='replace'` or `action='remove'`.\n\n"
            "CANVAS DOM MODIFICATION RULES:\n"
            "1. Use mcp__lazy-tool-service__canvas_modify_dom to update elements. Target elements accurately by their ID.\n\n"
            "2. VAGUE YOUTUBE REQUESTS: If the user asks generally to 'pull up a video' or 'play a youtube video' without specifying a topic or search term, choose a random search query and execute `html_notes_youtube_search` immediately. Do NOT ask for clarification."
        )

        # Ensure all possible tools are enabled
        enabled_tools = [
            "mcp__lazy-tool-service__html_notes_create_note",
            "mcp__lazy-tool-service__html_notes_update_note",
            "mcp__lazy-tool-service__html_notes_get_note",
            "mcp__lazy-tool-service__html_notes_search_notes",
            "mcp__lazy-tool-service__html_notes_link_notes",
            "mcp__lazy-tool-service__canvas_read_dom",
            "mcp__lazy-tool-service__canvas_add_widget",
            "mcp__lazy-tool-service__canvas_modify_dom",
            "mcp__lazy-tool-service__html_notes_youtube_search",
            "mcp__lazy-tool-service__create_widget",
            "mcp__lazy-tool-service__update_widget",
            "mcp__lazy-tool-service__validate_widget_html",
            "mcp__lazy-tool-service__list_widget_types",
            "mcp__lazy-tool-service__plan_widget",
            # Live-data tools served by tools-api (weather, time, web) — used to
            # feed custom widgets ("weather in Tokyo", "time in Berlin", etc.)
            "get_weather",
            "get_weather_forecast",
            "get_time_in_timezone",
            "parse_datetime",
            "search_web",
            "read_web_page",
            "search_news",
            "get_youtube_video"
        ]

        # Build messages array — use system role at index 0.
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            }
        ]

        # Only include recent history to avoid context overflow (last 10 messages)
        recent_history = history[-3:]
        for h in recent_history:
            content = h["content"]

            # Compress large HTML chunks in history
            if h["role"] == "assistant":
                # Strip out the new wrapped HTML
                content = re.sub(r'<!--CANVAS_HTML_START-->.*?<!--CANVAS_HTML_END-->', '[Visual Component Rendered]', content, flags=re.DOTALL)
                # Fallback for old history: strip common classes
                content = re.sub(r'<div class="[^"]*(glass-card|canvas-element|rendered-component)[^"]*">.*?</div>', '[Component]', content, flags=re.DOTALL)
                
                # Truncate very long assistant messages just in case
                if len(content) > 2000:
                    content = content[:2000] + "... [truncated]"

            # Skip tool-only placeholder messages
            if content == "[tool-only turn]":
                continue

            messages.append({"role": h["role"], "content": content})

        # The lazy-tool-service gateway runs the agentic loop and executes the
        # mcp__lazy-tool-service__* widget tools; plain Prism has no such
        # catalog registered, so LazyAgent is the default.
        target_url = LAZY_AGENT_URL if req.use_lazy_agent else PRISM_URL

        # Build /agent payload — NO tools array (the gateway uses its own catalog)
        model_name = req.model
        if not model_name:
            # Discover a provider/model pair from the gateway's local catalog so
            # the provider instance and model always match (e.g. "vllm" serves a
            # different model than "vllm-2").
            try:
                # /config-local probes each vLLM instance and takes ~3s itself,
                # so the client timeout must sit well above that.
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(f"{target_url}/config-local")
                    if resp.status_code == 200:
                        local_models = resp.json().get("models", {})
                        for provider_id, provider_models in local_models.items():
                            for m in provider_models:
                                if m.get("modelType") == "conversation" and "Tool Calling" in (m.get("tools") or []):
                                    req.provider = provider_id
                                    model_name = m.get("name")
                                    break
                            if model_name:
                                break
            except Exception as e:
                logger.warning(f"Failed to fetch model catalog from {target_url}: {e}")
        if not model_name:
            # VLLM_URL is Gold Spark (10.0.0.141), registered as provider
            # "vllm-2" in the gateway — the pair must match or the gateway
            # 404s with "model does not exist" on the wrong instance.
            try:
                with httpx.Client(timeout=2.0) as client:
                    resp = client.get(f"{VLLM_URL}/v1/models")
                    if resp.status_code == 200:
                        models_data = resp.json().get("data", [])
                        if models_data:
                            model_name = models_data[0].get("id")
                            req.provider = "vllm-2"
            except Exception as e:
                logger.warning(f"Failed to fetch dynamic model from {VLLM_URL}: {e}")
            if not model_name:
                # Last resort: the Jetson instance/model pair.
                req.provider = "vllm"
                model_name = "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit"
        # Sync the gateway's settings dynamically so MemoryExtractor doesn't crash on outdated models
        try:
            with httpx.Client(timeout=1.0) as client:
                client.put(f"{target_url}/settings", json={
                    "memory": {
                        "extractionModel": model_name
                    }
                })
        except Exception as e:
            logger.warning(f"Failed to sync memory extraction model to gateway: {e}")

        payload = {
            "provider": req.provider,
            "model": model_name,
            "workspaceRoot": "/home/lazycat/github/projects/sun/HTML-Notes",
            "workspaceEnabled": False,
            "enabledTools": enabled_tools,
            "messages": messages,
            "maxTokens": 4096,
            "project": "html-notes-client",
            "username": "lazycat",
            "skipConversation": True,
            "autoApprove": True,
            "memoryEnabled": False
        }

        async def proxy_prism_sse():
            """
            Stream Prism's /agent SSE events to the frontend.
            Prism handles the full agentic loop (tool calls, execution, re-prompting).
            We just proxy events and extract render_component results for the canvas.
            """
            final_text = ""
            all_rendered_html = req.current_canvas or ""
            executed_active_tool = False

            async def execute_mutation(tool_name, tool_args):
                nonlocal all_rendered_html
                logger.info(f"[WIDGET INJECTOR] Executing mutation for {tool_name} with args: {tool_args}")
                yield f'data: {json.dumps({"type": "status", "message": f"executing {tool_name}..."})}\n\n'
                try:
                    if tool_name == "mcp__lazy-tool-service__canvas_modify_dom":
                        css_selector = tool_args.get("css_selector", "")
                        action = tool_args.get("action", "")
                        html_snippet = tool_args.get("html_snippet", "")
                        
                        current_html = all_rendered_html if all_rendered_html else (req.current_canvas or "")
                        soup = BeautifulSoup(current_html, 'html.parser')
                        target = soup.select_one(css_selector)
                        if target:
                            if action == "append":
                                new_elem = BeautifulSoup(html_snippet, 'html.parser')
                                target.append(new_elem)
                            elif action == "replace":
                                new_elem = BeautifulSoup(html_snippet, 'html.parser')
                                target.replace_with(new_elem)
                            elif action == "remove":
                                target.decompose()
                            all_rendered_html = str(soup)
                            yield f'data: {json.dumps({"type": "component", "content": all_rendered_html})}\n\n'
                            
                        logger.info("[FAST LOOP] Terminating early after canvas_modify_dom to save latency")
                            
                    elif tool_name == "mcp__lazy-tool-service__canvas_add_widget":
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except Exception:
                                tool_args = {}

                        widget_type = tool_args.get("widget_type", "")
                        widget_id = tool_args.get("widget_id", f"widget-{uuid.uuid4().hex[:8]}")
                        config = tool_args.get("config", {})
                        
                        current_html = all_rendered_html if all_rendered_html else (req.current_canvas or "")
                        soup = BeautifulSoup(current_html, 'html.parser')
                        
                        replaced = False
                        if widget_type == "youtube_player":
                            for div in soup.find_all("div", class_="widget-container"):
                                xdata = div.get("x-data", "")
                                div_id = div.get("id", "")
                                has_iframe = div.find("iframe") is not None
                                is_youtube = ("youtubePlayerWidget" in xdata or 
                                              "youtube" in div_id.lower() or 
                                              "video" in div_id.lower() or 
                                              has_iframe)
                                if is_youtube:
                                    existing_id = div.get("id", widget_id)
                                    html_snippet = generate_widget_html(widget_type, existing_id, config)
                                    new_elem = BeautifulSoup(html_snippet, 'html.parser')
                                    div.replace_with(new_elem)
                                    replaced = True
                                    break
                                    
                        if replaced:
                            all_rendered_html = str(soup)
                            yield f'data: {json.dumps({"type": "component", "content": all_rendered_html})}\n\n'
                            logger.info("[WIDGET INJECTOR] Replaced existing youtube_player widget in-place")
                        else:
                            html_snippet = generate_widget_html(widget_type, widget_id, config)
                            target = soup.select_one('#dashboard-grid')
                            if target:
                                new_elem = BeautifulSoup(html_snippet, 'html.parser')
                                target.append(new_elem)
                                all_rendered_html = str(soup)
                                yield f'data: {json.dumps({"type": "component", "content": all_rendered_html})}\n\n'
                            else:
                                soup.append(BeautifulSoup(html_snippet, 'html.parser'))
                                all_rendered_html = str(soup)
                                yield f'data: {json.dumps({"type": "component", "content": all_rendered_html})}\n\n'
                            logger.info(f"[WIDGET INJECTOR] Appended new {widget_type} widget")
                            
                        logger.info("[FAST LOOP] Terminating early after canvas_add_widget to save latency")
                    elif tool_name == "mcp__lazy-tool-service__create_widget":
                        widget_type = tool_args.get("widgetType", "custom")
                        title = tool_args.get("title", "Widget")
                        html_content = tool_args.get("htmlContent", "")
                        css_content = tool_args.get("cssContent", "")
                        js_content = tool_args.get("jsContent", "")
                        
                        # Generate widget ID
                        widget_id = f"widget-{uuid.uuid4().hex[:8]}"
                        
                        # Scope CSS
                        scoped_css = ""
                        if css_content:
                            rules = []
                            for rule in css_content.split("}"):
                                if "{" in rule:
                                    sel, body = rule.split("{", 1)
                                    sel = sel.strip()
                                    if sel and not sel.startswith("@"):
                                        scoped_sel = ", ".join([f"#{widget_id} {s.strip()}" for s in sel.split(",")])
                                        rules.append(f"{scoped_sel} {{{body}}}")
                                    else:
                                        rules.append(rule + "}")
                            scoped_css = "\n".join(rules)
                            
                        # Wrap content
                        html_snippet = f"""
<div id="{widget_id}" class="glass-card canvas-widget" data-widget-type="{widget_type}">
    <div class="glass-card-title">{title}</div>
    <style>{scoped_css}</style>
    <div class="widget-body">{html_content}</div>
    <script>
    (function() {{
        const container = document.getElementById('{widget_id}');
        {js_content}
    }})();
    </script>
</div>
"""
                        current_html = all_rendered_html if all_rendered_html else (req.current_canvas or "")
                        soup = BeautifulSoup(current_html, 'html.parser')
                        target = soup.select_one('#dashboard-grid')
                        if target:
                            new_elem = BeautifulSoup(html_snippet, 'html.parser')
                            target.append(new_elem)
                            all_rendered_html = str(soup)
                            yield f'data: {json.dumps({"type": "component", "content": all_rendered_html})}\n\n'
                        else:
                            soup.append(BeautifulSoup(html_snippet, 'html.parser'))
                            all_rendered_html = str(soup)
                            yield f'data: {json.dumps({"type": "component", "content": all_rendered_html})}\n\n'
                        logger.info(f"[WIDGET INJECTOR] Created and appended new {widget_type} widget")
                        logger.info("[FAST LOOP] Terminating early after create_widget to save latency")
                    elif tool_name == "mcp__lazy-tool-service__update_widget":
                        widget_id = tool_args.get("widgetId")
                        title = tool_args.get("title")
                        html_content = tool_args.get("htmlContent")
                        css_content = tool_args.get("cssContent")
                        js_content = tool_args.get("jsContent")
                        
                        current_html = all_rendered_html if all_rendered_html else (req.current_canvas or "")
                        soup = BeautifulSoup(current_html, 'html.parser')
                        widget_div = soup.find(id=widget_id)
                        if widget_div:
                            if title is not None:
                                title_el = widget_div.select_one(".glass-card-title")
                                if title_el:
                                    title_el.string = title
                            if html_content is not None:
                                body_el = widget_div.select_one(".widget-body")
                                if body_el:
                                    body_el.clear()
                                    body_el.append(BeautifulSoup(html_content, 'html.parser'))
                            if css_content is not None:
                                style_el = widget_div.select_one("style")
                                if style_el:
                                    rules = []
                                    for rule in css_content.split("}"):
                                        if "{" in rule:
                                            sel, body = rule.split("{", 1)
                                            sel = sel.strip()
                                            if sel and not sel.startswith("@"):
                                                scoped_sel = ", ".join([f"#{widget_id} {s.strip()}" for s in sel.split(",")])
                                                rules.append(f"{scoped_sel} {{{body}}}")
                                            else:
                                                rules.append(rule + "}")
                                    style_el.string = "\n".join(rules)
                            if js_content is not None:
                                script_el = widget_div.select_one("script")
                                if script_el:
                                    script_el.string = f"""
                                    (function() {{
                                        const container = document.getElementById('{widget_id}');
                                        {js_content}
                                    }})();
                                    """
                            all_rendered_html = str(soup)
                            yield f'data: {json.dumps({"type": "component", "content": all_rendered_html})}\n\n'
                            logger.info(f"[WIDGET INJECTOR] Updated widget {widget_id} in-place")
                        logger.info("[FAST LOOP] Terminating early after update_widget to save latency")
                except Exception as ex:
                    logger.error(f"Failed to execute canvas mutation: {ex}")

            try:
                yield f'data: {json.dumps({"type": "status", "message": "connecting to agent..."})}\n\n'

                async with httpx.AsyncClient(timeout=600.0) as client:
                    async with client.stream(
                        "POST",
                        f"{target_url}/agent",
                        json=payload,
                        headers={"Accept": "text/event-stream"}
                    ) as resp:
                        if resp.status_code != 200:
                            error_body = ""
                            async for chunk in resp.aiter_text():
                                error_body += chunk
                            yield f'data: {json.dumps({"type": "error", "message": f"Prism error {resp.status_code}: {error_body[:500]}"})}\n\n'
                            return

                        buffer = ""
                        active_tool_name = None
                        active_tool_args = {}

                        async for chunk in resp.aiter_text():
                            buffer += chunk
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.strip()

                                if not line.startswith("data: "):
                                    continue

                                try:
                                    event = json.loads(line[6:])
                                except json.JSONDecodeError:
                                    continue

                                event_type = event.get("type", "")
                                logger.info(f"[SSE_PROXY] Received event_type: '{event_type}'")

                                if event_type in ("chunk", "done") and active_tool_name in ("mcp__lazy-tool-service__canvas_modify_dom", "mcp__lazy-tool-service__canvas_add_widget"):
                                    if not executed_active_tool and is_valid_tool_args(active_tool_name, active_tool_args):
                                        async for evt in execute_mutation(active_tool_name, active_tool_args):
                                            yield evt
                                        executed_active_tool = True
                                        active_tool_name = None
                                        active_tool_args = {}

                                if event_type == "chunk":
                                    # Text token from LLM
                                    token = event.get("content", "")
                                    final_text += token
                                    yield f'data: {json.dumps({"type": "chunk", "content": token})}\n\n'

                                elif event_type == "tool_execution":
                                    status = event.get("status", "")
                                    tool_info = event.get("tool", {})
                                    tool_name = tool_info.get("name", "unknown")
                                    args = tool_info.get("args", {})
                                    
                                    if active_tool_name != tool_name:
                                        active_tool_name = tool_name
                                        active_tool_args = {}
                                        executed_active_tool = False
                                        yield f'data: {json.dumps({"type": "tool_call", "tool": tool_name})}\n\n'
                                        yield f'data: {json.dumps({"type": "status", "message": f"preparing {tool_name}..."})}\n\n'
                                    
                                    active_tool_args = args

                                    # FAST PATH: Execute immediately when arguments are available!
                                    if active_tool_name in ("mcp__lazy-tool-service__canvas_modify_dom", "mcp__lazy-tool-service__canvas_add_widget", "mcp__lazy-tool-service__create_widget", "mcp__lazy-tool-service__update_widget"):
                                        if not executed_active_tool and is_valid_tool_args(active_tool_name, active_tool_args) and status in ("calling", "done", "success"):
                                            async for evt in execute_mutation(active_tool_name, active_tool_args):
                                                yield evt
                                            executed_active_tool = True
                                            active_tool_name = None
                                            active_tool_args = {}
                                        elif status in ("calling", "done", "success", "error"):
                                            active_tool_name = None
                                            active_tool_args = {}
                                    elif status == "error":
                                        error_msg = event.get("result", "Unknown tool error")
                                        yield f'data: {json.dumps({"type": "status", "message": f"tool error: {tool_name}: {str(error_msg)[:200]}"})}\n\n'

                                elif event_type == "thinking":
                                    yield f'data: {json.dumps({"type": "status", "message": "reasoning..."})}\n\n'

                                elif event_type == "done":
                                    # Prism finished the full agentic loop
                                    pass

                                elif event_type == "error":
                                    yield f'data: {json.dumps({"type": "error", "message": event.get("message", "Agent error")})}\n\n'

            except Exception as e:
                logger.error(f"Prism SSE proxy error: {e}")
                yield f'data: {json.dumps({"type": "error", "message": f"Connection error: {str(e)}"})}\n\n'

            # Save assistant response to DB
            asst_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
            saved_content = final_text
            if all_rendered_html:
                saved_content += f"\n\n<!--CANVAS_HTML_START-->\n{all_rendered_html}\n<!--CANVAS_HTML_END-->"
                
            if not saved_content.strip():
                saved_content = "[tool-only turn]"

            database.save_chat_message(
                message_id=asst_msg_id,
                session_id=req.session_id,
                role="assistant",
                content=saved_content
            )

            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(proxy_prism_sse(), media_type="text/event-stream")

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

@app.get("/api/youtube/search")
async def api_youtube_search(query: str):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MUSIC_PLAYER_URL}/api/youtube/search", params={"query": query}, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            else:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except Exception as e:
        logger.error(f"Failed to proxy YouTube search: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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

@app.post("/tts/synthesize")
async def tts_synthesize(req: TTSSynthesizeRequest):
    """
    Proxies TTS synthesis request to tts-service.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{TTS_SERVICE_URL}/api/v1/tts/synthesize",
                json={"text": req.text}
            )
            if resp.status_code != 200:
                logger.error(f"TTS service returned status code {resp.status_code}: {resp.text}")
                raise HTTPException(status_code=503, detail="TTS service failure")
            return Response(content=resp.content, media_type="audio/wav")
    except Exception as e:
        logger.error(f"Failed proxying to TTS service: {e}")
        raise HTTPException(status_code=503, detail=f"TTS service unavailable: {str(e)}")

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
            from app.templates import TEMPLATES
            ctype = a.get("component_type")
            data = a.get("data", {})
            if ctype in TEMPLATES:
                html = TEMPLATES[ctype](data)
            else:
                html = a.get("rendered_html", "")
            
            return {
                "success": True,
                "rendered_html": html,
                "component_type": ctype,
                "title": a.get("title", "Component")
            }

        elif t == "canvas_read_dom":
            canvas_html = a.get("canvas_html") or latest_canvas_html
            css_selector = a.get("css_selector")
            
            if not canvas_html or canvas_html.strip() == "":
                return {"elements": [], "element_count": 0, "summary": "Canvas is empty."}
            
            soup = BeautifulSoup(canvas_html, "html.parser")
            
            if css_selector:
                # Return specific element(s)
                matches = soup.select(css_selector)
                if not matches:
                    return {"error": f"No elements matched selector '{css_selector}'", "matched": 0}
                return {
                    "matched": len(matches),
                    "elements": [
                        {
                            "tag": el.name,
                            "classes": el.get("class", []),
                            "text": el.get_text(strip=True)[:300],
                            "html": str(el)[:1000],
                            "children_count": len(list(el.children))
                        }
                        for el in matches[:10]
                    ]
                }
            
            # Full canvas summary
            components = []
            for card in soup.select(".glass-card"):
                title_el = card.select_one(".glass-card-title")
                classes = card.get("class", [])
                comp_type = "unknown"
                for cls in classes:
                    if cls != "glass-card":
                        comp_type = cls
                        break
                components.append({
                    "type": comp_type,
                    "title": title_el.get_text(strip=True) if title_el else "",
                    "text_preview": card.get_text(strip=True)[:200]
                })
            
            all_text = soup.get_text(strip=True)[:500]
            all_tags = [el.name for el in soup.find_all(True)]
            tag_counts = {}
            for tag in all_tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            
            return {
                "element_count": len(all_tags),
                "tag_counts": tag_counts,
                "components": components,
                "component_count": len(components),
                "text_preview": all_text,
                "has_content": bool(all_text.strip())
            }

        elif t == "canvas_modify_dom":
            canvas_html = a.get("canvas_html") or latest_canvas_html
            css_selector = a.get("css_selector")
            action = a.get("action")
            html_snippet = a.get("html_snippet", "")
            
            if not canvas_html:
                return {"error": "Canvas is empty, nothing to modify", "is_error": True}
            if not css_selector:
                return {"error": "css_selector is required", "is_error": True}
            if not action:
                return {"error": "action is required", "is_error": True}
            
            soup = BeautifulSoup(canvas_html, "html.parser")
            target = soup.select_one(css_selector)
            
            if not target:
                return {"error": f"No element matched selector '{css_selector}'", "is_error": True}
            
            if action == "remove":
                target.decompose()
            elif action in ("append", "prepend", "replace", "insert_before", "insert_after"):
                if not html_snippet:
                    return {"error": f"html_snippet is required for action '{action}'", "is_error": True}
                snippet_soup = BeautifulSoup(html_snippet, "html.parser")
                if action == "append":
                    target.append(snippet_soup)
                elif action == "prepend":
                    target.insert(0, snippet_soup)
                elif action == "replace":
                    target.replace_with(snippet_soup)
                elif action == "insert_before":
                    target.insert_before(snippet_soup)
                elif action == "insert_after":
                    target.insert_after(snippet_soup)
            else:
                return {"error": f"Unknown action: {action}", "is_error": True}
            
            return {
                "success": True,
                "rendered_html": str(soup),
                "action_performed": action,
                "selector": css_selector
            }

        elif t == "html_notes_add_youtube_widget":
            # Handled by the SSE interceptor on the streaming wrapper, but we return success here as well
            return {"success": True, "message": "Successfully added YouTube widget to canvas."}

        elif t == "create_widget":
            return {"success": True, "message": "Widget created successfully."}

        elif t == "update_widget":
            return {"success": True, "message": "Widget updated successfully."}

        elif t == "html_notes_youtube_search":
            query = a.get("query", "")
            limit = int(a.get("limit", 5))
            order = a.get("order", "relevance")
            results = await search_youtube_videos(query, limit=limit, order=order)
            return {"results": results, "count": len(results)}

        elif t == "canvas_add_widget":
            # The actual injection to the frontend is handled by the SSE interceptor during 'calling' phase.
            # Here we just acknowledge success to the LLM so it doesn't think the tool failed.
            return {"success": True, "message": f"Successfully added {a.get('widget_type', 'widget')} to canvas."}

        else:
            return {"error": f"Unknown tool: {t}", "is_error": True}

    except Exception as e:
        logger.error(f"Internal tool execution error: {e}")
        return {"error": str(e), "is_error": True}

CANVAS_BLOCK_RE = re.compile(r'<!--CANVAS_HTML_START-->(.*?)<!--CANVAS_HTML_END-->', re.DOTALL)

@app.get("/session/{session_id}/history")
async def get_session_history(session_id: str):
    try:
        history = database.get_session_messages(session_id)
        # Persisted assistant messages embed the canvas snapshot; split it into
        # canvas_html so the client shows only text in the chat panel.
        messages = []
        for h in history:
            msg = dict(h)
            content = msg.get("content") or ""
            m = CANVAS_BLOCK_RE.search(content)
            if m:
                msg["canvas_html"] = m.group(1).strip()
                msg["content"] = CANVAS_BLOCK_RE.sub("", content).strip()
            messages.append(msg)
        return {"messages": messages}
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
