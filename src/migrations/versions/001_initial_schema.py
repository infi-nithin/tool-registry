"""Initial schema - Create MCP Server Registry and Audit Trail tables.

Revision ID: 001
Revises:
Create Date: 2026-03-04 20:00:00.000000

This migration creates the initial database schema for the MCP Server Registry
including:
- ENUM types for status and operation tracking
- MCP Server Registry tables (mcp_servers, mcp_server_tags)
- Audit Trail tables (audit_sessions, audit_correlations, audit_tool_invocations,
  audit_server_operations)
- All indexes and constraints as per the schema design

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def table_exists(table_name: str) -> bool:
    """Check if a table already exists."""
    from sqlalchemy import inspect
    from alembic import context
    
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def enum_exists(enum_name: str) -> bool:
    """Check if an ENUM type already exists in PostgreSQL."""
    from sqlalchemy import text
    
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :enum_name)"
        ),
        {"enum_name": enum_name}
    )
    return result.scalar()


def index_exists(table_name: str, index_name: str) -> bool:
    """Check if an index already exists."""
    from sqlalchemy import inspect
    
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = inspector.get_indexes(table_name)
    return any(idx["name"] == index_name for idx in indexes)


def upgrade() -> None:
    """Apply initial schema migration."""
    # Create ENUM types
    _create_enums()
    
    # Create MCP Server Registry tables
    _create_mcp_servers_table()
    _create_mcp_server_tags_table()
    
    # Create Audit Trail tables
    _create_audit_sessions_table()
    _create_audit_correlations_table()
    _create_audit_tool_invocations_table()
    _create_audit_server_operations_table()
    
    # Create indexes
    _create_mcp_servers_indexes()
    _create_mcp_server_tags_indexes()
    _create_audit_indexes()


def downgrade() -> None:
    """Revert initial schema migration.
    
    Drop tables in reverse order to respect foreign key constraints.
    """
    # Drop audit tables first (respect FK constraints)
    op.drop_table("audit_server_operations", checkfirst=True)
    op.drop_table("audit_tool_invocations", checkfirst=True)
    op.drop_table("audit_correlations", checkfirst=True)
    op.drop_table("audit_sessions", checkfirst=True)
    
    # Drop MCP Server tables
    op.drop_table("mcp_server_tags", checkfirst=True)
    op.drop_table("mcp_servers", checkfirst=True)
    
    # Drop ENUM types
    op.execute("DROP TYPE IF EXISTS server_operation_type CASCADE")
    op.execute("DROP TYPE IF EXISTS audit_operation_status CASCADE")
    op.execute("DROP TYPE IF EXISTS mcp_server_status CASCADE")


# =============================================================================
# ENUM Types
# =============================================================================

def _create_enums() -> None:
    """Create PostgreSQL ENUM types."""
    # mcp_server_status ENUM
    if not enum_exists("mcp_server_status"):
        mcp_server_status = postgresql.ENUM(
            "ACTIVE", "DISABLED", "ERROR",
            name="mcp_server_status"
        )
        mcp_server_status.create(op.get_bind())
    
    # audit_operation_status ENUM
    if not enum_exists("audit_operation_status"):
        audit_operation_status = postgresql.ENUM(
            "STARTED", "SUCCESS", "FAILURE", "TIMEOUT",
            name="audit_operation_status"
        )
        audit_operation_status.create(op.get_bind())
    
    # server_operation_type ENUM
    if not enum_exists("server_operation_type"):
        server_operation_type = postgresql.ENUM(
            "MOUNT", "UNMOUNT", "ENABLE", "DISABLE", "REMOVE", "UPDATE_CONFIG",
            name="server_operation_type"
        )
        server_operation_type.create(op.get_bind())


# =============================================================================
# MCP Server Registry Tables
# =============================================================================

def _create_mcp_servers_table() -> None:
    """Create the mcp_servers table."""
    if table_exists("mcp_servers"):
        return
    
    op.create_table(
        "mcp_servers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "server_name",
            sa.String(255),
            nullable=False,
        ),
        sa.Column(
            "spec_link",
            sa.Text,
            nullable=False,
        ),
        sa.Column(
            "base_url",
            sa.String(2048),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "headers",
            postgresql.JSONB,
            server_default="{}",
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "ACTIVE", "DISABLED", "ERROR",
                name="mcp_server_status",
                create_type=False,
            ),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column(
            "tool_count",
            sa.Integer,
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "is_deleted",
            sa.Boolean,
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "version",
            sa.Integer,
            server_default="1",
            nullable=False,
        ),
        # Primary key constraint
        sa.PrimaryKeyConstraint("id"),
        # Unique constraint on server_name
        sa.UniqueConstraint("server_name"),
        # Check constraints
        sa.CheckConstraint(
            "base_url ~ '^https?://'",
            name="chk_base_url_format"
        ),
        sa.CheckConstraint(
            "tool_count >= 0",
            name="chk_tool_count_non_negative"
        ),
    )


def _create_mcp_server_tags_table() -> None:
    """Create the mcp_server_tags table."""
    if table_exists("mcp_server_tags"):
        return
    
    op.create_table(
        "mcp_server_tags",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tag_name",
            sa.String(255),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "ACTIVE", "DISABLED", "ERROR",
                name="mcp_server_status",
                create_type=False,
            ),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "is_deleted",
            sa.Boolean,
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "version",
            sa.Integer,
            server_default="1",
            nullable=False,
        ),
        # Primary key constraint
        sa.PrimaryKeyConstraint("id"),
        # Unique constraint on server_id + tag_name
        sa.UniqueConstraint(
            "server_id", "tag_name",
            name="uq_server_tag"
        ),
        # Foreign key constraint
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["mcp_servers.id"],
            ondelete="CASCADE",
        ),
    )


# =============================================================================
# Audit Trail Tables
# =============================================================================

def _create_audit_sessions_table() -> None:
    """Create the audit_sessions table."""
    if table_exists("audit_sessions"):
        return
    
    op.create_table(
        "audit_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(255),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "api_key_id",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "client_ip",
            postgresql.INET,
            nullable=True,
        ),
        sa.Column(
            "user_agent",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "ended_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB,
            server_default="{}",
            nullable=True,
        ),
        # Primary key constraint
        sa.PrimaryKeyConstraint("id"),
        # Unique constraint on session_id
        sa.UniqueConstraint("session_id"),
    )


def _create_audit_correlations_table() -> None:
    """Create the audit_correlations table."""
    if table_exists("audit_correlations"):
        return
    
    op.create_table(
        "audit_correlations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "correlation_id",
            sa.String(255),
            nullable=False,
        ),
        sa.Column(
            "purpose",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "request_context",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB,
            server_default="{}",
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Primary key constraint
        sa.PrimaryKeyConstraint("id"),
        # Unique constraint on correlation_id
        sa.UniqueConstraint("correlation_id"),
    )


def _create_audit_tool_invocations_table() -> None:
    """Create the audit_tool_invocations table."""
    if table_exists("audit_tool_invocations"):
        return
    
    op.create_table(
        "audit_tool_invocations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "correlation_id",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "tool_name",
            sa.String(500),
            nullable=False,
        ),
        sa.Column(
            "arguments",
            postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "result_status",
            postgresql.ENUM(
                "STARTED", "SUCCESS", "FAILURE", "TIMEOUT",
                name="audit_operation_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "result_summary",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "duration_ms",
            sa.Float,
            nullable=False,
        ),
        sa.Column(
            "error_type",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "error_message",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "stack_trace",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "tags",
            postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "invoked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "partition_date",
            sa.Date,
            server_default=sa.func.current_date(),
            nullable=False,
        ),
        # Primary key constraint
        sa.PrimaryKeyConstraint("id"),
        # Foreign key constraints (preserve audit history with ON DELETE SET NULL)
        sa.ForeignKeyConstraint(
            ["correlation_id"],
            ["audit_correlations.correlation_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["audit_sessions.session_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["mcp_servers.id"],
            ondelete="SET NULL",
        ),
        # Check constraints
        sa.CheckConstraint(
            "duration_ms >= 0",
            name="chk_audit_tool_duration_non_negative"
        ),
    )


def _create_audit_server_operations_table() -> None:
    """Create the audit_server_operations table."""
    if table_exists("audit_server_operations"):
        return
    
    op.create_table(
        "audit_server_operations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "correlation_id",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            sa.String(255),
            nullable=True,
        ),
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "operation_type",
            postgresql.ENUM(
                "MOUNT", "UNMOUNT", "ENABLE", "DISABLE", "REMOVE", "UPDATE_CONFIG",
                name="server_operation_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "operation_details",
            postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "result_status",
            postgresql.ENUM(
                "STARTED", "SUCCESS", "FAILURE", "TIMEOUT",
                name="audit_operation_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "error_message",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "duration_ms",
            sa.Float,
            nullable=False,
        ),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "partition_date",
            sa.Date,
            server_default=sa.func.current_date(),
            nullable=False,
        ),
        # Primary key constraint
        sa.PrimaryKeyConstraint("id"),
        # Foreign key constraints
        sa.ForeignKeyConstraint(
            ["correlation_id"],
            ["audit_correlations.correlation_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["audit_sessions.session_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["mcp_servers.id"],
            ondelete="SET NULL",
        ),
        # Check constraints
        sa.CheckConstraint(
            "duration_ms >= 0",
            name="chk_audit_server_op_duration_non_negative"
        ),
    )


# =============================================================================
# Indexes
# =============================================================================

def _create_mcp_servers_indexes() -> None:
    """Create indexes for mcp_servers table."""
    table_name = "mcp_servers"
    
    # Index on server_name for active records
    if not index_exists(table_name, "idx_mcp_servers_name"):
        op.create_index(
            "idx_mcp_servers_name",
            table_name,
            ["server_name"],
            postgresql_where=sa.text("is_deleted = false"),
        )
    
    # Index on status for active records
    if not index_exists(table_name, "idx_mcp_servers_status"):
        op.create_index(
            "idx_mcp_servers_status",
            table_name,
            ["status"],
            postgresql_where=sa.text("is_deleted = false"),
        )
    
    # Compound index for listing active servers
    if not index_exists(table_name, "idx_mcp_servers_active"):
        op.create_index(
            "idx_mcp_servers_active",
            table_name,
            ["status", "created_at"],
            postgresql_where=sa.text("is_deleted = false"),
        )
    
    # Index for soft deleted records
    if not index_exists(table_name, "idx_mcp_servers_deleted"):
        op.create_index(
            "idx_mcp_servers_deleted",
            table_name,
            ["is_deleted", "deleted_at"],
            postgresql_where=sa.text("is_deleted = true"),
        )


def _create_mcp_server_tags_indexes() -> None:
    """Create indexes for mcp_server_tags table."""
    table_name = "mcp_server_tags"
    
    # Foreign key index on server_id
    if not index_exists(table_name, "idx_mcp_server_tags_server"):
        op.create_index(
            "idx_mcp_server_tags_server",
            table_name,
            ["server_id"],
        )
    
    # Index on tag_name
    if not index_exists(table_name, "idx_mcp_server_tags_name"):
        op.create_index(
            "idx_mcp_server_tags_name",
            table_name,
            ["tag_name"],
        )
    
    # Index for active tags by server
    if not index_exists(table_name, "idx_mcp_server_tags_active"):
        op.create_index(
            "idx_mcp_server_tags_active",
            table_name,
            ["server_id", "status"],
            postgresql_where=sa.text("is_deleted = false"),
        )


def _create_audit_indexes() -> None:
    """Create indexes for audit tables."""
    
    # audit_sessions indexes
    if not index_exists("audit_sessions", "idx_audit_sessions_session_id"):
        op.create_index(
            "idx_audit_sessions_session_id",
            "audit_sessions",
            ["session_id"],
        )
    
    if not index_exists("audit_sessions", "idx_audit_sessions_user_id"):
        op.create_index(
            "idx_audit_sessions_user_id",
            "audit_sessions",
            ["user_id"],
        )
    
    if not index_exists("audit_sessions", "idx_audit_sessions_started"):
        op.create_index(
            "idx_audit_sessions_started",
            "audit_sessions",
            ["started_at"],
        )
    
    # audit_correlations indexes
    if not index_exists("audit_correlations", "idx_audit_correlations_id"):
        op.create_index(
            "idx_audit_correlations_id",
            "audit_correlations",
            ["correlation_id"],
        )
    
    # audit_tool_invocations indexes
    _create_audit_tool_invocations_indexes()
    
    # audit_server_operations indexes
    _create_audit_server_operations_indexes()


def _create_audit_tool_invocations_indexes() -> None:
    """Create indexes for audit_tool_invocations table."""
    table_name = "audit_tool_invocations"
    
    indexes = [
        ("idx_audit_tool_invocations_session", ["session_id"]),
        ("idx_audit_tool_invocations_correlation", ["correlation_id"]),
        ("idx_audit_tool_invocations_server", ["server_id"]),
        ("idx_audit_tool_invocations_tool", ["tool_name", "invoked_at"]),
        ("idx_audit_tool_invocations_time", ["invoked_at"]),
        ("idx_audit_tool_invocations_status", ["result_status"]),
        ("idx_audit_tool_invocations_error_type", ["error_type"]),
    ]
    
    for index_name, columns in indexes:
        if not index_exists(table_name, index_name):
            op.create_index(index_name, table_name, columns)
    
    # Partial index for slow invocations
    if not index_exists(table_name, "idx_audit_tool_invocations_duration"):
        op.create_index(
            "idx_audit_tool_invocations_duration",
            table_name,
            ["duration_ms"],
            postgresql_where=sa.text("duration_ms > 1000"),
        )
    
    # Partial index for failed invocations
    if not index_exists(table_name, "idx_audit_tool_invocations_error"):
        op.create_index(
            "idx_audit_tool_invocations_error",
            table_name,
            ["result_status", "error_type"],
            postgresql_where=sa.text("result_status = 'FAILURE'"),
        )


def _create_audit_server_operations_indexes() -> None:
    """Create indexes for audit_server_operations table."""
    table_name = "audit_server_operations"
    
    indexes = [
        ("idx_audit_server_ops_session", ["session_id"]),
        ("idx_audit_server_ops_correlation", ["correlation_id"]),
        ("idx_audit_server_ops_server", ["server_id"]),
        ("idx_audit_server_ops_executed", ["executed_at"]),
        ("idx_audit_server_ops_status", ["result_status"]),
    ]
    
    for index_name, columns in indexes:
        if not index_exists(table_name, index_name):
            op.create_index(index_name, table_name, columns)
    
    # Index for operation type queries
    if not index_exists(table_name, "idx_audit_server_ops_type"):
        op.create_index(
            "idx_audit_server_ops_type",
            table_name,
            ["operation_type", "executed_at"],
        )
