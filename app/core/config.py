# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    LITELLM_PROXY_URL: str
    LITELLM_PROXY_API_KEY: str

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
