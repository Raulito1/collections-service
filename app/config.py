# app/config.py
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    SUPABASE_URL: str
    SUPABASE_JWKS_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: Optional[str] = None
    ALLOWED_ORIGINS: str = "http://localhost:5173"
    QBO_CLIENT_ID: str
    QBO_CLIENT_SECRET: str
    QBO_REDIRECT_URL: str           # e.g. http://localhost:8000/auth/quickbooks/callback
    QBO_ENV: str = "sandbox"        # or "production"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

settings = Settings()
