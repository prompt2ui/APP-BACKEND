# app/chat/router.py
from fastapi import APIRouter
from .schemas import PromptRequest, PromptResponse
from agent.orchestrator import analyze_and_generate


router = APIRouter()
@router.post("/prompt", response_model=PromptResponse, status_code=200)
async def sending_prompt(req: PromptRequest):
    print(req)
    res = await analyze_and_generate(req)
    data = PromptRequest(
        id=req.id + "ai",
        content=res,
        sender="assistant"
    )
    return data