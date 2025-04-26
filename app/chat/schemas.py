# app/chat/schemas.py
from pydantic import BaseModel
from typing import List


class PromptRequest(BaseModel):
    id: str
    project_id: str
    content: str
    sender: str
    attachments: List[str] = []

class PromptResponse(BaseModel):
    id: str
    content: str
    sender: str = "assistant"
