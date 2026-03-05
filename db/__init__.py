"""
Database package for MCP Server Registry.

This package provides PostgreSQL persistence for the MCP server registry
and comprehensive audit trail functionality.
"""

from db.config import DatabaseConfig
from db.connection import (
    engine,
    async_session_factory,
    get_db_session,
    health_check,
    close_connection,
)
from db.base import Base, BaseModel

# Import all models to ensure they are registered with the Base
from db.models.mcp_server import MCPServerDB, MCPServerTagDB, MCPServerStatus
from db.models.audit import (
    AuditCorrelation,
    AuditSessionDB,
    AuditToolInvocationDB,
    AuditServerOperationDB,
    AuditOperationStatus,
    ServerOperationType,
)

__all__ = [
    # Configuration
    "DatabaseConfig",
    # Connection
    "engine",
    "async_session_factory",
    "get_db_session",
    "health_check",
    "close_connection",
    # Base
    "Base",
    "BaseModel",
    # MCP Server Models
    "MCPServerDB",
    "MCPServerTagDB",
    "MCPServerStatus",
    # Audit Models
    "AuditCorrelation",
    "AuditSessionDB",
    "AuditToolInvocationDB",
    "AuditServerOperationDB",
    # Enums
    "AuditOperationStatus",
    "ServerOperationType",
]  