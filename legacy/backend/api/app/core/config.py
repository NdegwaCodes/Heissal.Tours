from pydantic_settings import BaseSettings
from pydantic import AnyUrl


class Settings(BaseSettings):
    PROJECT_NAME: str = "Heissal Tours"
    API_V1_STR: str = "/api/v1"

    # Mark as optional / with defaults so instantiation without args is valid.
    DATABASE_URL: AnyUrl | None = None
    REDIS_URL: str = "redis://redis:6379/0"

    JWT_SECRET_KEY: str = "CHANGE_ME"  # will be overridden by env
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    class Config:
        env_file = ".env"


settings = Settings()
