from fastapi import FastAPI
from chat.router import router as chat_router

from fastapi.middleware.cors import CORSMiddleware

origins = [
    "http://localhost:3000", 
]

app = FastAPI(
    title="AI Frontend Component Generator",
    version="1.0.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# app/main.py
app.include_router(chat_router, prefix="/api/v1/chat", tags=["v1"])