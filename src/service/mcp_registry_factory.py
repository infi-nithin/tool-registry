import asyncio
from typing import Optional

from service.mcp_service import MCPServerRegistry


class MCPRegistryFactory:
    def __init__(self):
        self._registry: Optional[MCPServerRegistry] = None
        self._lock = asyncio.Lock()

    async def get_registry(self) -> MCPServerRegistry:
        async with self._lock:
            if self._registry is None:
                self._registry = MCPServerRegistry()
            return self._registry

    async def create_registry(self) -> MCPServerRegistry:
        async with self._lock:
            self._registry = MCPServerRegistry()
        return self._registry

    def release_registry(self) -> bool:
        if self._registry is not None:
            self._registry = None
            return True
        return False

    async def clear_cache(self) -> int:
        async with self._lock:
            count = 1 if self._registry is not None else 0
            self._registry = None
            return count

    def get_cached_count(self) -> int:
        return 1 if self._registry is not None else 0


_factory: Optional[MCPRegistryFactory] = None


def get_factory() -> MCPRegistryFactory:
    global _factory
    if _factory is None:
        _factory = MCPRegistryFactory()
    return _factory


async def get_registry() -> MCPServerRegistry:
    factory = get_factory()
    return await factory.get_registry()


async def initialize_registry_with_main_server(
    server_name: str = "main-mcp-server",
) -> MCPServerRegistry:
    registry = await get_registry()
    await registry.initialize_main_server(name=server_name)
    return registry
