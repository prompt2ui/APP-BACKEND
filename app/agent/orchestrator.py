# app/agent/agent.orchestrator.py
import httpx
from pathlib import Path
from core.config import settings
from chat.schemas import PromptRequest, PromptResponse


def instructionsLoader(filename: str) -> str:
    path = Path(__file__).parent / "instructions" / f"{filename}.txt"
    return path.read_text(encoding="utf-8").strip()

async def analyze_and_generate(prompt: PromptRequest) -> PromptResponse:
    rewritten_output = await analyze_prompt(prompt.content, prompt.attachments)
    # final_output = await generate_code(rewritten_output, prompt.attachments)
    return rewritten_output  # ส่งแค่การแก้ไข prompt

async def analyze_prompt(prompt: str, attachments: list = []) -> str:
    headers = {
        "Authorization": f"Bearer {settings.LITELLM_PROXY_API_KEY}",
        "Content-Type": "application/json"
    }

    instructions = instructionsLoader("analyse")

    # เตรียมข้อความที่จะส่ง
    messages = [
        {
            "role": "system",
            "content": instructions
        },
        {
            "role": "user", 
            "content": [
                {"type": "text", "text": prompt}  # ส่งข้อความ prompt
            ]
        }
    ]

    # ถ้ามี attachments (รูปภาพ) ให้เพิ่มเข้าไปในข้อความ
    if attachments:
        for image_url in attachments:
            messages[1]["content"].append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url}
                }
            )

    payload = {
        "model": "llama3",
        "temperature": 0.3,
        "messages": messages,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.LITELLM_PROXY_URL + "/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

async def generate_code(prompt: str, attachments: list = []) -> str:
    headers = {
        "Authorization": f"Bearer {settings.LITELLM_PROXY_API_KEY}",
        "Content-Type": "application/json"
    }

    instructions = instructionsLoader("coder")
    
    # เตรียมข้อความที่จะส่ง
    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": [
            {"type": "text", "text": prompt}  # ส่งข้อความ prompt
        ]}
    ]

    # ถ้ามี attachments (รูปภาพ) ให้เพิ่มเข้าไปในข้อความ
    if attachments:
        for image_url in attachments:
            messages[1]["content"].append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url}
                }
            )

    payload = {
        "model": "gpt-4o-mini", 
        "messages": messages
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.LITELLM_PROXY_URL + "/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
