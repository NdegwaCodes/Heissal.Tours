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

    # libpq/psycopg2 connection params that asyncpg does not understand and must
    # be stripped from the async URL (SSL is passed via connect_args instead).
    _LIBPQ_ONLY_QUERY_KEYS = frozenset(
        {"sslmode", "channel_binding", "sslrootcert", "sslcert", "sslkey", "options"}
    )

    def _raw_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    def _url_with_driver(self, driver: str, *, strip_libpq: bool) -> str:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parts = urlsplit(self._raw_database_url())
        scheme = f"postgresql+{driver}"
        query = parts.query
        if strip_libpq and query:
            kept = [
                (k, v)
                for k, v in parse_qsl(query, keep_blank_values=True)
                if k not in self._LIBPQ_ONLY_QUERY_KEYS
            ]
            query = urlencode(kept)
        return urlunsplit((scheme, parts.netloc, parts.path, query, parts.fragment))

    @property
    def sqlalchemy_database_uri(self) -> str:
        """Async SQLAlchemy URL (asyncpg). libpq-only params are stripped; SSL for
        those is applied via :attr:`async_connect_args`."""
        return self._url_with_driver("asyncpg", strip_libpq=True)

    @property
    def sqlalchemy_sync_uri(self) -> str:
        """Sync SQLAlchemy URL (psycopg2) for Alembic — libpq params kept as-is."""
        return self._url_with_driver("psycopg2", strip_libpq=False)

    def _db_sslmode(self) -> str | None:
        from urllib.parse import parse_qsl, urlsplit

        query = urlsplit(self._raw_database_url()).query
        for key, value in parse_qsl(query, keep_blank_values=True):
            if key == "sslmode":
                return value.lower()
        return None

    @property
    def async_connect_args(self) -> dict[str, object]:
        """asyncpg connect args derived from the URL — SSL and pooler safety.

        - ``sslmode`` (a libpq param asyncpg can't read from the URL) becomes an
          asyncpg ``ssl`` value, so hosted Postgres like Neon connects over TLS.
        - Poolers (PgBouncer transaction mode, e.g. Neon's ``-pooler`` host)
          break asyncpg's prepared-statement cache, so it is disabled there.
        """
        args: dict[str, object] = {}
        sslmode = self._db_sslmode()
        if sslmode in {"require", "verify-ca", "verify-full", "prefer", "allow"}:
            args["ssl"] = sslmode
        elif sslmode == "disable":
            args["ssl"] = False

        host = self._raw_database_url()
        if "neon.tech" in host or "pooler" in host:
            args["statement_cache_size"] = 0
        return args

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
