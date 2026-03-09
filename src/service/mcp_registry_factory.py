"""MCP Server Registry Factory.

This module provides a factory pattern for creating and managing
MCPServerRegistry instances with in-memory storage.
"""

import asyncio
from typing import Optional

from service.mcp_service import MCPServerRegistry


class MCPRegistryFactory:
    """Factory for creating and managing MCP Server Registry instances.

    Provides centralized management of registry instances with proper
    lifecycle management. Uses singleton pattern for in-memory storage.

    Attributes:
        _registry: Singleton registry instance
        _lock: Async lock for thread-safe operations
    """

    def __init__(self):
        """Initialize the factory with an empty registry cache."""
        self._registry: Optional[MCPServerRegistry] = None
        self._lock = asyncio.Lock()

    async def get_registry(self) -> MCPServerRegistry:
        """Get or create a registry instance.

        Creates a new MCPServerRegistry instance if one doesn't exist,
        otherwise returns the cached instance.

        Returns:
            MCPServerRegistry instance
        """
        async with self._lock:
            if self._registry is None:
                self._registry = MCPServerRegistry()
            return self._registry

    async def create_registry(self) -> MCPServerRegistry:
        """Create a new registry instance regardless of cache.

        Use this method when you need a fresh registry instance
        even if one exists.

        Returns:
            New MCPServerRegistry instance
        """
        async with self._lock:
            self._registry = MCPServerRegistry()
        return self._registry

    def release_registry(self) -> bool:
        """Release the registry instance from the cache.

        Returns:
            True if a registry was found and removed, False otherwise
        """
        if self._registry is not None:
            self._registry = None
            return True
        return False

    async def clear_cache(self) -> int:
        """Clear the cached registry instance.

        Returns:
            Number of registry instances that were cleared (0 or 1)
        """
        async with self._lock:
            count = 1 if self._registry is not None else 0
            self._registry = None
            return count

    def get_cached_count(self) -> int:
        """Get the number of cached registry instances.

        Returns:
            Number of registry instances currently in cache (0 or 1)
        """
        return 1 if self._registry is not None else 0


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


async def get_registry() -> MCPServerRegistry:
    """Get a registry instance using the global factory.

    Convenience function that uses the global factory to get
    or create a registry instance.

    Returns:
        MCPServerRegistry instance

    Example:
        >>> registry = await get_registry()
        >>> servers = await registry.list_servers()
    """
    factory = get_factory()
    return await factory.get_registry()


async def initialize_registry_with_main_server(
    server_name: str = "main-mcp-server",
) -> MCPServerRegistry:
    """Initialize a registry with main server.

    Convenience function for full registry initialization.

    Args:
        server_name: Name for the main MCP server

    Returns:
        Fully initialized MCPServerRegistry instance

    Example:
        >>> registry = await initialize_registry_with_main_server()
        >>> # Registry is ready to use with main server initialized
    """
    registry = await get_registry()

    # Initialize main server
    await registry.initialize_main_server(name=server_name)

    return registry
