import re
import uuid

def process_file():
    with open("app/main.py", "r") as f:
        content = f.read()

    # 1. Add the youtube fast scraper helper
    scraper_code = """
import urllib.parse
import httpx

async def fast_youtube_search(query: str) -> str:
    \"\"\"Scrape youtube for the first video ID for a query.\"\"\"
    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            html = resp.text
            import re
            matches = re.findall(r'\"videoId\":\"([a-zA-Z0-9_-]{11})\"', html)
            if matches:
                # remove dupes but preserve order
                unique = list(dict.fromkeys(matches))
                return unique[0]
    except Exception as e:
        logger.error(f"fast_youtube_search error: {e}")
    return ""
"""

    if "fast_youtube_search" not in content:
        content = content.replace("from fastapi import FastAPI, HTTPException", scraper_code + "\nfrom fastapi import FastAPI, HTTPException")

    # 2. Add /yt fast path in api_chat
    fast_path_code = """
        text_lower = req.message.lower().strip()
        if text_lower.startswith("/yt ") or text_lower.startswith("/youtube "):
            query = req.message.split(" ", 1)[1]
            async def fast_path_stream():
                yield f'data: {{"type": "status", "message": "fast-path: fetching youtube video..."}}\\n\\n'
                video_id = await fast_youtube_search(query)
                if video_id:
                    widget_id = f"youtube-{uuid.uuid4().hex[:8]}"
                    html_snippet = generate_widget_html("youtube_player", widget_id, {"video_id": video_id})
                    
                    # Instead of parsing the whole DOM just to append, we can yield an append mutation
                    yield f'data: {{"type": "component", "content": html_snippet, "action": "append", "target": "#dashboard-grid"}}\\n\\n'
                    yield 'data: {"type": "done"}\\n\\n'
                else:
                    yield f'data: {{"type": "error", "message": "Failed to find video"}}\\n\\n'
            return StreamingResponse(fast_path_stream(), media_type="text/event-stream")

        # Start loading history
"""

    if "fast-path: fetching youtube" not in content:
        content = content.replace("history = database.get_session_messages(req.session_id)", fast_path_code + "\n        history = database.get_session_messages(req.session_id)")

    # 3. Add interception for add_youtube_widget in the SSE proxy
    macro_tool_interceptor = """
                                    if status in ("done", "success"):
                                        if active_tool_name == "mcp__lazy-tool-service__add_youtube_widget":
                                            query = active_tool_args.get("query", "")
                                            if query and not executed_active_tool:
                                                video_id = await fast_youtube_search(query)
                                                if video_id:
                                                    fake_args = {
                                                        "widget_type": "youtube_player",
                                                        "widget_id": f"youtube-{uuid.uuid4().hex[:8]}",
                                                        "config": {"video_id": video_id}
                                                    }
                                                    async for evt in execute_mutation("mcp__lazy-tool-service__canvas_add_widget", fake_args):
                                                        yield evt
                                                executed_active_tool = True
                                                active_tool_name = None
                                                active_tool_args = {}
                                        elif active_tool_name in ("mcp__lazy-tool-service__canvas_modify_dom", "mcp__lazy-tool-service__canvas_add_widget"):
"""

    if "add_youtube_widget" not in content.split("status in (\"done\", \"success\"):")[1][:200]:
        content = content.replace("""
                                    if status in ("done", "success"):
                                        if active_tool_name in ("mcp__lazy-tool-service__canvas_modify_dom", "mcp__lazy-tool-service__canvas_add_widget"):
""", macro_tool_interceptor)


    with open("app/main.py", "w") as f:
        f.write(content)

process_file()
