"""Logging models for structured AOP logging."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Literal
from pydantic import BaseModel, Field, field_serializer


class LogLevel(str, Enum):
    """Log level enumeration."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class OperationType(str, Enum):
    """Type of operation being logged."""
    API_ENDPOINT = "api_endpoint"
    MCP_TOOL = "mcp_tool"
    SERVICE_METHOD = "service_method"
    DATABASE_OPERATION = "database_operation"
    EXTERNAL_CALL = "external_call"


class OperationStatus(str, Enum):
    """Status of the logged operation."""
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"


class LogEntry(BaseModel):
    """Base log entry model for all AOP logging."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    operation_type: OperationType
    operation_name: str = Field(..., description="Name of the tool, endpoint, or method")
    status: OperationStatus
    duration_ms: Optional[float] = Field(None, description="Execution time in milliseconds")
    
    # Request/Response details
    request_data: Optional[Dict[str, Any]] = Field(None, description="Request parameters (sanitized)")
    response_data: Optional[Dict[str, Any]] = Field(None, description="Response summary (sanitized)")
    
    # Error details
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    stack_trace: Optional[str] = None
    
    # Context
    source_server: Optional[str] = Field(None, description="Source MCP server name if applicable")
    tags: Optional[list[str]] = Field(None, description="Tags associated with the operation")
    
    # Metadata
    correlation_id: Optional[str] = Field(None, description="Unique correlation ID for request tracing")
    user_agent: Optional[str] = None
    client_ip: Optional[str] = None
    
    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.isoformat()


class APILogEntry(LogEntry):
    """Log entry for API endpoint calls."""
    operation_type: Literal[OperationType.API_ENDPOINT] = OperationType.API_ENDPOINT
    http_method: str = Field(..., description="HTTP method (GET, POST, PUT, DELETE, etc.)")
    endpoint_path: str = Field(..., description="API endpoint path")
    status_code: Optional[int] = Field(None, description="HTTP response status code")
    query_params: Optional[Dict[str, Any]] = None
    path_params: Optional[Dict[str, Any]] = None


class MCPToolLogEntry(LogEntry):
    """Log entry for MCP tool invocations."""
    operation_type: Literal[OperationType.MCP_TOOL] = OperationType.MCP_TOOL
    tool_name: str = Field(..., description="Name of the MCP tool")
    server_name: str = Field(..., description="MCP server hosting the tool")
    arguments: Optional[Dict[str, Any]] = Field(None, description="Tool arguments (sanitized)")
    result_summary: Optional[str] = Field(None, description="Summary of tool result")


class ServiceMethodLogEntry(LogEntry):
    """Log entry for service method calls."""
    operation_type: Literal[OperationType.SERVICE_METHOD] = OperationType.SERVICE_METHOD
    service_name: str = Field(..., description="Name of the service class")
    method_name: str = Field(..., description="Name of the method")
    class_name: Optional[str] = None
    module_name: Optional[str] = None


class LogSummary(BaseModel):
    """Summary statistics for logging."""
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    average_duration_ms: float = 0.0
    total_duration_ms: float = 0.0
    operations_by_type: Dict[str, int] = Field(default_factory=dict)
    operations_by_status: Dict[str, int] = Field(default_factory=dict)
