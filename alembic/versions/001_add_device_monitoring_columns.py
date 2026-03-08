"""Add device monitoring columns (driver_status, sensor_health, system_info, pending_admin_password).

Revision ID: 934d4022466a
Revises: (none - first migration)
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '934d4022466a'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add monitoring columns to locker_devices table."""
    # Add JSON columns for device monitoring
    op.add_column('locker_devices', sa.Column('driver_status', sa.JSON(), nullable=True))
    op.add_column('locker_devices', sa.Column('sensor_health', sa.JSON(), nullable=True))
    op.add_column('locker_devices', sa.Column('system_info', sa.JSON(), nullable=True))
    op.add_column('locker_devices', sa.Column('pending_admin_password', sa.String(255), nullable=True))


def downgrade() -> None:
    """Remove monitoring columns from locker_devices table."""
    op.drop_column('locker_devices', 'pending_admin_password')
    op.drop_column('locker_devices', 'system_info')
    op.drop_column('locker_devices', 'sensor_health')
    op.drop_column('locker_devices', 'driver_status')
