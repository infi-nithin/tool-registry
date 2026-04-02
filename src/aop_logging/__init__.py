"""Core logging module for AOP (Aspect-Oriented Programming) logging."""

from aop_logging.logger import (
    AOPLogger,
    get_aop_logger,
    set_aop_logger,
    log_method,
    correlation_id_var,
)
from aop_logging.models import (
    LogEntry,
    APILogEntry,
    MCPToolLogEntry,
    ServiceMethodLogEntry,
    OperationStatus,
    OperationType,
    LogLevel,
    LogSummary,
)
from aop_logging.middleware import (
    AOPLoggingMiddleware,
    RequestTimingMiddleware,
)
from aop_logging.mcp_tool_wrapper import (
    MCPToolLogger,
    create_logged_tool,
    patch_fastmcp_server,
    MCPCallTracker,
    get_mcp_tracker,
)
from aop_logging.config import LoggingConfig, default_config

__all__ = [
    # Logger
    "AOPLogger",
    "get_aop_logger",
    "set_aop_logger",
    "log_method",
    "correlation_id_var",
    
    # Models
    "LogEntry",
    "APILogEntry",
    "MCPToolLogEntry",
    "ServiceMethodLogEntry",
    "OperationStatus",
    "OperationType",
    "LogLevel",
    "LogSummary",
    
    # Middleware
    "AOPLoggingMiddleware",
    "RequestTimingMiddleware",
    
    # MCP Tool Wrapper
    "MCPToolLogger",
    "create_logged_tool",
    "patch_fastmcp_server",
    "MCPCallTracker",
    "get_mcp_tracker",
    
    # Config
    "LoggingConfig",
    "default_config",
]