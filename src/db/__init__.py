"""Database package for MCP Server Registry.

This package contains database models, connection management,
and session handling for PostgreSQL persistence.
"""

from .models import (
    Base,
    MCPServer,
    MCPServerTag,
    MCPAuditLog,
    MCPServerStatusEnum,
    AuditActionEnum,
)
from .database import (
    init_db,
    get_engine,
    get_session,
    get_session_context,
    run_alembic_migrations,
    close_db,
)

__all__ = [
    # Models
    "Base",
    "MCPServer",
    "MCPServerTag",
    "MCPAuditLog",
    "MCPServerStatusEnum",
    "AuditActionEnum",
    # Database
    "init_db",
    "get_engine",
    "get_session",
    "get_session_context",
    "run_alembic_migrations",
    "close_db",
]
