import asyncio
import httpx
import json

async def main():
    payload = {
        "provider": "lm-studio",
        "model": "qwen3-vl-8b-instruct-abliterated-v2.0",
        "workspaceRoot": "/home/lazycat/github/projects/sun/HTML-Notes",
        "workspaceEnabled": False,
        "enabledTools": [
            "mcp__lazy-tool-service__html_notes_create_note",
            "mcp__lazy-tool-service__html_notes_update_note",
            "mcp__lazy-tool-service__html_notes_get_note",
            "mcp__lazy-tool-service__html_notes_search_notes",
            "mcp__lazy-tool-service__html_notes_link_notes",
            "mcp__lazy-tool-service__html_notes_modify_dom",
            "mcp__lazy-tool-service__render_component",
            "mcp__lazy-tool-service__canvas_read_dom",
            "mcp__lazy-tool-service__canvas_modify_dom"
        ],
        "messages": [{"role": "user", "content": "hello"}],
        "maxTokens": 4096,
        "project": "html-notes-client",
        "username": "lazycat",
        "skipConversation": True,
        "autoApprove": True
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            "http://10.0.0.16:7777/agent",
            json=payload,
            headers={"Accept": "text/event-stream"}
        ) as resp:
            print("Status:", resp.status_code)
            async for chunk in resp.aiter_text():
                print(chunk)

asyncio.run(main())
