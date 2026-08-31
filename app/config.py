"""
Application configuration - loads from environment variables.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://aegeansense:aegeansense_local_dev@localhost:5432/aegeansense"

    # Batch sync settings (to avoid the API's 10k-record-per-request limit)
    sync_batch_days: int = 7            # Fetch data in 7-day chunks
    sync_start_date: str = "2024-01-01" # Default start date for historical sync

    # Admin bootstrap - creates the first admin_users row on startup if none exists
    admin_email: str = ""
    admin_password: str = ""

    # Admin JWT auth
    jwt_secret: str = "change-me-in-production"
    jwt_expire_minutes: int = 720  # 12 hours

    # Fernet key used to encrypt FinotProfile passwords at rest
    credential_encryption_key: str = ""

    # Comma-separated browser origins allowed to call the API. In production,
    # set this to the public Railway domain of aegeansense-web.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def allowed_cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignore extra env vars like POSTGRES_*, DB_PASSWORD


@lru_cache
def get_settings() -> Settings:
    return Settings()
