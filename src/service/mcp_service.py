from typing import Any, Dict, List, Optional, Set
import httpx
from fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dto.models import (
    MCPServerConfig,
    MCPServerInfo,
    MCPServerStatus,
    ServerTagInfo,
    ToolInfo,
)
from db.models import (
    MCPServer as DBMcpServer,
    MCPServerTag as DBMcpServerTag,
    MCPAuditLog as DBMCPAuditLog,
    MCPServerStatusEnum,
    AuditActionEnum,
)
from db.database import get_session_context, init_db


class MCPServerRegistry:
    _instance: Optional["MCPServerRegistry"] = None
    _main_server: Optional[FastMCP] = None
    _mounted_servers: Dict[str, FastMCP] = {}
    _server_configs: Dict[str, MCPServerConfig] = {}
    _server_tags: Dict[str, Dict[str, MCPServerStatus]] = {}
    _server_status: Dict[str, MCPServerStatus] = {}
    _tools_cache: Dict[str, ToolInfo] = {}
    _db_initialized: bool = False

    def __init__(self):
        """Initialize the registry."""
        pass

    async def _ensure_db_initialized(self) -> None:
        """Ensure database is initialized."""
        if not self._db_initialized:
            await init_db(run_migrations=True)
            self._db_initialized = True

    async def _save_audit_log(
        self,
        session: AsyncSession,
        server_name: str,
        action: AuditActionEnum,
        status_before: Optional[str] = None,
        status_after: Optional[str] = None,
        tag_id: Optional[int] = None,
    ) -> None:
        """Save an audit log entry."""
        audit_log = DBMCPAuditLog(
            server_name=server_name,
            action=action,
            status_before=status_before,
            status_after=status_after,
            tag_id=tag_id,
        )
        session.add(audit_log)
        await session.flush()

    async def _load_from_database(self) -> None:
        """Load server configurations and statuses from database on startup.

        Only ACTIVE servers and their ACTIVE tags are loaded into memory.
        This also actually mounts the FastMCP server instances, not just the metadata.
        """
        await self._ensure_db_initialized()

        try:
            async with await get_session_context() as session:
                # Load all servers from DB
                stmt = select(DBMcpServer).options(selectinload(DBMcpServer.tags))
                result = await session.execute(stmt)
                db_servers = result.scalars().all()

                for db_server in db_servers:
                    # Only load ACTIVE servers into memory
                    if db_server.status == MCPServerStatusEnum.ACTIVE:
                        # Reconstruct MCPServerConfig
                        config = MCPServerConfig(
                            server_name=db_server.server_name,
                            spec_link=db_server.spec_link,
                            base_url=db_server.base_url,
                            headers=db_server.headers,
                            description=db_server.description,
                        )

                        # Actually create and mount the FastMCP server instance
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

                            # Mount to main server
                            if self._main_server:
                                self._main_server.mount(sub_server)

                            # Track the mounted server
                            self._mounted_servers[config.server_name] = sub_server

                            # Add server_name as a tag to all tools in the sub_server
                            tool_set = await sub_server.list_tools()
                            for tool in tool_set:
                                if hasattr(tool, 'tags') and tool.tags:
                                    if config.server_name not in tool.tags:
                                        tool.tags = list(tool.tags) + [config.server_name]
                                else:
                                    tool.tags = [config.server_name]

                            # Extract tags from DB - use database tag statuses
                            # db_server.tags was loaded via selectinload above
                            db_tags = db_server.tags or []
                            
                            if db_tags:
                                # Use database tags with their actual statuses
                                tag_statuses = {}
                                active_tags = []
                                disabled_tags = []
                                
                                for db_tag in db_tags:
                                    tag_status = (
                                        MCPServerStatus.ACTIVE
                                        if db_tag.status == MCPServerStatusEnum.ACTIVE
                                        else MCPServerStatus.DISABLED
                                    )
                                    tag_statuses[db_tag.tag_name] = tag_status
                                    
                                    if db_tag.status == MCPServerStatusEnum.ACTIVE:
                                        active_tags.append(db_tag.tag_name)
                                    else:
                                        disabled_tags.append(db_tag.tag_name)
                                
                                self._server_tags[config.server_name] = tag_statuses
                                
                                # Disable tags that were DISABLED in the database
                                if disabled_tags and self._main_server:
                                    self._main_server.disable(tags=set(disabled_tags))
                                    # Also add server_name as a tag to ensure all tools from this server are disabled
                                    self._main_server.disable(tags={config.server_name})
                            else:
                                # Fallback to extracted OpenAPI tags if no DB tags exist
                                extracted_tags = self.extract_openapi_tags(openapi_spec) or [config.server_name]
                                self._server_tags[config.server_name] = {
                                    tag: MCPServerStatus.ACTIVE for tag in extracted_tags
                                }

                        except httpx.HTTPError as e:
                            # Log but continue loading other servers
                            import logging
                            logging.warning(f"Failed to fetch OpenAPI spec for '{config.server_name}': {e}")
                            # Still add config but mark tags as empty since we couldn't mount
                            self._server_tags[config.server_name] = {}
                        except Exception as e:
                            import logging
                            logging.warning(f"Failed to mount server '{config.server_name}': {e}")
                            # Still add config but mark tags as empty since we couldn't mount
                            self._server_tags[config.server_name] = {}

                        # Store the config and status (after attempting mount)
                        self._server_configs[config.server_name] = config
                        self._server_status[config.server_name] = MCPServerStatus.ACTIVE

                # After loading all servers, refresh the tools cache
                if self._mounted_servers:
                    await self.refresh_tools_cache()

        except Exception as e:
            # Tables might not exist yet - that's okay, just log and continue
            import logging
            logging.warning(f"Could not load from database (tables may not exist yet): {e}")

    async def refresh_tools_cache(self) -> int:
        """Fetches all tools from the main server and populates the tools cache.

        Returns:
            The number of tools cached.
        """
        self._tools_cache.clear()

        if not self._main_server:
            return 0

        tool_set = await self._main_server.list_tools()

        for tool in tool_set:
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

            tool_info = ToolInfo(
                name=tool.name,
                description=tool.description if hasattr(tool, "description") else None,
                tags=tool_tags,
                server_name=source_server,
            )
            self._tools_cache[tool.name] = tool_info

        return len(self._tools_cache)

    async def get_tool(self, name: str) -> Optional[ToolInfo]:
        """Get a tool by name from cache or by fetching and checking.

        Args:
            name: The name of the tool to retrieve.

        Returns:
            Optional[ToolInfo] - the tool info if found, None otherwise.
        """
        # First check cache
        if name in self._tools_cache:
            return self._tools_cache[name]

        # If cache is empty, try to refresh it
        if not self._tools_cache:
            await self.refresh_tools_cache()
            if name in self._tools_cache:
                return self._tools_cache[name]

        # If still not found, return None
        return None

    async def initialize_main_server(self, name: str = "main-mcp-server") -> FastMCP:
        if self._main_server is None:
            self._main_server = FastMCP(name=name)
            # Load persisted servers from database
            await self._load_from_database()
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

    async def mount_server(self, config: MCPServerConfig) -> tuple[bool, int, str]:
        await self._ensure_db_initialized()

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

            # Add server_name as a tag to all tools in the sub_server
            # This ensures disable(tags=server_name) works correctly
            for tool in tool_set:
                if hasattr(tool, 'tags') and tool.tags:
                    if config.server_name not in tool.tags:
                        tool.tags = list(tool.tags) + [config.server_name]
                else:
                    tool.tags = [config.server_name]

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

            # Persist to database
            async with await get_session_context() as session:
                # Create server record
                db_server = DBMcpServer(
                    server_name=config.server_name,
                    spec_link=config.spec_link,
                    base_url=config.base_url,
                    headers=config.headers,
                    description=config.description,
                    status=MCPServerStatusEnum.ACTIVE,
                )
                session.add(db_server)

                # Create tag records
                for tag in extracted_tags:
                    db_tag = DBMcpServerTag(
                        server_name=config.server_name,
                        tag_name=tag,
                        status=MCPServerStatusEnum.ACTIVE,
                    )
                    session.add(db_tag)

                # Save audit log
                await self._save_audit_log(
                    session=session,
                    server_name=config.server_name,
                    action=AuditActionEnum.MOUNT,
                    status_before=None,
                    status_after=MCPServerStatusEnum.ACTIVE.value,
                )

                await session.commit()

            # Refresh tools cache after mounting
            await self.refresh_tools_cache()

            return (
                True,
                tool_count,
                f"Server '{config.server_name}' mounted successfully",
            )

        except httpx.HTTPError as e:
            return False, 0, f"Failed to fetch OpenAPI spec: {str(e)}"
        except Exception as e:
            return False, 0, f"Failed to mount server: {str(e)}"

    async def unmount_server(
        self, server_name: str, tags: Optional[List[str]] = None
    ) -> tuple[bool, int, str]:
        await self._ensure_db_initialized()

        # Check if server exists in memory or database
        if server_name not in self._mounted_servers:
            # Check if server exists in database
            try:
                async with await get_session_context() as session:
                    stmt = select(DBMcpServer).where(DBMcpServer.server_name == server_name)
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        return False, 0, f"Server '{server_name}' is not mounted (exists in database as '{db_server.status.value}')"
            except Exception:
                pass
            return False, 0, f"Server '{server_name}' is not mounted"

        try:
            # Get all tags for this server from _server_tags (OpenAPI-derived tags)
            server_tags = self._server_tags.get(server_name, {})

            # If specific tags are provided, use ONLY those tags
            # Otherwise, use all OpenAPI-derived tags (or fall back to server_name)
            if tags:
                tags_to_disable = set(tags)
                # Validate that all specified tags exist for this server
                invalid_tags = tags_to_disable - set(server_tags.keys()) - {server_name}
                if invalid_tags:
                    return False, 0, f"Invalid tags: {invalid_tags}. Available tags: {set(server_tags.keys())}"
            elif server_tags:
                tags_to_disable = set(server_tags.keys())
            else:
                tags_to_disable = {server_name}

            # Also add server_name as a tag to ensure all tools are disabled
            # (some tools might only have server_name tag)
            tags_to_disable.add(server_name)

            # Disable tools with these tags in main server
            disabled_count = 0
            if self._main_server:
                tool_set = await self._main_server.list_tools()
                # Count tools with matching tags
                for tool in tool_set:
                    if hasattr(tool, "tags") and tool.tags:
                        if any(tag in tags_to_disable for tag in tool.tags):
                            disabled_count += 1

                # Disable the tags
                self._main_server.disable(tags=tags_to_disable)

            # Get status before for audit
            status_before = self._server_status.get(server_name)

            # Update tag statuses
            for tag in tags_to_disable:
                if tag in server_tags:
                    server_tags[tag] = MCPServerStatus.DISABLED

            # Update server status based on whether all tags are disabled
            # Handle empty server_tags edge case - empty dict means we can't determine tag status
            all_disabled = bool(server_tags) and all(
                status == MCPServerStatus.DISABLED
                for status in server_tags.values()
            )
            if all_disabled:
                self._server_status[server_name] = MCPServerStatus.DISABLED
                new_db_status = MCPServerStatusEnum.DISABLED
            else:
                # Update in-memory status to ACTIVE when not all tags are disabled
                self._server_status[server_name] = MCPServerStatus.ACTIVE
                new_db_status = MCPServerStatusEnum.ACTIVE

            # Persist to database
            async with await get_session_context() as session:
                # Update server status
                stmt = select(DBMcpServer).where(DBMcpServer.server_name == server_name)
                result = await session.execute(stmt)
                db_server = result.scalar_one_or_none()
                if db_server:
                    db_server.status = new_db_status

                # Update tag statuses
                for tag in tags_to_disable:
                    tag_stmt = select(DBMcpServerTag).where(
                        DBMcpServerTag.server_name == server_name,
                        DBMcpServerTag.tag_name == tag,
                    )
                    tag_result = await session.execute(tag_stmt)
                    db_tag = tag_result.scalar_one_or_none()
                    if db_tag:
                        db_tag.status = MCPServerStatusEnum.DISABLED

                # Save audit log
                await self._save_audit_log(
                    session=session,
                    server_name=server_name,
                    action=AuditActionEnum.UNMOUNT,
                    status_before=status_before.value if status_before else None,
                    status_after=new_db_status.value,
                )

                await session.commit()

            # Refresh tools cache after unmounting
            await self.refresh_tools_cache()

            return (
                True,
                disabled_count,
                f"Server '{server_name}' unmounted successfully",
            )

        except Exception as e:
            self._server_status[server_name] = MCPServerStatus.ERROR
            return False, 0, f"Failed to unmount server: {str(e)}"

    async def enable_server(
        self, server_name: str, tags: Optional[List[str]] = None
    ) -> tuple[bool, int, str]:
        await self._ensure_db_initialized()

        # Check if server exists in memory or database
        if server_name not in self._mounted_servers:
            # Check if server exists in database but is disabled
            try:
                async with await get_session_context() as session:
                    stmt = select(DBMcpServer).where(DBMcpServer.server_name == server_name)
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        if db_server.status == MCPServerStatusEnum.DISABLED:
                            return False, 0, f"Server '{server_name}' exists in database but is disabled (not mounted in memory)"
                        return False, 0, f"Server '{server_name}' is not mounted"
            except Exception:
                pass
            return False, 0, f"Server '{server_name}' is not mounted"

        # Get all tags for this server
        server_tags = self._server_tags.get(server_name, {})

        # Determine which tags to enable based on input
        tags_to_enable: Set[str]

        if tags:
            # If specific tags are provided, use ONLY those tags
            tags_to_check = set(tags)
            # Validate that all specified tags exist for this server
            invalid_tags = tags_to_check - set(server_tags.keys()) - {server_name}
            if invalid_tags:
                return False, 0, f"Invalid tags: {invalid_tags}. Available tags: {set(server_tags.keys())}"

            # Check if at least one of the specified tags is disabled
            any_disabled = any(
                server_tags.get(tag) == MCPServerStatus.DISABLED
                for tag in tags_to_check
            )
            if not any_disabled:
                return False, 0, f"Specified tags are not disabled for server '{server_name}'"

            tags_to_enable = tags_to_check
        else:
            # If no tags specified, enable all tags for this server
            # First check if server is mounted in memory
            if server_name in self._mounted_servers:
                # Get actual tags from the FastMCP server
                mounted_server = self._mounted_servers[server_name]
                try:
                    tool_set = await mounted_server.list_tools()
                    # Extract all unique tags from the FastMCP server
                    fastmcp_tags: Set[str] = set()
                    for tool in tool_set:
                        if hasattr(tool, 'tags') and tool.tags:
                            fastmcp_tags.update(tool.tags)
                    # Also add server_name as a tag (it's added to all tools)
                    fastmcp_tags.add(server_name)

                    # Combine with existing server_tags - tags in FastMCP but not in _server_tags
                    # need to be considered as well
                    all_known_tags = set(server_tags.keys()) if server_tags else set()
                    missing_tags = fastmcp_tags - all_known_tags

                    # Create a combined view of tags - missing tags are considered as not enabled
                    # (they exist in FastMCP but are not registered in _server_tags)
                    if missing_tags:
                        # Initialize missing tags as DISABLED so they can be enabled
                        if not server_tags:
                            server_tags = {}
                        for tag in missing_tags:
                            server_tags[tag] = MCPServerStatus.DISABLED
                        self._server_tags[server_name] = server_tags
                except Exception:
                    # If we can't get tools from FastMCP, fall back to existing logic
                    pass

            # Get all tags from database if not in memory
            if not server_tags:
                try:
                    async with await get_session_context() as session:
                        stmt = select(DBMcpServerTag).where(DBMcpServerTag.server_name == server_name)
                        result = await session.execute(stmt)
                        db_tags = result.scalars().all()
                        if db_tags:
                            server_tags = {tag.tag_name: MCPServerStatus.DISABLED for tag in db_tags}
                            self._server_tags[server_name] = server_tags
                        else:
                            server_tags = {server_name: MCPServerStatus.DISABLED}
                            self._server_tags[server_name] = server_tags
                except Exception:
                    server_tags = {server_name: MCPServerStatus.DISABLED}
                    self._server_tags[server_name] = server_tags

            # Filter to only disabled tags
            disabled_tags = {tag for tag, status in server_tags.items() if status == MCPServerStatus.DISABLED}
            if not disabled_tags:
                return False, 0, f"Server '{server_name}' has no disabled tags"

            tags_to_enable = disabled_tags

        # Get status before for audit
        status_before = self._server_status.get(server_name)

        try:
            # Count tools with matching tags
            enabled_count = 0
            if self._main_server:
                tool_set = await self._main_server.list_tools()
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

            # Check if there are any enabled tags for this server - if so, set server to ACTIVE
            any_enabled = any(
                status == MCPServerStatus.ACTIVE
                for status in server_tags.values()
            )
            if any_enabled:
                self._server_status[server_name] = MCPServerStatus.ACTIVE

            # Persist to database
            async with await get_session_context() as session:
                # Update tag statuses
                for tag in tags_to_enable:
                    tag_stmt = select(DBMcpServerTag).where(
                        DBMcpServerTag.server_name == server_name,
                        DBMcpServerTag.tag_name == tag,
                    )
                    tag_result = await session.execute(tag_stmt)
                    db_tag = tag_result.scalar_one_or_none()
                    if db_tag:
                        db_tag.status = MCPServerStatusEnum.ACTIVE

                # Check if any tag is now active - if so, set server to ACTIVE
                any_active = any(
                    tag_status == MCPServerStatus.ACTIVE
                    for tag_status in server_tags.values()
                )
                if any_active:
                    stmt = select(DBMcpServer).where(DBMcpServer.server_name == server_name)
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        db_server.status = MCPServerStatusEnum.ACTIVE

                # Save audit log
                await self._save_audit_log(
                    session=session,
                    server_name=server_name,
                    action=AuditActionEnum.ENABLE,
                    status_before=status_before.value if status_before else None,
                    status_after=self._server_status.get(server_name).value if self._server_status.get(server_name) else None,
                )

                await session.commit()

            # Refresh tools cache after enabling
            await self.refresh_tools_cache()

            return True, enabled_count, f"Server '{server_name}' enabled successfully"

        except Exception as e:
            return False, 0, f"Failed to enable server: {str(e)}"

    async def get_server_info(self, server_name: str) -> Optional[MCPServerInfo]:
        # First check in-memory data
        config = self._server_configs.get(server_name)
        server_tags = self._server_tags.get(server_name, {})
        
        # If not in memory, try to get from database
        if not config:
            await self._ensure_db_initialized()
            try:
                async with await get_session_context() as session:
                    stmt = select(DBMcpServer).options(selectinload(DBMcpServer.tags)).where(DBMcpServer.server_name == server_name)
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        # Reconstruct from database
                        config = MCPServerConfig(
                            server_name=db_server.server_name,
                            spec_link=db_server.spec_link,
                            base_url=db_server.base_url,
                            headers=db_server.headers,
                            description=db_server.description,
                        )
                        server_tags = {tag.tag_name: MCPServerStatus.ACTIVE if tag.status == MCPServerStatusEnum.ACTIVE else MCPServerStatus.DISABLED for tag in db_server.tags}
            except Exception:
                pass
        
        if not config:
            return None

        # Get server tags with their statuses
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

    async def list_servers(self) -> List[MCPServerInfo]:
        servers = []
        server_names = set(self._mounted_servers.keys())
        
        # Also get all servers from database
        await self._ensure_db_initialized()
        try:
            async with await get_session_context() as session:
                stmt = select(DBMcpServer).options(selectinload(DBMcpServer.tags))
                result = await session.execute(stmt)
                db_servers = result.scalars().all()
                for db_server in db_servers:
                    server_names.add(db_server.server_name)
        except Exception:
            pass
        
        for server_name in server_names:
            info = await self.get_server_info(server_name)
            if info:
                servers.append(info)
        return servers

    async def list_tools(self) -> List[ToolInfo]:
        # Check if cache is empty, if so refresh it
        if not self._tools_cache:
            await self.refresh_tools_cache()

        # Return the cached list of tools
        return list(self._tools_cache.values())

    async def get_server_status(self) -> dict:
        # Query database for complete server status counts
        await self._ensure_db_initialized()
        total_tools = 0
        active_servers = 0
        
        try:
            async with await get_session_context() as session:
                # Get all servers from DB
                stmt = select(DBMcpServer)
                result = await session.execute(stmt)
                db_servers = result.scalars().all()
                
                for db_server in db_servers:
                    if db_server.status == MCPServerStatusEnum.ACTIVE:
                        active_servers += 1
        except Exception:
            # Fall back to in-memory if DB query fails
            active_servers = sum(
                1
                for status in self._server_status.values()
                if status == MCPServerStatus.ACTIVE
            )
        
        tool_set = await self.list_tools()
        total_tools = len(tool_set)

        return {
            "server_name": self._main_server.name if self._main_server else "unknown",
            "mounted_servers": active_servers,
            "active_servers": active_servers,
            "total_tools": total_tools,
            "status": "running" if self._main_server else "stopped",
        }

    async def remove_server(self, server_name: str) -> tuple[bool, int, str]:
        await self._ensure_db_initialized()

        # Check if server exists in memory or database
        if server_name not in self._mounted_servers:
            # Check if server exists in database regardless of memory state
            try:
                async with await get_session_context() as session:
                    stmt = select(DBMcpServer).where(DBMcpServer.server_name == server_name)
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        # Server exists in database, proceed to remove it
                        status_before = MCPServerStatus.ACTIVE if db_server.status == MCPServerStatusEnum.ACTIVE else MCPServerStatus.DISABLED
                        disabled_count = 0
                        
                        # Delete from database
                        try:
                            # Save audit log BEFORE deleting the server
                            await self._save_audit_log(
                                session=session,
                                server_name=server_name,
                                action=AuditActionEnum.REMOVE,
                                status_before=status_before.value if status_before else None,
                                status_after=None,
                            )

                            # Delete server (cascades to tags but not audit logs now)
                            await session.delete(db_server)
                            await session.commit()
                            return True, disabled_count, f"Server '{server_name}' removed successfully"
                        except Exception as e:
                            await session.rollback()
                            return False, disabled_count, f"Failed to remove server: {str(e)}"
            except Exception:
                pass
            return False, 0, f"Server '{server_name}' is not mounted"

        disabled_count = 0
        # Get status before for audit
        status_before = self._server_status.get(server_name)

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

            # Delete from database
            async with await get_session_context() as session:
                # Save audit log BEFORE deleting the server
                await self._save_audit_log(
                    session=session,
                    server_name=server_name,
                    action=AuditActionEnum.REMOVE,
                    status_before=status_before.value if status_before else None,
                    status_after=None,
                )

                # Delete server (cascades to tags but not audit logs now)
                stmt = select(DBMcpServer).where(DBMcpServer.server_name == server_name)
                result = await session.execute(stmt)
                db_server = result.scalar_one_or_none()
                if db_server:
                    await session.delete(db_server)

                await session.commit()

            # Refresh tools cache after removing
            await self.refresh_tools_cache()

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
