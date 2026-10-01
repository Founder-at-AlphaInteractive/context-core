from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    environment: str = "dev"
    log_level: str = "INFO"

    # Database
    database_url: str = (
        "postgresql+psycopg2://context_core:context_core@localhost:5432/context_core"
    )

    # Auth
    jwt_secret: str = "dev-insecure-jwt-secret-replace-in-production-min-32-chars"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 24 * 30
    device_registration_secret: str = "dev-device-registration-secret"

    # Groq
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Worker
    worker_poll_interval_seconds: int = 5
    worker_max_attempts: int = 5
    worker_enabled: bool = True

    # Context compiler
    context_default_token_budget: int = 4000


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
