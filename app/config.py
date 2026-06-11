"""
Application Configuration

Reads settings from environment variables.
On Railway, DATABASE_URL is auto-provided.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/smartlocker"

    # Auth
    SECRET_KEY: str = "dev-secret-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    ALGORITHM: str = "HS256"

    # App
    ENVIRONMENT: str = "development"
    APP_NAME: str = "SmartLocker Cloud"
    DEBUG: bool = False

    # Standalone client portal (separate Railway service).
    # When set, /client/* requests on this app redirect there.
    CLIENT_PORTAL_URL: str = ""

    # File uploads
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # Admin defaults
    ADMIN_EMAIL: str = "admin@ppg.com"
    ADMIN_PASSWORD: str = "Smartlocker2026"

    # Product Equivalence integration (technical datasheets: coverage m²/L,
    # volume solids, density, plus a grounded technical bot). The cloud is
    # the bridge: it holds the service key and caches results so devices
    # never see the key and keep working when Product Equivalence is down.
    # NOTE: the apex mip-pe.online 404s — the app is served on the www host.
    PRODUCT_EQUIVALENCE_URL: str = "https://www.mip-pe.online"
    SMARTLOCKER_SERVICE_KEY: str = ""  # must match the key set on Product Equivalence
    PRODUCT_SPEC_CACHE_TTL_HOURS: int = 168  # 7 days
    PRODUCT_EQUIVALENCE_TIMEOUT_SECONDS: float = 15.0

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
