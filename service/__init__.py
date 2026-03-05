"""Service layer for MCP Server Registry.

This module provides services for managing MCP servers, including:
- MCPServerRegistry: Legacy in-memory registry (kept for backward compatibility)
- MCPServerRegistryDB: Database-backed registry with persistence
- MCPRegistryFactory: Factory for creating registry instances
- AuditLogger: Database audit logging functionality
"""

# Legacy in-memory registry (kept for backward compatibility)
from service.mcp_service import (
    MCPServerRegistry,
    get_registry,
    initialize_main_server,
    get_main_server,
    mount_mcp_server,
    unmount_mcp_server,
    enable_mcp_server,
    remove_mcp_server,
)

# New database-backed registry
from service.mcp_service_db import MCPServerRegistryDB

# Factory for registry instances
from service.mcp_registry_factory import (
    MCPRegistryFactory,
    get_factory,
    get_registry as get_registry_db,
    registry_context,
    initialize_registry_with_main_server,
)

# Audit logging
from service.audit_logger import (
    AuditLogger,
    get_audit_logger,
)

__all__ = [
    # Legacy in-memory registry (backward compatibility)
    "MCPServerRegistry",
    "get_registry",
    "initialize_main_server",
    "get_main_server",
    "mount_mcp_server",
    "unmount_mcp_server",
    "enable_mcp_server",
    "remove_mcp_server",
    # New database-backed registry
    "MCPServerRegistryDB",
    # Factory
    "MCPRegistryFactory",
    "get_factory",
    "get_registry_db",
    "registry_context",
    "initialize_registry_with_main_server",
    # Audit logging
    "AuditLogger",
    "get_audit_logger",
]
