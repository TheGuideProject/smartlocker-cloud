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

        # Create sensor_health_logs table if not exists
        try:
            result = await conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name = 'sensor_health_logs'"
            ))
            if not result.scalar_one_or_none():
                await conn.execute(text("""
                    CREATE TABLE sensor_health_logs (
                        id SERIAL PRIMARY KEY,
                        device_id VARCHAR(36) NOT NULL REFERENCES locker_devices(id),
                        timestamp TIMESTAMP NOT NULL,
                        sensor VARCHAR(50) NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        message TEXT DEFAULT '',
                        value TEXT DEFAULT '',
                        received_at TIMESTAMP DEFAULT now()
                    )
                """))
                await conn.execute(text(
                    "CREATE INDEX idx_health_logs_device ON sensor_health_logs(device_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX idx_health_logs_timestamp ON sensor_health_logs(timestamp)"
                ))
                await conn.execute(text(
                    "CREATE INDEX idx_health_logs_sensor ON sensor_health_logs(sensor)"
                ))
                logger.info("  Created sensor_health_logs table")
        except Exception as e:
            logger.debug(f"  sensor_health_logs table check: {e}")

        # Create can_tracking table if not exists
        try:
            result = await conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name = 'can_tracking'"
            ))
            if not result.scalar_one_or_none():
                await conn.execute(text("""
                    CREATE TABLE can_tracking (
                        id VARCHAR(36) PRIMARY KEY,
                        tag_uid VARCHAR(100) NOT NULL,
                        lot_number VARCHAR(100),
                        product_id VARCHAR(36) REFERENCES products(id),
                        device_id VARCHAR(36) REFERENCES locker_devices(id),
                        status VARCHAR(30) DEFAULT 'in_stock',
                        slot_id VARCHAR(50),
                        weight_full_g FLOAT,
                        weight_current_g FLOAT,
                        weight_tare_g FLOAT,
                        can_size_ml INTEGER,
                        first_seen_at TIMESTAMP,
                        last_seen_at TIMESTAMP,
                        placed_at TIMESTAMP,
                        removed_at TIMESTAMP,
                        total_consumed_g FLOAT DEFAULT 0,
                        times_used INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT now(),
                        updated_at TIMESTAMP DEFAULT now()
                    )
                """))
                await conn.execute(text(
                    "CREATE INDEX idx_can_tracking_tag ON can_tracking(tag_uid)"
                ))
                await conn.execute(text(
                    "CREATE INDEX idx_can_tracking_device ON can_tracking(device_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX idx_can_tracking_status ON can_tracking(status)"
                ))
                logger.info("  Created can_tracking table")
        except Exception as e:
            logger.debug(f"  can_tracking table check: {e}")

        # Create inventory_adjustments table if not exists
        try:
            result = await conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name = 'inventory_adjustments'"
            ))
            if not result.scalar_one_or_none():
                await conn.execute(text("""
                    CREATE TABLE inventory_adjustments (
                        id VARCHAR(36) PRIMARY KEY,
                        device_id VARCHAR(36) REFERENCES locker_devices(id),
                        product_id VARCHAR(36) NOT NULL REFERENCES products(id),
                        adjustment_type VARCHAR(30) NOT NULL,
                        quantity_cans INTEGER DEFAULT 0,
                        quantity_liters FLOAT DEFAULT 0,
                        weight_g FLOAT DEFAULT 0,
                        lot_number VARCHAR(100),
                        notes TEXT,
                        source_document VARCHAR(255),
                        created_by VARCHAR(100) DEFAULT 'system',
                        created_at TIMESTAMP DEFAULT now()
                    )
                """))
                await conn.execute(text(
                    "CREATE INDEX idx_inv_adj_device ON inventory_adjustments(device_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX idx_inv_adj_product ON inventory_adjustments(product_id)"
                ))
                logger.info("  Created inventory_adjustments table")
        except Exception as e:
            logger.debug(f"  inventory_adjustments table check: {e}")

        # Create device_commands table if not exists
        try:
            result = await conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name = 'device_commands'"
            ))
            if not result.scalar_one_or_none():
                await conn.execute(text("""
                    CREATE TABLE device_commands (
                        id VARCHAR(36) PRIMARY KEY,
                        device_id VARCHAR(36) NOT NULL REFERENCES locker_devices(id),
                        command_type VARCHAR(50) NOT NULL,
                        payload JSONB DEFAULT '{}',
                        created_at TIMESTAMP DEFAULT now(),
                        delivered_at TIMESTAMP,
                        acked_at TIMESTAMP,
                        status VARCHAR(30) DEFAULT 'pending'
                    )
                """))
                await conn.execute(text(
                    "CREATE INDEX idx_device_commands_device ON device_commands(device_id)"
                ))
                logger.info("  Created device_commands table")
        except Exception as e:
            logger.debug(f"  device_commands table check: {e}")

        # Create product_barcodes table if not exists
        try:
            result = await conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name = 'product_barcodes'"
            ))
            if not result.scalar_one_or_none():
                await conn.execute(text("""
                    CREATE TABLE product_barcodes (
                        id VARCHAR(36) PRIMARY KEY,
                        barcode_data VARCHAR(500) NOT NULL UNIQUE,
                        product_id VARCHAR(36) NOT NULL REFERENCES products(id),
                        ppg_code VARCHAR(50) NOT NULL,
                        batch_number VARCHAR(100) NOT NULL,
                        product_name VARCHAR(255) NOT NULL,
                        color VARCHAR(100),
                        barcode_type VARCHAR(20) DEFAULT 'code128',
                        times_scanned INTEGER DEFAULT 0,
                        last_scanned_at TIMESTAMP,
                        created_by VARCHAR(100),
                        created_at TIMESTAMP DEFAULT now()
                    )
                """))
                await conn.execute(text(
                    "CREATE INDEX idx_product_barcodes_data ON product_barcodes(barcode_data)"
                ))
                await conn.execute(text(
                    "CREATE INDEX idx_product_barcodes_product ON product_barcodes(product_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX idx_product_barcodes_ppg ON product_barcodes(ppg_code)"
                ))
                logger.info("  Created product_barcodes table")
        except Exception as e:
            logger.debug(f"  product_barcodes table check: {e}")

        # Add device monitoring columns if missing
        monitoring_columns = {
            "driver_status": "JSONB",
            "sensor_health": "JSONB",
            "system_info": "JSONB",
            "pending_admin_password": "VARCHAR(255)",
        }
        for col_name, col_type in monitoring_columns.items():
            try:
                result = await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = 'locker_devices' AND column_name = '{col_name}'"
                ))
                if not result.scalar_one_or_none():
                    await conn.execute(text(
                        f"ALTER TABLE locker_devices ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info(f"  Added locker_devices.{col_name} column")
            except Exception as e:
                logger.debug(f"  Column {col_name} already exists or error: {e}")

        # OTA update columns
        ota_columns = {
            "pending_update_version": "VARCHAR(50)",
            "pending_update_branch": "VARCHAR(100)",
            "update_status": "VARCHAR(30)",
            "update_requested_at": "TIMESTAMP",
            "update_completed_at": "TIMESTAMP",
            "update_error": "VARCHAR(500)",
        }
        for col_name, col_type in ota_columns.items():
            try:
                await conn.execute(text(
                    f"ALTER TABLE locker_devices ADD COLUMN {col_name} {col_type}"
                ))
                logger.info(f"  Added column: locker_devices.{col_name}")
            except Exception:
                pass  # Column already exists
