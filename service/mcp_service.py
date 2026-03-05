from typing import Any, Dict, List, Optional, Set
import httpx
from fastmcp import FastMCP
from dto.models import (
    MCPServerConfig,
    MCPServerInfo,
    MCPServerStatus,
    ServerTagInfo,
    ToolInfo,
)
from aop_logging import log_method, patch_fastmcp_server


class MCPServerRegistry:
    _instance: Optional["MCPServerRegistry"] = None
    _main_server: Optional[FastMCP] = None
    _mounted_servers: Dict[str, FastMCP] = {}
    _server_configs: Dict[str, MCPServerConfig] = {}
    _server_tags: Dict[str, Dict[str, MCPServerStatus]] = {}
    _server_status: Dict[str, MCPServerStatus] = {}

    @log_method("MCPServerRegistry")
    async def initialize_main_server(self, name: str = "main-mcp-server") -> FastMCP:
        if self._main_server is None:
            self._main_server = FastMCP(name=name)
            # Patch for AOP logging
            self._main_server = patch_fastmcp_server(self._main_server)
        return self._main_server

    async def get_main_server(self) -> Optional[FastMCP]:
        return self._main_server

    def extract_openapi_tags(self, openapi_spec: Dict[str, Any]) -> List[str]:
        tags: Set[str] = set()

        root_tags = openapi_spec.get("tags", [])
        for tag in root_tags:
            if isinstance(tag, dict) and "name" in tag:
                tags.add(tag["name"])

        paths = openapi_spec.get("paths", {})
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, operation in methods.items():
                if not isinstance(operation, dict):
                    continue
                if method.lower() in [
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "trace",
                ]:
                    operation_tags = operation.get("tags", [])
                    tags.update(operation_tags)

        return sorted(list(tags))

    @log_method("MCPServerRegistry")
    async def mount_server(self, config: MCPServerConfig) -> tuple[bool, int, str]:
        if config.server_name in self._mounted_servers:
            return False, 0, f"Server '{config.server_name}' is already mounted"

        try:
            # Fetch OpenAPI spec
            response = httpx.get(config.spec_link, timeout=30.0)
            response.raise_for_status()
            openapi_spec = response.json()

            # Create async client with headers if provided
            client = httpx.AsyncClient(
                base_url=config.base_url, headers=config.headers or {}
            )

            # Create FastMCP server from OpenAPI spec
            sub_server = FastMCP.from_openapi(
                openapi_spec=openapi_spec,
                client=client,
                name=config.server_name,
            )

            tool_set = await sub_server.list_tools()
            # Count tools in the sub-server
            tool_count = len(tool_set)
            # Mount to main server
            if self._main_server:
                self._main_server.mount(sub_server)

            # Track the mounted server
            self._mounted_servers[config.server_name] = sub_server
            self._server_configs[config.server_name] = config
            extracted_tags = self.extract_openapi_tags(openapi_spec) or [config.server_name]
            # Initialize all tags as ACTIVE
            self._server_tags[config.server_name] = {
                tag: MCPServerStatus.ACTIVE for tag in extracted_tags
            }
            self._server_status[config.server_name] = MCPServerStatus.ACTIVE

            return (
                True,
                tool_count,
                f"Server '{config.server_name}' mounted successfully",
            )

        except httpx.HTTPError as e:
            return False, 0, f"Failed to fetch OpenAPI spec: {str(e)}"
        except Exception as e:
            return False, 0, f"Failed to mount server: {str(e)}"

    @log_method("MCPServerRegistry")
    async def unmount_server(
        self, server_name: str, tags: Optional[List[str]] = None
    ) -> tuple[bool, int, str]:
        if server_name not in self._mounted_servers:
            return False, 0, f"Server '{server_name}' is not mounted"

        try:
            # Get all tags for this server
            server_tags = self._server_tags.get(server_name, {})

            # Get tags to disable
            if tags:
                tags_to_disable = set(tags)
            else:
                tags_to_disable = set(server_tags.keys()) if server_tags else {server_name}

            tool_set = await self._main_server.list_tools()
            # Disable tools with these tags in main server
            disabled_count = 0
            if self._main_server:
                # Count tools with matching tags
                for tool in tool_set:
                    if hasattr(tool, "tags") and tool.tags:
                        if any(tag in tags_to_disable for tag in tool.tags):
                            disabled_count += 1

                # Disable the tags
                self._main_server.disable(tags=tags_to_disable)

            # Update tag statuses
            for tag in tags_to_disable:
                if tag in server_tags:
                    server_tags[tag] = MCPServerStatus.DISABLED

            # Update server status based on whether all tags are disabled
            all_disabled = all(
                status == MCPServerStatus.DISABLED
                for status in server_tags.values()
            )
            if all_disabled:
                self._server_status[server_name] = MCPServerStatus.DISABLED
            else:
                self._server_status[server_name] = MCPServerStatus.ACTIVE

            return (
                True,
                disabled_count,
                f"Server '{server_name}' unmounted successfully",
            )

        except Exception as e:
            self._server_status[server_name] = MCPServerStatus.ERROR
            return False, 0, f"Failed to unmount server: {str(e)}"

    @log_method("MCPServerRegistry")
    async def enable_server(
        self, server_name: str, tags: Optional[List[str]] = None
    ) -> tuple[bool, int, str]:
        if server_name not in self._mounted_servers:
            return False, 0, f"Server '{server_name}' is not mounted"

        # Get all tags for this server
        server_tags = self._server_tags.get(server_name, {})

        # Check if server/tags are disabled
        if tags:
            # Check if at least one of the specified tags is disabled
            tags_to_check = set(tags)
            any_disabled = any(
                server_tags.get(tag) == MCPServerStatus.DISABLED
                for tag in tags_to_check
            )
            if not any_disabled:
                return False, 0, f"Specified tags are not disabled for server '{server_name}'"
        else:
            # Check if the entire server is disabled
            if self._server_status.get(server_name) != MCPServerStatus.DISABLED:
                return False, 0, f"Server '{server_name}' is not disabled"

        try:
            # Get tags to enable
            if tags:
                tags_to_enable = set(tags)
            else:
                tags_to_enable = set(server_tags.keys()) if server_tags else {server_name}

            tool_set = await self._main_server.list_tools()
            # Count tools with matching tags
            enabled_count = 0
            if self._main_server:
                for tool in tool_set:
                    if hasattr(tool, "tags") and tool.tags:
                        if any(tag in tags_to_enable for tag in tool.tags):
                            enabled_count += 1

                # Re-enable in main server
                self._main_server.enable(tags=tags_to_enable)

            # Update tag statuses
            for tag in tags_to_enable:
                if tag in server_tags:
                    server_tags[tag] = MCPServerStatus.ACTIVE

            # Check if there are still disabled tags for this server
            any_disabled = any(
                status == MCPServerStatus.DISABLED
                for status in server_tags.values()
            )
            if not any_disabled:
                self._server_status[server_name] = MCPServerStatus.ACTIVE

            return True, enabled_count, f"Server '{server_name}' enabled successfully"

        except Exception as e:
            return False, 0, f"Failed to enable server: {str(e)}"

    @log_method("MCPServerRegistry")
    async def get_server_info(self, server_name: str) -> Optional[MCPServerInfo]:
        if server_name not in self._mounted_servers:
            return None

        config = self._server_configs.get(server_name)
        if not config:
            return None

        # Get server tags with their statuses
        server_tags = self._server_tags.get(server_name, {})
        tag_info_list = [
            ServerTagInfo(tag_name=tag_name, status=status)
            for tag_name, status in server_tags.items()
        ]

        # Count tools for this server
        tool_count = 0
        server_tag_names = set(server_tags.keys())
        tool_set = await self._main_server.list_tools()
        if self._main_server:
            for tool in tool_set:
                if hasattr(tool, "tags") and tool.tags:
                    if any(tag in server_tag_names for tag in tool.tags):
                        tool_count += 1

        return MCPServerInfo(
            server_name=config.server_name,
            spec_link=config.spec_link,
            base_url=config.base_url,
            description=config.description,
            tags=tag_info_list,
            status=self._server_status.get(server_name),
            tool_count=tool_count,
        )

    @log_method("MCPServerRegistry")
    async def list_servers(self) -> List[MCPServerInfo]:
        servers = []
        for server_name in self._mounted_servers.keys():
            info = await self.get_server_info(server_name)
            if info:
                servers.append(info)
        return servers

    @log_method("MCPServerRegistry")
    async def list_tools(self) -> List[ToolInfo]:
        tools = []

        if not self._main_server:
            return tools

        tool_set = await self._main_server.list_tools()
        for tool in tool_set:
            # Get tool tags
            tool_tags = []
            if hasattr(tool, "tags") and tool.tags:
                tool_tags = list(tool.tags)

            # Determine source server
            source_server = None
            for server_name, tags_dict in self._server_tags.items():
                server_tag_names = set(tags_dict.keys())
                if any(tag in tool_tags for tag in server_tag_names):
                    source_server = server_name
                    break

            tools.append(
                ToolInfo(
                    name=tool.name,
                    description=tool.description
                    if hasattr(tool, "description")
                    else None,
                    tags=tool_tags,
                    server_name=source_server,
                )
            )

        return tools

    @log_method("MCPServerRegistry")
    async def get_server_status(self) -> dict:
        tool_set = await self.list_tools()
        total_tools = len(tool_set)
        active_servers = sum(
            1
            for status in self._server_status.values()
            if status == MCPServerStatus.ACTIVE
        )

        return {
            "server_name": self._main_server.name if self._main_server else "unknown",
            "mounted_servers": len(self._mounted_servers),
            "active_servers": active_servers,
            "total_tools": total_tools,
            "status": "running" if self._main_server else "stopped",
        }

    @log_method("MCPServerRegistry")
    async def remove_server(self, server_name: str) -> tuple[bool, int, str]:
        if server_name not in self._mounted_servers:
            return False, 0, f"Server '{server_name}' is not mounted"

        disabled_count = 0
        try:
            # First disable/unmount if active
            if self._server_status.get(server_name) == MCPServerStatus.ACTIVE:
                success, disabled_count, msg = await self.unmount_server(server_name)
                if not success:
                    return False, disabled_count, msg

            # Get all tags for this server before removal
            server_tags = self._server_tags.get(server_name, {})
            tags_to_enable = set(server_tags.keys()) if server_tags else {server_name}

            # Enable the tags before removing the provider to clear disabled state
            # This ensures that if the server is remounted, the tools will be visible
            if self._main_server:
                self._main_server.enable(tags=tags_to_enable)

            # Properly unmount from main server by removing the provider
            if self._main_server and hasattr(self._main_server, 'providers'):
                # Find and remove the provider for this server
                providers = self._main_server.providers
                for i, provider in enumerate(providers):
                    if hasattr(provider, 'server') and hasattr(provider.server, 'name'):
                        if provider.server.name == server_name:
                            providers.pop(i)
                            print(f"Removed provider for server '{server_name}'")
                            break

            # Get the mounted server and close its client if it has one
            mounted_server = self._mounted_servers.get(server_name)
            if mounted_server and hasattr(mounted_server, '_client'):
                client = mounted_server._client
                if client and hasattr(client, 'aclose'):
                    await client.aclose()

            # Remove from tracking
            del self._mounted_servers[server_name]
            del self._server_configs[server_name]
            del self._server_tags[server_name]
            del self._server_status[server_name]

            return True, disabled_count, f"Server '{server_name}' removed successfully"

        except Exception as e:
            return False, disabled_count, f"Failed to remove server: {str(e)}"


# Global registry instance
_registry: Optional[MCPServerRegistry] = None


async def get_registry() -> MCPServerRegistry:
    global _registry
    if _registry is None:
        _registry = MCPServerRegistry()
    return _registry


async def initialize_main_server(name: str = "main-mcp-server") -> FastMCP:
    registry = await get_registry()
    return await registry.initialize_main_server(name)


async def get_main_server() -> Optional[FastMCP]:
    registry = await get_registry()
    return await registry.get_main_server()


async def mount_mcp_server(config: MCPServerConfig) -> tuple[bool, int, str]:
    registry = await get_registry()
    return await registry.mount_server(config)


async def unmount_mcp_server(
    server_name: str, tags: Optional[List[str]] = None
) -> tuple[bool, int, str]:
    registry = await get_registry()
    return await registry.unmount_server(server_name, tags)


async def enable_mcp_server(
    server_name: str, tags: Optional[List[str]] = None
) -> tuple[bool, int, str]:
    registry = await get_registry()
    return await registry.enable_server(server_name, tags)


async def remove_mcp_server(server_name: str) -> tuple[bool, int, str]:
    registry = await get_registry()
    return await registry.remove_server(server_name)
