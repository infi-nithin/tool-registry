"""Database-backed MCP Server Registry service.

This module provides a refactored MCPServerRegistry that uses PostgreSQL
for persistence instead of global variables. All server state is stored
in the database and restored on startup.
"""

import time
import uuid
from typing import Any, Dict, List, Optional, Set

import httpx
from fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aop_logging import log_method, patch_fastmcp_server
from db.models.mcp_server import MCPServerDB, MCPServerStatus, MCPServerTagDB
from db.models.audit import ServerOperationType, AuditOperationStatus
from dto.models import (
    MCPServerConfig,
    MCPServerInfo,
    MCPServerStatus as MCPServerStatusDTO,
    ServerTagInfo,
    ToolInfo,
)
from service.audit_logger import AuditLogger


class MCPServerRegistryDB:
    """Database-backed MCP Server Registry.
    
    This class replaces the in-memory global variables with PostgreSQL persistence.
    Server configurations, tags, and status are stored in the database and
    restored on application startup.
    
    Attributes:
        _session: AsyncSession for database operations
        _main_server: The main FastMCP server instance
        _mounted_servers: In-memory cache of mounted FastMCP instances
        _audit_logger: Audit logging helper
    
    Example:
        >>> async with get_db_session() as session:
        ...     registry = MCPServerRegistryDB(session)
        ...     await registry.initialize_main_server()
        ...     await registry.restore_servers_from_db()
    """

    def __init__(self, session: AsyncSession):
        """Initialize the registry with a database session.
        
        Args:
            session: SQLAlchemy AsyncSession for database operations
        """
        self._session = session
        self._main_server: Optional[FastMCP] = None
        # In-memory cache of mounted FastMCP instances (recreated from DB on startup)
        self._mounted_servers: Dict[str, FastMCP] = {}
        self._audit_logger = AuditLogger()

    @log_method("MCPServerRegistryDB")
    async def initialize_main_server(self, name: str = "main-mcp-server") -> FastMCP:
        """Initialize the main FastMCP server.
        
        Creates the main FastMCP instance and patches it for AOP logging.
        
        Args:
            name: Name for the main MCP server
            
        Returns:
            The initialized FastMCP instance
        """
        if self._main_server is None:
            self._main_server = FastMCP(name=name)
            # Patch for AOP logging
            self._main_server = patch_fastmcp_server(self._main_server)
        return self._main_server

    async def get_main_server(self) -> Optional[FastMCP]:
        """Get the main FastMCP server instance.
        
        Returns:
            The main FastMCP instance or None if not initialized
        """
        return self._main_server

    def _map_db_status_to_dto(self, status: MCPServerStatus) -> MCPServerStatusDTO:
        """Map database status enum to DTO status enum.
        
        Args:
            status: Database MCPServerStatus enum value
            
        Returns:
            DTO MCPServerStatus enum value
        """
        status_map = {
            MCPServerStatus.ACTIVE: MCPServerStatusDTO.ACTIVE,
            MCPServerStatus.DISABLED: MCPServerStatusDTO.DISABLED,
            MCPServerStatus.ERROR: MCPServerStatusDTO.ERROR,
        }
        return status_map.get(status, MCPServerStatusDTO.ERROR)

    def _map_dto_status_to_db(self, status: MCPServerStatusDTO) -> MCPServerStatus:
        """Map DTO status enum to database status enum.
        
        Args:
            status: DTO MCPServerStatus enum value
            
        Returns:
            Database MCPServerStatus enum value
        """
        status_map = {
            MCPServerStatusDTO.ACTIVE: MCPServerStatus.ACTIVE,
            MCPServerStatusDTO.DISABLED: MCPServerStatus.DISABLED,
            MCPServerStatusDTO.ERROR: MCPServerStatus.ERROR,
        }
        return status_map.get(status, MCPServerStatus.ERROR)

    def extract_openapi_tags(self, openapi_spec: Dict[str, Any]) -> List[str]:
        """Extract all tags from an OpenAPI specification.
        
        Args:
            openapi_spec: Parsed OpenAPI specification dictionary
            
        Returns:
            Sorted list of unique tag names
        """
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

    @log_method("MCPServerRegistryDB")
    async def mount_server(
        self, 
        config: MCPServerConfig,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> tuple[bool, int, str]:
        """Mount a new MCP server from an OpenAPI specification.
        
        Fetches the OpenAPI spec, creates a FastMCP sub-server, mounts it
        to the main server, and persists the configuration to the database.
        
        Args:
            config: Server configuration including spec_link, base_url, etc.
            user_id: Optional user identifier for audit trail
            correlation_id: Optional correlation ID for request tracing
            
        Returns:
            Tuple of (success: bool, tool_count: int, message: str)
        """
        start_time = time.time()
        
        # Check if server already exists in database
        result = await self._session.execute(
            select(MCPServerDB).where(
                MCPServerDB.server_name == config.server_name,
                MCPServerDB.is_deleted == False
            )
        )
        existing_server = result.scalar_one_or_none()
        
        if existing_server:
            # Server exists in DB, check if already mounted in memory
            if config.server_name in self._mounted_servers:
                await self._audit_logger.log_server_operation(
                    session=self._session,
                    operation_type=ServerOperationType.MOUNT,
                    server_name=config.server_name,
                    user_id=user_id,
                    correlation_id=correlation_id,
                    result_status=AuditOperationStatus.FAILURE,
                    error_message=f"Server '{config.server_name}' is already mounted",
                    duration_ms=(time.time() - start_time) * 1000,
                )
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
            tool_count = len(tool_set)

            # Mount to main server
            if self._main_server:
                self._main_server.mount(sub_server)

            # Extract tags from OpenAPI spec
            extracted_tags = self.extract_openapi_tags(openapi_spec) or [config.server_name]

            # Persist to database
            if existing_server:
                # Update existing server
                existing_server.spec_link = config.spec_link
                existing_server.base_url = config.base_url
                existing_server.description = config.description
                existing_server.headers = config.headers or {}
                existing_server.status = MCPServerStatus.ACTIVE
                existing_server.tool_count = tool_count
                existing_server.is_deleted = False
                existing_server.deleted_at = None
                server_db = existing_server
            else:
                # Create new server record
                server_db = MCPServerDB(
                    server_name=config.server_name,
                    spec_link=config.spec_link,
                    base_url=config.base_url,
                    description=config.description,
                    headers=config.headers or {},
                    status=MCPServerStatus.ACTIVE,
                    tool_count=tool_count,
                    created_by=user_id,
                )
                self._session.add(server_db)
            
            await self._session.flush()  # Get the server ID

            # Create or update tag records
            for tag_name in extracted_tags:
                tag_result = await self._session.execute(
                    select(MCPServerTagDB).where(
                        MCPServerTagDB.server_id == server_db.id,
                        MCPServerTagDB.tag_name == tag_name,
                    )
                )
                existing_tag = tag_result.scalar_one_or_none()
                
                if existing_tag:
                    existing_tag.status = MCPServerStatus.ACTIVE
                    existing_tag.is_deleted = False
                else:
                    tag_db = MCPServerTagDB(
                        server_id=server_db.id,
                        tag_name=tag_name,
                        status=MCPServerStatus.ACTIVE,
                        created_by=user_id,
                    )
                    self._session.add(tag_db)

            await self._session.commit()

            # Track the mounted server in memory
            self._mounted_servers[config.server_name] = sub_server

            # Log successful operation
            duration_ms = (time.time() - start_time) * 1000
            await self._audit_logger.log_server_operation(
                session=self._session,
                operation_type=ServerOperationType.MOUNT,
                server_id=server_db.id,
                server_name=config.server_name,
                user_id=user_id,
                correlation_id=correlation_id,
                result_status=AuditOperationStatus.SUCCESS,
                operation_details={
                    "tool_count": tool_count,
                    "tags": extracted_tags,
                    "base_url": config.base_url,
                },
                duration_ms=duration_ms,
            )

            return (
                True,
                tool_count,
                f"Server '{config.server_name}' mounted successfully",
            )

        except httpx.HTTPError as e:
            await self._session.rollback()
            duration_ms = (time.time() - start_time) * 1000
            await self._audit_logger.log_server_operation(
                session=self._session,
                operation_type=ServerOperationType.MOUNT,
                server_name=config.server_name,
                user_id=user_id,
                correlation_id=correlation_id,
                result_status=AuditOperationStatus.FAILURE,
                error_message=f"Failed to fetch OpenAPI spec: {str(e)}",
                duration_ms=duration_ms,
            )
            return False, 0, f"Failed to fetch OpenAPI spec: {str(e)}"
        except Exception as e:
            await self._session.rollback()
            duration_ms = (time.time() - start_time) * 1000
            await self._audit_logger.log_server_operation(
                session=self._session,
                operation_type=ServerOperationType.MOUNT,
                server_name=config.server_name,
                user_id=user_id,
                correlation_id=correlation_id,
                result_status=AuditOperationStatus.FAILURE,
                error_message=f"Failed to mount server: {str(e)}",
                duration_ms=duration_ms,
            )
            return False, 0, f"Failed to mount server: {str(e)}"

    @log_method("MCPServerRegistryDB")
    async def unmount_server(
        self, 
        server_name: str, 
        tags: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> tuple[bool, int, str]:
        """Unmount/disable tags for an MCP server.
        
        Disables the specified tags (or all tags if none specified) in the
        main FastMCP server and updates the database status.
        
        Args:
            server_name: Name of the server to unmount
            tags: Optional list of specific tags to disable
            user_id: Optional user identifier for audit trail
            correlation_id: Optional correlation ID for request tracing
            
        Returns:
            Tuple of (success: bool, disabled_count: int, message: str)
        """
        start_time = time.time()
        
        # Check if server is mounted in memory
        if server_name not in self._mounted_servers:
            return False, 0, f"Server '{server_name}' is not mounted"

        # Get server from database
        result = await self._session.execute(
            select(MCPServerDB).where(
                MCPServerDB.server_name == server_name,
                MCPServerDB.is_deleted == False
            ).options(selectinload(MCPServerDB.tags))
        )
        server_db = result.scalar_one_or_none()
        
        if not server_db:
            return False, 0, f"Server '{server_name}' not found in database"

        try:
            # Get all tags for this server from database
            db_tags = {tag.tag_name: tag for tag in server_db.tags if not tag.is_deleted}
            
            # Determine which tags to disable
            if tags:
                tags_to_disable = set(tags)
            else:
                tags_to_disable = set(db_tags.keys()) if db_tags else {server_name}

            # Count tools that will be disabled
            disabled_count = 0
            if self._main_server:
                tool_set = await self._main_server.list_tools()
                for tool in tool_set:
                    if hasattr(tool, "tags") and tool.tags:
                        if any(tag in tags_to_disable for tag in tool.tags):
                            disabled_count += 1

                # Disable the tags in FastMCP
                self._main_server.disable(tags=tags_to_disable)

            # Update tag statuses in database
            for tag_name in tags_to_disable:
                if tag_name in db_tags:
                    db_tags[tag_name].status = MCPServerStatus.DISABLED
                    db_tags[tag_name].updated_by = user_id

            # Update server status based on whether all tags are disabled
            all_disabled = all(
                tag.status == MCPServerStatus.DISABLED
                for tag in db_tags.values()
            )
            if all_disabled:
                server_db.status = MCPServerStatus.DISABLED
            server_db.updated_by = user_id

            await self._session.commit()

            # Log successful operation
            duration_ms = (time.time() - start_time) * 1000
            await self._audit_logger.log_server_operation(
                session=self._session,
                operation_type=ServerOperationType.UNMOUNT,
                server_id=server_db.id,
                server_name=server_name,
                user_id=user_id,
                correlation_id=correlation_id,
                result_status=AuditOperationStatus.SUCCESS,
                operation_details={
                    "disabled_tags": list(tags_to_disable),
                    "disabled_tools": disabled_count,
                },
                duration_ms=duration_ms,
            )

            return (
                True,
                disabled_count,
                f"Server '{server_name}' unmounted successfully",
            )

        except Exception as e:
            await self._session.rollback()
            server_db.status = MCPServerStatus.ERROR
            await self._session.commit()
            
            duration_ms = (time.time() - start_time) * 1000
            await self._audit_logger.log_server_operation(
                session=self._session,
                operation_type=ServerOperationType.UNMOUNT,
                server_id=server_db.id,
                server_name=server_name,
                user_id=user_id,
                correlation_id=correlation_id,
                result_status=AuditOperationStatus.FAILURE,
                error_message=f"Failed to unmount server: {str(e)}",
                duration_ms=duration_ms,
            )
            return False, 0, f"Failed to unmount server: {str(e)}"

    @log_method("MCPServerRegistryDB")
    async def enable_server(
        self, 
        server_name: str, 
        tags: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> tuple[bool, int, str]:
        """Enable tags for a previously disabled MCP server.
        
        Re-enables the specified tags (or all tags if none specified) in the
        main FastMCP server and updates the database status.
        
        Args:
            server_name: Name of the server to enable
            tags: Optional list of specific tags to enable
            user_id: Optional user identifier for audit trail
            correlation_id: Optional correlation ID for request tracing
            
        Returns:
            Tuple of (success: bool, enabled_count: int, message: str)
        """
        start_time = time.time()
        
        # Check if server is mounted in memory
        if server_name not in self._mounted_servers:
            return False, 0, f"Server '{server_name}' is not mounted"

        # Get server from database
        result = await self._session.execute(
            select(MCPServerDB).where(
                MCPServerDB.server_name == server_name,
                MCPServerDB.is_deleted == False
            ).options(selectinload(MCPServerDB.tags))
        )
        server_db = result.scalar_one_or_none()
        
        if not server_db:
            return False, 0, f"Server '{server_name}' not found in database"

        # Get all tags for this server
        db_tags = {tag.tag_name: tag for tag in server_db.tags if not tag.is_deleted}

        # Check if server/tags are disabled
        if tags:
            tags_to_check = set(tags)
            any_disabled = any(
                db_tags.get(tag) and db_tags[tag].status == MCPServerStatus.DISABLED
                for tag in tags_to_check
            )
            if not any_disabled:
                return False, 0, f"Specified tags are not disabled for server '{server_name}'"
        else:
            if server_db.status != MCPServerStatus.DISABLED:
                return False, 0, f"Server '{server_name}' is not disabled"

        try:
            # Determine which tags to enable
            if tags:
                tags_to_enable = set(tags)
            else:
                tags_to_enable = set(db_tags.keys()) if db_tags else {server_name}

            # Count tools that will be enabled
            enabled_count = 0
            if self._main_server:
                tool_set = await self._main_server.list_tools()
                for tool in tool_set:
                    if hasattr(tool, "tags") and tool.tags:
                        if any(tag in tags_to_enable for tag in tool.tags):
                            enabled_count += 1

                # Re-enable in main server
                self._main_server.enable(tags=tags_to_enable)

            # Update tag statuses in database
            for tag_name in tags_to_enable:
                if tag_name in db_tags:
                    db_tags[tag_name].status = MCPServerStatus.ACTIVE
                    db_tags[tag_name].updated_by = user_id

            # Update server status
            server_db.status = MCPServerStatus.ACTIVE
            server_db.updated_by = user_id

            await self._session.commit()

            # Log successful operation
            duration_ms = (time.time() - start_time) * 1000
            await self._audit_logger.log_server_operation(
                session=self._session,
                operation_type=ServerOperationType.ENABLE,
                server_id=server_db.id,
                server_name=server_name,
                user_id=user_id,
                correlation_id=correlation_id,
                result_status=AuditOperationStatus.SUCCESS,
                operation_details={
                    "enabled_tags": list(tags_to_enable),
                    "enabled_tools": enabled_count,
                },
                duration_ms=duration_ms,
            )

            return True, enabled_count, f"Server '{server_name}' enabled successfully"

        except Exception as e:
            await self._session.rollback()
            
            duration_ms = (time.time() - start_time) * 1000
            await self._audit_logger.log_server_operation(
                session=self._session,
                operation_type=ServerOperationType.ENABLE,
                server_id=server_db.id,
                server_name=server_name,
                user_id=user_id,
                correlation_id=correlation_id,
                result_status=AuditOperationStatus.FAILURE,
                error_message=f"Failed to enable server: {str(e)}",
                duration_ms=duration_ms,
            )
            return False, 0, f"Failed to enable server: {str(e)}"

    @log_method("MCPServerRegistryDB")
    async def disable_server(
        self, 
        server_name: str, 
        tags: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> tuple[bool, int, str]:
        """Disable an MCP server (alias for unmount_server).
        
        This is a convenience method that provides a clearer naming convention
        for disabling servers without fully removing them.
        
        Args:
            server_name: Name of the server to disable
            tags: Optional list of specific tags to disable
            user_id: Optional user identifier for audit trail
            correlation_id: Optional correlation ID for request tracing
            
        Returns:
            Tuple of (success: bool, disabled_count: int, message: str)
        """
        return await self.unmount_server(
            server_name=server_name,
            tags=tags,
            user_id=user_id,
            correlation_id=correlation_id,
        )

    @log_method("MCPServerRegistryDB")
    async def get_server_info(self, server_name: str) -> Optional[MCPServerInfo]:
        """Get information about a mounted MCP server.
        
        Args:
            server_name: Name of the server to get info for
            
        Returns:
            MCPServerInfo DTO or None if server not found
        """
        # Get server from database
        result = await self._session.execute(
            select(MCPServerDB).where(
                MCPServerDB.server_name == server_name,
                MCPServerDB.is_deleted == False
            ).options(selectinload(MCPServerDB.tags))
        )
        server_db = result.scalar_one_or_none()
        
        if not server_db:
            return None

        # Build tag info list
        tag_info_list = [
            ServerTagInfo(
                tag_name=tag.tag_name,
                status=self._map_db_status_to_dto(tag.status)
            )
            for tag in server_db.tags
            if not tag.is_deleted
        ]

        # Count tools for this server
        tool_count = 0
        server_tag_names = {tag.tag_name for tag in server_db.tags if not tag.is_deleted}
        
        if self._main_server:
            tool_set = await self._main_server.list_tools()
            for tool in tool_set:
                if hasattr(tool, "tags") and tool.tags:
                    if any(tag in server_tag_names for tag in tool.tags):
                        tool_count += 1

        return MCPServerInfo(
            server_name=server_db.server_name,
            spec_link=server_db.spec_link,
            base_url=server_db.base_url,
            description=server_db.description,
            tags=tag_info_list,
            status=self._map_db_status_to_dto(server_db.status),
            tool_count=tool_count,
        )

    @log_method("MCPServerRegistryDB")
    async def list_servers(
        self, 
        status_filter: Optional[MCPServerStatus] = None,
        include_deleted: bool = False,
    ) -> List[MCPServerInfo]:
        """List all MCP servers.
        
        Args:
            status_filter: Optional filter by server status
            include_deleted: Whether to include soft-deleted servers
            
        Returns:
            List of MCPServerInfo DTOs
        """
        query = select(MCPServerDB).options(selectinload(MCPServerDB.tags))
        
        if not include_deleted:
            query = query.where(MCPServerDB.is_deleted == False)
        
        if status_filter:
            query = query.where(MCPServerDB.status == status_filter)
        
        result = await self._session.execute(query)
        servers = result.scalars().all()
        
        server_info_list = []
        for server_db in servers:
            info = await self._build_server_info_from_db(server_db)
            if info:
                server_info_list.append(info)
        
        return server_info_list

    async def _build_server_info_from_db(self, server_db: MCPServerDB) -> Optional[MCPServerInfo]:
        """Build MCPServerInfo DTO from database model.
        
        Args:
            server_db: MCPServerDB database model instance
            
        Returns:
            MCPServerInfo DTO or None
        """
        # Build tag info list
        tag_info_list = [
            ServerTagInfo(
                tag_name=tag.tag_name,
                status=self._map_db_status_to_dto(tag.status)
            )
            for tag in server_db.tags
            if not tag.is_deleted
        ]

        # Count tools for this server
        tool_count = 0
        server_tag_names = {tag.tag_name for tag in server_db.tags if not tag.is_deleted}
        
        if self._main_server:
            tool_set = await self._main_server.list_tools()
            for tool in tool_set:
                if hasattr(tool, "tags") and tool.tags:
                    if any(tag in server_tag_names for tag in tool.tags):
                        tool_count += 1

        return MCPServerInfo(
            server_name=server_db.server_name,
            spec_link=server_db.spec_link,
            base_url=server_db.base_url,
            description=server_db.description,
            tags=tag_info_list,
            status=self._map_db_status_to_dto(server_db.status),
            tool_count=tool_count,
        )

    @log_method("MCPServerRegistryDB")
    async def list_tools(self) -> List[ToolInfo]:
        """List all tools from all mounted servers.
        
        Returns:
            List of ToolInfo DTOs
        """
        tools = []

        if not self._main_server:
            return tools

        tool_set = await self._main_server.list_tools()
        
        # Get all servers with their tags from database
        result = await self._session.execute(
            select(MCPServerDB).where(
                MCPServerDB.is_deleted == False
            ).options(selectinload(MCPServerDB.tags))
        )
        servers = result.scalars().all()
        
        # Build a map of tag -> server_name
        tag_to_server: Dict[str, str] = {}
        for server in servers:
            for tag in server.tags:
                if not tag.is_deleted:
                    tag_to_server[tag.tag_name] = server.server_name

        for tool in tool_set:
            # Get tool tags
            tool_tags = []
            if hasattr(tool, "tags") and tool.tags:
                tool_tags = list(tool.tags)

            # Determine source server
            source_server = None
            for tag in tool_tags:
                if tag in tag_to_server:
                    source_server = tag_to_server[tag]
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

    @log_method("MCPServerRegistryDB")
    async def get_server_status(self) -> dict:
        """Get overall registry status.
        
        Returns:
            Dictionary with server statistics
        """
        tool_set = await self.list_tools()
        total_tools = len(tool_set)
        
        # Count active servers from database
        result = await self._session.execute(
            select(MCPServerDB).where(
                MCPServerDB.status == MCPServerStatus.ACTIVE,
                MCPServerDB.is_deleted == False
            )
        )
        active_servers = len(result.scalars().all())
        
        # Count total servers from database
        result = await self._session.execute(
            select(MCPServerDB).where(MCPServerDB.is_deleted == False)
        )
        total_servers = len(result.scalars().all())

        return {
            "server_name": self._main_server.name if self._main_server else "unknown",
            "mounted_servers": len(self._mounted_servers),
            "total_servers": total_servers,
            "active_servers": active_servers,
            "total_tools": total_tools,
            "status": "running" if self._main_server else "stopped",
        }

    @log_method("MCPServerRegistryDB")
    async def remove_server(
        self, 
        server_name: str,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> tuple[bool, int, str]:
        """Remove an MCP server (soft delete).
        
        Performs a soft delete in the database and removes the server
        from the in-memory FastMCP instance.
        
        Args:
            server_name: Name of the server to remove
            user_id: Optional user identifier for audit trail
            correlation_id: Optional correlation ID for request tracing
            
        Returns:
            Tuple of (success: bool, disabled_count: int, message: str)
        """
        start_time = time.time()
        
        # Check if server is mounted in memory
        if server_name not in self._mounted_servers:
            # Check if it exists in database (might be unmounted but in DB)
            result = await self._session.execute(
                select(MCPServerDB).where(
                    MCPServerDB.server_name == server_name,
                    MCPServerDB.is_deleted == False
                )
            )
            server_db = result.scalar_one_or_none()
            
            if not server_db:
                return False, 0, f"Server '{server_name}' is not mounted"
        else:
            # Get server from database
            result = await self._session.execute(
                select(MCPServerDB).where(
                    MCPServerDB.server_name == server_name,
                    MCPServerDB.is_deleted == False
                )
            )
            server_db = result.scalar_one_or_none()

        if not server_db:
            return False, 0, f"Server '{server_name}' not found in database"

        disabled_count = 0
        try:
            # First disable/unmount if active
            if server_db.status == MCPServerStatus.ACTIVE:
                success, disabled_count, msg = await self.unmount_server(
                    server_name=server_name,
                    user_id=user_id,
                    correlation_id=correlation_id,
                )
                if not success:
                    return False, disabled_count, msg

            # Get all tags for this server before removal
            result = await self._session.execute(
                select(MCPServerTagDB).where(
                    MCPServerTagDB.server_id == server_db.id,
                    MCPServerTagDB.is_deleted == False
                )
            )
            server_tags = result.scalars().all()
            tags_to_enable = {tag.tag_name for tag in server_tags} if server_tags else {server_name}

            # Enable the tags before removing the provider to clear disabled state
            if self._main_server:
                self._main_server.enable(tags=tags_to_enable)

            # Properly unmount from main server by removing the provider
            if self._main_server and hasattr(self._main_server, 'providers'):
                providers = self._main_server.providers
                for i, provider in enumerate(providers):
                    if hasattr(provider, 'server') and hasattr(provider.server, 'name'):
                        if provider.server.name == server_name:
                            providers.pop(i)
                            break

            # Get the mounted server and close its client if it has one
            mounted_server = self._mounted_servers.get(server_name)
            if mounted_server and hasattr(mounted_server, '_client'):
                client = mounted_server._client
                if client and hasattr(client, 'aclose'):
                    await client.aclose()

            # Soft delete in database
            server_db.soft_delete(deleted_by=user_id)
            
            # Soft delete tags
            for tag in server_tags:
                tag.soft_delete(deleted_by=user_id)

            await self._session.commit()

            # Remove from in-memory cache
            if server_name in self._mounted_servers:
                del self._mounted_servers[server_name]

            # Log successful operation
            duration_ms = (time.time() - start_time) * 1000
            await self._audit_logger.log_server_operation(
                session=self._session,
                operation_type=ServerOperationType.REMOVE,
                server_id=server_db.id,
                server_name=server_name,
                user_id=user_id,
                correlation_id=correlation_id,
                result_status=AuditOperationStatus.SUCCESS,
                operation_details={
                    "disabled_tools": disabled_count,
                    "soft_delete": True,
                },
                duration_ms=duration_ms,
            )

            return True, disabled_count, f"Server '{server_name}' removed successfully"

        except Exception as e:
            await self._session.rollback()
            
            duration_ms = (time.time() - start_time) * 1000
            await self._audit_logger.log_server_operation(
                session=self._session,
                operation_type=ServerOperationType.REMOVE,
                server_id=server_db.id,
                server_name=server_name,
                user_id=user_id,
                correlation_id=correlation_id,
                result_status=AuditOperationStatus.FAILURE,
                error_message=f"Failed to remove server: {str(e)}",
                duration_ms=duration_ms,
            )
            return False, disabled_count, f"Failed to remove server: {str(e)}"

    @log_method("MCPServerRegistryDB")
    async def restore_servers_from_db(self) -> tuple[int, List[str]]:
        """Restore all ACTIVE servers from the database on startup.
        
        Queries all servers with ACTIVE status from the database and
        re-mounts them to the FastMCP main server.
        
        Returns:
            Tuple of (restored_count: int, errors: List[str])
        """
        if not self._main_server:
            raise RuntimeError("Main server must be initialized before restoring servers")

        # Query all active servers from database
        result = await self._session.execute(
            select(MCPServerDB).where(
                MCPServerDB.status == MCPServerStatus.ACTIVE,
                MCPServerDB.is_deleted == False
            ).options(selectinload(MCPServerDB.tags))
        )
        servers = result.scalars().all()

        restored_count = 0
        errors = []

        for server_db in servers:
            try:
                # Create config from database record
                config = MCPServerConfig(
                    server_name=server_db.server_name,
                    spec_link=server_db.spec_link,
                    base_url=server_db.base_url,
                    description=server_db.description,
                    headers=server_db.headers or {},
                )

                # Fetch OpenAPI spec
                response = httpx.get(config.spec_link, timeout=30.0)
                response.raise_for_status()
                openapi_spec = response.json()

                # Create async client
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
                self._main_server.mount(sub_server)

                # Check tag statuses and disable if needed
                disabled_tags = {
                    tag.tag_name for tag in server_db.tags
                    if tag.status == MCPServerStatus.DISABLED and not tag.is_deleted
                }
                if disabled_tags:
                    self._main_server.disable(tags=disabled_tags)

                # Track the mounted server
                self._mounted_servers[config.server_name] = sub_server
                restored_count += 1

            except Exception as e:
                error_msg = f"Failed to restore server '{server_db.server_name}': {str(e)}"
                errors.append(error_msg)
                # Update server status to ERROR in database
                server_db.status = MCPServerStatus.ERROR
                await self._session.commit()

        return restored_count, errors

    @log_method("MCPServerRegistryDB")
    async def get_server(self, server_name: str) -> Optional[MCPServerInfo]:
        """Get server information by name.
        
        Alias for get_server_info for API compatibility.
        
        Args:
            server_name: Name of the server to get
            
        Returns:
            MCPServerInfo DTO or None if not found
        """
        return await self.get_server_info(server_name)

    @log_method("MCPServerRegistryDB")
    async def list_server_tools(self, server_name: str) -> List[ToolInfo]:
        """List tools for a specific server.
        
        Args:
            server_name: Name of the server to get tools for
            
        Returns:
            List of ToolInfo DTOs for the specified server
        """
        all_tools = await self.list_tools()
        return [tool for tool in all_tools if tool.server_name == server_name]
