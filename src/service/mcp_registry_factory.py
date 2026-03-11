"""
MCP Server Registry Factory.

Provides a singleton MCPServerRegistry instance while allowing
request-scoped database sessions.
"""

import asyncio
from typing import Optional
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession
from service.mcp_service import MCPServerRegistry


class MCPRegistryFactory:
    """Singleton factory for MCPServerRegistry."""

    def __init__(self):
        self._registry: Optional[MCPServerRegistry] = None
        self._lock = asyncio.Lock()

    async def get_registry(self, session: AsyncSession) -> MCPServerRegistry:
        """
        Return the global registry instance and attach the current DB session.
        """

        async with self._lock:

            # Create registry only once
            if self._registry is None:
                self._registry = MCPServerRegistry(session)
                await self._registry.initialize_main_server()

            # Inject session for current request
            self._registry.session = session

            return self._registry

# Global factory
_factory: Optional[MCPRegistryFactory] = None

def get_factory() -> MCPRegistryFactory:
    """Return global MCP registry factory."""
    global _factory

    if _factory is None:
        _factory = MCPRegistryFactory()

    return _factory


async def get_registry(session: AsyncSession) -> MCPServerRegistry:
    """Convenience helper used inside API routes."""
    factory = get_factory()
    return await factory.get_registry(session)


@asynccontextmanager
async def registry_context(session: AsyncSession):
    """Context manager for registry usage."""
    registry = await get_registry(session)

    try:
        yield registry
    finally:
        pass


async def initialize_registry_with_main_server(
    session: AsyncSession,
    server_name: str = "main-mcp-server",
    restore_servers: bool = True,
) -> MCPServerRegistry:
    """
    Initialize registry during application startup.
    """

    factory = get_factory()
    registry = await factory.get_registry(session)

    await registry.initialize_main_server(name=server_name)

    if restore_servers:
        restored_count, errors = await registry.restore_servers_from_db()

        print(f"Restored {restored_count} MCP servers")

        for error in errors:
            print(f"Warning: {error}")

    return registry
