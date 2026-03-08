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

    # File uploads
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # Admin defaults
    ADMIN_EMAIL: str = "admin@ppg.com"
    ADMIN_PASSWORD: str = "admin123"  # Change in production!

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
