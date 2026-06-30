import asyncio
import httpx
import json

async def test_prism():
    payload = {
        "provider": "vllm",
        "model": "qwen3-vl-235b-a22b-instruct",
        "workspaceRoot": "/home/lazycat/github/projects/sun/HTML-Notes",
        "workspaceEnabled": False,
        "enabledTools": [
            "mcp__lazy-tool-service__canvas_add_widget",
            "mcp__lazy-tool-service__html_notes_youtube_search"
        ],
        "messages": [
            {
                "role": "system",
                "content": "You are a TOOL-ONLY JSON agent. You MUST NEVER output any conversational text, thinking process, or explanations. You MUST START your response immediately with the tool call. If you output any text that is not a tool call, the system will crash. Execute html_notes_youtube_search."
            },
            {
                "role": "user",
                "content": "new fireship video"
            }
        ],
        "maxTokens": 512,
        "project": "html-notes-client",
        "username": "lazycat",
        "skipConversation": True,
        "autoApprove": True,
        "memoryEnabled": False
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", "http://10.0.0.16:7777/agent", json=payload, headers={"Accept": "text/event-stream"}) as resp:
            print(f"Status: {resp.status_code}")
            async for chunk in resp.aiter_text():
                print(chunk, end="")

asyncio.run(test_prism())
