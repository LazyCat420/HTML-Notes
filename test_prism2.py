import asyncio
import httpx

async def main():
    payload = {
        "provider": "vllm",
        "model": "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit",
        "workspaceRoot": "/home/lazycat/github/projects/sun/HTML-Notes",
        "workspaceEnabled": False,
        "enabledTools": [
            "mcp__lazy-tool-service__html_notes_create_note"
        ],
        "messages": [{"role": "user", "content": "hello"}],
        "maxTokens": 4096,
        "project": "html-notes-client",
        "username": "lazycat",
        "skipConversation": True,
        "autoApprove": True
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
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
