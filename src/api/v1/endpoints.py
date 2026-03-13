from typing import Optional
from fastapi import APIRouter, HTTPException, status

from dto.models import (
    MCPServerMountRequest,
    MCPServerUnmountRequest,
    MCPServerEnableRequest,
    MCPServerInfo,
    MCPServerListResponse,
    MCPServerMountResponse,
    MCPServerUnmountResponse,
    MainServerStatusResponse,
    ToolInfo,
    ToolListResponse,
    ErrorResponse,
)
from service.mcp_service import get_registry

router = APIRouter()


@router.get("/ping", tags=["health"])
async def ping():
    return {"ping": "pong"}


@router.post(
    "/mcp/servers",
    response_model=MCPServerMountResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["mcp-servers"],
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        409: {"model": ErrorResponse, "description": "Server already exists"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def mount_server(request: MCPServerMountRequest):
    registry = await get_registry()
    success, tool_count, message = await registry.mount_server(
        config=request.config,
    )

    if not success:
        if "already mounted" in message or "already exists" in message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "Server already exists", "detail": message},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Mount failed", "detail": message},
        )

    return MCPServerMountResponse(
        success=True,
        server_name=request.config.server_name,
        message=message,
        tool_count=tool_count,
    )


@router.post(
    "/mcp/servers/{server_name}/unmount",
    response_model=MCPServerUnmountResponse,
    tags=["mcp-servers"],
    responses={
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unmount_server(
    server_name: str,
    request: Optional[MCPServerUnmountRequest] = None,
):
    tags = request.tags if request else None

    registry = await get_registry()
    success, disabled_count, message = await registry.unmount_server(server_name, tags)

    if not success:
        if "not mounted" in message or "not found" in message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Server not found", "detail": message},
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Unmount failed", "detail": message},
        )

    return MCPServerUnmountResponse(
        success=True,
        server_name=server_name,
        message=message,
        disabled_tools_count=disabled_count,
    )


@router.post(
    "/mcp/servers/{server_name}/enable",
    response_model=MCPServerMountResponse,
    tags=["mcp-servers"],
    responses={
        404: {"model": ErrorResponse, "description": "Server not found"},
        400: {"model": ErrorResponse, "description": "Server not disabled"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def enable_server(
    server_name: str,
    request: Optional[MCPServerEnableRequest] = None,
):
    registry = await get_registry()
    tags = request.tags if request else None
    success, enabled_count, message = await registry.enable_server(server_name, tags)

    if not success:
        if "not mounted" in message or "not found" in message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Server not found", "detail": message},
            )
        if "not disabled" in message:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "Server not disabled", "detail": message},
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Enable failed", "detail": message},
        )

    # Get updated info
    info = await registry.get_server_info(server_name)

    return MCPServerMountResponse(
        success=True,
        server_name=server_name,
        message=message,
        tool_count=info.tool_count if info else 0,
    )


@router.delete(
    "/mcp/servers/{server_name}",
    response_model=MCPServerUnmountResponse,
    tags=["mcp-servers"],
    responses={
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def remove_server(server_name: str):
    registry = await get_registry()
    success, disabled_tool_count, message = await registry.remove_server(server_name)

    if not success:
        if "not mounted" in message or "not found" in message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Server not found", "detail": message},
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Remove failed", "detail": message},
        )

    return MCPServerUnmountResponse(
        success=True,
        server_name=server_name,
        message=message,
        disabled_tools_count=disabled_tool_count,
    )


@router.get("/mcp/servers", response_model=MCPServerListResponse, tags=["mcp-servers"])
async def list_servers():
    registry = await get_registry()
    servers = await registry.list_servers()

    return MCPServerListResponse(
        servers=servers,
        total_count=len(servers),
    )


@router.get(
    "/mcp/servers/{server_name}",
    response_model=MCPServerInfo,
    tags=["mcp-servers"],
    responses={
        404: {"model": ErrorResponse, "description": "Server not found"},
    },
)
async def get_server(server_name: str):
    registry = await get_registry()
    info = await registry.get_server_info(server_name)

    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "Server not found",
                "detail": f"Server '{server_name}' is not mounted",
            },
        )

    return info


@router.get("/mcp/tools", response_model=ToolListResponse, tags=["mcp-tools"])
async def list_tools():
    registry = await get_registry()
    tools = await registry.list_tools()

    return ToolListResponse(
        tools=tools,
        total_count=len(tools),
    )


@router.get(
    "/mcp/tools/{tool_name}",
    response_model=ToolInfo,
    tags=["mcp-tools"],
    responses={
        404: {"model": ErrorResponse, "description": "Tool not found"},
    },
)
async def get_tool(tool_name: str):
    registry = await get_registry()
    tool = await registry.get_tool(tool_name)

    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "Tool not found",
                "detail": f"Tool '{tool_name}' not found",
            },
        )

    return tool


@router.get("/mcp/status", response_model=MainServerStatusResponse, tags=["mcp-status"])
async def get_main_server_status():
    registry = await get_registry()
    status_info = await registry.get_server_status()

    return MainServerStatusResponse(
        server_name=status_info["server_name"],
        mounted_servers=status_info["mounted_servers"],
        total_tools=status_info["total_tools"],
        status=status_info["status"],
    )
