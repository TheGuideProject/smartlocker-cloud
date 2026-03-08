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
    import logging
    logger = logging.getLogger("smartlocker.db")

    async with engine.begin() as conn:
        # Check if maintenance_charts needs schema update
        # by checking if vessel_id column exists
        try:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'maintenance_charts' AND column_name = 'vessel_id'"
            ))
            has_vessel_id = result.scalar_one_or_none() is not None
        except Exception:
            has_vessel_id = True  # Table might not exist yet

        if not has_vessel_id:
            logger.info("Rebuilding maintenance_charts table with new schema...")
            # Drop old tables (cascade) and let create_all rebuild them
            try:
                await conn.execute(text("DROP TABLE IF EXISTS coating_layers CASCADE"))
                await conn.execute(text("DROP TABLE IF EXISTS coating_cycles CASCADE"))
                await conn.execute(text("DROP TABLE IF EXISTS maintenance_charts CASCADE"))
                logger.info("  Dropped old maintenance tables")
                # Recreate with new schema
                await conn.run_sync(Base.metadata.create_all)
                logger.info("  Recreated maintenance tables with new schema")
            except Exception as e:
                logger.error(f"  Migration error: {e}")
        else:
            # Fix column types and constraints that changed after initial creation
            fixes = [
                "ALTER TABLE maintenance_charts ALTER COLUMN version TYPE VARCHAR(20) USING version::VARCHAR",
                "ALTER TABLE maintenance_charts ALTER COLUMN version DROP NOT NULL",
            ]
            for sql in fixes:
                try:
                    await conn.execute(text(sql))
                except Exception:
                    pass  # Already applied
            logger.info("  Maintenance chart schema fixes applied")
