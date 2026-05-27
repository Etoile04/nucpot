"""Service configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase connection
    SUPABASE_URL: str = "http://localhost:54321"
    SUPABASE_SERVICE_KEY: str = ""

    # Redis (for future Celery support)
    REDIS_URL: str = "redis://localhost:6379"

    # Calculation limits
    CALCULATION_TIMEOUT: int = 300  # seconds
    MAX_ATOMS: int = 10000
    EOS_NUM_POINTS: int = 15  # equation of state fitting points

    # Elastic constant strain amplitudes
    ELASTIC_STRAIN_EPSILONS: list[float] = [0.001, 0.002, 0.003]

    # Worker
    WORKER_CONCURRENCY: int = 2

    # Grading thresholds (relative error)
    GRADE_A_THRESHOLD: float = 0.01  # ≤1%
    GRADE_B_THRESHOLD: float = 0.03  # ≤3%
    GRADE_C_THRESHOLD: float = 0.05  # ≤5%
    GRADE_D_THRESHOLD: float = 0.10  # ≤10%
    # F > 10%

    # Service
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_prefix": ""}


settings = Settings()
