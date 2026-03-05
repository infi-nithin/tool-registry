"""MCP Server Registry Factory.

This module provides a factory pattern for creating and managing
MCPServerRegistryDB instances with proper session handling.
"""

import asyncio
from typing import Dict, Optional
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from service.mcp_service_db import MCPServerRegistryDB


class MCPRegistryFactory:
    """Factory for creating and managing MCP Server Registry instances.
    
    Provides centralized management of registry instances with proper
    session handling and lifecycle management. Supports singleton-like
    behavior per session to avoid duplicate instances.
    
    Attributes:
        _registries: Cache of registry instances by session ID
        _lock: Async lock for thread-safe operations
    
    Example:
        >>> factory = MCPRegistryFactory()
        >>> async with get_db_session() as session:
        ...     registry = await factory.get_registry(session)
        ...     await registry.initialize_main_server()
    """

    def __init__(self):
        """Initialize the factory with an empty registry cache."""
        self._registries: Dict[int, MCPServerRegistryDB] = {}
        self._lock = asyncio.Lock()

    async def get_registry(self, session: AsyncSession) -> MCPServerRegistryDB:
        """Get or create a registry instance for the given session.
        
        Creates a new MCPServerRegistryDB instance if one doesn't exist
        for this session, otherwise returns the cached instance.
        
        Args:
            session: SQLAlchemy AsyncSession for database operations
            
        Returns:
            MCPServerRegistryDB instance bound to the session
        """
        session_id = id(session)
        
        async with self._lock:
            if session_id not in self._registries:
                registry = MCPServerRegistryDB(session)
                self._registries[session_id] = registry
            
            return self._registries[session_id]

    async def create_registry(self, session: AsyncSession) -> MCPServerRegistryDB:
        """Create a new registry instance regardless of cache.
        
        Use this method when you need a fresh registry instance
        even if one exists for the session.
        
        Args:
            session: SQLAlchemy AsyncSession for database operations
            
        Returns:
            New MCPServerRegistryDB instance bound to the session
        """
        registry = MCPServerRegistryDB(session)
        
        async with self._lock:
            session_id = id(session)
            self._registries[session_id] = registry
        
        return registry

    def release_registry(self, session: AsyncSession) -> bool:
        """Release a registry instance from the cache.
        
        Removes the registry instance associated with this session
        from the cache. This should be called when the session is closed.
        
        Args:
            session: SQLAlchemy AsyncSession that was used to create the registry
            
        Returns:
            True if a registry was found and removed, False otherwise
        """
        session_id = id(session)
        
        if session_id in self._registries:
            del self._registries[session_id]
            return True
        
        return False

    async def clear_cache(self) -> int:
        """Clear all cached registry instances.
        
        Returns:
            Number of registry instances that were cleared
        """
        async with self._lock:
            count = len(self._registries)
            self._registries.clear()
            return count

    def get_cached_count(self) -> int:
        """Get the number of cached registry instances.
        
        Returns:
            Number of registry instances currently in cache
        """
        return len(self._registries)


# Global factory instance for convenience
_factory: Optional[MCPRegistryFactory] = None


def get_factory() -> MCPRegistryFactory:
    """Get the global MCP registry factory instance.
    
    Returns:
        The global MCPRegistryFactory instance (creates one if needed)
    """
    global _factory
    if _factory is None:
        _factory = MCPRegistryFactory()
    return _factory


async def get_registry(session: AsyncSession) -> MCPServerRegistryDB:
    """Get a registry instance using the global factory.
    
    Convenience function that uses the global factory to get
    or create a registry instance for the given session.
    
    Args:
        session: SQLAlchemy AsyncSession for database operations
        
    Returns:
        MCPServerRegistryDB instance bound to the session
        
    Example:
        >>> async with get_db_session() as session:
        ...     registry = await get_registry(session)
        ...     servers = await registry.list_servers()
    """
    factory = get_factory()
    return await factory.get_registry(session)


@asynccontextmanager
async def registry_context(session: AsyncSession):
    """Context manager for registry lifecycle management.
    
    Provides automatic cleanup of the registry instance when done.
    
    Args:
        session: SQLAlchemy AsyncSession for database operations
        
    Yields:
        MCPServerRegistryDB instance bound to the session
        
    Example:
        >>> async with get_db_session() as session:
        ...     async with registry_context(session) as registry:
        ...         await registry.initialize_main_server()
        ...         # Registry will be released from cache on exit
    """
    factory = get_factory()
    registry = await factory.get_registry(session)
    
    try:
        yield registry
    finally:
        factory.release_registry(session)


async def initialize_registry_with_main_server(
    session: AsyncSession,
    server_name: str = "main-mcp-server",
    restore_servers: bool = True,
) -> MCPServerRegistryDB:
    """Initialize a registry with main server and optionally restore servers.
    
    Convenience function for full registry initialization.
    
    Args:
        session: SQLAlchemy AsyncSession for database operations
        server_name: Name for the main MCP server
        restore_servers: Whether to restore active servers from database
        
    Returns:
        Fully initialized MCPServerRegistryDB instance
        
    Example:
        >>> async with get_db_session() as session:
        ...     registry = await initialize_registry_with_main_server(session)
        ...     # Registry is ready to use with main server initialized
    """
    registry = await get_registry(session)
    
    # Initialize main server
    await registry.initialize_main_server(name=server_name)
    
    # Restore servers from database if requested
    if restore_servers:
        restored_count, errors = await registry.restore_servers_from_db()
        if errors:
            # Log errors but don't fail initialization
            for error in errors:
                print(f"Warning: {error}")
    
    return registry
