from typing import Dict, Optional, List, Literal
from enum import Enum
from pydantic import BaseModel, Field


class MCPServerStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


class MCPServerConfig(BaseModel):
    spec_link: str = Field(..., description="URL to the OpenAPI spec")
    base_url: str = Field(..., description="Base URL for the API")
    server_name: str = Field(..., description="Unique name for the server")
    description: Optional[str] = Field(None, description="Server description")
    headers: Optional[Dict[str, str]] = Field(None, description="Default headers")


class MCPServerMountRequest(BaseModel):
    config: MCPServerConfig = Field(..., description="Server configuration")


class MCPServerUnmountRequest(BaseModel):
    server_name: str = Field(..., description="Name of the server to unmount")
    tags: Optional[List[str]] = Field(None, description="Tags to disable (if empty, disables all)")


class MCPServerEnableRequest(BaseModel):
    server_name: str = Field(..., description="Name of the server to enable")
    tags: Optional[List[str]] = Field(None, description="Tags to enable (if empty, enables all)")


class ServerTagInfo(BaseModel):
    tag_name: str
    status: MCPServerStatus


class MCPServerInfo(BaseModel):
    server_name: str
    spec_link: str
    base_url: str
    description: Optional[str]
    tags: List[ServerTagInfo]
    status: MCPServerStatus
    tool_count: int = Field(0, description="Number of tools exposed by this server")


class MCPServerListResponse(BaseModel):
    servers: List[MCPServerInfo]
    total_count: int


class MCPServerMountResponse(BaseModel):
    success: bool
    server_name: str
    message: str
    tool_count: int = 0


class MCPServerUnmountResponse(BaseModel):
    success: bool
    server_name: str
    message: str
    disabled_tools_count: int


class MainServerStatusResponse(BaseModel):
    server_name: str
    mounted_servers: int
    total_tools: int
    status: Literal["running", "stopped"]


class ToolInfo(BaseModel):
    name: str
    description: Optional[str]
    tags: List[str]
    server_name: Optional[str] = Field(None, description="Source server name if from a mounted server")


class ToolListResponse(BaseModel):
    tools: List[ToolInfo]
    total_count: int


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
