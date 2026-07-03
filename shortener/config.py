import os
from dataclasses import dataclass


def _bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Config:
    app_env: str = "development"
    port: int = 8080
    base_url: str = "http://localhost:8080"
    database_backend: str = "sqlite"
    database_path: str = "data/shortener.db"
    database_url: str | None = None
    redis_url: str | None = None
    rate_limit_backend: str = "database"
    api_keys: tuple[str, ...] = ("dev-api-key",)
    idempotency_ttl_hours: int = 24
    create_rate_limit: str = "60/hour"
    metadata_rate_limit: str = "300/hour"
    redirect_rate_limit: str = "1000/minute"
    max_url_length: int = 2048
    max_metadata_bytes: int = 4096
    validation_enabled: bool = True
    validation_timeout_ms: int = 5000
    validation_max_attempts: int = 5
    log_level: str = "info"
    log_destination_urls: bool = False
    service_mode: str = "api"

    @classmethod
    def from_env(cls) -> "Config":
        api_keys = tuple(
            key.strip()
            for key in os.getenv("API_KEYS", "dev-api-key").split(",")
            if key.strip()
        )
        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            port=_int("PORT", 8080),
            base_url=os.getenv("BASE_URL", "http://localhost:8080").rstrip("/"),
            database_backend=os.getenv("DATABASE_BACKEND", "sqlite"),
            database_path=os.getenv("DATABASE_PATH", "data/shortener.db"),
            database_url=os.getenv("DATABASE_URL"),
            redis_url=os.getenv("REDIS_URL"),
            rate_limit_backend=os.getenv("RATE_LIMIT_BACKEND", "database"),
            api_keys=api_keys or ("dev-api-key",),
            idempotency_ttl_hours=_int("IDEMPOTENCY_TTL_HOURS", 24),
            create_rate_limit=os.getenv("CREATE_RATE_LIMIT", "60/hour"),
            metadata_rate_limit=os.getenv("METADATA_RATE_LIMIT", "300/hour"),
            redirect_rate_limit=os.getenv("REDIRECT_RATE_LIMIT", "1000/minute"),
            max_url_length=_int("MAX_URL_LENGTH", 2048),
            max_metadata_bytes=_int("MAX_METADATA_BYTES", 4096),
            validation_enabled=_bool(os.getenv("VALIDATION_ENABLED"), True),
            validation_timeout_ms=_int("VALIDATION_TIMEOUT_MS", 5000),
            validation_max_attempts=_int("VALIDATION_MAX_ATTEMPTS", 5),
            log_level=os.getenv("LOG_LEVEL", "info"),
            log_destination_urls=_bool(os.getenv("LOG_DESTINATION_URLS"), False),
            service_mode=os.getenv("SERVICE_MODE", "api"),
        )
