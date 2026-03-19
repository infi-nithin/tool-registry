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
from aop_logging.aop_logger import log_method
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
        pass

    async def _ensure_db_initialized(self) -> None:
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
        await self._ensure_db_initialized()
        try:
            async with await get_session_context() as session:
                stmt = select(DBMcpServer).options(selectinload(DBMcpServer.tags))
                result = await session.execute(stmt)
                db_servers = result.scalars().all()
                for db_server in db_servers:
                    if db_server.status == MCPServerStatusEnum.ACTIVE:
                        config = MCPServerConfig(
                            server_name=db_server.server_name,
                            spec_link=db_server.spec_link,
                            base_url=db_server.base_url,
                            headers=db_server.headers,
                            description=db_server.description,
                        )
                        try:
                            response = httpx.get(config.spec_link, timeout=30.0)
                            response.raise_for_status()
                            openapi_spec = response.json()
                            client = httpx.AsyncClient(
                                base_url=config.base_url, headers=config.headers or {}
                            )
                            sub_server = FastMCP.from_openapi(
                                openapi_spec=openapi_spec,
                                client=client,
                                name=config.server_name,
                            )
                            if self._main_server:
                                self._main_server.mount(sub_server)
                            self._mounted_servers[config.server_name] = sub_server
                            tool_set = await sub_server.list_tools()
                            for tool in tool_set:
                                if hasattr(tool, "tags") and tool.tags:
                                    if config.server_name not in tool.tags:
                                        tool.tags = list(tool.tags) + [
                                            config.server_name
                                        ]
                                else:
                                    tool.tags = [config.server_name]
                            db_tags = db_server.tags or []
                            if db_tags:
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
                                if disabled_tags and self._main_server:
                                    self._main_server.disable(tags=set(disabled_tags))
                                    self._main_server.disable(tags={config.server_name})
                            else:
                                extracted_tags = self.extract_openapi_tags(
                                    openapi_spec
                                ) or [config.server_name]
                                self._server_tags[config.server_name] = {
                                    tag: MCPServerStatus.ACTIVE
                                    for tag in extracted_tags
                                }
                        except httpx.HTTPError:
                            self._server_tags[config.server_name] = {}
                        except Exception:
                            self._server_tags[config.server_name] = {}
                        self._server_configs[config.server_name] = config
                        self._server_status[config.server_name] = MCPServerStatus.ACTIVE
                if self._mounted_servers:
                    await self.refresh_tools_cache()
        except Exception:
            raise

    async def refresh_tools_cache(self) -> int:
        self._tools_cache.clear()
        if not self._main_server:
            return 0
        tool_set = await self._main_server.list_tools()
        for tool in tool_set:
            tool_tags = []
            if hasattr(tool, "tags") and tool.tags:
                tool_tags = list(tool.tags)
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
        if name in self._tools_cache:
            return self._tools_cache[name]
        if not self._tools_cache:
            await self.refresh_tools_cache()
            if name in self._tools_cache:
                return self._tools_cache[name]
        return None

    @log_method("MCPServerRegistry")
    async def initialize_main_server(self, name: str = "main-mcp-server") -> FastMCP:
        print(f"Initializing main MCP server: {name}")
        if self._main_server is None:
            self._main_server = FastMCP(name=name)
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

    @log_method("MCPServerRegistry")
    async def mount_server(self, config: MCPServerConfig) -> tuple[bool, int, str]:
        await self._ensure_db_initialized()
        if config.server_name in self._mounted_servers:
            return False, 0, f"Server '{config.server_name}' is already mounted"
        try:
            response = httpx.get(config.spec_link, timeout=30.0)
            response.raise_for_status()
            openapi_spec = response.json()
            client = httpx.AsyncClient(
                base_url=config.base_url, headers=config.headers or {}
            )
            sub_server = FastMCP.from_openapi(
                openapi_spec=openapi_spec,
                client=client,
                name=config.server_name,
            )
            tool_set = await sub_server.list_tools()
            tool_count = len(tool_set)
            for tool in tool_set:
                if hasattr(tool, "tags") and tool.tags:
                    if config.server_name not in tool.tags:
                        tool.tags = list(tool.tags) + [config.server_name]
                else:
                    tool.tags = [config.server_name]
            if self._main_server:
                self._main_server.mount(sub_server)
            self._mounted_servers[config.server_name] = sub_server
            self._server_configs[config.server_name] = config
            extracted_tags = self.extract_openapi_tags(openapi_spec) or [
                config.server_name
            ]
            self._server_tags[config.server_name] = {
                tag: MCPServerStatus.ACTIVE for tag in extracted_tags
            }
            self._server_status[config.server_name] = MCPServerStatus.ACTIVE
            async with await get_session_context() as session:
                db_server = DBMcpServer(
                    server_name=config.server_name,
                    spec_link=config.spec_link,
                    base_url=config.base_url,
                    headers=config.headers,
                    description=config.description,
                    status=MCPServerStatusEnum.ACTIVE,
                )
                session.add(db_server)
                for tag in extracted_tags:
                    db_tag = DBMcpServerTag(
                        server_name=config.server_name,
                        tag_name=tag,
                        status=MCPServerStatusEnum.ACTIVE,
                    )
                    session.add(db_tag)
                await self._save_audit_log(
                    session=session,
                    server_name=config.server_name,
                    action=AuditActionEnum.MOUNT,
                    status_before=None,
                    status_after=MCPServerStatusEnum.ACTIVE.value,
                )
                await session.commit()
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
        
    @log_method("MCPServerRegistry")
    async def unmount_server(
        self, server_name: str, tags: Optional[List[str]] = None
    ) -> tuple[bool, int, str]:
        await self._ensure_db_initialized()
        if server_name not in self._mounted_servers:
            try:
                async with await get_session_context() as session:
                    stmt = select(DBMcpServer).where(
                        DBMcpServer.server_name == server_name
                    )
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        return (
                            False,
                            0,
                            f"Server '{server_name}' is not mounted (exists in database as '{db_server.status.value}')",
                        )
            except Exception:
                pass
            return False, 0, f"Server '{server_name}' is not mounted"
        try:
            server_tags = self._server_tags.get(server_name, {})
            if tags:
                tags_to_disable = set(tags)
                invalid_tags = tags_to_disable - set(server_tags.keys()) - {server_name}
                if invalid_tags:
                    return (
                        False,
                        0,
                        f"Invalid tags: {invalid_tags}. Available tags: {set(server_tags.keys())}",
                    )
            elif server_tags:
                tags_to_disable = set(server_tags.keys())
            else:
                tags_to_disable = {server_name}
            tags_to_disable.add(server_name)
            disabled_count = 0
            if self._main_server:
                tool_set = await self._main_server.list_tools()
                for tool in tool_set:
                    if hasattr(tool, "tags") and tool.tags:
                        if any(tag in tags_to_disable for tag in tool.tags):
                            disabled_count += 1
                self._main_server.disable(tags=tags_to_disable)
            status_before = self._server_status.get(server_name)
            for tag in tags_to_disable:
                if tag in server_tags:
                    server_tags[tag] = MCPServerStatus.DISABLED
            all_disabled = bool(server_tags) and all(
                status == MCPServerStatus.DISABLED for status in server_tags.values()
            )
            if all_disabled:
                self._server_status[server_name] = MCPServerStatus.DISABLED
                new_db_status = MCPServerStatusEnum.DISABLED
            else:
                self._server_status[server_name] = MCPServerStatus.ACTIVE
                new_db_status = MCPServerStatusEnum.ACTIVE
            async with await get_session_context() as session:
                stmt = select(DBMcpServer).where(DBMcpServer.server_name == server_name)
                result = await session.execute(stmt)
                db_server = result.scalar_one_or_none()
                if db_server:
                    db_server.status = new_db_status
                for tag in tags_to_disable:
                    tag_stmt = select(DBMcpServerTag).where(
                        DBMcpServerTag.server_name == server_name,
                        DBMcpServerTag.tag_name == tag,
                    )
                    tag_result = await session.execute(tag_stmt)
                    db_tag = tag_result.scalar_one_or_none()
                    if db_tag:
                        db_tag.status = MCPServerStatusEnum.DISABLED
                await self._save_audit_log(
                    session=session,
                    server_name=server_name,
                    action=AuditActionEnum.UNMOUNT,
                    status_before=status_before.value if status_before else None,
                    status_after=new_db_status.value,
                )
                await session.commit()
            await self.refresh_tools_cache()
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
        await self._ensure_db_initialized()
        if server_name not in self._mounted_servers:
            try:
                async with await get_session_context() as session:
                    stmt = select(DBMcpServer).where(
                        DBMcpServer.server_name == server_name
                    )
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        if db_server.status == MCPServerStatusEnum.DISABLED:
                            return (
                                False,
                                0,
                                f"Server '{server_name}' exists in database but is disabled (not mounted in memory)",
                            )
                        return False, 0, f"Server '{server_name}' is not mounted"
            except Exception:
                pass
            return False, 0, f"Server '{server_name}' is not mounted"
        server_tags = self._server_tags.get(server_name, {})
        tags_to_enable: Set[str]
        if tags:
            tags_to_check = set(tags)
            invalid_tags = tags_to_check - set(server_tags.keys()) - {server_name}
            if invalid_tags:
                return (
                    False,
                    0,
                    f"Invalid tags: {invalid_tags}. Available tags: {set(server_tags.keys())}",
                )
            any_disabled = any(
                server_tags.get(tag) == MCPServerStatus.DISABLED
                for tag in tags_to_check
            )
            if not any_disabled:
                return (
                    False,
                    0,
                    f"Specified tags are not disabled for server '{server_name}'",
                )
            tags_to_enable = tags_to_check
        else:
            if server_name in self._mounted_servers:
                mounted_server = self._mounted_servers[server_name]
                try:
                    tool_set = await mounted_server.list_tools()
                    fastmcp_tags: Set[str] = set()
                    for tool in tool_set:
                        if hasattr(tool, "tags") and tool.tags:
                            fastmcp_tags.update(tool.tags)
                    fastmcp_tags.add(server_name)
                    all_known_tags = set(server_tags.keys()) if server_tags else set()
                    missing_tags = fastmcp_tags - all_known_tags
                    if missing_tags:
                        if not server_tags:
                            server_tags = {}
                        for tag in missing_tags:
                            server_tags[tag] = MCPServerStatus.DISABLED
                        self._server_tags[server_name] = server_tags
                except Exception:
                    pass
            if not server_tags:
                try:
                    async with await get_session_context() as session:
                        stmt = select(DBMcpServerTag).where(
                            DBMcpServerTag.server_name == server_name
                        )
                        result = await session.execute(stmt)
                        db_tags = result.scalars().all()
                        if db_tags:
                            server_tags = {
                                tag.tag_name: MCPServerStatus.DISABLED
                                for tag in db_tags
                            }
                            self._server_tags[server_name] = server_tags
                        else:
                            server_tags = {server_name: MCPServerStatus.DISABLED}
                            self._server_tags[server_name] = server_tags
                except Exception:
                    server_tags = {server_name: MCPServerStatus.DISABLED}
                    self._server_tags[server_name] = server_tags
            disabled_tags = {
                tag
                for tag, status in server_tags.items()
                if status == MCPServerStatus.DISABLED
            }
            if not disabled_tags:
                return False, 0, f"Server '{server_name}' has no disabled tags"
            tags_to_enable = disabled_tags
        status_before = self._server_status.get(server_name)
        try:
            enabled_count = 0
            if self._main_server:
                tool_set = await self._main_server.list_tools()
                for tool in tool_set:
                    if hasattr(tool, "tags") and tool.tags:
                        if any(tag in tags_to_enable for tag in tool.tags):
                            enabled_count += 1
                self._main_server.enable(tags=tags_to_enable)
            for tag in tags_to_enable:
                if tag in server_tags:
                    server_tags[tag] = MCPServerStatus.ACTIVE
            any_enabled = any(
                status == MCPServerStatus.ACTIVE for status in server_tags.values()
            )
            if any_enabled:
                self._server_status[server_name] = MCPServerStatus.ACTIVE
            async with await get_session_context() as session:
                for tag in tags_to_enable:
                    tag_stmt = select(DBMcpServerTag).where(
                        DBMcpServerTag.server_name == server_name,
                        DBMcpServerTag.tag_name == tag,
                    )
                    tag_result = await session.execute(tag_stmt)
                    db_tag = tag_result.scalar_one_or_none()
                    if db_tag:
                        db_tag.status = MCPServerStatusEnum.ACTIVE
                any_active = any(
                    tag_status == MCPServerStatus.ACTIVE
                    for tag_status in server_tags.values()
                )
                if any_active:
                    stmt = select(DBMcpServer).where(
                        DBMcpServer.server_name == server_name
                    )
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        db_server.status = MCPServerStatusEnum.ACTIVE
                await self._save_audit_log(
                    session=session,
                    server_name=server_name,
                    action=AuditActionEnum.ENABLE,
                    status_before=status_before.value if status_before else None,
                    status_after=self._server_status.get(server_name).value
                    if self._server_status.get(server_name)
                    else None,
                )
                await session.commit()
            await self.refresh_tools_cache()
            return True, enabled_count, f"Server '{server_name}' enabled successfully"
        except Exception as e:
            return False, 0, f"Failed to enable server: {str(e)}"

    @log_method("MCPServerRegistry")
    async def get_server_info(self, server_name: str) -> Optional[MCPServerInfo]:
        config = self._server_configs.get(server_name)
        server_tags = self._server_tags.get(server_name, {})
        if not config:
            await self._ensure_db_initialized()
            try:
                async with await get_session_context() as session:
                    stmt = (
                        select(DBMcpServer)
                        .options(selectinload(DBMcpServer.tags))
                        .where(DBMcpServer.server_name == server_name)
                    )
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        config = MCPServerConfig(
                            server_name=db_server.server_name,
                            spec_link=db_server.spec_link,
                            base_url=db_server.base_url,
                            headers=db_server.headers,
                            description=db_server.description,
                        )
                        server_tags = {
                            tag.tag_name: MCPServerStatus.ACTIVE
                            if tag.status == MCPServerStatusEnum.ACTIVE
                            else MCPServerStatus.DISABLED
                            for tag in db_server.tags
                        }
            except Exception:
                pass
        if not config:
            return None
        tag_info_list = [
            ServerTagInfo(tag_name=tag_name, status=status)
            for tag_name, status in server_tags.items()
        ]
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
        server_names = set(self._mounted_servers.keys())
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

    @log_method("MCPServerRegistry")
    async def list_tools(self) -> List[ToolInfo]:
        if not self._tools_cache:
            await self.refresh_tools_cache()
        return list(self._tools_cache.values())

    
    async def get_server_status(self) -> dict:
        await self._ensure_db_initialized()
        total_tools = 0
        active_servers = 0
        try:
            async with await get_session_context() as session:
                stmt = select(DBMcpServer)
                result = await session.execute(stmt)
                db_servers = result.scalars().all()
                for db_server in db_servers:
                    if db_server.status == MCPServerStatusEnum.ACTIVE:
                        active_servers += 1
        except Exception:
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


    @log_method("MCPServerRegistry")
    async def remove_server(self, server_name: str) -> tuple[bool, int, str]:
        await self._ensure_db_initialized()
        if server_name not in self._mounted_servers:
            try:
                async with await get_session_context() as session:
                    stmt = select(DBMcpServer).where(
                        DBMcpServer.server_name == server_name
                    )
                    result = await session.execute(stmt)
                    db_server = result.scalar_one_or_none()
                    if db_server:
                        status_before = (
                            MCPServerStatus.ACTIVE
                            if db_server.status == MCPServerStatusEnum.ACTIVE
                            else MCPServerStatus.DISABLED
                        )
                        disabled_count = 0
                        try:
                            await self._save_audit_log(
                                session=session,
                                server_name=server_name,
                                action=AuditActionEnum.REMOVE,
                                status_before=status_before.value
                                if status_before
                                else None,
                                status_after=None,
                            )
                            await session.delete(db_server)
                            await session.commit()
                            return (
                                True,
                                disabled_count,
                                f"Server '{server_name}' removed successfully",
                            )
                        except Exception as e:
                            await session.rollback()
                            return (
                                False,
                                disabled_count,
                                f"Failed to remove server: {str(e)}",
                            )
            except Exception:
                pass
            return False, 0, f"Server '{server_name}' is not mounted"
        disabled_count = 0
        status_before = self._server_status.get(server_name)
        try:
            if self._server_status.get(server_name) == MCPServerStatus.ACTIVE:
                success, disabled_count, msg = await self.unmount_server(server_name)
                if not success:
                    return False, disabled_count, msg
            server_tags = self._server_tags.get(server_name, {})
            tags_to_enable = set(server_tags.keys()) if server_tags else {server_name}
            if self._main_server:
                self._main_server.enable(tags=tags_to_enable)
            if self._main_server and hasattr(self._main_server, "providers"):
                providers = self._main_server.providers
                for i, provider in enumerate(providers):
                    if hasattr(provider, "server") and hasattr(provider.server, "name"):
                        if provider.server.name == server_name:
                            providers.pop(i)
                            print(f"Removed provider for server '{server_name}'")
                            break
            mounted_server = self._mounted_servers.get(server_name)
            if mounted_server and hasattr(mounted_server, "_client"):
                client = mounted_server._client
                if client and hasattr(client, "aclose"):
                    await client.aclose()
            del self._mounted_servers[server_name]
            del self._server_configs[server_name]
            del self._server_tags[server_name]
            del self._server_status[server_name]
            async with await get_session_context() as session:
                await self._save_audit_log(
                    session=session,
                    server_name=server_name,
                    action=AuditActionEnum.REMOVE,
                    status_before=status_before.value if status_before else None,
                    status_after=None,
                )
                stmt = select(DBMcpServer).where(DBMcpServer.server_name == server_name)
                result = await session.execute(stmt)
                db_server = result.scalar_one_or_none()
                if db_server:
                    await session.delete(db_server)
                await session.commit()
            await self.refresh_tools_cache()
            return True, disabled_count, f"Server '{server_name}' removed successfully"
        except Exception as e:
            return False, disabled_count, f"Failed to remove server: {str(e)}"


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
