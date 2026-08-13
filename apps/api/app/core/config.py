"""Application configuration, loaded from environment / .env.

All secrets and environment-specific values come from the environment. Nothing
business-related is hard-coded here — this is infrastructure configuration only.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- General ---
    ENVIRONMENT: str = "development"
    PROJECT_NAME: str = "Heissal Tours & Travel Platform"
    TIMEZONE: str = "Africa/Nairobi"
    BASE_REPORTING_CURRENCY: str = "USD"

    # --- API ---
    API_V1_STR: str = "/api/v1"
    BACKEND_CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Database ---
    POSTGRES_USER: str = "heissal"
    POSTGRES_PASSWORD: str = "heissal"
    POSTGRES_DB: str = "heissal"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str | None = None

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Security ---
    JWT_SECRET_KEY: str = "change_me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    # --- First superuser (seed) ---
    FIRST_SUPERUSER_EMAIL: str = "admin@heissal.co.ke"
    FIRST_SUPERUSER_PASSWORD: str = "ChangeMe123!"
    FIRST_SUPERUSER_NAME: str = "Platform Admin"

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def sqlalchemy_database_uri(self) -> str:
        """Async SQLAlchemy URL — explicit override wins, else assembled from parts."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
