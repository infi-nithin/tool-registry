"""Initial migration - create MCP tables

Revision ID: 001_initial
Revises: 
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create mcp_servers table
    op.create_table(
        'mcp_servers',
        sa.Column('server_name', sa.String(255), primary_key=True),
        sa.Column('spec_link', sa.String(512), nullable=False),
        sa.Column('base_url', sa.String(512), nullable=False),
        sa.Column('headers', postgresql.JSONB, nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('status', sa.Enum('ACTIVE', 'DISABLED', 'ERROR', name='mcpserverstatusenum'), nullable=False, server_default='ACTIVE'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.text('now()')),
    )

    # Create mcp_server_tags table
    op.create_table(
        'mcp_server_tags',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('server_name', sa.String(255), sa.ForeignKey('mcp_servers.server_name', ondelete='CASCADE'), nullable=False),
        sa.Column('tag_name', sa.String(255), nullable=False),
        sa.Column('status', sa.Enum('ACTIVE', 'DISABLED', 'ERROR', name='mcpserverstatusenum'), nullable=False, server_default='ACTIVE'),
    )
    op.create_index('idx_mcp_server_tags_unique', 'mcp_server_tags', ['server_name', 'tag_name'], unique=True)

    # Create mcp_audit_log table
    op.create_table(
        'mcp_audit_log',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('server_name', sa.String(255), nullable=True),
        sa.Column('tag_id', sa.Integer, nullable=True),
        sa.Column('action', sa.Enum('MOUNT', 'UNMOUNT', 'ENABLE', 'DISABLE', 'REMOVE', name='auditactionenum'), nullable=False),
        sa.Column('status_before', sa.String(50), nullable=True),
        sa.Column('status_after', sa.String(50), nullable=True),
        sa.Column('performed_at', sa.DateTime, nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('idx_mcp_audit_log_performed_at', 'mcp_audit_log', ['performed_at'])


def downgrade() -> None:
    op.drop_index('idx_mcp_audit_log_performed_at', table_name='mcp_audit_log')
    op.drop_table('mcp_audit_log')
    op.drop_index('idx_mcp_server_tags_unique', table_name='mcp_server_tags')
    op.drop_table('mcp_server_tags')
    op.drop_table('mcp_servers')

    # Drop enums
    op.execute('DROP TYPE IF EXISTS auditactionenum')
    op.execute('DROP TYPE IF EXISTS mcpserverstatusenum')
