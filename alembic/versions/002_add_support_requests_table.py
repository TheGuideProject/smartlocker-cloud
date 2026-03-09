"""Add support_requests table

Revision ID: d176b26560f2
Revises: 934d4022466a
Create Date: 2026-03-09
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'd176b26560f2'
down_revision = '934d4022466a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'support_requests',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('device_id', sa.String(100), sa.ForeignKey('locker_devices.device_id'), nullable=False),
        sa.Column('alarm_id', sa.String(100)),
        sa.Column('error_code', sa.String(10), nullable=False),
        sa.Column('error_title', sa.String(255)),
        sa.Column('severity', sa.String(20)),
        sa.Column('details', sa.Text()),
        sa.Column('user_name', sa.String(100)),
        sa.Column('status', sa.String(20), server_default='open'),
        sa.Column('resolution_notes', sa.Text()),
        sa.Column('resolved_by', sa.String(100)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_support_requests_device', 'support_requests', ['device_id'])
    op.create_index('ix_support_requests_status', 'support_requests', ['status'])


def downgrade() -> None:
    op.drop_index('ix_support_requests_status')
    op.drop_index('ix_support_requests_device')
    op.drop_table('support_requests')
