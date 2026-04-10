# src/config.py
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    APP_NAME:                       str = "App Backend"
    OPENAI_API_KEY:                 str = ""

    SUPABASE_PROJECT_URL:           str = ""
    SUPABASE_PUBLISHABLE_KEY:       str = ""
    SUPABASE_ANNON_KEY:             str = ""
    SUPABASE_SERVICE_ROLE_KEY:      str = ""
    SUPABASE_DATABASE_URL:          str = ""
    SUPABASE_DIRECT_URL:            str = ""

    OBJECTS_STORAGE_ENDPOINT:       Optional[str] = None
    OBJECTS_STORAGE_ROOT_USER:      Optional[str] = None
    OBJECTS_STORAGE_ROOT_PASSWORD:  Optional[str] = None
    OBJECTS_STORAGE_BUCKET:         Optional[str] = "app-object-storage"
    OBJECTS_STORAGE_BASE_PATH:      Optional[str] = "projects"

    # Optional defaults for POST /health/clickup-smoke (local testing only; do not commit real tokens).
    CLICKUP_SMOKE_TOKEN: Optional[str] = None
    CLICKUP_SMOKE_LIST_URL: Optional[str] = None

    class Config:
        env_file = ".env"

env = Settings()
