import asyncio
import httpx
from app.config import VLLM_URL

async def test_audio():
    # just a tiny dummy webm base64 or wav base64
    audio_b64 = "UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA="
    
    payload = {
        "model": "cyankiwi/MiniMax-M2.7-AWQ-4bit",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please transcribe this audio. Return ONLY the transcript."},
                    {"type": "image_url", "image_url": {"url": f"data:audio/wav;base64,{audio_b64}"}}
                ]
            }
        ]
    }
    
    # Try image_url first as some multimodal models accept audio through image_url
    # or input_audio
    async with httpx.AsyncClient() as client:
        res = await client.post(f"{VLLM_URL}/v1/chat/completions", json=payload)
        print("image_url type:", res.status_code, res.text)
        
    payload["messages"][0]["content"][1] = {
        "type": "audio_url",
        "audio_url": {"url": f"data:audio/wav;base64,{audio_b64}"}
    }
    async with httpx.AsyncClient() as client:
        res = await client.post(f"{VLLM_URL}/v1/chat/completions", json=payload)
        print("audio_url type:", res.status_code, res.text)
        
    payload["messages"][0]["content"][1] = {
        "type": "input_audio",
        "input_audio": {"data": audio_b64, "format": "wav"}
    }
    async with httpx.AsyncClient() as client:
        res = await client.post(f"{VLLM_URL}/v1/chat/completions", json=payload)
        print("input_audio type:", res.status_code, res.text)

if __name__ == "__main__":
    asyncio.run(test_audio())
