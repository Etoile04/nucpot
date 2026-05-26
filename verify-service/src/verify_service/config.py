"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    PORT: int = 8000
    DEBUG: bool = False

    # Supabase
    SUPABASE_URL: str = "http://127.0.0.1:54321"
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    # Direct PostgreSQL (alternative to Supabase client)
    DATABASE_URL: str = ""

    # Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    # Grading thresholds (relative error)
    GRADING_THRESHOLD_A: float = 0.01
    GRADING_THRESHOLD_B: float = 0.03
    GRADING_THRESHOLD_C: float = 0.05
    GRADING_THRESHOLD_D: float = 0.10

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def get_settings() -> Settings:
    return Settings()
