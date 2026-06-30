import re
import json

def patch():
    with open("app/main.py", "r") as f:
        content = f.read()

    # 1. Update fast_youtube_search to also include search_youtube_videos
    search_helper_code = """
import urllib.parse
import httpx

async def search_youtube_videos(query: str, limit: int = 5) -> list:
    \"\"\"Search YouTube and return a list of video dicts containing video_id and title.\"\"\"
    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            html = resp.text
            import re
            matches = re.findall(r'\"videoRenderer\":\{.*?\"videoId\":\"([a-zA-Z0-9_-]{11})\",.*?\"title\":\{\"runs\":\[\{\"text\":\"(.*?)\"\}\]\}', html)
            results = []
            seen = set()
            for vid, title in matches:
                if vid not in seen:
                    seen.add(vid)
                    try:
                        clean_title = json.loads('"' + title + '"')
                    except Exception:
                        clean_title = title
                    results.append({"video_id": vid, "title": clean_title})
                    if len(results) >= limit:
                        break
            return results
    except Exception as e:
        logger.error(f"search_youtube_videos error: {e}")
    return []

async def fast_youtube_search(query: str) -> str:
    \"\"\"Scrape youtube for the first video ID for a query.\"\"\"
    results = await search_youtube_videos(query, limit=1)
    if results:
        return results[0]["video_id"]
    return ""
"""

    # Replace the old fast_youtube_search
    old_helper_pattern = r"async def fast_youtube_search\(query: str\).*?return \"\""
    content = re.sub(old_helper_pattern, search_helper_code.strip(), content, flags=re.DOTALL)

    # 2. Update SYSTEM_PROMPT to list the new custom tools
    # Let's inspect the prompt block first.
    # We will replace the Canvas Tools section.
    old_tools_prompt = """            "CANVAS TOOLS:\\n"
            "- Inspect what's on screen → mcp__lazy-tool-service__canvas_read_dom()\\n"
            "- Add a Lego Widget (Checklist, Clock, Notes, Music Player, YouTube Player) → mcp__lazy-tool-service__canvas_add_widget()\\n"
            "- Modify/remove an existing widget → mcp__lazy-tool-service__canvas_modify_dom(css_selector='#widget-UUID', action='replace' or 'remove')\\n"
            "- Search notes → mcp__lazy-tool-service__html_notes_search_notes(query)\\n"
            "- Update a note → mcp__lazy-tool-service__html_notes_get_note(note_id) then mcp__lazy-tool-service__html_notes_update_note()\\n"
            "- Search YouTube for videos → mcp__lazy-tool-service__youtube_search(query, limit, sort)\\n"
            "  * Always use sort='date' if the user asks for the newest or latest videos.\\n\\n"
            "AGENTIC UI GENERATION RULES:\\n"
            "1. DASHBOARD GRID SYSTEM: The canvas is a CSS Grid (#dashboard-grid).\\n"
            "2. ADDING STANDARD WIDGETS: ALWAYS use `mcp__lazy-tool-service__canvas_add_widget(widget_type, widget_id, config)` to spawn pre-built Lego widgets (types: 'checklist', 'clock', 'notes', 'iframe_app', 'mini_music_player', 'youtube_player'). Provide a unique `widget_id`. For 'iframe_app', use config like `{\\"url\\": \\"http://nas:3000\\", \\"title\\": \\"App\\", \\"icon\\": \\"🌐\\"}`. For 'mini_music_player', use config `{\\"genre\\": \\"jazz\\", \\"autoplay\\": true}`. For YouTube videos, use `mcp__lazy-tool-service__add_youtube_widget(query)` instead to automatically search and add the widget in one step. Never pass raw search queries directly to `canvas_add_widget`. NEVER try to generate the raw HTML yourself for standard widgets.\\n\""""

    new_tools_prompt = """            "CANVAS TOOLS:\\n"
            "- Inspect what's on screen → mcp__lazy-tool-service__canvas_read_dom()\\n"
            "- Add a Lego Widget (Checklist, Clock, Notes, Music Player, YouTube Player) → mcp__lazy-tool-service__canvas_add_widget()\\n"
            "- Modify/remove an existing widget → mcp__lazy-tool-service__canvas_modify_dom(css_selector='#widget-[UUID]', action='replace' or 'remove')\\n"
            "- Search notes → mcp__lazy-tool-service__html_notes_search_notes(query)\\n"
            "- Update a note → mcp__lazy-tool-service__html_notes_get_note(note_id) then mcp__lazy-tool-service__html_notes_update_note()\\n"
            "- Search YouTube for videos → mcp__lazy-tool-service__html_notes_youtube_search(query, limit)\\n"
            "- Add a YouTube widget to canvas → mcp__lazy-tool-service__html_notes_add_youtube_widget(query)\\n\\n"
            "AGENTIC UI GENERATION RULES:\\n"
            "1. DASHBOARD GRID SYSTEM: The canvas is a CSS Grid (#dashboard-grid).\\n"
            "2. ADDING STANDARD WIDGETS: ALWAYS use `mcp__lazy-tool-service__canvas_add_widget(widget_type, widget_id, config)` to spawn pre-built Lego widgets (types: 'checklist', 'clock', 'notes', 'iframe_app', 'mini_music_player', 'youtube_player'). Provide a unique `widget_id`. For 'iframe_app', use config like `{\\"url\\": \\"http://nas:3000\\", \\"title\\": \\"App\\", \\"icon\\": \\"🌐\\"}`. For 'mini_music_player', use config `{\\"genre\\": \\"jazz\\", \\"autoplay\\": true}`. For YouTube videos, you MUST ALWAYS use `mcp__lazy-tool-service__html_notes_add_youtube_widget(query)` instead to automatically search and add the widget in one step. Never pass raw search queries directly to `canvas_add_widget`. NEVER try to generate the raw HTML yourself for standard widgets.\\n\""""

    content = content.replace(old_tools_prompt, new_tools_prompt)

    # 3. Update the SSE interceptor from add_youtube_widget to html_notes_add_youtube_widget
    content = content.replace("mcp__lazy-tool-service__add_youtube_widget", "mcp__lazy-tool-service__html_notes_add_youtube_widget")

    # 4. Add the html_notes_youtube_search and html_notes_add_youtube_widget tool execution endpoints in internal_tool_execute
    execute_add_youtube = """        elif t == "html_notes_add_youtube_widget":
            # Handled by the SSE interceptor on the streaming wrapper, but we return success here as well
            return {"success": True, "message": "Successfully added YouTube widget to canvas."}

        elif t == "html_notes_youtube_search":
            query = a.get("query", "")
            limit = int(a.get("limit", 5))
            results = await search_youtube_videos(query, limit=limit)
            return {"results": results, "count": len(results)}

        elif t == "canvas_add_widget":"""

    content = content.replace("        elif t == \"canvas_add_widget\":", execute_add_youtube)

    with open("app/main.py", "w") as f:
        f.write(content)

patch()
