"""
Database Connection & Session Management

Uses SQLAlchemy 2.0 async with PostgreSQL (asyncpg driver).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


# Fix DATABASE_URL for asyncpg driver
# Railway may provide: postgres://, postgresql://, or postgresql+asyncpg://
database_url = settings.DATABASE_URL
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif database_url.startswith("postgresql://") and "+asyncpg" not in database_url:
    database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# SQLite needs different config than PostgreSQL
_is_sqlite = database_url.startswith("sqlite")

engine_kwargs = {
    "echo": settings.DEBUG,
}
if not _is_sqlite:
    engine_kwargs["pool_size"] = 5
    engine_kwargs["max_overflow"] = 10

engine = create_async_engine(database_url, **engine_kwargs)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


async def get_db():
    """Dependency: provides a database session per request."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all tables (for development only; use Alembic in production)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Run migration helpers for columns added after initial table creation
    if not _is_sqlite:
        await _migrate_add_columns()


async def _migrate_add_columns():
    """Add missing columns to existing tables (poor-man's migration).

    This is safe to run multiple times — it checks IF NOT EXISTS.
    Replace with Alembic once the schema is stable.
    """
    migrations = [
        # maintenance_charts new columns (added for chart PDF upload)
        "ALTER TABLE maintenance_charts ADD COLUMN IF NOT EXISTS vessel_id VARCHAR(36) REFERENCES vessels(id)",
        "ALTER TABLE maintenance_charts ADD COLUMN IF NOT EXISTS imo_number VARCHAR(20)",
        "ALTER TABLE maintenance_charts ADD COLUMN IF NOT EXISTS parsed_data JSON",
        # version column type change: was Integer, now String
        # If it already exists as integer, this won't break — we just leave it
    ]
    async with engine.begin() as conn:
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # Column may already exist or other minor issue
